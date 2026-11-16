"""First-run activity setup: generate groups and production_tasks.

The console asks for a group count and people per group only when there are no
production tasks. This keeps a fresh deployment usable without a pre-made CSV
and gives the production workstation a ready queue.
"""

from __future__ import annotations

import secrets

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.errors import AppError, conflict
from app.models import (
    Group,
    Kill,
    Member,
    ProductionTask,
    Round,
    ScoreAdjustment,
    utcnow,
)
from app.services.audit import write_audit
from app.services.stats import get_active_round

GROUP_PALETTE = [
    ("红队", "#E53935"),
    ("蓝队", "#1E88E5"),
    ("黄队", "#FDD835"),
    ("绿队", "#43A047"),
    ("紫队", "#8E24AA"),
    ("橙队", "#FB8C00"),
]
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def needs_setup(db: Session) -> bool:
    return (db.scalar(select(func.count(ProductionTask.id))) or 0) == 0


def _new_code(existing: set[str]) -> str:
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if code not in existing:
            existing.add(code)
            return code


def initialize_activity(
    db: Session,
    *,
    group_count: int,
    per_group: int,
    actor_role: str,
    actor_id: str | None,
) -> dict:
    try:
        group_count = int(group_count)
        per_group = int(per_group)
    except (TypeError, ValueError):
        raise AppError(400, "invalid_setup_counts", "组数和每组人数必须是整数") from None

    if group_count < 1 or group_count > 6:
        raise AppError(400, "invalid_group_count", "组数必须在 1-6 之间")
    if per_group < 1 or per_group > 10:
        raise AppError(400, "invalid_per_group", "每组人数必须在 1-10 之间")
    if group_count * per_group > 60:
        raise AppError(400, "too_many_members", "总人数不能超过 60")

    if (db.scalar(select(func.count(ProductionTask.id))) or 0) > 0:
        raise conflict("already_initialized", "活动已经初始化，不能重复生成")
    if (db.scalar(select(func.count(Member.id))) or 0) > 0:
        raise conflict("activity_has_members", "已存在队员数据，不能执行首次初始化")
    if (db.scalar(select(func.count(Kill.id))) or 0) > 0:
        raise conflict("activity_has_kills", "已存在击杀数据，不能执行首次初始化")
    if (db.scalar(select(func.count(ScoreAdjustment.id))) or 0) > 0:
        raise conflict("activity_has_scores", "已存在积分数据，不能执行首次初始化")

    # A failed/old empty setup may have left group rows with no tasks. They are
    # safe to replace because members/kills/scores are empty.
    db.execute(delete(Group))
    db.flush()

    round_ = get_active_round(db)
    if round_ is None:
        round_ = Round(name="第 1 轮", status="active", started_at=utcnow())
        db.add(round_)
        db.flush()
    groups: list[Group] = []
    for index in range(group_count):
        name, color = GROUP_PALETTE[index]
        group = Group(name=name, color=color)
        db.add(group)
        groups.append(group)
    db.flush()

    existing_codes: set[str] = set()
    existing_tokens: set[str] = set()
    tasks: list[ProductionTask] = []
    for group in groups:
        for _ in range(per_group):
            token = secrets.token_urlsafe(24)
            while token in existing_tokens:
                token = secrets.token_urlsafe(24)
            existing_tokens.add(token)
            task = ProductionTask(
                ntag_token=token,
                qr_code=_new_code(existing_codes),
                group_no=group.name,
                group_color=group.color,
            )
            db.add(task)
            tasks.append(task)
    db.flush()

    write_audit(
        db,
        action="activity_initialized",
        actor_role=actor_role,
        actor_id=actor_id,
        round_id=round_.id,
        target_type="activity",
        target_id=str(round_.id),
        payload={
            "group_count": group_count,
            "per_group": per_group,
            "task_count": len(tasks),
            "groups": [
                {"id": group.id, "name": group.name, "color": group.color}
                for group in groups
            ],
        },
    )
    db.commit()
    return {
        "ok": True,
        "group_count": group_count,
        "per_group": per_group,
        "task_count": len(tasks),
        "groups": [
            {"id": group.id, "name": group.name, "color": group.color}
            for group in groups
        ],
        "initialized_at": round_.started_at.isoformat() if round_.started_at else None,
    }
