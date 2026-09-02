"""Read-only CLIP STUDIO animation importer.

The native ``.clip`` format is an undocumented chunk container.  This module
only reads the parts needed for animation interchange: the embedded SQLite
metadata, image-cel mixer curves, and the rendered mipmap stored for each cel.
It never opens the source file for writing.

Container/raster observations are adapted from ``dobrokot/clip_to_psd`` and
animation mixer observations from ``Aodaruma/clipfile-rs`` (both MIT licensed).
The implementation below is intentionally small and defensive rather than a
general-purpose CLIP STUDIO document reader.
"""

from __future__ import annotations

import math
import os
import sqlite3
import struct
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QPainter

from .constants import (
    MAX_IMAGE_DIMENSION,
    MAX_PROJECT_DECODED_PIXELS,
    MAX_PROJECT_FRAMES,
    MAX_PROJECT_LAYER_CELLS,
    MAX_PROJECT_LAYERS,
)
from .models import Frame, Layer


_BLOCK_DATA_BEGIN = "BlockDataBeginChunk".encode("utf-16-be")
_BLOCK_DATA_END = "BlockDataEndChunk".encode("utf-16-be")
_BLOCK_STATUS = "BlockStatus".encode("utf-16-be")
_BLOCK_CHECKSUM = "BlockCheckSum".encode("utf-16-be")
_MAX_SQLITE_BYTES = 512 * 1024 * 1024
_MAX_MIXER_BYTES = 128 * 1024 * 1024
_MAX_EXTERNAL_ID_BYTES = 4096
_MAX_CHUNKS = 1_000_000
_TILE_SIZE = 256


class ClipImportError(ValueError):
    """A safe, user-displayable failure while reading a ``.clip`` file."""


@dataclass(frozen=True)
class ClipExternalObject:
    offset: int
    size: int


@dataclass(frozen=True)
class ClipCelKey:
    frame: int
    tag: str


@dataclass(frozen=True)
class ClipAnimationLayer:
    name: str
    keys: tuple[ClipCelKey, ...]
    cell_images: dict[str, QImage]


@dataclass(frozen=True)
class ClipAnimationDocument:
    source_name: str
    timeline_name: str
    width: int
    height: int
    fps: float
    start_frame: int
    end_frame: int
    layers: tuple[ClipAnimationLayer, ...]
    frames: tuple[Frame, ...]

    @property
    def folder_count(self) -> int:
        return len(self.layers)

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    def source_metadata(self) -> dict:
        return {
            "file_name": self.source_name,
            "timeline_name": self.timeline_name,
            "fps": float(self.fps),
            "start_frame": int(self.start_frame),
            "end_frame": int(self.end_frame),
        }


class ClipContainer:
    """Indexed, read-only view of a CLIP STUDIO chunk container."""

    def __init__(
        self,
        path: Path,
        sqlite_bytes: bytes,
        external_objects: dict[bytes, ClipExternalObject],
    ):
        self.path = Path(path)
        self.sqlite_bytes = sqlite_bytes
        self.external_objects = external_objects

    @classmethod
    def scan(cls, path: str | os.PathLike[str]) -> "ClipContainer":
        source = Path(path)
        if source.suffix.lower() != ".clip":
            raise ClipImportError("CLIP STUDIOの.clipファイルを指定してください。")
        try:
            file_size = source.stat().st_size
            stream = source.open("rb")
        except OSError as exc:
            raise ClipImportError(f".clipファイルを開けません。\n{exc}") from exc

        sqlite_bytes: Optional[bytes] = None
        external: dict[bytes, ClipExternalObject] = {}
        try:
            header = stream.read(24)
            if len(header) != 24 or header[:8] != b"CSFCHUNK":
                raise ClipImportError("CLIP STUDIO形式のヘッダーを確認できません。")
            offset = 24
            chunk_count = 0
            while offset < file_size:
                chunk_count += 1
                if chunk_count > _MAX_CHUNKS:
                    raise ClipImportError(".clip内のチャンク数が上限を超えています。")
                stream.seek(offset)
                chunk_header = stream.read(16)
                if len(chunk_header) != 16 or chunk_header[:4] != b"CHNK":
                    raise ClipImportError(".clipのチャンク構造が壊れています。")
                chunk_name = chunk_header[4:8]
                chunk_size = int.from_bytes(chunk_header[12:16], "big")
                body_offset = offset + 16
                body_end = body_offset + chunk_size
                if body_end < body_offset or body_end > file_size:
                    raise ClipImportError(".clipのチャンクサイズが不正です。")
                if chunk_name == b"SQLi":
                    if sqlite_bytes is not None:
                        raise ClipImportError(".clip内にSQLite情報が重複しています。")
                    if chunk_size > _MAX_SQLITE_BYTES:
                        raise ClipImportError(".clipの文書情報が大きすぎます。")
                    stream.seek(body_offset)
                    sqlite_bytes = stream.read(chunk_size)
                    if len(sqlite_bytes) != chunk_size:
                        raise ClipImportError(".clipの文書情報が途中で切れています。")
                elif chunk_name == b"Exta":
                    if chunk_size < 16:
                        raise ClipImportError(".clipの外部データチャンクが不正です。")
                    stream.seek(body_offset)
                    id_length_raw = stream.read(8)
                    id_length = int.from_bytes(id_length_raw, "big")
                    if not (1 <= id_length <= _MAX_EXTERNAL_ID_BYTES):
                        raise ClipImportError(".clipの外部データ識別子が不正です。")
                    if 16 + id_length > chunk_size:
                        raise ClipImportError(".clipの外部データ識別子が途中で切れています。")
                    identifier = stream.read(id_length)
                    payload_size = int.from_bytes(stream.read(8), "big")
                    available = chunk_size - id_length - 16
                    if payload_size > available:
                        raise ClipImportError(".clipの外部データサイズが不正です。")
                    if identifier in external:
                        raise ClipImportError(".clipの外部データ識別子が重複しています。")
                    external[identifier] = ClipExternalObject(
                        stream.tell(), payload_size
                    )
                offset = body_end
            if offset != file_size:
                raise ClipImportError(".clipの末尾位置が不正です。")
        except OSError as exc:
            raise ClipImportError(f".clipファイルを読み取れません。\n{exc}") from exc
        finally:
            stream.close()
        if sqlite_bytes is None:
            raise ClipImportError(".clip内に文書情報（SQLi）がありません。")
        return cls(source, sqlite_bytes, external)

    def read_external(self, identifier: object) -> bytes:
        key = _identifier_bytes(identifier)
        item = self.external_objects.get(key)
        if item is None:
            raise ClipImportError(
                ".clip内の参照画像またはアニメーション情報が見つかりません。"
            )
        try:
            with self.path.open("rb") as stream:
                stream.seek(item.offset)
                data = stream.read(item.size)
        except OSError as exc:
            raise ClipImportError(f".clipの外部データを読めません。\n{exc}") from exc
        if len(data) != item.size:
            raise ClipImportError(".clipの外部データが途中で切れています。")
        return data


def _identifier_bytes(value: object) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    raise ClipImportError(".clipの外部データ識別子の型が不正です。")


def _normalize_uuid(value: object) -> bytes:
    raw = _identifier_bytes(value)
    if len(raw) == 16:
        return raw
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ClipImportError(".clipのレイヤーUUIDが不正です。") from exc
    digits = "".join(character for character in text if character in "0123456789abcdefABCDEF")
    if len(digits) != 32:
        raise ClipImportError(".clipのレイヤーUUIDが不正です。")
    return bytes.fromhex(digits)


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
    except sqlite3.Error as exc:
        raise ClipImportError(f".clipの{table}情報を確認できません。") from exc


def _require_columns(
    connection: sqlite3.Connection,
    table: str,
    columns: Iterable[str],
) -> set[str]:
    existing = _table_columns(connection, table)
    missing = [column for column in columns if column not in existing]
    if missing:
        raise ClipImportError(
            f".clipの{table}情報に必要な項目がありません：{', '.join(missing)}"
        )
    return existing


def _open_embedded_database(sqlite_bytes: bytes):
    temporary = tempfile.NamedTemporaryFile(
        prefix="pma_clip_", suffix=".sqlite", delete=False
    )
    path = Path(temporary.name)
    try:
        temporary.write(sqlite_bytes)
        temporary.flush()
        temporary.close()
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        return connection, path
    except (OSError, sqlite3.Error) as exc:
        temporary.close()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ClipImportError(f".clipの文書データを開けません。\n{exc}") from exc


def _read_u32_le(data: bytes, cursor: int) -> tuple[int, int]:
    end = cursor + 4
    if end > len(data):
        raise ClipImportError("アニメーション情報が途中で切れています。")
    return int.from_bytes(data[cursor:end], "little"), end


def _parse_string_table(data: bytes) -> tuple[list[str], int]:
    if len(data) < 20 or data[:12] not in (b"cmt 0100binc", b"cmt 0110binc"):
        raise ClipImportError("未対応のCLIP STUDIOアニメーション情報です。")
    count, cursor = _read_u32_le(data, 16)
    if count > 1_000_000:
        raise ClipImportError("アニメーション文字列数が上限を超えています。")
    strings: list[str] = []
    for _ in range(count):
        if cursor >= len(data):
            raise ClipImportError("アニメーション文字列が途中で切れています。")
        length = data[cursor]
        cursor += 1
        end = cursor + length
        if end > len(data):
            raise ClipImportError("アニメーション文字列が途中で切れています。")
        try:
            strings.append(data[cursor:end].decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ClipImportError("アニメーション文字列をUTF-8で読めません。") from exc
        cursor = end
    return strings, cursor


def _string_at(strings: list[str], index: int) -> str:
    if not (0 <= index < len(strings)):
        raise ClipImportError("アニメーション文字列の参照先が不正です。")
    return strings[index]


def _curve_header(
    data: bytes,
    start: int,
    strings: list[str],
    fcurve_id: int,
) -> Optional[tuple[int, str]]:
    cursor = start
    try:
        marker, cursor = _read_u32_le(data, cursor)
        zero, cursor = _read_u32_le(data, cursor)
        property_count, cursor = _read_u32_le(data, cursor)
    except ClipImportError:
        return None
    if marker != fcurve_id or zero != 0 or not (1 <= property_count <= 8):
        return None
    kind: Optional[str] = None
    try:
        for _ in range(property_count):
            property_id, cursor = _read_u32_le(data, cursor)
            value_id, cursor = _read_u32_le(data, cursor)
            property_name = _string_at(strings, property_id)
            value = _string_at(strings, value_id)
            if property_name == "Type":
                if kind is not None:
                    return None
                kind = value
    except ClipImportError:
        return None
    return (cursor, kind) if kind is not None else None


_FCURVE_ELEMENT_SIZES = {
    "Single[]": 4,
    "String[]": 4,
    "Int32[]": 4,
    "Byte[]": 1,
    "Float2[]": 8,
    "Float3[]": 12,
    "Quat[]": 16,
    "Matrix44[]": 64,
}


def _parse_image_cel_fields(
    data: bytes,
    cursor: int,
    strings: list[str],
) -> list[tuple[float, str]]:
    field_count, cursor = _read_u32_le(data, cursor)
    if field_count > 1024:
        raise ClipImportError("アニメーション曲線の項目数が上限を超えています。")
    frames: Optional[list[float]] = None
    values: Optional[list[float]] = None
    tags: Optional[list[str]] = None
    for _ in range(field_count):
        field_id, cursor = _read_u32_le(data, cursor)
        type_id, cursor = _read_u32_le(data, cursor)
        count, cursor = _read_u32_le(data, cursor)
        if count > MAX_PROJECT_FRAMES * 16:
            raise ClipImportError("アニメーション曲線の配列が大きすぎます。")
        field_name = _string_at(strings, field_id)
        field_type = _string_at(strings, type_id)
        element_size = _FCURVE_ELEMENT_SIZES.get(field_type)
        if element_size is None:
            raise ClipImportError(
                f"未対応のアニメーション曲線形式です：{field_type}"
            )
        byte_count = count * element_size
        end = cursor + byte_count
        if end > len(data):
            raise ClipImportError("アニメーション曲線が途中で切れています。")
        if field_type == "Single[]" and field_name in ("Frame", "Value"):
            array = [
                struct.unpack_from("<f", data, cursor + index * 4)[0]
                for index in range(count)
            ]
            if field_name == "Frame":
                frames = array
            else:
                values = array
        elif field_type == "String[]" and field_name == "Tag":
            tags = [
                _string_at(
                    strings,
                    int.from_bytes(
                        data[cursor + index * 4:cursor + index * 4 + 4],
                        "little",
                    ),
                )
                for index in range(count)
            ]
        cursor = end
        first, cursor = _read_u32_le(data, cursor)
        second, cursor = _read_u32_le(data, cursor)
        if first != 0 or second != 0:
            raise ClipImportError("アニメーション曲線の終端が不正です。")
    if frames is None or values is None or tags is None:
        raise ClipImportError("セル指定曲線にFrame、Value、Tagのいずれかがありません。")
    if not (len(frames) == len(values) == len(tags)):
        raise ClipImportError("セル指定曲線の配列長が一致しません。")
    if any(not math.isfinite(value) for value in frames + values):
        raise ClipImportError("セル指定曲線に不正な数値があります。")
    if any(left > right for left, right in zip(frames, frames[1:])):
        raise ClipImportError("セル指定曲線の時刻順が不正です。")
    return list(zip(frames, tags))


def _parse_image_cel_curves(data: bytes) -> list[list[tuple[float, str]]]:
    """Decode all ``ImageCelName`` candidates from a BINC mixer."""

    strings, data_start = _parse_string_table(data)
    try:
        fcurve_id = strings.index("FCurve")
    except ValueError:
        return []
    found: list[list[tuple[float, str]]] = []
    for start in range(data_start, max(data_start, len(data) - 11)):
        header = _curve_header(data, start, strings, fcurve_id)
        if header is None or header[1] != "ImageCelName":
            continue
        try:
            found.append(_parse_image_cel_fields(data, header[0], strings))
        except ClipImportError:
            if not found:
                raise
            # A malformed action/keyframe duplicate must not invalidate an
            # otherwise valid animation-folder assignment curve.
            continue
    return found


def parse_image_cel_curve(data: bytes) -> list[tuple[float, str]]:
    """Decode the primary ``ImageCelName`` curve from a BINC mixer.

    Some CLIP documents contain another ``ImageCelName`` curve in an action or
    keyframe block.  The first curve is the animation-folder cel assignment;
    later duplicates are deliberately ignored together with camera/transform
    curves.
    """

    curves = _parse_image_cel_curves(data)
    return curves[0] if curves else []


def _decompress_mixer(body: bytes) -> bytes:
    if len(body) < 4:
        raise ClipImportError("アニメーション圧縮データが途中で切れています。")
    compressed_size = int.from_bytes(body[:4], "little")
    if compressed_size > len(body) - 4:
        raise ClipImportError("アニメーション圧縮データのサイズが不正です。")
    decoder = zlib.decompressobj()
    try:
        result = decoder.decompress(
            body[4:4 + compressed_size], _MAX_MIXER_BYTES + 1
        )
    except zlib.error as exc:
        raise ClipImportError("アニメーション圧縮データを展開できません。") from exc
    if len(result) > _MAX_MIXER_BYTES or not decoder.eof:
        raise ClipImportError("アニメーション展開データが大きすぎるか不完全です。")
    return result


class _BigEndianReader:
    def __init__(self, data: bytes):
        self.data = data
        self.cursor = 0

    def u32(self) -> int:
        end = self.cursor + 4
        if end > len(self.data):
            raise ClipImportError("画像属性が途中で切れています。")
        value = int.from_bytes(self.data[self.cursor:end], "big")
        self.cursor = end
        return value

    def utf16(self) -> str:
        count = self.u32()
        end = self.cursor + count * 2
        if end > len(self.data):
            raise ClipImportError("画像属性の文字列が途中で切れています。")
        try:
            value = self.data[self.cursor:end].decode("utf-16-be")
        except UnicodeDecodeError as exc:
            raise ClipImportError("画像属性の文字列を読めません。") from exc
        self.cursor = end
        return value


@dataclass(frozen=True)
class _OffscreenAttributes:
    width: int
    height: int
    grid_width: int
    grid_height: int
    default_white: bool
    packing: tuple[int, ...]


def _parse_offscreen_attributes(data: bytes) -> _OffscreenAttributes:
    reader = _BigEndianReader(data)
    header_size = reader.u32()
    info_size = reader.u32()
    extra_size = reader.u32()
    reader.u32()
    if header_size != 16 or info_size != 102 or extra_size not in (42, 58):
        raise ClipImportError("未対応のCLIP STUDIO画像属性形式です。")
    if reader.utf16() != "Parameter":
        raise ClipImportError("CLIP STUDIO画像属性のParameterがありません。")
    width = reader.u32()
    height = reader.u32()
    grid_width = reader.u32()
    grid_height = reader.u32()
    packing = tuple(reader.u32() for _ in range(16))
    if reader.utf16() != "InitColor":
        raise ClipImportError("CLIP STUDIO画像属性のInitColorがありません。")
    reader.u32()
    default_white = bool(reader.u32())
    reader.u32()
    reader.u32()
    reader.u32()
    if extra_size == 58:
        for _ in range(4):
            reader.u32()
    if (
        width < 1
        or height < 1
        or width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
        or grid_width < 1
        or grid_height < 1
        or grid_width * grid_height > MAX_PROJECT_LAYER_CELLS
    ):
        raise ClipImportError("CLIP STUDIO画像のサイズが上限外です。")
    return _OffscreenAttributes(
        width, height, grid_width, grid_height, default_white, packing
    )


def _parse_bitmap_blocks(data: bytes) -> list[Optional[bytes]]:
    cursor = 0
    block_count = 0
    blocks: list[Optional[bytes]] = []
    while cursor < len(data):
        if data[cursor:cursor + 4 + len(_BLOCK_STATUS)] == b"\0\0\0\x0b" + _BLOCK_STATUS:
            count_start = cursor + 30
            count_end = count_start + 4
            if count_end > len(data):
                raise ClipImportError("画像ブロックステータスが途中で切れています。")
            status_count = int.from_bytes(data[count_start:count_end], "big")
            block_size = status_count * 4 + 12 + len(_BLOCK_STATUS) + 4
        elif data[cursor:cursor + 4 + len(_BLOCK_CHECKSUM)] == b"\0\0\0\x0d" + _BLOCK_CHECKSUM:
            block_size = 4 + len(_BLOCK_CHECKSUM) + 12 + block_count * 4
        elif data[cursor + 8:cursor + 8 + len(_BLOCK_DATA_BEGIN)] == _BLOCK_DATA_BEGIN:
            if cursor + 4 > len(data):
                raise ClipImportError("画像ブロックが途中で切れています。")
            block_size = int.from_bytes(data[cursor:cursor + 4], "big")
            end = cursor + block_size
            trailer = b"\0\0\0\x11" + _BLOCK_DATA_END
            if block_size <= len(trailer) or end > len(data) or data[end - len(trailer):end] != trailer:
                raise ClipImportError("画像ブロックの終端が不正です。")
            content_start = cursor + 8 + len(_BLOCK_DATA_BEGIN)
            content_end = end - len(trailer)
            block = data[content_start:content_end]
            if len(block) < 20:
                raise ClipImportError("画像ブロックの情報が不足しています。")
            has_data = int.from_bytes(block[16:20], "big")
            if has_data not in (0, 1):
                raise ClipImportError("画像ブロックの有無フラグが不正です。")
            if has_data:
                if len(block) < 28:
                    raise ClipImportError("画像ブロックデータが途中で切れています。")
                subblock_length = int.from_bytes(block[20:24], "big")
                if len(block) != subblock_length + 24:
                    raise ClipImportError("画像ブロックデータのサイズが不正です。")
                blocks.append(bytes(block[28:]))
            else:
                blocks.append(None)
            block_count += 1
        else:
            raise ClipImportError("CLIP STUDIO画像ブロックを解釈できません。")
        if block_size <= 0 or cursor + block_size > len(data):
            raise ClipImportError("CLIP STUDIO画像ブロックのサイズが不正です。")
        cursor += block_size
    if cursor != len(data):
        raise ClipImportError("CLIP STUDIO画像ブロックの末尾が不正です。")
    return blocks


def _decompress_tile(data: bytes, expected_size: int) -> bytes:
    decoder = zlib.decompressobj()
    try:
        result = decoder.decompress(data, expected_size + 1)
    except zlib.error as exc:
        raise ClipImportError("セル画像の圧縮ブロックを展開できません。") from exc
    if len(result) != expected_size or not decoder.eof or decoder.unconsumed_tail:
        raise ClipImportError("セル画像の展開後ブロックサイズが不正です。")
    return result


def _decode_offscreen(
    attribute: bytes,
    external_body: Optional[bytes],
    single_channel_color: tuple[int, int, int] = (0, 0, 0),
) -> QImage:
    info = _parse_offscreen_attributes(attribute)
    packing_type = (info.packing[1], info.packing[2])
    one_bit = info.packing[8] == 32
    supported_types = (
        ((1, 0), (0, 1))
        if one_bit
        else ((1, 4), (1, 0), (0, 1), (1, 1))
    )
    if packing_type not in supported_types:
        raise ClipImportError(
            f"未対応のセル画像チャンネル構成です：{packing_type}"
        )
    blocks = (
        _parse_bitmap_blocks(external_body)
        if external_body is not None
        else [None] * (info.grid_width * info.grid_height)
    )
    if len(blocks) != info.grid_width * info.grid_height:
        raise ClipImportError("セル画像のブロック数とグリッド数が一致しません。")
    image = QImage(info.width, info.height, QImage.Format.Format_RGBA8888)
    default_value = 255 if info.default_white else 0
    if packing_type == (1, 4):
        default_color = (
            QColor(255, 255, 255, 255)
            if info.default_white
            else QColor(0, 0, 0, 0)
        )
    elif packing_type == (1, 0):
        default_color = QColor(*single_channel_color, default_value)
    elif packing_type == (0, 1):
        default_color = QColor(default_value, default_value, default_value, 255)
    else:
        default_color = QColor(
            default_value, default_value, default_value, default_value
        )
    image.fill(default_color)
    painter = QPainter(image)
    try:
        pixel_count = _TILE_SIZE * _TILE_SIZE
        for index, compressed in enumerate(blocks):
            if compressed is None:
                continue
            channel_count = sum(packing_type)
            if one_bit:
                packed_size = (pixel_count * channel_count + 7) // 8
                packed = _decompress_tile(compressed, packed_size)
                unpacked = (
                    np.unpackbits(
                        np.frombuffer(packed, dtype=np.uint8),
                        bitorder="big",
                    )[:pixel_count * channel_count]
                    * 255
                ).astype(np.uint8, copy=False).tobytes()
            else:
                unpacked = _decompress_tile(
                    compressed, pixel_count * channel_count
                )
            rgba = np.empty((_TILE_SIZE, _TILE_SIZE, 4), dtype=np.uint8)
            if packing_type == (1, 4):
                alpha = np.frombuffer(
                    unpacked, dtype=np.uint8, count=pixel_count
                )
                bgra = np.frombuffer(
                    unpacked,
                    dtype=np.uint8,
                    count=pixel_count * 4,
                    offset=pixel_count,
                ).reshape((_TILE_SIZE, _TILE_SIZE, 4))
                rgba[:, :, 0] = bgra[:, :, 2]
                rgba[:, :, 1] = bgra[:, :, 1]
                rgba[:, :, 2] = bgra[:, :, 0]
                rgba[:, :, 3] = alpha.reshape((_TILE_SIZE, _TILE_SIZE))
            elif packing_type == (1, 0):
                rgba[:, :, :3] = single_channel_color
                rgba[:, :, 3] = np.frombuffer(
                    unpacked, dtype=np.uint8, count=pixel_count
                ).reshape((_TILE_SIZE, _TILE_SIZE))
            elif packing_type == (0, 1):
                gray = np.frombuffer(
                    unpacked, dtype=np.uint8, count=pixel_count
                ).reshape((_TILE_SIZE, _TILE_SIZE))
                rgba[:, :, 0] = gray
                rgba[:, :, 1] = gray
                rgba[:, :, 2] = gray
                rgba[:, :, 3] = 255
            else:
                alpha = np.frombuffer(
                    unpacked, dtype=np.uint8, count=pixel_count
                ).reshape((_TILE_SIZE, _TILE_SIZE))
                gray = np.frombuffer(
                    unpacked,
                    dtype=np.uint8,
                    count=pixel_count,
                    offset=pixel_count,
                ).reshape((_TILE_SIZE, _TILE_SIZE))
                rgba[:, :, 0] = gray
                rgba[:, :, 1] = gray
                rgba[:, :, 2] = gray
                rgba[:, :, 3] = alpha
            raw = rgba.tobytes()
            tile = QImage(
                raw,
                _TILE_SIZE,
                _TILE_SIZE,
                _TILE_SIZE * 4,
                QImage.Format.Format_RGBA8888,
            ).copy()
            x = (index % info.grid_width) * _TILE_SIZE
            y = (index // info.grid_width) * _TILE_SIZE
            painter.drawImage(QPoint(x, y), tile)
    finally:
        painter.end()
    return image


def _row_number(row: sqlite3.Row, name: str, default: int = 0) -> int:
    value = row[name] if name in row.keys() else default
    return default if value is None else int(value)


def _csp_color_byte(value: object) -> int:
    if value is None:
        return 0
    component = max(0, int(value))
    if component <= 255:
        return component
    return min(255, component >> 24)


def _single_channel_layer_color(layer: sqlite3.Row) -> tuple[int, int, int]:
    if _row_number(layer, "DrawColorEnable"):
        names = ("DrawColorMainRed", "DrawColorMainGreen", "DrawColorMainBlue")
    elif _row_number(layer, "LayerUsePaletteColor"):
        names = ("LayerPaletteRed", "LayerPaletteGreen", "LayerPaletteBlue")
    else:
        return (0, 0, 0)
    return tuple(
        _csp_color_byte(layer[name] if name in layer.keys() else 0)
        for name in names
    )


def _blank_canvas(width: int, height: int) -> QImage:
    image = QImage(
        width, height, QImage.Format.Format_ARGB32_Premultiplied
    )
    image.fill(QColor(0, 0, 0, 0))
    return image


def _render_cached_layer(
    connection: sqlite3.Connection,
    container: ClipContainer,
    layer: sqlite3.Row,
    canvas_width: int,
    canvas_height: int,
) -> QImage:
    mipmap_id = layer["LayerRenderMipmap"]
    if not mipmap_id:
        raise ClipImportError(
            f"セル「{layer['LayerName']}」の表示合成画像が.clip内にありません。"
        )
    mipmap = connection.execute(
        "SELECT BaseMipmapInfo FROM Mipmap WHERE MainId = ?", (mipmap_id,)
    ).fetchone()
    if mipmap is None or not mipmap[0]:
        raise ClipImportError(f"セル「{layer['LayerName']}」のMipmap情報がありません。")
    mipmap_info = connection.execute(
        "SELECT Offscreen FROM MipmapInfo WHERE MainId = ?", (mipmap[0],)
    ).fetchone()
    if mipmap_info is None or not mipmap_info[0]:
        raise ClipImportError(f"セル「{layer['LayerName']}」のOffscreen情報がありません。")
    offscreen = connection.execute(
        "SELECT BlockData, Attribute FROM Offscreen WHERE MainId = ?",
        (mipmap_info[0],),
    ).fetchone()
    if offscreen is None or not offscreen[0] or not offscreen[1]:
        raise ClipImportError(f"セル「{layer['LayerName']}」の画像データがありません。")
    block_identifier = _identifier_bytes(offscreen[0])
    external_body = (
        container.read_external(block_identifier)
        if block_identifier in container.external_objects
        else None
    )
    raster = _decode_offscreen(
        bytes(offscreen[1]),
        external_body,
        _single_channel_layer_color(layer),
    )
    result = _blank_canvas(canvas_width, canvas_height)
    x = _row_number(layer, "LayerOffsetX") + _row_number(
        layer, "LayerRenderOffscrOffsetX"
    )
    y = _row_number(layer, "LayerOffsetY") + _row_number(
        layer, "LayerRenderOffscrOffsetY"
    )
    opacity_raw = _row_number(layer, "LayerOpacity", 256)
    painter = QPainter(result)
    try:
        painter.setOpacity(max(0.0, min(1.0, opacity_raw / 256.0)))
        painter.drawImage(QPoint(x, y), raster)
    finally:
        painter.end()
    return result


def _child_layer_rows(
    layers_by_id: dict[int, sqlite3.Row], layer: sqlite3.Row
) -> list[sqlite3.Row]:
    child_id = _row_number(layer, "LayerFirstChildIndex")
    children: list[sqlite3.Row] = []
    visited: set[int] = set()
    while child_id:
        if child_id in visited:
            raise ClipImportError("セル内のレイヤー構造が循環しています。")
        visited.add(child_id)
        child = layers_by_id.get(child_id)
        if child is None:
            raise ClipImportError("セル内の子レイヤーが見つかりません。")
        children.append(child)
        child_id = _row_number(child, "LayerNextIndex")
    return children


def _render_layer(
    connection: sqlite3.Connection,
    container: ClipContainer,
    layer: sqlite3.Row,
    canvas_width: int,
    canvas_height: int,
    layers_by_id: Optional[dict[int, sqlite3.Row]] = None,
    render_stack: Optional[set[int]] = None,
) -> QImage:
    """Render a cel, flattening folders from all visible child layers.

    CLIP STUDIO can omit a folder cache or leave one that represents only part
    of the folder. Child layers are therefore authoritative whenever the cel
    is a folder; only leaf layers use their cached rendered mipmap.
    """

    has_children = layers_by_id is not None and bool(
        _row_number(layer, "LayerFirstChildIndex")
    )
    if not has_children:
        return _render_cached_layer(
            connection, container, layer, canvas_width, canvas_height
        )

    layer_id = _row_number(layer, "MainId")
    stack = set() if render_stack is None else set(render_stack)
    if layer_id in stack:
        raise ClipImportError("セル内のレイヤー構造が循環しています。")
    stack.add(layer_id)
    children = _child_layer_rows(layers_by_id, layer)

    # LayerFirstChildIndex follows CLIP STUDIO's palette order (top to
    # bottom). Paint bottom to top to reproduce the visible composite.
    composite = _blank_canvas(canvas_width, canvas_height)
    painter = QPainter(composite)
    try:
        for child in reversed(children):
            if not _row_number(child, "LayerVisibility", 1):
                continue
            child_image = _render_layer(
                connection,
                container,
                child,
                canvas_width,
                canvas_height,
                layers_by_id,
                stack,
            )
            painter.drawImage(QPoint(0, 0), child_image)
    finally:
        painter.end()

    opacity_raw = _row_number(layer, "LayerOpacity", 256)
    offset_x = _row_number(layer, "LayerOffsetX")
    offset_y = _row_number(layer, "LayerOffsetY")
    if opacity_raw == 256 and offset_x == 0 and offset_y == 0:
        return composite
    result = _blank_canvas(canvas_width, canvas_height)
    painter = QPainter(result)
    try:
        painter.setOpacity(max(0.0, min(1.0, opacity_raw / 256.0)))
        painter.drawImage(QPoint(offset_x, offset_y), composite)
    finally:
        painter.end()
    return result


def _numeric_cell_name(name: str) -> Optional[int]:
    stripped = str(name).strip()
    if not stripped or not stripped.isdecimal():
        return None
    try:
        value = int(stripped)
    except ValueError:
        return None
    return value if 1 <= value <= MAX_PROJECT_FRAMES else None


def cell_sequence_numbers(layer: ClipAnimationLayer) -> dict[str, int]:
    """Return stable PMA sequence numbers for every CLIP cel in a folder."""

    result: dict[str, int] = {}
    used: set[int] = set()
    deferred: list[str] = []
    for tag in layer.cell_images:
        number = _numeric_cell_name(tag)
        if number is None or number in used:
            deferred.append(tag)
            continue
        result[tag] = number
        used.add(number)
    next_number = 1
    for tag in deferred:
        while next_number in used:
            next_number += 1
        if next_number > MAX_PROJECT_FRAMES:
            raise ClipImportError("アニメーションセルの絵番号がPMAの上限外です。")
        result[tag] = next_number
        used.add(next_number)
    return result


def _layer_runs(
    keys: tuple[ClipCelKey, ...],
    start_frame: int,
    end_frame: int,
) -> list[tuple[int, int, str]]:
    duration = end_frame - start_frame + 1
    active = ""
    events: dict[int, str] = {}
    for key in keys:
        if key.frame <= start_frame:
            active = key.tag
        elif key.frame <= end_frame:
            events[key.frame - start_frame] = key.tag
    events[0] = active
    runs: list[tuple[int, int, str]] = []
    current_start = 0
    current_tag = events[0]
    for position in sorted(value for value in events if value > 0):
        next_tag = events[position]
        if next_tag == current_tag:
            continue
        runs.append((current_start, position - current_start, current_tag))
        current_start = position
        current_tag = next_tag
    runs.append((current_start, duration - current_start, current_tag))
    return runs


def build_pma_frames(
    width: int,
    height: int,
    start_frame: int,
    end_frame: int,
    layers: tuple[ClipAnimationLayer, ...],
) -> tuple[Frame, ...]:
    """Convert parsed CLIP tracks into PMA keyframes plus exposure spans."""

    duration = end_frame - start_frame + 1
    if not (1 <= duration <= MAX_PROJECT_FRAMES):
        raise ClipImportError("タイムラインのフレーム数がPMAの上限外です。")
    if not (1 <= len(layers) <= MAX_PROJECT_LAYERS):
        raise ClipImportError("アニメーションフォルダー数がPMAの上限外です。")
    cell_count = duration * len(layers)
    if cell_count > MAX_PROJECT_LAYER_CELLS:
        raise ClipImportError("タイムラインのレイヤーセル数がPMAの上限を超えています。")
    if width * height * cell_count > MAX_PROJECT_DECODED_PIXELS:
        raise ClipImportError("タイムライン展開後の画像サイズがPMAの上限を超えています。")
    transparent = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    transparent.fill(QColor(0, 0, 0, 0))
    frames = [
        Frame([
            Layer(layer.name, QImage(transparent))
            for layer in layers
        ])
        for _ in range(duration)
    ]
    for layer_index, source_layer in enumerate(layers):
        sequence_numbers = cell_sequence_numbers(source_layer)
        for run_start, exposure, tag in _layer_runs(
            source_layer.keys, start_frame, end_frame
        ):
            target = frames[run_start].layers[layer_index]
            target.exposure = exposure
            if not tag:
                target.cell_name = None
                target.is_blank_key = True
                target.has_content = False
                continue
            image = source_layer.cell_images.get(tag)
            if image is None:
                raise ClipImportError(
                    f"アニメーションフォルダー「{source_layer.name}」の"
                    f"セル「{tag}」を画像化できません。"
                )
            target.image = QImage(image)
            target.has_content = True
            target.is_blank_key = False
            target.sequence_number = sequence_numbers[tag]
            target.cell_name = (
                None if _numeric_cell_name(tag) == target.sequence_number else tag
            )
    return tuple(frames)


def _timeline_row(connection: sqlite3.Connection) -> sqlite3.Row:
    columns = _require_columns(
        connection,
        "TimeLine",
        ("MainId", "BankId", "FrameRate", "StartFrame", "EndFrame"),
    )
    preferred = None
    bank_columns = _table_columns(connection, "AnimationCutBank")
    if "FirstTimeLine" in bank_columns:
        where = "WHERE Enable != 0" if "Enable" in bank_columns else ""
        row = connection.execute(
            f"SELECT FirstTimeLine FROM AnimationCutBank {where} ORDER BY MainId LIMIT 1"
        ).fetchone()
        if row is not None and row[0]:
            preferred = int(row[0])
    optional_name = "TimeLineName" if "TimeLineName" in columns else "NULL AS TimeLineName"
    if preferred is not None:
        row = connection.execute(
            f"SELECT MainId, BankId, FrameRate, StartFrame, EndFrame, {optional_name} "
            "FROM TimeLine WHERE MainId = ?",
            (preferred,),
        ).fetchone()
        if row is None:
            raise ClipImportError("有効タイムラインの参照先がありません。")
        return row
    row = connection.execute(
        f"SELECT MainId, BankId, FrameRate, StartFrame, EndFrame, {optional_name} "
        "FROM TimeLine ORDER BY MainId LIMIT 1"
    ).fetchone()
    if row is None:
        raise ClipImportError(".clip内にアニメーションタイムラインがありません。")
    return row


def _direct_children(
    layers_by_id: dict[int, sqlite3.Row], folder: sqlite3.Row
) -> dict[str, sqlite3.Row]:
    child_id = _row_number(folder, "LayerFirstChildIndex")
    result: dict[str, sqlite3.Row] = {}
    visited: set[int] = set()
    while child_id:
        if child_id in visited:
            raise ClipImportError("アニメーションフォルダーのレイヤー構造が循環しています。")
        visited.add(child_id)
        child = layers_by_id.get(child_id)
        if child is None:
            raise ClipImportError("アニメーションフォルダーの子レイヤーがありません。")
        name = str(child["LayerName"] or "")
        if name in result:
            raise ClipImportError(
                f"アニメーションフォルダー内でセル名「{name}」が重複しています。"
            )
        result[name] = child
        child_id = _row_number(child, "LayerNextIndex")
    return result


def read_clip_animation(path: str | os.PathLike[str]) -> ClipAnimationDocument:
    """Parse a CLIP STUDIO animation without modifying its source file."""

    container = ClipContainer.scan(path)
    connection, temporary_path = _open_embedded_database(container.sqlite_bytes)
    try:
        try:
            connection.execute("PRAGMA query_only = ON")
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.Error as exc:
            raise ClipImportError(".clipのSQLite文書情報を検査できません。") from exc
        if quick_check is None or str(quick_check[0]).lower() != "ok":
            raise ClipImportError(".clipのSQLite文書情報が壊れています。")
        canvas_columns = _require_columns(
            connection, "Canvas", ("CanvasWidth", "CanvasHeight")
        )
        canvas = connection.execute(
            "SELECT CanvasWidth, CanvasHeight FROM Canvas ORDER BY MainId LIMIT 1"
            if "MainId" in canvas_columns
            else "SELECT CanvasWidth, CanvasHeight FROM Canvas LIMIT 1"
        ).fetchone()
        if canvas is None:
            raise ClipImportError(".clip内にキャンバス情報がありません。")
        width, height = int(round(float(canvas[0]))), int(round(float(canvas[1])))
        if (
            width < 1
            or height < 1
            or width > MAX_IMAGE_DIMENSION
            or height > MAX_IMAGE_DIMENSION
        ):
            raise ClipImportError(".clipのキャンバスサイズがPMAの上限外です。")

        timeline = _timeline_row(connection)
        fps = float(timeline["FrameRate"])
        start_value = float(timeline["StartFrame"])
        end_value = float(timeline["EndFrame"])
        if (
            not math.isfinite(fps)
            or fps <= 0
            or not math.isfinite(start_value)
            or not math.isfinite(end_value)
            or start_value > end_value
        ):
            raise ClipImportError(".clipのFPSまたはタイムライン範囲が不正です。")
        source_start_frame = int(round(start_value))
        source_end_frame = int(round(end_value))
        if source_end_frame < source_start_frame:
            raise ClipImportError(".clipのタイムライン範囲が不正です。")
        # CLIP can retain cells in a negative pre-roll (for example -0+08).
        # PMA starts at zero, so that entire pre-roll is omitted from the sheet.
        start_frame = max(0, source_start_frame)
        end_frame = max(0, source_end_frame)

        layer_columns = _require_columns(
            connection,
            "Layer",
            (
                "MainId",
                "LayerUuid",
                "LayerName",
                "LayerFirstChildIndex",
                "LayerNextIndex",
                "LayerRenderMipmap",
            ),
        )
        _require_columns(connection, "Mipmap", ("MainId", "BaseMipmapInfo"))
        _require_columns(connection, "MipmapInfo", ("MainId", "Offscreen"))
        _require_columns(
            connection, "Offscreen", ("MainId", "BlockData", "Attribute")
        )
        layer_rows = connection.execute("SELECT * FROM Layer ORDER BY MainId").fetchall()
        layers_by_id = {int(row["MainId"]): row for row in layer_rows}
        layers_by_uuid: dict[bytes, sqlite3.Row] = {}
        for row in layer_rows:
            if row["LayerUuid"] is None:
                continue
            uuid = _normalize_uuid(row["LayerUuid"])
            if uuid in layers_by_uuid:
                raise ClipImportError(".clip内でレイヤーUUIDが重複しています。")
            layers_by_uuid[uuid] = row

        _require_columns(
            connection,
            "Track",
            ("MainId", "BankId", "TrackKind", "TrackActionMixer", "LayerUuidWithTrack"),
        )
        tracks = connection.execute(
            "SELECT MainId, TrackActionMixer, LayerUuidWithTrack "
            "FROM Track WHERE BankId = ? AND TrackKind = 2000 ORDER BY MainId",
            (int(timeline["BankId"]),),
        ).fetchall()
        parsed_layers: list[ClipAnimationLayer] = []
        for track in tracks:
            if track["LayerUuidWithTrack"] is None:
                continue
            folder = layers_by_uuid.get(_normalize_uuid(track["LayerUuidWithTrack"]))
            if folder is None:
                continue
            if "AnimationFolder" in layer_columns and not _row_number(
                folder, "AnimationFolder"
            ):
                # Camera folders and ordinary/keyframe tracks can also be
                # attached to the selected timeline. They are out of scope.
                continue
            if len(parsed_layers) >= MAX_PROJECT_LAYERS:
                raise ClipImportError(
                    "アニメーションフォルダー数がPMAの上限を超えています。"
                )
            children = _direct_children(layers_by_id, folder)
            raw_keys: list[tuple[float, str]] = []
            if track["TrackActionMixer"]:
                mixer_body = container.read_external(track["TrackActionMixer"])
                candidates = _parse_image_cel_curves(
                    _decompress_mixer(mixer_body)
                )
                matching = [
                    curve
                    for curve in candidates
                    if all(not tag or str(tag) in children for _time, tag in curve)
                ]
                if matching:
                    # The folder assignment normally has the full event list;
                    # short duplicate curves belong to action/keyframe data.
                    raw_keys = max(matching, key=len)
                elif candidates:
                    raw_keys = candidates[0]
            # Negative keys are not carried forward to frame zero. Their
            # images remain available in the sequence-cell archive below.
            keys = tuple(
                ClipCelKey(frame, str(tag))
                for time_60hz, tag in raw_keys
                for frame in (int(round(time_60hz * fps / 60.0)),)
                if frame >= 0
            )
            required_tags = {
                key.tag
                for key in keys
                if key.tag and start_frame <= key.frame <= end_frame
            }
            missing_tags = required_tags.difference(children)
            if missing_tags:
                missing = sorted(missing_tags)[0]
                raise ClipImportError(
                    f"アニメーションフォルダー「{folder['LayerName']}」に"
                    f"セル「{missing}」がありません。"
                )
            images: dict[str, QImage] = {}
            # Render every cel, including unused and negative-pre-roll-only
            # cels, so they survive as PMA sequence entries.
            for tag, child in children.items():
                images[tag] = _render_layer(
                    connection,
                    container,
                    child,
                    width,
                    height,
                    layers_by_id,
                )
            parsed_layers.append(
                ClipAnimationLayer(
                    str(folder["LayerName"] or f"Animation {len(parsed_layers) + 1}"),
                    keys,
                    images,
                )
            )
        if not parsed_layers:
            raise ClipImportError(".clip内にアニメーションフォルダーがありません。")
        layer_tuple = tuple(parsed_layers)
        frames = build_pma_frames(
            width, height, start_frame, end_frame, layer_tuple
        )
        return ClipAnimationDocument(
            Path(path).name,
            str(timeline["TimeLineName"] or ""),
            width,
            height,
            fps,
            start_frame,
            end_frame,
            layer_tuple,
            frames,
        )
    except sqlite3.Error as exc:
        raise ClipImportError(f".clipの文書情報を読み取れません。\n{exc}") from exc
    finally:
        connection.close()
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = [
    "ClipAnimationDocument",
    "ClipAnimationLayer",
    "ClipCelKey",
    "ClipContainer",
    "ClipImportError",
    "build_pma_frames",
    "cell_sequence_numbers",
    "parse_image_cel_curve",
    "read_clip_animation",
]
