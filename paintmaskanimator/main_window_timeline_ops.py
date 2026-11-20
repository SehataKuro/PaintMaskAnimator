"""Timeline cell/frame/key operations; owned as ``window.timeline_ops``.

Drives timeline navigation, exposure and drawing-number editing, blank-key
creation, frame deletion, and cell move/copy against the timeline widget.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""

from .canvas_changes import publish_editable_position, publish_frame_changes
from typing import TYPE_CHECKING

from PySide6.QtCore import QItemSelectionModel, QTimer
from .i18n import tr
from .document import (
    is_pending,
)
from .undo_entries import ExposureUndo
from .normalize_dialog import NormalizeNumbersDialog
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
        canvas = self.window.canvas
        canvas.push_doc_undo()
        changes = canvas._document.normalize_sheet_numbers(layer_indices)
        publish_frame_changes(canvas, changes, notify_selection=True, clear_onion=False)
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
        canvas = self.window.canvas
        canvas.push_doc_undo()
        changes = canvas._document.clear_sheet_span(layer_index, start, start + 1, archive=True)
        publish_frame_changes(canvas, changes, notify_selection=True, clear_onion=False)

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

        canvas = self.window.canvas
        layer_count = len(canvas.layers)
        positions = ((layer_count - 1 - row, column) for row, column in selected_cells)
        moves = canvas._document.prepare_sheet_moves(positions, -row_delta, column_delta)
        if moves is None:
            self.window.statusBar().showMessage(
                tr("移動先がタイムライン範囲外です。"), 2500
            )
            return
        if not moves:
            return

        canvas.push_doc_undo()

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

        active_layer = max(0, min(layer_count - 1, layer_count - 1 - int(destination_row)))
        changes = canvas._document.move_sheet_cells(moves, int(destination_column), active_layer)
        if not changes:
            return
        publish_frame_changes(canvas, changes)
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
        prepared = canvas._document.prepare_exposure_resize(layer_index, key_column, boundary_column, edge)
        if prepared is None:
            return
        if edge == "left":
            canvas.push_doc_undo()
        else:
            canvas.push_undo(ExposureUndo.capture(canvas, layer_index, prepared[0]))
        changes = canvas._document.resize_sheet_exposure(layer_index, edge, prepared)
        publish_frame_changes(canvas, changes, clear_onion=False, mark_dirty=False)

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
        canvas = self.window.canvas
        if int(destination_column) < 0:
            return
        canvas.push_doc_undo()
        changes = canvas._document.copy_sheet_cell(int(source_column), int(destination_column), source_layer)
        publish_frame_changes(canvas, changes, clear_onion=False)

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
        changes = self.window.canvas._document.recall_sheet_number(int(column), layer_index, number, source)
        publish_frame_changes(self.window.canvas, changes, clear_onion=False)

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
            changes = self.window.canvas._document.ensure_frame_count(column + 1)
            publish_editable_position(self.window.canvas, changes)
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

    def _after_cell_edit(self, changes):
        if changes:
            publish_frame_changes(self.window.canvas, changes, notify_selection=True)
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
        changes = canvas._document.clear_sheet_span(layer_index, start, start + _exposure)
        self._after_cell_edit(changes)
        return bool(changes)

    def copy_cell_to_clipboard(self, visual_row, column):
        """セルを控える。絵・露出・番号ごと複製して持つ。"""
        self.window.canvas._commit_brush_stroke()
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
        length = getattr(self.window, "_timeline_cell_clipboard_length", 1)
        changes = canvas._document.paste_sheet_cell(column, layer_index, source, length)
        self._after_cell_edit(changes)
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
        changes = canvas._document.new_sheet_cell(column, layer_index)
        self._after_cell_edit(changes)
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
        canvas.push_doc_undo()
        changes = canvas._document.new_sheet_number(column, layer_index)
        self._after_cell_edit(changes)
        new_number = canvas.frames[column].layers[layer_index].sequence_number
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
        changes = canvas._document.split_sheet_clip(column, layer_index)
        self._after_cell_edit(changes)
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
        canvas.push_doc_undo()
        changes = canvas._document.merge_sheet_clip(start, layer_index)
        self._after_cell_edit(changes)
        return True

    def insert_frame_at(self, visual_row, column):
        """そのレイヤーに1コマ挿入し、区間途中なら同じ絵・○を伸ばす。"""
        target = self._cell_target(visual_row, column)
        if target is None:
            return False
        layer_index, column = target
        canvas = self.window.canvas
        canvas.push_doc_undo()
        changes = canvas._document.insert_track_frame(column, layer_index)
        self._after_cell_edit(changes)
        return bool(changes)

    def delete_frame_at(self, visual_row, column):
        """そのコマを削除して、以降を1コマ詰める。"""
        target = self._cell_target(visual_row, column)
        if target is None:
            return False
        layer_index, column = target
        canvas = self.window.canvas
        canvas.push_doc_undo()
        changes = canvas._document.delete_track_frame(column, layer_index)
        self._after_cell_edit(changes)
        return bool(changes)
