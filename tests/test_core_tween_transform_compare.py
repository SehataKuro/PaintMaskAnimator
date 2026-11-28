"""Compare pma-core's transform tween with Qt's ``TweenController``.

Tauri の画面は、保持のあるキーを自由／メッシュ変形のトゥイーンにするとき、変形の
始まりの点から今の点までを pma-core（``TransformSession.tween_images``）で割り振り、
``tween_spec`` の設定で ``Document.install_tween`` に渡す。Qt の
``TweenController.enable``・``commit_transform`` と同じ文書になるかを確かめる
（通常生成・逆生成、変形の途中でクオリティを切り替えた場合を含む）。
"""

import json
import random

import numpy as np
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox

from paintmaskanimator import constants, imaging, tween_groups
from paintmaskanimator.document_live import _blank_handle
from paintmaskanimator.legacy_v1 import LegacyLayerV1, legacy_layer_to_slot
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.models import Frame
from paintmaskanimator.pma_image import as_qimage
from pma_native import pma_native as native
from pma_native.pma_native import _CoreDocument

WIDTH, HEIGHT = 40, 32


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(constants, "CANVAS_WIDTH", WIDTH)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", HEIGHT)
    w = MainWindow()
    for timer in (w._autosave_timer, w._used_color_timer, w._title_timer):
        timer.stop()
    monkeypatch.setattr(w.used_color, "schedule_refresh", lambda: None)
    monkeypatch.setattr(w.used_color, "refresh_with_counter", lambda *_: None)
    yield w
    w._closing = True
    w.deleteLater()


def _drawing(rng):
    rgba = np.zeros((HEIGHT, WIDTH, 4), np.uint8)
    left, top = rng.randint(4, 12), rng.randint(4, 10)
    rgba[top:top + 12, left:left + 14] = (200, 40, 40, 255)
    rgba[top + 3:top + 7, left + 2:left + 6] = (255, 255, 255, 255)
    rgba[top - 1:top + 13, left + 6] = (0, 0, 0, 255)
    for _ in range(10):
        rgba[rng.randrange(top, top + 12), rng.randrange(left, left + 14)] = (20, 200, 60, 255)
    return imaging.rgba_array_to_qimage(rgba).convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied)


def _install(window, rng):
    canvas = window.canvas
    exposure = rng.randint(2, 5)
    count = exposure + rng.randint(0, 2)
    key = rng.randint(0, count - exposure)
    image = _drawing(rng)
    frames = []
    for index in range(count):
        if key <= index < key + exposure:
            slot = LegacyLayerV1("A", image.copy(), sequence_number=1, has_content=True)
        else:
            slot = LegacyLayerV1("A", _drawing(rng), sequence_number=2 + index, has_content=True)
        frames.append(Frame([legacy_layer_to_slot(slot)]))
    canvas.install_legacy_frames(frames)
    canvas.timeline_mode = "sheet"
    canvas.current_frame = key
    canvas.active_layer_index = 0
    canvas.undo_stack.clear()
    window.refresh_ui()
    return key, exposure


def _rgba(image):
    image = as_qimage(image).convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = image.width(), image.height()
    rows = np.frombuffer(image.constBits(), np.uint8).reshape(h, image.bytesPerLine())
    return native.RgbaImage(w, h, rows[:, : w * 4].tobytes())


def _graph(core):
    text, images = core.to_graph()
    graph = json.loads(text)
    order = {frame["id"]: index for index, frame in enumerate(graph["frames"])}
    for frame in graph["frames"]:
        frame["id"] = order[frame["id"]]
    graph.pop("next_frame_id")
    pixels = [(i.width, i.height, i.rgba_bytes()) for i in images]

    def resolve(value):
        if isinstance(value, dict):
            return {
                key: (pixels[item] if key in ("image", "initial_image") and isinstance(item, int)
                      else resolve(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    return resolve(graph)


def _shape(rng, points, mode):
    if mode == "mesh":
        return [(x + rng.uniform(-4, 4), y + rng.uniform(-4, 4)) for x, y in points]
    kind = rng.choice(["move", "free"])
    if kind == "move":
        dx, dy = rng.randint(-6, 6), rng.randint(-5, 5)
        return [(x + dx, y + dy) for x, y in points]
    return [(x + rng.uniform(-5, 5), y + rng.uniform(-5, 5)) for x, y in points]


@pytest.mark.parametrize("seed", range(32))
def test_tween_commit_matches_python(window, monkeypatch, seed):
    rng = random.Random(seed)
    key, exposure = _install(window, rng)
    canvas = window.canvas
    mode = rng.choice(["free", "mesh"])
    reverse = rng.random() < 0.5
    quality_start = rng.random() < 0.5
    quality_end = quality_start if rng.random() < 0.5 else not quality_start
    lines = [(0, 0, 0)] if rng.random() < 0.6 else []
    if mode == "mesh":
        window.tools.transform_mesh_grid_x.setValue(rng.randint(2, 4))
        window.tools.transform_mesh_grid_y.setValue(rng.randint(2, 4))
    window.tools.transform_quality.setChecked(quality_start)
    window.palette.selected_rgbs = list(lines)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *_: QMessageBox.StandardButton.Ok))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(
        lambda _parent, _title, message, *_: warnings.append(message)))
    monkeypatch.setattr(tween_groups, "new_id", lambda: "t" + str(seed))

    graph_text, images = canvas._document._live()["core"].to_graph()
    rust = _CoreDocument.from_graph(graph_text, images)
    rust.set_blank_image(_blank_handle())
    rust.set_selection_index(key, 0)  # _start がキーのコマへ移る
    original = _rgba(canvas.frames[key].layers[0].image)

    window.tween.enable(len(canvas.layers) - 1, key, mode)
    assert canvas.transform_active and canvas.tween_pending, warnings
    start = [(p.x(), p.y()) for p in canvas.transform_points]
    final = _shape(rng, start, mode)
    canvas.transform_points = [QPointF(x, y) for x, y in final]
    canvas._invalidate_tp_preview_cache()
    window.tween.set_reverse_generation(reverse)
    window.tools.transform_quality.setChecked(quality_end)
    window.tween.commit_transform()
    assert not warnings
    assert not canvas.tween_pending

    cols = window.tools.transform_mesh_grid_x.value()
    rows = window.tools.transform_mesh_grid_y.value()
    begun = native.TransformSession.begin(
        original, mode, quality=quality_start, line_colors=lines, mesh_cols=cols,
        mesh_rows=rows)
    assert begun is not None
    session = begun[0]
    assert session.points() == pytest.approx(start)
    assert session.set_points(final)
    session.set_quality(quality_end, lines)
    generated = session.tween_images(start, exposure, reverse)
    assert generated is not None
    spec = session.tween_spec("t" + str(seed), exposure, reverse, start)
    numbers = rust.tween_numbers(0, exposure - 1)
    rust.install_tween(key, 0, generated, spec, numbers)
    # 確定のあとは通常生成なら最後のコマ、逆生成ならキーへ移る（publish_tween_commit）。
    rust.set_selection_index(key if reverse else key + exposure - 1, 0)
    assert _graph(rust) == _graph(canvas._document._live()["core"])
