from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: Literal["ollama", "openai", "bailian", "dashscope"] = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    generation_model: str = "qwen2.5:14b"
    embedding_model: str = "qwen3-embedding:latest"

    openai_base_url: str = ""
    openai_api_key: str = ""

    # 阿里云百炼(DashScope,OpenAI 兼容模式)
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_api_key: str = ""
    bailian_generation_model: str = "qwen-plus"
    bailian_embedding_model: str = "text-embedding-v3"

    database_url: str = "sqlite:///./data/novel.db"
    chroma_dir: str = "./data/chroma"

    # This is a single-user application. Local development may omit both;
    # production startup refuses to expose manuscript/model access anonymously.
    app_env: Literal["local", "production"] = "local"
    access_password: str = Field(default="", repr=False)
    session_secret: str = Field(default="", repr=False)
    session_ttl_seconds: int = Field(default=43200, ge=60, le=604800)
    login_attempts_per_window: int = Field(default=5, ge=1, le=100)
    login_window_seconds: int = Field(default=300, ge=1)
    generation_requests_per_window: int = Field(default=30, ge=1)
    generation_window_seconds: int = Field(default=3600, ge=1)
    generation_max_concurrent: int = Field(default=2, ge=1, le=32)
    llm_requests_per_window: int = Field(default=600, ge=1, le=100_000)
    llm_window_seconds: int = Field(default=3600, ge=1)
    llm_connect_timeout_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    llm_read_timeout_seconds: float = Field(default=180.0, gt=0, allow_inf_nan=False)
    llm_write_timeout_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    llm_pool_timeout_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)

    # 允许跨域访问的前端地址,逗号分隔;开发默认放行 Vite 开发服务器,
    # 生产部署(前端与后端不同源时)需在 .env 里改成实际域名,不允许用 "*"
    # 与允许携带凭证同时开启(浏览器也会拒绝这种组合)。
    cors_allowed_origins: str = "http://localhost:5173"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    # 粗略以字符数近似 token,用于上下文预算控制
    context_token_budget: int = Field(default=6000, ge=0)
    previous_summary_count: int = Field(default=6, ge=0)

    # 智能体自主决策模式的安全上限,防止死循环/重写不止/额度失控
    agent_max_steps: int = Field(default=40, ge=0)
    agent_max_tool_calls: int = Field(default=60, ge=0)
    agent_max_rewrites: int = Field(default=2, ge=0)

    # 模型调用总尝试次数包含首次请求；退避延迟为 0 时立即重试。
    llm_retry_attempts: int = Field(default=3, ge=1)
    llm_retry_base_delay: float = Field(default=1.0, ge=0, allow_inf_nan=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
