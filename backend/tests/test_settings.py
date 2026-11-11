from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.fixture(autouse=True)
def no_local_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("provider", ["ollama", "openai", "bailian", "dashscope"])
def test_settings_accept_supported_model_providers(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", provider)
    assert Settings().llm_provider == provider


def test_settings_default_provider_is_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert Settings().llm_provider == "ollama"


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("LLM_PROVIDER", "balian"),
        ("LLM_RETRY_ATTEMPTS", "0"),
        ("LLM_RETRY_ATTEMPTS", "-1"),
        ("LLM_RETRY_BASE_DELAY", "-0.1"),
        ("LLM_RETRY_BASE_DELAY", "inf"),
        ("LLM_RETRY_BASE_DELAY", "NaN"),
        ("CONTEXT_TOKEN_BUDGET", "-1"),
        ("PREVIOUS_SUMMARY_COUNT", "-1"),
        ("AGENT_MAX_STEPS", "-1"),
        ("AGENT_MAX_TOOL_CALLS", "-1"),
        ("AGENT_MAX_REWRITES", "-1"),
    ],
)
def test_settings_fail_fast_on_invalid_model_configuration(
    setting: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(setting, value)
    with pytest.raises(ValidationError) as error:
        Settings()
    assert any(item["loc"] == (setting.lower(),) for item in error.value.errors())


def test_settings_allow_one_attempt_and_disabled_context_and_agent_budgets() -> None:
    settings = Settings(
        llm_retry_attempts=1,
        llm_retry_base_delay=0,
        context_token_budget=0,
        previous_summary_count=0,
        agent_max_steps=0,
        agent_max_tool_calls=0,
        agent_max_rewrites=0,
    )
    assert settings.llm_retry_attempts == 1
    assert settings.llm_retry_base_delay == 0
    assert settings.context_token_budget == settings.previous_summary_count == 0
    assert settings.agent_max_steps == settings.agent_max_tool_calls == 0
    assert settings.agent_max_rewrites == 0
