"""Timeline cell/frame/key operations; owned as ``window.timeline_ops``.

Drives timeline navigation, exposure and drawing-number editing, blank-key
creation, frame deletion, and cell move/copy against the timeline widget.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from dataclasses import replace

from .document import replace_slot
from .document import clear_pending
from typing import TYPE_CHECKING

from PySide6.QtCore import QItemSelectionModel, QTimer
from .i18n import tr
from .utils import blank_image
from .document import (
    is_pending,
    place_number, create_drawing, archive_hint, copy_drawing_reference, import_drawing_reference,
)
from .undo_entries import ExposureUndo
from .normalize_dialog import NormalizeNumbersDialog
from .timeline import TimelineWidget
from .logging_setup import get_logger

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)


class TimelineOpsController:
    """Owned by ``MainWindow`` as ``window.timeline_ops``.

    A collaborator rather than a mixin -- see main_window_export.py for why.
    """

    def __init__(self, window: "MainWindow"):
        self.window = window

    def set_mode(self, mode, *, select_number=False):
        mode = "sheet" if str(mode) == "sheet" else "sequence"
        layer_index = int(self.window.canvas.active_layer_index)
        selected_number = None
        if self.window.canvas.frames and 0 <= layer_index < len(self.window.canvas.layers):
            selected_number = self.window.canvas.frames[
                self.window.canvas.current_frame
            ].layers[layer_index].sequence_number
        if self.window.canvas.timeline_mode == "sequence" and mode == "sheet":
            # 連番専用セルのシート配置ではフレーム数・位置が変わるため、
            # 移動前の状態を文書単位で保存してUndo参照切れを防ぐ。
            if any(
                is_pending(layer)
                for frame in self.window.canvas.frames
                for layer in frame.layers
            ):
                self.window.canvas.push_doc_undo()
            self.window.canvas.apply_sequence_only_entries()
        if selected_number is not None and (select_number or mode != self.window.canvas.timeline_mode):
            matching = [
                column
                for column, frame in enumerate(self.window.canvas.frames)
                if (
                    frame.layers[layer_index].sequence_number == selected_number
                    and (frame.layers[layer_index].has_content or frame.layers[layer_index].is_blank_key)
                    and (mode == "sequence" or not is_pending(frame.layers[layer_index]))
                )
            ]
            if matching:
                self.window.canvas.current_frame = matching[0]
        self.window.canvas.timeline_mode = mode
        self.window.timeline.set_timeline_mode(mode)
        self.window.timeline.add_exposure.setVisible(mode == "sheet")
        # 切り替え前の選択セルを保持すると、キャンバスだけ先に切り替わり
        # 赤枠が旧タブの列へ残る。再構築前に選択を明示的に解除する。
        self.window.timeline.table.clearSelection()
        self.window.timeline.refresh(
            self.window.canvas.frames,
            self.window.canvas.current_frame,
            self.window.canvas.active_layer_index,
            getattr(self.window.canvas, "tween_pending", None),
        )
        self.window.timeline.select_current(
            self.window.canvas.current_frame,
            self.window.canvas.active_layer_index,
        )
        self.window.timeline.table.viewport().update()

    def _navigate_sequence_number(self, step, wrap=False):
        """シート配置ではなく絵番号順に連番セルを移動する。"""
        layer_index = int(self.window.canvas.active_layer_index)
        columns = self.window.canvas.sequence_entry_columns(layer_index)
        entries = sorted(
            (
                int(self.window.canvas.frames[column].layers[layer_index].sequence_number),
                int(column),
            )
            for column in columns
        )
        if not entries:
            return
        current_layer = self.window.canvas.frames[
            self.window.canvas.current_frame
        ].layers[layer_index]
        current_number = current_layer.sequence_number
        current_index = next(
            (
                index for index, (number, _column) in enumerate(entries)
                if number == current_number
            ),
            -1 if int(step) > 0 else len(entries),
        )
        target_index = current_index + (1 if int(step) > 0 else -1)
        if wrap:
            target_index %= len(entries)
        else:
            target_index = max(0, min(len(entries) - 1, target_index))
        self.window.canvas.current_frame = entries[target_index][1]
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()

    def jump_boundary(self, end=False):
        canvas = self.window.canvas
        if canvas.timeline_mode == "sequence":
            self._navigate_sequence_number(1000000 if end else -1000000, wrap=False)
        else:
            canvas.current_frame = len(canvas.frames) - 1 if end else 0
            canvas.selectionChanged.emit()
            canvas.update()

    def resize_range(self, edge, column):
        canvas = self.window.canvas
        if canvas.timeline_mode != "sheet":
            return
        start = max(0, int(canvas.playback_range_start))
        end = (
            len(canvas.frames)
            if canvas.playback_range_end is None
            else max(start + 1, int(canvas.playback_range_end))
        )
        if edge == "end":
            target = max(start + 1, int(column))
            if target == end:
                return
            canvas.playback_range_end = target
        else:
            target = max(0, min(int(column), end - 1))
            if target == start:
                return
            canvas.playback_range_start = target
            canvas.playback_range_end = end
        canvas.changed.emit()
        self.window.refresh_ui()

    def previous_frame(self):
        if self.window.canvas.timeline_mode == "sequence":
            self._navigate_sequence_number(-1, wrap=False)
        else:
            self.window.canvas.previous_frame()

    def next_frame(self):
        if self.window.canvas.timeline_mode == "sequence":
            self._navigate_sequence_number(1, wrap=False)
        else:
            self.window.canvas.next_frame()

    def previous_key(self):
        if self.window.canvas.timeline_mode == "sequence":
            self._navigate_sequence_number(-1, wrap=True)
        else:
            self.window.canvas.previous_key_frame()

    def next_key(self):
        if self.window.canvas.timeline_mode == "sequence":
            self._navigate_sequence_number(1, wrap=True)
        else:
            self.window.canvas.next_key_frame()

    def normalize_numbers(self, visual_rows):
        """確認画面で振り直す番号を見せてから、絵番号をシート順へ振り直す。"""
        if self.window.canvas.timeline_mode != "sheet":
            return
        canvas = self.window.canvas
        layer_count = len(canvas.layers)
        selected = {
            layer_count - 1 - int(row)
            for row in visual_rows
            if 0 <= layer_count - 1 - int(row) < layer_count
        }
        if not selected:
            selected = {int(canvas.active_layer_index)}
        layers = [
            (
                layer_index,
                canvas.layers[layer_index].name,
                canvas.sequence_number_normalization(layer_index),
            )
            for layer_index in reversed(range(layer_count))
        ]
        if not any(
            old != new
            for _index, _name, mapping in layers
            for old, new in mapping.items()
        ):
            self.window.statusBar().showMessage(
                tr("番号はすでにタイムラインの順番どおりです。"), 2500
            )
            return
        dialog = NormalizeNumbersDialog(layers, selected, self.window)
        if dialog.exec() != NormalizeNumbersDialog.DialogCode.Accepted:
            return
        self.apply_normalize_numbers(dialog.target_layer_indices())

    def apply_normalize_numbers(self, layer_indices):
        """指定レイヤーの絵番号をシート順へ振り直す（確認なし）。"""
        layer_indices = sorted({int(index) for index in layer_indices})
        if not layer_indices:
            return
        self.window.canvas.push_doc_undo()
        for layer_index in layer_indices:
            self.window.canvas.normalize_sequence_numbers(layer_index)
        self.window.canvas._cell_structure_dirty = True
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()
        self.window.statusBar().showMessage(
            tr("番号をタイムラインの順番に正規化しました。"), 2500
        )

    def create_blank_key(
        self,
        visual_row,
        column,
    ):
        layer_count = len(self.window.canvas.layers)
        layer_index = layer_count - 1 - int(visual_row)
        if not (0 <= layer_index < layer_count):
            return
        if self.window.canvas.timeline_mode == "sequence":
            self.window.canvas.insert_sequence_blank(
                layer_index,
                int(column) + 1,
            )
            return
        self.window.canvas.create_blank_key(
            int(column),
            layer_index,
        )

    def delete_frame(self):
        if self.window.canvas.timeline_mode == "sequence":
            self.window.canvas.delete_sequence_entry(
                self.window.canvas.active_layer_index,
                self.window.timeline.table.currentColumn() + 1,
            )
            return
        layer_index = int(self.window.canvas.active_layer_index)
        block = self.window.canvas.timeline_block_at(
            self.window.canvas.current_frame, layer_index
        )
        if block is None:
            return
        _kind, start, _exposure = block
        layer = self.window.canvas.frames[int(start)].layers[layer_index]
        self.window.canvas.push_doc_undo()
        if layer.has_content and layer.sequence_number is not None:
            self.window.canvas._document.put_archive_hint(
                layer._track, int(layer.sequence_number), archive_hint(layer),
            )
        self.window.canvas._clear_timeline_layer_cell(layer)
        self.window.canvas._cell_structure_dirty = True
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()

    def _restore_selection(self, cells):
        table = self.window.timeline.table
        table.clearSelection()
        valid = []
        for row, column in cells:
            item = table.item(int(row), int(column))
            if item is not None:
                item.setSelected(True)
                valid.append((int(row), int(column)))
        if valid:
            table.setCurrentCell(
                valid[0][0],
                valid[0][1],
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )

    def move_selection(
        self,
        cells,
        anchor_row,
        anchor_column,
        destination_row,
        destination_column,
    ):
        """複数選択に含まれるコマ塊を相対配置のまま移動する。"""
        if self.window.canvas.timeline_mode == "sequence":
            if int(anchor_row) != int(destination_row):
                self.window.statusBar().showMessage(
                    tr("連番画像は同じレイヤー内で入れ替えてください。"), 2500
                )
                return
            layer_count = len(self.window.canvas.layers)
            layer_index = layer_count - 1 - int(anchor_row)
            self.window.canvas.move_sequence_image(
                layer_index,
                int(anchor_column) + 1,
                int(destination_column) + 1,
            )
            return
        try:
            selected_cells = {
                (int(row), int(column))
                for row, column in cells
            }
        except (TypeError, ValueError) as exc:
            log.debug("could not normalize selected cells: %s", exc)
            return
        if not selected_cells:
            return

        row_delta = int(destination_row) - int(anchor_row)
        column_delta = (
            int(destination_column) - int(anchor_column)
        )
        if row_delta == 0 and column_delta == 0:
            return

        layer_count = len(self.window.canvas.layers)
        blocks = {}
        for visual_row, column in selected_cells:
            layer_index = layer_count - 1 - visual_row
            if not (0 <= layer_index < layer_count):
                continue
            kind, start, exposure = TimelineWidget.timeline_span_at(
                self.window.canvas.frames,
                layer_index,
                column,
            )
            if (
                kind in ("content", "blank")
                and start is not None
            ):
                key = (layer_index, int(start))
                if key not in blocks:
                    layer = self.window.canvas.frames[
                        int(start)
                    ].layers[layer_index]
                    blocks[key] = (
                        kind,
                        max(1, int(exposure)),
                        layer.clone(),
                    )

        if not blocks:
            return

        moves = []
        for (
            source_layer,
            source_start,
        ), (
            kind,
            exposure,
            copied,
        ) in blocks.items():
            source_visual_row = (
                layer_count - 1 - source_layer
            )
            target_visual_row = (
                source_visual_row + row_delta
            )
            target_layer = (
                layer_count - 1 - target_visual_row
            )
            target_start = (
                source_start + column_delta
            )
            if (
                target_start < 0
                or not (0 <= target_layer < layer_count)
            ):
                self.window.statusBar().showMessage(
                    tr("移動先がタイムライン範囲外です。"),
                    2500,
                )
                return
            moves.append(
                (
                    source_layer,
                    source_start,
                    target_layer,
                    target_start,
                    kind,
                    exposure,
                    copied,
                )
            )

        self.window.canvas.push_doc_undo()

        # 元位置を未使用セルへ戻す。
        for (
            source_layer,
            source_start,
            _target_layer,
            _target_start,
            _kind,
            _exposure,
            _copied,
        ) in moves:
            for index in range(source_start, source_start + _exposure):
                self.window.canvas._clear_timeline_layer_cell(
                    self.window.canvas.frames[index].layers[source_layer])

        maximum_end = max(
            target_start + exposure
            for (
                _source_layer,
                _source_start,
                _target_layer,
                target_start,
                _kind,
                exposure,
                _copied,
            ) in moves
        )
        self.window.canvas._ensure_frame_count(maximum_end)

        # 移動先と重なる既存露出を切り、既存の明示コマを消す。
        for (
            _source_layer,
            _source_start,
            target_layer,
            target_start,
            _kind,
            exposure,
            _copied,
        ) in moves:
            target_end = target_start + exposure - 1
            for column in range(
                target_start,
                target_end + 1,
            ):
                target = self.window.canvas.frames[
                    column
                ].layers[target_layer]
                if (
                    target.has_content
                    or getattr(
                        target,
                        "is_blank_key",
                        False,
                    )
                ):
                    self.window.canvas._clear_timeline_layer_cell(
                        target
                    )

        # 相対位置を保って配置。
        for (
            _source_layer,
            _source_start,
            target_layer,
            target_start,
            kind,
            exposure,
            copied,
        ) in moves:
            target = self.window.canvas.frames[
                target_start
            ].layers[target_layer]
            if _source_layer != target_layer:
                import_drawing_reference(
                    self.window.canvas.frames, {},
                    target_start, target_layer, copied, preferred_frame=int(destination_column),
                    content=kind == "content", blank=kind == "blank")
            else:
                copy_drawing_reference(target, copied, content=kind == "content", blank=kind == "blank")
            target.visible = bool(copied.visible)
            target.opacity = float(copied.opacity)
            target.alpha_locked = bool(
                copied.alpha_locked
            )
            target.color_filter_enabled = bool(
                copied.color_filter_enabled
            )
            target.color_filter_rgb = (
                tuple(copied.color_filter_rgb)
                if copied.color_filter_rgb is not None
                else None
            )
            target.blank_start = kind == "blank"
            for index in range(target_start + 1, target_start + exposure):
                self.window.canvas.frames[index].layers[target_layer] = target.clone_reference()

        moved_cells = [
            (
                row + row_delta,
                column + column_delta,
            )
            for row, column in selected_cells
            if (
                row + row_delta >= 0
                and column + column_delta >= 0
            )
        ]

        self.window.canvas.current_frame = max(
            0,
            int(destination_column),
        )
        self.window.canvas.active_layer_index = max(
            0,
            min(
                layer_count - 1,
                layer_count - 1 - int(destination_row),
            ),
        )
        self.window.canvas._cell_structure_dirty = True
        self.window.canvas._onion_cache.clear()
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()
        QTimer.singleShot(
            0,
            lambda cells=tuple(moved_cells):
                self._restore_selection(cells)
        )

    def resize_exposure(
        self, visual_row, key_column, boundary_column, edge="right"
    ):
        layer_count = len(self.window.canvas.layers)
        layer_index = layer_count - 1 - int(visual_row)
        key_column = int(key_column)
        boundary_column = int(boundary_column)
        if not (
            0 <= layer_index < layer_count
            and 0 <= key_column < len(self.window.canvas.frames)
        ):
            return
        canvas = self.window.canvas
        block = canvas.timeline_block_at(key_column, layer_index)
        if block is None:
            return
        _kind, key_column, old_exposure = block
        old_end = key_column + old_exposure - 1
        source = canvas.frames[key_column].layers[layer_index].clone_slot()
        if edge == "left":
            previous = [col for col in canvas.timeline_step_columns(layer_index) if col < key_column]
            minimum = 0
            if previous:
                prev = canvas.timeline_block_at(previous[-1], layer_index)
                if prev is not None:
                    minimum = prev[1] + prev[2]
            new_start = max(minimum, min(boundary_column, old_end))
            if new_start == key_column:
                return
            canvas.push_doc_undo()
            for col in range(min(key_column, new_start), old_end + 1):
                if col < new_start:
                    canvas._clear_timeline_layer_cell(canvas.frames[col].layers[layer_index])
                else:
                    slot = source.clone_reference()
                    slot.blank_start = bool(slot.is_blank_key and col == new_start)
                    if col != new_start:
                        slot.tween = None
                        slot.tween_member = None
                    replace_slot(canvas.frames[col], layer_index, slot)
            canvas.current_frame = new_start
        else:
            new_end = max(key_column, boundary_column)
            if new_end == old_end:
                return
            canvas.push_undo(ExposureUndo.capture(canvas, layer_index, key_column))
            following = [col for col in canvas.timeline_step_columns(layer_index)
                         if col > key_column]
            if following and new_end >= following[0]:
                shift = new_end + 1 - following[0]
                frame_count = len(canvas.frames)
                canvas._shift_timeline_layer_right(layer_index, following[0], shift)
                canvas._ensure_frame_count(frame_count + shift)
            canvas._ensure_frame_count(new_end + 1)
            for col in range(key_column + 1, max(old_end, new_end) + 1):
                if col > new_end:
                    canvas._clear_timeline_layer_cell(canvas.frames[col].layers[layer_index])
                elif col > old_end:
                    slot = source.clone_reference()
                    slot.blank_start = False
                    slot.tween = None
                    slot.tween_member = None
                    replace_slot(canvas.frames[col], layer_index, slot)
            canvas.current_frame = key_column
        canvas.active_layer_index = layer_index
        canvas.changed.emit()
        canvas.selectionChanged.emit()
        canvas.update()

    def move_cell(self, source_row, source_column, destination_row, destination_column):
        layer_count = len(self.window.canvas.layers)
        source_layer = layer_count - 1 - source_row
        destination_layer = layer_count - 1 - destination_row
        if self.window.canvas.timeline_mode == "sequence":
            if source_layer != destination_layer:
                self.window.statusBar().showMessage(
                    tr("連番画像は同じレイヤー内で入れ替えてください。"), 2500
                )
                return
            self.window.canvas.move_sequence_image(
                source_layer,
                int(source_column) + 1,
                int(destination_column) + 1,
            )
            return
        self.window._suppress_used_color_refresh_once = True
        moved = self.window.canvas.move_timeline_cell(source_column, source_layer, destination_column, destination_layer)
        if not moved:
            self.window._suppress_used_color_refresh_once = False
            self.window.statusBar().showMessage(tr("コマを移動できませんでした。"), 2500)

    def copy_cell(self, source_row, source_column, destination_row, destination_column):
        """Altドラッグで、同じ絵番号を参照するシートキーを複製する。"""
        if self.window.canvas.timeline_mode != "sheet":
            return
        layer_count = len(self.window.canvas.layers)
        source_layer = layer_count - 1 - int(source_row)
        destination_layer = layer_count - 1 - int(destination_row)
        if source_layer != destination_layer:
            self.window.statusBar().showMessage(tr("複製は同じレイヤー内で行ってください。"), 2500)
            return
        block = self.window.canvas.timeline_block_at(int(source_column), source_layer)
        if block is None or block[0] != "content":
            return
        source = self.window.canvas.frames[int(block[1])].layers[source_layer]
        self.window.canvas.push_doc_undo()
        self.window.canvas._ensure_frame_count(int(destination_column) + 1)
        target = self.window.canvas.frames[int(destination_column)].layers[destination_layer]
        copied = source.clone()
        copy_drawing_reference(target, copied)
        for index in range(int(destination_column) + 1, int(destination_column) + block[2]):
            self.window.canvas._ensure_frame_count(index + 1)
            self.window.canvas.frames[index].layers[destination_layer] = target.clone_reference()
        target.visible = copied.visible
        target.opacity = copied.opacity
        target.alpha_locked = copied.alpha_locked
        target.color_filter_enabled = copied.color_filter_enabled
        target.color_filter_rgb = copied.color_filter_rgb
        self.window.canvas.current_frame = int(destination_column)
        self.window.canvas.active_layer_index = destination_layer
        self.window.canvas._cell_structure_dirty = True
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()

    def recall_sequence_number(self, visual_row, column, number):
        """既存の絵番号をシートの指定位置へ再配置する。"""
        if self.window.canvas.timeline_mode != "sheet":
            return
        layer_count = len(self.window.canvas.layers)
        layer_index = layer_count - 1 - int(visual_row)
        candidates = self.window.canvas.sequence_entry_columns(layer_index)
        source = next((
            self.window.canvas.frames[index].layers[layer_index]
            for index in candidates
            if self.window.canvas.frames[index].layers[layer_index].sequence_number == int(number)
        ), None)
        if source is None:
            resolved = self.window.canvas._resolve_numbered_target(
                int(column), layer_index, int(number))
            if resolved is None:
                return
            _, source = resolved
        self.window.canvas.push_doc_undo()
        self.window.canvas._ensure_frame_count(int(column) + 1)
        target = self.window.canvas.frames[int(column)].layers[layer_index]
        place_number(target, source, number)
        self.window.canvas.current_frame = int(column)
        self.window.canvas.active_layer_index = layer_index
        self.window.canvas._cell_structure_dirty = True
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()

    def select_exposure(self, column, visual_row):
        previous_layer = self.window.canvas.active_layer_index
        if self.window.canvas.timeline_mode == "sequence":
            layer_count = len(self.window.canvas.layers)
            layer_index = layer_count - 1 - int(visual_row)
            number = int(column) + 1
            entries = self.window.canvas.sequence_entry_columns(layer_index)
            frame_by_number = {
                int(self.window.canvas.frames[index].layers[layer_index].sequence_number): index
                for index in entries
            }
            if number in frame_by_number:
                self.window.canvas.current_frame = frame_by_number[number]
                self.window.canvas.active_layer_index = layer_index
                self.window.canvas.selectionChanged.emit()
                self.window.canvas.update()
            return
        column = max(0, int(column))
        if column >= len(self.window.canvas.frames):
            self.window.canvas._ensure_frame_count(column + 1)
            self.window.canvas._cell_structure_dirty = True
        self.window.canvas.select_exposure(column, visual_row)
        # タイムラインの別レイヤーのコマを選んだ場合も、そのレイヤーの使用色へ即時更新。
        if self.window.canvas.active_layer_index != previous_layer:
            self.window.used_color._refresh_without_delay()
        else:
            # 同じレイヤーでは全コマ共通の使用色一覧なので、既存表示を維持する。
            self.window.canvas.update()

    # ------------------------------------------------------------------
    # セルの右クリックメニューから呼ぶ操作（Issue #48）
    # ------------------------------------------------------------------
    def _cell_target(self, visual_row, column):
        """``(レイヤー番号, 列)``。範囲外なら ``None``。

        タイムラインの行は下のレイヤーが下に並ぶので、表示行とレイヤー番号は
        逆順になる。
        """
        canvas = self.window.canvas
        layer_count = len(canvas.layers)
        layer_index = layer_count - 1 - int(visual_row)
        column = int(column)
        if not (0 <= layer_index < layer_count):
            return None
        if not (0 <= column < len(canvas.frames)):
            return None
        return layer_index, column

    def _cell_block(self, visual_row, column):
        """``(レイヤー番号, 露出の開始列, 露出の長さ)``。空セルなら ``None``。"""
        target = self._cell_target(visual_row, column)
        if target is None:
            return None
        layer_index, column = target
        block = self.window.canvas.timeline_block_at(column, layer_index)
        if block is None:
            return None
        _kind, start, exposure = block
        return layer_index, int(start), int(exposure)

    def _after_cell_edit(self):
        canvas = self.window.canvas
        canvas._cell_structure_dirty = True
        canvas._onion_cache.clear()
        canvas.changed.emit()
        canvas.selectionChanged.emit()
        canvas.update()
        self.window.refresh_ui()

    def clear_cell(self, visual_row, column):
        """セルを未使用に戻す（トラックラベルを削除／削除）。

        露出は詰めない。前のセルの表示が伸びるのではなく、そこが空くだけに
        するため。コマごと詰めたいときは「フレームを削除」を使う。
        """
        block = self._cell_block(visual_row, column)
        if block is None:
            return False
        layer_index, start, _exposure = block
        canvas = self.window.canvas
        canvas.push_doc_undo()
        for index in range(start, start + _exposure):
            canvas._clear_timeline_layer_cell(canvas.frames[index].layers[layer_index])
        self._after_cell_edit()
        return True

    def copy_cell_to_clipboard(self, visual_row, column):
        """セルを控える。絵・露出・番号ごと複製して持つ。"""
        block = self._cell_block(visual_row, column)
        if block is None:
            self.window.statusBar().showMessage(
                tr("コピーできるセルがありません。"), 2400
            )
            return False
        layer_index, start, _exposure = block
        self.window._timeline_cell_clipboard = (
            self.window.canvas.frames[start].layers[layer_index].clone()
        )
        self.window._timeline_cell_clipboard_length = _exposure
        self.window.timeline.set_cell_clipboard_filled(True)
        self.window.statusBar().showMessage(tr("セルをコピーしました。"), 2000)
        return True

    def cut_cell_to_clipboard(self, visual_row, column):
        if not self.copy_cell_to_clipboard(visual_row, column):
            return False
        self.clear_cell(visual_row, column)
        self.window.statusBar().showMessage(tr("セルを切り取りました。"), 2000)
        return True

    def paste_cell_from_clipboard(self, visual_row, column):
        """控えたセルを貼り付ける。名前はその場のレイヤー名のままにする。"""
        source = getattr(self.window, "_timeline_cell_clipboard", None)
        if source is None:
            self.window.statusBar().showMessage(
                tr("貼り付けられるセルがありません。"), 2400
            )
            return False
        target = self._cell_target(visual_row, column)
        if target is None:
            return False
        layer_index, column = target
        canvas = self.window.canvas
        canvas.push_doc_undo()
        existing = canvas.frames[column].layers[layer_index]
        pasted = source.clone()
        pasted.name = existing.name
        pasted.visible = existing.visible
        pasted.opacity = existing.opacity
        # トゥイーンの帯は貼り付け先では組み直せないので持ち込まない。
        pasted.tween = None
        pasted.tween_member = None
        import_drawing_reference(
            canvas.frames, {}, column, layer_index, pasted,
            preferred_frame=canvas.current_frame)
        existing.tween = None
        existing.tween_member = None
        length = getattr(self.window, "_timeline_cell_clipboard_length", 1)
        canvas._ensure_frame_count(column + length)
        for index in range(column + 1, column + length):
            # A later existing key truncates the pasted exposure.
            block = canvas.timeline_block_at(index, layer_index)
            if block is not None and block[1] == index:
                break
            replace_slot(canvas.frames[index], layer_index, existing.clone_reference())
        self._after_cell_edit()
        self.window.statusBar().showMessage(tr("セルを貼り付けました。"), 2000)
        return True

    def new_animation_cell(self, visual_row, column):
        """そのセルに、絵を持つ新しいセルを作る。"""
        target = self._cell_target(visual_row, column)
        if target is None:
            return False
        layer_index, column = target
        canvas = self.window.canvas
        canvas.push_doc_undo()
        layer = canvas.frames[column].layers[layer_index]
        canvas._clear_timeline_layer_cell(layer)
        create_drawing(layer, blank_image(), number=None)
        canvas.current_frame = column
        canvas.active_layer_index = layer_index
        self._after_cell_edit()
        return True

    def new_number_here(self, visual_row, column):
        """選んだ1コマを末尾の絵番号で複製し、前後は元の絵を保持する。"""
        canvas = self.window.canvas
        target = self._cell_target(visual_row, column)
        block = None
        if target is not None:
            layer_index, column = target
            block = canvas.timeline_block_at(column, layer_index)
        if block is None or block[0] != "content":
            self.window.statusBar().showMessage(
                tr("絵のあるセルを選んでください。"), 2400
            )
            return False
        _kind, start, _length = block
        start = int(start)
        source = canvas.frames[start].layers[layer_index]
        if source.sequence_number is None:
            self.window.statusBar().showMessage(
                tr("番号のある絵を選んでください。"), 2400
            )
            return False
        numbers = [
            layer.sequence_number
            for frame in canvas.frames
            if (layer := frame.layers[layer_index]).has_content
            and isinstance(layer.sequence_number, int)
        ]
        numbers.extend(source._track.cells)
        new_number = max(numbers, default=0) + 1
        canvas.push_doc_undo()
        selected = source.clone()
        selected.detach_cell()
        source._track.cells[new_number] = selected._cell
        selected.share_track_with(source)
        selected.sequence_number = new_number
        if selected._track.connected:
            selected.__dict__.pop("_cell", None)
        selected.cell_name = None
        selected.tween = None
        selected.tween_member = None
        selected.is_blank_key = False
        selected.has_content = True
        clear_pending(selected)
        replace_slot(canvas.frames[column], layer_index, selected)
        canvas.current_frame = column
        canvas.active_layer_index = layer_index
        self._after_cell_edit()
        self.window.statusBar().showMessage(
            tr("この位置を新しい番号 {number} にしました。").format(number=new_number),
            2400,
        )
        return True

    def split_clip(self, visual_row, column):
        """露出の途中で切り、そこから新しいセルにする（クリップを分割）。

        絵は複製するので、分割したあとは前半・後半を別々に直せる。
        """
        block = self._cell_block(visual_row, column)
        if block is None:
            return False
        layer_index, start, exposure = block
        target = self._cell_target(visual_row, column)
        if target is None:
            return False
        _layer_index, column = target
        if column <= start or column > start + exposure - 1:
            self.window.statusBar().showMessage(
                tr("露出の途中を選んでください。先頭では分割できません。"), 2400
            )
            return False
        canvas = self.window.canvas
        canvas.push_doc_undo()
        head = canvas.frames[start].layers[layer_index]
        copied = head.clone()
        copied.detach_cell()
        number = max(head._track.cells, default=0) + 1
        head._track.cells[number] = copied._cell
        for position in range(column, start + exposure):
            slot = canvas.frames[position].layers[layer_index]
            slot.sequence_number = number
            slot.has_content = True
            slot.is_blank_key = False
            slot.blank_start = False
            clear_pending(slot)
            slot.tween = None
            slot.tween_member = None
        self._after_cell_edit()
        return True

    def merge_clip(self, visual_row, column):
        """前のセルの露出を伸ばして、このセルを呑み込む（クリップを結合）。"""
        block = self._cell_block(visual_row, column)
        if block is None:
            return False
        layer_index, start, exposure = block
        canvas = self.window.canvas
        previous = canvas.timeline_block_at(start - 1, layer_index) if start else None
        if previous is None:
            self.window.statusBar().showMessage(
                tr("前に結合できるセルがありません。"), 2400
            )
            return False
        _kind, previous_start, _previous_length = previous
        canvas.push_doc_undo()
        source = canvas.frames[previous_start].layers[layer_index]
        for position in range(start, start + exposure):
            slot = source.clone_slot()
            slot.tween = None
            slot.tween_member = None
            slot.blank_start = False
            position_info = slot.__dict__.get("_v1_position")
            if position_info is not None and position_info.legacy is not None:
                # Cloned save keys must not reach beyond the merged region.
                slot.__dict__["_v1_position"] = replace(
                    position_info, legacy=replace(
                        position_info.legacy,
                        exposure=min(position_info.legacy.exposure, start + exposure - position)))
            replace_slot(canvas.frames[position], layer_index, slot)
        self._after_cell_edit()
        return True

    def insert_frame_at(self, visual_row, column):
        """そのレイヤーに1コマ挿入し、区間途中なら同じ絵・○を伸ばす。"""
        target = self._cell_target(visual_row, column)
        if target is None:
            return False
        layer_index, column = target
        canvas = self.window.canvas
        block = canvas.timeline_block_at(column, layer_index)
        fill = (canvas.frames[column].layers[layer_index].clone_reference()
                if block is not None and column > block[1] else None)
        canvas.push_doc_undo()
        canvas._ensure_frame_count(len(canvas.frames) + 1)
        canvas._shift_timeline_layer_right(layer_index, column, 1, extend_containing=True)
        if fill is not None:
            # Extending the visible reference does not replace the hidden PNG
            # already retained at the insertion position.
            position = canvas.frames[column].layers[layer_index].__dict__.get("_v1_position")
            if position is not None:
                fill.__dict__["_v1_position"] = position
            fill.tween = None
            fill.tween_member = None
            fill.blank_start = False
            replace_slot(canvas.frames[column], layer_index, fill)
        self._after_cell_edit()
        return True

    def delete_frame_at(self, visual_row, column):
        """そのコマを削除して、以降を1コマ詰める。"""
        target = self._cell_target(visual_row, column)
        if target is None:
            return False
        layer_index, column = target
        canvas = self.window.canvas
        canvas.push_doc_undo()
        canvas._shift_timeline_layer_left(layer_index, column + 1)
        canvas.current_frame = min(column, len(canvas.frames) - 1)
        self._after_cell_edit()
        return True
