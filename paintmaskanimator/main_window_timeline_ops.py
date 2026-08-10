"""Timeline cell/frame/key operations for MainWindow.

Split out of ``main_window.py`` as a mixin. These methods drive timeline
navigation, exposure/number editing, blank-key creation, frame deletion, and
cell move/copy against the timeline widget. They run against a live
``MainWindow`` instance.
"""
from .common import *  # noqa: F401,F403
from ._main_window_members import MainWindowMembers
from .utils import blank_image
from .timeline import TimelineWidget
from .logging_setup import get_logger

log = get_logger(__name__)


class TimelineOpsMixin(MainWindowMembers):
    def set_timeline_mode(self, mode):
        mode = "sheet" if str(mode) == "sheet" else "sequence"
        layer_index = int(self.canvas.active_layer_index)
        selected_number = None
        if self.canvas.frames and 0 <= layer_index < len(self.canvas.layers):
            selected_number = self.canvas.frames[
                self.canvas.current_frame
            ].layers[layer_index].sequence_number
        if self.canvas.timeline_mode == "sequence" and mode == "sheet":
            # 連番専用セルのシート配置ではフレーム数・位置が変わるため、
            # 移動前の状態を文書単位で保存してUndo参照切れを防ぐ。
            if any(
                layer.sequence_only
                for frame in self.canvas.frames
                for layer in frame.layers
            ):
                self.canvas.push_doc_undo()
            self.canvas.apply_sequence_only_entries()
        if selected_number is not None:
            matching = [
                column
                for column, frame in enumerate(self.canvas.frames)
                if (
                    frame.layers[layer_index].sequence_number == selected_number
                    and (frame.layers[layer_index].has_content or frame.layers[layer_index].is_blank_key)
                    and (mode == "sequence" or not frame.layers[layer_index].sequence_only)
                )
            ]
            if matching:
                self.canvas.current_frame = matching[0]
        self.canvas.timeline_mode = mode
        self.timeline.set_timeline_mode(mode)
        self.timeline.sequence_archive = self.canvas._sequence_archive
        self.timeline.add_exposure.setVisible(mode == "sheet")
        # 切り替え前の選択セルを保持すると、キャンバスだけ先に切り替わり
        # 赤枠が旧タブの列へ残る。再構築前に選択を明示的に解除する。
        self.timeline.table.clearSelection()
        self.timeline.refresh(
            self.canvas.frames,
            self.canvas.current_frame,
            self.canvas.active_layer_index,
            getattr(self.canvas, "tween_pending", None),
        )
        self.timeline.select_current(
            self.canvas.current_frame,
            self.canvas.active_layer_index,
        )
        self.timeline.table.viewport().update()

    def _navigate_sequence_number(self, step, wrap=False):
        """シート配置ではなく絵番号順に連番セルを移動する。"""
        layer_index = int(self.canvas.active_layer_index)
        columns = self.canvas.sequence_entry_columns(layer_index)
        entries = sorted(
            (
                int(self.canvas.frames[column].layers[layer_index].sequence_number),
                int(column),
            )
            for column in columns
        )
        if not entries:
            return
        current_layer = self.canvas.frames[
            self.canvas.current_frame
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
        self.canvas.current_frame = entries[target_index][1]
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def previous_timeline_frame(self):
        if self.canvas.timeline_mode == "sequence":
            self._navigate_sequence_number(-1, wrap=False)
        else:
            self.canvas.previous_frame()

    def next_timeline_frame(self):
        if self.canvas.timeline_mode == "sequence":
            self._navigate_sequence_number(1, wrap=False)
        else:
            self.canvas.next_frame()

    def previous_timeline_key(self):
        if self.canvas.timeline_mode == "sequence":
            self._navigate_sequence_number(-1, wrap=True)
        else:
            self.canvas.previous_key_frame()

    def next_timeline_key(self):
        if self.canvas.timeline_mode == "sequence":
            self._navigate_sequence_number(1, wrap=True)
        else:
            self.canvas.next_key_frame()

    def normalize_timeline_numbers(self, visual_rows):
        """選択レイヤーの絵番号をシート順へ振り直す。"""
        if self.canvas.timeline_mode != "sheet":
            return
        layer_count = len(self.canvas.layers)
        layer_indices = sorted({
            layer_count - 1 - int(row)
            for row in visual_rows
            if 0 <= layer_count - 1 - int(row) < layer_count
        })
        if not layer_indices:
            return
        self.canvas.push_doc_undo()
        for layer_index in layer_indices:
            self.canvas.normalize_sequence_numbers(layer_index)
        self.canvas._cell_structure_dirty = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        self.statusBar().showMessage(
            "選択レイヤーの番号をシート順に正規化しました。", 2500
        )

    def create_blank_timeline_key(
        self,
        visual_row,
        column,
    ):
        layer_count = len(self.canvas.layers)
        layer_index = layer_count - 1 - int(visual_row)
        if not (0 <= layer_index < layer_count):
            return
        if self.canvas.timeline_mode == "sequence":
            self.canvas.insert_sequence_blank(
                layer_index,
                int(column) + 1,
            )
            return
        self.canvas.create_blank_key(
            int(column),
            layer_index,
        )

    def delete_timeline_frame(self):
        if self.canvas.timeline_mode == "sequence":
            self.canvas.delete_sequence_entry(
                self.canvas.active_layer_index,
                self.timeline.table.currentColumn() + 1,
            )
            return
        layer_index = int(self.canvas.active_layer_index)
        block = self.canvas.timeline_block_at(
            self.canvas.current_frame, layer_index
        )
        if block is None:
            return
        _kind, start, _exposure = block
        layer = self.canvas.frames[int(start)].layers[layer_index]
        self.canvas.push_doc_undo()
        if layer.has_content and layer.sequence_number is not None:
            self.canvas._sequence_archive[
                (layer_index, int(layer.sequence_number))
            ] = layer.clone()
        self.canvas._clear_timeline_layer_cell(layer)
        self.canvas._cell_structure_dirty = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def _restore_timeline_selection(self, cells):
        table = self.timeline.table
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

    def move_timeline_selection(
        self,
        cells,
        anchor_row,
        anchor_column,
        destination_row,
        destination_column,
    ):
        """複数選択に含まれるコマ塊を相対配置のまま移動する。"""
        if self.canvas.timeline_mode == "sequence":
            if int(anchor_row) != int(destination_row):
                self.statusBar().showMessage(
                    "連番画像は同じレイヤー内で入れ替えてください。", 2500
                )
                return
            layer_count = len(self.canvas.layers)
            layer_index = layer_count - 1 - int(anchor_row)
            self.canvas.move_sequence_image(
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

        layer_count = len(self.canvas.layers)
        blocks = {}
        for visual_row, column in selected_cells:
            layer_index = layer_count - 1 - visual_row
            if not (0 <= layer_index < layer_count):
                continue
            kind, start, exposure = TimelineWidget.timeline_span_at(
                self.canvas.frames,
                layer_index,
                column,
            )
            if (
                kind in ("content", "blank")
                and start is not None
            ):
                key = (layer_index, int(start))
                if key not in blocks:
                    layer = self.canvas.frames[
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
                self.statusBar().showMessage(
                    "移動先がタイムライン範囲外です。",
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

        self.canvas.push_doc_undo()

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
            source = self.canvas.frames[
                source_start
            ].layers[source_layer]
            self.canvas._clear_timeline_layer_cell(source)

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
        self.canvas._ensure_frame_count(maximum_end)

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
            covering = self.canvas.timeline_block_at(
                target_start,
                target_layer,
            )
            if covering is not None:
                _cover_kind, cover_start, _cover_exposure = covering
                if cover_start < target_start:
                    cover_layer = self.canvas.frames[
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
                target = self.canvas.frames[
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
                    self.canvas._clear_timeline_layer_cell(
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
            target = self.canvas.frames[
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

        self.canvas.current_frame = max(
            0,
            int(destination_column),
        )
        self.canvas.active_layer_index = max(
            0,
            min(
                layer_count - 1,
                layer_count - 1 - int(destination_row),
            ),
        )
        self.canvas._cell_structure_dirty = True
        self.canvas._onion_cache.clear()
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        QTimer.singleShot(
            0,
            lambda cells=tuple(moved_cells):
                self._restore_timeline_selection(cells)
        )

    def resize_timeline_exposure(
        self, visual_row, key_column, boundary_column, edge="right"
    ):
        layer_count = len(self.canvas.layers)
        layer_index = layer_count - 1 - int(visual_row)
        key_column = int(key_column)
        boundary_column = int(boundary_column)
        if not (
            0 <= layer_index < layer_count
            and 0 <= key_column < len(self.canvas.frames)
        ):
            return
        layer = self.canvas.frames[key_column].layers[layer_index]
        old_exposure = max(1, int(layer.exposure))
        old_end = key_column + old_exposure - 1
        self.canvas.push_doc_undo()

        if edge == "left":
            previous_keys = [
                col for col in range(0, key_column)
                if (
                    self.canvas.frames[col].layers[layer_index].has_content
                    or getattr(
                        self.canvas.frames[col].layers[layer_index],
                        "is_blank_key",
                        False,
                    )
                )
            ]
            minimum_start = 0
            if previous_keys:
                previous_key = previous_keys[-1]
                previous_layer = self.canvas.frames[previous_key].layers[layer_index]
                minimum_start = previous_key + max(1, previous_layer.exposure)
            new_start = max(minimum_start, min(boundary_column, old_end))
            if new_start == key_column:
                if self.canvas.undo_stack:
                    self.canvas.undo_stack.pop()
                return
            self.canvas._ensure_frame_count(old_end + 1)
            destination = self.canvas.frames[new_start].layers[layer_index]
            if (
                (
                    destination.has_content
                    or getattr(destination, "is_blank_key", False)
                )
                and new_start != key_column
            ):
                if self.canvas.undo_stack:
                    self.canvas.undo_stack.pop()
                self.statusBar().showMessage(
                    "左端の移動先に別のコマがあるため伸縮できません。", 2500
                )
                return
            source = self.canvas.frames[key_column].layers[layer_index]
            copied = source.clone()
            destination.image = copied.image.copy()
            destination.has_content = bool(copied.has_content)
            destination.is_blank_key = bool(
                getattr(copied, "is_blank_key", False)
            )
            destination.sequence_number = copied.sequence_number
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
                self.canvas._clear_timeline_layer_cell(source)
            self.canvas.current_frame = new_start
        else:
            new_end = max(key_column, boundary_column)
            requested_exposure = max(1, new_end - key_column + 1)
            next_keys = [
                col for col in range(key_column + 1, len(self.canvas.frames))
                if (
                    self.canvas.frames[col].layers[layer_index].has_content
                    or getattr(
                        self.canvas.frames[col].layers[layer_index],
                        "is_blank_key",
                        False,
                    )
                )
            ]
            shift = 0
            if next_keys and key_column + requested_exposure > next_keys[0]:
                shift = key_column + requested_exposure - next_keys[0]
            if shift > 0:
                old_count = len(self.canvas.frames)
                self.canvas._ensure_frame_count(old_count + shift)
                for col in range(old_count - 1, key_column, -1):
                    source = self.canvas.frames[col].layers[layer_index]
                    if not (
                        source.has_content
                        or getattr(source, "is_blank_key", False)
                    ):
                        continue
                    destination = self.canvas.frames[col + shift].layers[layer_index]
                    destination.image = source.image.copy()
                    destination.has_content = bool(source.has_content)
                    destination.is_blank_key = bool(
                        getattr(source, "is_blank_key", False)
                    )
                    destination.sequence_number = source.sequence_number
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
                    self.canvas._clear_timeline_layer_cell(source)
            else:
                self.canvas._ensure_frame_count(key_column + requested_exposure)
            self.canvas.frames[key_column].layers[layer_index].exposure = requested_exposure
            self.canvas.current_frame = key_column

        self.canvas.active_layer_index = layer_index
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def move_timeline_cell(self, source_row, source_column, destination_row, destination_column):
        layer_count = len(self.canvas.layers)
        source_layer = layer_count - 1 - source_row
        destination_layer = layer_count - 1 - destination_row
        if self.canvas.timeline_mode == "sequence":
            if source_layer != destination_layer:
                self.statusBar().showMessage(
                    "連番画像は同じレイヤー内で入れ替えてください。", 2500
                )
                return
            self.canvas.move_sequence_image(
                source_layer,
                int(source_column) + 1,
                int(destination_column) + 1,
            )
            return
        self._suppress_used_color_refresh_once = True
        moved = self.canvas.move_timeline_cell(source_column, source_layer, destination_column, destination_layer)
        if not moved:
            self._suppress_used_color_refresh_once = False
            self.statusBar().showMessage("コマを移動できませんでした。", 2500)

    def copy_timeline_cell(self, source_row, source_column, destination_row, destination_column):
        """Altドラッグで、同じ絵番号を参照するシートキーを複製する。"""
        if self.canvas.timeline_mode != "sheet":
            return
        layer_count = len(self.canvas.layers)
        source_layer = layer_count - 1 - int(source_row)
        destination_layer = layer_count - 1 - int(destination_row)
        if source_layer != destination_layer:
            self.statusBar().showMessage("複製は同じレイヤー内で行ってください。", 2500)
            return
        block = self.canvas.timeline_block_at(int(source_column), source_layer)
        if block is None or block[0] != "content":
            return
        source = self.canvas.frames[int(block[1])].layers[source_layer]
        self.canvas.push_doc_undo()
        self.canvas._ensure_frame_count(int(destination_column) + 1)
        target = self.canvas.frames[int(destination_column)].layers[destination_layer]
        copied = source.clone()
        target.image = source.image
        target.has_content = True
        target.is_blank_key = False
        target.sequence_number = copied.sequence_number
        target.sequence_only = False
        target.exposure = max(1, int(copied.exposure))
        target.visible = copied.visible
        target.opacity = copied.opacity
        target.alpha_locked = copied.alpha_locked
        target.color_filter_enabled = copied.color_filter_enabled
        target.color_filter_rgb = copied.color_filter_rgb
        self.canvas.current_frame = int(destination_column)
        self.canvas.active_layer_index = destination_layer
        self.canvas._cell_structure_dirty = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def recall_sequence_number(self, visual_row, column, number):
        """既存の絵番号をシートの指定位置へ再配置する。"""
        if self.canvas.timeline_mode != "sheet":
            return
        layer_count = len(self.canvas.layers)
        layer_index = layer_count - 1 - int(visual_row)
        candidates = self.canvas.sequence_entry_columns(layer_index)
        source = next((
            self.canvas.frames[index].layers[layer_index]
            for index in candidates
            if self.canvas.frames[index].layers[layer_index].sequence_number == int(number)
        ), None)
        if source is None:
            source = self.canvas._sequence_archive.get(
                (layer_index, int(number))
            )
        if source is None:
            return
        self.canvas.push_doc_undo()
        self.canvas._ensure_frame_count(int(column) + 1)
        target = self.canvas.frames[int(column)].layers[layer_index]
        copied = source.clone()
        target.image = source.image
        target.has_content = bool(copied.has_content)
        target.is_blank_key = not bool(copied.has_content)
        target.sequence_number = int(number)
        target.sequence_only = False
        target.exposure = 1
        self.canvas.current_frame = int(column)
        self.canvas.active_layer_index = layer_index
        self.canvas._cell_structure_dirty = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def select_timeline_exposure(self, column, visual_row):
        previous_layer = self.canvas.active_layer_index
        if self.canvas.timeline_mode == "sequence":
            layer_count = len(self.canvas.layers)
            layer_index = layer_count - 1 - int(visual_row)
            number = int(column) + 1
            entries = self.canvas.sequence_entry_columns(layer_index)
            frame_by_number = {
                int(self.canvas.frames[index].layers[layer_index].sequence_number): index
                for index in entries
            }
            if number in frame_by_number:
                self.canvas.current_frame = frame_by_number[number]
                self.canvas.active_layer_index = layer_index
                self.canvas.selectionChanged.emit()
                self.canvas.update()
            return
        self.canvas.select_exposure(column, visual_row)
        # タイムラインの別レイヤーのコマを選んだ場合も、そのレイヤーの使用色へ即時更新。
        if self.canvas.active_layer_index != previous_layer:
            self._refresh_used_colors_without_delay()
        else:
            # 同じレイヤーでは全コマ共通の使用色一覧なので、既存表示を維持する。
            self.canvas.update()
