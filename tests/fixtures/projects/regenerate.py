"""Explicit offline update only: python tests/fixtures/projects/regenerate.py --update."""
import argparse

# Imports follow the explicit repository path setup.
# ruff: noqa: E402
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[3])]

from PySide6.QtWidgets import QApplication
from pytest import MonkeyPatch

from p5_6_support import CASES, ROOT, capture, create_v1
from test_p5_6_characterization import window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", required=True)
    parser.add_argument("--expectations-only", action="store_true")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    for case in CASES:
        with tempfile.TemporaryDirectory() as folder, MonkeyPatch.context() as patch:
            temp = Path(folder)
            fixture = window.__wrapped__(app, patch, temp)
            w = next(fixture)
            try:
                path = ROOT / "v1" / f"{case}.pman"
                if not args.expectations_only:
                    create_v1(case, path)
                result = capture(w, path, temp)
                (ROOT / "expected" / f"{case}.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(case, flush=True)
            finally:
                next(fixture, None)


if __name__ == "__main__":
    main()
