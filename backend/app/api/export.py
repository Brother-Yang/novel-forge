"""从同一书稿快照逐章写出 TXT 或 EPUB，再发送临时文件。"""

import html
import re
import tempfile
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.types import Receive, Scope, Send

from .. import models
from ..database import get_db

router = APIRouter()
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


def _ordered_chapters(
    db: Session, book_id: str
) -> Iterator[tuple[str | None, models.Chapter]]:
    """按卷顺序、卷内章顺序产出 (卷标题或None, 章);未归卷章节置于最后。"""
    volumes = db.scalars(
        select(models.Volume)
        .where(models.Volume.book_id == book_id)
        .order_by(models.Volume.order_index)
    ).all()
    for vol in volumes:
        chapters = db.scalars(
            select(models.Chapter)
            .where(
                models.Chapter.book_id == book_id,
                models.Chapter.volume_id == vol.id,
            )
            .order_by(models.Chapter.order_index)
        ).all()
        for ch in chapters:
            yield vol.title, ch
    loose = db.scalars(
        select(models.Chapter)
        .where(
            models.Chapter.book_id == book_id,
            models.Chapter.volume_id.is_(None),
        )
        .order_by(models.Chapter.order_index)
    ).all()
    for ch in loose:
        yield None, ch


def _body(db: Session, chapter_id: str) -> str:
    content = db.get(models.ChapterContent, chapter_id)
    return content.body if content else ""


def _export_txt(db: Session, book: models.Book) -> Iterator[str]:
    yield f"《{book.title}》\n"
    if book.intro:
        yield f"\n{book.intro}\n"
    last_volume: str | None = "\0"
    for volume_title, ch in _ordered_chapters(db, book.id):
        if volume_title != last_volume:
            last_volume = volume_title
            if volume_title:
                yield f"\n\n{'=' * 20}\n{volume_title}\n{'=' * 20}\n"
        body = _body(db, ch.id)
        yield f"\n\n{ch.title}\n\n{body}\n"


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

        for i, (_vol, ch) in enumerate(_ordered_chapters(db, book.id), start=1):
            item_id = f"chap{i}"
            href = f"{item_id}.xhtml"
            zf.writestr(f"OEBPS/{href}", _xhtml(ch.title, _body(db, ch.id)))
            manifest_items.append(
                f'<item id="{item_id}" href="{href}" '
                'media-type="application/xhtml+xml"/>'
            )
            spine_items.append(f'<itemref idref="{item_id}"/>')
            nav_points.append(
                f'<navPoint id="nav{i}" playOrder="{i}">'
                f"<navLabel><text>{html.escape(_xml_text(ch.title))}</text></navLabel>"
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
