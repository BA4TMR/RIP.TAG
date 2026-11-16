"""Seed CSV import for contract v1.1.

Canonical columns: ntag_token,qr_code,group_no,group_color

Seed rows become ``production_tasks``. A ``Member`` is created only when a
player registers with the NTAG token; production state is never part of the
activity business tables.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import BASE_DIR, Settings
from app.db import Base, build_engine, build_session_factory, ensure_schema
from app.models import Group, ProductionTask
from app.utils import COLOR_RE, normalize_color

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
REQUIRED_HEADERS = {"ntag_token", "qr_code", "group_color"}


class SeedError(ValueError):
    pass


def resolve_seed_path(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    return candidate


def import_seed(db: Session, path: str | Path, *, max_members: int = 60) -> dict:
    seed_path = resolve_seed_path(path)
    if not seed_path.is_file():
        raise SeedError(f"种子文件不存在: {seed_path}")

    with seed_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SeedError("种子 CSV 缺少表头")
        headers = {name.strip() for name in reader.fieldnames}
        missing = REQUIRED_HEADERS - headers
        if missing:
            raise SeedError(f"种子 CSV 缺少字段: {', '.join(sorted(missing))}")
        if "group_no" not in headers and "group_name" not in headers:
            raise SeedError("种子 CSV 缺少字段: group_no")
        rows = list(reader)

    if not rows:
        raise SeedError("种子 CSV 没有数据行")
    if len(rows) > max_members:
        raise SeedError(f"种子数据超过 {max_members} 人上限")

    seen_tokens: set[str] = set()
    seen_codes: set[str] = set()
    group_colors: dict[str, str] = {}
    group_counts: dict[str, int] = {}
    prepared: list[dict[str, str]] = []

    for line_no, row in enumerate(rows, start=2):
        token = (row.get("ntag_token") or "").strip()
        code = (row.get("qr_code") or "").strip().upper()
        # group_name is accepted only as an import compatibility alias.
        group_no = (row.get("group_no") or row.get("group_name") or "").strip()
        color = (row.get("group_color") or "").strip()

        if not TOKEN_RE.fullmatch(token):
            raise SeedError(f"第 {line_no} 行 ntag_token 必须是 16-128 位 URL 安全随机字符")
        if token in seen_tokens:
            raise SeedError(f"第 {line_no} 行 ntag_token 重复")
        if not re.fullmatch(r"[A-Z0-9]{6}", code):
            raise SeedError(f"第 {line_no} 行 qr_code 必须是 6 位 A-Z0-9")
        if code in seen_codes:
            raise SeedError(f"第 {line_no} 行 qr_code 重复")
        if not group_no or len(group_no) > 40:
            raise SeedError(f"第 {line_no} 行 group_no 长度无效")
        if not COLOR_RE.fullmatch(color):
            raise SeedError(f"第 {line_no} 行 group_color 必须是 #RRGGBB")
        if group_no in group_colors and group_colors[group_no] != normalize_color(color):
            raise SeedError(f"第 {line_no} 行组「{group_no}」颜色与前面行不一致")
        seen_tokens.add(token)
        seen_codes.add(code)
        group_colors.setdefault(group_no, normalize_color(color))
        group_counts[group_no] = group_counts.get(group_no, 0) + 1
        if group_counts[group_no] > 10:
            raise SeedError(f"组「{group_no}」超过每组 10 人上限")
        prepared.append(
            {
                "ntag_token": token,
                "qr_code": code,
                "group_no": group_no,
                "group_color": normalize_color(color),
            }
        )

    if len(group_colors) > 6:
        raise SeedError("组别数量超过 6 个上限")

    existing_tasks = db.scalar(select(func.count(ProductionTask.id))) or 0
    if existing_tasks:
        raise SeedError("数据库中已有 production_tasks，种子导入仅用于首次初始化")

    groups: dict[str, Group] = {}
    for name, color in group_colors.items():
        group = db.scalar(select(Group).where(Group.name == name))
        if group is None:
            group = Group(name=name, color=color)
            db.add(group)
        groups[name] = group
    db.flush()

    for item in prepared:
        db.add(
            ProductionTask(
                ntag_token=item["ntag_token"],
                qr_code=item["qr_code"],
                group_no=item["group_no"],
                group_color=item["group_color"],
            )
        )
    db.commit()

    return {
        "tasks": len(prepared),
        "groups": len(groups),
        "group_counts": group_counts,
        "path": str(seed_path),
    }


def seed_if_empty(db: Session, settings: Settings) -> dict | None:
    if not settings.seed_on_startup or not settings.seed_csv:
        return None
    existing = db.scalar(select(func.count(ProductionTask.id))) or 0
    if existing:
        return None
    path = resolve_seed_path(settings.seed_csv)
    if not path.is_file():
        return None
    return import_seed(db, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入种子 CSV（仅首次初始化 production_tasks）")
    parser.add_argument("--csv", default="data/seed.csv", help="种子 CSV 路径")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    ensure_schema(engine)
    factory = build_session_factory(engine)
    with factory() as db:
        try:
            result = import_seed(db, args.csv)
        except SeedError as exc:
            print(f"导入失败: {exc}", file=sys.stderr)
            return 1
        print(
            f"导入成功：{result['tasks']} 条制作任务，{result['groups']} 个组 "
            f"-> {result['path']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
