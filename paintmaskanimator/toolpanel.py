from .common import *  # noqa: F401,F403
from . import theme
from .widgets import (BrushSizeSpinBox, ClickableValueLabel, HSVColorWheel, LineTaperCurvePopup, SliderValueSpinBox, SwatchEyedropButton)


TOOL_DEFINITIONS = [
    ("brush", "ブラシ"), ("line", "ライン"),
    ("shape", "図形"), ("bucket", "バケツ"),
    ("lasso_fill", "投げ縄塗り"), ("lasso", "投げ縄選択"),
    ("rect_select", "長方形選択"), ("auto_select", "自動選択"),
    ("eyedropper", "スポイト"), ("dust", "ゴミ取り"),
]


def _tool_icon(tool_id):
    """Return a compact, theme-independent pictogram for a drawing tool."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    foreground = QColor("#37474f")
    accent = QColor("#00897b")
    painter.setPen(QPen(foreground, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if tool_id == "brush":
        painter.drawLine(9, 23, 21, 8)
        painter.setBrush(accent)
        painter.drawEllipse(6, 21, 7, 5)
    elif tool_id == "line":
        painter.drawLine(7, 24, 25, 7)
        painter.drawEllipse(5, 22, 4, 4)
        painter.drawEllipse(23, 5, 4, 4)
    elif tool_id == "shape":
        polygon = QPolygonF([QPointF(16, 6), QPointF(26, 16), QPointF(16, 26), QPointF(6, 16)])
        painter.drawPolygon(polygon)
    elif tool_id == "bucket":
        painter.save()
        painter.translate(16, 15)
        painter.rotate(-35)
        painter.drawRect(-7, -7, 14, 14)
        painter.restore()
        painter.setPen(QPen(accent, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(19, 24, 25, 24)
    elif tool_id in ("lasso", "lasso_fill"):
        path = QPainterPath(QPointF(8, 10))
        path.cubicTo(16, 4, 27, 9, 24, 17)
        path.cubicTo(21, 25, 8, 25, 7, 17)
        path.cubicTo(6, 13, 9, 10, 13, 11)
        if tool_id == "lasso_fill":
            painter.setBrush(accent)
        painter.drawPath(path)
        painter.drawLine(13, 11, 18, 26)
    elif tool_id == "rect_select":
        painter.setPen(QPen(foreground, 2, Qt.PenStyle.DashLine))
        painter.drawRect(7, 7, 18, 18)
    elif tool_id == "auto_select":
        painter.drawLine(9, 24, 20, 10)
        for x1, y1, x2, y2 in ((20, 6, 20, 3), (24, 8, 27, 6), (25, 12, 29, 12), (17, 7, 15, 4)):
            painter.drawLine(x1, y1, x2, y2)
    elif tool_id == "eyedropper":
        painter.drawLine(9, 24, 23, 9)
        painter.drawEllipse(19, 6, 7, 7)
        painter.setPen(QPen(accent, 2.5))
        painter.drawLine(7, 25, 11, 25)
    elif tool_id == "dust":
        painter.setBrush(accent)
        painter.drawEllipse(8, 9, 5, 5)
        painter.drawEllipse(19, 8, 4, 4)
        painter.drawEllipse(14, 19, 6, 6)
    painter.end()
    return QIcon(pixmap)


class SwatchStack(QWidget):
    """Compact drawing-colour control: stacked main/sub swatches plus a
    background swatch below, sized to fit the tool bar's current width.

    Emits ``modeRequested`` with ``"main"`` / ``"sub"`` / ``"transparent"``.
    ``backgroundColorRequested`` fires on right-clicking the background swatch
    (to change the background display colour).
    """
    modeRequested = Signal(str)
    backgroundColorRequested = Signal()

    def __init__(self, width=52, parent=None):
        super().__init__(parent)
        self._main = QColor("black")
        self._sub = QColor(255, 0, 0)
        self._bg = QColor("white")
        self._mode = "main"
        self._columns = 2
        self._sub_btn = QPushButton(self)
        self._main_btn = QPushButton(self)
        self._bg_btn = QPushButton(self)
        for btn in (self._sub_btn, self._main_btn, self._bg_btn):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._main_btn.setToolTip("クリック：メイン色に切替")
        self._sub_btn.setToolTip("クリック：サブ色に切替")
        self._bg_btn.setToolTip(
            "クリック：背景色で描画／右クリック：背景色を変更"
        )
        self._main_btn.clicked.connect(lambda: self.modeRequested.emit("main"))
        self._sub_btn.clicked.connect(lambda: self.modeRequested.emit("sub"))
        self._bg_btn.clicked.connect(
            lambda: self.modeRequested.emit("transparent")
        )
        self._bg_btn.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._bg_btn.customContextMenuRequested.connect(
            lambda _p: self.backgroundColorRequested.emit()
        )
        self.relayout(width)

    def relayout(self, width, columns=None):
        """Resize the swatch to ``width`` px (shrinks to one icon column)."""
        w = max(16, int(width))
        if columns is not None:
            self._columns = max(1, int(columns))
        if self._columns == 1:
            self.setFixedSize(w, w * 3 + 6)
            self._layout_buttons()
            self._restyle()
            return
        box = max(10, round(w * 0.64))
        off = w - box
        self._main_btn.setGeometry(0, 0, box, box)
        self._sub_btn.setGeometry(off, off, box, box)
        gap = 3
        bg_h = max(8, round(w * 0.26))
        self._bg_btn.setGeometry(0, w + gap, w, bg_h)
        self.setFixedSize(w, w + gap + bg_h)
        self._restyle()

    def _layout_buttons(self):
        if self._columns != 1:
            return
        w = self.width()
        gap = 3
        if self._mode == "sub":
            order = (self._sub_btn, self._main_btn, self._bg_btn)
        elif self._mode == "transparent":
            order = (self._bg_btn, self._main_btn, self._sub_btn)
        else:
            order = (self._main_btn, self._sub_btn, self._bg_btn)
        for row, button in enumerate(order):
            button.setGeometry(0, row * (w + gap), w, w)

    def set_colors(self, main, sub, mode, background=None):
        self._main = QColor(main)
        self._sub = QColor(sub)
        if background is not None:
            self._bg = QColor(background)
        self._mode = mode
        self._restyle()

    def _restyle(self):
        self._layout_buttons()
        accent = theme.accent()
        def style(c, selected):
            border = (
                f"2px solid {accent}" if selected else "1px solid #202020"
            )
            return (
                f"QPushButton{{background:{c.name()};border:{border};"
                "border-radius:4px;}"
            )
        self._sub_btn.setStyleSheet(style(self._sub, self._mode == "sub"))
        self._main_btn.setStyleSheet(style(self._main, self._mode == "main"))
        self._bg_btn.setStyleSheet(
            style(self._bg, self._mode == "transparent")
        )
        if self._mode == "sub":
            self._sub_btn.raise_()
        else:
            self._main_btn.raise_()


class ToolSelectorPanel(QWidget):
    """Narrow icon-only tool selector; options live in ``ToolPanel``."""
    toolChanged = Signal(str)
    snapWidthRequested = Signal(int)
    colorModeRequested = Signal(str)
    backgroundColorRequested = Signal()
    TOOLS = TOOL_DEFINITIONS

    CELL_SIZE = 30
    ICON_SIZE = 24
    PANEL_MARGIN = 2
    # QListView's wrapping check is strict at an exact N * gridSize boundary.
    # One shared pixel (not one per cell) keeps the final cell on the row.
    LAYOUT_SLACK = 1

    def __init__(self):
        super().__init__()
        self.items = {}
        self.active_tool = "brush"
        self._column_count = 1
        self._swatch_column_count = 1
        self._swatch_sync_timer = QTimer(self)
        self._swatch_sync_timer.setSingleShot(True)
        self._swatch_sync_timer.setInterval(0)
        self._swatch_sync_timer.timeout.connect(
            self.sync_swatch_to_displayed_columns
        )
        self.setMinimumWidth(self.width_for_columns(1))
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            self.PANEL_MARGIN, self.PANEL_MARGIN,
            self.PANEL_MARGIN, self.PANEL_MARGIN,
        )
        layout.setSpacing(0)
        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setFlow(QListView.Flow.LeftToRight)
        self.list.setWrapping(True)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        # Free movement + internal move so tool icons can be reordered by drag.
        self.list.setMovement(QListView.Movement.Snap)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.list.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.list.setIconSize(QSize(self.ICON_SIZE, self.ICON_SIZE))
        self.list.setGridSize(QSize(self.CELL_SIZE, self.CELL_SIZE))
        self.list.setSpacing(0)
        self.list.setFrameStyle(0)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._apply_list_theme()
        layout.addWidget(self.list, 1)
        # Drawing-colour swatch (main/sub/background) at the BOTTOM of the bar.
        # Its width tracks the icon column count (shrinks to one column).
        self.color_swatch = SwatchStack(self.CELL_SIZE * 2 - 8)
        self.color_swatch.modeRequested.connect(self.colorModeRequested)
        self.color_swatch.backgroundColorRequested.connect(
            self.backgroundColorRequested
        )
        self._swatch_holder = QWidget()
        swatch_row = QHBoxLayout(self._swatch_holder)
        swatch_row.setContentsMargins(0, 4, 0, 2)
        swatch_row.addStretch(1)
        swatch_row.addWidget(self.color_swatch)
        swatch_row.addStretch(1)
        layout.addWidget(self._swatch_holder)
        for tool_id, label in self.TOOLS:
            item = QListWidgetItem(_tool_icon(tool_id), "")
            item.setData(Qt.ItemDataRole.UserRole, tool_id)
            item.setData(Qt.ItemDataRole.ToolTipRole, label)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list.addItem(item)
            self.items[tool_id] = item
        self.list.currentItemChanged.connect(self._current_item_changed)
        # InternalMove drag-reorder recreates QListWidgetItems on drop, so the
        # references in self.items go stale. Rebuild the map when rows change.
        self.list.model().rowsInserted.connect(self._rebuild_item_map)
        self.set_active_tool("brush")
        self._resize_swatch_to_columns()

    def _rebuild_item_map(self, *args):
        rebuilt = {}
        for index in range(self.list.count()):
            item = self.list.item(index)
            tool_id = item.data(Qt.ItemDataRole.UserRole)
            if tool_id:
                rebuilt[tool_id] = item
        if rebuilt:
            self.items = rebuilt
            # Restore the active highlight on the (possibly new) item.
            active = self.items.get(self.active_tool)
            if active is not None:
                self.list.blockSignals(True)
                self.list.setCurrentItem(active)
                self.list.blockSignals(False)

    def _apply_list_theme(self):
        c = theme.palette()
        self.list.setStyleSheet(
            "QListWidget{background:transparent;outline:0;border:0;}"
            "QListWidget::item{margin:1px;border:1px solid transparent;"
            "border-radius:7px;}"
            f"QListWidget::item:hover{{background:{c['hover']};"
            "border-color:transparent;}"
            f"QListWidget::item:selected{{background:{c['selection']};"
            f"border:1px solid {c['accent']};}}"
            f"QListWidget::item:selected:hover{{background:{c['selection']};"
            f"border-color:{c['accent']};}}"
        )

    def apply_theme(self):
        """Re-apply palette-derived styling after a theme/accent change."""
        self._apply_list_theme()
        self.color_swatch._restyle()

    def set_swatch_colors(self, main, sub, mode, background=None):
        self.color_swatch.set_colors(main, sub, mode, background)

    def _resize_swatch_to_columns(self, columns=None):
        """Size the drawing-colour swatch to the current icon column count."""
        if columns is None:
            columns = self._column_count
        columns = max(1, int(columns))
        self._swatch_column_count = columns
        # Fit within the column band, capped so a wide bar stays reasonable.
        swatch_w = min(
            columns * self.CELL_SIZE - 6,
            self.CELL_SIZE * 3,
        )
        swatch_w = max(18, swatch_w)
        self.color_swatch.relayout(swatch_w, columns)

    def sync_swatch_to_displayed_columns(self):
        """Match the swatch to the columns the icon view actually rendered."""
        columns = self.displayed_column_count()
        if columns == self._swatch_column_count:
            return
        self._resize_swatch_to_columns(columns)

    def minimumSizeHint(self):
        return QSize(self.width_for_columns(1), 0)

    def sizeHint(self):
        return QSize(self.width_for_columns(1), self.CELL_SIZE * len(self.items))

    @classmethod
    def width_for_columns(cls, columns):
        columns = max(1, int(columns))
        return (
            cls.PANEL_MARGIN * 2
            + columns * cls.CELL_SIZE
            + cls.LAYOUT_SLACK
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        available_width = max(
            1,
            self.width() - self.PANEL_MARGIN * 2 - self.LAYOUT_SLACK,
        )
        columns = max(
            1,
            min(
                len(self.items),
                (available_width + self.CELL_SIZE // 2) // self.CELL_SIZE,
            ),
        )
        self._column_count = columns
        target_width = self.width_for_columns(columns)
        if self.width() != target_width:
            self.snapWidthRequested.emit(target_width)
        # QListWidget lays its items out after the parent resize.  Recheck on
        # the next event-loop turn so the colour control changes at exactly
        # the same threshold as the visible icon columns.
        self._swatch_sync_timer.start()

    def displayed_column_count(self):
        """Return the number of icons Qt actually placed on the first row."""
        if not self.items:
            return 1
        self.list.doItemsLayout()
        first_rect = self.list.visualItemRect(self.list.item(0))
        first_y = first_rect.y()
        columns = 0
        for index in range(self.list.count()):
            rect = self.list.visualItemRect(self.list.item(index))
            if rect.y() != first_y:
                break
            columns += 1
        return max(1, columns)

    def set_active_tool(self, tool_id):
        item = self.items.get(tool_id)
        if item is None:
            return
        self.active_tool = tool_id
        self.list.blockSignals(True)
        self.list.setCurrentItem(item)
        self.list.blockSignals(False)

    def _current_item_changed(self, current, _previous):
        if current is None:
            return
        tool_id = current.data(Qt.ItemDataRole.UserRole)
        if tool_id == self.active_tool:
            return
        self.active_tool = tool_id
        self.toolChanged.emit(tool_id)

    def select_tool(self, tool_id):
        item = self.items.get(tool_id)
        if item is None:
            return
        if tool_id == self.active_tool:
            self.list.setCurrentItem(item)
            return
        self.list.setCurrentItem(item)


class ToolPanel(QWidget):
    toolChanged = Signal(str)
    colorModeChanged = Signal(str)
    colorChanged = Signal(str, QColor)
    meshCommitRequested = Signal()
    meshCancelRequested = Signal()
    selectionTransformRequested = Signal()
    selectionScaleRequested = Signal()
    selectionMeshRequested = Signal()
    selectionClearRequested = Signal()
    selectionRotateRequested = Signal(float)
    transformMeshGridChanged = Signal(int, int)
    selectionCommitRequested = Signal()
    selectionCancelRequested = Signal()
    flipLayerRequested = Signal(bool)
    swapMainSubRequested = Signal()
    resetMainSubRequested = Signal()
    isolateColorRequested = Signal()
    removeDustRequested = Signal()
    backgroundColorRequested = Signal()
    clearColorFilterRequested = Signal()
    TOOLS = TOOL_DEFINITIONS

    def __init__(self):
        super().__init__(); self.setFixedWidth(190); self.active_tool="brush"
        self._line_curve_popup = None
        self.main_color=QColor("black"); self.sub_color=QColor(255,0,0); self.color_mode="main"; self.transparent_display_color=QColor("white")
        v=QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(3)
        v.addWidget(QLabel("<b>ツールプロパティ</b>"))
        self.active=QLabel(); self.active.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.active.setStyleSheet(
            "padding:6px;font-weight:bold;border-radius:4px;"
        ); v.addWidget(self.active)

        # Context commands: only commands required by the selected tool are shown.
        self.command_box=QWidget(); self.command_layout=QVBoxLayout(self.command_box)
        self.command_layout.setContentsMargins(0,2,0,2)
        self.command_layout.setSpacing(3)
        self.command_title=QLabel("<b>ツールコマンド</b>"); self.command_layout.addWidget(self.command_title)
        self.mesh_commit=QPushButton("変形を確定")
        self.mesh_cancel=QPushButton("変形をキャンセル")
        self.flip_h=QPushButton("左右反転")
        self.flip_v=QPushButton("上下反転")
        self.selection_transform=QPushButton("自由変形")
        self.selection_scale=QPushButton("拡大縮小")
        self.selection_mesh=QPushButton("メッシュ変形")
        self.selection_clear=QPushButton("選択範囲を解除")
        self.selection_clear.setToolTip(
            "選択ツール使用時に、現在の選択範囲を解除します。"
            "どのツールからでも右上の「選択解除」を使用できます。"
        )
        self.selection_rotate_left=QPushButton("左へ90°回転")
        self.selection_rotate_right=QPushButton("右へ90°回転")
        self.transform_mesh_grid_label=QLabel("メッシュ格子数")
        self.transform_mesh_grid_x=QSpinBox()
        self.transform_mesh_grid_y=QSpinBox()
        for spin in (self.transform_mesh_grid_x, self.transform_mesh_grid_y):
            spin.setRange(2, 12)
            spin.setValue(4)
            spin.setToolTip("メッシュ変形中でも格子数を変更できます。")
        self.transform_mesh_grid_x.setPrefix("横 ")
        self.transform_mesh_grid_y.setPrefix("縦 ")
        self.transform_mesh_grid_x_slider = QSlider(Qt.Orientation.Horizontal)
        self.transform_mesh_grid_x_slider.setRange(2, 12)
        self.transform_mesh_grid_x_slider.setValue(4)
        self.transform_mesh_grid_y_slider = QSlider(Qt.Orientation.Horizontal)
        self.transform_mesh_grid_y_slider.setRange(2, 12)
        self.transform_mesh_grid_y_slider.setValue(4)
        self.transform_mesh_grid_x_slider.valueChanged.connect(
            self.transform_mesh_grid_x.setValue
        )
        self.transform_mesh_grid_y_slider.valueChanged.connect(
            self.transform_mesh_grid_y.setValue
        )
        self.transform_mesh_grid_x.valueChanged.connect(
            self.transform_mesh_grid_x_slider.setValue
        )
        self.transform_mesh_grid_y.valueChanged.connect(
            self.transform_mesh_grid_y_slider.setValue
        )
        self.selection_scale.clicked.connect(self.selectionScaleRequested)
        self.selection_mesh.clicked.connect(self.selectionMeshRequested)
        self.selection_transform.clicked.connect(self.selectionTransformRequested)
        self.selection_clear.clicked.connect(self.selectionClearRequested)
        self.selection_rotate_left.clicked.connect(lambda: self.selectionRotateRequested.emit(-90.0))
        self.selection_rotate_right.clicked.connect(lambda: self.selectionRotateRequested.emit(90.0))
        self.transform_mesh_grid_x.valueChanged.connect(
            lambda _value: self.transformMeshGridChanged.emit(
                self.transform_mesh_grid_x.value(),
                self.transform_mesh_grid_y.value(),
            )
        )
        self.transform_mesh_grid_y.valueChanged.connect(
            lambda _value: self.transformMeshGridChanged.emit(
                self.transform_mesh_grid_x.value(),
                self.transform_mesh_grid_y.value(),
            )
        )
        self.selection_all_frames=QCheckBox("すべてのコマに適用")
        self.selection_all_frames.setChecked(False)

        self.transform_quality=QCheckBox("クオリティ（Tp_mask v0.7方式）")
        self.transform_quality.setChecked(True)
        self.transform_quality.setToolTip(
            "色ごとに分離して変形し、中間色を作らずに再合成します。"
            "拡大・縮小・回転で線や塗りが崩れにくくなります。"
        )
        self.transform_line_width_note=QLabel(
            "色選択があるときは実線の太さを調整できます。"
        )
        self.transform_line_width_note.setWordWrap(True)
        self.transform_line_width_note.setStyleSheet(
            "color:palette(placeholder-text);padding-left:4px;padding-right:4px;"
        )
        self.transform_line_width_label=QLabel("実線の太さ：159")
        self.transform_line_width=QSlider(Qt.Orientation.Horizontal)
        self.transform_line_width.setRange(1, 254)
        self.transform_line_width.setValue(96)
        self.transform_line_width.setInvertedAppearance(True)
        self.transform_line_width.setToolTip(
            "クオリティ変形で、使用色パネルの選択色を実線として残す太さを調整します。"
            "右へ動かすほど太くなります。"
        )
        self.transform_line_width.valueChanged.connect(
            lambda value: self.transform_line_width_label.setText(
                f"実線の太さ：{255 - value}"
            )
        )
        self._has_transform_line_colors=False
        self._tween_active=False
        self.transform_quality.toggled.connect(self._sync_transform_quality_options)
        self._sync_transform_quality_options(True)
        self._refresh_transform_line_width_visibility()

        self.bucket_adjacent=QCheckBox("隣接")
        self.bucket_adjacent.setChecked(True)
        self.bucket_adjacent.setToolTip(
            "ON：クリック位置につながる同色領域だけを塗ります。"
            "OFF：レイヤー内の同じ色を一括で塗ります。"
        )
        self.bucket_include_sub=QCheckBox("選択した使用色を含み塗り")
        self.bucket_include_sub.setChecked(False)

        # 隙間閉じと幅スライダーを同じ横一列へ配置する。
        self.bucket_close_gap=QCheckBox("隙間閉じ")
        self.bucket_close_gap.setChecked(False)
        self.bucket_gap_width=QSlider(Qt.Orientation.Horizontal)
        self.bucket_gap_width.setRange(1,20)
        self.bucket_gap_width.setValue(4)
        self.bucket_gap_width.setMinimumWidth(58)
        self.bucket_gap_width_label=QLabel("4 px")
        self.bucket_gap_width_label.setFixedWidth(34)
        self.bucket_gap_width.valueChanged.connect(
            lambda value: self.bucket_gap_width_label.setText(
                f"{value} px"
            )
        )
        self.bucket_gap_row=QWidget()
        bucket_gap_layout=QHBoxLayout(self.bucket_gap_row)
        bucket_gap_layout.setContentsMargins(0,0,0,0)
        bucket_gap_layout.setSpacing(4)
        bucket_gap_layout.addWidget(self.bucket_close_gap)
        bucket_gap_layout.addWidget(self.bucket_gap_width,1)
        bucket_gap_layout.addWidget(self.bucket_gap_width_label)

        self.lasso_inside_boundary=QCheckBox("境界線の内側だけを塗る")
        self.lasso_inside_boundary.setChecked(False)
        self.lasso_main_outline_sub_fill=QCheckBox("サブ色を実線、メイン色を内面にする")
        self.lasso_main_outline_sub_fill.setChecked(False)
        self.lasso_outline_width_label=QLabel("外線の太さ：1.0 px")
        self.lasso_outline_width=QSlider(Qt.Orientation.Horizontal)
        # 0.5 px単位。値2=1.0 px、3=1.5 px、5=2.5 px。
        self.lasso_outline_width.setRange(1,40)
        self.lasso_outline_width.setValue(2)
        self.lasso_outline_width.valueChanged.connect(
            lambda value: self.lasso_outline_width_label.setText(
                f"外線の太さ：{value / 2:.1f} px"
            )
        )
        self.lasso_main_outline_sub_fill.toggled.connect(
            lambda enabled: (
                self.lasso_outline_width_label.setEnabled(enabled),
                self.lasso_outline_width.setEnabled(enabled),
            )
        )
        self.lasso_outline_width_label.setEnabled(False)
        self.lasso_outline_width.setEnabled(False)

        # ラインツール
        self.line_type_label = QLabel("ライン種類")
        self.line_type = QComboBox()
        self.line_type.addItems(["直線", "曲線"])
        self.line_type.setToolTip(
            "曲線は、1回目のドラッグで始点と終点を決め、"
            "次のクリックで弓なりのカーブを確定します。"
        )
        self.line_taper_in = QCheckBox("入り")
        self.line_taper_in.setChecked(False)
        self.line_taper_in_size_label = ClickableValueLabel("入りサイズ：0.5 px")
        self.line_taper_in_size_label.setToolTip(
            "クリックすると入りカーブ設定がポップアップします。"
        )
        self.line_taper_in_size_label.clicked.connect(
            lambda global_pos: self._show_line_curve_popup("in", global_pos)
        )
        self.line_taper_in_size = QSlider(Qt.Orientation.Horizontal)
        self.line_taper_in_size.setRange(1, 800)
        self.line_taper_in_size.setValue(1)
        self.line_taper_in_size.valueChanged.connect(
            lambda value: self.line_taper_in_size_label.setText(
                f"入りサイズ：{value / 2:.1f} px"
            )
        )
        self.line_taper_in_curve_label = QLabel("入りカーブ：1.00")
        self.line_taper_in_curve = QSlider(Qt.Orientation.Horizontal)
        self.line_taper_in_curve.setRange(20, 400)
        self.line_taper_in_curve.setValue(100)
        self.line_taper_in_curve.setToolTip(
            "小さいほど緩やかに、値を大きくすると先端付近で急に太くなります。"
        )
        self.line_taper_in_curve.valueChanged.connect(
            lambda value: self.line_taper_in_curve_label.setText(
                f"入りカーブ：{value / 100:.2f}"
            )
        )
        self.line_taper_out = QCheckBox("抜き")
        self.line_taper_out.setChecked(False)
        self.line_taper_out_size_label = ClickableValueLabel("抜きサイズ：0.5 px")
        self.line_taper_out_size_label.setToolTip(
            "クリックすると抜きカーブ設定がポップアップします。"
        )
        self.line_taper_out_size_label.clicked.connect(
            lambda global_pos: self._show_line_curve_popup("out", global_pos)
        )
        self.line_taper_out_size = QSlider(Qt.Orientation.Horizontal)
        self.line_taper_out_size.setRange(1, 800)
        self.line_taper_out_size.setValue(1)
        self.line_taper_out_size.valueChanged.connect(
            lambda value: self.line_taper_out_size_label.setText(
                f"抜きサイズ：{value / 2:.1f} px"
            )
        )
        self.line_taper_out_curve_label = QLabel("抜きカーブ：1.00")
        self.line_taper_out_curve = QSlider(Qt.Orientation.Horizontal)
        self.line_taper_out_curve.setRange(20, 400)
        self.line_taper_out_curve.setValue(100)
        self.line_taper_out_curve.setToolTip(
            "小さいほど緩やかに、値を大きくすると終端付近で急に細くなります。"
        )
        self.line_taper_out_curve.valueChanged.connect(
            lambda value: self.line_taper_out_curve_label.setText(
                f"抜きカーブ：{value / 100:.2f}"
            )
        )
        self.line_taper_in.toggled.connect(
            lambda enabled: (
                self.line_taper_in_size_label.setEnabled(enabled),
                self.line_taper_in_size.setEnabled(enabled),
                self.line_taper_in_curve_label.setEnabled(enabled),
                self.line_taper_in_curve.setEnabled(enabled),
            )
        )
        self.line_taper_out.toggled.connect(
            lambda enabled: (
                self.line_taper_out_size_label.setEnabled(enabled),
                self.line_taper_out_size.setEnabled(enabled),
                self.line_taper_out_curve_label.setEnabled(enabled),
                self.line_taper_out_curve.setEnabled(enabled),
            )
        )
        self.line_taper_in_size_label.setEnabled(False)
        self.line_taper_in_size.setEnabled(False)
        self.line_taper_in_curve_label.setEnabled(False)
        self.line_taper_in_curve.setEnabled(False)
        self.line_taper_out_size_label.setEnabled(False)
        self.line_taper_out_size.setEnabled(False)
        self.line_taper_out_curve_label.setEnabled(False)
        self.line_taper_out_curve.setEnabled(False)

        # 図形ツール
        self.shape_type_label = QLabel("図形種類")
        self.shape_type = QComboBox()
        self.shape_type.addItems(["多角形", "楕円"])
        self.shape_corners_label = QLabel("角の数")
        self.shape_corners = QSpinBox()
        self.shape_corners.setRange(3, 32)
        self.shape_corners.setValue(4)
        self.shape_corners_slider = QSlider(Qt.Orientation.Horizontal)
        self.shape_corners_slider.setRange(3, 32)
        self.shape_corners_slider.setValue(4)
        self.shape_corners_slider.valueChanged.connect(self.shape_corners.setValue)
        self.shape_corners.valueChanged.connect(self.shape_corners_slider.setValue)
        self.shape_type.currentTextChanged.connect(
            lambda value: (
                self.shape_corners_label.setEnabled(value == "多角形"),
                self.shape_corners.setEnabled(value == "多角形"),
                self.shape_corners_slider.setEnabled(value == "多角形"),
            )
        )
        self.shape_lock_ratio = QCheckBox("比率固定")
        self.shape_fill_inside = QCheckBox("内側を塗る")
        self.shape_sub_outline_main_fill = QCheckBox(
            "サブ色を実線、メイン色を内面にする"
        )
        self.shape_outline_width_label = QLabel("線の太さ：1.0 px")
        self.shape_outline_width = QSlider(Qt.Orientation.Horizontal)
        self.shape_outline_width.setRange(1, 80)
        self.shape_outline_width.setValue(2)
        self.shape_outline_width.valueChanged.connect(
            lambda value: self.shape_outline_width_label.setText(
                f"線の太さ：{value / 2:.1f} px"
            )
        )

        self.bucket_require_closed=QCheckBox(
            "領域が開いている場合は塗りを開始しない"
        )
        self.bucket_require_closed.setChecked(False)

        def sync_bucket_options(_checked=None):
            adjacent = self.bucket_adjacent.isChecked()
            close_gap = (
                adjacent
                and self.bucket_close_gap.isChecked()
            )
            self.bucket_close_gap.setEnabled(adjacent)
            self.bucket_require_closed.setEnabled(adjacent)
            self.bucket_gap_width.setEnabled(close_gap)
            self.bucket_gap_width_label.setEnabled(close_gap)

        self.bucket_adjacent.toggled.connect(sync_bucket_options)
        self.bucket_close_gap.toggled.connect(sync_bucket_options)
        sync_bucket_options()

        self.dust_mode_label=QLabel("処理モード")
        self.dust_mode=QComboBox()
        self.dust_mode.addItems(["ゴミ取り", "塗り抜け"])
        self.dust_mode.setToolTip(
            "ゴミ取り：小さな色点を白（#FFFFFF）へ変更します。"
            "塗り抜け：小さな白い穴を周囲色で埋めます。"
        )
        self.dust_size_label=QLabel("適用サイズ：3 px")
        self.dust_size=QSlider(Qt.Orientation.Horizontal)
        self.dust_size.setRange(1,100)
        self.dust_size.setValue(3)
        self.dust_size.valueChanged.connect(
            lambda value: self.dust_size_label.setText(
                f"適用サイズ：{value} px"
            )
        )
        self.dust_selected_only=QCheckBox("選択色を対象")
        self.dust_selected_only.setChecked(False)
        self.dust_selected_only.setToolTip(
            "使用色パネルで選択している色だけを対象にします。"
            "色ごとに独立判定するため、別色と隣接していても"
            "小さな選択色を削除できます。"
        )
        self.dust_all_frames=QCheckBox(
            "選択レイヤーのすべてのコマに適用"
        )
        self.dust_all_frames.setChecked(False)
        self.dust_apply=QPushButton("ゴミ取りを適用")
        self.dust_mode.currentTextChanged.connect(
            lambda mode: self.dust_apply.setText(
                f"{mode}を適用"
            )
        )
        self.mesh_commit.clicked.connect(self.selectionCommitRequested)
        self.mesh_cancel.clicked.connect(self.selectionCancelRequested)
        self.flip_h.clicked.connect(lambda:self.flipLayerRequested.emit(True))
        self.flip_v.clicked.connect(lambda:self.flipLayerRequested.emit(False))
        self.dust_apply.clicked.connect(self.removeDustRequested)
        for command in (
            self.selection_transform,self.selection_scale,self.selection_mesh,
            self.selection_clear,self.selection_rotate_left,self.selection_rotate_right,
            self.transform_mesh_grid_label,
            self.transform_mesh_grid_x,self.transform_mesh_grid_x_slider,
            self.transform_mesh_grid_y,self.transform_mesh_grid_y_slider,
            self.transform_quality,self.transform_line_width_note,
            self.transform_line_width_label,self.transform_line_width,
            self.selection_all_frames,
            self.mesh_commit,self.mesh_cancel,
            self.flip_h,self.flip_v,
            self.line_type_label,self.line_type,
            self.line_taper_in,self.line_taper_in_size_label,self.line_taper_in_size,
            self.line_taper_in_curve_label,self.line_taper_in_curve,
            self.line_taper_out,self.line_taper_out_size_label,self.line_taper_out_size,
            self.line_taper_out_curve_label,self.line_taper_out_curve,
            self.shape_type_label,self.shape_type,
            self.shape_corners_label,self.shape_corners,self.shape_corners_slider,
            self.shape_lock_ratio,self.shape_fill_inside,
            self.shape_sub_outline_main_fill,
            self.shape_outline_width_label,self.shape_outline_width,
            self.lasso_inside_boundary,self.lasso_main_outline_sub_fill,
            self.lasso_outline_width_label,self.lasso_outline_width,
            self.bucket_adjacent,self.bucket_include_sub,
            self.bucket_gap_row,
            self.bucket_require_closed,
            self.dust_mode_label,self.dust_mode,
            self.dust_size_label,self.dust_size,
            self.dust_selected_only,
            self.dust_all_frames,self.dust_apply
        ):
            self.command_layout.addWidget(command)
        v.addWidget(self.command_box)

        self.size_title=QLabel("<b>ブラシサイズ</b>")
        size_title_row = QHBoxLayout()
        size_title_row.setContentsMargins(0, 0, 0, 0)
        size_title_row.addWidget(self.size_title)
        size_title_row.addStretch()
        self.pressure_settings_button = QPushButton("筆圧…")
        self.pressure_settings_button.setFixedHeight(24)
        self.pressure_settings_button.clicked.connect(
            lambda: self.brush_size_spinbox.pressureRequested.emit()
        )
        size_title_row.addWidget(self.pressure_settings_button)
        v.addLayout(size_title_row)
        self.brush_size_spinbox = BrushSizeSpinBox()
        self.brush_size_spinbox.setRange(0.5, 400.0)
        self.brush_size_spinbox.setSingleStep(0.5)
        self.brush_size_spinbox.setDecimals(1)
        self.brush_size_spinbox.setValue(8.0)
        self.size_slider=QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(1, 800)
        self.size_slider.setValue(16)
        self.brush_size_spinbox.valueChanged.connect(
            lambda value: self.size_slider.setValue(int(round(value * 2)))
        )
        self.size_slider.valueChanged.connect(
            lambda value: self.brush_size_spinbox.setValue(value / 2.0)
        )
        v.addWidget(self.size_slider); v.addWidget(self.brush_size_spinbox)

        self.brush_stabilizer_label = QLabel("手振れ補正：0")
        self.brush_stabilizer = QSlider(Qt.Orientation.Horizontal)
        self.brush_stabilizer.setRange(0, 300)
        self.brush_stabilizer.setValue(0)
        self.brush_stabilizer.setToolTip(
            "ブラシ軌跡を移動平均と遅延半径で滑らかにします。"
            "0～300。値が大きいほど補正を強くします。"
        )
        self.brush_stabilizer.valueChanged.connect(
            lambda value: self.brush_stabilizer_label.setText(
                f"手振れ補正：{value}"
            )
        )
        v.addWidget(self.brush_stabilizer_label)
        v.addWidget(self.brush_stabilizer)

        # Photoshop風の前景色／背景色スタック。
        self.drawing_color_box = QWidget()
        color_layout = QVBoxLayout(self.drawing_color_box)
        color_layout.setContentsMargins(6, 5, 6, 5)
        color_layout.setSpacing(3)
        swatch_row = QHBoxLayout()
        swatch_row.setContentsMargins(0, 0, 0, 0)
        swatch_row.setSpacing(7)
        self.color_swatch_stack = QWidget()
        self.color_swatch_stack.setFixedSize(104, 76)
        self.sub_btn = SwatchEyedropButton(self.color_swatch_stack)
        self.main_btn = SwatchEyedropButton(self.color_swatch_stack)
        # No "メイン/サブ" caption text — the colour itself is the label.
        self.sub_btn.setText("")
        self.main_btn.setText("")
        self.sub_btn.setGeometry(38, 25, 58, 46)
        self.main_btn.setGeometry(7, 4, 58, 46)
        self.main_btn.raise_()
        self.main_btn.setToolTip("クリック：メイン色を選択／ドラッグ：スポイト")
        self.sub_btn.setToolTip("クリック：サブ色を選択／ドラッグ：スポイト")
        controls = QVBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(3)
        self.swap_colors_button = QPushButton("⇄  切り替え")
        self.reset_colors_button = QPushButton("◩  初期色")
        self.transparent_btn = QPushButton("透明色")
        for button in (
            self.swap_colors_button,
            self.reset_colors_button,
            self.transparent_btn,
        ):
            button.setFixedHeight(22)
            button.setStyleSheet("QPushButton{font-size:10px;padding:1px 5px;}")
        controls.addWidget(self.swap_colors_button)
        controls.addWidget(self.reset_colors_button)
        controls.addWidget(self.transparent_btn)
        swatch_row.addWidget(self.color_swatch_stack)
        swatch_row.addLayout(controls, 1)
        color_layout.addLayout(swatch_row)

        # 背景色スウォッチ：メイン/サブの下に配置。クリックで背景色（透明表示色）
        # を描画色として選択、右クリックで表示色そのものを変更する。
        bg_row = QHBoxLayout()
        bg_row.setContentsMargins(0, 0, 0, 0)
        bg_row.setSpacing(7)
        self.background_label = QLabel("背景色")
        self.background_label.setStyleSheet("font-size:10px;")
        self.background_btn = SwatchEyedropButton()
        self.background_btn.setFixedSize(58, 22)
        self.background_btn.setToolTip(
            "クリック：背景色で描画／右クリック：背景色の表示色を変更"
        )
        self.background_btn.clicked.connect(
            lambda: self.set_color_mode("transparent")
        )
        self.background_btn.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.background_btn.customContextMenuRequested.connect(
            lambda _p: self.backgroundColorRequested.emit()
        )
        bg_row.addWidget(self.background_btn)
        bg_row.addWidget(self.background_label)
        bg_row.addStretch(1)
        color_layout.addLayout(bg_row)
        self.main_btn.clicked.connect(lambda: self.set_color_mode("main"))
        self.sub_btn.clicked.connect(lambda: self.set_color_mode("sub"))
        self.swap_colors_button.clicked.connect(self.swapMainSubRequested)
        self.reset_colors_button.clicked.connect(self.resetMainSubRequested)
        self.transparent_btn.clicked.connect(
            lambda: self.set_color_mode("transparent")
        )
        self.transparent_btn.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.transparent_btn.customContextMenuRequested.connect(
            lambda _p: self.backgroundColorRequested.emit()
        )
        color_layout.addStretch(1)

        # カラーサークルドック：色相リング／HSV(四角)／HLS(三角)を切替可能。
        self.wheel_mode = HSVColorWheel.DEFAULT_MODE
        self.wheel_hue_mode = HSVColorWheel.DEFAULT_HUE_MODE
        self.color_wheel_box = QWidget()
        self.color_wheel_box.setObjectName("colorPickerSurface")
        _wheel_c = theme.palette()
        self.color_wheel_box.setStyleSheet(
            "QWidget#colorPickerSurface{background:%s;"
            "border:1px solid %s;border-radius:6px;}"
            % (_wheel_c["surface_alt"], _wheel_c["border"])
        )
        wheel_layout = QVBoxLayout(self.color_wheel_box)
        wheel_layout.setContentsMargins(3, 3, 3, 3)
        wheel_layout.setSpacing(2)
        self.hsv_wheel = HSVColorWheel()
        self.hsv_wheel.colorChanged.connect(self.wheel_color_changed)
        wheel_layout.addWidget(self.hsv_wheel)
        wheel_layout.addStretch(1)

        # カラースライダードック：RGB／HLS／CMYKを切替可能。
        self.slider_mode = "RGB"
        self.color_slider_box = QWidget()
        slider_box_layout = QVBoxLayout(self.color_slider_box)
        slider_box_layout.setContentsMargins(3, 3, 3, 3)
        slider_box_layout.setSpacing(2)
        self.slider_box=QWidget(); self.slider_layout=QFormLayout(self.slider_box); self.slider_layout.setContentsMargins(4,4,4,4); self.slider_layout.setVerticalSpacing(3)
        slider_box_layout.addWidget(self.slider_box)
        slider_box_layout.addStretch(1)
        self.color_sliders=[]; self.color_value_labels=[]; self.rebuild_color_sliders(self.slider_mode)
        # サイズ欄は▲▼のみ、RGB/HSV数値欄は▲▼と半角数値入力に対応。
        for numeric in self.findChildren(QAbstractSpinBox):
            is_size_numeric = numeric is self.brush_size_spinbox
            is_color_numeric = (
                isinstance(numeric, SliderValueSpinBox)
            )
            is_interactive_numeric = (
                is_size_numeric or is_color_numeric
            )

            numeric.setReadOnly(not is_interactive_numeric)
            numeric.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                not is_interactive_numeric,
            )
            if not is_interactive_numeric:
                numeric.setButtonSymbols(
                    QAbstractSpinBox.ButtonSymbols.NoButtons
                )

            editor = numeric.lineEdit()
            if is_color_numeric:
                numeric.setFocusPolicy(
                    Qt.FocusPolicy.StrongFocus
                )
                if editor is not None:
                    editor.setReadOnly(False)
                    editor.setFocusPolicy(
                        Qt.FocusPolicy.StrongFocus
                    )
            elif is_size_numeric:
                numeric.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                if editor is not None:
                    editor.setReadOnly(True)
                    editor.setFocusPolicy(
                        Qt.FocusPolicy.NoFocus
                    )
            else:
                numeric.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                if editor is not None:
                    editor.setReadOnly(True)
                    editor.setFocusPolicy(
                        Qt.FocusPolicy.NoFocus
                    )

        v.addStretch(); self.select_tool("brush"); self.refresh_swatches()

    def _close_line_curve_popup(self):
        popup = self._line_curve_popup
        self._line_curve_popup = None
        if popup is not None:
            popup.close()
            popup.deleteLater()

    def _show_line_curve_popup(self, which, global_position):
        self._close_line_curve_popup()
        if which == "in":
            title = "入りカーブ"
            target = self.line_taper_in_curve
        else:
            title = "抜きカーブ"
            target = self.line_taper_out_curve
        popup = LineTaperCurvePopup(title, target.value(), self)
        self._line_curve_popup = popup
        popup.curveChanged.connect(target.setValue)
        popup.destroyed.connect(
            lambda _obj=None, current=popup:
            setattr(
                self,
                "_line_curve_popup",
                None if self._line_curve_popup is current
                else self._line_curve_popup,
            )
        )
        popup.adjustSize()
        screen = QApplication.screenAt(global_position) or QApplication.primaryScreen()
        position = QPoint(global_position)
        if screen is not None:
            available = screen.availableGeometry()
            width = popup.sizeHint().width()
            height = popup.sizeHint().height()
            position.setX(
                max(available.left(), min(position.x(), available.right() - width + 1))
            )
            position.setY(
                max(available.top(), min(position.y(), available.bottom() - height + 1))
            )
        popup.move(position)
        popup.show()
        popup.raise_()

    def _sync_transform_quality_options(self, enabled):
        self.selection_all_frames.setToolTip(
            "選択範囲の変形をすべてのコマへ適用します。"
            + ("クオリティ変形はコマごとに時間がかかります。"
               if bool(enabled) else "")
        )

    def _refresh_transform_line_width_visibility(self):
        visible = (
            self.active_tool in ("lasso", "rect_select", "auto_select")
            and bool(getattr(self, "_has_transform_line_colors", False))
            and not bool(getattr(self, "_tween_active", False))
        )
        self.transform_line_width_label.setVisible(visible)
        self.transform_line_width.setVisible(visible)
        self.transform_line_width_note.setVisible(
            self.active_tool in ("lasso", "rect_select", "auto_select")
            and not bool(getattr(self, "_tween_active", False))
        )

    def set_tween_active(self, active):
        self._tween_active = bool(active)
        self._refresh_transform_line_width_visibility()

    def set_transform_line_colors_available(self, available):
        self._has_transform_line_colors = bool(available)
        self._refresh_transform_line_width_visibility()

    def toggle_selection_all_frames(self):
        if self.selection_all_frames.isEnabled():
            self.selection_all_frames.toggle()

    def select_tool(self, tid):
        self.active_tool=tid
        self.active.setText("使用中："+dict(self.TOOLS)[tid])
        self.toolChanged.emit(tid)

        is_selection = tid in ("lasso", "rect_select", "auto_select")
        is_line = tid == "line"
        is_shape = tid == "shape"
        is_bucket = tid == "bucket"
        is_dust = tid == "dust"
        is_lasso_fill = tid == "lasso_fill"
        uses_size = tid in ("brush", "line")

        self.command_box.setVisible(
            is_selection or is_line or is_shape
            or is_bucket or is_dust or is_lasso_fill
        )
        for widget in (
            self.selection_transform,self.selection_scale,self.selection_mesh,
            self.selection_clear,self.selection_rotate_left,self.selection_rotate_right,
            self.transform_mesh_grid_label,
            self.transform_mesh_grid_x,self.transform_mesh_grid_x_slider,
            self.transform_mesh_grid_y,self.transform_mesh_grid_y_slider,
            self.transform_quality,self.transform_line_width_note,
            self.selection_all_frames
        ):
            widget.setVisible(is_selection)
        self._refresh_transform_line_width_visibility()
        self.mesh_commit.setVisible(is_selection)
        self.mesh_cancel.setVisible(is_selection)
        self.flip_h.setVisible(False)
        self.flip_v.setVisible(False)

        for widget in (
            self.line_type_label,self.line_type,
            self.line_taper_in,self.line_taper_in_size_label,self.line_taper_in_size,
            self.line_taper_out,self.line_taper_out_size_label,self.line_taper_out_size,
        ):
            widget.setVisible(is_line)
        # 入り／抜きカーブはサイズ数値クリック時のポップアップだけで調整する。
        self.line_taper_in_curve_label.setVisible(False)
        self.line_taper_in_curve.setVisible(False)
        self.line_taper_out_curve_label.setVisible(False)
        self.line_taper_out_curve.setVisible(False)
        self.line_taper_in_size_label.setEnabled(
            is_line and self.line_taper_in.isChecked()
        )
        self.line_taper_in_size.setEnabled(
            is_line and self.line_taper_in.isChecked()
        )
        self.line_taper_in_curve_label.setEnabled(
            is_line and self.line_taper_in.isChecked()
        )
        self.line_taper_in_curve.setEnabled(
            is_line and self.line_taper_in.isChecked()
        )
        self.line_taper_out_size_label.setEnabled(
            is_line and self.line_taper_out.isChecked()
        )
        self.line_taper_out_size.setEnabled(
            is_line and self.line_taper_out.isChecked()
        )
        self.line_taper_out_curve_label.setEnabled(
            is_line and self.line_taper_out.isChecked()
        )
        self.line_taper_out_curve.setEnabled(
            is_line and self.line_taper_out.isChecked()
        )

        for widget in (
            self.shape_type_label,self.shape_type,
            self.shape_corners_label,self.shape_corners,self.shape_corners_slider,
            self.shape_lock_ratio,self.shape_fill_inside,
            self.shape_sub_outline_main_fill,
            self.shape_outline_width_label,self.shape_outline_width,
        ):
            widget.setVisible(is_shape)
        polygon_enabled = is_shape and self.shape_type.currentText() == "多角形"
        self.shape_corners_label.setEnabled(polygon_enabled)
        self.shape_corners.setEnabled(polygon_enabled)
        self.shape_corners_slider.setEnabled(polygon_enabled)

        self.lasso_inside_boundary.setVisible(is_lasso_fill)
        self.lasso_main_outline_sub_fill.setVisible(is_lasso_fill)
        self.lasso_outline_width_label.setVisible(is_lasso_fill)
        self.lasso_outline_width.setVisible(is_lasso_fill)
        self.lasso_outline_width_label.setEnabled(
            is_lasso_fill and self.lasso_main_outline_sub_fill.isChecked()
        )
        self.lasso_outline_width.setEnabled(
            is_lasso_fill and self.lasso_main_outline_sub_fill.isChecked()
        )
        uses_bucket_region = is_bucket or tid == "auto_select"
        self.bucket_adjacent.setVisible(uses_bucket_region)
        self.bucket_include_sub.setVisible(is_bucket)
        self.bucket_gap_row.setVisible(uses_bucket_region)
        self.bucket_require_closed.setVisible(uses_bucket_region)

        self.dust_mode_label.setVisible(is_dust)
        self.dust_mode.setVisible(is_dust)
        self.dust_size_label.setVisible(is_dust)
        self.dust_size.setVisible(is_dust)
        self.dust_selected_only.setVisible(is_dust)
        self.dust_all_frames.setVisible(is_dust)
        self.dust_apply.setVisible(is_dust)

        self.size_title.setText(
            "<b>ラインサイズ</b>" if is_line else "<b>ブラシサイズ</b>"
        )
        self.size_title.setVisible(uses_size)
        self.brush_size_spinbox.setPressurePopupEnabled(tid == "brush")
        # ラインは筆圧を検出しないため、筆圧設定はブラシ時だけ表示する。
        self.pressure_settings_button.setVisible(tid == "brush")
        self.size_slider.setVisible(uses_size)
        self.brush_size_spinbox.setVisible(uses_size)
        self.brush_stabilizer_label.setVisible(tid == "brush")
        self.brush_stabilizer.setVisible(tid == "brush")


    def set_color_mode(self, mode):
        self.color_mode=mode; self.refresh_swatches(); self.sync_sliders(); self.colorModeChanged.emit(mode)

    def set_colors(self, main, sub, mode, transparent_display=None):
        self.main_color=QColor(main); self.sub_color=QColor(sub); self.color_mode=mode
        if transparent_display is not None: self.transparent_display_color=QColor(transparent_display)
        self.refresh_swatches(); self.sync_sliders()

    def refresh_swatches(self):
        palette = theme.palette()
        accent = palette["accent"]
        hover = palette["accent_hover"]
        def style(c, selected):
            fg = "white" if c.lightness() < 110 else "black"
            border = (
                f"3px solid {accent}" if selected else f"2px solid {palette['border']}"
            )
            return (
                f"QPushButton{{background:{c.name()};color:{fg};border:{border};"
                "font-size:10px;font-weight:600;padding:2px;border-radius:4px;}"
                f"QPushButton:hover{{border-color:{hover};}}"
            )
        self.main_btn.setStyleSheet(style(self.main_color,self.color_mode=="main"))
        self.sub_btn.setStyleSheet(style(self.sub_color,self.color_mode=="sub"))
        self.background_btn.setStyleSheet(
            style(self.transparent_display_color, self.color_mode == "transparent")
        )
        self.transparent_btn.setStyleSheet(
            "QPushButton{font-size:10px;padding:1px 5px;border-radius:4px;"
            + (
                f"border:2px solid {accent};}}"
                if self.color_mode == "transparent" else
                f"border:1px solid {palette['border']};}}"
            )
        )
        if self.color_mode == "sub":
            self.sub_btn.raise_()
        else:
            self.main_btn.raise_()

    def apply_theme(self):
        """Re-apply palette-derived styling after a theme/accent change."""
        c = theme.palette()
        self.color_wheel_box.setStyleSheet(
            "QWidget#colorPickerSurface{background:%s;"
            "border:1px solid %s;border-radius:6px;}" % (c["surface_alt"], c["border"])
        )
        self.active.setStyleSheet(
            "background:%s;color:%s;padding:6px;font-weight:bold;"
            "border-radius:4px;" % (c["accent"], c["accent_text"])
        )
        self.refresh_swatches()
        self.update_slider_gradients()

    def clear_slider_layout(self):
        while self.slider_layout.rowCount(): self.slider_layout.removeRow(0)
        self.color_sliders=[]
        self.color_value_labels=[]

    #: スライダーモードごとのチャンネル定義（表示名, 最小, 最大）。
    SLIDER_SPECS = {
        "RGB": [("R", 0, 255), ("G", 0, 255), ("B", 0, 255)],
        "HLS": [("H", 0, 359), ("L", 0, 255), ("S", 0, 255)],
        "CMYK": [("C", 0, 255), ("M", 0, 255), ("Y", 0, 255), ("K", 0, 255)],
    }

    def set_wheel_mode(self, mode):
        mode = str(mode).upper()
        if mode not in HSVColorWheel.MODES:
            return
        self.wheel_mode = mode
        self.hsv_wheel.setMode(mode)

    def set_wheel_hue_mode(self, mode):
        mode = str(mode).upper()
        if mode not in HSVColorWheel.HUE_MODES:
            return
        self.wheel_hue_mode = mode
        self.hsv_wheel.setHueMode(mode)

    def set_slider_mode(self, mode):
        mode = str(mode).upper()
        if mode not in self.SLIDER_SPECS or mode == self.slider_mode:
            return
        self.slider_mode = mode
        self.rebuild_color_sliders(mode)

    def rebuild_color_sliders(self, mode):
        mode = str(mode).upper()
        if mode not in self.SLIDER_SPECS:
            mode = "RGB"
        self.slider_mode = mode
        self.clear_slider_layout()
        for name, lo, hi in self.SLIDER_SPECS[mode]:
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(lo, hi)
            slider.setSingleStep(1)
            slider.setProperty("valueScale", 1.0)
            slider.setProperty("channel", name)
            value_control = SliderValueSpinBox(slider, scale=1.0, step=1.0)
            if mode == "HLS" and name == "H":
                value_control.setWrapping(True)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(slider, 1)
            row_layout.addWidget(value_control)
            slider.valueChanged.connect(self.slider_color_changed)
            self.slider_layout.addRow(name, row)
            self.color_sliders.append(slider)
            self.color_value_labels.append(value_control)
        self.sync_sliders()
        self.update_slider_gradients()

    def active_color(self): return self.main_color if self.color_mode=="main" else self.sub_color

    def _channel_values_from_color(self, color):
        """現在のスライダーモードにおける各チャンネル値を返す。"""
        if self.slider_mode == "RGB":
            return [color.red(), color.green(), color.blue()]
        if self.slider_mode == "HLS":
            return [
                max(0, color.hslHue()),
                color.lightness(),
                color.hslSaturation(),
            ]
        # CMYK
        cyan, magenta, yellow, black, _alpha = color.getCmyk()
        return [cyan, magenta, yellow, black]

    def _color_from_channel_values(self, values):
        """スライダー値から現在のモードに応じた色を組み立てる。"""
        clamp = lambda v: max(0, min(255, int(v)))
        if self.slider_mode == "RGB":
            return QColor(clamp(values[0]), clamp(values[1]), clamp(values[2]))
        if self.slider_mode == "HLS":
            hue = max(0, min(359, int(values[0])))
            return QColor.fromHsl(hue, clamp(values[2]), clamp(values[1]))
        return QColor.fromCmyk(
            clamp(values[0]), clamp(values[1]), clamp(values[2]), clamp(values[3])
        )

    def sync_sliders(self):
        self.hsv_wheel.setEnabled(self.color_mode != "transparent")
        if self.color_mode != "transparent":
            self.hsv_wheel.setColor(self.active_color())
        if not self.color_sliders or self.color_mode == "transparent":
            return
        values = self._channel_values_from_color(self.active_color())
        for index, slider in enumerate(self.color_sliders):
            if index >= len(values):
                break
            scale = float(slider.property("valueScale") or 1.0)
            slider_value = int(round(float(values[index]) * scale))
            slider_value = max(
                slider.minimum(),
                min(slider.maximum(), slider_value),
            )
            slider.blockSignals(True)
            slider.setValue(slider_value)
            slider.blockSignals(False)
            if index < len(self.color_value_labels):
                self.color_value_labels[index].setDisplayValue(
                    slider_value / scale
                )
        self.update_slider_gradients()

    def update_slider_gradients(self):
        specs = self.SLIDER_SPECS.get(self.slider_mode)
        if not specs or len(self.color_sliders) != len(specs):
            return
        active = self.active_color() if self.color_mode != "transparent" else QColor("black")
        if self.slider_mode == "RGB":
            gradients = [
                "stop:0 rgb(0,%d,%d), stop:1 rgb(255,%d,%d)" % (active.green(), active.blue(), active.green(), active.blue()),
                "stop:0 rgb(%d,0,%d), stop:1 rgb(%d,255,%d)" % (active.red(), active.blue(), active.red(), active.blue()),
                "stop:0 rgb(%d,%d,0), stop:1 rgb(%d,%d,255)" % (active.red(), active.green(), active.red(), active.green()),
            ]
        elif self.slider_mode == "HLS":
            hue = max(0, active.hslHue())
            sat = max(1, active.hslSaturation())
            mid = QColor.fromHsl(hue, sat, 128).name()
            gray = QColor.fromHsl(hue, 0, 128).name()
            pure = QColor.fromHsl(hue, sat, 128).name()
            gradients = [
                "stop:0 #ff0000, stop:0.17 #ffff00, stop:0.33 #00ff00, stop:0.50 #00ffff, stop:0.67 #0000ff, stop:0.83 #ff00ff, stop:1 #ff0000",
                f"stop:0 #000000, stop:0.5 {mid}, stop:1 #ffffff",
                f"stop:0 {gray}, stop:1 {pure}",
            ]
        else:  # CMYK
            gradients = [
                "stop:0 #ffffff, stop:1 #00ffff",
                "stop:0 #ffffff, stop:1 #ff00ff",
                "stop:0 #ffffff, stop:1 #ffff00",
                "stop:0 #ffffff, stop:1 #000000",
            ]
        c = theme.palette()
        for slider, gradient in zip(self.color_sliders, gradients):
            slider.setStyleSheet(
                "QSlider::groove:horizontal{height:14px;border:1px solid %s;"
                "border-radius:7px;"
                "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,%s);}"
                # Keep the gradient fully visible: the filled/empty halves must
                # not paint over it (the global theme fills sub-page with accent).
                "QSlider::sub-page:horizontal{background:transparent;}"
                "QSlider::add-page:horizontal{background:transparent;}"
                # Round, ring-style handle that reveals the colour beneath it.
                "QSlider::handle:horizontal{width:14px;height:14px;margin:-3px 0;"
                "border:2px solid white;background:transparent;border-radius:9px;}"
                % (c["border"], gradient)
            )

    def slider_color_changed(self):
        specs = self.SLIDER_SPECS.get(self.slider_mode)
        if (
            self.color_mode == "transparent"
            or not specs
            or len(self.color_sliders) != len(specs)
        ):
            return
        values = [slider.value() for slider in self.color_sliders]
        color = self._color_from_channel_values(values)

        if self.color_mode == "main":
            self.main_color = color
        else:
            self.sub_color = color
        self.refresh_swatches()
        self.hsv_wheel.setColor(color)
        self.update_slider_gradients()
        self.colorChanged.emit(self.color_mode, color)

    def wheel_color_changed(self, color):
        if self.color_mode == "transparent":
            return
        color = QColor(color)
        if self.color_mode == "main":
            self.main_color = color
        else:
            self.sub_color = color
        self.refresh_swatches()
        self.sync_sliders()
        self.colorChanged.emit(self.color_mode, color)
