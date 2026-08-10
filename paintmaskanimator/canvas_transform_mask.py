"""TP-quality transform: color-mask preparation and deformation mesh.

Split out of ``canvas.py`` as a mixin. These methods build and cache the
per-color masks used by the "TP quality" transform, render/project those masks
through the deformation mesh, and manage the mesh grid + preview. They run
against a live ``PaintCanvas`` instance and reuse its selection/transform state.
"""
from .common import *  # noqa: F401,F403
from . import geometry, imaging
from .utils import workspace_size
from .logging_setup import get_logger

log = get_logger(__name__)


class TransformMaskMixin:
    def _invalidate_tp_preview_cache(self, geometry=True):
        """Invalidate TP output; geometry=False keeps transformed mask cache."""
        self._tp_preview_cache_key = None
        self._tp_preview_cache_image = None
        if geometry:
            self._tp_geometry_cache_key = None
            self._tp_geometry_cache_bbox = None
            self._tp_geometry_cache_fill_overlay = None
            self._tp_geometry_cache_line_soft = []

    def _clear_tp_transform_masks(self, clear_proxy=True):
        self.transform_tp_palette = []
        self.transform_tp_masks = []
        self.transform_tp_line_masks = []
        self.transform_tp_prepared_preview = None
        self._tp_mask_source_key = None
        if clear_proxy:
            self._tp_proxy_source_key = None
            self._tp_proxy_source_image = None
        self._invalidate_tp_preview_cache()

    def _tp_mask_key(self, source):
        if source is None or source.isNull():
            return None
        return (
            int(source.cacheKey()),
            int(source.width()),
            int(source.height()),
            tuple(self.transform_tp_line_colors),
            bool(self._tp_proxy_rendering),
        )

    @staticmethod
    def _tp_uses_proxy(*args, **kwargs):
        return imaging.tp_uses_proxy(*args, **kwargs)

    def _tp_geometry_cache_is_current(self, source=None, target_width=None, target_height=None):
        source = source if source is not None else self.transform_source
        if source is None or source.isNull() or not self.transform_points:
            return False
        target_width = int(
            target_width if target_width is not None else self.active_layer.image.width()
        )
        target_height = int(
            target_height if target_height is not None else self.active_layer.image.height()
        )
        bbox, _raw_selection = self._tp_transform_bbox(target_width, target_height)
        if bbox.isEmpty():
            return self._tp_geometry_cache_key is not None
        key = self._tp_geometry_key(source, target_width, target_height, bbox)
        return (
            self._tp_geometry_cache_key == key
            and self._tp_geometry_cache_fill_overlay is not None
        )

    def init_mesh(self):
        w,h=workspace_size(); self.mesh_original=self.active_layer.image.copy(); self.mesh_points=[]
        for gy in range(self.mesh_grid):
            for gx in range(self.mesh_grid): self.mesh_points.append(QPointF(gx*w/(self.mesh_grid-1),gy*h/(self.mesh_grid-1)))

    def nearest_mesh(self,p):
        if not self.mesh_points:return -1
        d=[(q.x()-p.x())**2+(q.y()-p.y())**2 for q in self.mesh_points]; i=int(np.argmin(d)); return i if d[i]<(40/max(self.zoom,.01))**2 else -1

    def commit_mesh(self):
        if self.mesh_original is None or not self.mesh_points:return
        self.apply_mesh_release()

    def apply_mesh_release(self):
        if self.mesh_original is None:return
        img=self.mesh_original.convertToFormat(QImage.Format.Format_RGBA8888); w,h=img.width(),img.height()
        ptr=img.bits(); arr=np.frombuffer(ptr,dtype=np.uint8,count=w*h*4).reshape((h,w,4)).copy()
        yy,xx=np.mgrid[0:h,0:w].astype(np.float32); dx=np.zeros_like(xx);dy=np.zeros_like(yy);ws=np.zeros_like(xx)
        originals=[]
        for gy in range(self.mesh_grid):
            for gx in range(self.mesh_grid): originals.append(QPointF(gx*w/(self.mesh_grid-1),gy*h/(self.mesh_grid-1)))
        radius=max(w,h)/2.0
        for o,n in zip(originals,self.mesh_points):
            dist2=(xx-o.x())**2+(yy-o.y())**2; weight=np.exp(-dist2/(2*(radius/2.5)**2)); dx+=(n.x()-o.x())*weight;dy+=(n.y()-o.y())*weight;ws+=weight
        sx=np.clip(np.rint(xx-dx/np.maximum(ws,.001)),0,w-1).astype(np.int32); sy=np.clip(np.rint(yy-dy/np.maximum(ws,.001)),0,h-1).astype(np.int32)
        out=arr[sy,sx]; q=QImage(out.data,w,h,out.strides[0],QImage.Format.Format_RGBA8888).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self.active_layer.image=q; self.active_layer.has_content=True; self.mesh_original=None; self.mesh_points=[]; self.cellChanged.emit(self.current_frame,self.active_layer_index); self.update()

    def cancel_mesh(self):
        self.mesh_original=None; self.mesh_points=[]; self.mesh_active=-1; self.update()

    def _regular_mesh_reference_points(self, rect, cols, rows):
        cols = max(2, int(cols))
        rows = max(2, int(rows))
        source = getattr(self, "transform_source", None)

        if source is not None and not source.isNull():
            pixel_width = max(0.0, float(source.width() - 1))
            pixel_height = max(0.0, float(source.height() - 1))
        else:
            pixel_width = max(0.0, float(rect.width()) - 1.0)
            pixel_height = max(0.0, float(rect.height()) - 1.0)

        return [
            QPointF(
                float(rect.left())
                + pixel_width * gx / float(cols - 1),
                float(rect.top())
                + pixel_height * gy / float(rows - 1),
            )
            for gy in range(rows)
            for gx in range(cols)
        ]

    def set_transform_mesh_grid(self, cols, rows=None):
        cols = max(2, min(12, int(cols)))
        rows = max(2, min(12, int(rows if rows is not None else cols)))
        self.transform_mesh_cols = cols
        self.transform_mesh_rows = rows
        self.transform_mesh_grid = cols
        if not self.transform_active or self.transform_mode != "mesh":
            return

        old_points = list(self.transform_points)
        old_cols = max(2, int(getattr(self, "_active_mesh_cols", 0) or 0))
        old_rows = max(2, int(getattr(self, "_active_mesh_rows", 0) or 0))
        if old_cols * old_rows != len(old_points):
            old_cols = max(2, int(round(math.sqrt(len(old_points))))) if old_points else 0
            old_rows = old_cols if old_cols and old_cols * old_cols == len(old_points) else 0

        if old_cols < 2 or old_rows < 2 or old_cols * old_rows != len(old_points):
            self.transform_points = self._regular_grid_points(
                self.transform_source_rect, cols, rows
            )
        else:
            def sample(u, v):
                x = max(0.0, min(old_cols - 1.0, u * (old_cols - 1)))
                y = max(0.0, min(old_rows - 1.0, v * (old_rows - 1)))
                x0, y0 = int(math.floor(x)), int(math.floor(y))
                x1, y1 = min(old_cols - 1, x0 + 1), min(old_rows - 1, y0 + 1)
                tx, ty = x - x0, y - y0
                p00 = old_points[y0 * old_cols + x0]
                p10 = old_points[y0 * old_cols + x1]
                p01 = old_points[y1 * old_cols + x0]
                p11 = old_points[y1 * old_cols + x1]
                return QPointF(
                    (1 - ty) * ((1 - tx) * p00.x() + tx * p10.x())
                    + ty * ((1 - tx) * p01.x() + tx * p11.x()),
                    (1 - ty) * ((1 - tx) * p00.y() + tx * p10.y())
                    + ty * ((1 - tx) * p01.y() + tx * p11.y()),
                )

            self.transform_points = [
                sample(gx / (cols - 1), gy / (rows - 1))
                for gy in range(rows)
                for gx in range(cols)
            ]

        self.transform_mesh_reference_points = (
            self._regular_mesh_reference_points(
                self.transform_source_rect,
                cols,
                rows,
            )
        )
        self._active_mesh_cols = cols
        self._active_mesh_rows = rows
        self.transform_handle = -1
        self._invalidate_tp_preview_cache()
        if self.transform_quality_active:
            self.request_quality_preview_counter(
                "メッシュ格子のプレビューを生成しています"
            )
        self.update()

    @staticmethod
    def _mesh_catmull_scalar(p0, p1, p2, p3, t):
        return geometry.mesh_catmull_scalar(p0, p1, p2, p3, t)

    def _mesh_reference_grid(self, cols, rows):
        reference = list(
            getattr(
                self,
                "transform_mesh_reference_points",
                [],
            )
        )
        if len(reference) == cols * rows:
            return [QPointF(point) for point in reference]

        rect = self.transform_source_rect
        if rect is None:
            return []
        return self._regular_mesh_reference_points(
            QRectF(rect),
            cols,
            rows,
        )

    def _mesh_curve_point(self, points, cols, rows, grid_x, grid_y):
        """基準格子からの変位だけを滑らかに補間する。"""
        if not points or cols < 2 or rows < 2:
            return QPointF()

        reference = self._mesh_reference_grid(cols, rows)
        if len(reference) != cols * rows:
            return QPointF()

        grid_x = max(
            0.0,
            min(float(cols - 1), float(grid_x)),
        )
        grid_y = max(
            0.0,
            min(float(rows - 1), float(grid_y)),
        )
        cell_x = min(cols - 2, int(math.floor(grid_x)))
        cell_y = min(rows - 2, int(math.floor(grid_y)))
        local_x = grid_x - cell_x
        local_y = grid_y - cell_y

        def clamped_index(row, col):
            row = max(0, min(rows - 1, int(row)))
            col = max(0, min(cols - 1, int(col)))
            return row * cols + col

        def displacement_at(row, col):
            index = clamped_index(row, col)
            return QPointF(
                points[index].x() - reference[index].x(),
                points[index].y() - reference[index].y(),
            )

        horizontal_displacements = []
        for row in range(cell_y - 1, cell_y + 3):
            p0 = displacement_at(row, cell_x - 1)
            p1 = displacement_at(row, cell_x)
            p2 = displacement_at(row, cell_x + 1)
            p3 = displacement_at(row, cell_x + 2)
            horizontal_displacements.append(QPointF(
                self._mesh_catmull_scalar(
                    p0.x(),
                    p1.x(),
                    p2.x(),
                    p3.x(),
                    local_x,
                ),
                self._mesh_catmull_scalar(
                    p0.y(),
                    p1.y(),
                    p2.y(),
                    p3.y(),
                    local_x,
                ),
            ))

        displacement = QPointF(
            self._mesh_catmull_scalar(
                horizontal_displacements[0].x(),
                horizontal_displacements[1].x(),
                horizontal_displacements[2].x(),
                horizontal_displacements[3].x(),
                local_y,
            ),
            self._mesh_catmull_scalar(
                horizontal_displacements[0].y(),
                horizontal_displacements[1].y(),
                horizontal_displacements[2].y(),
                horizontal_displacements[3].y(),
                local_y,
            ),
        )

        # 基準格子上の位置は双線形補間する。
        i00 = cell_y * cols + cell_x
        i10 = cell_y * cols + cell_x + 1
        i01 = (cell_y + 1) * cols + cell_x
        i11 = (cell_y + 1) * cols + cell_x + 1
        r00 = reference[i00]
        r10 = reference[i10]
        r01 = reference[i01]
        r11 = reference[i11]

        base = QPointF(
            (1.0 - local_y)
            * (
                (1.0 - local_x) * r00.x()
                + local_x * r10.x()
            )
            + local_y
            * (
                (1.0 - local_x) * r01.x()
                + local_x * r11.x()
            ),
            (1.0 - local_y)
            * (
                (1.0 - local_x) * r00.y()
                + local_x * r10.y()
            )
            + local_y
            * (
                (1.0 - local_x) * r01.y()
                + local_x * r11.y()
            ),
        )

        return QPointF(
            base.x() + displacement.x(),
            base.y() + displacement.y(),
        )

    def _curved_mesh_points(
        self, points, cols, rows, subdivisions=8
    ):
        """基準格子に滑らかな変位を加えた高密度メッシュを生成する。"""
        subdivisions = max(2, int(subdivisions))
        dense_cols = (cols - 1) * subdivisions + 1
        dense_rows = (rows - 1) * subdivisions + 1
        dense = []
        for dense_y in range(dense_rows):
            grid_y = dense_y / float(subdivisions)
            for dense_x in range(dense_cols):
                grid_x = dense_x / float(subdivisions)
                dense.append(
                    self._mesh_curve_point(
                        points,
                        cols,
                        rows,
                        grid_x,
                        grid_y,
                    )
                )
        return dense, dense_cols, dense_rows

    def _mesh_preview_image(
        self, source_image=None, target_width=None, target_height=None, smooth=False
    ):
        # 変形では補間色を生成しない。呼び出し側の指定に関係なく最近傍に固定する。
        smooth = False
        source_image = source_image if source_image is not None else self.transform_source
        if source_image is None:
            return None
        source = source_image.convertToFormat(QImage.Format.Format_RGBA8888)
        sw, sh = source.width(), source.height()
        cw = int(target_width if target_width is not None else self.active_layer.image.width())
        ch = int(target_height if target_height is not None else self.active_layer.image.height())
        if sw <= 0 or sh <= 0 or cw <= 0 or ch <= 0:
            return None
        sp = source.bits()
        try:
            sp.setsize(source.sizeInBytes())
        except AttributeError:
            pass
        source_rows = np.frombuffer(sp, dtype=np.uint8).reshape(
            (sh, source.bytesPerLine())
        )
        source_pixels = source_rows[:, :sw * 4].reshape((sh, sw, 4)).copy()
        output = np.zeros((ch, cw, 4), dtype=np.uint8)
        cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
        rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
        if len(self.transform_points) != cols * rows:
            return None

        def raster_triangle(target_triangle, source_triangle):
            tx = np.array([p.x() for p in target_triangle], dtype=np.float64)
            ty = np.array([p.y() for p in target_triangle], dtype=np.float64)
            min_x = max(0, int(math.floor(float(tx.min()))))
            max_x = min(cw - 1, int(math.ceil(float(tx.max()))))
            min_y = max(0, int(math.floor(float(ty.min()))))
            max_y = min(ch - 1, int(math.ceil(float(ty.max()))))
            if max_x < min_x or max_y < min_y:
                return
            denominator = (
                (ty[1] - ty[2]) * (tx[0] - tx[2])
                + (tx[2] - tx[1]) * (ty[0] - ty[2])
            )
            if abs(denominator) < 1e-8:
                return
            yy, xx = np.mgrid[min_y:max_y + 1, min_x:max_x + 1]
            w0 = (
                (ty[1] - ty[2]) * (xx - tx[2])
                + (tx[2] - tx[1]) * (yy - ty[2])
            ) / denominator
            w1 = (
                (ty[2] - ty[0]) * (xx - tx[2])
                + (tx[0] - tx[2]) * (yy - ty[2])
            ) / denominator
            w2 = 1.0 - w0 - w1
            inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not np.any(inside):
                return
            sx_float = (
                w0 * source_triangle[0][0]
                + w1 * source_triangle[1][0]
                + w2 * source_triangle[2][0]
            )
            sy_float = (
                w0 * source_triangle[0][1]
                + w1 * source_triangle[1][1]
                + w2 * source_triangle[2][1]
            )
            region = output[min_y:max_y + 1, min_x:max_x + 1]
            if smooth:
                sx_float = np.clip(sx_float, 0.0, sw - 1.0)
                sy_float = np.clip(sy_float, 0.0, sh - 1.0)
                x0 = np.floor(sx_float).astype(np.int32)
                y0 = np.floor(sy_float).astype(np.int32)
                x1 = np.minimum(sw - 1, x0 + 1)
                y1 = np.minimum(sh - 1, y0 + 1)
                tx = (sx_float - x0)[..., None]
                ty = (sy_float - y0)[..., None]
                sampled = (
                    source_pixels[y0, x0].astype(np.float32) * (1.0 - tx) * (1.0 - ty)
                    + source_pixels[y0, x1].astype(np.float32) * tx * (1.0 - ty)
                    + source_pixels[y1, x0].astype(np.float32) * (1.0 - tx) * ty
                    + source_pixels[y1, x1].astype(np.float32) * tx * ty
                )
                region[inside] = np.clip(
                    sampled[inside] + 0.5, 0, 255
                ).astype(np.uint8)
            else:
                sx = np.clip(np.rint(sx_float).astype(np.int32), 0, sw - 1)
                sy = np.clip(np.rint(sy_float).astype(np.int32), 0, sh - 1)
                region[inside] = source_pixels[sy[inside], sx[inside]]

        curved, dense_cols, dense_rows = self._curved_mesh_points(
            self.transform_points, cols, rows, subdivisions=8
        )
        for dense_y in range(dense_rows - 1):
            source_y0 = (
                (sh - 1) * dense_y / max(1, dense_rows - 1)
            )
            source_y1 = (
                (sh - 1) * (dense_y + 1) / max(1, dense_rows - 1)
            )
            for dense_x in range(dense_cols - 1):
                source_x0 = (
                    (sw - 1) * dense_x / max(1, dense_cols - 1)
                )
                source_x1 = (
                    (sw - 1) * (dense_x + 1)
                    / max(1, dense_cols - 1)
                )
                index = dense_y * dense_cols + dense_x
                p00 = curved[index]
                p10 = curved[index + 1]
                p01 = curved[index + dense_cols]
                p11 = curved[index + dense_cols + 1]
                raster_triangle(
                    (p00, p10, p11),
                    (
                        (source_x0, source_y0),
                        (source_x1, source_y0),
                        (source_x1, source_y1),
                    ),
                )
                raster_triangle(
                    (p00, p11, p01),
                    (
                        (source_x0, source_y0),
                        (source_x1, source_y1),
                        (source_x0, source_y1),
                    ),
                )
        return QImage(
            output.data, cw, ch, output.strides[0], QImage.Format.Format_RGBA8888
        ).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    @staticmethod
    def _tp_transparent_to_white(*args, **kwargs):
        return imaging.tp_transparent_to_white(*args, **kwargs)

    @staticmethod
    def _tp_white_to_transparent(*args, **kwargs):
        return imaging.tp_white_to_transparent(*args, **kwargs)

    @staticmethod
    def _tp_prepare_palette_image(*args, **kwargs):
        return imaging.tp_prepare_palette_image(*args, **kwargs)

    @staticmethod
    def _tp_make_color_masks(*args, **kwargs):
        return imaging.tp_make_color_masks(*args, **kwargs)

    def _prepare_tp_transform_masks(self, source_image=None):
        """Build the same exact-color masks used by TP Mask Transform v0.7."""
        self._clear_tp_transform_masks(clear_proxy=False)
        source = source_image if source_image is not None else self.transform_source
        if (
            PILImage is None or PILImageFilter is None
            or source is None or source.isNull()
        ):
            return False

        source_rgba = self._qimage_to_pil_rgba(source).convert("RGBA")
        prepared = self._tp_transparent_to_white(source_rgba)
        exact_color_counts = prepared.getcolors(maxcolors=257)
        if exact_color_counts is None:
            # This intentionally mirrors v0.7: color-rich images are reduced
            # without dithering.  The large-image proxy uses fewer colors to
            # keep its temporary mask memory bounded.
            prepared, palette_rgba = self._tp_prepare_palette_image(
                source_rgba,
                (
                    TP_MASK_PROXY_MAX_COLORS
                    if self._tp_proxy_rendering
                    else 64
                ),
            )
        else:
            palette_rgba = sorted(
                tuple(int(channel) for channel in color)
                for _count, color in exact_color_counts
                if color[3] > 0
            )

        prepared_array = np.asarray(prepared.convert("RGBA"), dtype=np.uint8)
        line_masks = []
        for line_color in self.transform_tp_line_colors:
            red, green, blue = line_color
            selected = (
                (prepared_array[:, :, 3] > 0)
                & np.all(
                    prepared_array[:, :, :3]
                    == np.asarray((red, green, blue), dtype=np.uint8),
                    axis=2,
                )
            )
            candidate = PILImage.fromarray(
                (selected * 255).astype(np.uint8), "L"
            )
            if candidate.getbbox() is not None:
                line_masks.append((tuple(line_color), candidate))
                palette_rgba = [
                    color for color in palette_rgba
                    if tuple(color[:3]) != tuple(line_color)
                ]

        self.transform_tp_palette = list(palette_rgba)
        self.transform_tp_masks = self._tp_make_color_masks(
            prepared, self.transform_tp_palette
        )
        self.transform_tp_line_masks = line_masks
        self.transform_tp_prepared_preview = self._pil_rgba_to_qimage(
            self._tp_white_to_transparent(prepared)
        )
        self._tp_mask_source_key = self._tp_mask_key(source)
        self._invalidate_tp_preview_cache()
        return bool(self.transform_tp_masks or self.transform_tp_line_masks)

    def _tp_is_enlarging(self):
        source = self.transform_source
        if source is None or source.isNull() or not self.transform_points:
            return False
        source_width = max(1.0, float(source.width()))
        source_height = max(1.0, float(source.height()))

        if self.transform_mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
            if len(self.transform_points) != cols * rows:
                return False
            horizontal = []
            vertical = []
            for row in range(rows):
                for col in range(cols - 1):
                    p0 = self.transform_points[row * cols + col]
                    p1 = self.transform_points[row * cols + col + 1]
                    horizontal.append(math.hypot(p1.x()-p0.x(), p1.y()-p0.y()))
            for row in range(rows - 1):
                for col in range(cols):
                    p0 = self.transform_points[row * cols + col]
                    p1 = self.transform_points[(row + 1) * cols + col]
                    vertical.append(math.hypot(p1.x()-p0.x(), p1.y()-p0.y()))
            expected_x = source_width / max(1, cols - 1)
            expected_y = source_height / max(1, rows - 1)
            scale_x = (sum(horizontal) / len(horizontal)) / expected_x if horizontal else 1.0
            scale_y = (sum(vertical) / len(vertical)) / expected_y if vertical else 1.0
            return scale_x > 1.0001 or scale_y > 1.0001

        if len(self.transform_points) != 4:
            return False
        p0, p1, p2, p3 = self.transform_points[:4]
        scale_x = (
            math.hypot(p1.x()-p0.x(), p1.y()-p0.y())
            + math.hypot(p2.x()-p3.x(), p2.y()-p3.y())
        ) / (2.0 * source_width)
        scale_y = (
            math.hypot(p3.x()-p0.x(), p3.y()-p0.y())
            + math.hypot(p2.x()-p1.x(), p2.y()-p1.y())
        ) / (2.0 * source_height)
        return scale_x > 1.0001 or scale_y > 1.0001

    def _tp_transform_bbox(self, target_width, target_height):
        outline = self.transform_outer_polygon()
        if outline.isEmpty():
            return QRectF(), QRectF()
        raw_bounds = outline.boundingRect()
        canvas = QRectF(0, 0, target_width, target_height)
        expanded = raw_bounds.adjusted(-3, -3, 3, 3).intersected(canvas)
        if expanded.isEmpty():
            return QRectF(), raw_bounds.intersected(canvas)
        left = max(0, int(math.floor(expanded.left())))
        top = max(0, int(math.floor(expanded.top())))
        right = min(target_width, int(math.ceil(expanded.right())))
        bottom = min(target_height, int(math.ceil(expanded.bottom())))
        if right <= left or bottom <= top:
            return QRectF(), raw_bounds.intersected(canvas)
        return (
            QRectF(left, top, right-left, bottom-top),
            raw_bounds.intersected(canvas),
        )

    def _tp_mesh_render_mask(self, source_array, bbox, render_scale, smooth):
        # クオリティ変形も画素補間は行わず、色境界を最近傍で保持する。
        smooth = False
        source_array = np.asarray(source_array, dtype=np.uint8)
        source_height, source_width = source_array.shape
        out_width = max(1, int(math.ceil(bbox.width() * render_scale)))
        out_height = max(1, int(math.ceil(bbox.height() * render_scale)))
        output = np.zeros((out_height, out_width), dtype=np.uint8)
        cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
        rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
        if len(self.transform_points) != cols * rows:
            return output

        points = [
            QPointF(
                (point.x() - bbox.left()) * render_scale,
                (point.y() - bbox.top()) * render_scale,
            )
            for point in self.transform_points
        ]

        def raster_triangle(target_triangle, source_triangle):
            target_x = np.array(
                [point.x() for point in target_triangle], dtype=np.float64
            )
            target_y = np.array(
                [point.y() for point in target_triangle], dtype=np.float64
            )
            min_x = max(0, int(math.floor(float(target_x.min()))))
            max_x = min(out_width - 1, int(math.ceil(float(target_x.max()))))
            min_y = max(0, int(math.floor(float(target_y.min()))))
            max_y = min(out_height - 1, int(math.ceil(float(target_y.max()))))
            if max_x < min_x or max_y < min_y:
                return
            denominator = (
                (target_y[1] - target_y[2]) * (target_x[0] - target_x[2])
                + (target_x[2] - target_x[1]) * (target_y[0] - target_y[2])
            )
            if abs(denominator) < 1e-8:
                return
            yy, xx = np.mgrid[min_y:max_y + 1, min_x:max_x + 1]
            weight0 = (
                (target_y[1] - target_y[2]) * (xx - target_x[2])
                + (target_x[2] - target_x[1]) * (yy - target_y[2])
            ) / denominator
            weight1 = (
                (target_y[2] - target_y[0]) * (xx - target_x[2])
                + (target_x[0] - target_x[2]) * (yy - target_y[2])
            ) / denominator
            weight2 = 1.0 - weight0 - weight1
            inside = (
                (weight0 >= -1e-6)
                & (weight1 >= -1e-6)
                & (weight2 >= -1e-6)
            )
            if not np.any(inside):
                return
            source_x = (
                weight0 * source_triangle[0][0]
                + weight1 * source_triangle[1][0]
                + weight2 * source_triangle[2][0]
            )
            source_y = (
                weight0 * source_triangle[0][1]
                + weight1 * source_triangle[1][1]
                + weight2 * source_triangle[2][1]
            )
            region = output[min_y:max_y + 1, min_x:max_x + 1]
            if smooth:
                source_x = np.clip(source_x, 0.0, source_width - 1.0)
                source_y = np.clip(source_y, 0.0, source_height - 1.0)
                x0 = np.floor(source_x).astype(np.int32)
                y0 = np.floor(source_y).astype(np.int32)
                x1 = np.minimum(source_width - 1, x0 + 1)
                y1 = np.minimum(source_height - 1, y0 + 1)
                fraction_x = source_x - x0
                fraction_y = source_y - y0
                sampled = (
                    source_array[y0, x0].astype(np.float32)
                    * (1.0-fraction_x) * (1.0-fraction_y)
                    + source_array[y0, x1].astype(np.float32)
                    * fraction_x * (1.0-fraction_y)
                    + source_array[y1, x0].astype(np.float32)
                    * (1.0-fraction_x) * fraction_y
                    + source_array[y1, x1].astype(np.float32)
                    * fraction_x * fraction_y
                )
                region[inside] = np.clip(
                    sampled[inside] + 0.5, 0, 255
                ).astype(np.uint8)
            else:
                sample_x = np.clip(
                    np.rint(source_x).astype(np.int32), 0, source_width - 1
                )
                sample_y = np.clip(
                    np.rint(source_y).astype(np.int32), 0, source_height - 1
                )
                region[inside] = source_array[
                    sample_y[inside], sample_x[inside]
                ]

        curved, dense_cols, dense_rows = self._curved_mesh_points(
            points, cols, rows, subdivisions=8
        )
        for dense_y in range(dense_rows - 1):
            source_y0 = (
                (source_height - 1)
                * dense_y / max(1, dense_rows - 1)
            )
            source_y1 = (
                (source_height - 1)
                * (dense_y + 1) / max(1, dense_rows - 1)
            )
            for dense_x in range(dense_cols - 1):
                source_x0 = (
                    (source_width - 1)
                    * dense_x / max(1, dense_cols - 1)
                )
                source_x1 = (
                    (source_width - 1)
                    * (dense_x + 1) / max(1, dense_cols - 1)
                )
                index = dense_y * dense_cols + dense_x
                p00 = curved[index]
                p10 = curved[index + 1]
                p01 = curved[index + dense_cols]
                p11 = curved[index + dense_cols + 1]
                raster_triangle(
                    (p00, p10, p11),
                    (
                        (source_x0, source_y0),
                        (source_x1, source_y0),
                        (source_x1, source_y1),
                    ),
                )
                raster_triangle(
                    (p00, p11, p01),
                    (
                        (source_x0, source_y0),
                        (source_x1, source_y1),
                        (source_x0, source_y1),
                    ),
                )
        return output

    def _tp_project_mask_to_bbox(self, mask, bbox, render_scale=1):
        out_width = max(1, int(math.ceil(bbox.width() * render_scale)))
        out_height = max(1, int(math.ceil(bbox.height() * render_scale)))
        if self.transform_mode == "mesh":
            source_array = np.asarray(mask.convert("L"), dtype=np.uint8)
            rendered = self._tp_mesh_render_mask(
                source_array, bbox, render_scale, smooth=False
            )
            return PILImage.fromarray(rendered, "L")

        output = QImage(
            out_width, out_height, QImage.Format.Format_Grayscale8
        )
        output.fill(0)
        source_width, source_height = mask.size
        source_quad = [
            QPointF(0, 0), QPointF(source_width, 0),
            QPointF(source_width, source_height), QPointF(0, source_height),
        ]
        target_quad = [
            QPointF(
                (point.x() - bbox.left()) * render_scale,
                (point.y() - bbox.top()) * render_scale,
            )
            for point in self.transform_points[:4]
        ]
        transform = self._quad_homography(source_quad, target_quad)
        painter = QPainter(output)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setTransform(transform)
        painter.drawImage(QPointF(0, 0), self._pil_l_to_qimage(mask))
        painter.end()
        return PILImage.fromarray(self._qimage_gray_array(output), "L")

    def _tp_render_mask_to_bbox(
        self, mask, bbox, smoothing=0.0, supersample=4
    ):
        """v0.7 mask renderer, extended to free and mesh transformations."""
        if self._tp_proxy_rendering:
            supersample = 1
        out_width = max(1, int(math.ceil(bbox.width())))
        out_height = max(1, int(math.ceil(bbox.height())))
        if self._tp_is_enlarging():
            source = mask.convert("L")
            if smoothing > 0.001:
                source = source.filter(
                    PILImageFilter.GaussianBlur(radius=float(smoothing))
                )
            return self._tp_project_mask_to_bbox(source, bbox, 1)

        high = self._tp_project_mask_to_bbox(mask.convert("L"), bbox, supersample)
        high = high.filter(
            PILImageFilter.GaussianBlur(radius=0.65 * supersample)
        )
        return high.resize(
            (out_width, out_height), PILImage.Resampling.LANCZOS
        )

    def _tp_geometry_key(self, source, target_width, target_height, bbox):
        points = tuple(
            (round(point.x(), 5), round(point.y(), 5))
            for point in self.transform_points
        )
        return (
            int(source.cacheKey()),
            int(target_width), int(target_height),
            self.transform_mode,
            points,
            int(getattr(self, "transform_mesh_cols", 4)),
            int(getattr(self, "transform_mesh_rows", 4)),
            int(bbox.left()), int(bbox.top()),
            int(bbox.width()), int(bbox.height()),
            tuple(tuple(color) for color in self.transform_tp_palette),
            tuple(tuple(color) for color, _mask in self.transform_tp_line_masks),
        )

    def _build_tp_geometry_cache(
        self, source, target_width, target_height, progress_callback=None
    ):
        bbox, _raw_selection = self._tp_transform_bbox(
            target_width, target_height
        )
        if bbox.isEmpty():
            self._tp_geometry_cache_bbox = QRectF()
            self._tp_geometry_cache_fill_overlay = PILImage.new(
                "RGBA", (1, 1), (0, 0, 0, 0)
            )
            self._tp_geometry_cache_line_soft = []
            return bbox

        cache_key = self._tp_geometry_key(
            source, target_width, target_height, bbox
        )
        if (
            self._tp_geometry_cache_key == cache_key
            and self._tp_geometry_cache_fill_overlay is not None
        ):
            return QRectF(self._tp_geometry_cache_bbox)

        total = max(
            1,
            len(self.transform_tp_masks)
            + len(self.transform_tp_line_masks)
            + 2,
        )
        completed = 0
        if progress_callback is not None:
            progress_callback(
                completed, total, "色マスクの変形を準備しています"
            )

        width = max(1, int(math.ceil(bbox.width())))
        height = max(1, int(math.ceil(bbox.height())))
        best_value = np.zeros((height, width), dtype=np.uint8)
        best_rgba = np.zeros((height, width, 4), dtype=np.uint8)

        # Every exact color, including the temporary white background, competes
        # for each output pixel.  This is the defining v0.7 TP_mask behavior.
        fill_count = len(self.transform_tp_masks)
        for mask_index, (color, mask) in enumerate(zip(
            self.transform_tp_palette, self.transform_tp_masks
        ), 1):
            rendered = self._tp_render_mask_to_bbox(
                mask, bbox, self.transform_tp_fill_smoothing
            )
            values = np.asarray(rendered, dtype=np.uint8)
            replace = values > best_value
            if np.any(replace):
                best_value[replace] = values[replace]
                best_rgba[replace] = np.asarray(color, dtype=np.uint8)
            completed += 1
            if progress_callback is not None:
                progress_callback(
                    completed,
                    total,
                    f"色マスクを変形しています（{mask_index} / {fill_count}）",
                )

        output = np.zeros((height, width, 4), dtype=np.uint8)
        visible = best_value > 0
        if np.any(visible):
            output[visible, :3] = best_rgba[visible, :3]
            output[visible, 3] = 255
        white = visible & np.all(output[:, :, :3] == 255, axis=2)
        output[white, 3] = 0
        completed += 1
        if progress_callback is not None:
            progress_callback(
                completed, total, "色マスクを再合成しています"
            )

        line_soft = []
        line_count = len(self.transform_tp_line_masks)
        for line_index, (line_color, line_mask) in enumerate(
            self.transform_tp_line_masks, 1
        ):
            line_soft.append((
                tuple(line_color),
                self._tp_render_mask_to_bbox(
                    line_mask, bbox, self.transform_tp_line_smoothing
                ),
            ))
            completed += 1
            if progress_callback is not None:
                progress_callback(
                    completed,
                    total,
                    f"実線マスクを変形しています（{line_index} / {line_count}）",
                )

        self._tp_geometry_cache_key = cache_key
        self._tp_geometry_cache_bbox = QRectF(bbox)
        self._tp_geometry_cache_fill_overlay = PILImage.fromarray(
            output, "RGBA"
        )
        self._tp_geometry_cache_line_soft = line_soft
        completed += 1
        if progress_callback is not None:
            progress_callback(
                min(completed, total), total, "プレビューを仕上げています"
            )
        return bbox

    def _tp_mask_preview_image(
        self, source_image=None, target_width=None, target_height=None,
        progress_callback=None
    ):
        source = (
            source_image if source_image is not None else self.transform_source
        )
        if source is None or source.isNull():
            return None, QRectF()
        target_width = int(
            target_width
            if target_width is not None
            else self.active_layer.image.width()
        )
        target_height = int(
            target_height
            if target_height is not None
            else self.active_layer.image.height()
        )
        canvas_rect = QRectF(0, 0, target_width, target_height)

        expected_mask_key = self._tp_mask_key(source)
        if (
            self._tp_mask_source_key != expected_mask_key
            or (
                not self.transform_tp_masks
                and not self.transform_tp_line_masks
            )
        ):
            if not self._prepare_tp_transform_masks(source):
                return self._project_transform_source(
                    source, target_width, target_height, smooth=False
                )

        threshold = max(
            1, min(254, int(getattr(self, "transform_line_threshold", 96)))
        )
        bbox, raw_selection = self._tp_transform_bbox(
            target_width, target_height
        )
        point_key = tuple(
            (round(point.x(), 5), round(point.y(), 5))
            for point in self.transform_points
        )
        preview_key = (
            int(source.cacheKey()), target_width, target_height,
            self.transform_mode, point_key,
            int(getattr(self, "transform_mesh_cols", 4)),
            int(getattr(self, "transform_mesh_rows", 4)),
            threshold,
            tuple(self.transform_tp_line_colors),
        )
        if (
            source is self.transform_source
            and self._tp_preview_cache_key == preview_key
            and self._tp_preview_cache_image is not None
        ):
            return self._tp_preview_cache_image, canvas_rect

        bbox = self._build_tp_geometry_cache(
            source, target_width, target_height, progress_callback
        )
        full = QImage(
            target_width, target_height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        full.fill(Qt.GlobalColor.transparent)
        if not bbox.isEmpty():
            width = max(1, int(math.ceil(bbox.width())))
            height = max(1, int(math.ceil(bbox.height())))
            overlay = self._tp_geometry_cache_fill_overlay.copy()
            for line_color, line_soft in self._tp_geometry_cache_line_soft:
                binary = line_soft.point(
                    lambda value, limit=threshold:
                    255 if value >= limit else 0
                )
                red, green, blue = line_color
                line_fill = PILImage.new(
                    "RGBA", (width, height), (red, green, blue, 255)
                )
                overlay.paste(line_fill, (0, 0), binary)
            painter = QPainter(full)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )
            painter.drawImage(
                QPointF(math.floor(bbox.left()), math.floor(bbox.top())),
                self._pil_rgba_to_qimage(overlay),
            )
            painter.end()

        if source is self.transform_source:
            self._tp_preview_cache_key = preview_key
            self._tp_preview_cache_image = full
        return full, canvas_rect
