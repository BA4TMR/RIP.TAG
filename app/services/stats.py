"""Round handling and dashboard/statistics queries."""

from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from app.errors import conflict
from app.models import (
    Group,
    Kill,
    Member,
    ProductionTask,
    Round,
    ScoreAdjustment,
    utcnow,
)
from app.utils import iso_utc


def get_active_round(db: Session) -> Round | None:
    return db.scalar(
        select(Round).where(Round.status == "active").order_by(Round.id.desc()).limit(1)
    )


def require_active_round(db: Session) -> Round:
    round_ = get_active_round(db)
    if round_ is None:
        raise conflict("no_active_round", "当前没有进行中的轮次，请先初始化活动")
    return round_


def ensure_active_round(db: Session) -> Round:
    active_rounds = list(
        db.scalars(select(Round).where(Round.status == "active").order_by(Round.id.desc()))
    )
    if active_rounds:
        current = active_rounds[0]
        for extra in active_rounds[1:]:
            extra.status = "ended"
            extra.ended_at = extra.ended_at or utcnow()
        db.commit()
        return current

    latest = db.scalar(select(func.max(Round.id))) or 0
    round_ = Round(name=f"第 {latest + 1} 轮", status="active", started_at=utcnow())
    db.add(round_)
    db.commit()
    db.refresh(round_)
    return round_


def dashboard_payload(db: Session) -> dict:
    round_ = require_active_round(db)

    task_counts = dict(
        db.execute(
            select(ProductionTask.group_no, func.count(ProductionTask.id))
            .group_by(ProductionTask.group_no)
        ).all()
    )
    member_rows = db.execute(
        select(
            Group.id,
            Group.name,
            Group.color,
            func.sum(
                case((Member.status.in_(("active", "eliminated")), 1), else_=0)
            ).label("registered"),
            func.sum(case((Member.status == "active", 1), else_=0)).label("alive"),
            func.sum(case((Member.status == "eliminated", 1), else_=0)).label("eliminated"),
        )
        .outerjoin(Member, Member.group_id == Group.id)
        .group_by(Group.id, Group.name, Group.color)
        .order_by(Group.id)
    ).all()

    killer_counts = dict(
        db.execute(
            select(Member.group_id, func.count(Kill.id))
            .join(Kill, Kill.killer_id == Member.id)
            .where(Kill.round_id == round_.id, Kill.status == "active")
            .group_by(Member.group_id)
        ).all()
    )
    victim_counts = dict(
        db.execute(
            select(Member.group_id, func.count(Kill.id))
            .join(Kill, Kill.victim_id == Member.id)
            .where(Kill.round_id == round_.id, Kill.status == "active")
            .group_by(Member.group_id)
        ).all()
    )
    score_sums = dict(
        db.execute(
            select(ScoreAdjustment.group_id, func.coalesce(func.sum(ScoreAdjustment.delta), 0))
            .where(
                ScoreAdjustment.round_id == round_.id,
                ScoreAdjustment.status == "active",
            )
            .group_by(ScoreAdjustment.group_id)
        ).all()
    )

    groups: list[dict] = []
    totals = {
        "total_members": 0,
        "registered": 0,
        "alive": 0,
        "eliminated": 0,
        "unregistered": 0,
        "kills": 0,
        "deaths": 0,
        "score": 0,
    }
    for row in member_rows:
        total = int(task_counts.get(row.name, 0))
        registered = int(row.registered or 0)
        alive = int(row.alive or 0)
        eliminated = int(row.eliminated or 0)
        unregistered = max(0, total - registered)
        kills = int(killer_counts.get(row.id, 0))
        deaths = int(victim_counts.get(row.id, 0))
        score = int(score_sums.get(row.id, 0))
        groups.append(
            {
                "id": row.id,
                "name": row.name,
                "color": row.color,
                "total": total,
                "registered": registered,
                "alive": alive,
                "eliminated": eliminated,
                "unregistered": unregistered,
                "kills": kills,
                "deaths": deaths,
                "score": score,
            }
        )
        totals["total_members"] += total
        totals["registered"] += registered
        totals["alive"] += alive
        totals["eliminated"] += eliminated
        totals["unregistered"] += unregistered
        totals["kills"] += kills
        totals["deaths"] += deaths
        totals["score"] += score

    return {
        "round": {
            "id": round_.id,
            "name": round_.name,
            "status": round_.status,
            "started_at": iso_utc(round_.started_at),
        },
        "groups": groups,
        "totals": totals,
        "recent_kills": recent_kills(db, round_.id, limit=8),
        "server_time": iso_utc(utcnow()),
        "updated_at": iso_utc(utcnow()),
    }


def recent_kills(db: Session, round_id: int, *, limit: int = 8) -> list[dict]:
    killer = aliased(Member)
    victim = aliased(Member)
    killer_group = aliased(Group)
    victim_group = aliased(Group)
    rows = db.execute(
        select(
            Kill.id,
            Kill.kind,
            Kill.source,
            Kill.status,
            Kill.created_at,
            killer.id.label("killer_id"),
            killer.name.label("killer_name"),
            killer_group.name.label("killer_group"),
            killer_group.color.label("killer_color"),
            victim.id.label("victim_id"),
            victim.name.label("victim_name"),
            victim_group.name.label("victim_group"),
            victim_group.color.label("victim_color"),
        )
        .join(killer, killer.id == Kill.killer_id)
        .join(victim, victim.id == Kill.victim_id)
        .join(killer_group, killer_group.id == killer.group_id)
        .join(victim_group, victim_group.id == victim.group_id)
        .where(Kill.round_id == round_id, Kill.status == "active")
        .order_by(Kill.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "source": row.source,
            "status": row.status,
            "created_at": iso_utc(row.created_at),
            "killer": {
                "id": row.killer_id,
                "name": row.killer_name,
                "group": row.killer_group,
                "color": row.killer_color,
            },
            "victim": {
                "id": row.victim_id,
                "name": row.victim_name,
                "group": row.victim_group,
                "color": row.victim_color,
            },
        }
        for row in rows
    ]


def personal_stats(db: Session, member: Member) -> dict:
    round_ = require_active_round(db)
    kills = db.scalar(
        select(func.count(Kill.id)).where(
            Kill.round_id == round_.id,
            Kill.killer_id == member.id,
            Kill.status == "active",
        )
    )
    deaths = db.scalar(
        select(func.count(Kill.id)).where(
            Kill.round_id == round_.id,
            Kill.victim_id == member.id,
            Kill.status == "active",
        )
    )
    score = db.scalar(
        select(func.coalesce(func.sum(ScoreAdjustment.delta), 0)).where(
            ScoreAdjustment.round_id == round_.id,
            ScoreAdjustment.group_id == member.group_id,
            ScoreAdjustment.status == "active",
        )
    )
    return {
        "kills": int(kills or 0),
        "deaths": int(deaths or 0),
        "group_score": int(score or 0),
        "round_id": round_.id,
        "round_name": round_.name,
    }
