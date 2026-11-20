"""Qt-independent project (.zip) serialization.

Reads/writes the PaintMaskAnimator project archive format (a zip holding
project.json plus one PNG per layer-cell). Works with Frame/Layer domain
objects and returns/consumes plain data, so MainWindow keeps only the
widget<->metadata mapping and the UI feedback.
"""
import json
from copy import deepcopy
from dataclasses import dataclass
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
import zipfile
from pathlib import Path, PurePosixPath

from . import native
from .i18n import tr
from .document import Document, project_v1_state
from .errors import OperationError

from .constants import (
    MAX_PROJECT_ARCHIVE_BYTES,
    MAX_PROJECT_DECODED_PIXELS,
    MAX_PROJECT_FRAMES,
    MAX_PROJECT_IMAGE_BYTES,
    MAX_PROJECT_LAYER_CELLS,
    MAX_PROJECT_LAYERS,
    MAX_PROJECT_METADATA_BYTES,
)
from .imaging import white_to_transparent_qimage
from .models import Frame, default_layer_name
from .legacy_v1 import raw_v1_slot as Layer
from .pma_image import ImageInput, PmaImage, copy_image, image_key
from .pma_image_io import _decode_png, thumbnail_png
from .pma_image_io import _decode_png_qt as _decode_png_qt
from .pma_image_io import _encode_png_qt as _encode_png_qt
from .pma_image_io import encode_png as encode_png
from .tween_groups import sanitize_spec
from .utils import blank_image
from .logging_setup import get_logger

# Bump when the on-disk pixel contract changes. mask_format >= 2 stores erased
# areas as alpha=0; format 1 (or missing) stored them as pseudo-transparent
# opaque #FFFFFF and needs a white->transparent migration on load.
SUPPORTED_FORMAT_VERSION = 2
CURRENT_MASK_FORMAT = 2

log = get_logger(__name__)

# ファイルのアイコンに使う縮小画像。Finder（Quick Look の拡張）とエクスプローラー
# （サムネイルの拡張）が zip を解かずに読めるよう、無圧縮でアーカイブの先頭に置く。
THUMBNAIL_NAME = "thumbnail.png"
THUMBNAIL_SIZE = 512


@dataclass(frozen=True)
class LoadedProject:
    """Fully installed document, still private until UI state is prepared."""

    metadata: dict
    document: Document
    width: int
    height: int
    format_version: int = 1


def load_project(path, png_cache=None):
    """Adapt the legacy reader without changing its raw four-value API.

    Use a private cache while preparing an open; publish it only on success.
    Cursor selection must precede bank ownership/initial/name selection.
    """
    read_cache = PngCache() if png_cache is not None else None
    result = read_project_archive(path, read_cache, _allow_v2=True)
    if isinstance(result, LoadedProject):
        if png_cache is not None:
            png_cache.replace_from(read_cache)
        return result
    metadata, frames, width, height = result
    metadata = dict(metadata)
    document = object.__new__(Document)
    try:
        document.current_frame = max(0, min(int(metadata.get("current_frame", 0)), len(frames) - 1))
        document.active_layer_index = max(0, min(
            int(metadata.get("active_layer_index", 0)),
            len(frames[document.current_frame].layers) - 1))
    except (TypeError, ValueError, OverflowError) as error:
        raise OperationError(tr("プロジェクトの選択位置が不正です。")) from error
    document.install_legacy_frames(
        frames, metadata.pop("_loaded_sequence_archive", {}), canvas_size=(width, height))
    if png_cache is not None:
        png_cache.replace_from(read_cache)
    return LoadedProject(metadata, document, width, height)


@dataclass(frozen=True)
class ProjectSnapshot:
    """Detached save-owned graph; images share pixels through Qt COW."""

    metadata: dict
    document: Document
    width: int
    height: int
    thumbnail: ImageInput = None

    @classmethod
    def capture(cls, metadata, document, thumbnail=None):
        from . import constants
        return cls(deepcopy(metadata), document.clone_graph(),
                   constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT,
                   copy_image(thumbnail))


def build_v2_payload(snapshot, *, include_default_local_images=False):
    """Prepare explicit v2 metadata and PNGs without changing the snapshot."""
    from .project_v2 import build_payload
    return build_payload(snapshot, include_default_local_images=include_default_local_images)


def write_project_snapshot(path, snapshot, png_cache=None):
    """Save the current format (v2); legacy fixture output is separate."""
    from .project_v2 import write_snapshot
    return write_snapshot(path, snapshot, png_cache)


def legacy_fixture_write_snapshot(path, snapshot, png_cache=None):
    """Test/fixture-only v1 output; never use for user saves."""
    metadata = deepcopy(snapshot.metadata)
    metadata["format_version"] = 1
    legacy_fixture_write_archive(
        path, metadata, snapshot.document.frames, {}, png_cache,
        **({"thumbnail": snapshot.thumbnail} if snapshot.thumbnail is not None else {}))


class PngCache:
    """変更のないセルの PNG を保存のたびに再エンコードしないためのキャッシュ。

    モデルの画素世代でキーを引く。Qt 画像が渡された場合は別の名前空間で
    Qt のキーを使う。保存のたびに今回使ったセルだけを残す。
    """

    def __init__(self):
        self._entries = {}

    def __len__(self):
        return len(self._entries)

    def replace_from(self, other):
        """Publish a prepared read cache without exposing partially loaded state."""
        self._entries = dict(other._entries)

    def clear(self):
        self._entries.clear()

    def remember(self, image, data):
        self._entries[image_key(image)] = bytes(data)

    def encode_many(self, images, used_keys=None):
        """``images`` の PNG を ``{image_key: bytes | None}`` で返す。

        キャッシュに無いものだけを、複数のスレッドで同時にエンコードする。
        Rust の PNG エンコードも Qt の PNG 保存も実行中 GIL を手放すので、コア数に近い倍率で速くなる
        （1920x1080 で1枚約60ms かかり、全セルの書き出しが数十秒になっていた）。
        """
        result = {}
        missing = {}
        for image in images:
            key = image_key(image)
            if used_keys is not None:
                used_keys.add(key)
            data = self._entries.get(key)
            if data is not None:
                result[key] = data
            elif key not in missing:
                missing[key] = image
        for key, data in zip(missing, parallel_map(encode_png, missing.values())):
            result[key] = data
            if data is not None:
                self._entries[key] = data
        return result

    def retain_only(self, keys):
        for key in [key for key in self._entries if key not in keys]:
            del self._entries[key]


def _install_decoded_image(image, installed, seeded, blob=None):
    """Give each cell its own handle to the generation shared by the decoder."""
    key = image_key(image)
    if key not in installed:
        installed[key] = PmaImage.from_qimage(image)
    model_image = installed[key]
    # A white migration with no changed pixels may share this decoded image.
    if blob is not None:
        seeded[model_image.generation] = (model_image, blob)
    return model_image.copy()


def parallel_map(function, items):
    """``map`` を複数スレッドで行う（順番は保つ）。数が少なければそのまま回す。

    画像のエンコード・デコードのように、GIL を手放す Qt の処理に使う。
    """
    items = list(items)
    workers = min(_MAX_WORKERS, os.cpu_count() or 1, len(items))
    if workers <= 1:
        return [function(item) for item in items]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(function, items))


_MAX_WORKERS = 8


def _prefetch_pngs(archive, entries, width, height, decoded):
    """``entries``（project.json の各セル）の PNG を先にまとめて並列に読む。

    ``{パス: (PNG のバイト列, 画像)}`` を返す。参照が不正なものはここでは
    読まずに飛ばし、本体のループで従来どおりのエラーにする。

    中身が同じ PNG（空のセルなど）は1回だけデコードし、画像を暗黙共有する。
    編集中も空のセルは1枚の雛形を共有しているので、読み込みで枚数分の画素を
    確保すると、保存できたプロジェクトが開けなくなる。展開後の画素数の上限も
    この中身の種類数で数える。``decoded``（{PNG のバイト列: 画像}）は
    呼び出しをまたいで共有する。
    """
    paths = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        image_path = str(entry.get("image") or "")
        normalized = PurePosixPath(image_path)
        if (
            not image_path
            or image_path in seen
            or normalized.is_absolute()
            or ".." in normalized.parts
            or normalized.suffix.lower() != ".png"
        ):
            continue
        try:
            info = archive.getinfo(image_path)
        except KeyError:
            continue
        if info.file_size > MAX_PROJECT_IMAGE_BYTES:
            continue
        seen.add(image_path)
        paths.append(image_path)
    blobs = [archive.read(image_path) for image_path in paths]
    pending = list(dict.fromkeys(data for data in blobs if data not in decoded))
    if (len(decoded) + len(pending)) * width * height > MAX_PROJECT_DECODED_PIXELS:
        raise OperationError(tr("プロジェクトの展開後画像サイズが大きすぎます。"))
    decoded.update(zip(pending, parallel_map(_decode_png, pending)))
    return {
        image_path: (data, decoded[data])
        for image_path, data in zip(paths, blobs)
    }


@dataclass(frozen=True)
class _RustZipInfo:
    filename: str
    file_size: int
    flag_bits: int


class _RustZipArchive:
    """Rust（pma_core::project::zip）で読み、GIL を手放す。

    Rust で開けないものは _open_project_zip が zipfile に回す。
    """

    def __init__(self, path):
        self._reader = native.ZipReader(os.fspath(path))
        self._path = path
        self._infos = [_RustZipInfo(*entry) for entry in self._reader.entries()]
        self._indices = {info.filename: i for i, info in enumerate(self._infos)}
        self._closed = False

    def infolist(self):
        return list(self._infos)

    def getinfo(self, name):
        try:
            return self._infos[self._indices[name]]
        except KeyError:
            raise KeyError(f"There is no item named {name!r} in the archive") from None

    def read(self, name):
        if self._closed:
            raise ValueError("Attempt to use ZIP archive that was already closed")
        info = self.getinfo(name)
        try:
            return self._reader.read(self._indices[name])
        except RuntimeError as error:
            # 失敗時だけ既存実装の診断を使い、CRC 等の文言も維持する。
            # 全体の read() は使わず宣言サイズ+1まで。成功してもRustの失敗を隠さない。
            try:
                with zipfile.ZipFile(self._path, "r") as archive:
                    with archive.open(name) as source:
                        remaining = info.file_size + 1
                        while remaining:
                            chunk = source.read(min(remaining, 65536))
                            if not chunk:
                                break
                            remaining -= len(chunk)
            except zipfile.BadZipFile:
                raise
            except Exception as diagnostic:
                raise zipfile.BadZipFile(str(diagnostic)) from error
            raise zipfile.BadZipFile(str(error)) from error

    def close(self):
        self._reader.close()
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _RustZipWriter:
    """Rust（pma_core::project::zip）で書き、GIL を手放す。"""

    def __init__(self, path):
        self._writer = native.ZipWriter(os.fspath(path))

    def writestr(self, name, data, compress_type=zipfile.ZIP_DEFLATED):
        date_time = time.localtime(time.time())[:6]
        if date_time[0] < 1980:
            raise ValueError("ZIP does not support timestamps before 1980")
        if compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise NotImplementedError("That compression method is not supported")
        self._writer.write(name, data, compress_type == zipfile.ZIP_DEFLATED, date_time)

    def close(self):
        self._writer.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.close()
        else:
            try:
                self.close()
            except Exception:
                pass  # 本体の例外を保持する（close の失敗で置き換えない）。


def _create_project_zip(path):
    """Rust（pma_core::project::zip）で書き、GIL を手放す。"""
    return _RustZipWriter(path)


def _open_project_zip(path):
    """Rust（pma_core::project::zip）で読み、GIL を手放す。Rust で開けないものは zipfile に回す。"""
    try:
        return _RustZipArchive(path)
    except ValueError:
        return zipfile.ZipFile(path, "r")


def read_project_archive(path, png_cache=None, *, _allow_v2=False):
    """Open and validate a project archive.

    Returns (metadata, frames, width, height). App version fields in metadata
    are strings or None. Raises OperationError for unsupported formats and
    malformed/oversized/unsafe archives (the caller shows the UI error).

    ``png_cache`` (a ``PngCache``) is reset and seeded with each cell's PNG
    bytes from the archive, so the first save after opening only re-encodes
    the cells that were edited.
    """
    # 途中で読み込みに失敗したときに今のキャッシュを壊さないよう、
    # 成功してから差し替える。
    seeded = {}
    project_path = Path(path)
    with _open_project_zip(project_path) as archive:
        archive_entries = archive.infolist()
        # セルの画像と、保持した初期状態（あれば）で、セル1つにつき最大2ファイル。
        # ほかに project.json とサムネイル。
        if len(archive_entries) > 2 * MAX_PROJECT_LAYER_CELLS + 2:
            raise OperationError(tr("プロジェクト内のファイル数が多すぎます。"))
        if any(info.flag_bits & 0x1 for info in archive_entries):
            raise OperationError(tr("暗号化されたプロジェクトには対応していません。"))
        if sum(int(info.file_size) for info in archive_entries) > MAX_PROJECT_ARCHIVE_BYTES:
            raise OperationError(tr("プロジェクトの展開後サイズが大きすぎます。"))
        entry_names = [info.filename for info in archive_entries]
        if len(entry_names) != len(set(entry_names)):
            raise OperationError(tr("プロジェクト内に重複したファイル名があります。"))
        try:
            metadata_info = archive.getinfo("project.json")
        except KeyError as error:
            raise OperationError(tr("project.jsonがありません。")) from error
        if metadata_info.file_size > MAX_PROJECT_METADATA_BYTES:
            raise OperationError(tr("プロジェクト情報が大きすぎます。"))
        duplicates = []
        def pairs_hook(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    duplicates.append(key)
                result[key] = value
            return result
        metadata = json.loads(archive.read("project.json").decode("utf-8"),
                              object_pairs_hook=pairs_hook)
        if not isinstance(metadata, dict):
            raise OperationError(tr("対応していないプロジェクト形式です。"))
        if metadata.get("format") != "PaintMaskAnimatorProject":
            raise OperationError(tr("対応していないプロジェクト形式です。"))

        format_version = metadata.get("format_version", 1)
        if type(format_version) is not int or format_version < 1:
            raise OperationError(tr("対応していないプロジェクト形式です。"))
        for key in ("created_app_version", "saved_app_version"):
            if not isinstance(metadata.get(key), str):
                metadata[key] = None
        if format_version > (SUPPORTED_FORMAT_VERSION if _allow_v2 else 1):
            version = metadata["saved_app_version"]
            if version:
                raise OperationError(tr(
                    "このファイルは新しいバージョンのアプリ（v{version}）で保存されています。アプリを更新してから開いてください。"
                ).format(version=version))
            raise OperationError(tr(
                "このファイルは新しいバージョンのアプリで保存されています。アプリを更新してから開いてください。"
            ))

        if format_version == 2:
            if duplicates:
                raise OperationError(tr("プロジェクト情報に重複したキーがあります。"))
            if not _allow_v2:
                raise OperationError(tr("対応していないプロジェクト形式です。"))
            from .project_v2 import decode_payload
            return decode_payload(metadata, archive, png_cache)

        try:
            mask_format = int(metadata.get("mask_format", 1))
        except (TypeError, ValueError):
            mask_format = 1
        # Legacy files stored erased pixels as opaque #FFFFFF; convert them to
        # real transparency so they read the same now that pseudo-transparency
        # is gone. The paper layer legitimately stays white and is skipped.
        needs_white_migration = mask_format < CURRENT_MASK_FORMAT

        canvas_data = metadata.get("canvas", {})
        width = int(canvas_data.get("width", 1280))
        height = int(canvas_data.get("height", 720))
        if not (1 <= width <= 16384 and 1 <= height <= 16384):
            raise OperationError(tr("キャンバスサイズが不正です。"))

        loaded_frames = []
        frame_entries = metadata.get("frames", [])
        if not isinstance(frame_entries, list) or not frame_entries:
            raise OperationError(tr("フレーム情報がありません。"))
        if len(frame_entries) > MAX_PROJECT_FRAMES:
            raise OperationError(tr("フレーム数が上限を超えています。"))

        expected_layer_count = None
        layer_cell_count = 0
        for frame_data in frame_entries:
            if not isinstance(frame_data, dict):
                raise OperationError(tr("フレーム情報が不正です。"))
            layer_entries = frame_data.get("layers", [])
            if not isinstance(layer_entries, list) or not layer_entries:
                raise OperationError(tr("レイヤー情報がありません。"))
            if len(layer_entries) > MAX_PROJECT_LAYERS:
                raise OperationError(tr("レイヤー数が上限を超えています。"))
            if expected_layer_count is None:
                expected_layer_count = len(layer_entries)
            elif len(layer_entries) != expected_layer_count:
                raise OperationError(tr("フレームごとのレイヤー数が一致していません。"))
            layer_cell_count += len(layer_entries)
        if layer_cell_count > MAX_PROJECT_LAYER_CELLS:
            raise OperationError(tr("プロジェクトのセル数が上限を超えています。"))

        decoded = {}
        # 旧形式の白→透明の変換も、共有している画像ごとに1回だけ行う。
        migrated = {}
        installed = {}

        def migrate_white(image):
            key = image_key(image)
            if key not in migrated:
                migrated[key] = white_to_transparent_qimage(image)
            return migrated[key]

        prefetched = _prefetch_pngs(
            archive,
            (
                layer_data
                for frame_data in frame_entries
                for layer_data in frame_data.get("layers", [])
            ),
            width,
            height,
            decoded,
        )
        def load_initial(layer_data):
            """保持した色塗り前の初期状態。無ければ None。"""
            line_path = layer_data.get("initial")
            if not line_path:
                return None
            line_path = str(line_path)
            normalized = PurePosixPath(line_path)
            if (
                normalized.is_absolute()
                or ".." in normalized.parts
                or normalized.suffix.lower() != ".png"
            ):
                raise OperationError(tr("初期状態の画像の参照パスが不正です。"))
            try:
                line_info = archive.getinfo(line_path)
            except KeyError as error:
                raise OperationError(
                    tr("初期状態の画像がありません: {path}").format(path=line_path)
                ) from error
            if line_info.file_size > MAX_PROJECT_IMAGE_BYTES:
                raise OperationError(tr("初期状態の画像が大きすぎます: {path}").format(path=line_path))
            line_bytes = archive.read(line_path)
            line_image = _decode_png(line_bytes)
            if (
                line_image.isNull()
                or line_image.width() != width
                or line_image.height() != height
            ):
                raise OperationError(
                    tr("初期状態の画像を復元できません: {path}").format(path=line_path)
                )
            return _install_decoded_image(line_image, installed, seeded,
                                          line_bytes if png_cache is not None else None)

        for frame_data in frame_entries:
            loaded_layers = []
            for layer_data in frame_data.get("layers", []):
                if not isinstance(layer_data, dict):
                    raise OperationError(tr("レイヤー情報が不正です。"))
                image_path = layer_data.get("image")
                if not image_path:
                    raise OperationError(tr("レイヤー画像の参照がありません。"))
                image_path = str(image_path)
                normalized_path = PurePosixPath(image_path)
                if (
                    normalized_path.is_absolute()
                    or ".." in normalized_path.parts
                    or normalized_path.suffix.lower() != ".png"
                ):
                    raise OperationError(tr("レイヤー画像の参照パスが不正です。"))
                try:
                    image_info = archive.getinfo(image_path)
                except KeyError as error:
                    raise OperationError(
                        tr("レイヤー画像がありません: {path}").format(path=image_path)
                    ) from error
                if image_info.file_size > MAX_PROJECT_IMAGE_BYTES:
                    raise OperationError(
                        tr("レイヤー画像が大きすぎます: {path}").format(path=image_path)
                    )
                if image_path in prefetched:
                    image_bytes, image = prefetched[image_path]
                else:
                    image_bytes = archive.read(image_path)
                    image = _decode_png(image_bytes)
                if image.isNull():
                    raise OperationError(
                        tr("レイヤー画像を復元できません: {path}").format(path=image_path)
                    )
                if image.width() != width or image.height() != height:
                    raise OperationError(
                        tr("レイヤー画像のサイズが不正です: {path}").format(path=image_path)
                    )
                is_draft = bool(layer_data.get("is_draft", False))
                # 下書きレイヤーは読み込んだ画素をそのまま保持するため、
                # 用紙レイヤーと同様に白→透明の移行対象から除外する。
                if (
                    needs_white_migration
                    and not bool(layer_data.get("is_paper", False))
                    and not is_draft
                ):
                    image = migrate_white(image)
                    image_bytes = None
                image = _install_decoded_image(image, installed, seeded,
                                               image_bytes if png_cache is not None else None)

                filter_rgb = layer_data.get("color_filter_rgb")
                exposure = int(layer_data.get("exposure", 1))
                if not (1 <= exposure <= MAX_PROJECT_FRAMES):
                    raise OperationError(tr("レイヤーの露出フレーム数が不正です。"))
                sequence_number = layer_data.get("sequence_number")
                if sequence_number is not None:
                    sequence_number = int(sequence_number)
                    if not (1 <= sequence_number <= MAX_PROJECT_FRAMES):
                        raise OperationError(tr("絵番号が範囲外です。"))
                opacity = float(layer_data.get("opacity", 1.0))
                if not math.isfinite(opacity):
                    raise OperationError(tr("レイヤー不透明度が不正です。"))
                loaded_layers.append(
                    Layer(
                        str(layer_data.get("name", "Layer")),
                        image,
                        bool(layer_data.get("visible", True)),
                        max(0.0, min(1.0, opacity)),
                        bool(layer_data.get("is_paper", False)),
                        bool(layer_data.get("has_content", False)),
                        False,  # legacy alpha lock is intentionally ignored
                        exposure,
                        bool(
                            layer_data.get(
                                "color_filter_enabled",
                                False,
                            )
                        ),
                        (
                            tuple(int(v) for v in filter_rgb[:3])
                            if filter_rgb is not None
                            else None
                        ),
                        bool(
                            layer_data.get(
                                "is_blank_key",
                                (
                                    not bool(
                                        layer_data.get(
                                            "has_content",
                                            False,
                                        )
                                    )
                                    and max(
                                        1,
                                        int(
                                            layer_data.get(
                                                "exposure",
                                                1,
                                            )
                                        ),
                                    ) > 1
                                ),
                            )
                        ),
                        (
                            sequence_number
                        ),
                        bool(layer_data.get("sequence_only", False)),
                        is_draft,
                        (
                            str(layer_data.get("cell_name"))
                            if layer_data.get("cell_name") is not None
                            else None
                        ),
                        sanitize_spec(layer_data.get("tween")),
                        (
                            str(layer_data.get("tween_member"))
                            if layer_data.get("tween_member") is not None
                            else None
                        ),
                        load_initial(layer_data),
                    )
                )

            if not loaded_layers:
                loaded_layers = [Layer(default_layer_name(0), blank_image())]
            loaded_frames.append(
                Frame(
                    loaded_layers,
                    max(
                        1,
                        min(
                            MAX_PROJECT_FRAMES,
                            int(frame_data.get("duration", 1)),
                        ),
                    ),
                )
            )
        sequence_archive_data = metadata.get("sequence_archive", [])
        if not isinstance(sequence_archive_data, list):
            raise OperationError(tr("連番保管セル情報が不正です。"))
        if layer_cell_count + len(sequence_archive_data) > MAX_PROJECT_LAYER_CELLS:
            raise OperationError(tr("プロジェクトのセル数が上限を超えています。"))
        loaded_sequence_archive = {}
        prefetched = _prefetch_pngs(
            archive, sequence_archive_data, width, height, decoded
        )
        for archive_data in sequence_archive_data:
            if not isinstance(archive_data, dict):
                raise OperationError(tr("連番保管セル情報が不正です。"))
            layer_index = int(archive_data.get("layer_index", -1))
            number = int(archive_data.get("sequence_number", 0))
            if not (
                expected_layer_count is not None
                and 0 <= layer_index < expected_layer_count
                and 1 <= number <= MAX_PROJECT_FRAMES
            ):
                raise OperationError(tr("連番保管セルのレイヤーまたは絵番号が不正です。"))
            key = (layer_index, number)
            if key in loaded_sequence_archive:
                raise OperationError(tr("連番保管セルの絵番号が重複しています。"))
            image_path = str(archive_data.get("image", ""))
            normalized_path = PurePosixPath(image_path)
            if (
                not image_path
                or normalized_path.is_absolute()
                or ".." in normalized_path.parts
                or normalized_path.suffix.lower() != ".png"
            ):
                raise OperationError(tr("連番保管セル画像の参照パスが不正です。"))
            try:
                image_info = archive.getinfo(image_path)
            except KeyError as error:
                raise OperationError(
                    tr("連番保管セル画像がありません: {path}").format(path=image_path)
                ) from error
            if image_info.file_size > MAX_PROJECT_IMAGE_BYTES:
                raise OperationError(
                    tr("連番保管セル画像が大きすぎます: {path}").format(path=image_path)
                )
            if image_path in prefetched:
                image_bytes, image = prefetched[image_path]
            else:
                image_bytes = archive.read(image_path)
                image = _decode_png(image_bytes)
            if (
                image.isNull()
                or image.width() != width
                or image.height() != height
            ):
                raise OperationError(
                    tr("連番保管セル画像を復元できません: {path}").format(path=image_path)
                )
            is_draft = bool(archive_data.get("is_draft", False))
            if needs_white_migration and not is_draft:
                image = migrate_white(image)
                image_bytes = None
            image = _install_decoded_image(image, installed, seeded,
                                           image_bytes if png_cache is not None else None)
            opacity = float(archive_data.get("opacity", 1.0))
            if not math.isfinite(opacity):
                raise OperationError(tr("連番保管セルの不透明度が不正です。"))
            filter_rgb = archive_data.get("color_filter_rgb")
            loaded_sequence_archive[key] = Layer(
                str(archive_data.get("name", "Layer")),
                image,
                bool(archive_data.get("visible", True)),
                max(0.0, min(1.0, opacity)),
                False,
                bool(archive_data.get("has_content", True)),
                False,
                1,
                bool(archive_data.get("color_filter_enabled", False)),
                (
                    tuple(int(value) for value in filter_rgb[:3])
                    if filter_rgb is not None
                    else None
                ),
                bool(archive_data.get("is_blank_key", False)),
                number,
                False,
                is_draft,
                (
                    str(archive_data.get("cell_name"))
                    if archive_data.get("cell_name") is not None
                    else None
                ),
            )
            loaded_sequence_archive[key].initial_image = load_initial(archive_data)
        metadata["_loaded_sequence_archive"] = loaded_sequence_archive
    if png_cache is not None:
        png_cache.clear()
        for image, image_bytes in seeded.values():
            png_cache.remember(image, image_bytes)
    return metadata, loaded_frames, width, height


def _thumbnail_png(image):
    return thumbnail_png(image, THUMBNAIL_SIZE)


def legacy_fixture_build_v1_payload(frames, sequence_archive=None):
    """Test/fixture-only v1 metadata and ordered (name, image, error) PNGs.

    Only reads the document. Encoding, caching and ZIP output belong to the
    writer; images here retain their original cache keys.
    """
    frame_metadata = []
    # まず書き出すセルを集め、PNG はあとでまとめて並列にエンコードする。
    pending_pngs = []

    def write_png(image_name, image, message):
        pending_pngs.append((image_name, copy_image(image), message))

    for frame_index, frame in enumerate(frames):
        frame_data = {
            "duration": int(frame.duration),
            "layers": [],
        }
        for layer_index, layer in enumerate(frame.layers):
            image_name = (
                f"layers/layer_{layer_index:04d}/"
                f"frame_{frame_index:06d}.png"
            )
            write_png(
                image_name,
                layer.image,
                tr("画像を書き出せませんでした: {name}"),
            )

            initial_name = None
            if getattr(layer, "initial_image", None) is not None:
                initial_name = (
                    f"initial/layer_{layer_index:04d}/"
                    f"frame_{frame_index:06d}.png"
                )
                write_png(
                    initial_name,
                    layer.initial_image,
                    tr("初期状態の画像を書き出せませんでした: {name}"),
                )

            frame_data["layers"].append(
                {
                    "name": layer.name,
                    "image": image_name,
                    "initial": initial_name,
                    "visible": bool(layer.visible),
                    "opacity": float(layer.opacity),
                    "is_paper": bool(layer.is_paper),
                    "has_content": bool(layer.has_content),
                    "exposure": int(layer.exposure),
                    "is_blank_key": bool(
                        getattr(layer, "is_blank_key", False)
                    ),
                    "sequence_number": layer.sequence_number,
                    "sequence_only": bool(layer.sequence_only),
                    "is_draft": bool(getattr(layer, "is_draft", False)),
                    "cell_name": getattr(layer, "cell_name", None),
                    "tween": getattr(layer, "tween", None),
                    "tween_member": getattr(layer, "tween_member", None),
                    "color_filter_enabled": bool(
                        layer.color_filter_enabled
                    ),
                    "color_filter_rgb": (
                        list(layer.color_filter_rgb)
                        if layer.color_filter_rgb is not None
                        else None
                    ),
                }
            )
        frame_metadata.append(frame_data)

    archive_metadata = []
    for (layer_index, number), layer in sorted(
        (sequence_archive or {}).items()
    ):
        image_name = (
            f"sequence_archive/layer_{int(layer_index):04d}/"
            f"cell_{int(number):06d}.png"
        )
        write_png(
            image_name,
            layer.image,
            tr("連番保管セルを書き出せませんでした: {name}"),
        )
        initial_name = None
        if layer.initial_image is not None:
            initial_name = f"initial/{image_name}"
            write_png(initial_name, layer.initial_image,
                      tr("初期状態の画像を書き出せませんでした: {name}"))
        archive_metadata.append(
            {
                "layer_index": int(layer_index),
                "sequence_number": int(number),
                "image": image_name,
                "initial": initial_name,
                "name": layer.name,
                "visible": bool(layer.visible),
                "opacity": float(layer.opacity),
                "has_content": bool(layer.has_content),
                "is_blank_key": bool(layer.is_blank_key),
                "is_draft": bool(getattr(layer, "is_draft", False)),
                "cell_name": getattr(layer, "cell_name", None),
                "color_filter_enabled": bool(
                    layer.color_filter_enabled
                ),
                "color_filter_rgb": (
                    list(layer.color_filter_rgb)
                    if layer.color_filter_rgb is not None
                    else None
                ),
            }
        )
    return frame_metadata, archive_metadata, pending_pngs


def legacy_fixture_write_archive(
    project_path, metadata, frames, sequence_archive=None, png_cache=None,
    thumbnail=None,
):
    """Test/fixture-only v1 serialization into ``project_path``.

    Fills ``metadata["frames"]`` from the Frame/Layer domain objects, writes one
    PNG per layer-cell plus project.json into a zip, and atomically replaces the
    destination. Raises on error (the caller shows the UI message).

    With ``png_cache`` (a ``PngCache``), cells unchanged since the last save or
    open reuse their PNG bytes instead of being re-encoded. PNG entries are
    stored without zip compression: they are already deflated, so compressing
    them again only costs time.

    ``thumbnail`` (an image, e.g. the current frame) is shrunk and stored as
    the first entry so the OS file-icon extensions can find it cheaply.
    """
    project_path = Path(project_path)
    metadata.pop("_loaded_sequence_archive", None)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = project_path.with_suffix(project_path.suffix + ".tmp")
    cache = png_cache if png_cache is not None else PngCache()
    used_keys = set()
    try:
        with zipfile.ZipFile(
            temporary_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            thumbnail_png = _thumbnail_png(thumbnail)
            if thumbnail_png is not None:
                archive.writestr(
                    THUMBNAIL_NAME, thumbnail_png, compress_type=zipfile.ZIP_STORED
                )

            frame_data, archive_data, pending_pngs = legacy_fixture_build_v1_payload(
                *project_v1_state(frames, sequence_archive)
            )
            metadata["frames"].extend(frame_data)
            metadata["sequence_archive"] = archive_data

            encoded = cache.encode_many(
                (image for _name, image, _message in pending_pngs), used_keys
            )
            for image_name, image, message in pending_pngs:
                data = encoded.get(image_key(image))
                if data is None:
                    raise OperationError(message.format(name=image_name))
                archive.writestr(
                    image_name, data, compress_type=zipfile.ZIP_STORED
                )

            archive.writestr(
                "project.json",
                json.dumps(
                    metadata,
                    ensure_ascii=False,
                    indent=2,
                ).encode("utf-8"),
            )

        temporary_path.replace(project_path)
    except Exception:
        # Any failure while writing leaves a half-written temp file; remove it
        # and re-raise so the caller sees the original error.
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError as exc:
            log.debug("could not remove temp project file: %s", exc)
        raise
    # 消えたセル・描き換えたセルの古いPNGを持ち続けない。
    cache.retain_only(used_keys)
