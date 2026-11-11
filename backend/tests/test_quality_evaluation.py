import asyncio
import json
from pathlib import Path

import pytest

from evaluations import quality
from evaluations.cases import CASES
from evaluations.quality import EvaluationBudget, automated_checks, evaluate, main


class ScriptedClient:
    def __init__(self, replies: list[str | Exception]) -> None:
        super().__init__()
        self.replies = replies
        self.calls: list[str] = []

    async def chat(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ) -> str:
        self.calls.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


async def test_call_budget_and_bounded_output() -> None:
    client = ScriptedClient(
        ["正文" * 500, "林舟在雾港遇到许禾", "must not be requested"]
    )
    report = await evaluate(
        client, EvaluationBudget(max_calls=2, max_stored_characters=100)
    )
    results = report["results"]
    assert isinstance(results, list)
    assert len(client.calls) == report["calls_attempted"] == 2
    assert len(results[0]["output"]) == 100
    assert results[0]["output_truncated"] is True
    assert results[0]["checks"]["character_count"] == 1000
    assert "literary_score" not in report


async def test_provider_failure_stops_budget_without_leaking_credentials() -> None:
    client = ScriptedClient(
        [ValueError("https://secret.example?token=private"), "not requested"]
    )
    report = await evaluate(client, EvaluationBudget())
    assert len(client.calls) == 1
    assert report["failed"] is True
    serialized = json.dumps(report)
    assert "ValueError" in serialized
    assert "private" not in serialized
    assert "secret.example" not in serialized


async def test_timeout_cancels_request_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleaned = asyncio.Event()

    class SlowClient:
        async def chat(
            self, prompt: str, system: str = "", temperature: float = 0.8
        ) -> str:
            try:
                await asyncio.Event().wait()
                return ""
            finally:
                cleaned.set()

    original_timeout = asyncio.timeout
    monkeypatch.setattr(
        quality.asyncio, "timeout", lambda _seconds: original_timeout(0.01)
    )
    report = await evaluate(SlowClient(), EvaluationBudget())
    assert cleaned.is_set()
    assert report["calls_attempted"] == 1
    assert "TimeoutError" in json.dumps(report)


def test_dry_run_does_not_construct_or_call_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_model(_budget: EvaluationBudget) -> ScriptedClient:
        raise AssertionError("dry-run must not construct a real client")

    monkeypatch.setattr(quality, "_live_client", unexpected_model)
    output = tmp_path / "report.json"
    assert main(["--output", str(output), "--max-calls", "2"]) == 0
    report = json.loads(output.read_text())
    assert report["mode"] == "dry_run"
    assert report["maximum_requested_output_tokens"] == 2048
    assert "results" not in report
    assert all(axis["score_1_to_5"] is None for axis in report["human_review"])


def test_existing_report_prevents_paid_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"
    output.write_text("existing report")

    def unexpected_model(_budget: EvaluationBudget) -> ScriptedClient:
        raise AssertionError("existing report must be detected before a model call")

    monkeypatch.setattr(quality, "_live_client", unexpected_model)
    with pytest.raises(FileExistsError):
        main(["--live", "--output", str(output)])
    assert output.read_text() == "existing report"


def test_live_mode_passes_output_budget_and_returns_failed_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    budgets: list[EvaluationBudget] = []

    def fake_client(budget: EvaluationBudget) -> ScriptedClient:
        budgets.append(budget)
        return ScriptedClient([RuntimeError("temporary failure")])

    monkeypatch.setattr(quality, "_live_client", fake_client)
    output = tmp_path / "report.json"
    assert main(["--live", "--output", str(output), "--max-output-tokens", "512"]) == 1
    assert budgets[0].max_output_tokens == 512
    assert json.loads(output.read_text())["calls_attempted"] == 1


def test_checks_are_review_signals_not_quality_scores() -> None:
    repeated = "林舟在雾港等待许禾，他反复确认港口已关闭，决定等天亮再寻找新的线索。"
    checks = automated_checks(CASES[0], f"{repeated}\n{repeated}\n赤月钥匙")
    assert checks["missing_terms"] == []
    assert checks["future_fact_hits"] == ["赤月钥匙"]
    assert checks["duplicate_paragraph_count"] == 1
    assert "score" not in checks


@pytest.mark.parametrize(
    "calls,tokens,timeout", [(0, 1024, 120), (5, 1024, 120), (1, 10, 120), (1, 1024, 0)]
)
def test_invalid_budget_is_rejected(calls: int, tokens: int, timeout: float) -> None:
    with pytest.raises(ValueError):
        EvaluationBudget(
            max_calls=calls, max_output_tokens=tokens, request_timeout_seconds=timeout
        )
