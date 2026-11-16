"""Consistent SQLite online backup.

Usage:
    python -m scripts.backup
    python -m scripts.backup --database data/activity.db --out backups --keep 20

Uses sqlite3's backup API, which is safe while Uvicorn is running and includes
the WAL contents. The resulting database is verified with PRAGMA integrity_check.
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import BASE_DIR, Settings


def backup(database: Path, output_dir: Path, keep: int) -> Path:
    if not database.is_file():
        raise FileNotFoundError(f"数据库不存在: {database}")
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    temporary = output_dir / f"activity-{stamp}.db"
    final = output_dir / f"activity-{stamp}.db.gz"

    source = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
            check = target.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                raise RuntimeError(f"备份完整性检查失败: {check}")
        finally:
            target.close()
    finally:
        source.close()

    with temporary.open("rb") as raw, gzip.open(final, "wb", compresslevel=6) as compressed:
        shutil.copyfileobj(raw, compressed)
    temporary.unlink(missing_ok=True)

    backups = sorted(output_dir.glob("activity-*.db.gz"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        old.unlink(missing_ok=True)
    return final


def main(argv: list[str] | None = None) -> int:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="SQLite 在线备份（含 WAL，自动完整性检查）")
    parser.add_argument(
        "--database",
        default=str(settings.sqlite_path or (BASE_DIR / "data" / "activity.db")),
        help="SQLite 数据库路径",
    )
    parser.add_argument("--out", default=str(BASE_DIR / "backups"), help="备份输出目录")
    parser.add_argument("--keep", type=int, default=20, help="保留最近多少个压缩备份")
    args = parser.parse_args(argv)

    database = Path(args.database)
    if not database.is_absolute():
        database = BASE_DIR / database
    try:
        final = backup(database, Path(args.out), max(1, args.keep))
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"备份失败: {exc}", file=sys.stderr)
        return 1
    print(f"备份完成 -> {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
