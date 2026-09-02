from collections.abc import AsyncIterator
from typing import Protocol


class LLMClient(Protocol):
    """LLM 抽象接口,便于在 Ollama / OpenAI 兼容 API 间切换。"""

    async def chat_stream(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ) -> AsyncIterator[str]:
        yield ""  # pragma: no cover  # 仅用于类型标注,标记为异步生成器

    async def chat(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ) -> str:
        ...

    async def embed(self, text: str) -> list[float]:
        ...
