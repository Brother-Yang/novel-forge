"""Common request bounds for all provider adapters."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import httpx

from ..config import Settings


def provider_timeout(config: Settings) -> httpx.Timeout:
    return httpx.Timeout(
        connect=config.llm_connect_timeout_seconds,
        read=config.llm_read_timeout_seconds,
        write=config.llm_write_timeout_seconds,
        pool=config.llm_pool_timeout_seconds,
    )


def validate_output_limit(max_output_tokens: int | None) -> int | None:
    if max_output_tokens is not None and (
        type(max_output_tokens) is not int or max_output_tokens <= 0
    ):
        raise ValueError("max_output_tokens 必须是正整数")
    return max_output_tokens


@asynccontextmanager
async def provider_client(timeout: httpx.Timeout) -> AsyncIterator[httpx.AsyncClient]:
    client = httpx.AsyncClient(timeout=timeout)
    try:
        yield client
    finally:
        # ASGI disconnect cancels every checkpoint in the request scope. Let
        # transport cleanup finish in its own shielded scope.
        with anyio.CancelScope(shield=True):
            await client.aclose()


@asynccontextmanager
async def provider_stream(
    url: str,
    *,
    payload: dict[str, object],
    timeout: httpx.Timeout,
    headers: dict[str, str] | None = None,
) -> AsyncIterator[httpx.Response]:
    async with provider_client(timeout) as client:
        request = client.build_request("POST", url, json=payload, headers=headers)
        response = await client.send(request, stream=True)
        try:
            yield response
        finally:
            with anyio.CancelScope(shield=True):
                await response.aclose()
