import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage  # noqa: E402
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
        window.export.xdts_dialog()
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


def _painted_window(qapp):
    window = MainWindow()
    canvas = window.canvas
    cell = canvas.frames[0].layers[0]
    cell.image.fill(QColor(12, 34, 56, 255))
    cell.has_content = True
    return window


def test_clipboard_button_writes_the_canvas_image(qapp):
    window = _painted_window(qapp)
    try:
        clipboard = QApplication.clipboard()
        assert clipboard is not None
        clipboard.clear()
        window.copy_canvas_button.click()
        copied = clipboard.image()
        assert not copied.isNull()
        assert copied.size() == window.crop_image(0).size()
        assert copied.pixelColor(0, 0) == QColor(12, 34, 56, 255)
    finally:
        window.close()


def test_camera_button_saves_with_a_timestamp_name(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        main_window_export.preferences, "snapshot_folder", lambda: str(tmp_path)
    )
    window = _painted_window(qapp)
    try:
        # 保存先が決まっていれば、ボタン1回で尋ねずに1枚たまる。
        monkeypatch.setattr(
            main_window_export.QFileDialog,
            "getExistingDirectory",
            lambda *_a, **_k: pytest.fail("保存先が決まっているのに尋ねた"),
        )
        window.save_canvas_button.click()
        saved = list(tmp_path.glob("*.png"))
        assert len(saved) == 1
        stem = saved[0].stem
        assert len(stem) == 14 and stem.isdecimal()
        assert QImage(str(saved[0])).pixelColor(0, 0) == QColor(12, 34, 56, 255)
    finally:
        window.close()


def test_camera_button_asks_once_when_no_folder_is_set(qapp, tmp_path, monkeypatch):
    stored = [""]
    monkeypatch.setattr(
        main_window_export.preferences, "snapshot_folder", lambda: stored[0]
    )
    monkeypatch.setattr(
        main_window_export.preferences,
        "set_snapshot_folder",
        lambda path: stored.__setitem__(0, str(path)),
    )
    asked = []

    def _choose(*_args, **_kwargs):
        asked.append(True)
        return str(tmp_path)

    monkeypatch.setattr(
        main_window_export.QFileDialog, "getExistingDirectory", _choose
    )
    window = _painted_window(qapp)
    try:
        assert window.export.save_canvas_snapshot()
        assert asked == [True]
        assert stored == [str(tmp_path)]
        assert len(list(tmp_path.glob("*.png"))) == 1
        # 2回目は覚えた保存先へ、尋ねずに書き出す。
        assert window.export.save_canvas_snapshot()
        assert asked == [True]
        # 同じ秒でも前の1枚を上書きしない。
        assert len(list(tmp_path.glob("*.png"))) == 2
    finally:
        window.close()
