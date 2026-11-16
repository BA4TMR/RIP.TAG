"""Console NPC creation and production-lab NPC tag metadata."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import AuditLog, StaffToken

DATA = Path(__file__).resolve().parent.parent / "data"


class NpcHarness:
    def __init__(self) -> None:
        self.db_path = DATA / f"_npc_{uuid.uuid4().hex}.db"
        self.settings = Settings(
            app_env="test",
            app_name="test",
            public_base_url="https://play.example.com",
            npc_base_url="https://npc.example.com",
            database_url=f"sqlite:///{self.db_path.as_posix()}",
            console_secret="npc-console-secret-123456",
            production_secret="npc-production-secret-123456",
            seed_csv="data/seed.csv",
            seed_on_startup=True,
            cookie_secure=False,
            allow_insecure_console=True,
            allow_insecure_production=True,
        )
        self.settings.validate()
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.console = self.settings.console_base

    def close(self) -> None:
        self.client.__exit__(None, None, None)
        self.app.state.engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.db_path) + suffix).unlink(missing_ok=True)


@pytest.fixture
def npc_env():
    harness = NpcHarness()
    try:
        yield harness
    finally:
        harness.close()


def test_console_creates_npc_with_placeholder_kill_code(npc_env: NpcHarness):
    page = npc_env.client.get(f"{npc_env.console}/")
    assert page.status_code == 200
    assert "NPC 管理" in page.text

    response = npc_env.client.post(
        f"{npc_env.console}/api/npcs",
        json={"name": "NPC-场务-A"},
    )
    assert response.status_code == 200, response.text
    npc = response.json()["npc"]
    assert npc["name"] == "NPC-场务-A"
    assert npc["qr_code"] == "XXXXXX"
    assert npc["active"] is True
    assert npc["url"] == f"https://npc.example.com/staff/{npc['token']}"

    with npc_env.app.state.session_factory() as db:
        staff = db.scalar(select(StaffToken).where(StaffToken.id == npc["id"]))
        assert staff is not None
        assert staff.qr_code == "XXXXXX"
        audit = db.scalar(
            select(AuditLog).where(AuditLog.action == "npc_created")
        )
        assert audit is not None

    listed = npc_env.client.get(f"{npc_env.console}/api/npcs").json()["npcs"]
    assert [item["id"] for item in listed] == [npc["id"]]

    # Production workstation can list the NPC for local NTAG writing.
    production = npc_env.client.get("/api/production/npcs")
    assert production.status_code == 200
    assert production.json()["npcs"][0]["qr_code"] == "XXXXXX"
    assert production.json()["npcs"][0]["url"].startswith("https://npc.example.com/staff/")

    npc_env.settings.allow_insecure_production = False
    assert npc_env.client.get("/api/production/npcs").status_code == 403
    assert (
        npc_env.client.get(
            "/api/production/npcs", headers={"X-Client-Verify": "SUCCESS"}
        ).status_code
        == 200
    )
    npc_env.settings.allow_insecure_production = True

    duplicate = npc_env.client.post(
        f"{npc_env.console}/api/npcs", json={"name": "NPC-场务-A"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "npc_name_exists"


def test_disable_npc_invalidates_token_and_hides_production_card(npc_env: NpcHarness):
    created = npc_env.client.post(
        f"{npc_env.console}/api/npcs", json={"name": "NPC-B"}
    ).json()["npc"]

    assert npc_env.client.get(f"/staff/{created['token']}").status_code == 200
    disabled = npc_env.client.post(
        f"{npc_env.console}/api/npcs/{created['id']}/disable"
    )
    assert disabled.status_code == 200
    assert disabled.json()["npc"]["active"] is False

    assert npc_env.client.get(f"/staff/{created['token']}").status_code == 404
    assert npc_env.client.get("/api/production/npcs").json()["npcs"] == []
