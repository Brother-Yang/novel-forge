"""Single-user sessions and process-local budgets for the local-store runtime."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import Settings
from ..limits import WindowBudget

router = APIRouter()
COOKIE_NAME = "novel_forge_session"
PUBLIC_PATHS = frozenset(
    ("/api/health", "/api/auth/session", "/api/auth/login", "/api/auth/logout")
)
SAFE_METHODS = frozenset(("GET", "HEAD", "OPTIONS"))


def _signature(config: Settings, payload: str) -> str:
    key = hmac.digest(
        config.session_secret.encode(), config.access_password.encode(), "sha256"
    )
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def session_token(config: Settings) -> str:
    payload = f"{int(time.time())}.{secrets.token_urlsafe(24)}"
    return f"{payload}.{_signature(config, payload)}"


def authenticated(request: Request, config: Settings) -> bool:
    if not config.access_password:
        return True
    token = request.cookies.get(COOKIE_NAME, "")
    try:
        issued, nonce, signature = token.split(".")
        age = time.time() - int(issued)
    except (ValueError, OverflowError):
        return False
    return (
        len(nonce) == 32
        and 0 <= age < config.session_ttl_seconds
        and hmac.compare_digest(
            signature.encode(), _signature(config, f"{issued}.{nonce}").encode()
        )
    )


def _origin_allowed(request: Request, config: Settings) -> bool:
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return False
    own_origin = f"{request.url.scheme}://{request.url.netloc}"
    # Only exact scheme/host/port origins are accepted (no paths or suffixes).
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if (
        parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or not parsed.hostname
    ):
        return False
    if config.app_env == "production" and parsed.scheme != "https":
        return False
    return origin == own_origin or origin in config.cors_allowed_origins_list


def _is_model_request(path: str, method: str) -> bool:
    return method == "POST" and (
        path.startswith("/api/generate/")
        or (path.startswith("/api/tasks/") and path.endswith(("/resume", "/retry")))
        or (path.startswith("/api/books/") and path.endswith("/memory/repair"))
    )


class AccessControlMiddleware:
    def __init__(self, app: ASGIApp, config: Settings, budget: WindowBudget) -> None:
        super().__init__()
        self.app, self.config, self.budget = app, config, budget

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        if not request.app.state.runtime_ready:
            await JSONResponse(
                {"detail": "后端尚未初始化，请启用 ASGI lifespan"}, status_code=503
            )(scope, receive, send)
            return
        path = scope["path"]
        root_path = scope.get("root_path", "")
        if root_path and (path == root_path or path.startswith(f"{root_path}/")):
            path = path[len(root_path) :] or "/"
        if request.method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        async def send_private(message: Message) -> None:
            if message["type"] == "http.response.start" and self.config.access_password:
                MutableHeaders(scope=message)["Cache-Control"] = "private, no-store"
            await send(message)

        if path not in PUBLIC_PATHS and not authenticated(request, self.config):
            await JSONResponse({"detail": "请先输入访问密码登录"}, status_code=401)(
                scope, receive, send_private
            )
            return
        if (
            self.config.access_password
            and request.method not in SAFE_METHODS
            and not _origin_allowed(request, self.config)
        ):
            await JSONResponse(
                {"detail": "请求来源不受信任，请从应用页面操作"}, status_code=403
            )(scope, receive, send_private)
            return
        if _is_model_request(path, request.method):
            wait = self.budget.consume(
                "generation",
                self.config.generation_requests_per_window,
                self.config.generation_window_seconds,
            )
            if wait is not None:
                await JSONResponse(
                    {"detail": "生成请求已达到本时段限额，请稍后重试"},
                    status_code=429,
                    headers={"Retry-After": str(wait)},
                )(scope, receive, send_private)
                return
        await self.app(scope, receive, send_private)


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


@router.get("/auth/session")
def get_session(request: Request) -> dict[str, bool]:
    config: Settings = request.app.state.config
    return {
        "enabled": bool(config.access_password),
        "authenticated": authenticated(request, config),
    }


@router.post("/auth/login")
def login(
    payload: LoginRequest, request: Request, response: Response
) -> dict[str, bool]:
    config: Settings = request.app.state.config
    if config.access_password:
        budget: WindowBudget = request.app.state.login_budget
        host = request.client.host if request.client else "unknown"
        wait = budget.consume(
            host, config.login_attempts_per_window, config.login_window_seconds
        )
        if wait is not None:
            raise HTTPException(
                429, "登录尝试过于频繁，请稍后重试", headers={"Retry-After": str(wait)}
            )
        if not hmac.compare_digest(
            payload.password.encode(), config.access_password.encode()
        ):
            raise HTTPException(401, "访问密码不正确")
        response.set_cookie(
            COOKIE_NAME,
            session_token(config),
            httponly=True,
            secure=config.app_env == "production",
            samesite="strict",
            max_age=config.session_ttl_seconds,
            path="/",
        )
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}
