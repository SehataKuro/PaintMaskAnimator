"""Timeline cell/frame/key operations; owned as ``window.timeline_ops``.

Drives timeline navigation, exposure and drawing-number editing, blank-key
creation, frame deletion, and cell move/copy against the timeline widget.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from typing import TYPE_CHECKING

from PySide6.QtCore import QItemSelectionModel, QTimer
from .i18n import tr
from .utils import blank_image
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

    def set_mode(self, mode):
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
                layer.sequence_only
                for frame in self.window.canvas.frames
                for layer in frame.layers
            ):
                self.window.canvas.push_doc_undo()
            self.window.canvas.apply_sequence_only_entries()
        if selected_number is not None:
            matching = [
                column
                for column, frame in enumerate(self.window.canvas.frames)
                if (
                    frame.layers[layer_index].sequence_number == selected_number
                    and (frame.layers[layer_index].has_content or frame.layers[layer_index].is_blank_key)
                    and (mode == "sequence" or not frame.layers[layer_index].sequence_only)
                )
            ]
            if matching:
                self.window.canvas.current_frame = matching[0]
        self.window.canvas.timeline_mode = mode
        self.window.timeline.set_timeline_mode(mode)
        self.window.timeline.sequence_archive = self.window.canvas._sequence_archive
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
            self.window.canvas._sequence_archive[
                (layer_index, int(layer.sequence_number))
            ] = layer.clone()
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
            source = self.window.canvas.frames[
                source_start
            ].layers[source_layer]
            self.window.canvas._clear_timeline_layer_cell(source)

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
            covering = self.window.canvas.timeline_block_at(
                target_start,
                target_layer,
            )
            if covering is not None:
                _cover_kind, cover_start, _cover_exposure = covering
                if cover_start < target_start:
                    cover_layer = self.window.canvas.frames[
                        cover_start
                    ].layers[target_layer]
                    cover_layer.exposure = max(
                        1,
                        target_start - cover_start,
                    )

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
            target.image = (
                copied.image.copy()
                if kind == "content"
                else blank_image()
            )
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
            target.has_content = kind == "content"
            target.is_blank_key = kind == "blank"
            target.sequence_number = (
                copied.sequence_number
                if kind == "content"
                else None
            )
            target.cell_name = (
                getattr(copied, "cell_name", None)
                if kind == "content"
                else None
            )
            target.sequence_only = bool(copied.sequence_only)
            target.exposure = max(1, int(exposure))

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
        layer = self.window.canvas.frames[key_column].layers[layer_index]
        old_exposure = max(1, int(layer.exposure))
        old_end = key_column + old_exposure - 1
        self.window.canvas.push_doc_undo()

        if edge == "left":
            previous_keys = [
                col for col in range(0, key_column)
                if (
                    self.window.canvas.frames[col].layers[layer_index].has_content
                    or getattr(
                        self.window.canvas.frames[col].layers[layer_index],
                        "is_blank_key",
                        False,
                    )
                )
            ]
            minimum_start = 0
            if previous_keys:
                previous_key = previous_keys[-1]
                previous_layer = self.window.canvas.frames[previous_key].layers[layer_index]
                minimum_start = previous_key + max(1, previous_layer.exposure)
            new_start = max(minimum_start, min(boundary_column, old_end))
            if new_start == key_column:
                if self.window.canvas.undo_stack:
                    self.window.canvas.undo_stack.pop()
                return
            self.window.canvas._ensure_frame_count(old_end + 1)
            destination = self.window.canvas.frames[new_start].layers[layer_index]
            if (
                (
                    destination.has_content
                    or getattr(destination, "is_blank_key", False)
                )
                and new_start != key_column
            ):
                if self.window.canvas.undo_stack:
                    self.window.canvas.undo_stack.pop()
                self.window.statusBar().showMessage(
                    tr("左端の移動先に別のコマがあるため伸縮できません。"), 2500
                )
                return
            source = self.window.canvas.frames[key_column].layers[layer_index]
            copied = source.clone()
            destination.image = copied.image.copy()
            destination.has_content = bool(copied.has_content)
            destination.is_blank_key = bool(
                getattr(copied, "is_blank_key", False)
            )
            destination.sequence_number = copied.sequence_number
            destination.cell_name = getattr(copied, "cell_name", None)
            destination.sequence_only = bool(copied.sequence_only)
            destination.exposure = max(1, old_end - new_start + 1)
            destination.visible = copied.visible
            destination.opacity = copied.opacity
            destination.alpha_locked = copied.alpha_locked
            destination.color_filter_enabled = copied.color_filter_enabled
            destination.color_filter_rgb = (
                tuple(copied.color_filter_rgb)
                if copied.color_filter_rgb is not None else None
            )
            if new_start != key_column:
                self.window.canvas._clear_timeline_layer_cell(source)
            self.window.canvas.current_frame = new_start
        else:
            new_end = max(key_column, boundary_column)
            requested_exposure = max(1, new_end - key_column + 1)
            next_keys = [
                col for col in range(key_column + 1, len(self.window.canvas.frames))
                if (
                    self.window.canvas.frames[col].layers[layer_index].has_content
                    or getattr(
                        self.window.canvas.frames[col].layers[layer_index],
                        "is_blank_key",
                        False,
                    )
                )
            ]
            shift = 0
            if next_keys and key_column + requested_exposure > next_keys[0]:
                shift = key_column + requested_exposure - next_keys[0]
            if shift > 0:
                old_count = len(self.window.canvas.frames)
                self.window.canvas._ensure_frame_count(old_count + shift)
                for col in range(old_count - 1, key_column, -1):
                    source = self.window.canvas.frames[col].layers[layer_index]
                    if not (
                        source.has_content
                        or getattr(source, "is_blank_key", False)
                    ):
                        continue
                    destination = self.window.canvas.frames[col + shift].layers[layer_index]
                    destination.image = source.image.copy()
                    destination.has_content = bool(source.has_content)
                    destination.is_blank_key = bool(
                        getattr(source, "is_blank_key", False)
                    )
                    destination.sequence_number = source.sequence_number
                    destination.cell_name = getattr(source, "cell_name", None)
                    destination.sequence_only = bool(source.sequence_only)
                    destination.exposure = source.exposure
                    destination.visible = source.visible
                    destination.opacity = source.opacity
                    destination.alpha_locked = source.alpha_locked
                    destination.color_filter_enabled = source.color_filter_enabled
                    destination.color_filter_rgb = (
                        tuple(source.color_filter_rgb)
                        if source.color_filter_rgb is not None else None
                    )
                    self.window.canvas._clear_timeline_layer_cell(source)
            else:
                self.window.canvas._ensure_frame_count(key_column + requested_exposure)
            self.window.canvas.frames[key_column].layers[layer_index].exposure = requested_exposure
            self.window.canvas.current_frame = key_column

        self.window.canvas.active_layer_index = layer_index
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()

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
        target.image = source.image
        target.has_content = True
        target.is_blank_key = False
        target.sequence_number = copied.sequence_number
        target.cell_name = getattr(copied, "cell_name", None)
        target.sequence_only = False
        target.exposure = max(1, int(copied.exposure))
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
            source = self.window.canvas._sequence_archive.get(
                (layer_index, int(number))
            )
        if source is None:
            return
        self.window.canvas.push_doc_undo()
        self.window.canvas._ensure_frame_count(int(column) + 1)
        target = self.window.canvas.frames[int(column)].layers[layer_index]
        copied = source.clone()
        target.image = source.image
        target.has_content = bool(copied.has_content)
        target.is_blank_key = not bool(copied.has_content)
        target.sequence_number = int(number)
        target.cell_name = getattr(copied, "cell_name", None)
        target.sequence_only = False
        target.exposure = 1
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
        extended = int(column) >= len(self.window.canvas.frames)
        if extended:
            # ルーラーで範囲の外を指したら、そこまでコマを延ばす。
            # 空のコマは尺に数えず、使わなければ後で切り詰められる。
            self.window.canvas._ensure_frame_count(int(column) + 1)
        self.window.canvas.select_exposure(column, visual_row)
        if extended:
            self.window.refresh_ui()
        # タイムラインの別レイヤーのコマを選んだ場合も、そのレイヤーの使用色へ即時更新。
        if self.window.canvas.active_layer_index != previous_layer:
            self.window.used_color._refresh_without_delay()
        else:
            # 同じレイヤーでは全コマ共通の使用色一覧なので、既存表示を維持する。
            self.window.canvas.update()
