"""色置換セットの保存・読み込み（Issue #42）。"""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import replacement_sets  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def test_a_set_round_trips_through_a_file(tmp_path):
    mapping = {(255, 0, 0): (0, 0, 255), (12, 34, 56): (200, 100, 0)}

    path = replacement_sets.save(tmp_path / "scene", mapping)

    assert path.suffix == replacement_sets.SUFFIX
    assert replacement_sets.load(path) == mapping


def test_unreadable_entries_are_skipped_not_fatal():
    data = {
        "replacements": [
            {"from": "#ff0000", "to": "#0000ff"},
            {"from": "red", "to": "#0000ff"},
            {"to": "#0000ff"},
            "nonsense",
            {"from": "#123456", "to": "#123456"},
        ]
    }

    assert replacement_sets.from_data(data) == {(255, 0, 0): (0, 0, 255)}


def test_a_file_that_is_not_a_replacement_set_is_rejected(tmp_path):
    path = tmp_path / "other.pmarep"
    path.write_text(json.dumps({"tiles": []}), encoding="utf-8")

    with pytest.raises(ValueError):
        replacement_sets.load(path)


def test_loading_registers_the_colours_the_image_uses(window, tmp_path):
    layer = window.canvas.frames[0].layers[0]
    for x in range(10, 14):
        layer.image.setPixelColor(x, 10, QColor(255, 0, 0))
    layer.has_content = True
    window.used_color.refresh()

    path = replacement_sets.save(
        tmp_path / "scene",
        {(255, 0, 0): (0, 0, 255), (7, 7, 7): (1, 1, 1)},
    )

    assert window.used_color.load_replacement_set(str(path))

    # 絵にある色だけが登録される。
    assert window.palette.current_replacements() == {(255, 0, 0): (0, 0, 255)}


def test_saving_what_the_panel_holds(window, tmp_path, monkeypatch):
    layer = window.canvas.frames[0].layers[0]
    for x in range(10, 14):
        layer.image.setPixelColor(x, 10, QColor(0, 128, 0))
    layer.has_content = True
    window.used_color.refresh()
    window.palette.register_replacements({(0, 128, 0): (9, 9, 9)})

    target = tmp_path / "out.pmarep"
    monkeypatch.setattr(
        "paintmaskanimator.main_window_used_color.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(target), ""),
    )

    assert window.used_color.save_replacement_set()
    assert replacement_sets.load(target) == {(0, 128, 0): (9, 9, 9)}
