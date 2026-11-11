"""Frozen v1 inputs and offline observations; never regenerate during pytest."""
import json
import os
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from paintmaskanimator import constants
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.utils import remove_app_event_filter
from p5_6_support import CASES, ROOT, capture


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 4)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 3)
    def fail_dialog(*args):
        raise AssertionError(str(args[-1]))

    monkeypatch.setattr(QMessageBox, "critical", fail_dialog)
    w = MainWindow()
    for timer in (w._autosave_timer, w._used_color_timer, w._title_timer):
        timer.stop()
    monkeypatch.setattr(w.used_color, "schedule_refresh", lambda: None)
    monkeypatch.setattr(w.used_color, "refresh_with_counter", lambda *_: None)
    yield w
    w._closing = True
    remove_app_event_filter(w)
    remove_app_event_filter(w.subview)
    w.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("case", CASES)
def test_v1_characterization(window, tmp_path, case):
    with ZipFile(ROOT / "v1" / f"{case}.pman") as archive:
        metadata = json.loads(archive.read("project.json"))
    assert metadata["format_version"] == 1
    assert metadata["mask_format"] == (1 if case == "unnumbered-mask1" else 2)
    expected = json.loads((ROOT / "expected" / f"{case}.json").read_text(encoding="utf-8"))
    assert capture(window, ROOT / "v1" / f"{case}.pman", tmp_path) == expected
