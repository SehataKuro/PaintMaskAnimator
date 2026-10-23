"""串刺し塗り（バケツの全コマ一括適用）の結合テスト。

串刺し塗りは「クリックした座標を種にして、選択レイヤーの全コマを塗る」操作。
既存の一括適用ランナー（``frame_scope``）に載せているので、Undo が1回にまとまる
ことと、種の色種別が違うコマを飛ばして線を塗り潰さないことをここで固定する。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
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


def test_low_confidence_frame_waits_for_a_click_and_esc_skips_it(window):
    prepare(window, 2)
    # 2コマ目だけ枠をずっと大きくし、同じ図形とはみなせなくする（面積が3倍超）。
    outlined_cell(window, 1, origin=(8, 4), size=20)
    depth = len(window.canvas.undo_stack)
    messages = []
    window.canvas.status_message.connect(messages.append)

    window.canvas.flood_fill(QPoint(8, 8))

    assert window.canvas.skewer_fill_pending(), "自信のないコマで止まって指定を待つ"
    assert window.canvas.current_frame == 1, "待っているコマを表示する"
    assert len(window.canvas.undo_stack) == depth, "全コマ決まるまで塗らない"
    assert any("コマ 2" in message for message in messages)

    assert window.canvas.skip_skewer_fill_frame()

    assert not window.canvas.skewer_fill_pending()
    assert window.canvas.current_frame == 0, "塗り終えたら元のコマへ戻る"
    assert window.canvas.frames[0].layers[0].image.pixelColor(8, 8) == QColor(
        "#3355ff"
    )
    assert window.canvas.frames[1].layers[0].image.pixelColor(8, 8) == QColor(
        "black"
    ), "飛ばしたコマは触らない"
    entry = window.canvas.undo_stack[-1]
    assert isinstance(entry, ScopedBatchUndo)
    assert len(entry.cells) == 1
    assert any(
        "1 コマを塗り" in message and "1 コマは対象外" in message
        for message in messages
    )


def test_click_on_the_waiting_frame_fills_it_and_continues(window):
    prepare(window, 3)
    outlined_cell(window, 1, origin=(8, 4), size=20)
    outlined_cell(window, 2, origin=(8, 4), size=20)

    window.canvas.flood_fill(QPoint(8, 8))
    assert window.canvas.skewer_fill_pending()
    # 2コマ目の枠の内側をクリックして指定する。3コマ目はそこから追いかける。
    window.canvas.flood_fill(QPoint(11, 8))

    assert not window.canvas.skewer_fill_pending()
    assert window.canvas.frames[0].layers[0].image.pixelColor(8, 8) == QColor("#3355ff")
    for frame_index in (1, 2):
        image = window.canvas.frames[frame_index].layers[0].image
        assert image.pixelColor(11, 8) == QColor("#3355ff")
        assert image.pixelColor(8, 8) == QColor("black"), "線は塗らない"
        assert image.pixelColor(2, 2) == QColor("white"), "外の背景は塗らない"
    entry = window.canvas.undo_stack[-1]
    assert isinstance(entry, ScopedBatchUndo)
    assert len(entry.cells) == 3, "Undo 1回で全コマ戻る"


def test_tracks_a_moving_shape_instead_of_leaking_into_the_background(window):
    window.canvas._ensure_frame_count(4)
    for frame_index in range(4):
        outlined_cell(window, frame_index, origin=(4 + 3 * frame_index, 4), size=20)
    window.canvas.set_tool("bucket")
    window.canvas.color_mode = "main"
    window.canvas.main_color = QColor("#3355ff")
    window.tools.bucket_all_frames.setChecked(True)

    # 1コマ目の左寄りをクリックする。4コマ目ではこの座標は枠の外の背景になる。
    window.canvas.flood_fill(QPoint(6, 14))

    assert not window.canvas.skewer_fill_pending()
    for frame_index in range(4):
        image = window.canvas.frames[frame_index].layers[0].image
        inside = 4 + 3 * frame_index + 10
        assert image.pixelColor(inside, 14) == QColor("#3355ff")
        assert image.pixelColor(1, 1) == QColor("white"), "背景にはみ出さない"
    assert window.canvas.frames[3].layers[0].image.pixelColor(6, 14) == QColor(
        "white"
    )


def test_clicking_a_held_cell_does_not_create_a_new_cell(window):
    prepare(window, 1)
    window.canvas._ensure_frame_count(2)
    window.canvas.frames[0].layers[0].exposure = 2
    window.canvas.current_frame = 1
    assert not window.canvas.frames[1].layers[0].has_content

    canvas = window.canvas
    canvas.resize(400, 300)
    widget_point = canvas.canvas_to_widget(QPointF(8.5, 8.5)).toPoint()
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=widget_point)

    assert not canvas.frames[1].layers[0].has_content, "保持セルを新しいキーにしない"
    assert canvas.frames[0].layers[0].exposure == 2
    assert canvas.frames[0].layers[0].image.pixelColor(8, 8) == QColor("#3355ff")


def test_reports_when_the_clicked_region_cannot_be_filled(window):
    prepare(window, 2)
    for frame in window.canvas.frames:
        frame.layers[0].image.fill(QColor("white"))
    window.tools.bucket_require_closed.setChecked(True)
    depth = len(window.canvas.undo_stack)
    messages = []
    window.canvas.status_message.connect(messages.append)

    window.canvas.flood_fill(QPoint(8, 8))

    assert len(window.canvas.undo_stack) == depth, "何も変えないなら Undo も積まない"
    assert not window.canvas.skewer_fill_pending()
    assert any("開いている" in message for message in messages)


def test_switching_tools_discards_a_waiting_skewer_fill(window):
    prepare(window, 2)
    outlined_cell(window, 1, origin=(8, 4), size=20)
    depth = len(window.canvas.undo_stack)
    window.canvas.flood_fill(QPoint(8, 8))
    assert window.canvas.skewer_fill_pending()

    window.canvas.set_tool("brush")

    assert not window.canvas.skewer_fill_pending()
    assert len(window.canvas.undo_stack) == depth
    assert window.canvas.frames[0].layers[0].image.pixelColor(8, 8) == QColor("white")


def test_target_colour_applies_while_tracking(window):
    """対象色「黒に囲まれた部分」でも、動く図形を追いかけて色の違う部分ごと塗る。"""
    from paintmaskanimator import bucket_fill

    window.canvas._ensure_frame_count(3)
    for frame_index in range(3):
        layer = outlined_cell(window, frame_index, origin=(4 + 3 * frame_index, 4), size=20)
        # 枠の右半分を別の色にしておく（「すべての色」なら左半分しか塗れない）。
        for x in range(4 + 3 * frame_index + 10, 4 + 3 * frame_index + 19):
            for y in range(5, 23):
                layer.image.setPixelColor(x, y, QColor("#ffd0a0"))
    window.canvas.set_tool("bucket")
    window.canvas.color_mode = "main"
    window.canvas.main_color = QColor("#3355ff")
    window.tools.bucket_all_frames.setChecked(True)
    combo = window.tools.bucket_target_color
    combo.setCurrentIndex(combo.findData(bucket_fill.TARGET_SURROUNDED_BY_BLACK))

    window.canvas.flood_fill(QPoint(7, 14))

    assert not window.canvas.skewer_fill_pending()
    for frame_index in range(3):
        image = window.canvas.frames[frame_index].layers[0].image
        left = 4 + 3 * frame_index + 3
        right = 4 + 3 * frame_index + 15
        assert image.pixelColor(left, 14) == QColor("#3355ff")
        assert image.pixelColor(right, 14) == QColor("#3355ff"), "別の色の部分も塗る"
        assert image.pixelColor(1, 1) == QColor("white"), "枠の外は塗らない"


def test_tracks_a_shape_that_moves_further_than_its_width(window):
    """図形の幅の何倍も動いても、線画のずれを推定して追いかける。"""
    window.canvas._ensure_frame_count(4)
    for frame_index in range(4):
        outlined_cell(window, frame_index, origin=(4 + 40 * frame_index, 30), size=14)
    window.canvas.set_tool("bucket")
    window.canvas.color_mode = "main"
    window.canvas.main_color = QColor("#3355ff")
    window.tools.bucket_all_frames.setChecked(True)

    window.canvas.flood_fill(QPoint(10, 36))

    assert not window.canvas.skewer_fill_pending()
    for frame_index in range(4):
        image = window.canvas.frames[frame_index].layers[0].image
        assert image.pixelColor(4 + 40 * frame_index + 7, 37) == QColor("#3355ff")
        assert image.pixelColor(1, 1) == QColor("white")


def test_enter_accepts_the_guess_on_an_uncertain_frame(window):
    """自信のないコマでは推測を見せ、Enter（変形を確定のキー）でそのまま採用する。"""
    prepare(window, 2)
    # 2コマ目は少し大きい枠：同じ図形の可能性はあるが、自信は持てない。
    outlined_cell(window, 1, origin=(4, 4), size=12)
    window.canvas.flood_fill(QPoint(8, 8))
    assert window.canvas.skewer_fill_pending()
    assert window.canvas.skewer_fill_has_guess(), "推測した範囲を見せる"

    window.a_transform_commit.trigger()

    assert not window.canvas.skewer_fill_pending()
    image = window.canvas.frames[1].layers[0].image
    assert image.pixelColor(10, 10) == QColor("#3355ff"), "推測した範囲で塗る"
    assert image.pixelColor(4, 4) == QColor("black")


def test_tracks_an_area_closed_by_the_frame_edge(window):
    """下端まで線を引いて閉じた部分が、コマごとに大きく動いても追いかける。"""
    from PySide6.QtGui import QPainter, QPen

    from paintmaskanimator import constants

    bottom = constants.CANVAS_HEIGHT - 1
    window.canvas._ensure_frame_count(4)
    for frame_index in range(4):
        layer = window.canvas.frames[frame_index].layers[0]
        layer.image.fill(QColor("white"))
        left = 40 + frame_index * 120
        painter = QPainter(layer.image)
        painter.setPen(QPen(QColor("black"), 3))
        painter.drawLine(left, bottom, left + 60, bottom - 90)
        painter.drawLine(left + 60, bottom - 90, left + 120, bottom)
        painter.end()
        layer.has_content = True
    window.canvas.set_tool("bucket")
    window.canvas.color_mode = "main"
    window.canvas.main_color = QColor("#3355ff")
    window.tools.bucket_all_frames.setChecked(True)

    window.canvas.flood_fill(QPoint(100, bottom - 20))

    assert not window.canvas.skewer_fill_pending()
    for frame_index in range(4):
        image = window.canvas.frames[frame_index].layers[0].image
        assert image.pixelColor(100 + frame_index * 120, bottom - 20) == QColor("#3355ff")
        assert image.pixelColor(200 + frame_index * 120, 100) == QColor("white"), "背景へ出ない"
