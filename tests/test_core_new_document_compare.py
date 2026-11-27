"""Compare pma-core's new document (``Document::new_blank``) with Python's ``Document()``.

Qt の「新規作成」（main_window.replace_doc）と起動直後の文書は、``constants.CANVAS_WIDTH``
×``CANVAS_HEIGHT`` で ``Document()``（``make_frame()`` を ``install_legacy_frames`` に通した
もの）を作る。Tauri の画面は pma-core の ``Document::new_blank`` で同じ文書を作るので、
文書グラフと v2 の保存内容が一致するかを確かめる。
"""

import json

import pytest

from paintmaskanimator import constants, project_io
from paintmaskanimator.document import Document
from paintmaskanimator.project_v2 import build_payload
from pma_native.pma_native import _CoreDocument
from python_document_oracle import Document as OracleDocument

SIZES = [(1280, 720), (1920, 1080), (64, 48), (65, 1)]

# 保存の v2 で文書の外から渡す値（Qt の build_project_metadata の一部）。
METADATA = {
    "format": "PaintMaskAnimatorProject",
    "format_version": 2,
    "fps": 24,
    "memo": "",
    "frames": [],
}


@pytest.fixture
def canvas_size(request):
    old = constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT
    constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = request.param
    yield request.param
    constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = old


def _pixels(images):
    # 札（tag）は Python の窓口が Qt の画像属性の表に使う番号で、Rust 単独の文書には無い。
    return [(image.width, image.height, image.rgba_bytes()) for image in images]


@pytest.mark.parametrize("canvas_size", SIZES, indirect=True)
def test_new_document_graph_matches_python(canvas_size):
    width, height = canvas_size
    python = Document()._live()["core"]
    rust = _CoreDocument.new_blank(width, height)
    expected_json, expected_images = python.to_graph()
    actual_json, actual_images = rust.to_graph()
    assert json.loads(actual_json) == json.loads(expected_json)
    assert actual_json == expected_json
    assert _pixels(actual_images) == _pixels(expected_images)
    assert rust.canvas_size() == python.canvas_size() == (width, height)
    assert rust.order() == python.order()
    assert rust.selection_index() == python.selection_index() == (0, 0)


@pytest.mark.parametrize("canvas_size", SIZES, indirect=True)
@pytest.mark.parametrize("document_class", [Document, OracleDocument])
def test_new_document_v2_payload_matches_python(canvas_size, document_class):
    width, height = canvas_size
    expected, pngs = build_payload(project_io.ProjectSnapshot(
        dict(METADATA), document_class(), width, height,
    ))
    actual_json, actual_pngs = _CoreDocument.new_blank(width, height).project_v2(
        json.dumps(METADATA, ensure_ascii=False)
    )
    assert json.loads(actual_json) == expected
    assert [name for name, _ in actual_pngs] == [name for name, _, _ in pngs]


@pytest.mark.parametrize("size", [(0, 10), (10, 0)])
def test_new_document_rejects_empty_size(size):
    with pytest.raises(ValueError):
        _CoreDocument.new_blank(*size)
