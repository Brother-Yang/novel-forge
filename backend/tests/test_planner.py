"""Planner JSON 解析与规模约束。"""

from app.agents.planner import (
    clamp_plan,
    extract_json_object,
    fallback_title,
    plan_from_checkpoint,
)


def test_extract_json_object_from_fence() -> None:
    text = '废话\n```json\n{"title": "剑起", "volume_count": 3}\n```\n'
    assert extract_json_object(text)["title"] == "剑起"


def test_extract_json_object_raw() -> None:
    parsed = extract_json_object('{"genre": "玄幻"}')
    assert parsed["genre"] == "玄幻"


def test_clamp_plan_accepts_scale_within_target_band() -> None:
    plan = clamp_plan(
        {
            "title": "规划书名",
            "genre": "玄幻",
            "tone": "热血",
            "intro": "简介",
            "volume_count": 2,
            "chapters_per_volume": 3,
            "words_per_chapter": 2000,
            "character_count": 3,
        },
        target_words=12000,
        brief="少年觉醒",
    )
    assert plan.volume_count == 2
    assert plan.chapters_per_volume == 3
    assert plan.words_per_chapter == 2000
    assert plan.character_count == 3
    assert plan.title == "规划书名"


def test_clamp_plan_user_title_wins() -> None:
    plan = clamp_plan(
        {"title": "模型起的名", "volume_count": 2, "chapters_per_volume": 3, "words_per_chapter": 2000},
        target_words=12000,
        title="用户书名",
        brief="想法",
    )
    assert plan.title == "用户书名"


def test_clamp_plan_recalculates_when_far_from_target() -> None:
    plan = clamp_plan(
        {
            "volume_count": 2,
            "chapters_per_volume": 3,
            "words_per_chapter": 2000,
        },
        target_words=100000,
    )
    assert plan.volume_count * plan.chapters_per_volume * plan.words_per_chapter >= 50000


def test_fallback_title_uses_first_line() -> None:
    assert fallback_title("第一行\n第二行") == "第一行"
    assert fallback_title("   ") == "未命名"


def test_plan_from_checkpoint_requires_scale_keys() -> None:
    assert plan_from_checkpoint({}, title="a", genre="", tone="", intro="") is None
    plan = plan_from_checkpoint(
        {"volume_count": 2, "chapters_per_volume": 4, "words_per_chapter": 2500},
        title="a",
        genre="玄幻",
        tone="热血",
        intro="",
    )
    assert plan is not None
    assert plan.volume_count == 2
    assert plan.chapters_per_volume == 4
    assert plan.character_count == 5
