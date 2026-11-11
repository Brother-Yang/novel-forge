"""Provider-side budgets cover streamed, background, retry and embedding requests."""

from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.limits import WindowBudget
from app.llm import budget
from app.llm.bailian_client import BailianClient
from app.llm.ollama_client import OllamaClient
from app.llm.openai_client import OpenAIClient
from app.llm.retry import call_with_retry


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    monkeypatch.chdir(tmp_path)
    seen: list[str] = []

    def receive(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("embeddings"):
            return httpx.Response(200, json={"embedding": [0.1]})
        return httpx.Response(503)

    real_client = httpx.AsyncClient

    def mock_client(*, timeout: httpx.Timeout) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(receive), timeout=timeout)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    monkeypatch.setattr(budget, "_window", WindowBudget())
    monkeypatch.setattr(
        budget,
        "settings",
        Settings(
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            llm_requests_per_window=2,
        ),
    )
    return seen


async def test_each_retry_consumes_an_actual_provider_request(
    provider: list[str],
) -> None:
    model = OllamaClient()
    with pytest.raises(budget.ModelBudgetExceeded, match="模型调用已达到本时段限额"):
        await call_with_retry(lambda: model.chat("离线调用"), attempts=3, base_delay=0)
    assert provider == ["/api/chat", "/api/chat"]


async def test_providers_share_budget_across_embedding_stream_and_tools(
    provider: list[str],
) -> None:
    ollama, openai, bailian = OllamaClient(), OpenAIClient(), BailianClient()
    for _ in range(2):
        assert await ollama.embed("离线索引") == [0.1]
    with pytest.raises(budget.ModelBudgetExceeded):
        await ollama.chat("正文")
    with pytest.raises(budget.ModelBudgetExceeded):
        await ollama.embed("索引")
    with pytest.raises(budget.ModelBudgetExceeded):
        _ = [chunk async for chunk in ollama.chat_stream("章节")]
    with pytest.raises(budget.ModelBudgetExceeded):
        _ = [chunk async for chunk in openai.chat_stream("章节")]
    with pytest.raises(budget.ModelBudgetExceeded):
        await openai.chat("摘要")
    with pytest.raises(budget.ModelBudgetExceeded):
        await openai.chat_with_tools([], [])
    with pytest.raises(budget.ModelBudgetExceeded):
        await bailian.embed("另一提供方")
    assert provider == ["/api/embeddings", "/api/embeddings"]
