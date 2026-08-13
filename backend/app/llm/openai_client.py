import json
from typing import AsyncIterator

import httpx

from ..config import settings


class OpenAIClient:
    """OpenAI 兼容 API 客户端(云端切换用)。"""

    def __init__(self) -> None:
        self.base_url = (settings.openai_base_url or "https://api.openai.com/v1").rstrip(
            "/"
        )
        self.api_key = settings.openai_api_key
        self.model = settings.generation_model
        self.embedding_model = settings.embedding_model

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _messages(self, prompt: str, system: str) -> list[dict]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def chat_stream(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "messages": self._messages(prompt, system),
            "stream": True,
            "temperature": temperature,
        }
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
                    delta = obj["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta

    async def chat(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ) -> str:
        payload = {
            "model": self.model,
            "messages": self._messages(prompt, system),
            "stream": False,
            "temperature": temperature,
        }
        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def embed(self, text: str) -> list[float]:
        payload = {"model": self.embedding_model, "input": text}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings", json=payload, headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
