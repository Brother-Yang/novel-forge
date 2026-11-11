"""结构生成的格式约束和事务边界。解析整批输出成功后才能修改数据库。"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from unicodedata import normalize

from sqlalchemy.orm import Session

from .errors import GenerationError

WORLD_MIN = 4
WORLD_MAX = 6
CHARACTER_FIELDS = ("身份", "性格", "外貌", "背景", "目标与动机", "与其他人物关系")

_HEADING = re.compile(r"(?m)^(#{1,6})(?:[ \t]+|$)(.*)$")
_THREAD_HEADING = re.compile(r"(?m)^#[ \t]+剧情线程[ \t]*$")
_FIELD = re.compile(
    r"(?m)^[ \t]*[-*+][ \t]+(?:\*\*)?([^:*：\n]+)(?:\*\*)?[ \t]*[:：][ \t]*(\S[^\n]*)$"
)
_DIGITS = {char: number for number, char in enumerate("零一二三四五六七八九")}


class InvalidStage(GenerationError):
    """可呈现给用户的阶段结构错误，不包含原始模型输出。"""


@dataclass(frozen=True)
class Section:
    title: str
    body: str


def identity(title: str) -> str:
    return " ".join(normalize("NFKC", title).split()).casefold()


def require_unique_titles(sections: Sequence[Section], label: str) -> None:
    if len({identity(section.title) for section in sections}) != len(sections):
        raise InvalidStage(f"{label}标题重复")


def require_distinct(
    new: Sequence[Section], existing: Sequence[Section], label: str
) -> None:
    require_unique_titles(new, label)
    if {identity(section.title) for section in new} & {
        identity(section.title) for section in existing
    }:
        raise InvalidStage(f"模型输出的{label}与已有内容重名")


def validate_existing(
    sections: Sequence[Section],
    label: str,
    *,
    maximum: int | None = None,
) -> None:
    if maximum is not None and len(sections) > maximum:
        raise InvalidStage(
            f"已有{label} {len(sections)} 项，超出规划的 {maximum} 项；"
            "请调整目录或规划后重试，已有内容已保留"
        )
    if any(not item.title.strip() or not item.body.strip() for item in sections):
        raise InvalidStage(f"已有{label}的标题或内容为空；请补全后重试，已有内容已保留")
    require_unique_titles(sections, f"已有{label}")


def _strip_markdown_fence(text: str) -> str:
    text = text.strip().replace("\r\n", "\n")
    if fenced := re.fullmatch(
        r"```(?:markdown|md)?[ \t]*\n(.*?)\n```", text, re.DOTALL
    ):
        return fenced[1].strip()
    return text


def parse_sections(
    text: str,
    *,
    level: int,
    minimum: int,
    maximum: int,
    label: str,
    title_limit: int = 255,
) -> list[Section]:
    text = _strip_markdown_fence(text)
    headings = [h for h in _HEADING.finditer(text) if len(h[1]) <= level]
    if (
        not headings
        or headings[0].start() != 0
        or any(len(h[1]) != level for h in headings)
    ):
        raise InvalidStage(f"{label}格式不正确：每项须以 {'#' * level} 标题开头")
    if not minimum <= len(headings) <= maximum:
        expected = str(minimum) if minimum == maximum else f"{minimum} 到 {maximum}"
        raise InvalidStage(f"{label}需要 {expected} 项，模型返回了 {len(headings)} 项")
    sections: list[Section] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        title = heading[2].strip()
        body = text[heading.end() : end].strip()
        if not 1 <= len(title) <= title_limit or not body:
            raise InvalidStage(f"{label}第 {index + 1} 项缺少有效标题或内容")
        sections.append(Section(title, body))
    require_unique_titles(sections, label)
    return sections


def validate_character_fields(sections: Sequence[Section]) -> None:
    for index, section in enumerate(sections, 1):
        fields = {match[1].strip() for match in _FIELD.finditer(section.body)}
        missing = [field for field in CHARACTER_FIELDS if field not in fields]
        if missing:
            raise InvalidStage(f"人物卡第 {index} 项缺少字段：{'、'.join(missing)}")


def ordinal(title: str, unit: str) -> int | None:
    match = re.match(
        rf"^第\s*([0-9零一二三四五六七八九十两]+)\s*{unit}", normalize("NFKC", title)
    )
    if not match:
        return None
    number = match[1].replace("两", "二")
    if number.isdecimal():
        # 超出本项目范围的编号也属于非法输出，避免转换过大的整数。
        return int(number) if len(number) <= 3 else None
    if number in _DIGITS:
        return _DIGITS[number]
    if re.fullmatch(r"[一二三四五六七八九]?十[一二三四五六七八九]?", number):
        tens, _, ones = number.partition("十")
        return 10 * _DIGITS.get(tens, 1) + _DIGITS.get(ones, 0)
    return None


def validate_ordinals(
    sections: Sequence[Section], expected: Sequence[int], unit: str
) -> None:
    if [ordinal(section.title, unit) for section in sections] != list(expected):
        raise InvalidStage(
            f"{unit}编号必须按顺序覆盖 {'、'.join(str(number) for number in expected)}"
        )


def parse_outline(
    text: str,
    volume_numbers: Sequence[int],
    thread_count: int | None,
) -> tuple[list[Section], list[Section]]:
    text = _strip_markdown_fence(text)
    thread_heading = _THREAD_HEADING.search(text)
    volume_text = text[: thread_heading.start()] if thread_heading else text
    volumes = []
    if volume_numbers:
        volumes = parse_sections(
            volume_text,
            level=2,
            minimum=len(volume_numbers),
            maximum=len(volume_numbers),
            label="分卷大纲",
        )
        validate_ordinals(volumes, volume_numbers, "卷")
    elif volume_text.strip():
        raise InvalidStage("本次无需补写分卷大纲")
    if thread_count == 0:
        if thread_heading:
            raise InvalidStage("本次无需补写剧情线程")
        return volumes, []
    if not thread_heading:
        raise InvalidStage("分卷规划缺少「# 剧情线程」小节")
    threads: list[Section] = []
    for line in text[thread_heading.end() :].splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"\s*[-*+]\s+([^|｜]+)[|｜](.+)", line)
        if not match or not 1 <= len(match[1].strip()) <= 255 or not match[2].strip():
            raise InvalidStage("剧情线程须逐行填写「- 线程名 | 简要描述」")
        threads.append(Section(match[1].strip(), match[2].strip()))
    if not threads or (thread_count is not None and len(threads) != thread_count):
        raise InvalidStage(
            f"剧情线程需要 {thread_count or '至少 1'} 项，模型返回了 {len(threads)} 项"
        )
    require_unique_titles(threads, "剧情线程")
    return volumes, threads


@dataclass(frozen=True)
class Recovery:
    """非空时补齐已有数据；固定编排还可同事务写入阶段检查点。"""

    previous: dict[str, int] = field(default_factory=dict)
    record: Callable[[dict[str, int]], None] | None = None


def commit(
    db: Session,
    recovery: Recovery | None,
    *,
    before_commit: Callable[[], None] | None = None,
    **counts: int,
) -> None:
    try:
        db.flush()
        if before_commit:
            before_commit()
        if recovery is not None and recovery.record is not None:
            recovery.record(counts)
        db.commit()
    except Exception:
        db.rollback()
        raise
