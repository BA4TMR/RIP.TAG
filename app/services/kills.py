"""Kill lookup, atomic elimination, revocation and revival."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, joinedload

from app.errors import AppError, conflict, forbidden, not_found
from app.models import Kill, Member, utcnow
from app.services.audit import write_audit
from app.services.stats import require_active_round
from app.utils import iso_utc, normalize_code


def get_member(db: Session, member_id: str) -> Member:
    member = db.scalar(
        select(Member)
        .options(joinedload(Member.group), joinedload(Member.production_task))
        .where(Member.id == member_id)
    )
    if member is None:
        raise not_found("member_not_found", "未找到该队员")
    return member


def member_public(member: Member, *, include_code: bool = False, include_id: bool = True) -> dict:
    payload = {
        "id": member.id if include_id else None,
        "production_task_id": member.production_task_id,
        "name": member.name,
        "status": member.status,
        "group": {
            "id": member.group_id,
            "name": member.group_no,
            "color": member.group_color,
        },
    }
    if include_code:
        payload["qr_code"] = member.qr_code
    return payload


def lookup_victim(db: Session, killer: Member, code: object) -> dict:
    normalized = normalize_code(code)
    victim = db.scalar(
        select(Member).options(joinedload(Member.group)).where(Member.qr_code == normalized)
    )
    if victim is None:
        raise not_found("code_not_found", "未找到该击杀码，请核对后重试")

    if killer.status != "active":
        raise forbidden("killer_eliminated", "你已被淘汰，不能继续击杀")

    is_self = victim.id == killer.id
    can_kill = victim.status == "active"
    if victim.status == "eliminated":
        message = "该队员已被击杀"
    elif victim.status == "unregistered":
        message = "该队员尚未注册，无法击杀"
        can_kill = False
    elif is_self:
        message = "这是你自己的击杀码，确认后将自爆"
    else:
        message = "确认后将淘汰该队员"

    return {
        "victim": member_public(victim),
        "is_self": is_self,
        "can_kill": can_kill,
        "message": message,
    }


def _write_kill(
    db: Session,
    *,
    killer: Member,
    victim: Member,
    round_id: int,
    source: str,
    note: str | None,
    actor_role: str,
    actor_id: str | None,
    require_killer_active_after_lock: bool,
) -> Kill:
    # The conditional UPDATE is the concurrency gate: only one request can
    # move this victim from active to eliminated, even if several phones scan
    # the same QR code at the same instant.
    result = db.execute(
        update(Member)
        .where(Member.id == victim.id, Member.status == "active")
        .values(status="eliminated")
    )
    if result.rowcount != 1:
        db.rollback()
        current = db.get(Member, victim.id)
        if current is not None and current.status == "eliminated":
            raise conflict("victim_eliminated", "该队员已被击杀")
        raise conflict("kill_conflict", "淘汰失败，请刷新后重试")

    if require_killer_active_after_lock:
        # Prevent a kill from a player who was eliminated by a concurrent
        # request while this transaction was starting. Holding the SQLite
        # write lock makes this check stable until commit.
        current_killer = db.get(Member, killer.id)
        if current_killer is not None:
            db.refresh(current_killer)
        if current_killer is None or current_killer.status != "active":
            db.rollback()
            raise forbidden("killer_eliminated", "你已被淘汰，不能继续击杀")

    kind = "self" if killer.id == victim.id else "normal"
    kill = Kill(
        round_id=round_id,
        killer_id=killer.id,
        victim_id=victim.id,
        kind=kind,
        source=source,
        status="active",
        note=note,
    )
    db.add(kill)
    db.flush()
    write_audit(
        db,
        action="kill_self" if kind == "self" else "kill_recorded",
        actor_role=actor_role,
        actor_id=actor_id,
        round_id=round_id,
        target_type="kill",
        target_id=str(kill.id),
        payload={
            "killer_id": killer.id,
            "killer_name": killer.name,
            "victim_id": victim.id,
            "victim_name": victim.name,
            "kind": kind,
            "source": source,
            "note": note,
        },
    )
    db.commit()
    db.refresh(kill)
    return kill


def confirm_kill(db: Session, killer: Member, code: object) -> Kill:
    normalized = normalize_code(code)
    victim = db.scalar(
        select(Member).options(joinedload(Member.group)).where(Member.qr_code == normalized)
    )
    if victim is None:
        raise not_found("code_not_found", "未找到该击杀码，请核对后重试")
    if killer.status != "active":
        raise forbidden("killer_eliminated", "你已被淘汰，不能继续击杀")
    if victim.status == "eliminated":
        raise conflict("victim_eliminated", "该队员已被击杀")
    if victim.status == "unregistered":
        raise conflict("victim_unregistered", "该队员尚未注册，无法击杀")

    round_ = require_active_round(db)
    return _write_kill(
        db,
        killer=killer,
        victim=victim,
        round_id=round_.id,
        source="scan",
        note=None,
        actor_role="member",
        actor_id=killer.id,
        require_killer_active_after_lock=killer.id != victim.id,
    )


def manual_kill(
    db: Session,
    *,
    killer: Member,
    victim: Member,
    actor_role: str,
    actor_id: str | None,
    note: str | None = None,
) -> Kill:
    if killer.id == victim.id:
        raise AppError(400, "self_manual", "手动补录不能选择同一人；自爆请由队员扫码完成")
    if killer.status != "active":
        raise conflict("killer_eliminated", "击杀者已被淘汰，不能补录击杀")
    if victim.status == "eliminated":
        raise conflict("victim_eliminated", "被击杀者已被淘汰")
    if victim.status == "unregistered":
        raise conflict("victim_unregistered", "被击杀者尚未注册")
    round_ = require_active_round(db)
    manual_note = note or "技术台手动添加"
    return _write_kill(
        db,
        killer=killer,
        victim=victim,
        round_id=round_.id,
        source="manual",
        note=manual_note,
        actor_role=actor_role,
        actor_id=actor_id,
        require_killer_active_after_lock=False,
    )


def revoke_kill(
    db: Session,
    *,
    kill_id: int,
    actor_role: str,
    actor_id: str | None,
    actor_label: str,
) -> tuple[Kill, bool]:
    kill = db.get(Kill, kill_id)
    if kill is None:
        raise not_found("kill_not_found", "未找到该击杀记录")
    if kill.status != "active":
        raise conflict("kill_already_revoked", "该击杀记录已被撤销")

    result = db.execute(
        update(Kill)
        .where(Kill.id == kill.id, Kill.status == "active")
        .values(status="revoked", revoked_at=utcnow(), revoked_by=actor_label)
    )
    if result.rowcount != 1:
        db.rollback()
        raise conflict("kill_already_revoked", "该击杀记录已被撤销")

    victim = db.get(Member, kill.victim_id)
    revived = False
    if victim is not None and victim.status == "eliminated":
        # If this victim was revived and killed again, an older revocation must
        # not erase the newer active elimination. Only the latest active kill
        # has authority to revive the victim.
        later_active = db.scalar(
            select(func.count(Kill.id)).where(
                Kill.victim_id == victim.id,
                Kill.status == "active",
                Kill.id > kill.id,
            )
        )
        if not later_active:
            victim.status = "active"
            revived = True

    write_audit(
        db,
        action="kill_revoked",
        actor_role=actor_role,
        actor_id=actor_id,
        round_id=kill.round_id,
        target_type="kill",
        target_id=str(kill.id),
        payload={
            "killer_id": kill.killer_id,
            "victim_id": kill.victim_id,
            "kind": kill.kind,
            "source": kill.source,
            "revived": revived,
        },
    )
    db.commit()
    db.refresh(kill)
    return kill, revived


def revive_member(
    db: Session,
    *,
    member_id: str,
    actor_role: str,
    actor_id: str | None,
) -> Member:
    member = get_member(db, member_id)
    if member.status != "eliminated":
        raise conflict("member_not_eliminated", "该队员当前未被淘汰")
    round_ = require_active_round(db)

    result = db.execute(
        update(Member)
        .where(Member.id == member.id, Member.status == "eliminated")
        .values(status="active")
    )
    if result.rowcount != 1:
        db.rollback()
        raise conflict("member_not_eliminated", "该队员当前未被淘汰")

    write_audit(
        db,
        action="member_revived",
        actor_role=actor_role,
        actor_id=actor_id,
        round_id=round_.id,
        target_type="member",
        target_id=member.id,
        payload={"name": member.name, "group_id": member.group_id},
    )
    db.commit()
    db.refresh(member)
    return member


def kill_to_dict(kill: Kill, *, round_name: str | None = None) -> dict:
    return {
        "id": kill.id,
        "round_id": kill.round_id,
        "round_name": round_name,
        "killer_id": kill.killer_id,
        "victim_id": kill.victim_id,
        "kind": kill.kind,
        "source": kill.source,
        "status": kill.status,
        "created_at": iso_utc(kill.created_at),
        "revoked_at": iso_utc(kill.revoked_at),
        "revoked_by": kill.revoked_by,
        "note": kill.note,
    }
