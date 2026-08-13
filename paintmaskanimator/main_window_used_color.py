"""Used-color extraction and palette editing for MainWindow.

Split out of ``main_window.py`` as a mixin. These methods scan the active layer
for its used colors (with caching and progress), keep the used-color panel in
sync across undo/redo, and apply palette edits (isolate / replace / delete /
merge). They run against a live ``MainWindow`` instance.
"""
from .common import *  # noqa: F401,F403
from ._main_window_members import MainWindowMembers
from . import imaging
from .logging_setup import get_logger
from .undo_entries import LayerBatchUndo, PaletteStateUndo

log = get_logger(__name__)


class UsedColorMixin(MainWindowMembers):
    def schedule_used_color_refresh(self):
        # 描画直後は colorUsed で新色だけを即時追加し、全画像の色走査は
        # アイドル時にまとめる。投げ縄塗り・バケツ確定時の一拍停止を防ぐ。
        self._used_color_request += 1
        self._used_color_timer.start()

    def _refresh_used_colors_without_delay(self):
        self._used_color_timer.stop()
        self._used_color_request += 1
        self.refresh_used_colors(request=self._used_color_request)

    def undo_with_used_colors(self):
        if not self.canvas.undo_stack:
            return
        self.canvas.undo()
        self._refresh_used_colors_without_delay()
        self.refresh_history_panel()

    def redo_with_used_colors(self):
        if not self.canvas.redo_stack:
            return
        self.canvas.redo()
        self._refresh_used_colors_without_delay()
        self.refresh_history_panel()

    def push_palette_history(self, before, label):
        """使用色パネルの並べ替え・親子・表示/マスク変更をUndo履歴へ積む。"""
        self.canvas.push_undo(PaletteStateUndo(before, label))
        self.refresh_history_panel()

    def jump_history(self, delta):
        """ヒストリーパネルのクリック位置まで、必要な回数だけUndo/Redoする。"""
        try:
            steps = int(delta)
        except (TypeError, ValueError):
            return
        if steps < 0:
            for _ in range(-steps):
                if not self.canvas.undo_stack:
                    break
                self.canvas.undo()
        elif steps > 0:
            for _ in range(steps):
                if not self.canvas.redo_stack:
                    break
                self.canvas.redo()
        else:
            return
        self._refresh_used_colors_without_delay()
        self.refresh_history_panel()

    def refresh_history_panel(self):
        panel = getattr(self, "history_panel", None)
        if panel is not None:
            panel.refresh()

    def _used_color_cache_key(self, image):
        try:
            return (int(image.cacheKey()), image.width(), image.height())
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.debug("cacheKey() unavailable, using id() fallback: %s", exc)
            return (id(image), image.width(), image.height())

    def _used_color_layer_signature(self, layer_index):
        signature = []
        for frame in self.canvas.frames:
            if layer_index >= len(frame.layers):
                signature.append(None)
                continue
            layer = frame.layers[layer_index]
            signature.append(
                self._used_color_cache_key(layer.image)
                if layer.has_content else None
            )
        return tuple(signature)

    def _apply_used_color_result(self, colors, exceeded=False):
        ordered = [QColor(r, g, b) for r, g, b in colors[:100]]
        self.palette.set_colors(ordered)
        if exceeded:
            self.palette.count_label.setText("100色以上")

    def _extract_used_colors(self, image, limit=101):
        # Palette filtering needs the same full-image RGB index. Build it while
        # the used-color scan is already running so the first visibility toggle
        # can reuse it instead of scanning every pixel again.
        _rgba, packed, opaque = self.canvas._color_index_for_image(image)
        if packed is None or opaque is None:
            return []
        visible_pixels = packed[opaque]
        if visible_pixels.size == 0:
            return []
        unique = np.unique(visible_pixels)
        if len(unique) > limit:
            unique = unique[:limit]
        return [
            (
                int((value >> 16) & 255),
                int((value >> 8) & 255),
                int(value & 255),
            )
            for value in unique
        ]

    def refresh_used_colors(
        self,
        request=None,
        progress=None,
        progress_offset=0,
        progress_total=None,
        progress_label="使用色を認識しています",
    ):
        if request is not None and request != self._used_color_request:
            return
        if self.canvas.drawing:
            # Never let an all-frame palette scan interrupt a live brush stroke.
            self._used_color_timer.start(500)
            return
        if not self.canvas.frames:
            self.palette.set_colors([])
            if progress is not None:
                total = progress_total or max(1, progress_offset)
                self.update_progress_counter(
                    progress, progress_offset, total, progress_label
                )
            return

        layer_index = self.canvas.active_layer_index
        layer_signature = self._used_color_layer_signature(layer_index)
        layer_cache_key = (int(layer_index), layer_signature)
        cached_layer = self._used_color_layer_cache.get(layer_cache_key)
        if cached_layer is not None:
            colors, exceeded = cached_layer
            self._apply_used_color_result(colors, exceeded)
            if progress is not None:
                total = progress_total or max(1, progress_offset + len(self.canvas.frames))
                self.update_progress_counter(
                    progress, total, total, "使用色の認識が完了しました"
                )
            return
        all_colors = []
        seen_colors = set()
        exceeded = False
        frame_total = len(self.canvas.frames)
        combined_total = progress_total or max(1, progress_offset + frame_total)

        for scan_index, frame in enumerate(self.canvas.frames, 1):
            if progress is not None:
                self.update_progress_counter(
                    progress,
                    progress_offset + scan_index - 1,
                    combined_total,
                    f"{progress_label}（{scan_index}/{frame_total}コマ）",
                )

            if layer_index < len(frame.layers):
                layer = frame.layers[layer_index]
                if layer.has_content:
                    image = layer.image
                    key = self._used_color_cache_key(image)
                    cached = self._used_color_cache.get(key)
                    if cached is None:
                        colors = self._extract_used_colors(image, 101)
                        cached = (colors[:100], len(colors) > 100)
                        if len(self._used_color_cache) >= 512:
                            self._used_color_cache.pop(
                                next(iter(self._used_color_cache))
                            )
                        self._used_color_cache[key] = cached
                    colors, cell_exceeded = cached
                    for rgb in colors:
                        if rgb not in seen_colors:
                            seen_colors.add(rgb)
                            all_colors.append(rgb)
                    exceeded = (
                        exceeded or cell_exceeded or len(all_colors) > 100
                    )

            # 100色を超えた後も、進捗表示は最後まで進める。
            if len(all_colors) > 100:
                exceeded = True

        colors = all_colors[:100]
        if len(self._used_color_layer_cache) >= 128:
            self._used_color_layer_cache.pop(
                next(iter(self._used_color_layer_cache))
            )
        self._used_color_layer_cache[layer_cache_key] = (
            tuple(colors), bool(exceeded)
        )
        self._apply_used_color_result(colors, exceeded)

        if progress is not None:
            self.update_progress_counter(
                progress,
                progress_offset + frame_total,
                combined_total,
                "使用色の認識が完了しました",
            )

    def apply_palette_isolate_color(self, color):
        self.isolate_selected_color(QColor(color))

    def apply_palette_replacements(self, mapping, operation="色置換"):
        if not mapping:
            if operation == "色置換":
                message = "置換色が登録されていません。"
            elif operation == "色削除":
                message = "削除する使用色が選択されていません。"
            else:
                message = "統合する使用色が選択されていません。"
            self.statusBar().showMessage(message, 2200)
            return False

        packed_mapping = {}
        rgb_mapping = {}
        for source, destination in mapping.items():
            source = tuple(int(value) for value in source[:3])
            destination = tuple(int(value) for value in destination[:3])
            if source == (255, 255, 255) or source == destination:
                continue
            source_value = (source[0] << 16) | (source[1] << 8) | source[2]
            packed_mapping[source_value] = destination
            rgb_mapping[source] = destination

        if not packed_mapping:
            if operation == "色置換":
                message = "置換前と置換後が同じ色です。"
            elif operation == "色削除":
                message = "削除できる使用色が選択されていません。"
            else:
                message = "親以外の使用色を選択してください。"
            self.statusBar().showMessage(message, 2200)
            return False

        # 色ごとに画像全体を再走査せず、24bit RGBを一度だけ検索する。
        source_values = np.array(
            sorted(packed_mapping.keys()), dtype=np.uint32
        )
        destination_values = np.array(
            [packed_mapping[int(value)] for value in source_values],
            dtype=np.uint8,
        )

        layer_index = self.canvas.active_layer_index
        changed_pixels = 0
        changed_cells = 0
        undo_cells = []
        cache_updates = {}
        cache_removals = set()
        frame_items = list(enumerate(self.canvas.frames))
        progress = self.create_progress_counter(
            operation,
            len(frame_items),
            f"{operation}の対象コマを確認しています",
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for progress_index, (frame_index, frame) in enumerate(
                frame_items, 1
            ):
                self.update_progress_counter(
                    progress,
                    progress_index - 1,
                    len(frame_items),
                    f"コマ {frame_index + 1} に{operation}を適用しています",
                )
                if layer_index >= len(frame.layers):
                    continue
                layer = frame.layers[layer_index]
                if not layer.has_content:
                    continue

                old_cache_key = self._used_color_cache_key(layer.image)
                old_cached_colors = self._used_color_cache.get(old_cache_key)
                rgba = layer.image.convertToFormat(
                    QImage.Format.Format_RGBA8888
                )
                width, height = rgba.width(), rgba.height()
                if width <= 0 or height <= 0:
                    continue
                ptr = imaging.qimage_buffer(rgba)
                rows = np.frombuffer(
                    ptr, dtype=np.uint8
                ).reshape((height, rgba.bytesPerLine()))
                pixels = rows[:, :width * 4].reshape((height, width, 4))

                packed = (
                    (pixels[:, :, 0].astype(np.uint32) << 16)
                    | (pixels[:, :, 1].astype(np.uint32) << 8)
                    | pixels[:, :, 2].astype(np.uint32)
                )
                indices = np.searchsorted(source_values, packed)
                safe_indices = np.minimum(indices, len(source_values) - 1)
                matches = (
                    (indices < len(source_values))
                    & (source_values[safe_indices] == packed)
                    & (pixels[:, :, 3] != 0)
                )
                cell_count = int(np.count_nonzero(matches))
                if not cell_count:
                    continue

                undo_cells.append(
                    (frame_index, layer.image.copy(), layer.has_content)
                )
                pixels[:, :, :3][matches] = destination_values[
                    safe_indices[matches]
                ]
                layer.image = rgba.convertToFormat(
                    QImage.Format.Format_ARGB32_Premultiplied
                )
                cache_removals.add(old_cache_key)
                if old_cached_colors is not None:
                    old_colors, exceeded = old_cached_colors
                    transformed_colors = []
                    seen_transformed = set()
                    for rgb in old_colors:
                        new_rgb = tuple(rgb_mapping.get(tuple(rgb), tuple(rgb)))
                        if new_rgb in seen_transformed:
                            continue
                        seen_transformed.add(new_rgb)
                        transformed_colors.append(new_rgb)
                    cache_updates[
                        self._used_color_cache_key(layer.image)
                    ] = (transformed_colors[:100], exceeded)
                changed_pixels += cell_count
                changed_cells += 1
                self.update_progress_counter(
                    progress,
                    progress_index,
                    len(frame_items),
                    f"コマ {frame_index + 1} の{operation}が完了しました",
                )
        finally:
            QApplication.restoreOverrideCursor()
            self.close_progress_counter(progress)

        if not changed_pixels:
            self.statusBar().showMessage(
                "選択した使用色は画像内にありませんでした。",
                2400,
            )
            return False

        self.canvas.push_undo(LayerBatchUndo(layer_index, undo_cells))
        for cache_key in cache_removals:
            self._used_color_cache.pop(cache_key, None)
        self._used_color_cache.update(cache_updates)
        self.canvas._color_filter_cache.clear()
        self.canvas._color_index_cache.clear()
        self.canvas._silhouette_cache.clear()

        # コマ構造は変わらないためタイムラインを再構築しない。
        # 画像更新と、キャッシュを利用した使用色一覧の更新だけを行う。
        self.canvas.update()
        self.schedule_used_color_refresh()
        self.statusBar().showMessage(
            f"{changed_cells}セル・{changed_pixels:,}ピクセルへ{operation}を適用しました。",
            3000,
        )
        return True

    def apply_palette_delete(self, selected_rgbs):
        """選択した使用色を #FFFFFF へ統合する。"""
        selected = set()
        try:
            for rgb in selected_rgbs:
                if rgb is None or len(rgb) < 3:
                    continue
                color = tuple(
                    max(0, min(255, int(channel)))
                    for channel in rgb[:3]
                )
                if color != (255, 255, 255):
                    selected.add(color)
        except (TypeError, ValueError):
            selected = set()

        if not selected:
            self.statusBar().showMessage(
                "削除する使用色が選択されていません。",
                2400,
            )
            return

        mapping = {
            color: (255, 255, 255)
            for color in selected
        }
        if self.apply_palette_replacements(
            mapping,
            operation="色削除",
        ):
            # 削除後に存在しない親・子選択を残さない。
            self.palette._clear_used_color_selection()
            self.statusBar().showMessage(
                f"{len(selected)}色を #FFFFFF へ統合しました。",
                3200,
            )

    def apply_palette_merge(self, parent_rgb, selected_rgbs):
        parent = tuple(int(channel) for channel in parent_rgb[:3])
        selected = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in selected_rgbs
            if rgb is not None and len(rgb) >= 3
        }
        if parent == (255, 255, 255):
            self.statusBar().showMessage(
                "背景色は統合先にできません。",
                2400,
            )
            return
        mapping = {
            rgb: parent
            for rgb in selected
            if rgb != parent and rgb != (255, 255, 255)
        }
        if self.apply_palette_replacements(mapping, operation="色統合"):
            self.palette._retain_parent_selection()

    def refresh_used_colors_with_counter(self, title="使用色を更新しています"):
        self._used_color_timer.stop()
        self._used_color_request += 1
        total = max(1, len(self.canvas.frames))
        progress = self.create_progress_counter(
            title,
            total,
            "選択レイヤーの使用色を認識しています",
        )
        try:
            self.refresh_used_colors(
                progress=progress,
                progress_total=total,
                progress_label="選択レイヤーの使用色を認識しています",
            )
        finally:
            self.close_progress_counter(progress)
