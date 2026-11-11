"""从同一书稿快照逐章写出 TXT 或 EPUB，再发送临时文件。"""

import html
import re
import tempfile
import uuid
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from starlette.types import Receive, Scope, Send

from .. import models
from ..database import get_db

router = APIRouter()
_CHAPTER_BATCH_SIZE = 32
_INVALID_XML_TEXT = re.compile(
    r"[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]"
)


class _ExportFileResponse(FileResponse):
    def __init__(
        self,
        directory: tempfile.TemporaryDirectory[str],
        path: Path,
        *,
        media_type: str,
        filename: str,
    ) -> None:
        self.directory = directory
        super().__init__(path, media_type=media_type, filename=filename)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.directory.cleanup()


@dataclass(frozen=True)
class _ExportChapter:
    volume_id: str | None
    volume_title: str | None
    title: str
    body: str


def _chapter_bodies(db: Session, chapter_ids: Sequence[str]) -> dict[str, str]:
    statement = select(
        models.ChapterContent.chapter_id, models.ChapterContent.body
    ).where(models.ChapterContent.chapter_id.in_(chapter_ids))
    with db.execute(statement) as result:
        return {chapter_id: body for chapter_id, body in result.tuples()}


def _ordered_chapters(db: Session, book_id: str) -> Iterator[_ExportChapter]:
    """Read the ordered catalog and load at most one batch of bodies at a time."""
    chapter, volume = models.Chapter, models.Volume
    statement = (
        select(chapter.id, chapter.title, volume.id, volume.title)
        .select_from(chapter)
        .outerjoin(
            volume, and_(chapter.volume_id == volume.id, volume.book_id == book_id)
        )
        .where(
            chapter.book_id == book_id,
            or_(chapter.volume_id.is_(None), volume.id.is_not(None)),
        )
        .order_by(
            chapter.volume_id.is_(None),
            volume.order_index,
            volume.id,
            chapter.order_index,
            chapter.id,
        )
        .execution_options(yield_per=_CHAPTER_BATCH_SIZE)
    )
    # Keep the sort input small. Joining prose into this query would put it in
    # SQLite's sort buffer even if the application fetched rows in batches.
    with db.execute(statement) as result:
        for rows in result.tuples().partitions(_CHAPTER_BATCH_SIZE):
            bodies = _chapter_bodies(db, [chapter_id for chapter_id, _, _, _ in rows])
            for chapter_id, title, volume_id, volume_title in rows:
                yield _ExportChapter(
                    volume_id, volume_title, title, bodies.get(chapter_id, "")
                )
            del bodies  # Drop this batch before loading the next one.


def _export_txt(db: Session, book: models.Book) -> Iterator[str]:
    yield f"《{book.title}》\n"
    if book.intro:
        yield f"\n{book.intro}\n"
    last_volume_id: str | None = None
    for chapter in _ordered_chapters(db, book.id):
        if chapter.volume_id != last_volume_id:
            last_volume_id = chapter.volume_id
            if chapter.volume_title:
                yield f"\n\n{'=' * 20}\n{chapter.volume_title}\n{'=' * 20}\n"
        yield f"\n\n{chapter.title}\n\n{chapter.body}\n"


def _xml_text(value: str) -> str:
    return _INVALID_XML_TEXT.sub("\ufffd", value)


def _xhtml(title: str, body: str) -> str:
    safe_title = html.escape(_xml_text(title))
    paragraphs = "\n".join(
        f"<p>{html.escape(line.strip())}</p>"
        for line in _xml_text(body).splitlines()
        if line.strip()
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh">\n'
        f"<head><title>{safe_title}</title></head>\n"
        f"<body><h2>{safe_title}</h2>\n{paragraphs}\n</body></html>"
    )


def _build_epub(db: Session, book: models.Book, path: Path) -> None:
    book_uid = f"urn:uuid:{uuid.uuid4()}"
    safe_title = html.escape(_xml_text(book.title))
    manifest_items = []
    spine_items = []
    nav_points = []

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype 必须是第一个且不压缩
        zf.writestr(
            zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED
        )
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>\n'
            '<container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            "  <rootfiles>\n"
            '    <rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/>\n'
            "  </rootfiles>\n</container>",
        )

        for i, chapter in enumerate(_ordered_chapters(db, book.id), start=1):
            item_id = f"chap{i}"
            href = f"{item_id}.xhtml"
            zf.writestr(f"OEBPS/{href}", _xhtml(chapter.title, chapter.body))
            manifest_items.append(
                f'<item id="{item_id}" href="{href}" '
                'media-type="application/xhtml+xml"/>'
            )
            spine_items.append(f'<itemref idref="{item_id}"/>')
            nav_points.append(
                f'<navPoint id="nav{i}" playOrder="{i}">'
                f"<navLabel><text>{html.escape(_xml_text(chapter.title))}</text></navLabel>"
                f'<content src="{href}"/></navPoint>'
            )

        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
            'unique-identifier="bookid">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f"    <dc:title>{safe_title}</dc:title>\n"
            "    <dc:language>zh</dc:language>\n"
            f'    <dc:identifier id="bookid">{book_uid}</dc:identifier>\n'
            "  </metadata>\n"
            "  <manifest>\n"
            '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>\n'
            "    " + "\n    ".join(manifest_items) + "\n"
            "  </manifest>\n"
            '  <spine toc="ncx">\n'
            "    " + "\n    ".join(spine_items) + "\n"
            "  </spine>\n</package>",
        )

        zf.writestr(
            "OEBPS/toc.ncx",
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
            f'  <head><meta name="dtb:uid" content="{book_uid}"/></head>\n'
            f"  <docTitle><text>{safe_title}</text></docTitle>\n"
            "  <navMap>\n    " + "\n    ".join(nav_points) + "\n  </navMap>\n</ncx>",
        )


@router.get("/books/{book_id}/export")
def export_book(
    book_id: str,
    format: str = Query("txt", pattern="^(txt|epub)$"),
    db: Session = Depends(get_db),
) -> FileResponse:
    directory = tempfile.TemporaryDirectory(prefix="novel-forge-export-")
    path = Path(directory.name) / f"book.{format}"
    try:
        with db.begin():
            if db.get_bind().dialect.name == "sqlite":
                # The default SQLite driver mode does not begin for SELECT.
                db.connection().exec_driver_sql("BEGIN")
            book = db.get(models.Book, book_id)
            if book is None:
                raise HTTPException(404, "书籍不存在")
            filename = f"{book.title}.{format}"
            if format == "txt":
                media_type = "text/plain; charset=utf-8"
                with path.open("w", encoding="utf-8", newline="\n") as output:
                    output.writelines(_export_txt(db, book))
            else:
                media_type = "application/epub+zip"
                _build_epub(db, book, path)

        # Finish reading before a slow client can hold the database open.
        return _ExportFileResponse(
            directory, path, media_type=media_type, filename=filename
        )
    except BaseException:
        directory.cleanup()
        raise
