"""小说 Agent 的定义(架构文档里的 Agent Core 模块)。

这里只回答"小说 Agent 是什么"——用哪个 system prompt、能调用哪些工具、
哪个工具算"结束"、哪个工具算"评审"、安全上限是多少——具体"如何持续运行
这个循环"的通用机制在 [runner.py](runner.py) 里,与小说业务无关。

取代 novel_generation.py 里硬编码的固定顺序状态机:Runner 驱动的循环由
LLM 通过 function calling 自主决定下一步调用哪个创作工具,直到声明完成
(finish_book)或触碰安全上限为止。

产出 (event, data) 元组,供任务运行时落日志/写入 checkpoint,含义见
[runner.py](runner.py) 顶部说明;这里额外可能产出:
  - error: 书籍不存在、或当前 LLM 后端不支持 function calling。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from ..llm import get_llm, supports_tool_calling
from . import book_plans, chapter_writes, context, planner, runner, tools
from .guardrails import GuardrailLimits

Event = tuple[str, str]

FINISH_TOOL = "finish_book"
REVIEW_TOOL = "review_content"


async def run_agent(db: Session, book_id: str) -> AsyncIterator[Event]:
    book = db.get(models.Book, book_id)
    if not book:
        yield ("error", "书籍不存在")
        return

    if not supports_tool_calling():
        yield ("error", "当前 LLM 后端不支持自主决策(function calling),请改用「一句话出书」固定流程")
        return

    chapter_writes.clear_book_revision_approvals(db, book_id)
    if planner.is_placeholder_title(book.title, book.brief):
        expected, plan = await book_plans.prepare(db, book)
        book = book_plans.stage_if_current(db, expected, plan)
        db.commit()

    limits = GuardrailLimits(
        max_steps=settings.agent_max_steps,
        max_tool_calls=settings.agent_max_tool_calls,
        max_rewrites=settings.agent_max_rewrites,
    )
    async for event in runner.run(
        db,
        book_id,
        llm=get_llm(),
        tool_registry=tools.registry,
        messages=context.build_initial_messages(book),
        finish_tool=FINISH_TOOL,
        review_tool=REVIEW_TOOL,
        limits=limits,
    ):
        yield event
