"""Build a clean Ubuntu deployment archive.

Usage:
    python -m scripts.build_release

Output:
    dist/riptag-v1.1-ubuntu24.04.tar.gz
    dist/riptag-v1.1-ubuntu24.04.tar.gz.sha256

Excludes local secrets, virtualenvs, databases, test output and Windows-only
mTLS test material. The archive has a top-level ``riptag/`` directory.
"""

from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
NAME = "riptag-v1.1-ubuntu24.04"
ARCHIVE = DIST / f"{NAME}.tar.gz"
CHECKSUM = DIST / f"{NAME}.tar.gz.sha256"

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    ".local-certs",
    ".nginx",
    ".nginx-test",
    ".tools",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "output",
    "backups",
    "dist",
    "wheels",
}
EXCLUDED_FILES = {
    ".env",
    ".DS_Store",
    "pip-debug.log",
    "seed-group-no.csv",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_PREFIXES = ("data/_test_", "data/_production_", "data/_smoke", "data/activity.db")


def should_include(relative: Path) -> bool:
    parts = relative.parts
    if any(part in EXCLUDED_DIRS for part in parts):
        return False
    if relative.name in EXCLUDED_FILES:
        return False
    if relative.suffix in EXCLUDED_SUFFIXES:
        return False
    text = relative.as_posix()
    if text.startswith(EXCLUDED_PREFIXES):
        return False
    if text.startswith("deploy/certs/riptag-certs/"):
        return False
    return True


def main() -> int:
    DIST.mkdir(exist_ok=True)
    if ARCHIVE.exists():
        ARCHIVE.unlink()
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if path.is_file() and should_include(relative):
            files.append(path)

    with tarfile.open(ARCHIVE, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for path in files:
            relative = path.relative_to(ROOT)
            arcname = Path("riptag") / relative
            info = archive.gettarinfo(str(path), arcname.as_posix())
            if path.suffix == ".sh":
                info.mode = 0o755
            with path.open("rb") as handle:
                archive.addfile(info, handle)

    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    CHECKSUM.write_text(f"{digest}  {ARCHIVE.name}\n", encoding="utf-8")
    print(f"built {ARCHIVE} ({ARCHIVE.stat().st_size / 1024 / 1024:.1f} MiB)")
    print(f"sha256 {digest}")
    print(f"files {len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
