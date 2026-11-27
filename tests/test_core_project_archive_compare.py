"""純 Rust の .pman の読み書き（pma_core::project::archive）を Python の経路と突き合わせる。"""

import json
import platform
import struct
import time
import zlib
from io import BytesIO
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage

from paintmaskanimator import project_io
from paintmaskanimator.errors import OperationError
from paintmaskanimator.project_v2 import build_payload, write_snapshot
from pma_native.pma_native import _open_project, _save_project
from python_document_oracle import Document as OracleDocument
from v2_golden_support import CASES, V2_ROOT

DATE_TIME = (2026, 11, 28, 12, 34, 56)
# 戻しの丸めは aarch64 の Qt（NEON）に合わせている。x86 の Qt は半透明の画素が 1 ずれうる。
QT_NEON = platform.machine().lower() in ("arm64", "aarch64")
# tween を持つ見本。Rust は sanitize_spec の順（元のファイルと同じ）で書く。
SORTED_TWEEN_CASES = {"tween-initial-display"}


def _load_oracle(path):
    with patch.object(project_io, "Document", OracleDocument), patch(
            "paintmaskanimator.document.Document", OracleDocument):
        loaded = project_io.load_project(path)
    assert type(loaded.document) is OracleDocument
    return loaded


def _expected(loaded):
    payload, pngs = build_payload(project_io.ProjectSnapshot(
        loaded.metadata, loaded.document, loaded.width, loaded.height,
    ))
    return payload, [(name, image.width, image.height, image.rgba().tobytes())
                     for name, image, _ in pngs]


def _assert_same_document(loaded, document, metadata_json):
    """Rust で開いた文書を Python の build_payload と同じ値・画素に投影できる。"""
    expected, artwork = _expected(loaded)
    payload, images = document.project_v2(metadata_json)
    assert json.dumps(json.loads(payload), ensure_ascii=False, indent=2) == json.dumps(
        expected, ensure_ascii=False, indent=2)
    actual = [(name, image.width, image.height, image.rgba_bytes()) for name, image in images]
    assert [entry[:3] for entry in actual] == [entry[:3] for entry in artwork]
    assert actual == artwork


def _open_both(path):
    loaded = _load_oracle(path)
    document, metadata_json, width, height, version, cache = _open_project(path.read_bytes())
    assert (width, height, version) == (loaded.width, loaded.height, 2)
    # キーの順序まで Python の metadata と同じ。
    assert metadata_json == json.dumps(loaded.metadata, ensure_ascii=False)
    return loaded, document, metadata_json, cache


@pytest.mark.parametrize("case", CASES)
def test_open_matches_python(case):
    loaded, document, metadata_json, _ = _open_both(V2_ROOT / f"{case}.pman")
    _assert_same_document(loaded, document, metadata_json)


def _png(pixels):
    height, width = pixels.shape[:2]
    return project_io.encode_png(QImage(pixels.tobytes(), width, height, width * 4,
                                        QImage.Format.Format_RGBA8888).copy())


def _archive(path, payload, images, *, raw_json=None, extra=()):
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("project.json", raw_json if raw_json is not None else json.dumps(payload))
        for name, data in images.items():
            archive.writestr(name, data)
        for name, data in extra:
            archive.writestr(name, data)
    return path


def _semi_transparent(width, height, seed):
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
    pixels[0, 0] = (255, 255, 255, 255)
    pixels[-1, -1] = (255, 255, 255, 3)
    return pixels


@pytest.mark.skipif(not QT_NEON, reason="Qt の乗算済み形式の戻しは aarch64 の丸めに合わせている")
@pytest.mark.parametrize("mask_format", [1, 2])
@pytest.mark.parametrize("width", [1, 3, 4, 5, 7, 8, 13, 2051])
def test_open_semi_transparent_pixels_match_qt(tmp_path, width, mask_format):
    """半透明の画素の Qt の往復（NEON と行末のスカラー）と、旧形式の白消しが一致する。"""
    height = 3
    images = {"a.png": _png(_semi_transparent(width, height, width)),
              "b.png": _png(_semi_transparent(width, height, width + 100))}
    track = {"cells": [{"number": 1, "image": "a.png"}, {"number": 2, "image": "b.png",
                                                         "initial": "a.png"}],
             "sheet": [1, 2]}
    payload = {"format": "PaintMaskAnimatorProject", "format_version": 2,
               "mask_format": mask_format, "canvas": {"width": width, "height": height},
               "frame_durations": [1, 1],
               "tracks": [track, dict(track, is_paper=True), dict(track, is_draft=True)]}
    path = _archive(tmp_path / "semi.pman", payload, images)
    loaded, document, metadata_json, cache = _open_both(path)
    _assert_same_document(loaded, document, metadata_json)
    # 白消しした画像の PNG はキャッシュに入れない（Python の seeded と同じ）。
    python_cache = project_io.PngCache()
    project_io.load_project(path, python_cache)
    assert sorted(cache.values()) == sorted(python_cache._entries.values())
    assert len(cache) == 2


def _python_error(path):
    with pytest.raises(OperationError) as error:
        project_io.load_project(path)
    return str(error.value)


def _rust_error(path):
    with pytest.raises(ValueError) as error:
        _open_project(path.read_bytes())
    return error.value.args


def _base():
    return {"format": "PaintMaskAnimatorProject", "format_version": 2,
            "canvas": {"width": 2, "height": 1}, "frame_durations": [1],
            "tracks": [{"cells": [{"number": 1, "image": "a.png"}], "sheet": [1]}]}


RED = np.array([[[255, 0, 0, 255], [0, 0, 255, 128]]], dtype=np.uint8)


def _error_cases():
    base = _base()
    good = {"a.png": _png(RED)}
    text = json.dumps(base)
    return {
        "not-object": ("UnsupportedFormat", {}, dict(raw_json="[]")),
        "wrong-format": ("UnsupportedFormat", good, dict(raw_json=text.replace(
            "PaintMaskAnimatorProject", "Other"))),
        "version-bool": ("UnsupportedFormat", good, dict(raw_json=text.replace(
            '"format_version": 2', '"format_version": true'))),
        "version-zero": ("UnsupportedFormat", good, dict(raw_json=text.replace(
            '"format_version": 2', '"format_version": 0'))),
        "newer": ("NewerVersion", good, dict(raw_json=text.replace(
            '"format_version": 2', '"format_version": 3, "saved_app_version": "9.1"'))),
        "newer-huge": ("NewerVersion", good, dict(raw_json=text.replace(
            '"format_version": 2', '"format_version": 123456789012345678901234567890'))),
        "duplicate-key": ("DuplicateKeys", good, dict(raw_json=text.replace(
            '"sheet": [1]', '"sheet": [1], "sheet": [1]'))),
        "nan": ("InvalidStructure", good, dict(raw_json=text.replace(
            '"sheet": [1]', '"sheet": [1], "x": NaN'))),
        "overflow-float": ("InvalidStructure", good, dict(raw_json=text.replace(
            '"sheet": [1]', '"sheet": [1], "x": 1e400'))),
        "bad-sheet": ("InvalidStructure", good, dict(raw_json=text.replace(
            '"sheet": [1]', '"sheet": [2]'))),
        "missing-image": ("MissingImage", {}, {}),
        "wrong-size": ("InvalidStructure", {"a.png": _png(RED[:, :1])}, {}),
        "not-png": ("InvalidStructure", {"a.png": b"x" * 40}, {}),
        "missing-metadata": ("MissingMetadata", good, dict(raw_json=None, skip_json=True)),
    }


@pytest.mark.parametrize("case", sorted(_error_cases()))
def test_open_errors_match_python(tmp_path, case):
    kind, images, options = _error_cases()[case]
    path = tmp_path / f"{case}.pman"
    if options.pop("skip_json", False):
        with ZipFile(path, "w") as archive:
            for name, data in images.items():
                archive.writestr(name, data)
    else:
        _archive(path, _base(), images, **options)
    assert _rust_error(path) == (kind, _python_error(path))


def _rewrite_central(path, edit):
    data = bytearray(path.read_bytes())
    edit(data)
    path.write_bytes(bytes(data))


def test_archive_errors_match_python(tmp_path):
    path = _archive(tmp_path / "dup.pman", _base(), {"a.png": _png(RED), "b.png": _png(RED)})

    def duplicate(data):
        # 2番目の画像の名前を1番目と同じにする（ローカルと中央の両方）。
        start = 0
        while (at := data.find(b"b.png", start)) >= 0:
            data[at] = ord("a")
            start = at + 1
    _rewrite_central(path, duplicate)
    assert _rust_error(path) == ("DuplicateFileNames", _python_error(path))

    path = _archive(tmp_path / "enc.pman", _base(), {"a.png": _png(RED)})

    def encrypt(data):
        central = data.find(b"PK\x01\x02")
        data[central + 8] |= 1
        data[6] |= 1
    _rewrite_central(path, encrypt)
    assert _rust_error(path) == ("Encrypted", _python_error(path))


def _fixed_time():
    return patch.object(time, "localtime", lambda *_: (*DATE_TIME, 0, 0, -1))


def _thumbnail():
    image = QImage(700, 300, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(20, 120, 220, 200))
    return image


def _entries(data):
    with ZipFile(BytesIO(data)) as archive:
        return [(info.filename, info.compress_type, info.date_time, archive.read(info.filename))
                for info in archive.infolist()]


def _phys(png):
    """PNG の pHYs（メートルあたりの点）。"""
    at = 8
    while at < len(png):
        size, kind = struct.unpack(">I4s", png[at:at + 8])
        if kind == b"pHYs":
            x, y, unit = struct.unpack(">IIB", png[at + 8:at + 17])
            assert unit == 1
            return x, y
        at += size + 12
    return None


@pytest.mark.parametrize("cached", [True, False])
@pytest.mark.parametrize("case", CASES)
def test_save_matches_python_bytes(tmp_path, case, cached):
    """開いて保存したアーカイブが Python の write_snapshot とバイト単位で同じになる。"""
    source = V2_ROOT / f"{case}.pman"
    python_cache = project_io.PngCache() if cached else None
    loaded = project_io.load_project(source, python_cache)
    snapshot = project_io.ProjectSnapshot(loaded.metadata, loaded.document, loaded.width,
                                          loaded.height, _thumbnail())
    target = tmp_path / "python.pman"
    with _fixed_time():
        write_snapshot(target, snapshot, python_cache)
    expected = target.read_bytes()
    entries = _entries(expected)
    assert entries[0][0] == project_io.THUMBNAIL_NAME

    document, metadata_json, _, _, _, cache = _open_project(source.read_bytes())
    dots = {_phys(data) for name, _, _, data in entries[1:-1]}
    assert len(dots) == 1
    actual, kept = _save_project(document, metadata_json, DATE_TIME, thumbnail=entries[0][3],
                                 dots_per_meter=dots.pop(), cache=cache if cached else None)
    actual_entries = _entries(actual)
    if case in SORTED_TWEEN_CASES:
        # 製品の文書（Rust の文書を包む窓口）は tween の辞書をキーの昇順で返すため、
        # Python の project.json は tween の中だけ順序が違う。値は同じ。
        assert json.loads(actual_entries[-1][3]) == json.loads(entries[-1][3])
        assert actual_entries[:-1] == entries[:-1]
        assert [entry[:3] for entry in actual_entries] == [entry[:3] for entry in entries]
    else:
        assert actual_entries == entries
        assert actual == expected
    assert len(kept) == len({data for _, _, _, data in entries[1:-1]})
    if cached:
        # 読み込みのキャッシュをそのまま使うので、元の PNG を書き直さない。
        with ZipFile(source) as archive:
            assert all(data == archive.read(name) for name, _, _, data in entries[1:-1])


def test_rust_saved_project_opens_in_python(tmp_path):
    """Rust で保存したものを Python で開くと、Rust で開いた文書と同じになる。"""
    source = V2_ROOT / "tween-initial-display.pman"
    document, metadata_json, *_ = _open_project(source.read_bytes())
    metadata = json.loads(metadata_json)
    metadata["memo"] = "メモ\n\"引用\""
    metadata["current_frame"] = 1
    metadata_json = json.dumps(metadata, ensure_ascii=False)
    data, _ = _save_project(document, metadata_json, DATE_TIME)
    path = tmp_path / "rust.pman"
    path.write_bytes(data)
    loaded = _load_oracle(path)
    assert loaded.metadata["memo"] == metadata["memo"]
    _assert_same_document(loaded, document, json.dumps(loaded.metadata, ensure_ascii=False))
    with ZipFile(path) as archive:
        info = archive.getinfo("project.json")
        assert info.compress_type == ZIP_DEFLATED
        assert all(i.compress_type == ZIP_STORED for i in archive.infolist() if i is not info)
        text = archive.read("project.json")
    payload, _ = build_payload(project_io.ProjectSnapshot(
        loaded.metadata, loaded.document, loaded.width, loaded.height))
    assert text == json.dumps(payload, ensure_ascii=False, indent=2).encode()
    assert zlib.crc32(text) == info.CRC


def test_save_errors(tmp_path):
    source = V2_ROOT / "paper-draft.pman"
    document, metadata_json, *_ = _open_project(source.read_bytes())
    metadata = json.loads(metadata_json)
    metadata["big"] = "x" * project_io.MAX_PROJECT_METADATA_BYTES
    with pytest.raises(ValueError) as error:
        _save_project(document, json.dumps(metadata), DATE_TIME)
    assert error.value.args == ("MetadataTooLarge", "プロジェクト情報が大きすぎます。")
    with pytest.raises(ValueError) as error:
        _save_project(document, json.dumps({**metadata, "big": float("nan")}), DATE_TIME)
    assert error.value.args[0] == "Invalid"
