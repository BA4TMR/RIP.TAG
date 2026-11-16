"""Nameplate production state machine (contract v1.1).

Production state lives in ``production_tasks`` and has no effect on member
registration, kills, rounds or dashboard statistics.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.errors import conflict, not_found
from app.models import ProductionTask, utcnow
from app.services.audit import write_audit
from app.utils import CONTROL_RE, iso_utc

Stage = Literal["written", "printed", "done"]
STAGE_LABELS: dict[str, str] = {
    "pending": "待制作",
    "written": "已写卡",
    "printed": "已打印",
    "done": "已完成",
}


def task_stage(task: ProductionTask) -> str:
    if task.done_at is not None:
        return "done"
    if task.printed_at is not None:
        return "printed"
    if task.written_at is not None:
        return "written"
    return "pending"


def task_payload(task: ProductionTask) -> dict:
    stage = task_stage(task)
    return {
        "id": task.id,
        "ntag_token": task.ntag_token,
        "qr_code": task.qr_code,
        "group_no": task.group_no,
        "group_color": task.group_color,
        "written_at": iso_utc(task.written_at),
        "printed_at": iso_utc(task.printed_at),
        "done_at": iso_utc(task.done_at),
        "last_error": task.last_error,
        "stage": stage,
        "stage_label": STAGE_LABELS[stage],
    }


def list_tasks(db: Session, *, status: str = "all") -> list[dict]:
    stmt = select(ProductionTask)
    if status == "pending":
        stmt = stmt.where(
            ProductionTask.written_at.is_(None),
            ProductionTask.printed_at.is_(None),
            ProductionTask.done_at.is_(None),
        )
    elif status == "in_progress":
        stmt = stmt.where(
            ProductionTask.done_at.is_(None),
            (ProductionTask.written_at.is_not(None) | ProductionTask.printed_at.is_not(None)),
        )
    elif status == "done":
        stmt = stmt.where(ProductionTask.done_at.is_not(None))
    stmt = stmt.order_by(
        case(
            (ProductionTask.done_at.is_not(None), 2),
            (ProductionTask.written_at.is_not(None), 1),
            else_=0,
        ),
        ProductionTask.group_no,
        ProductionTask.id,
    )
    return [task_payload(task) for task in db.scalars(stmt)]


def _normalize_error(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > 500:
        text = text[:500]
    return CONTROL_RE.sub("", text) or None


def mark_stage(
    db: Session,
    *,
    task_id: int,
    stage: Stage,
    error: str | None,
    actor_id: str | None = "production",
) -> dict:
    task = db.get(ProductionTask, task_id)
    if task is None:
        raise not_found("production_task_not_found", "未找到该制作任务")

    clean_error = _normalize_error(error)
    previous_stage = task_stage(task)

    if clean_error is not None:
        task.last_error = clean_error
        write_audit(
            db,
            action="production_error",
            actor_role="admin",
            actor_id=actor_id,
            round_id=None,
            target_type="production_task",
            target_id=str(task.id),
            payload={
                "stage": stage,
                "previous_stage": previous_stage,
                "error": clean_error,
            },
        )
        db.commit()
        db.refresh(task)
        return task_payload(task)

    if stage == "written":
        if task.done_at is not None:
            raise conflict("production_already_done", "该名牌已完成，请先重置后再重做")
        task.written_at = task.written_at or utcnow()
    elif stage == "printed":
        if task.done_at is not None:
            raise conflict("production_already_done", "该名牌已完成，请先重置后再重做")
        if task.written_at is None:
            raise conflict("production_write_required", "请先完成 NTAG 写卡并校验")
        task.printed_at = task.printed_at or utcnow()
    elif stage == "done":
        if task.printed_at is None:
            raise conflict("production_print_required", "请先完成标签打印")
        task.done_at = task.done_at or utcnow()
    else:
        raise conflict("production_invalid_stage", "未知的制作阶段")

    task.last_error = None
    write_audit(
        db,
        action=f"production_{stage}",
        actor_role="admin",
        actor_id=actor_id,
        round_id=None,
        target_type="production_task",
        target_id=str(task.id),
        payload={
            "stage": stage,
            "previous_stage": previous_stage,
            "idempotent": previous_stage == stage,
        },
    )
    db.commit()
    db.refresh(task)
    return task_payload(task)


def reset_task(db: Session, *, task_id: int, actor_id: str | None = "production") -> dict:
    task = db.get(ProductionTask, task_id)
    if task is None:
        raise not_found("production_task_not_found", "未找到该制作任务")

    previous_stage = task_stage(task)
    task.written_at = None
    task.printed_at = None
    task.done_at = None
    task.last_error = None
    write_audit(
        db,
        action="production_reset",
        actor_role="admin",
        actor_id=actor_id,
        round_id=None,
        target_type="production_task",
        target_id=str(task.id),
        payload={"previous_stage": previous_stage},
    )
    db.commit()
    db.refresh(task)
    return task_payload(task)


def status_summary(db: Session) -> dict:
    total = db.scalar(select(func.count(ProductionTask.id))) or 0
    written = (
        db.scalar(
            select(func.count(ProductionTask.id)).where(ProductionTask.written_at.is_not(None))
        )
        or 0
    )
    printed = (
        db.scalar(
            select(func.count(ProductionTask.id)).where(ProductionTask.printed_at.is_not(None))
        )
        or 0
    )
    done = (
        db.scalar(
            select(func.count(ProductionTask.id)).where(ProductionTask.done_at.is_not(None))
        )
        or 0
    )
    failed = (
        db.scalar(
            select(func.count(ProductionTask.id)).where(
                ProductionTask.last_error.is_not(None),
                ProductionTask.done_at.is_(None),
            )
        )
        or 0
    )
    pending = (
        db.scalar(
            select(func.count(ProductionTask.id)).where(
                ProductionTask.written_at.is_(None),
                ProductionTask.printed_at.is_(None),
                ProductionTask.done_at.is_(None),
            )
        )
        or 0
    )
    in_progress = max(0, int(total) - int(pending) - int(done))
    return {
        "total": int(total),
        "written": int(written),
        "printed": int(printed),
        "done": int(done),
        "failed": int(failed),
        "pending": int(pending),
        "in_progress": in_progress,
        "all_done": bool(total) and int(done) == int(total),
    }
