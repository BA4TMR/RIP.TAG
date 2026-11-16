"""Generate a production-ready seed CSV and optional NTAG URL manifest.

Example:
    python -m scripts.generate_seed --per-group 10 --out data/seed.csv \
        --urls-out output/ntag_urls.csv --base-url https://tag.example.com
"""

from __future__ import annotations

import argparse
import csv
import secrets
import sys
from pathlib import Path

GROUPS = [
    ("红队", "#E53935"),
    ("蓝队", "#1E88E5"),
    ("黄队", "#FDD835"),
    ("绿队", "#43A047"),
    ("紫队", "#8E24AA"),
    ("橙队", "#FB8C00"),
]
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def make_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def make_token() -> str:
    return secrets.token_urlsafe(24)


def write_seed(
    *,
    per_group: int,
    out_path: Path,
    urls_path: Path | None,
    base_url: str,
    force: bool,
) -> tuple[int, int]:
    if per_group < 1 or per_group > 10:
        raise ValueError("每组人数必须在 1-10 之间")
    if out_path.exists() and not force:
        raise FileExistsError(f"{out_path} 已存在；如需覆盖请加 --force")
    if urls_path and urls_path.exists() and not force:
        raise FileExistsError(f"{urls_path} 已存在；如需覆盖请加 --force")

    tokens: set[str] = set()
    codes: set[str] = set()
    rows: list[dict[str, str]] = []
    for group_name, color in GROUPS:
        for _ in range(per_group):
            token = make_token()
            while token in tokens:
                token = make_token()
            code = make_code()
            while code in codes:
                code = make_code()
            tokens.add(token)
            codes.add(code)
            rows.append(
                {
                    "ntag_token": token,
                    "qr_code": code,
                    "group_no": group_name,
                    "group_color": color,
                }
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["ntag_token", "qr_code", "group_no", "group_color"],
        )
        writer.writeheader()
        writer.writerows(rows)

    if urls_path:
        urls_path.parent.mkdir(parents=True, exist_ok=True)
        base = base_url.rstrip("/")
        with urls_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["group_no", "qr_code", "ntag_token", "ntag_url"])
            for row in rows:
                writer.writerow(
                    [
                        row["group_no"],
                        row["qr_code"],
                        row["ntag_token"],
                        f"{base}/r/{row['ntag_token']}",
                    ]
                )

    return len(rows), len(GROUPS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成撕名牌活动种子 CSV")
    parser.add_argument("--per-group", type=int, default=10, help="每组人数 1-10，默认 10")
    parser.add_argument("--out", default="data/seed.csv", help="种子 CSV 输出路径")
    parser.add_argument("--urls-out", default="output/ntag_urls.csv", help="NTAG URL 清单输出路径")
    parser.add_argument("--base-url", default="https://tag.example.com", help="用于 NTAG 写卡的公网地址")
    parser.add_argument("--no-urls", action="store_true", help="不生成 NTAG URL 清单")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    args = parser.parse_args(argv)

    try:
        count, groups = write_seed(
            per_group=args.per_group,
            out_path=Path(args.out),
            urls_path=None if args.no_urls else Path(args.urls_out),
            base_url=args.base_url,
            force=args.force,
        )
    except (ValueError, FileExistsError) as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        return 1

    print(f"已生成 {count} 名队员 / {groups} 个组 -> {args.out}")
    if not args.no_urls:
        print(f"NTAG 写卡清单 -> {args.urls_out}")
    print("下一步：确认后运行 python -m app.seed --csv " + args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
