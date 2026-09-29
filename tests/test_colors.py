"""Unit tests for the extracted pure color/palette/tone-curve helpers."""
import os
from typing import Any
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from paintmaskanimator import colors  # noqa: E402
from paintmaskanimator import canvas_input_events  # noqa: E402
from paintmaskanimator.canvas import PaintCanvas  # noqa: E402
from paintmaskanimator.main_window_used_color import UsedColorController  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def test_is_pseudo_transparent_color(qapp):
    assert colors.is_pseudo_transparent_color(QColor("white")) is True
    assert colors.is_pseudo_transparent_color(QColor(255, 255, 255)) is True
    assert colors.is_pseudo_transparent_color(QColor("black")) is False
    assert colors.is_pseudo_transparent_color(QColor(254, 255, 255)) is False


def _hover_event(position):
    return QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(position),
        QPointF(position),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_fractional_zoom_line_hover_does_not_repaint_canvas(qapp):
    canvas = PaintCanvas()
    canvas.resize(800, 600)
    canvas.zoom = 0.57
    canvas.tool = "line"
    updates = []
    canvas.update = lambda *args: updates.append(args)

    canvas.mouseMoveEvent(_hover_event(QPointF(400, 300)))

    assert updates == []


def test_fractional_zoom_brush_hover_uses_full_repaint(qapp):
    canvas = PaintCanvas()
    canvas.resize(800, 600)
    canvas.zoom = 0.57
    canvas.tool = "brush"
    updates = []
    canvas.update = lambda *args: updates.append(args)

    canvas.mouseMoveEvent(_hover_event(QPointF(400, 300)))

    assert updates == [()]


@pytest.mark.parametrize(
    ("tool", "temp_tool"),
    (("eyedropper", None), ("brush", "eyedropper")),
)
def test_every_canvas_eyedropper_shows_loupe(
    qapp, monkeypatch, tool, temp_tool
):
    canvas = PaintCanvas()
    canvas.resize(800, 600)
    canvas.tool = tool
    canvas.temp_tool = temp_tool
    shown = []
    hidden = []
    monkeypatch.setattr(
        canvas_input_events, "show_screen_color_loupe",
        lambda _owner, point, _before=None: shown.append(point),
    )
    monkeypatch.setattr(
        canvas_input_events, "hide_screen_color_loupe",
        lambda _owner: hidden.append(True),
    )
    point = QPointF(400, 300)
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress, point, point,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease, point, point,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )

    canvas.mousePressEvent(press)
    canvas.mouseReleaseEvent(release)

    assert shown == [point.toPoint()]
    assert hidden == [True]


def test_paint_rgb_palette_dedups(qapp):
    pal = colors.paint_rgb_palette(
        [QColor("red"), QColor("red"), QColor("blue")]
    )
    assert pal.shape == (2, 3)
    assert pal.dtype == np.uint8


def test_paint_rgb_palette_empty(qapp):
    assert colors.paint_rgb_palette([]).shape == (0, 3)


def test_median_cut_palette_size_bound(qapp):
    rng = np.random.default_rng(1)
    sample = rng.integers(0, 256, (500, 3), dtype=np.uint8)
    pal = colors.median_cut_palette_from_samples(sample, 8)
    assert pal.ndim == 2 and pal.reshape(-1, 3).shape == pal.shape
    assert pal.shape[0] <= 8


def test_median_cut_fewer_unique_than_requested(qapp):
    sample = np.array([[0, 0, 0], [255, 255, 255]] * 10, dtype=np.uint8)
    pal = colors.median_cut_palette_from_samples(sample, 8)
    assert pal.shape[0] == 2


def test_rgb_hsv_features_shape(qapp):
    sample = np.random.default_rng(2).integers(0, 256, (30, 3), dtype=np.uint8)
    feats = colors.rgb_hsv_features(sample)
    assert feats.shape == (30, 4)
    assert feats.dtype == np.float32


def test_normalize_tone_curve_points_clamps_and_bounds(qapp):
    pts = colors.normalize_tone_curve_points([(0.5, 2.0), (-1.0, 0.3)])
    assert pts[0] == (0.0, 0.0)
    assert pts[-1] == (1.0, 1.0)
    # all values within [0,1] and x is sorted
    xs = [x for x, _ in pts]
    assert xs == sorted(xs)
    assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in pts)


def test_local_color_variation_flat_is_zero(qapp):
    flat = np.full((5, 5, 3), 128, dtype=np.uint8)
    assert int(colors.local_color_variation(flat).max()) == 0


def test_priority_palette_within_bound(qapp):
    sample = np.random.default_rng(3).integers(0, 256, (200, 3), dtype=np.uint8)
    pal = colors.priority_palette_colors_from_samples(sample, 5)
    # Compared as a whole: NumPy 2 types ``.shape`` as a variadic tuple, so
    # both indexing and unpacking it are static type errors.
    assert pal.ndim == 2
    assert pal.shape[-1:] == (3,)
    assert len(pal) <= 5


def test_used_color_scan_prewarms_palette_filter_index(qapp):
    canvas = PaintCanvas()
    image = QImage(2, 1, QImage.Format.Format_ARGB32)
    image.setPixelColor(0, 0, QColor(255, 0, 0))
    image.setPixelColor(1, 0, QColor(0, 0, 255))
    window: Any = SimpleNamespace(canvas=canvas)
    owner = UsedColorController(window)

    assert owner._extract(image) == [(0, 0, 255), (255, 0, 0)]
    assert len(canvas._color_index_cache) == 1
    warmed_index = next(iter(canvas._color_index_cache.values()))

    canvas.visible_color_rgbs = {(255, 0, 0)}
    layer = SimpleNamespace(
        image=image,
        color_filter_enabled=False,
        color_filter_rgb=None,
    )
    filtered = canvas.filtered_layer_image(layer)

    assert next(iter(canvas._color_index_cache.values())) is warmed_index
    assert filtered.pixelColor(0, 0).alpha() == 255
    assert filtered.pixelColor(1, 0).alpha() == 0
