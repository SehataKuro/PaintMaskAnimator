"""Main-line repaint, dust removal and mask-contour helpers for MainWindow.

Split out of ``main_window.py`` as a mixin. These methods operate on the drawn
line art rather than on documents or the UI shell: contour extraction from binary
masks, main/sub line swapping, silhouette display, wire/mesh transform entry
points, same-image colour replacement registration, the main-line repaint pass,
and the surrounding-dust fill. The modal progress-counter helpers used by these
long-running passes live here too. They run against a live ``MainWindow``.
"""
from .common import *  # noqa: F401,F403
from ._main_window_members import MainWindowMembers
from . import despeckle, frame_scope, imaging
from .models import Layer
from .utils import blank_image
from .errors import OPERATION_ERRORS
from .logging_setup import get_logger

log = get_logger(__name__)

_OPERATION_ERRORS = OPERATION_ERRORS


class LineOpsMixin(MainWindowMembers):
    @staticmethod
    def _image_contains_rgb(image, rgb):
        if image is None or image.isNull():
            return False
        pixels = imaging.qimage_rgba_array(image)
        height, width = pixels.shape[:2]
        if width <= 0 or height <= 0:
            return False
        target = np.array(
            [int(rgb[0]), int(rgb[1]), int(rgb[2])],
            dtype=np.uint8,
        )
        return bool(
            np.any(
                (pixels[:, :, 3] > 0)
                & np.all(pixels[:, :, :3] == target, axis=2)
            )
        )

    @staticmethod
    def _mask_contours(mask):
        """2値マスクの外周と穴の輪郭を画素境界上のポリゴンとして返す。"""
        mask = np.asarray(mask, dtype=bool)
        if mask.ndim != 2 or not np.any(mask):
            return []

        top = mask.copy()
        top[1:, :] &= ~mask[:-1, :]
        right = mask.copy()
        right[:, :-1] &= ~mask[:, 1:]
        bottom = mask.copy()
        bottom[:-1, :] &= ~mask[1:, :]
        left = mask.copy()
        left[:, 1:] &= ~mask[:, :-1]
        edges = set()
        for y, x in zip(*np.nonzero(top)):
            edges.add(((int(x), int(y)), (int(x) + 1, int(y))))
        for y, x in zip(*np.nonzero(right)):
            edges.add(((int(x) + 1, int(y)), (int(x) + 1, int(y) + 1)))
        for y, x in zip(*np.nonzero(bottom)):
            edges.add(((int(x) + 1, int(y) + 1), (int(x), int(y) + 1)))
        for y, x in zip(*np.nonzero(left)):
            edges.add(((int(x), int(y) + 1), (int(x), int(y))))

        outgoing = {}
        for start, end in edges:
            outgoing.setdefault(start, []).append(end)

        direction_index = {
            (1, 0): 0,
            (0, 1): 1,
            (-1, 0): 2,
            (0, -1): 3,
        }
        unused = set(edges)
        contours = []
        while unused:
            start_edge = min(unused)
            start, current = start_edge
            unused.remove(start_edge)
            contour = [start, current]
            previous = start

            while current != start:
                candidates = [
                    end for end in outgoing.get(current, ())
                    if (current, end) in unused
                ]
                if not candidates:
                    contour = []
                    break
                previous_direction = direction_index[
                    (current[0] - previous[0], current[1] - previous[1])
                ]

                def turn_priority(
                    end,
                    current=current,
                    previous_direction=previous_direction,
                ):
                    next_direction = direction_index[
                        (end[0] - current[0], end[1] - current[1])
                    ]
                    turn = (next_direction - previous_direction) % 4
                    return ({1: 0, 0: 1, 3: 2, 2: 3}[turn], end)

                next_point = min(candidates, key=turn_priority)
                unused.remove((current, next_point))
                previous, current = current, next_point
                contour.append(current)

            if len(contour) >= 4 and contour[-1] == contour[0]:
                contours.append(contour[:-1])

        simplified_contours = []
        for contour in contours:
            simplified = []
            for index, point in enumerate(contour):
                previous = contour[index - 1]
                following = contour[(index + 1) % len(contour)]
                if (
                    (previous[0] == point[0] == following[0])
                    or (previous[1] == point[1] == following[1])
                ):
                    continue
                simplified.append(QPointF(point[0], point[1]))
            if len(simplified) >= 3:
                simplified_contours.append(simplified)
        return simplified_contours

    @classmethod
    def _largest_mask_outer_contour(cls, mask):
        contours = cls._mask_contours(mask)
        return cls._largest_contour(contours)

    @staticmethod
    def _largest_contour(contours):
        if not contours:
            return []

        def area(points):
            values = [(point.x(), point.y()) for point in points]
            return abs(0.5 * sum(
                x1 * y2 - x2 * y1
                for (x1, y1), (x2, y2) in zip(
                    values,
                    values[1:] + values[:1],
                )
            ))

        return max(contours, key=area)


    def swap_main_sub(self):
        self.canvas.main_color, self.canvas.sub_color = (
            QColor(self.canvas.sub_color),
            QColor(self.canvas.main_color),
        )
        self.tools.set_colors(
            self.canvas.main_color,
            self.canvas.sub_color,
            self.canvas.color_mode,
            self.canvas.transparent_display_color,
        )
        self.statusBar().showMessage("メインカラーとサブカラーを交換しました。", 1800)

    def reset_main_sub(self):
        self.canvas.main_color = QColor("black")
        self.canvas.sub_color = QColor("white")
        if self.canvas.color_mode not in ("main", "sub"):
            self.canvas.color_mode = "main"
        self.tools.set_colors(
            self.canvas.main_color,
            self.canvas.sub_color,
            self.canvas.color_mode,
            self.canvas.transparent_display_color,
        )
        self.canvas.update()
        self.statusBar().showMessage("メイン色とサブ色を初期化しました。", 1800)

    def toggle_silhouette(self, checked=None):
        if checked is None:
            checked = not self.canvas.silhouette_non_background
        checked = bool(checked)
        self.canvas.silhouette_non_background = checked

        if hasattr(self, "a_silhouette"):
            self.a_silhouette.blockSignals(True)
            self.a_silhouette.setChecked(checked)
            self.a_silhouette.blockSignals(False)

        silhouette_button = self.action_panel.button("silhouette")
        if silhouette_button is not None:
            silhouette_button.blockSignals(True)
            silhouette_button.setChecked(checked)
            silhouette_button.blockSignals(False)

        self.canvas._silhouette_cache.clear()
        self.canvas.update()
        self.statusBar().showMessage(
            "背景以外を黒シルエット表示しています。"
            if checked else
            "黒シルエット表示を解除しました。",
            2200,
        )

    def choose_transform_mesh_grid(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("メッシュ格子数")
        form = QFormLayout(dialog)

        cols_spin = QSpinBox(dialog)
        rows_spin = QSpinBox(dialog)
        for spin in (cols_spin, rows_spin):
            spin.setRange(2, 12)
        cols_spin.setValue(
            max(2, int(getattr(self.canvas, "transform_mesh_cols", 4)))
        )
        rows_spin.setValue(
            max(2, int(getattr(self.canvas, "transform_mesh_rows", 4)))
        )
        form.addRow("横の格子数", cols_spin)
        form.addRow("縦の格子数", rows_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        cols = cols_spin.value()
        rows = rows_spin.value()
        for spin, value in (
            (self.tools.transform_mesh_grid_x, cols),
            (self.tools.transform_mesh_grid_y, rows),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self.canvas.set_transform_mesh_grid(cols, rows)
        if not self.canvas.transform_active:
            self.statusBar().showMessage(
                f"次回のメッシュ変形を 横{cols}×縦{rows} 格子に設定しました。",
                2200,
            )


    def start_wire_transform(self, mode, line_colors_override=None):
        if not self.canvas.selection_polygon:
            if not self.canvas.auto_select_used_area():
                QMessageBox.warning(
                    self,
                    "変形",
                    "使用色がある領域を検出できないため、変形を開始できません。",
                )
                return
            self.statusBar().showMessage(
                "選択範囲がなかったため、使用色がある領域を自動選択しました。",
                2600,
            )
        self.canvas.transform_mesh_cols = self.tools.transform_mesh_grid_x.value()
        self.canvas.transform_mesh_rows = self.tools.transform_mesh_grid_y.value()
        self.canvas.transform_mesh_grid = self.canvas.transform_mesh_cols

        active_line_colors = (
            {
                tuple(int(channel) for channel in rgb[:3])
                for rgb in line_colors_override
            }
            if line_colors_override is not None
            else {
                tuple(int(channel) for channel in rgb[:3])
                for rgb in self.palette.selected_rgbs
            }
        )
        self.canvas.set_transform_line_colors(active_line_colors)
        self.tools.set_transform_line_colors_available(
            bool(active_line_colors)
        )

        quality_active = self.tools.transform_quality.isChecked()
        self.canvas.set_transform_quality(quality_active)
        self.canvas.set_transform_line_threshold(
            self.tools.transform_line_width.value()
        )
        self.canvas.transform_apply_all_frames = (
            self.tools.selection_all_frames.isChecked()
        )
        if not self.canvas.begin_selection_transform(mode):
            self.canvas.transform_apply_all_frames = False
            QMessageBox.warning(
                self, "変形",
                "選択範囲から変形対象を作成できませんでした。"
            )
            return
        self.canvas.setFocus()
        target_note = (
            ("クオリティ方式／" if quality_active else "")
            + (
                "すべてのコマへ適用します。"
                if self.canvas.transform_apply_all_frames
                else "現在のコマへ適用します。"
            )
        )
        line_note = (
            f" 選択中の使用色{len(self.canvas.transform_tp_line_colors)}色を実線として処理します。"
            if quality_active and self.canvas.transform_tp_line_colors else ""
        )
        self.statusBar().showMessage(
            "白い点：変形／枠内：移動／黄色い点：回転。"
            f"Enterで確定、Escでキャンセルできます。{target_note}{line_note}",
            5000,
        )

    @staticmethod
    def _qimage_rgba_array(image):
        return imaging.qimage_rgba_array(image)

    def _replacement_mapping_for_aligned_images(self, source_image, target_image):
        """Return source->target RGB mapping when every aligned pixel is consistent."""
        if (
            source_image.isNull()
            or target_image.isNull()
            or source_image.size() != target_image.size()
        ):
            return None
        source = self._qimage_rgba_array(source_image)
        target = self._qimage_rgba_array(target_image)

        # A palette-swapped copy must occupy exactly the same pixels and retain
        # the same alpha values. Color values may differ.
        if not np.array_equal(source[:, :, 3], target[:, :, 3]):
            return None
        opaque = source[:, :, 3] > 0
        if not np.any(opaque):
            return None

        source_rgb = source[:, :, :3][opaque]
        target_rgb = target[:, :, :3][opaque]
        source_packed = (
            source_rgb[:, 0].astype(np.uint32) << 16
            | source_rgb[:, 1].astype(np.uint32) << 8
            | source_rgb[:, 2].astype(np.uint32)
        )
        target_packed = (
            target_rgb[:, 0].astype(np.uint32) << 16
            | target_rgb[:, 1].astype(np.uint32) << 8
            | target_rgb[:, 2].astype(np.uint32)
        )

        mapping = {}
        for packed in np.unique(source_packed):
            mapped_values = np.unique(target_packed[source_packed == packed])
            if len(mapped_values) != 1:
                return None
            mapped = int(mapped_values[0])
            source_value = int(packed)
            mapping[(
                (source_value >> 16) & 255,
                (source_value >> 8) & 255,
                source_value & 255,
            )] = (
                (mapped >> 16) & 255,
                (mapped >> 8) & 255,
                mapped & 255,
            )
        return mapping

    def register_same_image_replacements(self):
        if not self.canvas.frames or not self.canvas.layers:
            return
        frame_index = self.canvas.current_frame
        source_index = self.canvas.active_layer_index
        source_key = self.canvas.resolve_key_frame(frame_index, source_index)
        if source_key is None:
            QMessageBox.warning(
                self,
                "同一画像を置換色に登録",
                "選択レイヤーの現在位置に画像がありません。",
            )
            return
        source_layer = self.canvas.frames[source_key].layers[source_index]
        if not source_layer.has_content:
            QMessageBox.warning(
                self,
                "同一画像を置換色に登録",
                "選択レイヤーの現在位置に画像がありません。",
            )
            return

        candidates = []
        for layer_index in range(source_index + 1, len(self.canvas.layers)):
            key_frame = self.canvas.resolve_key_frame(frame_index, layer_index)
            if key_frame is None:
                continue
            layer = self.canvas.frames[key_frame].layers[layer_index]
            if layer.has_content:
                candidates.append((layer_index, key_frame, layer))

        if not candidates:
            QMessageBox.warning(
                self,
                "同一画像を置換色に登録",
                "同じタイムライン位置の上側レイヤーに、比較できる画像がありません。",
            )
            return

        for _layer_index, _key_frame, target_layer in candidates:
            mapping = self._replacement_mapping_for_aligned_images(
                source_layer.image,
                target_layer.image,
            )
            if mapping is None:
                continue
            # 置換色列は廃止したため、対応付けはその場で実画像へ適用する。
            applied = self.apply_palette_replacements(
                mapping,
                operation="同一画像から色置換",
            )
            if not applied:
                QMessageBox.warning(
                    self,
                    "同一画像から色置換",
                    "一致する画像は見つかりましたが、置換できる使用色がありません。",
                )
                return
            return

        QMessageBox.warning(
            self,
            "同一画像を置換色に登録",
            "上側レイヤーの画像とピクセルが一致していないため、"
            "置換色には登録できません。\n"
            "画像サイズ、透明度、色領域の形が同じか確認してください。",
        )

    def main_line_repaint(self):
        if not self.canvas.frames:
            return
        answer = QMessageBox.question(
            self,
            "MainLineRepaint",
            "選択した使用色と、それ以外の部分を別レイヤーに分離します。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return

        selected = list(self.palette.selected_rgb_set())
        if not selected:
            QMessageBox.information(
                self, "MainLineRepaint",
                "使用色欄で分離したい色を1色以上選択してください。"
            )
            return
        selected_set = {tuple(map(int, rgb)) for rgb in selected}

        source_index = self.canvas.active_layer_index
        frame_total = max(1, len(self.canvas.frames))
        progress = self.create_progress_counter(
            "MainLineRepaint",
            frame_total * 2,
            "対象色を確認しています",
        )

        found_target = False
        for scan_index, frame in enumerate(self.canvas.frames, 1):
            self.update_progress_counter(
                progress,
                scan_index - 1,
                frame_total * 2,
                f"コマ {scan_index} の対象色を確認しています",
            )
            source = frame.layers[source_index]
            if not source.has_content:
                continue
            pixels = imaging.qimage_rgba_array(source.image)
            height, width = pixels.shape[:2]
            visible = pixels[:, :, 3] > 0
            rgb24 = (
                pixels[:, :, 0].astype(np.uint32) << 16
                | pixels[:, :, 1].astype(np.uint32) << 8
                | pixels[:, :, 2].astype(np.uint32)
            )
            selected24 = np.array(
                [(r << 16) | (g << 8) | b for r, g, b in selected_set],
                dtype=np.uint32,
            )
            match = visible & np.isin(rgb24, selected24)
            if np.any(match):
                found_target = True
                break

        if not found_target:
            self.close_progress_counter(progress)
            QMessageBox.warning(
                self,
                "MainLineRepaint",
                "選択した使用色が選択レイヤー内に見つかりませんでした。\n処理は適用しません。",
            )
            return

        self.canvas.push_doc_undo()

        # Add two layers directly above the source layer:
        # source (hidden) -> Paint -> LINE
        paint_index = source_index + 1
        line_index = source_index + 2

        for frame in self.canvas.frames:
            source_layer = frame.layers[source_index]
            source_layer.visible = False

            paint_layer = Layer(
                "Paint",
                blank_image(),
                visible=True,
                opacity=1.0,
                has_content=False,
            )
            line_layer = Layer(
                "LINE",
                blank_image(),
                visible=True,
                opacity=1.0,
                has_content=False,
            )
            frame.layers.insert(paint_index, paint_layer)
            frame.layers.insert(line_index, line_layer)

        for frame_index, frame in enumerate(self.canvas.frames):
            self.update_progress_counter(
                progress,
                frame_total + frame_index,
                frame_total * 2,
                f"コマ {frame_index + 1} をレイヤー分離しています",
            )
            source = frame.layers[source_index]
            if not source.has_content:
                continue

            pixels = imaging.qimage_rgba_array(source.image)
            height, width = pixels.shape[:2]

            visible = pixels[:, :, 3] > 0
            rgb24 = (
                pixels[:, :, 0].astype(np.uint32) << 16
                | pixels[:, :, 1].astype(np.uint32) << 8
                | pixels[:, :, 2].astype(np.uint32)
            )
            selected24 = np.array(
                [(r << 16) | (g << 8) | b for r, g, b in selected_set],
                dtype=np.uint32,
            )
            line_mask = visible & np.isin(rgb24, selected24)
            line_pixels = np.zeros_like(pixels)
            line_pixels[line_mask] = pixels[line_mask]

            # Paint: remove the line pixels, then fill those gaps using
            # the most frequent adjacent existing color. No intermediate colors.
            paint_pixels = pixels.copy()
            paint_pixels[line_mask, 3] = 0

            pending = [tuple(v) for v in np.argwhere(line_mask)]
            for _pass in range(12):
                if not pending:
                    break
                remaining = []
                for y, x in pending:
                    neighbors = []
                    for nx, ny in (
                        (x - 1, y),
                        (x + 1, y),
                        (x, y - 1),
                        (x, y + 1),
                    ):
                        if (
                            0 <= nx < width
                            and 0 <= ny < height
                            and paint_pixels[ny, nx, 3] > 0
                        ):
                            neighbors.append(
                                (
                                    int(paint_pixels[ny, nx, 0]),
                                    int(paint_pixels[ny, nx, 1]),
                                    int(paint_pixels[ny, nx, 2]),
                                )
                            )
                    if not neighbors:
                        remaining.append((y, x))
                        continue

                    counts = {}
                    for color in neighbors:
                        counts[color] = counts.get(color, 0) + 1
                    fill_color = max(counts, key=counts.get)
                    paint_pixels[y, x, 0] = fill_color[0]
                    paint_pixels[y, x, 1] = fill_color[1]
                    paint_pixels[y, x, 2] = fill_color[2]
                    paint_pixels[y, x, 3] = 255
                pending = remaining

            line_image = QImage(
                line_pixels.data,
                width,
                height,
                line_pixels.strides[0],
                QImage.Format.Format_RGBA8888,
            ).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

            paint_image = QImage(
                paint_pixels.data,
                width,
                height,
                paint_pixels.strides[0],
                QImage.Format.Format_RGBA8888,
            ).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

            frame.layers[line_index].image = line_image
            frame.layers[line_index].has_content = bool(np.any(line_mask))
            frame.layers[line_index].exposure = source.exposure

            frame.layers[paint_index].image = paint_image
            frame.layers[paint_index].has_content = bool(np.any(paint_pixels[:, :, 3] > 0))
            frame.layers[paint_index].exposure = source.exposure

        self.close_progress_counter(progress)
        self.canvas.active_layer_index = line_index
        self._used_color_cache.clear()
        self.canvas._color_filter_cache.clear()
        self.canvas._silhouette_cache.clear()
        self._suppress_used_color_refresh_once = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

        self.statusBar().showMessage(
            "LINE／Paintを作成し、元レイヤーを非表示にしました。",
            3800,
        )

    def create_progress_counter(
        self,
        title,
        total,
        label=None,
        cancellable=False,
    ):
        """進捗カウンター。`cancellable` で中断ボタンを出す。

        中断は一括処理ランナー（frame_scope）が拾い、それまでの変更を
        巻き戻すので、押した時点で部分適用は残らない。
        """
        total = max(1, int(total))
        dialog = QProgressDialog(
            label or title,
            "中止" if cancellable else "",
            0,
            total,
            self,
        )
        dialog.setWindowTitle(title)
        if not cancellable:
            dialog.setCancelButton(None)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumWidth(330)
        dialog.setValue(0)
        dialog.show()
        QApplication.processEvents()
        return dialog

    def update_progress_counter(self, dialog, value, total, label):
        if dialog is None:
            return
        value = max(0, min(int(total), int(value)))
        dialog.setLabelText(f"{label}\n{value} / {int(total)}")
        dialog.setValue(value)
        QApplication.processEvents()

    def close_progress_counter(self, dialog):
        if dialog is None:
            return
        dialog.setValue(dialog.maximum())
        dialog.close()
        dialog.deleteLater()
        QApplication.processEvents()

    def remove_dust_fill_surrounding(self):
        """選択レイヤー内でゴミ取り／塗り抜けを実行する。"""
        mode = self.tools.dust_mode.currentText()
        max_area = max(
            1,
            min(100, int(self.tools.dust_size.value())),
        )
        selected_only = (
            self.tools.dust_selected_only.isChecked()
        )
        selected_colors = self.canvas.selected_used_colors()
        selected_colors.discard((255,255,255))

        if selected_only and not selected_colors:
            self.statusBar().showMessage(
                "使用色パネルで対象色を選択してください。",
                2800,
            )
            return

        layer_index = int(
            self.canvas.active_layer_index
        )
        scope = (
            frame_scope.FrameScope.all_frames(self.canvas, [layer_index])
            if self.tools.dust_all_frames.isChecked()
            else frame_scope.FrameScope.current_frame(
                self.canvas, [layer_index]
            )
        )
        cells = frame_scope.resolve_scope_cells(self.canvas, scope)
        if not cells:
            self.statusBar().showMessage(
                "選択レイヤーに処理できるキーフレームがありません。",
                2800,
            )
            return

        def despeckle_cell(context):
            pixels = imaging.qimage_rgba_array(context.image)
            changed = despeckle.despeckle_pixels(
                pixels,
                mode=mode,
                max_area=max_area,
                selected_colors=(
                    selected_colors if selected_only else None
                ),
            )
            if not changed:
                return None
            # ○化や未使用化はせず、キーフレーム構造を維持する。
            return imaging.rgba_array_to_qimage(pixels), changed

        progress = self.create_progress_counter(
            mode,
            max(1, len(cells)),
            f"{mode}対象を解析しています",
            cancellable=True,
        )
        QApplication.setOverrideCursor(
            Qt.CursorShape.WaitCursor
        )
        try:
            result = frame_scope.apply_over_scope(
                self.canvas,
                scope,
                despeckle_cell,
                label=mode,
                progress=lambda value, total, message: (
                    self.update_progress_counter(
                        progress,
                        value,
                        max(1, total),
                        message,
                    )
                ),
                cancelled=progress.wasCanceled,
            )
        finally:
            QApplication.restoreOverrideCursor()
            self.close_progress_counter(progress)

        if result.cancelled:
            self.statusBar().showMessage(
                f"{mode}を中止しました。変更は残していません。",
                3000,
            )
            return

        changed_cells = result.changed_cells
        changed_pixels = result.changed_pixels
        changed_frame_indices = [
            frame_index for frame_index, _layer in result.cells
        ]

        if not changed_pixels:
            target_text = (
                "選択色の" if selected_only else ""
            )
            self.statusBar().showMessage(
                f"指定サイズ以内の{target_text}{mode}対象は"
                "見つかりませんでした。",
                3000,
            )
            return

        # 表示専用キャッシュも含めてすべて破棄する。
        # ゴミ取り／塗り抜けは画像オブジェクトを差し替えるため、
        # ここを更新しないと表示／非表示切替まで旧画像が残る場合がある。
        self.canvas._color_filter_cache.clear()
        self.canvas._color_index_cache.clear()
        self.canvas._pseudo_transparency_cache.clear()
        self.canvas._silhouette_cache.clear()
        self.canvas._onion_cache.clear()
        self.canvas._playback_frame_cache.clear()

        # 変更したセルを通知し、現在表示中の保持コマも即時再描画する。
        for changed_frame_index in changed_frame_indices:
            self.canvas.cellChanged.emit(
                int(changed_frame_index),
                int(layer_index),
            )

        self.canvas.changed.emit()
        self.canvas.update()
        self.canvas.repaint()
        QApplication.processEvents()

        self.refresh_used_colors_with_counter(
            f"{mode}後の使用色を更新しています"
        )

        # 使用色の再走査後にも再描画を予約し、進捗ダイアログの
        # 閉鎖後に旧表示へ戻ることを防ぐ。
        self.canvas.update()
        self.statusBar().showMessage(
            f"選択レイヤーの{changed_cells}コマで"
            f"{changed_pixels:,}ピクセルへ{mode}を適用しました。",
            3600,
        )
