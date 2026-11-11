"""Validate provider output before it can reach stored content or vectors."""

import math

from .base import ModelResponseError

FLOAT32_MAX = 3.4028234663852886e38


def validate_embedding(vector: object) -> list[float]:
    """Chroma stores float32 vectors; reject invalid numbers before conversion."""
    if not isinstance(vector, list) or not vector:
        raise ModelResponseError("模型返回的嵌入向量为空，请重试")
    result: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ModelResponseError("模型返回的嵌入向量含无效数值，请重试")
        try:
            number = float(value)
        except OverflowError:
            raise ModelResponseError("模型返回的嵌入向量含无效数值，请重试") from None
        if not math.isfinite(number) or abs(number) > FLOAT32_MAX:
            raise ModelResponseError("模型返回的嵌入向量含无效数值，请重试")
        result.append(number)
    return result


def validate_finish_reason(reason: object, *, allow_tools: bool = False) -> None:
    # Some compatible providers omit the optional reason but still send a
    # terminal marker. A known truncation must never be treated as completion.
    if reason is None or reason == "stop":
        return
    if allow_tools and reason == "tool_calls":
        return
    if reason == "length":
        raise ModelResponseError("模型输出达到长度上限，本次内容未完成，请调整后重试")
    if reason == "content_filter":
        raise ModelResponseError("模型未能完整输出本次内容，请调整请求后重试")
    raise ModelResponseError("模型返回了不支持的结束状态，本次内容未保存")
