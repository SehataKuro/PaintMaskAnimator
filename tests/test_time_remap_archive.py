"""タイムシートの反映：シートに並べていない絵（連番保管セル）も番号どおりに使う。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from paintmaskanimator.errors import OperationError  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.models import Layer, make_frame  # noqa: E402
from paintmaskanimator.utils import blank_image  # noqa: E402

COLORS = {1: "#ff0000", 2: "#00ff00", 3: "#0000ff", 4: "#ffaa00"}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def image_of(number):
    image = blank_image()
    image.fill(QColor(COLORS[number]))
    return image


def sheet_with_archived_four(window, placed=(1, 2, 3), archived=(4,)):
    """シートに placed を1コマずつ並べ、archived は連番保管セルにだけ置く。"""
    canvas = window.canvas
    names = [layer.name for layer in canvas.frames[0].layers]
    canvas.frames = [make_frame(names) for _ in range(len(placed))]
    for column, number in enumerate(placed):
        layer = canvas.frames[column].layers[0]
        layer.image = image_of(number)
        layer.has_content = True
        layer.sequence_number = number
    canvas._sequence_archive = {
        (0, number): Layer(names[0], image_of(number), has_content=True, sequence_number=number)
        for number in archived
    }
    canvas.timeline_mode = "sheet"
    canvas.current_frame = 0
    canvas.active_layer_index = 0
    window.refresh_ui()
    return canvas


def pixel(canvas, column):
    return canvas.frames[column].layers[0].image.pixelColor(5, 5).name()


def test_source_bank_includes_archived_cells(window):
    sheet_with_archived_four(window)
    bank = window.time_remap._time_remap_source_bank(0)
    assert len(bank) == 4
    assert [image.pixelColor(5, 5).name() for image in bank] == [
        COLORS[1], COLORS[2], COLORS[3], COLORS[4]
    ]


def test_applying_a_remap_uses_archived_numbers_and_keeps_unused_ones(window):
    canvas = sheet_with_archived_four(window)
    parsed = {"states": [1, 4, 2], "start_frame": 0, "end_frame": 2, "format": "test"}
    assert window.time_remap.apply_to_active_layer(parsed)
    assert [pixel(canvas, column) for column in range(3)] == [
        COLORS[1], COLORS[4], COLORS[2]
    ], "シートに無かった4番も正しい絵で並ぶ"
    assert (0, 3) in canvas._sequence_archive, "使わなかった3番は連番保管セルに残す"
    assert (0, 4) not in canvas._sequence_archive, "並べた4番は保管から外す"
    assert canvas._sequence_archive[(0, 3)].image.pixelColor(5, 5).name() == COLORS[3]


def test_a_real_gap_is_reported_instead_of_renumbering(window):
    # 1 と 3 だけがあり、2 はどこにも無い。
    sheet_with_archived_four(window, placed=(1, 3), archived=())
    bank = window.time_remap._time_remap_source_bank(0)
    assert bank[1] is None
    with pytest.raises(OperationError):
        window.time_remap.apply_to_active_layer(
            {"states": [2], "start_frame": 0, "end_frame": 0, "format": "test"}
        )
    assert window.time_remap.apply_to_active_layer(
        {"states": [3], "start_frame": 0, "end_frame": 0, "format": "test"}
    )
    assert pixel(window.canvas, 0) == COLORS[3], "3番は3番の絵のまま（左から詰めない）"
