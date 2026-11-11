"""智能体自主决策的全书生成任务运行时(type=agent)。

与 novel_generation.py 的固定流水线并存、互不影响:novel_generation 由 Python
硬编码顺序决定每一步做什么,agent_generation 则把"下一步做什么"交给 LLM 通过
function calling 自主决策(见 agents/orchestrator.py)。仅在 LLM 后端支持
function calling 时可用,否则调用方应改走一句话出书(type=novel)。

决策过程没有 SSE 直连通道(与 book/novel 任务一致,均通过 GenerationTask
轮询获取状态)。每一步决策会记录进 checkpoint_json 的 decisions 字段(只留
最近 MAX_DECISION_LOG 条,给 UI 快速预览用),并落日志;同时额外写入
agent_trace_entries 表(见 agents/observability.py)保存完整、不截断的
执行轨迹,便于事后排查智能体做了什么、为什么这么做。
"""

from __future__ import annotations

import logging
from typing import cast

from sqlalchemy.orm import Session

from .. import models
from ..agents import observability, orchestrator
from ..agents.completion import (
    chapter_counts,
    require_book_complete,
)
from ..agents.errors import GenerationError, redact_secrets
from . import book_generation as runtime

logger = logging.getLogger(__name__)

MAX_DECISION_LOG = 30


def _append_decision(task: models.GenerationTask, entry: str) -> None:
    ckpt = runtime.load_checkpoint(task)
    raw = ckpt.get("decisions")
    history: list[str] = []
    if isinstance(raw, list):
        for item in cast(list[object], raw):
            history.append(str(item))
    history.append(entry)
    runtime.merge_checkpoint(task, decisions=history[-MAX_DECISION_LOG:])


def _update_progress(db: Session, task: models.GenerationTask) -> None:
    total, done = chapter_counts(db, task.book_id)
    if not total:
        return
    task.progress = min(95, 5 + int(done / total * 90))


_TRACED_EVENTS = ("decision", "thought", "log", "observation")


def _tool_name_from_decision(data: str) -> str | None:
    """从 "write_chapter({...})" 这类 decision 文本里取出工具名。"""
    name = data.split("(", 1)[0].strip()
    return name or None


async def run_agent_task(db: Session, task: models.GenerationTask) -> None:
    task_id, book_id = task.id, task.book_id
    step_index = 0
    try:
        async for ev, data in orchestrator.run_agent(db, task.book_id):
            runtime.ensure_running(db, task)
            data = redact_secrets(data)
            if ev == "error":
                raise GenerationError(data)
            logger.info(
                "agent task %s book %s [%s] step=%s", task_id, book_id, ev, step_index
            )
            if ev in _TRACED_EVENTS:
                step_index += 1
                tool_name = _tool_name_from_decision(data) if ev == "decision" else None
                _append_decision(task, f"[{ev}] {data}")
                _update_progress(db, task)
                observability.record_step(
                    db, task.id, step_index, ev, data, tool_name=tool_name
                )
                db.commit()
            if ev == "done":
                require_book_complete(db, task.book_id)
                task.status = "done"
                task.progress = 100
                db.commit()
                return
        raise GenerationError("智能体未确认完成即停止,可重试继续")
    except runtime.Paused:
        db.rollback()
        logger.info("agent task %s paused", task.id)
        return
    except Exception as exc:  # noqa: BLE001 - persist failure and trace for task retry
        runtime.fail_task(
            db,
            exc,
            task_id=task_id,
            book_id=book_id,
            operation="agent run",
            trace_step=step_index + 1,
        )


runtime.register_task_runner("agent", run_agent_task)
