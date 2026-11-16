"""Authentication, sessions, cookies and mTLS enforcement."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db_session
from app.errors import forbidden, unauthorized
from app.models import Member, StaffToken, UserSession, utcnow

Role = Literal["member", "npc", "admin"]


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class AuthContext:
    role: Role
    session: UserSession | None = None
    member: Member | None = None
    staff: StaffToken | None = None

    @property
    def actor_id(self) -> str | None:
        if self.role == "member" and self.member:
            return self.member.id
        if self.role == "npc" and self.staff:
            return str(self.staff.id)
        if self.role == "admin":
            return "console"
        return None

    @property
    def display_name(self) -> str:
        if self.member:
            return self.member.name or "未注册队员"
        if self.staff:
            return self.staff.name
        return "技术台"


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def create_member_session(db: Session, member: Member, settings: Settings) -> tuple[str, UserSession]:
    return create_session(
        db,
        settings,
        role="member",
        member_id=member.id,
    )


def create_session(
    db: Session,
    settings: Settings,
    *,
    role: Role,
    member_id: str | None = None,
    staff_token_id: int | None = None,
) -> tuple[str, UserSession]:
    token = new_session_token()
    session = UserSession(
        token_hash=hash_token(token),
        role=role,
        member_id=member_id,
        staff_token_id=staff_token_id,
        expires_at=utcnow() + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(session)
    db.flush()
    return token, session


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.cookie_name,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def resolve_auth(request: Request, db: Session, settings: Settings) -> AuthContext | None:
    raw_token = request.cookies.get(settings.cookie_name)
    if not raw_token:
        return None
    session = db.scalar(
        select(UserSession).where(
            UserSession.token_hash == hash_token(raw_token),
            UserSession.revoked.is_(False),
        )
    )
    if session is None or session.expires_at <= utcnow():
        return None

    if session.role == "member":
        member = db.get(Member, session.member_id) if session.member_id else None
        if member is None:
            return None
        return AuthContext(role="member", session=session, member=member)
    if session.role == "npc":
        staff = db.get(StaffToken, session.staff_token_id) if session.staff_token_id else None
        if staff is None or not staff.active:
            return None
        return AuthContext(role="npc", session=session, staff=staff)
    return None


def get_current_auth(
    request: Request,
    db: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthContext:
    auth = resolve_auth(request, db, settings)
    if auth is None:
        raise unauthorized()
    return auth


def require_member(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> AuthContext:
    if auth.role != "member" or auth.member is None:
        raise forbidden("member_only", "此操作仅限参赛队员")
    return auth


def require_npc(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> AuthContext:
    if auth.role != "npc" or auth.staff is None:
        raise forbidden("npc_only", "此操作仅限 NPC 场务")
    return auth


def require_dashboard_access(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> AuthContext:
    if auth.role not in {"npc", "admin"}:
        raise forbidden("staff_only", "看板仅限技术台与 NPC 访问")
    return auth


def _assert_local_development_bypass(request: Request) -> None:
    """Keep an accidentally enabled dev bypass from becoming a public bypass."""
    host = request.client.host if request.client else ""
    allowed_hosts = {"127.0.0.1", "::1", "localhost", "testclient"}
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    if host not in allowed_hosts:
        raise forbidden("insecure_bypass_remote", "开发绕过仅允许本机访问")
    if forwarded and forwarded not in allowed_hosts:
        raise forbidden("insecure_bypass_remote", "开发绕过仅允许本机访问")


def require_admin(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthContext:
    """Trust only the reverse proxy's TLS client verification result.

    Uvicorn must bind to loopback. Nginx overwrites X-Client-Verify from the
    actual TLS handshake, so a request sent directly to the app cannot forge a
    successful result in production.
    """
    if settings.allow_insecure_console:
        _assert_local_development_bypass(request)
        return AuthContext(role="admin")
    verify = (request.headers.get("x-client-verify") or "").strip().upper()
    if verify != "SUCCESS":
        raise forbidden(
            "mtls_required",
            "主控入口需要有效的客户端证书，请使用已安装主控证书的设备访问",
        )
    return AuthContext(role="admin")


def require_production(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthContext:
    """Gate the production workstation and its APIs with mTLS.

    This intentionally has its own bypass flag so local development can open
    the workstation without weakening the console's production checks.
    """
    if settings.allow_insecure_production:
        _assert_local_development_bypass(request)
        return AuthContext(role="admin")
    verify = (request.headers.get("x-client-verify") or "").strip().upper()
    if verify != "SUCCESS":
        raise forbidden(
            "mtls_required",
            "制作 API 需要有效的客户端证书，请使用已安装制作证书的设备访问",
        )
    return AuthContext(role="admin")


def client_ip(request: Request) -> str:
    # The app is reachable only through the local reverse proxy in production.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"
