"""Pixel-writing tools for PaintCanvas: brush, fill, line, shape and lasso.

Split out of ``canvas.py`` as a mixin. Everything here turns a pointer gesture
into pixels on the active layer -- the pressure-driven brush stroke, the flood
fill and its region search, the auto-select-by-region helper, the line/shape tool
previews and their commits, and lasso polygon filling. Undo entries are pushed
through ``PaintCanvas.push_undo`` as usual; they run against a live
``PaintCanvas``.
"""
import math
import numpy as np
import time
from typing import Any
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF
from .i18n import tr
from ._canvas_members import CanvasMembers
from . import bucket_fill, imaging
from .pressure import _pressure_bezier_at
from .logging_setup import get_logger

log = get_logger(__name__)


class PaintToolsMixin(CanvasMembers):
    def pressure_size_scale(self, pressure):
        """筆圧から線幅倍率だけを返す。不透明度には使用しない。"""
        if not self.pressure_enabled:
            return 1.0
        pressure = max(
            0.0,
            min(1.0, float(pressure)),
        )
        points = getattr(
            self,
            "pressure_curve_points",
            None,
        )
        if points and len(points) >= 2:
            value = _pressure_bezier_at(
                points,
                pressure,
            )
        else:
            value = pressure ** self.pressure_curve
        return (
            max(self.pressure_min, value)
            * self.pressure_max
        )

    def pressure_value(self, pressure):
        """旧呼び出し互換。返す値は線幅倍率のみ。"""
        return self.pressure_size_scale(pressure)

    def _update_stroke_region(self, a, b, width):
        """Repaint only the widget area touched by a brush segment."""
        wa = self.canvas_to_widget(a)
        wb = self.canvas_to_widget(b)
        margin = max(4, int(math.ceil(float(width) * max(self.zoom, 0.01) / 2.0)) + 3)
        dirty = QRectF(wa, wb).normalized().adjusted(
            -margin, -margin, margin, margin
        ).toAlignedRect()
        self.update(dirty)

    def draw_line(self,a,b,pressure):
        """完全な非AAマスクで、下地色と均一にRGB合成する。"""
        painter_tool = self.effective_tool()
        source_color = self.paint_source_color()
        # 筆圧はここで線幅にだけ使用する。
        width = max(
            0.5,
            self.pen_size
            * self.pressure_size_scale(pressure)
        )
        draw_width = max(0.5, float(width))
        raster_a = QPointF(float(a.x()), float(a.y()))
        raster_b = QPointF(float(b.x()), float(b.y()))

        margin = int(math.ceil(draw_width / 2.0)) + 3
        left = max(
            0,
            int(math.floor(min(a.x(), b.x()))) - margin,
        )
        top = max(
            0,
            int(math.floor(min(a.y(), b.y()))) - margin,
        )
        right = min(
            self.active_layer.image.width(),
            int(math.ceil(max(a.x(), b.x()))) + margin + 1,
        )
        bottom = min(
            self.active_layer.image.height(),
            int(math.ceil(max(a.y(), b.y()))) + margin + 1,
        )
        if right <= left or bottom <= top:
            return

        rect = QRectF(
            left,
            top,
            right - left,
            bottom - top,
        ).toAlignedRect()
        self._ensure_before_region(rect)
        before_region = self._stroke_before_region(rect)
        overlay = QImage(
            rect.width(),
            rect.height(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        overlay.fill(Qt.GlobalColor.transparent)

        painter = QPainter(overlay)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing,
            False,
        )
        self.apply_selection_clip(
            painter,
            rect.x(),
            rect.y(),
        )
        local_a = QPointF(
            raster_a.x() - rect.x(),
            raster_a.y() - rect.y(),
        )
        local_b = QPointF(
            raster_b.x() - rect.x(),
            raster_b.y() - rect.y(),
        )
        painter.setPen(
            QPen(
                source_color,
                draw_width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawLine(local_a, local_b)
        painter.end()

        sub_only = bool(
            painter_tool == "brush"
            and not getattr(self, "mask_all_enabled", True)
        )
        if sub_only:
            overlay_rgba = self._qimage_rgba_array(overlay)
            base_region = before_region
            base_rgba = self._qimage_rgba_array(base_region)

            selected_rgbs = self.selected_mask_colors()
            eligible = np.zeros(
                base_rgba.shape[:2],
                dtype=bool,
            )
            if selected_rgbs:
                packed = (
                    (
                        base_rgba[:, :, 0].astype(np.uint32)
                        << 16
                    )
                    | (
                        base_rgba[:, :, 1].astype(np.uint32)
                        << 8
                    )
                    | base_rgba[:, :, 2].astype(np.uint32)
                )
                selected_values = np.fromiter(
                    (
                        (r << 16) | (g << 8) | b
                        for r, g, b in selected_rgbs
                    ),
                    dtype=np.uint32,
                    count=len(selected_rgbs),
                )
                eligible |= (
                    (base_rgba[:, :, 3] > 0)
                    & np.isin(packed, selected_values)
                )
                if self.background_mask_rgb in selected_rgbs:
                    eligible |= (
                        (base_rgba[:, :, 3] == 0)
                        | np.all(
                            base_rgba[:, :, :3] == 255,
                            axis=2,
                        )
                    )

            overlay_rgba[:, :, 3][~eligible] = 0
            overlay = self._rgba_array_to_qimage(
                overlay_rgba
            )

        colors = self._blend_overlay_into_active_layer(
            overlay,
            rect.topLeft(),
            exact_colors=(source_color,),
        )
        color_set = getattr(
            self,
            "_brush_blended_colors",
            None,
        )
        if color_set is not None:
            color_set.update(colors)

        self.active_layer.has_content = True
        self._update_stroke_region(a, b, draw_width)

    @staticmethod
    def _scanline_connected_region(*args, **kwargs):
        return imaging.scanline_connected_region(*args, **kwargs)

    def _bucket_tools(self):
        """ツールパネル。ウィンドウを持たないテスト用キャンバスでは ``None``。"""
        window: Any = self.window()
        return getattr(window, "tools", None)

    def bucket_options(self):
        """現在のバケツ設定を UI から読み出す。"""
        # 含み塗りの対象はサブカラー。使用色パネルでの選択に依存させると、
        # 何を含むのかを塗る前に別の場所で準備する必要があり分かりにくい。
        sub = QColor(self.sub_color)
        mask_colors = ((sub.red(), sub.green(), sub.blue()),)
        return bucket_fill.BucketOptions.from_tools(
            self._bucket_tools(),
            mask_colors=mask_colors,
            background_selected=self.background_mask_rgb in mask_colors,
        )

    def _layer_fill_pixels(self, image):
        """RGBA8888 に変換した複製と、その画素ビューを返す。"""
        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = converted.width(), converted.height()
        rows = np.frombuffer(
            imaging.qimage_buffer(converted), dtype=np.uint8
        ).reshape((height, converted.bytesPerLine()))
        return converted, rows[:, :width * 4].reshape((height, width, 4))

    def _fill_patch(self, patch, patch_region, replacement):
        """``patch`` の ``patch_region`` を塗る。書き込んだ RGB の組を返す。

        白は消しゴムとして alpha を抜き、空の組を返す（使用色に登録しない）。
        """
        source_rgb = np.asarray(
            [replacement.red(), replacement.green(), replacement.blue()],
            dtype=np.uint8,
        )
        if bool(np.all(source_rgb == 255)):
            # 白バケツは本物の消しゴム：該当領域を alpha=0 へ抜く。
            patch[patch_region] = 0
            return ()

        opacity = max(0.0, min(1.0, float(getattr(self, "fill_opacity", 1.0))))
        if opacity >= 0.999999:
            # 100%は正規RGBをそのまま書き込む（近似色を生成しない）。
            patch[patch_region, :3] = source_rgb
            written_colors = (
                (int(source_rgb[0]), int(source_rgb[1]), int(source_rgb[2])),
            )
        else:
            # 100%未満だけ下地RGB（透明は白とみなす）と混色する。
            base_rgb = patch[patch_region, :3].astype(np.float32)
            base_rgb[patch[patch_region, 3] == 0] = 255.0
            mixed = np.clip(
                np.rint(
                    base_rgb * (1.0 - opacity)
                    + source_rgb.astype(np.float32)[None, :] * opacity
                ),
                0,
                255,
            ).astype(np.uint8)
            # 混色の結果が白になった画素を消しゴム扱いにしない。
            mixed[np.all(mixed == 255, axis=1)] = 254
            patch[patch_region, :3] = mixed
            written_colors = tuple(
                tuple(int(channel) for channel in row)
                for row in np.unique(mixed, axis=0)
            )
        patch[patch_region, 3] = 255
        return written_colors

    def flood_fill(self, p):
        if self._bucket_all_frames_enabled():
            self.flood_fill_over_frames(p)
            return
        image, pixels = self._layer_fill_pixels(self.active_layer.image)
        start_x, start_y = int(p.x()), int(p.y())
        selection_mask = self.selection_mask_bool(image.width(), image.height())
        result = bucket_fill.compute_fill_region(
            pixels, start_x, start_y, self.bucket_options(), selection_mask
        )
        if not result.ok:
            message = self._bucket_failure_message(result.reason)
            if message:
                self.status_message.emit(message)
            return

        # Undo は塗る範囲が決まってから、その外接矩形だけを保存する。
        # 大きなキャンバスでレイヤー全体をコピーしないため。
        x0, y0, x1, y1 = result.bounds
        fill_rect = QRect(x0, y0, x1 - x0, y1 - y0)
        if not self.push_layer_region_undo(fill_rect):
            return
        # 以降の書き込みは外接矩形の中だけで行い、その部分だけをレイヤーへ戻す。
        patch = pixels[y0:y1, x0:x1]
        written_colors = self._fill_patch(
            patch, result.mask[y0:y1, x0:x1], self.paint_source_color()
        )
        self._write_rgba_patch(patch, fill_rect)
        self.active_layer.has_content = True
        if written_colors:
            self._emit_actual_paint_colors(written_colors)
        self.cellChanged.emit(self.current_frame, self.active_layer_index)
        self.update()

    @staticmethod
    def _bucket_failure_message(reason):
        """塗れなかった理由のうち、ユーザーに伝える価値があるものだけ文言にする。"""
        if reason == bucket_fill.REASON_OUTSIDE_SELECTION:
            return tr("選択範囲の外側なので塗りを開始しませんでした。")
        if reason == bucket_fill.REASON_OPEN_REGION:
            return tr("領域がキャンバス端まで開いているため、塗りを開始しませんでした。")
        return None

    def _bucket_all_frames_enabled(self):
        tools = self._bucket_tools()
        checkbox = getattr(tools, "bucket_all_frames", None)
        return bool(checkbox is not None and checkbox.isChecked())

    def flood_fill_over_frames(self, p):
        """串刺し塗り：同じ座標を種として、スコープ内の各コマを塗る。

        コマごとに絵は動くので、同じ座標が別のコマでは線や別のパーツの上に来る
        ことがある。開始点の色種別（背景か、どの色か）が現在コマと一致するコマ
        だけを塗り、一致しないコマは触らない。これで線を塗り潰す事故を防ぐ。
        取りこぼしたコマは、そのコマを表示して塗り直せばよい。
        """
        window: Any = self.window()
        scope_controller = getattr(window, "scope", None)
        if scope_controller is None:
            return
        start_x, start_y = int(p.x()), int(p.y())
        options = self.bucket_options()
        replacement = self.paint_source_color()
        selection_mask = self.selection_mask_bool(
            self.active_layer.image.width(), self.active_layer.image.height()
        )
        if not self._seed_in_bounds(start_x, start_y):
            return
        # 現在コマの開始点の色種別を基準にする。各コマはこれと一致したときだけ塗る。
        _current, current_pixels = self._layer_fill_pixels(self.active_layer.image)
        reference = bucket_fill.seed_category(current_pixels, start_x, start_y)

        skipped = {"count": 0}
        written_colors = set()

        def op(context):
            image, pixels = self._layer_fill_pixels(context.layer.image)
            if not self._seed_in_bounds(start_x, start_y, image):
                return None
            if bucket_fill.seed_category(pixels, start_x, start_y) != reference:
                skipped["count"] += 1
                return None
            result = bucket_fill.compute_fill_region(
                pixels, start_x, start_y, options, selection_mask
            )
            if not result.ok:
                skipped["count"] += 1
                return None
            x0, y0, x1, y1 = result.bounds
            written_colors.update(self._fill_patch(
                pixels[y0:y1, x0:x1], result.mask[y0:y1, x0:x1], replacement
            ))
            return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

        scope = scope_controller.current_frame(True, selection_only=False)
        result = scope_controller.run_over(
            scope,
            op,
            label=tr("串刺し塗り"),
            cancellable=True,
        )
        if result.cancelled:
            self.status_message.emit(tr("串刺し塗りを中止しました。"))
            return
        if not result.changed:
            self.status_message.emit(
                tr("串刺し塗りの対象になるコマがありませんでした。")
            )
            return
        if written_colors:
            self._emit_actual_paint_colors(tuple(sorted(written_colors)))
        if skipped["count"]:
            self.status_message.emit(
                tr("串刺し塗り：{filled} コマを塗り、{skipped} コマは対象外でした。").format(
                    filled=result.changed_cells, skipped=skipped["count"]
                )
            )
        else:
            self.status_message.emit(
                tr("串刺し塗り：{filled} コマを塗りました。").format(
                    filled=result.changed_cells
                )
            )

    def _seed_in_bounds(self, start_x, start_y, image=None):
        image = image if image is not None else self.active_layer.image
        return 0 <= start_x < image.width() and 0 <= start_y < image.height()

    def _write_rgba_patch(self, patch, rect):
        """RGBA8888 の部分配列を、アクティブレイヤーの rect へそのまま書き戻す。"""
        patch_image = imaging.rgba_array_to_qimage(patch)
        painter = QPainter(self.active_layer.image)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Source
        )
        painter.drawImage(rect.topLeft(), patch_image)
        painter.end()

    def auto_select_region(self, point, modifiers=Qt.KeyboardModifier.NoModifier):
        """バケツと同じ連続領域判定で選択マスクを作成・加減算する。"""
        image = self.active_layer.image.convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        width, height = image.width(), image.height()
        start_x, start_y = int(point.x()), int(point.y())
        if not (0 <= start_x < width and 0 <= start_y < height):
            return False

        ptr = imaging.qimage_buffer(image)
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, image.bytesPerLine())
        )
        pixels = rows[:, :width * 4].reshape((height, width, 4))
        target = pixels[start_y, start_x]
        pseudo_background = (
            (pixels[:, :, 3] == 0)
            | np.all(pixels[:, :, :3] == 255, axis=2)
        )
        target_is_background = bool(
            int(target[3]) == 0
            or np.all(target[:3] == 255)
        )
        target_mask = (
            pseudo_background.copy()
            if target_is_background
            else (
                (pixels[:, :, 3] > 0)
                & np.all(pixels[:, :, :3] == target[:3], axis=2)
            )
        )

        window = self.window()
        tools = getattr(window, "tools", None)
        adjacent = bool(
            tools is None or tools.bucket_adjacent.isChecked()
        )
        close_gap = bool(
            tools is not None
            and tools.bucket_close_gap.isChecked()
            and adjacent
        )

        if adjacent:
            passable = target_mask
            if close_gap and tools is not None:
                radius = max(1, int(tools.bucket_gap_width.value()))
                boundary = ~target_mask
                expanded = boundary.copy()
                for _ in range(radius):
                    source = expanded.copy()
                    expanded[1:, :] |= source[:-1, :]
                    expanded[:-1, :] |= source[1:, :]
                    expanded[:, 1:] |= source[:, :-1]
                    expanded[:, :-1] |= source[:, 1:]
                    expanded[1:, 1:] |= source[:-1, :-1]
                    expanded[:-1, :-1] |= source[1:, 1:]
                    expanded[1:, :-1] |= source[:-1, 1:]
                    expanded[:-1, 1:] |= source[1:, :-1]
                virtual_boundary = expanded
                for _ in range(radius):
                    padded = np.pad(
                        virtual_boundary,
                        ((1, 1), (1, 1)),
                        mode="constant",
                        constant_values=False,
                    )
                    virtual_boundary = (
                        padded[0:-2, 0:-2]
                        & padded[0:-2, 1:-1]
                        & padded[0:-2, 2:]
                        & padded[1:-1, 0:-2]
                        & padded[1:-1, 1:-1]
                        & padded[1:-1, 2:]
                        & padded[2:, 0:-2]
                        & padded[2:, 1:-1]
                        & padded[2:, 2:]
                    )
                passable = target_mask & ~virtual_boundary
                passable[start_y, start_x] = bool(
                    target_mask[start_y, start_x]
                )
            region = self._scanline_connected_region(
                passable, (start_x, start_y)
            )
            require_closed = bool(
                tools is not None
                and tools.bucket_require_closed.isChecked()
            )
            if require_closed and (
                np.any(region[0, :]) or np.any(region[-1, :])
                or np.any(region[:, 0]) or np.any(region[:, -1])
            ):
                self.status_message.emit(
                    tr("領域がキャンバス端まで開いているため選択しませんでした。")
                )
                return False
        else:
            region = target_mask.copy()

        current = self.selection_mask_bool(width, height)
        if current is None:
            current = np.zeros((height, width), dtype=bool)
        if modifiers & Qt.KeyboardModifier.AltModifier:
            combined = current & ~region
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            combined = current | region
        else:
            combined = region

        if not np.any(combined):
            self.clear_selection()
            return True

        contour_owner: Any = self.window()
        contours = contour_owner._mask_contours(combined)
        contour = contour_owner._largest_contour(contours)
        if not contour:
            return False
        ys, xs = np.nonzero(combined)
        self.selection_polygon = contour
        self.selection_mask_override = combined
        self.selection_outline_polygons = contours
        self.selection_mask_rect = QRectF(
            int(xs.min()), int(ys.min()),
            int(xs.max() - xs.min() + 1),
            int(ys.max() - ys.min() + 1),
        )
        self._selection_fade_started = time.monotonic()
        self.lasso = []
        self.rect_start = None
        self.rect_end = None
        self.selectionChanged.emit()
        self.update()
        return True


    def _line_preview_path(self):
        if self.line_start is None or self.line_end is None:
            return None
        path = QPainterPath(QPointF(self.line_start))
        if self.line_curve_stage == 2 and self.line_control is not None:
            path.quadTo(QPointF(self.line_control), QPointF(self.line_end))
        else:
            path.lineTo(QPointF(self.line_end))
        return path

    def _draw_tapered_path(
        self,
        painter,
        path,
        color,
        base_width,
        start_width=None,
        end_width=None,
        start_curve=1.0,
        end_curve=1.0,
    ):
        """入り抜き幅を変化させながら、非AA線分として描画する。"""
        try:
            length = max(1.0, float(path.length()))
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            log.debug("path.length() failed, using endpoint distance: %s", exc)
            length = max(
                1.0,
                math.hypot(
                    self.line_end.x() - self.line_start.x(),
                    self.line_end.y() - self.line_start.y(),
                ),
            )
        steps = max(2, min(4096, int(math.ceil(length * 1.5))))
        previous = path.pointAtPercent(0.0)
        for index in range(1, steps + 1):
            ratio = index / steps
            point = path.pointAtPercent(ratio)
            width = float(base_width)
            if start_width is not None and ratio <= 0.5:
                local = max(0.0, min(1.0, ratio * 2.0))
                eased = local ** max(0.05, float(start_curve))
                width = float(start_width) + (
                    float(base_width) - float(start_width)
                ) * eased
            elif end_width is not None and ratio >= 0.5:
                local = max(0.0, min(1.0, (ratio - 0.5) * 2.0))
                eased = local ** max(0.05, float(end_curve))
                width = float(base_width) + (
                    float(end_width) - float(base_width)
                ) * eased
            width = max(0.5, width)
            pen = QPen(
                color,
                width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            painter.drawLine(previous, point)
            previous = point

    def commit_line_tool(self):
        path = self._line_preview_path()
        if path is None:
            return
        self.ensure_editable_key()
        panel = self._active_tool_panel()
        base_width = max(0.5, float(self.pen_size))
        start_width = None
        end_width = None
        start_curve = 1.0
        end_curve = 1.0
        if panel is not None:
            if panel.line_taper_in.isChecked():
                start_width = (
                    panel.line_taper_in_size.value() / 2.0
                )
                start_curve = (
                    panel.line_taper_in_curve.value() / 100.0
                )
            if panel.line_taper_out.isChecked():
                end_width = (
                    panel.line_taper_out_size.value() / 2.0
                )
                end_curve = (
                    1.0
                    / max(
                        0.05,
                        panel.line_taper_out_curve.value() / 100.0,
                    )
                )

        source_color = self.paint_source_color()
        maximum_width = max(
            base_width,
            float(start_width or 0.0),
            float(end_width or 0.0),
        )
        undo_margin = int(math.ceil(maximum_width / 2.0)) + 3
        undo_rect = path.boundingRect().adjusted(
            -undo_margin, -undo_margin, undo_margin, undo_margin
        ).toAlignedRect()
        if not self.push_layer_region_undo(undo_rect):
            return
        overlay = QImage(
            self.active_layer.image.width(),
            self.active_layer.image.height(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        overlay.fill(Qt.GlobalColor.transparent)

        painter = QPainter(overlay)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            False,
        )
        self.apply_selection_clip(painter)
        if start_width is None and end_width is None:
            painter.setPen(
                QPen(
                    source_color,
                    base_width,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.drawPath(path)
        else:
            self._draw_tapered_path(
                painter,
                path,
                source_color,
                base_width,
                start_width,
                end_width,
                start_curve,
                end_curve,
            )
        painter.end()

        colors = self._blend_overlay_into_active_layer(
            overlay,
            QPoint(0, 0),
            exact_colors=(source_color,),
        )
        self._emit_actual_paint_colors(colors)

        self.active_layer.has_content = True
        self.cellChanged.emit(
            self.current_frame,
            self.active_layer_index,
        )
        self.line_start = None
        self.line_end = None
        self.line_control = None
        self.line_curve_stage = 0
        self.drawing = False
        self.update()

    def _shape_rect(self):
        if self.shape_start is None or self.shape_end is None:
            return QRectF()
        start = QPointF(self.shape_start)
        end = QPointF(self.shape_end)
        panel = self._active_tool_panel()
        if panel is not None and panel.shape_lock_ratio.isChecked():
            dx = end.x() - start.x()
            dy = end.y() - start.y()
            size = max(abs(dx), abs(dy))
            end = QPointF(
                start.x() + (size if dx >= 0 else -size),
                start.y() + (size if dy >= 0 else -size),
            )
        return QRectF(start, end).normalized()

    def _shape_path(self):
        rect = self._shape_rect()
        if rect.isEmpty():
            return None
        panel = self._active_tool_panel()
        shape_type = panel.shape_type.currentData() if panel is not None else "polygon"
        path = QPainterPath()
        if shape_type == "ellipse":
            path.addEllipse(rect)
            return path

        corners = max(3, int(panel.shape_corners.value()) if panel is not None else 4)
        center = rect.center()
        radius_x = rect.width() / 2.0
        radius_y = rect.height() / 2.0
        if corners == 4:
            # 4角はひし形ではなく、ドラッグ範囲に沿う□（長方形）にする。
            points = [
                QPointF(rect.left(), rect.top()),
                QPointF(rect.right(), rect.top()),
                QPointF(rect.right(), rect.bottom()),
                QPointF(rect.left(), rect.bottom()),
            ]
        else:
            points = []
            for index in range(corners):
                angle = -math.pi / 2.0 + (2.0 * math.pi * index / corners)
                points.append(
                    QPointF(
                        center.x() + math.cos(angle) * radius_x,
                        center.y() + math.sin(angle) * radius_y,
                    )
                )
        if points:
            path.moveTo(points[0])
            for point in points[1:]:
                path.lineTo(point)
            path.closeSubpath()
        return path

    def commit_shape_tool(self):
        path = self._shape_path()
        if path is None:
            self.shape_start = None
            self.shape_end = None
            self.drawing = False
            self.update()
            return

        self.ensure_editable_key()
        panel = self._active_tool_panel()
        use_split_colors = bool(
            panel is not None
            and panel.shape_sub_outline_main_fill.isChecked()
        )
        fill_inside = bool(
            panel is not None
            and panel.shape_fill_inside.isChecked()
        )
        outline_width = (
            panel.shape_outline_width.value() / 2.0
            if panel is not None
            else 1.0
        )
        undo_margin = int(math.ceil(float(outline_width) / 2.0)) + 3
        undo_rect = path.boundingRect().adjusted(
            -undo_margin, -undo_margin, undo_margin, undo_margin
        ).toAlignedRect()
        if not self.push_layer_region_undo(undo_rect):
            return

        if use_split_colors:
            outline_color = self.paint_source_color(
                self.sub_color
            )
            fill_color = self.paint_source_color(
                self.main_color
            )
        else:
            outline_color = self.paint_source_color()
            fill_color = QColor(outline_color)

        overlay = QImage(
            self.active_layer.image.width(),
            self.active_layer.image.height(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        overlay.fill(Qt.GlobalColor.transparent)

        painter = QPainter(overlay)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            False,
        )
        self.apply_selection_clip(painter)
        if fill_inside:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill_color)
            painter.drawPath(path)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                outline_color,
                max(0.5, float(outline_width)),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawPath(path)
        painter.end()

        exact_shape_colors = (
            (outline_color, fill_color)
            if fill_inside
            else (outline_color,)
        )
        colors = self._blend_overlay_into_active_layer(
            overlay,
            QPoint(0, 0),
            exact_colors=exact_shape_colors,
        )
        self._emit_actual_paint_colors(colors)

        self.active_layer.has_content = True
        self.cellChanged.emit(
            self.current_frame,
            self.active_layer_index,
        )
        self.shape_start = None
        self.shape_end = None
        self.drawing = False
        self.update()

    def fill_lasso_polygon(self, points):
        if len(points) < 3:
            return

        self.ensure_editable_key()
        window: Any = self.window()
        outline_and_fill = bool(
            hasattr(window, "tools")
            and window.tools.lasso_main_outline_sub_fill.isChecked()
        )
        mask_only = not getattr(self, "mask_all_enabled", True)
        inside_boundary = bool(
            hasattr(window, "tools")
            and window.tools.lasso_inside_boundary.isChecked()
        )
        outline_width = (
            window.tools.lasso_outline_width.value() / 2.0
            if outline_and_fill and hasattr(window, "tools")
            else 0.0
        )

        polygon = QPolygonF(points)
        margin = int(math.ceil(outline_width / 2.0)) + 2
        rect = polygon.boundingRect().adjusted(
            -margin, -margin, margin, margin
        ).toAlignedRect().intersected(self.active_layer.image.rect())
        if rect.isEmpty():
            return
        self.push_layer_region_undo(rect)

        local_polygon = QPolygonF([
            QPointF(point.x() - rect.x(), point.y() - rect.y())
            for point in points
        ])
        overlay = QImage(
            rect.width(),
            rect.height(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        overlay.fill(Qt.GlobalColor.transparent)

        fill_color = (
            self.paint_source_color(self.main_color)
            if outline_and_fill
            else self.paint_source_color()
        )

        painter = QPainter(overlay)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.apply_selection_clip(painter, rect.x(), rect.y())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill_color)
        painter.drawPolygon(local_polygon)

        line_color = None
        if outline_and_fill:
            line_color = self.paint_source_color(self.sub_color)
            line_pen = QPen(line_color)
            line_pen.setWidthF(max(0.5, float(outline_width)))
            line_pen.setStyle(Qt.PenStyle.SolidLine)
            line_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(line_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(local_polygon)
        painter.end()

        width, height = rect.width(), rect.height()
        source = self.active_layer.image.copy(rect).convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        mask_image = overlay.convertToFormat(
            QImage.Format.Format_RGBA8888
        )

        source_ptr = imaging.qimage_buffer(source)
        mask_ptr = imaging.qimage_buffer(mask_image)
        source_rows = np.frombuffer(
            source_ptr, dtype=np.uint8
        ).reshape((height, source.bytesPerLine()))
        mask_rows = np.frombuffer(
            mask_ptr, dtype=np.uint8
        ).reshape((height, mask_image.bytesPerLine()))
        source_pixels = source_rows[:, :width * 4].reshape(
            (height, width, 4)
        )
        mask_pixels = mask_rows[:, :width * 4].reshape(
            (height, width, 4)
        )

        if mask_only:
            selected_rgbs = self.selected_mask_colors()
            if not selected_rgbs:
                target_mask = np.zeros((height, width), dtype=bool)
            else:
                packed_source = (
                    (source_pixels[:, :, 0].astype(np.uint32) << 16)
                    | (source_pixels[:, :, 1].astype(np.uint32) << 8)
                    | source_pixels[:, :, 2].astype(np.uint32)
                )
                selected_values = np.fromiter(
                    ((r << 16) | (g << 8) | b for r, g, b in selected_rgbs),
                    dtype=np.uint32,
                    count=len(selected_rgbs),
                )
                target_mask = (
                    (source_pixels[:, :, 3] > 0)
                    & np.isin(packed_source, selected_values)
                )
                if self.background_mask_rgb in selected_rgbs:
                    target_mask |= (
                        (source_pixels[:, :, 3] == 0)
                        | np.all(
                            source_pixels[:, :, :3] == 255,
                            axis=2,
                        )
                    )
            mask_pixels[:, :, 3][~target_mask] = 0

        if inside_boundary:
            polygon_area = mask_pixels[:, :, 3] > 0
            passable = polygon_area & (
                (source_pixels[:, :, 3] == 0)
                | np.all(
                    source_pixels[:, :, :3] == 255,
                    axis=2,
                )
            )
            candidates = np.argwhere(passable)
            if candidates.size:
                center = np.mean(
                    np.array([[point.x() - rect.x(), point.y() - rect.y()]
                              for point in points]),
                    axis=0,
                )
                distances = (
                    (candidates[:, 1] - center[0]) ** 2
                    + (candidates[:, 0] - center[1]) ** 2
                )
                seed_y, seed_x = candidates[int(np.argmin(distances))]
                region = self._scanline_connected_region(
                    passable, (int(seed_x), int(seed_y))
                )
                mask_pixels[:, :, 3][~region] = 0
            else:
                mask_pixels[:, :, 3] = 0

        overlay = mask_image.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        exact_lasso_colors = (
            (fill_color, line_color)
            if line_color is not None
            else (fill_color,)
        )
        colors = self._blend_overlay_into_active_layer(
            overlay,
            rect.topLeft(),
            exact_colors=exact_lasso_colors,
            opacity=getattr(self, "fill_opacity", 1.0),
        )

        self.active_layer.has_content = True
        self._emit_actual_paint_colors(colors)
        self.cellChanged.emit(
            self.current_frame,
            self.active_layer_index,
        )
        self.update()
