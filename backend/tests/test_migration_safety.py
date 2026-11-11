import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app import database
from app.config import Settings
from app.runtime import single_process_resources

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def test_invalid_foreign_key_rolls_back_schema_and_revision(tmp_path: Path) -> None:
    path = tmp_path / "rollback.db"
    config = Config(str(ALEMBIC_INI))
    config.attributes["database_url"] = f"sqlite:///{path}"
    prior_revision = "20261111_trace_sequence"
    command.upgrade(config, prior_revision)

    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "INSERT INTO chapter_content (chapter_id, body) VALUES (?, ?)",
            ("missing-chapter", "不能随失败迁移消失"),
        )

    with pytest.raises(RuntimeError, match="Migration left invalid foreign keys"):
        command.upgrade(config, "head")

    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [
            (prior_revision,)
        ]
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name = 'chapter_facts'"
            ).fetchall()
            == []
        )
        assert db.execute("SELECT body FROM chapter_content").fetchall() == [
            ("不能随失败迁移消失",)
        ]
        db.execute("DELETE FROM chapter_content WHERE chapter_id = 'missing-chapter'")

    command.upgrade(config, "head")
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name = 'chapter_facts'"
        ).fetchone() == ("chapter_facts",)


def test_cli_upgrade_respects_service_database_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "service.db"
    settings = Settings(
        database_url=f"sqlite:///{path}",
        chroma_dir=str(tmp_path / "chroma"),
    )
    monkeypatch.setattr(database, "settings", settings)
    cli = [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"]
    env = {**os.environ, "DATABASE_URL": settings.database_url}

    with single_process_resources(settings):
        locked = subprocess.run(
            cli,
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert locked.returncode != 0
        assert "请使用 uvicorn --workers 1，并在执行迁移前关闭后端" in locked.stderr
        assert not path.exists()
        # Startup already owns the database lock and can upgrade under it.
        database.init_db(database_lock_held=True)
        assert path.exists()

    unlocked = subprocess.run(
        cli,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert unlocked.returncode == 0, unlocked.stderr
