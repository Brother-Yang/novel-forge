"""Run bounded, explicitly enabled real-model probes without touching book data.

Usage: python -m evaluations.quality --output /tmp/quality-plan.json
Add --live only when the model configuration and potential cost are approved.
"""

import argparse
import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .cases import CASES, REVIEW_AXES, SYSTEM_PROMPT, QualityCase


class EvaluationClient(Protocol):
    async def chat(
        self, prompt: str, system: str = "", temperature: float = 0.8
    ) -> str: ...


@dataclass(frozen=True)
class EvaluationBudget:
    max_calls: int = 4
    max_output_tokens: int = 1024
    request_timeout_seconds: float = 120
    max_stored_characters: int = 8000

    def __post_init__(self) -> None:
        if not 1 <= self.max_calls <= len(CASES):
            raise ValueError(f"max_calls must be between 1 and {len(CASES)}")
        if not 128 <= self.max_output_tokens <= 4096:
            raise ValueError("max_output_tokens must be between 128 and 4096")
        if not 1 <= self.request_timeout_seconds <= 300:
            raise ValueError("request_timeout_seconds must be between 1 and 300")
        if not 100 <= self.max_stored_characters <= 20_000:
            raise ValueError("max_stored_characters must be between 100 and 20000")


def automated_checks(case: QualityCase, output: str) -> dict[str, object]:
    """Mechanical signals for review, deliberately not a literary quality score."""
    paragraphs = [part.strip() for part in output.splitlines() if part.strip()]
    duplicate_paragraphs = sorted(
        {part for part in paragraphs if len(part) >= 30 and paragraphs.count(part) > 1}
    )
    return {
        "nonempty": bool(output.strip()),
        "character_count": len(output),
        "missing_terms": [term for term in case.required_terms if term not in output],
        "future_fact_hits": [term for term in case.forbidden_terms if term in output],
        "duplicate_paragraph_count": len(duplicate_paragraphs),
    }


def evaluation_plan(budget: EvaluationBudget) -> dict[str, object]:
    return {
        "mode": "dry_run",
        "scope": "synthetic chapter probes, not a full-novel or end-to-end pipeline evaluation",
        "budget": asdict(budget),
        "maximum_requested_output_tokens": budget.max_calls * budget.max_output_tokens,
        "cases": [
            {"id": case.id, "title": case.title} for case in CASES[: budget.max_calls]
        ],
        "human_review": [
            {"criterion": axis, "score_1_to_5": None, "notes": ""}
            for axis in REVIEW_AXES
        ],
        "limitations": [
            "模型调用次数与请求输出上限不等于货币费用上限；输入和供应商计费另计。",
            "自动信号仅用于发现疑点，需要人工阅读；通过不代表长篇叙事质量。",
            "没有读取真实书稿，也没有运行完整生成、RAG 或数百章连续生成。",
        ],
    }


async def evaluate(
    client: EvaluationClient, budget: EvaluationBudget
) -> dict[str, object]:
    report = evaluation_plan(budget)
    report["mode"] = "live"
    results: list[dict[str, object]] = []
    for case in CASES[: budget.max_calls]:
        started = time.monotonic()
        result: dict[str, object] = {"id": case.id, "title": case.title}
        try:
            # No automatic retries: one case consumes at most one provider call.
            async with asyncio.timeout(budget.request_timeout_seconds):
                output = await client.chat(
                    case.prompt, system=SYSTEM_PROMPT, temperature=0.3
                )
            stored = output[: budget.max_stored_characters]
            result.update(
                {
                    "status": "completed",
                    "output": stored,
                    "output_truncated": len(output) > len(stored),
                    "checks": automated_checks(case, output),
                }
            )
        except Exception as exc:  # noqa: BLE001 - persist a safe failure report and stop the paid run
            # Provider exception strings can contain URLs/credentials; retain type only.
            result.update({"status": "failed", "error_type": type(exc).__name__})
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        results.append(result)
        if result["status"] == "failed":
            # An unavailable/invalid provider should not spend the remaining budget.
            break
    report["results"] = results
    report["calls_attempted"] = len(results)
    report["failed"] = any(result["status"] == "failed" for result in results)
    return report


def _live_client(budget: EvaluationBudget) -> EvaluationClient:
    # Lazy import keeps dry-run and offline unit tests free of .env/model setup.
    from app.llm import get_llm

    return get_llm(max_output_tokens=budget.max_output_tokens)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="明确允许真实模型请求和相关费用"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="新建 JSON 报告路径，不覆盖已有文件"
    )
    parser.add_argument("--max-calls", type=int, default=4)
    parser.add_argument("--max-output-tokens", type=int, default=1024)
    parser.add_argument("--request-timeout", type=float, default=120)
    args = parser.parse_args(argv)
    try:
        budget = EvaluationBudget(
            max_calls=args.max_calls,
            max_output_tokens=args.max_output_tokens,
            request_timeout_seconds=args.request_timeout,
        )
    except ValueError as exc:
        parser.error(str(exc))
    # Reserve the output before any paid request. Exclusive create protects old reports.
    with args.output.open("x", encoding="utf-8") as stream:
        report = evaluation_plan(budget)
        if args.live:
            print(
                f"Real model evaluation: <= {budget.max_calls} calls, "
                f"<= {budget.max_output_tokens} requested output tokens per call; no retries."
            )
            report = asyncio.run(evaluate(_live_client(budget), budget))
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return 1 if report.get("failed") is True else 0


if __name__ == "__main__":
    raise SystemExit(main())
