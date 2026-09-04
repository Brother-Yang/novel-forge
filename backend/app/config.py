from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "ollama"
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

    # 允许跨域访问的前端地址,逗号分隔;开发默认放行 Vite 开发服务器,
    # 生产部署(前端与后端不同源时)需在 .env 里改成实际域名,不允许用 "*"
    # 与允许携带凭证同时开启(浏览器也会拒绝这种组合)。
    cors_allowed_origins: str = "http://localhost:5173"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    # 粗略以字符数近似 token,用于上下文预算控制
    context_token_budget: int = 6000
    previous_summary_count: int = 6

    # 智能体自主决策模式的安全上限,防止死循环/重写不止/额度失控
    agent_max_steps: int = 40
    agent_max_tool_calls: int = 60
    agent_max_rewrites: int = 2

    # LLM 调用失败(超时/限流/5xx)时的重试次数与首次重试延迟(指数退避)
    llm_retry_attempts: int = 3
    llm_retry_base_delay: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
