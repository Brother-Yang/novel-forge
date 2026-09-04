"""智能体的安全护栏(架构文档里的 Guardrails 模块)。

把原来内联在 orchestrator.py 循环里的三个安全阀抽成纯函数,只做判断/加工,
不访问数据库、不调用 LLM,方便独立测试,也方便未来接入更多护栏(比如
高风险工具的人工审批)而不必改动循环本身。

当前应用没有会读取任意外部内容的工具(无网页搜索),也没有真正意义上的
"高风险/破坏性"工具(都是在书本范围内追加/生成内容),因此这里只做循环
安全阀,不引入 prompt injection 防护或审批流程——引入这些超出当前风险面
的防护属于过度设计。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuardrailLimits:
    """一次智能体运行允许的安全上限,直接对应 config.py 里的三个设置项。"""

    max_steps: int
    max_tool_calls: int
    max_rewrites: int


def step_budget_exceeded(step: int, limits: GuardrailLimits) -> bool:
    """step 从 0 开始计数;达到 max_steps 步即应停止决策循环。"""
    return step >= limits.max_steps


def tool_call_budget_exceeded(tool_call_count: int, limits: GuardrailLimits) -> bool:
    """tool_call_count 是本次运行累计已执行的工具调用次数(从 1 开始计数)。"""
    return tool_call_count > limits.max_tool_calls


def apply_rewrite_limit(
    observation: dict[str, object],
    rewrite_counts: dict[str, int],
    key: str,
    limits: GuardrailLimits,
) -> dict[str, object]:
    """review_content 工具要求重写超过上限后,强制把 needs_revision 关掉,
    避免模型对同一处内容无限重写、耗尽额度却没有推进创作。

    rewrite_counts 由调用方维护并原地更新(同一 key 的计数在多次调用间累积)。
    """
    if not observation.get("needs_revision"):
        return observation
    rewrite_counts[key] = rewrite_counts.get(key, 0) + 1
    if rewrite_counts[key] > limits.max_rewrites:
        observation = dict(observation)
        observation["needs_revision"] = False
        observation["feedback"] = "已达最大重写次数,请继续推进,不要再重写此项。"
    return observation
