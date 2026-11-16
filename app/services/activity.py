"""Activity lifecycle: reset rounds, admin registration/rename."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.errors import AppError, not_found
from app.models import Member, Round, UserSession, utcnow
from app.services.audit import write_audit
from app.services.stats import get_active_round, require_active_round
from app.utils import normalize_name


def reset_activity(
    db: Session,
    *,
    mode: str,
    actor_role: str,
    actor_id: str | None,
) -> dict:
    if mode not in {"soft", "hard"}:
        raise AppError(400, "invalid_reset_mode", "重置模式必须是 soft 或 hard")

    current = require_active_round(db)
    current.status = "ended"
    current.ended_at = utcnow()
    next_number = (db.scalar(select(func.max(Round.id))) or 0) + 1
    new_round = Round(
        name=f"第 {next_number} 轮",
        status="active",
        started_at=utcnow(),
    )
    db.add(new_round)
    db.flush()

    if mode == "hard":
        db.execute(
            update(Member).values(name=None, status="unregistered", registered_at=None)
        )
        db.execute(
            update(UserSession)
            .where(UserSession.role == "member")
            .values(revoked=True)
        )
        affected = db.scalar(select(func.count(Member.id))) or 0
    else:
        result = db.execute(
            update(Member)
            .where(Member.name.is_not(None), Member.status != "active")
            .values(status="active")
        )
        affected = result.rowcount or 0

    write_audit(
        db,
        action=f"activity_reset_{mode}",
        actor_role=actor_role,
        actor_id=actor_id,
        round_id=new_round.id,
        target_type="round",
        target_id=str(new_round.id),
        payload={
            "mode": mode,
            "previous_round_id": current.id,
            "previous_round_name": current.name,
            "new_round_name": new_round.name,
            "members_affected": affected,
        },
    )
    db.commit()
    db.refresh(new_round)
    return {
        "mode": mode,
        "round_id": new_round.id,
        "round_name": new_round.name,
        "members_affected": affected,
    }


def admin_set_member_name(
    db: Session,
    *,
    member_id: str,
    name: object,
    actor_role: str,
    actor_id: str | None,
) -> Member:
    member = db.get(Member, member_id)
    if member is None:
        raise not_found("member_not_found", "未找到该队员")
    clean_name = normalize_name(name)
    previous_name = member.name
    previous_status = member.status
    member.name = clean_name
    if member.status == "unregistered":
        member.status = "active"
        member.registered_at = utcnow()

    current_round = get_active_round(db)
    write_audit(
        db,
        action="member_name_changed",
        actor_role=actor_role,
        actor_id=actor_id,
        round_id=current_round.id if current_round else None,
        target_type="member",
        target_id=member.id,
        payload={
            "previous_name": previous_name,
            "new_name": clean_name,
            "previous_status": previous_status,
            "new_status": member.status,
            "source": "console",
        },
    )
    db.commit()
    db.refresh(member)
    return member
