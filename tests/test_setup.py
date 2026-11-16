"""First-run setup wizard and generated production queue."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models import AuditLog, Group, ProductionTask

DATA = Path(__file__).resolve().parent.parent / "data"


class SetupHarness:
    def __init__(self) -> None:
        self.db_path = DATA / f"_setup_{uuid.uuid4().hex}.db"
        self.settings = Settings(
            app_env="test",
            app_name="test",
            public_base_url="https://play.example.com",
            database_url=f"sqlite:///{self.db_path.as_posix()}",
            console_secret="setup-console-secret-123456",
            production_secret="setup-production-secret-123456",
            seed_csv="data/seed.csv",
            seed_on_startup=False,
            cookie_secure=False,
            allow_insecure_console=True,
            allow_insecure_production=True,
        )
        self.settings.validate()
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.console = self.settings.console_base
        self.production = self.settings.production_base

    def close(self) -> None:
        self.client.__exit__(None, None, None)
        self.app.state.engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.db_path) + suffix).unlink(missing_ok=True)


@pytest.fixture
def setup_env():
    harness = SetupHarness()
    try:
        yield harness
    finally:
        harness.close()


def test_empty_database_shows_console_setup(setup_env: SetupHarness):
    assert setup_env.db_path.exists()
    page = setup_env.client.get(f"{setup_env.console}/")
    assert page.status_code == 200
    assert "首次启用" in page.text
    assert "生成活动数据" in page.text

    production = setup_env.client.get(f"{setup_env.production}/")
    assert production.status_code == 200
    assert "技术台尚未初始化" in production.text


def test_setup_creates_groups_tasks_and_production_page(setup_env: SetupHarness):
    response = setup_env.client.post(
        f"{setup_env.console}/api/setup",
        json={"group_count": 3, "per_group": 4},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["group_count"] == 3
    assert payload["per_group"] == 4
    assert payload["task_count"] == 12
    assert [group["name"] for group in payload["groups"]] == ["红队", "蓝队", "黄队"]
    assert payload["groups"][0]["color"] == "#E53935"

    with setup_env.app.state.session_factory() as db:
        assert db.scalar(select(func.count(Group.id))) == 3
        assert db.scalar(select(func.count(ProductionTask.id))) == 12
        tasks = list(db.scalars(select(ProductionTask)))
        assert len({task.ntag_token for task in tasks}) == 12
        assert len({task.qr_code for task in tasks}) == 12
        assert {task.group_no for task in tasks} == {"红队", "蓝队", "黄队"}
        assert all(task.written_at is None for task in tasks)
        audit = db.scalar(
            select(AuditLog).where(AuditLog.action == "activity_initialized")
        )
        assert audit is not None
        assert audit.payload_json is not None

    console = setup_env.client.get(f"{setup_env.console}/")
    assert console.status_code == 200
    assert "首次启用" not in console.text

    production = setup_env.client.get(f"{setup_env.production}/")
    assert production.status_code == 200
    assert "制作队列" in production.text

    tasks = setup_env.client.get("/api/production/tasks").json()
    assert len(tasks) == 12
    summary = setup_env.client.get("/api/production/status").json()
    assert summary == {
        "total": 12,
        "written": 0,
        "printed": 0,
        "done": 0,
        "failed": 0,
        "pending": 12,
        "in_progress": 0,
        "all_done": False,
    }

    task_id = tasks[0]["id"]
    qr = setup_env.client.get(
        f"{setup_env.production}/api/tasks/{task_id}/qr.svg"
    )
    assert qr.status_code == 200
    assert qr.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in qr.text

    duplicate = setup_env.client.post(
        f"{setup_env.console}/api/setup",
        json={"group_count": 2, "per_group": 2},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "already_initialized"


@pytest.mark.parametrize(
    "group_count,per_group",
    [(0, 10), (7, 10), (6, 0), (6, 11)],
)
def test_setup_rejects_invalid_counts(
    setup_env: SetupHarness, group_count: int, per_group: int
):
    response = setup_env.client.post(
        f"{setup_env.console}/api/setup",
        json={"group_count": group_count, "per_group": per_group},
    )
    assert response.status_code == 422


def test_setup_endpoints_require_mtls(setup_env: SetupHarness):
    setup_env.settings.allow_insecure_console = False
    assert setup_env.client.get(f"{setup_env.console}/").status_code == 403
    assert (
        setup_env.client.post(
            f"{setup_env.console}/api/setup",
            json={"group_count": 1, "per_group": 1},
        ).status_code
        == 403
    )

    setup_env.settings.allow_insecure_production = False
    assert setup_env.client.get(f"{setup_env.production}/").status_code == 403
    allowed = setup_env.client.get(
        f"{setup_env.production}/", headers={"X-Client-Verify": "SUCCESS"}
    )
    assert allowed.status_code == 200

