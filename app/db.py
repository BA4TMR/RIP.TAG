"""Database engine, session management, and v1.0 -> v1.1 migration."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy import Integer, String, create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings


class Base(DeclarativeBase):
    pass


def build_engine(settings: Settings) -> Engine:
    connect_args: dict[str, object] = {}
    if settings.database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 15}

    engine = create_engine(
        settings.database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )

    if settings.database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=15000")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()

    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Columns added after v1.0. create_all() cannot alter an existing table.
_MEMBER_ADDED_COLUMNS: dict[str, object] = {
    "production_task_id": Integer(),
    "group_no": String(40),
    "group_color": String(7),
}
_OLD_MEMBER_PRODUCTION_COLUMNS = ("written_at", "printed_at", "done_at", "last_error")
_STAFF_ADDED_COLUMNS: dict[str, object] = {
    "qr_code": String(6),
}


def ensure_schema(engine: Engine) -> None:
    """Bring an existing v1.0 database to the v1.1 data model.

    v1.0 kept seed identity and production state on ``members``. v1.1 moves the
    physical-nameplate state to ``production_tasks`` and links a registered
    member to one task. Existing rows are copied once; the old columns are left
    in place because SQLite cannot drop them cheaply, but they are no longer
    read by the application.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "staff_tokens" in tables:
        staff_columns = {
            column["name"] for column in inspector.get_columns("staff_tokens")
        }
        if "qr_code" not in staff_columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE staff_tokens ADD COLUMN qr_code "
                        "VARCHAR(6) NOT NULL DEFAULT 'XXXXXX'"
                    )
                )
                connection.execute(
                    text("UPDATE staff_tokens SET qr_code = 'XXXXXX'")
                )

    if "members" not in tables:
        return

    member_columns = {column["name"] for column in inspector.get_columns("members")}
    missing = {
        name: column_type
        for name, column_type in _MEMBER_ADDED_COLUMNS.items()
        if name not in member_columns
    }
    if missing:
        with engine.begin() as connection:
            for name, column_type in missing.items():
                sql_type = column_type.compile(dialect=engine.dialect)
                connection.execute(
                    text(f'ALTER TABLE members ADD COLUMN "{name}" {sql_type}')
                )
        member_columns.update(missing)

    if "production_tasks" not in tables:
        return

    # Minimum columns needed to migrate a v1.0 member row into a task.
    required = {"id", "ntag_token", "qr_code"}
    if not required.issubset(member_columns):
        return

    task_columns = {
        column["name"] for column in inspect(engine).get_columns("production_tasks")
    }
    has_groups = "groups" in tables
    select_extra = []
    if "group_id" in member_columns and has_groups:
        select_extra += [
            "g.name AS group_name",
            "g.color AS group_color",
        ]
    else:
        select_extra += ["NULL AS group_name", "NULL AS group_color"]
    for old_name in _OLD_MEMBER_PRODUCTION_COLUMNS:
        if old_name in member_columns:
            select_extra.append(f"m.{old_name} AS {old_name}")
        else:
            select_extra.append(f"NULL AS {old_name}")

    join_groups = (
        "LEFT JOIN groups g ON g.id = m.group_id"
        if "group_id" in member_columns and has_groups
        else ""
    )
    query = text(
        "SELECT m.id, m.ntag_token, m.qr_code, m.production_task_id, "
        + ", ".join(select_extra)
        + f" FROM members m {join_groups} "
        "WHERE m.ntag_token IS NOT NULL AND m.qr_code IS NOT NULL"
    )

    with engine.begin() as connection:
        rows = list(connection.execute(query).mappings())
        for row in rows:
            group_no = (row.get("group_name") or "未分组").strip()
            group_color = row.get("group_color") or "#888888"
            task_id = row.get("production_task_id")
            if task_id is None:
                existing = connection.execute(
                    text(
                        "SELECT id FROM production_tasks WHERE ntag_token = :token"
                    ),
                    {"token": row["ntag_token"]},
                ).first()
                if existing:
                    task_id = existing[0]
                else:
                    result = connection.execute(
                        text(
                            "INSERT INTO production_tasks "
                            "(ntag_token, qr_code, group_no, group_color, "
                            " written_at, printed_at, done_at, last_error, created_at) "
                            "VALUES (:ntag_token, :qr_code, :group_no, :group_color, "
                            " :written_at, :printed_at, :done_at, :last_error, "
                            " CURRENT_TIMESTAMP)"
                        ),
                        {
                            "ntag_token": row["ntag_token"],
                            "qr_code": row["qr_code"],
                            "group_no": group_no,
                            "group_color": group_color,
                            "written_at": row.get("written_at"),
                            "printed_at": row.get("printed_at"),
                            "done_at": row.get("done_at"),
                            "last_error": row.get("last_error"),
                        },
                    )
                    task_id = result.lastrowid
            connection.execute(
                text(
                    "UPDATE members SET production_task_id = :task_id, "
                    "group_no = :group_no, group_color = :group_color "
                    "WHERE id = :member_id"
                ),
                {
                    "task_id": task_id,
                    "group_no": group_no,
                    "group_color": group_color,
                    "member_id": row["id"],
                },
            )

        # Add the unique index that a fresh table would have received from
        # SQLAlchemy. Old member rows are now one-to-one with tasks.
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_members_production_task_id "
                "ON members (production_task_id)"
            )
        )

    # Older deployments may have a stale task link; do not fail startup on a
    # partially migrated database. The application still requires non-null
    # links when creating a member.


def get_db_session(request: Request) -> Generator[Session, None, None]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    db = factory()
    try:
        yield db
    finally:
        db.close()
