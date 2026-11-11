"""智能体可调用的工具:把 pipeline.py 的既有生成能力包装成结构化的
"调用参数 -> 观察结果"接口,并新增状态感知(get_book_state)与质检反思
(review_content)能力,供 orchestrator 的决策循环使用。

每个工具:
- 输入 book_id(由编排层绑定,不暴露给模型选)+ 模型给出的其它参数。
- 参数非法或目标不属于本书时抛出 ToolError,由编排层转换成"观察结果"
  回传给模型,而不是让整个任务崩溃。
- 对"一次性"结构(设定/人物/大纲/某卷章纲)做幂等保护:已存在则跳过,
  避免模型重复调用产生冗余数据。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..llm import get_llm
from ..llm.retry import call_with_retry
from . import pipeline, planner, prompts
from .completion import chapter_is_complete, require_book_complete
from .state import BookNotFound, load_state


class ToolError(Exception):
    """工具参数非法或目标不存在/不属于本书;应作为观察结果回传给模型。"""


async def _consume_pipeline(gen: AsyncIterator[tuple[str, str]]) -> list[str]:
    logs: list[str] = []
    async for ev, data in gen:
        if ev == "error":
            raise ToolError(data)
        if ev == "log":
            logs.append(data)
    return logs


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _clamp_int(value: object, default: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, _as_int(value, default)))


def _require_str(value: object, field: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ToolError(f"参数 {field} 不能为空,实际收到 {value!r}") from None
    return text


# ---------- 状态感知 ----------
async def tool_get_book_state(
    db: Session, book_id: str, _args: dict[str, object]
) -> dict[str, object]:
    try:
        state = load_state(db, book_id)
    except BookNotFound:
        raise ToolError("书籍不存在") from None
    return state.to_dict()


# ---------- 创作工具(复用 pipeline.py,加幂等保护) ----------
async def tool_write_worldbuilding(
    db: Session, book_id: str, _args: dict[str, object]
) -> dict[str, object]:
    existing = (
        db.scalar(
            select(func.count())
            .select_from(models.Worldbuilding)
            .where(models.Worldbuilding.book_id == book_id)
        )
        or 0
    )
    if existing:
        return {"ok": False, "skipped": True, "message": f"已存在 {existing} 条设定,无需重复生成"}
    logs = await _consume_pipeline(pipeline.gen_worldbuilding(db, book_id))
    return {"ok": True, "logs": logs}


async def tool_create_characters(
    db: Session, book_id: str, args: dict[str, object]
) -> dict[str, object]:
    existing = (
        db.scalar(
            select(func.count())
            .select_from(models.Character)
            .where(models.Character.book_id == book_id)
        )
        or 0
    )
    if existing:
        return {"ok": False, "skipped": True, "message": f"已存在 {existing} 个人物,无需重复生成"}
    count = _clamp_int(args.get("count"), 5, 3, 8)
    logs = await _consume_pipeline(pipeline.gen_characters(db, book_id, count))
    return {"ok": True, "logs": logs}


async def tool_create_outline(
    db: Session, book_id: str, args: dict[str, object]
) -> dict[str, object]:
    existing = (
        db.scalar(
            select(func.count())
            .select_from(models.Volume)
            .where(models.Volume.book_id == book_id)
        )
        or 0
    )
    if existing:
        return {"ok": False, "skipped": True, "message": f"已存在 {existing} 卷大纲,无需重复生成"}
    volume_count = _clamp_int(args.get("volume_count"), 3, 1, 12)
    logs = await _consume_pipeline(pipeline.gen_outline(db, book_id, volume_count))
    return {"ok": True, "logs": logs}


async def tool_create_chapter_outlines(
    db: Session, book_id: str, args: dict[str, object]
) -> dict[str, object]:
    volume_id = _require_str(args.get("volume_id"), "volume_id")
    volume = db.get(models.Volume, volume_id)
    if not volume or volume.book_id != book_id:
        raise ToolError(f"volume_id={volume_id} 不存在或不属于本书")
    existing = (
        db.scalar(
            select(func.count())
            .select_from(models.Chapter)
            .where(models.Chapter.volume_id == volume_id)
        )
        or 0
    )
    if existing:
        return {"ok": False, "skipped": True, "message": f"该卷已有 {existing} 章大纲,无需重复生成"}
    chapter_count = _clamp_int(args.get("chapter_count"), 10, 1, 20)
    logs = await _consume_pipeline(
        pipeline.gen_chapter_outlines(db, volume_id, chapter_count)
    )
    return {"ok": True, "logs": logs}


async def tool_write_chapter(
    db: Session, book_id: str, args: dict[str, object]
) -> dict[str, object]:
    chapter_id = _require_str(args.get("chapter_id"), "chapter_id")
    chapter = db.get(models.Chapter, chapter_id)
    if not chapter or chapter.book_id != book_id:
        raise ToolError(f"chapter_id={chapter_id} 不存在或不属于本书")
    if chapter_is_complete(chapter):
        # 高风险操作护栏:覆盖已完成正文会丢弃已有内容,自动拦截而非直接执行。
        # 只有 review_content 刚对这一章给出 needs_revision=true 时才放行一次。
        if not chapter.revision_approved:
            raise ToolError(
                "该章节正文已完成,直接覆盖会丢弃已有内容;请先调用 review_content "
                + "评审该章节(target_type=chapter),只有返回 needs_revision=true 后才允许重写。"
            )
        chapter.revision_approved = False
        db.commit()
    words = _clamp_int(args.get("words"), 2500, 1000, 6000)
    is_ending_raw = args.get("is_ending")
    is_ending = bool(is_ending_raw) if is_ending_raw is not None else None
    logs = await _consume_pipeline(
        pipeline.gen_chapter_body(db, chapter_id, words, is_ending=is_ending)
    )
    db.refresh(chapter)
    return {"ok": True, "logs": logs, "word_count": chapter.word_count, "status": chapter.status}


# ---------- 重新规划(剧情走偏时主动触发) ----------
async def tool_replan_volume(
    db: Session, book_id: str, args: dict[str, object]
) -> dict[str, object]:
    volume_id = _require_str(args.get("volume_id"), "volume_id")
    volume = db.get(models.Volume, volume_id)
    if not volume or volume.book_id != book_id:
        raise ToolError(f"volume_id={volume_id} 不存在或不属于本书")
    reason = str(args.get("reason") or "").strip()
    if not reason:
        raise ToolError("reason 不能为空,需说明剧情具体如何偏离原大纲")
    logs = await _consume_pipeline(
        pipeline.replan_volume_chapters(db, volume_id, reason)
    )
    return {"ok": True, "logs": logs}


# ---------- 质检反思(新增能力) ----------
def _load_review_target(
    db: Session, book_id: str, target_type: str, target_id: object
) -> tuple[str, str]:
    if target_type == "outline":
        volume = db.get(models.Volume, _require_str(target_id, "target_id"))
        if not volume or volume.book_id != book_id:
            raise ToolError("待评审的卷不存在或不属于本书")
        return f"《{volume.title}》分卷大纲", volume.summary
    if target_type == "character":
        character = db.get(models.Character, _require_str(target_id, "target_id"))
        if not character or character.book_id != book_id:
            raise ToolError("待评审的人物不存在或不属于本书")
        return f"人物《{character.name}》", character.md_content
    if target_type == "chapter":
        chapter = db.get(models.Chapter, _require_str(target_id, "target_id"))
        if not chapter or chapter.book_id != book_id:
            raise ToolError("待评审的章节不存在或不属于本书")
        body = chapter.content.body if chapter.content else ""
        return f"章节《{chapter.title}》正文", body
    raise ToolError(f"未知的评审对象类型:{target_type}")


def _parse_review(raw: str) -> dict[str, object]:
    try:
        obj = planner.extract_json_object(raw)
    except ValueError:
        return {"needs_revision": False, "feedback": "评审输出解析失败,默认视为通过"}
    return {
        "needs_revision": bool(obj.get("needs_revision", False)),
        "feedback": str(obj.get("feedback", "")),
    }


async def tool_review_content(
    db: Session, book_id: str, args: dict[str, object]
) -> dict[str, object]:
    target_type = str(args.get("target_type") or "")
    label, text = _load_review_target(db, book_id, target_type, args.get("target_id"))
    if not text.strip():
        return {"needs_revision": True, "feedback": f"{label}内容为空,需要先生成内容"}
    llm = get_llm()
    raw = await call_with_retry(
        lambda: llm.chat(
            prompts.REVIEW_USER.format(label=label, content=text[:3000]),
            system=prompts.REVIEW_SYSTEM,
        ),
        attempts=settings.llm_retry_attempts,
        base_delay=settings.llm_retry_base_delay,
    )
    result = _parse_review(raw)
    if target_type == "chapter" and result.get("needs_revision"):
        chapter = db.get(models.Chapter, _require_str(args.get("target_id"), "target_id"))
        if chapter and chapter.book_id == book_id and chapter.status == "done":
            chapter.revision_approved = True
            db.commit()
    return result


# ---------- 控制 ----------
async def tool_finish_book(
    db: Session, book_id: str, args: dict[str, object]
) -> dict[str, object]:
    try:
        require_book_complete(db, book_id)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "reason": str(args.get("reason", ""))}


# ---------- Tool Registry(架构文档里的 Tool 模块) ----------
ToolHandler = Callable[[Session, str, dict[str, object]], Awaitable[dict[str, object]]]


@dataclass(frozen=True)
class Tool:
    """一个可供 Agent 调用的工具:OpenAI function-calling schema + 处理函数。

    read_only 只做元数据标注(当前工具里只有 get_book_state 为 True),不接
    审批逻辑——为 Guardrails 未来扩展"高风险操作审批"预留位置。
    """

    name: str
    description: str
    parameters: dict[str, object]
    handler: ToolHandler
    read_only: bool = False

    def schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """所有工具统一注册、统一分发,调用方不应该写

        if name == "write_chapter": ...
        elif name == "create_outline": ...

    这类硬编码分支,而是 registry.get(name)/registry.dispatch(...)。
    """

    def __init__(self) -> None:
        super().__init__()
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, object]]:
        return [t.schema() for t in self._tools.values()]

    async def dispatch(
        self, db: Session, book_id: str, name: str, args: dict[str, object]
    ) -> dict[str, object]:
        tool = self.get(name)
        if tool is None:
            raise ToolError(f"未知工具:{name}")
        return await tool.handler(db, book_id, args)


registry = ToolRegistry()

registry.register(
    Tool(
        name="get_book_state",
        description=(
            "查询当前书籍的创作进度:设定/人物/卷/章/字数/剧情线程状态。"
            + "建议每次决策前先调用了解现状。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=tool_get_book_state,
        read_only=True,
    )
)
registry.register(
    Tool(
        name="write_worldbuilding",
        description="生成世界观设定条目(仅在尚未生成设定时调用一次)。",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=tool_write_worldbuilding,
    )
)
registry.register(
    Tool(
        name="create_characters",
        description="生成主要人物(仅在尚未生成人物时调用一次)。",
        parameters={
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "人物数量,3-8"}
            },
            "required": ["count"],
        },
        handler=tool_create_characters,
    )
)
registry.register(
    Tool(
        name="create_outline",
        description="生成全书分卷大纲与剧情线程(仅在尚未生成大纲时调用一次)。",
        parameters={
            "type": "object",
            "properties": {
                "volume_count": {"type": "integer", "description": "卷数,1-12"}
            },
            "required": ["volume_count"],
        },
        handler=tool_create_outline,
    )
)
registry.register(
    Tool(
        name="create_chapter_outlines",
        description="为指定卷生成逐章细纲(每卷仅需调用一次)。",
        parameters={
            "type": "object",
            "properties": {
                "volume_id": {
                    "type": "string",
                    "description": "目标卷 id,来自 get_book_state",
                },
                "chapter_count": {"type": "integer", "description": "该卷章数,1-20"},
            },
            "required": ["volume_id", "chapter_count"],
        },
        handler=tool_create_chapter_outlines,
    )
)
registry.register(
    Tool(
        name="write_chapter",
        description="为指定章节生成/重写正文;该章须已有细纲。",
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {
                    "type": "string",
                    "description": "目标章节 id,来自 get_book_state 返回的 volumes[].chapters[].id,不能凭空编造",
                },
                "words": {"type": "integer", "description": "目标字数,1000-6000"},
                "is_ending": {
                    "type": "boolean",
                    "description": (
                        "是否是全书最后一章(结局章)。若为 true,会要求写出"
                        + "明确、完整的结局,主线冲突收束、不留开放式悬念。不填时按章节顺序"
                        + "自动判断是否为末章。"
                    ),
                },
            },
            "required": ["chapter_id", "words"],
        },
        handler=tool_write_chapter,
    )
)
registry.register(
    Tool(
        name="replan_volume",
        description=(
            "当发现某一卷实际写出来的剧情已明显偏离原大纲时,重新规划该卷【还没写"
            + "正文】的章节细纲(已完成正文的章节与分卷大纲本身不受影响)。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "volume_id": {"type": "string", "description": "目标卷 id"},
                "reason": {
                    "type": "string",
                    "description": "为什么需要重新规划,简要说明剧情具体如何偏离",
                },
            },
            "required": ["volume_id", "reason"],
        },
        handler=tool_replan_volume,
    )
)
registry.register(
    Tool(
        name="review_content",
        description="评审已生成的大纲/人物/章节正文是否存在明显问题,返回是否需要修改与具体建议。",
        parameters={
            "type": "object",
            "properties": {
                "target_type": {
                    "type": "string",
                    "enum": ["outline", "character", "chapter"],
                },
                "target_id": {
                    "type": "string",
                    "description": "对应的卷/人物/章节 id,来自 get_book_state",
                },
            },
            "required": ["target_type", "target_id"],
        },
        handler=tool_review_content,
        read_only=True,
    )
)
registry.register(
    Tool(
        name="finish_book",
        description="全部章节正文完成且无待处理问题后,调用此工具结束本次创作。",
        parameters={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "结束理由的简短说明"}
            },
            "required": ["reason"],
        },
        handler=tool_finish_book,
    )
)


# ---------- 向后兼容的模块级接口 ----------
# orchestrator.py/runner.py 与既有测试都是通过这两个名字使用工具层的,保持
# 签名不变;内部实现改为委托给上面的 ToolRegistry。
TOOLS: list[dict[str, object]] = registry.schemas()


async def dispatch(
    db: Session, book_id: str, name: str, args: dict[str, object]
) -> dict[str, object]:
    return await registry.dispatch(db, book_id, name, args)
