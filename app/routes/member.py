"""Player-facing routes and APIs."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db_session
from app.errors import AppError, conflict, not_found, too_many
from app.models import Member
from app.qrcode_utils import qr_png_data_uri
from app.security import (
    AuthContext,
    clear_session_cookie,
    client_ip,
    create_member_session,
    require_member,
    resolve_auth,
    set_session_cookie,
)
from app.services.kills import confirm_kill, lookup_victim, member_public
from app.services.members import (
    find_member_by_token,
    find_task_by_token,
    register_from_task,
)
from app.services.stats import personal_stats
from app.utils import iso_utc, normalize_code
from app.web import error_page, member_context, render

router = APIRouter()


class RegisterBody(BaseModel):
    ntag_token: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=60)


class CodeBody(BaseModel):
    code: str = Field(min_length=1, max_length=20)


def _member_from_request(
    request: Request, db: Session, settings: Settings
) -> AuthContext | None:
    auth = resolve_auth(request, db, settings)
    if auth is None or auth.role != "member" or auth.member is None:
        return None
    return auth


def _member_payload(db: Session, member: Member) -> dict:
    stats = personal_stats(db, member)
    return {
        "member": {
            "id": member.id,
            "name": member.name,
            "status": member.status,
            "status_label": {
                "active": "存活中",
                "eliminated": "已淘汰",
                "unregistered": "未注册",
            }.get(member.status, member.status),
            "production_task_id": member.production_task_id,
            "group": {
                "id": member.group_id,
                "name": member.group_no,
                "color": member.group_color,
            },
            "registered_at": iso_utc(member.registered_at),
        },
        "stats": stats,
        "can_kill": member.status == "active",
        "round": {"id": stats["round_id"], "name": stats["round_name"]},
    }


@router.get("/")
def landing(request: Request, db: Annotated[Session, Depends(get_db_session)]):
    auth = resolve_auth(request, db, request.app.state.settings)
    if auth and auth.role == "member":
        return RedirectResponse("/home", status_code=303)
    return render(request, "landing.html")


@router.get("/r/{ntag_token}")
def ntag_entry(ntag_token: str, request: Request, db: Annotated[Session, Depends(get_db_session)]):
    settings: Settings = request.app.state.settings
    member = find_member_by_token(db, ntag_token)
    if member is not None and member.name and member.status != "unregistered":
        token, _session = create_member_session(db, member, settings)
        db.commit()
        response = RedirectResponse("/home", status_code=303)
        set_session_cookie(response, token, settings)
        return response

    task = find_task_by_token(db, ntag_token)
    if task is None:
        return error_page(
            request,
            404,
            "标签未登记",
            "这个 NTAG 标签不在本次活动名单中，请联系场务核对。",
        )

    return render(
        request,
        "register.html",
        token=ntag_token,
        entry_task=task,
        group=SimpleNamespace(name=task.group_no, color=task.group_color),
    )


@router.post("/api/register")
def register(
    body: RegisterBody,
    request: Request,
    db: Annotated[Session, Depends(get_db_session)],
):
    settings: Settings = request.app.state.settings
    task = find_task_by_token(db, body.ntag_token)
    if task is None:
        raise not_found("tag_not_found", "标签未登记，请联系场务")
    try:
        member, _created = register_from_task(
            db,
            task=task,
            name=body.name,
            actor_role="member",
            actor_id=None,
        )
        token, _session = create_member_session(db, member, settings)
        db.commit()
        db.refresh(member)
    except IntegrityError:
        db.rollback()
        raise conflict(
            "already_registered", "该标签已完成注册，请重新碰触标签恢复登录"
        ) from None
    except Exception:
        db.rollback()
        raise

    response = JSONResponse(
        {
            "ok": True,
            "redirect": "/home",
            "member": member_public(member),
        }
    )
    set_session_cookie(response, token, settings)
    request.app.state.events.publish()
    return response


@router.get("/home")
def home(request: Request, db: Annotated[Session, Depends(get_db_session)]):
    settings: Settings = request.app.state.settings
    auth = _member_from_request(request, db, settings)
    if auth is None:
        return error_page(
            request,
            401,
            "需要队员身份",
            "请先碰触自己名牌上的 NTAG 标签进入活动页面。",
        )
    member = auth.member
    assert member is not None
    stats = personal_stats(db, member)
    return render(
        request,
        "home.html",
        **member_context(member),
        stats=stats,
    )


@router.get("/me/qr")
def my_qr_page(request: Request, db: Annotated[Session, Depends(get_db_session)]):
    settings: Settings = request.app.state.settings
    auth = _member_from_request(request, db, settings)
    if auth is None:
        return error_page(
            request,
            401,
            "需要队员身份",
            "请先碰触自己名牌上的 NTAG 标签进入活动页面。",
        )
    assert auth.member is not None
    return render(request, "me_qr.html", **member_context(auth.member))


@router.get("/scan")
def scan_page(request: Request, db: Annotated[Session, Depends(get_db_session)]):
    settings: Settings = request.app.state.settings
    auth = _member_from_request(request, db, settings)
    if auth is None:
        return error_page(
            request,
            401,
            "需要队员身份",
            "请先碰触自己名牌上的 NTAG 标签进入活动页面。",
        )
    assert auth.member is not None
    return render(request, "scan.html", **member_context(auth.member))


@router.get("/kill/manual")
def manual_page(request: Request, db: Annotated[Session, Depends(get_db_session)]):
    settings: Settings = request.app.state.settings
    auth = _member_from_request(request, db, settings)
    if auth is None:
        return error_page(
            request,
            401,
            "需要队员身份",
            "请先碰触自己名牌上的 NTAG 标签进入活动页面。",
        )
    assert auth.member is not None
    return render(request, "manual.html", **member_context(auth.member))


@router.get("/kill/confirm")
def confirm_page(
    request: Request,
    code: str = "",
    db: Session = Depends(get_db_session),
):
    settings: Settings = request.app.state.settings
    auth = _member_from_request(request, db, settings)
    if auth is None:
        return error_page(
            request,
            401,
            "需要队员身份",
            "请先碰触自己名牌上的 NTAG 标签进入活动页面。",
        )
    assert auth.member is not None
    initial_code = ""
    code_error = ""
    try:
        initial_code = normalize_code(code)
    except AppError as exc:
        code_error = exc.message
    return render(
        request,
        "confirm.html",
        **member_context(auth.member),
        initial_code=initial_code,
        code_error=code_error,
    )


@router.get("/api/me")
def api_me(
    request: Request,
    auth: Annotated[AuthContext, Depends(require_member)],
    db: Annotated[Session, Depends(get_db_session)],
):
    assert auth.member is not None
    db.refresh(auth.member)
    return _member_payload(db, auth.member)


@router.get("/api/me/qr")
def api_me_qr(
    request: Request,
    auth: Annotated[AuthContext, Depends(require_member)],
    db: Annotated[Session, Depends(get_db_session)],
):
    assert auth.member is not None
    member = auth.member
    content = f"UID:{member.id}"
    return {
        "content": content,
        "member_id": member.id,
        "name": member.name,
        "status": member.status,
        "group": {
            "name": member.group_no,
            "color": member.group_color,
        },
        "image": qr_png_data_uri(content, box_size=12),
        "notice": "身份二维码仅用于同名区分，不参与击杀。",
    }


@router.post("/api/kills/lookup")
def api_lookup(
    body: CodeBody,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_member)],
    db: Annotated[Session, Depends(get_db_session)],
):
    assert auth.member is not None
    settings: Settings = request.app.state.settings
    limiter = request.app.state.limiter
    failure_key = f"kill-failure:{auth.member.id}"
    _check_kill_rate_limits(settings, limiter, auth.member.id, request, failure_key)

    try:
        result = lookup_victim(db, auth.member, body.code)
    except AppError as exc:
        if exc.code in {"code_not_found", "invalid_code"}:
            limiter.hit(
                failure_key,
                limit=settings.kill_failure_limit,
                window_seconds=settings.kill_failure_window_seconds,
            )
        raise
    limiter.reset(failure_key)
    return {"ok": True, **result}


@router.post("/api/kills/confirm")
def api_confirm(
    body: CodeBody,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_member)],
    db: Annotated[Session, Depends(get_db_session)],
):
    assert auth.member is not None
    settings: Settings = request.app.state.settings
    limiter = request.app.state.limiter
    failure_key = f"kill-failure:{auth.member.id}"
    _check_kill_rate_limits(settings, limiter, auth.member.id, request, failure_key)
    try:
        kill = confirm_kill(db, auth.member, body.code)
    except AppError as exc:
        if exc.code in {"code_not_found", "invalid_code"}:
            limiter.hit(
                failure_key,
                limit=settings.kill_failure_limit,
                window_seconds=settings.kill_failure_window_seconds,
            )
        raise
    limiter.reset(failure_key)
    request.app.state.events.publish()
    db.refresh(auth.member)
    stats = personal_stats(db, auth.member)
    return {
        "ok": True,
        "kill_id": kill.id,
        "kind": kill.kind,
        "source": kill.source,
        "message": "自爆成功" if kill.kind == "self" else "击杀成功",
        "stats": stats,
    }


def _check_kill_rate_limits(
    settings: Settings,
    limiter,
    member_id: str,
    request: Request,
    failure_key: str,
) -> None:
    allowed, retry_after = limiter.hit(
        f"kill-lookup:{member_id}:{client_ip(request)}",
        limit=settings.kill_lookup_limit,
        window_seconds=settings.kill_lookup_window_seconds,
    )
    if not allowed:
        raise too_many("操作过于频繁，请稍后再试", retry_after)
    failures = limiter.count(
        failure_key,
        window_seconds=settings.kill_failure_window_seconds,
    )
    if failures >= settings.kill_failure_limit:
        raise too_many(
            "连续查询失败次数过多，请稍后再试",
            settings.kill_failure_window_seconds,
        )


@router.post("/api/logout")
def logout(
    request: Request,
    auth: Annotated[AuthContext, Depends(require_member)],
    db: Annotated[Session, Depends(get_db_session)],
):
    if auth.session:
        auth.session.revoked = True
        db.commit()
    response = JSONResponse({"ok": True})
    clear_session_cookie(response, request.app.state.settings)
    return response
