from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Book ----------
class BookCreate(BaseModel):
    title: str
    intro: str = ""
    genre: str = ""
    tone: str = ""
    target_words: int = 100000


class BookUpdate(BaseModel):
    title: str | None = None
    intro: str | None = None
    genre: str | None = None
    tone: str | None = None
    target_words: int | None = None


class BookOut(ORMModel):
    id: int
    title: str
    intro: str
    genre: str
    tone: str
    target_words: int
    created_at: datetime


# ---------- Volume ----------
class VolumeCreate(BaseModel):
    book_id: int
    title: str
    order_index: int = 0
    summary: str = ""


class VolumeUpdate(BaseModel):
    title: str | None = None
    order_index: int | None = None
    summary: str | None = None


class VolumeOut(ORMModel):
    id: int
    book_id: int
    title: str
    order_index: int
    summary: str


# ---------- Chapter ----------
class ChapterCreate(BaseModel):
    book_id: int
    volume_id: int | None = None
    title: str
    order_index: int = 0
    outline: str = ""


class ChapterUpdate(BaseModel):
    title: str | None = None
    volume_id: int | None = None
    order_index: int | None = None
    outline: str | None = None
    status: str | None = None


class ChapterOut(ORMModel):
    id: int
    book_id: int
    volume_id: int | None
    title: str
    order_index: int
    outline: str
    status: str
    word_count: int


class ChapterContentOut(BaseModel):
    chapter_id: int
    body: str


class ChapterContentUpdate(BaseModel):
    body: str


class ReorderItem(BaseModel):
    id: int
    order_index: int
    volume_id: int | None = None


class ReorderRequest(BaseModel):
    items: list[ReorderItem]


# ---------- Tree ----------
class TreeChapter(BaseModel):
    id: int
    title: str
    order_index: int
    status: str
    word_count: int


class TreeVolume(BaseModel):
    id: int
    title: str
    order_index: int
    chapters: list[TreeChapter]


class BookTree(BaseModel):
    volumes: list[TreeVolume]
    loose_chapters: list[TreeChapter]  # 未归卷的章节


# ---------- Character ----------
class CharacterCreate(BaseModel):
    book_id: int
    name: str
    md_content: str = ""
    order_index: int = 0


class CharacterUpdate(BaseModel):
    name: str | None = None
    md_content: str | None = None
    state_json: str | None = None
    order_index: int | None = None


class CharacterOut(ORMModel):
    id: int
    book_id: int
    name: str
    order_index: int
    md_content: str
    state_json: str


# ---------- Worldbuilding ----------
class WorldbuildingCreate(BaseModel):
    book_id: int
    category: str = "general"
    title: str
    md_content: str = ""


class WorldbuildingUpdate(BaseModel):
    category: str | None = None
    title: str | None = None
    md_content: str | None = None


class WorldbuildingOut(ORMModel):
    id: int
    book_id: int
    category: str
    title: str
    md_content: str


# ---------- PlotThread ----------
class PlotThreadOut(ORMModel):
    id: int
    book_id: int
    name: str
    status: str
    description: str


# ---------- GenerationTask ----------
class GenerationTaskOut(ORMModel):
    id: int
    book_id: int
    type: str
    target_id: int | None
    status: str
    progress: int
    error: str
    created_at: datetime


# ---------- Generate 请求 ----------
class GenerateWorldbuildingReq(BaseModel):
    book_id: int


class GenerateCharactersReq(BaseModel):
    book_id: int
    count: int = 5


class GenerateOutlineReq(BaseModel):
    book_id: int
    volume_count: int = 3


class GenerateChapterOutlinesReq(BaseModel):
    volume_id: int
    chapter_count: int = 10


class GenerateChapterReq(BaseModel):
    chapter_id: int


class GenerateBookReq(BaseModel):
    book_id: int
