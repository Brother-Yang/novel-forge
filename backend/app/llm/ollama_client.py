import json
from collections.abc import AsyncIterator

from ..config import settings
from .base import ModelResponseError, ToolChatResult
from .budget import require_model_budget
from .request_options import (
    provider_client,
    provider_stream,
    provider_timeout,
    validate_output_limit,
)
from .responses import validate_embedding, validate_finish_reason


class OllamaClient:
    def __init__(self, *, max_output_tokens: int | None = None) -> None:
        super().__init__()
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.generation_model
        self.embedding_model = settings.embedding_model
        self.timeout = provider_timeout(settings)
        self.max_output_tokens = validate_output_limit(max_output_tokens)

    @property
    def embedding_endpoint(self) -> str:
        return f"{self.base_url}/api/embed"

    def _messages(self, prompt: str, system: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _chat_payload(
        self, prompt: str, system: str, temperature: float, *, stream: bool
    ) -> dict[str, object]:
        options: dict[str, int | float] = {"temperature": temperature}
        if self.max_output_tokens is not None:
            options["num_predict"] = self.max_output_tokens
        return {
            "model": self.model,
            "messages": self._messages(prompt, system),
            "stream": stream,
            "options": options,
        }

    async def chat_stream(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ) -> AsyncIterator[str]:
        require_model_budget()
        payload = self._chat_payload(prompt, system, temperature, stream=True)
        async with provider_stream(
            f"{self.base_url}/api/chat", payload=payload, timeout=self.timeout
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if "error" in data:
                    raise ModelResponseError("模型流返回错误，本次内容未完成，请重试")
                finished = data.get("done") is True
                if finished:
                    validate_finish_reason(data.get("done_reason"))
                chunk = data.get("message", {}).get("content", "")
                if chunk:
                    yield chunk
                if finished:
                    return
            raise ModelResponseError("模型流在完成前中断，本次内容未保存，请重试")

    async def chat(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ) -> str:
        require_model_budget()
        payload = self._chat_payload(prompt, system, temperature, stream=False)
        async with provider_client(self.timeout) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("done") is False or "error" in data:
                raise ModelResponseError("模型未完成本次输出，请重试")
            validate_finish_reason(data.get("done_reason"))
            return data.get("message", {}).get("content", "")

    async def embed(self, text: str) -> list[float]:
        require_model_budget()
        payload = {"model": self.embedding_model, "input": text}
        async with provider_client(self.timeout) as client:
            resp = await client.post(self.embedding_endpoint, json=payload)
            resp.raise_for_status()
            try:
                data: object = resp.json()
            except ValueError:
                raise ModelResponseError("模型未返回有效嵌入向量，请重试") from None
            vectors = data.get("embeddings") if isinstance(data, dict) else None
            if not isinstance(vectors, list) or len(vectors) != 1:
                raise ModelResponseError("模型未返回单条有效嵌入向量，请重试")
            return validate_embedding(vectors[0])

    async def chat_with_tools(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        temperature: float = 0.7,
    ) -> ToolChatResult:
        # 本地 Ollama 后端不支持 function calling;智能体自主决策模式应在启动前
        # 用 ..llm.supports_tool_calling() 探测并降级到固定流水线,不应调用到这里。
        raise NotImplementedError(
            "OllamaClient 不支持 function calling(智能体自主决策)"
        )
