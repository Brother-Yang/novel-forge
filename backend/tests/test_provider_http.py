"""Provider HTTP contracts tested without external servers or model calls."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import cast

import anyio
import httpx
import pytest
from anyio.lowlevel import checkpoint
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import llm, models
from app.agents import pipeline
from app.agents.errors import public_failure
from app.config import Settings
from app.db_base import Base
from app.limits import WindowBudget
from app.llm import bailian_client, budget, ollama_client, openai_client
from app.llm.base import ModelResponseError

TIMEOUTS = {"connect": 1.25, "read": 2.5, "write": 3.75, "pool": 4.5}
OPENAI_CHUNK = b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
OLLAMA_CHUNK = b'{"message":{"content":"first"},"done":false}\n'
type ProviderHandler = Callable[[httpx.Request], httpx.Response]
type MockProvider = Callable[[ProviderHandler], list[httpx.AsyncClient]]


@pytest.fixture(autouse=True)
def provider_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Settings]:
    monkeypatch.chdir(tmp_path)
    config = Settings(
        llm_connect_timeout_seconds=TIMEOUTS["connect"],
        llm_read_timeout_seconds=TIMEOUTS["read"],
        llm_write_timeout_seconds=TIMEOUTS["write"],
        llm_pool_timeout_seconds=TIMEOUTS["pool"],
    )
    for module in (ollama_client, openai_client, bailian_client, budget, llm):
        monkeypatch.setattr(module, "settings", config)
    monkeypatch.setattr(budget, "_window", WindowBudget())
    llm.get_llm.cache_clear()
    yield config
    llm.get_llm.cache_clear()


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> MockProvider:
    real_client = httpx.AsyncClient

    def attach(handler: ProviderHandler) -> list[httpx.AsyncClient]:
        clients: list[httpx.AsyncClient] = []

        def create(*, timeout: httpx.Timeout) -> httpx.AsyncClient:
            client = real_client(
                timeout=timeout, transport=httpx.MockTransport(handler)
            )
            clients.append(client)
            return client

        monkeypatch.setattr(httpx, "AsyncClient", create)
        return clients

    return attach


@pytest.mark.parametrize("provider", ["ollama", "openai", "bailian"])
async def test_all_provider_requests_use_configured_timeouts_and_generation_caps(
    provider: str,
    provider_settings: Settings,
    wire: MockProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        llm, "settings", provider_settings.model_copy(update={"llm_provider": provider})
    )
    bodies: list[dict[str, object]] = []

    def receive(request: httpx.Request) -> httpx.Response:
        assert request.extensions["timeout"] == TIMEOUTS
        payload = cast(dict[str, object], json.loads(request.content))
        bodies.append(payload)
        if request.url.path.endswith("embeddings"):
            if provider == "ollama":
                return httpx.Response(200, json={"embedding": [0.1]})
            return httpx.Response(200, json={"data": [{"embedding": [0.1]}]})
        if payload.get("stream") is True:
            stream = OLLAMA_CHUNK if provider == "ollama" else OPENAI_CHUNK
            return httpx.Response(200, content=stream)
        if provider == "ollama":
            return httpx.Response(200, json={"message": {"content": "text"}})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "text",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "sample",
                                        "arguments": '{"chapter_id":"id"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    clients = wire(receive)
    model = llm.get_llm(max_output_tokens=64)
    assert await model.chat("one") == "text"
    assert [chunk async for chunk in model.chat_stream("two")] == ["first"]
    if provider != "ollama":
        result = await model.chat_with_tools([], [])
        assert result.tool_calls[0].arguments == {"chapter_id": "id"}
    assert await model.embed("three") == [0.1]
    for payload in bodies[:-1]:
        if provider == "ollama":
            options = payload["options"]
            assert isinstance(options, dict) and options["num_predict"] == 64
        else:
            assert payload["max_tokens"] == 64
            if provider == "bailian":
                assert payload["enable_thinking"] is False
    assert "max_tokens" not in bodies[-1] and "options" not in bodies[-1]
    assert clients and all(client.is_closed for client in clients)


@pytest.mark.parametrize(
    "arguments", ["{bad-fixture-input", "[]", "null", "3", '{"n":NaN}', {}, None]
)
async def test_invalid_tool_parameters_are_not_replaced_with_empty_object(
    arguments: object,
    wire: MockProvider,
) -> None:
    clients = wire(
        lambda _request: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "sample",
                                        "arguments": arguments,
                                    },
                                }
                            ]
                        }
                    }
                ],
            },
        )
    )
    with pytest.raises(ModelResponseError, match="参数必须是 JSON 对象") as error:
        await openai_client.OpenAIClient().chat_with_tools([], [])
    assert "bad-fixture-input" not in public_failure(error.value)
    assert clients[0].is_closed


class PausedResponse(httpx.AsyncByteStream):
    def __init__(self, first: bytes) -> None:
        super().__init__()
        self.first = first
        self.waiting = asyncio.Event()
        self.expire = asyncio.Event()
        self.closed = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.first
        self.waiting.set()
        await self.expire.wait()
        raise httpx.ReadTimeout("mock upstream timeout")

    async def aclose(self) -> None:
        await checkpoint()
        self.closed.set()


@pytest.mark.parametrize("provider", ["ollama", "openai"])
@pytest.mark.parametrize("interrupt", ["cancel", "timeout"])
async def test_stream_timeout_or_cancellation_closes_response_and_client(
    provider: str,
    interrupt: str,
    wire: MockProvider,
) -> None:
    body = PausedResponse(OLLAMA_CHUNK if provider == "ollama" else OPENAI_CHUNK)
    clients = wire(lambda _request: httpx.Response(200, stream=body))
    model = (
        ollama_client.OllamaClient()
        if provider == "ollama"
        else openai_client.OpenAIClient()
    )
    stream = model.chat_stream("start")
    assert await anext(stream) == "first"

    async def next_chunk() -> str:
        return await anext(stream)

    waiting = asyncio.create_task(next_chunk())
    await asyncio.wait_for(body.waiting.wait(), 2)
    if interrupt == "timeout":
        body.expire.set()
        with pytest.raises(httpx.ReadTimeout):
            await waiting
    else:
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    assert body.closed.is_set() and clients[0].is_closed


async def test_asgi_style_cancellation_does_not_interrupt_transport_close(
    wire: MockProvider,
) -> None:
    body = PausedResponse(OPENAI_CHUNK)
    clients = wire(lambda _request: httpx.Response(200, stream=body))

    async def consume() -> None:
        stream = openai_client.OpenAIClient().chat_stream("start")
        assert await anext(stream) == "first"
        await anext(stream)

    async with anyio.create_task_group() as group:
        group.start_soon(consume)
        await asyncio.wait_for(body.waiting.wait(), 2)
        group.cancel_scope.cancel()
    assert body.closed.is_set() and clients[0].is_closed


async def test_closing_pipeline_after_first_token_also_closes_provider_stream(
    wire: MockProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = PausedResponse(OLLAMA_CHUNK)
    clients = wire(lambda _request: httpx.Response(200, stream=body))
    monkeypatch.setattr(pipeline, "get_llm", ollama_client.OllamaClient)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            book = models.Book(title="离线测试书")
            db.add(book)
            db.commit()
            stream = pipeline.gen_worldbuilding(db, book.id)
            assert await anext(stream) == ("token", "first")
            # The SSE producer does the same aclose when its consumer disconnects.
            assert isinstance(stream, AsyncGenerator)
            await stream.aclose()
            assert body.closed.is_set() and clients[0].is_closed
            assert db.scalars(select(models.Worldbuilding)).all() == []
    finally:
        engine.dispose()
