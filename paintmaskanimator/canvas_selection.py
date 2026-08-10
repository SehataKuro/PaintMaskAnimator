"""Selection state, transform, and clipboard for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods own the selection mask,
its fade overlay, the begin/commit/cancel free-transform + rotate flow, and
copy/cut/paste of the selected region. They run against a live ``PaintCanvas``
instance and reuse its frame/layer state and the TP-transform mask helpers.
"""
from .common import *  # noqa: F401,F403
from . import imaging
from .logging_setup import get_logger

log = get_logger(__name__)


class SelectionMixin:
    def selection_mask_bool(self, width=None, height=None, offset_x=0, offset_y=0):
        """Return a boolean mask for the active selection in the requested local area."""
        if not self.selection_polygon:
            return None
        width = int(width if width is not None else self.active_layer.image.width())
        height = int(height if height is not None else self.active_layer.image.height())
        if width <= 0 or height <= 0:
            return np.zeros((max(0, height), max(0, width)), dtype=bool)
        if self.selection_mask_override is not None:
            source = np.asarray(self.selection_mask_override, dtype=bool)
            result = np.zeros((height, width), dtype=bool)
            source_x1 = max(0, int(offset_x))
            source_y1 = max(0, int(offset_y))
            source_x2 = min(source.shape[1], int(offset_x) + width)
            source_y2 = min(source.shape[0], int(offset_y) + height)
            if source_x2 > source_x1 and source_y2 > source_y1:
                target_x1 = source_x1 - int(offset_x)
                target_y1 = source_y1 - int(offset_y)
                result[
                    target_y1:target_y1 + source_y2 - source_y1,
                    target_x1:target_x1 + source_x2 - source_x1,
                ] = source[source_y1:source_y2, source_x1:source_x2]
            return result
        mask = QImage(width, height, QImage.Format.Format_RGBA8888)
        mask.fill(Qt.GlobalColor.transparent)
        local_polygon = QPolygonF([
            QPointF(point.x() - offset_x, point.y() - offset_y)
            for point in self.selection_polygon
        ])
        painter = QPainter(mask)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 255))
        painter.drawPolygon(local_polygon)
        painter.end()
        ptr = mask.bits()
        try:
            ptr.setsize(mask.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, mask.bytesPerLine())
        )
        return rows[:, :width * 4].reshape((height, width, 4))[:, :, 3].copy() > 0

    @staticmethod
    def _bool_mask_image(*args, **kwargs):
        return imaging.bool_mask_image(*args, **kwargs)

    def _update_selection_fade(self):
        phase = (
            (time.monotonic() - self._selection_fade_started) % 2.0
        ) / 2.0
        self._selection_fade_opacity = (
            0.08 + 0.92 * (0.5 + 0.5 * math.cos(phase * math.tau))
        )
        if self.selection_polygon:
            if self.transform_active and not self.transform_outer_polygon().isEmpty():
                selection_rect = self.transform_outer_polygon().boundingRect()
            else:
                selection_rect = (
                    self.selection_mask_rect
                    if self.selection_mask_rect is not None
                    else QRectF(self.selection_bounds())
                )
            corners = [
                self.canvas_to_widget(selection_rect.topLeft()),
                self.canvas_to_widget(selection_rect.topRight()),
                self.canvas_to_widget(selection_rect.bottomRight()),
                self.canvas_to_widget(selection_rect.bottomLeft()),
            ]
            xs = [point.x() for point in corners]
            ys = [point.y() for point in corners]
            dirty = QRectF(
                min(xs), min(ys),
                max(xs) - min(xs), max(ys) - min(ys),
            ).adjusted(-4, -4, 4, 4).toAlignedRect()
            region = QRegion(dirty.intersected(self.rect()))
            if self._brush_cursor_inside:
                cursor_radius = max(
                    10,
                    int(math.ceil(
                        float(self.pen_size) * max(self.zoom, 0.01) / 2.0
                    )) + 6,
                )
                cursor_rect = QRectF(
                    self._brush_cursor_widget_pos,
                    self._brush_cursor_widget_pos,
                ).adjusted(
                    -cursor_radius, -cursor_radius,
                    cursor_radius, cursor_radius,
                ).toAlignedRect()
                region -= QRegion(cursor_rect)
            if not region.isEmpty():
                self.update(region)

    def _clear_selection_state(self):
        if self.transform_active:
            self.cancel_selection_transform()
        self.selection_polygon = []
        self.selection_mask_override = None
        self.selection_outline_polygons = []
        self.selection_mask_rect = None
        self.lasso = []
        self.rect_start = None
        self.rect_end = None
        self.selectionChanged.emit()
        self.update()

    def clear_selection(self):
        self._clear_selection_state()
        self.selectionCleared.emit()

    def clear_selection_preserving_used_colors(self):
        """内部処理用。使用色の選択を維持したまま画像選択だけ解除する。"""
        self._clear_selection_state()

    def _masked_selection_source(self, rect):
        source = self.active_layer.image.copy(rect)
        if not self.selection_polygon:
            return source
        if self.selection_mask_override is not None:
            mask = self._bool_mask_image(self.selection_mask_bool(
                rect.width(), rect.height(), rect.x(), rect.y()
            ))
            painter = QPainter(source)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_DestinationIn
            )
            painter.drawImage(0, 0, mask)
            painter.end()
            return source
        mask = QImage(rect.width(), rect.height(), QImage.Format.Format_ARGB32_Premultiplied)
        mask.fill(Qt.GlobalColor.transparent)
        painter = QPainter(mask)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 255))
        painter.drawPolygon(QPolygonF([
            QPointF(point.x() - rect.x(), point.y() - rect.y())
            for point in self.selection_polygon
        ]))
        painter.end()
        painter = QPainter(source)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, mask)
        painter.end()
        return source

    def auto_select_used_area(self):
        """Select the bounding area of every opaque pixel, including gray colors."""
        if not self.frames or not self.layers:
            return False
        self.ensure_editable_key()
        image = self.active_layer.image.convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        width, height = image.width(), image.height()
        if width <= 0 or height <= 0:
            return False
        ptr = image.bits()
        try:
            ptr.setsize(image.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, image.bytesPerLine())
        )
        pixels = rows[:, :width * 4].reshape((height, width, 4))
        used = pixels[:, :, 3] > 0
        ys, xs = np.nonzero(used)
        if len(xs) == 0:
            return False
        left = int(xs.min())
        right = int(xs.max()) + 1
        top = int(ys.min())
        bottom = int(ys.max()) + 1
        self.selection_polygon = [
            QPointF(left, top),
            QPointF(right, top),
            QPointF(right, bottom),
            QPointF(left, bottom),
        ]
        self.selectionChanged.emit()
        self.update()
        return True

    def begin_selection_transform(self, mode):
        if not self.selection_polygon:
            return False
        selection_rect = self.selection_bounds().intersected(
            self.active_layer.image.rect()
        )
        # 選択境界ぴったりで切り出すと、縮小時のサンプリングで
        # 外周画素が透明側へ丸められて欠ける。変形元だけに透明な
        # 安全余白を持たせ、選択マスク自体の形状は変更しない。
        rect = selection_rect.adjusted(-2, -2, 2, 2).intersected(
            self.active_layer.image.rect()
        )
        if rect.isEmpty():
            return False
        if self.transform_active:
            self.cancel_selection_transform()

        mode = mode if mode in ("scale", "free", "mesh") else "free"
        self.transform_active = True
        self.transform_mode = mode
        self.transform_quality_active = bool(getattr(self, "transform_quality", False))
        self.transform_tp_line_colors = tuple(sorted(self.selected_used_colors()))
        if self.transform_quality_active:
            self.transform_apply_all_frames = False
        self._invalidate_tp_preview_cache()
        self.transform_frame_index = self.current_frame
        self.transform_layer_index = self.active_layer_index
        self.transform_original_layer = self.active_layer.image.copy()
        self.transform_original_has_content = self.active_layer.has_content
        self.transform_source_rect = QRectF(rect)
        self.transform_source = self._masked_selection_source(rect)
        if self.transform_quality_active:
            target_width = self.active_layer.image.width()
            target_height = self.active_layer.image.height()
            use_proxy = self._tp_uses_proxy(target_width, target_height)
            if PILImage is None or PILImageFilter is None:
                # Keep the transform usable even when Pillow is unavailable,
                # but do not pretend that the TP_mask method is active.
                self.transform_quality_active = False
                self.status_message.emit(
                    "クオリティ変形には Pillow が必要です。通常変形へ切り替えました。"
                )
            elif use_proxy:
                self._clear_tp_transform_masks(clear_proxy=False)
                self.status_message.emit(
                    "5000px以上の画像は軽量TpMaskプレビューで表示し、"
                    "確定時に原寸で処理します。"
                )
            elif not self._prepare_tp_transform_masks():
                self.transform_quality_active = False
                self.status_message.emit(
                    "TpMaskを準備できなかったため、通常変形へ切り替えました。"
                )
        self.transform_drag_kind = None
        self.transform_drag_start = QPointF()
        self.transform_drag_points = []

        painter = QPainter(self.active_layer.image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 255))
        if self.selection_mask_override is not None:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_DestinationOut
            )
            local_mask = self.selection_mask_bool(
                rect.width(), rect.height(), rect.x(), rect.y()
            )
            painter.drawImage(
                rect.topLeft(), self._bool_mask_image(local_mask)
            )
        else:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
            )
            painter.drawPolygon(QPolygonF(self.selection_polygon))
        painter.end()

        if mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
            self.transform_mesh_cols = cols
            self.transform_mesh_rows = rows
            self.transform_mesh_grid = cols
            self.transform_points = (
                self._regular_mesh_reference_points(
                    QRectF(rect),
                    cols,
                    rows,
                )
            )
            self.transform_mesh_reference_points = [
                QPointF(point) for point in self.transform_points
            ]
            self._active_mesh_cols = cols
            self._active_mesh_rows = rows
        else:
            self.transform_mesh_reference_points = []
            left, top = float(rect.left()), float(rect.top())
            right, bottom = float(rect.right() + 1), float(rect.bottom() + 1)
            self.transform_points = [
                QPointF(left, top), QPointF(right, top),
                QPointF(right, bottom), QPointF(left, bottom),
            ]
        self.transform_handle = -1
        if self.transform_quality_active:
            self.request_quality_preview_counter(
                "最初のクオリティプレビューを生成しています"
            )
        self.update()
        return True

    def transformed_selection_outline(self, outline):
        """選択輪郭を現在の変形プレビューと同じ座標へ写像する。"""
        if (
            not self.transform_active
            or self.transform_source_rect is None
            or not self.transform_points
        ):
            return [QPointF(point) for point in outline]
        rect = QRectF(self.transform_source_rect)
        width = max(1.0, float(rect.width()))
        height = max(1.0, float(rect.height()))
        if self.transform_mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
            if len(self.transform_points) != cols * rows:
                return [QPointF(point) for point in outline]
            return [
                self._mesh_curve_point(
                    self.transform_points,
                    cols,
                    rows,
                    (float(point.x()) - rect.left()) / width * (cols - 1),
                    (float(point.y()) - rect.top()) / height * (rows - 1),
                )
                for point in outline
            ]
        if len(self.transform_points) != 4:
            return [QPointF(point) for point in outline]
        transform = self._quad_homography(
            [
                QPointF(0, 0), QPointF(width, 0),
                QPointF(width, height), QPointF(0, height),
            ],
            self.transform_points,
        )
        return [
            transform.map(QPointF(
                float(point.x()) - rect.left(),
                float(point.y()) - rect.top(),
            ))
            for point in outline
        ]

    def rotate_selection_transform(self, degrees):
        if not self.transform_active or not self.transform_points:
            return
        center = self.transform_center()
        angle = math.radians(float(degrees))
        cs, sn = math.cos(angle), math.sin(angle)
        self.transform_points = [
            QPointF(
                center.x() + (p.x() - center.x()) * cs - (p.y() - center.y()) * sn,
                center.y() + (p.x() - center.x()) * sn + (p.y() - center.y()) * cs,
            )
            for p in self.transform_points
        ]
        self._invalidate_tp_preview_cache()
        if self.transform_quality_active:
            self.request_quality_preview_counter(
                "回転後のクオリティプレビューを生成しています"
            )
        self.update()

    def _selection_source_from_image(self, image):
        rect = self.transform_source_rect.toAlignedRect().intersected(image.rect())
        if rect.isEmpty():
            return None
        source = image.copy(rect)
        if self.selection_polygon:
            if self.selection_mask_override is not None:
                mask = self._bool_mask_image(self.selection_mask_bool(
                    rect.width(), rect.height(), rect.x(), rect.y()
                ))
                painter = QPainter(source)
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_DestinationIn
                )
                painter.drawImage(0, 0, mask)
                painter.end()
                return source
            mask = QImage(rect.width(), rect.height(), QImage.Format.Format_ARGB32_Premultiplied)
            mask.fill(Qt.GlobalColor.transparent)
            painter = QPainter(mask)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 255))
            painter.drawPolygon(QPolygonF([
                QPointF(point.x() - rect.x(), point.y() - rect.y())
                for point in self.selection_polygon
            ]))
            painter.end()
            painter = QPainter(source)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.drawImage(0, 0, mask)
            painter.end()
        return source

    def _cleared_selection_base(self, image):
        base = image.copy()
        painter = QPainter(base)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 255))
        if self.selection_mask_override is not None:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_DestinationOut
            )
            painter.drawImage(
                0, 0, self._bool_mask_image(self.selection_mask_override)
            )
        elif self.selection_polygon:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
            )
            painter.drawPolygon(QPolygonF(self.selection_polygon))
        elif self.transform_source_rect is not None:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
            )
            painter.fillRect(self.transform_source_rect, QColor(0, 0, 0, 255))
        painter.end()
        return base

    def _reset_selection_transform(self):
        self.transform_active = False
        self.transform_mode = None
        self.transform_original_layer = None
        self.transform_original_has_content = False
        self.transform_source = None
        self.transform_source_rect = None
        self.transform_points = []
        self.transform_mesh_reference_points = []
        self.transform_handle = -1
        self.transform_drag_kind = None
        self.transform_drag_points = []
        self.transform_quality_active = False
        self.transform_tp_line_colors = ()
        self._clear_tp_transform_masks()

    def commit_selection_transform(self, all_frames=None):
        if not self.transform_active:
            return
        window = self.window()
        quality_active = bool(getattr(self, "transform_quality_active", False))
        if all_frames is None:
            all_frames = bool(
                window.tools.selection_all_frames.isChecked()
                if hasattr(window, "tools")
                else getattr(self, "transform_apply_all_frames", False)
            )
        if quality_active:
            all_frames = False
            target_width = self.active_layer.image.width()
            target_height = self.active_layer.image.height()
            if self._tp_uses_proxy(target_width, target_height):
                # 大画像の表示中キャッシュは縮小代理画像なので、確定時だけ
                # 原寸マスクを生成する。代理画像を確定結果へ流用しない。
                self._clear_tp_transform_masks(clear_proxy=False)
                self.refresh_quality_preview_with_counter(
                    "変形確定用の原寸クオリティ画像を生成しています",
                    full_resolution=True,
                )
            elif self._tp_preview_cache_image is None:
                self.refresh_quality_preview_with_counter(
                    "変形確定用のクオリティ画像を生成しています"
                )
            if self._tp_preview_cache_image is None:
                self.status_message.emit(
                    "クオリティ画像を生成できなかったため、変形確定を中止しました。"
                )
                return

        layer_index = int(getattr(self, "transform_layer_index", self.active_layer_index))
        current_index = int(getattr(self, "transform_frame_index", self.current_frame))
        original_current = self.transform_original_layer
        original_current_has_content = getattr(
            self, "transform_original_has_content", self.active_layer.has_content
        )
        frame_indices = list(
            range(len(self.frames)) if all_frames else [current_index]
        )
        undo_cells = []
        changed_frames = []
        progress = None
        if all_frames and hasattr(window, "create_progress_counter"):
            progress = window.create_progress_counter(
                "すべてのコマに変形",
                len(frame_indices),
                "変形を準備しています",
            )

        for progress_index, frame_index in enumerate(frame_indices, 1):
            if progress is not None:
                window.update_progress_counter(
                    progress,
                    progress_index - 1,
                    len(frame_indices),
                    f"コマ {frame_index + 1} を変形しています",
                )
            if not (0 <= frame_index < len(self.frames)):
                continue
            frame = self.frames[frame_index]
            if not (0 <= layer_index < len(frame.layers)):
                continue
            layer = frame.layers[layer_index]
            if frame_index == current_index and original_current is not None:
                original = original_current.copy()
                original_has_content = original_current_has_content
            else:
                if not layer.has_content:
                    continue
                original = layer.image.copy()
                original_has_content = layer.has_content

            source = (
                self.transform_source
                if quality_active and frame_index == current_index
                and self.transform_source is not None
                else self._selection_source_from_image(original)
            )
            if source is None or source.isNull():
                continue
            preview, _target = self.transform_preview_image(
                source, original.width(), original.height(),
                quality=quality_active,
            )
            if preview is None:
                continue

            base = self._cleared_selection_base(original)
            painter = QPainter(base)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawImage(0, 0, preview)
            painter.end()

            undo_cells.append((frame_index, original.copy(), original_has_content))
            layer.image = base
            layer.has_content = True
            changed_frames.append(frame_index)

        if progress is not None:
            window.close_progress_counter(progress)

        for frame_index in changed_frames:
            self.sync_numbered_image_from_cell(
                frame_index,
                layer_index,
            )

        if undo_cells:
            if len(undo_cells) == 1:
                frame_index, image, has_content = undo_cells[0]
                self.undo_stack.append((
                    "layer", frame_index, layer_index, image, has_content
                ))
            else:
                self.undo_stack.append(("layer_batch", layer_index, undo_cells))
            self.undo_stack = self.undo_stack[-MAX_UNDO:]
            self.redo_stack.clear()
            # 変形ではコマ構造と使用色の種類は変わらないため、
            # タイムライン全再構築・使用色全走査は行わない。
        elif (
            original_current is not None
            and 0 <= current_index < len(self.frames)
            and 0 <= layer_index < len(self.frames[current_index].layers)
        ):
            layer = self.frames[current_index].layers[layer_index]
            layer.image = original_current
            layer.has_content = original_current_has_content

        self._reset_selection_transform()
        self.transform_apply_all_frames = False
        # V62: a confirmed transform finishes the operation completely.
        self.selection_polygon = []
        self.selection_mask_override = None
        self.selection_outline_polygons = []
        self.selection_mask_rect = None
        self.lasso = []
        self.rect_start = None
        self.rect_end = None
        self.selectionChanged.emit()
        self._update_selection_clear_overlay()
        self.update()

    def cancel_selection_transform(self):
        if not self.transform_active:
            return
        if self.transform_original_layer is not None:
            frame_index = int(getattr(self, "transform_frame_index", self.current_frame))
            layer_index = int(getattr(self, "transform_layer_index", self.active_layer_index))
            if (
                0 <= frame_index < len(self.frames)
                and 0 <= layer_index < len(self.frames[frame_index].layers)
            ):
                layer = self.frames[frame_index].layers[layer_index]
                layer.image = self.transform_original_layer
                layer.has_content = getattr(
                    self, "transform_original_has_content", layer.has_content
                )
        self._reset_selection_transform()
        self.transform_apply_all_frames = False
        self.update()

    def selection_bounds(self):
        if not self.selection_polygon:
            return QRectF(0,0,self.active_layer.image.width(),self.active_layer.image.height()).toAlignedRect()
        if self.selection_mask_rect is not None:
            return QRectF(self.selection_mask_rect).toAlignedRect()
        xs=[p.x() for p in self.selection_polygon]; ys=[p.y() for p in self.selection_polygon]
        return QRectF(min(xs),min(ys),max(xs)-min(xs),max(ys)-min(ys)).toAlignedRect()

    def copy_selection(self):
        rect=self.selection_bounds().intersected(self.active_layer.image.rect())
        if rect.isEmpty(): return
        QApplication.clipboard().setImage(self.active_layer.image.copy(rect))

    def cut_selection(self):
        rect=self.selection_bounds().intersected(self.active_layer.image.rect())
        if rect.isEmpty(): return
        self.push_layer_region_undo(rect)
        QApplication.clipboard().setImage(self.active_layer.image.copy(rect))
        p=QPainter(self.active_layer.image)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        if self.selection_polygon:
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(0,0,0,255));p.drawPolygon(QPolygonF(self.selection_polygon))
        else:p.fillRect(rect,QColor(0,0,0,255))
        p.end();self.cellChanged.emit(self.current_frame,self.active_layer_index);self.update()

    def paste_clipboard(self):
        image=QApplication.clipboard().image()
        if image.isNull(): return
        self.ensure_editable_key()
        rect=self.selection_bounds()
        pos=rect.topLeft() if self.selection_polygon else QPoint(OUTSIDE_MARGIN,OUTSIDE_MARGIN)
        changed_rect = QRect(pos, image.size()).intersected(
            self.active_layer.image.rect()
        )
        if not self.push_layer_region_undo(changed_rect): return
        p=QPainter(self.active_layer.image);p.drawImage(pos,image);p.end()
        self.active_layer.has_content=True
        self.cellChanged.emit(self.current_frame,self.active_layer_index);self.update()
