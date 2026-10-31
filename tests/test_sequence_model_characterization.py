"""連番を本体にする移行（Issue #31）の Phase 0：いまの振る舞いを固定する。

データの本体を連番にする移行で、静かに壊れやすいところを先に固定しておく。
ここが赤くなったら、移行で**利用者から見える動きが変わっている**。
設計は docs/sequence-first-data-model.md。

このファイルは「いまこう動く」を書き留めたもので、理想の仕様ではない。
移行の各段階で、意図して変える場合はテストごと書き換える（そのときに
何が変わるのかを、差分として見えるようにするのが狙い）。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.frame_scope import resolve_cells  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    window.canvas._ensure_frame_count(12)
    try:
        yield window
    finally:
        window.deleteLater()


def put_cell(window, column, number, *, exposure=1, layer_index=0, color="#101010"):
    """コマ ``column`` に絵番号 ``number`` のセルを置く。"""
    layer = window.canvas.frames[column].layers[layer_index]
    layer.image.setPixelColor(10, 10, QColor(color))
    layer.has_content = True
    layer.is_blank_key = False
    layer.sequence_number = int(number)
    layer.exposure = int(exposure)
    return layer


def sheet_states(window, columns, layer_index=0):
    """シートの並びを ``[絵番号 または None, ...]`` として読み出す。

    `TimeRemapController.timeline_states` と同じものを、ここでは手で組む。
    Phase 0 は main の振る舞いを固定するためのものなので、まだ入っていない
    機能には寄りかからない。
    """
    states = []
    for column in range(columns):
        block = window.canvas.timeline_block_at(column, layer_index)
        if block is None:
            states.append(None)
            continue
        _kind, start, _exposure = block
        layer = window.canvas.frames[start].layers[layer_index]
        states.append(
            int(layer.sequence_number)
            if layer.has_content and layer.sequence_number is not None
            else None
        )
    return states


def after_effects_text(states, fps=24):
    """``states`` から AE の Time Remap キーフレームデータを組む（テスト用）。"""
    tab = chr(9)
    time_lines, opacity_lines, visible = [], [], None
    for index, state in enumerate(states):
        frame = index + 1
        if state is None:
            if visible is not False:
                opacity_lines.append(f"{tab}{frame}{tab}0")
                visible = False
            continue
        if visible is not True:
            opacity_lines.append(f"{tab}{frame}{tab}100")
            visible = True
        time_lines.append(f"{tab}{frame}{tab}{(int(state) - 1) / fps:.6f}")
    return chr(10).join([
        "Adobe After Effects 8.0 Keyframe Data",
        "",
        f"{tab}Units Per Second{tab}{fps:g}",
        "",
        "Time Remap",
        f"{tab}Frame{tab}seconds",
        *time_lines,
        "",
        f"Transform{tab}Opacity",
        f"{tab}Frame{tab}percent",
        *opacity_lines,
        "",
        "End of Keyframe Data",
        "",
    ])


# ---------------------------------------------------------------------------
# 1. 同じ番号のセルは同じ絵を指す（coalesce_numbered_images）
# ---------------------------------------------------------------------------

def test_the_same_number_shares_one_image(window):
    """同じ番号のセルは、同じ QImage の実体を共有する。

    いまは共有を `id(layer.image)` でしか確かめられない。連番が本体になれば
    「同じ番号なら同じ絵」はデータの形から言えるようになる。
    """
    put_cell(window, 0, 1)
    put_cell(window, 4, 1)
    put_cell(window, 8, 2)

    window.canvas.coalesce_numbered_images()

    first = window.canvas.frames[0].layers[0].image
    same_number = window.canvas.frames[4].layers[0].image
    other_number = window.canvas.frames[8].layers[0].image
    assert first is same_number
    assert first is not other_number


def test_editing_one_cell_shows_on_every_frame_with_that_number(window):
    """共有されている絵を直すと、同じ番号の全コマに出る。"""
    put_cell(window, 0, 1)
    put_cell(window, 4, 1)
    window.canvas.coalesce_numbered_images()

    window.canvas.frames[0].layers[0].image.setPixelColor(20, 20, QColor("#ff0000"))

    assert window.canvas.frames[4].layers[0].image.pixelColor(20, 20) == QColor(
        "#ff0000"
    )


# ---------------------------------------------------------------------------
# 2. 保持セルへ描くと独立したキーへ分かれる（ensure_editable_key）
# ---------------------------------------------------------------------------

def test_drawing_on_a_held_frame_edits_the_key_not_a_new_cell(window):
    """保持区間の途中を選んで描くと、編集対象は露出元のキーへ寄る。

    移行後（§6.2 の B案）もここは変わらない。番号を直すのだから、
    露出元を直すのが正しい。
    """
    put_cell(window, 0, 1, exposure=4)
    window.canvas.current_frame = 2
    window.canvas.active_layer_index = 0

    window.canvas.ensure_editable_key()

    assert window.canvas.current_frame == 0
    assert window.canvas.frames[0].layers[0].exposure == 4


def test_drawing_on_an_unused_frame_makes_a_key_there(window):
    """未使用のコマへ描くと、そのコマがキーになる。"""
    put_cell(window, 0, 1)
    window.canvas.current_frame = 5
    window.canvas.active_layer_index = 0
    assert not window.canvas.frames[5].layers[0].has_content

    window.canvas.ensure_editable_key()

    assert window.canvas.frames[5].layers[0].has_content


def test_a_blank_key_never_stays_marked_as_content(window):
    """○と絵ありのフラグが同時に立たない。

    いまは has_content / is_blank_key / sequence_only の3つのフラグの
    組み合わせで表していて、不正な組み合わせが作れてしまう。移行後は
    1つの値（number | BLANK | EMPTY）になり、この修復自体が要らなくなる。
    """
    layer = window.canvas.frames[3].layers[0]
    layer.has_content = True
    layer.is_blank_key = True
    layer.sequence_number = 7
    window.canvas.current_frame = 3
    window.canvas.active_layer_index = 0

    window.canvas.ensure_editable_key()

    assert not window.canvas.frames[3].layers[0].is_blank_key


# ---------------------------------------------------------------------------
# 3. 一括処理は同じ絵を1回しか処理しない（frame_scope）
# ---------------------------------------------------------------------------

def test_a_held_exposure_is_processed_once(window):
    """露出で何コマ出ていても、処理するセルは露出元の1つ。"""
    put_cell(window, 0, 1, exposure=5)

    cells = resolve_cells(window.canvas, window.scope.current_frame(True))

    assert [frame for frame, _layer in cells].count(0) == 1
    assert not any(1 <= frame <= 4 for frame, _layer in cells)


def test_the_same_drawing_in_two_places_is_processed_once(window):
    """同じ番号が離れた2か所に出ていても、絵としては1回だけ処理する。

    これが Issue #31 の「目的」に書かれていた重複排除。**すでに効いている**
    ので、移行で速くなるわけではない（設計文書 §1）。
    """
    put_cell(window, 0, 1)
    put_cell(window, 6, 1)
    window.canvas.coalesce_numbered_images()

    cells = resolve_cells(window.canvas, window.scope.current_frame(True))
    images = {
        id(window.canvas.frames[frame].layers[layer].image)
        for frame, layer in cells
    }

    # セルとしては2つ返るが、絵の実体は1つ。
    assert len([c for c in cells if c[0] in (0, 6)]) == 2
    assert len(images) < len(cells)


def test_a_batch_edit_touches_a_shared_drawing_once(window):
    """共有された絵への一括処理は、1回で全コマに反映される。"""
    put_cell(window, 0, 1, color="#ff0000")
    put_cell(window, 6, 1, color="#ff0000")
    window.canvas.coalesce_numbered_images()

    assert window.used_color.apply_palette_replacements(
        {(255, 0, 0): (0, 0, 255)}
    )

    for column in (0, 6):
        assert window.canvas.frames[column].layers[0].image.pixelColor(
            10, 10
        ) == QColor("#0000ff")


# ---------------------------------------------------------------------------
# 4. タイムリマップの往復で並びが変わらない
# ---------------------------------------------------------------------------

def test_the_sheet_survives_a_time_remap_round_trip(window):
    """シート → AE テキスト → シート で並びが変わらない。

    移行後はシートの番号列を差し替えるだけになるが、外から見た結果は同じで
    なければならない。
    """
    for column, number in enumerate([1, 2, None, 3, 3, None, 4]):
        layer = window.canvas.frames[column].layers[0]
        layer.exposure = 1
        if number is None:
            layer.has_content = False
            layer.is_blank_key = False
            layer.sequence_number = None
        else:
            put_cell(window, column, number)
    before = sheet_states(window, 7)

    parsed = window.time_remap.parse_text(after_effects_text(before))

    assert parsed["states"] == before


def test_an_exposure_reads_back_as_the_repeated_number(window):
    """露出は「同じ番号が続いている」として読み出される。

    移行後はこれがデータそのものになる（exposure フィールドが無くなる）。
    """
    put_cell(window, 0, 1, exposure=3)

    assert sheet_states(window, 3) == [1, 1, 1]


# ---------------------------------------------------------------------------
# 5. 表示の設定がセルごとに重複している（移行で Track へ移す）
# ---------------------------------------------------------------------------

def test_display_properties_live_on_every_cell_today(window):
    """いまは表示の設定をセルごとに持ち、写して回っている。

    移行後は Track が1つ持つだけになるので、このテストは Phase 2 で
    書き換える。ここでは「いま写しが要る」ことを記録しておく。
    """
    source = window.canvas.frames[0].layers[0]
    source.opacity = 0.5
    source.visible = False
    target = window.canvas.frames[1].layers[0]

    window.layers._copy_display_properties(source, target)

    assert target.opacity == 0.5
    assert target.visible is False
    # 写す前の他のコマは変わっていない（＝レイヤー共通の設定ではない）。
    assert window.canvas.frames[2].layers[0].opacity == 1.0
