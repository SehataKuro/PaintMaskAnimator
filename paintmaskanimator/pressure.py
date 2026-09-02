import math

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .i18n import tr
from .logging_setup import get_logger

log = get_logger(__name__)


def _pressure_bezier_segments(points):
    """形状を保ちながら滑らかにつながる区分3次ベジエ曲線を生成する。"""
    ordered = []
    for x, y in sorted(
        (
            max(0.0, min(1.0, float(x))),
            max(0.0, min(1.0, float(y))),
        )
        for x, y in points
    ):
        if ordered and abs(x - ordered[-1][0]) < 1e-6:
            ordered[-1] = (x, y)
        else:
            ordered.append((x, y))

    if len(ordered) < 2:
        ordered = [(0.0, 0.0), (1.0, 1.0)]

    count = len(ordered)
    widths = [
        max(1e-6, ordered[index + 1][0] - ordered[index][0])
        for index in range(count - 1)
    ]
    slopes = [
        (ordered[index + 1][1] - ordered[index][1]) / widths[index]
        for index in range(count - 1)
    ]

    tangents = [0.0] * count
    tangents[0] = slopes[0]
    tangents[-1] = slopes[-1]
    for index in range(1, count - 1):
        left = slopes[index - 1]
        right = slopes[index]
        if left == 0.0 or right == 0.0 or left * right <= 0.0:
            tangents[index] = 0.0
        else:
            left_width = widths[index - 1]
            right_width = widths[index]
            weight_left = 2.0 * right_width + left_width
            weight_right = right_width + 2.0 * left_width
            tangents[index] = (
                weight_left + weight_right
            ) / (
                weight_left / left
                + weight_right / right
            )

    for index, slope in enumerate(slopes):
        if abs(slope) < 1e-12:
            tangents[index] = 0.0
            tangents[index + 1] = 0.0
            continue
        alpha = tangents[index] / slope
        beta = tangents[index + 1] / slope
        magnitude = alpha * alpha + beta * beta
        if magnitude > 9.0:
            scale = 3.0 / math.sqrt(magnitude)
            tangents[index] = scale * alpha * slope
            tangents[index + 1] = scale * beta * slope

    segments = []
    for index in range(count - 1):
        p1 = ordered[index]
        p2 = ordered[index + 1]
        width = widths[index]
        c1 = (
            p1[0] + width / 3.0,
            p1[1] + tangents[index] * width / 3.0,
        )
        c2 = (
            p2[0] - width / 3.0,
            p2[1] - tangents[index + 1] * width / 3.0,
        )
        c1 = (
            max(p1[0], min(p2[0], c1[0])),
            max(0.0, min(1.0, c1[1])),
        )
        c2 = (
            max(p1[0], min(p2[0], c2[0])),
            max(0.0, min(1.0, c2[1])),
        )
        segments.append((p1, c1, c2, p2))
    return segments


def _cubic_bezier_value(a, b, c, d, t):
    inv = 1.0 - t
    return (
        inv * inv * inv * a
        + 3.0 * inv * inv * t * b
        + 3.0 * inv * t * t * c
        + t * t * t * d
    )


def _pressure_bezier_at(points, pressure):
    """入力Xに対応する区分3次ベジエ曲線上のYを返す。"""
    x = max(0.0, min(1.0, float(pressure)))
    segments = _pressure_bezier_segments(points)
    segment = segments[-1]
    for candidate in segments:
        if x <= candidate[3][0]:
            segment = candidate
            break
    p0, c1, c2, p3 = segment
    if x <= p0[0]:
        return max(0.0, min(1.0, p0[1]))
    if x >= p3[0]:
        return max(0.0, min(1.0, p3[1]))

    # ベジエ曲線のX(t)=入力値となるtを二分探索する。
    low, high = 0.0, 1.0
    for _ in range(32):
        t = (low + high) * 0.5
        bx = _cubic_bezier_value(p0[0], c1[0], c2[0], p3[0], t)
        if bx < x:
            low = t
        else:
            high = t
    t = (low + high) * 0.5
    y = _cubic_bezier_value(p0[1], c1[1], c2[1], p3[1], t)
    return max(0.0, min(1.0, y))


class PressureCurveWidget(QWidget):
    """アンカーポイントを追加・移動・削除できる筆圧ベジエカーブ。"""
    curveChanged = Signal(object)

    def __init__(self, curve=1.0, parent=None):
        super().__init__(parent)
        if isinstance(curve, (list, tuple)) and len(curve) >= 2:
            points = []
            for point in curve:
                try:
                    x, y = float(point[0]), float(point[1])
                    points.append((max(0.0, min(1.0, x)), max(0.0, min(1.0, y))))
                except (TypeError, ValueError, IndexError, KeyError) as exc:
                    log.debug("skipping malformed pressure-curve point %r: %s", point, exc)
                    continue
            self._points = sorted(points, key=lambda value: value[0])
        else:
            exponent = max(0.20, min(4.0, float(curve)))
            self._points = [(0.0, 0.0), (0.5, 0.5 ** exponent), (1.0, 1.0)]
        if len(self._points) < 2:
            self._points = [(0.0, 0.0), (1.0, 1.0)]
        self._points[0] = (0.0, self._points[0][1])
        self._points[-1] = (1.0, self._points[-1][1])
        self._active_index = None
        self.setMinimumSize(260, 170)
        self.setMouseTracking(True)
        self.setToolTip(
            tr("左クリック：アンカーポイント追加／ドラッグ：移動／"
            "右クリック：中間アンカーポイント削除／曲線：3次ベジエ補間")
        )

    def points(self):
        return [[float(x), float(y)] for x, y in self._points]

    def exponent(self):
        # 旧形式との互換用。保存・描画では points() を使用する。
        return 1.0

    def _graph_rect(self):
        return self.rect().adjusted(28, 12, -12, -24)

    def _point_to_value(self, point):
        graph = self._graph_rect()
        x = (point.x() - graph.left()) / max(1, graph.width())
        y = 1.0 - (point.y() - graph.top()) / max(1, graph.height())
        return max(0.0, min(1.0, x)), max(0.0, min(1.0, y))

    def _value_to_point(self, x, y):
        graph = self._graph_rect()
        return QPointF(
            graph.left() + x * graph.width(),
            graph.bottom() - y * graph.height(),
        )

    def _nearest_index(self, point, radius=10.0):
        nearest = None
        nearest_distance = radius * radius
        for index, (x, y) in enumerate(self._points):
            screen = self._value_to_point(x, y)
            dx = screen.x() - point.x()
            dy = screen.y() - point.y()
            distance = dx * dx + dy * dy
            if distance <= nearest_distance:
                nearest = index
                nearest_distance = distance
        return nearest

    def _emit_changed(self):
        self.curveChanged.emit(self.points())
        self.update()

    def mousePressEvent(self, event):
        point = event.position()
        if event.button() == Qt.MouseButton.RightButton:
            index = self._nearest_index(point)
            if index is not None and 0 < index < len(self._points) - 1:
                del self._points[index]
                self._active_index = None
                self._emit_changed()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._nearest_index(point)
            if index is None:
                x, y = self._point_to_value(point)
                self._points.append((x, y))
                self._points.sort(key=lambda value: value[0])
                index = min(
                    range(len(self._points)),
                    key=lambda i: abs(self._points[i][0] - x) + abs(self._points[i][1] - y),
                )
                self._emit_changed()
            self._active_index = index
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._active_index is not None and event.buttons() & Qt.MouseButton.LeftButton:
            x, y = self._point_to_value(event.position())
            index = self._active_index
            if index == 0:
                x = 0.0
            elif index == len(self._points) - 1:
                x = 1.0
            else:
                left = self._points[index - 1][0] + 0.005
                right = self._points[index + 1][0] - 0.005
                x = max(left, min(right, x))
            self._points[index] = (x, y)
            self._emit_changed()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._active_index = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(36, 36, 36))
        graph = self._graph_rect()
        painter.setPen(QPen(QColor(90, 90, 90), 1))
        for i in range(5):
            x = graph.left() + graph.width() * i / 4
            y = graph.top() + graph.height() * i / 4
            painter.drawLine(QPointF(x, graph.top()), QPointF(x, graph.bottom()))
            painter.drawLine(QPointF(graph.left(), y), QPointF(graph.right(), y))
        painter.setPen(QPen(QColor(210, 210, 210), 1))
        painter.drawRect(graph)
        path = QPainterPath()
        segments = _pressure_bezier_segments(self._points)
        first_point = self._value_to_point(*segments[0][0])
        path.moveTo(first_point)
        for _start, control1, control2, end in segments:
            path.cubicTo(
                self._value_to_point(*control1),
                self._value_to_point(*control2),
                self._value_to_point(*end),
            )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(255, 210, 70), 2))
        painter.drawPath(path)
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(QPen(QColor(255, 210, 70), 2))
        for x, y in self._points:
            painter.drawEllipse(self._value_to_point(x, y), 5, 5)
        painter.setPen(QColor(220, 220, 220))
        painter.drawText(4, graph.top() + 10, tr("出力"))
        painter.drawText(graph.right() - 24, self.height() - 5, tr("入力"))
        painter.end()


class PressureDialog(QDialog):
    def __init__(self, enabled, minimum, maximum, curve, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("筆圧設定"))
        self.resize(430, 390)
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox(tr("筆圧をブラシサイズに反映"))
        self.enabled.setChecked(enabled)
        layout.addWidget(self.enabled)

        pressure_note = QLabel(
            tr("筆圧はブラシサイズだけに反映されます。"
            "描画不透明度・色・アルファ値には一切影響しません。")
        )
        pressure_note.setWordWrap(True)
        pressure_note.setStyleSheet(
            "color:#a33;font-weight:bold;"
        )
        layout.addWidget(pressure_note)

        form = QFormLayout()
        self.minimum = QSlider(Qt.Orientation.Horizontal)
        self.minimum.setRange(1, 100)
        self.minimum.setValue(max(1, min(100, int(round(float(minimum) * 100)))))
        self.minimum_label = QLabel(f"{self.minimum.value()}%")
        min_row = QWidget(); min_layout = QHBoxLayout(min_row)
        min_layout.setContentsMargins(0, 0, 0, 0)
        min_layout.addWidget(self.minimum, 1); min_layout.addWidget(self.minimum_label)
        self.minimum.valueChanged.connect(lambda v: self.minimum_label.setText(f"{v}%"))

        self.maximum = QSlider(Qt.Orientation.Horizontal)
        self.maximum.setRange(10, 300)
        self.maximum.setValue(max(10, min(300, int(round(float(maximum) * 100)))))
        self.maximum_label = QLabel(f"{self.maximum.value()}%")
        max_row = QWidget(); max_layout = QHBoxLayout(max_row)
        max_layout.setContentsMargins(0, 0, 0, 0)
        max_layout.addWidget(self.maximum, 1); max_layout.addWidget(self.maximum_label)
        self.maximum.valueChanged.connect(lambda v: self.maximum_label.setText(f"{v}%"))

        form.addRow(tr("最小サイズ"), min_row)
        form.addRow(tr("最大倍率"), max_row)
        layout.addLayout(form)
        layout.addWidget(QLabel(tr("筆圧カーブ")))
        self.curve = PressureCurveWidget(curve)
        layout.addWidget(self.curve, 1)
        self.curve_value = QLabel(tr("アンカーポイント: {len}").format(len=len(self.curve.points())))
        self.curve.curveChanged.connect(
            lambda points: self.curve_value.setText(tr("アンカーポイント: {len}").format(len=len(points)))
        )
        layout.addWidget(self.curve_value)

        b = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        b.accepted.connect(self.accept); b.rejected.connect(self.reject)
        layout.addWidget(b)
