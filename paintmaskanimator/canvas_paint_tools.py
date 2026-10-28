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
from typing import Any
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF
from .i18n import tr
from ._canvas_members import CanvasMembers
from . import bucket_fill, imaging
from .undo_entries import LayerRegionUndo
from .pressure import _pressure_bezier_at
from .brush_raster import paint_segment
from .logging_setup import get_logger

log = get_logger(__name__)


#: ドラッグ塗りで、端に接した領域を「外の背景ではない」とみなす大きさの上限
#: （画像全体に対する割合）。最初にクリックした部分の3倍までもよしとする。
BUCKET_DRAG_EDGE_AREA = 0.1


class PaintToolsMixin(CanvasMembers):
    # バケツのドラッグ塗りの途中経過（押している間だけ持つ）。
    _bucket_drag: Any = None

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
        paint_segment(painter, local_a, local_b, source_color, draw_width)
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
        # 含み塗りの対象は、ツールパネルの「含む色」に登録した色。未登録なら
        # サブカラー。使用色パネルでの選択に依存させると、何を含むのかを
        # 塗る前に別の場所で準備する必要があり分かりにくい。
        tools = self._bucket_tools()
        slots = getattr(tools, "bucket_include_colors", None)
        mask_colors = tuple(slots.colors()) if slots is not None else ()
        if not mask_colors:
            sub = QColor(self.sub_color)
            mask_colors = ((sub.red(), sub.green(), sub.blue()),)
        return bucket_fill.BucketOptions.from_tools(
            tools,
            mask_colors=mask_colors,
            background_selected=self.background_mask_rgb in mask_colors,
            island_replacements=self._island_replacements(),
        )

    def _island_replacements(self):
        """使用色パネルに登録した置換色 ``((元の RGB, 置換色の RGB), ...)``。"""
        window: Any = self.window()
        palette = getattr(window, "palette", None)
        replacements = getattr(palette, "replacements", None) or {}
        return tuple(
            (tuple(int(value) for value in source[:3]),
             (color.red(), color.green(), color.blue()))
            for source, color in replacements.items()
        )

    def _fill_islands(self, patch, islands, bounds):
        """囲まれた島を、それぞれの置換色で ``patch``（外接矩形の部分）へ塗る。"""
        x0, y0, x1, y1 = bounds
        written = set()
        for rgb, mask in islands:
            local = mask[y0:y1, x0:x1]
            if local.any():
                written.update(self._fill_patch(patch, local, QColor(*rgb)))
        return written

    def _layer_fill_pixels(self, image):
        """RGBA8888 に変換した複製と、その画素ビューを返す。"""
        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = converted.width(), converted.height()
        rows = np.frombuffer(
            imaging.qimage_buffer(converted), dtype=np.uint8
        ).reshape((height, converted.bytesPerLine()))
        return converted, rows[:, :width * 4].reshape((height, width, 4))

    # ------------------------------------------------------------------
    # 参照レイヤー：どの絵を見て塗る範囲を決めるか。
    # ------------------------------------------------------------------
    def bucket_reference_pixels(self, options, frame, layer_index,
                                layer_image, layer_pixels):
        """判定に使う ``(保持用の QImage, RGBA8888 の画素)`` を返す。

        塗る先はいつもアクティブレイヤーなので、ここで返すのは「どこを塗るか」を
        決めるための絵だけ。参照が「選択したレイヤーのみ」なら、呼び出し側が
        すでに持っている画素をそのまま返す（余分な変換をしない）。

        画素は返した QImage のバッファを指すので、使い終わるまで QImage を
        手放さないように両方を返す。
        """
        image = self._bucket_reference_image(
            options, frame, layer_index, layer_image
        )
        if image is layer_image:
            return layer_image, layer_pixels
        drag = self._bucket_drag
        if drag is not None:
            key = image.cacheKey()
            cached = drag["reference_pixels"].get(key)
            if cached is None:
                cached = self._layer_fill_pixels(image)
                drag["reference_pixels"].clear()
                drag["reference_pixels"][key] = cached
            return cached
        return self._layer_fill_pixels(image)

    def _bucket_reference_image(self, options, frame, layer_index, fallback):
        """参照レイヤーの設定に合う絵を返す。無ければ ``fallback`` のまま。"""
        reference = getattr(
            options, "reference", bucket_fill.REFERENCE_LAYER
        )
        if reference == bucket_fill.REFERENCE_CANVAS:
            return self._bucket_canvas_reference(frame, fallback)
        if reference == bucket_fill.REFERENCE_INITIAL:
            key_frame = self.resolve_key_frame(frame, layer_index)
            if key_frame is None:
                return fallback
            initial = self.frames[key_frame].layers[layer_index].initial_image
            # 初期状態を保持していないセルは、今の絵で判定するしかない。
            if initial is None or initial.size() != fallback.size():
                return fallback
            return initial
        return fallback

    def _bucket_canvas_reference(self, frame, fallback):
        """表示しているレイヤーを重ねた絵。別のレイヤーの線でも区切れるように。

        用紙レイヤーは外す。キャンバス一面の不透明な色で覆われると、透明を
        たどる対象色が働かなくなるため。表示プレビューを通さない生の絵を重ねる。
        """
        if not (0 <= frame < len(self.frames)):
            return fallback
        result = QImage(
            fallback.size(), QImage.Format.Format_ARGB32_Premultiplied
        )
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        for layer_index in range(len(self.frames[frame].layers)):
            key_frame = self.resolve_key_frame(frame, layer_index)
            if key_frame is None:
                continue
            layer = self.frames[key_frame].layers[layer_index]
            if not layer.visible or layer.is_paper:
                continue
            if layer.image is None or layer.image.isNull():
                continue
            painter.setOpacity(layer.opacity)
            painter.drawImage(0, 0, layer.image)
        painter.end()
        return result

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
        # 串刺し塗りの指定待ち中にチェックを外したら、途中の串刺し塗りは捨てる。
        self.abort_skewer_fill()
        self._bucket_fill_at(p, push_undo=True)

    def _bucket_fill_at(self, p, push_undo=True, quiet=False, accept=None, line_cache=None):
        """``p`` を種に1か所塗る。``(塗ったマスク, 塗らずに済ませた領域)`` を返す。

        塗れなければ塗ったマスクは None。``push_undo`` が偽なら Undo を積まない
        （ドラッグ塗りが最後にまとめて積む）。``accept`` を渡すと、塗る前に
        ``accept(結果)`` で塗ってよいかを決め、偽ならその領域を2つ目に返す。
        """
        drag = self._bucket_drag
        active = self.active_layer.image
        if (drag is not None and drag["frame"] == self.current_frame
                and drag["layer"] == self.active_layer_index
                and drag["pixels_key"] == active.cacheKey()):
            image, pixels = drag["pixels"]
        else:
            image, pixels = self._layer_fill_pixels(active)
        start_x, start_y = int(p.x()), int(p.y())
        selection_mask = self.selection_mask_bool(image.width(), image.height())
        options = self.bucket_options()
        # 判定は参照レイヤーの絵で行い、書き込みはアクティブレイヤーへ。
        _reference_image, reference_pixels = self.bucket_reference_pixels(
            options, self.current_frame, self.active_layer_index, image, pixels
        )
        result = bucket_fill.compute_fill_region(
            reference_pixels, start_x, start_y, options, selection_mask, line_cache=line_cache,
        )
        if not result.ok:
            message = None if quiet else self._bucket_failure_message(result.reason)
            if message:
                self.status_message.emit(message)
            return None, result.region
        if accept is not None and not accept(result):
            return None, result.mask

        # Undo は塗る範囲が決まってから、その外接矩形だけを保存する。
        # 大きなキャンバスでレイヤー全体をコピーしないため。
        assert result.bounds is not None  # ok なら必ず外接矩形がある
        x0, y0, x1, y1 = result.bounds
        fill_rect = QRect(x0, y0, x1 - x0, y1 - y0)
        if push_undo and not self.push_layer_region_undo(fill_rect):
            return None, None
        # 以降の書き込みは外接矩形の中だけで行い、その部分だけをレイヤーへ戻す。
        patch = pixels[y0:y1, x0:x1]
        written_colors = set(self._fill_patch(
            patch, result.mask[y0:y1, x0:x1], self.paint_source_color()
        ))
        written_colors.update(self._fill_islands(patch, result.islands, result.bounds))
        written_colors = tuple(sorted(written_colors))
        self._write_rgba_patch(patch, fill_rect)
        if drag is not None:
            drag["pixels"] = (image, pixels)
            drag["pixels_key"] = self.active_layer.image.cacheKey()
        self.active_layer.has_content = True
        if written_colors:
            self._emit_actual_paint_colors(written_colors)
        self.cellChanged.emit(self.current_frame, self.active_layer_index)
        self.update()
        return result.mask, None

    # ------------------------------------------------------------------
    # ドラッグ塗り：なぞった軌跡が通った囲まれた部分を、それぞれ塗る。
    # ------------------------------------------------------------------
    def begin_bucket_drag(self, p):
        """バケツを押した。最初の1か所を塗り、なぞりの準備をする。"""
        self.finish_bucket_drag()
        layer = self.active_layer
        # 画像は暗黙共有なので、ここでは複製しない（塗った時点で分かれる）。
        before = QImage(layer.image)
        converted, layer_pixels = self._layer_fill_pixels(before)
        width, height = before.width(), before.height()
        x, y = int(p.x()), int(p.y())
        if not (0 <= x < width and 0 <= y < height):
            self._bucket_drag = None
            return
        options = self.bucket_options()
        # なぞる先の判定も、1か所塗るときと同じ絵を見る。
        _reference_image, original = self.bucket_reference_pixels(
            options, self.current_frame, self.active_layer_index,
            converted, layer_pixels,
        )
        category = bucket_fill.seed_category(original, x, y)
        # なぞった先で塗り始めてよい画素。線の上を通っても線を塗らないよう、
        # 「すべての色」では最初にクリックした色の種類、それ以外は対象色の画素に限る。
        eligible = bucket_fill.fill_target_mask(
            original, options.target_color, category
        )
        self._bucket_drag = {
            "frame": int(self.current_frame),
            "layer": int(self.active_layer_index),
            "before": before,
            "has_content": bool(layer.has_content),
            "eligible": eligible,
            # 試した場所（塗れなかった点も含む）と、実際に塗った範囲。
            "tried": np.zeros((height, width), dtype=bool),
            "painted": np.zeros((height, width), dtype=bool),
            "last": QPointF(p),
            "line_cache": {},
            "pixels": (converted, layer_pixels),
            "pixels_key": layer.image.cacheKey(),
            "reference_pixels": {},
        }
        # 最初の1か所はクリックと同じ（開いた領域も設定どおりに塗る）。
        self._bucket_drag_fill(p, first=True)

    def continue_bucket_drag(self, p):
        """なぞった区間上の、まだ塗っていない囲まれた部分を塗る。"""
        drag = self._bucket_drag
        if drag is None:
            return
        start = drag["last"]
        distance = math.hypot(p.x() - start.x(), p.y() - start.y())
        steps = max(1, int(math.ceil(distance)))
        for step in range(1, steps + 1):
            t = step / steps
            self._bucket_drag_fill(
                QPointF(
                    start.x() + (p.x() - start.x()) * t,
                    start.y() + (p.y() - start.y()) * t,
                ),
                first=False,
            )
        drag["last"] = QPointF(p)

    def _bucket_drag_fill(self, p, first):
        drag = self._bucket_drag
        x, y = int(p.x()), int(p.y())
        tried = drag["tried"]
        height, width = tried.shape
        if not (0 <= x < width and 0 <= y < height):
            return
        if tried[y, x] or not drag["eligible"][y, x]:
            return
        mask, skipped = self._bucket_fill_at(
            QPointF(x, y), push_undo=False, quiet=not first,
            accept=None if first else self._bucket_drag_accepts,
            line_cache=drag["line_cache"],
        )
        # 塗れなかった点・塗らなかった領域も、同じ場所で何度も判定しない。
        tried[y, x] = True
        if skipped is not None:
            tried |= skipped
        if mask is not None:
            tried |= mask
            drag["painted"] |= mask
            if first:
                drag["first_area"] = int(np.count_nonzero(mask))

    def _bucket_drag_accepts(self, result):
        """なぞった先の領域を塗るか。外の背景だけは塗らない。

        余白の無いキャンバスでは、フレームの端で閉じた部分（端まで線を引いた所）も
        外の背景も「端に接した領域」になる。外の背景はふつう画面の大半を占めるので、
        端に接していて大きすぎる領域だけを外の背景とみなして塗らない。
        """
        region = result.mask
        touches_edge = bool(
            region[0].any() or region[-1].any()
            or region[:, 0].any() or region[:, -1].any()
        )
        if not touches_edge:
            return True
        area = int(np.count_nonzero(region))
        first_area = int(self._bucket_drag.get("first_area", 0)) if self._bucket_drag else 0
        limit = max(3 * first_area, int(region.size * BUCKET_DRAG_EDGE_AREA))
        return area <= limit

    def finish_bucket_drag(self):
        """ドラッグで塗った分を、Undo 1回で戻せるようにまとめて積む。"""
        drag = self._bucket_drag
        self._bucket_drag = None
        if drag is None:
            return
        if not self._bucket_drag_target_exists(drag):
            return
        ys, xs = np.nonzero(drag["painted"])
        if not xs.size:
            return
        rect = QRect(
            int(xs.min()), int(ys.min()),
            int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1),
        )
        self.push_undo(LayerRegionUndo(
            drag["frame"], drag["layer"], rect,
            drag["before"].copy(rect), drag["has_content"],
        ))

    def _bucket_drag_target_exists(self, drag):
        return (
            0 <= drag["frame"] < len(self.frames)
            and 0 <= drag["layer"] < len(self.frames[drag["frame"]].layers)
        )

    @staticmethod
    def _bucket_failure_message(reason):
        """塗れなかった理由のうち、ユーザーに伝える価値があるものだけ文言にする。"""
        if reason == bucket_fill.REASON_OUTSIDE_SELECTION:
            return tr("選択範囲の外側なので塗りを開始しませんでした。")
        if reason == bucket_fill.REASON_OPEN_REGION:
            return tr("領域がキャンバス端まで開いているため、塗りを開始しませんでした。")
        if reason == bucket_fill.REASON_NOT_TARGET:
            return tr("クリックした場所は「対象色」で塗る対象にしていない色です。")
        return None

    def _bucket_all_frames_enabled(self):
        tools = self._bucket_tools()
        checkbox = getattr(tools, "bucket_all_frames", None)
        return bool(checkbox is not None and checkbox.isChecked())

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

        return self.set_selection_mask(combined)


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

    def _begin_brush_straight_line(self, point):
        anchor = getattr(self, "_brush_line_anchor", None)
        key = (self.current_frame, self.active_layer_index)
        self._brush_shift_line = True
        self.line_start = QPointF(anchor[1] if anchor is not None and anchor[0] == key else point)
        self.line_end = QPointF(point)
        self.line_control = None
        self.line_curve_stage = 1
        self.drawing = True
        self.update()

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
        if panel is not None and not getattr(self, "_brush_shift_line", False):
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
        if getattr(self, "_brush_shift_line", False):
            self._brush_line_anchor = (
                (self.current_frame, self.active_layer_index), QPointF(self.line_end),
            )
        self._brush_shift_line = False
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
        mask_image = overlay.convertToFormat(
            QImage.Format.Format_RGBA8888
        )

        mask_ptr = imaging.qimage_buffer(mask_image)
        mask_rows = np.frombuffer(
            mask_ptr, dtype=np.uint8
        ).reshape((height, mask_image.bytesPerLine()))
        mask_pixels = mask_rows[:, :width * 4].reshape(
            (height, width, 4)
        )

        options = bucket_fill.BucketOptions.from_tools(
            window.tools if hasattr(window, "tools") else None,
        )
        reference = self._bucket_reference_image(
            options, self.current_frame, self.active_layer_index, self.active_layer.image,
        )
        reference_owner, reference_pixels = self._layer_fill_pixels(reference.copy(rect))
        if options.target_color != bucket_fill.TARGET_ALL:
            if options.target_color in (
                bucket_fill.TARGET_OPAQUE_AND_INNER_TRANSPARENT,
                bucket_fill.TARGET_TRANSPARENT_AND_CLOSED_OPAQUE,
            ):
                # 内部の穴は投げ縄の外側まで含む元の参照画像で判断する。
                full_owner, full_pixels = self._layer_fill_pixels(reference)
                eligible = bucket_fill.fill_target_mask(full_pixels, options.target_color, None)
                eligible |= bucket_fill._enclosed_by(eligible)
                eligible = eligible[rect.y():rect.y() + height, rect.x():rect.x() + width]
            else:
                eligible = bucket_fill.fill_target_mask(reference_pixels, options.target_color, None)
            mask_pixels[:, :, 3][~eligible] = 0

        if mask_only:
            selected_rgbs = self.selected_mask_colors()
            if not selected_rgbs:
                target_mask = np.zeros((height, width), dtype=bool)
            else:
                packed_source = (
                    (reference_pixels[:, :, 0].astype(np.uint32) << 16)
                    | (reference_pixels[:, :, 1].astype(np.uint32) << 8)
                    | reference_pixels[:, :, 2].astype(np.uint32)
                )
                selected_values = np.fromiter(
                    ((r << 16) | (g << 8) | b for r, g, b in selected_rgbs),
                    dtype=np.uint32,
                    count=len(selected_rgbs),
                )
                target_mask = (
                    (reference_pixels[:, :, 3] > 0)
                    & np.isin(packed_source, selected_values)
                )
                if self.background_mask_rgb in selected_rgbs:
                    target_mask |= (
                        (reference_pixels[:, :, 3] == 0)
                        | np.all(
                            reference_pixels[:, :, :3] == 255,
                            axis=2,
                        )
                    )
            mask_pixels[:, :, 3][~target_mask] = 0

        if inside_boundary:
            polygon_area = mask_pixels[:, :, 3] > 0
            passable = polygon_area.copy()
            if options.target_color == bucket_fill.TARGET_ALL:
                passable &= (
                    (reference_pixels[:, :, 3] == 0)
                    | np.all(reference_pixels[:, :, :3] == 255, axis=2)
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

        if not mask_pixels[:, :, 3].any():
            return
        self.push_layer_region_undo(rect)
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
