"""Layer-row operations; owned by ``MainWindow`` as ``window.layers``.

Adds, duplicates, merges, deletes, reorders and edits layer rows across all
frames, keeping the timeline widget in sync.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QMessageBox
from .models import Layer
from .utils import blank_image
from .progress import close_counter, create_counter, update_counter
from .logging_setup import get_logger

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)


class LayerOpsController:
    """Owned by ``MainWindow`` as ``window.layers``.

    A collaborator rather than a mixin -- see main_window_export.py for why.
    """

    def __init__(self, window: "MainWindow"):
        self.window = window

    def _indices_from_rows(self, rows):
        count = len(self.window.canvas.layers)
        result = []
        for row in rows:
            index = count - 1 - int(row)
            if 0 <= index < count and index not in result:
                result.append(index)
        return sorted(result)

    def add_fast(self):
        """新規空レイヤーでは全コマの使用色走査を行わず即時表示する。"""
        self.window._used_color_timer.stop()
        self.window._used_color_request += 1
        self.window._suppress_used_color_refresh_once = True
        self.window.canvas.add_layer()
        self.window.palette.set_colors([])

    def duplicate_rows(self, rows):
        indices = self._indices_from_rows(rows)
        if not indices:
            return
        self.window.canvas.push_doc_undo()
        for frame in self.window.canvas.frames:
            for index in sorted(indices, reverse=True):
                copied = frame.layers[index].clone()
                copied.name = f"{copied.name} コピー"
                frame.layers.insert(index + 1, copied)
        self.window.canvas.active_layer_index = min(
            len(self.window.canvas.layers) - 1,
            max(indices) + len(indices),
        )
        self.window.canvas._onion_cache.clear()
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()

    def merge_rows(self, rows):
        indices = self._indices_from_rows(rows)
        if len(indices) < 2:
            self.window.statusBar().showMessage(
                "結合するレイヤーをShift＋クリックで2つ以上選択してください。",
                2600,
            )
            return
        if indices != list(range(indices[0], indices[-1] + 1)):
            self.window.statusBar().showMessage(
                "結合できるのは連続しているレイヤーです。",
                2600,
            )
            return

        base_index = indices[0]
        top_index = indices[-1]
        result_name = self.window.canvas.layers[top_index].name
        frame_count = len(self.window.canvas.frames)
        self.window.canvas.push_doc_undo()

        # 元レイヤーを削除する前に、各タイムライン位置で実際に表示される
        # キーフレームを解決する。これにより「ーーー｜」の保持区間が
        # 白紙へ置き換わる問題を防ぐ。
        merged_layers = [
            Layer(
                result_name,
                blank_image(),
                visible=True,
                opacity=1.0,
                has_content=False,
                exposure=1,
            )
            for _ in range(frame_count)
        ]

        previous_signature = None
        active_key_frame = None

        progress = create_counter(
            self.window,
            "レイヤーを結合",
            max(1, frame_count),
            "保持コマを解析しています",
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for frame_index in range(frame_count):
                resolved = []
                signature_parts = []

                for layer_index in indices:
                    key_frame = self.window.canvas.resolve_key_frame(
                        frame_index, layer_index
                    )
                    if key_frame is None:
                        continue
                    source_layer = self.window.canvas.frames[
                        key_frame
                    ].layers[layer_index]
                    if (
                        not source_layer.has_content
                        or not source_layer.visible
                        or source_layer.opacity <= 0.0
                    ):
                        continue

                    resolved.append(source_layer)
                    signature_parts.append((
                        int(layer_index),
                        int(key_frame),
                        int(source_layer.image.cacheKey()),
                        round(float(source_layer.opacity), 6),
                    ))

                signature = tuple(signature_parts)

                if not resolved:
                    # 白紙区間では直前の露出を延長しない。
                    previous_signature = None
                    active_key_frame = None
                elif (
                    signature == previous_signature
                    and active_key_frame is not None
                ):
                    merged_layers[active_key_frame].exposure += 1
                else:
                    merged_image = blank_image()
                    painter = QPainter(merged_image)
                    painter.setCompositionMode(
                        QPainter.CompositionMode.CompositionMode_SourceOver
                    )
                    for source_layer in resolved:
                        painter.setOpacity(
                            max(
                                0.0,
                                min(1.0, float(source_layer.opacity)),
                            )
                        )
                        # 表示フィルターはデータへ焼き込まず、元画像を結合する。
                        painter.drawImage(0, 0, source_layer.image)
                    painter.end()

                    merged_layers[frame_index] = Layer(
                        result_name,
                        merged_image,
                        visible=True,
                        opacity=1.0,
                        has_content=True,
                        exposure=1,
                    )
                    previous_signature = signature
                    active_key_frame = frame_index

                update_counter(
                    progress,
                    frame_index + 1,
                    max(1, frame_count),
                    f"{frame_index + 1} / {frame_count} コマを結合しています",
                )
        finally:
            QApplication.restoreOverrideCursor()
            close_counter(progress)

        for frame_index, frame in enumerate(self.window.canvas.frames):
            for layer_index in reversed(indices):
                frame.layers.pop(layer_index)
            frame.layers.insert(base_index, merged_layers[frame_index])

        self.window.canvas.active_layer_index = base_index
        self.window.canvas._onion_cache.clear()
        self.window.canvas._color_filter_cache.clear()
        self.window.canvas._color_index_cache.clear()
        self.window.canvas._silhouette_cache.clear()
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()
        self.window.used_color.refresh_with_counter(
            "結合後の使用色を更新しています"
        )

    def delete_rows(self, rows):
        indices = self._indices_from_rows(rows)
        if not indices:
            return
        if len(indices) >= len(self.window.canvas.layers):
            QMessageBox.warning(
                self.window,
                "レイヤー削除",
                "すべてのレイヤーは削除できません。1つ以上残してください。",
            )
            return
        self.window.canvas.push_doc_undo()
        for frame in self.window.canvas.frames:
            for index in sorted(indices, reverse=True):
                frame.layers.pop(index)
        self.window.canvas.active_layer_index = min(
            indices[0],
            len(self.window.canvas.layers) - 1,
        )
        self.window.canvas._onion_cache.clear()
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()
        self.window.used_color.refresh_with_counter("削除後の使用色を更新しています")

    def move_row(self, source_rows, destination_row):
        """レイヤー名と全コマのタイムラインデータを同じ順序で移動する。"""
        if not self.window.canvas.frames:
            return

        count = len(self.window.canvas.layers)
        try:
            rows = sorted({int(row) for row in source_rows})
        except TypeError:
            rows = [int(source_rows)]

        if (
            not rows
            or any(row < 0 or row >= count for row in rows)
            or rows != list(range(rows[0], rows[-1] + 1))
        ):
            self.window.refresh_ui()
            return

        block_count = len(rows)
        destination_row = max(
            0,
            min(int(destination_row), count - block_count),
        )
        if destination_row == rows[0]:
            return

        # 現在レイヤーを視覚行番号で記憶し、移動後も同じレイヤーを選択する。
        active_visual_row = count - 1 - self.window.canvas.active_layer_index
        visual_order = list(range(count))
        moved_order = visual_order[rows[0]:rows[-1] + 1]
        del visual_order[rows[0]:rows[-1] + 1]
        visual_order[destination_row:destination_row] = moved_order
        new_active_visual_row = visual_order.index(active_visual_row)

        self.window.canvas.push_doc_undo()
        for frame in self.window.canvas.frames:
            visual_layers = list(reversed(frame.layers))
            moved_layers = visual_layers[rows[0]:rows[-1] + 1]
            del visual_layers[rows[0]:rows[-1] + 1]
            visual_layers[destination_row:destination_row] = moved_layers
            frame.layers = list(reversed(visual_layers))

        self.window.canvas.active_layer_index = count - 1 - new_active_visual_row
        self.window.canvas._onion_cache.clear()
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()

    def rename_row(self, row, name):
        if getattr(self.window, "_closing", False) or row < 0:
            return
        layers = self.window.canvas.layers
        index = len(layers) - 1 - row
        if not (0 <= index < len(layers)):
            return
        name = name.strip() or f"Layer {index + 1}"
        self.window.canvas.push_doc_undo()
        for frame in self.window.canvas.frames:
            if index < len(frame.layers):
                frame.layers[index].name = name
        self.window.canvas.changed.emit()

    def on_selected(self, row):
        if getattr(self.window, "_closing", False) or row < 0:
            return
        layers = self.window.canvas.layers
        index = len(layers) - 1 - row
        if 0 <= index < len(layers):
            if self.window.canvas.active_layer_index == index:
                return
            self.window.canvas.active_layer_index = index
            self.window.canvas._onion_cache.clear()
            self.window.timeline.select_current(self.window.canvas.current_exposure(), index)
            self.window.used_color._refresh_without_delay()
            self.window.canvas.update()

    def set_visibility_row(self, row, on):
        if getattr(self.window, "_closing", False) or row < 0:
            return
        layers = self.window.canvas.layers
        index = len(layers) - 1 - row
        if 0 <= index < len(layers):
            self.window.canvas.set_layer_visibility(index, on)

    def set_opacity_row(self, row, opacity):
        if getattr(self.window, "_closing", False) or row < 0:
            return
        layers = self.window.canvas.layers
        index = len(layers) - 1 - row
        if 0 <= index < len(layers):
            self.window.canvas.set_layer_opacity(index, opacity)
            # 一覧の保持値もその場で更新し、別レイヤー選択時に正しく復元する。
            item = self.window.timeline.layer_list.item(row)
            if item is not None:
                item.setData(
                    Qt.ItemDataRole.UserRole + 5,
                    float(opacity),
                )

    def set_draft_rows(self, rows):
        """選択レイヤーの下書きレイヤーモードを切り替える（後から変更可能）。"""
        indices = self._indices_from_rows(rows)
        if not indices:
            return
        layers = self.window.canvas.layers
        # 選択内の1つでも下書きでなければ全て下書きにする（トグル）。
        new_state = not all(
            bool(getattr(layers[index], "is_draft", False))
            for index in indices
        )
        self.window.canvas.push_doc_undo()
        for frame in self.window.canvas.frames:
            for index in indices:
                if index < len(frame.layers):
                    frame.layers[index].is_draft = new_state
        self.window.canvas._onion_cache.clear()
        self.window.canvas._color_filter_cache.clear()
        self.window.canvas._color_index_cache.clear()
        self.window.canvas._silhouette_cache.clear()
        self.window._used_color_cache.clear()
        self.window._used_color_layer_cache.clear()
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()
        self.window.used_color._refresh_without_delay()
        self.window.statusBar().showMessage(
            "下書きレイヤーモードを"
            + ("有効化" if new_state else "解除")
            + "しました。",
            2600,
        )

    @staticmethod
    def _copy_display_properties(source, target):
        target.name = str(source.name)
        target.visible = bool(source.visible)
        target.opacity = float(source.opacity)
        target.is_paper = bool(source.is_paper)
        target.is_draft = bool(getattr(source, "is_draft", False))
        target.alpha_locked = bool(source.alpha_locked)
        target.color_filter_enabled = bool(
            source.color_filter_enabled
        )
        target.color_filter_rgb = (
            tuple(source.color_filter_rgb)
            if source.color_filter_rgb is not None
            else None
        )
