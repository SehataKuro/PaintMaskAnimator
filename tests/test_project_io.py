"""Roundtrip tests for the extracted project serializer — no widget needed."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from paintmaskanimator import constants, project_io  # noqa: E402
from paintmaskanimator.errors import OperationError  # noqa: E402
from paintmaskanimator.document import Document  # noqa: E402
from paintmaskanimator.models import make_frame  # noqa: E402


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
    content.image.setPixelColor(0, 0, QColor(255, 255, 255, 255))
    content.image.setPixelColor(1, 0, QColor(0, 0, 255, 255))
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
    rgba = out_content.image.convertToFormat(QImage.Format.Format_RGBA8888)
    assert rgba.pixelColor(0, 0).alpha() == 0  # legacy white erased
    assert rgba.pixelColor(1, 0) == QColor(0, 0, 255, 255)  # colour preserved
    if paper is not None:
        out_paper = next(
            layer for layer in loaded[0].layers
            if getattr(layer, "is_paper", False)
        )
        assert out_paper.image.pixelColor(
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
    draft.image.setPixelColor(0, 0, QColor(255, 255, 255, 255))

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
    rgba = out.image.convertToFormat(QImage.Format.Format_RGBA8888)
    assert rgba.pixelColor(0, 0) == QColor(255, 255, 255, 255)


def test_current_save_is_mask_format_2(qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage

    frames = [make_frame()]
    content = next(
        layer for layer in frames[0].layers
        if not getattr(layer, "is_paper", False)
    )
    content.image.setPixelColor(2, 2, QColor(255, 255, 255, 255))
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
    rgba = out_content.image.convertToFormat(QImage.Format.Format_RGBA8888)
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

    monkeypatch.setattr(project_io.zipfile, "ZipFile", BrokenArchive)

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
    # Shared transparent artwork is encoded once per cacheKey.
    cell_count = len({
        layer.image.cacheKey() for frame in frames for layer in frame.layers
    })
    cache = project_io.PngCache()
    calls = _count_png_encodes(monkeypatch)
    path = tmp_path / "cached.pman"

    _write_v2(path, _metadata(), frames, None, cache)
    assert calls["count"] == cell_count

    # 何も変えずに保存し直すと、エンコードは1回も走らない。
    _write_v2(path, _metadata(), frames, None, cache)
    assert calls["count"] == cell_count

    # 描き換えたセルだけがエンコードし直され、その内容が保存される。
    painter = QPainter(frames[1].layers[0].image)
    painter.fillRect(0, 0, 3, 3, QColor("#ff0000"))
    painter.end()
    _write_v2(path, _metadata(), frames, None, cache)
    assert calls["count"] == cell_count + 1
    _meta, loaded, _w, _h = _read_v2(path)
    assert loaded[1].layers[0].image.pixelColor(1, 1) == QColor("#ff0000")
    assert loaded[0].layers[0].image.pixelColor(1, 1) != QColor("#ff0000")


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
    assert reloaded[0].layers[0].image == loaded[0].layers[0].image


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
            painter = QPainter(layer.image)
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
        image = loaded[frame_index].layers[layer_index].image
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
        layer.image.cacheKey()
        for frame in loaded
        for layer in frame.layers
    }
    assert len(keys) <= 3


def test_distinct_cells_still_hit_pixel_limit(qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QColor, QPainter

    frames = [make_frame() for _ in range(4)]
    for index, frame in enumerate(frames):
        layer = next(layer for layer in frame.layers if not layer.is_paper)
        painter = QPainter(layer.image)
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

    original = project_io._decode_png_qt
    calls = []

    def qt_decode(data):
        calls.append(data)
        return original(data)

    monkeypatch.setattr(project_io, "_decode_png_qt", qt_decode)
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
