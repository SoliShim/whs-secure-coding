"""바로마켓 FastAPI 애플리케이션 팩토리."""

from __future__ import annotations

import logging
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from anyio import CapacityLimiter
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.chat import ChatManager
from app.config import Settings
from app.db import connect, init_database
from app.rate_limit import RateLimiter
from app.request_limit import MAX_HTTP_BODY_BYTES, RequestBodyLimitMiddleware
from app.routers import admin, auth, chat, moderation, products, wallet
from app.security import hash_password, security_headers
from app.web import apply_session_cookie, attach_session, render

LOGGER = logging.getLogger("baromarket")
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_NAME = re.compile(r"^[0-9a-f]{40}\.jpg$")


def _format_money(value: object) -> str:
    try:
        return f"{int(value):,}원"
    except (TypeError, ValueError):
        return "0원"


def _format_time(value: object) -> str:
    text = str(value or "")
    return text.replace("T", " ")[:16]


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        init_database(app_settings)
        app_settings.upload_dir.mkdir(parents=True, exist_ok=True)
        application.state.image_processing_limiter = CapacityLimiter(3)
        yield

    application = FastAPI(
        title="바로마켓",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.rate_limiter = RateLimiter()
    application.state.chat_manager = ChatManager()
    application.state.dummy_password_hash = hash_password(
        "this-is-a-dummy-password-used-only-to-balance-login-timing-2026"
    )
    # Jinja2Templates enables HTML/XML auto-escaping through its selected-file policy.
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    templates.env.filters["money"] = _format_money
    templates.env.filters["datetime"] = _format_time
    application.state.templates = templates

    application.add_middleware(
        TrustedHostMiddleware, allowed_hosts=list(app_settings.allowed_hosts)
    )

    @application.middleware("http")
    async def security_and_session(request: Request, call_next):
        request_id = secrets.token_hex(12)
        skip_session = request.url.path.startswith("/static/") or request.url.path == "/healthz"
        if not skip_session:
            issue_anonymous = request.method == "GET" and request.url.path in {
                "/login",
                "/register",
            }
            await run_in_threadpool(
                attach_session,
                request,
                app_settings,
                issue_anonymous=issue_anonymous,
            )
        try:
            response = await call_next(request)
        except Exception:
            if getattr(request.state, "request_body_limit_exceeded", False):
                raise
            LOGGER.exception(
                "unhandled request error request_id=%s method=%s path=%s",
                request_id,
                request.method,
                request.url.path,
            )
            if request.url.path.startswith("/api/"):
                response = JSONResponse(
                    {"error": "internal_server_error", "request_id": request_id}, status_code=500
                )
            elif skip_session:
                response = JSONResponse({"error": "internal_server_error"}, status_code=500)
            else:
                response = render(
                    request,
                    "error.html",
                    status_code=500,
                    title="서버 오류",
                    error=f"요청을 처리하지 못했습니다. 오류 번호: {request_id}",
                )
        if not skip_session:
            apply_session_cookie(request, response, app_settings)
        for name, value in security_headers(app_settings).items():
            response.headers[name] = value
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=3600"
        else:
            response.headers["Cache-Control"] = "no-store"
        return response

    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=MAX_HTTP_BODY_BYTES,
        response_headers=security_headers(app_settings),
    )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        titles = {400: "잘못된 요청", 401: "로그인 필요", 403: "접근 거부", 404: "찾을 수 없음"}
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            title=titles.get(exc.status_code, "요청 오류"),
            error=str(exc.detail),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        return render(
            request,
            "error.html",
            status_code=400,
            title="입력 오류",
            error="요청 형식이 올바르지 않습니다. 입력값을 확인해 주세요.",
        )

    @application.get("/healthz", include_in_schema=False)
    def healthz():
        with connect(app_settings) as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok"}

    @application.get("/uploads/{filename}", include_in_schema=False)
    def uploaded_image(request: Request, filename: str):
        if not UPLOAD_NAME.fullmatch(filename):
            raise HTTPException(status_code=404, detail="이미지를 찾을 수 없습니다.")
        with connect(app_settings) as conn:
            product = conn.execute(
                """
                SELECT id, seller_id, status, is_hidden FROM products
                WHERE image_filename = ? ORDER BY id DESC LIMIT 1
                """,
                (filename,),
            ).fetchone()
        if product is None or product["status"] == "deleted":
            raise HTTPException(status_code=404, detail="이미지를 찾을 수 없습니다.")
        user = getattr(request.state, "user", None)
        can_manage = bool(
            user and (int(product["seller_id"]) == int(user["id"]) or user["role"] == "admin")
        )
        if product["is_hidden"] and not can_manage:
            raise HTTPException(status_code=404, detail="이미지를 찾을 수 없습니다.")
        path = app_settings.upload_dir / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="이미지를 찾을 수 없습니다.")
        return FileResponse(path, media_type="image/jpeg", filename=None)

    application.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    application.include_router(products.router)
    application.include_router(auth.router)
    application.include_router(moderation.router)
    application.include_router(wallet.router)
    application.include_router(chat.router)
    application.include_router(admin.router)
    return application


app = create_app()
