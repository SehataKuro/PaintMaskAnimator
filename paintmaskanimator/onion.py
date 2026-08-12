from .common import *  # noqa: F401,F403
from . import theme
from .logging_setup import get_logger

log = get_logger(__name__)


class OnionOpacityGraph(QWidget):
    """表示枚数と同数の縦スライダーで、コマ別の濃度を設定する。"""

    levelsChanged = Signal()

    def __init__(
        self,
        count=1,
        levels=None,
        reverse_order=False,
        parent=None,
    ):
        super().__init__(parent)
        self._reverse_order = bool(reverse_order)
        self._sliders = []
        self._values = [
            max(0, min(100, int(value)))
            for value in (levels or [])
        ]
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.setFixedHeight(102)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.setToolTip(
            "各縦スライダーが、近いコマから順に1枚ずつ対応します。"
            "上ほど濃く、下ほど薄く表示します。"
        )
        self.set_count(count)

    @staticmethod
    def _default_level(index, count):
        if count <= 1:
            return 100
        return max(
            10,
            min(
                100,
                int(round(
                    100.0
                    - 55.0 * index / float(max(1, count - 1))
                )),
            ),
        )

    def _clear_layout(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def set_count(self, count):
        count = max(0, min(12, int(count)))
        current = self.levels()
        if not current:
            current = list(self._values)
        self._clear_layout()
        self._sliders = [None] * count

        visual_indices = (
            range(count - 1, -1, -1)
            if self._reverse_order
            else range(count)
        )
        for index in visual_indices:
            value = (
                current[index]
                if index < len(current)
                else self._default_level(index, count)
            )
            column = QWidget()
            column.setFixedWidth(22)
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(0)

            number = QLabel(str(index + 1))
            number.setFixedSize(22, 14)
            number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            number.setStyleSheet(
                "font-size:9px;padding:0px;margin:0px;"
            )
            number.setToolTip(
                f"現在コマから{index + 1}枚離れたオニオンスキン"
            )

            slider = QSlider(Qt.Orientation.Vertical)
            slider.setRange(0, 100)
            slider.setSingleStep(1)
            slider.setPageStep(10)
            slider.setValue(max(0, min(100, int(value))))
            slider.setFixedSize(16, 70)
            slider.setTickPosition(QSlider.TickPosition.NoTicks)
            _c = theme.palette()
            slider.setStyleSheet(
                "QSlider::groove:vertical{width:4px;"
                "background:%s;border-radius:2px;}"
                "QSlider::handle:vertical{height:12px;width:12px;"
                "margin:0 -6px;background:%s;"
                "border:2px solid %s;border-radius:8px;}"
                % (_c["border"], _c["surface"], _c["accent"])
            )
            slider.setToolTip(
                f"{index + 1}枚目の濃度：{slider.value()}%"
            )

            value_label = QLabel(f"{slider.value()}")
            value_label.setFixedSize(22, 14)
            value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value_label.setStyleSheet(
                "font-size:9px;padding:0px;margin:0px;"
            )

            slider.valueChanged.connect(
                lambda value, target=slider, label=value_label: (
                    target.setToolTip(
                        f"コマ別濃度：{int(value)}%"
                    ),
                    label.setText(str(int(value))),
                    self.levelsChanged.emit(),
                )
            )

            column_layout.addWidget(number)
            column_layout.addWidget(
                slider,
                1,
                Qt.AlignmentFlag.AlignHCenter,
            )
            column_layout.addWidget(value_label)
            self._layout.addWidget(
                column,
                0,
                Qt.AlignmentFlag.AlignLeft,
            )
            self._sliders[index] = slider

        # 余分な伸縮領域を置かず、表示枚数分だけの幅にする。
        self.setFixedWidth(max(22, count * 22))
        self._values = self.levels()

    def levels(self):
        if self._sliders:
            return [int(slider.value()) for slider in self._sliders]
        return list(self._values)

    def set_levels(self, levels):
        values = [
            max(0, min(100, int(value)))
            for value in (levels or [])
        ]
        self._values = values
        for index, slider in enumerate(self._sliders):
            value = (
                values[index]
                if index < len(values)
                else self._default_level(index, len(self._sliders))
            )
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)


class OnionScaleSlider(QSlider):
    """100%を中央に置く、低速・1%刻みのTU/TB用スライダー。"""

    def __init__(self, value=100, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        # 100%が物理的にも中央になる対称範囲。
        self.setRange(1, 199)
        self.setSingleStep(1)
        self.setPageStep(1)
        self.setValue(max(1, min(199, int(round(float(value))))))
        self.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.setTickInterval(10)
        self.setToolTip(
            "中央が100%。左で縮小、右で拡大します。"
            "通常は1%ずつ、Shiftを押しながら操作すると3%ずつ動きます。"
        )
        self._slow_dragging = False
        self._slow_drag_start_x = 0.0
        self._slow_drag_start_value = self.value()

    def _step_multiplier(self, modifiers):
        return (
            3
            if modifiers & Qt.KeyboardModifier.ShiftModifier
            else 1
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._slow_dragging = True
            self._slow_drag_start_x = event.globalPosition().x()
            self._slow_drag_start_value = int(self.value())
            try:
                self.grabMouse()
            except RuntimeError as exc:
                log.debug("grabMouse() failed: %s", exc)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._slow_dragging
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            # 2pxで1%。Shift中は同じ移動量で3倍速。
            delta_x = (
                event.globalPosition().x()
                - self._slow_drag_start_x
            )
            raw_steps = int(round(delta_x / 2.0))
            steps = raw_steps * self._step_multiplier(
                event.modifiers()
            )
            self.setValue(
                max(
                    self.minimum(),
                    min(
                        self.maximum(),
                        self._slow_drag_start_value + steps,
                    ),
                )
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            self._slow_dragging
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._slow_dragging = False
            try:
                self.releaseMouse()
            except RuntimeError as exc:
                log.debug("releaseMouse() failed: %s", exc)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Down,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
        ):
            direction = (
                -1
                if key in (Qt.Key.Key_Left, Qt.Key.Key_Down)
                else 1
            )
            self.setValue(
                self.value()
                + direction
                * self._step_multiplier(event.modifiers())
            )
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            direction = 1 if delta > 0 else -1
            self.setValue(
                self.value()
                + direction
                * self._step_multiplier(event.modifiers())
            )
            event.accept()
            return
        super().wheelEvent(event)


class OnionRotationSlider(QSlider):
    """0.5度刻みでゆっくり動くオニオン回転スライダー。"""

    def __init__(self, value=0.0, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        # 内部値は0.1度単位。通常5=0.5度、Shift時30=3度。
        self.setRange(-1800, 1800)
        self.setSingleStep(5)
        self.setPageStep(5)
        self.setValue(
            max(
                self.minimum(),
                min(
                    self.maximum(),
                    int(round(float(value) * 10.0)),
                ),
            )
        )
        self.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.setTickInterval(300)
        self.setToolTip(
            "通常は0.5°ずつゆっくり調整します。"
            "Shiftを押しながら操作すると3°ずつ動きます。"
        )
        self._slow_dragging = False
        self._slow_drag_start_x = 0.0
        self._slow_drag_start_value = int(self.value())

    @staticmethod
    def _step_units(modifiers):
        return (
            30
            if modifiers & Qt.KeyboardModifier.ShiftModifier
            else 5
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._slow_dragging = True
            self._slow_drag_start_x = event.globalPosition().x()
            self._slow_drag_start_value = int(self.value())
            try:
                self.grabMouse()
            except RuntimeError as exc:
                log.debug("grabMouse() failed: %s", exc)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._slow_dragging
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            # 3pxの移動につき、通常0.5°／Shift時3°。
            delta_x = (
                event.globalPosition().x()
                - self._slow_drag_start_x
            )
            steps = int(round(delta_x / 3.0))
            self.setValue(
                max(
                    self.minimum(),
                    min(
                        self.maximum(),
                        self._slow_drag_start_value
                        + steps * self._step_units(event.modifiers()),
                    ),
                )
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            self._slow_dragging
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._slow_dragging = False
            try:
                self.releaseMouse()
            except RuntimeError as exc:
                log.debug("releaseMouse() failed: %s", exc)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Down,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
        ):
            direction = (
                -1
                if key in (Qt.Key.Key_Left, Qt.Key.Key_Down)
                else 1
            )
            self.setValue(
                self.value()
                + direction * self._step_units(event.modifiers())
            )
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            direction = 1 if delta > 0 else -1
            self.setValue(
                self.value()
                + direction * self._step_units(event.modifiers())
            )
            event.accept()
            return
        super().wheelEvent(event)


class OnionSkinSettingsBrowser(QWidget):
    """オニオンスキンを即時調整する一時表示ブラウザ。"""

    settingsChanged = Signal()
    shiftEditRequested = Signal(int, str)
    # 旧バージョンとの互換用。新UIでは使用しない。
    transformEditRequested = Signal(int)
    canvasPositionEditRequested = Signal()
    canvasRotationRequested = Signal(float)
    centerCanvasRequested = Signal(float)

    def __init__(
        self,
        previous_count,
        next_count,
        previous_opacity,
        next_opacity,
        previous_color,
        next_color,
        previous_color_enabled=True,
        next_color_enabled=True,
        selected_colors_only=False,
        previous_shift_x=0.0,
        previous_shift_y=0.0,
        previous_rotation=0.0,
        next_shift_x=0.0,
        next_shift_y=0.0,
        next_rotation=0.0,
        previous_scale=100.0,
        next_scale=100.0,
        previous_levels=None,
        next_levels=None,
        center_percent=50.0,
        canvas_rotation=0.0,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("temporaryOnionSkinSettingsBrowser")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumWidth(320)

        self.previous_color = QColor(previous_color)
        self.next_color = QColor(next_color)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(3)

        explanation = QLabel(
            "X・Yシフトボタンを押すと、キャンバス上のドラッグで"
            "上下左右へ移動できます。回転とTU／TB拡大率は"
            "スライダーで調整します。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.selected_colors_only = QCheckBox(
            "選択した使用色のみ表示"
        )
        self.selected_colors_only.setChecked(
            bool(selected_colors_only)
        )
        self.selected_colors_only.setToolTip(
            "使用色パネルで親・子として選択している色だけを"
            "オニオンスキンに表示します。"
        )
        layout.addWidget(self.selected_colors_only)

        self.tabs = QTabWidget()
        self.tabs.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        layout.addWidget(self.tabs, 0)

        previous_values = self._build_direction_page(
            -1,
            previous_count,
            previous_opacity,
            previous_color_enabled,
            previous_shift_x,
            previous_shift_y,
            previous_rotation,
            previous_scale,
            previous_levels,
        )
        (
            previous_page,
            self.previous_count,
            self.previous_opacity,
            self.previous_opacity_label,
            self.previous_opacity_graph,
            self.previous_color_enabled,
            self.previous_color_button,
            self.previous_shift_x_button,
            self.previous_shift_x,
            self.previous_shift_y_button,
            self.previous_shift_y,
            self.previous_rotation_slider,
            self.previous_rotation,
            self.previous_scale_slider,
            self.previous_scale,
            self.previous_reset_button,
            self.previous_reset_both,
        ) = previous_values
        self.tabs.addTab(previous_page, "前のコマ")

        next_values = self._build_direction_page(
            1,
            next_count,
            next_opacity,
            next_color_enabled,
            next_shift_x,
            next_shift_y,
            next_rotation,
            next_scale,
            next_levels,
        )
        (
            next_page,
            self.next_count,
            self.next_opacity,
            self.next_opacity_label,
            self.next_opacity_graph,
            self.next_color_enabled,
            self.next_color_button,
            self.next_shift_x_button,
            self.next_shift_x,
            self.next_shift_y_button,
            self.next_shift_y,
            self.next_rotation_slider,
            self.next_rotation,
            self.next_scale_slider,
            self.next_scale,
            self.next_reset_button,
            self.next_reset_both,
        ) = next_values
        self.tabs.addTab(next_page, "後のコマ")

        position_panel = QWidget()
        position_form = QFormLayout(position_panel)
        position_form.setContentsMargins(0, 2, 0, 0)
        position_form.setHorizontalSpacing(4)
        position_form.setVerticalSpacing(2)

        self.canvas_position_button = QPushButton("表示位置")
        self.canvas_position_button.setCheckable(True)
        self.canvas_position_button.setToolTip(
            "左ドラッグでキャンバスを移動します。"
            "Shift＋左ドラッグまたは右ドラッグで回転します。"
            "前後のオニオンスキンは画面上の位置・回転・拡大率を"
            "保つよう相対値へ反映されます。"
        )
        position_help = QLabel(
            "左：移動　Shift／右：回転"
        )
        position_help.setWordWrap(True)
        position_form.addRow(
            self.canvas_position_button,
            position_help,
        )

        # 表示位置にも、前後タブと同じ回転UIを用意する。
        self.canvas_rotation_slider = OnionRotationSlider(
            canvas_rotation
        )
        self.canvas_rotation = QDoubleSpinBox()
        self.canvas_rotation.setRange(-180.0, 180.0)
        self.canvas_rotation.setDecimals(1)
        self.canvas_rotation.setSingleStep(0.5)
        self.canvas_rotation.setValue(float(canvas_rotation))
        self._compact_spinbox(
            self.canvas_rotation,
            width=62,
        )
        canvas_degree_label = QLabel("°")
        canvas_degree_label.setFixedWidth(9)
        self.canvas_rotation_slider.valueChanged.connect(
            lambda value: self.canvas_rotation.setValue(
                float(value) / 10.0
            )
        )
        self.canvas_rotation.valueChanged.connect(
            lambda value: self.canvas_rotation_slider.setValue(
                int(round(float(value) * 10.0))
            )
        )
        self.canvas_rotation.valueChanged.connect(
            lambda value: self.canvasRotationRequested.emit(
                float(value)
            )
        )

        canvas_rotation_row = QWidget()
        canvas_rotation_layout = QHBoxLayout(
            canvas_rotation_row
        )
        canvas_rotation_layout.setContentsMargins(0, 0, 0, 0)
        canvas_rotation_layout.setSpacing(2)
        canvas_rotation_layout.addWidget(
            self.canvas_rotation_slider,
            1,
        )
        canvas_rotation_layout.addWidget(
            self.canvas_rotation
        )
        canvas_rotation_layout.addWidget(
            canvas_degree_label
        )
        position_form.addRow(
            "回転",
            canvas_rotation_row,
        )

        self.center_canvas_button = QPushButton(
            "キャンバスを中央位置へ"
        )
        self.center_canvas_button.setToolTip(
            "中央％で指定した前後間の位置・回転・TU／TB拡大率を"
            "キャンバス基準へ取り込みます。"
            "キャンバス回転は0°へ戻し、表示用デジタルズームは"
            "変更しません。"
        )

        # 中央％はボタンと同じ行へ寄せる。
        self.center_percent_slider = QSlider(
            Qt.Orientation.Horizontal
        )
        self.center_percent_slider.setRange(0, 100)
        self.center_percent_slider.setSingleStep(1)
        self.center_percent_slider.setValue(
            max(0, min(100, int(round(float(center_percent)))))
        )
        self.center_percent_value = QSpinBox()
        self.center_percent_value.setRange(0, 100)
        self.center_percent_value.setValue(
            self.center_percent_slider.value()
        )
        self._compact_spinbox(
            self.center_percent_value,
            width=54,
        )
        center_percent_mark = QLabel("%")
        center_percent_mark.setFixedWidth(10)

        self.center_percent_slider.valueChanged.connect(
            self.center_percent_value.setValue
        )
        self.center_percent_value.valueChanged.connect(
            self.center_percent_slider.setValue
        )
        center_row = QWidget()
        center_layout = QHBoxLayout(center_row)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(2)
        center_layout.addWidget(QLabel("中央％"))
        center_layout.addWidget(self.center_percent_slider, 1)
        center_layout.addWidget(self.center_percent_value)
        center_layout.addWidget(center_percent_mark)
        position_form.addRow(
            self.center_canvas_button,
            center_row,
        )

        layout.addWidget(position_panel)

        outline_note = QLabel(
            "オニオンスキン外周は濃度設定に関係なく、"
            "常に100%の1px線で表示されます。"
        )
        outline_note.setWordWrap(True)
        layout.addWidget(outline_note)

        close_button = QPushButton("設定ブラウザを閉じる")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)


        self.previous_color_button.clicked.connect(
            lambda: self._choose_color("previous")
        )
        self.next_color_button.clicked.connect(
            lambda: self._choose_color("next")
        )
        self.previous_reset_button.clicked.connect(
            lambda: self._reset_shift("previous")
        )
        self.next_reset_button.clicked.connect(
            lambda: self._reset_shift("next")
        )
        self.previous_reset_both.toggled.connect(
            lambda checked: self._sync_reset_both(
                self.next_reset_both,
                checked,
            )
        )
        self.next_reset_both.toggled.connect(
            lambda checked: self._sync_reset_both(
                self.previous_reset_both,
                checked,
            )
        )

        self.previous_shift_x_button.clicked.connect(
            lambda: self._request_shift_edit(-1, "xy")
        )
        self.next_shift_x_button.clicked.connect(
            lambda: self._request_shift_edit(1, "xy")
        )
        self.canvas_position_button.clicked.connect(
            self._request_canvas_position_edit
        )
        self.center_canvas_button.clicked.connect(
            lambda: self.centerCanvasRequested.emit(
                float(self.center_percent_value.value())
            )
        )

        for checkbox in (
            self.previous_color_enabled,
            self.next_color_enabled,
            self.selected_colors_only,
        ):
            checkbox.toggled.connect(
                lambda _checked=False: self.settingsChanged.emit()
            )

        self.previous_color_enabled.toggled.connect(
            self.previous_color_button.setEnabled
        )
        self.next_color_enabled.toggled.connect(
            self.next_color_button.setEnabled
        )
        self.previous_color_button.setEnabled(
            self.previous_color_enabled.isChecked()
        )
        self.next_color_button.setEnabled(
            self.next_color_enabled.isChecked()
        )
        self._refresh_color_buttons()

    @staticmethod
    def _compact_spinbox(spinbox, width=72):
        """数字を隠さず、標準▲▼だけを横方向へ圧縮する。"""
        spinbox.setFixedWidth(int(width))
        spinbox.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.UpDownArrows
        )
        spinbox.setStyleSheet(
            "QSpinBox,QDoubleSpinBox{"
            "padding-left:2px;padding-right:9px;}"
            "QSpinBox::up-button,QDoubleSpinBox::up-button{"
            "subcontrol-origin:border;subcontrol-position:top right;"
            "width:8px;padding:0px;margin:0px;}"
            "QSpinBox::down-button,QDoubleSpinBox::down-button{"
            "subcontrol-origin:border;subcontrol-position:bottom right;"
            "width:8px;padding:0px;margin:0px;}"
            "QSpinBox::up-arrow,QDoubleSpinBox::up-arrow,"
            "QSpinBox::down-arrow,QDoubleSpinBox::down-arrow{"
            "width:5px;height:4px;}"
        )
        editor = spinbox.lineEdit()
        if editor is not None:
            editor.setTextMargins(0, 0, 7, 0)
            editor.setAlignment(Qt.AlignmentFlag.AlignRight)

    def _build_direction_page(
        self,
        direction,
        count,
        opacity,
        color_enabled,
        shift_x,
        shift_y,
        rotation,
        scale,
        levels,
    ):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(3, 3, 3, 0)
        outer.setSpacing(2)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(4)
        form.setVerticalSpacing(2)
        outer.addLayout(form)
        page.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )

        count_box = QSpinBox()
        count_box.setRange(0, 12)
        count_box.setValue(int(count))
        count_box.setSuffix(" 枚")
        form.addRow("表示枚数", count_box)

        opacity_graph = OnionOpacityGraph(
            count_box.value(),
            levels,
            reverse_order=(int(direction) < 0),
        )
        form.addRow("コマ別濃度", opacity_graph)

        opacity_slider = QSlider(Qt.Orientation.Horizontal)
        opacity_slider.setRange(1, 100)
        opacity_slider.setValue(
            max(1, min(100, int(round(float(opacity) * 100))))
        )
        opacity_label = QLabel(f"{opacity_slider.value()}%")
        opacity_label.setFixedWidth(38)
        opacity_row = QWidget()
        opacity_layout = QHBoxLayout(opacity_row)
        opacity_layout.setContentsMargins(0, 0, 0, 0)
        opacity_layout.addWidget(opacity_slider, 1)
        opacity_layout.addWidget(opacity_label)
        form.addRow("全体濃度", opacity_row)

        color_enabled_box = QCheckBox("表示色を適用")
        color_enabled_box.setChecked(bool(color_enabled))
        color_button = QPushButton()
        color_row = QWidget()
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.addWidget(color_enabled_box)
        color_layout.addWidget(color_button, 1)
        form.addRow("表示色", color_row)

        shift_xy_button = QPushButton("X・Yシフト")
        shift_xy_button.setCheckable(True)
        shift_xy_button.setToolTip(
            "押した後、キャンバス上を上下左右へドラッグして"
            "X・Yシフトを同時に調整します。"
        )

        shift_x_box = QSpinBox()
        shift_x_box.setRange(-10000, 10000)
        shift_x_box.setSingleStep(1)
        shift_x_box.setValue(
            max(-10000, min(10000, int(round(float(shift_x)))))
        )
        self._compact_spinbox(shift_x_box, width=66)

        shift_y_box = QSpinBox()
        shift_y_box.setRange(-10000, 10000)
        shift_y_box.setSingleStep(1)
        shift_y_box.setValue(
            max(-10000, min(10000, int(round(float(shift_y)))))
        )
        self._compact_spinbox(shift_y_box, width=66)

        shift_row = QWidget()
        shift_layout = QHBoxLayout(shift_row)
        shift_layout.setContentsMargins(0, 0, 0, 0)
        shift_layout.setSpacing(2)
        shift_layout.addWidget(QLabel("X"))
        shift_layout.addWidget(shift_x_box)
        shift_layout.addWidget(QLabel("Y"))
        shift_layout.addWidget(shift_y_box)
        shift_layout.addWidget(QLabel("px"))
        shift_layout.addStretch(1)
        form.addRow(shift_xy_button, shift_row)

        # 既存の属性名との互換性を保ちつつ、同一ボタンを共有する。
        shift_x_button = shift_xy_button
        shift_y_button = shift_xy_button

        rotation_slider = OnionRotationSlider(rotation)
        rotation_box = QDoubleSpinBox()
        rotation_box.setRange(-180.0, 180.0)
        rotation_box.setDecimals(1)
        rotation_box.setSingleStep(0.5)
        rotation_box.setValue(float(rotation))
        self._compact_spinbox(rotation_box, width=62)
        degree_label = QLabel("°")
        degree_label.setFixedWidth(9)

        rotation_slider.valueChanged.connect(
            lambda value, box=rotation_box: box.setValue(
                float(value) / 10.0
            )
        )
        rotation_box.valueChanged.connect(
            lambda value, slider=rotation_slider: slider.setValue(
                int(round(float(value) * 10.0))
            )
        )
        rotation_row = QWidget()
        rotation_layout = QHBoxLayout(rotation_row)
        rotation_layout.setContentsMargins(0, 0, 0, 0)
        rotation_layout.setSpacing(2)
        rotation_layout.addWidget(rotation_slider, 1)
        rotation_layout.addWidget(rotation_box)
        rotation_layout.addWidget(degree_label)
        form.addRow("回転", rotation_row)

        # TU/TB拡大率。100%が中央、1%刻み、Shiftで3倍速。
        scale_slider = OnionScaleSlider(scale)
        scale_box = QSpinBox()
        scale_box.setRange(1, 199)
        scale_box.setSingleStep(1)
        scale_box.setValue(scale_slider.value())
        self._compact_spinbox(scale_box, width=54)
        percent_label = QLabel("%")
        percent_label.setFixedWidth(10)
        scale_slider.valueChanged.connect(scale_box.setValue)
        scale_box.valueChanged.connect(scale_slider.setValue)

        scale_row = QWidget()
        scale_layout = QHBoxLayout(scale_row)
        scale_layout.setContentsMargins(0, 0, 0, 0)
        scale_layout.setSpacing(2)
        scale_layout.addWidget(scale_slider, 1)
        scale_layout.addWidget(scale_box)
        scale_layout.addWidget(percent_label)
        form.addRow("拡大率", scale_row)

        reset_button = QPushButton("表示をリセット")
        reset_both_box = QCheckBox("前後")
        reset_both_box.setToolTip(
            "チェックすると、前後両方の位置・回転・"
            "拡大率を一度に初期値へ戻します。"
        )
        reset_row = QWidget()
        reset_layout = QHBoxLayout(reset_row)
        reset_layout.setContentsMargins(0, 0, 0, 0)
        reset_layout.setSpacing(3)
        reset_layout.addWidget(reset_button, 1)
        reset_layout.addWidget(reset_both_box)
        form.addRow(reset_row)

        count_box.valueChanged.connect(opacity_graph.set_count)
        count_box.valueChanged.connect(
            lambda _value: self.settingsChanged.emit()
        )
        opacity_graph.levelsChanged.connect(
            self.settingsChanged
        )
        opacity_slider.valueChanged.connect(
            lambda value, label=opacity_label: (
                label.setText(f"{int(value)}%"),
                self.settingsChanged.emit(),
            )
        )
        color_enabled_box.toggled.connect(
            lambda _checked=False: self.settingsChanged.emit()
        )
        shift_x_box.valueChanged.connect(
            lambda _value: self.settingsChanged.emit()
        )
        shift_y_box.valueChanged.connect(
            lambda _value: self.settingsChanged.emit()
        )
        rotation_box.valueChanged.connect(
            lambda _value: self.settingsChanged.emit()
        )
        scale_box.valueChanged.connect(
            lambda _value: self.settingsChanged.emit()
        )

        return (
            page,
            count_box,
            opacity_slider,
            opacity_label,
            opacity_graph,
            color_enabled_box,
            color_button,
            shift_x_button,
            shift_x_box,
            shift_y_button,
            shift_y_box,
            rotation_slider,
            rotation_box,
            scale_slider,
            scale_box,
            reset_button,
            reset_both_box,
        )

    @staticmethod
    def _sync_reset_both(other_checkbox, checked):
        other_checkbox.blockSignals(True)
        other_checkbox.setChecked(bool(checked))
        other_checkbox.blockSignals(False)

    def _request_shift_edit(self, direction, axis="xy"):
        self.clear_interaction_buttons()
        direction = -1 if int(direction) < 0 else 1
        button = (
            self.previous_shift_x_button
            if direction < 0
            else self.next_shift_x_button
        )
        button.setChecked(True)
        self.shiftEditRequested.emit(direction, "xy")

    def _request_canvas_position_edit(self):
        self.clear_interaction_buttons()
        self.canvas_position_button.setChecked(True)
        self.canvasPositionEditRequested.emit()

    def clear_interaction_buttons(self):
        for button in (
            self.previous_shift_x_button,
            self.next_shift_x_button,
            self.canvas_position_button,
        ):
            button.blockSignals(True)
            button.setChecked(False)
            button.blockSignals(False)

    def previous_levels(self):
        return self.previous_opacity_graph.levels()

    def next_levels(self):
        return self.next_opacity_graph.levels()

    def set_transform_values(
        self,
        previous_x,
        previous_y,
        previous_rotation,
        previous_scale,
        next_x,
        next_y,
        next_rotation,
        next_scale,
    ):
        for spinbox, value in (
            (self.previous_shift_x, previous_x),
            (self.previous_shift_y, previous_y),
            (self.next_shift_x, next_x),
            (self.next_shift_y, next_y),
        ):
            integer_value = max(
                -10000,
                min(10000, int(round(float(value)))),
            )
            spinbox.blockSignals(True)
            spinbox.setValue(integer_value)
            spinbox.blockSignals(False)

        for spinbox, slider, value in (
            (
                self.previous_rotation,
                self.previous_rotation_slider,
                previous_rotation,
            ),
            (
                self.next_rotation,
                self.next_rotation_slider,
                next_rotation,
            ),
        ):
            angle = (
                (float(value) + 180.0) % 360.0
            ) - 180.0
            spinbox.blockSignals(True)
            slider.blockSignals(True)
            spinbox.setValue(angle)
            slider.setValue(int(round(angle * 10.0)))
            slider.blockSignals(False)
            spinbox.blockSignals(False)

        for spinbox, slider, value in (
            (
                self.previous_scale,
                self.previous_scale_slider,
                previous_scale,
            ),
            (
                self.next_scale,
                self.next_scale_slider,
                next_scale,
            ),
        ):
            scale_value = max(1, min(199, int(round(float(value)))))
            spinbox.blockSignals(True)
            slider.blockSignals(True)
            spinbox.setValue(scale_value)
            slider.setValue(scale_value)
            slider.blockSignals(False)
            spinbox.blockSignals(False)

    def set_canvas_rotation_value(self, value):
        angle = (
            (float(value) + 180.0) % 360.0
        ) - 180.0
        self.canvas_rotation.blockSignals(True)
        self.canvas_rotation_slider.blockSignals(True)
        self.canvas_rotation.setValue(angle)
        self.canvas_rotation_slider.setValue(
            int(round(angle * 10.0))
        )
        self.canvas_rotation_slider.blockSignals(False)
        self.canvas_rotation.blockSignals(False)

    def _choose_color(self, which):
        current = (
            self.previous_color
            if which == "previous"
            else self.next_color
        )
        color = QColorDialog.getColor(
            current,
            self,
            "オニオンスキンの表示色",
        )
        if not color.isValid():
            return
        if which == "previous":
            self.previous_color = QColor(color)
        else:
            self.next_color = QColor(color)
        self._refresh_color_buttons()
        self.settingsChanged.emit()

    def _reset_shift(self, which):
        reset_both = (
            self.previous_reset_both.isChecked()
            if which == "previous"
            else self.next_reset_both.isChecked()
        )
        targets = (
            ("previous", "next")
            if reset_both
            else (which,)
        )

        for target in targets:
            if target == "previous":
                shift_controls = (
                    self.previous_shift_x,
                    self.previous_shift_y,
                )
                rotation_box = self.previous_rotation
                rotation_slider = (
                    self.previous_rotation_slider
                )
                scale_box = self.previous_scale
                scale_slider = self.previous_scale_slider
            else:
                shift_controls = (
                    self.next_shift_x,
                    self.next_shift_y,
                )
                rotation_box = self.next_rotation
                rotation_slider = self.next_rotation_slider
                scale_box = self.next_scale
                scale_slider = self.next_scale_slider

            for control in shift_controls:
                control.blockSignals(True)
                control.setValue(0)
                control.blockSignals(False)

            rotation_box.blockSignals(True)
            rotation_slider.blockSignals(True)
            rotation_box.setValue(0.0)
            rotation_slider.setValue(0)
            rotation_slider.blockSignals(False)
            rotation_box.blockSignals(False)

            scale_box.blockSignals(True)
            scale_slider.blockSignals(True)
            scale_box.setValue(100)
            scale_slider.setValue(100)
            scale_slider.blockSignals(False)
            scale_box.blockSignals(False)

        self.settingsChanged.emit()

    def _refresh_color_buttons(self):
        for button, color in (
            (self.previous_color_button, self.previous_color),
            (self.next_color_button, self.next_color),
        ):
            foreground = (
                "#000" if color.lightness() > 145 else "#fff"
            )
            button.setText(
                color.name(QColor.NameFormat.HexRgb).upper()
            )
            button.setStyleSheet(
                f"background:{color.name()};color:{foreground};"
                "padding:5px;"
            )
