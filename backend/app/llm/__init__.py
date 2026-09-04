from functools import lru_cache

from ..config import settings
from .bailian_client import BailianClient
from .base import LLMClient
from .ollama_client import OllamaClient
from .openai_client import OpenAIClient

# 目前只有走 OpenAI 兼容协议的后端原生支持 function calling;
# 本地 Ollama 暂不支持,智能体自主决策模式应在启动前用此函数探测并降级。
_TOOL_CALLING_PROVIDERS = ("openai", "bailian", "dashscope")


@lru_cache
def get_llm() -> LLMClient:
    if settings.llm_provider in ("bailian", "dashscope"):
        return BailianClient()
    if settings.llm_provider == "openai":
        return OpenAIClient()
    return OllamaClient()


def supports_tool_calling() -> bool:
    """当前配置的 LLM 后端是否支持 function calling。"""
    return settings.llm_provider in _TOOL_CALLING_PROVIDERS
