"""智能体决策循环测试:用脚本化 LLM 与假工具验证循环控制逻辑
(finish 条件、步数上限、工具调用次数上限、重写次数上限、后端能力探测、
工具异常不导致任务崩溃),不依赖真实模型与真实数据库。
"""

from __future__ import annotations

import json

import pytest

from app.agents import orchestrator, planner, tools
from app.config import settings
from app.llm.base import ToolCall, ToolChatResult


@pytest.fixture(autouse=True)
def _tool_calling_backend(monkeypatch):
    """默认让能力探测放行(智能体逻辑本身与后端无关),

    仅 test_agent_reports_error_when_backend_lacks_tool_calling 显式覆盖为
    不支持的后端,验证探测生效。
    """
    monkeypatch.setattr(settings, "llm_provider", "openai")


class FakeBook:
    id = "book-1"
    title = "测试之书"
    brief = "一个测试用的故事想法"
    intro = ""
    target_words = 12000


class FakeDB:
    def get(self, model: object, id_: object) -> FakeBook | None:
        return FakeBook()


class ScriptedLLM:
    """按顺序返回预设响应,脚本耗尽仍被调用则视为测试失败。"""

    def __init__(self, results: list[ToolChatResult]) -> None:
        self._results = list(results)

    async def chat_with_tools(
        self, messages: object, tool_defs: object, temperature: float = 0.7
    ) -> ToolChatResult:
        assert self._results, "脚本已耗尽,循环未按预期终止"
        return self._results.pop(0)


def _call(name: str, **kwargs: object) -> ToolChatResult:
    return ToolChatResult(content="", tool_calls=[ToolCall(id="1", name=name, arguments=kwargs)])


def _finish(reason: str = "完成") -> ToolChatResult:
    return _call("finish_book", reason=reason)


async def test_agent_finishes_on_finish_book(monkeypatch):
    llm = ScriptedLLM([_call("write_worldbuilding"), _finish()])
    monkeypatch.setattr(orchestrator, "get_llm", lambda: llm)

    async def fake_dispatch(db, book_id, name, args):
        return {"ok": True, "logs": [f"{name} 完成"]}

    monkeypatch.setattr(tools.registry, "dispatch", fake_dispatch)

    events = [ev async for ev in orchestrator.run_agent(FakeDB(), "book-1")]
    assert events[-1] == ("done", "")
    assert any(ev == "log" and "write_worldbuilding 完成" in data for ev, data in events)


async def test_agent_stops_at_max_steps(monkeypatch):
    llm = ScriptedLLM([_call("write_worldbuilding") for _ in range(10)])
    monkeypatch.setattr(orchestrator, "get_llm", lambda: llm)

    async def fake_dispatch(db, book_id, name, args):
        return {"ok": True}

    monkeypatch.setattr(tools.registry, "dispatch", fake_dispatch)
    monkeypatch.setattr(settings, "agent_max_steps", 3)

    events = [ev async for ev in orchestrator.run_agent(FakeDB(), "book-1")]
    assert events[-1] == ("done", "")
    assert any("最大决策步数" in data for ev, data in events if ev == "log")


async def test_agent_stops_at_max_tool_calls(monkeypatch):
    llm = ScriptedLLM([_call("write_worldbuilding") for _ in range(20)])
    monkeypatch.setattr(orchestrator, "get_llm", lambda: llm)

    async def fake_dispatch(db, book_id, name, args):
        return {"ok": True}

    monkeypatch.setattr(tools.registry, "dispatch", fake_dispatch)
    monkeypatch.setattr(settings, "agent_max_steps", 50)
    monkeypatch.setattr(settings, "agent_max_tool_calls", 2)

    events = [ev async for ev in orchestrator.run_agent(FakeDB(), "book-1")]
    assert events[-1][0] == "error"
    assert "工具调用次数上限" in events[-1][1]


async def test_agent_readonly_tool_calls_do_not_count_toward_budget(monkeypatch):
    """get_book_state 是只读工具,提示词鼓励模型每步都调用它;
    即使调用次数远超上限,也不应触发"工具调用次数上限"熔断。"""
    script = [_call("get_book_state") for _ in range(10)] + [_finish()]
    llm = ScriptedLLM(script)
    monkeypatch.setattr(orchestrator, "get_llm", lambda: llm)

    async def fake_dispatch(db, book_id, name, args):
        return {"ok": True}

    monkeypatch.setattr(tools.registry, "dispatch", fake_dispatch)
    monkeypatch.setattr(settings, "agent_max_steps", 20)
    monkeypatch.setattr(settings, "agent_max_tool_calls", 3)

    events = [ev async for ev in orchestrator.run_agent(FakeDB(), "book-1")]
    assert events[-1] == ("done", "")


async def test_agent_review_rewrite_limit(monkeypatch):
    """无论模型怎么要求,超过 agent_max_rewrites 后 needs_revision 必须被强制置 false。"""

    async def fake_dispatch(db, book_id, name, args):
        if name == "review_content":
            return {"needs_revision": True, "feedback": "重写一下"}
        return {"ok": True}

    monkeypatch.setattr(tools.registry, "dispatch", fake_dispatch)
    monkeypatch.setattr(settings, "agent_max_rewrites", 1)
    monkeypatch.setattr(settings, "agent_max_steps", 20)
    monkeypatch.setattr(settings, "agent_max_tool_calls", 20)

    script = [
        _call("review_content", target_type="chapter", target_id=1) for _ in range(3)
    ] + [_finish()]
    llm = ScriptedLLM(script)
    monkeypatch.setattr(orchestrator, "get_llm", lambda: llm)

    events = [ev async for ev in orchestrator.run_agent(FakeDB(), "book-1")]
    observations = [json.loads(data) for ev, data in events if ev == "observation"]
    assert observations[0]["needs_revision"] is True, "第1次重写请求应放行"
    assert observations[1]["needs_revision"] is False, "第2次(超过上限1次)应被强制关闭"
    assert "已达最大重写次数" in observations[1]["feedback"]
    assert observations[2]["needs_revision"] is False
    assert events[-1] == ("done", "")


async def test_agent_tool_error_becomes_observation_not_crash(monkeypatch):
    async def failing_dispatch(db, book_id, name, args):
        raise tools.ToolError("参数非法:章节不存在")

    monkeypatch.setattr(tools.registry, "dispatch", failing_dispatch)
    llm = ScriptedLLM([_call("write_chapter", chapter_id=999), _finish()])
    monkeypatch.setattr(orchestrator, "get_llm", lambda: llm)

    events = [ev async for ev in orchestrator.run_agent(FakeDB(), "book-1")]
    assert events[-1] == ("done", "")


async def test_agent_no_tool_calls_prompts_continue(monkeypatch):
    """模型只给了一段文字没有调用任何工具时,应提醒它继续,而不是直接结束。"""
    llm = ScriptedLLM(
        [ToolChatResult(content="我在思考接下来该做什么", tool_calls=[]), _finish()]
    )
    monkeypatch.setattr(orchestrator, "get_llm", lambda: llm)

    async def fake_dispatch(db, book_id, name, args):
        return {"ok": True}

    monkeypatch.setattr(tools.registry, "dispatch", fake_dispatch)

    events = [ev async for ev in orchestrator.run_agent(FakeDB(), "book-1")]
    assert ("thought", "我在思考接下来该做什么") in events
    assert events[-1] == ("done", "")


async def test_agent_context_window_bounds_message_growth(monkeypatch):
    """长篇场景下(几十步决策)发给模型的消息列表不能无限增长,
    否则真实调用会越跑越贵、最终超出模型上下文窗口报错。"""
    monkeypatch.setattr(settings, "agent_max_steps", 30)
    monkeypatch.setattr(settings, "agent_max_tool_calls", 100)

    received: list[list[dict[str, object]]] = []

    class RecordingLLM(ScriptedLLM):
        async def chat_with_tools(
            self, messages: object, tool_defs: object, temperature: float = 0.7
        ) -> ToolChatResult:
            received.append(list(messages))  # type: ignore[arg-type]
            return await super().chat_with_tools(messages, tool_defs, temperature)

    step_count = 20
    script = [_call("write_worldbuilding") for _ in range(step_count)] + [_finish()]
    llm = RecordingLLM(script)
    monkeypatch.setattr(orchestrator, "get_llm", lambda: llm)

    async def fake_dispatch(db, book_id, name, args):
        return {"ok": True, "logs": ["写了一大段设定内容" * 50]}

    monkeypatch.setattr(tools.registry, "dispatch", fake_dispatch)

    events = [ev async for ev in orchestrator.run_agent(FakeDB(), "book-1")]
    assert events[-1] == ("done", "")

    lengths = [len(msgs) for msgs in received]
    # 未裁剪的话,最后一次调用应该带着 20 步的历史(约 42 条消息);
    # 裁剪之后必须显著小于这个量级,且后期步数增加时不再继续增长(已收敛)。
    assert lengths[-1] < 20, "窗口裁剪未生效,消息数量随步数线性增长"
    assert lengths[-1] == lengths[-2], "超过窗口大小后,消息数量应保持恒定不再增长"

    # system/user 首条上下文始终保留,模型不会因为裁剪而忘记任务本身。
    windowed = received[-1]
    assert windowed[0]["role"] == "system"
    assert windowed[1]["role"] == "user"


async def test_agent_reports_error_when_backend_lacks_tool_calling(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")

    events = [ev async for ev in orchestrator.run_agent(FakeDB(), "book-1")]
    assert events == [("error", events[0][1])]
    assert "function calling" in events[0][1]


async def test_agent_plans_placeholder_title_before_loop(monkeypatch):
    class UnnamedBook:
        id = "book-1"
        title = "未命名"
        brief = "AI时代，中年程序员靠古法编程力挽狂澜"
        intro = ""
        genre = ""
        tone = ""
        target_words = 10000

    book = UnnamedBook()

    class DB:
        def get(self, model: object, id_: object) -> UnnamedBook:
            return book

        def commit(self) -> None:
            return None

    async def fake_plan(brief: str, **kwargs: object) -> planner.NovelPlan:
        assert kwargs.get("title") in ("", None)
        return planner.NovelPlan(
            title="古法残编",
            genre="都市",
            tone="讽刺",
            intro="中年程序员在 AI 浪潮中用古法编程杀出一条路。",
            volume_count=1,
            chapters_per_volume=2,
            words_per_chapter=2000,
            character_count=3,
        )

    monkeypatch.setattr(planner, "plan_novel", fake_plan)
    llm = ScriptedLLM([_finish()])
    monkeypatch.setattr(orchestrator, "get_llm", lambda: llm)

    events = [ev async for ev in orchestrator.run_agent(DB(), "book-1")]
    assert events[-1] == ("done", "")
    assert book.title == "古法残编"
    assert book.intro == "中年程序员在 AI 浪潮中用古法编程杀出一条路。"
    assert book.genre == "都市"
