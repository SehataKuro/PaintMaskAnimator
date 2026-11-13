"""Offline only: regenerate.py --update; expectations come from pre-save sources."""
# ruff: noqa: E402
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from zipfile import ZipFile, ZipInfo

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [str(Path(__file__).resolve().parents[3]), str(Path(__file__).resolve().parents[4])]

from PySide6.QtWidgets import QApplication
from pytest import MonkeyPatch
from paintmaskanimator import project_io
from p5_6_support import observe
from test_project_boundaries import window
from v2_golden_support import CASES, V2_ROOT, low_observation, source_expectation, source_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", required=True)
    parser.parse_args()
    app = QApplication.instance() or QApplication([])
    for case in CASES:
        with tempfile.TemporaryDirectory() as folder, MonkeyPatch.context() as patch:
            fixture = window.__wrapped__(Path(folder), patch)
            w = next(fixture)
            try:
                snapshot = source_snapshot(case)
                expected = source_expectation(w, snapshot, patch)
                path = Path(folder) / f"{case}.pman"
                project_io.write_project_snapshot(path, snapshot)
                loaded = project_io.load_project(path)
                assert low_observation(w, loaded.document) == expected["low"], case
                assert w.project.open(path)
                assert json.loads(json.dumps(observe(w))) == expected["ui"], case
                with ZipFile(path) as archive:
                    entries = [(info, archive.read(info)) for info in archive.infolist()]
                with ZipFile(V2_ROOT / path.name, "w") as archive:
                    for original, blob in entries:
                        info = ZipInfo(original.filename, (2000, 1, 1, 0, 0, 0))
                        info.compress_type = original.compress_type
                        archive.writestr(info, blob)
                (V2_ROOT / "expected" / f"{case}.json").write_text(
                    json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(case, flush=True)
            finally:
                next(fixture, None)
    assert app


if __name__ == "__main__":
    main()
