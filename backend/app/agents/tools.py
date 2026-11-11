"""智能体可调用的工具:把 pipeline.py 的既有生成能力包装成结构化的
"调用参数 -> 观察结果"接口,并新增状态感知(get_book_state)与质检反思
(review_content)能力,供 orchestrator 的决策循环使用。

每个工具:
- 输入 book_id(由编排层绑定,不暴露给模型选)+ 模型给出的其它参数。
- 参数非法或目标不属于本书时抛出 ToolError,由编排层转换成"观察结果"
  回传给模型,而不是让整个任务崩溃。
- 对"一次性"结构(设定/人物/大纲/某卷章纲)做幂等保护:完整时才跳过,
  避免模型重复调用产生冗余数据。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..llm import get_llm
from ..llm.retry import call_with_retry
from . import chapter_writes, pipeline, planner, prompts, stages
from .chapter_versions import content_version
from .completion import require_book_complete
from .errors import GenerationError
from .state import CHAPTER_WINDOW_LIMIT, BookNotFound, load_state
from .tool_registry import Tool, ToolError, ToolRegistry


async def _consume_pipeline(gen: AsyncIterator[tuple[str, str]]) -> dict[str, object]:
    logs: list[str] = []
    skipped: str | None = None
    async for ev, data in gen:
        if ev == "error":
            raise ToolError(data)
        if ev == "log":
            logs.append(data)
        if ev == "skipped":
            skipped = data
    if skipped is not None:
        return {"ok": False, "skipped": True, "message": skipped}
    return {"ok": True, "logs": logs}


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
    db: Session, book_id: str, args: dict[str, object]
) -> dict[str, object]:
    offset = args.get("chapter_offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ToolError("chapter_offset 必须是非负整数")
    try:
        state = load_state(db, book_id, chapter_offset=offset)
    except BookNotFound:
        raise ToolError("书籍不存在") from None
    return state.to_dict()


# ---------- 创作工具(复用 pipeline.py,加幂等保护) ----------
async def tool_write_worldbuilding(
    db: Session, book_id: str, _args: dict[str, object]
) -> dict[str, object]:
    return await _consume_pipeline(
        pipeline.gen_worldbuilding(db, book_id, recovery=stages.Recovery())
    )


async def tool_create_characters(
    db: Session, book_id: str, args: dict[str, object]
) -> dict[str, object]:
    count = _clamp_int(args.get("count"), 5, 3, planner.CHARACTER_MAX)
    return await _consume_pipeline(
        pipeline.gen_characters(db, book_id, count, recovery=stages.Recovery())
    )


async def tool_create_outline(
    db: Session, book_id: str, args: dict[str, object]
) -> dict[str, object]:
    volume_count = _as_int(args.get("volume_count"), 3)
    if not 1 <= volume_count <= planner.VOLUME_MAX:
        raise ToolError(
            f"volume_count 必须在 1..{planner.VOLUME_MAX} 之间，不能截断卷数"
        )
    return await _consume_pipeline(
        pipeline.gen_outline(db, book_id, volume_count, recovery=stages.Recovery())
    )


async def tool_create_chapter_outlines(
    db: Session, book_id: str, args: dict[str, object]
) -> dict[str, object]:
    volume_id = _require_str(args.get("volume_id"), "volume_id")
    volume = db.get(models.Volume, volume_id)
    if not volume or volume.book_id != book_id:
        raise ToolError(f"volume_id={volume_id} 不存在或不属于本书")
    chapter_count = _as_int(args.get("chapter_count"), 10)
    if not 1 <= chapter_count <= planner.CHAPTERS_PER_VOLUME_MAX:
        raise ToolError(
            f"chapter_count 必须在 1..{planner.CHAPTERS_PER_VOLUME_MAX} 之间，请增加卷数分批规划"
        )
    return await _consume_pipeline(
        pipeline.gen_chapter_outlines(
            db, volume_id, chapter_count, recovery=stages.Recovery()
        )
    )


async def tool_write_chapter(
    db: Session, book_id: str, args: dict[str, object]
) -> dict[str, object]:
    chapter_id = _require_str(args.get("chapter_id"), "chapter_id")
    chapter = db.get(models.Chapter, chapter_id)
    if not chapter or chapter.book_id != book_id:
        raise ToolError(f"chapter_id={chapter_id} 不存在或不属于本书")
    words = _clamp_int(
        args.get("words"), 2500, 1000, schemas.CHAPTER_GENERATION_WORDS_MAX
    )
    is_ending_raw = args.get("is_ending")
    is_ending = bool(is_ending_raw) if is_ending_raw is not None else None
    result = await _consume_pipeline(
        pipeline.gen_chapter_body(
            db, chapter_id, words, is_ending=is_ending, require_revision_approval=True
        )
    )
    db.refresh(chapter)
    return {
        **result,
        "word_count": chapter.word_count,
        "status": chapter.status,
    }


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
    return await _consume_pipeline(
        pipeline.replan_volume_chapters(db, volume_id, reason)
    )


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
    reviewed_version = content_version(text) if target_type == "chapter" else None
    db.rollback()
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
    if reviewed_version is not None:
        try:
            chapter_writes.set_revision_approval(
                db,
                book_id,
                _require_str(args.get("target_id"), "target_id"),
                reviewed_version,
                approved=result.get("needs_revision") is True,
            )
        except GenerationError as exc:
            raise ToolError(str(exc)) from exc
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


registry = ToolRegistry()

registry.register(
    Tool(
        name="get_book_state",
        description=(
            f"查询全书创作进度和最多 {CHAPTER_WINDOW_LIMIT} 条章节。窗口优先显示未完成章，"
            "可从 chapter_window.next_offset 继续翻页。写入后用空参数重新获取最早待写章。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "chapter_offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "可选，从当前 chapter_window.next_offset 继续。省略时从最新窗口开头读取。",
                },
            },
            "required": [],
        },
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
                "count": {
                    "type": "integer",
                    "description": f"人物数量,3-{planner.CHARACTER_MAX}",
                }
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
                "volume_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": planner.VOLUME_MAX,
                    "description": f"卷数,1-{planner.VOLUME_MAX};长篇优先增加卷数",
                }
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
                "chapter_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": planner.CHAPTERS_PER_VOLUME_MAX,
                    "description": f"该卷章数,1-{planner.CHAPTERS_PER_VOLUME_MAX}",
                },
            },
            "required": ["volume_id", "chapter_count"],
        },
        handler=tool_create_chapter_outlines,
    )
)
registry.register(
    Tool(
        name="write_chapter",
        description="为指定章节生成或重写正文。归卷章应先准备细纲，未归卷章可按前情写作。",
        parameters={
            "type": "object",
            "properties": {
                "chapter_id": {
                    "type": "string",
                    "description": "目标章节 id，来自 get_book_state 的 volumes[].chapters、loose_chapters 或 final_chapter",
                },
                "words": {
                    "type": "integer",
                    "description": f"目标字数,1000-{schemas.CHAPTER_GENERATION_WORDS_MAX}",
                },
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
