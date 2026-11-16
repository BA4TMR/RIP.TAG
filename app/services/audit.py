"""Audit-log helper. Callers commit in the same transaction as the action."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AuditLog
from app.utils import json_dumps


def write_audit(
    db: Session,
    *,
    action: str,
    actor_role: str,
    actor_id: str | None,
    round_id: int | None,
    target_type: str | None = None,
    target_id: str | None = None,
    payload: object | None = None,
) -> AuditLog:
    entry = AuditLog(
        round_id=round_id,
        actor_role=actor_role,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload_json=json_dumps(payload) if payload is not None else None,
    )
    db.add(entry)
    return entry
