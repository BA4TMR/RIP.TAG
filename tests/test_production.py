"""Contract tests for the v1.1 mTLS production API."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import AuditLog, Group, ProductionTask

DATA = Path(__file__).resolve().parent.parent / "data"


class ProductionHarness:
    def __init__(self) -> None:
        self.db_path = DATA / f"_production_{uuid.uuid4().hex}.db"
        self.settings = Settings(
            app_env="test",
            app_name="test",
            public_base_url="https://play.example.com",
            database_url=f"sqlite:///{self.db_path.as_posix()}",
            console_secret="test-console-secret-123456",
            seed_csv=None,
            seed_on_startup=False,
            cookie_secure=False,
            allow_insecure_console=True,
            allow_insecure_production=True,
        )
        self.settings.validate()
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        with self.app.state.session_factory() as db:
            red = Group(name="红队", color="#E53935")
            blue = Group(name="蓝队", color="#1E88E5")
            db.add_all([red, blue])
            db.flush()
            self.tasks = [
                ProductionTask(
                    ntag_token="A" * 24,
                    qr_code="ABC123",
                    group_no="红队",
                    group_color="#E53935",
                ),
                ProductionTask(
                    ntag_token="B" * 24,
                    qr_code="DEF456",
                    group_no="蓝队",
                    group_color="#1E88E5",
                ),
            ]
            db.add_all(self.tasks)
            db.commit()
            self.task_ids = [task.id for task in self.tasks]

    def close(self) -> None:
        self.client.__exit__(None, None, None)
        self.app.state.engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.db_path) + suffix).unlink(missing_ok=True)


@pytest.fixture
def production_env():
    harness = ProductionHarness()
    try:
        yield harness
    finally:
        harness.close()


def test_production_api_lifecycle_and_status(production_env: ProductionHarness):
    client = production_env.client
    first, second = production_env.task_ids

    tasks = client.get("/api/production/tasks")
    assert tasks.status_code == 200
    payload = tasks.json()
    assert isinstance(payload, list)
    assert len(payload) == 2
    assert set(payload[0]) >= {
        "id",
        "ntag_token",
        "qr_code",
        "group_no",
        "group_color",
        "written_at",
        "printed_at",
        "done_at",
        "last_error",
    }
    assert all(item["written_at"] is None for item in payload)

    written = client.post(
        f"/api/production/{first}/mark", json={"stage": "written"}
    )
    assert written.status_code == 200
    first_written = written.json()["task"]["written_at"]
    assert first_written is not None

    # Idempotent: the timestamp does not move and no duplicate task is created.
    again = client.post(
        f"/api/production/{first}/mark", json={"stage": "written"}
    )
    assert again.status_code == 200
    assert again.json()["task"]["written_at"] == first_written

    printed = client.post(
        f"/api/production/{first}/mark", json={"stage": "printed"}
    )
    assert printed.status_code == 200
    assert printed.json()["task"]["printed_at"] is not None
    done = client.post(f"/api/production/{first}/mark", json={"stage": "done"})
    assert done.status_code == 200
    assert done.json()["task"]["stage"] == "done"

    status = client.get("/api/production/status").json()
    assert status["total"] == 2
    assert status["done"] == 1
    assert status["written"] == 1
    assert status["printed"] == 1
    assert status["pending"] == 1
    assert status["all_done"] is False

    pending = client.get("/api/production/tasks?status=pending").json()
    assert [item["id"] for item in pending] == [second]
    in_progress = client.get("/api/production/tasks?status=in_progress").json()
    assert in_progress == []
    completed = client.get("/api/production/tasks?status=done").json()
    assert [item["id"] for item in completed] == [first]

    reset = client.post(f"/api/production/{first}/reset")
    assert reset.status_code == 200
    reset_task = reset.json()["task"]
    assert reset_task["written_at"] is None
    assert reset_task["stage"] == "pending"

    with production_env.app.state.session_factory() as db:
        actions = list(
            db.scalars(
                select(AuditLog.action).where(
                    AuditLog.action.in_(
                        ("production_written", "production_printed", "production_done", "production_reset")
                    )
                )
            )
        )
    assert {"production_written", "production_printed", "production_done", "production_reset"} <= set(actions)


def test_production_mark_order_errors_and_failure_recording(production_env: ProductionHarness):
    client = production_env.client
    _, second = production_env.task_ids

    too_early = client.post(
        f"/api/production/{second}/mark", json={"stage": "printed"}
    )
    assert too_early.status_code == 409
    assert too_early.json()["error"]["code"] == "production_write_required"

    done_early = client.post(
        f"/api/production/{second}/mark", json={"stage": "done"}
    )
    assert done_early.status_code == 409
    assert done_early.json()["error"]["code"] == "production_print_required"

    failed = client.post(
        f"/api/production/{second}/mark",
        json={"stage": "written", "error": "NFC timeout"},
    )
    assert failed.status_code == 200
    assert failed.json()["task"]["written_at"] is None
    assert failed.json()["task"]["last_error"] == "NFC timeout"
    summary = client.get("/api/production/status").json()
    assert summary["failed"] == 1

    recovered = client.post(
        f"/api/production/{second}/mark", json={"stage": "written"}
    )
    assert recovered.status_code == 200
    assert recovered.json()["task"]["written_at"] is not None
    assert recovered.json()["task"]["last_error"] is None

    missing = client.post("/api/production/999999/mark", json={"stage": "written"})
    assert missing.status_code == 404
    assert client.post(
        f"/api/production/{second}/mark", json={"stage": "bogus"}
    ).status_code == 422
    assert client.get("/api/production/tasks?status=bogus").status_code == 400
    assert client.post("/api/production/999999/reset").status_code == 404


def test_production_api_requires_mtls(production_env: ProductionHarness):
    client = production_env.client
    production_env.settings.allow_insecure_production = False

    denied = client.get("/api/production/tasks")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "mtls_required"

    allowed = client.get(
        "/api/production/tasks", headers={"X-Client-Verify": "SUCCESS"}
    )
    assert allowed.status_code == 200

    production_env.settings.allow_insecure_production = True


def test_production_page_and_qr_require_mtls(production_env: ProductionHarness):
    client = production_env.client
    base = production_env.settings.production_base

    page = client.get(f"{base}/")
    assert page.status_code == 200
    assert "制作队列" in page.text

    first = production_env.task_ids[0]
    qr = client.get(f"{base}/api/tasks/{first}/qr.svg")
    assert qr.status_code == 200
    assert qr.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in qr.text

    production_env.settings.allow_insecure_production = False
    denied = client.get(f"{base}/")
    assert denied.status_code == 403
    allowed = client.get(f"{base}/", headers={"X-Client-Verify": "SUCCESS"})
    assert allowed.status_code == 200
    production_env.settings.allow_insecure_production = True


def test_production_status_is_isolated_from_activity(production_env: ProductionHarness):
    client = production_env.client
    first = production_env.task_ids[0]
    before = client.get(
        f"/console-{production_env.settings.console_secret}/api/dashboard"
    ).json()
    assert before["totals"]["total_members"] == 2
    assert before["totals"]["registered"] == 0
    assert before["totals"]["alive"] == 0

    client.post(f"/api/production/{first}/mark", json={"stage": "written"})
    after_mark = client.get(
        f"/console-{production_env.settings.console_secret}/api/dashboard"
    ).json()
    assert after_mark["totals"] == before["totals"]

    register = client.post(
        "/api/register",
        json={"ntag_token": "A" * 24, "name": "正式队员"},
    )
    assert register.status_code == 200
    after_register = client.get(
        f"/console-{production_env.settings.console_secret}/api/dashboard"
    ).json()
    assert after_register["totals"]["registered"] == 1
    assert after_register["totals"]["alive"] == 1
    assert after_register["totals"]["total_members"] == 2

