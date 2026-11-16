"""Schema tests for the v1.1 production_tasks migration."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401 - register declarative tables
from app.db import Base, ensure_schema


def test_fresh_schema_has_production_tasks_and_member_links() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    tables = set(inspect(engine).get_table_names())
    assert {"production_tasks", "members"} <= tables
    member_columns = {
        column["name"] for column in inspect(engine).get_columns("members")
    }
    assert {"production_task_id", "group_no", "group_color"} <= member_columns
    task_columns = {
        column["name"] for column in inspect(engine).get_columns("production_tasks")
    }
    assert {
        "ntag_token",
        "qr_code",
        "group_no",
        "group_color",
        "written_at",
        "printed_at",
        "done_at",
        "last_error",
    } <= task_columns

    staff_columns = {
        column["name"] for column in inspect(engine).get_columns("staff_tokens")
    }
    assert "qr_code" in staff_columns


def test_old_staff_tokens_get_placeholder_qr_code() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE staff_tokens ("
                "id INTEGER PRIMARY KEY, token VARCHAR(128) UNIQUE, role VARCHAR(12), "
                "name VARCHAR(40), active BOOLEAN, created_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO staff_tokens (token, role, name, active, created_at) "
                "VALUES ('old-npc-token', 'npc', '旧 NPC', 1, '2025-01-01 00:00:00')"
            )
        )

    Base.metadata.create_all(engine)
    ensure_schema(engine)

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT qr_code FROM staff_tokens WHERE token = 'old-npc-token'")
        ).one()
        assert row[0] == "XXXXXX"


def test_v10_members_migrate_to_tasks_once() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE groups ("
                "id INTEGER PRIMARY KEY, name VARCHAR(40), color VARCHAR(7), created_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE members ("
                "id VARCHAR(32) PRIMARY KEY, ntag_token VARCHAR(128), qr_code VARCHAR(6), "
                "group_id INTEGER, name VARCHAR(30), status VARCHAR(20), "
                "registered_at DATETIME, created_at DATETIME, written_at DATETIME, "
                "printed_at DATETIME, done_at DATETIME, last_error TEXT)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO groups (id, name, color) VALUES (1, '红队', '#E53935')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO members "
                "(id, ntag_token, qr_code, group_id, name, status, created_at, written_at) "
                "VALUES ('m1', :token1, 'ABC123', 1, '旧队员', 'active', "
                "'2025-01-01 00:00:00', '2025-01-02 00:00:00')"
            ),
            {"token1": "A" * 24},
        )
        connection.execute(
            text(
                "INSERT INTO members "
                "(id, ntag_token, qr_code, group_id, name, status, created_at) "
                "VALUES ('m2', :token2, 'DEF456', 1, NULL, 'unregistered', "
                "'2025-01-01 00:00:00')"
            ),
            {"token2": "B" * 24},
        )

    Base.metadata.create_all(engine)
    ensure_schema(engine)
    ensure_schema(engine)  # idempotent

    with engine.connect() as connection:
        tasks = connection.execute(
            text(
                "SELECT id, ntag_token, qr_code, group_no, group_color, written_at "
                "FROM production_tasks ORDER BY ntag_token"
            )
        ).mappings().all()
        assert len(tasks) == 2
        assert tasks[0]["group_no"] == "红队"
        assert tasks[0]["group_color"] == "#E53935"
        assert tasks[0]["written_at"] is not None

        members = connection.execute(
            text(
                "SELECT id, production_task_id, group_no, group_color "
                "FROM members ORDER BY id"
            )
        ).mappings().all()
        assert len(members) == 2
        assert all(row["production_task_id"] is not None for row in members)
        assert all(row["group_no"] == "红队" for row in members)
        assert all(row["group_color"] == "#E53935" for row in members)

    indexes = {index["name"] for index in inspect(engine).get_indexes("members")}
    assert "ux_members_production_task_id" in indexes
