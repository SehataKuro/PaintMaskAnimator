import numpy as np
import time
from PySide6.QtCore import QEvent, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from .i18n import tr
from .canvas import PaintCanvas
from .color_picker_dialog import ColorPickerDialog
from .errors import OperationError
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
            tr("空いている場所をクリック：点を追加／"
            "点をドラッグ：濃さを調整／"
            "右クリック：中間点を削除／"
            "ダブルクリック：標準へ戻す")
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
    """代表フレームからパレットを決め、全コマへ適用する。

    プレビューは1枚目で見せるが、パレットは ``reference_images``（先頭・中間・
    末尾などの代表フレーム）からまとめて作る。1枚だけで決めると、中間で色が
    増えるカットやフラッシュで色が潰れるため（Issue #10）。
    """

    def __init__(
        self,
        source_image,
        original_color_count,
        semi_transparent_count=0,
        opaque_background=False,
        background_rgb=None,
        parent=None,
        reference_images=(),
        allow_without_reduction=True,
        scope_choice=False,
        scan=False,
    ):
        """``allow_without_reduction`` が偽なら「そのまま読み込む」を出さない（読み込んだ
        あとのレイヤーにかけるとき）。``scope_choice`` なら「今のコマ／すべてのコマ」を選ばせる。
        ``scan`` なら「紙の地色とムラを白にそろえる」を出して ON にする（#51）。
        """
        super().__init__(parent)
        self.setWindowTitle(tr("2値化"))
        self.resize(820, 560)

        self.source_image = source_image.copy()
        # パレットの手本にする代表フレーム（1枚目を除く）。
        self.reference_images = [
            image.copy() for image in (reference_images or ())
            if image is not None and not image.isNull()
        ]
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
        # 紙の地色をそろえる前の画像と、自動検出した背景（#51 の切り替えで戻す）。
        self._raw_source_image = self.source_image.copy()
        self._raw_reference_images = [image.copy() for image in self.reference_images]
        self._detected_background = (self.opaque_background, self.background_rgb)
        # 人が指定した出力色（#62）。None なら色数から自動で決める。
        self._custom_palette = None
        self._custom_reduced_cache = {}
        self._palette = None
        self._palette_key = None
        self._base_palette_cache = {}
        self._base_reduced_cache = {}
        self._reduced_full_image = self.source_image.copy()
        self._confirmed_preview_image = self.source_image.copy()
        self._confirmed_preview_title = tr("2値化　100%")
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
            tr("白背景を自動検出")
            if self.opaque_background
            else tr("半透明 {count:,}px").format(count=self.semi_transparent_count)
        )
        summary = QLabel(
            tr("代表{frames}枚：1枚目{count:,}色／{note}　トーンカーブを元画像へ先に適用し、その後に色数を調整して2値化します。").format(
                frames=len(self.reference_images) + 1,
                count=self.original_color_count,
                note=background_note,
            )
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
        ) = self._build_preview_column(tr("元画像（トーンカーブ）　100%"))
        (
            reduced_column,
            self.reduced_title,
            self.reduced_preview,
            self.reduced_scroll,
        ) = self._build_preview_column(
            tr("2値化　100%")
        )
        previews.addLayout(original_column, 1)
        previews.addLayout(reduced_column, 1)
        layout.addLayout(previews, 1)

        controls = QFormLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setHorizontalSpacing(8)
        controls.setVerticalSpacing(4)

        self.extraction_mode = QComboBox()
        self.extraction_mode.addItem(tr("色面抽出"), "surface")
        self.extraction_mode.addItem(tr("ライン抽出"), "line")
        self.extraction_mode.setCurrentIndex(1)
        self.extraction_mode.setToolTip(
            tr("色面抽出：塗りの色面を保ちながらアンチエイリアスを除去します。\n"
            "ライン抽出：白背景と元の線色を保ちながら線画を2値化します。")
        )
        self.extraction_mode.currentIndexChanged.connect(
            self._mark_preview_dirty
        )
        controls.addRow(tr("2値化方式"), self.extraction_mode)

        # 設定のプリセット（#10）。方式・色数・トーンカーブ・出力する色・紙の地色を
        # 名前を付けて保存し、次の素材で呼び出す。
        preset_row = QWidget()
        preset_layout = QHBoxLayout(preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(4)
        self.preset_choice = QComboBox()
        self.preset_choice.setMinimumWidth(160)
        self.preset_choice.activated.connect(self._preset_chosen)
        self.preset_save_button = QPushButton(tr("保存…"))
        self.preset_save_button.setToolTip(tr("今の設定に名前を付けて保存します。"))
        self.preset_save_button.clicked.connect(self._save_preset_clicked)
        self.preset_delete_button = QPushButton(tr("削除"))
        self.preset_delete_button.clicked.connect(self._delete_preset_clicked)
        preset_layout.addWidget(self.preset_choice, 1)
        preset_layout.addWidget(self.preset_save_button)
        preset_layout.addWidget(self.preset_delete_button)
        controls.addRow(tr("プリセット"), preset_row)

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
        self.color_count.setSuffix(tr(" 色"))
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
        controls.addRow(tr("色数"), color_row)

        # 出力する色（#62）。自動で決めた色を初期値に、足す・消す・変える。
        palette_row = QWidget()
        self.palette_layout = QHBoxLayout(palette_row)
        self.palette_layout.setContentsMargins(0, 0, 0, 0)
        self.palette_layout.setSpacing(3)
        self.palette_swatches = []
        self.add_color_button = QToolButton()
        self.add_color_button.setText("＋")
        self.add_color_button.setToolTip(tr("出力する色を足します。"))
        self.add_color_button.clicked.connect(self._add_palette_color)
        self.auto_palette_button = QPushButton(tr("自動に戻す"))
        self.auto_palette_button.setToolTip(
            tr("指定した色を捨てて、色数から自動で決めた色に戻します。")
        )
        self.auto_palette_button.clicked.connect(self._reset_custom_palette)
        self.auto_palette_button.setEnabled(False)
        self.palette_layout.addStretch(1)
        self.palette_layout.addWidget(self.add_color_button)
        self.palette_layout.addWidget(self.auto_palette_button)
        palette_row.setToolTip(tr(
            "左クリックで色を変更、右クリックで削除。指定した色だけで描き直します。"
            "指定すると色数は使いません。"
        ))
        controls.addRow(tr("出力する色"), palette_row)

        self.flatten_paper = None
        if scan:
            self.flatten_paper = QCheckBox(tr("紙の地色とムラを白にそろえる"))
            self.flatten_paper.setToolTip(tr(
                "スキャンした紙のクリーム色・照明のムラ・影を、場所ごとに見積もって白へ"
                "そろえてから2値化します。鉛筆の濃淡は、紙との比のまま残ります。"
            ))
            self.flatten_paper.setChecked(True)
            self.flatten_paper.toggled.connect(self._flatten_paper_toggled)
            controls.addRow(tr("スキャン"), self.flatten_paper)

        # ゴミ取り（#10）：2値化のあとで、ほかの絵から離れた小さな点を消す。
        from .despeckle import DEFAULT_MAX_PIXELS

        despeckle_row = QWidget()
        despeckle_layout = QHBoxLayout(despeckle_row)
        despeckle_layout.setContentsMargins(0, 0, 0, 0)
        despeckle_layout.setSpacing(4)
        self.despeckle_check = QCheckBox(tr("離れた小さな点を消す"))
        self.despeckle_check.setToolTip(tr(
            "2値化のあとで、ほかの絵に触れていない小さな点（スキャンのゴミなど）を消します。"
            "線や色面にくっついた点は消しません。"
        ))
        self.despeckle_check.setChecked(bool(scan))
        self.despeckle_size = QSpinBox()
        self.despeckle_size.setRange(1, 100)
        self.despeckle_size.setValue(DEFAULT_MAX_PIXELS)
        self.despeckle_size.setSuffix(tr(" px以下"))
        self.despeckle_check.toggled.connect(self._mark_preview_dirty)
        self.despeckle_check.toggled.connect(self.despeckle_size.setEnabled)
        self.despeckle_size.valueChanged.connect(self._mark_preview_dirty)
        self.despeckle_size.setEnabled(self.despeckle_check.isChecked())
        despeckle_layout.addWidget(self.despeckle_check)
        despeckle_layout.addWidget(self.despeckle_size)
        despeckle_layout.addStretch(1)
        controls.addRow(tr("ゴミ取り"), despeckle_row)

        self.all_frames_choice = None
        if scope_choice:
            self.all_frames_choice = QComboBox()
            self.all_frames_choice.addItem(tr("今のコマ"), False)
            self.all_frames_choice.addItem(tr("すべてのコマ"), True)
            self.all_frames_choice.setCurrentIndex(1)
            controls.addRow(tr("範囲"), self.all_frames_choice)

        tone_row = QWidget()
        tone_layout = QHBoxLayout(tone_row)
        tone_layout.setContentsMargins(0, 0, 0, 0)
        tone_layout.setSpacing(6)

        self.tone_curve = ToneCurveWidget()
        self.tone_curve_label = QLabel(tr("標準"))
        self.tone_curve_label.setFixedWidth(66)
        self.tone_curve_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        tone_reset = QPushButton(tr("標準"))
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
        controls.addRow(tr("トーンカーブ"), tone_row)
        layout.addLayout(controls)

        note = QLabel(
            tr("色数の変更中はプレビューを更新しません。"
            "トーンカーブ操作中は元画像へ一時適用し、"
            "離すと直前の2値化プレビューへ戻ります。")
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size:10px;")
        layout.addWidget(note)

        confirm_row = QHBoxLayout()
        self.preview_status = QLabel(tr("プレビュー反映済み"))
        self.preview_status.setStyleSheet(
            "color:#28744a;font-weight:bold;"
        )
        self.preview_confirm_button = QPushButton(tr("プレビュー"))
        self.preview_confirm_button.setToolTip(
            tr("現在のトーンカーブと色数を使って"
            "2値化プレビューを更新します。")
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
        self.apply_button = QPushButton(
            tr("全コマを2値化") if allow_without_reduction else tr("2値化")
        )
        self.no_reduction_button = QPushButton(tr("そのまま読み込む"))
        self.no_reduction_button.setVisible(bool(allow_without_reduction))
        cancel_button = QPushButton(tr("キャンセル"))
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

        self._apply_paper_setting()
        self.refresh_presets()
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
            tr("標準")
            if point_count == 0
            else tr("中間点 {count}").format(count=point_count)
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
            self._custom_palette_key(),
            self.despeckle_pixels(),
        )

    def despeckle_pixels(self):
        """ゴミ取りで消す点の大きさ（px）。切っていれば 0。"""
        if not self.despeckle_check.isChecked():
            return 0
        return int(self.despeckle_size.value())

    # -- プリセット（#10） ---------------------------------------------------
    def current_settings(self):
        """プリセットとして保存する設定（JSON にできる値だけ）。"""
        settings = {
            "extraction_mode": self.selected_extraction_mode(),
            "color_count": int(self.color_count.value()),
            "tone_curve_points": [[float(x), float(y)] for x, y in self.tone_curve_points()],
            "palette": (
                [[int(v) for v in color] for color in self._custom_palette]
                if self._custom_palette is not None else None
            ),
        }
        if self.flatten_paper is not None:
            settings["flatten_paper"] = bool(self.flatten_paper.isChecked())
        settings["despeckle"] = self.despeckle_pixels()
        return settings

    def apply_settings(self, settings):
        """プリセットの設定をダイアログへ入れる（知らない値・壊れた値は飛ばす）。"""
        mode = settings.get("extraction_mode")
        index = self.extraction_mode.findData(mode)
        if index >= 0:
            self.extraction_mode.setCurrentIndex(index)
        count = settings.get("color_count")
        if isinstance(count, int) and not isinstance(count, bool):
            self.color_count.setValue(max(2, min(100, count)))
        points = settings.get("tone_curve_points")
        if isinstance(points, list):
            try:
                self.tone_curve.setPoints([(float(x), float(y)) for x, y in points])
            except (TypeError, ValueError) as exc:
                log.info("ignoring a broken tone curve in a preset: %s", exc)
        if self.flatten_paper is not None and isinstance(settings.get("flatten_paper"), bool):
            self.flatten_paper.setChecked(settings["flatten_paper"])
        despeckle = settings.get("despeckle")
        if isinstance(despeckle, int) and not isinstance(despeckle, bool):
            self.despeckle_check.setChecked(despeckle > 0)
            if despeckle > 0:
                self.despeckle_size.setValue(max(1, min(100, despeckle)))
        palette = settings.get("palette")
        try:
            colors = [tuple(int(v) for v in color[:3]) for color in palette] if palette else []
        except (TypeError, ValueError):
            colors = []
        if all(0 <= v <= 255 for color in colors for v in color):
            self.set_custom_palette(colors)
        self._mark_preview_dirty()

    def refresh_presets(self, selected=None):
        from . import preferences

        names = sorted(preferences.binarize_presets())
        self.preset_choice.blockSignals(True)
        self.preset_choice.clear()
        self.preset_choice.addItem(tr("（選ぶ）"), None)
        for name in names:
            self.preset_choice.addItem(name, name)
        if selected is not None:
            index = self.preset_choice.findData(selected)
            self.preset_choice.setCurrentIndex(max(0, index))
        self.preset_choice.blockSignals(False)
        self.preset_delete_button.setEnabled(bool(names))

    def load_preset(self, name):
        from . import preferences

        settings = preferences.binarize_presets().get(str(name))
        if settings is None:
            return False
        self.apply_settings(settings)
        return True

    def save_preset(self, name):
        from . import preferences

        name = str(name).strip()
        if not name:
            return False
        preferences.save_binarize_preset(name, self.current_settings())
        self.refresh_presets(selected=name)
        return True

    def _preset_chosen(self, index):
        name = self.preset_choice.itemData(index)
        if name is not None:
            self.load_preset(name)

    def _save_preset_clicked(self):
        current = self.preset_choice.currentData() or ""
        name, accepted = QInputDialog.getText(
            self, tr("プリセットを保存"), tr("名前："), text=str(current),
        )
        if accepted:
            self.save_preset(name)

    def _delete_preset_clicked(self):
        from . import preferences

        name = self.preset_choice.currentData()
        if name is None:
            return
        preferences.delete_binarize_preset(name)
        self.refresh_presets()

    # -- 紙の地色（#51） ---------------------------------------------------
    def flatten_paper_enabled(self):
        return bool(self.flatten_paper is not None and self.flatten_paper.isChecked())

    def _apply_paper_setting(self):
        """チェックに合わせて、プレビューとパレットの手本にする画像を作り直す。"""
        if self.flatten_paper_enabled():
            from .canvas_image_import import flatten_paper_image

            self.source_image = flatten_paper_image(self._raw_source_image)
            self.reference_images = [
                flatten_paper_image(image) for image in self._raw_reference_images
            ]
            # そろえたあとの紙は白なので、白背景として扱う。
            self.opaque_background, self.background_rgb = True, (255, 255, 255)
        else:
            self.source_image = self._raw_source_image.copy()
            self.reference_images = [image.copy() for image in self._raw_reference_images]
            self.opaque_background, self.background_rgb = self._detected_background
        self._base_palette_cache.clear()
        self._base_reduced_cache.clear()
        self._custom_reduced_cache.clear()
        self._tone_preview_source_rgba = None
        self._tone_preview_background_mask = None
        self._tone_adjusted_preview_image = self.source_image.copy()

    def _flatten_paper_toggled(self, _checked=None):
        self._apply_paper_setting()
        self._mark_preview_dirty()

    def _custom_palette_key(self):
        if self._custom_palette is None:
            return None
        return tuple(tuple(int(v) for v in color) for color in self._custom_palette)

    # -- 出力する色（#62） -----------------------------------------------
    def selected_all_frames(self):
        """範囲の選択（``scope_choice`` のときだけ意味がある）。"""
        if self.all_frames_choice is None:
            return True
        return bool(self.all_frames_choice.currentData())

    def set_custom_palette(self, colors):
        """出力する色を指定する。空なら自動に戻す。"""
        colors = [tuple(int(v) for v in color[:3]) for color in colors]
        unique = list(dict.fromkeys(colors))
        self._custom_palette = (
            np.asarray(unique, dtype=np.uint8).reshape((-1, 3)) if unique else None
        )
        custom = self._custom_palette is not None
        self.color_count.setEnabled(not custom)
        self.color_slider.setEnabled(not custom)
        self.auto_palette_button.setEnabled(custom)
        self._refresh_palette_swatches(
            self._custom_palette if custom else self._palette
        )
        self._mark_preview_dirty()

    def _reset_custom_palette(self):
        self.set_custom_palette([])

    def _shown_palette(self):
        palette = self._custom_palette if self._custom_palette is not None else self._palette
        if palette is None:
            return []
        return [tuple(int(v) for v in color) for color in palette]

    def _add_palette_color(self):
        colors = self._shown_palette()
        initial = QColor(*colors[-1]) if colors else QColor("black")
        color = ColorPickerDialog.get_color(initial, self, tr("出力する色を足す"))
        if color.isValid():
            self.set_custom_palette(colors + [(color.red(), color.green(), color.blue())])

    def _edit_palette_color(self, index):
        colors = self._shown_palette()
        if not 0 <= index < len(colors):
            return
        color = ColorPickerDialog.get_color(QColor(*colors[index]), self, tr("出力する色"))
        if color.isValid():
            colors[index] = (color.red(), color.green(), color.blue())
            self.set_custom_palette(colors)

    def _remove_palette_color(self, index):
        colors = self._shown_palette()
        if len(colors) <= 1 or not 0 <= index < len(colors):
            return
        del colors[index]
        self.set_custom_palette(colors)

    def _palette_swatch_menu(self, index, button, position):
        menu = QMenu(self)
        remove = menu.addAction(tr("この色を使わない"))
        remove.setEnabled(len(self._shown_palette()) > 1)
        if menu.exec(button.mapToGlobal(position)) is remove:
            self._remove_palette_color(index)

    def _refresh_palette_swatches(self, palette):
        for button in self.palette_swatches:
            self.palette_layout.removeWidget(button)
            button.deleteLater()
        self.palette_swatches = []
        colors = [] if palette is None else [tuple(int(v) for v in c) for c in palette]
        for index, rgb in enumerate(colors):
            button = QToolButton()
            button.setFixedSize(22, 22)
            button.setToolTip("#{:02X}{:02X}{:02X}".format(*rgb))
            button.setStyleSheet(
                "QToolButton{background:#%02x%02x%02x;border:1px solid #777;}" % rgb
            )
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.clicked.connect(lambda _checked=False, i=index: self._edit_palette_color(i))
            button.customContextMenuRequested.connect(
                lambda position, i=index, b=button: self._palette_swatch_menu(i, b, position)
            )
            self.palette_layout.insertWidget(index, button)
            self.palette_swatches.append(button)

    def _mark_preview_dirty(self, _value=None):
        if self._initial_preview:
            return
        self._preview_dirty = True
        self.preview_status.setText(tr("設定未反映"))
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
                tr("元画像（トーンカーブ）　100%")
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
            title += tr("（設定未反映）")
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

        self.original_title.setText(tr("元画像（トーンカーブ）　100%"))
        original_image = getattr(
            self,
            "_tone_adjusted_preview_image",
            self.source_image,
        )
        set_image(self.original_preview, original_image)
        set_image(self.reduced_preview, self._reduced_full_image)

    def _create_color_progress(self):
        progress = QProgressDialog(
            tr("色相を分類しています"),
            "",
            0,
            3,
            self,
        )
        progress.setWindowTitle(tr("2値化"))
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
                raise OperationError(
                    tr("トーンカーブ適用後の画像を生成できませんでした。")
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
                    tr("トーンカーブを元画像へ適用しています"),
                )
                self._update_color_progress(
                    progress,
                    1,
                    tr("近い色相をまとめて代表色を作成しています"),
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
                        # 代表フレームにも同じトーンカーブをかけて手本にする。
                        extra_images=[
                            PaintCanvas.apply_tone_curve(
                                reference,
                                tone_curve_points=tone_points,
                                background_rgb=processing_background_rgb,
                            )
                            for reference in self.reference_images
                        ],
                    )
                )

                self._update_color_progress(
                    progress,
                    2,
                    (
                        tr("線のアンチエイリアスを線色と白背景へ分けています")
                        if extraction_mode == "line"
                        else tr("色面の境界を2色へ分けています")
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
                    raise OperationError(
                        tr("2値化画像を生成できませんでした。")
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
                    tr("プレビューを更新しています"),
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
            custom_key = self._custom_palette_key()
            if custom_key is not None and self._custom_palette is not None:
                # 指定した色だけで描き直す。自動の色では上書きしない（#62）。
                palette = self._custom_palette.copy()
                custom_cache_key = (extraction_mode, tone_key, custom_key)
                if custom_cache_key not in self._custom_reduced_cache:
                    custom_reduced = PaintCanvas.apply_color_reduction_palette(
                        tone_adjusted_source,
                        palette,
                        alpha_threshold=alpha_threshold,
                        opaque_background=processing_opaque_background,
                        background_rgb=processing_background_rgb,
                        tone_curve_points=[(0.0, 0.0), (1.0, 1.0)],
                        extraction_mode=extraction_mode,
                    )
                    if custom_reduced is None or custom_reduced.isNull():
                        raise OperationError(tr("2値化画像を生成できませんでした。"))
                    if len(self._custom_reduced_cache) >= 8:
                        self._custom_reduced_cache.clear()
                    self._custom_reduced_cache[custom_cache_key] = custom_reduced.copy()
                reduced = self._custom_reduced_cache[custom_cache_key].copy()

            if self.despeckle_pixels():
                from . import despeckle, imaging

                cleaned, removed = despeckle.remove_isolated_specks(
                    imaging.qimage_rgba_array(reduced), self.despeckle_pixels()
                )
                if removed:
                    reduced = imaging.rgba_array_to_qimage(cleaned)

            self._palette = palette
            self._palette_key = self._current_preview_key()
            self._refresh_palette_swatches(palette)
            self._reduced_full_image = reduced

            statistics = PaintCanvas.image_alpha_statistics(
                reduced
            )
            actual = len(palette)
            mode_name = self.extraction_mode.currentText()
            title = (
                tr("{name}　100%（{actual}色／半透明 {transparent}px）").format(name=mode_name, actual=actual, transparent=statistics['semi_transparent'])
            )
            self.reduced_title.setText(title)
            self._refresh_preview_pixmaps()

            self._confirmed_preview_image = reduced.copy()
            self._confirmed_preview_title = title
            self._preview_dirty = False
            self.preview_status.setText(tr("プレビュー反映済み"))
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
                tr("2値化"),
                tr("2値化プレビューの生成中にエラーが発生しました。\n\n{exc}").format(exc=exc),
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
            raise OperationError(
                tr("現在の設定はまだプレビューへ反映されていません。"
                "先に「プレビュー」を押してください。")
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
                tr("2値化"),
                tr("トーンカーブまたは色数が未反映です。\n"
                "先に下の「プレビュー」を押してください。"),
            )
            return
        try:
            self.selected_palette()
        except ValueError as exc:
            log.info("palette not ready for apply: %s", exc)
            QMessageBox.warning(
                self,
                tr("2値化"),
                str(exc),
            )
            return
        self.reduction_enabled = True
        self.accept()

    def _accept_without_reduction(self):
        self.reduction_enabled = False
        self.accept()
