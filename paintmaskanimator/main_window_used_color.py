"""Used-colour scanning and palette editing; owned as ``window.used_color``.

Scans the active layer for the colours it uses (cached, with a progress
counter), keeps the used-colour panel in sync across undo/redo, and applies
palette edits: isolate, replace, delete and merge.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtGui import QColor, QImage
from . import imaging
from .progress import close_counter, create_counter, update_counter
from .logging_setup import get_logger
from .undo_entries import PaletteStateUndo

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)


class UsedColorController:
    """Owned by ``MainWindow`` as ``window.used_color``.

    A collaborator rather than a mixin -- see main_window_export.py for why.
    """

    def __init__(self, window: "MainWindow"):
        self.window = window

    def schedule_refresh(self):
        # 描画直後は colorUsed で新色だけを即時追加し、全画像の色走査は
        # アイドル時にまとめる。投げ縄塗り・バケツ確定時の一拍停止を防ぐ。
        self.window._used_color_request += 1
        self.window._used_color_timer.start()

    def _refresh_without_delay(self):
        self.window._used_color_timer.stop()
        self.window._used_color_request += 1
        self.refresh(request=self.window._used_color_request)

    def undo(self):
        if not self.window.canvas.undo_stack:
            return
        self.window.canvas.undo()
        self._refresh_without_delay()
        self.refresh_history_panel()

    def redo(self):
        if not self.window.canvas.redo_stack:
            return
        self.window.canvas.redo()
        self._refresh_without_delay()
        self.refresh_history_panel()

    def push_palette_history(self, before, label):
        """使用色パネルの並べ替え・親子・表示/マスク変更をUndo履歴へ積む。"""
        self.window.canvas.push_undo(PaletteStateUndo(before, label))
        self.refresh_history_panel()

    def jump_history(self, delta):
        """ヒストリーパネルのクリック位置まで、必要な回数だけUndo/Redoする。"""
        try:
            steps = int(delta)
        except (TypeError, ValueError):
            return
        if steps < 0:
            for _ in range(-steps):
                if not self.window.canvas.undo_stack:
                    break
                self.window.canvas.undo()
        elif steps > 0:
            for _ in range(steps):
                if not self.window.canvas.redo_stack:
                    break
                self.window.canvas.redo()
        else:
            return
        self._refresh_without_delay()
        self.refresh_history_panel()

    def switch_history_branch(self, branch_index, redo_steps=0):
        """ヒストリーパネルで選ばれた分岐へ履歴を切り替える。

        ``redo_steps`` はクリックされたブロックが分岐点から何手目かで、
        切り替え後にその回数だけRedoして、クリックした状態まで進む。
        """
        try:
            index = int(branch_index)
            steps = max(0, int(redo_steps))
        except (TypeError, ValueError):
            return
        if not self.window.canvas.switch_history_branch(index):
            # 到達できなくなった分岐だった場合も一覧を作り直す。
            self.refresh_history_panel()
            return
        # クリックしたブロックまで、分岐の未来を進める。
        for _ in range(steps):
            if not self.window.canvas.redo_stack:
                break
            self.window.canvas.redo()
        self._refresh_without_delay()
        self.refresh_history_panel()

    def refresh_history_panel(self):
        panel = getattr(self.window, "history_panel", None)
        if panel is not None:
            panel.refresh()

    def _cache_key(self, image):
        try:
            return (int(image.cacheKey()), image.width(), image.height())
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.debug("cacheKey() unavailable, using id() fallback: %s", exc)
            return (id(image), image.width(), image.height())

    def _layer_signature(self, layer_index):
        signature = []
        for frame in self.window.canvas.frames:
            if layer_index >= len(frame.layers):
                signature.append(None)
                continue
            layer = frame.layers[layer_index]
            signature.append(
                self._cache_key(layer.image)
                if layer.has_content else None
            )
        return tuple(signature)

    def _apply_result(self, colors, exceeded=False):
        ordered = [QColor(r, g, b) for r, g, b in colors[:100]]
        self.window.palette.set_colors(ordered)
        pending_categories = getattr(
            self.window, "_pending_palette_categories", None
        )
        if pending_categories is not None:
            self.window._pending_palette_categories = None
            self.window.palette.restore_categories(pending_categories)
        if exceeded:
            self.window.palette.count_label.setText("100色以上")

    def _extract(self, image, limit=101):
        # Palette filtering needs the same full-image RGB index. Build it while
        # the used-color scan is already running so the first visibility toggle
        # can reuse it instead of scanning every pixel again.
        _rgba, packed, opaque = self.window.canvas._color_index_for_image(image)
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

    def refresh(
        self,
        request=None,
        progress=None,
        progress_offset=0,
        progress_total=None,
        progress_label="使用色を認識しています",
    ):
        if request is not None and request != self.window._used_color_request:
            return
        if self.window.canvas.drawing:
            # Never let an all-frame palette scan interrupt a live brush stroke.
            self.window._used_color_timer.start(500)
            return
        if not self.window.canvas.frames:
            self.window.palette.set_colors([])
            if progress is not None:
                total = progress_total or max(1, progress_offset)
                update_counter(
                    progress, progress_offset, total, progress_label
                )
            return

        layer_index = self.window.canvas.active_layer_index
        # 下書きレイヤーは色数削減の対象外。使用色を取得せず空表示にする。
        current_frame = self.window.canvas.frames[
            max(0, min(int(self.window.canvas.current_frame), len(self.window.canvas.frames) - 1))
        ]
        if (
            0 <= layer_index < len(current_frame.layers)
            and getattr(current_frame.layers[layer_index], "is_draft", False)
        ):
            self.window.palette.set_colors([])
            if progress is not None:
                total = progress_total or max(1, progress_offset + len(self.window.canvas.frames))
                update_counter(
                    progress, total, total, "使用色の認識が完了しました"
                )
            return
        layer_signature = self._layer_signature(layer_index)
        layer_cache_key = (int(layer_index), layer_signature)
        cached_layer = self.window._used_color_layer_cache.get(layer_cache_key)
        if cached_layer is not None:
            colors, exceeded = cached_layer
            self._apply_result(colors, exceeded)
            if progress is not None:
                total = progress_total or max(1, progress_offset + len(self.window.canvas.frames))
                update_counter(
                    progress, total, total, "使用色の認識が完了しました"
                )
            return
        all_colors = []
        seen_colors = set()
        exceeded = False
        frame_total = len(self.window.canvas.frames)
        combined_total = progress_total or max(1, progress_offset + frame_total)

        for scan_index, frame in enumerate(self.window.canvas.frames, 1):
            if progress is not None:
                update_counter(
                    progress,
                    progress_offset + scan_index - 1,
                    combined_total,
                    f"{progress_label}（{scan_index}/{frame_total}コマ）",
                )

            if layer_index < len(frame.layers):
                layer = frame.layers[layer_index]
                if layer.has_content:
                    image = layer.image
                    key = self._cache_key(image)
                    cached = self.window._used_color_cache.get(key)
                    if cached is None:
                        colors = self._extract(image, 101)
                        cached = (colors[:100], len(colors) > 100)
                        if len(self.window._used_color_cache) >= 512:
                            self.window._used_color_cache.pop(
                                next(iter(self.window._used_color_cache))
                            )
                        self.window._used_color_cache[key] = cached
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
        if len(self.window._used_color_layer_cache) >= 128:
            self.window._used_color_layer_cache.pop(
                next(iter(self.window._used_color_layer_cache))
            )
        self.window._used_color_layer_cache[layer_cache_key] = (
            tuple(colors), bool(exceeded)
        )
        self._apply_result(colors, exceeded)

        if progress is not None:
            update_counter(
                progress,
                progress_offset + frame_total,
                combined_total,
                "使用色の認識が完了しました",
            )

    def apply_palette_isolate_color(self, color):
        self.window.colors.isolate_selected_color(QColor(color))

    def apply_palette_replacements(self, mapping, operation="色置換"):
        if not mapping:
            if operation == "色置換":
                message = "置換色が登録されていません。"
            elif operation == "色削除":
                message = "削除する使用色が選択されていません。"
            else:
                message = "統合する使用色が選択されていません。"
            self.window.statusBar().showMessage(message, 2200)
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
            self.window.statusBar().showMessage(message, 2200)
            return False

        # 色ごとに画像全体を再走査せず、24bit RGBを一度だけ検索する。
        source_values = np.array(
            sorted(packed_mapping.keys()), dtype=np.uint32
        )
        destination_values = np.array(
            [packed_mapping[int(value)] for value in source_values],
            dtype=np.uint8,
        )

        cache_updates = {}
        cache_removals = set()
        changed_by_cell = {}

        def replace_colors(context):
            """1レイヤー分の色置換。純粋な op として一括処理ランナーへ渡す。"""
            layer = context.layer
            old_cache_key = self._cache_key(layer.image)
            old_cached_colors = self.window._used_color_cache.get(old_cache_key)
            rgba = layer.image.convertToFormat(
                QImage.Format.Format_RGBA8888
            )
            width, height = rgba.width(), rgba.height()
            if width <= 0 or height <= 0:
                return None
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
                return None

            pixels[:, :, :3][matches] = destination_values[
                safe_indices[matches]
            ]
            produced = rgba.convertToFormat(
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
                    self._cache_key(produced)
                ] = (transformed_colors[:100], exceeded)
            changed_by_cell[(context.frame, context.layer_index)] = cell_count
            return produced

        result = self.window.scope.run_over(
            self.window.scope.current_frame(True),
            replace_colors,
            label=operation,
            count_pixels=lambda context, _image: changed_by_cell.get(
                (context.frame, context.layer_index), 0
            ),
        )
        changed_cells = result.changed_cells
        changed_pixels = result.changed_pixels

        if not changed_pixels:
            self.window.statusBar().showMessage(
                "選択した使用色は画像内にありませんでした。",
                2400,
            )
            return False

        for cache_key in cache_removals:
            self.window._used_color_cache.pop(cache_key, None)
        self.window._used_color_cache.update(cache_updates)

        # 次の使用色再走査で旧RGB行が消える前に、整理情報を置換後のRGBへ
        # 移しておく。これにより置換直後もチャートへ新しい色構成を追記できる。
        self.window.palette.remap_saved_color_metadata(rgb_mapping)

        # コマ構造は変わらないためタイムラインを再構築しない。画像更新と、
        # キャッシュを利用した使用色一覧の更新だけを行う（表示キャッシュの
        # 破棄と再描画は run_over_scope 側で済んでいる）。
        self.schedule_refresh()
        self.window.statusBar().showMessage(
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
            self.window.statusBar().showMessage(
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
            self.window.palette._clear_used_color_selection()
            self.window.statusBar().showMessage(
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
            self.window.statusBar().showMessage(
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
            self.window.palette._retain_parent_selection()

    def refresh_with_counter(self, title="使用色を更新しています"):
        self.window._used_color_timer.stop()
        self.window._used_color_request += 1
        total = max(1, len(self.window.canvas.frames))
        progress = create_counter(
            self.window,
            title,
            total,
            "選択レイヤーの使用色を認識しています",
        )
        try:
            self.refresh(
                progress=progress,
                progress_total=total,
                progress_label="選択レイヤーの使用色を認識しています",
            )
        finally:
            close_counter(progress)
