"""Exclusive ownership of local SQLite/Chroma resources for this process.

The generation registry and limiter are deliberately process-local. Hold these
OS locks for the complete lifespan, including recovery and worker shutdown.
Do not unlink lock files: unlinking a held file allows another inode to bypass it.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

from .config import Settings


def validate_runtime_settings(config: Settings) -> None:
    origins = config.cors_allowed_origins_list
    for origin in origins:
        try:
            url = urlsplit(origin)
            _ = url.port
        except ValueError:
            raise RuntimeError("CORS_ALLOWED_ORIGINS 包含无效的来源") from None
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.path
            or url.query
            or url.fragment
            or url.username is not None
            or "*" in origin
        ):
            raise RuntimeError(
                "CORS_ALLOWED_ORIGINS 必须是明确的 http(s) 来源，不能使用 *"
            )
    if config.app_env == "production" and not config.access_password:
        raise RuntimeError("生产模式必须配置 ACCESS_PASSWORD 和 SESSION_SECRET")
    if config.access_password and len(config.session_secret.encode()) < 32:
        raise RuntimeError("启用访问密码时 SESSION_SECRET 必须至少包含 32 字节")
    if len(config.access_password) > 1024:
        raise RuntimeError("ACCESS_PASSWORD 不能超过 1024 个字符")
    if config.app_env == "production" and len(config.access_password) < 12:
        raise RuntimeError("生产模式 ACCESS_PASSWORD 必须至少包含 12 个字符")
    if config.app_env == "production" and (
        not origins or any(not origin.startswith("https://") for origin in origins)
    ):
        raise RuntimeError("生产模式 CORS_ALLOWED_ORIGINS 必须使用具体 HTTPS 来源")


def _sqlite_lock_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
        return None
    if url.database.startswith("file:"):
        raise RuntimeError(
            "DATABASE_URL 请使用普通 SQLite 文件路径，以便锁定真实数据目录"
        )
    database_path = Path(url.database).resolve()
    return database_path.with_name(database_path.name + ".lock")


@contextmanager
def _hold_locks(locks: set[Path]) -> Iterator[None]:
    with ExitStack() as stack:
        for path in sorted(locks):
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = stack.enter_context(path.open("a+", encoding="utf-8"))
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "数据目录已被其它后端或迁移进程占用；SQLite/本地 Chroma 仅支持单进程，"
                    "请使用 uvicorn --workers 1，并在执行迁移前关闭后端"
                ) from exc
            stack.callback(fcntl.flock, handle.fileno(), fcntl.LOCK_UN)
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()}\n")
            handle.flush()
        yield


@contextmanager
def sqlite_migration_lock(database_url: str) -> Iterator[None]:
    path = _sqlite_lock_path(database_url)
    with _hold_locks({path} if path is not None else set()):
        yield


@contextmanager
def single_process_resources(config: Settings) -> Iterator[None]:
    """Fail startup before migrations/recovery if either store is already owned."""
    chroma = Path(config.chroma_dir).resolve()
    locks = {chroma / ".novel-forge.lock"}
    database = _sqlite_lock_path(config.database_url)
    if database is not None:
        locks.add(database)
    with _hold_locks(locks):
        yield
