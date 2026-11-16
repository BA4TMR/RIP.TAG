"""Application configuration.

Production values are supplied with environment variables (see .env.example).
A tiny .env reader is included so local development works without extra tooling.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONSOLE_SECRET = "local-console-secret"
DEFAULT_PRODUCTION_SECRET = "local-production-secret"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs without overriding real environment variables."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是整数") from exc


def _default_database_url() -> str:
    return f"sqlite:///{(BASE_DIR / 'data' / 'activity.db').as_posix()}"


@dataclass(slots=True)
class Settings:
    app_env: str = "development"
    app_name: str = "撕名牌现场技术系统"
    public_base_url: str = "http://127.0.0.1:8000"
    npc_base_url: str = "http://127.0.0.1:8000"
    database_url: str = field(default_factory=_default_database_url)
    console_secret: str = DEFAULT_CONSOLE_SECRET
    production_secret: str = DEFAULT_PRODUCTION_SECRET
    seed_csv: str | None = "data/seed.csv"
    seed_on_startup: bool = False
    cookie_secure: bool = True
    cookie_name: str = "sm_session"
    session_ttl_hours: int = 168
    allow_insecure_console: bool = False
    allow_insecure_production: bool = False
    sse_heartbeat_seconds: int = 15
    kill_lookup_limit: int = 30
    kill_lookup_window_seconds: int = 60
    kill_failure_limit: int = 8
    kill_failure_window_seconds: int = 300

    def __post_init__(self) -> None:
        # Resolve a relative SQLite URL against the project directory, not the
        # process working directory. This keeps CLI tools and systemd/Uvicorn
        # pointed at the same database file.
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return
        raw = self.database_url.removeprefix(prefix)
        if raw == ":memory:" or raw.startswith("file:"):
            return
        path = Path(raw)
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
            self.database_url = prefix + path.as_posix()

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def console_base(self) -> str:
        return f"/console-{self.console_secret}"

    @property
    def production_base(self) -> str:
        return f"/production-{self.production_secret}"

    @property
    def sqlite_path(self) -> Path | None:
        if not self.database_url.startswith("sqlite:///"):
            return None
        raw = self.database_url.removeprefix("sqlite:///")
        if raw == ":memory:":
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
        return path

    def validate(self) -> None:
        secret_re = re.compile(r"[A-Za-z0-9_-]{8,64}")
        if not self.console_secret or not secret_re.fullmatch(self.console_secret):
            raise RuntimeError("CONSOLE_SECRET 只能包含字母、数字、下划线和短横线，长度 8-64")
        if not self.production_secret or not secret_re.fullmatch(self.production_secret):
            raise RuntimeError("PRODUCTION_SECRET 只能包含字母、数字、下划线和短横线，长度 8-64")
        if self.production_secret == self.console_secret:
            raise RuntimeError("PRODUCTION_SECRET 不能与 CONSOLE_SECRET 相同")
        parsed = urlsplit(self.public_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("PUBLIC_BASE_URL 必须是完整的 http(s) URL")
        npc = urlsplit(self.npc_base_url)
        if npc.scheme not in {"http", "https"} or not npc.netloc:
            raise RuntimeError("NPC_BASE_URL 必须是完整的 http(s) URL")

        if self.is_production:
            if self.console_secret == DEFAULT_CONSOLE_SECRET or len(self.console_secret) < 20:
                raise RuntimeError("生产环境必须设置至少 20 位的随机 CONSOLE_SECRET")
            if (
                self.production_secret == DEFAULT_PRODUCTION_SECRET
                or len(self.production_secret) < 20
            ):
                raise RuntimeError("生产环境必须设置至少 20 位的随机 PRODUCTION_SECRET")
            if not self.cookie_secure:
                raise RuntimeError("生产环境必须启用 COOKIE_SECURE=true")
            if self.allow_insecure_console:
                raise RuntimeError("生产环境禁止 ALLOW_INSECURE_CONSOLE=true")
            if self.allow_insecure_production:
                raise RuntimeError("生产环境禁止 ALLOW_INSECURE_PRODUCTION=true")
            if self.public_base_url.startswith("http://") and parsed.hostname not in LOOPBACK_HOSTS:
                raise RuntimeError("生产环境 PUBLIC_BASE_URL 必须使用 HTTPS")
            if self.npc_base_url.startswith("http://") and npc.hostname not in LOOPBACK_HOSTS:
                raise RuntimeError("生产环境 NPC_BASE_URL 必须使用 HTTPS")
            if npc.hostname == parsed.hostname:
                raise RuntimeError("生产环境 NPC_BASE_URL 必须使用独立子域")
        if self.session_ttl_hours < 1:
            raise RuntimeError("SESSION_TTL_HOURS 必须大于 0")
        if self.sqlite_path is not None:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            app_env=os.getenv("APP_ENV", "development"),
            app_name=os.getenv("APP_NAME", "撕名牌现场技术系统"),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000"),
            npc_base_url=os.getenv(
                "NPC_BASE_URL", os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
            ),
            database_url=os.getenv("DATABASE_URL", _default_database_url()),
            console_secret=os.getenv("CONSOLE_SECRET", DEFAULT_CONSOLE_SECRET),
            production_secret=os.getenv(
                "PRODUCTION_SECRET", DEFAULT_PRODUCTION_SECRET
            ),
            seed_csv=os.getenv("SEED_CSV", "data/seed.csv") or None,
            seed_on_startup=_env_bool("SEED_ON_STARTUP", False),
            cookie_secure=_env_bool("COOKIE_SECURE", True),
            cookie_name=os.getenv("SESSION_COOKIE_NAME", "sm_session"),
            session_ttl_hours=_env_int("SESSION_TTL_HOURS", 168),
            allow_insecure_console=_env_bool("ALLOW_INSECURE_CONSOLE", False),
            allow_insecure_production=_env_bool("ALLOW_INSECURE_PRODUCTION", False),
            sse_heartbeat_seconds=_env_int("SSE_HEARTBEAT_SECONDS", 15),
            kill_lookup_limit=_env_int("KILL_LOOKUP_LIMIT", 30),
            kill_lookup_window_seconds=_env_int("KILL_LOOKUP_WINDOW_SECONDS", 60),
            kill_failure_limit=_env_int("KILL_FAILURE_LIMIT", 8),
            kill_failure_window_seconds=_env_int("KILL_FAILURE_WINDOW_SECONDS", 300),
        )
        settings.validate()
        return settings
