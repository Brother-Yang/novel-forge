from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolCall:
    """模型在一次 function calling 响应中请求的一次工具调用。"""

    id: str
    name: str
    arguments: dict[str, object]


@dataclass
class ToolChatResult:
    """带工具调用的一次对话响应:content 为模型的自然语言部分(可能为空)。"""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)


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

    async def chat_with_tools(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        temperature: float = 0.7,
    ) -> ToolChatResult:
        """支持 function calling 的后端才需实现(智能体自主决策的前提)。

        本地 Ollama 等不支持的后端可不实现此方法;调用方应通过
        ``hasattr(llm, "chat_with_tools")`` 探测能力后降级到固定流水线,
        而不是直接调用后崩溃。
        """
        ...
