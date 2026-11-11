"""Cap actual provider requests across API, tasks, indexing and retries."""

from ..config import settings
from ..limits import WindowBudget

_window = WindowBudget()


class ModelBudgetExceeded(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"模型调用已达到本时段限额，请在 {retry_after} 秒后重试")


def require_model_budget() -> None:
    retry_after = _window.consume(
        "provider", settings.llm_requests_per_window, settings.llm_window_seconds
    )
    if retry_after is not None:
        raise ModelBudgetExceeded(retry_after)
