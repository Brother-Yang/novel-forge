"""Validate and save generation requests before starting their background work."""

from typing import Literal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .. import models
from ..agents.errors import GenerationError
from ..llm import supports_tool_calling
from . import book_generation as runtime

type NewBookTask = Literal["novel", "agent"]
type BookTask = Literal["book", "novel", "agent"]


class UnsupportedBackend(GenerationError):
    """The selected provider cannot start an agent task."""


def _check_provider(task_type: BookTask) -> None:
    if task_type == "agent" and not supports_tool_calling():
        raise UnsupportedBackend(
            "当前 LLM 后端不支持自主决策(function calling),请改用「一句话出书」固定流程"
        )


def _save_and_launch(
    db: Session, book: models.Book, task_type: BookTask
) -> models.GenerationTask:
    # No await from validation to launch: every admission runs on the single
    # ASGI event loop. Flush for IDs, then commit the book and task together.
    try:
        db.add(book)
        db.flush()
        task = models.GenerationTask(book_id=book.id, type=task_type, status="running")
        db.add(task)
        db.flush()
        task_id = task.id
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    runtime.launch(task_id)
    return task


def start_new_book(
    db: Session, book: models.Book, task_type: NewBookTask
) -> models.GenerationTask:
    brief = (book.brief or "").strip()
    if not brief:
        raise GenerationError("请填写一段故事想法")
    _check_provider(task_type)
    runtime.ensure_generation_capacity()
    book.brief = brief
    return _save_and_launch(db, book, task_type)


def start_existing_book(
    db: Session, book: models.Book, task_type: BookTask
) -> models.GenerationTask:
    runtime.ensure_book_idle(db, book.id)
    _check_provider(task_type)
    if task_type != "book":
        brief = (book.brief or "").strip() or (book.intro or "").strip()
        if not brief:
            raise GenerationError("请先填写简介,或从首页使用一句话出书")
        if not (book.brief or "").strip():
            book.brief = brief
    return _save_and_launch(db, book, task_type)
