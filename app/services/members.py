"""Task/member lookup and registration helpers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.errors import conflict, not_found
from app.models import Group, Member, ProductionTask, utcnow
from app.services.audit import write_audit
from app.services.stats import get_active_round
from app.utils import normalize_name


def find_task_by_token(db: Session, token: str) -> ProductionTask | None:
    return db.scalar(select(ProductionTask).where(ProductionTask.ntag_token == token))


def find_member_by_token(db: Session, token: str) -> Member | None:
    return db.scalar(
        select(Member)
        .options(joinedload(Member.group), joinedload(Member.production_task))
        .where(Member.ntag_token == token)
    )


def find_member_by_task(db: Session, task_id: int) -> Member | None:
    return db.scalar(
        select(Member)
        .options(joinedload(Member.group), joinedload(Member.production_task))
        .where(Member.production_task_id == task_id)
    )


def group_for_task(db: Session, task: ProductionTask) -> Group:
    group = db.scalar(select(Group).where(Group.name == task.group_no))
    if group is None:
        group = Group(name=task.group_no, color=task.group_color)
        db.add(group)
        db.flush()
    return group


def register_from_task(
    db: Session,
    *,
    task: ProductionTask,
    name: object,
    actor_role: str,
    actor_id: str | None,
) -> tuple[Member, bool]:
    """Create a member, or finish registration for a hard-reset/migrated row.

    Returns ``(member, created)``. The caller owns the commit and session
    creation so registration and cookie issuing stay in one transaction.
    """
    clean_name = normalize_name(name)
    member = find_member_by_task(db, task.id)
    created = member is None
    if member is not None and (member.name or member.status != "unregistered"):
        raise conflict("already_registered", "该标签已完成注册，请重新碰触标签恢复登录")

    group = group_for_task(db, task)
    if member is None:
        member = Member(
            production_task_id=task.id,
            ntag_token=task.ntag_token,
            qr_code=task.qr_code,
            group_id=group.id,
            group_no=task.group_no,
            group_color=task.group_color,
            status="unregistered",
        )
        db.add(member)

    member.name = clean_name
    member.status = "active"
    member.registered_at = utcnow()
    member.group_id = group.id
    member.group_no = task.group_no
    member.group_color = task.group_color
    member.ntag_token = task.ntag_token
    member.qr_code = task.qr_code
    db.flush()

    current_round = get_active_round(db)
    write_audit(
        db,
        action="member_registered" if actor_role == "member" else "npc_registered_member",
        actor_role=actor_role,
        actor_id=actor_id or (member.id if actor_role == "member" else None),
        round_id=current_round.id if current_round else None,
        target_type="member",
        target_id=member.id,
        payload={
            "name": member.name,
            "group_no": member.group_no,
            "group_color": member.group_color,
            "production_task_id": task.id,
            "created": created,
        },
    )
    return member, created


def require_task(db: Session, task_id: int) -> ProductionTask:
    task = db.get(ProductionTask, task_id)
    if task is None:
        raise not_found("production_task_not_found", "未找到该制作任务")
    return task
