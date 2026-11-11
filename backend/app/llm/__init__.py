import hashlib
import json
from functools import lru_cache

import httpx

from ..config import settings
from .bailian_client import BailianClient
from .base import LLMClient
from .ollama_client import OllamaClient
from .openai_client import OpenAIClient

# 这里检查本项目适配器已实现的能力，而非供应商支持的全部功能。
# 当前 OllamaClient 未实现工具调用，自主决策入口需降级到固定流程。
_TOOL_CALLING_PROVIDERS = ("openai", "bailian", "dashscope")


@lru_cache
def get_llm(*, max_output_tokens: int | None = None) -> LLMClient:
    if settings.llm_provider in ("bailian", "dashscope"):
        return BailianClient(max_output_tokens=max_output_tokens)
    if settings.llm_provider == "openai":
        return OpenAIClient(max_output_tokens=max_output_tokens)
    return OllamaClient(max_output_tokens=max_output_tokens)


def get_embedding_profile() -> str:
    """Fingerprint the effective embedding service without persisting its URL."""
    client = get_llm()
    if isinstance(client, BailianClient):
        provider = "bailian"
    elif isinstance(client, OpenAIClient):
        provider = "openai"
    elif isinstance(client, OllamaClient):
        provider = "ollama"
    else:
        raise TypeError("unknown embedding provider")
    endpoint = httpx.URL(client.embedding_endpoint).copy_with(
        userinfo=None, fragment=None
    )
    profile = json.dumps(
        (provider, str(endpoint), client.embedding_model), ensure_ascii=False
    )
    return hashlib.sha256(profile.encode()).hexdigest()


def supports_tool_calling() -> bool:
    """当前配置的 LLM 后端是否支持 function calling。"""
    return settings.llm_provider in _TOOL_CALLING_PROVIDERS
