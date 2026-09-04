"""智能体执行轨迹的落库与查询(架构文档里的 Observability 模块)。

GenerationTask.checkpoint_json 里的 decisions 字段只保留最近 30 条,给 UI
快速预览用;这里额外落一份完整、不截断的记录到独立表([models.AgentTraceEntry](../models.py)),
供事后排查"Agent 到底做了什么、为什么做出某个决定"。
"""

from __future__ import annotations

from sqlalchemy import select
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
    db.add(
        models.AgentTraceEntry(
            task_id=task_id,
            step_index=step_index,
            kind=kind,
            tool_name=tool_name,
            payload=payload,
        )
    )
    db.commit()


def list_trace(
    db: Session, task_id: str, limit: int = 500
) -> list[models.AgentTraceEntry]:
    # 主键已改为 UUID 字符串,不再具备插入顺序语义;step_index 由调用方
    # (agent_generation.py) 对同一任务严格单调递增,用它排序才能还原真实执行顺序。
    return list(
        db.scalars(
            select(models.AgentTraceEntry)
            .where(models.AgentTraceEntry.task_id == task_id)
            .order_by(models.AgentTraceEntry.step_index)
            .limit(limit)
        ).all()
    )
