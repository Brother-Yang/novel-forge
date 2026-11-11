"""离线端到端回归测试(使用假 LLM,不调用真实模型,不消耗算力)。

覆盖:世界观 -> 人物 -> 全书大纲 -> 章节大纲 -> 全书批量正文任务
-> 断点续写 -> TXT/EPUB 导出 -> 一句话出书编排器 -> 章纲失败重试
-> 跳过已有数据 -> 进程重启恢复。验证解析、落库、向量索引、进度与续写逻辑。

运行(在 backend 目录下):
    python tests/e2e_offline.py

全部通过时退出码为 0,存在失败时为 1。数据写入临时目录并在结束时清理。
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import uuid
import zipfile

# 保证可从 backend 根目录导入 app,并使用独立的临时数据目录
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_ROOT)
_TMP = tempfile.mkdtemp(prefix="novel_e2e_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/novel.db"
os.environ["CHROMA_DIR"] = f"{_TMP}/chroma"

from app import models  # noqa: E402
from app.agents import orchestrator, pipeline, planner, prompts, tools  # noqa: E402
from app.api import export  # noqa: E402
from app.config import settings as app_settings  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.llm.base import ToolCall, ToolChatResult  # noqa: E402
from app.memory import context as memory  # noqa: E402
from app.memory import vector_store  # noqa: E402
from app.tasks import agent_generation, book_generation, novel_generation  # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  OK  " if cond else " FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


class FakeLLM:
    """按 system 提示词路由,产出符合各层解析格式的固定文本。"""

    async def chat_stream(self, prompt, system="", temperature=0.8):
        for ch in self._route(system):
            yield ch

    async def chat(self, prompt, system="", temperature=0.8):
        if system == prompts.PLANNER_SYSTEM:
            return self._route(system)
        return "本章摘要:主角推进关键情节,埋下新的伏笔。"

    async def embed(self, text):
        return [0.11, 0.22, 0.33, 0.44, 0.55, 0.66, 0.77, 0.88]

    def _route(self, system: str) -> str:
        if system == prompts.PLANNER_SYSTEM:
            return json.dumps(
                {
                    "title": "验证之书",
                    "genre": "玄幻",
                    "tone": "热血",
                    "intro": "用于逻辑验证",
                    "volume_count": 2,
                    "chapters_per_volume": 3,
                    "words_per_chapter": 2000,
                    "character_count": 3,
                    "key_elements": ["用户明确要求的开篇场景"],
                },
                ensure_ascii=False,
            )
        if system == prompts.WORLDBUILDING_SYSTEM:
            return (
                "## 世界规则\n灵气复苏,修炼分九阶。\n\n"
                "## 主要地理\n东荒、南疆、西域三大板块。\n\n"
                "## 核心冲突\n人族与妖族的千年之争。"
            )
        if system == prompts.CHARACTERS_SYSTEM:
            return (
                "# 林逸\n- 身份:少年天才\n- 性格:坚毅\n- 目标:变强\n\n"
                "# 苏婉\n- 身份:同门师姐\n- 性格:温柔\n\n"
                "# 玄冥\n- 身份:妖族大将\n- 性格:狠辣"
            )
        if system == prompts.OUTLINE_SYSTEM:
            return (
                "## 第一卷 觉醒\n林逸觉醒血脉,踏入修炼之路。\n\n"
                "## 第二卷 试炼\n宗门大比与妖族入侵。\n\n"
                "# 剧情线程\n- 血脉之谜 | 林逸的身世\n- 人妖之战 | 主线冲突\n"
            )
        if system == prompts.CHAPTER_OUTLINE_SYSTEM:
            return (
                "## 第1章 觉醒之夜\n林逸夜半觉醒血脉,涉及林逸。\n\n"
                "## 第2章 初入宗门\n林逸拜入青云宗,遇到苏婉。\n\n"
                "## 第3章 危机初现\n妖族斥候玄冥出现。"
            )
        if system == prompts.CHAPTER_BODY_SYSTEM:
            return (
                "夜色如墨,林逸盘膝而坐。\n"
                "体内血脉忽然苏醒,一股暖流涌遍四肢百骸。\n"
                "“这就是传说中的力量吗?”他喃喃自语。"
            )
        return "占位文本。"


def _tc(name: str, **kwargs: object) -> ToolChatResult:
    return ToolChatResult(content="", tool_calls=[ToolCall(id="1", name=name, arguments=kwargs)])


class FakeAgentLLM:
    """按当前书籍数据库状态确定性地返回下一步工具调用,模拟自主决策
    (离线、不依赖真实模型的决策脚本,但真实驱动 orchestrator + tools 的整套逻辑)。
    """

    def __init__(self, db, book_id: str, *, volume_count: int = 2, chapter_count: int = 2) -> None:
        self.db = db
        self.book_id = book_id
        self.volume_count = volume_count
        self.chapter_count = chapter_count

    async def chat_with_tools(self, messages, tool_defs, temperature=0.7) -> ToolChatResult:
        db, book_id = self.db, self.book_id
        db.expire_all()
        if db.query(models.Worldbuilding).filter_by(book_id=book_id).count() == 0:
            return _tc("write_worldbuilding")
        if db.query(models.Character).filter_by(book_id=book_id).count() == 0:
            return _tc("create_characters", count=3)
        volumes = (
            db.query(models.Volume)
            .filter_by(book_id=book_id)
            .order_by(models.Volume.order_index)
            .all()
        )
        if not volumes:
            return _tc("create_outline", volume_count=self.volume_count)
        for v in volumes:
            if db.query(models.Chapter).filter_by(book_id=book_id, volume_id=v.id).count() == 0:
                return _tc("create_chapter_outlines", volume_id=v.id, chapter_count=self.chapter_count)
        pending = (
            db.query(models.Chapter)
            .filter(models.Chapter.book_id == book_id, models.Chapter.status != "done")
            .order_by(models.Chapter.order_index)
            .first()
        )
        if pending:
            return _tc("write_chapter", chapter_id=pending.id, words=1500)
        return _tc("finish_book", reason="全部完成")


class StuckLLM:
    """永远重复已完成动作,验证步数耗尽后停止并保留可重试错误。"""

    async def chat_with_tools(self, messages, tool_defs, temperature=0.7) -> ToolChatResult:
        return _tc("write_worldbuilding")


async def consume(gen):
    async for _ev, _data in gen:
        pass


async def main() -> None:
    fake = FakeLLM()
    pipeline.get_llm = lambda: fake
    planner.get_llm = lambda: fake
    memory.get_llm = lambda: fake
    tools.get_llm = lambda: fake
    # 智能体模式需要支持 function calling 的后端;离线测试里没有真实的
    # openai/bailian 账号,只需让能力探测放行,决策本身用 FakeAgentLLM 打桩。
    app_settings.llm_provider = "openai"

    init_db()
    db = SessionLocal()

    book = models.Book(
        title="验证之书", intro="用于逻辑验证", genre="玄幻", tone="热血", target_words=100000
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    try:
        uuid.UUID(book.id)
        id_ok = True
    except ValueError:
        id_ok = False
    check(id_ok, f"书籍 id 应为 UUID,实际 {book.id!r}")

    print("\n[1] 世界观")
    await consume(pipeline.gen_worldbuilding(db, book.id))
    wb = db.query(models.Worldbuilding).filter_by(book_id=book.id).all()
    check(len(wb) == 3, f"世界观应为3条,实际{len(wb)}")
    check(vector_store._collection(book.id).count() >= 3, "世界观已写入向量库")

    print("\n[2] 人物")
    await consume(pipeline.gen_characters(db, book.id, count=3))
    chars = db.query(models.Character).filter_by(book_id=book.id).all()
    check(len(chars) == 3, f"人物应为3个,实际{len(chars)}")
    check(all(c.md_content.startswith("# ") for c in chars), "每个人物为独立 md")
    check(
        {c.name for c in chars} == {"林逸", "苏婉", "玄冥"},
        f"人物名解析正确:{[c.name for c in chars]}",
    )

    print("\n[3] 全书大纲 + 剧情线程")
    await consume(pipeline.gen_outline(db, book.id, volume_count=2))
    vols = (
        db.query(models.Volume)
        .filter_by(book_id=book.id)
        .order_by(models.Volume.order_index)
        .all()
    )
    threads = db.query(models.PlotThread).filter_by(book_id=book.id).all()
    check(len(vols) == 2, f"应为2卷,实际{len(vols)}")
    check(all(v.summary for v in vols), "每卷有梗概")
    check(len(threads) == 2, f"应为2条剧情线程,实际{len(threads)}")

    print("\n[4] 章节大纲(逐卷)")
    for v in vols:
        await consume(pipeline.gen_chapter_outlines(db, v.id, chapter_count=3))
    chapters = (
        db.query(models.Chapter)
        .filter_by(book_id=book.id)
        .order_by(models.Chapter.order_index)
        .all()
    )
    check(len(chapters) == 6, f"应为6章,实际{len(chapters)}")
    check(
        all(c.status == "outline" and c.outline for c in chapters),
        "每章有大纲且状态=outline",
    )
    check(all(c.volume_id in {v.id for v in vols} for c in chapters), "每章归属到卷")
    orders = [c.order_index for c in chapters]
    check(orders == sorted(orders) and len(set(orders)) == 6, f"order_index 唯一且有序:{orders}")

    print("\n[5] 全书批量正文任务")
    task = book_generation.start_book_generation(db, book.id)
    await book_generation._running[task.id]
    db.expire_all()
    task = db.get(models.GenerationTask, task.id)
    chapters = db.query(models.Chapter).filter_by(book_id=book.id).all()
    summaries = (
        db.query(models.ChapterSummary)
        .join(models.Chapter, models.Chapter.id == models.ChapterSummary.chapter_id)
        .filter(models.Chapter.book_id == book.id)
        .all()
    )
    check(
        task.status == "done" and task.progress == 100,
        f"任务完成,status={task.status},progress={task.progress}",
    )
    check(all(c.status == "done" and c.word_count > 0 for c in chapters), "所有章节已生成正文")
    check(len(summaries) == 6, f"每章有摘要,实际{len(summaries)}")
    check(vector_store._collection(book.id).count() >= 9, "章摘要已写入向量库(世界观3+章6)")

    print("\n[6] 断点续写(把1章回退,重启任务只补该章)")
    c0 = chapters[0]
    c0.status = "outline"
    if c0.content:
        c0.content.body = ""
    c0.word_count = 0
    db.commit()
    pending_before = book_generation._pending_chapters(db, book.id)
    check(pending_before == [c0.id], f"仅1章待写:{pending_before}")
    task2 = book_generation.start_book_generation(db, book.id)
    await book_generation._running[task2.id]
    db.expire_all()
    c0 = db.get(models.Chapter, c0.id)
    task2 = db.get(models.GenerationTask, task2.id)
    check(c0.status == "done" and c0.word_count > 0, "回退章已被续写完成")
    check(task2.status == "done" and task2.progress == 100, "续写任务完成且进度100")

    print("\n[7] 导出 TXT / EPUB")
    txt = "".join(export._export_txt(db, book))
    check(all(c.title in txt for c in chapters), "TXT 含全部章节标题")
    check("第一卷 觉醒" in txt and "第二卷 试炼" in txt, "TXT 含卷标题")
    epub_path = export._build_epub(db, book)
    with zipfile.ZipFile(epub_path) as zf:
        names = zf.namelist()
        check(names[0] == "mimetype", "EPUB mimetype 在首位")
        check(zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED, "mimetype 未压缩")
        check("OEBPS/content.opf" in names and "OEBPS/toc.ncx" in names, "EPUB 含 opf/ncx")
        check(sum(1 for n in names if n.endswith(".xhtml")) == 6, "EPUB 含6章 xhtml")
        check(zf.testzip() is None, "EPUB zip 完整性通过")
    os.remove(epub_path)

    print("\n[8] 拖拽排序(移动到未归卷 / 移回卷)")
    from app.api.chapters import reorder_chapters
    from app import schemas

    ch = chapters[0]
    reorder_chapters(
        schemas.ReorderRequest(
            items=[schemas.ReorderItem(id=ch.id, order_index=0, volume_id=None)]
        ),
        db,
    )
    db.expire_all()
    check(db.get(models.Chapter, ch.id).volume_id is None, "拖到未归卷区生效(volume_id=None)")
    reorder_chapters(
        schemas.ReorderRequest(
            items=[schemas.ReorderItem(id=ch.id, order_index=0, volume_id=vols[0].id)]
        ),
        db,
    )
    db.expire_all()
    check(db.get(models.Chapter, ch.id).volume_id == vols[0].id, "拖回卷生效")

    print("\n[9] 一句话出书编排器(brief → 全书) + 单任务锁")
    book_n = models.Book(
        title="未命名",
        brief="少年林逸觉醒上古血脉,从宗门杂役踏上与妖族争锋之路。",
        intro="",
        genre="",
        tone="",
        target_words=12000,
    )
    db.add(book_n)
    db.commit()
    db.refresh(book_n)
    task_n = novel_generation.start_novel_generation(db, book_n.id)
    try:
        novel_generation.start_novel_generation(db, book_n.id)
        check(False, "同一本书不应允许第二个全书任务")
    except book_generation.BookBusyError:
        check(True, "重复启动全书任务被拒绝")
    await book_generation._running[task_n.id]
    db.expire_all()
    task_n = db.get(models.GenerationTask, task_n.id)
    book_n = db.get(models.Book, book_n.id)
    wb_n = db.query(models.Worldbuilding).filter_by(book_id=book_n.id).all()
    chars_n = db.query(models.Character).filter_by(book_id=book_n.id).all()
    vols_n = (
        db.query(models.Volume)
        .filter_by(book_id=book_n.id)
        .order_by(models.Volume.order_index)
        .all()
    )
    chs_n = (
        db.query(models.Chapter)
        .filter_by(book_id=book_n.id)
        .order_by(models.Chapter.order_index)
        .all()
    )
    check(task_n.status == "done" and task_n.progress == 100, "novel 任务完成")
    check(task_n.type == "novel", "任务 type=novel")
    check(book_n.title == "验证之书" and book_n.genre == "玄幻", "planner 写回书名/题材")
    check(
        json.loads(book_n.key_elements or "[]") == ["用户明确要求的开篇场景"],
        f"planner 提取的 key_elements 已写回书籍,实际{book_n.key_elements!r}",
    )
    check(len(wb_n) == 3, f"编排器生成3条设定,实际{len(wb_n)}")
    check(len(chars_n) == 3, f"编排器生成3个人物,实际{len(chars_n)}")
    check(len(vols_n) == 2 and len(chs_n) == 6, f"编排器 2卷6章,实际卷{len(vols_n)}章{len(chs_n)}")
    check(all(c.status == "done" and c.word_count > 0 for c in chs_n), "编排器全部章节已有正文")

    print("\n[10] 章纲中途失败后重试,已完成卷不重复")
    book_f = models.Book(
        title="未命名",
        brief="试炼失败续写",
        target_words=12000,
    )
    db.add(book_f)
    db.commit()
    db.refresh(book_f)
    real_outlines = pipeline.gen_chapter_outlines

    async def flaky_outlines(db_inner, volume_id, chapter_count=10):
        vol_rows = (
            db_inner.query(models.Volume)
            .filter_by(book_id=book_f.id)
            .order_by(models.Volume.order_index)
            .all()
        )
        if len(vol_rows) >= 2 and volume_id == vol_rows[1].id:
            yield ("error", "injected failure")
            return
        async for item in real_outlines(db_inner, volume_id, chapter_count):
            yield item

    pipeline.gen_chapter_outlines = flaky_outlines
    task_f = novel_generation.start_novel_generation(db, book_f.id)
    await book_generation._running[task_f.id]
    db.expire_all()
    task_f = db.get(models.GenerationTask, task_f.id)
    vols_f = (
        db.query(models.Volume)
        .filter_by(book_id=book_f.id)
        .order_by(models.Volume.order_index)
        .all()
    )
    chs_f = db.query(models.Chapter).filter_by(book_id=book_f.id).all()
    vol1_count = len([c for c in chs_f if c.volume_id == vols_f[0].id]) if vols_f else 0
    vol2_count = (
        len([c for c in chs_f if c.volume_id == vols_f[1].id]) if len(vols_f) > 1 else 0
    )
    check(task_f.status == "error", f"注入失败后任务为 error,实际{task_f.status}")
    check(len(vols_f) == 2, f"失败前已落2卷,实际{len(vols_f)}")
    check(vol1_count == 3 and vol2_count == 0, f"仅卷1有章纲:{vol1_count}/{vol2_count}")
    pipeline.gen_chapter_outlines = real_outlines
    book_generation.resume_task(db, task_f.id)
    await book_generation._running[task_f.id]
    db.expire_all()
    task_f = db.get(models.GenerationTask, task_f.id)
    chs_f = db.query(models.Chapter).filter_by(book_id=book_f.id).all()
    vol1_after = len([c for c in chs_f if c.volume_id == vols_f[0].id])
    vol2_after = len([c for c in chs_f if c.volume_id == vols_f[1].id])
    check(task_f.status == "done", f"重试后任务完成,实际{task_f.status}")
    check(vol1_after == 3 and vol2_after == 3, f"重试不重复卷1,补齐卷2:{vol1_after}/{vol2_after}")
    check(all(c.status == "done" for c in chs_f), "重试后全部章节正文完成")

    print("\n[11] 已有设定/人物再启动 novel,不追加重复条目")
    book_s = models.Book(
        title="跳过之书",
        brief="已有设定的续跑",
        genre="玄幻",
        tone="热血",
        target_words=12000,
    )
    db.add(book_s)
    db.commit()
    db.refresh(book_s)
    await consume(pipeline.gen_worldbuilding(db, book_s.id))
    await consume(pipeline.gen_characters(db, book_s.id, count=3))
    wb_before = db.query(models.Worldbuilding).filter_by(book_id=book_s.id).count()
    char_before = db.query(models.Character).filter_by(book_id=book_s.id).count()
    check(wb_before == 3 and char_before == 3, "预置设定/人物各3")
    task_s = novel_generation.start_novel_generation(db, book_s.id)
    await book_generation._running[task_s.id]
    db.expire_all()
    wb_after = db.query(models.Worldbuilding).filter_by(book_id=book_s.id).count()
    char_after = db.query(models.Character).filter_by(book_id=book_s.id).count()
    chs_s = db.query(models.Chapter).filter_by(book_id=book_s.id).all()
    task_s = db.get(models.GenerationTask, task_s.id)
    check(wb_after == wb_before, f"设定条数不变,实际{wb_after}")
    check(char_after == char_before, f"人物数不变,实际{char_after}")
    check(task_s.status == "done", "跳过后仍能完成全书")
    check(len(chs_s) == 6 and all(c.status == "done" for c in chs_s), "跳过前两层后补齐章与正文")

    print("\n[12] 模拟进程重启:库中 running + 内存为空,recover 后续跑")
    book_r = models.Book(
        title="重启之书",
        brief="进程重启续写",
        genre="玄幻",
        tone="热血",
        target_words=12000,
    )
    db.add(book_r)
    db.commit()
    db.refresh(book_r)
    await consume(pipeline.gen_worldbuilding(db, book_r.id))
    await consume(pipeline.gen_characters(db, book_r.id, count=3))
    await consume(pipeline.gen_outline(db, book_r.id, volume_count=2))
    vols_r = (
        db.query(models.Volume)
        .filter_by(book_id=book_r.id)
        .order_by(models.Volume.order_index)
        .all()
    )
    await consume(pipeline.gen_chapter_outlines(db, vols_r[0].id, chapter_count=3))
    task_r = models.GenerationTask(
        book_id=book_r.id,
        type="novel",
        status="running",
        progress=30,
        checkpoint_json=json.dumps(
            {
                "stage": "chapter_outlines",
                "volume_count": 2,
                "chapters_per_volume": 3,
                "words_per_chapter": 2000,
                "character_count": 3,
            },
            ensure_ascii=False,
        ),
    )
    db.add(task_r)
    db.commit()
    db.refresh(task_r)
    check(task_r.id not in book_generation._running, "模拟重启后内存任务表为空")
    recovered = book_generation.recover_running_tasks()
    check(recovered >= 1 and task_r.id in book_generation._running, "recover 重新拉起 running 任务")
    await book_generation._running[task_r.id]
    db.expire_all()
    task_r = db.get(models.GenerationTask, task_r.id)
    chs_r = db.query(models.Chapter).filter_by(book_id=book_r.id).all()
    check(task_r.status == "done", f"重启续跑完成,实际{task_r.status}")
    check(len(chs_r) == 6, f"补齐第二卷章纲后共6章,实际{len(chs_r)}")
    check(all(c.status == "done" for c in chs_r), "重启续跑后全部正文完成")

    print("\n[13] 智能体自主决策(type=agent),一句话到成书")
    book_a = models.Book(
        title="未命名",
        brief="一位废柴少年意外获得上古传承,踏上强者之路。",
        intro="",
        genre="",
        tone="",
        target_words=8000,
    )
    db.add(book_a)
    db.commit()
    db.refresh(book_a)

    # 注:FakeLLM 对各层是按 system 提示词路由到固定文本(忽略调用参数),
    # 所以无论这里传入的 volume_count/chapter_count 是多少,实际落库结果都是
    # 固定的 2 卷、每卷 3 章(与本文件其它章节的 FakeLLM 输出一致)。
    orchestrator.get_llm = lambda: FakeAgentLLM(db, book_a.id, volume_count=2, chapter_count=3)
    task_a = agent_generation.start_agent_generation(db, book_a.id)
    await book_generation._running[task_a.id]
    db.expire_all()
    task_a = db.get(models.GenerationTask, task_a.id)
    wb_a = db.query(models.Worldbuilding).filter_by(book_id=book_a.id).all()
    chars_a = db.query(models.Character).filter_by(book_id=book_a.id).all()
    vols_a = (
        db.query(models.Volume)
        .filter_by(book_id=book_a.id)
        .order_by(models.Volume.order_index)
        .all()
    )
    chs_a = db.query(models.Chapter).filter_by(book_id=book_a.id).all()
    check(task_a.status == "done", f"智能体任务完成,实际{task_a.status}")
    check(task_a.type == "agent", "任务 type=agent")
    book_a = db.get(models.Book, book_a.id)
    check(book_a.title == "验证之书", f"智能体规划后写回书名,实际{book_a.title}")
    check(book_a.intro == "用于逻辑验证", "智能体简介来自规划,不是用户原话")
    check(len(wb_a) == 3, f"智能体生成3条设定,实际{len(wb_a)}")
    check(len(chars_a) == 3, f"智能体生成3个人物,实际{len(chars_a)}")
    check(len(vols_a) == 2 and len(chs_a) == 6, f"智能体2卷6章,实际卷{len(vols_a)}章{len(chs_a)}")
    check(all(c.status == "done" and c.word_count > 0 for c in chs_a), "智能体已完成全部章节正文")
    ckpt_a = json.loads(task_a.checkpoint_json or "{}")
    decisions_a = ckpt_a.get("decisions")
    check(
        isinstance(decisions_a, list) and len(decisions_a) > 0,
        f"决策历史已记录到 checkpoint,实际{decisions_a!r}",
    )

    print("\n[14] 智能体安全上限(max_steps 熔断,不死循环)")
    book_b = models.Book(title="未命名", brief="用于验证步数上限", target_words=8000)
    db.add(book_b)
    db.commit()
    db.refresh(book_b)

    orig_max_steps = app_settings.agent_max_steps
    app_settings.agent_max_steps = 3
    orchestrator.get_llm = lambda: StuckLLM()
    task_b = agent_generation.start_agent_generation(db, book_b.id)
    await book_generation._running[task_b.id]
    app_settings.agent_max_steps = orig_max_steps
    db.expire_all()
    task_b = db.get(models.GenerationTask, task_b.id)
    assert task_b is not None
    check(task_b.status == "error", f"达到步数上限后保留可重试错误,实际{task_b.status}")
    check(task_b.progress < 100 and "最大决策步数" in task_b.error, "熔断任务未标为100%完成")
    wb_b = db.query(models.Worldbuilding).filter_by(book_id=book_b.id).count()
    check(wb_b > 0, f"熔断前已执行过工具调用,设定条数{wb_b}")

    db.close()


def run() -> int:
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    print("\n===== 结果 =====")
    if FAILURES:
        print(f"存在 {len(FAILURES)} 处失败:")
        for f in FAILURES:
            print(" -", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(run())
