"""Qt-independent project (.zip) serialization.

Reads/writes the PaintMaskAnimator project archive format (a zip holding
project.json plus one PNG per layer-cell). Works with Frame/Layer domain
objects and returns/consumes plain data, so MainWindow keeps only the
widget<->metadata mapping and the UI feedback.
"""
from .common import *  # noqa: F401,F403
from . import constants
from .models import Frame, Layer
from .utils import blank_image
from .logging_setup import get_logger

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
            raise ValueError("プロジェクト内のファイル数が多すぎます。")
        if any(info.flag_bits & 0x1 for info in archive_entries):
            raise ValueError("暗号化されたプロジェクトには対応していません。")
        if sum(int(info.file_size) for info in archive_entries) > MAX_PROJECT_ARCHIVE_BYTES:
            raise ValueError("プロジェクトの展開後サイズが大きすぎます。")
        entry_names = [info.filename for info in archive_entries]
        if len(entry_names) != len(set(entry_names)):
            raise ValueError("プロジェクト内に重複したファイル名があります。")
        try:
            metadata_info = archive.getinfo("project.json")
        except KeyError as error:
            raise ValueError("project.jsonがありません。") from error
        if metadata_info.file_size > MAX_PROJECT_METADATA_BYTES:
            raise ValueError("プロジェクト情報が大きすぎます。")
        metadata = json.loads(
            archive.read("project.json").decode("utf-8")
        )
        if metadata.get("format") not in (
            "PaintMaskAnimatorProject",
            "OekakiAnimationProject",
        ):
            raise ValueError("対応していないプロジェクト形式です。")

        canvas_data = metadata.get("canvas", {})
        width = int(canvas_data.get("width", 1280))
        height = int(canvas_data.get("height", 720))
        if not (1 <= width <= 16384 and 1 <= height <= 16384):
            raise ValueError("キャンバスサイズが不正です。")

        loaded_frames = []
        frame_entries = metadata.get("frames", [])
        if not isinstance(frame_entries, list) or not frame_entries:
            raise ValueError("フレーム情報がありません。")
        if len(frame_entries) > MAX_PROJECT_FRAMES:
            raise ValueError("フレーム数が上限を超えています。")

        expected_layer_count = None
        layer_cell_count = 0
        for frame_data in frame_entries:
            if not isinstance(frame_data, dict):
                raise ValueError("フレーム情報が不正です。")
            layer_entries = frame_data.get("layers", [])
            if not isinstance(layer_entries, list) or not layer_entries:
                raise ValueError("レイヤー情報がありません。")
            if len(layer_entries) > MAX_PROJECT_LAYERS:
                raise ValueError("レイヤー数が上限を超えています。")
            if expected_layer_count is None:
                expected_layer_count = len(layer_entries)
            elif len(layer_entries) != expected_layer_count:
                raise ValueError("フレームごとのレイヤー数が一致していません。")
            layer_cell_count += len(layer_entries)
        if layer_cell_count > MAX_PROJECT_LAYER_CELLS:
            raise ValueError("プロジェクトのセル数が上限を超えています。")
        if width * height * layer_cell_count > MAX_PROJECT_DECODED_PIXELS:
            raise ValueError("プロジェクトの展開後画像サイズが大きすぎます。")

        for frame_data in frame_entries:
            loaded_layers = []
            for layer_data in frame_data.get("layers", []):
                if not isinstance(layer_data, dict):
                    raise ValueError("レイヤー情報が不正です。")
                image_path = layer_data.get("image")
                if not image_path:
                    raise ValueError("レイヤー画像の参照がありません。")
                image_path = str(image_path)
                normalized_path = PurePosixPath(image_path)
                if (
                    normalized_path.is_absolute()
                    or ".." in normalized_path.parts
                    or normalized_path.suffix.lower() != ".png"
                ):
                    raise ValueError("レイヤー画像の参照パスが不正です。")
                try:
                    image_info = archive.getinfo(image_path)
                except KeyError as error:
                    raise ValueError(
                        f"レイヤー画像がありません: {image_path}"
                    ) from error
                if image_info.file_size > MAX_PROJECT_IMAGE_BYTES:
                    raise ValueError(
                        f"レイヤー画像が大きすぎます: {image_path}"
                    )
                image_bytes = archive.read(image_path)
                image = QImage.fromData(image_bytes, "PNG")
                if image.isNull():
                    raise ValueError(
                        f"レイヤー画像を復元できません: {image_path}"
                    )
                if image.width() != width or image.height() != height:
                    raise ValueError(
                        f"レイヤー画像のサイズが不正です: {image_path}"
                    )
                image = image.convertToFormat(
                    QImage.Format.Format_ARGB32_Premultiplied
                )

                filter_rgb = layer_data.get("color_filter_rgb")
                exposure = int(layer_data.get("exposure", 1))
                if not (1 <= exposure <= MAX_PROJECT_FRAMES):
                    raise ValueError("レイヤーの露出フレーム数が不正です。")
                sequence_number = layer_data.get("sequence_number")
                if sequence_number is not None:
                    sequence_number = int(sequence_number)
                    if not (1 <= sequence_number <= MAX_PROJECT_FRAMES):
                        raise ValueError("絵番号が範囲外です。")
                opacity = float(layer_data.get("opacity", 1.0))
                if not math.isfinite(opacity):
                    raise ValueError("レイヤー不透明度が不正です。")
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
                    )
                )

            if not loaded_layers:
                loaded_layers = [Layer("Layer 1", blank_image())]
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
    return metadata, loaded_frames, width, height


def write_project_archive(project_path, metadata, frames):
    """Serialize frames + metadata into the project archive at ``project_path``.

    Fills ``metadata["frames"]`` from the Frame/Layer domain objects, writes one
    PNG per layer-cell plus project.json into a zip, and atomically replaces the
    destination. Raises on error (the caller shows the UI message).
    """
    project_path = Path(project_path)
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
                        raise RuntimeError(
                            f"画像を書き出せませんでした: {image_name}"
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
