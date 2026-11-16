"""Shared real-time dashboard API and SSE stream."""

from __future__ import annotations

import asyncio
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db_session
from app.errors import forbidden
from app.security import AuthContext, require_dashboard_access, resolve_auth
from app.services.stats import dashboard_payload
from app.utils import json_dumps

router = APIRouter()


@router.get("/api/dashboard")
def dashboard(
    auth: Annotated[AuthContext, Depends(require_dashboard_access)],
    db: Annotated[Session, Depends(get_db_session)],
):
    if auth.role != "npc":
        raise forbidden("npc_only", "此看板接口仅供 NPC 使用")
    return dashboard_payload(db)


def build_stream_response(request: Request) -> StreamingResponse:
    """Build an authenticated SSE response; callers perform auth first."""
    settings = request.app.state.settings
    bus = request.app.state.events
    factory = request.app.state.session_factory
    heartbeat = max(5, settings.sse_heartbeat_seconds)

    def _snapshot() -> str:
        with factory() as db:
            return json_dumps(dashboard_payload(db))

    async def event_generator():
        queue = await bus.subscribe()
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                snapshot = await anyio.to_thread.run_sync(_snapshot)
                yield f"event: dashboard\ndata: {snapshot}\n\n"
                try:
                    await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except TimeoutError:
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/stream")
async def stream(request: Request):
    settings = request.app.state.settings
    admin_ok = settings.allow_insecure_console or (
        (request.headers.get("x-client-verify") or "").strip().upper() == "SUCCESS"
    )
    if not admin_ok:
        factory = request.app.state.session_factory
        with factory() as db:
            auth = resolve_auth(request, db, settings)
        if auth is None or auth.role != "npc":
            raise forbidden("staff_only", "实时看板仅限技术台与 NPC 访问")
    return build_stream_response(request)
