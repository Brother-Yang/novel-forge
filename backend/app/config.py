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

    # 粗略以字符数近似 token,用于上下文预算控制
    context_token_budget: int = 6000
    previous_summary_count: int = 6


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
