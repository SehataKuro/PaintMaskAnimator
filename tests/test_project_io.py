"""Roundtrip tests for the extracted project serializer — no widget needed."""
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from paintmaskanimator import constants, project_io  # noqa: E402
from paintmaskanimator.errors import OperationError  # noqa: E402
from paintmaskanimator.document import Document  # noqa: E402
from paintmaskanimator.models import make_frame  # noqa: E402
from image_edit_helpers import edit_qimage
from paintmaskanimator.pma_image import as_qimage


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _metadata():
    return {
        "format": "PaintMaskAnimatorProject",
        "format_version": 1,
        "canvas": {
            "width": int(constants.CANVAS_WIDTH),
            "height": int(constants.CANVAS_HEIGHT),
        },
        "current_frame": 1,
        "active_layer_index": 0,
        "frames": [],
    }


def _write_v2(path, metadata, frames, sequence_archive=None, png_cache=None, thumbnail=None):
    document = Document()
    document.install_legacy_frames(frames, sequence_archive or {}, canvas_size=(
        constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT))
    snapshot = project_io.ProjectSnapshot.capture(metadata, document, thumbnail)
    project_io.write_project_snapshot(path, snapshot, png_cache)


def _read_v2(path, png_cache=None):
    loaded = project_io.load_project(path, png_cache)
    assert loaded.format_version == 2
    return loaded.metadata, loaded.document.frames, loaded.width, loaded.height


def test_write_read_roundtrip(qapp, tmp_path):
    frames = [make_frame(), make_frame()]
    frames[0].layers[0].name = "Custom"
    frames[0].layers[0].visible = False
    frames[0].layers[0].opacity = 0.5
    frames[1].duration = 3

    path = tmp_path / "proj.pmap"
    _write_v2(path, _metadata(), frames)
    assert path.exists()

    metadata, loaded, width, height = _read_v2(path)
    assert (width, height) == (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)
    assert len(loaded) == 2
    assert loaded[0].layers[0].name == "Custom"
    assert loaded[0].layers[0].visible is False
    assert abs(loaded[0].layers[0].opacity - 0.5) < 1e-6
    assert loaded[1].duration == 3
    assert metadata["current_frame"] == 1


def test_legacy_white_migrates_to_transparent(qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage

    frames = [make_frame()]
    # Non-paper content layer with a legacy opaque-white erased pixel plus a
    # real coloured pixel; and the paper layer which must stay white.
    content = next(
        layer for layer in frames[0].layers
        if not getattr(layer, "is_paper", False)
    )
    paper = next(
        (layer for layer in frames[0].layers if getattr(layer, "is_paper", False)),
        None,
    )
    with edit_qimage(content.image) as edited:
        edited.setPixelColor(0, 0, QColor(255, 255, 255, 255))
        edited.setPixelColor(1, 0, QColor(0, 0, 255, 255))
    content.has_content = True

    # Simulate a legacy file: write, then strip the mask_format marker.
    metadata = _metadata()
    path = tmp_path / "legacy.pmap"
    project_io.legacy_fixture_write_archive(path, metadata, frames)

    import json
    import zipfile
    raw = tmp_path / "legacy_raw.pmap"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(raw, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "project.json":
                meta = json.loads(data.decode("utf-8"))
                meta.pop("mask_format", None)
                data = json.dumps(meta).encode("utf-8")
            zout.writestr(info, data)

    _meta, loaded, _w, _h = project_io.read_project_archive(raw)
    out_content = next(
        layer for layer in loaded[0].layers
        if not getattr(layer, "is_paper", False)
    )
    rgba = out_content.image.to_qimage().convertToFormat(QImage.Format.Format_RGBA8888)
    assert rgba.pixelColor(0, 0).alpha() == 0  # legacy white erased
    assert rgba.pixelColor(1, 0) == QColor(0, 0, 255, 255)  # colour preserved
    if paper is not None:
        out_paper = next(
            layer for layer in loaded[0].layers
            if getattr(layer, "is_paper", False)
        )
        assert out_paper.image.to_qimage().pixelColor(
            constants.CANVAS_WIDTH // 2, constants.CANVAS_HEIGHT // 2
        ).alpha() == 255  # paper stays opaque white


def test_draft_layer_roundtrip_preserves_white(qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage

    frames = [make_frame()]
    draft = next(
        layer for layer in frames[0].layers
        if not getattr(layer, "is_paper", False)
    )
    draft.is_draft = True
    draft.has_content = True
    # Draft layers keep imported pixels as-is, including opaque white.
    with edit_qimage(draft.image) as edited:
        edited.setPixelColor(0, 0, QColor(255, 255, 255, 255))

    path = tmp_path / "draft.pmap"
    project_io.legacy_fixture_write_archive(path, _metadata(), frames)

    # Strip the mask_format marker so the legacy white->transparent migration
    # would run for non-draft layers; draft layers must be exempt.
    import json
    import zipfile
    raw = tmp_path / "draft_raw.pmap"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(raw, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "project.json":
                meta = json.loads(data.decode("utf-8"))
                meta.pop("mask_format", None)
                data = json.dumps(meta).encode("utf-8")
            zout.writestr(info, data)

    _meta, loaded, _w, _h = project_io.read_project_archive(raw)
    out = next(
        layer for layer in loaded[0].layers
        if getattr(layer, "is_draft", False)
    )
    assert out.is_draft is True
    rgba = out.image.to_qimage().convertToFormat(QImage.Format.Format_RGBA8888)
    assert rgba.pixelColor(0, 0) == QColor(255, 255, 255, 255)


def test_current_save_is_mask_format_2(qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage

    frames = [make_frame()]
    content = next(
        layer for layer in frames[0].layers
        if not getattr(layer, "is_paper", False)
    )
    with edit_qimage(content.image) as edited:
        edited.setPixelColor(2, 2, QColor(255, 255, 255, 255))
    content.has_content = True

    metadata = dict(_metadata())
    metadata["mask_format"] = project_io.CURRENT_MASK_FORMAT
    path = tmp_path / "modern.pmap"
    _write_v2(path, metadata, frames)

    _meta, loaded, _w, _h = _read_v2(path)
    assert _meta["mask_format"] == 2
    out_content = next(
        layer for layer in loaded[0].layers
        if not getattr(layer, "is_paper", False)
    )
    rgba = out_content.image.to_qimage().convertToFormat(QImage.Format.Format_RGBA8888)
    # No legacy migration on a current-format file: white stays as stored.
    assert rgba.pixelColor(2, 2) == QColor(255, 255, 255, 255)


def test_read_rejects_non_project(qapp, tmp_path):
    import zipfile
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("project.json", '{"format": "Nope", "frames": []}')
    with pytest.raises(OperationError):
        _read_v2(path)


def test_read_rejects_missing_metadata(qapp, tmp_path):
    import zipfile
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("nothing.txt", "x")
    with pytest.raises(OperationError):
        _read_v2(path)


def test_v2_write_builds_tracks_without_mutating_metadata(qapp, tmp_path):
    from copy import deepcopy
    from zipfile import ZipFile
    import json
    md = _metadata()
    before = deepcopy(md)
    path = tmp_path / "p.pmap"
    _write_v2(path, md, [make_frame()])
    assert md == before
    with ZipFile(path) as archive:
        payload = json.loads(archive.read("project.json"))
    assert payload["format_version"] == 2
    assert payload["frame_durations"] == [1]
    assert payload["tracks"][0]["name"]
    assert "frames" not in payload and "sequence_archive" not in payload


def test_failed_write_preserves_existing_project(qapp, tmp_path, monkeypatch):
    path = tmp_path / "important.pmap"
    original = b"existing project data"
    path.write_bytes(original)

    class BrokenArchive:
        def __init__(self, *_args, **_kwargs):
            raise OSError("disk full")

    monkeypatch.setattr(project_io, "_create_project_zip", BrokenArchive)

    with pytest.raises(OSError, match="disk full"):
        _write_v2(path, _metadata(), [make_frame()])

    assert path.read_bytes() == original
    assert not path.with_suffix(".pmap.tmp").exists()


def _count_png_encodes(monkeypatch):
    calls = {"count": 0}
    original = project_io.encode_png

    def counting(image):
        calls["count"] += 1
        return original(image)

    monkeypatch.setattr(project_io, "encode_png", counting)
    return calls


def test_png_cache_reencodes_only_changed_cells(qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QColor, QPainter

    frames = [make_frame(), make_frame()]
    # Named local cells need PNGs even when their pixels are transparent.
    for frame in frames:
        for layer in frame.layers:
            layer.cell_name = "cache-local"
    # The shared blank template requires one encode, as it did with Qt keys.
    cell_count = 1
    cache = project_io.PngCache()
    calls = _count_png_encodes(monkeypatch)
    path = tmp_path / "cached.pman"

    _write_v2(path, _metadata(), frames, None, cache)
    assert calls["count"] == cell_count

    # 何も変えずに保存し直すと、エンコードは1回も走らない。
    _write_v2(path, _metadata(), frames, None, cache)
    assert calls["count"] == cell_count

    # 描き換えたセルだけがエンコードし直され、その内容が保存される。
    with edit_qimage(frames[1].layers[0].image) as patch:
        painter = QPainter(patch)
        painter.fillRect(0, 0, 3, 3, QColor("#ff0000"))
        painter.end()
    _write_v2(path, _metadata(), frames, None, cache)
    assert calls["count"] == cell_count + 1
    _meta, loaded, _w, _h = _read_v2(path)
    assert loaded[1].layers[0].image.to_qimage().pixelColor(1, 1) == QColor("#ff0000")
    assert loaded[0].layers[0].image.to_qimage().pixelColor(1, 1) != QColor("#ff0000")


def test_png_cache_is_seeded_on_read(qapp, tmp_path, monkeypatch):
    frames = [make_frame(), make_frame()]
    path = tmp_path / "seed.pman"
    metadata = _metadata()
    # 旧形式（白→透明の移行対象）でなければ、読み込んだ画素はPNGそのまま。
    metadata["mask_format"] = project_io.CURRENT_MASK_FORMAT
    _write_v2(path, metadata, frames)

    cache = project_io.PngCache()
    _meta, loaded, _w, _h = _read_v2(path, cache)
    calls = _count_png_encodes(monkeypatch)
    # 開いた直後の保存は、読み込んだPNGをそのまま使う。
    _write_v2(
        tmp_path / "copy.pman", _metadata(), loaded, None, cache
    )
    assert calls["count"] == 0
    _meta, reloaded, _w, _h = _read_v2(tmp_path / "copy.pman")
    assert as_qimage(reloaded[0].layers[0].image) == as_qimage(loaded[0].layers[0].image)


def test_png_cache_drops_cells_no_longer_saved(qapp, tmp_path):
    frames = [make_frame(), make_frame()]
    for frame in frames:
        for layer in frame.layers:
            layer.cell_name = "cache-local"
    cache = project_io.PngCache()
    path = tmp_path / "prune.pman"
    _write_v2(path, _metadata(), frames, None, cache)
    before = len(cache)
    _write_v2(path, _metadata(), frames[:1], None, cache)
    assert 0 < len(cache) <= before
    assert len(cache) <= len(frames[0].layers)


def test_parallel_save_and_open_keep_every_cell_distinct(qapp, tmp_path):
    """PNG の書き出し・読み込みはスレッドで並列に行うが、セルが入れ替わらない。"""
    from PySide6.QtGui import QColor, QPainter

    frames = [make_frame() for _ in range(12)]
    expected = {}
    for frame_index, frame in enumerate(frames):
        for layer_index, layer in enumerate(frame.layers):
            if layer.is_paper:
                continue
            color = QColor(10 + frame_index * 20, 30 + layer_index * 40, 200)
            with edit_qimage(layer.image) as patch:
                painter = QPainter(patch)
                painter.fillRect(frame_index * 3, layer_index * 3, 3, 3, color)
                painter.end()
            layer.has_content = True
            expected[(frame_index, layer_index)] = color
    metadata = _metadata()
    metadata["mask_format"] = project_io.CURRENT_MASK_FORMAT
    path = tmp_path / "many.pman"
    _write_v2(path, metadata, frames, None, project_io.PngCache())

    _meta, loaded, _w, _h = _read_v2(path, project_io.PngCache())
    for (frame_index, layer_index), color in expected.items():
        image = loaded[frame_index].layers[layer_index].image.to_qimage()
        assert image.pixelColor(frame_index * 3 + 1, layer_index * 3 + 1) == color
        # 他のセルの印は入っていない。
        other = (frame_index + 1) % len(frames)
        assert image.pixelColor(other * 3 + 1, layer_index * 3 + 1).alpha() == 0


def test_parallel_map_keeps_order():
    assert project_io.parallel_map(lambda value: value * 2, range(50)) == [
        value * 2 for value in range(50)
    ]
    assert project_io.parallel_map(str, []) == []


def test_blank_cells_share_one_image_and_do_not_hit_pixel_limit(qapp, tmp_path, monkeypatch):
    """空のセルは中身が同じなので、展開後の画素数の上限は1枚分しか数えない。"""
    frames = [make_frame() for _ in range(6)]
    metadata = _metadata()
    metadata["mask_format"] = project_io.CURRENT_MASK_FORMAT
    path = tmp_path / "blank.pman"
    project_io.legacy_fixture_write_archive(path, metadata, frames)

    cells = sum(len(frame.layers) for frame in frames)
    pixels = constants.CANVAS_WIDTH * constants.CANVAS_HEIGHT
    # 全セル分では超えるが、中身の種類（空・用紙）の分には収まる上限。
    monkeypatch.setattr(project_io, "MAX_PROJECT_DECODED_PIXELS", pixels * 3)
    assert cells > 3

    _meta, loaded, _w, _h = project_io.read_project_archive(path)
    keys = {
        layer.image.generation
        for frame in loaded
        for layer in frame.layers
    }
    assert len(keys) <= 3


def test_distinct_cells_still_hit_pixel_limit(qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QColor, QPainter

    frames = [make_frame() for _ in range(4)]
    for index, frame in enumerate(frames):
        layer = next(layer for layer in frame.layers if not layer.is_paper)
        with edit_qimage(layer.image) as patch:
            painter = QPainter(patch)
            painter.fillRect(index, 0, 1, 1, QColor("#ff0000"))
            painter.end()
        layer.has_content = True
    path = tmp_path / "distinct.pman"
    _write_v2(path, _metadata(), frames)

    pixels = constants.CANVAS_WIDTH * constants.CANVAS_HEIGHT
    monkeypatch.setattr(project_io, "MAX_PROJECT_DECODED_PIXELS", pixels * 3)
    with pytest.raises(OperationError):
        _read_v2(path)


def test_thumbnail_is_first_stored_entry(qapp, tmp_path):
    """The OS icon extensions read thumbnail.png straight from offset 0."""
    import struct
    import zipfile

    from PySide6.QtGui import QColor, QImage

    image = QImage(1600, 900, QImage.Format.Format_ARGB32)
    image.fill(QColor("crimson"))
    path = tmp_path / "proj.pman"
    _write_v2(path, _metadata(), [make_frame()], thumbnail=image)

    with zipfile.ZipFile(path) as archive:
        first = archive.infolist()[0]
        assert first.filename == project_io.THUMBNAIL_NAME
        assert first.compress_type == zipfile.ZIP_STORED
        thumbnail = QImage.fromData(archive.read(first.filename))
    assert (thumbnail.width(), thumbnail.height()) == (512, 288)

    # What the Swift / Rust readers check in the local file header.
    header = path.read_bytes()[:30]
    signature, _version, flags, method = struct.unpack_from("<IHHH", header)
    assert signature == 0x04034B50 and method == 0 and not flags & 0x8
    size, name_length = struct.unpack_from("<I", header, 18)[0], struct.unpack_from("<H", header, 26)[0]
    assert size == first.file_size and name_length == len(project_io.THUMBNAIL_NAME)

    # Readers that predate the thumbnail ignore the extra entry.
    _metadata_read, frames, _w, _h = _read_v2(path)
    assert len(frames) == 1


def test_no_thumbnail_without_image(qapp, tmp_path):
    import zipfile

    path = tmp_path / "proj.pman"
    _write_v2(path, _metadata(), [make_frame()])
    with zipfile.ZipFile(path) as archive:
        assert project_io.THUMBNAIL_NAME not in archive.namelist()


def test_cells_are_stored_and_loaded_without_a_colour_profile(qapp):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColorSpace, QImage

    tagged = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
    tagged.fill(0xFF102030)
    tagged.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))

    assert b"iCCP" not in project_io.encode_png(tagged)

    # Projects saved before profiles were stripped still hold tagged PNGs.
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert tagged.save(buffer, "PNG")
    buffer.close()
    assert b"iCCP" in bytes(data.data())
    loaded = project_io._decode_png(bytes(data.data()))
    assert not loaded.colorSpace().isValid()
    assert loaded.pixel(0, 0) == tagged.pixel(0, 0)


def _assert_png_matches_qt(data):
    actual = project_io._decode_png(data)
    expected = project_io._decode_png_qt(data)
    assert actual.isNull() == expected.isNull()
    assert (actual.width(), actual.height(), actual.format()) == (
        expected.width(), expected.height(), expected.format())
    assert actual.colorSpace().isValid() == expected.colorSpace().isValid()
    if not actual.isNull():
        assert bytes(actual.constBits()) == bytes(expected.constBits())
    return actual


@pytest.mark.parametrize("width", [1, 2, 31, 63, 64, 65, 127, 128, 129, 200])
def test_png_random_premultiplied_matches_qt(qapp, width):
    import numpy as np
    from PySide6.QtGui import QImage

    rng = np.random.default_rng(3100 + width)
    height = 1 + width % 40
    rgba = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
    image = QImage(rgba.tobytes(), width, height, width * 4,
                   QImage.Format.Format_RGBA8888).convertToFormat(
                       QImage.Format.Format_ARGB32_Premultiplied)
    data = project_io.encode_png(image)
    assert data is not None
    assert project_io.native.decode_png(data, 1 << 30)[:2] == (width, height)
    assert not _assert_png_matches_qt(data).isNull()


def _pillow_png(mode, transparency=False):
    from io import BytesIO
    from PIL import Image

    image = Image.new(mode, (7, 3))
    if mode == "P":
        image.putpalette([10, 20, 30, 90, 120, 150] + [0] * 762)
        image.putdata([i % 2 for i in range(21)])
    elif mode in ("1", "L", "I;16"):
        image.putdata([i * (3000 if mode == "I;16" else 11) for i in range(21)])
    else:
        channels = len(mode)
        image.putdata([tuple((i * 37 + c * 51) % 256 for c in range(channels))
                       for i in range(21)])
    options = {}
    if transparency:
        options["transparency"] = {"P": bytes([0, 127]), "RGB": (0, 51, 102), "L": 22}[mode]
    buffer = BytesIO()
    image.save(buffer, format="PNG", **options)
    return buffer.getvalue()


@pytest.mark.parametrize("mode,transparency", [
    ("1", False), ("L", False), ("L", True), ("LA", False),
    ("P", False), ("P", True), ("RGB", False), ("RGB", True),
    ("RGBA", False), ("I;16", False),
])
def test_png_pillow_matches_qt(qapp, mode, transparency):
    data = _pillow_png(mode, transparency)
    if mode == "I;16":
        with pytest.raises(ValueError):
            project_io.native.decode_png(data, 1 << 30)
    else:
        assert project_io.native.decode_png(data, 1 << 30)[:2] == (7, 3)
    assert not _assert_png_matches_qt(data).isNull()


def test_png_profile_matches_qt(qapp):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColorSpace, QImage

    image = QImage(5, 3, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0x70503010)
    image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()
    assert not _assert_png_matches_qt(bytes(data.data())).colorSpace().isValid()


def test_png_invalid_and_limits(qapp):
    data = _pillow_png("RGBA")
    corrupt = bytearray(data)
    # IHDR の CRC を壊す。
    corrupt[29] ^= 1
    for broken in (b"", data[:8], data[:len(data) // 2], data[:-1], bytes(corrupt)):
        with pytest.raises(ValueError):
            project_io.native.decode_png(broken, 1 << 30)
        _assert_png_matches_qt(broken)
    with pytest.raises(ValueError):
        project_io.native.decode_png(data, 7 * 3 * 4 - 1)
    assert project_io.native.decode_png(data, 7 * 3 * 4)[:2] == (7, 3)


def test_png_interlaced_matches_qt(qapp):
    import struct
    import zlib

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload)))

    width, height = 9, 7
    pixels = [bytes(((x * 37) % 256, (y * 51) % 256, 83, (x * 29 + y * 11) % 256))
              for y in range(height) for x in range(width)]
    rows = bytearray()
    for x0, y0, dx, dy in [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8),
                           (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]:
        for y in range(y0, height, dy):
            rows.append(0)
            for x in range(x0, width, dx):
                rows.extend(pixels[y * width + x])
    data = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 1))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    assert project_io.native.decode_png(data, 1 << 30) == (width, height, b"".join(pixels))
    assert not _assert_png_matches_qt(data).isNull()


def test_png_fallback_and_allocation_limit(qapp, monkeypatch):
    from PySide6.QtGui import QImageReader

    from paintmaskanimator import pma_image_io

    original = pma_image_io._decode_png_qt
    calls = []

    def qt_decode(data):
        calls.append(data)
        return original(data)

    monkeypatch.setattr(pma_image_io, "_decode_png_qt", qt_decode)
    data = _pillow_png("I;16")
    project_io._decode_png(data)
    assert calls == [data]
    seen = []
    decode = project_io.native.decode_png

    def native_decode(data, max_bytes):
        seen.append(max_bytes)
        return decode(data, max_bytes)

    monkeypatch.setattr(project_io.native, "decode_png", native_decode)
    previous = QImageReader.allocationLimit()
    try:
        for mb in (1, 2, 0):
            QImageReader.setAllocationLimit(mb)
            project_io._decode_png(_pillow_png("RGBA"))
        assert seen == [1024 * 1024, 2 * 1024 * 1024, (1 << 64) - 1]
    finally:
        QImageReader.setAllocationLimit(previous)


def _png_chunks(data):
    chunks = []
    offset = 8
    while offset < len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        chunks.append((data[offset + 4:offset + 8], data[offset + 8:offset + 8 + size]))
        offset += size + 12
    return chunks


def _assert_encoded_png_matches_qt(image):
    actual = project_io.encode_png(image)
    expected = project_io._encode_png_qt(image)
    assert actual is not None and expected is not None
    assert project_io.native.decode_png(actual, 1 << 30) == project_io.native.decode_png(expected, 1 << 30)
    a, b = project_io._decode_png(actual), project_io._decode_png(expected)
    assert (a.width(), a.height(), a.format()) == (b.width(), b.height(), b.format())
    assert bytes(a.constBits()) == bytes(b.constBits())
    ac, bc = _png_chunks(actual), _png_chunks(expected)
    assert {kind for kind, _ in ac} == {kind for kind, _ in bc}
    assert [(k, p) for k, p in ac if k in (b"IHDR", b"pHYs")] == [
        (k, p) for k, p in bc if k in (b"IHDR", b"pHYs")]
    assert {k for k, _ in ac} <= {b"IHDR", b"pHYs", b"IDAT", b"IEND"}


@pytest.mark.parametrize("width", [1, 2, 31, 63, 64, 65, 127, 128, 129, 200])
def test_png_encode_random_matches_qt(qapp, width):
    import numpy as np
    from PySide6.QtGui import QImage

    height = 1 + width % 40
    rgba = np.random.default_rng(3200 + width).integers(0, 256, (height, width, 4), dtype=np.uint8)
    image = QImage(rgba.tobytes(), width, height, width * 4,
                   QImage.Format.Format_RGBA8888).convertToFormat(
                       QImage.Format.Format_ARGB32_Premultiplied)
    _assert_encoded_png_matches_qt(image)


@pytest.mark.parametrize("fill", [0, 0xFF123456])
@pytest.mark.parametrize("dpm", [(2835, 2835), (0, 0), (0, 2835), (-1, 2835), (-1, -1)])
def test_png_encode_density_transparency_profile(qapp, fill, dpm):
    from PySide6.QtGui import QColorSpace, QImage

    image = QImage(17, 9, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(fill)
    image.setDotsPerMeterX(dpm[0])
    image.setDotsPerMeterY(dpm[1])
    image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    _assert_encoded_png_matches_qt(image)
    assert b"iCCP" not in {k for k, _ in _png_chunks(project_io.encode_png(image))}


@pytest.mark.parametrize("format_name", ["Format_RGB32", "Format_ARGB32", "Format_Grayscale8", "text"])
def test_png_encode_qt_formats(qapp, monkeypatch, format_name):
    from PySide6.QtGui import QImage

    fmt = (QImage.Format.Format_ARGB32_Premultiplied if format_name == "text"
           else getattr(QImage.Format, format_name))
    image = QImage(13, 7, fmt)
    image.fill(0xFF123456)
    if format_name == "text":
        image.setText("key", "value")

    def unexpected(*args):
        pytest.fail("Qt形式でRustを呼んだ")

    monkeypatch.setattr(project_io.native, "encode_png", unexpected)
    assert project_io.encode_png(image) == project_io._encode_png_qt(image)


def test_png_encode_native_error_falls_back(qapp, monkeypatch):
    from PySide6.QtGui import QImage

    image = QImage(13, 7, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0x70102030)
    calls = []

    def invalid(*args):
        calls.append(args)
        raise ValueError("invalid")

    monkeypatch.setattr(project_io.native, "encode_png", invalid)
    assert project_io.encode_png(image) == project_io._encode_png_qt(image)
    assert len(calls) == 1
    assert isinstance(calls[0][0], bytes)


def test_native_png_encode_invalid_input():
    for w, h, pixels in [(0, 1, b""), (1, 0, b""), (1, 1, b""), (1, 1, b"12345")]:
        with pytest.raises(ValueError):
            project_io.native.encode_png(pixels, w, h, 3780, 3780)


# 段階3-3: ZIP読み取りのRust/zipfile互換性。
def _zip_read_signature(archive):
    return [(i.filename, i.file_size, i.flag_bits) for i in archive.infolist()]


def _assert_zip_read_equal(path):
    import zipfile
    with project_io._RustZipArchive(path) as rust, zipfile.ZipFile(path) as python:
        assert _zip_read_signature(rust) == _zip_read_signature(python)
        for info in python.infolist():
            assert rust.read(info.filename) == python.read(info.filename)
        with pytest.raises(KeyError) as a:
            rust.getinfo("missing")
        with pytest.raises(KeyError) as b:
            python.getinfo("missing")
        assert str(a.value) == str(b.value)
    with pytest.raises(ValueError, match="already closed"):
        rust.read("missing")
    rust.close()
    assert rust.infolist() == [project_io._RustZipInfo(*i) for i in _zip_read_signature(python)]


def test_rust_zip_all_project_fixtures():
    from pathlib import Path
    root = Path(__file__).parent / "fixtures" / "projects"
    paths = sorted(root.glob("v[12]/*.pman"))
    assert paths
    for path in paths:
        _assert_zip_read_equal(path)


def test_rust_zip_saved_project(qapp, tmp_path):
    path = tmp_path / "saved.pman"
    _write_v2(path, _metadata(), [make_frame()])
    _assert_zip_read_equal(path)


def _zip_case(path, case):
    import io
    import struct
    import warnings
    import zipfile
    stream = io.BytesIO()
    compression = {"bzip2": zipfile.ZIP_BZIP2, "stored": zipfile.ZIP_STORED}.get(
        case, zipfile.ZIP_DEFLATED
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(stream, "w", compression=compression) as archive:
            # 有効な最小v1プロジェクト（透明な1画素画像）。
            import json
            from PySide6.QtGui import QImage
            image = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            metadata = {"format": "PaintMaskAnimatorProject", "mask_format": 2,
                        "canvas": {"width": 1, "height": 1},
                        "frames": [{"layers": [{"image": "pixel.png"}]}]}
            archive.writestr("project.json", json.dumps(metadata).encode())
            archive.writestr("extra", b"abcdef" * 10)
            if case == "duplicate":
                archive.writestr("extra", b"duplicate")
            archive.writestr("pixel.png", project_io.encode_png(image))
            if case in {"utf8", "cp437_utf8"}:
                archive.writestr("日本語", b"name")
            if case == "zip64":
                with archive.open("large", "w", force_zip64=True) as output:
                    output.write(b"small ZIP64 entry")
            if case == "comment":
                archive.comment = b"comment"
    data = bytearray(stream.getvalue())
    central = data.index(b"PK\x01\x02")
    if case in ("encrypted", "descriptor"):
        flag = 1 if case == "encrypted" else 8
        for offset in [6, central + 8]:
            struct.pack_into("<H", data, offset, struct.unpack_from("<H", data, offset)[0] | flag)
    if case == "crc":
        struct.pack_into("<I", data, central + 16, 0)
    if case == "oversize":
        struct.pack_into("<I", data, central + 24, 1)
    if case == "cp437":
        # 二番目の名前の先頭だけをCP437に置換（長さは変えない）。
        local2 = data.index(b"PK\x03\x04", 4)
        central2 = data.index(b"PK\x01\x02", central + 4)
        data[local2 + 30] = data[central2 + 46] = 0x82
    if case == "cp437_utf8":
        for signature, flag_offset in [(b"PK\x03\x04", 6), (b"PK\x01\x02", 8)]:
            start = 0
            while (at := data.find(signature, start)) >= 0:
                flags = struct.unpack_from("<H", data, at + flag_offset)[0]
                struct.pack_into("<H", data, at + flag_offset, flags & ~0x800)
                start = at + 4
    if case == "zip64_eocd":
        end = data.index(b"PK\x05\x06")
        _, disk, cd_disk, count_disk, count, size, offset, comment_len = struct.unpack_from(
            "<4s4H2IH", data, end
        )
        zip64_end = struct.pack("<4sQ2H2I4Q", b"PK\x06\x06", 44, 45, 45,
                                disk, cd_disk, count_disk, count, size, offset)
        locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, end, 1)
        legacy = struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0, 65535, 65535,
                             0xffffffff, 0xffffffff, comment_len)
        data = data[:end] + zip64_end + locator + legacy
    if case == "prefix":
        data = b"leading garbage" + data
    if case == "broken":
        data = b"broken zip"
    if case == "empty":
        data = b""
    path.write_bytes(data)


@pytest.mark.parametrize("case", [
    "stored", "utf8", "cp437", "cp437_utf8", "descriptor", "duplicate", "encrypted", "crc",
    "oversize", "prefix", "comment", "zip64", "zip64_eocd", "bzip2", "broken", "empty",
])
def test_rust_zip_project_results_match_zipfile(qapp, tmp_path, monkeypatch, case):
    import zipfile
    path = tmp_path / f"{case}.pman"
    _zip_case(path, case)
    if case not in {"duplicate", "encrypted", "bzip2", "broken", "empty", "crc", "oversize", "cp437_utf8"}:
        _assert_zip_read_equal(path)

    def result():
        try:
            metadata, frames, width, height = project_io.read_project_archive(path)
            # 内部保持オブジェクトを除くメタデータと全レイヤー画素を比較する。
            metadata.pop("_loaded_sequence_archive", None)
            pixels = []
            for frame in frames:
                for layer in frame.layers:
                    image = layer.image.to_qimage()
                    pixels.append((layer.name, layer.visible, layer.opacity,
                                   image.width(), image.height(), bytes(image.constBits())))
            return ("ok", metadata, pixels, width, height)
        except Exception as error:
            return ("error", type(error), str(error))

    actual = result()
    if case not in {"duplicate", "encrypted", "crc", "oversize", "broken", "empty"}:
        assert actual[0] == "ok", actual
    monkeypatch.setattr(project_io, "_open_project_zip", lambda p: zipfile.ZipFile(p, "r"))
    assert actual == result()


def test_rust_zip_falls_back_on_open_error(tmp_path, monkeypatch):
    import zipfile
    path = tmp_path / "fallback.pman"
    _zip_case(path, "comment")
    def unsupported(_path):
        raise ValueError("unsupported")
    monkeypatch.setattr(project_io.native, "ZipReader", unsupported)
    with project_io._open_project_zip(path) as archive:
        assert isinstance(archive, zipfile.ZipFile)
        assert archive.read("extra") == b"abcdef" * 10


def test_rust_zip_missing_path_is_oserror(tmp_path):
    with pytest.raises(FileNotFoundError):
        project_io._open_project_zip(tmp_path / "absent.pman")


@pytest.mark.parametrize("case", ["crc", "oversize"])
def test_rust_zip_read_failure_is_bad_zip(tmp_path, case):
    import zipfile
    path = tmp_path / "bad.pman"
    _zip_case(path, case)
    with project_io._RustZipArchive(path) as archive:
        with pytest.raises(zipfile.BadZipFile):
            archive.read("project.json")


@pytest.mark.parametrize("case", ["duplicate", "encrypted", "bzip2", "cp437_utf8"])
def test_rust_zip_incompatible_archives_use_zipfile(qapp, tmp_path, case):
    import zipfile
    path = tmp_path / "fallback.pman"
    _zip_case(path, case)
    with project_io._open_project_zip(path) as archive:
        assert isinstance(archive, zipfile.ZipFile)


# Rust ZIP writer: small snapshots keep the added coverage inexpensive.
def _assert_zip_writers_equal(tmp_path, monkeypatch, snapshot):
    import struct
    import zipfile
    import time
    from paintmaskanimator import project_v2

    fixed = time.localtime(1_700_000_000)
    monkeypatch.setattr(time, "localtime", lambda *_: fixed)
    rust_path, python_path = tmp_path / "rust.pman", tmp_path / "python.pman"
    project_v2.write_snapshot(rust_path, snapshot)
    with monkeypatch.context() as patch:
        patch.setattr(project_io, "_create_project_zip", lambda p: zipfile.ZipFile(
            p, "w", compression=zipfile.ZIP_DEFLATED))
        project_v2.write_snapshot(python_path, snapshot)
    assert rust_path.stat().st_size <= python_path.stat().st_size
    with zipfile.ZipFile(rust_path) as rust, zipfile.ZipFile(python_path) as python:
        assert rust.namelist() == python.namelist()
        with project_io._RustZipArchive(rust_path) as reader:
            assert [i.filename for i in reader.infolist()] == rust.namelist()
            for a, b in zip(rust.infolist(), python.infolist()):
                for field in ("filename", "file_size", "flag_bits", "date_time", "external_attr", "extra", "compress_type"):
                    assert getattr(a, field) == getattr(b, field), (a.filename, field)
                assert rust.read(a) == python.read(b) == reader.read(a.filename)
        if snapshot.thumbnail is not None:
            first = rust.infolist()[0]
            assert first.filename == project_io.THUMBNAIL_NAME
            assert first.compress_type == zipfile.ZIP_STORED
            header = rust_path.read_bytes()[:30]
            fields = struct.unpack("<4s5H3I2H", header)
            assert fields[2] & 8 == 0
            assert fields[7] == fields[8] == first.file_size
    a, b = project_io.load_project(rust_path), project_io.load_project(python_path)
    assert a.metadata == b.metadata
    assert project_io.build_v2_payload(project_io.ProjectSnapshot(
        a.metadata, a.document, a.width, a.height))[0] == project_io.build_v2_payload(
            project_io.ProjectSnapshot(b.metadata, b.document, b.width, b.height))[0]
    for path in (rust_path, python_path):
        assert project_io.read_project_archive(path, _allow_v2=True).metadata == a.metadata


@pytest.mark.parametrize("thumbnail", [False, True])
def test_rust_zip_writer_snapshot(qapp, tmp_path, monkeypatch, thumbnail):
    from PySide6.QtGui import QImage
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 4)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 3)
    frame = make_frame()
    frame.layers[0].name = "日本語レイヤー"
    frame.layers[0].cell_name = "セル壱"
    document = Document()
    document.install_legacy_frames([frame], {}, canvas_size=(4, 3))
    image = QImage(4, 3, QImage.Format.Format_RGBA8888)
    image.fill(0xff123456)
    snapshot = project_io.ProjectSnapshot.capture(_metadata(), document, image if thumbnail else None)
    _assert_zip_writers_equal(tmp_path, monkeypatch, snapshot)


@pytest.mark.parametrize("fixture", sorted((Path(__file__).parent / "fixtures/projects/v2").glob("*.pman")))
def test_rust_zip_writer_v2_fixtures(qapp, tmp_path, monkeypatch, fixture):
    loaded = project_io.load_project(fixture)
    snapshot = project_io.ProjectSnapshot(loaded.metadata, loaded.document, loaded.width, loaded.height)
    _assert_zip_writers_equal(tmp_path, monkeypatch, snapshot)


def test_rust_zip_writer_failure_cleanup(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 4)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 3)
    path = tmp_path / "existing.pman"
    path.write_bytes(b"original")
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(project_io._RustZipWriter, "writestr", fail)
    with pytest.raises(OSError, match="disk full"):
        _write_v2(path, _metadata(), [make_frame()])
    assert path.read_bytes() == b"original"
    assert not path.with_suffix(".pman.tmp").exists()
    # フォルダを書き込み用に開くと、Windows では IsADirectoryError ではなく
    # PermissionError（os error 5）になる。
    folder_error = PermissionError if os.name == "nt" else IsADirectoryError
    with pytest.raises(folder_error):
        project_io._create_project_zip(tmp_path)


def test_rust_zip_writer_utf8_and_timestamp(tmp_path):
    import zipfile
    writer = project_io.native.ZipWriter(str(tmp_path / "utf8.zip"))
    writer.write("日本語.png", b"abc", False, (2026, 11, 17, 12, 34, 57))
    writer.close()
    writer.close()
    with zipfile.ZipFile(tmp_path / "utf8.zip") as archive:
        info = archive.infolist()[0]
        assert info.flag_bits == 0x800
        assert info.date_time == (2026, 11, 17, 12, 34, 56)
        assert info.external_attr == 0o600 << 16


@pytest.mark.parametrize("version", [1, 2])
def test_save_shares_generations_when_qimage_conversion_returns_copies(
    qapp, tmp_path, monkeypatch, version,
):
    from zipfile import ZipFile

    from PySide6.QtGui import QColor

    from paintmaskanimator.models import Frame, Layer
    from paintmaskanimator.pma_image import PmaImage

    monkeypatch.setattr(constants, "CANVAS_WIDTH", 8)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 8)
    original = PmaImage.blank(8, 8)
    frames = [Frame([Layer(name, original.copy(), cell_name=name)]) for name in ("a", "b")]
    frames[0].layers[0].initial_image = original.copy()
    path = tmp_path / "shared.pman"
    calls = _count_png_encodes(monkeypatch)

    def save():
        if version == 1:
            project_io.legacy_fixture_write_archive(path, _metadata(), frames)
        else:
            _write_v2(path, _metadata(), frames)
        with ZipFile(path) as archive:
            return [(name, archive.read(name)) for name in archive.namelist()]

    expected = save()
    assert calls["count"] == 1
    to_qimage = PmaImage.to_qimage
    monkeypatch.setattr(PmaImage, "to_qimage", lambda image: to_qimage(image).copy())
    assert save() == expected
    assert calls["count"] == 2

    with edit_qimage(frames[1].layers[0].image) as patch:
        patch.setPixelColor(0, 0, QColor("red"))
    assert save() != expected
    assert calls["count"] == 4


@pytest.mark.parametrize("name", ["v1/all-unplaced.pman", "v2/basic-bank-sheet.pman", "v2/paper-draft.pman"])
def test_opened_document_makes_blanks_at_the_file_canvas_size(qapp, monkeypatch, name):
    """作業領域と違う大きさのファイルを開いても、あとで作る空画像はファイルの大きさになる。

    Rust の文書は読み込みの途中で Rust に移した時点の作業領域の大きさを持っていたので、
    開いたあとに作った空キーが開く前の作業領域の大きさ（1280×720）になっていた。
    """
    path = Path(__file__).parent / "fixtures" / "projects" / name
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 1280)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 720)
    loaded = project_io.load_project(path)
    assert (loaded.width, loaded.height) != (1280, 720)
    # アプリ（main_window_project_io）と同じく、読み込みのあとに作業領域を合わせる。
    monkeypatch.setattr(constants, "CANVAS_WIDTH", loaded.width)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", loaded.height)
    document = loaded.document
    if os.environ.get("PMA_CORE_DOCUMENT") != "0":
        # Rust の文書のまま（Python の表に戻っていない）で確かめる。
        assert document.__dict__["_live_state"]["graph"] is None
    document.create_blank_key(0, 0)
    sizes = {(layer.image.width, layer.image.height)
             for frame in document.frames for layer in frame.layers}
    assert sizes == {(loaded.width, loaded.height)}


@pytest.mark.parametrize("live", [False, True])
def test_install_legacy_frames_pads_ragged_frames_at_the_canvas_size(qapp, live):
    """コマごとに列の数がそろわない v1 の内容も、足りない列を指定の大きさの空画像で埋める。

    以前は列の数を入れた変数をキャンバスの幅で上書きし、埋める繰り返しが終わらずに落ちていた。
    """
    from paintmaskanimator import document_live
    from paintmaskanimator.document import GraphDocument
    from paintmaskanimator.models import Frame, Layer
    from paintmaskanimator.pma_image import PmaImage
    frames = [Frame([Layer("A", PmaImage.blank(4, 3)), Layer("B", PmaImage.blank(4, 3))]),
              Frame([Layer("A", PmaImage.blank(4, 3))])]
    document = document_live.Document() if live else GraphDocument()
    document.install_legacy_frames(frames, canvas_size=(4, 3))
    assert [len(frame.layers) for frame in document.frames] == [2, 2]
    padded = document.frames[1].layers[1]
    assert padded.name == "B"
    assert (padded.image.width, padded.image.height) == (4, 3)
    assert padded.__dict__.get("_legacy_missing") is True
