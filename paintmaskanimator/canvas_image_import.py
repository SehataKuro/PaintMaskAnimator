"""Raster-image import and drag-and-drop for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods decode image files
(Qt reader with a Pillow fallback), place them onto layers, run the optional
color-reduction pipeline, and handle file drag-and-drop onto the canvas. They
run against a live ``PaintCanvas`` instance and reuse its frame/layer state and
the color-reduction delegating methods that remain on the widget.
"""
import numpy as np
from pathlib import Path
from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage, QImageReader, QPainter
from .i18n import tr
from .pma_image import as_qimage, to_pma_image
from .optional_deps import PILImage
from .constants import MAX_IMAGE_DIMENSION, MAX_SINGLE_IMAGE_PIXELS, OUTSIDE_MARGIN
from .errors import OperationError
from ._canvas_members import CanvasMembers
from . import color_ops, constants, imaging, timesheet_file
from .models import Layer
from .utils import blank_image, natural_path_key
from .logging_setup import get_logger

log = get_logger(__name__)


def blur_image(image, sigma):
    """:func:`soft_blur.soft_blur` を QImage にかける（#10）。"""
    from . import soft_blur

    rgba = imaging.qimage_rgba_array(image)
    return imaging.rgba_array_to_qimage(soft_blur.soft_blur(rgba, sigma))


def flatten_paper_image(image):
    """:func:`scan_paper.flatten_paper` を QImage にかける（#51）。"""
    from . import scan_paper

    rgba = imaging.qimage_rgba_array(image)
    return imaging.rgba_array_to_qimage(scan_paper.flatten_paper(rgba))


class ImageImportMixin(CanvasMembers):
    """Image-file import pipeline + canvas drag-and-drop event handlers."""

    def _read_image_file(self, path):
        path = str(path)
        image = QImage()
        errors = []
        reader = QImageReader(path)
        reader.setAutoTransform(False)
        if hasattr(reader, "setDecideFormatFromContent"):
            reader.setDecideFormatFromContent(True)
        declared_size = reader.size()
        if declared_size.isValid():
            declared_width = int(declared_size.width())
            declared_height = int(declared_size.height())
            if (
                declared_width < 1
                or declared_height < 1
                or declared_width > MAX_IMAGE_DIMENSION
                or declared_height > MAX_IMAGE_DIMENSION
                or declared_width * declared_height > MAX_SINGLE_IMAGE_PIXELS
            ):
                return None, (
                    tr("画像サイズが上限を超えています。\n{width} × {height}px").format(width=declared_width, height=declared_height)
                )
        image = reader.read()
        if image.isNull():
            errors.append("Qt: " + (reader.errorString() or tr("画像データを解釈できませんでした")))
        if image.isNull() and PILImage is not None:
            try:
                with PILImage.open(path) as pil:
                    pil_width, pil_height = map(int, pil.size)
                    if (
                        pil_width < 1
                        or pil_height < 1
                        or pil_width > MAX_IMAGE_DIMENSION
                        or pil_height > MAX_IMAGE_DIMENSION
                        or pil_width * pil_height > MAX_SINGLE_IMAGE_PIXELS
                    ):
                        raise OperationError(
                            tr("画像サイズが上限を超えています。 ({width} × {height}px)").format(width=pil_width, height=pil_height)
                        )
                    pil.load()
                    pil = pil.convert("RGBA")
                    raw = pil.tobytes("raw", "RGBA")
                    converted = QImage(raw, pil.width, pil.height, pil.width * 4, QImage.Format.Format_RGBA8888)
                    image = converted.copy()
            except (OSError, ValueError, TypeError, MemoryError, RuntimeError) as exc:
                # Pillow raises a wide, loosely-documented set on undecodable
                # or oversized images; record it and fall back to the Qt reader.
                log.info("Pillow decode of %s failed: %s", path, exc)
                errors.append(f"Pillow: {exc}")
        if image.isNull():
            suffix = Path(path).suffix.lower() or tr("拡張子なし")
            return None, tr("形式: {suffix}\n").format(suffix=suffix) + "\n".join(errors)
        return imaging.without_color_space(
            image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        ), ""

    @staticmethod
    def _natural_path_key(*args, **kwargs):
        return natural_path_key(*args, **kwargs)

    def _placed_imported_image(self, image):
        """Build a workspace image with the decoded image centered in it."""
        target = blank_image()
        x = OUTSIDE_MARGIN + (constants.CANVAS_WIDTH - image.width()) // 2
        y = OUTSIDE_MARGIN + (constants.CANVAS_HEIGHT - image.height()) // 2
        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(QPoint(x, y), image)
        painter.end()
        return target

    def _make_white_transparent(self, image):
        """Convert exact #FFFFFF pixels to fully transparent before placement."""
        if image is None or image.isNull():
            return image
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = rgba.width(), rgba.height()
        if width <= 0 or height <= 0:
            return rgba

        ptr = imaging.qimage_buffer(rgba)
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, rgba.bytesPerLine())
        )
        pixels = rows[:, :width * 4].reshape((height, width, 4))
        white = (
            (pixels[:, :, 0] == 255)
            & (pixels[:, :, 1] == 255)
            & (pixels[:, :, 2] == 255)
            & (pixels[:, :, 3] != 0)
        )
        pixels[:, :, 3][white] = 0
        return rgba.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    def make_border_background_transparent(self, image, tolerance=24):
        """外周からつながる明色背景を透明にする（Issue #10）。

        JPEG は圧縮で背景が 254,255,253 のようにばらつくため、完全一致
        #FFFFFF では落とせない。しきい値で拾い、外周からつながる範囲に
        限ることで、絵の中の白（目のハイライトなど）は残す。

        背景と見なせる色が見つからなければ、元の画像をそのまま返す。
        """
        if image is None or image.isNull():
            return image
        background_rgb = color_ops.detect_opaque_border_background(image)
        if background_rgb is None:
            return image

        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = rgba.width(), rgba.height()
        if width <= 0 or height <= 0:
            return image
        ptr = imaging.qimage_buffer(rgba)
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, rgba.bytesPerLine())
        )
        pixels = rows[:, :width * 4].reshape((height, width, 4))

        difference = (
            pixels[:, :, :3].astype(np.int32)
            - np.asarray(background_rgb, dtype=np.int32)
        )
        near = np.sum(difference * difference, axis=2) <= (
            int(tolerance) * int(tolerance) * 3
        )
        starts = [
            (int(x), 0) for x in np.flatnonzero(near[0, :])
        ]
        if height > 1:
            starts.extend((int(x), height - 1) for x in np.flatnonzero(near[-1, :]))
        if width > 1:
            starts.extend((0, int(y)) for y in np.flatnonzero(near[:, 0]))
            starts.extend(
                (width - 1, int(y)) for y in np.flatnonzero(near[:, -1])
            )
        if not starts:
            return image

        outside = imaging.scanline_connected_region_numpy(near, starts)
        if not outside.any():
            return image
        pixels[:, :, 3][outside] = 0
        return rgba.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    def import_image(self, path, color_reduction=None, draft=False):
        return self.import_image_sequence(
            [path],
            color_reduction=color_reduction,
            draft=draft,
        )

    def reduce_image_with_settings(self, image, color_reduction):
        """2値化の設定をかけ、ゴミ取り（``despeckle``、#10）があれば最後に行う。"""
        image = self._reduce_image(image, color_reduction)
        max_pixels = int(color_reduction.get("despeckle") or 0)
        if max_pixels > 0:
            from . import despeckle

            cleaned, removed = despeckle.remove_isolated_specks(
                imaging.qimage_rgba_array(image), max_pixels
            )
            if removed:
                image = imaging.rgba_array_to_qimage(cleaned)
        return image

    def _reduce_image(self, image, color_reduction):
        """2値化ダイアログで決めた設定（``color_reduction``）を1枚の画像にかける。

        読み込み時と、読み込んだあとのレイヤーへの2値化（#62）で同じ処理を使う。
        トーンカーブを元画像へ先にかけ、そのあと共通パレットへ寄せる。
        """
        extraction_mode = color_reduction.get("extraction_mode", "surface")
        if extraction_mode == "digital":
            # デジタル TP：色数を決めず、いまある色面の色だけを残す（#63）。
            from . import digital_antialias

            rgba = imaging.qimage_rgba_array(image)
            cleaned, changed = digital_antialias.remove_antialias(rgba)
            if changed:
                image = imaging.rgba_array_to_qimage(cleaned)
            return self._make_white_transparent(image)
        if color_reduction.get("flatten_paper"):
            # スキャンした紙の地色とムラを白へそろえてから2値化する（#51）。
            image = flatten_paper_image(image)
        if float(color_reduction.get("blur") or 0) > 0:
            # トーンカーブの前に、ざらつきだけを弱いぼかしでならす（#10）。
            image = blur_image(image, float(color_reduction["blur"]))
        palette = color_reduction.get("palette")
        line_extraction = extraction_mode == "line"
        reduction_background = (
            (255, 255, 255)
            if line_extraction
            else color_reduction.get("background_rgb")
        )
        alpha_threshold = int(color_reduction.get("alpha_threshold", 128))
        tone_curve_points = color_reduction.get(
            "tone_curve_points", [(0.0, 0.0), (1.0, 1.0)]
        )
        if tone_curve_points:
            image = self.apply_tone_curve(
                image,
                tone_curve_points=tone_curve_points,
                background_rgb=reduction_background,
            )
        if palette is not None:
            image = self.apply_color_reduction_palette(
                image,
                palette,
                alpha_threshold=alpha_threshold,
                opaque_background=(
                    True
                    if line_extraction
                    else bool(color_reduction.get("opaque_background", False))
                ),
                background_rgb=reduction_background,
                tone_curve_points=[(0.0, 0.0), (1.0, 1.0)],
                extraction_mode=extraction_mode,
            )
        return image

    def convert_import_image(self, path, image, color_reduction=None, draft=False):
        """読み込んだ1枚を、置く前の形へ変換する（読み込みと再変換で同じ処理）。"""
        if draft:
            # 変換なし読み込み: 色数削減も白透明化も行わず、
            # 読み込んだ画素をそのまま下書きレイヤーへ配置する。
            return image
        if color_reduction:
            return self.reduce_image_with_settings(image, color_reduction)
        if Path(path).suffix.lower() in (".jpg", ".jpeg"):
            # JPEGは圧縮で背景の白がばらつくため、完全一致では落とせない。
            # しきい値で拾い、外周からつながる範囲だけを透明にする。
            # 絵の中の白（目のハイライトなど）は残る（Issue #10）。
            return self.make_border_background_transparent(image)
        return self._make_white_transparent(image)

    def import_image_sequence(
        self,
        paths,
        progress_callback=None,
        color_reduction=None,
        layer_name=None,
        draft=False,
    ):
        paths = sorted([str(path) for path in paths], key=self._natural_path_key)
        if not paths:
            return False, tr("画像ファイルがありません。")
        decoded = []
        for index, path in enumerate(paths, 1):
            if progress_callback:
                progress_callback(index - 1, len(paths), tr("{Path} を読み込んでいます").format(Path=Path(path).name))
            image, error = self._read_image_file(path)
            if image is None:
                return False, f"{Path(path).name}\n{error}"
            if progress_callback and color_reduction and color_reduction.get("palette") is not None:
                progress_callback(
                    index - 1,
                    len(paths),
                    tr("{Path} の元画像へトーンカーブを適用し、共通パレットで2値化しています").format(Path=Path(path).name),
                )
            image = self.convert_import_image(path, image, color_reduction, draft=draft)
            decoded.append((path, image))
        if progress_callback:
            progress_callback(len(paths), len(paths), tr("画像の配置を準備しています"))
        self.push_doc_undo()
        start_frame = self.current_frame
        self._ensure_frame_count(start_frame + len(decoded))
        name = str(layer_name).strip() if layer_name is not None else ""
        if not name:
            name = Path(decoded[0][0]).stem or f"Image {len(self.layers)}"
        insert_index = len(self.frames[0].layers)
        slots = [Layer(name, to_pma_image(blank_image()), is_draft=bool(draft)) for _ in self.frames]
        self._document.insert_track(insert_index, slots, staged=True)
        for offset, (_, image) in enumerate(decoded):
            if progress_callback:
                progress_callback(offset, len(decoded), tr("{value} / {len} 枚を配置しています").format(value=offset + 1, len=len(decoded)))
            self._document.stage_numbered_drawing(
                start_frame + offset, insert_index, self._placed_imported_image(image), True, offset + 1,
            )
        self.install_legacy_frames()
        track = self.frames[start_frame].layers[insert_index]._track
        track.import_sources = [str(path) for path, _image in decoded]
        self._sequence_source_bank = [
            as_qimage(self.frames[start_frame + offset].layers[insert_index].image).copy()
            for offset in range(len(decoded))
        ]
        self._sequence_source_bank_layer_index = int(insert_index)
        self._sequence_source_bank_layer_name = str(name)
        self.active_layer_index = insert_index
        self.current_frame = start_frame
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        if progress_callback:
            progress_callback(len(decoded), len(decoded), tr("読み込み完了"))
        return True, ""

    def dragEnterEvent(self,e):
        urls=e.mimeData().urls() if e.mimeData().hasUrls() else []
        valid=any(Path(u.toLocalFile()).suffix.lower() in (".pman",".clip",*timesheet_file.SUFFIXES,".jpg",".jpeg",".png",".tga") for u in urls)
        if valid:e.acceptProposedAction()
        else:e.ignore()

    def dragMoveEvent(self,e):
        e.acceptProposedAction()

    def dropEvent(self,e):
        project_paths=[]
        clip_paths=[]
        remap_paths=[]
        paths=[]
        for u in e.mimeData().urls():
            path=u.toLocalFile()
            suffix=Path(path).suffix.lower()
            if suffix==".pman":
                project_paths.append(path)
            elif suffix==".clip":
                clip_paths.append(path)
            elif suffix in timesheet_file.SUFFIXES:
                remap_paths.append(path)
            elif suffix in (".jpg",".jpeg",".png",".tga"):
                paths.append(path)
        if project_paths:
            self.projectDropped.emit(project_paths[0])
        elif clip_paths:
            self.clipAnimationDropped.emit(clip_paths[0])
        elif remap_paths:
            self.timeRemapDropped.emit(remap_paths[0])
        elif paths:
            self.imagesDropped.emit(paths)
        e.acceptProposedAction()
