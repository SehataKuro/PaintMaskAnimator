"""#88：#60〜#90 で入れた機能を、画面から手を動かす流れで通す。

見た目・実機のマウス操作・大きな画像での待ち時間は、ここでは確かめられない
（#88 の手での確認に残す）。
"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen

from paintmaskanimator import imaging

pytestmark = pytest.mark.e2e

SKIN = (255, 228, 205)
HAIR = (120, 80, 60)
RED = (255, 0, 0)
BLUE = (0, 0, 255)
BLACK = (0, 0, 0)


def tp(antialiased=False, width=160, height=120):
    """肌と髪の色面に、赤と黒の1px線がある TP。``antialiased`` なら境目に混ざった色。"""
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[:, :, 3] = 255
    rgba[:, : width // 2, :3] = SKIN
    rgba[:, width // 2:, :3] = HAIR
    rgba[20:100, 30, :3] = RED
    rgba[60, 10:150, :3] = BLACK
    if antialiased:
        rgba[20:100, 31, :3] = (255, 114, 103)  # 赤と肌の中間
        rgba[:, width // 2 - 1, :3] = (188, 154, 132)  # 肌と髪の中間
        rgba[61, 10:80, :3] = (128, 114, 102)  # 黒と肌の中間
    return rgba


def save_frames(tmp_path, frames, prefix="A"):
    paths = []
    for index, rgba in enumerate(frames):
        path = tmp_path / f"{prefix}{index + 1:04d}.png"
        assert imaging.rgba_array_to_qimage(np.ascontiguousarray(rgba)).save(str(path))
        paths.append(path)
    return paths


ORIGINAL = {SKIN, HAIR, RED, BLACK}


# -- 読み込み ----------------------------------------------------------------
def test_digital_import_finds_antialias_on_a_later_frame_and_keeps_tp_colours(app, tmp_path):
    """#63 #65：2枚目にだけあるアンチエイリアスを見つけて取り除き、初期状態を保持する。"""
    paths = save_frames(tmp_path, [tp(), tp(antialiased=True)])

    app.import_images(paths, report="取り除いて読み込む", keep_initial="保存する")

    for frame in range(2):
        assert app.layer_colours(frame) == ORIGINAL, "色は増えも減りもしない"
    layer = app.canvas.frames[0].layers[app.active_layer_index]
    assert layer.initial_image is not None, "読み込み後に初期状態として保持した"


def test_reconvert_from_files_after_saving_and_reopening(app, tmp_path):
    """#80 #89：そのまま読み込んだ連番を、保存して開き直したあとに元のファイルから再変換する。"""
    paths = save_frames(tmp_path, [tp(antialiased=True), tp(antialiased=True)])
    app.import_images(paths, report="そのまま読み込む")
    assert len(app.layer_colours(0)) > len(ORIGINAL)
    project = tmp_path / "reconvert.pman"
    assert app.save_project_as(project)

    app.open_project(project)
    layer_index = len(app.canvas.layers) - 1
    app.canvas.active_layer_index = layer_index

    assert app.reconvert_active_layer(), (app.status_message(), app.messages.shown,
                                          app.canvas.active_layer._track.import_sources)
    assert app.layer_colours(0, layer_index) == ORIGINAL
    app.canvas.undo()
    assert len(app.layer_colours(0, layer_index)) > len(ORIGINAL), "Undo 1回で戻る"


def test_binarize_an_imported_layer_from_the_edit_menu(app, tmp_path):
    """#62：読み込んだあとのレイヤーに、編集メニューの「2値化…」をかける。"""
    from paintmaskanimator.color_reduction import ColorReductionDialog

    paths = save_frames(tmp_path, [tp(antialiased=True)])
    app.import_images(paths, report="そのまま読み込む")
    before = app.layer_colours(0)

    with app.accept_dialog(ColorReductionDialog):
        app.trigger_action("a_binarize_layer")

    after = app.layer_colours(0)
    assert len(after) < len(before) and len(after) <= 8


# -- 色・バケツ ----------------------------------------------------------------
def test_bucket_replace_mode_replaces_a_colour_in_every_frame(app):
    """#67：バケツの色置換をキャンバスのクリックで実行する。"""
    app.set_main_color(QColor(*RED))
    app.select_tool("brush")
    app.stroke([(100, 100), (200, 100)])
    app.add_frame(duplicate=True)
    app.set_main_color(QColor(*BLUE))
    app.select_tool("bucket")
    app.set_bucket_replace_color(True)

    app.click((150, 100))

    for frame in range(2):
        assert (255, 0, 0) not in app.layer_colours(frame, 0)
        assert BLUE in app.layer_colours(frame, 0)


def test_cleared_replacement_colour_is_not_applied(app):
    """#66：置換色を外すと、色置換をかけてもその色は変わらない。Undo で戻せる。"""
    app.set_main_color(QColor(*RED))
    app.select_tool("brush")
    app.stroke([(100, 100), (200, 100)])
    app.refresh_used_colors()
    app.set_replacement_color(RED, QColor(*BLUE))

    assert app.clear_replacement_color(RED)
    app.apply_replacements()
    assert RED in app.layer_colours(0, 0)

    app.canvas.undo()
    assert app.window.palette.current_replacements() == {RED: BLUE}


# -- 線整え ----------------------------------------------------------------------
def test_line_tidy_evens_out_a_diagonal_of_blocks(app):
    """#64：2×2の塊が角でつながった45°の線を、足して太さをそろえる。"""
    layer = app.canvas.frames[0].layers[0]
    for k in range(4):
        x, y = 100 + 2 * k, 100 + 2 * k
        for dx in range(2):
            for dy in range(2):
                layer.image.setPixelColor(x + dx, y + dy, QColor("black"))
    layer.has_content = True
    from paintmaskanimator.document import create_drawing

    create_drawing(layer, layer.image.copy(), number=1)

    assert app.tidy_lines([BLACK], diagonal="add")
    rgba = app.layer_rgba(0, 0)
    rows = [int((rgba[y, 95:115, 3] > 0).sum()) for y in range(101, 107)]
    assert len(set(rows)) == 1, f"行ごとの画素数がそろう: {rows}"


# -- 原画・オニオン・トゥイーン --------------------------------------------------
def test_import_genga_and_insert_inbetweens(app, tmp_path):
    """#24：原画を読み込み、原画のあいだに中割りの○を入れる。"""
    paths = save_frames(tmp_path, [tp(), tp()], prefix="G")
    app.import_images(paths, genga=True)
    layer_index = app.active_layer_index
    assert all(
        frame.layers[layer_index].is_genga
        for frame in app.canvas.frames if frame.layers[layer_index].has_content
    )
    app.canvas.timeline_mode = "sheet"
    app.move_cell(layer_index, 1, 4)        # 2枚目の原画を4コマ目へ
    app.set_exposure(0, layer_index, 4)     # 1枚目の原画を、2枚目の手前まで伸ばす
    assert app.block_at(4, layer_index)[:2] == ("content", 4)

    assert app.insert_inbetweens(layer_index, 0, 1), (app.status_message(), [app.block_at(c, layer_index) for c in range(6)])
    assert app.block_at(2, layer_index)[:2] == ("blank", 2), "後半が中割りの○になる"


@pytest.mark.parametrize("flip", [False, True])
def test_ctrl_click_on_an_onion_frame_switches_to_it(app, flip):
    """#60：オニオンに見えている前のコマを Ctrl＋クリックして切り替える（表示の反転も）。"""
    app.set_main_color(QColor(*BLACK))
    app.select_tool("brush")
    app.stroke([(80, 80), (140, 80)])
    app.add_frame()
    app.go_to_frame(1)
    app.stroke([(80, 300), (140, 300)])
    app.set_onion(previous=1, following=0)
    app.set_view_flip(horizontal=flip)
    drawn_before = [app.layer_rgba(frame, 0).copy() for frame in range(2)]

    app.click((110, 80), Qt.KeyboardModifier.ControlModifier)

    assert app.canvas.current_frame == 0
    for frame in range(2):
        assert (app.layer_rgba(frame, 0) == drawn_before[frame]).all(), "クリックでは描かない"


def test_transforming_a_held_key_becomes_a_tween(app):
    """#61：オプションを ON にすると、保持のあるキーの変形がそのままトゥイーンになる。"""
    app.set_main_color(QColor(*BLACK))
    app.select_tool("brush")
    pen = QPen(QColor("black"), 6)
    layer = app.canvas.frames[0].layers[0]
    painter = QPainter(layer.image)
    painter.setPen(pen)
    painter.drawEllipse(200, 200, 120, 120)
    painter.end()
    from paintmaskanimator.document import create_drawing

    create_drawing(layer, layer.image.copy(), number=1)
    app.set_exposure(0, 0, 4)
    app.set_transform_as_tween(True)

    app.start_transform("free")
    assert app.canvas.tween_pending, "確認なしでトゥイーンが始まる"
    app.drag_transform((260, 260), (300, 280))
    app.commit_transform()

    key = app.canvas.frames[0].layers[0]
    assert key.tween and all(
        app.canvas.frames[column].layers[0].tween_member == key.tween["id"]
        for column in range(1, 4)
    )
