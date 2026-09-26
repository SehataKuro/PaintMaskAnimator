"""串刺し塗り（バケツの全コマ一括適用）の結合テスト。

串刺し塗りは「クリックした座標を種にして、選択レイヤーの全コマを塗る」操作。
既存の一括適用ランナー（``frame_scope``）に載せているので、Undo が1回にまとまる
ことと、種の色種別が違うコマを飛ばして線を塗り潰さないことをここで固定する。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.undo_entries import ScopedBatchUndo


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


def outlined_cell(window, frame_index, *, origin=(4, 4), size=8):
    """白地に閉じた黒枠を1つ描いたコマを作る。枠の内側が塗りの対象になる。"""
    layer = window.canvas.frames[frame_index].layers[0]
    layer.image.fill(QColor("white"))
    x0, y0 = origin
    x1, y1 = x0 + size, y0 + size
    black = QColor("black")
    for x in range(x0, x1):
        layer.image.setPixelColor(x, y0, black)
        layer.image.setPixelColor(x, y1 - 1, black)
    for y in range(y0, y1):
        layer.image.setPixelColor(x0, y, black)
        layer.image.setPixelColor(x1 - 1, y, black)
    layer.has_content = True
    return layer


def prepare(window, frame_count, **kwargs):
    window.canvas._ensure_frame_count(frame_count)
    for frame_index in range(frame_count):
        outlined_cell(window, frame_index, **kwargs)
    window.canvas.set_tool("bucket")
    window.canvas.color_mode = "main"
    window.canvas.main_color = QColor("#3355ff")
    window.tools.bucket_all_frames.setChecked(True)


def test_fills_every_frame_and_undoes_once(window):
    prepare(window, 3)
    before = [frame.layers[0].image.copy() for frame in window.canvas.frames]

    window.canvas.flood_fill(QPoint(8, 8))

    for frame in window.canvas.frames:
        assert frame.layers[0].image.pixelColor(8, 8) == QColor("#3355ff")
        assert frame.layers[0].image.pixelColor(4, 4) == QColor("black"), (
            "線は塗り潰さない"
        )

    entry = window.canvas.undo_stack[-1]
    assert isinstance(entry, ScopedBatchUndo)
    assert len(entry.cells) == 3

    window.canvas.undo()
    for frame, original in zip(window.canvas.frames, before):
        assert frame.layers[0].image == original


def test_unchecked_checkbox_touches_only_the_current_cell(window):
    prepare(window, 2)
    window.tools.bucket_all_frames.setChecked(False)

    window.canvas.flood_fill(QPoint(8, 8))

    assert window.canvas.frames[0].layers[0].image.pixelColor(8, 8) == QColor(
        "#3355ff"
    )
    assert window.canvas.frames[1].layers[0].image.pixelColor(8, 8) == QColor(
        "white"
    )


def test_frames_whose_seed_sits_on_a_line_are_skipped(window):
    prepare(window, 2)
    # 2コマ目だけ枠をずらし、種の座標がちょうど線の上に来るようにする。
    outlined_cell(window, 1, origin=(8, 4))

    window.canvas.flood_fill(QPoint(8, 8))

    assert window.canvas.frames[0].layers[0].image.pixelColor(8, 8) == QColor(
        "#3355ff"
    )
    assert window.canvas.frames[1].layers[0].image.pixelColor(8, 8) == QColor(
        "black"
    ), "線の上に落ちたコマは塗らずに飛ばす"

    entry = window.canvas.undo_stack[-1]
    assert isinstance(entry, ScopedBatchUndo)
    assert len(entry.cells) == 1


def test_frames_already_holding_another_colour_are_skipped(window):
    prepare(window, 2)
    # 2コマ目は種の位置が既に別の色。基準（白＝背景）と一致しないので対象外。
    window.canvas.frames[1].layers[0].image.setPixelColor(8, 8, QColor("#ff0000"))

    window.canvas.flood_fill(QPoint(8, 8))

    assert window.canvas.frames[1].layers[0].image.pixelColor(8, 8) == QColor(
        "#ff0000"
    )


def test_white_fill_erases_across_frames(window):
    prepare(window, 2)
    window.canvas.flood_fill(QPoint(8, 8))
    window.canvas.main_color = QColor("white")

    window.canvas.flood_fill(QPoint(8, 8))

    for frame in window.canvas.frames:
        assert frame.layers[0].image.pixelColor(8, 8).alpha() == 0, (
            "白バケツは串刺しでも消しゴムとして働く"
        )


def test_reports_when_no_frame_matches(window):
    prepare(window, 2)
    # 枠のない白紙にして「開いている領域は塗らない」を有効にすると、
    # どのコマも塗れる領域を持たない。
    for frame in window.canvas.frames:
        frame.layers[0].image.fill(QColor("white"))
    window.tools.bucket_require_closed.setChecked(True)
    depth = len(window.canvas.undo_stack)
    messages = []
    window.canvas.status_message.connect(messages.append)

    window.canvas.flood_fill(QPoint(8, 8))

    assert len(window.canvas.undo_stack) == depth, "何も変えないなら Undo も積まない"
    assert any("対象になるコマがありません" in message for message in messages)


def test_skipped_frames_are_reported_in_the_status_message(window):
    prepare(window, 2)
    outlined_cell(window, 1, origin=(8, 4))
    messages = []
    window.canvas.status_message.connect(messages.append)

    window.canvas.flood_fill(QPoint(8, 8))

    assert any(
        "1 コマを塗り" in message and "1 コマは対象外" in message
        for message in messages
    )


def test_fill_opacity_applies_to_every_frame(window):
    prepare(window, 2)
    window.canvas.fill_opacity = 0.5

    window.canvas.flood_fill(QPoint(8, 8))

    colors = [
        frame.layers[0].image.pixelColor(8, 8) for frame in window.canvas.frames
    ]
    assert colors[0] == colors[1], "どのコマも単発のバケツと同じ混色になる"
    assert colors[0] not in (QColor("#3355ff"), QColor("white"))
    assert colors[0].alpha() == 255
