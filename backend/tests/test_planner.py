"""Planner JSON 解析与规模约束。"""

import json

import pytest

from app import models
from app.agents import planner
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
        {
            "title": "模型起的名",
            "volume_count": 2,
            "chapters_per_volume": 3,
            "words_per_chapter": 2000,
        },
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
    assert (
        plan.volume_count * plan.chapters_per_volume * plan.words_per_chapter >= 50000
    )


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


@pytest.mark.parametrize("target", [1_000_000, 2_000_000])
@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"volume_count": 12, "chapters_per_volume": 20},
        {"volume_count": 1, "chapters_per_volume": 1},
    ],
)
def test_long_plan_covers_target_without_huge_single_volume(
    target: int, raw: dict[str, object]
) -> None:
    plan = clamp_plan(raw, target_words=target)
    planned = plan.volume_count * plan.chapters_per_volume * plan.words_per_chapter
    assert target <= planned <= target * 1.05
    assert 12 < plan.volume_count <= 40
    assert 1 <= plan.chapters_per_volume <= 20
    assert plan.words_per_chapter == 2500


@pytest.mark.parametrize(
    "target", [1, 2000, 2500, 2501, 99999, 100001, 1_000_001, 2_000_001, 3_200_000]
)
def test_plan_rounds_up_to_target_with_bounded_excess(target: int) -> None:
    plan = clamp_plan({}, target_words=target)
    planned = plan.volume_count * plan.chapters_per_volume * plan.words_per_chapter
    assert target <= planned <= target + max(4000, target // 20)
    assert plan.volume_count <= 40
    assert plan.chapters_per_volume <= 20
    assert 2000 <= plan.words_per_chapter <= 4000


@pytest.mark.parametrize("target", [0, -1, 3_200_001])
def test_invalid_target_is_explicitly_rejected(target: int) -> None:
    with pytest.raises(ValueError, match="目标字数"):
        clamp_plan({}, target_words=target)


def test_model_half_target_is_not_accepted_as_complete_plan() -> None:
    plan = clamp_plan(
        {"volume_count": 12, "chapters_per_volume": 20, "words_per_chapter": 2500},
        target_words=1_000_000,
    )
    assert (
        plan.volume_count * plan.chapters_per_volume * plan.words_per_chapter
        >= 1_000_000
    )


def test_long_plan_checkpoint_roundtrip_keeps_volume_count() -> None:
    original = clamp_plan({}, target_words=2_000_000)
    restored = plan_from_checkpoint(
        dict(original.to_checkpoint()),
        title="书",
        genre="",
        tone="",
        intro="",
        target_words=2_000_000,
    )
    assert restored is not None
    assert restored.volume_count == original.volume_count
    assert restored.chapters_per_volume == original.chapters_per_volume


def test_checkpoint_below_target_is_rejected_without_resizing_existing_plan() -> None:
    with pytest.raises(ValueError, match="600000.*1000000"):
        plan_from_checkpoint(
            {"volume_count": 12, "chapters_per_volume": 20, "words_per_chapter": 2500},
            title="书",
            genre="",
            tone="",
            intro="",
            target_words=1_000_000,
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("volume_count", 41),
        ("chapters_per_volume", 21),
        ("words_per_chapter", 5000),
        ("volume_count", "bad"),
    ],
)
def test_checkpoint_outside_capacity_is_rejected_instead_of_clamped(
    key: str, value: object
) -> None:
    checkpoint: dict[str, object] = {
        "volume_count": 40,
        "chapters_per_volume": 20,
        "words_per_chapter": 2500,
    }
    checkpoint[key] = value
    with pytest.raises(ValueError, match="检查点"):
        plan_from_checkpoint(checkpoint, title="书", genre="", tone="", intro="")


async def test_oversize_target_rejected_before_contacting_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbid_llm() -> None:
        raise AssertionError("超容量目标不能调用模型")

    monkeypatch.setattr(planner, "get_llm", forbid_llm)
    with pytest.raises(ValueError, match="3200000"):
        await planner.plan_novel("想法", target_words=3_200_001)


async def test_invalid_model_json_is_rejected_instead_of_advancing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeLLM:
        async def chat(self, prompt: str, system: str = "") -> str:
            calls.append(prompt)
            return "不是JSON"

    monkeypatch.setattr(planner, "get_llm", lambda: FakeLLM())
    with pytest.raises(ValueError, match="有效的规划 JSON.*重试"):
        await planner.plan_novel("想法", target_words=2_000_000)
    assert len(calls) == 1
    assert "1 到 40" in calls[0]


async def test_json_array_is_not_accepted_as_planning_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeLLM:
        async def chat(self, prompt: str, system: str = "") -> str:
            return '[{"title":"一本书"}]'

    monkeypatch.setattr(planner, "get_llm", lambda: FakeLLM())
    with pytest.raises(ValueError, match="一个 JSON 对象"):
        await planner.plan_novel("想法")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", ""),
        ("intro", ["列表不是简介"]),
        ("volume_count", True),
        ("chapters_per_volume", "10"),
        ("words_per_chapter", 1),
        ("character_count", None),
        ("key_elements", [7]),
    ],
)
async def test_invalid_planning_field_is_not_coerced(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    payload: dict[str, object] = {
        "title": "好书", "genre": "幻想", "tone": "温暖", "intro": "简介",
        "volume_count": 1, "chapters_per_volume": 10, "words_per_chapter": 2500,
        "character_count": 3, "key_elements": [],
    }
    payload[field] = value

    class FakeLLM:
        async def chat(self, prompt: str, system: str = "") -> str:
            return json.dumps(payload)

    monkeypatch.setattr(planner, "get_llm", lambda: FakeLLM())
    with pytest.raises(ValueError, match=field):
        await planner.plan_novel("想法", target_words=25000)


@pytest.mark.parametrize("target", [57_500, 92_500, 100_000])
def test_short_plan_does_not_create_many_volumes_just_to_fit_integer_product(
    target: int,
) -> None:
    plan = clamp_plan({}, target_words=target)
    assert plan.volume_count <= 3
    assert (
        target
        <= plan.volume_count * plan.chapters_per_volume * plan.words_per_chapter
        <= target * 1.05
    )


@pytest.mark.parametrize("words", [2000, 2500, 4000])
def test_scale_bounds_and_target_deviation_across_supported_range(words: int) -> None:
    for target in range(1, planner.TARGET_WORDS_MAX + 1, 9973):
        plan = clamp_plan({"words_per_chapter": words}, target_words=target)
        planned = plan.volume_count * plan.chapters_per_volume * plan.words_per_chapter
        assert target <= planned <= target + max(plan.words_per_chapter, target // 20)
        assert 1 <= plan.volume_count <= planner.VOLUME_MAX
        assert 1 <= plan.chapters_per_volume <= planner.CHAPTERS_PER_VOLUME_MAX
        assert (
            planner.WORDS_PER_CHAPTER_MIN
            <= plan.words_per_chapter
            <= planner.WORDS_PER_CHAPTER_MAX
        )
