"""Qt-independent project (.zip) serialization.

Reads/writes the PaintMaskAnimator project archive format (a zip holding
project.json plus one PNG per layer-cell). Works with Frame/Layer domain
objects and returns/consumes plain data, so MainWindow keeps only the
widget<->metadata mapping and the UI feedback.
"""
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
import zipfile
from pathlib import Path, PurePosixPath

from .i18n import tr
from .errors import OperationError

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage, QImageReader

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
from .models import Frame, Layer, default_layer_name
from .tween_groups import sanitize_spec
from .utils import blank_image
from .logging_setup import get_logger

# Bump when the on-disk pixel contract changes. mask_format >= 2 stores erased
# areas as alpha=0; format 1 (or missing) stored them as pseudo-transparent
# opaque #FFFFFF and needs a white->transparent migration on load.
CURRENT_MASK_FORMAT = 2

log = get_logger(__name__)


class PngCache:
    """変更のないセルの PNG を保存のたびに再エンコードしないためのキャッシュ。

    キーは ``QImage.cacheKey()``。QImage は画素を書き換えると（QPainter や
    ``bits()`` 経由でも）キーが変わるので、キーが同じなら前回のPNGをそのまま
    使える。保存のたびに今回使ったセルだけを残し、消えたセルの分は捨てる。
    """

    def __init__(self):
        self._entries = {}

    def __len__(self):
        return len(self._entries)

    def clear(self):
        self._entries.clear()

    def remember(self, image, data):
        self._entries[int(image.cacheKey())] = bytes(data)

    def encode_many(self, images, used_keys=None):
        """``images`` の PNG を ``{cacheKey: bytes | None}`` で返す。

        キャッシュに無いものだけを、複数のスレッドで同時にエンコードする。
        QImage.save は実行中 GIL を手放すので、コア数に近い倍率で速くなる
        （1920x1080 で1枚約60ms かかり、全セルの書き出しが数十秒になっていた）。
        """
        result = {}
        missing = {}
        for image in images:
            key = int(image.cacheKey())
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


def parallel_map(function, items):
    """``map`` を複数スレッドで行う（順番は保つ）。数が少なければそのまま回す。

    QImage のエンコード・デコードのように、GIL を手放す Qt の処理に使う。
    """
    items = list(items)
    workers = min(_MAX_WORKERS, os.cpu_count() or 1, len(items))
    if workers <= 1:
        return [function(item) for item in items]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(function, items))


_MAX_WORKERS = 8


def encode_png(image):
    """QImage を PNG のバイト列にする。失敗したら None。"""
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    try:
        if not image.save(buffer, "PNG"):
            return None
    finally:
        buffer.close()
    return bytes(data.data())


def _decode_png(data):
    """PNG を読み、キャンバスと同じ形式（乗算済み ARGB）にする。失敗なら null。

    QImage.fromData は読み込み中も GIL を握ったままなので、スレッドに分けても
    速くならない。QImageReader.read は GIL を手放すので、こちらを使う。
    """
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    try:
        image = QImageReader(buffer, b"png").read()
    finally:
        buffer.close()
    if image.isNull():
        return image
    return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


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


def read_project_archive(path, png_cache=None):
    """Open and validate a project archive.

    Returns (metadata, frames, width, height). Raises ValueError on any
    malformed/oversized/unsafe archive (the caller shows the UI error).

    ``png_cache`` (a ``PngCache``) is reset and seeded with each cell's PNG
    bytes from the archive, so the first save after opening only re-encodes
    the cells that were edited.
    """
    # 途中で読み込みに失敗したときに今のキャッシュを壊さないよう、
    # 成功してから差し替える。
    seeded = []
    project_path = Path(path)
    with zipfile.ZipFile(project_path, "r") as archive:
        archive_entries = archive.infolist()
        # セルの画像と、保持した初期状態（あれば）で、セル1つにつき最大2ファイル。
        if len(archive_entries) > 2 * MAX_PROJECT_LAYER_CELLS + 1:
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
        metadata = json.loads(
            archive.read("project.json").decode("utf-8")
        )
        if metadata.get("format") != "PaintMaskAnimatorProject":
            raise OperationError(tr("対応していないプロジェクト形式です。"))

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

        def migrate_white(image):
            key = image.cacheKey()
            if key not in migrated:
                migrated[key] = white_to_transparent_qimage(image)
            return QImage(migrated[key])

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
            if png_cache is not None:
                seeded.append((line_image, line_bytes))
            return line_image

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
                    # セルごとに別の QImage にする（画素は書き込むまで共有される）。
                    image = QImage(image)
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
                elif png_cache is not None:
                    seeded.append((image, image_bytes))

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
                image = QImage(image)
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
            elif png_cache is not None:
                seeded.append((image, image_bytes))
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
        metadata["_loaded_sequence_archive"] = loaded_sequence_archive
    if png_cache is not None:
        png_cache.clear()
        for image, image_bytes in seeded:
            png_cache.remember(image, image_bytes)
    return metadata, loaded_frames, width, height


def write_project_archive(
    project_path, metadata, frames, sequence_archive=None, png_cache=None
):
    """Serialize frames + metadata into the project archive at ``project_path``.

    Fills ``metadata["frames"]`` from the Frame/Layer domain objects, writes one
    PNG per layer-cell plus project.json into a zip, and atomically replaces the
    destination. Raises on error (the caller shows the UI message).

    With ``png_cache`` (a ``PngCache``), cells unchanged since the last save or
    open reuse their PNG bytes instead of being re-encoded. PNG entries are
    stored without zip compression: they are already deflated, so compressing
    them again only costs time.
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

            # まず書き出すセルを集め、PNG はあとでまとめて並列にエンコードする。
            pending_pngs = []

            def write_png(image_name, image, message):
                pending_pngs.append((image_name, image, message))

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
                metadata["frames"].append(frame_data)

            metadata["sequence_archive"] = []
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
                metadata["sequence_archive"].append(
                    {
                        "layer_index": int(layer_index),
                        "sequence_number": int(number),
                        "image": image_name,
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

            encoded = cache.encode_many(
                (image for _name, image, _message in pending_pngs), used_keys
            )
            for image_name, image, message in pending_pngs:
                data = encoded.get(int(image.cacheKey()))
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
