from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from .tasks.book_generation import recover_running_tasks

    recover_running_tasks()
    yield


app = FastAPI(title="AI 长篇小说生成平台", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


def _register_routers() -> None:
    from .api import (
        books,
        chapters,
        characters,
        export,
        generate,
        volumes,
        worldbuilding,
    )

    app.include_router(books.router, prefix="/api", tags=["books"])
    app.include_router(volumes.router, prefix="/api", tags=["volumes"])
    app.include_router(chapters.router, prefix="/api", tags=["chapters"])
    app.include_router(characters.router, prefix="/api", tags=["characters"])
    app.include_router(worldbuilding.router, prefix="/api", tags=["worldbuilding"])
    app.include_router(generate.router, prefix="/api", tags=["generate"])
    app.include_router(export.router, prefix="/api", tags=["export"])


_register_routers()
