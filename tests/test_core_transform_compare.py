"""Rust の変形ツール（pma_core::tools::transform）を、Qt の画面の経路と画素で突き合わせる。

- 変形を掛けた QPainter::drawImage（compose::painter）を、行列の種類ごとに QPainter と比べる。
- 変形ツールの始めから確定までを、Python のキャンバス（PaintCanvas）と同じ点で行い、
  確定したレイヤーの画素が一致することを確かめる（通常・クオリティ・メッシュ）。
- 移動は 1 画素単位（#116）：0.5 画素ずれる所へドラッグしても線が太らない。
"""
import math
import os
import random

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QImage, QPainter, QTransform  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import pma_native  # noqa: E402
from paintmaskanimator import imaging  # noqa: E402
from paintmaskanimator.canvas import PaintCanvas  # noqa: E402
from paintmaskanimator.pma_image import as_qimage  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _premul_bytes(image: QImage) -> bytes:
    """ARGB32_Premultiplied を R, G, B, A の順のバイト列にする。"""
    image = image.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    w, h = image.width(), image.height()
    rows = np.frombuffer(image.constBits(), np.uint8).reshape(h, image.bytesPerLine())
    return rows[:, : w * 4].tobytes()


def _random_image(rng: random.Random, width: int, height: int) -> QImage:
    rgba = np.zeros((height, width, 4), np.uint8)
    palette = [(255, 0, 0, 255), (0, 0, 0, 255), (20, 200, 60, 255), (0, 0, 0, 0),
               (255, 255, 255, 255), (40, 80, 200, 128)]
    for y in range(height):
        for x in range(width):
            rgba[y, x] = palette[rng.randrange(len(palette))]
    return imaging.rgba_array_to_qimage(rgba).convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied
    )


def _random_matrix(rng: random.Random) -> QTransform:
    kind = rng.choice(["translate", "scale", "rotate", "shear", "project", "flip"])
    t = QTransform()
    t.translate(rng.uniform(-10, 50), rng.uniform(-10, 50))
    if kind == "translate":
        return t
    if kind in ("scale", "flip"):
        sx, sy = rng.uniform(0.3, 3.0), rng.uniform(0.3, 3.0)
        if kind == "flip":
            sx = -sx if rng.random() < 0.5 else sx
            sy = -sy if rng.random() < 0.5 else sy
            t.translate(30, 30)
        t.scale(sx, sy)
        return t
    if kind == "rotate":
        t.rotate(rng.choice([rng.uniform(-180, 180), 90.0, 180.0, 37.5]))
        t.scale(rng.uniform(0.5, 2.0), rng.uniform(0.5, 2.0))
        return t
    if kind == "shear":
        t.shear(rng.uniform(-0.6, 0.6), rng.uniform(-0.6, 0.6))
        return t
    return QTransform(
        rng.uniform(0.6, 1.6), rng.uniform(-0.3, 0.3), rng.uniform(-0.004, 0.004),
        rng.uniform(-0.3, 0.3), rng.uniform(0.6, 1.6), rng.uniform(-0.004, 0.004),
        rng.uniform(0, 30), rng.uniform(0, 30), 1.0,
    )


def _matrix_values(t: QTransform):
    return [t.m11(), t.m12(), t.m13(), t.m21(), t.m22(), t.m23(), t.m31(), t.m32(), t.m33()]


def test_painter_draw_image_matches_qpainter(app):
    rng = random.Random(7)
    for case in range(400):
        sw, sh = rng.randint(1, 40), rng.randint(1, 40)
        if sw * sh == 1:
            sw = 2
        source = _random_image(rng, sw, sh)
        matrix = _random_matrix(rng)
        bounds = matrix.mapRect(QRectF(0, 0, sw, sh))
        if (
            matrix.type() == QTransform.TransformationType.TxRotate
            and (bounds.width() < 16 or bounds.height() < 16)
        ):
            # 16 画素未満の回転は Qt が rasterizeLine で塗る（未対応。モジュールの説明を参照）。
            continue
        w, h = 64, 56
        opacity = rng.choice([1.0, 1.0, 0.5, 0.22])
        stretch = rng.random() < 0.3
        target = QRectF(rng.uniform(-3, 3), rng.uniform(-3, 3),
                        sw * (rng.uniform(0.5, 2.0) if stretch else 1.0),
                        sh * (rng.uniform(0.5, 2.0) if stretch else 1.0))
        expected = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        expected.fill(0)
        painter = QPainter(expected)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.setTransform(matrix)
        painter.setOpacity(opacity)
        painter.drawImage(target, source, QRectF(0, 0, sw, sh))
        painter.end()
        actual = pma_native.painter_draw_image(
            _premul_bytes(source), sw, sh, _matrix_values(matrix),
            (target.x(), target.y(), target.width(), target.height()),
            (0.0, 0.0, float(sw), float(sh)), w, h, opacity,
        )
        assert actual == _premul_bytes(expected), (case, matrix, target)


# --- 変形ツール ---------------------------------------------------------------

WIDTH, HEIGHT = 96, 80


def _layer_rgba(rng: random.Random) -> np.ndarray:
    rgba = np.zeros((HEIGHT, WIDTH, 4), np.uint8)
    rgba[20:60, 24:70] = (200, 40, 40, 255)
    rgba[30:50, 30:40] = (255, 255, 255, 255)
    rgba[18:62, 46] = (0, 0, 0, 255)
    rgba[40, 22:72] = (0, 0, 0, 255)
    for _ in range(40):
        rgba[rng.randrange(16, 64), rng.randrange(20, 74)] = (20, 200, 60, 255)
    return rgba


def _canvas_with(rgba: np.ndarray) -> PaintCanvas:
    canvas = PaintCanvas()
    canvas.set_transform_quality(False)
    image = imaging.rgba_array_to_qimage(rgba)
    canvas._document.replace_drawing_image(
        canvas.current_frame, canvas.active_layer_index,
        _padded(canvas, image), has_content=True,
    )
    return canvas


def _padded(canvas: PaintCanvas, image: QImage) -> QImage:
    layer = as_qimage(canvas.active_layer.image)
    full = QImage(layer.width(), layer.height(), QImage.Format.Format_ARGB32_Premultiplied)
    full.fill(0)
    painter = QPainter(full)
    painter.drawImage(0, 0, image)
    painter.end()
    return full


def _layer_bytes(canvas: PaintCanvas) -> bytes:
    image = as_qimage(canvas.active_layer.image).convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = image.width(), image.height()
    rows = np.frombuffer(image.constBits(), np.uint8).reshape(h, image.bytesPerLine())
    return rows[:, : w * 4].tobytes()


def _begin(layer, mode: str, **options) -> pma_native.TransformSession:
    begun = pma_native.TransformSession.begin(layer, mode, **options)
    assert begun is not None
    return begun[0]


def _shapes(rng: random.Random, points):
    """四角形の点を、移動・拡縮・回転・自由変形にした例。"""
    cx = sum(p[0] for p in points) / 4
    cy = sum(p[1] for p in points) / 4
    angle = math.radians(rng.uniform(-60, 60))
    scale = rng.uniform(0.4, 1.6)
    moved = [(x + rng.randint(-15, 15), y + rng.randint(-10, 10)) for x, y in points]
    rotated = [
        (cx + (x - cx) * math.cos(angle) - (y - cy) * math.sin(angle),
         cy + (x - cx) * math.sin(angle) + (y - cy) * math.cos(angle))
        for x, y in points
    ]
    scaled = [(cx + (x - cx) * scale, cy + (y - cy) * scale * 0.8) for x, y in points]
    free = [(x + rng.uniform(-8, 8), y + rng.uniform(-8, 8)) for x, y in points]
    return [moved, rotated, scaled, free]


@pytest.mark.parametrize("quality", [False, True])
def test_quad_transform_commit_matches_canvas(app, quality):
    rng = random.Random(11 if quality else 5)
    rgba = _layer_rgba(rng)
    polygon = [(20.0, 14.0), (75.0, 14.0), (75.0, 66.0), (20.0, 66.0)]
    lines = [(0, 0, 0)]
    for mode in ("free", "scale"):
        probe = _canvas_with(rgba)
        probe.selection_polygon = [QPointF(x, y) for x, y in polygon]
        assert probe.begin_selection_transform(mode)
        start = [(p.x(), p.y()) for p in probe.transform_points]
        probe.cancel_selection_transform()
        probe.deleteLater()
        for shape in _shapes(rng, start):
            canvas = _canvas_with(rgba)
            original = pma_native.RgbaImage(
                as_qimage(canvas.active_layer.image).width(),
                as_qimage(canvas.active_layer.image).height(),
                _layer_bytes(canvas),
            )
            canvas.set_transform_quality(quality)
            canvas.set_transform_line_colors(set(lines))
            canvas.selection_polygon = [QPointF(x, y) for x, y in polygon]
            assert canvas.begin_selection_transform(mode)
            canvas.transform_points = [QPointF(x, y) for x, y in shape]
            canvas._invalidate_tp_preview_cache()
            canvas.commit_selection_transform(False)
            session = _begin(
                original, mode, polygon=polygon, quality=quality, line_colors=lines,
            )
            assert session.quality() == quality
            assert session.set_points(shape)
            image, _rect, _before, _after = session.commit()
            assert image.rgba_bytes() == _layer_bytes(canvas), (mode, shape)
            canvas.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("quality", [False, True])
def test_mesh_transform_commit_matches_canvas(app, quality):
    rng = random.Random(3)
    rgba = _layer_rgba(rng)
    polygon = [(20.0, 14.0), (75.0, 14.0), (75.0, 66.0), (20.0, 66.0)]
    for trial in range(3):
        canvas = _canvas_with(rgba)
        original = pma_native.RgbaImage(
            as_qimage(canvas.active_layer.image).width(),
            as_qimage(canvas.active_layer.image).height(),
            _layer_bytes(canvas),
        )
        canvas.set_transform_quality(quality)
        canvas.set_transform_line_colors({(0, 0, 0)})
        canvas.transform_mesh_cols = 3 + trial
        canvas.transform_mesh_rows = 4
        canvas.selection_polygon = [QPointF(x, y) for x, y in polygon]
        assert canvas.begin_selection_transform("mesh")
        shape = [(p.x() + rng.uniform(-6, 6), p.y() + rng.uniform(-6, 6))
                 for p in canvas.transform_points]
        canvas.transform_points = [QPointF(x, y) for x, y in shape]
        canvas._invalidate_tp_preview_cache()
        canvas.commit_selection_transform(False)
        session = _begin(
            original, "mesh", polygon=polygon, quality=quality,
            line_colors=[(0, 0, 0)], mesh_cols=3 + trial, mesh_rows=4,
        )
        assert session.set_points(shape)
        image = session.commit()[0]
        assert image.rgba_bytes() == _layer_bytes(canvas), trial
        canvas.deleteLater()
    app.processEvents()


def test_geometry_matches_canvas(app):
    """ハンドル・回転・外周・ドラッグの判定が Python と同じ。"""
    rng = random.Random(9)
    rgba = _layer_rgba(rng)
    polygon = [(20.0, 14.0), (75.0, 14.0), (75.0, 66.0), (20.0, 66.0)]
    for mode in ("free", "scale", "mesh"):
        canvas = _canvas_with(rgba)
        original = pma_native.RgbaImage(
            as_qimage(canvas.active_layer.image).width(),
            as_qimage(canvas.active_layer.image).height(), _layer_bytes(canvas),
        )
        canvas.selection_polygon = [QPointF(x, y) for x, y in polygon]
        assert canvas.begin_selection_transform(mode)
        session = _begin(original, mode, polygon=polygon)
        canvas.zoom = 2.0
        for _ in range(30):
            a = (rng.uniform(10, 85), rng.uniform(5, 75))
            b = (a[0] + rng.uniform(-20, 20), a[1] + rng.uniform(-20, 20))
            started = canvas.begin_transform_drag(QPointF(*a))
            kind = session.begin_drag(a, 2.0)
            assert started == (kind is not None)
            if kind is not None:
                assert (kind[0], kind[1]) == canvas.transform_drag_kind
                if kind[0] == "move":
                    b = (round(b[0]), round(b[1]))
                    a_int = (round(a[0]), round(a[1]))
                    b = (b[0] + a[0] - a_int[0], b[1] + a[1] - a_int[1])
                canvas.update_transform_drag(QPointF(*b))
                canvas.end_transform_drag()
                session.update_drag(b)
                session.end_drag()
            expected = [(p.x(), p.y()) for p in canvas.transform_points]
            for got, want in zip(session.points(), expected):
                assert got == pytest.approx(want, abs=1e-9)
            handle = canvas.transform_rotation_handle()
            assert handle is not None
            assert session.rotation_handle(2.0) == pytest.approx((handle.x(), handle.y()))
            outline = [v for p in canvas.transform_outer_polygon().toList() for v in (p.x(), p.y())]
            got = [v for p in session.outer_polygon() for v in p]
            assert got == pytest.approx(outline, abs=1e-9)
        canvas.rotate_selection_transform(90.0)
        session.rotate(90.0)
        expected = [v for p in canvas.transform_points for v in (p.x(), p.y())]
        assert [v for p in session.points() for v in p] == pytest.approx(expected, abs=1e-9)
        canvas.cancel_selection_transform()
        canvas.deleteLater()
    app.processEvents()


def test_half_pixel_move_does_not_thicken_line(app):
    """#116：0.5 画素ずれる所へドラッグしても、線の太さが変わらない。"""
    rgba = np.zeros((HEIGHT, WIDTH, 4), np.uint8)
    rgba[10:50, 30] = (0, 0, 0, 255)
    original = pma_native.RgbaImage(WIDTH, HEIGHT, rgba.tobytes())
    polygon = [(20.0, 5.0), (40.0, 5.0), (40.0, 55.0), (20.0, 55.0)]
    for quality in (False, True):
        session = _begin(
            original, "free", polygon=polygon, quality=quality, line_colors=[(0, 0, 0)],
        )
        assert session.begin_drag((30.0, 30.0), 8.0) == ("move", -1)
        session.update_drag((33.5, 31.5))
        session.end_drag()
        image = np.frombuffer(session.commit()[0].rgba_bytes(), np.uint8).reshape(HEIGHT, WIDTH, 4)
        moved = np.zeros_like(rgba)
        moved[12:52, 34] = (0, 0, 0, 255)
        assert np.array_equal(image, moved), quality
