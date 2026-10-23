"""タイムラインで貼り付けるだけで、タイムシート（タイムリマップ）を反映する。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.models import make_frame  # noqa: E402
from paintmaskanimator.utils import blank_image  # noqa: E402

REVERSED = """Adobe After Effects 8.0 Keyframe Data

\tUnits Per Second\t24

Time Remap
\tFrame\tseconds
\t0\t0.0833333
\t1\t0.0416666
\t2\t0

End of Keyframe Data
"""
COLORS = ["#ff0000", "#00ff00", "#0000ff"]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def no_question(*args, **kwargs):
        raise AssertionError("タイムラインへの貼り付けでは確認を出さない")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(no_question))
    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    qapp.processEvents()
    canvas = window.canvas
    names = [layer.name for layer in canvas.frames[0].layers]
    canvas.frames = [make_frame(names) for _ in range(3)]
    for column, color in enumerate(COLORS):
        layer = canvas.frames[column].layers[0]
        image = blank_image()
        image.fill(QColor(color))
        layer.image = image
        layer.has_content = True
        layer.sequence_number = column + 1
    canvas.timeline_mode = "sheet"
    canvas.active_layer_index = 0
    window.refresh_ui()
    try:
        yield window
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def colors(window):
    return [
        window.canvas.frames[column].layers[0].image.pixelColor(5, 5).name()
        for column in range(3)
    ]


def test_paste_on_the_timeline_applies_the_remap_without_asking(window):
    QApplication.clipboard().setText(REVERSED)
    window.timeline.table.setFocus()
    window.a_paste.trigger()
    assert colors(window) == list(reversed(COLORS)), "そのまま逆順に並ぶ"

    window.canvas.undo()
    assert colors(window) == COLORS, "Undo 1回で戻る"


def test_non_timesheet_text_falls_back_to_the_normal_paste(window, monkeypatch):
    pasted = []
    monkeypatch.setattr(window.canvas, "paste_clipboard", lambda: pasted.append(True))
    QApplication.clipboard().setText("ただのメモ")
    window.timeline.table.setFocus()
    window.a_paste.trigger()
    assert pasted == [True]
    assert colors(window) == COLORS


def test_paste_away_from_the_timeline_is_the_normal_paste(window, monkeypatch):
    pasted = []
    monkeypatch.setattr(window.canvas, "paste_clipboard", lambda: pasted.append(True))
    monkeypatch.setattr(window, "_pointer_or_focus_on_timeline", lambda: False)
    QApplication.clipboard().setText(REVERSED)
    window.a_paste.trigger()
    assert pasted == [True]
    assert colors(window) == COLORS
