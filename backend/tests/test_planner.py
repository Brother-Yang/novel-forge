"""Planner JSON 解析与规模约束。"""

import json

from app import models
from app.agents.planner import (
    NovelPlan,
    apply_plan_to_book,
    clamp_plan,
    extract_json_object,
    fallback_title,
    format_key_elements,
    is_placeholder_title,
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


def test_clamp_plan_does_not_copy_brief_into_intro() -> None:
    plan = clamp_plan({}, target_words=10000, brief="用户原话不应当简介")
    assert plan.intro == ""
    assert plan.title == "未命名"


def test_is_placeholder_title_treats_brief_snippet_as_not_user_title() -> None:
    brief = "AI时代，中年程序员靠古法编程力挽狂澜"
    assert is_placeholder_title("未命名", brief)
    assert is_placeholder_title("", brief)
    assert is_placeholder_title(fallback_title(brief), brief)
    assert not is_placeholder_title("古法残编", brief)


def test_apply_plan_replaces_fallback_title_and_fills_intro() -> None:
    brief = "AI时代，中年程序员靠古法编程力挽狂澜"
    book = models.Book(
        title=fallback_title(brief),
        brief=brief,
        intro="",
        genre="",
        tone="",
        target_words=10000,
    )
    plan = NovelPlan(
        title="古法残编",
        genre="都市",
        tone="讽刺",
        intro="中年程序员在 AI 浪潮中用古法编程杀出一条路。",
        volume_count=1,
        chapters_per_volume=3,
        words_per_chapter=2000,
        character_count=3,
        key_elements=["场景:AI 病毒格式化全球服务器"],
    )
    apply_plan_to_book(book, plan)
    assert book.title == "古法残编"
    assert book.intro == plan.intro
    assert book.genre == "都市"
    assert book.tone == "讽刺"
    assert book.brief == brief
    assert json.loads(book.key_elements) == ["场景:AI 病毒格式化全球服务器"]


def test_apply_plan_keeps_user_chosen_title() -> None:
    book = models.Book(
        title="我起的书名",
        brief="随便一个想法",
        intro="已有简介",
        genre="科幻",
        tone="冷静",
        target_words=10000,
    )
    plan = NovelPlan(
        title="模型起的名",
        genre="玄幻",
        tone="热血",
        intro="模型简介",
        volume_count=1,
        chapters_per_volume=3,
        words_per_chapter=2000,
        character_count=3,
        key_elements=[],
    )
    apply_plan_to_book(book, plan)
    assert book.title == "我起的书名"
    assert book.intro == "已有简介"
    assert book.genre == "科幻"
    assert book.tone == "冷静"


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
    assert plan.key_elements == []


def test_plan_from_checkpoint_restores_key_elements() -> None:
    plan = plan_from_checkpoint(
        {"volume_count": 2, "chapters_per_volume": 4, "words_per_chapter": 2500},
        title="a",
        genre="玄幻",
        tone="热血",
        intro="",
        key_elements=json.dumps(["场景:裸机格式化"], ensure_ascii=False),
    )
    assert plan is not None
    assert plan.key_elements == ["场景:裸机格式化"]


def test_clamp_plan_extracts_explicit_key_elements() -> None:
    plan = clamp_plan(
        {
            "title": "全员AI写代码,只有我用手写二进制",
            "volume_count": 1,
            "chapters_per_volume": 3,
            "words_per_chapter": 2000,
            "key_elements": [
                "场景:全球服务器被AI病毒格式化至裸机,所有高级语言无法运行",
                "主角行动:掏出电烙铁现场焊接寄存器,用机器语言逐字输入",
                "",
                "  ",
            ],
        },
        target_words=6000,
        brief='写一个"全员AI写代码,只有我用手写二进制"的装逼打脸故事。',
    )
    assert plan.title == "全员AI写代码,只有我用手写二进制"
    assert plan.key_elements == [
        "场景:全球服务器被AI病毒格式化至裸机,所有高级语言无法运行",
        "主角行动:掏出电烙铁现场焊接寄存器,用机器语言逐字输入",
    ]


def test_clamp_plan_key_elements_non_list_falls_back_to_empty() -> None:
    plan = clamp_plan(
        {"key_elements": "不是列表"},
        target_words=10000,
        brief="随便一个想法",
    )
    assert plan.key_elements == []


def test_clamp_plan_key_elements_truncated_to_max() -> None:
    plan = clamp_plan(
        {"key_elements": [f"要素{i}" for i in range(20)]},
        target_words=10000,
        brief="想法",
    )
    assert len(plan.key_elements) == 10


def test_format_key_elements_empty_list() -> None:
    assert format_key_elements("[]") == "(无,由你自由创作)"
    assert format_key_elements("") == "(无,由你自由创作)"
    assert format_key_elements("not json") == "(无,由你自由创作)"


def test_format_key_elements_renders_bullet_lines() -> None:
    raw = json.dumps(["场景设定", "结局反转"], ensure_ascii=False)
    assert format_key_elements(raw) == "- 场景设定\n- 结局反转"
