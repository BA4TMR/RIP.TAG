"""Group score adjustments."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.errors import AppError, conflict, not_found
from app.models import Group, ScoreAdjustment, utcnow
from app.services.audit import write_audit
from app.services.stats import require_active_round
from app.utils import iso_utc, normalize_reason


def add_score(
    db: Session,
    *,
    group_id: int,
    delta: int,
    reason: object,
    actor_role: str,
    actor_id: str | None,
    allow_negative: bool,
) -> ScoreAdjustment:
    group = db.get(Group, group_id)
    if group is None:
        raise not_found("group_not_found", "未找到该组")
    try:
        value = int(delta)
    except (TypeError, ValueError):
        raise AppError(400, "invalid_delta", "分值必须是整数") from None
    if value == 0:
        raise AppError(400, "invalid_delta", "分值不能为 0")
    if abs(value) > 1000:
        raise AppError(400, "invalid_delta", "单次分值不能超过 1000")
    if not allow_negative and value < 0:
        raise AppError(403, "npc_positive_only", "NPC 只能加分，扣分或修正请联系技术台")
    clean_reason = normalize_reason(reason)
    round_ = require_active_round(db)

    adjustment = ScoreAdjustment(
        round_id=round_.id,
        group_id=group.id,
        delta=value,
        reason=clean_reason,
        operator_role=actor_role,
        operator_id=actor_id,
        status="active",
    )
    db.add(adjustment)
    db.flush()
    write_audit(
        db,
        action="score_added",
        actor_role=actor_role,
        actor_id=actor_id,
        round_id=round_.id,
        target_type="score_adjustment",
        target_id=str(adjustment.id),
        payload={
            "group_id": group.id,
            "group_name": group.name,
            "delta": value,
            "reason": clean_reason,
        },
    )
    db.commit()
    db.refresh(adjustment)
    return adjustment


def revoke_score(
    db: Session,
    *,
    adjustment_id: int,
    actor_role: str,
    actor_id: str | None,
    actor_label: str,
) -> ScoreAdjustment:
    adjustment = db.get(ScoreAdjustment, adjustment_id)
    if adjustment is None:
        raise not_found("score_not_found", "未找到该积分记录")
    if adjustment.status != "active":
        raise conflict("score_already_revoked", "该积分记录已被撤销")

    result = db.execute(
        update(ScoreAdjustment)
        .where(
            ScoreAdjustment.id == adjustment.id,
            ScoreAdjustment.status == "active",
        )
        .values(status="revoked", revoked_at=utcnow(), revoked_by=actor_label)
    )
    if result.rowcount != 1:
        db.rollback()
        raise conflict("score_already_revoked", "该积分记录已被撤销")

    write_audit(
        db,
        action="score_revoked",
        actor_role=actor_role,
        actor_id=actor_id,
        round_id=adjustment.round_id,
        target_type="score_adjustment",
        target_id=str(adjustment.id),
        payload={"group_id": adjustment.group_id, "delta": adjustment.delta},
    )
    db.commit()
    db.refresh(adjustment)
    return adjustment


def adjustment_to_dict(adjustment: ScoreAdjustment, group: Group | None = None) -> dict:
    return {
        "id": adjustment.id,
        "round_id": adjustment.round_id,
        "group_id": adjustment.group_id,
        "group_name": group.name if group else None,
        "group_color": group.color if group else None,
        "delta": adjustment.delta,
        "reason": adjustment.reason,
        "operator_role": adjustment.operator_role,
        "operator_id": adjustment.operator_id,
        "status": adjustment.status,
        "created_at": iso_utc(adjustment.created_at),
        "revoked_at": iso_utc(adjustment.revoked_at),
        "revoked_by": adjustment.revoked_by,
    }


def list_adjustments(
    db: Session,
    *,
    round_id: int | None = None,
    limit: int = 100,
) -> list[dict]:
    stmt = select(ScoreAdjustment, Group).join(Group, Group.id == ScoreAdjustment.group_id)
    if round_id is not None:
        stmt = stmt.where(ScoreAdjustment.round_id == round_id)
    stmt = stmt.order_by(ScoreAdjustment.id.desc()).limit(limit)
    return [adjustment_to_dict(row, group) for row, group in db.execute(stmt).all()]
