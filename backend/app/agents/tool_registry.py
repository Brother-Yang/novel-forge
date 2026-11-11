"""Agent 工具的注册和分派契约。具体业务工具在 tools.py 中注册。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session


class ToolError(Exception):
    """可作为观察结果返回给模型的工具参数或业务错误。"""


ToolHandler = Callable[[Session, str, dict[str, object]], Awaitable[dict[str, object]]]


@dataclass(frozen=True)
class Tool:
    """供应商工具契约与处理函数。只读工具不占用 Runner 的写操作预算。"""

    name: str
    description: str
    parameters: dict[str, object]
    handler: ToolHandler
    read_only: bool = False

    def schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """根据模型返回的名称分派工具，不依赖具体的小说工具实现。"""

    def __init__(self) -> None:
        super().__init__()
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, object]]:
        return [t.schema() for t in self._tools.values()]

    async def dispatch(
        self, db: Session, book_id: str, name: str, args: dict[str, object]
    ) -> dict[str, object]:
        tool = self.get(name)
        if tool is None:
            raise ToolError(f"未知工具:{name}")
        return await tool.handler(db, book_id, args)
