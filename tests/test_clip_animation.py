import os
import sqlite3
import struct
import zlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from paintmaskanimator import constants, main_window_import
from paintmaskanimator.clip_animation import (
    ClipAnimationDocument,
    ClipAnimationLayer,
    ClipCelKey,
    ClipContainer,
    ClipImportError,
    build_pma_frames,
    _composite,
    _decode_offscreen,
    parse_image_cel_curve,
    read_clip_animation,
)
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.project_io import read_project_archive, write_project_archive


def _u32(value):
    return struct.pack("<I", value)


def _binc(keys=((0.0, "1"), (60.0, "2")), extra_keys=None):
    curves = [tuple(keys)]
    if extra_keys is not None:
        curves.append(tuple(extra_keys))
    strings = [
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


def _offscreen_attribute(
    width=2, height=2, packing_type=(1, 4), one_bit=False
):
    packing = [0] * 16
    packing[1], packing[2] = packing_type
    if one_bit:
        packing[8] = 32
    return b"".join(
        [
            (16).to_bytes(4, "big"),
            (102).to_bytes(4, "big"),
            (42).to_bytes(4, "big"),
            (0).to_bytes(4, "big"),
            _utf16be("Parameter"),
            width.to_bytes(4, "big"),
            height.to_bytes(4, "big"),
            (1).to_bytes(4, "big"),
            (1).to_bytes(4, "big"),
            *(value.to_bytes(4, "big") for value in packing),
            _utf16be("InitColor"),
            (0).to_bytes(4, "big"),
            (0).to_bytes(4, "big"),
            (0).to_bytes(4, "big"),
            (0).to_bytes(4, "big"),
            (0).to_bytes(4, "big"),
        ]
    )


def _raster_container(unpacked):
    compressed = zlib.compress(bytes(unpacked))
    block = b"".join(
        [
            (0).to_bytes(4, "big"),
            (0).to_bytes(4, "big"),
            (0).to_bytes(4, "big"),
            (0).to_bytes(4, "big"),
            (1).to_bytes(4, "big"),
            (len(compressed) + 4).to_bytes(4, "big"),
            (len(compressed)).to_bytes(4, "little"),
            compressed,
        ]
    )
    begin = "BlockDataBeginChunk".encode("utf-16-be")
    end = b"\0\0\0\x11" + "BlockDataEndChunk".encode("utf-16-be")
    size = 8 + len(begin) + len(block) + len(end)
    return size.to_bytes(4, "big") + b"\0" * 4 + begin + block + end


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
        (2, b"B" * 16, "1", 5, 0, 2, 0, 0, 0, 0, 0, 256),
    )
    connection.execute(
        "INSERT INTO Layer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (5, b"E" * 16, "空", 0, 3, 4, 0, 0, 0, 0, 0, 256),
    )
    connection.execute(
        "INSERT INTO Layer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (3, b"C" * 16, "線", 0, 4, 1, 0, 0, 0, 0, 0, 256),
    )
    connection.execute(
        "INSERT INTO Layer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (4, b"D" * 16, "塗り", 0, 0, 3, 0, 0, 0, 0, 0, 256),
    )
    connection.execute(
        "INSERT INTO Track VALUES (1, 7, 2000, ?, ?)",
        (mixer_id, b"A" * 16),
    )
    if include_non_animation_track:
        connection.execute(
            "INSERT INTO Track VALUES (2, 7, 2000, NULL, ?)",
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
    include_non_animation_track=False,
):
    (
        sqlite_data,
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
    compressed_mixer = zlib.compress(_binc(mixer_keys))
    mixer_body = len(compressed_mixer).to_bytes(4, "little") + compressed_mixer
    data = b"CSFCHUNK" + b"\0" * 16
    data += _chunk(b"SQLi", sqlite_data)
    data += _chunk(b"Exta", _external(mixer_id, mixer_body))
    # The cell folder cache intentionally contains only a wrong/stale solid
    # image. Import must flatten both visible child layers instead of using it.
    data += _chunk(
        b"Exta",
        _external(raster_id, _raster_body(alpha_pixels={0})),
    )
    data += _chunk(
        b"Exta",
        _external(lower_raster_id, _raster_body((40, 50, 60))),
    )
    data += _chunk(
        b"Exta",
        _external(stale_folder_raster_id, _raster_body((200, 0, 0))),
    )
    path = tmp_path / "animation.clip"
    path.write_bytes(data)
    return path


def test_parse_image_cel_curve_reads_key_times_and_tags():
    assert parse_image_cel_curve(_binc()) == [(0.0, "1"), (60.0, "2")]


def test_parse_image_cel_curve_ignores_later_duplicate_curve():
    data = _binc(((0.0, "1"),), extra_keys=((0.0, "2"),))
    assert parse_image_cel_curve(data) == [(0.0, "1")]


def test_one_channel_line_layer_uses_layer_color_and_channel_as_alpha():
    image = _decode_offscreen(
        _offscreen_attribute(packing_type=(1, 0)),
        _alpha_raster_body({0}),
        (12, 34, 56),
    )
    assert image.pixelColor(0, 0) == QColor(12, 34, 56, 255)
    assert image.pixelColor(1, 0).alpha() == 0


def test_one_bit_line_layer_uses_msb_first_mask_as_alpha():
    image = _decode_offscreen(
        _offscreen_attribute(
            width=16,
            height=2,
            packing_type=(1, 0),
            one_bit=True,
        ),
        _one_bit_alpha_raster_body({0, 9}),
        (90, 80, 70),
    )
    assert image.pixelColor(0, 0) == QColor(90, 80, 70, 255)
    assert image.pixelColor(1, 0).alpha() == 0
    assert image.pixelColor(9, 0) == QColor(90, 80, 70, 255)


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


def test_clip_container_is_read_only_and_indexes_external_data(tmp_path):
    identifier = b"extrnlid_test"
    original = b"CSFCHUNK" + b"\0" * 16
    original += _chunk(b"SQLi", b"sqlite")
    original += _chunk(b"Exta", _external(identifier, b"payload"))
    path = tmp_path / "index.clip"
    path.write_bytes(original)
    container = ClipContainer.scan(path)
    assert container.sqlite_bytes == b"sqlite"
    assert container.read_external(identifier) == b"payload"
    assert path.read_bytes() == original


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


def test_invalid_container_does_not_look_like_clip(tmp_path):
    path = tmp_path / "broken.clip"
    path.write_bytes(b"not a clip")
    try:
        ClipContainer.scan(path)
    except ClipImportError as exc:
        assert "ヘッダー" in str(exc)
    else:
        raise AssertionError("invalid .clip was accepted")


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

        def fail(_path):
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
    monkeypatch.setattr(
        main_window_import, "read_clip_animation", lambda _path: parsed
    )
    window = MainWindow()
    try:
        assert window.importer.clip_animation(
            tmp_path / "sample.clip", confirm_replace=False
        )
        assert (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT) == (3, 2)
        assert window.canvas.timeline_mode == "sheet"
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
        main_window_import, "read_clip_animation", lambda _path: parsed
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

    def add_raster(body, packing_type=(1, 4)):
        raster_id = next_id[0]
        next_id[0] += 1
        identifier = f"extrnlid_{raster_id}".encode()
        connection.execute("INSERT INTO Mipmap VALUES (?, ?)", (raster_id, raster_id))
        connection.execute("INSERT INTO MipmapInfo VALUES (?, ?)", (raster_id, raster_id))
        connection.execute(
            "INSERT INTO Offscreen VALUES (?, ?, ?)",
            (raster_id, identifier, _offscreen_attribute(packing_type=packing_type)),
        )
        externals.append(_external(identifier, body))
        return raster_id

    def add_layers(specs):
        ids = []
        for spec in specs:
            layer_id = next_id[0]
            next_id[0] += 1
            ids.append((layer_id, spec))
        for index, (layer_id, spec) in enumerate(ids):
            next_sibling = ids[index + 1][0] if index + 1 < len(ids) else 0
            first_child = add_layers(spec["children"]) if "children" in spec else 0
            render = add_raster(spec["raster"]) if "raster" in spec else None
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
    data = b"CSFCHUNK" + b"\0" * 16
    data += _chunk(b"SQLi", database_path.read_bytes())
    data += _chunk(b"Exta", _external(mixer_id, mixer_body))
    for external in externals:
        data += _chunk(b"Exta", external)
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
