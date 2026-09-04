"""Guardrails 纯函数测试:步数/工具调用/重写次数三个安全阀的边界值。"""

from __future__ import annotations

from app.agents.guardrails import (
    GuardrailLimits,
    apply_rewrite_limit,
    step_budget_exceeded,
    tool_call_budget_exceeded,
)

_LIMITS = GuardrailLimits(max_steps=3, max_tool_calls=5, max_rewrites=1)


def test_step_budget_not_exceeded_below_limit():
    assert step_budget_exceeded(0, _LIMITS) is False
    assert step_budget_exceeded(2, _LIMITS) is False


def test_step_budget_exceeded_at_and_above_limit():
    assert step_budget_exceeded(3, _LIMITS) is True
    assert step_budget_exceeded(4, _LIMITS) is True


def test_tool_call_budget_not_exceeded_at_limit():
    assert tool_call_budget_exceeded(5, _LIMITS) is False


def test_tool_call_budget_exceeded_above_limit():
    assert tool_call_budget_exceeded(6, _LIMITS) is True


def test_apply_rewrite_limit_passes_through_when_not_needing_revision():
    observation = {"needs_revision": False, "feedback": "还行"}
    result = apply_rewrite_limit(observation, {}, "chapter:1", _LIMITS)
    assert result is observation


def test_apply_rewrite_limit_allows_up_to_max_rewrites():
    counts: dict[str, int] = {}
    observation = {"needs_revision": True, "feedback": "重写一下"}

    first = apply_rewrite_limit(dict(observation), counts, "chapter:1", _LIMITS)
    assert first["needs_revision"] is True
    assert counts["chapter:1"] == 1


def test_apply_rewrite_limit_forces_off_after_exceeding():
    counts: dict[str, int] = {"chapter:1": 1}  # 已重写过 1 次(= max_rewrites)
    observation = {"needs_revision": True, "feedback": "还要重写"}

    result = apply_rewrite_limit(observation, counts, "chapter:1", _LIMITS)

    assert result["needs_revision"] is False
    assert "已达最大重写次数" in str(result["feedback"])
    assert counts["chapter:1"] == 2


def test_apply_rewrite_limit_counts_are_independent_per_key():
    counts: dict[str, int] = {"chapter:1": 5}
    observation = {"needs_revision": True, "feedback": "x"}

    result = apply_rewrite_limit(observation, counts, "chapter:2", _LIMITS)

    assert result["needs_revision"] is True
    assert counts["chapter:2"] == 1
    assert counts["chapter:1"] == 5  # 未受影响
