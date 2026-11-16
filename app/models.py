"""SQLAlchemy models for the activity system (contract v1.1)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    """Return naive UTC, which is stored consistently by SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_member_id() -> str:
    return uuid.uuid4().hex


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#888888")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    members: Mapped[list[Member]] = relationship(back_populates="group")


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (
        CheckConstraint("status IN ('active','ended')", name="ck_rounds_status"),
        Index("ix_rounds_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProductionTask(Base):
    """Pre-bound seed row plus nameplate production state.

    This table is deliberately independent of the activity/member lifecycle.
    Production status never affects registration, kills, dashboard or rounds.
    """

    __tablename__ = "production_tasks"
    __table_args__ = (
        CheckConstraint("length(qr_code) = 6", name="ck_production_tasks_qr_len"),
        Index("ix_production_tasks_group", "group_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ntag_token: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    qr_code: Mapped[str] = mapped_column(String(6), nullable=False, unique=True, index=True)
    group_no: Mapped[str] = mapped_column(String(40), nullable=False)
    group_color: Mapped[str] = mapped_column(String(7), nullable=False, default="#888888")
    written_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    printed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    member: Mapped[Member | None] = relationship(back_populates="production_task", uselist=False)


class Member(Base):
    """A registered player. Seed rows live in production_tasks until signup."""

    __tablename__ = "members"
    __table_args__ = (
        CheckConstraint(
            "status IN ('unregistered','active','eliminated')",
            name="ck_members_status",
        ),
        CheckConstraint("length(qr_code) = 6", name="ck_members_qr_len"),
        Index("ix_members_group_status", "group_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_member_id)
    production_task_id: Mapped[int] = mapped_column(
        ForeignKey("production_tasks.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    ntag_token: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    qr_code: Mapped[str] = mapped_column(String(6), nullable=False, unique=True, index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), nullable=False, index=True)
    group_no: Mapped[str] = mapped_column(String(40), nullable=False)
    group_color: Mapped[str] = mapped_column(String(7), nullable=False, default="#888888")
    name: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unregistered")
    registered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    group: Mapped[Group] = relationship(back_populates="members")
    production_task: Mapped[ProductionTask] = relationship(back_populates="member")


class UserSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("role IN ('member','npc','admin')", name="ck_sessions_role"),
        Index("ix_sessions_token_hash", "token_hash", unique=True),
        Index("ix_sessions_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    member_id: Mapped[str | None] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), nullable=True, index=True
    )
    staff_token_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_tokens.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class StaffToken(Base):
    __tablename__ = "staff_tokens"
    __table_args__ = (Index("ix_staff_tokens_token", "token", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(12), nullable=False, default="npc")
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    # NPCs are not kill targets. All NPC rows intentionally use the same
    # placeholder kill code so the production workstation can render them
    # consistently without ever generating a scannable player code.
    qr_code: Mapped[str] = mapped_column(
        String(6), nullable=False, default="XXXXXX", server_default="XXXXXX"
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Kill(Base):
    __tablename__ = "kills"
    __table_args__ = (
        CheckConstraint("kind IN ('normal','self')", name="ck_kills_kind"),
        CheckConstraint("source IN ('scan','manual')", name="ck_kills_source"),
        CheckConstraint("status IN ('active','revoked')", name="ck_kills_status"),
        Index("ix_kills_round_status", "round_id", "status"),
        Index("ix_kills_victim", "victim_id"),
        Index("ix_kills_killer", "killer_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), nullable=False, index=True)
    killer_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    victim_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="normal")
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="scan")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ScoreAdjustment(Base):
    __tablename__ = "score_adjustments"
    __table_args__ = (
        CheckConstraint("status IN ('active','revoked')", name="ck_scores_status"),
        Index("ix_scores_round_status", "round_id", "status"),
        Index("ix_scores_group", "group_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), nullable=False, index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    operator_role: Mapped[str] = mapped_column(String(12), nullable=False)
    operator_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(80), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_round_created", "round_id", "created_at"),
        Index("ix_audit_action", "action"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int | None] = mapped_column(ForeignKey("rounds.id"), nullable=True)
    actor_role: Mapped[str] = mapped_column(String(12), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
