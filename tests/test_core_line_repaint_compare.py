"""Compare pma-core's MainLineRepaint with Qt's ``line_ops.main_line_repaint``.

Qt は選んだレイヤーを隠し、すぐ上に Paint・LINE の列を入れて、元の絵ごとに線と塗りへ
分ける。Tauri の画面は pma-core の ``Document::main_line_repaint`` で同じ文書を作るので、
文書グラフ（コマの ID は並びの位置に直す）と v2 の保存内容が一致するかを確かめる。
"""

import json
import random

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QMessageBox

from paintmaskanimator import constants, project_io
from paintmaskanimator.document_live import _blank_handle
from paintmaskanimator.legacy_v1 import LegacyLayerV1, legacy_layer_to_slot
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.models import Frame
from paintmaskanimator.project_v2 import build_payload
from pma_native.pma_native import _CoreDocument

SIZE = 6
METADATA = {
    "format": "PaintMaskAnimatorProject",
    "format_version": 2,
    "fps": 24,
    "memo": "",
    "frames": [],
}
COLOURS = [(0, 0, 0), (255, 0, 0), (0, 0, 255), (255, 255, 255), (10, 200, 30)]


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(constants, "CANVAS_WIDTH", SIZE)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", SIZE)
    w = MainWindow()
    for timer in (w._autosave_timer, w._used_color_timer, w._title_timer):
        timer.stop()
    monkeypatch.setattr(w.used_color, "schedule_refresh", lambda: None)
    monkeypatch.setattr(w.used_color, "refresh_with_counter", lambda *_: None)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *_: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *_: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *_: None))
    yield w
    w._closing = True
    w.deleteLater()


def _image(rng, empty=False):
    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    if empty:
        return image
    for y in range(SIZE):
        for x in range(SIZE):
            if rng.random() < 0.75:
                r, g, b = rng.choice(COLOURS)
                image.setPixelColor(x, y, QColor(r, g, b, rng.choice([255, 255, 255, 128])))
    return image


def _slot(rng, kind, number):
    if kind == "blank":
        return legacy_layer_to_slot(LegacyLayerV1("A", _image(rng, True), sequence_number=number,
                                                  is_blank_key=True))
    if kind == "empty":
        return legacy_layer_to_slot(LegacyLayerV1("A", _image(rng, True)))
    return legacy_layer_to_slot(LegacyLayerV1(
        "A", _image(rng), sequence_number=number, has_content=True,
        exposure=rng.randint(1, 3)))


def _install(window, rng):
    canvas = window.canvas
    count = rng.randint(1, 6)
    slots = []
    number = 1
    for _ in range(count):
        kind = rng.choice(["cell", "cell", "cell", "empty", "blank"])
        slots.append(_slot(rng, kind, number))
        number += 1
    if not any(s.has_content for s in slots):
        slots[0] = _slot(rng, "cell", number)
    frames = [Frame([s]) for s in slots]
    if rng.random() < 0.5:
        for frame in frames:
            frame.layers.append(legacy_layer_to_slot(LegacyLayerV1("B", _image(rng, True))))
    canvas.install_legacy_frames(frames)
    canvas.timeline_mode = "sheet"
    canvas.current_frame = rng.randrange(len(canvas.frames))
    canvas.active_layer_index = rng.randrange(len(canvas.frames[0].layers))
    canvas.undo_stack.clear()
    window.refresh_ui()
    return canvas


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


@pytest.mark.parametrize("seed", range(16))
def test_main_line_repaint_matches_python(window, monkeypatch, seed):
    rng = random.Random(seed)
    canvas = _install(window, rng)
    selected = set(rng.sample(COLOURS, rng.randint(1, 2)))
    monkeypatch.setattr(window.palette, "selected_rgb_set", lambda: set(selected))
    document = canvas._document
    text, images = document._live()["core"].to_graph()
    rust = _CoreDocument.from_graph(text, images)
    rust.set_blank_image(_blank_handle())
    layer = canvas.active_layer_index

    window.line_ops.main_line_repaint()
    result = rust.main_line_repaint(layer, sorted(selected))
    python = document._live()["core"]
    if len(canvas.frames[0].layers) == len(json.loads(text)["tracks"]):
        assert result is None
        return
    assert result is not None
    assert canvas.active_layer_index == layer + 2
    assert _graph(rust) == _graph(python)
    expected, pngs = build_payload(project_io.ProjectSnapshot(
        dict(METADATA), document, SIZE, SIZE,
    ))
    actual, actual_pngs = rust.project_v2(json.dumps(METADATA, ensure_ascii=False))
    assert json.loads(actual) == expected
    assert [(n, i.rgba_bytes()) for n, i in actual_pngs] == [
        (n, image.rgba().tobytes()) for n, image, _ in pngs
    ]


def _solid(rgb):
    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(*rgb))
    return image


def test_cells_with_only_line_or_only_paint_match_python(window, monkeypatch):
    """線だけの絵（塗りが空）と、線の無い絵（LINE が空）、同じ絵を見る保持のコマ。"""
    canvas = window.canvas
    slots = [
        legacy_layer_to_slot(LegacyLayerV1("A", _solid((0, 0, 0)), sequence_number=1,
                                           has_content=True, exposure=2)),
        legacy_layer_to_slot(LegacyLayerV1("A", _solid((255, 0, 0)), sequence_number=2,
                                           has_content=True, exposure=2)),
        legacy_layer_to_slot(LegacyLayerV1("A", _image(random.Random(3)), sequence_number=3,
                                           has_content=True)),
    ]
    canvas.install_legacy_frames([Frame([s]) for s in slots])
    canvas.timeline_mode = "sheet"
    canvas.active_layer_index = 0
    window.refresh_ui()
    monkeypatch.setattr(window.palette, "selected_rgb_set", lambda: {(0, 0, 0)})
    document = canvas._document
    text, images = document._live()["core"].to_graph()
    rust = _CoreDocument.from_graph(text, images)
    rust.set_blank_image(_blank_handle())
    window.line_ops.main_line_repaint()
    assert rust.main_line_repaint(0, [(0, 0, 0)]) is not None
    assert _graph(rust) == _graph(document._live()["core"])
