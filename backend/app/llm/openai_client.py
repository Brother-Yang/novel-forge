import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import settings
from .base import ToolCall, ToolChatResult


class OpenAIClient:
    """OpenAI 兼容 API 客户端(云端切换用)。"""

    def __init__(self) -> None:
        super().__init__()
        self.base_url = (settings.openai_base_url or "https://api.openai.com/v1").rstrip(
            "/"
        )
        self.api_key = settings.openai_api_key
        self.model = settings.generation_model
        self.embedding_model = settings.embedding_model

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
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": self._messages(prompt, system),
            "stream": stream,
            "temperature": temperature,
        }
        extra = getattr(self, "extra_body", None)
        if extra:
            payload.update(extra)
        return payload

    async def chat_stream(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ) -> AsyncIterator[str]:
        payload = self._chat_payload(prompt, system, temperature, stream=True)
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
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
        payload = self._chat_payload(prompt, system, temperature, stream=False)
        async with httpx.AsyncClient(timeout=None) as client:
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
        payload = {"model": self.embedding_model, "input": text}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings", json=payload, headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float = 0.7,
    ) -> ToolChatResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": False,
            "temperature": temperature,
        }
        extra = getattr(self, "extra_body", None)
        if extra:
            payload.update(extra)
        async with httpx.AsyncClient(timeout=None) as client:
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
            calls: list[ToolCall] = []
            for raw_call in message.get("tool_calls") or []:
                fn = raw_call.get("function") or {}
                try:
                    arguments = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                calls.append(
                    ToolCall(
                        id=raw_call.get("id", ""),
                        name=fn.get("name", ""),
                        arguments=arguments,
                    )
                )
            return ToolChatResult(content=content, tool_calls=calls)
