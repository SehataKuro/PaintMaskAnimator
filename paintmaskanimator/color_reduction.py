from .common import *  # noqa: F401,F403
from .canvas import PaintCanvas
from .logging_setup import get_logger

log = get_logger(__name__)


class ToneCurveWidget(QWidget):
    """両端固定・複数制御点対応の軽量トーンカーブ。"""

    curveChanged = Signal(object)
    interactionStarted = Signal()
    interactionFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._points = [
            QPointF(0.0, 0.0),
            QPointF(1.0, 1.0),
        ]
        self._active_index = -1
        self._dragging = False
        self.setMinimumWidth(260)
        self.setFixedHeight(112)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip(
            "空いている場所をクリック：点を追加／"
            "点をドラッグ：濃さを調整／"
            "右クリック：中間点を削除／"
            "ダブルクリック：標準へ戻す"
        )

    def points(self):
        return [
            (
                round(float(point.x()), 5),
                round(float(point.y()), 5),
            )
            for point in self._points
        ]

    def setPoints(self, points):
        normalized = self._normalized_points(points)
        if normalized == self.points():
            return
        self._points = [
            QPointF(x, y) for x, y in normalized
        ]
        self._active_index = -1
        self.update()
        self.curveChanged.emit(self.points())

    def resetCurve(self):
        self.setPoints([(0.0, 0.0), (1.0, 1.0)])

    @staticmethod
    def _normalized_points(points):
        normalized = []
        try:
            iterable = list(points)
        except TypeError:
            iterable = []

        for point in iterable:
            try:
                if isinstance(point, QPointF):
                    x, y = point.x(), point.y()
                else:
                    x, y = point[:2]
                x = max(0.0, min(1.0, float(x)))
                y = max(0.0, min(1.0, float(y)))
                normalized.append((x, y))
            except (TypeError, ValueError, IndexError):
                continue

        normalized.extend([(0.0, 0.0), (1.0, 1.0)])
        normalized.sort(key=lambda value: value[0])

        merged = []
        for x, y in normalized:
            if merged and abs(x - merged[-1][0]) < 1e-5:
                merged[-1] = (x, y)
            else:
                merged.append((x, y))

        if not merged or merged[0][0] > 1e-5:
            merged.insert(0, (0.0, 0.0))
        merged[0] = (0.0, 0.0)
        if merged[-1][0] < 1.0 - 1e-5:
            merged.append((1.0, 1.0))
        merged[-1] = (1.0, 1.0)
        return merged

    def _graph_rect(self):
        return QRectF(self.rect()).adjusted(9, 9, -9, -9)

    def _point_to_widget(self, point):
        rect = self._graph_rect()
        return QPointF(
            rect.left() + rect.width() * point.x(),
            rect.bottom() - rect.height() * point.y(),
        )

    def _widget_to_point(self, position):
        rect = self._graph_rect()
        x = (
            (position.x() - rect.left())
            / max(1.0, rect.width())
        )
        y = (
            (rect.bottom() - position.y())
            / max(1.0, rect.height())
        )
        return QPointF(
            max(0.0, min(1.0, x)),
            max(0.0, min(1.0, y)),
        )

    def _nearest_point_index(self, position):
        radius_sq = 9.0 * 9.0
        best_index = -1
        best_distance = radius_sq
        for index, point in enumerate(self._points):
            widget_point = self._point_to_widget(point)
            distance = (
                (widget_point.x() - position.x()) ** 2
                + (widget_point.y() - position.y()) ** 2
            )
            if distance <= best_distance:
                best_distance = distance
                best_index = index
        return best_index

    def _emit_curve(self):
        self.update()
        self.curveChanged.emit(self.points())

    def _move_active_point(self, position):
        index = int(self._active_index)
        if not (0 <= index < len(self._points)):
            return

        point = self._widget_to_point(position)
        if index == 0:
            point = QPointF(0.0, 0.0)
        elif index == len(self._points) - 1:
            point = QPointF(1.0, 1.0)
        else:
            left_x = self._points[index - 1].x() + 0.01
            right_x = self._points[index + 1].x() - 0.01
            point.setX(
                max(left_x, min(right_x, point.x()))
            )

        self._points[index] = point
        self._emit_curve()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            index = self._nearest_point_index(
                event.position()
            )
            if 0 < index < len(self._points) - 1:
                self._points.pop(index)
                self._active_index = -1
                self._emit_curve()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            index = self._nearest_point_index(
                event.position()
            )
            if index < 0:
                point = self._widget_to_point(
                    event.position()
                )
                self._points.append(point)
                self._points.sort(
                    key=lambda item: item.x()
                )
                index = min(
                    range(len(self._points)),
                    key=lambda candidate: abs(
                        self._points[candidate].x()
                        - point.x()
                    ),
                )
            self._active_index = index
            self._dragging = True
            self.interactionStarted.emit()
            self._move_active_point(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._dragging
            and event.buttons()
            & Qt.MouseButton.LeftButton
        ):
            self._move_active_point(event.position())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            self._dragging
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._dragging = False
            self._move_active_point(event.position())
            self.interactionFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.resetCurve()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        rect = self._graph_rect()
        painter.fillRect(rect, QColor("#20252a"))
        painter.setPen(QPen(QColor("#4a535a"), 1))
        for fraction in (0.25, 0.5, 0.75):
            x = rect.left() + rect.width() * fraction
            y = rect.top() + rect.height() * fraction
            painter.drawLine(
                QPointF(x, rect.top()),
                QPointF(x, rect.bottom()),
            )
            painter.drawLine(
                QPointF(rect.left(), y),
                QPointF(rect.right(), y),
            )

        painter.setPen(QPen(QColor("#7b858c"), 1))
        painter.drawLine(
            QPointF(rect.left(), rect.bottom()),
            QPointF(rect.right(), rect.top()),
        )

        path = QPainterPath()
        sample_inputs = np.linspace(
            0.0,
            1.0,
            129,
            dtype=np.float64,
        )
        sample_outputs = PaintCanvas.tone_curve_samples(
            self.points(),
            sample_inputs,
        )
        for index, (input_value, output_value) in enumerate(
            zip(sample_inputs, sample_outputs)
        ):
            widget_point = QPointF(
                rect.left() + rect.width() * float(input_value),
                rect.bottom() - rect.height() * float(output_value),
            )
            if index == 0:
                path.moveTo(widget_point)
            else:
                path.lineTo(widget_point)

        painter.setPen(QPen(QColor("#67d5ff"), 2))
        painter.drawPath(path)

        for index, point in enumerate(self._points):
            widget_point = self._point_to_widget(point)
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.setBrush(
                QColor("#ffd65a")
                if index == self._active_index
                else QColor("#67d5ff")
            )
            painter.drawEllipse(widget_point, 4.5, 4.5)
        painter.end()


class ColorReductionDialog(QDialog):
    """1枚目で非アンチエイリアス化を調整し、全コマへ適用する。"""

    def __init__(
        self,
        source_image,
        original_color_count,
        semi_transparent_count=0,
        opaque_background=False,
        background_rgb=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("2値化")
        self.resize(820, 560)

        self.source_image = source_image.copy()
        self.original_color_count = int(original_color_count)
        self.semi_transparent_count = int(
            semi_transparent_count
        )
        self.opaque_background = bool(opaque_background)
        self.background_rgb = (
            tuple(int(value) for value in background_rgb)
            if background_rgb is not None
            else None
        )
        self.reduction_enabled = True
        self._palette = None
        self._palette_key = None
        self._base_palette_cache = {}
        self._base_reduced_cache = {}
        self._reduced_full_image = self.source_image.copy()
        self._confirmed_preview_image = self.source_image.copy()
        self._confirmed_preview_title = "2値化　100%"
        self._preview_dirty = False
        self._tone_drag_active = False
        self._tone_preview_source_rgba = None
        self._tone_preview_background_mask = None
        self._tone_adjusted_preview_image = self.source_image.copy()
        self._color_processing = False
        self._initial_preview = True
        self._last_color_processing_seconds = 0.0
        self._syncing_preview_scroll = False
        self._preview_pan_active = False
        self._preview_pan_widget = None
        self._preview_pan_origin = QPointF()
        self._preview_pan_start_h = 0
        self._preview_pan_start_v = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        background_note = (
            "白背景を自動検出"
            if self.opaque_background
            else f"半透明 {self.semi_transparent_count:,}px"
        )
        summary = QLabel(
            f"1枚目：{self.original_color_count:,}色／"
            f"{background_note}　"
            "トーンカーブを元画像へ先に適用し、その後に色数を調整して2値化します。"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        previews = QHBoxLayout()
        previews.setSpacing(6)
        (
            original_column,
            self.original_title,
            self.original_preview,
            self.original_scroll,
        ) = self._build_preview_column("元画像（トーンカーブ）　100%")
        (
            reduced_column,
            self.reduced_title,
            self.reduced_preview,
            self.reduced_scroll,
        ) = self._build_preview_column(
            "2値化　100%"
        )
        previews.addLayout(original_column, 1)
        previews.addLayout(reduced_column, 1)
        layout.addLayout(previews, 1)

        controls = QFormLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setHorizontalSpacing(8)
        controls.setVerticalSpacing(4)

        self.extraction_mode = QComboBox()
        self.extraction_mode.addItem("色面抽出", "surface")
        self.extraction_mode.addItem("ライン抽出", "line")
        self.extraction_mode.setCurrentIndex(1)
        self.extraction_mode.setToolTip(
            "色面抽出：塗りの色面を保ちながらアンチエイリアスを除去します。\n"
            "ライン抽出：白背景と元の線色を保ちながら線画を2値化します。"
        )
        self.extraction_mode.currentIndexChanged.connect(
            self._mark_preview_dirty
        )
        controls.addRow("2値化方式", self.extraction_mode)

        color_row = QWidget()
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(5)
        self.color_slider = QSlider(Qt.Orientation.Horizontal)
        self.color_slider.setRange(2, 100)
        self.color_slider.setSingleStep(1)
        self.color_slider.setPageStep(8)
        self.color_slider.setValue(8)
        self.color_count = QSpinBox()
        self.color_count.setRange(2, 100)
        self.color_count.setValue(8)
        self.color_count.setSuffix(" 色")
        self.color_count.setFixedWidth(76)
        self.color_slider.valueChanged.connect(
            self.color_count.setValue
        )
        self.color_count.valueChanged.connect(
            self.color_slider.setValue
        )
        self.color_count.valueChanged.connect(
            self._mark_preview_dirty
        )
        color_layout.addWidget(self.color_slider, 1)
        color_layout.addWidget(self.color_count)
        controls.addRow("色数", color_row)

        tone_row = QWidget()
        tone_layout = QHBoxLayout(tone_row)
        tone_layout.setContentsMargins(0, 0, 0, 0)
        tone_layout.setSpacing(6)

        self.tone_curve = ToneCurveWidget()
        self.tone_curve_label = QLabel("標準")
        self.tone_curve_label.setFixedWidth(66)
        self.tone_curve_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        tone_reset = QPushButton("標準")
        tone_reset.setFixedWidth(46)
        tone_reset.clicked.connect(
            self.tone_curve.resetCurve
        )
        self.tone_curve.curveChanged.connect(
            self._tone_curve_changed
        )
        self.tone_curve.interactionStarted.connect(
            self._tone_curve_drag_started
        )
        self.tone_curve.interactionFinished.connect(
            self._tone_curve_drag_finished
        )

        tone_layout.addWidget(self.tone_curve, 1)
        tone_layout.addWidget(self.tone_curve_label)
        tone_layout.addWidget(tone_reset)
        controls.addRow("トーンカーブ", tone_row)
        layout.addLayout(controls)

        note = QLabel(
            "色数の変更中はプレビューを更新しません。"
            "トーンカーブ操作中は元画像へ一時適用し、"
            "離すと直前の2値化プレビューへ戻ります。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size:10px;")
        layout.addWidget(note)

        confirm_row = QHBoxLayout()
        self.preview_status = QLabel("プレビュー反映済み")
        self.preview_status.setStyleSheet(
            "color:#28744a;font-weight:bold;"
        )
        self.preview_confirm_button = QPushButton("プレビュー")
        self.preview_confirm_button.setToolTip(
            "現在のトーンカーブと色数を使って"
            "2値化プレビューを更新します。"
        )
        self.preview_confirm_button.setMinimumWidth(110)
        self.preview_confirm_button.clicked.connect(
            self._confirm_preview_settings
        )
        confirm_row.addWidget(self.preview_status)
        confirm_row.addStretch(1)
        confirm_row.addWidget(self.preview_confirm_button)
        layout.addLayout(confirm_row)

        button_row = QHBoxLayout()
        self.apply_button = QPushButton("全コマを2値化")
        self.no_reduction_button = QPushButton("そのまま読み込む")
        cancel_button = QPushButton("キャンセル")
        self.apply_button.clicked.connect(self._accept_reduction)
        self.no_reduction_button.clicked.connect(
            self._accept_without_reduction
        )
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.apply_button)
        button_row.addWidget(self.no_reduction_button)
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

        # 操作中の元画像＋トーンカーブ表示だけを軽く間引く。
        self._tone_live_timer = QTimer(self)
        self._tone_live_timer.setSingleShot(True)
        self._tone_live_timer.setInterval(24)
        self._tone_live_timer.timeout.connect(
            self._show_tone_curve_live_preview
        )

        self._install_preview_event_filters()
        self._connect_preview_scrolls()
        self._update_preview()
        self._initial_preview = False
        QTimer.singleShot(0, self._center_preview_position)

    def tone_curve_points(self):
        return self.tone_curve.points()

    def tone_curve_points_key(self):
        """複数点トーンカーブを安定したハッシュ可能キーへ変換する。"""
        normalized = []
        for point in self.tone_curve_points():
            try:
                x, y = point[:2]
                normalized.append(
                    (
                        round(float(x), 5),
                        round(float(y), 5),
                    )
                )
            except (
                TypeError,
                ValueError,
                IndexError,
            ):
                continue

        if not normalized:
            normalized = [
                (0.0, 0.0),
                (1.0, 1.0),
            ]
        return tuple(normalized)

    def _tone_curve_changed(self, points):
        point_count = max(0, len(points) - 2)
        self.tone_curve_label.setText(
            "標準"
            if point_count == 0
            else f"中間点 {point_count}"
        )
        self._mark_preview_dirty()
        if self._tone_drag_active:
            self._tone_live_timer.start()
        else:
            self._show_tone_curve_live_preview(force=True)

    def _current_preview_key(self):
        return (
            self.extraction_mode.currentData(),
            int(self.color_count.value()),
            self.tone_curve_points_key(),
        )

    def _mark_preview_dirty(self, _value=None):
        if self._initial_preview:
            return
        self._preview_dirty = True
        self.preview_status.setText("設定未反映")
        self.preview_status.setStyleSheet(
            "color:#a15a00;font-weight:bold;"
        )
        self.preview_confirm_button.setEnabled(True)
        self.apply_button.setEnabled(False)
        if not self._tone_drag_active:
            self._restore_confirmed_preview(
                show_dirty_state=True
            )

    def _tone_curve_drag_started(self):
        self._tone_drag_active = True
        self._show_tone_curve_live_preview()

    def _tone_curve_drag_finished(self):
        self._show_tone_curve_live_preview()
        self._tone_drag_active = False
        self._tone_live_timer.stop()

    def _prepare_tone_preview_source(self):
        if self._tone_preview_source_rgba is not None:
            return
        self._tone_preview_source_rgba = (
            PaintCanvas._qimage_rgba_array(
                self.source_image
            )
        )
        if self.background_rgb is None:
            self._tone_preview_background_mask = None
            return

        background = np.asarray(
            self.background_rgb,
            dtype=np.int16,
        )
        difference = (
            self._tone_preview_source_rgba[:, :, :3]
            .astype(np.int16)
            - background
        )
        self._tone_preview_background_mask = (
            np.sum(
                difference.astype(np.int32)
                * difference.astype(np.int32),
                axis=2,
            )
            <= (5 * 5 * 3)
        )

    def _show_tone_curve_live_preview(self, force=False):
        """元画像へ現在のトーンカーブを軽量適用して表示する。"""
        if not self._tone_drag_active and not force:
            return
        try:
            self._prepare_tone_preview_source()
            source_rgba = self._tone_preview_source_rgba
            if source_rgba is None or source_rgba.size == 0:
                return

            lut = PaintCanvas.tone_curve_lut(
                self.tone_curve_points()
            )
            result = source_rgba.copy()
            result[:, :, :3] = lut[
                result[:, :, :3]
            ]

            if (
                self._tone_preview_background_mask is not None
                and self.background_rgb is not None
            ):
                result[:, :, :3][
                    self._tone_preview_background_mask
                ] = np.asarray(
                    self.background_rgb,
                    dtype=np.uint8,
                )

            preview = PaintCanvas._rgba_array_to_qimage(
                result
            )
            if preview is None or preview.isNull():
                return

            self._tone_adjusted_preview_image = preview.copy()
            self.original_title.setText(
                "元画像（トーンカーブ）　100%"
            )
            self.original_preview.setPixmap(
                QPixmap.fromImage(preview)
            )
            self.original_preview.setFixedSize(preview.size())
        except (ValueError, IndexError, TypeError, RuntimeError, AttributeError) as exc:
            # 操作中の簡易表示失敗は、確定済み表示へ静かに戻す。
            log.debug("tone-curve live preview failed: %s", exc)
            self._restore_confirmed_preview(
                show_dirty_state=True
            )

    def _restore_confirmed_preview(
        self,
        show_dirty_state=False,
    ):
        confirmed = getattr(
            self,
            "_confirmed_preview_image",
            None,
        )
        if confirmed is None or confirmed.isNull():
            return
        self._reduced_full_image = confirmed.copy()
        title = self._confirmed_preview_title
        if show_dirty_state and self._preview_dirty:
            title += "（設定未反映）"
        self.reduced_title.setText(title)
        self._refresh_preview_pixmaps()

    def _confirm_preview_settings(self):
        if self._color_processing:
            return
        self._tone_drag_active = False
        self._tone_live_timer.stop()
        self._restore_confirmed_preview(
            show_dirty_state=True
        )
        self._update_preview()

    def alpha_threshold_255(self):
        # 透明背景のアルファ二値化は中央50%へ固定する。
        return 128

    def _build_preview_column(self, title_text):
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)

        title = QLabel(title_text)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        label = QLabel()
        label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
        )
        label.setStyleSheet("background:white;")
        label.setScaledContents(False)
        label.setCursor(Qt.CursorShape.OpenHandCursor)

        scroll = QScrollArea()
        scroll.setWidget(label)
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setMinimumSize(350, 340)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        scroll.setStyleSheet(
            "QScrollArea{border:1px solid #777;"
            "background:#d8d8d8;}"
        )

        column.addWidget(title)
        column.addWidget(scroll, 1)
        return column, title, label, scroll

    def _install_preview_event_filters(self):
        """左右のプレビュー生成完了後にハンド操作を有効化する。"""
        for widget in self._preview_event_widgets():
            widget.installEventFilter(self)

    def _preview_event_widgets(self):
        """初期化途中でも安全に取得できるプレビュー部品一覧。"""
        widgets = []

        for attribute_name in (
            "original_preview",
            "reduced_preview",
        ):
            widget = getattr(self, attribute_name, None)
            if widget is not None:
                widgets.append(widget)

        for attribute_name in (
            "original_scroll",
            "reduced_scroll",
        ):
            scroll = getattr(self, attribute_name, None)
            if scroll is not None:
                viewport = scroll.viewport()
                if viewport is not None:
                    widgets.append(viewport)

        return tuple(widgets)

    def _connect_preview_scrolls(self):
        for bar in (
            self.original_scroll.horizontalScrollBar(),
            self.reduced_scroll.horizontalScrollBar(),
        ):
            bar.valueChanged.connect(
                lambda value, axis="h": self._sync_preview_scroll(
                    axis, value
                )
            )
        for bar in (
            self.original_scroll.verticalScrollBar(),
            self.reduced_scroll.verticalScrollBar(),
        ):
            bar.valueChanged.connect(
                lambda value, axis="v": self._sync_preview_scroll(
                    axis, value
                )
            )

    def _sync_preview_scroll(self, axis, value):
        if self._syncing_preview_scroll:
            return
        self._syncing_preview_scroll = True
        try:
            if axis == "h":
                bars = (
                    self.original_scroll.horizontalScrollBar(),
                    self.reduced_scroll.horizontalScrollBar(),
                )
            else:
                bars = (
                    self.original_scroll.verticalScrollBar(),
                    self.reduced_scroll.verticalScrollBar(),
                )
            for bar in bars:
                target = max(
                    bar.minimum(),
                    min(bar.maximum(), int(value)),
                )
                if bar.value() != target:
                    bar.setValue(target)
        finally:
            self._syncing_preview_scroll = False

    def _center_preview_position(self):
        original_scroll = getattr(
            self, "original_scroll", None
        )
        if original_scroll is None:
            return
        horizontal = original_scroll.horizontalScrollBar()
        vertical = original_scroll.verticalScrollBar()
        self._sync_preview_scroll(
            "h",
            int(round((horizontal.minimum() + horizontal.maximum()) / 2)),
        )
        self._sync_preview_scroll(
            "v",
            int(round((vertical.minimum() + vertical.maximum()) / 2)),
        )

    def eventFilter(self, watched, event):
        # macOSではウィジェット生成中にもイベントが届くため、
        # 未作成の属性を直接参照しない。
        preview_widgets = self._preview_event_widgets()
        if watched in preview_widgets:
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._preview_pan_active = True
                self._preview_pan_widget = watched
                self._preview_pan_origin = QPointF(
                    event.globalPosition()
                )
                original_scroll = getattr(
                    self, "original_scroll", None
                )
                if original_scroll is None:
                    return super().eventFilter(watched, event)

                self._preview_pan_start_h = (
                    original_scroll.horizontalScrollBar().value()
                )
                self._preview_pan_start_v = (
                    original_scroll.verticalScrollBar().value()
                )
                watched.setCursor(Qt.CursorShape.ClosedHandCursor)
                try:
                    watched.grabMouse()
                except RuntimeError as exc:
                    log.debug("grabMouse() failed: %s", exc)
                event.accept()
                return True

            if (
                event.type() == QEvent.Type.MouseMove
                and self._preview_pan_active
                and event.buttons() & Qt.MouseButton.LeftButton
            ):
                delta = QPointF(event.globalPosition()) - (
                    self._preview_pan_origin
                )
                self._sync_preview_scroll(
                    "h",
                    self._preview_pan_start_h
                    - int(round(delta.x())),
                )
                self._sync_preview_scroll(
                    "v",
                    self._preview_pan_start_v
                    - int(round(delta.y())),
                )
                event.accept()
                return True

            if (
                event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
                and self._preview_pan_active
            ):
                self._preview_pan_active = False
                pan_widget = self._preview_pan_widget
                self._preview_pan_widget = None
                if pan_widget is not None:
                    try:
                        pan_widget.releaseMouse()
                    except RuntimeError as exc:
                        log.debug("releaseMouse() failed: %s", exc)
                    pan_widget.setCursor(
                        Qt.CursorShape.OpenHandCursor
                    )
                event.accept()
                return True

        return super().eventFilter(watched, event)

    def _refresh_preview_pixmaps(self):
        def set_image(label, image):
            label.setPixmap(QPixmap.fromImage(image))
            label.setFixedSize(image.size())

        self.original_title.setText("元画像（トーンカーブ）　100%")
        original_image = getattr(
            self,
            "_tone_adjusted_preview_image",
            self.source_image,
        )
        set_image(self.original_preview, original_image)
        set_image(self.reduced_preview, self._reduced_full_image)

    def _create_color_progress(self):
        progress = QProgressDialog(
            "色相を分類しています",
            "",
            0,
            3,
            self,
        )
        progress.setWindowTitle("2値化")
        progress.setCancelButton(None)
        progress.setWindowModality(
            Qt.WindowModality.WindowModal
        )
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumWidth(330)
        progress.setValue(0)
        progress.show()
        QApplication.processEvents()
        return progress

    @staticmethod
    def _update_color_progress(
        progress,
        value,
        label,
    ):
        if progress is None:
            return
        progress.setLabelText(
            f"{label}\n{int(value)} / 3"
        )
        progress.setValue(int(value))
        QApplication.processEvents()

    @staticmethod
    def _close_color_progress(progress):
        if progress is None:
            return
        progress.setValue(progress.maximum())
        progress.close()
        progress.deleteLater()
        QApplication.processEvents()

    def _trim_color_preview_cache(self, maximum=8):
        maximum = max(1, int(maximum))
        while len(self._base_palette_cache) > maximum:
            oldest = next(iter(self._base_palette_cache))
            self._base_palette_cache.pop(oldest, None)
            self._base_reduced_cache.pop(oldest, None)

    def _update_preview(self):
        if self._color_processing:
            return False

        target = int(self.color_count.value())
        alpha_threshold = self.alpha_threshold_255()
        tone_points = self.tone_curve_points()
        try:
            tone_key = self.tone_curve_points_key()
        except (TypeError, ValueError, IndexError, AttributeError) as exc:
            log.debug("tone_curve_points_key() failed, using identity: %s", exc)
            tone_points = [
                (0.0, 0.0),
                (1.0, 1.0),
            ]
            tone_key = tuple(tone_points)

        extraction_mode = (
            "line"
            if self.extraction_mode.currentData() == "line"
            else "surface"
        )
        cache_key = (extraction_mode, target, tone_key)
        processing_opaque_background = (
            True
            if extraction_mode == "line"
            else self.opaque_background
        )
        processing_background_rgb = (
            (255, 255, 255)
            if extraction_mode == "line"
            else self.background_rgb
        )
        needs_color_processing = (
            cache_key not in self._base_palette_cache
        )
        progress = None
        started_at = None
        success = False
        self._color_processing = True
        self.preview_confirm_button.setEnabled(False)
        self.apply_button.setEnabled(False)

        old_h = self.original_scroll.horizontalScrollBar().value()
        old_v = self.original_scroll.verticalScrollBar().value()

        QApplication.setOverrideCursor(
            Qt.CursorShape.WaitCursor
        )
        try:
            tone_adjusted_source = PaintCanvas.apply_tone_curve(
                self.source_image,
                tone_curve_points=tone_points,
                background_rgb=processing_background_rgb,
            )
            if (
                tone_adjusted_source is None
                or tone_adjusted_source.isNull()
            ):
                raise ValueError(
                    "トーンカーブ適用後の画像を生成できませんでした。"
                )
            self._tone_adjusted_preview_image = (
                tone_adjusted_source.copy()
            )

            if needs_color_processing:
                started_at = time.perf_counter()
                if not self._initial_preview:
                    progress = self._create_color_progress()

                self._update_color_progress(
                    progress,
                    0,
                    "トーンカーブを元画像へ適用しています",
                )
                self._update_color_progress(
                    progress,
                    1,
                    "近い色相をまとめて代表色を作成しています",
                )
                base_palette = (
                    PaintCanvas.build_color_reduction_palette(
                        tone_adjusted_source,
                        target,
                        alpha_threshold=alpha_threshold,
                        max_samples=131072,
                        opaque_background=processing_opaque_background,
                        background_rgb=processing_background_rgb,
                        extraction_mode=extraction_mode,
                    )
                )

                self._update_color_progress(
                    progress,
                    2,
                    (
                        "線のアンチエイリアスを線色と白背景へ分けています"
                        if extraction_mode == "line"
                        else "色面の境界を2色へ分けています"
                    ),
                )
                base_reduced = (
                    PaintCanvas.apply_color_reduction_palette(
                        tone_adjusted_source,
                        base_palette,
                        alpha_threshold=alpha_threshold,
                        opaque_background=processing_opaque_background,
                        background_rgb=processing_background_rgb,
                        tone_curve_points=[
                            (0.0, 0.0),
                            (1.0, 1.0),
                        ],
                        extraction_mode=extraction_mode,
                    )
                )
                if base_reduced is None or base_reduced.isNull():
                    raise ValueError(
                        "2値化画像を生成できませんでした。"
                    )

                self._base_palette_cache[cache_key] = (
                    np.asarray(
                        base_palette,
                        dtype=np.uint8,
                    ).copy()
                )
                self._base_reduced_cache[cache_key] = (
                    base_reduced.copy()
                )
                self._trim_color_preview_cache()
                self._update_color_progress(
                    progress,
                    3,
                    "プレビューを更新しています",
                )
                self._last_color_processing_seconds = (
                    time.perf_counter() - started_at
                )

            palette = np.asarray(
                self._base_palette_cache[cache_key],
                dtype=np.uint8,
            ).copy()
            reduced = self._base_reduced_cache[
                cache_key
            ].copy()

            self._palette = palette
            self._palette_key = cache_key
            self._reduced_full_image = reduced

            statistics = PaintCanvas.image_alpha_statistics(
                reduced
            )
            actual = len(palette)
            mode_name = self.extraction_mode.currentText()
            title = (
                f"{mode_name}　100%（{actual}色／"
                f"半透明 {statistics['semi_transparent']}px）"
            )
            self.reduced_title.setText(title)
            self._refresh_preview_pixmaps()

            self._confirmed_preview_image = reduced.copy()
            self._confirmed_preview_title = title
            self._preview_dirty = False
            self.preview_status.setText("プレビュー反映済み")
            self.preview_status.setStyleSheet(
                "color:#28744a;font-weight:bold;"
            )
            success = True

            QTimer.singleShot(
                0,
                lambda: (
                    self._sync_preview_scroll("h", old_h),
                    self._sync_preview_scroll("v", old_v),
                ),
            )
        except (ValueError, IndexError, TypeError, RuntimeError, AttributeError, MemoryError) as exc:
            log.warning("binarization preview generation failed: %s", exc, exc_info=True)
            self._preview_dirty = True
            self._restore_confirmed_preview(
                show_dirty_state=True
            )
            QMessageBox.warning(
                self,
                "2値化",
                "2値化プレビューの生成中にエラーが発生しました。\n\n"
                f"{exc}",
            )
        finally:
            self._close_color_progress(progress)
            self._color_processing = False
            self.preview_confirm_button.setEnabled(
                self._preview_dirty
            )
            self.apply_button.setEnabled(
                success and not self._preview_dirty
            )
            QApplication.restoreOverrideCursor()

        return success

    def selected_palette(self):
        expected_key = self._current_preview_key()
        if (
            self._preview_dirty
            or self._palette is None
            or self._palette_key != expected_key
        ):
            raise ValueError(
                "現在の設定はまだプレビューへ反映されていません。"
                "先に「プレビュー」を押してください。"
            )
        return np.asarray(
            self._palette,
            dtype=np.uint8,
        ).copy()

    def selected_extraction_mode(self):
        return (
            "line"
            if self.extraction_mode.currentData() == "line"
            else "surface"
        )

    def _accept_reduction(self):
        if self._preview_dirty:
            QMessageBox.information(
                self,
                "2値化",
                "トーンカーブまたは色数が未反映です。\n"
                "先に下の「プレビュー」を押してください。",
            )
            return
        try:
            self.selected_palette()
        except ValueError as exc:
            log.info("palette not ready for apply: %s", exc)
            QMessageBox.warning(
                self,
                "2値化",
                str(exc),
            )
            return
        self.reduction_enabled = True
        self.accept()

    def _accept_without_reduction(self):
        self.reduction_enabled = False
        self.accept()
