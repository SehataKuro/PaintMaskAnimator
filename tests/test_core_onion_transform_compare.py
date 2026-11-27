"""Compare pma-core's onion transforms with the Python/Qt canvas.

- オニオンのずらし・回転・拡大を掛けた描画と外周の枠（`_draw_onion_range`）を、
  実際の QPainter で描いた画素と比べる。
- ドラッグでオニオンを合わせる操作（`canvas_onion_interaction`）の値を比べる。
- クリックした所のオニオンのコマ（`onion_frame_at`）の判定を比べる。
"""
import os
import random
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QTransform  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import pma_native  # noqa: E402
from paintmaskanimator import constants  # noqa: E402
from paintmaskanimator.canvas import PaintCanvas  # noqa: E402
from paintmaskanimator.document_live import _live_of  # noqa: E402
from paintmaskanimator.models import Frame, Layer  # noqa: E402
from paintmaskanimator.pma_image import PmaImage  # noqa: E402

WIDTH, HEIGHT = 48, 36


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def small_canvas(monkeypatch):
    monkeypatch.setattr(constants, "CANVAS_WIDTH", WIDTH)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", HEIGHT)


def _premul_bytes(image: QImage) -> bytes:
    """ARGB32_Premultiplied の QImage を R, G, B, A の順の乗算済みの画素にする。"""
    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    w, h = image.width(), image.height()
    rows = np.frombuffer(image.constBits(), np.uint8).reshape(h, image.bytesPerLine())
    return rows[:, : w * 4].reshape(h, w, 4)[..., [2, 1, 0, 3]].tobytes()


def _random_onion(rng: random.Random, width: int, height: int) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    for y in range(height):
        for x in range(width):
            if rng.random() < 0.6:
                alpha = rng.choice((255, 255, rng.randint(1, 254)))
                image.setPixelColor(x, y, QColor(
                    rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255), alpha))
    return image


def _matrix(t: QTransform):
    return [t.m11(), t.m12(), t.m13(), t.m21(), t.m22(), t.m23(), t.m31(), t.m32(), t.m33()]


def _side_dict(canvas: PaintCanvas, prefix: str) -> dict[str, Any]:
    return {
        "count": getattr(canvas, f"onion_{prefix}_count"),
        "opacity": getattr(canvas, f"onion_{prefix}_opacity"),
        "color": getattr(canvas, f"onion_{prefix}_color").getRgb()[:3],
        "color_enabled": getattr(canvas, f"onion_{prefix}_color_enabled"),
        "levels": list(getattr(canvas, f"onion_{prefix}_levels")),
        "shift_x": getattr(canvas, f"onion_{prefix}_shift_x"),
        "shift_y": getattr(canvas, f"onion_{prefix}_shift_y"),
        "rotation": getattr(canvas, f"onion_{prefix}_rotation"),
        "scale": getattr(canvas, f"onion_{prefix}_scale"),
    }


def _onion_dict(canvas: PaintCanvas) -> dict[str, Any]:
    return {
        "previous": _side_dict(canvas, "previous"),
        "next": _side_dict(canvas, "next"),
        "all_layers": canvas.onion_all_layers,
        "tu_tb_scale": canvas.onion_tu_tb_scale,
    }


def _randomize_onion(canvas: PaintCanvas, rng: random.Random):
    for prefix in ("previous", "next"):
        setattr(canvas, f"onion_{prefix}_shift_x", rng.uniform(-9, 9))
        setattr(canvas, f"onion_{prefix}_shift_y", rng.uniform(-9, 9))
        setattr(canvas, f"onion_{prefix}_rotation", rng.choice((0.0, 90.0, rng.uniform(-60, 60))))
        setattr(canvas, f"onion_{prefix}_scale", rng.choice((100.0, rng.uniform(60, 160))))


def test_onion_range_matches_qpainter(app, small_canvas, monkeypatch):
    """_draw_onion_range を実際の QPainter で描いた画素と、Rust の描画が同じ。"""
    rng = random.Random(5)
    device_w, device_h = 150, 120
    for trial in range(40):
        canvas = PaintCanvas()
        canvas.zoom = rng.choice((1.0, 1.5, 2.0, rng.uniform(0.8, 2.6)))
        canvas.pan = QPointF(rng.uniform(-10, 40), rng.uniform(-10, 30))
        canvas.rotation = rng.choice((0.0, rng.uniform(-40, 40)))
        canvas.onion_tu_tb_scale = rng.choice((1.0, rng.uniform(0.7, 1.4)))
        _randomize_onion(canvas, rng)
        frames = [3, 5, 6][: rng.randint(1, 3)]
        images = {
            frame: _random_onion(rng, rng.randint(20, 60), rng.randint(16, 50))
            for frame in frames
        }
        direction = rng.choice((-1, 1))
        prefix = "previous" if direction < 0 else "next"
        levels = [rng.uniform(10, 100) for _ in frames]
        setattr(canvas, f"onion_{prefix}_levels", levels)
        opacity = rng.uniform(0.2, 1.0)
        color = QColor(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        monkeypatch.setattr(
            canvas, "_onion_frame_indices", lambda d, c, frames=frames: list(frames))
        monkeypatch.setattr(
            canvas, "_tinted_onion_image",
            lambda frame, *args, images=images, **kwargs: images[frame])
        background = _random_onion(rng, device_w, device_h)
        start = _premul_bytes(background)

        target = QImage(background)
        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        work_rect, canvas_rect = canvas.work_rect(), canvas.canvas_rect()
        center = work_rect.center()
        scale = max(0.0001, float(canvas.onion_tu_tb_scale))
        painter.translate(center)
        painter.rotate(canvas.rotation)
        painter.scale(scale, scale)
        painter.translate(-center)
        base = _matrix(painter.worldTransform())
        canvas._draw_onion_range(
            painter, work_rect, direction, len(frames), opacity, color, False, False,
        )
        painter.end()
        expected = _premul_bytes(target)
        assert expected != start

        side = _side_dict(canvas, prefix)
        side["color"] = (color.red(), color.green(), color.blue())
        rect = (work_rect.x(), work_rect.y(), work_rect.width(), work_rect.height())
        crect = (canvas_rect.x(), canvas_rect.y(), canvas_rect.width(), canvas_rect.height())
        got = start
        for distance, frame in reversed(list(enumerate(frames, start=1))):
            level = max(0.0, min(1.0, levels[distance - 1] / 100.0))
            draw_opacity = max(0.0, min(1.0, opacity * level))
            image = images[frame]
            got = pma_native.onion_draw(
                got, device_w, device_h, base, rect, side, canvas.zoom,
                _premul_bytes(image), image.width(), image.height(), draw_opacity,
            )
        got = pma_native.onion_draw_outline(
            got, device_w, device_h, base, rect, crect, side, canvas.zoom,
        )
        if got != expected:
            diff = np.argwhere(
                np.frombuffer(got, np.uint8).reshape(device_h, device_w, 4)
                != np.frombuffer(expected, np.uint8).reshape(device_h, device_w, 4)
            )
            pytest.fail(f"trial {trial}: {len(diff)} values differ, first {diff[:5].tolist()}")
        canvas.deleteLater()
    app.processEvents()


def test_outline_matches_qpainter(app):
    """1px のコスメティックペンの矩形の枠が、いろいろな行列で Qt と同じ。"""
    rng = random.Random(11)
    width, height = 90, 70
    drawn = 0
    for _ in range(300):
        t = QTransform()
        t.translate(rng.uniform(-10, 60), rng.uniform(-10, 50))
        kind = rng.random()
        if kind < 0.3:
            t.rotate(rng.uniform(-180, 180))
        elif kind < 0.45:
            t.rotate(rng.choice((90.0, 180.0, 270.0)))
        if rng.random() < 0.5:
            s = rng.uniform(0.3, 2.5)
            t.scale(s, s if rng.random() < 0.7 else rng.uniform(0.3, 2.5))
        if rng.random() < 0.1:
            t.shear(rng.uniform(-0.4, 0.4), 0.0)
        rect = QRectF(rng.uniform(-20, 40), rng.uniform(-20, 30),
                      rng.uniform(0.5, 70), rng.uniform(0.5, 60)).adjusted(-0.5, -0.5, 0.5, 0.5)
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setTransform(t)
        color = QColor(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        pen = QPen(color, 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        painter.end()
        side: dict[str, Any] = {"shift_x": 0.0, "shift_y": 0.0, "rotation": 0.0, "scale": 100.0,
                "color": (color.red(), color.green(), color.blue())}
        # 中心 (0, 0)・倍率 1 のオニオンの行列は恒等なので、外周は base の行列で描かれる。
        crect = (rect.x() + 0.5, rect.y() + 0.5, rect.width() - 1.0, rect.height() - 1.0)
        got = pma_native.onion_draw_outline(
            bytes(width * height * 4), width, height, _matrix(t), (0.0, 0.0, 0.0, 0.0),
            crect, side, 1.0,
        )
        assert got == _premul_bytes(image), (_matrix(t), rect)
        drawn += any(got)
    assert drawn > 200


def _view(canvas: PaintCanvas):
    return (canvas.pan.x(), canvas.pan.y(), canvas.rotation, canvas.zoom)


def _values(canvas: PaintCanvas):
    def side(prefix):
        return tuple(
            float(getattr(canvas, f"onion_{prefix}_{key}"))
            for key in ("shift_x", "shift_y", "rotation", "scale")
        )

    return side("previous"), side("next"), float(canvas.onion_tu_tb_scale)


def _close(a, b):
    flat_a = np.asarray([*a[0], *a[1], a[2]], float)
    flat_b = np.asarray([*b[0], *b[1], b[2]], float)
    return np.allclose(flat_a, flat_b, atol=1e-9, rtol=0)


def test_onion_drag_matches_canvas(app):
    """ずらし・キャンバスの移動・回転のドラッグで、オニオンと表示の値が Python と同じ。"""
    rng = random.Random(3)
    for trial in range(60):
        canvas = PaintCanvas()
        canvas.zoom = rng.uniform(0.5, 3.0)
        canvas.rotation = rng.uniform(-90, 90)
        canvas.onion_tu_tb_scale = rng.choice((1.0, rng.uniform(0.6, 1.6)))
        _randomize_onion(canvas, rng)
        kind = rng.choice(("shift", "canvas"))
        if kind == "shift":
            direction = rng.choice((-1, 1))
            axis = rng.choice(("x", "y", "xy", "XY", "z"))
            canvas.begin_onion_shift_interaction(direction, axis)
            interaction = pma_native.OnionInteraction.shift(direction, axis)
        else:
            canvas.begin_onion_canvas_position_interaction()
            interaction = pma_native.OnionInteraction.canvas()
        rotate = rng.random() < 0.5
        start = QPointF(rng.uniform(0, 300), rng.uniform(0, 200))
        onion = _onion_dict(canvas)
        interaction.begin_drag((start.x(), start.y()), rotate, _view(canvas), onion)
        canvas._begin_onion_interaction_drag(
            start,
            Qt.MouseButton.RightButton if rotate else Qt.MouseButton.LeftButton,
        )
        assert interaction.operation() == canvas._onion_interaction_operation
        for _ in range(4):
            point = QPointF(rng.uniform(0, 300), rng.uniform(0, 200))
            before_view, before_onion = _view(canvas), _onion_dict(canvas)
            canvas._update_onion_interaction_drag(point)
            view, values, view_changed, _ = interaction.update_drag(
                (point.x(), point.y()), before_view, before_onion)
            assert view_changed == (kind == "canvas")
            assert _close(values, _values(canvas)), trial
            assert np.allclose(view, _view(canvas), atol=1e-9), trial
        restore = rng.random() < 0.5
        restored, view, values = interaction.cancel(restore, _view(canvas), _onion_dict(canvas))
        canvas.cancel_onion_interaction(restore)
        assert restored == restore
        assert _close(values, _values(canvas))
        assert np.allclose(view, _view(canvas), atol=1e-9)
        target = rng.uniform(-400, 400)
        changed, view, values = pma_native.onion_set_view_rotation(
            target, _view(canvas), _onion_dict(canvas))
        canvas.set_onion_canvas_view_rotation(target)
        assert changed
        assert _close(values, _values(canvas))
        assert np.allclose(view, _view(canvas), atol=1e-9)
        canvas.deleteLater()
    app.processEvents()


def _picker_canvas(rng: np.random.Generator) -> PaintCanvas:
    def image(fill):
        rgba = np.zeros((HEIGHT, WIDTH, 4), np.uint8)
        if fill:
            rgba[..., :3] = (240, 240, 240)
            rgba[..., 3] = 255
        for _ in range(6):
            x, y = rng.integers(0, WIDTH - 8), rng.integers(0, HEIGHT - 8)
            rgba[y:y + rng.integers(2, 8), x:x + rng.integers(2, 8)] = (
                *rng.integers(0, 200, 3), 255)
        result = PmaImage.blank(WIDTH, HEIGHT)
        result.write_rgba(np.ascontiguousarray(rgba))
        return result

    fills = [rng.random() < 0.5, rng.random() < 0.5]
    frames = []
    for number in range(1, 6):
        frames.append(Frame([
            Layer(f"L{index}", image(fills[index]), has_content=True, sequence_number=number)
            for index in range(2)
        ]))
    canvas = PaintCanvas()
    canvas.frames = frames
    return canvas


def test_onion_frame_at_matches_canvas(app, small_canvas):
    """クリックした所のオニオンのコマの判定が Python と同じ。"""
    rng = random.Random(21)
    nrng = np.random.default_rng(21)
    checked = found = 0
    for trial in range(12):
        canvas = _picker_canvas(nrng)
        canvas.onion_skin = True
        canvas.current_frame = rng.randint(0, 4)
        canvas.active_layer_index = rng.randint(0, 1)
        canvas.onion_all_layers = rng.random() < 0.6
        canvas.onion_previous_count = rng.randint(1, 3)
        canvas.onion_next_count = rng.randint(1, 3)
        _randomize_onion(canvas, rng)
        core = _live_of(canvas._document)["state"].core
        onion = _onion_dict(canvas)
        for _ in range(80):
            point = (rng.uniform(-2, WIDTH + 2), rng.uniform(-2, HEIGHT + 2))
            expected = canvas.onion_frame_at(QPointF(*point))
            got = pma_native.onion_frame_at_point(
                core, point, canvas.current_frame, canvas.active_layer_index, True, onion,
            )
            assert got == expected, (trial, point)
            source = canvas._onion_source_point(QPointF(*point), 1)
            assert pma_native.onion_source_position(point, onion["next"], (WIDTH, HEIGHT)) == (
                pytest.approx((source.x(), source.y()), abs=1e-9))
            checked += 1
            found += expected is not None
        assert pma_native.onion_frame_at_point(
            core, (1.0, 1.0), canvas.current_frame, canvas.active_layer_index, False, onion,
        ) is None
        canvas.deleteLater()
    assert found > 20 and checked - found > 20
    app.processEvents()
