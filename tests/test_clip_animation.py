import os
import sqlite3
import struct
import zlib
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from paintmaskanimator import constants, main_window_import, native
from paintmaskanimator.clip_animation import (
    ClipAnimationDocument,
    ClipAnimationLayer,
    ClipCelKey,
    ClipImportError,
    build_pma_frames,
    _composite,
    read_clip_animation,
)
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.project_io import read_project_archive, write_project_archive


requires_clip_native = pytest.mark.skipif(
    not hasattr(native._native, "ClipAnimationReader"), reason="CLIP Rust codec is not built"
)


def _u32(value):
    return struct.pack("<I", value)


def _binc(keys=((0.0, "1"), (60.0, "2")), extra_keys=None):
    curves = [] if keys is None else [tuple(keys)]
    if extra_keys is not None:
        curves.append(tuple(extra_keys))
    # Keep curve IDs distinct from numeric counts/zeros in this small fixture.
    strings = [f"reserved-{index}" for index in range(32)] + [
        "FCurve",
        "Type",
        "ImageCelName",
        "Frame",
        "Single[]",
        "Value",
        "Tag",
        "String[]",
    ]
    for curve in curves:
        for _frame, tag in curve:
            if tag not in strings:
                strings.append(tag)
    result = bytearray(b"cmt 0100binc" + b"\0" * 4)
    result += _u32(len(strings))
    for value in strings:
        encoded = value.encode("utf-8")
        result.append(len(encoded))
        result += encoded

    def string_id(value):
        return strings.index(value)

    for curve in curves:
        result += _u32(string_id("FCurve")) + _u32(0) + _u32(1)
        result += _u32(string_id("Type")) + _u32(string_id("ImageCelName"))
        result += _u32(3)
        result += _u32(string_id("Frame")) + _u32(string_id("Single[]"))
        result += _u32(len(curve))
        result += b"".join(
            struct.pack("<f", frame) for frame, _tag in curve
        )
        result += _u32(0) + _u32(0)
        result += _u32(string_id("Value")) + _u32(string_id("Single[]"))
        result += _u32(len(curve))
        result += b"".join(
            struct.pack("<f", index) for index in range(len(curve))
        )
        result += _u32(0) + _u32(0)
        result += _u32(string_id("Tag")) + _u32(string_id("String[]"))
        result += _u32(len(curve))
        result += b"".join(_u32(string_id(tag)) for _frame, tag in curve)
        result += _u32(0) + _u32(0)
    return bytes(result)


def _chunk(name, body):
    return b"CHNK" + name + b"\0" * 4 + len(body).to_bytes(4, "big") + body


def _external(identifier, body):
    return (
        len(identifier).to_bytes(8, "big")
        + identifier
        + len(body).to_bytes(8, "big")
        + body
    )


def _utf16be(value):
    encoded = value.encode("utf-16-be")
    return (len(value)).to_bytes(4, "big") + encoded


def _offscreen_attribute(width=2, height=2, packing_type=(1, 4), one_bit=False):
    packing = [0] * 16
    packing[1], packing[2] = packing_type
    packing[3] = sum(packing_type)
    packing[6] = packing[2] * 8 << 5
    packing[8] = (1 if one_bit else 8) << 5
    packing[10] = packing[11] = 256
    packing[14] = int(one_bit)
    if one_bit and packing_type == (1, 1):
        packing = [17, 1, 1, 1, 8192, 1, 32, 1, 32, 65536, 256, 256, 8, 8, 1, 0]
    parameter = _utf16be("Parameter") + b"".join(
        value.to_bytes(4, "big") for value in [width, height, 1, 1, *packing]
    )
    init = _utf16be("InitColor") + struct.pack(">5I", 20, 0, 0, 0, 4)
    blocks = _utf16be("BlockSize") + struct.pack(">4I", 12, 1, 4, 0)
    return struct.pack(">4I", 16, len(parameter), len(init), len(blocks)) + parameter + init + blocks


def _pack_clip(database_path, externals):
    """Complete container and schema, accepted by the upstream validator."""
    connection = sqlite3.connect(database_path)
    connection.executescript("""
        CREATE TABLE Project (ProjectInternalVersion TEXT, ProjectCanvas INTEGER);
        INSERT INTO Project VALUES ('1', 1);
        CREATE TABLE ExternalChunk (ExternalID BLOB, Offset INTEGER);
    """)
    for table, columns in {
        "Canvas": {"CanvasUnit": 0, "CanvasResolution": 72,
                   "CanvasRootFolder": 999999, "CanvasCurrentLayer": 1},
        "Layer": {"CanvasId": 1, "LayerType": 0, "LayerComposite": 0,
                  "LayerLock": 0, "LayerClip": 0, "LayerMasking": 0,
                  "LayerFolder": 0, "LayerVisibility": 1, "LayerLayerMaskMipmap": 0},
        "Offscreen": {"LayerId": 1},
    }.items():
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, default in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} INTEGER DEFAULT {default}")
    connection.execute("UPDATE Layer SET LayerFolder = 1 WHERE LayerFirstChildIndex > 0")
    connection.execute("""INSERT INTO Layer
        (MainId, CanvasId, LayerName, LayerType, LayerOpacity, LayerComposite,
         LayerLock, LayerClip, LayerMasking, LayerFolder, LayerVisibility,
         LayerNextIndex, LayerFirstChildIndex, LayerUuid)
        VALUES (999999, 1, 'Root', 0, 256, 0, 0, 0, 0, 1, 1, 0, 1, ?)
    """, (b'Z' * 16,))
    header = _chunk(b"Head", struct.pack(">QQQ", 256, 0, 16) + b'F' * 16)
    data = bytearray(b"CSFCHUNK" + struct.pack(">QQ", 0, 24) + header)
    for external in externals:
        id_size = int.from_bytes(external[:8], "big")
        identifier = external[8:8 + id_size]
        connection.execute("INSERT INTO ExternalChunk VALUES (?, ?)", (identifier, len(data)))
        data += _chunk(b"Exta", external)
    connection.commit()
    connection.close()
    database_offset = len(data)
    data += _chunk(b"SQLi", database_path.read_bytes()) + _chunk(b"Foot", b"")
    data[8:16] = len(data).to_bytes(8, "big")
    data[48:56] = database_offset.to_bytes(8, "big")
    return bytes(data)


def _raster_container(unpacked, *, packed_gray_alpha=False):
    compressed = zlib.compress(bytes(unpacked))
    block = (
        struct.pack(">IHHIII", 0, 0 if packed_gray_alpha else len(unpacked) // (256 * 256),
                    16384 if packed_gray_alpha else 0, 256, 256, 1)
        + (len(compressed) + 4).to_bytes(4, "big")
        + len(compressed).to_bytes(4, "little") + compressed
    )
    begin = "BlockDataBeginChunk".encode("utf-16-be")
    end = b"\0\0\0\x11" + "BlockDataEndChunk".encode("utf-16-be")
    size = 8 + len(begin) + len(block) + len(end)
    body = size.to_bytes(4, "big") + (19).to_bytes(4, "big") + begin + block + end
    for label in ("BlockStatus", "BlockCheckSum"):
        body += _utf16be(label) + struct.pack(">4I", 12, 1, 4, 0)
    return body


def _raster_body(color=(10, 20, 30), alpha_pixels=None):
    pixel_count = 256 * 256
    alpha = bytearray([255]) * pixel_count
    if alpha_pixels is not None:
        alpha = bytearray(pixel_count)
        for pixel in alpha_pixels:
            alpha[pixel] = 255
    bgra = bytearray(pixel_count * 4)
    red, green, blue = color
    for pixel in range(pixel_count):
        offset = pixel * 4
        bgra[offset:offset + 4] = bytes((blue, green, red, 0))
    return _raster_container(alpha + bgra)


def _alpha_raster_body(alpha_pixels):
    alpha = bytearray(256 * 256)
    for pixel in alpha_pixels:
        alpha[pixel] = 255
    return _raster_container(alpha)


def _one_bit_alpha_raster_body(alpha_pixels):
    packed = bytearray((256 * 256) // 8)
    for pixel in alpha_pixels:
        packed[pixel // 8] |= 1 << (7 - pixel % 8)
    return _raster_container(packed)


def _sqlite_bytes(
    tmp_path, start_frame=0, end_frame=2, include_non_animation_track=False
):
    database_path = tmp_path / "source.sqlite"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE Canvas (
            MainId INTEGER, CanvasWidth REAL, CanvasHeight REAL
        );
        CREATE TABLE TimeLine (
            MainId INTEGER, BankId INTEGER, FrameRate REAL,
            StartFrame REAL, EndFrame REAL, TimeLineName TEXT
        );
        CREATE TABLE AnimationCutBank (
            MainId INTEGER, FirstTimeLine INTEGER, Enable INTEGER
        );
        CREATE TABLE Layer (
            MainId INTEGER, LayerUuid BLOB, LayerName TEXT,
            LayerFirstChildIndex INTEGER, LayerNextIndex INTEGER,
            LayerRenderMipmap INTEGER, AnimationFolder INTEGER,
            LayerOffsetX INTEGER, LayerOffsetY INTEGER,
            LayerRenderOffscrOffsetX INTEGER,
            LayerRenderOffscrOffsetY INTEGER, LayerOpacity INTEGER
        );
        CREATE TABLE Track (
            MainId INTEGER, BankId INTEGER, TrackKind INTEGER,
            TrackActionMixer BLOB, LayerUuidWithTrack BLOB
        );
        CREATE TABLE Mipmap (MainId INTEGER, BaseMipmapInfo INTEGER);
        CREATE TABLE MipmapInfo (MainId INTEGER, Offscreen INTEGER);
        CREATE TABLE Offscreen (
            MainId INTEGER, BlockData BLOB, Attribute BLOB
        );
        """
    )
    mixer_id = b"extrnlid_mixer"
    raster_id = b"extrnlid_raster"
    lower_raster_id = b"extrnlid_lower_raster"
    stale_folder_raster_id = b"extrnlid_stale_folder_raster"
    omitted_empty_raster_id = b"extrnlid_omitted_empty_raster"
    connection.execute("INSERT INTO Canvas VALUES (1, 2, 2)")
    connection.execute(
        "INSERT INTO TimeLine VALUES (1, 7, 24, ?, ?, 'TL')",
        (start_frame, end_frame),
    )
    connection.execute("INSERT INTO AnimationCutBank VALUES (1, 1, 1)")
    connection.execute(
        "INSERT INTO Layer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (1, b"A" * 16, "動画", 2, 0, None, 1, 0, 0, 0, 0, 256),
    )
    connection.execute(
        "INSERT INTO Layer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (2, b"B" * 16, "1", 4, 0, 2, 0, 0, 0, 0, 0, 256),
    )
    connection.execute(
        "INSERT INTO Layer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (5, b"E" * 16, "空", 0, 0, 4, 0, 0, 0, 0, 0, 256),
    )
    connection.execute(
        "INSERT INTO Layer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (3, b"C" * 16, "線", 0, 5, 1, 0, 0, 0, 0, 0, 256),
    )
    connection.execute(
        "INSERT INTO Layer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (4, b"D" * 16, "塗り", 0, 3, 3, 0, 0, 0, 0, 0, 256),
    )
    connection.execute(
        "INSERT INTO Track VALUES (1, 7, 2000, ?, ?)",
        (mixer_id, b"A" * 16),
    )
    if include_non_animation_track:
        connection.execute(
            "INSERT INTO Track VALUES (2, 7, 2001, NULL, ?)",
            (b"B" * 16,),
        )
    connection.execute("INSERT INTO Mipmap VALUES (1, 1)")
    connection.execute("INSERT INTO Mipmap VALUES (2, 2)")
    connection.execute("INSERT INTO Mipmap VALUES (3, 3)")
    connection.execute("INSERT INTO Mipmap VALUES (4, 4)")
    connection.execute("INSERT INTO MipmapInfo VALUES (1, 1)")
    connection.execute("INSERT INTO MipmapInfo VALUES (2, 2)")
    connection.execute("INSERT INTO MipmapInfo VALUES (3, 3)")
    connection.execute("INSERT INTO MipmapInfo VALUES (4, 4)")
    connection.execute(
        "INSERT INTO Offscreen VALUES (1, ?, ?)",
        (raster_id, _offscreen_attribute()),
    )
    connection.execute(
        "INSERT INTO Offscreen VALUES (2, ?, ?)",
        (stale_folder_raster_id, _offscreen_attribute()),
    )
    connection.execute(
        "INSERT INTO Offscreen VALUES (3, ?, ?)",
        (lower_raster_id, _offscreen_attribute()),
    )
    # Sparse/default-filled leaf rasters can keep their database reference
    # while CLIP STUDIO omits the corresponding Exta payload.
    connection.execute(
        "INSERT INTO Offscreen VALUES (4, ?, ?)",
        (omitted_empty_raster_id, _offscreen_attribute()),
    )
    connection.commit()
    connection.close()
    return (
        database_path.read_bytes(),
        mixer_id,
        raster_id,
        lower_raster_id,
        stale_folder_raster_id,
    )


def _clip_file(
    tmp_path,
    *,
    start_frame=0,
    end_frame=2,
    mixer_keys=((0.0, "1"),),
    extra_keys=None,
    include_non_animation_track=False,
):
    (
        _sqlite_data,
        mixer_id,
        raster_id,
        lower_raster_id,
        stale_folder_raster_id,
    ) = _sqlite_bytes(
        tmp_path,
        start_frame,
        end_frame,
        include_non_animation_track,
    )
    compressed_mixer = zlib.compress(_binc(mixer_keys, extra_keys))
    mixer_body = len(compressed_mixer).to_bytes(4, "little") + compressed_mixer
    data = _pack_clip(tmp_path / "source.sqlite", [
        _external(mixer_id, mixer_body),
        _external(raster_id, _raster_body(alpha_pixels={0})),
        _external(lower_raster_id, _raster_body((40, 50, 60))),
        _external(stale_folder_raster_id, _raster_body((200, 0, 0))),
    ])
    path = tmp_path / "animation.clip"
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("one_bit", [False, True])
@requires_clip_native
def test_native_alpha_raster_and_unsupported_bit_depth(tmp_path, one_bit):
    path = _layered_clip_file(tmp_path, {"1": [{
        "name": "line", "raster": _one_bit_alpha_raster_body({0}) if one_bit
        else _alpha_raster_body({0}),
        "packing_type": (1, 0), "one_bit": one_bit,
    }]})
    reader = native.clip_animation_reader(str(path), 16384, 512 * 1024 * 1024)
    from paintmaskanimator.clip_animation import _native_raster
    mipmap = next(row["LayerRenderMipmap"] for row in reader.metadata()["layers"]
                  if row["LayerName"] == "line")
    if one_bit:
        with pytest.raises(ValueError, match="8-bit"):
            _native_raster(reader, mipmap)
    else:
        image, alpha_only, _ = _native_raster(reader, mipmap, (12, 34, 56))
        assert alpha_only
        assert image.pixelColor(0, 0) == QColor(12, 34, 56, 255)
        assert image.pixelColor(1, 0).alpha() == 0


@requires_clip_native
def test_native_csp_monochrome_gray_alpha_planes(tmp_path):
    # MSB-first alpha then gray, with a row stride of 32 packed bytes.
    planes = bytearray(16384)
    for pixel in (0, 1, 256):
        planes[pixel // 8] |= 1 << (7 - pixel % 8)
    for pixel in (1, 257):
        planes[8192 + pixel // 8] |= 1 << (7 - pixel % 8)
    path = _layered_clip_file(tmp_path, {"1": [{
        "name": "mono", "raster": _raster_container(planes, packed_gray_alpha=True),
        "packing_type": (1, 1), "one_bit": True,
    }]})
    image = read_clip_animation(path).layers[0].cell_images["1"]
    assert image.pixelColor(0, 0) == QColor(0, 0, 0, 255)
    assert image.pixelColor(1, 0) == QColor(255, 255, 255, 255)
    assert image.pixelColor(0, 1) == QColor(0, 0, 0, 255)
    assert image.pixelColor(1, 1).alpha() == 0


def test_build_pma_frames_coalesces_holds_and_preserves_cell_names():
    red = QImage(4, 3, QImage.Format.Format_ARGB32_Premultiplied)
    red.fill(QColor("red"))
    blue = QImage(4, 3, QImage.Format.Format_ARGB32_Premultiplied)
    blue.fill(QColor("blue"))
    layer = ClipAnimationLayer(
        "動画A",
        (
            ClipCelKey(0, "1"),
            ClipCelKey(2, "1"),
            ClipCelKey(4, ""),
            ClipCelKey(5, "A"),
        ),
        {"1": red, "A": blue},
    )
    frames = build_pma_frames(4, 3, 0, 7, (layer,))
    assert len(frames) == 8
    assert frames[0].layers[0].exposure == 4
    assert frames[0].layers[0].sequence_number == 1
    assert frames[0].layers[0].cell_name is None
    assert not frames[2].layers[0].has_content
    assert frames[4].layers[0].is_blank_key
    assert frames[4].layers[0].exposure == 1
    assert frames[5].layers[0].has_content
    assert frames[5].layers[0].cell_name == "A"
    assert frames[5].layers[0].sequence_number == 2
    assert frames[5].layers[0].exposure == 3


@requires_clip_native
def test_read_clip_animation_restores_rendered_cell_and_timeline(tmp_path):
    path = _clip_file(tmp_path)
    original = path.read_bytes()
    document = read_clip_animation(path)
    assert document.width == 2
    assert document.height == 2
    assert document.fps == 24
    assert document.start_frame == 0
    assert document.end_frame == 2
    assert document.folder_count == 1
    assert document.frame_count == 3
    key = document.frames[0].layers[0]
    assert key.name == "動画"
    assert key.cell_name is None
    assert key.sequence_number == 1
    assert key.exposure == 3
    assert key.image.pixelColor(0, 0) == QColor(10, 20, 30, 255)
    assert key.image.pixelColor(1, 0) == QColor(40, 50, 60, 255)
    assert path.read_bytes() == original


@requires_clip_native
def test_clip_reader_reports_analysis_cels_and_timeline(tmp_path):
    updates = []
    parsed = read_clip_animation(
        _clip_file(tmp_path), progress=lambda *args: updates.append(args)
    )
    assert updates[0][:2] == (0, 0)
    total = sum(len(layer.cell_images) for layer in parsed.layers)
    counted = [(done, count) for done, count, _ in updates if count]
    assert counted[0] == (0, total)
    assert counted[-1] == (total, total)
    assert [done for done, _ in counted] == sorted(done for done, _ in counted)


@requires_clip_native
def test_read_clip_animation_omits_negative_keys_but_keeps_cell_image(tmp_path):
    path = _clip_file(
        tmp_path,
        start_frame=-8,
        end_frame=2,
        mixer_keys=((-20.0, "1"),),
        include_non_animation_track=True,
    )
    document = read_clip_animation(path)
    assert document.start_frame == 0
    assert document.end_frame == 2
    assert document.folder_count == 1
    assert "1" in document.layers[0].cell_images
    assert not document.frames[0].layers[0].has_content
    assert document.frames[0].layers[0].is_blank_key


@requires_clip_native
def test_invalid_container_does_not_look_like_clip(tmp_path):
    path = tmp_path / "broken.clip"
    path.write_bytes(b"not a clip")
    with pytest.raises(ClipImportError):
        read_clip_animation(path)


def _repack_fixture_clip(path, database_path):
    with sqlite3.connect(database_path) as connection:
        connection.executescript("""
            DROP TABLE Project;
            DROP TABLE ExternalChunk;
            DELETE FROM Layer WHERE MainId = 999999;
        """)
    # Repack the changed database, preserving the fixture's external chunks.
    data = path.read_bytes()
    position = 24
    externals = []
    while position < len(data):
        size = int.from_bytes(data[position + 8:position + 16], "big")
        if data[position + 4:position + 8] == b"Exta":
            externals.append(data[position + 16:position + 16 + size])
        position += 16 + size
    path.write_bytes(_pack_clip(database_path, externals))


@requires_clip_native
def test_unassigned_cel_track_keeps_chain_and_unused_cels(tmp_path):
    path = _clip_file(tmp_path, mixer_keys=None, include_non_animation_track=True)
    database_path = tmp_path / "source.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.executescript("""
            ALTER TABLE TimeLine ADD COLUMN FirstTrack INTEGER;
            UPDATE TimeLine SET FirstTrack = 1;
            ALTER TABLE Track ADD COLUMN TrackNextIndex INTEGER;
            UPDATE Track SET TrackNextIndex = CASE MainId WHEN 1 THEN 2 ELSE 0 END;
        """)
    _repack_fixture_clip(path, database_path)
    original = path.read_bytes()
    parsed = read_clip_animation(path)
    assert parsed.layers[0].keys == ()
    assert "1" in parsed.layers[0].cell_images
    assert parsed.frames[0].layers[0].is_blank_key
    assert parsed.frames[0].layers[0].exposure == 3
    assert path.read_bytes() == original


@requires_clip_native
@pytest.mark.parametrize("selected_timeline", [1, 2])
def test_shared_bank_tracks_belong_to_selected_timeline(tmp_path, selected_timeline):
    path = _clip_file(tmp_path)
    database_path = tmp_path / "source.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.executescript("""
            ALTER TABLE TimeLine ADD COLUMN FirstTrack INTEGER;
            UPDATE TimeLine SET FirstTrack = 1;
            INSERT INTO TimeLine VALUES (2, 7, 30, 0, 4, 'Second', 2);
            ALTER TABLE Track ADD COLUMN ItemId INTEGER;
            ALTER TABLE Track ADD COLUMN TrackNextIndex INTEGER;
            UPDATE Track SET ItemId = 1, TrackNextIndex = 0;
            INSERT INTO Track
                SELECT 2, BankId, TrackKind, TrackActionMixer,
                       LayerUuidWithTrack, 2, 0 FROM Track WHERE MainId = 1;
        """)
        connection.execute("UPDATE AnimationCutBank SET FirstTimeLine = ?", (selected_timeline,))
    _repack_fixture_clip(path, database_path)
    original = path.read_bytes()
    parsed = read_clip_animation(path)
    assert parsed.folder_count == 1
    assert parsed.frame_count == (3 if selected_timeline == 1 else 5)
    assert parsed.fps == (24 if selected_timeline == 1 else 30)
    assert parsed.layers[0].keys == (ClipCelKey(0, "1"),)
    assert path.read_bytes() == original


@requires_clip_native
def test_hidden_tracks_and_ancestors_are_skipped_and_stack_order_is_preserved(tmp_path):
    path = _clip_file(tmp_path)
    database_path = tmp_path / "source.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        template = dict(connection.execute("SELECT * FROM Layer WHERE MainId = 1").fetchone())

        def add_layer(id, name, child=0, next=0, *, animation=False, visible=True, raster=0):
            row = dict(template)
            row.update(MainId=id, LayerName=name, LayerUuid=bytes([id]) * 16,
                       LayerFirstChildIndex=child, LayerNextIndex=next,
                       LayerRenderMipmap=raster, AnimationFolder=int(animation),
                       LayerFolder=int(bool(child)), LayerVisibility=int(visible))
            connection.execute(
                f"INSERT INTO Layer ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                tuple(row.values()),
            )

        # The visible bottom folder's track ID is larger than the top folder's.
        connection.execute("UPDATE Track SET MainId = 100 WHERE MainId = 1")
        connection.execute("UPDATE Layer SET LayerNextIndex = 10 WHERE MainId = 1")
        add_layer(10, "Top", 11, 20, animation=True)
        # Cel visibility is the saved current-frame flag, not a reason to drop
        # a cel explicitly assigned on the imported timeline.
        add_layer(11, "1", visible=False, raster=3)
        add_layer(20, "Hidden", 21, 30, animation=True, visible=False)
        add_layer(21, "1")  # Missing raster must never be decoded.
        add_layer(30, "Hidden parent", 40, visible=False)
        add_layer(40, "Hidden by parent", 41, animation=True)
        add_layer(41, "1")
        for track_id, folder_id in ((2, 10), (3, 20), (4, 40)):
            connection.execute(
                "INSERT INTO Track VALUES (?, 7, 2000, ?, ?)",
                (track_id, b"extrnlid_mixer", bytes([folder_id]) * 16),
            )
    _repack_fixture_clip(path, database_path)
    updates = []
    parsed = read_clip_animation(path, progress=lambda *args: updates.append(args))
    assert [layer.name for layer in parsed.layers] == ["動画", "Top"]
    assert {total for _, total, _ in updates if total} == {2}
    assert parsed.frames[0].layers[1].image.pixelColor(0, 0) == QColor(40, 50, 60, 255)
    composite = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    composite.fill(QColor(0, 0, 0, 0))
    from PySide6.QtGui import QPainter
    painter = QPainter(composite)
    for layer in parsed.frames[0].layers:
        painter.drawImage(0, 0, layer.image)
    painter.end()
    assert composite.pixelColor(0, 0) == QColor(40, 50, 60, 255)


@requires_clip_native
def test_no_visible_animation_folders_reports_clear_error(tmp_path):
    path = _clip_file(tmp_path)
    database_path = tmp_path / "source.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE Layer SET LayerVisibility = 0 WHERE MainId = 1")
    _repack_fixture_clip(path, database_path)
    with pytest.raises(ClipImportError, match="表示中"):
        read_clip_animation(path)


def test_held_frames_use_shared_image_budget(monkeypatch):
    import paintmaskanimator.clip_animation as clip_module

    image = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("red"))
    layer = ClipAnimationLayer("A", (ClipCelKey(0, "1"),), {"1": image})
    monkeypatch.setattr(clip_module, "MAX_PROJECT_DECODED_PIXELS", 8)
    frames = build_pma_frames(2, 2, 0, 120, (layer,))
    assert len(frames) == 121
    assert frames[0].layers[0].exposure == 121
    assert frames[1].layers[0].image.cacheKey() == frames[120].layers[0].image.cacheKey()
    frames[1].layers[0].image.fill(QColor("blue"))
    assert frames[120].layers[0].image.pixelColor(0, 0).alpha() == 0
    monkeypatch.setattr(clip_module, "MAX_PROJECT_DECODED_PIXELS", 7)
    with pytest.raises(ClipImportError, match="PMA"):
        build_pma_frames(2, 2, 0, 120, (layer,))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_canvas_size():
    saved = (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)
    try:
        yield
    finally:
        constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = saved


def test_failed_ui_import_keeps_current_document(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        original_frames = window.canvas.frames
        original_size = (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)

        def fail(_path, *, progress):
            progress(0, 0, "Analyzing")
            assert threading.get_ident() != threading.main_thread().ident
            raise ClipImportError("synthetic failure")

        messages = []
        monkeypatch.setattr(main_window_import, "read_clip_animation", fail)
        monkeypatch.setattr(
            QMessageBox,
            "critical",
            staticmethod(lambda *args: messages.append(args)),
        )
        assert not window.importer.clip_animation(
            tmp_path / "broken.clip", confirm_replace=False
        )
        assert window.canvas.frames is original_frames
        assert (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT) == original_size
        assert messages
        assert window.isEnabled()
        assert not window.importer._clip_import_active
        assert not any(dialog.isVisible() for dialog in window.findChildren(main_window_import._ClipImportDialog))
    finally:
        window.close()


@requires_clip_native
def test_native_decode_failure_does_not_transfer_reader_traceback(qapp, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = _layered_clip_file(tmp_path, {"1": [{
        "name": "unsupported", "raster": _one_bit_alpha_raster_body({0}),
        "packing_type": (1, 0), "one_bit": True,
    }]})
    window = MainWindow()
    try:
        frames = window.canvas.frames
        with pytest.raises(ClipImportError, match="8-bit") as caught:
            window.importer._read_clip_with_progress(path)
        assert caught.value.__cause__ is None
        assert window.canvas.frames is frames
        assert window.isEnabled()
        records = [record for record in caplog.records if "CLIP reader worker failed" in record.message]
        assert records and all(record.exc_info is None for record in records)
    finally:
        window.close()


def test_successful_ui_import_replaces_document_in_sheet_mode(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    image = QImage(3, 2, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("green"))
    unused = QImage(3, 2, QImage.Format.Format_ARGB32_Premultiplied)
    unused.fill(QColor("magenta"))
    source_layer = ClipAnimationLayer(
        "動画B",
        (ClipCelKey(12, "A-7"),),
        {"A-7": image, "B-8": unused},
    )
    frames = build_pma_frames(3, 2, 12, 13, (source_layer,))
    parsed = ClipAnimationDocument(
        "sample.clip", "Main", 3, 2, 24.0, 12, 13,
        (source_layer,), frames,
    )
    progress_bars = []
    heartbeat = []
    observed = []
    reached_progress = threading.Event()

    def read_with_progress(_path, *, progress):
        assert threading.get_ident() != threading.main_thread().ident
        progress(0, 0, "Analyzing")
        progress(1, 2, "Reading cel")
        assert reached_progress.wait(5), "UI did not receive progress during decoding"
        progress(2, 2, "Building timeline")
        return parsed

    monkeypatch.setattr(main_window_import, "read_clip_animation", read_with_progress)
    window = MainWindow()
    timer = QTimer(window)
    timer.setInterval(5)

    def observe_ui():
        heartbeat.append(threading.get_ident())
        if reached_progress.is_set():
            return
        dialog = window.findChild(main_window_import._ClipImportDialog, "clipImportProgressDialog")
        bar = dialog.bar if dialog is not None else None
        if bar is not None and bar.maximum() == 2 and bar.value() == 1:
            progress_bars.append(bar)
            observed.append(dialog.label.text())
            assert dialog.isVisible() and dialog.isModal()
            assert dialog.isEnabled()
            dialog.reject()
            assert dialog.isVisible()
            assert not window.importer.clip_animation(tmp_path / "sample.clip", confirm_replace=False)
            reached_progress.set()

    timer.timeout.connect(observe_ui)
    timer.start()
    try:
        assert window.importer.clip_animation(
            tmp_path / "sample.clip", confirm_replace=False
        )
        assert (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT) == (3, 2)
        assert window.canvas.timeline_mode == "sheet"
        assert window.isEnabled()
        assert not window.importer._clip_import_active
        assert not progress_bars[0].isVisible()
        assert observed == ["Reading cel"]
        assert heartbeat and set(heartbeat) == {threading.get_ident()}
        assert window.timeline.timeline_mode == "sheet"
        assert len(window.canvas.frames) == 2
        assert window.canvas.frames[0].layers[0].cell_name == "A-7"
        assert window.canvas.frames[0].layers[0].sequence_number == 1
        sheet_cell = window.timeline.table.item(0, 0)
        assert sheet_cell is not None and sheet_cell.text() == "A-7"
        metadata = window.canvas.clip_studio_source_metadata
        assert metadata is not None and metadata["start_frame"] == 12
        archived = window.canvas._sequence_archive[(0, 2)]
        assert archived.cell_name == "B-8"
        assert archived.image.pixelColor(0, 0) == QColor("magenta")
        window.timeline_ops.set_mode("sequence")
        sequence_cell = window.timeline.table.item(0, 1)
        assert sequence_cell is not None and sequence_cell.text() == "B-8"
    finally:
        timer.stop()
        window.close()


def test_ui_apply_failure_rolls_back_replaced_document(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    image = QImage(3, 2, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("cyan"))
    source_layer = ClipAnimationLayer(
        "動画", (ClipCelKey(0, "1"),), {"1": image}
    )
    frames = build_pma_frames(3, 2, 0, 0, (source_layer,))
    parsed = ClipAnimationDocument(
        "sample.clip", "", 3, 2, 24.0, 0, 0,
        (source_layer,), frames,
    )
    monkeypatch.setattr(
        main_window_import, "read_clip_animation", lambda _path, **_kwargs: parsed
    )
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *_args: None))
    window = MainWindow()
    try:
        original_frames = window.canvas.frames
        original_size = (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)
        original_archive = window.canvas._sequence_archive

        original_refresh = window.used_color.schedule_refresh
        refresh_calls = 0

        def fail_after_apply():
            nonlocal refresh_calls
            refresh_calls += 1
            if refresh_calls == 1:
                raise RuntimeError("synthetic apply failure")
            return original_refresh()

        monkeypatch.setattr(
            window.used_color, "schedule_refresh", fail_after_apply
        )
        assert not window.importer.clip_animation(
            tmp_path / "sample.clip", confirm_replace=False
        )
        assert window.canvas.frames is original_frames
        assert (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT) == original_size
        assert window.canvas._sequence_archive is original_archive
        assert window.canvas.clip_studio_source_metadata is None
    finally:
        window.close()


def test_cell_name_roundtrips_in_pman(tmp_path):
    image = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("yellow"))
    layer = ClipAnimationLayer(
        "動画", (ClipCelKey(0, "A-1"),), {"A-1": image}
    )
    frames = build_pma_frames(2, 2, 0, 0, (layer,))
    metadata = {
        "format": "PaintMaskAnimatorProject",
        "canvas": {"width": 2, "height": 2},
        "frames": [],
    }
    archived = frames[0].layers[0].clone()
    archived.sequence_number = 2
    archived.cell_name = "未配置A"
    path = tmp_path / "cell-name.pman"
    write_project_archive(
        path,
        metadata,
        frames,
        {(0, 2): archived},
    )
    _metadata, loaded, _width, _height = read_project_archive(path)
    assert loaded[0].layers[0].cell_name == "A-1"
    restored = _metadata["_loaded_sequence_archive"][(0, 2)]
    assert restored.cell_name == "未配置A"
    assert restored.sequence_number == 2
    assert restored.image.pixelColor(0, 0) == QColor("yellow")


_LAYER_COLUMNS = (
    "MainId", "LayerUuid", "LayerName", "LayerFirstChildIndex",
    "LayerNextIndex", "LayerRenderMipmap", "AnimationFolder", "LayerFolder",
    "LayerVisibility", "LayerOpacity", "LayerComposite", "LayerClip",
    "LayerLayerMaskMipmap",
    "DrawColorEnable", "DrawColorMainRed", "DrawColorMainGreen", "DrawColorMainBlue",
    "DrawColorSubRed", "DrawColorSubGreen", "DrawColorSubBlue",
)


def _layered_clip_file(tmp_path, cels):
    """Build a 2x2 .clip whose animation folder holds the given cel trees.

    ``cels`` maps a cel name to a list of layer dicts (top to bottom). A dict
    with ``children`` is a folder; otherwise ``raster`` is a raster body.
    """

    database_path = tmp_path / "layered.sqlite"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE Canvas (MainId INTEGER, CanvasWidth REAL, CanvasHeight REAL);
        CREATE TABLE TimeLine (
            MainId INTEGER, BankId INTEGER, FrameRate REAL,
            StartFrame REAL, EndFrame REAL, TimeLineName TEXT
        );
        CREATE TABLE AnimationCutBank (
            MainId INTEGER, FirstTimeLine INTEGER, Enable INTEGER
        );
        CREATE TABLE Track (
            MainId INTEGER, BankId INTEGER, TrackKind INTEGER,
            TrackActionMixer BLOB, LayerUuidWithTrack BLOB
        );
        CREATE TABLE Mipmap (MainId INTEGER, BaseMipmapInfo INTEGER);
        CREATE TABLE MipmapInfo (MainId INTEGER, Offscreen INTEGER);
        CREATE TABLE Offscreen (MainId INTEGER, BlockData BLOB, Attribute BLOB);
        """
    )
    connection.execute(
        "CREATE TABLE Layer ("
        + ", ".join(
            f"{name} {'BLOB' if name == 'LayerUuid' else 'TEXT' if name == 'LayerName' else 'INTEGER'}"
            for name in _LAYER_COLUMNS
        )
        + ")"
    )
    connection.execute("INSERT INTO Canvas VALUES (1, 2, 2)")
    connection.execute("INSERT INTO TimeLine VALUES (1, 7, 24, 0, 0, 'TL')")
    connection.execute("INSERT INTO AnimationCutBank VALUES (1, 1, 1)")
    externals = []
    next_id = [2]

    def add_raster(body, packing_type=(1, 4), one_bit=False):
        raster_id = next_id[0]
        next_id[0] += 1
        identifier = f"extrnlid_{raster_id}".encode()
        connection.execute("INSERT INTO Mipmap VALUES (?, ?)", (raster_id, raster_id))
        connection.execute("INSERT INTO MipmapInfo VALUES (?, ?)", (raster_id, raster_id))
        connection.execute(
            "INSERT INTO Offscreen VALUES (?, ?, ?)",
            (raster_id, identifier, _offscreen_attribute(packing_type=packing_type, one_bit=one_bit)),
        )
        externals.append(_external(identifier, body))
        return raster_id

    def add_layers(specs):
        ids = []
        # Real CSP links siblings bottom to top; specs remain top to bottom.
        for spec in reversed(specs):
            layer_id = next_id[0]
            next_id[0] += 1
            ids.append((layer_id, spec))
        for index, (layer_id, spec) in enumerate(ids):
            next_sibling = ids[index + 1][0] if index + 1 < len(ids) else 0
            first_child = add_layers(spec["children"]) if "children" in spec else 0
            render = add_raster(
                spec["raster"], spec.get("packing_type", (1, 4)), spec.get("one_bit", False)
            ) if "raster" in spec else None
            mask = (
                add_raster(spec["mask"], packing_type=(1, 0))
                if "mask" in spec
                else None
            )
            values = {
                "MainId": layer_id,
                "LayerUuid": f"uuid-{layer_id:011d}".encode(),
                "LayerName": spec["name"],
                "LayerFirstChildIndex": first_child,
                "LayerNextIndex": next_sibling,
                "LayerRenderMipmap": render,
                "AnimationFolder": 0,
                "LayerFolder": 1 if "children" in spec else 0,
                "LayerVisibility": spec.get("visibility", 1),
                "LayerOpacity": spec.get("opacity", 256),
                "LayerComposite": spec.get("composite", 0),
                "LayerClip": spec.get("clip", 0),
                "LayerLayerMaskMipmap": mask,
                "DrawColorEnable": int("layer_color" in spec),
                **{f"DrawColorMain{channel}": value for channel, value in
                   zip(("Red", "Green", "Blue"), spec.get("layer_color", (0, 0, 0)))},
                **{f"DrawColorSub{channel}": value for channel, value in
                   zip(("Red", "Green", "Blue"), spec.get("sub_color", (255, 255, 255)))},
            }
            connection.execute(
                f"INSERT INTO Layer VALUES ({', '.join('?' for _ in _LAYER_COLUMNS)})",
                tuple(values[name] for name in _LAYER_COLUMNS),
            )
        return ids[0][0] if ids else 0

    first_cel = add_layers(
        [{"name": name, "children": children} for name, children in cels.items()]
    )
    folder: dict[str, object] = {name: 0 for name in _LAYER_COLUMNS}
    folder.update(
        MainId=1,
        LayerUuid=b"A" * 16,
        LayerName="動画",
        LayerFirstChildIndex=first_cel,
        AnimationFolder=1,
        LayerFolder=1,
        LayerVisibility=1,
        LayerOpacity=256,
    )
    connection.execute(
        f"INSERT INTO Layer VALUES ({', '.join('?' for _ in _LAYER_COLUMNS)})",
        tuple(folder[name] for name in _LAYER_COLUMNS),
    )
    mixer_id = b"extrnlid_mixer"
    connection.execute(
        "INSERT INTO Track VALUES (1, 7, 2000, ?, ?)", (mixer_id, b"A" * 16)
    )
    connection.commit()
    connection.close()
    first_name = next(iter(cels))
    compressed_mixer = zlib.compress(_binc(((0.0, first_name),)))
    mixer_body = len(compressed_mixer).to_bytes(4, "little") + compressed_mixer
    data = _pack_clip(database_path, [_external(mixer_id, mixer_body), *externals])
    path = tmp_path / "layered.clip"
    path.write_bytes(data)
    return path


def _pixel(color, alpha=255):
    red, green, blue = color
    return QColor(red, green, blue, alpha)


def test_composite_multiply_uses_backdrop_where_both_layers_overlap():
    backdrop = np.array([[[0.5, 0.5, 0.5, 1.0]]], dtype=np.float32)
    source = np.array([[[0.5, 0.5, 0.5, 1.0]]], dtype=np.float32)
    result = _composite(backdrop, source, 2)
    assert np.allclose(result, [[[0.25, 0.25, 0.25, 1.0]]])


def test_composite_on_empty_backdrop_keeps_source_for_any_mode():
    backdrop = np.zeros((1, 1, 4), dtype=np.float32)
    source = np.array([[[0.2, 0.1, 0.0, 0.4]]], dtype=np.float32)
    for mode in (0, 2, 8, 14, 21, 25):
        assert np.allclose(_composite(backdrop, source, mode), source)


@requires_clip_native
def test_cel_folder_applies_clipping_blend_mode_mask_and_visibility(tmp_path):
    path = _layered_clip_file(
        tmp_path,
        {
            "1": [
                {
                    "name": "影",
                    "raster": _raster_body((128, 128, 128)),
                    "composite": 2,
                    "clip": 1,
                },
                {"name": "塗り", "raster": _raster_body((200, 100, 40), {0, 1})},
                # Mask enabled (bit 2) but the layer itself is hidden (bit 1).
                {"name": "非表示", "raster": _raster_body((0, 255, 0)), "visibility": 2},
                {
                    "name": "線",
                    "raster": _raster_body((0, 0, 0)),
                    "mask": _alpha_raster_body({256 + 1}),
                    "visibility": 3,
                },
            ]
        },
    )
    image = read_clip_animation(path).frames[0].layers[0].image
    shaded = image.pixelColor(0, 0)
    assert (shaded.red(), shaded.green(), shaded.blue(), shaded.alpha()) == (100, 50, 20, 255)
    assert image.pixelColor(1, 0) == shaded
    # The shadow is clipped to the paint, and the line is masked out here.
    assert image.pixelColor(0, 1).alpha() == 0
    assert image.pixelColor(1, 1) == _pixel((0, 0, 0))


@requires_clip_native
def test_pass_through_folder_blends_with_layers_below_it(tmp_path):
    path = _layered_clip_file(
        tmp_path,
        {
            "1": [
                {
                    "name": "影フォルダー",
                    "composite": 30,
                    "children": [
                        {
                            "name": "影",
                            "raster": _raster_body((128, 128, 128)),
                            "composite": 2,
                        }
                    ],
                },
                {"name": "塗り", "raster": _raster_body((200, 100, 40))},
            ],
            "2": [
                {
                    "name": "通常フォルダー",
                    "children": [
                        {
                            "name": "影",
                            "raster": _raster_body((128, 128, 128)),
                            "composite": 2,
                        }
                    ],
                },
                {"name": "塗り", "raster": _raster_body((200, 100, 40))},
            ],
        },
    )
    layer = read_clip_animation(path).layers[0]
    through = layer.cell_images["1"].pixelColor(0, 0)
    assert (through.red(), through.green(), through.blue()) == (100, 50, 20)
    # A normal (isolated) folder multiplies onto nothing, so the grey shows.
    assert layer.cell_images["2"].pixelColor(0, 0) == _pixel((128, 128, 128))


@requires_clip_native
def test_qt_normal_compositing_preserves_clipping_opacity_and_offsets(tmp_path):
    from paintmaskanimator.clip_animation import _CelRenderer, _premultiplied_to_image
    from paintmaskanimator.imaging import qimage_rgba_array

    path = _layered_clip_file(tmp_path, {"1": [
        {"name": "hidden", "raster": _raster_body((255, 0, 0)), "visibility": 0},
        {"name": "clipped", "raster": _raster_body((20, 80, 200)),
         "opacity": 110, "clip": 1},
        {"name": "base", "opacity": 177, "children": [
            {"name": "paint", "raster": _raster_body((200, 100, 40), {0, 256}),
             "opacity": 179},
        ]},
    ]})
    reader = native.clip_animation_reader(str(path), 16384, 512 * 1024 * 1024)
    rows = {row["MainId"]: row for row in reader.metadata()["layers"]}
    cel = next(row for row in rows.values() if row["LayerName"] == "1")
    # Folder translation applies after clipping and composition, as in the
    # reference implementation. Keep a transparent column to verify its shape.
    cel["LayerOffsetX"] = 1
    cel["LayerOpacity"] = 220
    renderer = _CelRenderer(reader, rows, 2, 2)
    assert renderer._can_render_qt(cel, frozenset())
    actual = renderer.render(cel)
    reference = _premultiplied_to_image(renderer._content(cel, frozenset()) * renderer._opacity(cel))
    # QPainter rounds at each 8-bit composition; compare premultiplied pixels
    # so tiny alpha rounding cannot exaggerate invisible straight RGB values.
    def premultiplied(image):
        rgba = qimage_rgba_array(image).astype(float)
        rgba[..., :3] *= rgba[..., 3:4] / 255
        return rgba
    assert np.max(np.abs(premultiplied(actual) - premultiplied(reference))) <= 2
    assert actual.pixelColor(0, 0).alpha() == 0
    assert actual.pixelColor(1, 0).alpha() > 0


def test_layer_color_maps_black_gray_white_and_preserves_alpha():
    from paintmaskanimator.clip_animation import _apply_layer_color
    image = QImage(3, 1, QImage.Format.Format_ARGB32_Premultiplied)
    image.setPixelColor(0, 0, QColor(0, 0, 0, 255))
    image.setPixelColor(1, 0, QColor(128, 128, 128, 255))
    image.setPixelColor(2, 0, QColor(255, 255, 255, 128))
    actual = _apply_layer_color(image, {
        "DrawColorEnable": 1,
        "DrawColorMainRed": 10, "DrawColorMainGreen": 20, "DrawColorMainBlue": 30,
        "DrawColorSubRed": 210, "DrawColorSubGreen": 220, "DrawColorSubBlue": 230,
    })
    assert actual.pixelColor(0, 0) == QColor(10, 20, 30, 255)
    assert actual.pixelColor(1, 0) == QColor(110, 120, 130, 255)
    assert actual.pixelColor(2, 0).alpha() == 128
    assert abs(actual.pixelColor(2, 0).red() - 210) <= 1


@requires_clip_native
@pytest.mark.parametrize("on_folder", [False, True])
@pytest.mark.parametrize("source_color,expected", [
    ((0, 0, 0), (128, 255, 192)), ((255, 255, 255), (30, 40, 50)),
])
def test_csp_layer_color_is_applied_to_rgba_rasters_and_folders(tmp_path, on_folder, source_color, expected):
    spec = {"name": "Raster", "raster": _raster_body(source_color, {0})}
    if on_folder:
        spec = {"name": "Colored folder", "children": [spec]}
    spec["layer_color"] = (0x80808080, 0xFFFFFFFF, 0xC0C0C0C0)
    spec["sub_color"] = (30, 40, 50)
    path = _layered_clip_file(tmp_path, {"1": [spec]})
    image = read_clip_animation(path).layers[0].cell_images["1"]
    assert image.pixelColor(0, 0) == QColor(*expected, 255)
    assert image.pixelColor(1, 0).alpha() == 0


def test_missing_native_codec_reports_actionable_error():
    from types import SimpleNamespace
    api = {name: getattr(native._native, name) for name in native.REQUIRED_API
           if name != "ClipAnimationReader"}
    with pytest.raises(ImportError, match="pip install ./native"):
        native.validate_backend(SimpleNamespace(**api))


@requires_clip_native
def test_legacy_opt_out_does_not_disable_required_clip_codec(monkeypatch, tmp_path):
    monkeypatch.setenv("PMA_NO_NATIVE", "1")
    assert native.available()
    assert read_clip_animation(_clip_file(tmp_path)).frame_count == 3


@requires_clip_native
def test_timeline_limit_is_checked_before_raster_decode(tmp_path):
    path = _clip_file(tmp_path, end_frame=constants.MAX_PROJECT_FRAMES)
    with pytest.raises(ClipImportError, match="PMA"):
        read_clip_animation(path)


@requires_clip_native
def test_native_rejects_inconsistent_root_size_without_modifying_source(tmp_path):
    path = _clip_file(tmp_path)
    data = bytearray(path.read_bytes())
    data[8:16] = (len(data) - 1).to_bytes(8, "big")
    path.write_bytes(data)
    with pytest.raises(ClipImportError):
        read_clip_animation(path)
    assert path.read_bytes() == data


@requires_clip_native
def test_duplicate_cel_curve_uses_complete_matching_curve_and_60hz_timebase(tmp_path):
    path = _clip_file(tmp_path, end_frame=25, mixer_keys=((0.0, "1"),),
                      extra_keys=((0.0, "1"), (60.0, "")))
    parsed = read_clip_animation(path)
    assert parsed.layers[0].keys == (ClipCelKey(0, "1"), ClipCelKey(24, ""))
    assert parsed.frames[0].layers[0].exposure == 24
    assert parsed.frames[24].layers[0].exposure == 2


@requires_clip_native
def test_native_clip_can_restore_initial_line_art_without_replacing_paint(qapp, tmp_path):
    path = _clip_file(tmp_path)
    window = MainWindow()
    try:
        assert window.importer.clip_animation(path, confirm_replace=False)
        canvas = window.canvas
        frames = canvas.frames
        cell = frames[0].layers[0]
        # 読み込みが済んだ時点で、線画は初期状態として保持されている。
        assert cell.initial_image.pixelColor(0, 0) == QColor(10, 20, 30, 255)
        cell.image = QImage(cell.image)
        cell.image.fill(QColor("red"))
        exposure = cell.exposure
        assert window.initial_state.import_clip_line_art(path) == 1
        assert canvas.frames is frames and cell.exposure == exposure
        assert cell.image.pixelColor(0, 0) == QColor("red")
        assert cell.initial_image.pixelColor(0, 0) == QColor(10, 20, 30, 255)
        canvas.undo()
        # 読み込み時に保持した初期状態へ戻る（塗りはそのまま）。
        assert cell.initial_image.pixelColor(0, 0) == QColor(10, 20, 30, 255)
        assert cell.image.pixelColor(0, 0) == QColor("red")
    finally:
        window.close()


@requires_clip_native
def test_clip_import_keeps_the_line_art_as_the_initial_state(qapp, tmp_path):
    path = _clip_file(tmp_path)
    window = MainWindow()
    try:
        assert window.importer.clip_animation(path, confirm_replace=False)
        cells = window.initial_state.cells()
        assert cells
        assert window.initial_state.count(cells) == len(cells)
    finally:
        window.close()


@requires_clip_native
def test_dropped_clip_keeps_the_initial_state_too(qapp, tmp_path):
    path = _clip_file(tmp_path)
    window = MainWindow()
    try:
        window.canvas.clipAnimationDropped.emit(str(path))
        cells = window.initial_state.cells()
        assert cells
        assert window.initial_state.count(cells) == len(cells)
    finally:
        window.close()
