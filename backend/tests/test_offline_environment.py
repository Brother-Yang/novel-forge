import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from app.config import Settings, settings


def test_settings_created_during_tests_ignore_dotenv_and_use_safe_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "LLM_PROVIDER=fixture-provider-that-must-not-load\n"
        "OPENAI_API_KEY=fixture-credential-that-must-not-load\n",
        encoding="utf-8",
    )
    current = Settings()
    assert current.llm_provider == "ollama" and current.openai_api_key == ""
    assert current.database_url == settings.database_url
    database_path = make_url(current.database_url).database
    assert database_path is not None and Path(database_path).is_absolute()
    assert Path(database_path).parent == Path(current.chroma_dir).parent
    assert (
        current.openai_base_url
        == current.ollama_base_url
        == current.dashscope_base_url
        == "http://127.0.0.1:1"
    )


def test_first_import_ignores_dotenv_and_inherited_app_configuration(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        "LLM_PROVIDER=invalid-dotenv-provider\n"
        "OPENAI_API_KEY=fixture-dotenv-value\n"
        "ANONYMIZED_TELEMETRY=invalid-dotenv-value\n",
        encoding="utf-8",
    )
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    code = """
import os
import sys
from pathlib import Path

from tests.offline_environment import configure_offline_environment

original_cwd = Path.cwd()
original_path = os.environ.get("PATH")
data_dir = Path(sys.argv[1]).resolve()
configure_offline_environment(data_dir)

from app.config import Settings, settings
from chromadb.config import Settings as ChromaSettings

assert Path.cwd() == original_cwd and os.environ.get("PATH") == original_path
assert settings.llm_provider == Settings().llm_provider == "ollama"
assert not settings.openai_api_key and not Settings().openai_api_key
assert settings.database_url == f"sqlite:///{data_dir / 'novel.db'}"
assert settings.chroma_dir == str(data_dir / "chroma")
assert ChromaSettings().anonymized_telemetry is False
"""
    inherited = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "LLM_PROVIDER": "invalid-inherited-provider",
        "OPENAI_API_KEY": "fixture-inherited-value",
        "ANONYMIZED_TELEMETRY": "invalid-inherited-value",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'must-not-open.db'}",
    }
    result = subprocess.run(
        [sys.executable, "-c", code, str(isolated)],
        cwd=tmp_path,
        env=inherited,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, "离线测试首次加载配置失败"
    assert not (tmp_path / "must-not-open.db").exists()
