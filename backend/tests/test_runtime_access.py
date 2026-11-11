"""Exercise the real HTTP middleware, generation ownership and process locks offline."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app import database, main, models
from app.agents import orchestrator, pipeline, planner
from app.api import auth
from app.config import Settings
from app.db_base import Base
from app.llm.base import ToolChatResult
from app.memory import sync, vector_store
from app.runtime import single_process_resources
from app.tasks import admission, agent_generation, novel_generation
from app.tasks import book_generation as runtime

ORIGIN = "https://workbench.test"
TEST_PASSWORD = "pytest-only-password"
TEST_SECRET = "fixture-only-signing-value-for-offline-tests"


@pytest.fixture(autouse=True)
def isolated_settings_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def database_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime.db'}")
    event.listen(engine, "connect", database._enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(runtime, "SessionLocal", sessions)
    monkeypatch.setattr(main, "init_db", lambda **_: None)

    async def no_worker() -> None:
        return None

    monkeypatch.setattr(sync, "start_memory_worker", no_worker)
    monkeypatch.setattr(sync, "stop_memory_worker", no_worker)
    yield sessions
    engine.dispose()


def config(
    tmp_path: Path, *, production: bool = False, rate: int = 30, concurrent: int = 2
) -> Settings:
    return Settings(
        app_env="production" if production else "local",
        access_password=TEST_PASSWORD if production else "",
        session_secret=TEST_SECRET if production else "",
        cors_allowed_origins=ORIGIN,
        database_url=f"sqlite:///{tmp_path / 'runtime.db'}",
        chroma_dir=str(tmp_path / "vectors"),
        login_attempts_per_window=2,
        generation_requests_per_window=rate,
        generation_max_concurrent=concurrent,
    )


def app_for(conf: Settings, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(main, "settings", conf)
    monkeypatch.setattr(runtime, "settings", conf)
    return main.create_app()


@asynccontextmanager
async def client_for(
    app: FastAPI, *, raise_app_exceptions: bool = True
) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=app, raise_app_exceptions=raise_app_exceptions
            ),
            base_url=ORIGIN,
        ) as client,
    ):
        yield client


def seed(sessions: sessionmaker[Session], title: str = "离线书稿") -> tuple[str, str]:
    with sessions() as db:
        book = models.Book(title=title, brief="离线生成演练")
        chapter = models.Chapter(book=book, title="第一章", status="done")
        chapter.content = models.ChapterContent(body="只有登录后可以读取的正文")
        db.add(chapter)
        db.commit()
        return book.id, chapter.id


async def test_unsupported_agent_fallback_creates_only_the_successful_book(
    tmp_path: Path,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = app_for(config(tmp_path), monkeypatch)
    monkeypatch.setattr(admission, "supports_tool_calling", lambda: False)
    visible: list[tuple[str, str]] = []
    started = asyncio.Event()

    async def waiting_novel(db: Session, task: models.GenerationTask) -> None:
        book = db.get(models.Book, task.book_id)
        assert book is not None
        visible.append((task.id, book.brief))
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setitem(runtime._task_runners, "novel", waiting_novel)
    async with client_for(app) as client:
        rejected = await client.post("/api/generate/agent", json={"brief": "星海冒险"})
        assert (
            rejected.status_code == 400
            and "不支持自主决策" in rejected.json()["detail"]
        )
        assert (await client.get("/api/books")).json() == []

        fallback = await client.post(
            "/api/generate/novel", json={"brief": " 星海冒险 "}
        )
        assert fallback.status_code == 200
        payload = fallback.json()
        await asyncio.wait_for(started.wait(), 2)
        assert visible == [(payload["task"]["id"], "星海冒险")]
        books = (await client.get("/api/books")).json()
        assert [book["id"] for book in books] == [payload["book"]["id"]]

    with database_sessions() as db:
        assert len(db.scalars(select(models.GenerationTask)).all()) == 1


@pytest.mark.parametrize("existing", [False, True])
async def test_task_write_failure_rolls_back_new_book_or_existing_brief(
    tmp_path: Path,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    existing: bool,
) -> None:
    app = app_for(config(tmp_path), monkeypatch)
    book_id = ""
    if existing:
        with database_sessions() as db:
            book = models.Book(title="原有书稿", intro="已有简介", brief="")
            db.add(book)
            db.commit()
            book_id = book.id

    def fail_insert(_mapper: object, _connection: object, _task: object) -> None:
        raise SQLAlchemyError("injected task insert failure")

    event.listen(models.GenerationTask, "before_insert", fail_insert)
    try:
        async with client_for(app, raise_app_exceptions=False) as client:
            failed = (
                await client.post(
                    "/api/generate/novel/from-book", json={"book_id": book_id}
                )
                if existing
                else await client.post(
                    "/api/generate/novel", json={"brief": "不会留下空书"}
                )
            )
            assert failed.status_code == 500
            assert runtime._running == {} and runtime._active_books == {}
    finally:
        event.remove(models.GenerationTask, "before_insert", fail_insert)

    with database_sessions() as db:
        assert db.scalars(select(models.GenerationTask)).all() == []
        books = db.scalars(select(models.Book)).all()
        if existing:
            assert len(books) == 1 and books[0].id == book_id
            assert books[0].brief == "" and books[0].intro == "已有简介"
        else:
            assert books == []


async def test_competing_background_requests_create_one_task_for_the_book(
    tmp_path: Path,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = app_for(config(tmp_path), monkeypatch)
    book_id, _ = seed(database_sessions)

    async def hold(_db: Session, _task: models.GenerationTask) -> None:
        await asyncio.Event().wait()

    for task_type in ("book", "novel"):
        monkeypatch.setitem(runtime._task_runners, task_type, hold)
    async with client_for(app) as client:
        first, second = await asyncio.gather(
            client.post("/api/generate/book", json={"book_id": book_id}),
            client.post("/api/generate/novel/from-book", json={"book_id": book_id}),
        )
        assert sorted([first.status_code, second.status_code]) == [200, 409]
        assert len((await client.get(f"/api/books/{book_id}/tasks")).json()) == 1


async def test_signed_cookie_protects_manuscript_export_and_writes(
    tmp_path: Path,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = app_for(config(tmp_path, production=True), monkeypatch)
    book_id, _ = seed(database_sessions)
    async with client_for(app) as client:
        assert (await client.get("/api/health")).status_code == 200
        assert (await client.get("/api/auth/session")).json() == {
            "enabled": True,
            "authenticated": False,
        }
        for method, path, payload in (
            ("GET", f"/api/books/{book_id}", None),
            ("GET", f"/api/books/{book_id}/export?format=txt", None),
            ("DELETE", f"/api/books/{book_id}", None),
            ("POST", "/api/generate/book", {"book_id": book_id}),
            ("POST", f"/api/books/{book_id}/memory/repair", {}),
        ):
            response = await client.request(
                method, path, json=payload, headers={"Origin": ORIGIN}
            )
            assert response.status_code == 401
        assert (
            await client.post("/api/auth/login", json={"password": TEST_PASSWORD})
        ).status_code == 403

        response = await client.post(
            "/api/auth/login",
            json={"password": TEST_PASSWORD},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert all(flag in cookie for flag in ("httponly", "secure", "samesite=strict"))
        assert (await client.get("/api/auth/session")).json()["authenticated"] is True
        export = await client.get(f"/api/books/{book_id}/export?format=txt")
        assert export.status_code == 200
        assert "只有登录后可以读取的正文" in export.text
        assert export.headers["cache-control"] == "private, no-store"
        assert (
            await client.delete(
                f"/api/books/{book_id}", headers={"Origin": "https://foreign.test"}
            )
        ).status_code == 403
        assert (
            await client.patch(
                f"/api/books/{book_id}",
                headers={"Origin": ORIGIN},
                json={"title": "已修改"},
            )
        ).status_code == 200
        token = client.cookies[auth.COOKIE_NAME]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as attacker:
            response = await attacker.get(
                "/api/books", headers={"Cookie": f"{auth.COOKIE_NAME}={token}x"}
            )
            assert response.status_code == 401
        assert (
            await client.post("/api/auth/logout", headers={"Origin": ORIGIN})
        ).status_code == 200
        assert (await client.get("/api/books")).status_code == 401


async def test_login_attempt_limit_and_expired_session(
    tmp_path: Path,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = app_for(config(tmp_path, production=True), monkeypatch)
    seed(database_sessions)
    async with client_for(app) as client:
        for _ in range(2):
            response = await client.post(
                "/api/auth/login",
                headers={"Origin": ORIGIN},
                json={"password": "wrong"},
            )
            assert response.status_code == 401
        response = await client.post(
            "/api/auth/login",
            headers={"Origin": ORIGIN},
            json={"password": TEST_PASSWORD},
        )
        assert response.status_code == 429
        assert int(response.headers["retry-after"]) > 0

        # The signature is valid, but age is checked independently of the browser.
        now = auth.time.time()
        with monkeypatch.context() as clock:
            clock.setattr(auth.time, "time", lambda: now - 100_000)
            expired = auth.session_token(config(tmp_path, production=True))
        response = await client.get(
            "/api/books", headers={"Cookie": f"{auth.COOKIE_NAME}={expired}"}
        )
        assert response.status_code == 401


async def test_generation_request_limit_covers_sse_background_resume_and_memory(
    tmp_path: Path,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = app_for(config(tmp_path, production=True, rate=1), monkeypatch)
    book_id, _ = seed(database_sessions)
    calls: list[str] = []

    async def fake_generate(_db: Session, owner: str) -> AsyncIterator[tuple[str, str]]:
        calls.append(owner)
        yield ("done", "")

    monkeypatch.setattr(pipeline, "gen_worldbuilding", fake_generate)
    async with client_for(app) as client:
        assert (
            await client.post(
                "/api/generate/book",
                json={"book_id": book_id},
                headers={"Origin": ORIGIN},
            )
        ).status_code == 401
        client.headers["Origin"] = ORIGIN
        assert (
            await client.post("/api/auth/login", json={"password": TEST_PASSWORD})
        ).status_code == 200
        response = await client.post(
            "/api/generate/worldbuilding", json={"book_id": book_id}
        )
        assert response.status_code == 200 and "event: done" in response.text
        for path in (
            "/api/generate/book",
            "/api/tasks/test/retry",
            "/api/tasks/test/resume",
            f"/api/books/{book_id}/memory/repair",
        ):
            response = await client.post(path, json={"book_id": book_id})
            assert response.status_code == 429 and "retry-after" in response.headers
    assert calls == [book_id]


async def test_manual_sse_blocks_other_generators_and_drains_before_delete(
    tmp_path: Path,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = app_for(config(tmp_path), monkeypatch)
    book_id, chapter_id = seed(database_sessions)
    entered, stopped = asyncio.Event(), asyncio.Event()
    cleanup: list[str] = []

    async def waiting_model(
        db: Session, cid: str, _words: int, *, expected_version: str | None = None
    ) -> AsyncIterator[tuple[str, str]]:
        assert cid == chapter_id and expected_version is None
        try:
            entered.set()
            await asyncio.Event().wait()
            db.add(models.Worldbuilding(book_id=book_id, title="迟到写入"))
            db.commit()
            yield ("done", "")
        finally:
            stopped.set()

    async def suspend(owner: str) -> None:
        assert owner == book_id and stopped.is_set()
        cleanup.append("memory stopped")

    def delete_vectors(owner: str) -> None:
        assert owner == book_id and cleanup == ["memory stopped"]
        cleanup.append("vectors deleted")

    monkeypatch.setattr(pipeline, "gen_chapter_body", waiting_model)
    monkeypatch.setattr(sync, "suspend_book_memory", suspend)
    monkeypatch.setattr(vector_store, "delete_book", delete_vectors)
    async with client_for(app) as client:
        active = asyncio.create_task(
            client.post("/api/generate/chapter", json={"chapter_id": chapter_id})
        )
        await asyncio.wait_for(entered.wait(), 2)
        for path, payload in (
            ("/api/generate/chapter", {"chapter_id": chapter_id}),
            ("/api/generate/book", {"book_id": book_id}),
            ("/api/generate/novel/from-book", {"book_id": book_id}),
            ("/api/generate/agent/from-book", {"book_id": book_id}),
        ):
            rejected = await client.post(path, json=payload)
            assert rejected.status_code == 409
            assert rejected.headers["content-type"].startswith("application/json")

        response = await asyncio.wait_for(client.delete(f"/api/books/{book_id}"), 2)
        assert response.status_code == 200
        streamed = await asyncio.wait_for(active, 2)
        assert "event: error" in streamed.text
        assert cleanup == ["memory stopped", "vectors deleted"]
        assert (await client.get(f"/api/books/{book_id}")).status_code == 404
        assert book_id not in runtime._active_books
    with database_sessions() as db:
        assert db.scalars(select(models.Worldbuilding)).all() == []
        assert db.get(models.ChapterContent, chapter_id) is None


async def test_background_pause_disconnect_and_retry_release_same_book(
    tmp_path: Path,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conf = config(tmp_path, concurrent=1)
    app = app_for(conf, monkeypatch)
    book_id, chapter_id = seed(database_sessions)
    other, _ = seed(database_sessions, "另一本书")
    background_started, background_closed = asyncio.Event(), asyncio.Event()
    stream_started, stream_closed = asyncio.Event(), asyncio.Event()
    attempts = 0

    async def runner(db: Session, task: models.GenerationTask) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            background_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                background_closed.set()
        else:
            task.status = "done"
            task.progress = 100
            db.commit()

    async def waiting_model(
        _db: Session, _cid: str, _words: int, *, expected_version: str | None = None
    ) -> AsyncIterator[tuple[str, str]]:
        assert expected_version is None
        try:
            stream_started.set()
            await asyncio.Event().wait()
            yield ("done", "")
        finally:
            stream_closed.set()

    monkeypatch.setitem(runtime._task_runners, "book", runner)
    monkeypatch.setattr(pipeline, "gen_chapter_body", waiting_model)
    async with client_for(app) as client:
        result = await client.post("/api/generate/book", json={"book_id": book_id})
        assert result.status_code == 200
        task_id = result.json()["id"]
        await asyncio.wait_for(background_started.wait(), 2)
        assert (
            await client.post("/api/generate/chapter", json={"chapter_id": chapter_id})
        ).status_code == 409
        assert (
            await client.post("/api/generate/book", json={"book_id": other})
        ).status_code == 429
        assert (
            await client.post(
                "/api/generate/novel", json={"brief": "被限流，不能建空书"}
            )
        ).status_code == 429
        assert len((await client.get("/api/books")).json()) == 2
        assert (await client.post(f"/api/tasks/{task_id}/pause")).status_code == 200
        await asyncio.wait_for(background_closed.wait(), 2)

        active = asyncio.create_task(
            client.post("/api/generate/chapter", json={"chapter_id": chapter_id})
        )
        await asyncio.wait_for(stream_started.wait(), 2)
        assert (await client.post(f"/api/tasks/{task_id}/resume")).status_code == 409
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        assert stream_closed.is_set() and book_id not in runtime._active_books
        assert (await client.post(f"/api/tasks/{task_id}/retry")).status_code == 200
        handle = runtime._running.get(task_id)
        if handle:
            await asyncio.wait_for(handle, 2)
        assert (await client.get(f"/api/tasks/{task_id}")).json()["status"] == "done"
        assert attempts == 2


async def test_sse_error_logs_context_without_provider_exception_text(
    tmp_path: Path,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    book_id, chapter_id = seed(database_sessions)
    provider_error = "provider included a fragment of authorization material"

    async def broken_model(
        _db: Session, _cid: str, _words: int, *, expected_version: str | None = None
    ) -> AsyncIterator[tuple[str, str]]:
        assert expected_version is None
        yield ("token", "部分正文")
        raise RuntimeError(provider_error)

    monkeypatch.setattr(pipeline, "gen_chapter_body", broken_model)
    with caplog.at_level(logging.ERROR, logger="app.api.generate"):
        async with client_for(app_for(config(tmp_path), monkeypatch)) as client:
            response = await client.post(
                "/api/generate/chapter", json={"chapter_id": chapter_id}
            )
            assert response.status_code == 200
            assert "event: error" in response.text and "生成失败" in response.text
    assert provider_error not in response.text and provider_error not in caplog.text
    assert all(
        value in caplog.text
        for value in (book_id, chapter_id, "RuntimeError", "broken_model")
    )


@pytest.mark.parametrize("task_type", ["book", "novel", "agent"])
async def test_task_errors_keep_safe_status_and_trace_without_provider_details(
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    task_type: str,
) -> None:
    book_id, chapter_id = seed(database_sessions)
    fragment = "fixture-provider-credential-fragment"
    request = httpx.Request("POST", f"https://provider.test/model?api_key={fragment}")
    provider_error = httpx.HTTPStatusError(
        f"Provider response included {fragment}",
        request=request,
        response=httpx.Response(401, request=request),
    )

    async def broken_stream(
        _db: Session, _cid: str, _words: int
    ) -> AsyncIterator[tuple[str, str]]:
        yield ("token", "中断前的内容")
        raise provider_error

    async def broken_plan(*_args: object, **_kwargs: object) -> planner.NovelPlan:
        raise provider_error

    class BrokenProvider:
        async def chat_with_tools(
            self,
            _messages: list[dict[str, object]],
            _tools: list[dict[str, object]],
        ) -> ToolChatResult:
            raise provider_error

    monkeypatch.setattr(pipeline, "gen_chapter_body", broken_stream)
    monkeypatch.setattr(planner, "plan_novel", broken_plan)
    monkeypatch.setattr(orchestrator, "supports_tool_calling", lambda: True)
    monkeypatch.setattr(orchestrator, "get_llm", BrokenProvider)
    with database_sessions() as db:
        chapter = db.get(models.Chapter, chapter_id)
        assert chapter is not None
        chapter.status = "outline"
        task = models.GenerationTask(book_id=book_id, type=task_type, status="running")
        db.add(task)
        db.commit()
        task_id = task.id
        with caplog.at_level(logging.WARNING):
            if task_type == "book":
                await runtime.run_chapter_bodies(db, task)
            elif task_type == "novel":
                await novel_generation.run_novel_task(db, task)
            else:
                await agent_generation.run_agent_task(db, task)
        assert task.status == "error" and "HTTP 401" in task.error
        assert fragment not in task.error and fragment not in caplog.text
        assert book_id in caplog.text and task_id in caplog.text
        if task_type == "book":
            assert chapter_id in caplog.text
        if task_type == "agent":
            traces = db.scalars(
                select(models.AgentTraceEntry).where(
                    models.AgentTraceEntry.task_id == task_id
                )
            ).all()
            assert len(traces) == 1 and traces[0].kind == "error"
            assert fragment not in traces[0].payload and "HTTP 401" in traces[0].payload


async def test_shutdown_releases_recovered_tasks_and_deduplicates_legacy_rows(
    tmp_path: Path,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id, _ = seed(database_sessions)
    with database_sessions() as db:
        db.add_all(
            [
                models.GenerationTask(book_id=book_id, type="book", status="running"),
                models.GenerationTask(book_id=book_id, type="book", status="running"),
            ]
        )
        db.commit()
    started: list[str] = []
    stopped: list[str] = []
    ready = asyncio.Event()

    async def waiting_runner(_db: Session, task: models.GenerationTask) -> None:
        started.append(task.id)
        ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(task.id)

    monkeypatch.setitem(runtime._task_runners, "book", waiting_runner)
    conf = config(tmp_path)
    for count in (1, 2):
        ready.clear()
        async with client_for(app_for(conf, monkeypatch)):
            await asyncio.wait_for(ready.wait(), 2)
            assert len(started) == count and len(set(started)) == 1
        assert (
            len(stopped) == count
            and runtime._running == {}
            and runtime._active_books == {}
        )
    with database_sessions() as db:
        statuses = sorted(db.scalars(select(models.GenerationTask.status)))
        assert statuses == ["error", "running"]


def test_production_requires_secrets_and_concrete_https_origins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected = config(tmp_path, production=True)
    with pytest.raises(RuntimeError, match="ACCESS_PASSWORD"):
        app_for(protected.model_copy(update={"access_password": ""}), monkeypatch)
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        app_for(
            protected.model_copy(update={"session_secret": "too-short"}), monkeypatch
        )
    for origin in ("", "*", "http://production.test", "https://workbench.test/path"):
        with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
            app_for(
                protected.model_copy(update={"cors_allowed_origins": origin}),
                monkeypatch,
            )


async def test_http_refuses_to_serve_without_lifespan_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = app_for(config(tmp_path), monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        assert (await client.get("/api/health")).status_code == 503


async def test_route_prefix_cannot_bypass_access_control(
    tmp_path: Path,
    database_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = app_for(config(tmp_path, production=True), monkeypatch)
    seed(database_sessions)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, root_path="/novel"), base_url=ORIGIN
        ) as client,
    ):
        assert (await client.get("/novel/api/health")).status_code == 200
        assert (await client.get("/novel/api/books")).status_code == 401


def test_second_process_cannot_open_either_shared_store(tmp_path: Path) -> None:
    conf = config(tmp_path)
    code = (
        "import sys\n"
        "from app.config import Settings\n"
        "from app.runtime import single_process_resources\n"
        "cfg = Settings(_env_file=None, database_url=sys.argv[1], chroma_dir=sys.argv[2])\n"
        "with single_process_resources(cfg):\n"
        "    print('ready')\n"
    )

    def child(db_url: str, chroma: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", code, db_url, chroma],
            cwd=tmp_path,
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    with single_process_resources(conf):
        for db_url, chroma in (
            (conf.database_url, str(tmp_path / "other_vectors")),
            (f"sqlite:///{tmp_path / 'other.db'}", conf.chroma_dir),
        ):
            failed = child(db_url, chroma)
            assert failed.returncode != 0 and "--workers 1" in failed.stderr
    restarted = child(conf.database_url, conf.chroma_dir)
    assert restarted.returncode == 0 and restarted.stdout.strip() == "ready"
