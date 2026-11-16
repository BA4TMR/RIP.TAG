"""Small shared utilities."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.errors import bad_request

DISPLAY_TZ = ZoneInfo("Asia/Shanghai")
CODE_RE = re.compile(r"^[A-Z0-9]{6}$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def display_time(value: datetime | str | None) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S")


def normalize_code(value: object) -> str:
    """Normalize a six-character elimination code.

    Uses NFKC so full-width letters/digits entered on a phone become ASCII,
    strips all whitespace, and uppercases. The alphabet remains A-Z0-9.
    """
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s+", "", text).upper()
    if not CODE_RE.fullmatch(text):
        raise bad_request("invalid_code", "击杀码应为 6 位数字或大写字母")
    return text


def normalize_name(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        raise bad_request("name_required", "请填写姓名")
    if len(text) > 30:
        raise bad_request("name_too_long", "姓名不能超过 30 个字符")
    if CONTROL_RE.search(text):
        raise bad_request("invalid_name", "姓名包含不可用字符")
    return text


def normalize_reason(value: object, *, max_len: int = 120) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return None
    if len(text) > max_len:
        raise bad_request("reason_too_long", f"说明不能超过 {max_len} 个字符")
    if CONTROL_RE.search(text):
        raise bad_request("invalid_reason", "说明包含不可用字符")
    return text


def normalize_color(value: object) -> str:
    return str(value or "").strip() if COLOR_RE.fullmatch(str(value or "").strip()) else "#888888"


def json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None) -> object:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def clamp_limit(value: int, default: int = 100, maximum: int = 500) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(number, maximum))


def csv_safe(value: object) -> object:
    """Prevent spreadsheet formula injection while preserving numeric values."""
    if not isinstance(value, str):
        return value
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value
