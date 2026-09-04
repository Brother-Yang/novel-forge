"""LLM 调用的通用重试封装。

只对"重试后有机会恢复"的失败重试:网络层错误(超时/连接失败)与限流/服务端
临时错误(HTTP 429/5xx)。参数错误、认证失败等 4xx(除 429)重试也不会成功,
直接向上抛出,避免浪费调用次数、拖长失败反馈时间。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return isinstance(exc, httpx.TransportError)


async def call_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
) -> T:
    """执行 fn(),对网络错误/超时/429/5xx 按指数退避重试,最多 attempts 次。

    其它异常(如参数错误、鉴权失败)或重试耗尽后,原样向上抛出最后一次的异常。
    """
    for attempt in range(attempts):
        try:
            return await fn()
        except Exception as exc:
            if not _is_retryable(exc) or attempt == attempts - 1:
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                "LLM 调用失败,%.1fs 后重试(第 %d/%d 次):%s",
                delay,
                attempt + 1,
                attempts,
                exc,
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
