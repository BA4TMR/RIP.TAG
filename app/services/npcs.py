"""NPC token management and production-lab writing metadata."""

from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AppError, conflict, not_found
from app.models import StaffToken
from app.services.audit import write_audit
from app.services.stats import get_active_round

NPC_QR_CODE = "XXXXXX"


def npc_url(settings: Settings, staff: StaffToken) -> str:
    return f"{settings.npc_base_url.rstrip('/')}/staff/{staff.token}"


def npc_payload(settings: Settings, staff: StaffToken) -> dict:
    return {
        "id": staff.id,
        "name": staff.name,
        "role": staff.role,
        "qr_code": staff.qr_code or NPC_QR_CODE,
        "active": staff.active,
        "token": staff.token,
        "url": npc_url(settings, staff),
        "created_at": staff.created_at.isoformat() if staff.created_at else None,
    }


def list_npcs(
    db: Session, settings: Settings, *, include_inactive: bool = True
) -> list[dict]:
    stmt = select(StaffToken).where(StaffToken.role == "npc")
    if not include_inactive:
        stmt = stmt.where(StaffToken.active.is_(True))
    stmt = stmt.order_by(StaffToken.id)
    return [npc_payload(settings, staff) for staff in db.scalars(stmt)]


def create_npc(
    db: Session,
    settings: Settings,
    *,
    name: str,
    actor_role: str = "admin",
    actor_id: str | None = "console",
) -> dict:
    clean_name = " ".join(str(name or "").split())
    if not clean_name:
        raise AppError(400, "invalid_npc_name", "NPC 名称不能为空")
    if len(clean_name) > 40:
        raise AppError(400, "invalid_npc_name", "NPC 名称不能超过 40 个字符")
    if db.scalar(
        select(StaffToken).where(
            StaffToken.role == "npc",
            StaffToken.name == clean_name,
            StaffToken.active.is_(True),
        )
    ):
        raise conflict("npc_name_exists", "已有同名且启用的 NPC")

    staff = StaffToken(
        token=secrets.token_urlsafe(32),
        role="npc",
        name=clean_name,
        qr_code=NPC_QR_CODE,
        active=True,
    )
    db.add(staff)
    db.flush()
    round_ = get_active_round(db)
    write_audit(
        db,
        action="npc_created",
        actor_role=actor_role,
        actor_id=actor_id,
        round_id=round_.id if round_ else None,
        target_type="npc",
        target_id=str(staff.id),
        payload={"name": clean_name, "qr_code": NPC_QR_CODE},
    )
    db.commit()
    db.refresh(staff)
    return npc_payload(settings, staff)


def disable_npc(
    db: Session,
    settings: Settings,
    *,
    staff_id: int,
    actor_role: str = "admin",
    actor_id: str | None = "console",
) -> dict:
    staff = db.get(StaffToken, staff_id)
    if staff is None or staff.role != "npc":
        raise not_found("npc_not_found", "未找到该 NPC")
    staff.active = False
    round_ = get_active_round(db)
    write_audit(
        db,
        action="npc_disabled",
        actor_role=actor_role,
        actor_id=actor_id,
        round_id=round_.id if round_ else None,
        target_type="npc",
        target_id=str(staff.id),
        payload={"name": staff.name},
    )
    db.commit()
    db.refresh(staff)
    return npc_payload(settings, staff)
