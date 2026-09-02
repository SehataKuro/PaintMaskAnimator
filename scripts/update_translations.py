"""Regenerate the ``.ts`` catalogues and compile them to ``.qm``.

``--check`` is the CI mode: it regenerates into a temporary directory and fails
if the committed ``.ts`` files are out of date, so a newly wrapped ``tr()``
string cannot land without its catalogue entry.

Usage::

    python scripts/update_translations.py            # update *.ts and build *.qm
    python scripts/update_translations.py --check    # verify *.ts are current
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "paintmaskanimator"
TRANSLATIONS = PACKAGE / "translations"
# The source language is the message id itself, so it needs no catalogue.
TARGET_LANGUAGES = ["en"]

# lupdate stamps a build-specific header; ignore it when comparing.
_VOLATILE = re.compile(r'^\s*<location .*/>\s*$', re.M)


def _tool(name: str) -> str:
    found = shutil.which(name) or shutil.which(name.replace("pyside6-", "pyside6-") + ".exe")
    if not found:
        sys.exit(f"{name} not found; install PySide6 (it ships the Qt linguist tools).")
    return found


def _sources() -> list[str]:
    return sorted(str(p.relative_to(ROOT)) for p in PACKAGE.rglob("*.py"))


def _generate(into: Path) -> list[Path]:
    into.mkdir(parents=True, exist_ok=True)
    written = []
    for code in TARGET_LANGUAGES:
        target = into / f"paintmaskanimator_{code}.ts"
        existing = TRANSLATIONS / target.name
        if existing.exists() and existing.parent != into:
            # Seed from the committed file so human translations are preserved.
            shutil.copyfile(existing, target)
        subprocess.run(
            [_tool("pyside6-lupdate"), *_sources(), "-source-language", "ja",
             "-target-language", code, "-ts", str(target)],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        written.append(target)
    return written


def _comparable(path: Path) -> str:
    return _VOLATILE.sub("", path.read_text(encoding="utf-8"))


def check() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        for fresh in _generate(Path(tmp)):
            committed = TRANSLATIONS / fresh.name
            if not committed.exists():
                print(f"missing catalogue: {committed.relative_to(ROOT)}")
                return 1
            if _comparable(fresh) != _comparable(committed):
                print(
                    f"{committed.relative_to(ROOT)} is out of date.\n"
                    "Run: python scripts/update_translations.py"
                )
                return 1
    print("translation catalogues are up to date.")
    return 0


def update() -> int:
    for path in _generate(TRANSLATIONS):
        subprocess.run(
            [_tool("pyside6-lrelease"), str(path), "-qm", str(path.with_suffix(".qm"))],
            check=True, capture_output=True, text=True,
        )
        print(f"updated {path.relative_to(ROOT)} (+ .qm)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if .ts files are stale")
    args = parser.parse_args()
    return check() if args.check else update()


if __name__ == "__main__":
    raise SystemExit(main())
