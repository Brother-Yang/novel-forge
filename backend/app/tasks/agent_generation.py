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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..agents import observability, orchestrator
from ..llm import supports_tool_calling
from . import book_generation as runtime

logger = logging.getLogger(__name__)

MAX_DECISION_LOG = 30


class UnsupportedBackend(Exception):
    """当前 LLM 后端不支持 function calling,不能使用自主决策模式。"""


def start_agent_generation(db: Session, book_id: str) -> models.GenerationTask:
    runtime.ensure_book_idle(db, book_id)
    if not supports_tool_calling():
        raise UnsupportedBackend(
            "当前 LLM 后端不支持自主决策(function calling),请改用「一句话出书」固定流程"
        )
    task = models.GenerationTask(
        book_id=book_id,
        type="agent",
        status="running",
        progress=0,
        checkpoint_json="{}",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    runtime._launch(task.id)
    return task


def _append_decision(task: models.GenerationTask, entry: str) -> None:
    ckpt = runtime.load_checkpoint(task)
    history = ckpt.get("decisions")
    if not isinstance(history, list):
        history = []
    history.append(entry)
    runtime.merge_checkpoint(task, decisions=history[-MAX_DECISION_LOG:])


def _update_progress(db: Session, task: models.GenerationTask) -> None:
    total = (
        db.scalar(
            select(func.count())
            .select_from(models.Chapter)
            .where(models.Chapter.book_id == task.book_id)
        )
        or 0
    )
    if not total:
        return
    done = (
        db.scalar(
            select(func.count())
            .select_from(models.Chapter)
            .where(
                models.Chapter.book_id == task.book_id,
                models.Chapter.status == "done",
            )
        )
        or 0
    )
    task.progress = min(95, 5 + int(done / total * 90))


_TRACED_EVENTS = ("decision", "thought", "log", "observation")


def _tool_name_from_decision(data: str) -> str | None:
    """从 "write_chapter({...})" 这类 decision 文本里取出工具名。"""
    name = data.split("(", 1)[0].strip()
    return name or None


async def run_agent_task(db: Session, task: models.GenerationTask) -> None:
    step_index = 0
    try:
        async for ev, data in orchestrator.run_agent(db, task.book_id):
            runtime.ensure_running(db, task)
            if ev == "error":
                raise RuntimeError(data)
            logger.info("agent task %s [%s] %s", task.id, ev, data)
            if ev in _TRACED_EVENTS:
                step_index += 1
                tool_name = _tool_name_from_decision(data) if ev == "decision" else None
                observability.record_step(
                    db, task.id, step_index, ev, data, tool_name=tool_name
                )
                _append_decision(task, f"[{ev}] {data}")
                _update_progress(db, task)
                db.commit()
            if ev == "done":
                task.status = "done"
                task.progress = 100
                db.commit()
    except runtime.Paused:
        logger.info("agent task %s paused", task.id)
        return
    except Exception as exc:
        logger.exception("agent task %s failed", task.id)
        db.refresh(task)
        step_index += 1
        observability.record_step(db, task.id, step_index, "error", str(exc))
        if task.status == "running":
            task.status = "error"
            task.error = str(exc)
            db.commit()
