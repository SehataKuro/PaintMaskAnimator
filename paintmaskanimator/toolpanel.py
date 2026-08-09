from .common import *  # noqa: F401,F403
from .widgets import (BrushSizeSpinBox, ClickableValueLabel, HSVColorWheel, LineTaperCurvePopup, SliderValueSpinBox, SwatchEyedropButton)


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
    sameImageReplacementRequested = Signal()
    mainLineRepaintRequested = Signal()
    flipLayerRequested = Signal(bool)
    swapMainSubRequested = Signal()
    isolateColorRequested = Signal()
    silhouetteRequested = Signal()
    removeDustRequested = Signal()
    backgroundColorRequested = Signal()
    clearColorFilterRequested = Signal()
    TOOLS = [
        ("brush","ブラシ"),("line","ライン"),
        ("shape","図形"),("bucket","バケツ"),
        ("lasso_fill","投げ縄塗り"),("lasso","投げ縄選択"),
        ("rect_select","長方形選択"),("auto_select","自動選択"),
        ("eyedropper","スポイト"),
        ("dust","ゴミ取り")
    ]

    def __init__(self):
        super().__init__(); self.setFixedWidth(190); self.buttons={}; self.active_tool="brush"
        self._line_curve_popup = None
        self.main_color=QColor("black"); self.sub_color=QColor(255,0,0); self.color_mode="main"; self.transparent_display_color=QColor("white")
        v=QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(3)
        v.addWidget(QLabel("<b>ツール</b>"))
        g=QGridLayout()
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(2)
        g.setVerticalSpacing(2)
        for i,(tid,label) in enumerate(self.TOOLS):
            b=QToolButton(); b.setText(label); b.setCheckable(True); b.setMinimumHeight(27)
            if tid == "auto_select":
                b.setToolTip(
                    "クリックした連続領域を選択します。"
                    "Shift＋クリックで追加、Alt＋クリックで削除します。"
                )
            b.clicked.connect(lambda _=False,t=tid:self.select_tool(t)); self.buttons[tid]=b; g.addWidget(b,i//2,i%2)
        v.addLayout(g)
        self.active=QLabel(); self.active.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.active.setStyleSheet("background:#2f6fa5;color:white;padding:6px;font-weight:bold"); v.addWidget(self.active)

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
        self.transform_quality.setChecked(False)
        self.transform_quality.setToolTip(
            "使用色ごとのマスクを変形して再合成します。"
            "有効時は現在のコマだけに適用されます。"
        )
        self.transform_line_width_note=QLabel(
            "色選択があるときは実線の太さを調整できます。"
        )
        self.transform_line_width_note.setWordWrap(True)
        self.transform_line_width_note.setStyleSheet(
            "color:#b8b8b8;padding-left:4px;padding-right:4px;"
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
        self._sync_transform_quality_options(False)
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
            lambda: self.size.pressureRequested.emit()
        )
        size_title_row.addWidget(self.pressure_settings_button)
        v.addLayout(size_title_row)
        self.size=BrushSizeSpinBox()
        self.size.setRange(0.5, 400.0)
        self.size.setSingleStep(0.5)
        self.size.setDecimals(1)
        self.size.setValue(8.0)
        self.size_slider=QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(1, 800)
        self.size_slider.setValue(16)
        self.size.valueChanged.connect(
            lambda value: self.size_slider.setValue(int(round(value * 2)))
        )
        self.size_slider.valueChanged.connect(
            lambda value: self.size.setValue(value / 2.0)
        )
        v.addWidget(self.size_slider); v.addWidget(self.size)

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

        self.opacity_enabled=QCheckBox("不透明度を使用")
        self.opacity_enabled.setChecked(False)
        self.opacity_title=self.opacity_enabled
        v.addWidget(self.opacity_title)

        self.opacity=QSpinBox()
        self.opacity.setRange(1,100)
        self.opacity.setValue(100)
        self.opacity.setSuffix("%")
        self.opacity_slider=QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(1,100)
        self.opacity_slider.setValue(100)

        opacity_tooltip = (
            "OFF：常に100%で、選択RGBをそのまま保存します。"
            "ON：設定値に応じて下地RGBと合成します。"
            "100%時はONでもRGB直書きなので近似色は増えません。"
            "筆圧は線幅だけに反映し、不透明度やアルファへは"
            "絶対に使用しません。"
        )
        self.opacity_title.setToolTip(opacity_tooltip)
        self.opacity.setToolTip(opacity_tooltip)
        self.opacity_slider.setToolTip(opacity_tooltip)

        self.opacity.valueChanged.connect(
            self.opacity_slider.setValue
        )
        self.opacity_slider.valueChanged.connect(
            self.opacity.setValue
        )

        def sync_opacity_controls(enabled):
            enabled = bool(enabled)
            self.opacity.setEnabled(enabled)
            self.opacity_slider.setEnabled(enabled)

        self.opacity_enabled.toggled.connect(
            sync_opacity_controls
        )
        sync_opacity_controls(False)

        v.addWidget(self.opacity_slider)
        v.addWidget(self.opacity)
        self.drawing_color_box = QWidget()
        color_layout = QVBoxLayout(self.drawing_color_box)
        color_layout.setContentsMargins(3, 3, 3, 3)
        color_layout.setSpacing(2)

        cg=QGridLayout()
        self.main_btn=SwatchEyedropButton(); self.main_btn.setText("メイン"); self.sub_btn=SwatchEyedropButton(); self.sub_btn.setText("サブ"); self.transparent_btn=QPushButton("背景色")
        self.main_btn.setToolTip("クリック：メイン色を選択／左または右へドラッグして離す：その位置をスポイト")
        self.sub_btn.setToolTip("クリック：サブ色を選択／左または右へドラッグして離す：その位置をスポイト")
        self.main_btn.clicked.connect(lambda:self.set_color_mode("main")); self.sub_btn.clicked.connect(lambda:self.set_color_mode("sub")); self.transparent_btn.clicked.connect(lambda:self.set_color_mode("transparent")); self.transparent_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.transparent_btn.customContextMenuRequested.connect(lambda _p:self.backgroundColorRequested.emit())
        for button in (self.main_btn, self.sub_btn, self.transparent_btn):
            button.setFixedHeight(24)
        cg.addWidget(self.main_btn,0,0); cg.addWidget(self.sub_btn,0,1); cg.addWidget(self.transparent_btn,1,0,1,2)
        self.hsv_wheel = HSVColorWheel()
        self.hsv_wheel.colorChanged.connect(self.wheel_color_changed)
        color_layout.addWidget(self.hsv_wheel)
        self.color_space=QComboBox(); self.color_space.setFixedHeight(22); self.color_space.addItems(["RGB", "HSV"]); self.color_space.currentTextChanged.connect(self.rebuild_color_sliders); color_layout.addWidget(self.color_space)
        self.slider_box=QWidget(); self.slider_layout=QFormLayout(self.slider_box); self.slider_layout.setContentsMargins(0,0,0,0); self.slider_layout.setVerticalSpacing(1); color_layout.addWidget(self.slider_box)
        color_layout.addLayout(cg)
        self.color_sliders=[]; self.color_value_labels=[]; self.rebuild_color_sliders("RGB")
        self.silhouette_btn=QPushButton("背景以外を黒シルエット表示")
        self.silhouette_btn.setCheckable(True)
        v.addWidget(self.silhouette_btn)
        self.same_image_replacement_btn=QPushButton("同一画像を置換色に登録")
        self.same_image_replacement_btn.setToolTip(
            "同じタイムライン位置にある上のレイヤーと画素配置を比較し、"
            "一致した色対応を置換色へ登録します。"
        )
        self.same_image_replacement_btn.clicked.connect(
            self.sameImageReplacementRequested
        )
        v.addWidget(self.same_image_replacement_btn)
        self.mainline_btn=QPushButton("MainLineRepaint")
        self.mainline_btn.setToolTip("メイン色・サブ色を線レイヤーへ分離し、抜けた面を周囲の最多色で埋めます。")
        self.mainline_btn.clicked.connect(self.mainLineRepaintRequested)
        v.addWidget(self.mainline_btn)
        for button in (
            self.silhouette_btn,
            self.same_image_replacement_btn,
            self.mainline_btn,
        ):
            button.setFixedHeight(23)
        # サイズ欄は▲▼のみ、RGB/HSV数値欄は▲▼と半角数値入力に対応。
        for numeric in self.findChildren(QAbstractSpinBox):
            is_size_numeric = numeric is self.size
            is_color_numeric = (
                isinstance(numeric, SliderValueSpinBox)
                or numeric is self.hsv_wheel.hue_value
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
        enabled = bool(enabled)
        if enabled:
            self.selection_all_frames.setChecked(False)
        self.selection_all_frames.setEnabled(not enabled)
        self.selection_all_frames.setToolTip(
            "クオリティ（Tp_mask v0.7方式）では選択できません。"
            if enabled else
            "選択範囲の変形をすべてのコマへ適用します。"
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
        for k,b in self.buttons.items():
            b.setChecked(k==tid)
        self.active.setText("使用中："+dict(self.TOOLS)[tid])
        self.toolChanged.emit(tid)

        is_selection = tid in ("lasso", "rect_select", "auto_select")
        is_line = tid == "line"
        is_shape = tid == "shape"
        is_bucket = tid == "bucket"
        is_dust = tid == "dust"
        is_lasso_fill = tid == "lasso_fill"
        uses_size = tid in ("brush", "line")
        uses_opacity = tid in ("brush", "line", "shape", "bucket", "lasso_fill")

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
        self.size.setPressurePopupEnabled(tid == "brush")
        # ラインは筆圧を検出しないため、筆圧設定はブラシ時だけ表示する。
        self.pressure_settings_button.setVisible(tid == "brush")
        self.size_slider.setVisible(uses_size)
        self.size.setVisible(uses_size)
        self.brush_stabilizer_label.setVisible(tid == "brush")
        self.brush_stabilizer.setVisible(tid == "brush")
        self.opacity_title.setVisible(uses_opacity)
        self.opacity_slider.setVisible(uses_opacity)
        self.opacity.setVisible(uses_opacity)


    def set_color_mode(self, mode):
        self.color_mode=mode; self.refresh_swatches(); self.sync_sliders(); self.colorModeChanged.emit(mode)

    def set_colors(self, main, sub, mode, transparent_display=None):
        self.main_color=QColor(main); self.sub_color=QColor(sub); self.color_mode=mode
        if transparent_display is not None: self.transparent_display_color=QColor(transparent_display)
        self.refresh_swatches(); self.sync_sliders()

    def refresh_swatches(self):
        def style(c, selected):
            fg='white' if c.lightness()<110 else 'black'; border='3px solid #e53935' if selected else '1px solid #777'
            return f"background:{c.name()};color:{fg};border:{border};padding:5px;"
        self.main_btn.setStyleSheet(style(self.main_color,self.color_mode=="main"))
        self.sub_btn.setStyleSheet(style(self.sub_color,self.color_mode=="sub"))
        self.transparent_btn.setStyleSheet(style(self.transparent_display_color,self.color_mode=="transparent"))

    def clear_slider_layout(self):
        while self.slider_layout.rowCount(): self.slider_layout.removeRow(0)
        self.color_sliders=[]
        self.color_value_labels=[]

    def rebuild_color_sliders(self, mode):
        self.clear_slider_layout()
        specs = (
            [("R", 0, 255), ("G", 0, 255), ("B", 0, 255)]
            if mode == "RGB"
            else [("H", 0, 359), ("S", 0, 255), ("V", 0, 255)]
        )
        for name, lo, hi in specs:
            slider = QSlider(Qt.Orientation.Horizontal)
            if mode == "RGB":
                slider.setRange(lo, hi)
                slider.setSingleStep(1)
                slider.setProperty("valueScale", 1.0)
                value_control = SliderValueSpinBox(
                    slider, scale=1.0, step=1.0
                )
            else:
                slider.setRange(lo, hi)
                slider.setSingleStep(1)
                slider.setProperty("valueScale", 1.0)
                value_control = SliderValueSpinBox(
                    slider, scale=1.0, step=1.0
                )
            slider.setProperty("channel", name)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(slider, 1)
            row_layout.addWidget(value_control)
            slider.valueChanged.connect(
                self.slider_color_changed
            )
            self.slider_layout.addRow(name, row)
            self.color_sliders.append(slider)
            self.color_value_labels.append(value_control)
        self.sync_sliders()
        self.update_slider_gradients()


    def active_color(self): return self.main_color if self.color_mode=="main" else self.sub_color

    def sync_sliders(self):
        self.hsv_wheel.setEnabled(self.color_mode != "transparent")
        if self.color_mode != "transparent":
            self.hsv_wheel.setColor(self.active_color())
        if not self.color_sliders or self.color_mode == "transparent":
            return
        color = self.active_color()
        if self.color_space.currentText() == "RGB":
            values = [
                int(color.red()),
                int(color.green()),
                int(color.blue()),
            ]
        else:
            hsv = color.getHsv()
            values = [max(0, hsv[0]), hsv[1], hsv[2]]

        for index, (slider, value) in enumerate(
            zip(self.color_sliders, values)
        ):
            scale = float(slider.property("valueScale") or 1.0)
            slider_value = int(round(float(value) * scale))
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
        if len(self.color_sliders) != 3:
            return
        mode = self.color_space.currentText()
        active = self.active_color() if self.color_mode != "transparent" else QColor("black")
        if mode == "RGB":
            gradients = [
                "stop:0 rgb(0,%d,%d), stop:1 rgb(255,%d,%d)" % (active.green(), active.blue(), active.green(), active.blue()),
                "stop:0 rgb(%d,0,%d), stop:1 rgb(%d,255,%d)" % (active.red(), active.blue(), active.red(), active.blue()),
                "stop:0 rgb(%d,%d,0), stop:1 rgb(%d,%d,255)" % (active.red(), active.green(), active.red(), active.green()),
            ]
        else:
            hue = max(0, active.hsvHue())
            hue_color = QColor.fromHsv(hue, 255, 255).name()
            gradients = [
                "stop:0 #ff0000, stop:0.17 #ffff00, stop:0.33 #00ff00, stop:0.50 #00ffff, stop:0.67 #0000ff, stop:0.83 #ff00ff, stop:1 #ff0000",
                f"stop:0 #ffffff, stop:1 {hue_color}",
                f"stop:0 #000000, stop:1 {QColor.fromHsv(hue, max(1,active.hsvSaturation()),255).name()}",
            ]
        for slider, gradient in zip(self.color_sliders, gradients):
            slider.setStyleSheet(
                "QSlider::groove:horizontal{height:12px;border:1px solid #555;"
                f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0,{gradient});}}"
                "QSlider::handle:horizontal{width:12px;margin:-3px 0;border:2px solid white;"
                "background:#333;border-radius:6px;}"
            )

    def slider_color_changed(self):
        if (
            self.color_mode == "transparent"
            or len(self.color_sliders) != 3
        ):
            return
        if self.color_space.currentText() == "RGB":
            red, green, blue = [
                max(0, min(255, int(slider.value())))
                for slider in self.color_sliders
            ]
            color = QColor(red, green, blue)
        else:
            hue, saturation, value = [
                slider.value() for slider in self.color_sliders
            ]
            color = QColor.fromHsv(
                int(hue), int(saturation), int(value)
            )

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
