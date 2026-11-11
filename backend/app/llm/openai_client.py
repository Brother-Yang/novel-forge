import json
from collections.abc import AsyncIterator
from typing import Never, cast

from ..config import settings
from .base import ModelResponseError, ToolCall, ToolChatResult
from .budget import require_model_budget
from .request_options import (
    provider_client,
    provider_stream,
    provider_timeout,
    validate_output_limit,
)


def _reject_json_constant(_value: str) -> Never:
    raise ValueError("non-finite JSON value")


def _parse_tool_call(raw: object) -> ToolCall:
    if not isinstance(raw, dict) or not isinstance(raw.get("function"), dict):
        raise ModelResponseError("模型返回的工具调用结构无效，请重试")
    call_id: object = raw.get("id")
    function = cast(dict[str, object], raw["function"])
    name = function.get("name")
    if (
        not isinstance(call_id, str)
        or not call_id.strip()
        or not isinstance(name, str)
        or not name.strip()
    ):
        raise ModelResponseError("模型返回的工具调用缺少有效 ID 或名称，请重试")
    encoded = function.get("arguments")
    message = "模型返回无效的工具参数：参数必须是 JSON 对象，请重试"
    if not isinstance(encoded, str):
        raise ModelResponseError(message)
    try:
        arguments = cast(
            object, json.loads(encoded, parse_constant=_reject_json_constant)
        )
    except (ValueError, RecursionError):
        raise ModelResponseError(message) from None
    if not isinstance(arguments, dict):
        raise ModelResponseError(message)
    return ToolCall(id=call_id, name=name, arguments=cast(dict[str, object], arguments))


class OpenAIClient:
    """OpenAI 兼容 API 客户端(云端切换用)。"""

    def __init__(self, *, max_output_tokens: int | None = None) -> None:
        super().__init__()
        self.base_url = (
            settings.openai_base_url or "https://api.openai.com/v1"
        ).rstrip("/")
        self.api_key = settings.openai_api_key
        self.model = settings.generation_model
        self.embedding_model = settings.embedding_model
        self.timeout = provider_timeout(settings)
        self.max_output_tokens = validate_output_limit(max_output_tokens)
        self.extra_body: dict[str, object] = {}

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _messages(self, prompt: str, system: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _chat_payload(
        self, prompt: str, system: str, temperature: float, stream: bool
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": self._messages(prompt, system),
            "stream": stream,
            "temperature": temperature,
        }
        self._set_generation_options(payload)
        return payload

    def _set_generation_options(self, payload: dict[str, object]) -> None:
        payload.update(self.extra_body)
        if self.max_output_tokens is not None:
            payload["max_tokens"] = self.max_output_tokens

    async def chat_stream(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ) -> AsyncIterator[str]:
        require_model_budget()
        payload = self._chat_payload(prompt, system, temperature, stream=True)
        async with provider_stream(
            f"{self.base_url}/chat/completions",
            payload=payload,
            headers=self._headers(),
            timeout=self.timeout,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                obj = json.loads(data)
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content") or ""
                if content:
                    yield content

    async def chat(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ) -> str:
        require_model_budget()
        payload = self._chat_payload(prompt, system, temperature, stream=False)
        async with provider_client(self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            choices = resp.json().get("choices") or []
            if not choices:
                return ""
            return choices[0].get("message", {}).get("content") or ""

    async def embed(self, text: str) -> list[float]:
        require_model_budget()
        payload = {"model": self.embedding_model, "input": text}
        async with provider_client(self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings", json=payload, headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]

    async def chat_with_tools(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        temperature: float = 0.7,
    ) -> ToolChatResult:
        require_model_budget()
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": False,
            "temperature": temperature,
        }
        self._set_generation_options(payload)
        async with provider_client(self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            choices = resp.json().get("choices") or []
            if not choices:
                return ToolChatResult(content="")
            message = choices[0].get("message") or {}
            content = message.get("content") or ""
            raw_calls: object = message.get("tool_calls")
            if raw_calls is not None and not isinstance(raw_calls, list):
                raise ModelResponseError("模型返回的工具调用列表无效，请重试")
            calls = [_parse_tool_call(raw_call) for raw_call in (raw_calls or [])]
            return ToolChatResult(content=content, tool_calls=calls)
