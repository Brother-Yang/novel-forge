from functools import lru_cache

from ..config import settings
from .base import LLMClient
from .ollama_client import OllamaClient
from .openai_client import OpenAIClient


@lru_cache
def get_llm() -> LLMClient:
    if settings.llm_provider == "openai":
        return OpenAIClient()
    return OllamaClient()
