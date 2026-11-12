"""#31 §7 の性能確認で軽くした native.resolve_timeline が、書き換え前と同じ結果を返すこと。

参照実装は 853760f の native.resolve_timeline をそのまま写したもの。
全コマ × 全レイヤーで snapshot_metadata（保留の照会つき）を通していた。
"""
from pathlib import Path

import numpy as np
import pytest

from paintmaskanimator import native, project_io
from paintmaskanimator.document import clear_pending, mark_pending
from paintmaskanimator.models import Frame, Layer
from paintmaskanimator.timeline_metadata import snapshot_metadata
from test_p5_6_characterization import qapp as _qapp, window as _window

qapp = _qapp
window = _window

PROJECTS = Path(__file__).parent / "fixtures" / "projects"
FIXTURES = sorted((PROJECTS / "v1").glob("*.pman")) + sorted((PROJECTS / "v2").glob("*.pman"))


def reference_resolve(frames):
    rows = [[snapshot_metadata(layer, resolve=True) for layer in frame.layers]
            for frame in frames]
    layers = max(map(len, rows), default=0)
    data = native._native.resolve_timeline(rows)
    result = np.frombuffer(data, dtype=np.int64).reshape(len(rows), layers)
    overrides = [(column, li, layer) for column, frame in enumerate(frames)
                 for li, layer in enumerate(frame.layers)
                 if "_v2_resolve_source" in layer.__dict__]
    if overrides:
        result = result.copy()
        for column, li, layer in overrides:
            signature, source, original_column, original_count = layer.__dict__["_v2_resolve_source"]
            if ("_dense_import" in layer.__dict__
                    and column == original_column and len(frames) == original_count
                    and signature == (layer.has_content, layer.is_blank_key, layer.sequence_number,
                                      layer.sequence_only, layer.blank_start)):
                result[column, li] = -1 if source is None else source
    return result


def assert_same(frames):
    expected = reference_resolve(frames)
    actual = native.resolve_timeline(frames)
    assert actual.shape == expected.shape
    assert actual.tolist() == expected.tolist()


def slots(frames):
    return [layer for frame in frames for layer in frame.layers]


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_loaded_fixture_matches_reference(qapp, path):
    frames = project_io.load_project(path).document.frames
    assert_same(frames)
    # 保留を付けた位置・外した位置（Track の pending_slots が空でない状態）。
    for layer in slots(frames)[::2]:
        mark_pending(layer)
    assert_same(frames)
    for layer in slots(frames)[::4]:
        clear_pending(layer)
    assert_same(frames)
    # 編集で _dense_import だけが外れ、_v2_resolve_source が残った位置。
    for layer in slots(frames)[1::3]:
        layer.__dict__.pop("_dense_import", None)
    assert_same(frames)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_opened_fixture_matches_reference(window, path):
    assert window.project.open(path)
    assert_same(window.canvas.frames)


def test_dense_import_positions_still_use_the_snapshot(qapp):
    frames = project_io.load_project(PROJECTS / "v2" / "pending-compat.pman").document.frames
    assert any("_dense_import" in layer.__dict__ for layer in slots(frames))
    assert any("_v2_resolve_source" in layer.__dict__ for layer in slots(frames))
    assert_same(frames)


def test_synthetic_pending_and_missing_metadata(qapp):
    from PySide6.QtGui import QImage

    def layer(number=None, blank=False):
        item = Layer("A", QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied))
        item.has_content = number is not None
        item.sequence_number = number
        item.is_blank_key = blank
        return item

    frames = [Frame([layer(1)]), Frame([layer(1)]), Frame([layer(None, True)]),
              Frame([layer(2)]), Frame([layer(2)]), Frame([layer(3)])]
    assert_same(frames)
    for frame in frames[1::2]:
        frame.layers[0].sequence_only = True  # 未接続の Track では値として持つ
    assert_same(frames)
    # メタデータがまだ無い位置は、どちらも snapshot_metadata が作る。
    frames[4].layers[0].__dict__.pop("_timeline_metadata")
    assert_same(frames)
    assert "_timeline_metadata" in frames[4].layers[0].__dict__
    assert_same([])
    assert_same([Frame([])])
