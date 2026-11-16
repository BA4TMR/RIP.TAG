"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import BASE_DIR, Settings
from app.db import Base, build_engine, build_session_factory, ensure_schema
from app.errors import AppError
from app.events import EventBus
from app.ratelimit import SlidingWindowLimiter
from app.routes import console, member, production, realtime, staff
from app.seed import seed_if_empty
from app.services.setup import needs_setup
from app.services.stats import ensure_active_round
from app.web import error_page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("activity")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Base.metadata.create_all(engine)
        ensure_schema(engine)
        with session_factory() as db:
            round_ = ensure_active_round(db)
            seeded = seed_if_empty(db, settings)
            if seeded:
                logger.info(
                    "已从种子文件导入 %s 条制作任务、%s 个组",
                    seeded["tasks"],
                    seeded["groups"],
                )
            setup_required = needs_setup(db)
            logger.info(
                "活动系统启动 env=%s round=%s(%s); console + production workstation enabled; setup_required=%s",
                settings.app_env,
                round_.name,
                round_.id,
                setup_required,
            )
            if setup_required:
                logger.info("首次启动：请打开主控台，按向导设置组数和每组人数")
            if settings.allow_insecure_console:
                logger.warning("ALLOW_INSECURE_CONSOLE=true：主控台 mTLS 校验已绕过，仅限本地开发")
            if settings.allow_insecure_production:
                logger.warning(
                    "ALLOW_INSECURE_PRODUCTION=true：制作 API mTLS 校验已绕过，仅限本地开发"
                )
            if not settings.cookie_secure:
                logger.warning("COOKIE_SECURE=false：Cookie 未启用 Secure，仅限本地开发")
        yield
        engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.events = EventBus()
    app.state.limiter = SlidingWindowLimiter()
    app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
    app.state.templates.env.globals["app_name"] = settings.app_name
    app.state.templates.env.globals["current_year"] = datetime.now().year

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=()")
        if request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "public, max-age=3600")
        else:
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        payload = {
            "ok": False,
            "error": {"code": exc.code, "message": exc.message, **exc.extra},
        }
        is_api = "/api/" in request.url.path or request.url.path.endswith("/api")
        if is_api or "application/json" in request.headers.get("accept", ""):
            return JSONResponse(payload, status_code=exc.status_code, headers=exc.headers)
        return error_page(request, exc.status_code, "操作未完成", exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            {
                "ok": False,
                "error": {
                    "code": "invalid_request",
                    "message": "请求参数不完整或格式不正确",
                    "fields": exc.errors(),
                },
            },
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404 and "text/html" in request.headers.get("accept", ""):
            return error_page(request, 404, "页面不存在", "该链接无效或已过期。")
        return JSONResponse(
            {"ok": False, "error": {"code": "http_error", "message": str(exc.detail)}},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")

    app.include_router(member.router)
    app.include_router(staff.router)
    app.include_router(realtime.router)
    app.include_router(console.create_console_router(settings))
    app.include_router(production.create_production_router(settings))

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        with session_factory() as db:
            db.execute(text("SELECT 1"))
        return {"ok": True, "service": "activity", "env": settings.app_env}

    return app


app = create_app()
