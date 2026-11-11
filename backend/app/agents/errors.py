"""Distinguish manuscript validation errors from unsafe provider details."""

import logging
import traceback
from pathlib import Path

import httpx

from ..config import settings
from ..llm.base import ModelResponseError
from ..llm.budget import ModelBudgetExceeded


class GenerationError(ValueError):
    """A validation/operation message constructed for the user by the application."""


def redact_secrets(text: str) -> str:
    for secret in (
        settings.openai_api_key,
        settings.dashscope_api_key,
        settings.access_password,
        settings.session_secret,
    ):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def public_failure(exc: Exception) -> str:
    if isinstance(exc, (GenerationError, ModelBudgetExceeded, ModelResponseError)):
        return redact_secrets(str(exc))
    if isinstance(exc, httpx.TimeoutException):
        return "模型请求网络超时，请稍后重试"
    if isinstance(exc, httpx.HTTPStatusError):
        return (
            f"模型服务请求失败（HTTP {exc.response.status_code}），请检查配置或稍后重试"
        )
    if isinstance(exc, httpx.TransportError):
        return "无法连接模型服务，请检查连接后重试"
    return "生成失败，请重试；详情见服务端日志"


def log_failure(
    logger: logging.Logger,
    exc: Exception,
    *,
    operation: str,
    book_id: str,
    task_id: str | None = None,
    chapter_id: str | None = None,
) -> None:
    # URLs, authorization headers and model outputs can appear in exception
    # messages. Keep source locations/status, without formatting the exception.
    stack = " > ".join(
        f"{Path(frame.f_code.co_filename).name}:{line}:{frame.f_code.co_name}"
        for frame, line in traceback.walk_tb(exc.__traceback__)
    )
    logger.log(
        logging.WARNING
        if isinstance(exc, (GenerationError, ModelBudgetExceeded))
        else logging.ERROR,
        "generation failed operation=%s book=%s task=%s chapter=%s type=%s status=%s stack=%s",
        redact_secrets(operation),
        book_id,
        task_id,
        chapter_id,
        type(exc).__name__,
        exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None,
        stack,
    )
