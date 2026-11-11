import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import auth
from .config import Settings, settings
from .database import init_db
from .limits import WindowBudget
from .memory import sync
from .runtime import single_process_resources, validate_runtime_settings
from .tasks import book_generation


def _validation_text(value: object) -> str:
    return str(value).encode("utf-8", errors="replace").decode("utf-8")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config: Settings = app.state.config
    with single_process_resources(config):
        init_db(database_lock_held=True)
        try:
            await sync.start_memory_worker()
            book_generation.recover_running_tasks()
            app.state.runtime_ready = True
            yield
        finally:
            app.state.runtime_ready = False
            try:
                await book_generation.stop_all_generation()
            finally:
                await sync.stop_memory_worker()


def create_app() -> FastAPI:
    config = settings
    validate_runtime_settings(config)
    # Transport debug/info logs include upstream request URLs and headers.
    # Task and SSE boundaries log safe provider status and context instead.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    app = FastAPI(title="AI 长篇小说生成平台", version="0.1.0", lifespan=lifespan)
    app.state.config = config
    app.state.runtime_ready = False
    app.state.login_budget = WindowBudget()
    app.add_middleware(
        auth.AccessControlMiddleware, config=config, budget=WindowBudget()
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "Retry-After"],
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Raw validation inputs may contain manuscript text, credentials or
        # values that cannot be serialized in a JSON response.
        details = [
            {
                "loc": [
                    part if isinstance(part, int) else _validation_text(part)
                    for part in error["loc"]
                ],
                "msg": _validation_text(error["msg"]),
                "type": _validation_text(error["type"]),
            }
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": details})

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    from .api import (
        books,
        chapters,
        characters,
        export,
        generate,
        memory,
        volumes,
        worldbuilding,
    )

    for name, router in (
        ("auth", auth.router),
        ("books", books.router),
        ("volumes", volumes.router),
        ("chapters", chapters.router),
        ("characters", characters.router),
        ("worldbuilding", worldbuilding.router),
        ("generate", generate.router),
        ("export", export.router),
        ("memory", memory.router),
    ):
        app.include_router(router, prefix="/api", tags=[name])

    return app


app = create_app()
