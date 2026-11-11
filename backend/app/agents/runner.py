"""Agent Runner(架构文档里的 Runner 模块)。

与具体"写小说"业务无关的通用 ReAct 循环机制:构建上下文 -> 调 LLM -> 判断
决策 -> 执行工具 -> 观察 -> 回到上下文,直到智能体调用"结束工具"或触碰安全
上限为止。哪个 Agent、用什么工具、什么算结束,由调用方(见
[orchestrator.py](orchestrator.py) 里定义的小说 Agent)通过参数传入,Runner 本身
不关心、也不导入任何小说领域的模块。

产出 (event, data) 元组:
  - thought:     模型给出的自然语言思考(可能为空,不总有)
  - decision:    即将执行的工具调用
  - log:         工具执行过程中的提示(沿用工具内部的 log 事件)
  - observation: 工具执行后的结构化结果(JSON),含重写次数上限等护栏生效后的
                 最终结果,便于追溯"模型看到的是什么"
  - done:        正常结束(成功调用了 finish_tool)
  - error:       本次运行失败(决策调用失败、耗尽决策步数或工具次数,可由任务重试)
"""

from __future__ import annotations

import itertools
import json
import logging
from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from ..config import settings
from ..llm.base import LLMClient
from ..llm.budget import ModelBudgetExceeded
from ..llm.retry import call_with_retry
from . import context
from .errors import log_failure, public_failure, redact_secrets
from .guardrails import (
    GuardrailLimits,
    apply_rewrite_limit,
    step_budget_exceeded,
    tool_call_budget_exceeded,
)
from .tool_registry import ToolError, ToolRegistry

logger = logging.getLogger(__name__)

Event = tuple[str, str]


def _review_key(args: dict[str, object]) -> str:
    target_id = args.get("target_id")
    normalized_id = str(target_id).strip() if target_id is not None else ""
    return f"{args.get('target_type') or ''}:{normalized_id}"


async def run(
    db: Session,
    book_id: str,
    *,
    llm: LLMClient,
    tool_registry: ToolRegistry,
    messages: list[dict[str, object]],
    finish_tool: str,
    review_tool: str,
    limits: GuardrailLimits,
) -> AsyncIterator[Event]:
    """驱动一次完整的 Agent 决策循环。

    messages 须已包含初始 system/user 消息(由调用方通过类似
    context.build_initial_messages 的方式构造好并传入),Runner 只负责在此
    基础上追加每一步的 assistant/tool 消息并做滑动窗口裁剪。
    """
    rewrite_counts: dict[str, int] = {}
    tool_call_count = 0
    step_starts: list[int] = []

    for step in itertools.count():
        if step_budget_exceeded(step, limits):
            break
        step_starts.append(len(messages))
        try:
            result = await call_with_retry(
                lambda: llm.chat_with_tools(
                    context.windowed_messages(messages, step_starts),
                    tool_registry.schemas(),
                ),
                attempts=settings.llm_retry_attempts,
                base_delay=settings.llm_retry_base_delay,
            )
        except Exception as exc:  # noqa: BLE001 - model adapter failure boundary
            log_failure(
                logger, exc, operation=f"agent decision step {step}", book_id=book_id
            )
            yield ("error", f"决策调用失败：{public_failure(exc)}")
            return

        if result.content:
            yield ("thought", result.content)

        if not result.tool_calls:
            # 模型未调用工具也未结束,提醒它必须通过工具推进或收尾。
            messages.append({"role": "assistant", "content": result.content or ""})
            messages.append(
                {
                    "role": "user",
                    "content": f"你必须通过调用工具推进创作,或调用 {finish_tool} 结束。请继续。",
                }
            )
            continue

        messages.append(
            {
                "role": "assistant",
                "content": result.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in result.tool_calls
                ],
            }
        )

        for call in result.tool_calls:
            tool = tool_registry.get(call.name)
            # 只读工具(如 get_book_state)不消耗生成预算:提示词鼓励模型每步都
            # 查一次状态,若与写入类工具共享同一个上限,长篇场景下预算可能被
            # 状态查询占满,导致还没写完就被熔断。未知工具名仍计入预算,避免
            # 模型靠瞎猜工具名钻空子。
            if tool is None or not tool.read_only:
                tool_call_count += 1
                if tool_call_budget_exceeded(tool_call_count, limits):
                    yield ("error", "已达到本次创作的最大工具调用次数上限,自动结束")
                    return

            yield (
                "decision",
                f"{call.name}({json.dumps(call.arguments, ensure_ascii=False)})",
            )

            review_key = (
                _review_key(call.arguments) if call.name == review_tool else None
            )
            try:
                if (
                    review_key is not None
                    and rewrite_counts.get(review_key, 0) >= limits.max_rewrites
                ):
                    # The review tool may persist an approval. Enforce the
                    # budget before it runs, even if the model ignores feedback.
                    observation: dict[str, object] = {"needs_revision": True}
                else:
                    observation = await tool_registry.dispatch(
                        db, book_id, call.name, call.arguments
                    )
            except ToolError as exc:
                observation = {"ok": False, "error": redact_secrets(str(exc))}
            except Exception as exc:  # noqa: BLE001 - isolate plugin/tool failures
                log_failure(
                    logger, exc, operation=f"agent tool step {step}", book_id=book_id
                )
                db.rollback()
                if isinstance(exc, ModelBudgetExceeded):
                    yield ("error", public_failure(exc))
                    return
                observation = {"ok": False, "error": public_failure(exc)}

            if review_key is not None and observation.get("needs_revision"):
                observation = apply_rewrite_limit(
                    observation, rewrite_counts, review_key, limits
                )

            logs = observation.get("logs")
            if isinstance(logs, list):
                for log_line in logs:
                    yield ("log", str(log_line))
            yield ("observation", json.dumps(observation, ensure_ascii=False))

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(observation, ensure_ascii=False),
                }
            )

            if call.name == finish_tool and observation.get("ok") is True:
                yield ("log", f"智能体决定结束创作:{call.arguments.get('reason', '')}")
                yield ("done", "")
                return

    yield ("error", "已达到最大决策步数,本次创作未完成,可重试继续")
