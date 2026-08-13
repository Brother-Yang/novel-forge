from ..config import settings
from .openai_client import OpenAIClient


class BailianClient(OpenAIClient):
    """阿里云百炼(DashScope)客户端。

    百炼提供 OpenAI 兼容模式,复用 OpenAIClient 的请求逻辑,
    仅覆盖 base_url / api_key / 模型名为百炼配置。
    """

    def __init__(self) -> None:
        self.base_url = settings.dashscope_base_url.rstrip("/")
        self.api_key = settings.dashscope_api_key
        self.model = settings.bailian_generation_model
        self.embedding_model = settings.bailian_embedding_model
