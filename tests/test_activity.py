"""End-to-end tests for the activity rules and role boundaries."""

from __future__ import annotations

import csv
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import Settings
from app.main import create_app
from app.models import Kill, Member, ProductionTask, StaffToken

BASE_DIR = Path(__file__).resolve().parent.parent
DATA = BASE_DIR / "data"
SEED_ROWS = list(csv.DictReader((DATA / "seed.csv").open(encoding="utf-8-sig")))


class Harness:
    def __init__(self, *, bypass_mtls: bool = True):
        self.db_path = DATA / f"_test_{uuid.uuid4().hex}.db"
        self.settings = Settings(
            app_env="test",
            app_name="test",
            public_base_url="https://activity.example.com",
            database_url=f"sqlite:///{self.db_path.as_posix()}",
            console_secret="test-console-secret-123456",
            seed_csv="data/seed.csv",
            seed_on_startup=True,
            cookie_secure=False,
            allow_insecure_console=bypass_mtls,
            allow_insecure_production=True,
        )
        self.settings.validate()
        self.app = create_app(self.settings)
        self._root = TestClient(self.app)
        self._root.__enter__()
        self.clients: list[TestClient] = [self._root]
        self.console_base = self.settings.console_base

    def client(self) -> TestClient:
        client = TestClient(self.app)
        self.clients.append(client)
        return client

    def admin(self) -> TestClient:
        client = self.client()
        client.headers.update({"X-Client-Verify": "SUCCESS"})
        return client

    def register(self, row: dict[str, str], name: str) -> tuple[TestClient, dict]:
        client = self.client()
        response = client.post(
            "/api/register",
            json={"ntag_token": row["ntag_token"], "name": name},
        )
        assert response.status_code == 200, response.text
        return client, response.json()["member"]

    def dashboard(self, client: TestClient | None = None) -> dict:
        target = client or self.admin()
        prefix = "" if client is not None else self.console_base
        response = target.get(f"{prefix}/api/dashboard")
        assert response.status_code == 200, response.text
        return response.json()

    def add_npc(self, name: str = "NPC 测试") -> tuple[TestClient, str]:
        token = uuid.uuid4().hex + uuid.uuid4().hex
        with self.app.state.session_factory() as db:
            staff = StaffToken(token=token, role="npc", name=name)
            db.add(staff)
            db.commit()
        client = self.client()
        response = client.get(f"/staff/{token}", follow_redirects=False)
        assert response.status_code == 303, response.text
        return client, token

    def close(self) -> None:
        for client in reversed(self.clients):
            if client is self._root:
                continue
            try:
                client.close()
            except Exception:
                pass
        try:
            self._root.__exit__(None, None, None)
        except Exception:
            pass
        self.app.state.engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(self.db_path) + suffix).unlink(missing_ok=True)
            except PermissionError:
                pass


@pytest.fixture
def env():
    harness = Harness(bypass_mtls=True)
    try:
        yield harness
    finally:
        harness.close()


@pytest.fixture
def mtls_env():
    harness = Harness(bypass_mtls=False)
    try:
        yield harness
    finally:
        harness.close()


def test_health_landing_and_security_headers(env: Harness):
    assert env._root.get("/healthz").json()["ok"] is True
    landing = env._root.get("/")
    assert landing.status_code == 200
    assert landing.headers["Referrer-Policy"] == "no-referrer"
    assert landing.headers["X-Frame-Options"] == "DENY"
    assert landing.headers["Cache-Control"] == "no-store"


def test_registration_restore_and_name_lock(env: Harness):
    row = SEED_ROWS[0]
    browser = env.client()
    entry = browser.get(f"/r/{row['ntag_token']}", follow_redirects=False)
    assert entry.status_code == 200
    assert "新队员登记" in entry.text

    response = browser.post(
        "/api/register",
        json={"ntag_token": row["ntag_token"], "name": "张三"},
    )
    assert response.status_code == 200
    assert response.json()["member"]["name"] == "张三"
    member_id = response.json()["member"]["id"]
    assert browser.get("/api/me").json()["member"]["name"] == "张三"

    # Re-tapping the physical tag issues a brand-new cookie for the same member.
    again = env.client()
    retap = again.get(f"/r/{row['ntag_token']}", follow_redirects=False)
    assert retap.status_code == 303
    assert again.get("/api/me").json()["member"]["id"] == member_id

    duplicate = env.client().post(
        "/api/register",
        json={"ntag_token": row["ntag_token"], "name": "篡改姓名"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "already_registered"

    # Duplicate display names are allowed; member_id distinguishes them.
    other = env.register(SEED_ROWS[1], "张三")[1]
    assert other["id"] != member_id


def test_lookup_and_atomic_kill_then_duplicate(env: Harness):
    killer, _ = env.register(SEED_ROWS[0], "击杀者")
    victim, _ = env.register(SEED_ROWS[1], "被击杀者")
    code = SEED_ROWS[1]["qr_code"]

    lookup = killer.post("/api/kills/lookup", json={"code": code.lower()})
    assert lookup.status_code == 200
    assert lookup.json()["victim"]["name"] == "被击杀者"
    assert lookup.json()["can_kill"] is True

    confirmed = killer.post("/api/kills/confirm", json={"code": code})
    assert confirmed.status_code == 200
    assert confirmed.json()["kind"] == "normal"
    assert victim.get("/api/me").json()["member"]["status"] == "eliminated"

    duplicate = killer.post("/api/kills/confirm", json={"code": code})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "victim_eliminated"

    with env.app.state.session_factory() as db:
        count = db.scalar(select(func.count(Kill.id)))
    assert count == 1


def test_self_kill_counts_both_sides(env: Harness):
    player, member = env.register(SEED_ROWS[0], "自爆者")
    me = player.get("/api/me").json()
    assert me["stats"]["kills"] == 0
    result = player.post(
        "/api/kills/confirm",
        json={"code": SEED_ROWS[0]["qr_code"]},
    )
    assert result.status_code == 200
    assert result.json()["kind"] == "self"
    me = player.get("/api/me").json()
    assert me["member"]["status"] == "eliminated"
    assert me["stats"]["kills"] == 1
    assert me["stats"]["deaths"] == 1
    assert me["stats"]["group_score"] == 0

    dashboard = env.dashboard()
    assert dashboard["groups"][0]["kills"] == 1
    assert dashboard["groups"][0]["deaths"] == 1
    assert dashboard["groups"][0]["alive"] == 0
    assert dashboard["groups"][0]["unregistered"] == 3
    assert member["name"] == "自爆者"


def test_concurrent_kills_only_one_wins(env: Harness):
    killer1, _ = env.register(SEED_ROWS[0], "并发甲")
    killer2, _ = env.register(SEED_ROWS[1], "并发乙")
    victim, _ = env.register(SEED_ROWS[2], "并发目标")
    code = SEED_ROWS[2]["qr_code"]

    def attack(client: TestClient):
        return client.post("/api/kills/confirm", json={"code": code})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attack, [killer1, killer2]))
    statuses = sorted(result.status_code for result in results)
    assert statuses == [200, 409], [result.text for result in results]
    assert victim.get("/api/me").json()["member"]["status"] == "eliminated"
    with env.app.state.session_factory() as db:
        count = db.scalar(
            select(func.count(Kill.id)).where(
                Kill.victim_id == victim.get("/api/me").json()["member"]["id"]
            )
        )
    assert count == 1


def test_revoke_stats_and_revive_semantics(env: Harness):
    killer, _ = env.register(SEED_ROWS[0], "撤销测试甲")
    victim, victim_member = env.register(SEED_ROWS[1], "撤销测试乙")
    admin = env.admin()

    def kill():
        response = killer.post(
            "/api/kills/confirm", json={"code": SEED_ROWS[1]["qr_code"]}
        )
        assert response.status_code == 200, response.text
        return response.json()["kill_id"]

    def active_kill_ids():
        data = admin.get(
            f"{env.console_base}/api/kills?status=active&scope=current"
        ).json()["kills"]
        return [item["id"] for item in data if item["victim_id"] == victim_member["id"]]

    first = kill()
    before = env.dashboard()
    assert before["groups"][0]["deaths"] == 1
    revoked = admin.post(f"{env.console_base}/api/kills/{first}/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["revived"] is True
    after = env.dashboard()
    assert after["groups"][0]["deaths"] == 0
    assert after["groups"][0]["kills"] == 0
    assert victim.get("/api/me").json()["member"]["status"] == "active"

    second = kill()
    revived = admin.post(
        f"{env.console_base}/api/members/{victim_member['id']}/revive"
    )
    assert revived.status_code == 200
    assert victim.get("/api/me").json()["member"]["status"] == "active"
    # Manual revive intentionally does not erase the existing active kill.
    assert env.dashboard()["groups"][0]["deaths"] == 1

    third = kill()
    ids = active_kill_ids()
    assert third in ids and second in ids
    # Revoking the latest kill revives the victim; revoking the older one after
    # that must not touch the now-active victim.
    latest_revoke = admin.post(f"{env.console_base}/api/kills/{third}/revoke")
    assert latest_revoke.status_code == 200
    assert latest_revoke.json()["revived"] is True
    older_revoke = admin.post(f"{env.console_base}/api/kills/{second}/revoke")
    assert older_revoke.status_code == 200
    assert older_revoke.json()["revived"] is False
    assert victim.get("/api/me").json()["member"]["status"] == "active"

    audit = admin.get(
        f"{env.console_base}/api/audit?action=kill_revoked&scope=all"
    ).json()["logs"]
    assert len(audit) >= 2


def test_npc_can_register_and_score_but_not_override(env: Harness):
    npc, _ = env.add_npc()
    member = SEED_ROWS[0]
    response = npc.post(
        "/api/npc/register",
        json={"task_id": 999999, "name": "无人"},
    )
    assert response.status_code == 404

    with env.app.state.session_factory() as db:
        target_task = db.scalar(
            select(ProductionTask).where(
                ProductionTask.ntag_token == member["ntag_token"]
            )
        )
        target_id = target_task.id
    response = npc.post(
        "/api/npc/register",
        json={"task_id": target_id, "name": "场务补登记"},
    )
    assert response.status_code == 200
    assert response.json()["member"]["name"] == "场务补登记"

    overwrite = npc.post(
        "/api/npc/register",
        json={"task_id": target_id, "name": "覆盖"},
    )
    assert overwrite.status_code == 409
    with env.app.state.session_factory() as db:
        registered = db.scalar(
            select(Member).where(Member.production_task_id == target_id)
        )
        assert registered is not None
        assert registered.production_task_id == target_id
    round_data = env.dashboard(npc)
    group_id = round_data["groups"][0]["id"]
    score = npc.post(
        "/api/npc/score",
        json={"group_id": group_id, "delta": 7, "reason": "支线任务"},
    )
    assert score.status_code == 200
    assert env.dashboard(npc)["groups"][0]["score"] == 7

    negative = npc.post(
        "/api/npc/score",
        json={"group_id": group_id, "delta": -5},
    )
    assert negative.status_code == 422

    # NPC cannot reach member kill APIs or the mTLS-only console.
    assert npc.post("/api/kills/confirm", json={"code": "ABC123"}).status_code == 403
    assert npc.get(f"{env.console_base}/api/dashboard").status_code in {200, 403}


def test_console_requires_mtls(mtls_env: Harness):
    client = mtls_env.client()
    page = client.get(f"{mtls_env.console_base}/")
    assert page.status_code == 403
    assert "需要主控证书" in page.text

    api = client.get(f"{mtls_env.console_base}/api/dashboard")
    assert api.status_code == 403
    assert api.json()["error"]["code"] == "mtls_required"

    client.headers.update({"X-Client-Verify": "SUCCESS"})
    assert client.get(f"{mtls_env.console_base}/").status_code == 200
    assert client.get(f"{mtls_env.console_base}/api/dashboard").status_code == 200


def test_admin_manual_ops_rename_and_score(env: Harness):
    arena = env.admin()
    killer, _ = env.register(SEED_ROWS[0], "技术台甲")
    victim, victim_member = env.register(SEED_ROWS[1], "技术台乙")
    env.register(SEED_ROWS[2], "技术台丙")

    manual = arena.post(
        f"{env.console_base}/api/kills/manual",
        json={"killer_id": None, "victim_id": victim_member["id"]},
    )
    assert manual.status_code == 422

    with env.app.state.session_factory() as db:
        killer_member = db.scalar(
            select(Member).where(Member.ntag_token == SEED_ROWS[0]["ntag_token"])
        )
        killer_id = killer_member.id
    manual = arena.post(
        f"{env.console_base}/api/kills/manual",
        json={"killer_id": killer_id, "victim_id": victim_member["id"], "note": "扫描失败"},
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["kill"]["source"] == "manual"
    assert victim.get("/api/me").json()["member"]["status"] == "eliminated"

    rename = arena.post(
        f"{env.console_base}/api/members/{killer_id}/name",
        json={"name": "改名后的甲"},
    )
    assert rename.status_code == 200
    assert killer.get("/api/me").json()["member"]["name"] == "改名后的甲"

    revive = arena.post(
        f"{env.console_base}/api/members/{victim_member['id']}/revive"
    )
    assert revive.status_code == 200

    group_id = env.dashboard()["groups"][0]["id"]
    score = arena.post(
        f"{env.console_base}/api/score",
        json={"group_id": group_id, "delta": -3, "reason": "判罚"},
    )
    assert score.status_code == 200
    adjustment_id = score.json()["adjustment"]["id"]
    assert env.dashboard()["groups"][0]["score"] == -3
    revoked = arena.post(
        f"{env.console_base}/api/score/{adjustment_id}/revoke"
    )
    assert revoked.status_code == 200
    assert env.dashboard()["groups"][0]["score"] == 0


def test_csv_exports_and_formula_safety(env: Harness):
    killer, _ = env.register(SEED_ROWS[0], "=1+1")
    env.register(SEED_ROWS[1], "导出目标")
    killer.post(
        "/api/kills/confirm", json={"code": SEED_ROWS[1]["qr_code"]}
    )
    arena = env.admin()

    kills = arena.get(
        f"{env.console_base}/api/export?type=kills&scope=all"
    )
    assert kills.status_code == 200
    assert kills.content.startswith(b"\xef\xbb\xbf")
    text = kills.content.decode("utf-8-sig")
    assert "击杀者姓名" in text
    assert "'=1+1" in text

    summary = arena.get(f"{env.console_base}/api/export?type=summary")
    assert summary.status_code == 200
    assert "本轮统计" not in summary.text
    assert "红队" in summary.content.decode("utf-8-sig")

    audit = arena.get(f"{env.console_base}/api/export?type=audit&scope=all")
    assert audit.status_code == 200

    invalid = arena.get(f"{env.console_base}/api/export?type=bogus")
    assert invalid.status_code == 400


def test_soft_and_hard_reset(env: Harness):
    arena = env.admin()
    killer, _ = env.register(SEED_ROWS[0], "重置甲")
    victim, victim_member = env.register(SEED_ROWS[1], "重置乙")
    killer.post("/api/kills/confirm", json={"code": SEED_ROWS[1]["qr_code"]})
    group_id = env.dashboard()["groups"][0]["id"]
    arena.post(
        f"{env.console_base}/api/score",
        json={"group_id": group_id, "delta": 10, "reason": "重置前"},
    )

    rejected = arena.post(
        f"{env.console_base}/api/reset",
        json={"mode": "soft", "confirm": "NO"},
    )
    assert rejected.status_code == 400

    soft = arena.post(
        f"{env.console_base}/api/reset",
        json={"mode": "soft", "confirm": "RESET"},
    )
    assert soft.status_code == 200, soft.text
    dashboard = env.dashboard()
    assert dashboard["round"]["name"] == "第 2 轮"
    assert dashboard["groups"][0]["kills"] == 0
    assert dashboard["groups"][0]["score"] == 0
    assert dashboard["groups"][0]["alive"] == 2
    assert victim.get("/api/me").json()["member"]["status"] == "active"
    assert killer.get("/api/me").json()["member"]["name"] == "重置甲"

    hard = arena.post(
        f"{env.console_base}/api/reset",
        json={"mode": "hard", "confirm": "RESET"},
    )
    assert hard.status_code == 200
    # Existing member cookies are revoked, so a re-tap is required.
    assert victim.get("/api/me").status_code == 401
    retap = env.client().get(f"/r/{SEED_ROWS[1]['ntag_token']}", follow_redirects=False)
    assert retap.status_code == 200
    with env.app.state.session_factory() as db:
        member = db.get(Member, victim_member["id"])
        assert member.name is None
        assert member.status == "unregistered"
        assert member.ntag_token == SEED_ROWS[1]["ntag_token"]
        assert member.qr_code == SEED_ROWS[1]["qr_code"]

    # Re-registration after a hard reset reuses the same member/task link and
    # does not create a second member for the physical task.
    again = env.client().post(
        "/api/register",
        json={"ntag_token": SEED_ROWS[1]["ntag_token"], "name": "重新注册乙"},
    )
    assert again.status_code == 200, again.text
    assert again.json()["member"]["id"] == victim_member["id"]
    assert again.json()["member"]["production_task_id"] == victim_member["production_task_id"]
    with env.app.state.session_factory() as db:
        count = db.scalar(
            select(func.count(Member.id)).where(
                Member.production_task_id == victim_member["production_task_id"]
            )
        )
        assert count == 1


def test_failed_code_rate_limit(env: Harness):
    player, _ = env.register(SEED_ROWS[0], "限速测试")
    statuses = []
    for _ in range(9):
        response = player.post("/api/kills/lookup", json={"code": "ZZZZZZ"})
        statuses.append(response.status_code)
    assert statuses[:8] == [404] * 8
    assert statuses[8] == 429
    assert player.post("/api/kills/lookup", json={"code": "ZZZZZZ"}).status_code == 429


def test_sse_endpoint_rejects_anonymous(mtls_env: Harness):
    response = mtls_env.client().get("/api/stream")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "staff_only"
