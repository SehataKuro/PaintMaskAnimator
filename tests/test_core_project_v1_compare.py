"""旧形式（format_version 1）の .pman を純 Rust（pma_core::project::archive）で開き、Python と突き合わせる。

開いた文書のグラフ（位置の互換情報まで）、v2 への投影と画素、metadata、PNG のキャッシュ、
開いて保存したアーカイブのバイト列を比べる。
"""

import hashlib
import json
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pytest
from PySide6.QtGui import QImage

from paintmaskanimator import constants, document_core, project_io
from paintmaskanimator.errors import OperationError
from paintmaskanimator.native import _native
from paintmaskanimator.project_v2 import write_snapshot
from pma_native.pma_native import _open_project, _save_project
from python_document_oracle import Document as OracleDocument
from test_core_project_archive_compare import (
    DATE_TIME, QT_NEON, _assert_same_document, _entries, _fixed_time, _phys, _thumbnail)
from v2_golden_support import ROOT

V1_ROOT = ROOT / "v1"
FIXTURES = sorted(path.stem for path in V1_ROOT.glob("*.pman"))


def test_every_v1_fixture_is_covered():
    assert len(FIXTURES) == 14


def _canvas(path):
    """ファイルのキャンバスの大きさ。Python は番号のない配置待ちの空画像を作業領域の大きさで作る。"""
    with ZipFile(path) as archive:
        canvas = json.loads(archive.read("project.json")).get("canvas", {})
    return int(canvas.get("width", 1280)), int(canvas.get("height", 720))


def _load_oracle(path, png_cache=None):
    width, height = _canvas(path)
    with patch.object(project_io, "Document", OracleDocument), patch(
            "paintmaskanimator.document.Document", OracleDocument), patch.object(
            constants, "CANVAS_WIDTH", width), patch.object(constants, "CANVAS_HEIGHT", height):
        loaded = project_io.load_project(path, png_cache)
    assert type(loaded.document) is OracleDocument
    return loaded


def _digest(image):
    return (image.width, image.height, hashlib.sha256(image.rgba_bytes()).hexdigest())


def _resolve_images(value, images):
    """グラフの画像の添字を画素の要約に置き換える（世代の違いは比べない）。"""
    if isinstance(value, dict):
        return {key: (_digest(images[item]) if key in ("image", "initial_image")
                      and isinstance(item, int) else _resolve_images(item, images))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_images(item, images) for item in value]
    return value


def _graph(core):
    text, images = core.to_graph()
    return _resolve_images(json.loads(text), images)


def _open_both(path):
    loaded = _load_oracle(path)
    document, metadata_json, width, height, version, cache = _open_project(path.read_bytes())
    assert (width, height, version) == (loaded.width, loaded.height, 1)
    # キーの順序まで Python の metadata と同じ（v1 は frames・sequence_archive も残す）。
    assert metadata_json == json.dumps(loaded.metadata, ensure_ascii=False)
    return loaded, document, metadata_json, cache


def _assert_same_graph(loaded, document):
    """Rust で開いた文書が、Python で開いた素の文書を Rust へ移したものと同じ。"""
    text, images = document_core.graph_payload(loaded.document, loaded.width, loaded.height)
    expected = _native._CoreDocument.from_graph(text, images)
    assert _graph(document) == _graph(expected)
    assert document.canvas_size() == (loaded.width, loaded.height)
    assert document.selection_index() == (loaded.document.current_frame,
                                          loaded.document.active_layer_index)


def _assert_same_cache(path, cache):
    python_cache = project_io.PngCache()
    _load_oracle(path, python_cache)
    assert sorted(cache.values()) == sorted(python_cache._entries.values())


@pytest.mark.parametrize("case", FIXTURES)
def test_open_matches_python(case):
    path = V1_ROOT / f"{case}.pman"
    loaded, document, metadata_json, cache = _open_both(path)
    _assert_same_graph(loaded, document)
    _assert_same_document(loaded, document, metadata_json)
    _assert_same_cache(path, cache)


def _save_python(path, tmp_path, cached):
    python_cache = project_io.PngCache() if cached else None
    loaded = project_io.load_project(path, python_cache)
    assert loaded.format_version == 1
    snapshot = project_io.ProjectSnapshot(loaded.metadata, loaded.document, loaded.width,
                                          loaded.height, _thumbnail())
    target = tmp_path / "python.pman"
    with _fixed_time():
        write_snapshot(target, snapshot, python_cache)
    return target.read_bytes(), python_cache


def _save_rust(path, entries, cached):
    document, metadata_json, _, _, _, cache = _open_project(path.read_bytes())
    # キャッシュから書いた PNG は元のファイルの解像度のまま。新しく符号化したものの解像度を渡す。
    with ZipFile(path) as source:
        original = {source.read(info.filename) for info in source.infolist()}
    encoded = [data for _, _, _, data in entries[1:-1] if not (cached and data in original)]
    dots = {_phys(data) for data in encoded or [entries[1][3]]}
    assert len(dots) == 1
    actual, kept = _save_project(document, metadata_json, DATE_TIME, thumbnail=entries[0][3],
                                 dots_per_meter=dots.pop(), cache=cache if cached else None)
    return actual, kept


# tween を持つ見本。製品の文書は tween の辞書をキーの昇順で返す（v2 の比較と同じ扱い）。
SORTED_TWEEN_CASES = {"tween-initial"}


def _assert_same_archive(actual, expected, case):
    entries, actual_entries = _entries(expected), _entries(actual)
    if case in SORTED_TWEEN_CASES:
        assert json.loads(actual_entries[-1][3]) == json.loads(entries[-1][3])
        assert actual_entries[:-1] == entries[:-1]
        assert [entry[:3] for entry in actual_entries] == [entry[:3] for entry in entries]
    else:
        assert actual_entries == entries
        assert actual == expected


@pytest.mark.parametrize("cached", [True, False])
@pytest.mark.parametrize("case", FIXTURES)
def test_save_matches_python_bytes(tmp_path, case, cached):
    """旧形式を開いて保存すると、Python と同じく v2 のアーカイブになり、バイト単位で同じ。"""
    path = V1_ROOT / f"{case}.pman"
    expected, python_cache = _save_python(path, tmp_path, cached)
    entries = _entries(expected)
    assert json.loads(entries[-1][3])["format_version"] == 2
    actual, kept = _save_rust(path, entries, cached)
    _assert_same_archive(actual, expected, case)
    if cached:
        # 保存のあとに残る PNG（世代ごと）も Python の PngCache と同じ。
        assert python_cache is not None
        assert sorted(kept.values()) == sorted(python_cache._entries.values())


def test_rust_saved_v1_reopens_in_rust_as_same_projection(tmp_path):
    """Rust で開いて保存した v2 を Rust で開き直すと、同じ v2 に投影される。"""
    path = V1_ROOT / "hidden-retained.pman"
    document, metadata_json, *_ = _open_project(path.read_bytes())
    data, _ = _save_project(document, metadata_json, DATE_TIME)
    reopened, reopened_json, *_ = _open_project(data)
    first = json.loads(document.project_v2(metadata_json)[0])
    second = json.loads(reopened.project_v2(reopened_json)[0])
    assert first == second


# ---------------------------------------------------------------------------
# 見本にない組み合わせ（白消し・用紙と下書き・旧連番保管・値の変換・エラー）
# ---------------------------------------------------------------------------


def _png(pixels):
    height, width = pixels.shape[:2]
    return project_io.encode_png(QImage(pixels.tobytes(), width, height, width * 4,
                                        QImage.Format.Format_RGBA8888).copy())


def _write(path, payload, images, *, raw_json=None):
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("project.json", raw_json if raw_json is not None else json.dumps(payload))
        for name, data in images.items():
            archive.writestr(name, data)
    return path


def _pixels(seed, width=3, height=2, white=True):
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
    if white:
        pixels[0, 0] = (255, 255, 255, 255)
        pixels[-1, -1] = (255, 255, 255, 7)
    return pixels


def _layer(image, **values):
    return {"image": image, **values}


def _base(frames, archive=None, **values):
    payload = {"format": "PaintMaskAnimatorProject", "format_version": 1,
               "canvas": {"width": 3, "height": 2}, "frames": frames, **values}
    if archive is not None:
        payload["sequence_archive"] = archive
    return payload


def _images():
    return {"a.png": _png(_pixels(1)), "b.png": _png(_pixels(2)),
            "c.png": _png(_pixels(3, white=False)), "same.png": _png(_pixels(1))}


def _synthetic_cases():
    opaque = {"a.png": _png(_pixels(1, white=False)), "b.png": _png(_pixels(2, white=False)),
              "c.png": _png(_pixels(3, white=False))}
    return {
        # 白消し（mask_format 1）。用紙と下書きは白消ししない。同じ中身の PNG は1回だけ展開する。
        "white-migration": (_base([
            {"duration": 2, "layers": [
                _layer("a.png", has_content=True, exposure=3, sequence_number=4),
                _layer("same.png", has_content=True, is_paper=True),
                _layer("a.png", has_content=True, is_draft=True, initial="b.png")]},
            {"layers": [_layer("c.png"), _layer("same.png", has_content=True),
                        _layer("b.png", is_draft=True)]},
            {"duration": "4", "layers": [
                _layer("b.png", has_content=True, sequence_only=True, sequence_number=4),
                _layer("c.png", is_blank_key=True, exposure=2),
                _layer("c.png", has_content=True)]},
            {"layers": [_layer("c.png"), _layer("c.png"), _layer("c.png", exposure=2)]},
        ], [{"layer_index": 0, "sequence_number": 7, "image": "b.png", "has_content": False,
             "is_blank_key": True, "cell_name": 5, "initial": "c.png"},
            {"layer_index": 2, "sequence_number": 2, "image": "a.png", "is_draft": True},
            {"layer_index": 1, "sequence_number": 1, "image": "c.png", "opacity": "0.25"}],
            mask_format=1, current_frame=3, active_layer_index=1), _images()),
        # 値の変換（Python の int()・float()・bool()・str()）と、tween の検証。
        "python-values": (_base([
            {"duration": 2.9, "layers": [
                _layer("a.png", name=12, has_content=1, exposure="2", sequence_number=" 3 ",
                       opacity=7, visible=0, color_filter_enabled="yes",
                       color_filter_rgb=[10.7, True, "20", 99], cell_name=1.5,
                       tween={"id": 9, "length": "2", "mode": "mesh", "reverse": 1,
                              "start_points": [[1, 2]], "mesh_cols": 2.5},
                       tween_member=["x", None])]},
            {"layers": [_layer("b.png", has_content=True, sequence_number=3.2,
                               tween_member="9", tween={"id": "", "length": 2})]},
            {"layers": [_layer("c.png", exposure=2, opacity=-0.5, cell_name={"k": "v"})]},
        ], [], mask_format="x", current_frame=1), opaque),
        # 選択位置は範囲に丸める（v2 の保存は丸めない値を拒むので、開くだけを比べる）。
        "selection": (_base([{"layers": [_layer("a.png"), _layer("b.png")]}] * 2,
                            current_frame="7", active_layer_index=-3, mask_format=1.5), opaque),
        # 番号のない絵の採番（旧連番保管・保持番号の上から）と、今のコマの絵をバンクにする。
        "numbering": (_base([
            {"layers": [_layer("a.png", has_content=True), _layer("a.png", is_blank_key=True,
                                                                   sequence_number=5)]},
            {"layers": [_layer("b.png", has_content=True, sequence_number=2, cell_name="x"),
                        _layer("b.png", has_content=True)]},
            {"layers": [_layer("c.png", has_content=True, sequence_number=2, cell_name="y",
                               initial="a.png"),
                        _layer("c.png", has_content=True, sequence_only=True)]},
        ], [{"layer_index": 0, "sequence_number": 6, "image": "c.png"},
            {"layer_index": 1, "sequence_number": 3, "image": "a.png", "has_content": False}],
            mask_format=2, current_frame=2), opaque),
    }


@pytest.mark.skipif(not QT_NEON, reason="Qt の乗算済み形式の戻しは aarch64 の丸めに合わせている")
@pytest.mark.parametrize("case", sorted(_synthetic_cases()))
def test_synthetic_open_and_save_match_python(tmp_path, case):
    payload, images = _synthetic_cases()[case]
    path = _write(tmp_path / f"{case}.pman", payload, images)
    loaded, document, metadata_json, cache = _open_both(path)
    _assert_same_graph(loaded, document)
    _assert_same_document(loaded, document, metadata_json)
    _assert_same_cache(path, cache)
    if case == "selection":
        return
    expected, python_cache = _save_python(path, tmp_path, True)
    actual, kept = _save_rust(path, _entries(expected), True)
    assert python_cache is not None
    assert sorted(kept.values()) == sorted(python_cache._entries.values())
    entries, actual_entries = _entries(expected), _entries(actual)
    # tween の辞書のキーの順序だけは違いうる（値は同じ）。
    assert json.loads(actual_entries[-1][3]) == json.loads(entries[-1][3])
    assert actual_entries[:-1] == entries[:-1]


def _python_error(path):
    with pytest.raises(OperationError) as error:
        project_io.load_project(path)
    return str(error.value)


def _rust_error(path):
    with pytest.raises(ValueError) as error:
        _open_project(path.read_bytes())
    return error.value.args


def _error_cases():
    good = {"a.png": _png(_pixels(1)), "big.png": _png(_pixels(1, width=4))}
    one = [{"layers": [_layer("a.png")]}]
    return {
        "canvas": _base(one, canvas={"width": 0, "height": 2}),
        "no-frames": _base([]),
        "frame-not-object": _base([1]),
        "no-layers": _base([{"layers": []}]),
        "layer-count": _base([{"layers": [_layer("a.png")]},
                              {"layers": [_layer("a.png"), _layer("a.png")]}]),
        "layer-not-object": _base([{"layers": [3]}]),
        "no-image": _base([{"layers": [{"image": ""}]}]),
        "bad-path": _base([{"layers": [_layer("../a.png")]}]),
        "missing-image": _base([{"layers": [_layer("none.png")]}]),
        "image-size": _base([{"layers": [_layer("big.png")]}]),
        "exposure": _base([{"layers": [_layer("a.png", exposure=0)]}]),
        "number": _base([{"layers": [_layer("a.png", sequence_number=10001)]}]),
        "opacity": _base([{"layers": [_layer("a.png", opacity="nan")]}]),
        "initial-path": _base([{"layers": [_layer("a.png", initial="a.txt")]}]),
        "initial-missing": _base([{"layers": [_layer("a.png", initial="x.png")]}]),
        "initial-size": _base([{"layers": [_layer("a.png", initial="big.png")]}]),
        "archive-type": _base(one, {"x": 1}),
        "archive-entry": _base(one, [3]),
        "archive-range": _base(one, [{"layer_index": 1, "sequence_number": 1, "image": "a.png"}]),
        "archive-duplicate": _base(one, [
            {"layer_index": 0, "sequence_number": 1, "image": "a.png"},
            {"layer_index": 0, "sequence_number": 1, "image": "a.png"}]),
        "archive-path": _base(one, [{"layer_index": 0, "sequence_number": 1, "image": None}]),
        "archive-missing": _base(one, [{"layer_index": 0, "sequence_number": 1,
                                        "image": "x.png"}]),
        "archive-size": _base(one, [{"layer_index": 0, "sequence_number": 1,
                                     "image": "big.png"}]),
        "archive-opacity": _base(one, [{"layer_index": 0, "sequence_number": 1,
                                        "image": "a.png", "opacity": float("inf")}]),
        "selection": _base(one, current_frame="x"),
    }, good


@pytest.mark.parametrize("case", sorted(_error_cases()[0]))
def test_open_errors_match_python(tmp_path, case):
    cases, images = _error_cases()
    path = _write(tmp_path / f"{case}.pman", cases[case], images)
    assert _rust_error(path) == ("Legacy", _python_error(path))


def test_python_exceptions_are_invalid(tmp_path):
    """Python で OperationError にならない値の誤りは、Rust では Invalid。"""
    good = {"a.png": _png(_pixels(1))}
    for values in ({"exposure": "x"}, {"color_filter_rgb": 5}, {"opacity": None}):
        path = _write(tmp_path / "bad.pman", _base([{"layers": [_layer("a.png", **values)]}]),
                      good)
        with pytest.raises((TypeError, ValueError)):
            project_io.load_project(path)
        assert _rust_error(path)[0] == "Invalid"
