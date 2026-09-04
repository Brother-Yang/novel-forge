"""Tool Registry 测试:9 个工具是否都注册、schema 是否与对外的
tools.TOOLS 完全一致、read_only 元数据是否符合预期。
"""

from __future__ import annotations

from app.agents import tools

_EXPECTED_TOOL_NAMES = {
    "get_book_state",
    "write_worldbuilding",
    "create_characters",
    "create_outline",
    "create_chapter_outlines",
    "write_chapter",
    "replan_volume",
    "review_content",
    "finish_book",
}


def test_registry_has_all_nine_tools():
    for name in _EXPECTED_TOOL_NAMES:
        assert tools.registry.get(name) is not None, f"{name} 未注册进 ToolRegistry"


def test_tools_compat_list_matches_registry_schemas():
    assert tools.TOOLS == tools.registry.schemas()
    schema_names = {s["function"]["name"] for s in tools.TOOLS}
    assert schema_names == _EXPECTED_TOOL_NAMES


def test_get_book_state_and_review_content_are_read_only():
    get_book_state = tools.registry.get("get_book_state")
    review_content = tools.registry.get("review_content")
    assert get_book_state is not None and get_book_state.read_only is True
    assert review_content is not None and review_content.read_only is True


def test_mutating_tools_are_not_read_only():
    for name in _EXPECTED_TOOL_NAMES - {"get_book_state", "review_content"}:
        tool = tools.registry.get(name)
        assert tool is not None and tool.read_only is False


def test_get_unknown_tool_returns_none():
    assert tools.registry.get("no_such_tool") is None
