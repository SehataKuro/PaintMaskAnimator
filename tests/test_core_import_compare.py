"""Compare pma-core's import into the document with Qt's import steps.

Qt の画像の読み込み（``canvas_image_import.import_image_sequence``）・PSD の読み込み
（``main_window_import.psd_dialog``）・レイアウト用紙（``main_window.insert_layout_paper``）
は、空の絵の ``Layer`` を各コマに作って ``insert_track(staged=True)`` で入れ、絵と番号を
置いてから ``install_legacy_frames``（用紙は ``link_layer_tracks``）を通す。Tauri の画面は
pma-core の ``Document::import_image_tracks``・``insert_layout_paper`` で同じ文書を作るので、
文書グラフ（コマの ID は Python の途中の組み直しで振り直されるため並びの位置に直す）と
v2 の保存内容が一致するかを確かめる。
"""

import json
import random

import pytest
from PySide6.QtGui import QColor, QImage

from paintmaskanimator import constants, project_io
from paintmaskanimator.document import Document
from paintmaskanimator.document_live import _blank_handle
from paintmaskanimator.models import Layer
from paintmaskanimator.pma_image import PmaImage, to_pma_image
from paintmaskanimator.project_v2 import build_payload
from paintmaskanimator.utils import blank_image
from pma_native.pma_native import _CoreDocument

WIDTH, HEIGHT = 5, 4
METADATA = {
    "format": "PaintMaskAnimatorProject",
    "format_version": 2,
    "fps": 24,
    "memo": "",
    "frames": [],
}


@pytest.fixture(autouse=True)
def canvas_size(qapp):
    old = constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT
    constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = WIDTH, HEIGHT
    yield
    constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = old


def _image(rng):
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(rng.randrange(256), rng.randrange(256), rng.randrange(256), 255))
    return image


def _start(rng):
    """新しい文書に、ほかの列・コマ・描いた絵を少し足した文書。"""
    doc = Document()
    frames = rng.randint(1, 4)
    doc.ensure_frame_count(frames)
    for _ in range(rng.randint(0, 2)):
        doc.add_track()
    doc.current_frame = rng.randrange(frames)
    return doc


def _python_import(doc, start, tracks):
    """psd_dialog・import_image_sequence と同じ手順（複数の列は PSD）。"""
    doc.current_frame = start
    doc.ensure_frame_count(start + max(len(images) for *_, images in tracks))
    first = len(doc.frames[0].layers)
    for name, draft, visible, opacity, images in tracks:
        index = len(doc.frames[0].layers)
        slots = [
            Layer(name, to_pma_image(blank_image()), is_draft=draft, visible=visible,
                  opacity=opacity)
            for _ in doc.frames
        ]
        doc.insert_track(index, slots, staged=True)
        for offset, image in enumerate(images):
            doc.stage_numbered_drawing(start + offset, index, image, True, offset + 1)
    doc.current_frame = start
    doc.install_legacy_frames(doc.frames)
    doc.active_layer_index = first


def _python_paper(doc, image):
    slots = [
        Layer("レイアウト用紙", to_pma_image(image), is_draft=True, has_content=True)
        for _ in doc.frames
    ]
    doc.insert_track(0, slots, staged=True)
    doc.active_layer_index = 1
    doc.link_layer_tracks()


def _graph(core):
    """文書グラフ。コマの ID は並びの位置に、絵の添字は画素に直す。

    空の絵（透明）を共有するかどうかは Python の窓口の写し方で変わる（保存には出ない）ので、
    共有ではなく画素で比べる。
    """
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


def _rust(doc):
    text, images = doc._live()["core"].to_graph()
    core = _CoreDocument.from_graph(text, images)
    # Qt の空の絵は1枚の雛形を共有する（Python の窓口も Rust の命令へ同じ雛形を渡す）。
    core.set_blank_image(_blank_handle())
    return core


def _compare(python_doc, rust_core):
    assert _graph(rust_core) == _graph(python_doc._live()["core"])
    expected, pngs = build_payload(project_io.ProjectSnapshot(
        dict(METADATA), python_doc, WIDTH, HEIGHT,
    ))
    actual, actual_pngs = rust_core.project_v2(json.dumps(METADATA, ensure_ascii=False))
    assert json.loads(actual) == expected
    assert [(n, i.rgba_bytes()) for n, i in actual_pngs] == [
        (n, image.rgba().tobytes()) for n, image, _ in pngs
    ]


@pytest.mark.parametrize("seed", range(12))
def test_image_sequence_import_matches_python(seed):
    rng = random.Random(seed)
    doc = _start(rng)
    rust = _rust(doc)
    start = doc.current_frame
    count = rng.randint(1, 5)
    tracks = [("連番", rng.random() < 0.5, True, 1.0, [_image(rng) for _ in range(count)])]
    _python_import(doc, start, tracks)
    rust.import_image_tracks(start, [
        (name, draft, visible, opacity, [PmaImage.from_qimage(i)._image for i in images])
        for name, draft, visible, opacity, images in tracks
    ])
    _compare(doc, rust)


@pytest.mark.parametrize("seed", range(8))
def test_psd_layers_import_matches_python(seed):
    rng = random.Random(100 + seed)
    doc = _start(rng)
    rust = _rust(doc)
    start = doc.current_frame
    tracks = [
        (f"L{index}", False, rng.random() < 0.7, rng.choice([1.0, 0.5, 128 / 255]),
         [_image(rng) for _ in range(rng.randint(1, 3))])
        for index in range(rng.randint(1, 3))
    ]
    _python_import(doc, start, tracks)
    rust.import_image_tracks(start, [
        (name, draft, visible, opacity, [PmaImage.from_qimage(i)._image for i in images])
        for name, draft, visible, opacity, images in tracks
    ])
    _compare(doc, rust)


def test_layout_paper_matches_python():
    rng = random.Random(7)
    doc = Document()
    rust = _rust(doc)
    image = _image(rng)
    _python_paper(doc, image)
    rust.insert_layout_paper("レイアウト用紙", PmaImage.from_qimage(image)._image)
    _compare(doc, rust)
