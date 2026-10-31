"""取り込みパイプラインの不具合2件（Issue #10 から切り出し）。

1. JPEG の白背景が、圧縮ノイズで完全一致にならず残っていた。
2. パレットを1枚目だけから作っていたため、1枚目に無い色が潰れていた。
"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import color_ops, imaging  # noqa: E402
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


def noisy_white_page(size=32):
    """圧縮ノイズで少しずれた白背景に、黒い囲みと中の白を置いた絵。"""
    pixels = np.zeros((size, size, 4), dtype=np.uint8)
    pixels[:, :, 3] = 255
    rng = np.random.default_rng(0)
    noise = rng.integers(250, 256, size=(size, size, 3), dtype=np.uint8)
    pixels[:, :, :3] = noise
    pixels[8:24, 8:24, :3] = 0          # 黒い囲み
    pixels[12:20, 12:20, :3] = 255      # 囲みの中の白（目のハイライト相当）
    return imaging.rgba_array_to_qimage(pixels)


def test_the_noisy_white_background_is_removed(window):
    image = noisy_white_page()

    result = window.canvas.make_border_background_transparent(image)
    pixels = imaging.qimage_rgba_array(result)

    # 外周の背景は落ちる。
    assert pixels[0, 0, 3] == 0
    assert pixels[2, 30, 3] == 0


def test_white_inside_the_drawing_survives(window):
    """絵の中の白は外周とつながっていないので残る。"""
    image = noisy_white_page()

    result = window.canvas.make_border_background_transparent(image)
    pixels = imaging.qimage_rgba_array(result)

    assert pixels[16, 16, 3] == 255
    assert pixels[9, 9, 3] == 255


def test_an_image_without_a_light_border_is_left_alone(window):
    pixels = np.zeros((16, 16, 4), dtype=np.uint8)
    pixels[:, :, :3] = (30, 40, 50)
    pixels[:, :, 3] = 255
    image = imaging.rgba_array_to_qimage(pixels)

    result = window.canvas.make_border_background_transparent(image)

    assert imaging.qimage_rgba_array(result)[0, 0, 3] == 255


def test_jpeg_now_goes_through_background_removal(window, tmp_path):
    path = tmp_path / "frame.jpg"
    assert noisy_white_page(64).save(str(path), "JPG", 92)

    ok, message = window.canvas.import_image_sequence([str(path)])

    assert ok, message
    layer = window.canvas.frames[window.canvas.current_frame].layers[0]
    pixels = imaging.qimage_rgba_array(layer.image)
    # 画像は中央へ置かれるので、キャンバスの隅は必ず背景。
    assert pixels[0, 0, 3] == 0


# --- パレットの代表フレーム -------------------------------------------------

def frame_with(colors, size=8):
    pixels = np.zeros((size, size, 4), dtype=np.uint8)
    pixels[:, :, 3] = 255
    for index, color in enumerate(colors):
        pixels[index, :, :3] = color
    return imaging.rgba_array_to_qimage(pixels)


def test_a_colour_missing_from_the_first_frame_is_kept(qapp):
    first = frame_with([(255, 0, 0), (255, 0, 0), (0, 0, 0)])
    later = frame_with([(0, 255, 0), (0, 255, 0), (0, 0, 0)])

    alone = color_ops.build_color_reduction_palette(first, 8)
    together = color_ops.build_color_reduction_palette(
        first, 8, extra_images=[later]
    )

    assert [0, 255, 0] not in alone.tolist()
    assert [0, 255, 0] in together.tolist()


def test_representative_frames_cover_the_whole_sequence():
    assert color_ops.reference_frame_indices(1) == []
    # 末尾は必ず入る。
    assert color_ops.reference_frame_indices(100)[-1] == 99
    assert all(
        index >= 1 for index in color_ops.reference_frame_indices(100)
    )


def test_a_short_sequence_does_not_repeat_frames():
    picked = color_ops.reference_frame_indices(3)
    assert len(picked) == len(set(picked))
    assert all(1 <= index <= 2 for index in picked)


def test_an_empty_image_does_not_break_the_palette(qapp):
    empty = QImage(0, 0, QImage.Format.Format_ARGB32_Premultiplied)
    palette = color_ops.build_color_reduction_palette(empty, 8)
    assert palette.shape[1] == 3
