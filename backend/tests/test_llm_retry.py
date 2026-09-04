"""app/llm/retry.py::call_with_retry 测试:网络错误/429/5xx 按退避重试,
其它错误(4xx 参数错误等)直接向上抛出,不浪费重试次数。"""

from __future__ import annotations

import httpx
import pytest

from app.llm.retry import call_with_retry


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://example.test")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


async def test_call_with_retry_succeeds_on_first_try():
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        return "ok"

    result = await call_with_retry(fn, attempts=3, base_delay=0)
    assert result == "ok"
    assert calls == 1


async def test_call_with_retry_recovers_after_transient_failure():
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        if calls < 2:
            raise _status_error(503)
        return "ok"

    result = await call_with_retry(fn, attempts=3, base_delay=0)
    assert result == "ok"
    assert calls == 2


async def test_call_with_retry_raises_after_exhausting_attempts():
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise _status_error(429)

    with pytest.raises(httpx.HTTPStatusError):
        await call_with_retry(fn, attempts=3, base_delay=0)
    assert calls == 3


async def test_call_with_retry_does_not_retry_non_retryable_status():
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise _status_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        await call_with_retry(fn, attempts=3, base_delay=0)
    assert calls == 1


async def test_call_with_retry_does_not_retry_unrelated_exceptions():
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        raise ValueError("参数错误")

    with pytest.raises(ValueError):
        await call_with_retry(fn, attempts=3, base_delay=0)
    assert calls == 1
