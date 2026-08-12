import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import main_window_export  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_xdts_export_handles_missing_key_reference(qapp, tmp_path, monkeypatch):
    destination = tmp_path / "sheet.xdts"
    monkeypatch.setattr(
        main_window_export.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(destination), "XDTS"),
    )
    monkeypatch.setattr(
        main_window_export.QMessageBox,
        "information",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        main_window_export.TimelineWidget,
        "timeline_span_at",
        staticmethod(lambda *_args: ("content", None, 1)),
    )

    window = MainWindow()
    try:
        window.export_xdts_dialog()
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()

    text = destination.read_text(encoding="utf-8")
    payload = json.loads(text.split("\n", 1)[1])
    value = payload["timeTables"][0]["fields"][0]["tracks"][0]["frames"][0][
        "data"
    ][0]["values"][0]
    assert value == "SYMBOL_NULL_CELL"
