"""验证小说工具清单、供应商契约和写操作预算分类。"""

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


def test_registry_schemas_match_available_tools():
    schema_names: set[str] = set()
    for schema in tools.registry.schemas():
        function = schema["function"]
        assert isinstance(function, dict)
        name = function["name"]
        assert isinstance(name, str)
        schema_names.add(name)
    assert schema_names == _EXPECTED_TOOL_NAMES


def test_only_get_book_state_is_read_only():
    get_book_state = tools.registry.get("get_book_state")
    review_content = tools.registry.get("review_content")
    assert get_book_state is not None and get_book_state.read_only is True
    assert review_content is not None and review_content.read_only is False


def test_mutating_tools_are_not_read_only():
    for name in _EXPECTED_TOOL_NAMES - {"get_book_state"}:
        tool = tools.registry.get(name)
        assert tool is not None and tool.read_only is False


def test_get_unknown_tool_returns_none():
    assert tools.registry.get("no_such_tool") is None
