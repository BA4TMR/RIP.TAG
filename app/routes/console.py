"""Main-control console: mTLS-protected operations and exports."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased, joinedload

from app.config import Settings
from app.db import get_db_session
from app.errors import AppError, bad_request
from app.models import AuditLog, Group, Kill, Member, Round, ScoreAdjustment
from app.routes.realtime import build_stream_response
from app.security import AuthContext, require_admin
from app.services.activity import admin_set_member_name, reset_activity
from app.services.audit import write_audit
from app.services.kills import (
    get_member,
    kill_to_dict,
    manual_kill,
    member_public,
    revive_member,
    revoke_kill,
)
from app.services.npcs import (
    create_npc,
    disable_npc,
    list_npcs,
)
from app.services.scores import add_score, adjustment_to_dict, revoke_score
from app.services.setup import initialize_activity, needs_setup
from app.services.stats import dashboard_payload, require_active_round
from app.utils import (
    clamp_limit,
    csv_safe,
    display_time,
    iso_utc,
    json_loads,
    normalize_reason,
)
from app.web import error_page, render

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


class ManualKillBody(BaseModel):
    killer_id: str = Field(min_length=1, max_length=40)
    victim_id: str = Field(min_length=1, max_length=40)
    note: str | None = Field(default=None, max_length=200)


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class ScoreBody(BaseModel):
    group_id: int
    delta: int = Field(ge=-1000, le=1000)
    reason: str | None = Field(default=None, max_length=200)


class ResetBody(BaseModel):
    mode: str = Field(pattern="^(soft|hard)$")
    confirm: str = Field(min_length=1, max_length=20)


class SetupBody(BaseModel):
    group_count: int = Field(ge=1, le=6)
    per_group: int = Field(ge=1, le=10)


class NpcCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)


def create_console_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix=settings.console_base, tags=["console"])
    base = settings.console_base

    def _page_guard(request: Request) -> AuthContext | None:
        try:
            return require_admin(request, settings)
        except AppError:
            return None

    @router.get("")
    @router.get("/")
    def console_page(request: Request, db: Annotated[Session, Depends(get_db_session)]):
        if _page_guard(request) is None:
            return error_page(
                request,
                403,
                "需要主控证书",
                "主控台要求有效的客户端证书。请使用已安装主控证书的设备，通过专属 HTTPS 地址访问。",
            )
        if needs_setup(db):
            return render(request, "console_setup.html", console_base=base)
        round_ = require_active_round(db)
        groups = list(db.scalars(select(Group).order_by(Group.id)))
        return render(
            request,
            "console.html",
            console_base=base,
            groups=groups,
            round=round_,
        )

    @router.post("/api/setup")
    def setup_activity(
        body: SetupBody,
        request: Request,
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        result = initialize_activity(
            db,
            group_count=body.group_count,
            per_group=body.per_group,
            actor_role="admin",
            actor_id=auth.actor_id,
        )
        request.app.state.events.publish()
        return result

    @router.get("/api/setup/status")
    def setup_status(
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        return {"needs_setup": needs_setup(db)}

    @router.get("/api/npcs")
    def npc_list(
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        return {"npcs": list_npcs(db, settings)}

    @router.post("/api/npcs")
    def npc_create(
        body: NpcCreateBody,
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        npc = create_npc(
            db,
            settings,
            name=body.name,
            actor_role="admin",
            actor_id=auth.actor_id,
        )
        return {"ok": True, "npc": npc}

    @router.post("/api/npcs/{staff_id}/disable")
    def npc_disable(
        staff_id: int,
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        npc = disable_npc(
            db,
            settings,
            staff_id=staff_id,
            actor_role="admin",
            actor_id=auth.actor_id,
        )
        return {"ok": True, "npc": npc}

    @router.get("/api/dashboard")
    def dashboard_admin(
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        return dashboard_payload(db)

    @router.get("/api/stream")
    def stream_admin(
        request: Request,
        auth: Annotated[AuthContext, Depends(require_admin)],
    ):
        return build_stream_response(request)

    @router.get("/api/members")
    def members_admin(
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
        q: str = "",
        group_id: int | None = None,
        status: str = "all",
        limit: int = 200,
    ):
        stmt = (
            select(Member)
            .options(joinedload(Member.group))
            .order_by(Member.group_id, Member.status, Member.id)
            .limit(clamp_limit(limit, default=200, maximum=300))
        )
        if q.strip():
            needle = f"%{q.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(Member.name).like(needle),
                    func.lower(Member.qr_code).like(needle),
                    func.lower(Member.id).like(needle),
                )
            )
        if group_id:
            stmt = stmt.where(Member.group_id == group_id)
        if status in {"unregistered", "active", "eliminated"}:
            stmt = stmt.where(Member.status == status)
        members = list(db.scalars(stmt))
        return {
            "members": [
                {
                    **member_public(member, include_code=True),
                    "group_id": member.group_id,
                    "qr_code": member.qr_code,
                    "registered_at": iso_utc(member.registered_at),
                    "created_at": iso_utc(member.created_at),
                }
                for member in members
            ]
        }

    @router.get("/api/kills")
    def kills_admin(
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
        status: str = "active",
        scope: str = "current",
        limit: int = 100,
        offset: int = 0,
    ):
        rows = _kill_rows(
            db,
            status=status,
            scope=scope,
            limit=clamp_limit(limit, default=100, maximum=500),
            offset=max(0, offset),
        )
        return {"kills": rows}

    @router.post("/api/kills/manual")
    def manual_kill_admin(
        body: ManualKillBody,
        request: Request,
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        killer = get_member(db, body.killer_id)
        victim = get_member(db, body.victim_id)
        kill = manual_kill(
            db,
            killer=killer,
            victim=victim,
            actor_role="admin",
            actor_id="console",
            note=normalize_reason(body.note) or "技术台手动添加",
        )
        request.app.state.events.publish()
        db.refresh(victim)
        return {
            "ok": True,
            "kill": kill_to_dict(kill),
            "killer": member_public(killer),
            "victim": member_public(victim),
        }

    @router.post("/api/kills/{kill_id}/revoke")
    def revoke_kill_admin(
        kill_id: int,
        request: Request,
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        kill, revived = revoke_kill(
            db,
            kill_id=kill_id,
            actor_role="admin",
            actor_id="console",
            actor_label="技术台",
        )
        request.app.state.events.publish()
        return {"ok": True, "kill": kill_to_dict(kill), "revived": revived}

    @router.post("/api/members/{member_id}/revive")
    def revive_member_admin(
        member_id: str,
        request: Request,
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        member = revive_member(
            db,
            member_id=member_id,
            actor_role="admin",
            actor_id="console",
        )
        request.app.state.events.publish()
        return {"ok": True, "member": member_public(member)}

    @router.post("/api/members/{member_id}/name")
    def rename_member_admin(
        member_id: str,
        body: RenameBody,
        request: Request,
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        member = admin_set_member_name(
            db,
            member_id=member_id,
            name=body.name,
            actor_role="admin",
            actor_id="console",
        )
        request.app.state.events.publish()
        return {"ok": True, "member": member_public(member)}

    @router.get("/api/scores")
    def scores_admin(
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
        scope: str = "current",
        limit: int = 100,
    ):
        return {"adjustments": _score_rows(db, scope=scope, limit=clamp_limit(limit, 100, 500))}

    @router.post("/api/score")
    def add_score_admin(
        body: ScoreBody,
        request: Request,
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        adjustment = add_score(
            db,
            group_id=body.group_id,
            delta=body.delta,
            reason=body.reason,
            actor_role="admin",
            actor_id="console",
            allow_negative=True,
        )
        request.app.state.events.publish()
        group = db.get(Group, adjustment.group_id)
        return {"ok": True, "adjustment": adjustment_to_dict(adjustment, group)}

    @router.post("/api/score/{adjustment_id}/revoke")
    def revoke_score_admin(
        adjustment_id: int,
        request: Request,
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        adjustment = revoke_score(
            db,
            adjustment_id=adjustment_id,
            actor_role="admin",
            actor_id="console",
            actor_label="技术台",
        )
        request.app.state.events.publish()
        group = db.get(Group, adjustment.group_id)
        return {"ok": True, "adjustment": adjustment_to_dict(adjustment, group)}

    @router.get("/api/audit")
    def audit_admin(
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
        action: str = "",
        scope: str = "all",
        limit: int = 150,
    ):
        return {"logs": _audit_rows(db, scope=scope, action=action, limit=clamp_limit(limit, 150, 500))}

    @router.get("/api/export")
    def export_admin(
        request: Request,
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
        type: str = Query(default="kills"),
        scope: str = Query(default="all"),
    ):
        result = _export_csv(db, export_type=type, scope=scope)
        write_audit(
            db,
            action="export_created",
            actor_role="admin",
            actor_id="console",
            round_id=None,
            target_type="export",
            target_id=type,
            payload={"type": type, "scope": scope, "rows": result["rows"]},
        )
        db.commit()
        filename = f"activity-{type}-{datetime.now(LOCAL_TZ).strftime('%Y%m%d-%H%M%S')}.csv"
        return Response(
            content=result["content"],
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    @router.post("/api/reset")
    def reset_admin(
        body: ResetBody,
        request: Request,
        auth: Annotated[AuthContext, Depends(require_admin)],
        db: Annotated[Session, Depends(get_db_session)],
    ):
        if body.confirm.strip().upper() != "RESET":
            raise bad_request("confirm_required", "请输入 RESET 以确认重置")
        result = reset_activity(
            db,
            mode=body.mode,
            actor_role="admin",
            actor_id="console",
        )
        request.app.state.events.publish()
        return {"ok": True, **result}

    return router


def _kill_rows(
    db: Session,
    *,
    status: str,
    scope: str,
    limit: int,
    offset: int,
) -> list[dict]:
    killer = aliased(Member)
    victim = aliased(Member)
    kg = aliased(Group)
    vg = aliased(Group)
    stmt = (
        select(
            Kill,
            Round.name.label("round_name"),
            killer.name.label("killer_name"),
            kg.name.label("killer_group"),
            kg.color.label("killer_color"),
            victim.name.label("victim_name"),
            vg.name.label("victim_group"),
            vg.color.label("victim_color"),
        )
        .join(Round, Round.id == Kill.round_id)
        .join(killer, killer.id == Kill.killer_id)
        .join(victim, victim.id == Kill.victim_id)
        .join(kg, kg.id == killer.group_id)
        .join(vg, vg.id == victim.group_id)
    )
    if status in {"active", "revoked"}:
        stmt = stmt.where(Kill.status == status)
    if scope == "current":
        round_ = require_active_round(db)
        stmt = stmt.where(Kill.round_id == round_.id)
    stmt = stmt.order_by(Kill.id.desc()).limit(limit).offset(offset)
    return [
        {
            **kill_to_dict(row[0], round_name=row.round_name),
            "killer_name": row.killer_name,
            "killer_group": row.killer_group,
            "killer_color": row.killer_color,
            "victim_name": row.victim_name,
            "victim_group": row.victim_group,
            "victim_color": row.victim_color,
        }
        for row in db.execute(stmt).all()
    ]


def _score_rows(db: Session, *, scope: str, limit: int) -> list[dict]:
    stmt = (
        select(ScoreAdjustment, Group)
        .join(Group, Group.id == ScoreAdjustment.group_id)
    )
    if scope == "current":
        round_ = require_active_round(db)
        stmt = stmt.where(ScoreAdjustment.round_id == round_.id)
    stmt = stmt.order_by(ScoreAdjustment.id.desc()).limit(limit)
    return [adjustment_to_dict(row, group) for row, group in db.execute(stmt).all()]


def _audit_rows(
    db: Session,
    *,
    scope: str,
    action: str,
    limit: int,
) -> list[dict]:
    stmt = select(AuditLog)
    if scope == "current":
        round_ = require_active_round(db)
        stmt = stmt.where(AuditLog.round_id == round_.id)
    if action.strip():
        stmt = stmt.where(AuditLog.action == action.strip())
    stmt = stmt.order_by(AuditLog.id.desc()).limit(limit)
    rows = list(db.scalars(stmt))
    return [
        {
            "id": row.id,
            "round_id": row.round_id,
            "actor_role": row.actor_role,
            "actor_id": row.actor_id,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "payload": json_loads(row.payload_json),
            "created_at": iso_utc(row.created_at),
        }
        for row in rows
    ]


def _export_csv(db: Session, *, export_type: str, scope: str) -> dict:
    if export_type not in {"kills", "scores", "audit", "summary"}:
        raise bad_request("invalid_export_type", "导出类型必须是 kills/scores/audit/summary")
    output = io.StringIO(newline="")
    writer = csv.writer(output)

    if export_type == "summary":
        data = dashboard_payload(db)
        writer.writerow(
            ["轮次", "组别", "组色", "总人数", "存活", "淘汰", "未注册", "击杀", "被击杀", "积分"]
        )
        for group in data["groups"]:
            writer.writerow(
                [
                    data["round"]["name"],
                    group["name"],
                    group["color"],
                    group["total"],
                    group["alive"],
                    group["eliminated"],
                    group["unregistered"],
                    group["kills"],
                    group["deaths"],
                    group["score"],
                ]
            )
        rows = len(data["groups"])
    elif export_type == "kills":
        if scope == "current":
            require_active_round(db)
        raw = _kill_rows(
            db,
            status="all",
            scope=scope,
            limit=2000,
            offset=0,
        )
        writer.writerow(
            [
                "记录ID",
                "轮次",
                "时间",
                "击杀者ID",
                "击杀者姓名",
                "击杀者组",
                "被击杀者ID",
                "被击杀者姓名",
                "被击杀者组",
                "类型",
                "来源",
                "状态",
                "撤销时间",
                "撤销人",
                "备注",
            ]
        )
        for row in raw:
            writer.writerow(
                [
                    row["id"],
                    csv_safe(row["round_name"]),
                    display_time(row["created_at"]),
                    row["killer_id"],
                    csv_safe(row["killer_name"]),
                    csv_safe(row["killer_group"]),
                    row["victim_id"],
                    csv_safe(row["victim_name"]),
                    csv_safe(row["victim_group"]),
                    "自爆" if row["kind"] == "self" else "正常击杀",
                    "技术台手动" if row["source"] == "manual" else "扫码",
                    "有效" if row["status"] == "active" else "已撤销",
                    display_time(row["revoked_at"]),
                    csv_safe(row["revoked_by"]),
                    csv_safe(row["note"]),
                ]
            )
        rows = len(raw)
    elif export_type == "scores":
        raw = _score_rows(db, scope=scope, limit=2000)
        writer.writerow(
            [
                "记录ID",
                "轮次ID",
                "组别",
                "组色",
                "分值",
                "原因",
                "操作者角色",
                "操作者ID",
                "状态",
                "时间",
                "撤销时间",
                "撤销人",
            ]
        )
        for row in raw:
            writer.writerow(
                [
                    row["id"],
                    row["round_id"],
                    csv_safe(row["group_name"]),
                    row["group_color"],
                    row["delta"],
                    csv_safe(row["reason"]),
                    row["operator_role"],
                    row["operator_id"],
                    "有效" if row["status"] == "active" else "已撤销",
                    display_time(row["created_at"]),
                    display_time(row["revoked_at"]),
                    csv_safe(row["revoked_by"]),
                ]
            )
        rows = len(raw)
    else:
        raw = _audit_rows(db, scope=scope, action="", limit=5000)
        writer.writerow(
            [
                "日志ID",
                "轮次ID",
                "时间",
                "操作者角色",
                "操作者ID",
                "动作",
                "对象类型",
                "对象ID",
                "详情JSON",
            ]
        )
        for row in raw:
            writer.writerow(
                [
                    row["id"],
                    row["round_id"],
                    display_time(row["created_at"]),
                    row["actor_role"],
                    row["actor_id"],
                    row["action"],
                    row["target_type"],
                    row["target_id"],
                    csv_safe(
                        json.dumps(row["payload"], ensure_ascii=False)
                        if row["payload"] is not None
                        else ""
                    ),
                ]
            )
        rows = len(raw)

    content = ("\ufeff" + output.getvalue()).encode("utf-8")
    return {"content": content, "rows": rows}
