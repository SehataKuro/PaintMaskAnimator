"""Qt-independent project (.zip) serialization.

Reads/writes the PaintMaskAnimator project archive format (a zip holding
project.json plus one PNG per layer-cell). Works with Frame/Layer domain
objects and returns/consumes plain data, so MainWindow keeps only the
widget<->metadata mapping and the UI feedback.
"""
import json
import math
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .i18n import tr
from .errors import OperationError

from PySide6.QtGui import QImage

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
from .utils import blank_image
from .logging_setup import get_logger

# Bump when the on-disk pixel contract changes. mask_format >= 2 stores erased
# areas as alpha=0; format 1 (or missing) stored them as pseudo-transparent
# opaque #FFFFFF and needs a white->transparent migration on load.
CURRENT_MASK_FORMAT = 2

log = get_logger(__name__)


def read_project_archive(path):
    """Open and validate a project archive.

    Returns (metadata, frames, width, height). Raises ValueError on any
    malformed/oversized/unsafe archive (the caller shows the UI error).
    """
    project_path = Path(path)
    with zipfile.ZipFile(project_path, "r") as archive:
        archive_entries = archive.infolist()
        if len(archive_entries) > MAX_PROJECT_LAYER_CELLS + 1:
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
        if width * height * layer_cell_count > MAX_PROJECT_DECODED_PIXELS:
            raise OperationError(tr("プロジェクトの展開後画像サイズが大きすぎます。"))

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
                image_bytes = archive.read(image_path)
                image = QImage.fromData(image_bytes)
                if image.isNull():
                    raise OperationError(
                        tr("レイヤー画像を復元できません: {path}").format(path=image_path)
                    )
                if image.width() != width or image.height() != height:
                    raise OperationError(
                        tr("レイヤー画像のサイズが不正です: {path}").format(path=image_path)
                    )
                image = image.convertToFormat(
                    QImage.Format.Format_ARGB32_Premultiplied
                )
                is_draft = bool(layer_data.get("is_draft", False))
                # 下書きレイヤーは読み込んだ画素をそのまま保持するため、
                # 用紙レイヤーと同様に白→透明の移行対象から除外する。
                if (
                    needs_white_migration
                    and not bool(layer_data.get("is_paper", False))
                    and not is_draft
                ):
                    image = white_to_transparent_qimage(image)

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
        if (
            width
            * height
            * (layer_cell_count + len(sequence_archive_data))
            > MAX_PROJECT_DECODED_PIXELS
        ):
            raise OperationError(tr("プロジェクトの展開後画像サイズが大きすぎます。"))
        loaded_sequence_archive = {}
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
            image = QImage.fromData(archive.read(image_path))
            if (
                image.isNull()
                or image.width() != width
                or image.height() != height
            ):
                raise OperationError(
                    tr("連番保管セル画像を復元できません: {path}").format(path=image_path)
                )
            image = image.convertToFormat(
                QImage.Format.Format_ARGB32_Premultiplied
            )
            is_draft = bool(archive_data.get("is_draft", False))
            if needs_white_migration and not is_draft:
                image = white_to_transparent_qimage(image)
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
    return metadata, loaded_frames, width, height


def write_project_archive(
    project_path, metadata, frames, sequence_archive=None
):
    """Serialize frames + metadata into the project archive at ``project_path``.

    Fills ``metadata["frames"]`` from the Frame/Layer domain objects, writes one
    PNG per layer-cell plus project.json into a zip, and atomically replaces the
    destination. Raises on error (the caller shows the UI message).
    """
    project_path = Path(project_path)
    metadata.pop("_loaded_sequence_archive", None)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = project_path.with_suffix(project_path.suffix + ".tmp")
    try:
        with tempfile.TemporaryDirectory() as temp_directory:
            temp_root = Path(temp_directory)
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
                    local_image = temp_root / image_name
                    local_image.parent.mkdir(parents=True, exist_ok=True)
                    if not layer.image.save(str(local_image), "PNG"):
                        raise OperationError(
                            tr("画像を書き出せませんでした: {name}").format(name=image_name)
                        )

                    frame_data["layers"].append(
                        {
                            "name": layer.name,
                            "image": image_name,
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
                local_image = temp_root / image_name
                local_image.parent.mkdir(parents=True, exist_ok=True)
                if not layer.image.save(str(local_image), "PNG"):
                    raise OperationError(
                        tr("連番保管セルを書き出せませんでした: {name}").format(name=image_name)
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

            with zipfile.ZipFile(
                temporary_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                archive.writestr(
                    "project.json",
                    json.dumps(
                        metadata,
                        ensure_ascii=False,
                        indent=2,
                    ).encode("utf-8"),
                )
                for local_file in temp_root.rglob("*.png"):
                    archive.write(
                        local_file,
                        local_file.relative_to(temp_root).as_posix(),
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
