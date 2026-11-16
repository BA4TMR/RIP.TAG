"""mTLS-only production workstation page and API."""

from __future__ import annotations

import io
from typing import Annotated, Literal

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db_session
from app.errors import AppError, bad_request
from app.models import ProductionTask
from app.security import AuthContext, require_production
from app.services.npcs import list_npcs
from app.services.production import list_tasks, mark_stage, reset_task, status_summary
from app.services.setup import needs_setup
from app.web import error_page, render


class MarkBody(BaseModel):
    stage: Literal["written", "printed", "done"]
    # The service truncates to 500 characters after stripping control chars.
    # Do not reject a long printer/NFC error here, otherwise the failure would
    # not be recorded at all.
    error: str | None = Field(default=None)


def create_production_router(settings: Settings) -> APIRouter:
    router = APIRouter(tags=["production"])
    page_base = settings.production_base

    def _page_guard(request: Request) -> AuthContext | None:
        try:
            return require_production(request, settings)
        except AppError:
            return None

    @router.get(page_base)
    @router.get(f"{page_base}/")
    def production_page(
        request: Request,
        db: Annotated[Session, Depends(get_db_session)],
    ):
        if _page_guard(request) is None:
            return error_page(
                request,
                403,
                "需要制作工具证书",
                "制作工位要求有效的客户端证书。请使用已安装制作证书的设备，通过专属 HTTPS 地址访问。",
            )
        if needs_setup(db):
            return render(request, "production_setup.html", production_base=page_base)
        return render(request, "production.html", production_base=page_base)

    @router.get(f"{page_base}/api/tasks/{{task_id}}/qr.svg")
    def production_qr(
        task_id: int,
        auth: Annotated[AuthContext, Depends(require_production)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        task = db.get(ProductionTask, task_id)
        if task is None:
            raise bad_request("task_not_found", "制作任务不存在")
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(task.qr_code)
        qr.make(fit=True)
        image = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
        buffer = io.BytesIO()
        image.save(buffer)
        return Response(
            content=buffer.getvalue(),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/api/production/tasks")
    def production_tasks(
        auth: Annotated[AuthContext, Depends(require_production)],
        db: Annotated[Session, Depends(get_db_session)],
        status: str = Query(default="all"),
    ):
        if status not in {"all", "pending", "in_progress", "done"}:
            raise bad_request(
                "invalid_production_status",
                "status 必须是 all/pending/in_progress/done",
            )
        return list_tasks(db, status=status)

    @router.get("/api/production/status")
    def production_status(
        auth: Annotated[AuthContext, Depends(require_production)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        return status_summary(db)

    @router.get("/api/production/npcs")
    def production_npcs(
        auth: Annotated[AuthContext, Depends(require_production)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        return {"npcs": list_npcs(db, settings, include_inactive=False)}

    @router.post("/api/production/{task_id}/mark")
    def production_mark(
        task_id: int,
        body: MarkBody,
        auth: Annotated[AuthContext, Depends(require_production)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        task = mark_stage(
            db,
            task_id=task_id,
            stage=body.stage,
            error=body.error,
        )
        return {"ok": True, "task": task}

    @router.post("/api/production/{task_id}/reset")
    def production_reset(
        task_id: int,
        auth: Annotated[AuthContext, Depends(require_production)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        task = reset_task(db, task_id=task_id)
        return {"ok": True, "task": task}

    return router
