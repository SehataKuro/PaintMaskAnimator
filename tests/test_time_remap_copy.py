"""タイムラインからタイムリマップをコピーする（Issue #23）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.errors import OperationError  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    window.canvas._ensure_frame_count(6)
    try:
        yield window
    finally:
        window.deleteLater()


def lay_out(window, plan):
    """``plan`` は列ごとの絵番号（None は空セル）。"""
    for column, number in enumerate(plan):
        layer = window.canvas.frames[column].layers[0]
        layer.exposure = 1
        if number is None:
            layer.has_content = False
            layer.is_blank_key = False
            layer.sequence_number = None
        else:
            layer.has_content = True
            layer.is_blank_key = False
            layer.sequence_number = int(number)


def test_the_sheet_reads_back_as_drawing_numbers(window):
    lay_out(window, [1, 2, None, 3, 3, None])

    assert window.time_remap.timeline_states() == [1, 2, None, 3, 3, None]


def test_an_exposure_repeats_its_number_for_every_frame(window):
    lay_out(window, [1, None, None, None, None, None])
    window.canvas.frames[0].layers[0].exposure = 3

    assert window.time_remap.timeline_states()[:3] == [1, 1, 1]


def test_the_text_is_after_effects_keyframe_data(window):
    lay_out(window, [1, 2, None, None, None, None])

    text = window.time_remap.after_effects_text(fps=24)

    assert text.startswith("Adobe After Effects 8.0 Keyframe Data")
    assert "Units Per Second\t24" in text
    assert "Time Remap" in text
    assert "End of Keyframe Data" in text
    # 絵番号1は0秒、2は1/24秒。
    assert "\t1\t0.000000" in text
    assert "\t2\t0.041667" in text


def test_blank_cells_become_opacity_zero(window):
    lay_out(window, [1, None, 2, None, None, None])

    text = window.time_remap.after_effects_text(fps=24)
    opacity = text.split("Transform\tOpacity")[1]

    assert "\t2\t0" in opacity
    assert "\t1\t100" in opacity


def test_a_sheet_without_numbers_is_refused(window):
    lay_out(window, [None] * 6)

    with pytest.raises(OperationError):
        window.time_remap.after_effects_text(fps=24)

    assert not window.time_remap.copy_from_timeline()


def test_copying_puts_the_text_on_the_clipboard(window, qapp):
    lay_out(window, [1, 2, 3, None, None, None])

    assert window.time_remap.copy_from_timeline()

    assert "Time Remap" in qapp.clipboard().text()


def test_what_we_copy_can_be_pasted_back(window):
    """往復しても同じ並びになる。"""
    lay_out(window, [1, 2, None, 3, 3, None])
    text = window.time_remap.after_effects_text(fps=24)

    parsed = window.time_remap.parse_text(text)

    assert parsed["states"] == [1, 2, None, 3, 3, None]


def test_the_action_is_offered_in_the_shortcut_list(window):
    actions = [action for _, action in window.timeline_actions]
    assert window.a_tl_copy_time_remap in actions
