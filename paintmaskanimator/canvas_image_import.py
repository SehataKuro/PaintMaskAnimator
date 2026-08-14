"""Raster-image import and drag-and-drop for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods decode image files
(Qt reader with a Pillow fallback), place them onto layers, run the optional
color-reduction pipeline, and handle file drag-and-drop onto the canvas. They
run against a live ``PaintCanvas`` instance and reuse its frame/layer state and
the color-reduction delegating methods that remain on the widget.
"""
from .common import *  # noqa: F401,F403
from ._canvas_members import CanvasMembers
from . import constants, despeckle, imaging
from .models import Layer
from .utils import blank_image, natural_path_key
from .logging_setup import get_logger

log = get_logger(__name__)


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
                    "画像サイズが上限を超えています。\n"
                    f"{declared_width} × {declared_height}px"
                )
        image = reader.read()
        if image.isNull():
            errors.append("Qt: " + (reader.errorString() or "画像データを解釈できませんでした"))
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
                        raise ValueError(
                            "画像サイズが上限を超えています。"
                            f" ({pil_width} × {pil_height}px)"
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
            suffix = Path(path).suffix.lower() or "拡張子なし"
            return None, f"形式: {suffix}\n" + "\n".join(errors)
        return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied), ""

    @staticmethod
    def _natural_path_key(*args, **kwargs):
        return natural_path_key(*args, **kwargs)

    def _place_imported_image(self, image, target):
        x = OUTSIDE_MARGIN + (constants.CANVAS_WIDTH - image.width()) // 2
        y = OUTSIDE_MARGIN + (constants.CANVAS_HEIGHT - image.height()) // 2
        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(QPoint(x, y), image)
        painter.end()

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

    def _remove_import_background(self, image, path):
        """取り込み時の背景除去。外周連結の明色背景を既定で透明化する。"""
        if image is None or image.isNull():
            return image
        background_rgb = self.detect_opaque_border_background(image)
        if background_rgb is not None:
            return self.remove_border_connected_background(
                image,
                background_rgb,
            )
        # 背景を検出できない画像では従来の挙動を保つ。JPEGはアルファを
        # 持たないため、完全一致の白抜きは行わない（圧縮で白くなった
        # 画素まで欠けて見えるため）。
        if Path(path).suffix.lower() in (".jpg", ".jpeg"):
            return image
        return self._make_white_transparent(image)

    def _apply_import_pipeline(self, image, path, color_reduction):
        """1枚分の取り込み整形。取り込みと再変換で同じ経路を通す。"""
        if not color_reduction:
            return self._remove_import_background(image, path)

        palette = color_reduction.get("palette")
        extraction_mode = color_reduction.get("extraction_mode", "surface")
        line_extraction = extraction_mode == "line"
        reduction_background = (
            (255, 255, 255)
            if line_extraction
            else color_reduction.get("background_rgb")
        )
        alpha_threshold = int(color_reduction.get("alpha_threshold", 128))
        tone_curve_points = color_reduction.get(
            "tone_curve_points",
            [(0.0, 0.0), (1.0, 1.0)],
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
        stage = color_reduction.get("despeckle") or {}
        if stage.get("enabled"):
            image, _changed = despeckle.despeckle_image(
                image,
                mode=stage.get("mode", despeckle.DUST_MODE),
                max_area=int(stage.get("max_area", 4)),
                removal_rgba=tuple(
                    color_reduction.get(
                        "despeckle_removal_rgba",
                        (255, 255, 255, 255),
                    )
                ),
            )
        return image

    def reconvert_imported_sequence(
        self,
        color_reduction,
        progress_callback=None,
    ):
        """取り込み済み連番を、元ファイルから設定だけ変えて作り直す。

        取り込みし直し（レイヤー追加）ではなく、同じレイヤーの各コマを
        置き換える。元画像は再読込するため、整形の積み重ねにならない。
        """
        paths = list(getattr(self, "_sequence_import_paths", []))
        layer_index = int(
            getattr(self, "_sequence_source_bank_layer_index", -1)
        )
        start_frame = int(getattr(self, "_sequence_import_start_frame", 0))
        if not paths or layer_index < 0:
            return False, "再変換できる取り込み連番がありません。"

        missing = [path for path in paths if not Path(path).exists()]
        if missing:
            return False, (
                "元の画像ファイルが見つかりません。\n"
                f"{Path(missing[0]).name} ほか {len(missing)} 件"
            )

        converted = []
        for index, path in enumerate(paths, 1):
            if progress_callback:
                progress_callback(
                    index - 1,
                    len(paths),
                    f"{Path(path).name} を再変換しています",
                )
            image, error = self._read_image_file(path)
            if image is None:
                return False, f"{Path(path).name}\n{error}"
            converted.append(
                self._apply_import_pipeline(image, path, color_reduction)
            )

        last_frame = start_frame + len(converted) - 1
        if last_frame >= len(self.frames):
            return False, "取り込み時のコマが残っていないため再変換できません。"
        for frame in self.frames[start_frame:last_frame + 1]:
            if layer_index >= len(frame.layers):
                return False, "取り込み時のレイヤーが残っていないため再変換できません。"

        self.push_doc_undo()
        for offset, image in enumerate(converted):
            layer = self.frames[start_frame + offset].layers[layer_index]
            layer.image = blank_image()
            self._place_imported_image(image, layer.image)
            layer.has_content = True
        self._sequence_source_bank = [
            self.frames[start_frame + offset]
            .layers[layer_index].image.copy()
            for offset in range(len(converted))
        ]
        self._sequence_import_settings = dict(color_reduction or {})
        self._color_filter_cache.clear()
        self._color_index_cache.clear()
        self._pseudo_transparency_cache.clear()
        self._silhouette_cache.clear()
        self._onion_cache.clear()
        self._playback_frame_cache.clear()
        self.changed.emit()
        self.update()
        if progress_callback:
            progress_callback(len(paths), len(paths), "再変換が完了しました")
        return True, ""

    def import_image(self, path, color_reduction=None):
        return self.import_image_sequence(
            [path],
            color_reduction=color_reduction,
        )

    def import_image_sequence(
        self,
        paths,
        progress_callback=None,
        color_reduction=None,
        layer_name=None,
    ):
        paths = sorted([str(path) for path in paths], key=self._natural_path_key)
        if not paths:
            return False, "画像ファイルがありません。"
        decoded = []
        for index, path in enumerate(paths, 1):
            if progress_callback:
                progress_callback(index - 1, len(paths), f"{Path(path).name} を読み込んでいます")
            image, error = self._read_image_file(path)
            if image is None:
                return False, f"{Path(path).name}\n{error}"
            if progress_callback and color_reduction:
                progress_callback(
                    index - 1,
                    len(paths),
                    f"{Path(path).name} を整形しています",
                )
            image = self._apply_import_pipeline(
                image,
                path,
                color_reduction,
            )
            decoded.append((path, image))
        if progress_callback:
            progress_callback(len(paths), len(paths), "画像の配置を準備しています")
        self.push_doc_undo()
        start_frame = self.current_frame
        self._ensure_frame_count(start_frame + len(decoded))
        name = str(layer_name).strip() if layer_name is not None else ""
        if not name:
            name = Path(decoded[0][0]).stem or f"Image {len(self.layers)}"
        insert_index = len(self.frames[0].layers)
        for frame in self.frames:
            frame.layers.append(Layer(name, blank_image()))
        for offset, (_, image) in enumerate(decoded):
            if progress_callback:
                progress_callback(offset, len(decoded), f"{offset + 1} / {len(decoded)} 枚を配置しています")
            layer = self.frames[start_frame + offset].layers[insert_index]
            self._place_imported_image(image, layer.image)
            layer.has_content = True
            layer.exposure = 1
            layer.sequence_number = offset + 1
        self._sequence_source_bank = [
            self.frames[start_frame + offset]
            .layers[insert_index].image.copy()
            for offset in range(len(decoded))
        ]
        self._sequence_source_bank_layer_index = int(insert_index)
        self._sequence_source_bank_layer_name = str(name)
        # 取り込み設定を変えた再変換のために、元ファイルと設定を控える。
        self._sequence_import_paths = [str(path) for path, _ in decoded]
        self._sequence_import_start_frame = int(start_frame)
        self._sequence_import_settings = dict(color_reduction or {})
        self.active_layer_index = insert_index
        self.current_frame = start_frame
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        if progress_callback:
            progress_callback(len(decoded), len(decoded), "読み込み完了")
        return True, ""

    def dragEnterEvent(self,e):
        urls=e.mimeData().urls() if e.mimeData().hasUrls() else []
        valid=any(Path(u.toLocalFile()).suffix.lower() in (".pman",".xdts",".xtds",".jpg",".jpeg",".png",".tga") for u in urls)
        if valid:e.acceptProposedAction()
        else:e.ignore()

    def dragMoveEvent(self,e):
        e.acceptProposedAction()

    def dropEvent(self,e):
        project_paths=[]
        remap_paths=[]
        paths=[]
        for u in e.mimeData().urls():
            path=u.toLocalFile()
            suffix=Path(path).suffix.lower()
            if suffix==".pman":
                project_paths.append(path)
            elif suffix in (".xdts", ".xtds"):
                remap_paths.append(path)
            elif suffix in (".jpg",".jpeg",".png",".tga"):
                paths.append(path)
        if project_paths:
            self.projectDropped.emit(project_paths[0])
        elif remap_paths:
            self.timeRemapDropped.emit(remap_paths[0])
        elif paths:
            self.imagesDropped.emit(paths)
        e.acceptProposedAction()
