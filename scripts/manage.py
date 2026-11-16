"""Small operational CLI.

Examples:
    python -m scripts.manage create-npc --name "NPC 场务 A"
    python -m scripts.manage list-npc
    python -m scripts.manage disable-npc --id 1
    python -m scripts.manage export-ntag --out output/ntag_urls.csv
    python -m scripts.manage show-stats
"""

from __future__ import annotations

import argparse
import csv
import secrets
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import BASE_DIR, Settings
from app.db import Base, build_engine, build_session_factory, ensure_schema
from app.models import ProductionTask, Round, StaffToken
from app.services.stats import dashboard_payload, ensure_active_round
from app.utils import iso_utc


def _settings_and_session():
    settings = Settings.from_env()
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    ensure_schema(engine)
    factory = build_session_factory(engine)
    return settings, factory


def create_npc(name: str) -> int:
    settings, factory = _settings_and_session()
    with factory() as db:
        ensure_active_round(db)
        token = secrets.token_urlsafe(32)
        staff = StaffToken(
            token=token,
            role="npc",
            name=name.strip(),
            qr_code="XXXXXX",
        )
        db.add(staff)
        db.commit()
        db.refresh(staff)
        base = settings.npc_base_url.rstrip("/")
        print(f"NPC 已创建: #{staff.id} {staff.name}")
        print(f"URL: {base}/staff/{staff.token}")
        print("提示：该 URL 包含登录凭证，请只写入对应的 NTAG 标签并妥善保管。")
    return 0


def list_npc() -> int:
    _settings, factory = _settings_and_session()
    with factory() as db:
        staff = list(db.scalars(select(StaffToken).order_by(StaffToken.id)))
        if not staff:
            print("暂无 NPC")
            return 0
        for item in staff:
            print(f"#{item.id}\t{item.name}\t{'启用' if item.active else '停用'}\t{item.token}")
    return 0


def disable_npc(staff_id: int) -> int:
    _settings, factory = _settings_and_session()
    with factory() as db:
        staff = db.get(StaffToken, staff_id)
        if staff is None:
            print(f"未找到 NPC #{staff_id}", file=sys.stderr)
            return 1
        staff.active = False
        db.commit()
        print(f"已停用 NPC #{staff.id} {staff.name}")
    return 0


def export_ntag(out_path: str, base_url: str | None) -> int:
    settings, factory = _settings_and_session()
    with factory() as db:
        tasks = list(
            db.scalars(
                select(ProductionTask).order_by(ProductionTask.group_no, ProductionTask.id)
            )
        )
        if not tasks:
            print("暂无制作任务", file=sys.stderr)
            return 1
        output = Path(out_path)
        if not output.is_absolute():
            output = BASE_DIR / output
        output.parent.mkdir(parents=True, exist_ok=True)
        base = (base_url or settings.public_base_url).rstrip("/")
        with output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["group_no", "qr_code", "ntag_token", "ntag_url"])
            for task in tasks:
                writer.writerow(
                    [
                        task.group_no,
                        task.qr_code,
                        task.ntag_token,
                        f"{base}/r/{task.ntag_token}",
                    ]
                )
        print(f"已导出 {len(tasks)} 条 NTAG 记录 -> {output}")
    return 0


def export_seed(out_path: str, force: bool = False) -> int:
    """Export live production_tasks back into a v1.1 seed CSV.

    Use this after an earlier local production run to carry the exact
    ntag_token -> qr_code -> group mapping to the deployment server.
    """
    _settings, factory = _settings_and_session()
    with factory() as db:
        tasks = list(
            db.scalars(
                select(ProductionTask).order_by(ProductionTask.group_no, ProductionTask.id)
            )
        )
        if not tasks:
            print("暂无 production_tasks", file=sys.stderr)
            return 1
        output = Path(out_path)
        if not output.is_absolute():
            output = BASE_DIR / output
        if output.exists() and not force:
            print(f"{output} 已存在；确认覆盖请加 --force", file=sys.stderr)
            return 1
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["ntag_token", "qr_code", "group_no", "group_color"])
            for task in tasks:
                writer.writerow(
                    [task.ntag_token, task.qr_code, task.group_no, task.group_color]
                )
        print(f"已导出 {len(tasks)} 条种子关联 -> {output}")
    return 0


def export_production(out_path: str) -> int:
    """Export task production state for archival, not for player import."""
    _settings, factory = _settings_and_session()
    with factory() as db:
        tasks = list(
            db.scalars(
                select(ProductionTask).order_by(ProductionTask.group_no, ProductionTask.id)
            )
        )
        if not tasks:
            print("暂无 production_tasks", file=sys.stderr)
            return 1
        output = Path(out_path)
        if not output.is_absolute():
            output = BASE_DIR / output
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "id",
                    "ntag_token",
                    "qr_code",
                    "group_no",
                    "group_color",
                    "written_at",
                    "printed_at",
                    "done_at",
                    "last_error",
                ]
            )
            for task in tasks:
                writer.writerow(
                    [
                        task.id,
                        task.ntag_token,
                        task.qr_code,
                        task.group_no,
                        task.group_color,
                        iso_utc(task.written_at),
                        iso_utc(task.printed_at),
                        iso_utc(task.done_at),
                        task.last_error or "",
                    ]
                )
        done = sum(1 for task in tasks if task.done_at is not None)
        print(f"已导出 {len(tasks)} 条制作状态（完成 {done}）-> {output}")
    return 0


def show_stats() -> int:
    _settings, factory = _settings_and_session()
    with factory() as db:
        ensure_active_round(db)
        data = dashboard_payload(db)
        round_ = db.get(Round, data["round"]["id"])
        print(f"轮次: {round_.name} (#{round_.id}, {round_.status})")
        print(
            f"{'组别':<8}{'总人数':>6}{'已注册':>7}{'存活':>6}{'淘汰':>6}"
            f"{'击杀':>6}{'被击杀':>8}{'积分':>6}"
        )
        for group in data["groups"]:
            print(
                f"{group['name']:<8}{group['total']:>6}{group['registered']:>7}"
                f"{group['alive']:>6}{group['eliminated']:>6}{group['kills']:>6}"
                f"{group['deaths']:>8}{group['score']:>6}"
            )
        totals = data["totals"]
        print(
            f"合计: {totals['total_members']} 人 / 已注册 {totals['registered']} / "
            f"存活 {totals['alive']} / 击杀 {totals['kills']} / 未注册 {totals['unregistered']}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="活动系统运维 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-npc", help="创建 NPC 场务令牌")
    create.add_argument("--name", required=True, help="NPC 名称，例如 NPC-A")

    sub.add_parser("list-npc", help="列出 NPC 令牌")

    disable = sub.add_parser("disable-npc", help="停用 NPC 令牌")
    disable.add_argument("--id", type=int, required=True)

    export = sub.add_parser("export-ntag", help="导出 NTAG 写卡 URL 清单")
    export.add_argument("--out", default="output/ntag_urls.csv")
    export.add_argument("--base-url", default=None)

    seed = sub.add_parser("export-seed", help="从 production_tasks 导出 v1.1 种子 CSV")
    seed.add_argument("--out", default="output/seed-export.csv")
    seed.add_argument("--force", action="store_true", help="覆盖已有输出文件")

    production = sub.add_parser("export-production", help="导出制作状态 CSV")
    production.add_argument("--out", default="output/production_status.csv")

    sub.add_parser("show-stats", help="在终端显示当前看板统计")

    args = parser.parse_args(argv)
    if args.command == "create-npc":
        return create_npc(args.name)
    if args.command == "list-npc":
        return list_npc()
    if args.command == "disable-npc":
        return disable_npc(args.id)
    if args.command == "export-ntag":
        return export_ntag(args.out, args.base_url)
    if args.command == "export-seed":
        return export_seed(args.out, args.force)
    if args.command == "export-production":
        return export_production(args.out)
    if args.command == "show-stats":
        return show_stats()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
