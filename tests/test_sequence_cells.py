"""絵の共有を Cell というデータにする（Issue #31 Phase 1）。

これまで「同じ番号は同じ絵」は `id(layer.image)` を見るしかなく、どこかで
片方だけ差し替わると静かに壊れた。Cell を間に置くと、共有は
「同じ Cell を指しているか」として確かめられる。

設計は docs/sequence-first-data-model.md。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.models import Layer  # noqa: E402
from paintmaskanimator.utils import blank_image  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    window.canvas._ensure_frame_count(10)
    try:
        yield window
    finally:
        window.deleteLater()


def put_cell(window, column, number, *, exposure=1, layer_index=0):
    layer = window.canvas.frames[column].layers[layer_index]
    layer.has_content = True
    layer.is_blank_key = False
    layer.sequence_number = int(number)
    layer.exposure = int(exposure)
    return layer


def cell_ids(window, columns, layer_index=0):
    return [
        window.canvas.frames[column].layers[layer_index].cell_id
        for column in columns
    ]


# --- Cell そのもの -----------------------------------------------------------

def test_layers_start_with_their_own_drawing(qapp):
    first = Layer("A", blank_image())
    second = Layer("A", blank_image())

    assert first.cell_id != second.cell_id


def test_sharing_makes_writes_show_on_both(qapp):
    first = Layer("A", blank_image())
    second = Layer("A", blank_image())

    second.share_cell_with(first)

    assert second.cell_id == first.cell_id
    second.image.setPixelColor(5, 5, QColor("#ff0000"))
    assert first.image.pixelColor(5, 5) == QColor("#ff0000")


def test_assigning_a_new_image_also_shows_on_both(qapp):
    """差し替えも共有先へ届く。ここが `id(layer.image)` 時代との違い。"""
    first = Layer("A", blank_image())
    second = Layer("A", blank_image())
    second.share_cell_with(first)

    replacement = blank_image()
    replacement.setPixelColor(1, 1, QColor("#00ff00"))
    second.image = replacement

    assert first.image.pixelColor(1, 1) == QColor("#00ff00")
    assert first.cell_id == second.cell_id


def test_detaching_keeps_the_picture_but_ends_the_sharing(qapp):
    first = Layer("A", blank_image())
    first.image.setPixelColor(2, 2, QColor("#0000ff"))
    second = Layer("A", blank_image())
    second.share_cell_with(first)

    second.detach_cell()

    assert second.cell_id != first.cell_id
    # 切り離しても、いまの絵はそのまま持っている。
    assert second.image.pixelColor(2, 2) == QColor("#0000ff")
    second.image.setPixelColor(3, 3, QColor("#ffff00"))
    assert first.image.pixelColor(3, 3) != QColor("#ffff00")


def test_a_clone_owns_its_drawing(qapp):
    """Undo のスナップショットが、あとの編集で汚れないこと。"""
    original = Layer("A", blank_image())

    copied = original.clone()

    assert copied.cell_id != original.cell_id
    original.image.setPixelColor(4, 4, QColor("#ff00ff"))
    assert copied.image.pixelColor(4, 4) != QColor("#ff00ff")


# --- 文書の中での共有 --------------------------------------------------------

def test_coalesce_links_cells_of_the_same_number(window):
    put_cell(window, 0, 1)
    put_cell(window, 4, 1)
    put_cell(window, 8, 2)

    window.canvas.coalesce_numbered_images()

    first, same, other = cell_ids(window, (0, 4, 8))
    assert first == same
    assert first != other


def test_a_replacement_image_reaches_every_frame_with_that_number(window):
    """差し替えで共有が切れない。これまでは切れていた。"""
    put_cell(window, 0, 1)
    put_cell(window, 4, 1)
    window.canvas.coalesce_numbered_images()

    replacement = blank_image()
    replacement.setPixelColor(7, 7, QColor("#123456"))
    window.canvas.frames[0].layers[0].image = replacement

    assert window.canvas.frames[4].layers[0].image.pixelColor(7, 7) == QColor(
        "#123456"
    )
    assert cell_ids(window, (0, 4))[0] == cell_ids(window, (0, 4))[1]


def test_clearing_a_cell_does_not_erase_the_other_frames(window):
    """シート操作は共有を切ってから。消すのはこの位置だけ。"""
    put_cell(window, 0, 1)
    put_cell(window, 4, 1)
    window.canvas.coalesce_numbered_images()
    window.canvas.frames[0].layers[0].image.setPixelColor(9, 9, QColor("#abcdef"))

    window.canvas._clear_timeline_layer_cell(
        window.canvas.frames[4].layers[0]
    )

    assert window.canvas.frames[0].layers[0].image.pixelColor(9, 9) == QColor(
        "#abcdef"
    )
    assert cell_ids(window, (0,))[0] != cell_ids(window, (4,))[0]


def test_sync_links_the_cells_before_a_stroke(window):
    put_cell(window, 0, 1)
    put_cell(window, 4, 1)
    assert cell_ids(window, (0,))[0] != cell_ids(window, (4,))[0]

    window.canvas.sync_numbered_image_from_cell(0, 0)

    assert cell_ids(window, (0,))[0] == cell_ids(window, (4,))[0]


def test_different_layers_with_the_same_number_stay_apart(window):
    """番号はレイヤーごと。レイヤーAの1番とレイヤーBの1番は別の絵。"""
    window.canvas.add_layer()
    put_cell(window, 0, 1, layer_index=0)
    put_cell(window, 0, 1, layer_index=1)

    window.canvas.coalesce_numbered_images()

    first = window.canvas.frames[0].layers[0].cell_id
    second = window.canvas.frames[0].layers[1].cell_id
    assert first != second
