from pathlib import Path
from .i18n import tr
from PySide6.QtCore import QEvent, QItemSelectionModel, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from . import theme


class LayerListDelegate(QStyledItemDelegate):
    """レイヤー表示記号を固定幅で描画し、名前の位置を変えない。"""

    NAME_ROLE = Qt.ItemDataRole.UserRole + 3
    VISIBLE_ROLE = Qt.ItemDataRole.UserRole + 4
    MARKER_WIDTH = 38

    def paint(self, painter, option, index):
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        background = (
            option.palette.highlight().color()
            if selected else option.palette.base().color()
        )
        foreground = (
            option.palette.highlightedText().color()
            if selected else option.palette.text().color()
        )
        painter.fillRect(option.rect, background)
        painter.setPen(foreground)
        painter.setFont(option.font)

        marker_text = "[●]" if bool(index.data(self.VISIBLE_ROLE)) else "[-]"
        marker_rect = QRectF(
            option.rect.left(),
            option.rect.top(),
            self.MARKER_WIDTH,
            option.rect.height(),
        )
        painter.drawText(
            marker_rect,
            Qt.AlignmentFlag.AlignCenter,
            marker_text,
        )

        name = index.data(self.NAME_ROLE)
        if name is None:
            name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        name_rect = QRectF(
            option.rect.left() + self.MARKER_WIDTH,
            option.rect.top(),
            max(0, option.rect.width() - self.MARKER_WIDTH - 4),
            option.rect.height(),
        )
        painter.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            str(name),
        )

        painter.setPen(QPen(option.palette.mid().color(), 1))
        painter.drawLine(
            option.rect.left(),
            option.rect.bottom(),
            option.rect.right(),
            option.rect.bottom(),
        )
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(190, 28)

    def updateEditorGeometry(self, editor, option, index):
        """表示切替記号を避け、レイヤー名の領域だけを編集欄にする。"""
        editor.setGeometry(
            option.rect.adjusted(self.MARKER_WIDTH, 1, -4, -1)
        )

    def eventFilter(self, editor, event):
        # MainWindow の「Enter＝変形確定」より名前編集の確定を優先する。
        if (
            event.type() == QEvent.Type.ShortcutOverride
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            event.accept()
            return True
        return super().eventFilter(editor, event)


class TimelineCellDelegate(QStyledItemDelegate):
    """タイムラインセルの背景色をスタイルシートに上書きされず描画する。"""
    STATE_ROLE = Qt.ItemDataRole.UserRole + 20

    @staticmethod
    def _state_colors(state_name):
        c = theme.palette()
        key = state_name if state_name in (
            "key", "hold", "sheet_key", "sheet_hold", "blank", "uncreated"
        ) else "uncreated"
        return QColor(c[f"timeline_{key}"]), QColor(c[f"timeline_{key}_text"])

    def paint(self, painter, option, index):
        state_name = index.data(self.STATE_ROLE) or "uncreated"
        painter.save()
        background, foreground = self._state_colors(state_name)
        painter.fillRect(option.rect, background)
        painter.setFont(index.data(Qt.ItemDataRole.FontRole) or option.font)
        painter.setPen(foreground)
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, str(text))
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(QPen(QColor(theme.palette()["error"]), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(option.rect.adjusted(1, 1, -1, -1))
        painter.restore()


class TimelineTable(QTableWidget):
    cellMoveRequested = Signal(int, int, int, int)
    cellCopyRequested = Signal(int, int, int, int)
    sequenceRecallRequested = Signal(int, int, int)
    multiCellMoveRequested = Signal(object, int, int, int, int)
    blankFrameRequested = Signal(int, int)
    exposureResizeRequested = Signal(int, int, int, str)
    headerFrameRequested = Signal(int)
    tweenRequested = Signal(int, int)
    tweenMeshRequested = Signal(int, int)
    tweenCancelRequested = Signal()
    addFrameRequested = Signal(bool)
    extendExposureRequested = Signal(int, int, int)

    END_HANDLE_ROLE = Qt.ItemDataRole.UserRole + 2
    START_HANDLE_ROLE = Qt.ItemDataRole.UserRole + 4
    TWEEN_PENDING_ROLE = Qt.ItemDataRole.UserRole + 21
    HANDLE_HIT_WIDTH = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        # Mirrors the owning TimelineWidget's frame count; the widget writes it
        # (``self.table._real_frame_count = ...``) and cell-hit tests read it.
        self._real_frame_count = 1
        self._drag_source = None
        self._drag_preview_dest = None
        self._duplicate_drag = False
        self._sequence_numbers_by_row = {}
        self._group_drag_cells = None
        self._group_drag_anchor = None
        self._group_drag_dest = None
        self._resize_source = None
        self._resize_preview_column = None
        self.setMouseTracking(True)
        self.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.horizontalHeader().setMouseTracking(True)
        self.horizontalHeader().installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.horizontalHeader():
            if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove):
                buttons = event.buttons() if hasattr(event, "buttons") else Qt.MouseButton.NoButton
                pressed = (
                    event.type() == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton
                )
                dragging = (
                    event.type() == QEvent.Type.MouseMove
                    and bool(buttons & Qt.MouseButton.LeftButton)
                )
                if pressed or dragging:
                    column = self.horizontalHeader().logicalIndexAt(
                        int(event.position().x())
                    )
                    if 0 <= column < self._real_frame_count:
                        self.headerFrameRequested.emit(column)
                        return True
        return super().eventFilter(watched, event)

    def _edge_source_at(self, point):
        index = self.indexAt(point)
        if not index.isValid():
            return None
        item = self.item(index.row(), index.column())
        if item is None:
            return None
        key_col = item.data(Qt.ItemDataRole.UserRole)
        exposure = item.data(Qt.ItemDataRole.UserRole + 1)
        if key_col is None or exposure is None:
            return None
        key_col = int(key_col)
        exposure = max(1, int(exposure))
        end_col = key_col + exposure - 1
        rect = self.visualRect(index)
        x = point.x()
        if index.column() == key_col and x <= rect.left() + self.HANDLE_HIT_WIDTH:
            return (index.row(), key_col, "left")
        if index.column() == end_col and x >= rect.right() - self.HANDLE_HIT_WIDTH:
            return (index.row(), key_col, "right")
        return None

    def _column_from_x(self, x):
        column = self.columnAt(int(x))
        if column >= 0:
            return column
        count = self.columnCount()
        if count <= 0:
            return 0
        if x < 0:
            return 0
        last = count - 1
        last_right = self.columnViewportPosition(last) + self.columnWidth(last)
        if x >= last_right:
            width = max(1, self.columnWidth(last))
            return last + 1 + int((x - last_right) // width)
        return last

    def _set_cell_drag_cursor(self):
        """ALT複製中だけ、右下に＋の付いたコピーカーソルを表示する。"""
        self.setCursor(
            Qt.CursorShape.DragCopyCursor
            if self._duplicate_drag
            else Qt.CursorShape.ClosedHandCursor
        )

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        self._drag_source = None
        self._drag_preview_dest = None
        self._duplicate_drag = False
        self._group_drag_cells = None
        self._group_drag_anchor = None
        self._group_drag_dest = None
        self._resize_source = None
        self._resize_preview_column = None

        if event.button() == Qt.MouseButton.LeftButton and index.isValid():
            modifiers = event.modifiers()
            self._duplicate_drag = bool(
                modifiers & Qt.KeyboardModifier.AltModifier
            )
            multi_select = bool(
                modifiers
                & (
                    Qt.KeyboardModifier.ShiftModifier
                    | Qt.KeyboardModifier.ControlModifier
                )
            )

            selected = {
                (selected_index.row(), selected_index.column())
                for selected_index in self.selectedIndexes()
                if selected_index.isValid()
            }
            clicked = (index.row(), index.column())

            # 複数選択済みの範囲をそのままドラッグする。
            if (
                not multi_select
                and clicked in selected
                and len(selected) > 1
            ):
                self._group_drag_cells = tuple(sorted(selected))
                self._group_drag_anchor = clicked
                self._group_drag_dest = clicked
                self.setCurrentCell(
                    index.row(),
                    index.column(),
                    QItemSelectionModel.SelectionFlag.NoUpdate,
                )
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return

            edge_source = (
                None
                if multi_select
                else self._edge_source_at(
                    event.position().toPoint()
                )
            )
            if edge_source is not None:
                self._resize_source = edge_source
                self._resize_preview_column = edge_source[1]
                self.setCurrentCell(edge_source[0], edge_source[1])
                self.cellClicked.emit(edge_source[0], edge_source[1])
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                event.accept()
                return

            item = self.item(index.row(), index.column())
            if (
                not multi_select
                and item
                and (
                    item.data(TimelineCellDelegate.STATE_ROLE)
                    in ("key", "sheet_key")
                    or item.text() == "○"
                )
            ):
                self._drag_source = (index.row(), index.column())
                self._drag_preview_dest = self._drag_source
                self.setCurrentCell(index.row(), index.column())
                self.cellClicked.emit(index.row(), index.column())
                self._set_cell_drag_cursor()
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._group_drag_cells is not None:
            index = self.indexAt(event.position().toPoint())
            if index.isValid():
                self._group_drag_dest = (
                    index.row(),
                    index.column(),
                )
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.viewport().update()
            event.accept()
            return
        if self._resize_source is not None:
            self._resize_preview_column = self._column_from_x(event.position().x())
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            self.viewport().update()
            event.accept()
            return
        if self._drag_source is not None:
            index = self.indexAt(event.position().toPoint())
            if index.isValid():
                self._drag_preview_dest = (index.row(), index.column())
            self._set_cell_drag_cursor()
            self.viewport().update()
            event.accept()
            return
        edge = self._edge_source_at(event.position().toPoint())
        if edge is not None:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            index = self.indexAt(event.position().toPoint())
            item = self.item(index.row(), index.column()) if index.isValid() else None
            self.setCursor(
                Qt.CursorShape.OpenHandCursor
                if item and (
                    item.data(TimelineCellDelegate.STATE_ROLE)
                    in ("key", "sheet_key")
                    or item.text() == "○"
                )
                else Qt.CursorShape.ArrowCursor
            )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if (
            self._resize_source is None
            and self._drag_source is None
            and self._group_drag_cells is None
        ):
            self.unsetCursor()
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        if self._resize_source is not None and self._resize_preview_column is not None:
            row, key_col, edge = self._resize_source
            item = self.item(row, key_col)
            exposure = max(
                1,
                int(item.data(Qt.ItemDataRole.UserRole + 1) or 1)
                if item is not None else 1,
            )
            fixed_end = key_col + exposure - 1
            if edge == "left":
                start_col = max(0, min(self._resize_preview_column, fixed_end))
                end_col = fixed_end
            else:
                start_col = key_col
                end_col = max(key_col, self._resize_preview_column)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 215, 0, 110))
            for column in range(start_col, min(end_col + 1, self.columnCount())):
                index = self.model().index(row, column)
                if index.isValid():
                    painter.drawRect(self.visualRect(index).adjusted(1, 1, -1, -1))
        if (
            self._group_drag_cells is not None
            and self._group_drag_anchor is not None
            and self._group_drag_dest is not None
        ):
            row_delta = (
                self._group_drag_dest[0]
                - self._group_drag_anchor[0]
            )
            column_delta = (
                self._group_drag_dest[1]
                - self._group_drag_anchor[1]
            )
            painter.setPen(QPen(QColor(229, 57, 53), 2))
            painter.setBrush(QColor(255, 215, 0, 65))
            for source_row, source_column in self._group_drag_cells:
                row = source_row + row_delta
                column = source_column + column_delta
                index = self.model().index(row, column)
                if index.isValid():
                    painter.drawRect(
                        self.visualRect(index).adjusted(1, 1, -1, -1)
                    )

        if self._drag_source is not None and self._drag_preview_dest is not None:
            row, column = self._drag_preview_dest
            index = self.model().index(row, column)
            if index.isValid():
                rect = self.visualRect(index)
                painter.setPen(QPen(QColor(229, 57, 53), 3))
                painter.drawLine(rect.left(), rect.top() + 1, rect.left(), rect.bottom() - 1)
        painter.end()

    def mouseReleaseEvent(self, event):
        group_cells = self._group_drag_cells
        group_anchor = self._group_drag_anchor
        group_destination = self._group_drag_dest
        self._group_drag_cells = None
        self._group_drag_anchor = None
        self._group_drag_dest = None

        if (
            group_cells
            and group_anchor
            and group_destination
            and event.button() == Qt.MouseButton.LeftButton
        ):
            if group_destination != group_anchor:
                self.multiCellMoveRequested.emit(
                    group_cells,
                    int(group_anchor[0]),
                    int(group_anchor[1]),
                    int(group_destination[0]),
                    int(group_destination[1]),
                )
            self.viewport().update()
            self.unsetCursor()
            event.accept()
            return

        resize_source = self._resize_source
        resize_column = self._resize_preview_column
        self._resize_source = None
        self._resize_preview_column = None
        self.viewport().update()
        if resize_source and event.button() == Qt.MouseButton.LeftButton:
            row = self.rowAt(int(event.position().y()))
            if row < 0:
                row = resize_source[0]
            if row == resize_source[0]:
                boundary = (
                    self._column_from_x(event.position().x())
                    if resize_column is None else int(resize_column)
                )
                self.exposureResizeRequested.emit(
                    resize_source[0], resize_source[1], boundary, resize_source[2]
                )
                self.unsetCursor()
                event.accept()
                return

        source = self._drag_source
        destination = self._drag_preview_dest
        duplicate_drag = self._duplicate_drag
        self._drag_source = None
        self._drag_preview_dest = None
        self._duplicate_drag = False
        self.viewport().update()
        if source and destination and event.button() == Qt.MouseButton.LeftButton:
            if destination != source:
                signal = (
                    self.cellCopyRequested
                    if duplicate_drag
                    else self.cellMoveRequested
                )
                signal.emit(
                    source[0], source[1], destination[0], destination[1]
                )
            self.unsetCursor()
            event.accept()
            return
        self.unsetCursor()
        super().mouseReleaseEvent(event)


    def contextMenuEvent(self, event):
        index = self.indexAt(event.pos())
        item = (
            self.item(index.row(), index.column())
            if index.isValid() else None
        )

        menu = QMenu(self)
        add_menu = menu.addMenu(tr("コマを増やす"))
        action_add_blank = add_menu.addAction(
            tr("空フレームを追加")
        )
        action_extend = add_menu.addAction(
            tr("表示コマを1コマ伸ばす")
        )
        key_column = (
            item.data(Qt.ItemDataRole.UserRole)
            if item is not None else None
        )
        exposure = (
            item.data(Qt.ItemDataRole.UserRole + 1)
            if item is not None else None
        )
        action_extend.setEnabled(
            index.isValid()
            and key_column is not None
            and exposure is not None
        )

        action_cancel = None
        action_free = None
        action_mesh = None
        recall_actions = {}
        if index.isValid():
            numbers = self._sequence_numbers_by_row.get(int(index.row()), ())
            if numbers:
                recall_menu = menu.addMenu(tr("連番の番号を呼び出す"))
                for number in numbers:
                    recall_actions[recall_menu.addAction(str(number))] = int(number)

        item_tween_pending = bool(
            item is not None
            and item.data(
                TimelineTable.TWEEN_PENDING_ROLE
            )
        )
        if (
            item is not None
            and (
                item.text() in ("→", "♦", "◆")
                or item_tween_pending
            )
        ):
            menu.addSeparator()
            if (
                item_tween_pending
                or item.text() in ("♦", "◆")
            ):
                action_cancel = menu.addAction(
                    tr("トゥイーンをキャンセル")
                )
            else:
                key_col = item.data(Qt.ItemDataRole.UserRole)
                exposure = item.data(
                    Qt.ItemDataRole.UserRole + 1
                )
                tween_menu = menu.addMenu(
                    tr("トゥイーンを有効にする")
                )
                action_free = tween_menu.addAction(tr("自由変形"))
                action_mesh = tween_menu.addAction(tr("メッシュ変形"))
                tween_enabled = (
                    key_col is not None
                    and exposure is not None
                    and int(exposure) >= 2
                )
                action_free.setEnabled(tween_enabled)
                action_mesh.setEnabled(tween_enabled)

        chosen = menu.exec(event.globalPos())
        if chosen is action_add_blank:
            if index.isValid():
                self.blankFrameRequested.emit(
                    int(index.row()),
                    int(index.column()),
                )
        elif chosen is action_extend and key_column is not None and exposure is not None:
            self.extendExposureRequested.emit(
                int(index.row()),
                int(key_column),
                int(exposure),
            )
        elif action_cancel is not None and chosen is action_cancel:
            self.tweenCancelRequested.emit()
        elif action_free is not None and chosen is action_free and item is not None:
            self.tweenRequested.emit(
                int(index.row()),
                int(item.data(Qt.ItemDataRole.UserRole)),
            )
        elif action_mesh is not None and chosen is action_mesh and item is not None:
            self.tweenMeshRequested.emit(
                int(index.row()),
                int(item.data(Qt.ItemDataRole.UserRole)),
            )
        elif chosen in recall_actions:
            self.sequenceRecallRequested.emit(
                int(index.row()), int(index.column()), recall_actions[chosen]
            )
        event.accept()


class TimelineWidget(QWidget):
    frameSelected=Signal(int,int); addFrameRequested=Signal(bool); deleteFrameRequested=Signal(); playRequested=Signal(bool)
    onionChanged=Signal(bool); onionAllLayersChanged=Signal(bool); onionPopupToggled=Signal(bool); durationChanged=Signal(int); previousRequested=Signal(); nextRequested=Signal(); previousKeyRequested=Signal(); nextKeyRequested=Signal()
    layerSelected=Signal(int); layerVisibilityChanged=Signal(int,bool); layerOpacityChanged=Signal(int,float); layerNameChanged=Signal(int,str); layerMoveRequested=Signal(object,int); addLayerRequested=Signal(); deleteLayerRequested=Signal()
    duplicateLayersRequested=Signal(object); mergeLayersRequested=Signal(object); deleteLayersRequested=Signal(object)
    cellMoveRequested=Signal(int,int,int,int)
    cellCopyRequested=Signal(int,int,int,int)
    sequenceRecallRequested=Signal(int,int,int)
    multiCellMoveRequested=Signal(object,int,int,int,int)
    blankFrameRequested=Signal(int,int)
    exposureResizeRequested=Signal(int,int,int,str)
    tweenRequested=Signal(int,int)
    tweenMeshRequested=Signal(int,int)
    tweenCancelRequested=Signal()
    timeRemapPasteRequested=Signal()
    timeRemapFileDropped=Signal(str)
    extendExposureRequested=Signal(int,int,int)
    timelineModeChanged=Signal(str)
    normalizeNumbersRequested=Signal(object)
    toggleDraftLayersRequested=Signal(object)
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.sequence_archive = {}
        self._timeline_zoom_scale = 1.0
        self._timeline_base_cell_width = 36
        self._timeline_base_row_height = 28
        v=QVBoxLayout(self)
        v.setContentsMargins(2,2,2,2)
        v.setSpacing(2)
        c=QHBoxLayout()
        c.setContentsMargins(0,0,0,0)
        c.setSpacing(2)

        self.add_blank = QPushButton(tr("+空"))
        self.add_blank.setToolTip(
            tr("●／○の開始セルでは直後へ同じ長さの○を挿入。"
            "ー部分では選択位置から後半を○へ分割します。")
        )
        self.add_exposure = QPushButton(tr("+コマ"))
        self.add_exposure.setToolTip(
            tr("現在のキーフレーム／空フレームを1コマ伸ばします。")
        )
        self.delete=QPushButton(tr("削除"))
        self.delete.setToolTip(tr("現在の表示コマを1コマ削除します。"))
        self.time_remap_paste=QPushButton(tr("リマップ"))
        self.time_remap_paste.setToolTip(
            tr("AEまたはToeiDigitalTimeSheetのコピー情報を"
            "タイムシートへ貼り付けます。XDTSはタイムラインへ"
            "ドラッグ＆ドロップできます。")
        )
        self.prev=QPushButton("◀F")
        self.prev.setToolTip(tr("前のフレーム（1）"))
        self.next=QPushButton("F▶")
        self.next.setToolTip(tr("次のフレーム（2）"))
        self.prev_key=QPushButton("◀K")
        self.prev_key.setToolTip(tr("前のコマ（A）"))
        self.next_key=QPushButton("K▶")
        self.next_key.setToolTip(tr("次のコマ（S）"))
        self.play=QPushButton(tr("再生"))
        self.play.setToolTip(tr("再生／停止"))
        self.play.setCheckable(True)

        self.onion_all_layers=QCheckBox(tr("すべてのレイヤー"))
        self.onion_all_layers.setChecked(True)
        self.onion=QCheckBox(tr("オニオンスキン"))
        self.onion.setToolTip(tr("オニオンスキン表示のON／OFF"))
        self.onion_settings=QPushButton(tr("設定"))
        self.onion_settings.setCheckable(True)
        self.onion_settings.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.onion_settings.setToolTip(
            tr("クリックでオニオンスキン設定を開き、"
            "再クリックで閉じます。")
        )
        self.onion_settings.setStyleSheet(
            "QPushButton{padding:1px 5px;font-size:10px;}"
            "QPushButton:checked{background:#d7eef8;"
            "border:1px solid #4a9fc5;}"
        )
        self.duration=QSpinBox(); self.duration.setRange(1,240); self.duration.setSuffix(tr(" コマ"))
        self.duration.hide()
        self.fps=QSpinBox(); self.fps.setRange(1,60); self.fps.setValue(24); self.fps.setSuffix(" fps")
        self.fps.setFixedSize(68,22)

        compact_buttons = (
            self.add_blank,
            self.add_exposure,
            self.delete,
            self.time_remap_paste,
            self.prev,
            self.prev_key,
            self.play,
            self.next_key,
            self.next,
        )
        compact_widths = (
            36, 44, 36, 48, 32, 32, 40, 32, 32,
        )
        for button, width in zip(
            compact_buttons,
            compact_widths,
        ):
            button.setFixedSize(width,22)
            button.setStyleSheet(
                "QPushButton{padding:0px 2px;font-size:10px;}"
            )
        self.onion_settings.setFixedHeight(22)
        self.onion.setFixedHeight(22)

        for widget in (
            self.add_blank,
            self.add_exposure,
            self.delete,
            self.time_remap_paste,
            self.prev,
            self.prev_key,
            self.play,
            self.next_key,
            self.next,
            self.onion,
            self.onion_settings,
        ):
            c.addWidget(widget)
        self.onion_all_layers.hide()
        self.layer_opacity_text = QLabel(tr("不透明"))
        self.layer_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.layer_opacity_slider.setRange(0, 100)
        self.layer_opacity_slider.setValue(100)
        self.layer_opacity_slider.setFixedWidth(84)
        self.layer_opacity_slider.setToolTip(
            tr("選択レイヤーの表示不透明度です。画像の色データ自体は変更しません。")
        )
        # レイヤー不透明度は操作列の一番左に固定する。
        c.insertWidget(0, self.layer_opacity_slider)
        c.insertWidget(0, self.layer_opacity_text)
        self.layer_opacity_value = QLabel("100%")
        self.layer_opacity_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.layer_opacity_value.setFixedWidth(38)
        self.layer_opacity_value.setToolTip(
            tr("現在選択しているレイヤーの表示不透明度")
        )

        c.addStretch()
        compact_hint = QLabel(tr("Shift/Ctrl：複数選択"))
        self.compact_hint = compact_hint
        compact_hint.setToolTip(
            tr("ドラッグ・Shift＋クリック：複数選択／"
            "選択範囲をそのままドラッグ：まとめて移動／"
            "●・○中央：移動／左右端：伸縮／"
            "Space：ハンド／Ctrl＋Space：拡大縮小")
        )
        c.addWidget(compact_hint)
        c.addWidget(self.fps)
        c.addWidget(self.layer_opacity_value)
        v.addLayout(c)
        self.mode_tabs = QTabBar()
        self.mode_tabs.addTab(tr("シート"))
        self.mode_tabs.addTab(tr("連番"))
        self.mode_tabs.setCurrentIndex(0)
        self.mode_tabs.setExpanding(False)
        self.mode_tabs.setDrawBase(True)
        self.mode_tabs.setToolTip(
            tr("連番：左から順番に自動採番／"
            "シート：タイムシートの絵番号を保持")
        )
        self.timeline_mode = "sheet"
        self.mode_tabs.currentChanged.connect(
            self._timeline_mode_tab_changed
        )
        self._update_timeline_mode_tab_style()
        v.addWidget(self.mode_tabs)
        body=QHBoxLayout(); body.setContentsMargins(0,0,0,0); body.setSpacing(0)
        self.setMinimumHeight(82)
        self.layer_list=QListWidget()
        self.layer_list.setFixedWidth(190)
        self.layer_list.setMinimumHeight(70)
        self.layer_list.setFrameShape(QListWidget.Shape.NoFrame)
        self.layer_list.setItemDelegate(LayerListDelegate(self.layer_list))
        self.layer_list.setContentsMargins(0,0,0,0)
        self.layer_list.setSpacing(0)
        self.layer_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.layer_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.layer_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ContiguousSelection
        )
        self.layer_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.layer_list.customContextMenuRequested.connect(
            self._show_layer_context_menu
        )
        self.layer_list.viewport().installEventFilter(self)
        self._active_layer_index = 0

        # レイヤー名とタイムラインを同一面として見せる。
        # タイムライン見出しと同じ高さのヘッダー内に追加・削除ボタンを置き、
        # その直下からレイヤー名とタイムライン行を隙間なく隣接させる。
        layer_box=QVBoxLayout()
        layer_box.setContentsMargins(0,0,0,0)
        layer_box.setSpacing(0)
        self.layer_header_spacer = QWidget()
        self.layer_header_spacer.setFixedHeight(25)
        self.layer_header_spacer.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        layer_header_layout = QHBoxLayout(self.layer_header_spacer)
        layer_header_layout.setContentsMargins(2,0,2,0)
        layer_header_layout.setSpacing(2)
        layer_header_layout.addWidget(QLabel(tr("レイヤー")))
        layer_header_layout.addStretch()
        self.layer_add=QPushButton("＋")
        self.layer_del=QPushButton("－")
        for button in (self.layer_add, self.layer_del):
            button.setFixedSize(28,23)
            button.setContentsMargins(0,0,0,0)
        layer_header_layout.addWidget(self.layer_add)
        layer_header_layout.addWidget(self.layer_del)
        layer_box.addWidget(self.layer_header_spacer)
        layer_box.addWidget(self.layer_list,1)

        layer_widget=QWidget()
        layer_widget.setContentsMargins(0,0,0,0)
        layer_widget.setLayout(layer_box)
        self.layer_widget = layer_widget
        body.addWidget(layer_widget,0)

        self.table=TimelineTable()
        self.table.setAcceptDrops(False)
        self.table.viewport().setAcceptDrops(False)
        self.table.setItemDelegate(TimelineCellDelegate(self.table))
        self.table.setRowCount(1)
        self.table.setColumnCount(1)
        self.table.setMinimumHeight(70)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.table.setFrameShape(QTableWidget.Shape.NoFrame)
        self.table.setContentsMargins(0,0,0,0)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionsMovable(False)
        self.table.verticalHeader().setSectionsMovable(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)

        # どちらを縦スクロールしてもレイヤー名とタイムライン行を同時に動かす。
        self.table.verticalScrollBar().valueChanged.connect(
            self.layer_list.verticalScrollBar().setValue
        )
        self.layer_list.verticalScrollBar().valueChanged.connect(
            self.table.verticalScrollBar().setValue
        )

        self._sequence_frame_by_cell = {}
        self.table.cellClicked.connect(self._select_table_cell)
        self._real_frame_count = 1
        self.table._real_frame_count = 1
        self.table.headerFrameRequested.connect(self._scrub_header_frame)
        self.table.cellMoveRequested.connect(self.cellMoveRequested)
        self.table.cellCopyRequested.connect(self.cellCopyRequested)
        self.table.sequenceRecallRequested.connect(self.sequenceRecallRequested)
        self.table.multiCellMoveRequested.connect(
            self.multiCellMoveRequested
        )
        self.table.blankFrameRequested.connect(
            self.blankFrameRequested
        )
        self.table.exposureResizeRequested.connect(self.exposureResizeRequested)
        self.table.tweenRequested.connect(self.tweenRequested)
        self.table.tweenMeshRequested.connect(self.tweenMeshRequested)
        self.table.tweenCancelRequested.connect(self.tweenCancelRequested)
        self.table.addFrameRequested.connect(
            self.addFrameRequested
        )
        self.table.extendExposureRequested.connect(
            self.extendExposureRequested
        )
        body.addWidget(self.table,1)
        v.addLayout(body)
        self.layer_list.currentRowChanged.connect(self._layer_selected)
        self.layer_list.model().rowsMoved.connect(self._layer_rows_moved)
        self.layer_list.itemChanged.connect(self._layer_item_changed)
        self.layer_list.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
        )
        self.layer_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.layer_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.layer_add.clicked.connect(self.addLayerRequested); self.layer_del.clicked.connect(self.deleteLayerRequested)
        self.add_blank.clicked.connect(
            self._create_blank_at_current
        )
        self.add_exposure.clicked.connect(
            self._extend_current_exposure
        )
        self.delete.clicked.connect(self.deleteFrameRequested)
        self.time_remap_paste.clicked.connect(
            self.timeRemapPasteRequested
        )
        self.prev.clicked.connect(self.previousRequested); self.next.clicked.connect(self.nextRequested)
        self.prev_key.clicked.connect(self.previousKeyRequested); self.next_key.clicked.connect(self.nextKeyRequested)
        self.play.toggled.connect(self._play)
        self.onion_all_layers.toggled.connect(self.onionAllLayersChanged)
        self.onion.toggled.connect(self._on_onion_toggled)
        self.onion_settings.toggled.connect(
            self.onionPopupToggled
        )
        self.duration.valueChanged.connect(self.durationChanged)
        self.layer_opacity_slider.valueChanged.connect(
            self._layer_opacity_slider_changed
        )
        self.apply_theme()

    def apply_theme(self):
        """Palette-aware styling for the timeline surfaces.

        Keeps the timeline's design (compact rows, red selected-cell frame,
        amber/blue mode tabs) but sources neutral colours from the active theme
        so it reads well in both light and dark, with a touch more polish.
        """
        c = theme.palette()
        panel = "border:0px;margin:0px;padding:0px;background:%s;" % c["surface_alt"]
        self.layer_widget.setStyleSheet(panel)
        self.layer_header_spacer.setStyleSheet(panel)
        self.layer_list.setStyleSheet(
            "QListWidget{border:0px;margin:0px;padding:0px;background:%s;outline:0;}"
            "QListWidget::item{border:0px;padding:2px 0px;background:%s;color:%s;}"
            "QListWidget::item:selected{background:%s;color:%s;}"
            % (
                c["surface_alt"], c["surface_alt"], c["text"],
                c["selection"], c["text"],
            )
        )
        self.table.setStyleSheet(
            "QTableWidget{border:0px;margin:0px;padding:0px;"
            "gridline-color:%s;background:%s;}"
            "QHeaderView{border:0px;margin:0px;padding:0px;}"
            "QHeaderView::section{background:%s;border:0px;"
            "border-bottom:1px solid %s;border-right:1px solid %s;"
            "padding:2px;color:%s;}"
            "QTableWidget::item{border:0px;color:%s;}"
            "QTableWidget::item:selected{background:transparent;color:%s;"
            "border:2px solid %s;}"
            % (
                c["border"], c["surface"],
                c["surface_alt"], c["border"], c["border"], c["text"],
                c["text"], c["text"], c["error"],
            )
        )
        self.compact_hint.setStyleSheet(
            "font-size:9px;color:%s;" % c["text_muted"]
        )
        self.layer_opacity_value.setStyleSheet(
            "font-size:10px;color:%s;padding:0px;" % c["text_muted"]
        )
        self._update_timeline_mode_tab_style()
        self.table.viewport().update()
        self.layer_list.viewport().update()

    def _timeline_mode_tab_changed(self, index):
        self.timeline_mode = (
            "sequence" if int(index) == 1 else "sheet"
        )
        self._update_timeline_mode_tab_style()
        self._sync_timeline_mode_controls()
        self.timelineModeChanged.emit(self.timeline_mode)

    def _sync_timeline_mode_controls(self):
        sequence_mode = self.timeline_mode == "sequence"
        self.add_exposure.setVisible(not sequence_mode)
        self.add_blank.setToolTip(
            tr("選択番号の直後へ、新しい空の番号画像を追加します。")
            if sequence_mode
            else
            tr("●／○の開始セルでは直後へ同じ長さの○を挿入。"
            "ー部分では選択位置から後半を○へ分割します。")
        )
        self.delete.setToolTip(
            tr("選択番号を削除し、シート側の対応セルを未使用にします。")
            if sequence_mode
            else tr("現在の表示コマを1コマ削除します。")
        )

    def _update_timeline_mode_tab_style(self):
        if self.timeline_mode == "sheet":
            selected_background = "#E5AD18"
            selected_border = "#9B6C00"
            selected_foreground = "#241900"
        else:
            selected_background = "#2F83B8"
            selected_border = "#155E8A"
            selected_foreground = "#FFFFFF"
        c = theme.palette()
        self.mode_tabs.setStyleSheet(
            "QTabBar::tab{background:%s;color:%s;"
            "border:1px solid %s;border-bottom:1px solid %s;"
            "padding:4px 18px;min-width:58px;"
            "border-top-left-radius:6px;border-top-right-radius:6px;}"
            % (c["surface_alt"], c["text_muted"], c["border"], c["border"])
            + "QTabBar::tab:selected{"
            f"background:{selected_background};"
            f"color:{selected_foreground};"
            f"border:2px solid {selected_border};"
            "font-weight:bold;padding:3px 17px;}"
            "QTabBar::tab:!selected{margin-top:3px;}"
        )

    def set_timeline_mode(self, mode):
        mode = "sheet" if str(mode) == "sheet" else "sequence"
        self.timeline_mode = mode
        target_index = 1 if mode == "sequence" else 0
        if self.mode_tabs.currentIndex() != target_index:
            self.mode_tabs.blockSignals(True)
            self.mode_tabs.setCurrentIndex(target_index)
            self.mode_tabs.blockSignals(False)
        self._update_timeline_mode_tab_style()
        self._sync_timeline_mode_controls()

    def timeline_cell_metrics(self):
        scale = max(0.5, min(3.0, float(self._timeline_zoom_scale)))
        return (
            max(18, int(round(self._timeline_base_cell_width * scale))),
            max(20, int(round(self._timeline_base_row_height * scale))),
        )

    def set_timeline_zoom_scale(self, scale, anchor_x=None, anchor_y=None):
        old_width, old_height = self.timeline_cell_metrics()
        new_scale = max(0.5, min(3.0, float(scale)))
        if abs(new_scale - self._timeline_zoom_scale) < 1e-6:
            return
        anchor_x = float(
            self.table.viewport().width() / 2
            if anchor_x is None else anchor_x
        )
        anchor_y = float(
            self.table.viewport().height() / 2
            if anchor_y is None else anchor_y
        )
        horizontal = self.table.horizontalScrollBar()
        vertical = self.table.verticalScrollBar()
        horizontal_position = (
            horizontal.value() + anchor_x
        ) / float(max(1, old_width))
        vertical_position = (
            vertical.value() + anchor_y
        ) / float(max(1, old_height))

        self._timeline_zoom_scale = new_scale
        new_width, new_height = self.timeline_cell_metrics()
        self.table.horizontalHeader().setDefaultSectionSize(new_width)
        self.table.verticalHeader().setDefaultSectionSize(new_height)
        for column in range(self.table.columnCount()):
            self.table.setColumnWidth(column, new_width)
        for row in range(self.table.rowCount()):
            self.table.setRowHeight(row, new_height)
        for index in range(self.layer_list.count()):
            item = self.layer_list.item(index)
            if item is not None:
                item.setSizeHint(QSize(0, new_height))

        horizontal.setValue(
            int(round(horizontal_position * new_width - anchor_x))
        )
        vertical.setValue(
            int(round(vertical_position * new_height - anchor_y))
        )
        self.table.viewport().update()
        self.layer_list.viewport().update()

    def adjust_timeline_zoom(self, factor, anchor_x=None, anchor_y=None):
        self.set_timeline_zoom_scale(
            self._timeline_zoom_scale * float(factor),
            anchor_x,
            anchor_y,
        )

    def _create_blank_at_current(self):
        """現在のタイムライン位置へ明示的な○を作る。"""
        row = self.table.currentRow()
        column = self.table.currentColumn()

        if row < 0:
            active = int(
                getattr(self, "_active_layer_index", 0)
            )
            row = max(
                0,
                self.table.rowCount() - 1 - active,
            )
        if column < 0:
            column = 0

        if (
            row >= self.table.rowCount()
            or column >= self.table.columnCount()
        ):
            return

        self.blankFrameRequested.emit(
            int(row),
            int(column),
        )

    def _extend_current_exposure(self):
        if self.timeline_mode == "sequence":
            return
        row = self.table.currentRow()
        column = self.table.currentColumn()
        item = (
            self.table.item(row, column)
            if row >= 0 and column >= 0
            else None
        )
        if item is None:
            return
        key_column = item.data(Qt.ItemDataRole.UserRole)
        exposure = item.data(Qt.ItemDataRole.UserRole + 1)
        if key_column is None or exposure is None:
            return
        self.extendExposureRequested.emit(
            int(row),
            int(key_column),
            max(1, int(exposure)),
        )

    def _on_onion_toggled(self, checked):
        self.onionChanged.emit(bool(checked))

    @staticmethod
    def _time_remap_drop_path(mime_data):
        if mime_data is None or not mime_data.hasUrls():
            return None
        for url in mime_data.urls():
            path = str(url.toLocalFile() or "")
            if Path(path).suffix.lower() in (".xdts", ".xtds"):
                return path
        return None

    def dragEnterEvent(self, event):
        if self._time_remap_drop_path(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._time_remap_drop_path(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        path = self._time_remap_drop_path(event.mimeData())
        if path:
            self.timeRemapFileDropped.emit(path)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def eventFilter(self, watched, event):
        if (
            watched is self.layer_list.viewport()
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            point = event.position().toPoint()
            item = self.layer_list.itemAt(point)
            if item is not None and point.x() <= 46:
                row = self.layer_list.row(item)
                visible = bool(
                    item.data(Qt.ItemDataRole.UserRole + 4)
                )
                visible = not visible
                item.setData(Qt.ItemDataRole.UserRole + 4, visible)
                self.layer_list.setCurrentItem(item)
                self.layer_list.viewport().update()
                self.layerVisibilityChanged.emit(row, visible)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _layer_opacity_slider_changed(self, value):
        value = max(0, min(100, int(value)))
        self.layer_opacity_value.setText(f"{value}%")
        row = self.layer_list.currentRow()
        if row >= 0:
            self.layerOpacityChanged.emit(row, value / 100.0)

    def _sync_layer_opacity_slider(self, opacity):
        value = max(0, min(100, int(round(float(opacity) * 100))))
        self.layer_opacity_slider.blockSignals(True)
        self.layer_opacity_slider.setValue(value)
        self.layer_opacity_slider.blockSignals(False)
        self.layer_opacity_value.setText(f"{value}%")

    def _scrub_header_frame(self, column):
        if not (0 <= int(column) < self._real_frame_count):
            return
        row = self.table.currentRow()
        if row < 0:
            row = 0
        self.table.setCurrentCell(row, int(column))
        self._select_table_cell(row, int(column))

    def _select_table_cell(self, row, column):
        if not (0 <= int(column) < self._real_frame_count):
            return
        # 連番の表示列から内部フレームへの変換は受け手側で一度だけ行う。
        self.frameSelected.emit(int(column), int(row))

    def selected_layer_rows(self):
        return sorted({
            index.row() for index in self.layer_list.selectedIndexes()
            if index.isValid()
        })

    def _rows_all_draft(self, rows):
        """選択した全レイヤー行が下書きレイヤーなら True。"""
        checked = False
        for row in rows:
            item = self.layer_list.item(int(row))
            if item is None:
                return False
            checked = True
            if not bool(item.data(Qt.ItemDataRole.UserRole + 6)):
                return False
        return checked

    def _show_layer_context_menu(self, position):
        item = self.layer_list.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self.layer_list.clearSelection()
            item.setSelected(True)
            self.layer_list.setCurrentItem(item)
        rows = tuple(self.selected_layer_rows())
        if not rows:
            return
        menu = QMenu(self)
        action_duplicate = menu.addAction(tr("複製"))
        action_merge = menu.addAction(tr("結合"))
        action_merge.setEnabled(len(rows) >= 2)
        action_delete = menu.addAction(tr("削除"))
        menu.addSeparator()
        action_draft = menu.addAction(tr("下書きレイヤー"))
        action_draft.setCheckable(True)
        action_draft.setChecked(bool(self._rows_all_draft(rows)))
        action_normalize = None
        if self.timeline_mode == "sheet":
            menu.addSeparator()
            action_normalize = menu.addAction(tr("番号の正規化"))
        chosen = menu.exec(self.layer_list.viewport().mapToGlobal(position))
        if chosen is action_duplicate:
            self.duplicateLayersRequested.emit(rows)
        elif chosen is action_merge:
            self.mergeLayersRequested.emit(rows)
        elif chosen is action_delete:
            self.deleteLayersRequested.emit(rows)
        elif chosen is action_draft:
            self.toggleDraftLayersRequested.emit(rows)
        elif action_normalize is not None and chosen is action_normalize:
            self.normalizeNumbersRequested.emit(rows)

    def rename_selected_layer(self):
        row = self.layer_list.currentRow()
        item = self.layer_list.item(row) if row >= 0 else None
        if item is None:
            return
        self.layer_list.setCurrentItem(item)
        self.layer_list.scrollToItem(item)
        self.layer_list.editItem(item)

    def _move_timeline_visual_rows(self, start, end, final_row):
        """レイヤー名のドラッグ中、対応するタイムライン行も同時に移動する。"""
        row_count = self.table.rowCount()
        if not (0 <= start <= end < row_count):
            return
        block_count = end - start + 1
        final_row = max(0, min(int(final_row), row_count - block_count))
        if final_row == start:
            return

        current_column = self.table.currentColumn()
        moved_rows = []
        self.table.blockSignals(True)
        try:
            for row in range(start, end + 1):
                moved_rows.append([
                    self.table.takeItem(row, column)
                    for column in range(self.table.columnCount())
                ])

            for row in range(end, start - 1, -1):
                self.table.removeRow(row)

            for offset, items in enumerate(moved_rows):
                target_row = final_row + offset
                self.table.insertRow(target_row)
                self.table.setRowHeight(target_row, 28)
                for column, item in enumerate(items):
                    if item is not None:
                        self.table.setItem(target_row, column, item)

            if current_column >= 0:
                self.table.setCurrentCell(final_row, current_column)
        finally:
            self.table.blockSignals(False)

    def _layer_rows_moved(self, parent, start, end, destination, destination_row):
        block_count = end - start + 1
        final_row = (
            destination_row - block_count
            if destination_row > start
            else destination_row
        )
        final_row = max(
            0,
            min(final_row, self.layer_list.count() - block_count),
        )
        if final_row == start:
            return

        # 名前だけ先に動いてタイムラインが残る状態を作らない。
        self._move_timeline_visual_rows(start, end, final_row)
        self.layerMoveRequested.emit(
            tuple(range(start, end + 1)),
            int(final_row),
        )

    def _layer_selected(self, row):
        item = self.layer_list.item(row) if row >= 0 else None
        if item is not None:
            opacity = item.data(Qt.ItemDataRole.UserRole + 5)
            if opacity is not None:
                self._sync_layer_opacity_slider(float(opacity))
        self.layerSelected.emit(row)

    def _layer_item_changed(self, item):
        row = self.layer_list.row(item)
        if row < 0:
            return
        previous_name = str(
            item.data(Qt.ItemDataRole.UserRole + 3) or ""
        ).strip()
        entered_name = item.text()
        current_name = entered_name.strip() or previous_name or "Layer"

        # 表示用の名前と比較用の保持値を揃える。ここで発生する itemChanged は
        # 再帰させず、実際に名前が変わった場合だけ外側へ通知する。
        was_blocked = self.layer_list.blockSignals(True)
        try:
            if entered_name != current_name:
                item.setText(current_name)
            item.setData(Qt.ItemDataRole.UserRole + 3, current_name)
        finally:
            self.layer_list.blockSignals(was_blocked)

        if current_name != previous_name:
            self.layerNameChanged.emit(row, current_name)
    def _play(self,on): self.playRequested.emit(on)
    @staticmethod
    def timeline_span_at(frames, layer_index, column):
        """表示中のコマ区間を返す。

        content は●、blank は○、None は未使用セル。
        """
        if (
            column < 0
            or column >= len(frames)
            or layer_index < 0
        ):
            return None, None, 0

        for start in range(column, -1, -1):
            if layer_index >= len(frames[start].layers):
                continue
            layer = frames[start].layers[layer_index]
            if getattr(layer, "sequence_only", False):
                continue
            is_content = bool(layer.has_content)
            is_blank = bool(
                getattr(layer, "is_blank_key", False)
            )
            if not (is_content or is_blank):
                continue

            exposure = max(1, int(layer.exposure))
            if column < start + exposure:
                return (
                    "content" if is_content else "blank",
                    start,
                    exposure,
                )
            # 直近の明示コマが届いていない場合、それ以前の露出は
            # この明示コマを越えて表示しない。
            break

        return None, None, 0

    def refresh(self, frames, current, active, tween_pending=None):
        if not frames:
            return

        selected_cells = {
            (index.row(), index.column())
            for index in self.table.selectedIndexes()
            if index.isValid()
        }
        previous_current = (
            self.table.currentRow(),
            self.table.currentColumn(),
        )

        current = max(0, min(current, len(frames) - 1))
        pending_tween = tween_pending if isinstance(tween_pending, dict) else {}
        pending_layer = int(pending_tween.get("layer_index", -1))
        pending_key = int(pending_tween.get("key_col", -1))
        pending_end = int(pending_tween.get("end_col", -1))
        pending_reverse = bool(
            pending_tween.get(
                "reverse_generation",
                False,
            )
        )
        rows = len(frames[current].layers)
        if self.timeline_mode == "sequence":
            real_frame_count = max(
                (
                    max({
                        int(layer.sequence_number)
                        for frame in frames
                        for layer in [frame.layers[layer_index]]
                        if layer.sequence_number is not None
                        and (layer.has_content or layer.is_blank_key)
                    } or {1})
                    for layer_index in range(len(frames[current].layers))
                ),
                default=1,
            )
            real_frame_count = max(
                real_frame_count,
                max(
                    (
                        int(number)
                        for _layer_index, number in self.sequence_archive
                    ),
                    default=1,
                ),
            )
            real_frame_count = max(1, real_frame_count)
        else:
            real_frame_count = len(frames)
        self._real_frame_count = real_frame_count
        self.table._real_frame_count = real_frame_count
        cell_width, row_height = self.timeline_cell_metrics()
        visible_cols = max(
            1, self.table.viewport().width() // max(1, cell_width) + 4
        )
        cols = max(real_frame_count + 24, visible_cols)
        self.table.blockSignals(True)
        self.table._sequence_numbers_by_row = {}
        self._sequence_frame_by_cell = {}
        self.table.setRowCount(rows)
        self.table.setColumnCount(cols)
        self.table.setHorizontalHeaderLabels([str(i + 1) if (i + 1) % 6 == 0 else "" for i in range(cols)])
        self.table.horizontalHeader().setVisible(True)
        self.table.horizontalHeader().setFixedHeight(25)
        self.layer_header_spacer.setFixedHeight(25)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setVerticalHeaderLabels(["" for _ in range(rows)])
        self.table.verticalHeader().setDefaultSectionSize(row_height)
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        self._active_layer_index = int(active)
        for layer in reversed(frames[current].layers):
            is_draft = bool(getattr(layer, "is_draft", False))
            item = QListWidgetItem(layer.name)
            item.setFlags(
                (
                    item.flags()
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsDragEnabled
                    | Qt.ItemFlag.ItemIsEditable
                )
                & ~Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setData(Qt.ItemDataRole.UserRole + 2, False)
            item.setData(Qt.ItemDataRole.UserRole + 3, layer.name)
            item.setData(Qt.ItemDataRole.UserRole + 4, bool(layer.visible))
            item.setData(Qt.ItemDataRole.UserRole + 5, float(layer.opacity))
            item.setData(Qt.ItemDataRole.UserRole + 6, is_draft)
            if is_draft:
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
            item.setToolTip(
                (tr("下書きレイヤー（色数削減の対象外）。\n") if is_draft else "")
                + tr("[●] 表示／[-] 非表示。左端クリックで切替、"
                     "ダブルクリックでレイヤー名を変更。")
            )
            item.setSizeHint(QSize(0, row_height))
            self.layer_list.addItem(item)
        self.layer_list.setCurrentRow(rows - 1 - active)
        if 0 <= active < len(frames[current].layers):
            self._sync_layer_opacity_slider(
                frames[current].layers[active].opacity
            )
        self.layer_list.blockSignals(False)
        for col in range(cols):
            self.table.setColumnWidth(col, cell_width)
        for visual_row in range(rows):
            self.table.setRowHeight(visual_row, row_height)
            layer_index = rows - 1 - visual_row
            sequence_columns = [
                column
                for _number, column in sorted(
                    {
                        int(frame.layers[layer_index].sequence_number): column
                        for column, frame in enumerate(frames)
                        if (
                            frame.layers[layer_index].sequence_number is not None
                            and (frame.layers[layer_index].has_content or frame.layers[layer_index].is_blank_key)
                        )
                    }.items()
                )
            ]
            sequence_by_number = {
                int(frames[column].layers[layer_index].sequence_number): column
                for column in sequence_columns
            }
            archive_by_number = {
                int(number): archived
                for (archived_layer, number), archived
                in self.sequence_archive.items()
                if int(archived_layer) == layer_index
            }
            for number, frame_column in sequence_by_number.items():
                self._sequence_frame_by_cell[(visual_row, number - 1)] = frame_column
            self.table._sequence_numbers_by_row[visual_row] = tuple(
                sorted(
                    set(sequence_by_number)
                    | {
                        int(number)
                        for archived_layer, number in self.sequence_archive
                        if int(archived_layer) == layer_index
                    }
                )
            )
            content_columns = [
                column
                for column in range(len(frames))
                if frames[column].layers[layer_index].has_content
            ]
            if self.timeline_mode == "sequence":
                content_key_numbers = {
                    number - 1: number
                    for number in set(sequence_by_number) | set(archive_by_number)
                }
            elif self.timeline_mode == "sheet":
                used_numbers = [
                    int(frames[column].layers[layer_index].sequence_number)
                    for column in content_columns
                    if frames[column].layers[layer_index].sequence_number
                    is not None
                ]
                next_number = max(used_numbers, default=0) + 1
                for column in content_columns:
                    layer = frames[column].layers[layer_index]
                    if layer.sequence_number is None:
                        layer.sequence_number = next_number
                        next_number += 1
                content_key_numbers = {
                    column: int(
                        frames[column].layers[layer_index].sequence_number
                    )
                    for column in content_columns
                }
            else:
                content_key_numbers = {
                    column: number
                    for number, column in enumerate(
                        content_columns,
                        start=1,
                    )
                }
            for col in range(cols):
                text = ""
                sequence_source = None
                if self.timeline_mode == "sequence":
                    number = col + 1
                    if number in sequence_by_number:
                        sequence_source = frames[
                            sequence_by_number[number]
                        ].layers[layer_index]
                    elif number in archive_by_number:
                        sequence_source = archive_by_number[number]
                    if sequence_source is not None:
                        span_kind = (
                            "content"
                            if sequence_source.has_content
                            else "sequence_blank"
                        )
                        key_col, exposure = col, 1
                    else:
                        span_kind, key_col, exposure = None, None, 0
                else:
                    span_kind, key_col, exposure = self.timeline_span_at(
                        frames, layer_index, col
                    )
                is_handle = False
                is_start_handle = False
                is_pending_tween = False
                if key_col is not None:
                    end_col = key_col + exposure - 1
                    is_pending_tween = (
                        layer_index == pending_layer
                        and int(key_col) == pending_key
                        and int(end_col) == pending_end
                    )
                    if (
                        span_kind == "content"
                        and col == key_col
                    ):
                        source_key_layer = (
                            sequence_source
                            if self.timeline_mode == "sequence"
                            else frames[key_col].layers[layer_index]
                        )
                        source_cell_name = getattr(
                            source_key_layer, "cell_name", None
                        )
                        text = (
                            "◆"
                            if (
                                is_pending_tween
                                and pending_reverse
                            )
                            else str(
                                source_cell_name
                                or content_key_numbers.get(key_col, "")
                            )
                        )
                    elif (
                        span_kind == "blank"
                        and col == key_col
                    ):
                        text = "○"
                    elif col == end_col:
                        text = (
                            "♦"
                            if (
                                is_pending_tween
                                and not pending_reverse
                            )
                            else "→"
                        )
                    elif col > key_col:
                        text = "ー"
                    is_handle = col == end_col
                    is_start_handle = col == key_col
                if span_kind == "sequence_blank":
                    text = str(col + 1)
                item = QTableWidgetItem(text)
                if span_kind == "content":
                    state_name = (
                        "sheet_key"
                        if self.timeline_mode == "sheet" and col == key_col
                        else "sheet_hold"
                        if self.timeline_mode == "sheet"
                        else "key"
                        if col == key_col
                        else "hold"
                    )
                else:
                    state_name = (
                        "blank" if span_kind in ("blank", "sequence_blank") else "uncreated"
                    )
                item.setData(TimelineCellDelegate.STATE_ROLE, state_name)
                item.setFlags((item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled) & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                # 参考UIに合わせたタイムライン専用配色。
                # キーフレームと保持区間は薄い水色、空フレームはさらに薄く、
                # 未作成フレームはテーブル既定色のままにする。
                cell_colors = theme.palette()
                if span_kind == "content" and col == key_col:
                    cell_key = "sheet_key" if self.timeline_mode == "sheet" else "key"
                    item.setBackground(QColor(cell_colors[f"timeline_{cell_key}"]))
                    item.setForeground(QColor(cell_colors[f"timeline_{cell_key}_text"]))
                    key_font = item.font()
                    key_font.setBold(True)
                    key_font.setPointSize(max(10, key_font.pointSize()))
                    item.setFont(key_font)
                elif span_kind == "content":
                    cell_key = "sheet_hold" if self.timeline_mode == "sheet" else "hold"
                    item.setBackground(QColor(cell_colors[f"timeline_{cell_key}"]))
                    item.setForeground(QColor(cell_colors[f"timeline_{cell_key}_text"]))
                elif span_kind == "blank":
                    item.setBackground(QColor(cell_colors["timeline_blank"]))
                    item.setForeground(QColor(cell_colors["timeline_blank_text"]))
                    if text == "○":
                        blank_font = item.font()
                        blank_font.setBold(True)
                        blank_font.setPointSize(
                            max(10, blank_font.pointSize())
                        )
                        item.setFont(blank_font)
                else:
                    item.setForeground(QColor(cell_colors["timeline_uncreated_text"]))
                if text in ("→", "♦", "◆"):
                    item.setForeground(
                        QColor("#A43A9E")
                        if text in ("♦", "◆")
                        else QColor("#145F80")
                    )
                    marker_font = item.font()
                    marker_font.setBold(True)
                    marker_font.setPointSize(
                        max(10, marker_font.pointSize())
                    )
                    item.setFont(marker_font)
                if key_col is not None:
                    item.setData(Qt.ItemDataRole.UserRole, int(key_col))
                    item.setData(Qt.ItemDataRole.UserRole + 1, int(exposure))
                    item.setData(TimelineTable.END_HANDLE_ROLE, bool(is_handle))
                    item.setData(TimelineTable.START_HANDLE_ROLE, bool(is_start_handle))
                    item.setData(
                        TimelineTable.TWEEN_PENDING_ROLE,
                        bool(
                            is_pending_tween
                            and col in (key_col, end_col)
                        ),
                    )
                if is_handle:
                    if is_pending_tween:
                        item.setToolTip(
                            (
                                tr("逆生成トゥイーン中。"
                                "この右端は元の初期形状です。"
                                "右クリックでキャンセルできます。")
                            )
                            if pending_reverse
                            else
                            tr("トゥイーン変形中。"
                            "右クリックでキャンセルできます。")
                        )
                    else:
                        item.setToolTip(
                            (
                                tr("右端をドラッグして後方向の表示コマ数を変更／"
                                "右クリックでトゥイーンを有効化")
                            )
                            if span_kind == "content"
                            else tr("空フレームの右端をドラッグして表示コマ数を変更")
                        )
                elif text == "◆":
                    item.setToolTip(
                        tr("逆生成トゥイーン中。"
                        "キーフレーム側が操作中の変形形状、"
                        "右端側が元の初期形状になります。"
                        "右クリックでキャンセルできます。")
                    )
                elif (
                    span_kind == "content"
                    and col == key_col
                ):
                    source_cell_name = getattr(
                        (
                            sequence_source
                            if self.timeline_mode == "sequence"
                            else frames[key_col].layers[layer_index]
                        ),
                        "cell_name",
                        None,
                    )
                    item.setToolTip(
                        (
                            tr("CLIP STUDIOセル名：{name}\n").format(name=source_cell_name)
                            if source_cell_name
                            else ""
                        )
                        + tr("中央をドラッグして移動／左端をドラッグして前方向へ伸縮\nレイヤーセル {value} / {exposure}コマ").format(value=col + 1, exposure=exposure)
                    )
                elif text == "○":
                    item.setToolTip(
                        tr("空フレームの先頭です。"
                        "描画すると自動的にキーフレーム化します。")
                    )
                if (
                    (self.timeline_mode != "sequence" and col == current)
                    or (
                        self.timeline_mode == "sequence"
                        and self._sequence_frame_by_cell.get(
                            (visual_row, col)
                        ) == current
                    )
                ):
                    item.setForeground(QColor(190, 40, 40))
                self.table.setItem(visual_row, col, item)
        selected_layer = frames[current].layers[active]
        self.duration.blockSignals(True)
        self.duration.setValue(max(1, selected_layer.exposure))
        self.duration.blockSignals(False)

        valid_selected = [
            (row, column)
            for row, column in selected_cells
            if (
                0 <= row < self.table.rowCount()
                and 0 <= column < self._real_frame_count
            )
        ]
        if valid_selected:
            self.table.clearSelection()
            for row, column in valid_selected:
                item = self.table.item(row, column)
                if item is not None:
                    item.setSelected(True)

            current_row, current_column = previous_current
            if not (
                0 <= current_row < self.table.rowCount()
                and 0 <= current_column < self._real_frame_count
            ):
                current_row, current_column = valid_selected[-1]
            current_index = self.table.model().index(
                current_row, current_column
            )
            self.table.selectionModel().setCurrentIndex(
                current_index,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
        else:
            self.select_current(current, active)
        self.table.blockSignals(False)

    @staticmethod
    def key_at_or_before(frames, layer_index, column):
        for key_col in range(column, -1, -1):
            if layer_index >= len(frames[key_col].layers):
                continue
            layer = frames[key_col].layers[layer_index]
            if layer.has_content:
                if column < key_col + max(1, layer.exposure):
                    return key_col
                return None
        return None

    def select_current(self, column, active):
        row = max(0, self.table.rowCount() - 1 - active)
        if self.timeline_mode == "sequence":
            column = next(
                (
                    visual_column
                    for (visual_row, visual_column), frame_column
                    in self._sequence_frame_by_cell.items()
                    if visual_row == row and frame_column == int(column)
                ),
                0,
            )
        column = max(
            0,
            min(column, max(0, self._real_frame_count - 1)),
        )

        # ドラッグ／Shift選択中のセルを消さず、
        # 再生ヘッドに相当する現在セルだけを更新する。
        selected_count = len(self.table.selectedIndexes())
        modifiers = QApplication.keyboardModifiers()
        preserve_selection = (
            selected_count > 1
            or bool(
                modifiers
                & (
                    Qt.KeyboardModifier.ShiftModifier
                    | Qt.KeyboardModifier.ControlModifier
                )
            )
        )
        if preserve_selection:
            index = self.table.model().index(row, column)
            self.table.selectionModel().setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
        else:
            self.table.setCurrentCell(row, column)

    def update_cell(self, frames, frame_index, layer_index):
        if not frames or frame_index < 0 or frame_index >= len(frames):
            return
        if layer_index < 0 or layer_index >= len(frames[frame_index].layers):
            return
        self.refresh(frames, frame_index, layer_index)
