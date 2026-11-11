"""Content versions shared by manual saves and generation."""

from hashlib import sha256


class ChapterContentConflict(ValueError):
    """The editor's content version no longer matches the stored body."""


def content_version(body: str) -> str:
    return sha256(body.encode("utf-8")).hexdigest()


def require_content_version(body: str, expected: str | None) -> None:
    if expected is not None and expected != content_version(body):
        raise ChapterContentConflict
