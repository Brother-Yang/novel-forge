import json
from typing import AsyncIterator

import httpx

from ..config import settings


class OllamaClient:
    def __init__(self) -> None:
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.generation_model
        self.embedding_model = settings.embedding_model

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
            "options": {"temperature": temperature},
        }
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", f"{self.base_url}/api/chat", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        break

    async def chat(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ) -> str:
        payload = {
            "model": self.model,
            "messages": self._messages(prompt, system),
            "stream": False,
            "options": {"temperature": temperature},
        }
        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "")

    async def embed(self, text: str) -> list[float]:
        payload = {"model": self.embedding_model, "prompt": text}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self.base_url}/api/embeddings", json=payload)
            resp.raise_for_status()
            return resp.json().get("embedding", [])
