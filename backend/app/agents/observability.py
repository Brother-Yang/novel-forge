"""智能体执行轨迹的落库与查询(架构文档里的 Observability 模块)。

GenerationTask.checkpoint_json 里的 decisions 字段只保留最近 30 条,给 UI
快速预览用;这里额外落一份完整、不截断的记录到独立表([models.AgentTraceEntry](../models.py)),
供事后排查"Agent 到底做了什么、为什么做出某个决定"。
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import models


def record_step(
    db: Session,
    task_id: str,
    step_index: int,
    kind: str,
    payload: str,
    *,
    tool_name: str | None = None,
) -> None:
    """Stage the trace and sequence increment in the caller's transaction."""
    # Updating the task row serializes writers for this task until commit.
    # A retried run can reuse step_index, so cursors use this persisted counter.
    db.flush()
    sequence = db.scalars(
        update(models.GenerationTask)
        .where(models.GenerationTask.id == task_id)
        .values(trace_sequence=models.GenerationTask.trace_sequence + 1)
        .returning(models.GenerationTask.trace_sequence)
        .execution_options(synchronize_session=False)
    ).one()
    db.add(
        models.AgentTraceEntry(
            task_id=task_id,
            sequence=sequence,
            step_index=step_index,
            kind=kind,
            tool_name=tool_name,
            payload=payload,
        )
    )


def list_trace(
    db: Session, task_id: str, limit: int = 500
) -> list[models.AgentTraceEntry]:
    return list_trace_page(db, task_id, limit, after=0)[0]


def list_trace_page(
    db: Session,
    task_id: str,
    limit: int,
    *,
    before: int | None = None,
    after: int | None = None,
) -> tuple[list[models.AgentTraceEntry], bool]:
    entry = models.AgentTraceEntry
    query = select(entry).where(entry.task_id == task_id)
    if after is not None:
        query = query.where(entry.sequence > after).order_by(entry.sequence)
    else:
        if before is not None:
            query = query.where(entry.sequence < before)
        query = query.order_by(entry.sequence.desc())

    entries = list(db.scalars(query.limit(limit + 1)))
    has_more = len(entries) > limit
    entries = entries[:limit]
    if after is None:
        entries.reverse()
    return entries, has_more
