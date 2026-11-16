"""Render printable QR label sheets for every production task (A4, 10/page).

The QR content is exactly the task's six-character kill code, intended for the
back of the name tag. NTAG URLs are never printed here.
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import BASE_DIR, Settings
from app.db import Base, build_engine, build_session_factory, ensure_schema
from app.models import ProductionTask
from app.qrcode_utils import qr_svg_markup


def render(rows: list[ProductionTask], title: str) -> str:
    cards = []
    for index, task in enumerate(rows, start=1):
        svg = qr_svg_markup(task.qr_code, box_size=10)
        cards.append(
            f"""
            <article class="card" style="--group-color:{html.escape(task.group_color)}">
              <div class="card-top">
                <span class="group"><i></i>{html.escape(task.group_no)}</span>
                <span class="serial">RIP TAG · {index:02d}</span>
              </div>
              <div class="card-body">
                <div class="qr">{svg}</div>
                <div class="meta">
                  <div class="code-label">击杀码</div>
                  <div class="code">{html.escape(task.qr_code)}</div>
                  <div class="name-label">姓 名</div>
                  <div class="name-line"></div>
                  <div class="tiny">贴于名牌背面 · 撕下后扫码</div>
                </div>
              </div>
              <div class="tear" aria-hidden="true"></div>
            </article>
            """
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  @page {{ size: A4; margin: 9mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: #fff;
    color: #111;
    font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  .sheet-head {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin: 0 0 5mm;
    padding-bottom: 2.5mm;
    border-bottom: 1px solid #ddd;
  }}
  .sheet-head h1 {{ margin: 0; font-size: 13pt; letter-spacing: .02em; }}
  .sheet-head span {{ color: #777; font-size: 7pt; letter-spacing: .10em; }}
  .sheet {{
    display: grid;
    grid-template-columns: repeat(2, 92mm);
    gap: 4mm;
  }}
  .card {{
    position: relative;
    height: 50mm;
    padding: 3.5mm 4mm 3mm;
    border: 1px dashed #c9c9c9;
    border-radius: 2.5mm;
    background:
      linear-gradient(135deg, color-mix(in srgb, var(--group-color) 8%, #fff), #fff 45%);
    overflow: hidden;
    break-inside: avoid;
  }}
  .card::before {{
    content: "";
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 2mm;
    background: var(--group-color);
  }}
  .card-top {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: 0 0 2mm 2mm;
  }}
  .group {{
    display: inline-flex;
    align-items: center;
    gap: 1.4mm;
    font-size: 8.5pt;
    font-weight: 800;
  }}
  .group i {{
    width: 2.4mm; height: 2.4mm; border-radius: 50%;
    background: var(--group-color);
  }}
  .serial {{ color: #aaa; font-family: monospace; font-size: 5.8pt; letter-spacing: .10em; }}
  .card-body {{
    display: flex;
    gap: 4mm;
    height: 38mm;
    margin-left: 2mm;
  }}
  .qr {{
    width: 35mm; height: 35mm; flex: none;
    padding: 1.2mm;
    border: 1px solid #e4e4e4;
    border-radius: 1.5mm;
    background: #fff;
  }}
  .qr svg {{ width: 100%; height: 100%; display: block; }}
  .meta {{ min-width: 0; flex: 1; padding-top: .5mm; }}
  .code-label, .name-label {{
    color: #999;
    font-size: 5.6pt;
    letter-spacing: .22em;
    line-height: 1;
  }}
  .code {{
    margin: 1mm 0 2.5mm;
    font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 18pt;
    font-weight: 800;
    letter-spacing: .12em;
    line-height: 1;
  }}
  .name-line {{
    height: 5.2mm;
    margin: .8mm 0 1.5mm;
    border-bottom: 1px solid #bbb;
  }}
  .tiny {{ color: #aaa; font-size: 5.2pt; white-space: nowrap; }}
  .tear {{
    position: absolute;
    right: 5mm; bottom: 1.2mm;
    color: #ccc;
    font-size: 5pt;
    letter-spacing: .15em;
  }}
  .tear::before {{ content: "✂ - - - - - - - - - - - - - -"; }}
  @media print {{
    .sheet-head {{ margin-bottom: 4mm; }}
    .card {{ box-shadow: none; }}
  }}
</style>
</head>
<body>
  <header class="sheet-head">
    <h1>{html.escape(title)}</h1>
    <span>PRINT ON A4 · CUT ALONG DASHED LINE · {len(rows)} CARDS</span>
  </header>
  <main class="sheet">
    {''.join(cards)}
  </main>
</body>
</html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 A4 击杀码标签打印页")
    parser.add_argument("--out", default="output/name_tags.html")
    parser.add_argument("--title", default="撕名牌活动 · 击杀码标签")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    ensure_schema(engine)
    factory = build_session_factory(engine)
    with factory() as db:
        rows = list(
            db.scalars(
                select(ProductionTask).order_by(
                    ProductionTask.group_no, ProductionTask.id
                )
            )
        )
    if not rows:
        print("数据库中没有制作任务，请先导入种子数据", file=sys.stderr)
        return 1

    output = Path(args.out)
    if not output.is_absolute():
        output = BASE_DIR / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(rows, args.title), encoding="utf-8")
    pages = (len(rows) + 9) // 10
    print(f"已生成 {len(rows)} 张标签（约 {pages} 页）-> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
