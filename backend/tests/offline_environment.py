"""Keep offline test entry points independent of operator configuration."""

import os
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

from pydantic_settings import BaseSettings


def configure_offline_environment(data_dir: Path) -> None:
    directory = data_dir.resolve()
    # Both packages instantiate settings on import. Pydantic constructs its
    # dotenv source before selecting sources, so use a temporary cwd as well.
    with (
        chdir(directory),
        patch.object(BaseSettings, "settings_customise_sources", return_value=()),
    ):
        from chromadb.config import Settings as ChromaSettings

        from app.config import Settings, settings

    Settings.model_config["env_file"] = None
    ChromaSettings.model_config["env_file"] = None
    fields = {
        field.casefold()
        for field in Settings.model_fields.keys() | ChromaSettings.model_fields.keys()
    }
    for name in tuple(os.environ):
        if name.casefold() in fields:
            del os.environ[name]
    os.environ["ANONYMIZED_TELEMETRY"] = "false"

    unavailable = "http://127.0.0.1:1"
    defaults = {
        "database_url": f"sqlite:///{directory / 'novel.db'}",
        "chroma_dir": str(directory / "chroma"),
        "ollama_base_url": unavailable,
        "openai_base_url": unavailable,
        "dashscope_base_url": unavailable,
    }
    for field, value in defaults.items():
        os.environ[field.upper()] = value
        setattr(settings, field, value)
