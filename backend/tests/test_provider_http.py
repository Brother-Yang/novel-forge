"""Provider HTTP contracts tested without external servers or model calls."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import anyio
import httpx
import pytest
from anyio.lowlevel import checkpoint
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import llm, models
from app.agents import pipeline, runner
from app.agents.errors import public_failure
from app.agents.guardrails import GuardrailLimits
from app.agents.tool_registry import ToolRegistry
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
        if request.url.path == "/api/embed":
            assert payload == {
                "model": provider_settings.embedding_model,
                "input": "three",
            }
            return httpx.Response(200, json={"embeddings": [[0.1]]})
        if request.url.path.endswith("embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.1]}]})
        if payload.get("stream") is True:
            stream = (
                OLLAMA_CHUNK + b'{"done":true,"done_reason":"stop"}\n'
                if provider == "ollama"
                else OPENAI_CHUNK + b"data: [DONE]\n\n"
            )
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
    "response",
    [
        None,
        {},
        {"embeddings": []},
        {"embeddings": [[], []]},
        {"embeddings": [[]]},
        {"embeddings": [[True]]},
        {"embeddings": [["0.1"]]},
        {"embeddings": [[None]]},
    ],
)
async def test_ollama_rejects_invalid_embedding_responses(
    response: object,
    wire: MockProvider,
) -> None:
    clients = wire(lambda _request: httpx.Response(200, json=response))
    with pytest.raises(ModelResponseError, match="嵌入向量"):
        await ollama_client.OllamaClient().embed("离线测试")
    assert clients[0].is_closed


@pytest.mark.parametrize("provider", ["ollama", "openai", "bailian"])
@pytest.mark.parametrize(
    "value", ["NaN", "Infinity", "-Infinity", "9" * 400, "3.5e38", "-3.5e38"]
)
async def test_provider_rejects_values_that_cannot_be_stored_as_finite_float32(
    provider: str,
    value: str,
    wire: MockProvider,
) -> None:
    content = (
        '{"embeddings":[[' + value + "]] }"
        if provider == "ollama"
        else '{"data":[{"embedding":[' + value + "]}]}"
    )
    wire(lambda _request: httpx.Response(200, content=content))
    factory = {
        "ollama": ollama_client.OllamaClient,
        "openai": openai_client.OpenAIClient,
        "bailian": bailian_client.BailianClient,
    }[provider]
    with pytest.raises(ModelResponseError, match="无效数值"):
        await factory().embed("离线测试")


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


def completion_stream(provider: str, ending: str) -> bytes:
    if provider == "ollama":
        first = OLLAMA_CHUNK
        terminal: dict[str, object] = {"done": True, "done_reason": ending}
        if ending == "error":
            terminal = {"error": "synthetic private upstream detail"}
        return (
            first
            if ending == "missing"
            else first + json.dumps(terminal).encode() + b"\n"
        )
    if ending == "missing":
        return OPENAI_CHUNK
    if ending == "marker_only":
        return OPENAI_CHUNK + b"data: [DONE]\n\n"
    terminal = {"choices": [{"delta": {}, "finish_reason": ending}]}
    if ending == "error":
        terminal = {"error": {"message": "synthetic private upstream detail"}}
    return OPENAI_CHUNK + b"data: " + json.dumps(terminal).encode() + b"\n\n"


@pytest.mark.parametrize("provider", ["ollama", "openai", "bailian"])
@pytest.mark.parametrize(
    "ending", ["stop", "missing", "length", "content_filter", "error"]
)
async def test_provider_requires_completed_stream_without_truncation(
    provider: str,
    ending: str,
    wire: MockProvider,
) -> None:
    clients = wire(
        lambda _request: httpx.Response(
            200, content=completion_stream(provider, ending)
        )
    )
    factory = {
        "ollama": ollama_client.OllamaClient,
        "openai": openai_client.OpenAIClient,
        "bailian": bailian_client.BailianClient,
    }[provider]
    stream = factory().chat_stream("合成正文")
    assert await anext(stream) == "first"
    if ending == "stop":
        assert [token async for token in stream] == []
    else:
        with pytest.raises(ModelResponseError) as error:
            _ = [token async for token in stream]
        assert "synthetic private upstream" not in public_failure(error.value)
    assert clients[0].is_closed


async def test_openai_compatible_done_marker_is_a_valid_terminal_signal(
    wire: MockProvider,
) -> None:
    wire(
        lambda _request: httpx.Response(
            200, content=completion_stream("openai", "marker_only")
        )
    )
    assert [
        token async for token in openai_client.OpenAIClient().chat_stream("合成正文")
    ] == ["first"]


async def test_completed_stream_closes_without_waiting_for_more_socket_bytes(
    wire: MockProvider,
) -> None:
    body = PausedResponse(completion_stream("openai", "stop"))
    clients = wire(lambda _request: httpx.Response(200, stream=body))

    async def consume() -> list[str]:
        return [
            token
            async for token in openai_client.OpenAIClient().chat_stream("合成正文")
        ]

    assert await asyncio.wait_for(consume(), 1) == ["first"]
    assert body.closed.is_set() and clients[0].is_closed


@pytest.mark.parametrize("provider", ["ollama", "openai"])
@pytest.mark.parametrize("ending", ["missing", "length"])
async def test_incomplete_provider_output_does_not_replace_saved_chapter(
    provider: str,
    ending: str,
    wire: MockProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(
        lambda _request: httpx.Response(
            200, content=completion_stream(provider, ending)
        )
    )
    factory = (
        ollama_client.OllamaClient
        if provider == "ollama"
        else openai_client.OpenAIClient
    )
    monkeypatch.setattr(pipeline, "get_llm", factory)
    monkeypatch.setattr(
        pipeline.memory, "build_chapter_context", AsyncMock(return_value="")
    )
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            chapter = models.Chapter(
                book=models.Book(title="合成书"),
                title="原章",
                outline="章纲",
                status="done",
                content=models.ChapterContent(body="作者已保存的完整正文"),
            )
            db.add(chapter)
            db.commit()
            events: list[tuple[str, str]] = []
            with pytest.raises(ModelResponseError):
                async for event in pipeline.gen_chapter_body(db, chapter.id):
                    events.append(event)
            db.expire_all()
            assert chapter.content and chapter.content.body == "作者已保存的完整正文"
            assert chapter.status == "done"
            assert not any(kind == "done" for kind, _ in events)
            assert db.scalars(select(models.MemorySyncJob)).all() == []
    finally:
        engine.dispose()


@pytest.mark.parametrize("provider", ["ollama", "openai", "bailian"])
async def test_nonstreamed_truncation_is_rejected_before_use(
    provider: str,
    wire: MockProvider,
) -> None:
    response = (
        {"message": {"content": "部分内容"}, "done": True, "done_reason": "length"}
        if provider == "ollama"
        else {
            "choices": [
                {
                    "message": {"content": "部分内容", "tool_calls": []},
                    "finish_reason": "length",
                }
            ]
        }
    )
    wire(lambda _request: httpx.Response(200, json=response))
    factory = {
        "ollama": ollama_client.OllamaClient,
        "openai": openai_client.OpenAIClient,
        "bailian": bailian_client.BailianClient,
    }[provider]
    model = factory()
    with pytest.raises(ModelResponseError, match="长度上限"):
        await model.chat("合成提示")
    if provider != "ollama":
        with pytest.raises(ModelResponseError, match="长度上限"):
            await model.chat_with_tools([], [])


@pytest.mark.parametrize("provider", ["ollama", "openai", "bailian"])
async def test_nonstreamed_provider_error_is_not_an_empty_success(
    provider: str,
    wire: MockProvider,
) -> None:
    clients = wire(
        lambda _request: httpx.Response(
            200, json={"error": {"message": "synthetic private upstream detail"}}
        )
    )
    factory = {
        "ollama": ollama_client.OllamaClient,
        "openai": openai_client.OpenAIClient,
        "bailian": bailian_client.BailianClient,
    }[provider]
    model = factory()
    with pytest.raises(ModelResponseError) as error:
        await model.chat("合成提示")
    assert "synthetic private upstream" not in public_failure(error.value)
    if provider != "ollama":
        with pytest.raises(ModelResponseError):
            await model.chat_with_tools([], [])
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("provider", ["openai", "bailian"])
@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"choices": []},
        {"choices": "invalid"},
        {"choices": [{}]},
        {"choices": [{"message": {"content": 42}}]},
    ],
)
async def test_malformed_completion_is_not_a_successful_empty_decision(
    provider: str, response: object, wire: MockProvider
) -> None:
    wire(lambda _request: httpx.Response(200, json=response))
    model = (
        openai_client.OpenAIClient()
        if provider == "openai"
        else bailian_client.BailianClient()
    )
    with pytest.raises(ModelResponseError):
        await model.chat("合成提示")
    with pytest.raises(ModelResponseError):
        await model.chat_with_tools([], [])


async def test_missing_choices_stops_agent_instead_of_spending_more_model_calls(
    wire: MockProvider,
) -> None:
    clients = wire(lambda _request: httpx.Response(200, json={"choices": []}))
    engine = create_engine("sqlite://")
    try:
        with Session(engine) as db:
            events = [
                event
                async for event in runner.run(
                    db,
                    "synthetic-book",
                    llm=openai_client.OpenAIClient(),
                    tool_registry=ToolRegistry(),
                    messages=[],
                    finish_tool="finish",
                    review_tool="review",
                    limits=GuardrailLimits(
                        max_steps=10, max_tool_calls=10, max_rewrites=1
                    ),
                )
            ]
        assert [kind for kind, _ in events] == ["error"]
        assert len(clients) == 1
        assert clients[0].is_closed
    finally:
        engine.dispose()
