from datetime import datetime
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

# 分卷细纲每次最多 20 章；长篇通过增加卷数扩展，字数是规划目标而非模型产出保证。
NOVEL_VOLUME_MAX = 40
NOVEL_CHAPTERS_PER_VOLUME_MAX = 20
NOVEL_WORDS_PER_CHAPTER_MIN = 2000
NOVEL_WORDS_PER_CHAPTER_MAX = 4000
# 手工重写允许比自动规划更长的单章，与 Agent 写章工具上限一致。
CHAPTER_GENERATION_WORDS_MAX = 6000
CHARACTERS_PER_GENERATION_MAX = 8
NOVEL_TARGET_WORDS_MAX = (
    NOVEL_VOLUME_MAX * NOVEL_CHAPTERS_PER_VOLUME_MAX * NOVEL_WORDS_PER_CHAPTER_MAX
)
_TARGET_WORDS_DESCRIPTION = "规划目标字数，非实际生成字数保证；当前上限 320 万字"

# 排序值经过浏览器 number，再写入 SQLite；两端都必须能精确表示。
ORDER_INDEX_MAX = 2**53 - 1
OrderIndex = Annotated[int, Field(ge=-ORDER_INDEX_MAX, le=ORDER_INDEX_MAX)]
ChapterStatus = Literal["empty", "outline", "drafting", "done"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PatchModel(BaseModel):
    """Omitted fields stay unchanged; only explicitly nullable fields accept null."""

    nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @field_validator("*", mode="after")
    @classmethod
    def reject_unexpected_nulls(cls, value: object, info: ValidationInfo) -> object:
        if value is None and info.field_name not in cls.nullable_fields:
            raise ValueError("不能为 null；不修改时请省略该字段")
        return value


# ---------- Book ----------
class BookCreate(BaseModel):
    title: str
    intro: str = ""
    brief: str = ""
    genre: str = ""
    tone: str = ""
    target_words: int = Field(
        default=100000,
        ge=1,
        le=NOVEL_TARGET_WORDS_MAX,
        description=_TARGET_WORDS_DESCRIPTION,
    )


class BookUpdate(PatchModel):
    title: str | None = None
    intro: str | None = None
    brief: str | None = None
    genre: str | None = None
    tone: str | None = None
    target_words: int | None = Field(
        default=None,
        ge=1,
        le=NOVEL_TARGET_WORDS_MAX,
        description=_TARGET_WORDS_DESCRIPTION,
    )


class BookOut(ORMModel):
    id: str
    title: str
    intro: str
    brief: str
    genre: str
    tone: str
    target_words: int
    key_elements: str
    created_at: datetime


# ---------- Volume ----------
class VolumeCreate(BaseModel):
    book_id: str
    title: str
    order_index: OrderIndex = 0
    summary: str = ""


class VolumeUpdate(PatchModel):
    title: str | None = None
    order_index: OrderIndex | None = None
    summary: str | None = None


class VolumeOut(ORMModel):
    id: str
    book_id: str
    title: str
    order_index: int
    summary: str


# ---------- Chapter ----------
class ChapterCreate(BaseModel):
    book_id: str
    volume_id: str | None = None
    title: str
    order_index: OrderIndex = 0
    outline: str = ""


class ChapterUpdate(PatchModel):
    nullable_fields = frozenset({"volume_id"})

    title: str | None = None
    volume_id: str | None = None
    order_index: OrderIndex | None = None
    outline: str | None = None
    status: ChapterStatus | None = None


class ChapterOut(ORMModel):
    id: str
    book_id: str
    volume_id: str | None
    title: str
    order_index: int
    outline: str
    status: str
    word_count: int


class ChapterContentOut(BaseModel):
    chapter_id: str
    body: str
    version: str


class ChapterContentSaved(ChapterContentOut):
    status: str
    word_count: int


class ChapterContentUpdate(BaseModel):
    body: str
    expected_version: str | None = None


class ReorderItem(BaseModel):
    id: str
    order_index: OrderIndex
    volume_id: str | None = None


class ReorderRequest(BaseModel):
    items: list[ReorderItem]


# ---------- Tree ----------
class TreeChapter(BaseModel):
    id: str
    title: str
    order_index: int
    status: str
    word_count: int


class TreeVolume(BaseModel):
    id: str
    title: str
    order_index: int
    chapters: list[TreeChapter]


class BookTree(BaseModel):
    volumes: list[TreeVolume]
    loose_chapters: list[TreeChapter]  # 未归卷的章节


# ---------- Character ----------
class CharacterCreate(BaseModel):
    book_id: str
    name: str
    md_content: str = ""
    order_index: OrderIndex = 0


class CharacterUpdate(PatchModel):
    name: str | None = None
    md_content: str | None = None
    state_json: str | None = None
    order_index: OrderIndex | None = None


class CharacterOut(ORMModel):
    id: str
    book_id: str
    name: str
    order_index: int
    md_content: str
    state_json: str
    derived_state: dict[str, str] = Field(default_factory=dict)


# ---------- Worldbuilding ----------
class WorldbuildingCreate(BaseModel):
    book_id: str
    category: str = "general"
    title: str
    md_content: str = ""


class WorldbuildingUpdate(PatchModel):
    category: str | None = None
    title: str | None = None
    md_content: str | None = None


class WorldbuildingOut(ORMModel):
    id: str
    book_id: str
    category: str
    title: str
    md_content: str


# ---------- PlotThread ----------
class PlotThreadOut(ORMModel):
    id: str
    book_id: str
    name: str
    status: str
    description: str
    derived_status: Literal["active", "closed"] | None = None


# ---------- GenerationTask ----------
class GenerationTaskOut(ORMModel):
    id: str
    book_id: str
    type: str
    target_id: str | None
    status: str
    progress: int
    error: str
    checkpoint_json: str
    created_at: datetime


class AgentTraceEntryOut(ORMModel):
    id: str
    task_id: str
    step_index: int
    kind: str
    tool_name: str | None
    payload: str
    created_at: datetime


class AgentTracePageEntryOut(AgentTraceEntryOut):
    sequence: int


class AgentTracePageOut(BaseModel):
    entries: list[AgentTracePageEntryOut]
    has_more: bool


# ---------- Generate 请求 ----------
class GenerateWorldbuildingReq(BaseModel):
    book_id: str


class GenerateCharactersReq(BaseModel):
    book_id: str
    count: int = Field(default=5, ge=1, le=CHARACTERS_PER_GENERATION_MAX)


class GenerateOutlineReq(BaseModel):
    book_id: str
    volume_count: int = Field(default=3, ge=1, le=NOVEL_VOLUME_MAX)


class GenerateChapterOutlinesReq(BaseModel):
    volume_id: str
    chapter_count: int = Field(default=10, ge=1, le=NOVEL_CHAPTERS_PER_VOLUME_MAX)


class GenerateChapterReq(BaseModel):
    chapter_id: str
    words: int = Field(default=2000, ge=1, le=CHAPTER_GENERATION_WORDS_MAX)
    expected_version: str | None = None


class GenerateBookReq(BaseModel):
    book_id: str


class GenerateNovelReq(BaseModel):
    brief: str
    title: str | None = None
    genre: str = ""
    tone: str = ""
    target_words: int = Field(
        default=100000,
        ge=1,
        le=NOVEL_TARGET_WORDS_MAX,
        description=_TARGET_WORDS_DESCRIPTION,
    )


class GenerateNovelFromBookReq(BaseModel):
    book_id: str


class NovelGenerationOut(BaseModel):
    book: BookOut
    task: GenerationTaskOut


class GenerateAgentReq(BaseModel):
    brief: str
    title: str | None = None
    genre: str = ""
    tone: str = ""
    target_words: int = Field(
        default=100000,
        ge=1,
        le=NOVEL_TARGET_WORDS_MAX,
        description=_TARGET_WORDS_DESCRIPTION,
    )


class GenerateAgentFromBookReq(BaseModel):
    book_id: str
