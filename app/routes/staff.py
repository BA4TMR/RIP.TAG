"""NPC staff routes: tap-in, catch-up registration, group scoring."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db_session
from app.errors import conflict, not_found
from app.models import Group, Member, ProductionTask, StaffToken
from app.security import (
    AuthContext,
    clear_session_cookie,
    create_session,
    require_npc,
    resolve_auth,
    set_session_cookie,
)
from app.services.kills import member_public
from app.services.members import register_from_task
from app.services.scores import add_score, adjustment_to_dict, list_adjustments
from app.services.stats import require_active_round
from app.utils import iso_utc
from app.web import error_page, render

router = APIRouter()


class NpcRegisterBody(BaseModel):
    task_id: int
    name: str = Field(min_length=1, max_length=60)


class NpcScoreBody(BaseModel):
    group_id: int
    delta: int = Field(ge=1, le=1000)
    reason: str | None = Field(default=None, max_length=200)


def _npc_context(auth: AuthContext, db: Session) -> dict:
    round_ = require_active_round(db)
    groups = list(db.scalars(select(Group).order_by(Group.id)))
    unregistered = db.scalar(
        select(func.count(ProductionTask.id))
        .outerjoin(Member, Member.production_task_id == ProductionTask.id)
        .where(Member.id.is_(None))
    )
    return {
        "staff": auth.staff,
        "round": round_,
        "groups": groups,
        "has_unregistered": bool(unregistered),
    }


@router.get("/staff/{npc_token}")
def npc_entry(
    npc_token: str,
    request: Request,
    db: Annotated[Session, Depends(get_db_session)],
):
    settings: Settings = request.app.state.settings
    staff = db.scalar(
        select(StaffToken).where(
            StaffToken.token == npc_token,
            StaffToken.active.is_(True),
            StaffToken.role == "npc",
        )
    )
    if staff is None:
        return error_page(
            request,
            404,
            "场务标签无效",
            "这个 NPC 标签未启用或已停用，请联系技术台。",
        )
    token, _session = create_session(
        db,
        settings,
        role="npc",
        staff_token_id=staff.id,
    )
    db.commit()
    response = RedirectResponse("/staff", status_code=303)
    set_session_cookie(response, token, settings)
    return response


@router.get("/staff")
def staff_home(request: Request, db: Annotated[Session, Depends(get_db_session)]):
    auth = resolve_auth(request, db, request.app.state.settings)
    if auth is None or auth.role != "npc" or auth.staff is None:
        return error_page(
            request,
            401,
            "需要 NPC 身份",
            "请碰触场务专用的 NTAG 标签进入 NPC 场务台。",
        )
    return render(request, "staff.html", **_npc_context(auth, db))


@router.get("/api/npc/members")
def npc_members(
    request: Request,
    auth: Annotated[AuthContext, Depends(require_npc)],
    db: Annotated[Session, Depends(get_db_session)],
):
    """Return the full roster: pending production tasks + registered members."""
    groups = {
        group.name: group for group in db.scalars(select(Group).order_by(Group.id))
    }
    rows = db.execute(
        select(ProductionTask, Member)
        .outerjoin(Member, Member.production_task_id == ProductionTask.id)
        .order_by(ProductionTask.group_no, ProductionTask.id)
    ).all()
    roster: list[dict] = []
    for task, member in rows:
        group = groups.get(task.group_no)
        if member is not None:
            roster.append(
                {
                    **member_public(member),
                    "task_id": task.id,
                    "group_id": member.group_id,
                    "registered_at": iso_utc(member.registered_at),
                }
            )
        else:
            roster.append(
                {
                    "id": None,
                    "task_id": task.id,
                    "name": None,
                    "status": "unregistered",
                    "group": {
                        "id": group.id if group else None,
                        "name": task.group_no,
                        "color": task.group_color,
                    },
                    "group_id": group.id if group else None,
                    "registered_at": None,
                }
            )
    return {"members": roster}


@router.get("/api/npc/scores")
def npc_scores(
    request: Request,
    auth: Annotated[AuthContext, Depends(require_npc)],
    db: Annotated[Session, Depends(get_db_session)],
):
    round_ = require_active_round(db)
    return {"adjustments": list_adjustments(db, round_id=round_.id, limit=50)}


@router.post("/api/npc/register")
def npc_register(
    body: NpcRegisterBody,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_npc)],
    db: Annotated[Session, Depends(get_db_session)],
):
    task = db.get(ProductionTask, body.task_id)
    if task is None:
        raise not_found("task_not_found", "未找到该制作任务")
    try:
        member, _created = register_from_task(
            db,
            task=task,
            name=body.name,
            actor_role="npc",
            actor_id=auth.actor_id,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise conflict("already_registered", "该队员已被注册，请刷新后查看") from None
    except Exception:
        db.rollback()
        raise
    request.app.state.events.publish()
    db.refresh(member)
    return {"ok": True, "member": member_public(member)}


@router.post("/api/npc/score")
def npc_score(
    body: NpcScoreBody,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_npc)],
    db: Annotated[Session, Depends(get_db_session)],
):
    adjustment = add_score(
        db,
        group_id=body.group_id,
        delta=body.delta,
        reason=body.reason,
        actor_role="npc",
        actor_id=auth.actor_id,
        allow_negative=False,
    )
    request.app.state.events.publish()
    group = db.get(Group, adjustment.group_id)
    return {"ok": True, "adjustment": adjustment_to_dict(adjustment, group)}


@router.post("/api/npc/logout")
def npc_logout(
    request: Request,
    auth: Annotated[AuthContext, Depends(require_npc)],
    db: Annotated[Session, Depends(get_db_session)],
):
    if auth.session:
        auth.session.revoked = True
        db.commit()
    response = JSONResponse({"ok": True})
    clear_session_cookie(response, request.app.state.settings)
    return response
