#!/usr/bin/env python3
"""PaintMaskAnimator V0.5
Requires: PySide6, numpy
"""
import csv, json, math, re, shutil, subprocess, sys, tempfile, time, zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import List, Optional

APP_NAME = "PaintMaskAnimator"
APP_VERSION = "0.5"
APP_DISPLAY_NAME = f"{APP_NAME} V{APP_VERSION}"

try:
    from PIL import Image as PILImage, ImageFilter as PILImageFilter
except Exception:
    PILImage = None
    PILImageFilter = None

try:
    from psd_tools import PSDImage
except Exception:
    PSDImage = None

try:
    import numpy as np
    from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
    from PySide6.QtGui import QAction, QColor, QImage, QImageReader, QKeySequence, QPainter, QPainterPath, QPen, QPolygonF, QRegion, QTransform, QPixmap, QCursor, QValidator
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QColorDialog, QDialog, QDialogButtonBox,
        QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
        QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
        QMenu, QPushButton, QSlider, QSpinBox, QTableWidget, QComboBox,
        QTableWidgetItem, QTabBar, QTabWidget, QToolButton, QVBoxLayout, QWidget, QAbstractItemView, QHeaderView, QScrollArea,
        QKeySequenceEdit, QDockWidget, QProgressDialog, QPlainTextEdit, QStyledItemDelegate, QStyle,
        QSizePolicy, QAbstractSpinBox,
    )
except ModuleNotFoundError as exc:
    # Double-clicking a .py normally closes the console immediately. Show a visible
    # explanation instead, and also print a copy-pasteable installation command.
    missing = exc.name or "必要なライブラリ"
    message = (
        f"{missing} がインストールされていないため起動できません。\n\n"
        "Windowsのコマンドプロンプトで次を実行してください。\n\n"
        "py -m pip install PySide6 numpy\n\n"
        "インストール後、このファイルをもう一度起動してください。"
    )
    print(message)
    try:
        import tkinter as _tk
        from tkinter import messagebox as _messagebox
        _root = _tk.Tk(); _root.withdraw()
        _messagebox.showerror(f"{APP_DISPLAY_NAME} 起動エラー", message)
        _root.destroy()
    except Exception:
        pass
    raise SystemExit(1) from None

CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720
OUTSIDE_MARGIN = 0
MAX_UNDO = 30
MAX_PROJECT_METADATA_BYTES = 16 * 1024 * 1024
MAX_PROJECT_FRAMES = 10000
MAX_PROJECT_LAYERS = 256
MAX_PROJECT_LAYER_CELLS = 100000
MAX_PROJECT_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024
MAX_PROJECT_IMAGE_BYTES = 512 * 1024 * 1024
MAX_PROJECT_DECODED_PIXELS = 2 * 1024 * 1024 * 1024
MAX_IMAGE_DIMENSION = 16384
MAX_SINGLE_IMAGE_PIXELS = MAX_IMAGE_DIMENSION * MAX_IMAGE_DIMENSION
TP_MASK_PROXY_THRESHOLD = 5000
TP_MASK_PROXY_MAX_DIMENSION = 1280
TP_MASK_PROXY_MAX_COLORS = 32


def workspace_size():
    return CANVAS_WIDTH + OUTSIDE_MARGIN * 2, CANVAS_HEIGHT + OUTSIDE_MARGIN * 2


def disable_windows_ink_feedback(*widgets):
    """Windows Inkの波紋・長押し・タップ視覚効果を無効化する。"""
    if sys.platform != "win32":
        return

    try:
        import ctypes
        from ctypes import wintypes

        function = (
            ctypes.windll.user32.SetWindowFeedbackSetting
        )
        function.argtypes = [
            wintypes.HWND,
            ctypes.c_uint,
            wintypes.DWORD,
            ctypes.c_uint,
            ctypes.c_void_p,
        ]
        function.restype = wintypes.BOOL

        # FWFS_OVERRIDE
        flags = 0x00000001
        disabled = wintypes.BOOL(False)

        # 1～11:
        # タッチ接触表示、ペン樽表示、タップ、ダブルタップ、
        # 長押し、右タップ、タッチ系表示、PressAndTap。
        feedback_types = range(1, 12)

        for widget in widgets:
            if widget is None:
                continue
            try:
                handle = wintypes.HWND(
                    int(widget.winId())
                )
            except Exception:
                continue

            for feedback_type in feedback_types:
                try:
                    function(
                        handle,
                        int(feedback_type),
                        flags,
                        ctypes.sizeof(disabled),
                        ctypes.byref(disabled),
                    )
                except Exception:
                    pass
    except Exception:
        # Windowsのバージョンや環境が未対応でも起動は継続する。
        pass


def blank_image(fill=Qt.GlobalColor.transparent):
    w, h = workspace_size()
    im = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    im.fill(fill)
    return im


def paper_image():
    """White paper only inside the real canvas; outside stays transparent/dark."""
    im = blank_image()
    p = QPainter(im)
    p.fillRect(OUTSIDE_MARGIN, OUTSIDE_MARGIN, CANVAS_WIDTH, CANVAS_HEIGHT, QColor("white"))
    p.end()
    return im


def checker_pixmap(width=48, height=28, cell=7):
    pm = QPixmap(width, height)
    p = QPainter(pm)
    for y in range(0, height, cell):
        for x in range(0, width, cell):
            c = QColor(235,235,235) if ((x//cell)+(y//cell)) % 2 == 0 else QColor(165,165,165)
            p.fillRect(x, y, cell, cell, c)
    p.end()
    return pm


@dataclass
class Layer:
    name: str
    image: QImage
    visible: bool = True
    opacity: float = 1.0
    is_paper: bool = False
    has_content: bool = False
    alpha_locked: bool = False
    exposure: int = 1
    color_filter_enabled: bool = False
    color_filter_rgb: Optional[tuple] = None
    is_blank_key: bool = False
    sequence_number: Optional[int] = None
    sequence_only: bool = False

    def clone(self):
        return Layer(
            self.name, self.image.copy(), self.visible, self.opacity,
            self.is_paper, self.has_content, self.alpha_locked, self.exposure,
            self.color_filter_enabled,
            tuple(self.color_filter_rgb) if self.color_filter_rgb is not None else None,
            bool(self.is_blank_key),
            self.sequence_number,
            bool(self.sequence_only),
        )


@dataclass
class Frame:
    layers: List[Layer]
    duration: int = 1

    def clone(self):
        return Frame([x.clone() for x in self.layers], self.duration)


def make_frame(layer_names=None):
    names = layer_names or ["Layer 1"]
    return Frame([Layer(name, blank_image()) for name in names])




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
                except Exception:
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
            "左クリック：アンカーポイント追加／ドラッグ：移動／"
            "右クリック：中間アンカーポイント削除／曲線：3次ベジエ補間"
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
        painter.drawText(4, graph.top() + 10, "出力")
        painter.drawText(graph.right() - 24, self.height() - 5, "入力")
        painter.end()


class PressureDialog(QDialog):
    def __init__(self, enabled, minimum, maximum, curve, parent=None):
        super().__init__(parent)
        self.setWindowTitle("筆圧設定")
        self.resize(430, 390)
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox("筆圧をブラシサイズに反映")
        self.enabled.setChecked(enabled)
        layout.addWidget(self.enabled)

        pressure_note = QLabel(
            "筆圧はブラシサイズだけに反映されます。"
            "描画不透明度・色・アルファ値には一切影響しません。"
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

        form.addRow("最小サイズ", min_row)
        form.addRow("最大倍率", max_row)
        layout.addLayout(form)
        layout.addWidget(QLabel("筆圧カーブ"))
        self.curve = PressureCurveWidget(curve)
        layout.addWidget(self.curve, 1)
        self.curve_value = QLabel(f"アンカーポイント: {len(self.curve.points())}")
        self.curve.curveChanged.connect(
            lambda points: self.curve_value.setText(f"アンカーポイント: {len(points)}")
        )
        layout.addWidget(self.curve_value)

        b = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        b.accepted.connect(self.accept); b.rejected.connect(self.reject)
        layout.addWidget(b)


class TransformLineThicknessDialog(QDialog):
    """TPクオリティ変形の実線太さをライブ調整する。"""

    def __init__(self, value=96, parent=None):
        super().__init__(parent)
        self.setWindowTitle("太さを調整")
        self.setModal(True)
        self.resize(380, 130)

        layout = QVBoxLayout(self)
        note = QLabel(
            "選択中の複数色を実線として残す太さをまとめて調整します。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        row = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(1, 254)
        self.slider.setInvertedAppearance(True)
        self.slider.setValue(max(1, min(254, int(value))))
        self.value_label = QLabel(f"{255 - self.slider.value()}")
        self.value_label.setFixedWidth(42)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.slider.valueChanged.connect(
            lambda current: self.value_label.setText(str(255 - current))
        )
        row.addWidget(self.slider, 1)
        row.addWidget(self.value_label)
        layout.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self):
        return int(self.slider.value())



class CanvasSizeDialog(QDialog):
    def __init__(self, width, height, title, parent=None):
        super().__init__(parent); self.setWindowTitle(title)
        l = QFormLayout(self)
        self.w = QSpinBox(); self.w.setRange(64, 8192); self.w.setValue(width)
        self.h = QSpinBox(); self.h.setRange(64, 8192); self.h.setValue(height)
        l.addRow("幅", self.w); l.addRow("高さ", self.h)
        b = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        b.accepted.connect(self.accept); b.rejected.connect(self.reject); l.addRow(b)
    def values(self): return self.w.value(), self.h.value()


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
            widget = item.widget()
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
            slider.setStyleSheet(
                "QSlider::groove:vertical{width:4px;"
                "background:#777;border-radius:2px;}"
                "QSlider::handle:vertical{height:8px;width:12px;"
                "margin:0 -4px;background:#e8e8e8;"
                "border:1px solid #555;border-radius:2px;}"
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
            except Exception:
                pass
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
            except Exception:
                pass
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
            except Exception:
                pass
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
            except Exception:
                pass
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


class OnionSkinSettingsBrowser(QDockWidget):
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
        super().__init__("オニオンスキン設定", parent)
        self.setObjectName("temporaryOnionSkinSettingsBrowser")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.setMinimumWidth(320)

        self.previous_color = QColor(previous_color)
        self.next_color = QColor(next_color)

        container = QWidget()
        layout = QVBoxLayout(container)
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

        self.setWidget(container)

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

class HoldShortcutEdit(QPushButton):
    """押している間だけ有効な操作用。修飾キー単独も登録できるキー入力欄。"""

    def __init__(self, shortcut_text="", parent=None):
        super().__init__(parent)
        self._shortcut_text = str(shortcut_text or "")
        self._waiting = False
        self.setMinimumWidth(180)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._begin_capture)
        self._refresh_text()

    def _begin_capture(self):
        self._waiting = True
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.setText("キーを押してください…")

    def shortcutText(self):
        return self._shortcut_text

    def setShortcutText(self, text):
        self._shortcut_text = str(text or "")
        self._waiting = False
        self._refresh_text()

    def _refresh_text(self):
        self.setText(self._shortcut_text or "未設定")
        self.setToolTip(
            "クリックしてキーを入力。BackspaceまたはDeleteで解除。"
        )

    @staticmethod
    def _key_name(event):
        key_map = {
            Qt.Key.Key_Control: "Ctrl",
            Qt.Key.Key_Shift: "Shift",
            Qt.Key.Key_Alt: "Alt",
            Qt.Key.Key_Meta: "Meta",
            Qt.Key.Key_Space: "Space",
        }
        if event.key() in key_map:
            return key_map[event.key()]
        return QKeySequence(int(event.key())).toString(
            QKeySequence.SequenceFormat.PortableText
        )

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.setShortcutText("")
            event.accept()
            return
        key_name = self._key_name(event)
        if not key_name:
            super().keyPressEvent(event)
            return
        tokens = []
        modifiers = event.modifiers()
        for flag, name in (
            (Qt.KeyboardModifier.ControlModifier, "Ctrl"),
            (Qt.KeyboardModifier.ShiftModifier, "Shift"),
            (Qt.KeyboardModifier.AltModifier, "Alt"),
            (Qt.KeyboardModifier.MetaModifier, "Meta"),
        ):
            if modifiers & flag:
                tokens.append(name)
        if key_name not in tokens:
            tokens.append(key_name)
        self.setShortcutText("+".join(tokens))
        event.accept()

    def focusOutEvent(self, event):
        self._waiting = False
        self._refresh_text()
        super().focusOutEvent(event)


class ShortcutDialog(QDialog):
    def __init__(self, categories, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ショートカット設定")
        self.resize(700, 620)
        self.categories = categories
        layout = QVBoxLayout(self)
        note = QLabel(
            "ショートカット欄を選び、実際のキーまたはキーの組み合わせを押してください。"
            "文字列入力ではなくキー入力として認識します。"
            "「キャンバス操作」では修飾キー単独も登録できます。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tabs = QTabWidget()
        self.editors = []
        for title, actions in categories:
            page = QWidget()
            form = QFormLayout(page)
            page_editors = []
            for name, action in actions:
                if bool(action.property("holdOperation")):
                    stored = action.property("holdShortcutText")
                    editor = HoldShortcutEdit(
                        "" if stored is None else str(stored)
                    )
                else:
                    editor = QKeySequenceEdit(action.shortcut())
                    editor.setClearButtonEnabled(True)
                form.addRow(name, editor)
                page_editors.append((name, action, editor))
            form.addRow(QWidget())
            self.tabs.addTab(page, title)
            self.editors.extend(page_editors)
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self):
        used = {}
        duplicates = []
        pending = []
        for name, action, editor in self.editors:
            if isinstance(editor, HoldShortcutEdit):
                normalized = editor.shortcutText().strip()
                sequence = QKeySequence(normalized)
                is_hold = True
            else:
                sequence = editor.keySequence()
                normalized = sequence.toString(
                    QKeySequence.SequenceFormat.PortableText
                )
                is_hold = False
            if normalized:
                if normalized in used:
                    duplicates.append((normalized, used[normalized], name))
                else:
                    used[normalized] = name
            pending.append((action, sequence, is_hold, normalized))
        if duplicates:
            details = "\n".join(
                f"{shortcut}: {first} / {second}"
                for shortcut, first, second in duplicates[:12]
            )
            QMessageBox.warning(
                self,
                "ショートカットの重複",
                "同じショートカットが複数の機能に登録されています。\n\n" + details,
            )
            return
        for action, sequence, is_hold, normalized in pending:
            if is_hold:
                action.setProperty("holdShortcutText", normalized)
            action.setShortcut(sequence)
        self.accept()


def _sample_screen_color(global_position):
    """Return the opaque screen color at a global position."""
    screen = QApplication.screenAt(global_position) or QApplication.primaryScreen()
    if screen is None:
        return None
    geometry = screen.geometry()
    pixmap = screen.grabWindow(
        0,
        global_position.x() - geometry.x(),
        global_position.y() - geometry.y(),
        1,
        1,
    )
    image = pixmap.toImage()
    if image.isNull():
        return None
    color = image.pixelColor(0, 0)
    color.setAlpha(255)
    return color


class _ScreenColorDragMixin:
    """Mouse, pen and touch drag support for the screen eyedropper."""

    def _init_screen_color_drag(self):
        self._screen_pick_active = False
        self._screen_pick_button = Qt.MouseButton.NoButton
        self._screen_pick_press_global = None
        self._left_drag_candidate = False
        self._screen_pick_cursor_pushed = False
        self._touch_pick_candidate = False
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)

    @staticmethod
    def _event_global_position(event):
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        if hasattr(event, "points"):
            points = event.points()
            if points:
                return points[0].globalPosition().toPoint()
        return None

    def _emit_screen_color(self, color):
        if hasattr(self, "colorPicked"):
            self.colorPicked.emit(color)
        elif hasattr(self, "screenColorPicked"):
            self.screenColorPicked.emit(color)

    def _begin_screen_pick(self, button=Qt.MouseButton.LeftButton):
        self._screen_pick_active = True
        self._screen_pick_button = button
        self._left_drag_candidate = False
        self._touch_pick_candidate = False
        try:
            self.grabMouse()
        except Exception:
            pass
        QApplication.setOverrideCursor(Qt.CursorShape.CrossCursor)
        self._screen_pick_cursor_pushed = True
        try:
            self.setDown(False)
        except Exception:
            pass

    def _cancel_screen_pick_cursor(self):
        try:
            self.releaseMouse()
        except Exception:
            pass
        if self._screen_pick_cursor_pushed:
            QApplication.restoreOverrideCursor()
            self._screen_pick_cursor_pushed = False

    def _finish_screen_pick(self, global_position):
        self._screen_pick_active = False
        self._screen_pick_button = Qt.MouseButton.NoButton
        self._left_drag_candidate = False
        self._touch_pick_candidate = False
        self._screen_pick_press_global = None
        self._cancel_screen_pick_cursor()
        color = _sample_screen_color(global_position)
        if color is not None:
            self._emit_screen_color(color)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._screen_pick_press_global = event.globalPosition().toPoint()
            self._begin_screen_pick(Qt.MouseButton.RightButton)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._screen_pick_press_global = event.globalPosition().toPoint()
            self._left_drag_candidate = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._screen_pick_active:
            event.accept()
            return
        if (
            self._left_drag_candidate
            and self._screen_pick_press_global is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            distance = (
                event.globalPosition().toPoint() - self._screen_pick_press_global
            ).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._begin_screen_pick(Qt.MouseButton.LeftButton)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            self._screen_pick_active
            and event.button() == self._screen_pick_button
        ):
            self._finish_screen_pick(event.globalPosition().toPoint())
            event.accept()
            return
        self._left_drag_candidate = False
        self._screen_pick_press_global = None
        super().mouseReleaseEvent(event)

    def event(self, event):
        event_type = event.type()
        tablet_press = getattr(QEvent.Type, "TabletPress", None)
        tablet_move = getattr(QEvent.Type, "TabletMove", None)
        tablet_release = getattr(QEvent.Type, "TabletRelease", None)
        touch_begin = getattr(QEvent.Type, "TouchBegin", None)
        touch_update = getattr(QEvent.Type, "TouchUpdate", None)
        touch_end = getattr(QEvent.Type, "TouchEnd", None)
        touch_cancel = getattr(QEvent.Type, "TouchCancel", None)

        if event_type in (tablet_press, touch_begin):
            position = self._event_global_position(event)
            if position is not None:
                self._screen_pick_press_global = position
                self._touch_pick_candidate = True
                event.accept()
                return True

        if event_type in (tablet_move, touch_update):
            position = self._event_global_position(event)
            if position is not None:
                if self._screen_pick_active:
                    event.accept()
                    return True
                if (
                    self._touch_pick_candidate
                    and self._screen_pick_press_global is not None
                    and (position - self._screen_pick_press_global).manhattanLength()
                    >= max(4, QApplication.startDragDistance())
                ):
                    self._begin_screen_pick(Qt.MouseButton.LeftButton)
                    event.accept()
                    return True

        if event_type in (tablet_release, touch_end):
            position = self._event_global_position(event)
            if self._screen_pick_active and position is not None:
                self._finish_screen_pick(position)
                event.accept()
                return True
            if self._touch_pick_candidate:
                self._touch_pick_candidate = False
                self._screen_pick_press_global = None
                # A tap keeps the button's ordinary click behavior.
                QTimer.singleShot(0, self.click)
                event.accept()
                return True

        if event_type == touch_cancel:
            self._screen_pick_active = False
            self._touch_pick_candidate = False
            self._screen_pick_press_global = None
            self._cancel_screen_pick_cursor()
            event.accept()
            return True

        return super().event(event)


class SwatchEyedropButton(_ScreenColorDragMixin, QPushButton):
    colorPicked = Signal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_screen_color_drag()


class BrushSizeSpinBox(QDoubleSpinBox):
    """▲▼で0.5ずつ変更するブラシ／ラインサイズ表示。"""

    pressureRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0.5, 400.0)
        self.setDecimals(1)
        self.setSingleStep(0.5)
        self.setReadOnly(False)
        self.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.UpDownArrows
        )
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pressure_popup_enabled = True
        editor = self.lineEdit()
        editor.setReadOnly(True)
        editor.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._refresh_tooltip()

    def _refresh_tooltip(self):
        self.setToolTip(
            "▲▼をクリック：0.5ずつ変更"
            + (
                "／数値部分をクリック：筆圧設定"
                if self._pressure_popup_enabled else ""
            )
        )

    def setPressurePopupEnabled(self, enabled):
        self._pressure_popup_enabled = bool(enabled)
        self._refresh_tooltip()

    def _clear_number_selection(self):
        editor = self.lineEdit()
        if editor is None:
            return
        editor.deselect()
        editor.setCursorPosition(len(editor.text()))
        editor.clearFocus()
        self.clearFocus()

    def stepBy(self, steps):
        # ▲▼で変更した後、数値全体が青く選択される状態を残さない。
        super().stepBy(steps)
        QTimer.singleShot(0, self._clear_number_selection)

    def mouseReleaseEvent(self, event):
        if (
            self._pressure_popup_enabled
            and event.button() == Qt.MouseButton.LeftButton
            and self.lineEdit().geometry().contains(
                event.position().toPoint()
            )
        ):
            self.pressureRequested.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        QTimer.singleShot(0, self._clear_number_selection)

    def wheelEvent(self, event):
        event.ignore()



class SliderValueSpinBox(QDoubleSpinBox):
    """RGB/HSVスライダーと連動する、編集可能な小型数値欄。"""

    def __init__(self, slider, scale=1.0, step=1.0, parent=None):
        super().__init__(parent)
        self.slider = slider
        self.scale = max(1e-9, float(scale))
        self._ascii_step = float(step)

        self.setDecimals(
            1 if abs(float(step) - round(float(step))) > 1e-9 else 0
        )
        self.setSingleStep(float(step))
        self.setRange(
            float(slider.minimum()) / self.scale,
            float(slider.maximum()) / self.scale,
        )
        self.setReadOnly(False)
        self.setKeyboardTracking(True)
        self.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # 0～255の数字と小型▲▼だけが収まる幅に圧縮する。
        self.setFixedWidth(48)
        self.setFixedHeight(22)
        self.setStyleSheet(
            "QDoubleSpinBox{font-size:10px;padding-left:1px;"
            "padding-right:10px;"
            "selection-background-color:#3874b8;}"
        )

        editor = self.lineEdit()
        editor.setReadOnly(False)
        editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        editor.setMaxLength(5 if self.decimals() else 3)
        editor.setAlignment(Qt.AlignmentFlag.AlignRight)
        editor.setTextMargins(0, 0, 9, 0)
        editor.setToolTip(
            "半角数字だけ入力できます。"
            if self.decimals() == 0
            else "半角数字と小数点だけ入力できます。"
        )

        # 標準の四角いスピンボタンを使わず、文字の▲▼を直接表示する。
        self._up_button = QToolButton(self)
        self._down_button = QToolButton(self)
        for button, label in (
            (self._up_button, "▲"),
            (self._down_button, "▼"),
        ):
            button.setText(label)
            button.setAutoRepeat(True)
            button.setAutoRepeatDelay(350)
            button.setAutoRepeatInterval(70)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                "QToolButton{border:none;background:transparent;"
                "padding:0px;margin:0px;font-size:7px;}"
                "QToolButton:hover{background:#d8d8d8;}"
                "QToolButton:pressed{background:#bcbcbc;}"
            )

        self._up_button.clicked.connect(
            self._step_up_without_selection
        )
        self._down_button.clicked.connect(
            self._step_down_without_selection
        )

        self.setToolTip(
            f"▲▼をクリック：{float(step):g}ずつ変更／"
            + (
                "半角整数を直接入力できます。"
                if self.decimals() == 0
                else "半角数値を直接入力できます。"
            )
        )
        self.valueChanged.connect(self._spin_changed)
        slider.valueChanged.connect(self._slider_changed)
        self._slider_changed(slider.value())

    def _clear_number_selection(self):
        editor = self.lineEdit()
        if editor is None:
            return
        editor.deselect()
        editor.setCursorPosition(len(editor.text()))

        # ▲▼ボタンにフォーカスを残さず、編集欄の青い選択も解除する。
        self._up_button.clearFocus()
        self._down_button.clearFocus()

    def _step_up_without_selection(self):
        self.stepUp()
        QTimer.singleShot(0, self._clear_number_selection)

    def _step_down_without_selection(self):
        self.stepDown()
        QTimer.singleShot(0, self._clear_number_selection)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        button_width = 9
        half_height = max(8, self.height() // 2)
        x = self.width() - button_width - 1
        self._up_button.setGeometry(
            x, 1, button_width, half_height - 1
        )
        self._down_button.setGeometry(
            x,
            half_height,
            button_width,
            self.height() - half_height - 1,
        )
        self._up_button.raise_()
        self._down_button.raise_()

    def validate(self, input_text, position):
        """全角文字・英字・記号を拒否し、ASCII数値だけ許可する。"""
        text = str(input_text)
        if text == "":
            return (
                QValidator.State.Intermediate,
                text,
                position,
            )

        allowed_characters = (
            "0123456789."
            if self.decimals() > 0
            else "0123456789"
        )
        if any(
            character not in allowed_characters
            for character in text
        ):
            return (
                QValidator.State.Invalid,
                text,
                position,
            )
        if text.count(".") > 1:
            return (
                QValidator.State.Invalid,
                text,
                position,
            )

        if "." in text:
            integer_part, fraction_part = text.split(".", 1)
            if len(fraction_part) > self.decimals():
                return (
                    QValidator.State.Invalid,
                    text,
                    position,
                )
            if integer_part == "":
                return (
                    QValidator.State.Intermediate,
                    text,
                    position,
                )

        try:
            value = float(text)
        except ValueError:
            return (
                QValidator.State.Intermediate,
                text,
                position,
            )

        if value < self.minimum() or value > self.maximum():
            return (
                QValidator.State.Invalid,
                text,
                position,
            )

        state = (
            QValidator.State.Intermediate
            if text.endswith(".")
            else QValidator.State.Acceptable
        )
        return (state, text, position)

    def valueFromText(self, text):
        try:
            return float(str(text))
        except ValueError:
            return float(self.value())

    def textFromValue(self, value):
        if self.decimals() > 0:
            return f"{float(value):.1f}"
        return str(int(round(float(value))))

    def _spin_changed(self, value):
        slider_value = int(round(float(value) * self.scale))
        slider_value = max(
            self.slider.minimum(),
            min(self.slider.maximum(), slider_value),
        )
        if self.slider.value() != slider_value:
            self.slider.setValue(slider_value)

    def _slider_changed(self, value):
        self.blockSignals(True)
        self.setValue(float(value) / self.scale)
        self.blockSignals(False)

    def setDisplayValue(self, value):
        self.blockSignals(True)
        self.setValue(float(value))
        self.blockSignals(False)

    def wheelEvent(self, event):
        event.ignore()



class ClickableValueLabel(QLabel):
    """数値表示をクリックした時だけ設定ポップアップを開くラベル。"""

    clicked = Signal(QPoint)

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class LineTaperCurvePopup(QDialog):
    """ラインの入り／抜きカーブをその場で調整するポップアップ。"""

    curveChanged = Signal(int)

    def __init__(self, title, value, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumWidth(260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        layout.addWidget(QLabel(f"<b>{title}</b>"))
        self.value_label = QLabel()
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.value_label)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(20, 400)
        self.slider.setValue(max(20, min(400, int(value))))
        self.slider.valueChanged.connect(self._value_changed)
        layout.addWidget(self.slider)
        note = QLabel("小さいほど緩やか、大きいほど先端付近で急に変化します。")
        note.setWordWrap(True)
        note.setStyleSheet("font-size:10px;color:#666;")
        layout.addWidget(note)
        self._value_changed(self.slider.value())

    def _value_changed(self, value):
        self.value_label.setText(f"{value / 100:.2f}")
        self.curveChanged.emit(int(value))


class TweenCommandPopup(QDialog):
    """自由変形／メッシュ変形トゥイーン中の操作パネル。"""

    commitRequested = Signal()
    cancelRequested = Signal()
    rotateLeftRequested = Signal()
    rotateRightRequested = Signal()
    reverseChanged = Signal(bool)

    def __init__(
        self,
        mode="free",
        mesh_cols=4,
        mesh_rows=4,
        parent=None,
        reverse=False,
    ):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint,
        )
        self.mode = "mesh" if mode == "mesh" else "free"
        mode_name = "メッシュ変形" if self.mode == "mesh" else "自由変形"
        self.setWindowTitle(f"長方形選択：{mode_name}トゥイーン")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setModal(False)
        self.setMinimumWidth(260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        if self.mode == "mesh":
            description = (
                f"<b>メッシュ変形トゥイーン</b><br>"
                f"横{int(mesh_cols)}×縦{int(mesh_rows)}の格子点を操作して、"
                "最後のコマの形を指定してください。"
            )
        else:
            description = (
                "<b>自由変形トゥイーン</b><br>"
                "白い点や枠を操作して、最後のコマの形を指定してください。"
            )
        label = QLabel(description)
        label.setWordWrap(True)
        layout.addWidget(label)

        self.reverse_generation = QCheckBox(
            "逆生成（◆ーーーー│）"
        )
        self.reverse_generation.setChecked(
            bool(reverse)
        )
        self.reverse_generation.setToolTip(
            "ON：操作中の変形形状をキーフレーム側へ置き、"
            "元の初期形状をラストコマ側へ置きます。"
        )
        layout.addWidget(self.reverse_generation)

        self.direction_note = QLabel()
        self.direction_note.setWordWrap(True)
        self.direction_note.setStyleSheet(
            "font-size:10px;color:#555;"
        )
        layout.addWidget(self.direction_note)
        self.reverse_generation.toggled.connect(
            self._sync_direction_note
        )
        self.reverse_generation.toggled.connect(
            self.reverseChanged
        )
        self._sync_direction_note(
            self.reverse_generation.isChecked()
        )

        rotate_row = QHBoxLayout()
        rotate_left = QPushButton("左へ90°")
        rotate_right = QPushButton("右へ90°")
        rotate_left.clicked.connect(self.rotateLeftRequested)
        rotate_right.clicked.connect(self.rotateRightRequested)
        rotate_row.addWidget(rotate_left)
        rotate_row.addWidget(rotate_right)
        layout.addLayout(rotate_row)

        commit = QPushButton(f"{mode_name}を確定してトゥイーン作成")
        cancel = QPushButton("キャンセル")
        commit.clicked.connect(self.commitRequested)
        cancel.clicked.connect(self.cancelRequested)
        layout.addWidget(commit)
        layout.addWidget(cancel)

    def _sync_direction_note(self, reverse):
        if reverse:
            self.direction_note.setText(
                "逆生成：現在操作している変形形状を"
                "キーフレーム側の◆へ配置し、"
                "ラストコマ側を元の初期形状にします。"
            )
        else:
            self.direction_note.setText(
                "通常生成：キーフレーム側を元の初期形状、"
                "ラストコマ側を現在操作している変形形状にします。"
            )


class HSVColorWheel(QWidget):
    colorChanged = Signal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor("black")
        self._cache_key = None
        self._cache_image = QImage()
        self._drag_part = None
        self.hue_value = QSpinBox(self)
        self.hue_value.setRange(0, 359)
        self.hue_value.setFixedWidth(54)
        self.hue_value.valueChanged.connect(self._hue_value_changed)
        self.setMinimumSize(160, 180)
        self.setMaximumHeight(220)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setToolTip(
            "上のバーで色相、下の四角で彩度と明度を選択します。"
        )

    def setColor(self, color):
        color = QColor(color)
        if not color.isValid():
            return
        self._color = color
        hue = max(0, color.hsvHue())
        self.hue_value.blockSignals(True)
        self.hue_value.setValue(hue)
        self.hue_value.blockSignals(False)
        self.update()

    def _wheel_geometry(self):
        hue_bar = QRectF(
            20.0,
            7.0,
            max(24.0, self.width() - 82.0),
            18.0,
        )
        available_width = max(1.0, self.width() - 36.0)
        available_height = max(1.0, self.height() - 40.0)
        square_side = min(available_width, available_height)
        square = QRectF(
            30.0,
            35.0,
            square_side,
            square_side,
        )
        return hue_bar, square

    def resizeEvent(self, event):
        self.hue_value.setGeometry(
            max(0, self.width() - 56),
            3,
            54,
            26,
        )
        self._cache_key = None
        super().resizeEvent(event)

    def _wheel_image(self):
        hue_bar, square = self._wheel_geometry()
        width, height = self.width(), self.height()
        hue = max(0, self._color.hsvHue())
        cache_key = (width, height, hue)
        if self._cache_key == cache_key:
            return self._cache_image
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        for y in range(height):
            for x in range(width):
                if hue_bar.contains(x + 0.5, y + 0.5):
                    bar_hue = int(round(
                        359.0
                        * (x + 0.5 - hue_bar.left())
                        / hue_bar.width()
                    ))
                    image.setPixelColor(
                        x, y, QColor.fromHsv(bar_hue, 255, 255)
                    )
                elif square.contains(x + 0.5, y + 0.5):
                    saturation = int(round(
                        255.0 * (x + 0.5 - square.left()) / square.width()
                    ))
                    value = int(round(
                        255.0 * (square.bottom() - (y + 0.5)) / square.height()
                    ))
                    image.setPixelColor(
                        x,
                        y,
                        QColor.fromHsv(
                            hue,
                            max(0, min(255, saturation)),
                            max(0, min(255, value)),
                        ),
                    )
        self._cache_key = cache_key
        self._cache_image = image
        return image

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.drawImage(0, 0, self._wheel_image())
        hue_bar, square = self._wheel_geometry()
        hue = max(0, self._color.hsvHue())
        hue_x = (
            hue_bar.left()
            + hue_bar.width() * hue / 359.0
        )
        square_point = QPointF(
            square.left()
            + square.width() * self._color.hsvSaturation() / 255.0,
            square.bottom()
            - square.height() * self._color.value() / 255.0,
        )
        painter.setPen(QPen(QColor("#555555"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(hue_bar.adjusted(-1, -1, 1, 1))
        painter.drawRect(square.adjusted(-1, -1, 1, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("white"), 2))
        painter.drawRect(QRectF(
            hue_x - 3.0,
            hue_bar.top() - 2.0,
            6.0,
            hue_bar.height() + 4.0,
        ))
        painter.drawEllipse(square_point, 5, 5)
        painter.setPen(QPen(QColor("black"), 1))
        painter.drawRect(QRectF(
            hue_x - 4.0,
            hue_bar.top() - 3.0,
            8.0,
            hue_bar.height() + 6.0,
        ))
        painter.drawEllipse(square_point, 6, 6)
        painter.setPen(QColor("#333333"))
        painter.drawText(
            QRectF(0, 4, 18, 22),
            Qt.AlignmentFlag.AlignCenter,
            "H",
        )

    def _part_at(self, position):
        hue_bar, square = self._wheel_geometry()
        if hue_bar.adjusted(-3, -4, 3, 4).contains(position):
            return "hue"
        if square.contains(position):
            return "sv"
        return None

    def _select_at(self, position, part=None):
        if not self.isEnabled():
            return
        hue_bar, square = self._wheel_geometry()
        part = part or self._part_at(position)
        if part == "hue":
            hue = int(round(
                359.0
                * (position.x() - hue_bar.left())
                / hue_bar.width()
            ))
            hue = max(0, min(359, hue))
            saturation = self._color.hsvSaturation()
            value = self._color.value() or 255
        elif part == "sv":
            hue = max(0, self._color.hsvHue())
            saturation = int(round(
                255.0
                * (position.x() - square.left())
                / square.width()
            ))
            value = int(round(
                255.0
                * (square.bottom() - position.y())
                / square.height()
            ))
            saturation = max(0, min(255, saturation))
            value = max(0, min(255, value))
        else:
            return
        self._color = QColor.fromHsv(hue, saturation, value)
        if part == "hue":
            self._cache_key = None
            self.hue_value.blockSignals(True)
            self.hue_value.setValue(hue)
            self.hue_value.blockSignals(False)
        self.update()
        self.colorChanged.emit(QColor(self._color))

    def _hue_value_changed(self, hue):
        saturation = self._color.hsvSaturation()
        value = self._color.value() or 255
        self._color = QColor.fromHsv(int(hue), saturation, value)
        self._cache_key = None
        self.update()
        self.colorChanged.emit(QColor(self._color))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_part = self._part_at(event.position())
            if self._drag_part is not None:
                self._select_at(event.position(), self._drag_part)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            if self._drag_part is not None:
                self._select_at(event.position(), self._drag_part)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_part = None
        super().mouseReleaseEvent(event)


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

        painter.setPen(QPen(QColor("#bcc8ce"), 1))
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

    COLORS = {
        "key": QColor("#BFE7F4"),
        "hold": QColor("#CDECF6"),
        "sheet_key": QColor("#FFE08A"),
        "sheet_hold": QColor("#FFF1B8"),
        "blank": QColor("#EAF7FB"),
        "uncreated": QColor("#F5F5F5"),
    }
    FOREGROUNDS = {
        "key": QColor("#0D6694"),
        "hold": QColor("#256B88"),
        "sheet_key": QColor("#795300"),
        "sheet_hold": QColor("#80621A"),
        "blank": QColor("#8EB7C7"),
        "uncreated": QColor("#8A8A8A"),
    }

    def paint(self, painter, option, index):
        state_name = index.data(self.STATE_ROLE) or "uncreated"
        painter.save()
        painter.fillRect(option.rect, self.COLORS.get(state_name, self.COLORS["uncreated"]))
        painter.setFont(index.data(Qt.ItemDataRole.FontRole) or option.font)
        painter.setPen(self.FOREGROUNDS.get(state_name, self.FOREGROUNDS["uncreated"]))
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, str(text))
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(QPen(QColor("#FF2B1C"), 2))
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
        add_menu = menu.addMenu("コマを増やす")
        action_add_blank = add_menu.addAction(
            "空フレームを追加"
        )
        action_extend = add_menu.addAction(
            "表示コマを1コマ伸ばす"
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
                recall_menu = menu.addMenu("連番の番号を呼び出す")
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
                    "トゥイーンをキャンセル"
                )
            else:
                key_col = item.data(Qt.ItemDataRole.UserRole)
                exposure = item.data(
                    Qt.ItemDataRole.UserRole + 1
                )
                tween_menu = menu.addMenu(
                    "トゥイーンを有効にする"
                )
                action_free = tween_menu.addAction("自由変形")
                action_mesh = tween_menu.addAction("メッシュ変形")
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
        elif chosen is action_extend:
            self.extendExposureRequested.emit(
                int(index.row()),
                int(key_column),
                int(exposure),
            )
        elif action_cancel is not None and chosen is action_cancel:
            self.tweenCancelRequested.emit()
        elif action_free is not None and chosen is action_free:
            self.tweenRequested.emit(
                int(index.row()),
                int(item.data(Qt.ItemDataRole.UserRole)),
            )
        elif action_mesh is not None and chosen is action_mesh:
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

        self.add_blank = QPushButton("+空")
        self.add_blank.setToolTip(
            "●／○の開始セルでは直後へ同じ長さの○を挿入。"
            "ー部分では選択位置から後半を○へ分割します。"
        )
        self.add_exposure = QPushButton("+コマ")
        self.add_exposure.setToolTip(
            "現在のキーフレーム／空フレームを1コマ伸ばします。"
        )
        self.delete=QPushButton("削除")
        self.delete.setToolTip("現在の表示コマを1コマ削除します。")
        self.time_remap_paste=QPushButton("リマップ")
        self.time_remap_paste.setToolTip(
            "AEまたはToeiDigitalTimeSheetのコピー情報を"
            "タイムシートへ貼り付けます。XDTSはタイムラインへ"
            "ドラッグ＆ドロップできます。"
        )
        self.prev=QPushButton("◀F")
        self.prev.setToolTip("前のフレーム（1）")
        self.next=QPushButton("F▶")
        self.next.setToolTip("次のフレーム（2）")
        self.prev_key=QPushButton("◀K")
        self.prev_key.setToolTip("前のコマ（A）")
        self.next_key=QPushButton("K▶")
        self.next_key.setToolTip("次のコマ（S）")
        self.play=QPushButton("再生")
        self.play.setToolTip("再生／停止")
        self.play.setCheckable(True)

        self.onion_all_layers=QCheckBox("すべてのレイヤー")
        self.onion_all_layers.setChecked(True)
        self.onion=QCheckBox("オニオンスキン")
        self.onion.setToolTip("オニオンスキン表示のON／OFF")
        self.onion_settings=QPushButton("設定")
        self.onion_settings.setCheckable(True)
        self.onion_settings.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.onion_settings.setToolTip(
            "クリックでオニオンスキン設定を開き、"
            "再クリックで閉じます。"
        )
        self.onion_settings.setStyleSheet(
            "QPushButton{padding:1px 5px;font-size:10px;}"
            "QPushButton:checked{background:#d7eef8;"
            "border:1px solid #4a9fc5;}"
        )
        self.duration=QSpinBox(); self.duration.setRange(1,240); self.duration.setSuffix(" コマ")
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
        self.layer_opacity_text = QLabel("不透明")
        self.layer_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.layer_opacity_slider.setRange(0, 100)
        self.layer_opacity_slider.setValue(100)
        self.layer_opacity_slider.setFixedWidth(84)
        self.layer_opacity_slider.setToolTip(
            "選択レイヤーの表示不透明度です。画像の色データ自体は変更しません。"
        )
        # レイヤー不透明度は操作列の一番左に固定する。
        c.insertWidget(0, self.layer_opacity_slider)
        c.insertWidget(0, self.layer_opacity_text)
        self.layer_opacity_value = QLabel("100%")
        self.layer_opacity_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.layer_opacity_value.setFixedWidth(38)
        self.layer_opacity_value.setStyleSheet(
            "font-size:10px;color:#555;padding:0px;"
        )
        self.layer_opacity_value.setToolTip(
            "現在選択しているレイヤーの表示不透明度"
        )

        c.addStretch()
        compact_hint = QLabel("Shift/Ctrl：複数選択")
        compact_hint.setStyleSheet("font-size:9px;color:#666;")
        compact_hint.setToolTip(
            "ドラッグ・Shift＋クリック：複数選択／"
            "選択範囲をそのままドラッグ：まとめて移動／"
            "●・○中央：移動／左右端：伸縮／"
            "Space：ハンド／Ctrl＋Space：拡大縮小"
        )
        c.addWidget(compact_hint)
        c.addWidget(self.fps)
        c.addWidget(self.layer_opacity_value)
        v.addLayout(c)
        self.mode_tabs = QTabBar()
        self.mode_tabs.addTab("シート")
        self.mode_tabs.addTab("連番")
        self.mode_tabs.setCurrentIndex(0)
        self.mode_tabs.setExpanding(False)
        self.mode_tabs.setDrawBase(True)
        self.mode_tabs.setToolTip(
            "連番：左から順番に自動採番／"
            "シート：タイムシートの絵番号を保持"
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
        self.layer_list.setStyleSheet(
            "QListWidget{border:0px;margin:0px;padding:0px;background:#f5f5f5;outline:0;}"
            "QListWidget::item{border:0px;padding:0px;background:#f5f5f5;}"
            "QListWidget::item:selected{background:#d9ecf6;color:#155f83;}"
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
        self.layer_header_spacer.setStyleSheet(
            "background:#f3f3f3;border:0px;margin:0px;padding:0px;"
        )
        layer_header_layout = QHBoxLayout(self.layer_header_spacer)
        layer_header_layout.setContentsMargins(2,0,2,0)
        layer_header_layout.setSpacing(2)
        layer_header_layout.addWidget(QLabel("レイヤー"))
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
        layer_widget.setStyleSheet("border:0px;margin:0px;padding:0px;background:#f5f5f5;")
        layer_widget.setLayout(layer_box)
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
        self.table.setStyleSheet(
            "QTableWidget{border:0px;margin:0px;padding:0px;"
            "gridline-color:#bcc8ce;background:#f5f5f5;}"
            "QHeaderView{border:0px;margin:0px;padding:0px;}"
            "QHeaderView::section{background:#f3f3f3;border:0px;"
            "border-bottom:1px solid #c8c8c8;border-right:1px solid #c8c8c8;"
            "padding:2px;color:#222;}"
            "QTableWidget::item{border:0px;color:#155f83;}"
            "QTableWidget::item:selected{background:transparent;color:#155f83;"
            "border:2px solid #ff2b1c;}"
        )
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
            "選択番号の直後へ、新しい空の番号画像を追加します。"
            if sequence_mode
            else
            "●／○の開始セルでは直後へ同じ長さの○を挿入。"
            "ー部分では選択位置から後半を○へ分割します。"
        )
        self.delete.setToolTip(
            "選択番号を削除し、シート側の対応セルを未使用にします。"
            if sequence_mode
            else "現在の表示コマを1コマ削除します。"
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
        self.mode_tabs.setStyleSheet(
            "QTabBar::tab{background:#E2E2E2;color:#555;"
            "border:1px solid #999;border-bottom:1px solid #777;"
            "padding:4px 18px;min-width:58px;}"
            "QTabBar::tab:selected{"
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
        action_duplicate = menu.addAction("複製")
        action_merge = menu.addAction("結合")
        action_merge.setEnabled(len(rows) >= 2)
        action_delete = menu.addAction("削除")
        action_normalize = None
        if self.timeline_mode == "sheet":
            menu.addSeparator()
            action_normalize = menu.addAction("番号の正規化")
        chosen = menu.exec(self.layer_list.viewport().mapToGlobal(position))
        if chosen is action_duplicate:
            self.duplicateLayersRequested.emit(rows)
        elif chosen is action_merge:
            self.mergeLayersRequested.emit(rows)
        elif chosen is action_delete:
            self.deleteLayersRequested.emit(rows)
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
            item.setToolTip(
                "[●] 表示／[-] 非表示。左端クリックで切替、"
                "ダブルクリックでレイヤー名を変更。"
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
                    for number in sequence_by_number
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
                if self.timeline_mode == "sequence":
                    number = col + 1
                    if number in sequence_by_number:
                        source_layer = frames[sequence_by_number[number]].layers[layer_index]
                        span_kind = (
                            "content" if source_layer.has_content else "sequence_blank"
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
                        text = (
                            "◆"
                            if (
                                is_pending_tween
                                and pending_reverse
                            )
                            else str(content_key_numbers.get(key_col, ""))
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
                if span_kind == "content" and col == key_col:
                    item.setBackground(QColor(
                        "#FFE08A"
                        if self.timeline_mode == "sheet"
                        else "#BFE7F4"
                    ))
                    item.setForeground(QColor(
                        "#795300"
                        if self.timeline_mode == "sheet"
                        else "#0D6694"
                    ))
                    key_font = item.font()
                    key_font.setBold(True)
                    key_font.setPointSize(max(10, key_font.pointSize()))
                    item.setFont(key_font)
                elif span_kind == "content":
                    item.setBackground(QColor(
                        "#FFF1B8"
                        if self.timeline_mode == "sheet"
                        else "#CDECF6"
                    ))
                    item.setForeground(QColor(
                        "#80621A"
                        if self.timeline_mode == "sheet"
                        else "#256B88"
                    ))
                elif span_kind == "blank":
                    item.setBackground(QColor("#EAF7FB"))
                    item.setForeground(QColor("#8EB7C7"))
                    if text == "○":
                        blank_font = item.font()
                        blank_font.setBold(True)
                        blank_font.setPointSize(
                            max(10, blank_font.pointSize())
                        )
                        item.setFont(blank_font)
                else:
                    item.setForeground(QColor("#8A8A8A"))
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
                                "逆生成トゥイーン中。"
                                "この右端は元の初期形状です。"
                                "右クリックでキャンセルできます。"
                            )
                            if pending_reverse
                            else
                            "トゥイーン変形中。"
                            "右クリックでキャンセルできます。"
                        )
                    else:
                        item.setToolTip(
                            (
                                "右端をドラッグして後方向の表示コマ数を変更／"
                                "右クリックでトゥイーンを有効化"
                            )
                            if span_kind == "content"
                            else "空フレームの右端をドラッグして表示コマ数を変更"
                        )
                elif text == "◆":
                    item.setToolTip(
                        "逆生成トゥイーン中。"
                        "キーフレーム側が操作中の変形形状、"
                        "右端側が元の初期形状になります。"
                        "右クリックでキャンセルできます。"
                    )
                elif (
                    span_kind == "content"
                    and col == key_col
                ):
                    item.setToolTip(
                        f"中央をドラッグして移動／左端をドラッグして前方向へ伸縮\n"
                        f"レイヤーセル {col + 1} / "
                        f"{exposure}コマ"
                    )
                elif text == "○":
                    item.setToolTip(
                        "空フレームの先頭です。"
                        "描画すると自動的にキーフレーム化します。"
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


class PaintCanvas(QWidget):
    status_message=Signal(str)
    colorUsed=Signal(QColor)
    viewChanged=Signal(float, float)
    onionInteractionChanged=Signal()
    onionInteractionFinished=Signal()
    changed=Signal(); selectionChanged=Signal(); selectionCleared=Signal(); cellChanged=Signal(int,int); imagesDropped=Signal(object); projectDropped=Signal(str); timeRemapDropped=Signal(str); colorSampled=Signal(QColor)
    def __init__(self):
        super().__init__(); self.setFocusPolicy(Qt.FocusPolicy.StrongFocus); self.setMouseTracking(True); self.setTabletTracking(True); self.setMinimumSize(320,120); self.setAcceptDrops(True)
        self.frames=[make_frame()]; self.current_frame=0; self.active_layer_index=0
        self._playback_active=False
        self._playback_frame_cache={}
        self._playback_cache_limit=14
        self._playback_resolved_keys=[]
        # 連番読込直後の画像を、タイムリマップ再配置用に保持する。
        self._sequence_source_bank=[]
        self._sequence_source_bank_layer_index=-1
        self._sequence_source_bank_layer_name=""
        # シートから外した絵番号を、右クリックで再配置するため保持する。
        self._sequence_archive={}
        self.timeline_mode="sheet"
        self.tool="brush"; self.temp_tool=None; self.main_color=QColor("black"); self.sub_color=QColor(255,0,0); self.color_mode="main"; self.transparent_display_color=QColor("white"); self.background_mask_rgb=(255,255,255); self.mask_color_rgbs=set(); self.mask_color_rgb=None; self.mask_all_enabled=True; self.selected_used_color_rgbs=set(); self.visible_color_rgbs=None
        self.selection_polygon=[]
        self.selection_mask_override=None
        self.selection_outline_polygons=[]
        self.selection_mask_rect=None
        self._selection_fade_opacity=1.0
        self._selection_fade_started=time.monotonic()
        self._selection_blink_timer=QTimer(self)
        self._selection_blink_timer.setInterval(40)
        self._selection_blink_timer.timeout.connect(
            self._update_selection_fade
        )
        self._selection_blink_timer.start()
        self.rect_start=None
        self.rect_end=None
        self.transform_active=False
        self.transform_mode=None
        self.transform_original_layer=None
        self.transform_source=None
        self.transform_source_rect=None
        self.transform_points=[]
        self.transform_handle=-1
        self.transform_drag_kind=None
        self.transform_drag_start=QPointF()
        self.transform_drag_points=[]
        self.transform_original_has_content=False
        self.transform_frame_index=0
        self.transform_layer_index=0
        self.transform_apply_all_frames=False
        self.tween_pending = None
        self.transform_quality=False
        self.transform_quality_active=False
        self.transform_line_threshold=96
        self.transform_tp_line_colors=()
        # TP_mask v0.7 compatible data.  The selected image is converted to
        # one monochrome mask per exact color, then every mask is transformed
        # independently and recombined without interpolation colors.
        self.transform_tp_palette=[]
        self.transform_tp_masks=[]
        self.transform_tp_line_masks=[]
        self.transform_tp_prepared_preview=None
        self.transform_tp_fill_smoothing=0.55
        self.transform_tp_line_smoothing=0.85
        self._tp_geometry_cache_key=None
        self._tp_geometry_cache_bbox=None
        self._tp_geometry_cache_fill_overlay=None
        self._tp_geometry_cache_line_soft=[]
        self._tp_preview_cache_key=None
        self._tp_preview_cache_image=None
        self._tp_mask_source_key=None
        self._tp_proxy_source_key=None
        self._tp_proxy_source_image=None
        self._tp_proxy_rendering=False
        # V62: quality preview is generated outside paintEvent.  While the
        # per-color TP masks are transformed, a counter popup reports progress.
        self._tp_preview_progress_busy=False
        self._tp_preview_progress_scheduled=False
        self._transform_line_adjusting=False
        self._transform_line_preview_timer=QTimer(self)
        self._transform_line_preview_timer.setSingleShot(True)
        self._transform_line_preview_timer.setInterval(120)
        self._transform_line_preview_timer.timeout.connect(
            self._render_deferred_transform_line_preview
        )
        self.transform_mesh_cols=4
        self.transform_mesh_rows=4
        self.transform_mesh_grid=4  # legacy compatibility
        self.transform_mesh_reference_points=[]
        self.pen_size=8; self.pen_opacity=1.0
        self.brush_stabilizer_strength = 0
        self._stabilized_canvas = None
        self._last_raw_canvas = None
        self._brush_stabilizer_history = []
        self._last_brush_pressure = 1.0
        self._pressure_input_history = []
        self._brush_follow_settle = 0.0
        self._brush_follow_timer = QTimer(self)
        self._brush_follow_timer.setInterval(16)
        self._brush_follow_timer.timeout.connect(
            self._advance_stabilized_brush
        )
        self.pressure_enabled=True; self.pressure_min=.1; self.pressure_max=1.0; self.pressure_curve=1.0; self.pressure_curve_points=[[0.0,0.0],[0.5,0.5],[1.0,1.0]]
        self.zoom=1.0; self.pan=QPointF(); self.rotation=0.0; self.flip_horizontal=False; self.onion_skin=False
        self.onion_previous_count=1
        self.onion_next_count=1
        self.onion_previous_opacity=0.22
        self.onion_next_opacity=0.22
        self.onion_previous_color=QColor(255, 92, 92)
        self.onion_next_color=QColor(92, 160, 255)
        self.onion_previous_color_enabled=False
        self.onion_next_color_enabled=False
        self.onion_selected_colors_only=False
        self.onion_previous_shift_x=0.0
        self.onion_previous_shift_y=0.0
        self.onion_previous_rotation=0.0
        self.onion_previous_scale=100.0
        self.onion_next_shift_x=0.0
        self.onion_next_shift_y=0.0
        self.onion_next_rotation=0.0
        self.onion_next_scale=100.0
        # TU/TB用のカメラ倍率。表示用デジタルズームself.zoomとは分離。
        self.onion_tu_tb_scale=1.0
        self.onion_previous_levels=[100]
        self.onion_next_levels=[100]
        self.onion_center_percent=50.0
        self._onion_interaction_mode=None
        self._onion_interaction_direction=-1
        self._onion_interaction_axis=None
        self._onion_interaction_operation=None
        self._onion_interaction_dragging=False
        self._onion_interaction_start_widget=QPointF()
        self._onion_interaction_start_pan=QPointF()
        self._onion_interaction_start_view_rotation=0.0
        self._onion_interaction_start_previous=(0.0, 0.0)
        self._onion_interaction_start_next=(0.0, 0.0)
        self._onion_interaction_start_previous_rotation=0.0
        self._onion_interaction_start_next_rotation=0.0
        self._onion_interaction_start_previous_scale=100.0
        self._onion_interaction_start_next_scale=100.0
        self._onion_interaction_start_tu_tb_scale=1.0
        self.onion_all_layers=True
        self._color_filter_cache = {}
        self._pseudo_transparency_cache = {}
        # 画像ごとのRGBインデックスを保持し、表示チェックのたびの再計算を避ける。
        self._color_index_cache = {}
        self.silhouette_non_background = False
        self._silhouette_cache = {}
        self._onion_cache = {}
        self.drawing=False; self.last_canvas=QPointF(); self.last_widget=QPointF(); self.lasso=[]; self.middle_hand=False
        self._brush_cursor_widget_pos = QPointF(-1000, -1000)
        self._brush_cursor_inside = False
        # ブラシ確定時にタイムライン全体を作り直さないための状態。
        self._brush_started_with_content = True
        self._brush_blend_base_image = None
        self._brush_blended_colors = set()
        self._brush_stroke_opacity = 1.0
        self._cell_structure_dirty = True
        # ライン／図形ツールのプレビュー状態
        self.line_start=None
        self.line_end=None
        self.line_control=None
        self.line_curve_stage=0  # 0:待機、1:基準線ドラッグ、2:曲率指定
        self.shape_start=None
        self.shape_end=None
        self.undo_stack=[]; self.redo_stack=[]; self.stroke_before=None
        self.mesh_points=[]; self.mesh_original=None; self.mesh_active=-1; self.mesh_grid=4
        self.checker_light=QColor(255,255,255); self.checker_dark=QColor(255,255,255)

        self.selection_clear_overlay = QPushButton("選択解除", self)
        self.selection_clear_overlay.setToolTip("現在の選択範囲を解除します。")
        self.selection_clear_overlay.setFixedHeight(26)
        self.selection_clear_overlay.setStyleSheet(
            "QPushButton{background:rgba(35,35,35,215);color:white;"
            "border:1px solid white;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:rgba(70,70,70,235);}"
        )
        self.selection_clear_overlay.setFocusPolicy(
            Qt.FocusPolicy.NoFocus
        )
        self.selection_clear_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            False,
        )
        self.selection_clear_overlay.clicked.connect(
            self.clear_selection
        )
        self.selection_clear_overlay.hide()
        self.selectionChanged.connect(
            self._update_selection_clear_overlay
        )
    def _position_selection_clear_overlay(self):
        button = getattr(self, "selection_clear_overlay", None)
        if button is None:
            return
        button.adjustSize()
        margin = 10
        button.move(
            max(margin, self.width() - button.width() - margin),
            margin,
        )
        button.raise_()

    def _update_selection_clear_overlay(self):
        """選択中は、使用ツールに関係なく右上の解除を有効化する。"""
        button = getattr(
            self,
            "selection_clear_overlay",
            None,
        )
        if button is None:
            return
        visible = bool(self.selection_polygon)
        button.setEnabled(True)
        button.setVisible(visible)
        if visible:
            self._position_selection_clear_overlay()
            button.raise_()

    def resizeEvent(self, event):
        self._position_selection_clear_overlay()
        super().resizeEvent(event)

    @property
    def layers(self): return self.frames[self.current_frame].layers
    @property
    def active_layer(self): return self.layers[self.active_layer_index]
    def document_snapshot(self): return ([f.clone() for f in self.frames],self.current_frame,self.active_layer_index,CANVAS_WIDTH,CANVAS_HEIGHT)
    def push_doc_undo(self): self.undo_stack.append(("doc",self.document_snapshot())); self.undo_stack=self.undo_stack[-MAX_UNDO:]; self.redo_stack.clear()
    def push_layer_undo(self):
        l=self.active_layer; self.undo_stack.append(("layer",self.current_frame,self.active_layer_index,l.image.copy(),l.has_content)); self.undo_stack=self.undo_stack[-MAX_UNDO:]; self.redo_stack.clear()
    def current_state_for(self,entry):
        if entry[0]=="doc":
            return ("doc",self.document_snapshot())
        if entry[0]=="layer_batch":
            _, li, cells = entry
            current = []
            for fi, _, _ in cells:
                if 0 <= fi < len(self.frames) and 0 <= li < len(self.frames[fi].layers):
                    layer = self.frames[fi].layers[li]
                    current.append((fi, layer.image.copy(), layer.has_content))
            return ("layer_batch", li, current) if current else None
        if entry[0] == "layer_remove":
            _, index, _target_active = entry
            stored = []
            for frame in self.frames:
                if 0 <= index < len(frame.layers):
                    stored.append(frame.layers[index].clone())
            return (
                "layer_insert",
                int(index),
                stored,
                int(self.active_layer_index),
            )
        if entry[0] == "layer_insert":
            _, index, _layers, _target_active = entry
            return (
                "layer_remove",
                int(index),
                int(self.active_layer_index),
            )
        if entry[0] == "tween_batch":
            _, li, _restore_count, start, end, _cells = entry
            current = []
            for fi in range(int(start), min(int(end) + 1, len(self.frames))):
                if 0 <= li < len(self.frames[fi].layers):
                    layer = self.frames[fi].layers[li]
                    current.append((
                        fi,
                        layer.image.copy(),
                        bool(layer.has_content),
                        int(layer.exposure),
                    ))
            return (
                "tween_batch",
                int(li),
                len(self.frames),
                int(start),
                int(end),
                current,
            )
        _,fi,li,_,_=entry
        if not (
            0 <= int(fi) < len(self.frames)
            and 0 <= int(li) < len(self.frames[int(fi)].layers)
        ):
            return None
        l=self.frames[fi].layers[li]
        return ("layer",fi,li,l.image.copy(),l.has_content)
    def apply_undo_entry(self,e):
        global CANVAS_WIDTH,CANVAS_HEIGHT
        if e[0]=="doc":
            _,snap=e
            fs,cf,al,w,h=snap
            CANVAS_WIDTH=w
            CANVAS_HEIGHT=h
            self.frames=[f.clone() for f in fs]
            self.current_frame=cf
            self.active_layer_index=al
            self.coalesce_numbered_images()
            self.changed.emit()
        elif e[0]=="layer_batch":
            _, li, cells = e
            self.active_layer_index = li
            for fi, img, hc in cells:
                if 0 <= fi < len(self.frames) and 0 <= li < len(self.frames[fi].layers):
                    self.frames[fi].layers[li].image = img.copy()
                    self.frames[fi].layers[li].has_content = hc
                    self.cellChanged.emit(fi, li)
            self.selectionChanged.emit()
        elif e[0] == "layer_remove":
            _, index, target_active = e
            for frame in self.frames:
                if 0 <= index < len(frame.layers) and len(frame.layers) > 1:
                    frame.layers.pop(index)
            self.active_layer_index = max(
                0,
                min(int(target_active), len(self.layers) - 1),
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        elif e[0] == "layer_insert":
            _, index, stored_layers, target_active = e
            for frame_index, frame in enumerate(self.frames):
                if frame_index < len(stored_layers):
                    layer = stored_layers[frame_index].clone()
                else:
                    layer = Layer(
                        f"Layer {index + 1}",
                        blank_image(),
                        visible=True,
                        opacity=1.0,
                    )
                frame.layers.insert(
                    max(0, min(int(index), len(frame.layers))),
                    layer,
                )
            self.active_layer_index = max(
                0,
                min(int(target_active), len(self.layers) - 1),
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        elif e[0] == "tween_batch":
            _, li, restore_count, start, end, cells = e
            restore_count = max(1, int(restore_count))
            if len(self.frames) < restore_count:
                self._ensure_frame_count(restore_count)
            for fi, image, has_content, exposure in cells:
                if (
                    0 <= fi < len(self.frames)
                    and 0 <= li < len(self.frames[fi].layers)
                ):
                    layer = self.frames[fi].layers[li]
                    layer.image = image.copy()
                    layer.has_content = bool(has_content)
                    layer.exposure = max(1, int(exposure))
            if len(self.frames) > restore_count:
                del self.frames[restore_count:]
            self.current_frame = max(
                0, min(int(start), len(self.frames) - 1)
            )
            self.active_layer_index = max(
                0, min(int(li), len(self.layers) - 1)
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        else:
            _,fi,li,img,hc=e
            self.current_frame=fi
            self.active_layer_index=li
            self.frames[fi].layers[li].image=img.copy()
            self.frames[fi].layers[li].has_content=hc
            self.cellChanged.emit(fi,li)
            self.selectionChanged.emit()
        self.update()
    def undo(self):
        while self.undo_stack:
            entry = self.undo_stack.pop()
            current = self.current_state_for(entry)
            if current is None:
                # タイムラインの削除・正規化後に残った、既に存在しない
                # セルの古い履歴は安全に破棄する。
                continue
            self.redo_stack.append(current)
            self.apply_undo_entry(entry)
            return
    def redo(self):
        while self.redo_stack:
            entry = self.redo_stack.pop()
            current = self.current_state_for(entry)
            if current is None:
                continue
            self.undo_stack.append(current)
            self.apply_undo_entry(entry)
            return
    def set_layer_visibility(self, li, on):
        if li < 0:
            return
        changed = False
        for f in self.frames:
            if li < len(f.layers):
                f.layers[li].visible = bool(on)
                changed = True
        if changed:
            self._onion_cache.clear()
            self.update()

    def set_layer_opacity(self, li, opacity):
        """表示用のレイヤー不透明度。画像内のRGBA値は変更しない。"""
        if li < 0:
            return
        opacity = max(0.0, min(1.0, float(opacity)))
        changed = False
        for frame in self.frames:
            if li < len(frame.layers):
                frame.layers[li].opacity = opacity
                changed = True
        if changed:
            self._onion_cache.clear()
            self.update()

    def add_layer(self):
        # 全フレーム・全画像の文書スナップショットを作らず、
        # 追加レイヤーだけをUndo対象にして大量コマ時の待ち時間を抑える。
        old_active = int(self.active_layer_index)
        insert_index = len(self.layers)
        name = f"Layer {insert_index + 1}"
        self.undo_stack.append((
            "layer_remove",
            insert_index,
            old_active,
        ))
        self.undo_stack = self.undo_stack[-MAX_UNDO:]
        self.redo_stack.clear()

        # QImageの暗黙共有を使い、空画像バッファをコマ数分確保しない。
        shared_blank = blank_image()
        for frame in self.frames:
            frame.layers.append(
                Layer(
                    name,
                    shared_blank.copy(),
                    visible=True,
                    opacity=1.0,
                )
            )
        self.active_layer_index = insert_index
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
    def delete_layer(self):
        if len(self.layers)<=1:return
        self.push_doc_undo();
        for f in self.frames:f.layers.pop(self.active_layer_index)
        self.active_layer_index=max(0,self.active_layer_index-1); self.changed.emit(); self.update()
    @staticmethod
    def _clear_timeline_layer_cell(layer):
        layer.image = blank_image()
        layer.has_content = False
        layer.is_blank_key = False
        layer.exposure = 1
        layer.sequence_number = None
        layer.sequence_only = False

    def timeline_block_at(self, column, layer_index):
        if not self.frames:
            return None
        column = int(column)
        layer_index = int(layer_index)
        if not (0 <= column < len(self.frames)):
            return None
        kind, start, exposure = TimelineWidget.timeline_span_at(
            self.frames,
            layer_index,
            column,
        )
        if kind not in ("content", "blank") or start is None:
            return None
        return kind, int(start), max(1, int(exposure))

    def _shift_timeline_layer_right(
        self,
        layer_index,
        start_column,
        amount,
    ):
        """指定レイヤーの明示コマを右へ移動し、時間の隙間を作る。"""
        if not self.frames:
            return

        layer_index = int(layer_index)
        start_column = max(0, int(start_column))
        amount = max(1, int(amount))
        if not (
            0 <= layer_index
            < len(self.frames[0].layers)
        ):
            return

        explicit_cells = []
        required_count = start_column + amount
        for column in range(
            start_column,
            len(self.frames),
        ):
            if (
                layer_index
                >= len(self.frames[column].layers)
            ):
                continue
            layer = self.frames[column].layers[
                layer_index
            ]
            if (
                layer.has_content
                or getattr(
                    layer,
                    "is_blank_key",
                    False,
                )
            ):
                copied = layer.clone()
                explicit_cells.append(
                    (column, copied)
                )
                required_count = max(
                    required_count,
                    column
                    + amount
                    + max(1, int(copied.exposure)),
                )

        self._ensure_frame_count(required_count)

        # 先に元の明示コマをすべて未使用へ戻してから配置する。
        # これにより、移動元と移動先が重なっても内容を失わない。
        for column, _copied in explicit_cells:
            self._clear_timeline_layer_cell(
                self.frames[column].layers[
                    layer_index
                ]
            )

        for column, copied in reversed(
            explicit_cells
        ):
            self.frames[
                column + amount
            ].layers[layer_index] = copied

    def _shift_timeline_layer_left(
        self,
        layer_index,
        start_column,
        amount=1,
    ):
        """指定位置以降の明示コマを左へ移動し、削除した時間を詰める。"""
        if not self.frames:
            return

        layer_index = int(layer_index)
        start_column = max(0, int(start_column))
        amount = max(1, int(amount))
        if not (
            0 <= layer_index
            < len(self.frames[0].layers)
        ):
            return

        explicit_cells = []
        for column in range(start_column, len(self.frames)):
            layer = self.frames[column].layers[layer_index]
            if (
                layer.has_content
                or getattr(layer, "is_blank_key", False)
            ):
                explicit_cells.append((column, layer.clone()))

        for column, _copied in explicit_cells:
            self._clear_timeline_layer_cell(
                self.frames[column].layers[layer_index]
            )

        for column, copied in explicit_cells:
            target_column = column - amount
            if target_column >= 0:
                self.frames[target_column].layers[layer_index] = copied

    def _trim_unused_trailing_frames(self):
        """全レイヤーで未使用になった末尾の時間列を取り除く。"""
        while len(self.frames) > 1:
            last_column = len(self.frames) - 1
            if any(
                self.timeline_block_at(last_column, layer_index)
                is not None
                for layer_index in range(len(self.frames[0].layers))
            ):
                break
            self.frames.pop()

    def create_blank_key(
        self,
        column=None,
        layer_index=None,
    ):
        """○を作成する。

        開始セル（●／○）を選択した場合：
            元のコマを残し、露出末尾の次へ同じ長さの○を挿入する。

        露出途中（ー／│）を選択した場合：
            全体の長さを変えず、選択位置から後半を○へ分割する。
        """
        if column is None:
            column = self.current_frame
        if layer_index is None:
            layer_index = self.active_layer_index

        column = max(0, int(column))
        layer_index = int(layer_index)
        if not self.frames:
            return False
        if not (
            0 <= layer_index
            < len(self.frames[0].layers)
        ):
            return False

        self.push_doc_undo()
        self._ensure_frame_count(column + 1)

        block = self.timeline_block_at(
            column,
            layer_index,
        )
        blank_exposure = 1

        if block is not None:
            _kind, start, exposure = block
            start = int(start)
            exposure = max(1, int(exposure))
            source = self.frames[
                start
            ].layers[layer_index]
            template = source.clone()

            if column == start:
                # ●／○の開始セルを選択：
                # 元のコマを保持し、❘の次へ同じ長さの○を挿入する。
                insertion_column = start + exposure
                blank_exposure = exposure
                self._shift_timeline_layer_right(
                    layer_index,
                    insertion_column,
                    blank_exposure,
                )
                self._ensure_frame_count(
                    insertion_column + blank_exposure
                )
                column = insertion_column
            else:
                # ー／│を選択：
                # 露出全体の長さは変えず、選択位置で前後に分割する。
                #
                # 例：
                # ●ーーーーー│
                #       ↓
                # ●ーー○ーー│
                split_offset = max(
                    1,
                    column - start,
                )
                split_offset = min(
                    split_offset,
                    exposure - 1,
                )
                blank_exposure = max(
                    1,
                    exposure - split_offset,
                )
                source.exposure = max(
                    1,
                    split_offset,
                )
                column = start + split_offset
                self._ensure_frame_count(
                    column + blank_exposure
                )
        else:
            # 未使用セル上では、その位置へ○を作る。
            # 直前の明示コマがある場合は、○の直前まで露出を伸ばす。
            self._ensure_frame_count(column + 1)
            target = self.frames[
                column
            ].layers[layer_index]
            template = target.clone()

            previous_start = None
            for candidate in range(
                column - 1,
                -1,
                -1,
            ):
                layer = self.frames[
                    candidate
                ].layers[layer_index]
                if (
                    layer.has_content
                    or getattr(
                        layer,
                        "is_blank_key",
                        False,
                    )
                ):
                    previous_start = candidate
                    template = layer.clone()
                    break

            if previous_start is not None:
                previous = self.frames[
                    previous_start
                ].layers[layer_index]
                previous.exposure = max(
                    1,
                    column - previous_start,
                )

        target = self.frames[
            column
        ].layers[layer_index]
        target.image = blank_image()
        target.visible = bool(template.visible)
        target.opacity = float(template.opacity)
        target.alpha_locked = bool(
            template.alpha_locked
        )
        target.color_filter_enabled = bool(
            template.color_filter_enabled
        )
        target.color_filter_rgb = (
            tuple(template.color_filter_rgb)
            if template.color_filter_rgb
            is not None
            else None
        )
        target.has_content = False
        target.is_blank_key = True
        target.exposure = blank_exposure

        self.current_frame = column
        self.active_layer_index = layer_index
        self._cell_structure_dirty = True
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def add_frame(self, dup):
        # 互換用。空追加は選択中コマの直後へ○を挿入する。
        if not dup:
            return self.create_blank_key(
                self.current_frame,
                self.active_layer_index,
            )

        self.push_doc_undo()
        source_frame = self.frames[self.current_frame]
        new_frame = source_frame.clone()
        at = self.current_frame + 1
        self.frames.insert(at, new_frame)
        self.current_frame = at
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def delete_frame(self):
        """選択レイヤーの表示コマを1つ削除する。"""
        if not self.frames:
            return
        layer_index = int(self.active_layer_index)
        current = int(self.current_frame)
        block = self.timeline_block_at(current, layer_index)

        self.push_doc_undo()
        if block is not None:
            _kind, start, exposure = block
            layer = self.frames[start].layers[layer_index]
            if layer.has_content and layer.sequence_number is not None:
                self._sequence_archive[
                    (layer_index, int(layer.sequence_number))
                ] = layer.clone()
            if exposure > 1:
                layer.exposure = exposure - 1
                self._shift_timeline_layer_left(
                    layer_index,
                    start + exposure,
                )
            else:
                self._clear_timeline_layer_cell(layer)
                self._shift_timeline_layer_left(
                    layer_index,
                    start + 1,
                )
            self.current_frame = min(current, len(self.frames) - 1)
        elif len(self.frames) > 1:
            # ほかのレイヤーの時間列は動かさず、選択レイヤーだけを詰める。
            self._shift_timeline_layer_left(
                layer_index,
                current + 1,
            )
            self.current_frame = current
        else:
            if self.undo_stack:
                self.undo_stack.pop()
            return

        self._trim_unused_trailing_frames()
        self.current_frame = min(self.current_frame, len(self.frames) - 1)

        self._cell_structure_dirty = True
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()

    def previous_frame(self):
        if not self.frames:
            return
        self.current_frame = max(0, self.current_frame - 1)
        self.selectionChanged.emit()
        self.update()

    def next_frame(self):
        if not self.frames:
            return
        next_column = self.current_frame + 1
        old_count = len(self.frames)
        self._ensure_frame_count(next_column + 1)
        self.current_frame = next_column
        # 追加されたセルは未使用のまま。○の空キーフレームにはしない。
        if len(self.frames) != old_count:
            self._cell_structure_dirty = True
            self.changed.emit()
        self.selectionChanged.emit()
        self.update()

    def timeline_step_columns(self, layer_index=None):
        """このレイヤーで有効なコマ開始位置一覧を返す。

        内容キーフレーム（●）だけでなく、空フレーム（○）も
        1つのコマ開始位置として扱う。
        """
        if not self.frames:
            return []
        if layer_index is None:
            layer_index = self.active_layer_index
        layer_index = int(layer_index)

        columns = []
        for column in range(len(self.frames)):
            try:
                kind, start, _exposure = TimelineWidget.timeline_span_at(
                    self.frames,
                    layer_index,
                    column,
                )
            except Exception:
                continue
            if kind in ("content", "blank") and start == column:
                columns.append(column)
        return columns

    def previous_key_frame(self):
        layer_index = self.active_layer_index
        keys = self.timeline_step_columns(layer_index)
        previous = [i for i in keys if i < self.current_frame]
        if previous:
            self.current_frame = previous[-1]
        elif keys:
            self.current_frame = keys[-1]
        self.selectionChanged.emit()
        self.update()

    def next_key_frame(self):
        layer_index = self.active_layer_index
        keys = self.timeline_step_columns(layer_index)
        following = [i for i in keys if i > self.current_frame]
        if following:
            self.current_frame = following[0]
        elif keys:
            self.current_frame = keys[0]
        self.selectionChanged.emit()
        self.update()

    def set_duration(self, d):
        layer = self.active_layer
        if not layer.has_content:
            source = self.resolve_key_frame(self.current_frame, self.active_layer_index)
            if source is not None:
                self.frames[source].layers[self.active_layer_index].exposure = max(1, int(d))
            else:
                layer.exposure = max(1, int(d))
        else:
            layer.exposure = max(1, int(d))
        self.changed.emit()
    def select_exposure(self, column, visual_row):
        self.current_frame = max(0, min(int(column), len(self.frames) - 1))
        layer_count = len(self.layers)
        if layer_count:
            visual_row = max(0, min(int(visual_row), layer_count - 1))
            self.active_layer_index = layer_count - 1 - visual_row
        self.selectionChanged.emit()
        self.update()

    def current_exposure(self):
        return self.current_frame

    def resolve_key_frame(self, column, layer_index):
        if not self.frames:
            return None
        column = max(0, min(int(column), len(self.frames) - 1))
        for key_col in range(column, -1, -1):
            if layer_index >= len(self.frames[key_col].layers):
                continue
            layer = self.frames[key_col].layers[layer_index]
            if layer.has_content:
                return (
                    key_col
                    if column < key_col + max(1, layer.exposure)
                    else None
                )
            if getattr(layer, "is_blank_key", False):
                return None
        return None

    def resolve_exposure_block(self, column, layer_index):
        """●ーーーー｜を1つのキーフレーム露出ブロックとして返す。"""
        if not self.frames:
            return None
        column = max(0, min(int(column), len(self.frames) - 1))
        layer_index = int(layer_index)
        key_column = self.resolve_key_frame(column, layer_index)
        if key_column is None:
            return None
        if not (
            0 <= key_column < len(self.frames)
            and 0 <= layer_index
            < len(self.frames[key_column].layers)
        ):
            return None
        key_layer = self.frames[key_column].layers[layer_index]
        exposure = max(1, int(key_layer.exposure))
        end_column = min(
            len(self.frames) - 1,
            key_column + exposure - 1,
        )
        return (
            int(key_column),
            int(end_column),
            int(exposure),
        )

    def normalize_sequence_numbers(self, layer_index=None):
        """シートの登場順で絵番号を正規化し、連番にも反映する。"""
        if not self.frames:
            return
        if layer_index is None:
            layer_indices = range(len(self.frames[0].layers))
        else:
            layer_indices = (int(layer_index),)
        for target_layer_index in layer_indices:
            sheet_numbers = []
            for frame in self.frames:
                layer = frame.layers[target_layer_index]
                if (
                    layer.has_content
                    and not layer.sequence_only
                    and layer.sequence_number is not None
                    and int(layer.sequence_number) not in sheet_numbers
                ):
                    sheet_numbers.append(int(layer.sequence_number))
            all_numbers = {
                int(frame.layers[target_layer_index].sequence_number)
                for frame in self.frames
                if frame.layers[target_layer_index].sequence_number is not None
            }
            all_numbers.update(
                int(number)
                for archived_layer, number in self._sequence_archive
                if int(archived_layer) == target_layer_index
            )
            remaining = sorted(all_numbers - set(sheet_numbers))
            ordered = sheet_numbers + remaining
            mapping = {
                old_number: new_number
                for new_number, old_number in enumerate(ordered, 1)
            }
            next_number = len(mapping) + 1
            for frame in self.frames:
                layer = frame.layers[target_layer_index]
                if layer.sequence_number is not None:
                    layer.sequence_number = mapping[int(layer.sequence_number)]
                elif layer.has_content and not layer.sequence_only:
                    layer.sequence_number = next_number
                    next_number += 1
            normalized_archive = {}
            for (archived_layer, old_number), archived in self._sequence_archive.items():
                if int(archived_layer) == target_layer_index:
                    new_number = mapping.get(int(old_number), int(old_number))
                    archived.sequence_number = new_number
                else:
                    new_number = int(old_number)
                normalized_archive[(int(archived_layer), new_number)] = archived
            self._sequence_archive = normalized_archive

    def sequence_entry_columns(self, layer_index):
        """絵番号ごとの代表セル位置を番号順で返す。"""
        entries = {}
        for column, frame in enumerate(self.frames):
            if not (0 <= layer_index < len(frame.layers)):
                continue
            layer = frame.layers[layer_index]
            number = layer.sequence_number
            if (
                number is not None
                and int(number) >= 1
                and (layer.has_content or layer.is_blank_key)
            ):
                number = int(number)
                if number not in entries or layer.sequence_only:
                    entries[number] = column
        return [entries[number] for number in sorted(entries)]

    def insert_sequence_blank(self, layer_index, after_number):
        """選択番号の直後へ空画像番号を挿入し、後続番号を送る。"""
        layer_index = int(layer_index)
        entries = self.sequence_entry_columns(layer_index)
        insert_number = (
            1 if not entries else max(1, int(after_number) + 1)
        )
        self.push_doc_undo()
        # 同じ番号は同じ画像を指すため、シート上の重複参照も含めて
        # 挿入位置以降を一括で繰り下げる。
        for frame in self.frames:
            layer = frame.layers[layer_index]
            if (
                layer.sequence_number is not None
                and int(layer.sequence_number) >= insert_number
            ):
                layer.sequence_number = int(layer.sequence_number) + 1
        shifted_archive = {}
        for (archived_layer, number), archived in self._sequence_archive.items():
            if archived_layer == layer_index and int(number) >= insert_number:
                archived.sequence_number = int(number) + 1
                number = int(number) + 1
            shifted_archive[(archived_layer, int(number))] = archived
        self._sequence_archive = shifted_archive
        new_column = len(self.frames)
        self._ensure_frame_count(new_column + 1)
        target = self.frames[new_column].layers[layer_index]
        target.image = blank_image()
        target.has_content = False
        target.is_blank_key = True
        target.sequence_number = insert_number
        target.sequence_only = True
        target.exposure = 1
        self.current_frame = new_column
        self.active_layer_index = layer_index
        self._cell_structure_dirty = True
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def move_sequence_image(self, layer_index, first_number, second_number):
        """連番画像を差し込み移動し、間の画像を1コマずつ送る。"""
        first_number = int(first_number)
        second_number = int(second_number)
        if first_number == second_number:
            return False
        columns = self.sequence_entry_columns(int(layer_index))
        by_number = {
            int(self.frames[column].layers[layer_index].sequence_number): column
            for column in columns
        }
        if first_number not in by_number or second_number not in by_number:
            return False
        self.push_doc_undo()
        low, high = sorted((first_number, second_number))
        ordered_numbers = list(range(low, high + 1))
        snapshots = {}
        for number in ordered_numbers:
            if number not in by_number:
                return False
            layer = self.frames[by_number[number]].layers[layer_index]
            snapshots[number] = (
                layer.image.copy(), bool(layer.has_content), bool(layer.is_blank_key)
            )
        if first_number < second_number:
            source_for_number = {
                number: number + 1 for number in range(first_number, second_number)
            }
        else:
            source_for_number = {
                number: number - 1 for number in range(second_number + 1, first_number + 1)
            }
        source_for_number[second_number] = first_number
        for frame in self.frames:
            layer = frame.layers[layer_index]
            number = layer.sequence_number
            if number in source_for_number:
                image, has_content, is_blank = snapshots[source_for_number[number]]
                layer.image = image.copy()
                layer.has_content = has_content
                layer.is_blank_key = is_blank
        self.changed.emit()
        self.update()
        return True

    def sync_numbered_image_from_cell(self, frame_index, layer_index):
        """同じレイヤー・同じ絵番号を、1つの画像オブジェクトへ結び直す。"""
        source = self.frames[int(frame_index)].layers[int(layer_index)]
        if source.sequence_number is None or not source.has_content:
            return
        for index, frame in enumerate(self.frames):
            if index == int(frame_index):
                continue
            target = frame.layers[int(layer_index)]
            if target.sequence_number == source.sequence_number:
                # QImageのコピーを配ると、次の描画開始時点で各セルが
                # 別画像へ分離する。同じPythonオブジェクトを共有し、
                # 同じ番号を実体1枚として扱う。
                target.image = source.image
                target.has_content = True
                target.is_blank_key = False
        archived = self._sequence_archive.get((
            int(layer_index), int(source.sequence_number)
        ))
        if archived is not None:
            archived.image = source.image
            archived.has_content = True
            archived.is_blank_key = False

    def coalesce_numbered_images(self):
        """文書内の同一レイヤー・同一番号の画像参照を統合する。"""
        shared = {}
        preferred = int(self.current_frame)
        if 0 <= preferred < len(self.frames):
            for layer_index, layer in enumerate(self.frames[preferred].layers):
                if layer.has_content and layer.sequence_number is not None:
                    shared[(layer_index, int(layer.sequence_number))] = layer.image

        for frame in self.frames:
            for layer_index, layer in enumerate(frame.layers):
                if not layer.has_content or layer.sequence_number is None:
                    continue
                key = (layer_index, int(layer.sequence_number))
                image = shared.setdefault(key, layer.image)
                layer.image = image

        for key, archived in self._sequence_archive.items():
            shared_image = shared.get((int(key[0]), int(key[1])))
            if shared_image is not None:
                archived.image = shared_image

    def delete_sequence_entry(self, layer_index, number):
        """連番画像を削除し、シート側の参照セルを未使用へ戻す。"""
        number = int(number)
        self.push_doc_undo()
        found = False
        for frame in self.frames:
            layer = frame.layers[int(layer_index)]
            if layer.sequence_number == number:
                self._clear_timeline_layer_cell(layer)
                found = True
        if not found:
            self.undo_stack.pop()
            return False
        self._trim_unused_trailing_frames()
        self.current_frame = min(self.current_frame, len(self.frames) - 1)
        self._cell_structure_dirty = True
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def apply_sequence_only_entries(self):
        """連番で追加した番号をシートの保持区間、または末尾へ反映する。"""
        pending = []
        for column, frame in enumerate(self.frames):
            for layer_index, layer in enumerate(frame.layers):
                if layer.sequence_only and layer.sequence_number is not None:
                    pending.append((int(layer.sequence_number), layer_index, column, layer.clone()))
        for number, layer_index, source_column, copied in sorted(pending):
            target_exposure = 1
            previous = None
            for column, frame in enumerate(self.frames):
                layer = frame.layers[layer_index]
                if (
                    not layer.sequence_only
                    and layer.sequence_number == number - 1
                    and (layer.has_content or layer.is_blank_key)
                ):
                    previous = (column, layer)
                    break
            target_column = None
            if previous is not None:
                start, previous_layer = previous
                exposure = max(1, int(previous_layer.exposure))
                if exposure > 1:
                    # 保持区間の長さは変えず、前半を既存番号、後半を
                    # 追加番号へ分配する。
                    target_column = start + max(1, exposure // 2)
                    previous_layer.exposure = target_column - start
                    target_exposure = start + exposure - target_column
            if target_column is None:
                visible_columns = [
                    column
                    for column, frame in enumerate(self.frames)
                    if any(
                        (layer.has_content or layer.is_blank_key)
                        and not layer.sequence_only
                        for layer in frame.layers
                    )
                ]
                target_column = (max(visible_columns) + 1) if visible_columns else 0
            self._ensure_frame_count(target_column + 1)
            target = self.frames[target_column].layers[layer_index]
            if target.has_content or target.is_blank_key:
                target_column = len(self.frames)
                self._ensure_frame_count(target_column + 1)
                target = self.frames[target_column].layers[layer_index]
            target.image = copied.image.copy()
            target.has_content = bool(copied.has_content)
            target.is_blank_key = bool(
                copied.is_blank_key and not copied.has_content
            )
            target.sequence_number = number
            target.sequence_only = False
            target.exposure = max(1, int(target_exposure))
            target.visible = copied.visible
            target.opacity = copied.opacity
            self._clear_timeline_layer_cell(
                self.frames[source_column].layers[layer_index]
            )
        if pending:
            self._trim_unused_trailing_frames()
            self.current_frame = min(self.current_frame, len(self.frames) - 1)
            self._cell_structure_dirty = True
            self.changed.emit()
            self.selectionChanged.emit()
            self.update()

    def ensure_editable_key(self):
        """未使用／○／保持セルを独立した●キーフレームへ変換する。"""
        layer = self.active_layer
        if layer.has_content:
            if layer.sequence_number is not None:
                # 描画開始前から全参照を同じ画像へ結び、ストローク中も
                # 同番号の画像が分離しないようにする。
                self.sync_numbered_image_from_cell(
                    self.current_frame,
                    self.active_layer_index,
                )
            if layer.is_blank_key:
                # 連番の空セルへ描画した旧データでは両方のフラグが
                # Trueになり得る。内容キーとして即時修復する。
                layer.is_blank_key = False
                layer.sequence_only = False
                self._cell_structure_dirty = True
                return True
            return False

        current = int(self.current_frame)
        layer_index = int(self.active_layer_index)
        block = self.timeline_block_at(current, layer_index)
        retained_number = layer.sequence_number

        if block is not None:
            kind, start, exposure = block
            source_layer = self.frames[start].layers[layer_index]
            copied = source_layer.clone()
            old_end = start + exposure - 1

            if current > start:
                source_layer.exposure = max(1, current - start)

            layer.visible = bool(copied.visible)
            layer.opacity = float(copied.opacity)
            layer.alpha_locked = bool(copied.alpha_locked)
            layer.color_filter_enabled = bool(
                copied.color_filter_enabled
            )
            layer.color_filter_rgb = (
                tuple(copied.color_filter_rgb)
                if copied.color_filter_rgb is not None
                else None
            )
            layer.image = (
                copied.image.copy()
                if kind == "content"
                else blank_image()
            )
            layer.exposure = max(1, old_end - current + 1)
        else:
            layer.image = blank_image()
            layer.exposure = 1

        layer.has_content = True
        layer.is_blank_key = False
        if self.timeline_mode == "sheet":
            if retained_number is not None:
                layer.sequence_number = int(retained_number)
            else:
                existing_numbers = [
                    int(frame.layers[layer_index].sequence_number)
                    for frame in self.frames
                    if (
                        frame.layers[layer_index].has_content
                        and frame.layers[layer_index].sequence_number is not None
                    )
                ]
                layer.sequence_number = max(existing_numbers, default=0) + 1
        self._cell_structure_dirty = True
        return True

    def set_tool(self, t):
        if t != self.tool:
            self._stop_brush_follow_timer()
            self.line_start = None
            self.line_end = None
            self.line_control = None
            self.line_curve_stage = 0
            self.shape_start = None
            self.shape_end = None
            self.drawing = False
        self.tool = t
        self.update_tool_cursor()
        self.update()

    def _eyedropper_cursor(self):
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor("black"), 2)
        painter.setPen(pen)
        painter.drawLine(5, 18, 16, 7)
        painter.drawLine(8, 21, 19, 10)
        painter.drawLine(15, 6, 20, 11)
        painter.drawEllipse(QPointF(5, 19), 2, 2)
        painter.end()
        return QCursor(pixmap, 5, 19)

    def begin_onion_transform_interaction(self, direction):
        """旧UI互換：前／後オニオンをXY移動モードにする。"""
        self.begin_onion_shift_interaction(direction, "xy")

    def begin_onion_shift_interaction(self, direction, axis="x"):
        """前／後オニオンのXYシフトをキャンバス上で調整する。"""
        axis = str(axis).lower()
        if axis not in ("x", "y", "xy"):
            axis = "xy"
        self._onion_interaction_mode = "onion_shift"
        self._onion_interaction_direction = (
            -1 if int(direction) < 0 else 1
        )
        self._onion_interaction_axis = axis
        self._onion_interaction_operation = "move"
        self._onion_interaction_dragging = False
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update_tool_cursor()
        label = (
            "前" if self._onion_interaction_direction < 0 else "後"
        )
        axis_label = (
            "X方向" if axis == "x"
            else "Y方向" if axis == "y"
            else "XY方向"
        )
        self.status_message.emit(
            f"{label}のオニオンスキン：ドラッグで"
            f"{axis_label}へ移動します。Escで解除します。"
        )

    def begin_onion_canvas_position_interaction(self):
        """キャンバスの移動と回転を行い、前後の相対値を補正する。"""
        self._onion_interaction_mode = "canvas"
        self._onion_interaction_direction = 0
        self._onion_interaction_axis = None
        self._onion_interaction_operation = None
        self._onion_interaction_dragging = False
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update_tool_cursor()
        self.status_message.emit(
            "表示位置：左ドラッグでキャンバス移動、"
            "Shift＋左ドラッグまたは右ドラッグで回転します。"
            "TU／TB拡大率は維持され、前後オニオンの相対値へ"
            "反映されます。Escで解除します。"
        )

    @staticmethod
    def _normalized_angle(angle):
        return ((float(angle) + 180.0) % 360.0) - 180.0

    @staticmethod
    def _rotated_vector(x, y, angle_degrees):
        angle = math.radians(float(angle_degrees))
        return QPointF(
            float(x) * math.cos(angle) - float(y) * math.sin(angle),
            float(x) * math.sin(angle) + float(y) * math.cos(angle),
        )

    def set_onion_canvas_view_rotation(self, value):
        """表示位置の回転UIからキャンバス角度を変更する。"""
        target = self._normalized_angle(value)
        current = self._normalized_angle(self.rotation)
        delta = self._normalized_angle(target - current)
        if abs(delta) < 1e-9:
            return

        # キャンバス回転分を前後の相対値から差し引き、
        # オニオンの画面上の位置・角度を保つ。
        previous_shift = self._rotated_vector(
            self.onion_previous_shift_x,
            self.onion_previous_shift_y,
            -delta,
        )
        next_shift = self._rotated_vector(
            self.onion_next_shift_x,
            self.onion_next_shift_y,
            -delta,
        )
        self.onion_previous_shift_x = previous_shift.x()
        self.onion_previous_shift_y = previous_shift.y()
        self.onion_next_shift_x = next_shift.x()
        self.onion_next_shift_y = next_shift.y()
        self.onion_previous_rotation = self._normalized_angle(
            self.onion_previous_rotation - delta
        )
        self.onion_next_rotation = self._normalized_angle(
            self.onion_next_rotation - delta
        )
        self.rotation = target
        self.viewChanged.emit(
            float(self.zoom),
            float(self.rotation),
        )
        self.onionInteractionChanged.emit()
        self.update()

    def cancel_onion_interaction(self, restore=False):
        if self._onion_interaction_mode is None:
            return
        if restore and self._onion_interaction_dragging:
            self.pan = QPointF(self._onion_interaction_start_pan)
            self.rotation = float(
                self._onion_interaction_start_view_rotation
            )
            (
                self.onion_previous_shift_x,
                self.onion_previous_shift_y,
            ) = self._onion_interaction_start_previous
            (
                self.onion_next_shift_x,
                self.onion_next_shift_y,
            ) = self._onion_interaction_start_next
            self.onion_previous_rotation = float(
                self._onion_interaction_start_previous_rotation
            )
            self.onion_next_rotation = float(
                self._onion_interaction_start_next_rotation
            )
            self.onion_previous_scale = float(
                self._onion_interaction_start_previous_scale
            )
            self.onion_next_scale = float(
                self._onion_interaction_start_next_scale
            )
            self.onion_tu_tb_scale = float(
                self._onion_interaction_start_tu_tb_scale
            )
            self.onionInteractionChanged.emit()
            self.viewChanged.emit(
                float(self.zoom),
                float(self.rotation),
            )
        self._onion_interaction_mode = None
        self._onion_interaction_axis = None
        self._onion_interaction_operation = None
        self._onion_interaction_dragging = False
        self.drawing = False
        self.update_tool_cursor()
        self.update()
        self.onionInteractionFinished.emit()

    def _onion_widget_delta_to_canvas(self, delta):
        zoom = max(
            0.0001,
            float(self.zoom) * float(self.onion_tu_tb_scale),
        )
        dx = float(delta.x())
        dy = float(delta.y())
        angle = math.radians(float(self.rotation))
        canvas_dx = (
            dx * math.cos(angle) + dy * math.sin(angle)
        ) / zoom
        canvas_dy = (
            -dx * math.sin(angle) + dy * math.cos(angle)
        ) / zoom
        return QPointF(canvas_dx, canvas_dy)

    def _begin_onion_interaction_drag(
        self,
        widget_position,
        button=Qt.MouseButton.LeftButton,
        modifiers=Qt.KeyboardModifier.NoModifier,
    ):
        self._onion_interaction_dragging = True
        self._onion_interaction_start_widget = QPointF(
            widget_position
        )
        self._onion_interaction_start_pan = QPointF(self.pan)
        self._onion_interaction_start_view_rotation = float(
            self.rotation
        )
        self._onion_interaction_start_previous = (
            float(self.onion_previous_shift_x),
            float(self.onion_previous_shift_y),
        )
        self._onion_interaction_start_next = (
            float(self.onion_next_shift_x),
            float(self.onion_next_shift_y),
        )
        self._onion_interaction_start_previous_rotation = float(
            self.onion_previous_rotation
        )
        self._onion_interaction_start_next_rotation = float(
            self.onion_next_rotation
        )
        self._onion_interaction_start_previous_scale = float(
            self.onion_previous_scale
        )
        self._onion_interaction_start_next_scale = float(
            self.onion_next_scale
        )
        self._onion_interaction_start_tu_tb_scale = float(
            self.onion_tu_tb_scale
        )
        if self._onion_interaction_mode == "onion_shift":
            self._onion_interaction_operation = "move"
        else:
            self._onion_interaction_operation = (
                "rotate"
                if (
                    button == Qt.MouseButton.RightButton
                    or modifiers
                    & Qt.KeyboardModifier.ShiftModifier
                )
                else "move"
            )
        self.drawing = True
        self.update_tool_cursor()

    def _update_onion_interaction_drag(self, widget_position):
        if not self._onion_interaction_dragging:
            return
        widget_delta = (
            QPointF(widget_position)
            - self._onion_interaction_start_widget
        )
        canvas_delta = self._onion_widget_delta_to_canvas(
            widget_delta
        )

        previous_x, previous_y = (
            self._onion_interaction_start_previous
        )
        next_x, next_y = self._onion_interaction_start_next
        operation = self._onion_interaction_operation or "move"

        if self._onion_interaction_mode == "onion_shift":
            direction = self._onion_interaction_direction
            axis = self._onion_interaction_axis or "x"
            delta_x = (
                canvas_delta.x() if axis in ("x", "xy") else 0.0
            )
            delta_y = (
                canvas_delta.y() if axis in ("y", "xy") else 0.0
            )
            if direction < 0:
                self.onion_previous_shift_x = (
                    previous_x + delta_x
                )
                self.onion_previous_shift_y = (
                    previous_y + delta_y
                )
            else:
                self.onion_next_shift_x = next_x + delta_x
                self.onion_next_shift_y = next_y + delta_y

        elif self._onion_interaction_mode == "canvas":
            if operation == "rotate":
                angle_delta = float(widget_delta.x()) * 0.35
                self.rotation = self._normalized_angle(
                    self._onion_interaction_start_view_rotation
                    + angle_delta
                )

                # キャンバスの回転分をシフト座標とオニオン回転から
                # 差し引き、オニオンスキンの画面上の位置・角度を保つ。
                previous_shift = self._rotated_vector(
                    previous_x,
                    previous_y,
                    -angle_delta,
                )
                next_shift = self._rotated_vector(
                    next_x,
                    next_y,
                    -angle_delta,
                )
                self.onion_previous_shift_x = previous_shift.x()
                self.onion_previous_shift_y = previous_shift.y()
                self.onion_next_shift_x = next_shift.x()
                self.onion_next_shift_y = next_shift.y()
                self.onion_previous_rotation = (
                    self._normalized_angle(
                        self._onion_interaction_start_previous_rotation
                        - angle_delta
                    )
                )
                self.onion_next_rotation = (
                    self._normalized_angle(
                        self._onion_interaction_start_next_rotation
                        - angle_delta
                    )
                )
                self.viewChanged.emit(
                    float(self.zoom),
                    float(self.rotation),
                )
            else:
                # キャンバスは画面上でドラッグし、前後のオニオンは
                # 画面上の位置を保つよう逆向きにシフト値を補正する。
                self.pan = (
                    self._onion_interaction_start_pan
                    + widget_delta
                )
                self.onion_previous_shift_x = (
                    previous_x - canvas_delta.x()
                )
                self.onion_previous_shift_y = (
                    previous_y - canvas_delta.y()
                )
                self.onion_next_shift_x = (
                    next_x - canvas_delta.x()
                )
                self.onion_next_shift_y = (
                    next_y - canvas_delta.y()
                )
                self.viewChanged.emit(
                    float(self.zoom),
                    float(self.rotation),
                )

        self.onionInteractionChanged.emit()
        self.update()

    def update_tool_cursor(self):
        if self._onion_interaction_mode in (
            "canvas",
            "onion_shift",
            "onion_transform",
        ):
            if (
                self._onion_interaction_dragging
                and self._onion_interaction_operation == "rotate"
            ):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif self._onion_interaction_mode == "canvas":
                self.setCursor(
                    Qt.CursorShape.ClosedHandCursor
                    if self._onion_interaction_dragging
                    else Qt.CursorShape.OpenHandCursor
                )
            elif self._onion_interaction_axis == "x":
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif self._onion_interaction_axis == "y":
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            else:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            return

        tool = self.effective_tool()
        if tool == "hand":
            self.setCursor(
                Qt.CursorShape.ClosedHandCursor
                if self.drawing or self.middle_hand
                else Qt.CursorShape.OpenHandCursor
            )
        elif tool in ("zoom", "rotate"):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif tool == "eyedropper":
            self.setCursor(self._eyedropper_cursor())
        elif tool == "brush" and self._brush_cursor_inside:
            # 同じBlankCursorをマウス移動のたびに再設定すると、Windowsで
            # OSカーソルと描画リングが一瞬切り替わって見えることがある。
            if self.cursor().shape() != Qt.CursorShape.BlankCursor:
                self.setCursor(Qt.CursorShape.BlankCursor)
        else:
            self.unsetCursor()

    def set_pen_size(self, value):
        self.pen_size = max(0.5, float(value))
        self.update()

    def set_brush_stabilizer(self, value):
        self.brush_stabilizer_strength = max(
            0, min(300, int(value))
        )

    def _brush_stabilizer_window(self):
        strength = max(
            0, min(300, int(self.brush_stabilizer_strength))
        )
        if strength <= 0:
            return 1
        return max(2, min(64, 2 + int(round(strength * 0.20))))

    def _reset_brush_stabilizer(self, point=None):
        initial = QPointF(point) if point is not None else None
        self._stabilized_canvas = (
            QPointF(initial) if initial is not None else None
        )
        self._last_raw_canvas = (
            QPointF(initial) if initial is not None else None
        )
        self._brush_stabilizer_history = (
            [QPointF(initial)] if initial is not None else []
        )
        self._last_brush_pressure = 1.0
        self._pressure_input_history = []

    def _smooth_brush_pressure(self, pressure):
        """直近の筆圧を重み付き平均し、サイズ変化の段差を抑える。"""
        value = max(0.001, min(1.0, float(pressure)))
        history = self._pressure_input_history
        history.append(value)
        if len(history) > 7:
            del history[:-7]
        weights = list(range(1, len(history) + 1))
        return sum(
            sample * weight
            for sample, weight in zip(history, weights)
        ) / float(sum(weights))


    def _stabilized_brush_point(self, raw_point, settle=0.0):
        """移動平均＋遅延半径で、実際に描画する座標を返す。"""
        raw = QPointF(raw_point)
        strength = max(
            0, min(300, int(self.brush_stabilizer_strength))
        )
        settle = max(0.0, min(1.0, float(settle)))
        self._last_raw_canvas = QPointF(raw)

        if strength <= 0:
            self._brush_stabilizer_history = [QPointF(raw)]
            self._stabilized_canvas = QPointF(raw)
            return QPointF(raw)

        history = self._brush_stabilizer_history
        history.append(QPointF(raw))
        window = self._brush_stabilizer_window()
        if len(history) > window:
            del history[:-window]

        # 新しい点を少し強くしつつ、直近の細かな揺れを平均化する。
        weights = list(range(1, len(history) + 1))
        weight_sum = float(sum(weights))
        target = QPointF(
            sum(point.x() * weight for point, weight in zip(history, weights))
            / weight_sum,
            sum(point.y() * weight for point, weight in zip(history, weights))
            / weight_sum,
        )

        if self._stabilized_canvas is None:
            self._stabilized_canvas = QPointF(target)
            return QPointF(target)

        current = QPointF(self._stabilized_canvas)
        dx = target.x() - current.x()
        dy = target.y() - current.y()
        distance = math.hypot(dx, dy)

        # 画面上の約0～20pxを補正半径として使う。
        # 強度が大きいほど、小さな手振れでは描画点が動かない。
        radius_screen = (
            60.0 * math.pow(strength / 300.0, 1.35)
        )
        radius_canvas = (
            radius_screen / max(0.05, float(self.zoom))
        )
        radius_canvas *= (1.0 - settle)

        if distance <= radius_canvas or distance <= 1e-9:
            filtered = current
        else:
            move_distance = distance - radius_canvas
            ratio = move_distance / distance
            filtered = QPointF(
                current.x() + dx * ratio,
                current.y() + dy * ratio,
            )

        self._stabilized_canvas = QPointF(filtered)
        return filtered

    def _draw_stabilized_brush_to(self, raw_point, pressure):
        """入力イベント間も補間し、補正後の軌跡を連続描画する。"""
        raw = QPointF(raw_point)
        self._brush_follow_settle = 0.0
        self._start_brush_follow_timer()
        if self.last_canvas is None:
            self.last_canvas = QPointF(raw)

        strength = max(
            0, min(300, int(self.brush_stabilizer_strength))
        )
        if strength <= 0:
            self.draw_line(self.last_canvas, raw, pressure)
            self.last_canvas = QPointF(raw)
            self._last_raw_canvas = QPointF(raw)
            return

        start_raw = (
            QPointF(self._last_raw_canvas)
            if self._last_raw_canvas is not None
            else QPointF(raw)
        )
        distance = math.hypot(
            raw.x() - start_raw.x(),
            raw.y() - start_raw.y(),
        )
        step_canvas = max(
            0.5,
            min(
                2.5,
                1.5 / max(0.05, float(self.zoom)),
            ),
        )
        steps = max(
            1,
            min(96, int(math.ceil(distance / step_canvas))),
        )

        for index in range(1, steps + 1):
            ratio = index / float(steps)
            intermediate = QPointF(
                start_raw.x() + (raw.x() - start_raw.x()) * ratio,
                start_raw.y() + (raw.y() - start_raw.y()) * ratio,
            )
            filtered = self._stabilized_brush_point(intermediate)
            if (
                abs(filtered.x() - self.last_canvas.x()) > 0.005
                or abs(filtered.y() - self.last_canvas.y()) > 0.005
            ):
                self.draw_line(
                    self.last_canvas,
                    filtered,
                    pressure,
                )
                self.last_canvas = QPointF(filtered)

    def _start_brush_follow_timer(self):
        self._brush_follow_settle = 0.0
        if (
            int(self.brush_stabilizer_strength) > 0
            and not self._brush_follow_timer.isActive()
        ):
            self._brush_follow_timer.start()

    def _stop_brush_follow_timer(self):
        self._brush_follow_timer.stop()
        self._brush_follow_settle = 0.0

    def _advance_stabilized_brush(self):
        """入力が止まっていても、描画点をカーソル終点へ追従させる。"""
        if (
            not self.drawing
            or self.effective_tool() != "brush"
            or self._last_raw_canvas is None
            or self.last_canvas is None
        ):
            self._stop_brush_follow_timer()
            return

        raw = QPointF(self._last_raw_canvas)
        distance = math.hypot(
            raw.x() - self.last_canvas.x(),
            raw.y() - self.last_canvas.y(),
        )
        if distance <= 0.01:
            return

        strength = max(
            0,
            min(300, int(self.brush_stabilizer_strength)),
        )
        if strength <= 0:
            self.draw_line(
                self.last_canvas,
                raw,
                self._last_brush_pressure,
            )
            self.last_canvas = QPointF(raw)
            return

        self._brush_follow_settle = min(
            1.0,
            self._brush_follow_settle
            + 0.035
            + 0.055 * (strength / 300.0),
        )
        filtered = self._stabilized_brush_point(
            raw,
            settle=self._brush_follow_settle,
        )
        if (
            abs(filtered.x() - self.last_canvas.x()) > 0.005
            or abs(filtered.y() - self.last_canvas.y()) > 0.005
        ):
            self.draw_line(
                self.last_canvas,
                filtered,
                self._last_brush_pressure,
            )
            self.last_canvas = QPointF(filtered)

    def _finish_stabilized_brush(self, raw_point, pressure):
        """ペンを離したとき、補正点を終点へ滑らかに収束させる。"""
        raw = QPointF(raw_point)
        self._draw_stabilized_brush_to(raw, pressure)

        strength = max(
            0, min(300, int(self.brush_stabilizer_strength))
        )
        if strength <= 0:
            return

        settle_steps = self._brush_stabilizer_window() + 8
        for index in range(settle_steps):
            settle = (index + 1) / float(settle_steps)
            filtered = self._stabilized_brush_point(
                raw,
                settle=settle,
            )
            if (
                abs(filtered.x() - self.last_canvas.x()) > 0.005
                or abs(filtered.y() - self.last_canvas.y()) > 0.005
            ):
                self.draw_line(
                    self.last_canvas,
                    filtered,
                    pressure,
                )
                self.last_canvas = QPointF(filtered)

        # 数値誤差だけが残った場合も、終点を確実に一致させる。
        if (
            abs(raw.x() - self.last_canvas.x()) > 0.01
            or abs(raw.y() - self.last_canvas.y()) > 0.01
        ):
            self.draw_line(self.last_canvas, raw, pressure)
            self.last_canvas = QPointF(raw)

    def enterEvent(self, event):
        local = self.mapFromGlobal(QCursor.pos())
        self._brush_cursor_widget_pos = QPointF(local)
        self._brush_cursor_inside = self.inside(
            self.widget_to_canvas(self._brush_cursor_widget_pos)
        )
        self.update_tool_cursor()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._brush_cursor_inside = False
        self.update_tool_cursor()
        self.update()
        super().leaveEvent(event)

    def effective_tool(self): return self.temp_tool or self.tool
    def work_rect(self):
        w,h=workspace_size(); return QRectF(self.pan.x(),self.pan.y(),w*self.zoom,h*self.zoom)
    def canvas_rect(self):
        return QRectF(self.pan.x()+OUTSIDE_MARGIN*self.zoom,self.pan.y()+OUTSIDE_MARGIN*self.zoom,CANVAS_WIDTH*self.zoom,CANVAS_HEIGHT*self.zoom)
    def widget_to_canvas(self,p):
        c=self.work_rect().center(); q=QPointF(p)
        if self.rotation:
            a=math.radians(-self.rotation); dx=q.x()-c.x();dy=q.y()-c.y();q=QPointF(c.x()+dx*math.cos(a)-dy*math.sin(a),c.y()+dx*math.sin(a)+dy*math.cos(a))
        camera_scale=max(0.0001,float(self.onion_tu_tb_scale))
        if abs(camera_scale-1.0)>1e-9:
            q=QPointF(
                c.x()+(q.x()-c.x())/camera_scale,
                c.y()+(q.y()-c.y())/camera_scale,
            )
        x=(q.x()-self.pan.x())/self.zoom; y=(q.y()-self.pan.y())/self.zoom
        if self.flip_horizontal:x=workspace_size()[0]-x
        return QPointF(x,y)
    def canvas_to_widget(self,p):
        w,_=workspace_size(); x=w-p.x() if self.flip_horizontal else p.x(); q=QPointF(self.pan.x()+x*self.zoom,self.pan.y()+p.y()*self.zoom)
        c=self.work_rect().center()
        camera_scale=max(0.0001,float(self.onion_tu_tb_scale))
        if abs(camera_scale-1.0)>1e-9:
            q=QPointF(
                c.x()+(q.x()-c.x())*camera_scale,
                c.y()+(q.y()-c.y())*camera_scale,
            )
        if self.rotation:
            a=math.radians(self.rotation);dx=q.x()-c.x();dy=q.y()-c.y();q=QPointF(c.x()+dx*math.cos(a)-dy*math.sin(a),c.y()+dx*math.sin(a)+dy*math.cos(a))
        return q
    def inside(self,p): w,h=workspace_size(); return 0<=p.x()<w and 0<=p.y()<h
    def color(self):
        if self.color_mode=="transparent":
            return QColor(0,0,0,0)
        return QColor(self.main_color if self.color_mode=="main" else self.sub_color)

    @staticmethod
    def is_pseudo_transparent_color(color):
        color = QColor(color)
        return (
            color.red() == 255
            and color.green() == 255
            and color.blue() == 255
        )

    def paint_source_color(self, base_color=None):
        """描画元のRGB色を、常にα255で返す。"""
        source = QColor(
            self.color() if base_color is None else base_color
        )
        if source.alpha() == 0:
            # 透明色モードは、キャンバス上では疑似透明色の白。
            source = QColor(255, 255, 255)
        source.setAlpha(255)
        return source

    def paint_opacity_value(self, opacity=None):
        """UI不透明度だけを返す。筆圧値は一切参照しない。"""
        window = self.window()
        tools = getattr(window, "tools", None)
        checkbox = getattr(
            tools,
            "opacity_enabled",
            None,
        )
        enabled = bool(
            checkbox is not None
            and checkbox.isChecked()
        )
        if not enabled:
            return 1.0

        amount = (
            float(self.pen_opacity)
            if opacity is None
            else float(opacity)
        )
        return max(0.0, min(1.0, amount))

    def blended_paint_color(
        self,
        destination_color,
        base_color=None,
        opacity=None,
    ):
        """互換用。下地と混色せず、指定RGBをそのまま返す。"""
        del destination_color, opacity
        return self.paint_source_color(base_color)

    def opaque_paint_color(
        self,
        base_color=None,
        opacity=None,
    ):
        """選択RGBをα255のまま返す。不透明度ではRGBを変えない。"""
        del opacity
        return self.paint_source_color(base_color)

    def _emit_actual_paint_colors(self, colors, maximum=64):
        """実際に生成されたRGBを安全に使用色へ通知する。"""
        if colors is None:
            return
        try:
            iterator = iter(colors)
        except TypeError:
            return

        emitted = 0
        seen = set()
        for value in iterator:
            try:
                rgb = tuple(
                    max(0, min(255, int(channel)))
                    for channel in value[:3]
                )
            except (
                TypeError,
                ValueError,
                IndexError,
            ):
                continue
            if rgb == (255, 255, 255) or rgb in seen:
                continue
            seen.add(rgb)
            red, green, blue = (
                int(rgb[0]),
                int(rgb[1]),
                int(rgb[2]),
            )
            self.colorUsed.emit(
                QColor(red, green, blue)
            )
            emitted += 1
            if emitted >= max(1, int(maximum)):
                break

    def _binary_paint_overlay_rgba(self, overlay):
        """描画マスクの境界を完全な0／255へ2値化する。

        筆圧による線幅変化、曲線、ラインの入り抜きで発生した
        半端なアルファ値を、RGB合成の前に除去する。
        """
        rgba = self._qimage_rgba_array(overlay)
        if rgba.size == 0:
            return rgba

        alpha = rgba[:, :, 3]
        active = alpha >= 128

        # 非描画部分は完全な透明、描画部分は完全な不透明に固定する。
        rgba[~active, :3] = 0
        rgba[:, :, 3][~active] = 0
        rgba[:, :, 3][active] = 255
        return rgba

    @staticmethod
    def _paint_rgb_palette(colors):
        """QColor／RGB列を重複のないuint8パレットへ変換する。"""
        normalized = []
        seen = set()
        for value in colors or ():
            try:
                if isinstance(value, QColor):
                    rgb = (
                        int(value.red()),
                        int(value.green()),
                        int(value.blue()),
                    )
                else:
                    rgb = tuple(
                        max(0, min(255, int(channel)))
                        for channel in value[:3]
                    )
            except (
                TypeError,
                ValueError,
                IndexError,
            ):
                continue
            if len(rgb) != 3 or rgb in seen:
                continue
            seen.add(rgb)
            normalized.append(rgb)

        if not normalized:
            return np.zeros((0, 3), dtype=np.uint8)
        return np.asarray(
            normalized,
            dtype=np.uint8,
        )

    @classmethod
    def _snap_overlay_to_exact_rgbs(
        cls,
        overlay_rgba,
        active_mask,
        exact_colors,
    ):
        """境界の丸め誤差を、指定された正規RGBへ吸着する。"""
        rgba = np.asarray(
            overlay_rgba,
            dtype=np.uint8,
        ).copy()
        mask = np.asarray(
            active_mask,
            dtype=bool,
        )
        palette = cls._paint_rgb_palette(
            exact_colors
        )
        if (
            palette.size == 0
            or not np.any(mask)
        ):
            return rgba

        if len(palette) == 1:
            rgba[mask, :3] = palette[0]
            return rgba

        # 図形など複数色を使う場合は、丸め誤差を含む画素を
        # 最も近い正規RGBへ割り当てる。
        samples = rgba[mask, :3].astype(np.int16)
        palette_values = palette.astype(np.int16)
        delta = (
            samples[:, None, :]
            - palette_values[None, :, :]
        )
        distance = np.sum(
            delta * delta,
            axis=2,
            dtype=np.int32,
        )
        nearest = np.argmin(
            distance,
            axis=1,
        )
        rgba[mask, :3] = palette[nearest]
        return rgba

    def _blend_overlay_into_active_layer(
        self,
        overlay,
        top_left=None,
        base_image=None,
        opacity=None,
        exact_colors=None,
    ):
        """100%は正規RGB直書き、100%未満だけ通常の不透明度合成。"""
        if overlay is None or overlay.isNull():
            return ()

        destination_image = self.active_layer.image
        if (
            destination_image is None
            or destination_image.isNull()
        ):
            return ()

        point = (
            top_left
            if top_left is not None
            else QPoint(0, 0)
        )
        origin_x = int(point.x())
        origin_y = int(point.y())

        source_x = max(0, -origin_x)
        source_y = max(0, -origin_y)
        destination_x = max(0, origin_x)
        destination_y = max(0, origin_y)
        width = min(
            overlay.width() - source_x,
            destination_image.width() - destination_x,
        )
        height = min(
            overlay.height() - source_y,
            destination_image.height() - destination_y,
        )
        if width <= 0 or height <= 0:
            return ()

        overlay_rgba = self._binary_paint_overlay_rgba(
            overlay
        )
        overlay_rgba = overlay_rgba[
            source_y:source_y + height,
            source_x:source_x + width,
        ]
        active_mask = (
            overlay_rgba[:, :, 3] == 255
        )
        if not np.any(active_mask):
            return ()

        # プレマルチプライ変換の±1誤差を、正規RGBへ必ず吸着する。
        overlay_rgba = self._snap_overlay_to_exact_rgbs(
            overlay_rgba,
            active_mask,
            exact_colors,
        )

        amount = self.paint_opacity_value(opacity)
        if amount <= 0.0:
            return ()

        result_rgba = np.zeros_like(overlay_rgba)

        if amount >= 0.999999:
            # 最重要経路：100%では下地を参照せず正規RGBを直書きする。
            # ここでは新しい近似RGBを生成しない。
            result_rgba[active_mask, :3] = (
                overlay_rgba[active_mask, :3]
            )
            result_rgba[active_mask, 3] = 255
            written_rgb = overlay_rgba[
                active_mask,
                :3,
            ]
        else:
            # 不透明度を明示的にONにした場合だけ、通常のRGB合成を行う。
            reference = (
                base_image
                if (
                    base_image is not None
                    and not base_image.isNull()
                    and base_image.width()
                    == destination_image.width()
                    and base_image.height()
                    == destination_image.height()
                )
                else destination_image
            )
            base_crop = reference.copy(
                destination_x,
                destination_y,
                width,
                height,
            )
            base_rgba = self._qimage_rgba_array(
                base_crop
            )
            base_rgb = base_rgba[
                :, :, :3
            ].astype(np.float32)
            base_rgb[
                base_rgba[:, :, 3] == 0
            ] = 255.0

            source_rgb = overlay_rgba[
                :, :, :3
            ].astype(np.float32)
            blended_rgb = np.clip(
                np.rint(
                    base_rgb * (1.0 - amount)
                    + source_rgb * amount
                ),
                0,
                255,
            ).astype(np.uint8)

            result_rgba[
                active_mask,
                :3,
            ] = blended_rgb[active_mask]
            result_rgba[
                active_mask,
                3,
            ] = 255
            written_rgb = blended_rgb[
                active_mask
            ]

        result_image = self._rgba_array_to_qimage(
            result_rgba
        )
        painter = QPainter(destination_image)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver
        )
        painter.drawImage(
            QPoint(destination_x, destination_y),
            result_image,
        )
        painter.end()

        if written_rgb.size == 0:
            return ()

        # 100%は正規RGBだけを通知する。
        if amount >= 0.999999:
            palette = self._paint_rgb_palette(
                exact_colors
            )
            if palette.size:
                return tuple(
                    tuple(int(channel) for channel in rgb)
                    for rgb in palette
                )

        unique = np.unique(
            written_rgb,
            axis=0,
        )
        return tuple(
            tuple(int(channel) for channel in rgb)
            for rgb in unique
        )

    def _begin_opaque_brush_stroke(self):
        """UI不透明度を固定し、筆圧から完全に分離する。"""
        self._brush_blend_base_image = (
            self.active_layer.image.copy()
        )
        self._brush_blended_colors = set()
        # この値はUIの不透明度だけから取得する。
        # タブレット筆圧値は絶対に掛けない。
        self._brush_stroke_opacity = (
            self.paint_opacity_value()
        )

    def _finish_opaque_brush_stroke(self):
        colors = tuple(
            getattr(self, "_brush_blended_colors", set())
        )
        self._emit_actual_paint_colors(colors)
        self._brush_blend_base_image = None
        self._brush_blended_colors = set()
        self._brush_stroke_opacity = (
            self.paint_opacity_value()
        )

    def selected_mask_colors(self):
        values = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in getattr(self, "mask_color_rgbs", set())
            if rgb is not None and len(rgb) >= 3
        }
        legacy = getattr(self, "mask_color_rgb", None)
        if legacy is not None and not values:
            values.add(tuple(int(channel) for channel in legacy[:3]))
        return values
    def selected_used_colors(self):
        return {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in getattr(self, "selected_used_color_rgbs", set())
            if rgb is not None and len(rgb) >= 3
        }

    def _invalidate_tp_preview_cache(self, geometry=True):
        """Invalidate TP output; geometry=False keeps transformed mask cache."""
        self._tp_preview_cache_key = None
        self._tp_preview_cache_image = None
        if geometry:
            self._tp_geometry_cache_key = None
            self._tp_geometry_cache_bbox = None
            self._tp_geometry_cache_fill_overlay = None
            self._tp_geometry_cache_line_soft = []

    def _clear_tp_transform_masks(self, clear_proxy=True):
        self.transform_tp_palette = []
        self.transform_tp_masks = []
        self.transform_tp_line_masks = []
        self.transform_tp_prepared_preview = None
        self._tp_mask_source_key = None
        if clear_proxy:
            self._tp_proxy_source_key = None
            self._tp_proxy_source_image = None
        self._invalidate_tp_preview_cache()

    def _tp_mask_key(self, source):
        if source is None or source.isNull():
            return None
        return (
            int(source.cacheKey()),
            int(source.width()),
            int(source.height()),
            tuple(self.transform_tp_line_colors),
            bool(self._tp_proxy_rendering),
        )

    @staticmethod
    def _tp_uses_proxy(target_width, target_height):
        return max(int(target_width), int(target_height)) >= TP_MASK_PROXY_THRESHOLD

    def _tp_geometry_cache_is_current(self, source=None, target_width=None, target_height=None):
        source = source if source is not None else self.transform_source
        if source is None or source.isNull() or not self.transform_points:
            return False
        target_width = int(
            target_width if target_width is not None else self.active_layer.image.width()
        )
        target_height = int(
            target_height if target_height is not None else self.active_layer.image.height()
        )
        bbox, _raw_selection = self._tp_transform_bbox(target_width, target_height)
        if bbox.isEmpty():
            return self._tp_geometry_cache_key is not None
        key = self._tp_geometry_key(source, target_width, target_height, bbox)
        return (
            self._tp_geometry_cache_key == key
            and self._tp_geometry_cache_fill_overlay is not None
        )

    def request_quality_preview_counter(self, label="クオリティプレビューを生成しています"):
        """Schedule quality rendering after the current paint/input event."""
        if (
            self._tp_preview_progress_busy
            or self._tp_preview_progress_scheduled
            or not self.transform_active
            or not self.transform_quality_active
        ):
            return
        self._tp_preview_progress_scheduled = True
        QTimer.singleShot(
            0,
            lambda text=str(label): self.refresh_quality_preview_with_counter(text),
        )

    def refresh_quality_preview_with_counter(
        self,
        label="クオリティプレビューを生成しています",
        full_resolution=False,
    ):
        """Build the expensive TP_mask preview with a visible mask counter."""
        self._tp_preview_progress_scheduled = False
        if (
            self._tp_preview_progress_busy
            or not self.transform_active
            or not self.transform_quality_active
            or self._transform_line_adjusting
            or self.transform_source is None
            or self.transform_source.isNull()
        ):
            return

        target_width = self.active_layer.image.width()
        target_height = self.active_layer.image.height()
        use_proxy = (
            not full_resolution
            and self._tp_uses_proxy(target_width, target_height)
        )
        if use_proxy:
            if self._tp_preview_cache_image is not None:
                return
        else:
            geometry_current = self._tp_geometry_cache_is_current(
                self.transform_source, target_width, target_height
            )
            if geometry_current and self._tp_preview_cache_image is not None:
                return

        total = max(
            1,
            len(self.transform_tp_masks)
            + len(self.transform_tp_line_masks)
            + 2,
        )
        window = self.window()
        progress = None
        can_show_counter = all(
            hasattr(window, name)
            for name in (
                "create_progress_counter",
                "update_progress_counter",
                "close_progress_counter",
            )
        )

        # create_progress_counter() calls processEvents().  Mark the renderer as
        # busy first so a repaint during popup creation uses the lightweight
        # preview instead of recursively starting another TP_mask calculation.
        self._tp_preview_progress_busy = True
        try:
            if can_show_counter:
                progress = window.create_progress_counter(
                    (
                        "Tp_mask 軽量プレビュー"
                        if use_proxy
                        else "Tp_mask クオリティプレビュー"
                    ),
                    total,
                    label,
                )

            def report(value, report_total, message):
                if progress is not None:
                    window.update_progress_counter(
                        progress, value, report_total, message
                    )

            self.transform_preview_image(
                self.transform_source,
                target_width,
                target_height,
                quality=True,
                preview_only=use_proxy,
                progress_callback=report,
            )
            if progress is not None:
                window.update_progress_counter(
                    progress,
                    total,
                    total,
                    "プレビューを更新しました",
                )
        except Exception as exc:
            self.status_message.emit(
                f"クオリティプレビューを生成できませんでした: {exc}"
            )
        finally:
            if progress is not None:
                window.close_progress_counter(progress)
            self._tp_preview_progress_busy = False
        self.update()

    def set_transform_quality(self, enabled):
        self.transform_quality = bool(enabled)
        if self.transform_quality:
            self.transform_apply_all_frames = False
        if self.transform_active:
            self.transform_quality_active = self.transform_quality
            self.transform_tp_line_colors = tuple(sorted(self.selected_used_colors()))
            if self.transform_quality_active:
                target_width = self.active_layer.image.width()
                target_height = self.active_layer.image.height()
                if self._tp_uses_proxy(target_width, target_height):
                    self._clear_tp_transform_masks(clear_proxy=False)
                else:
                    self._prepare_tp_transform_masks()
            else:
                self._clear_tp_transform_masks()
        self._invalidate_tp_preview_cache()
        if self.transform_active and self.transform_quality_active:
            self.request_quality_preview_counter(
                "クオリティ方式へ切り替えています"
            )
        self.update()

    def set_transform_line_threshold(self, value):
        self.transform_line_threshold = max(1, min(254, int(value)))
        # v0.7 caches transformed soft masks.  A line-width change only
        # reapplies the threshold and does not transform every color again.
        self._invalidate_tp_preview_cache(geometry=False)
        if (
            self.transform_active
            and self.transform_quality_active
            and not self._tp_preview_progress_busy
            and self._tp_geometry_cache_fill_overlay is not None
        ):
            target_width = self.active_layer.image.width()
            target_height = self.active_layer.image.height()
            self.transform_preview_image(
                self.transform_source,
                target_width,
                target_height,
                quality=True,
                preview_only=self._tp_uses_proxy(
                    target_width, target_height
                ),
            )
        self.update()

    def begin_transform_line_adjustment(self):
        """スライダー操作中は重いクオリティ再生成を保留する。"""
        self._transform_line_adjusting = True
        self._transform_line_preview_timer.stop()
        self._tp_preview_progress_scheduled = False

    def finish_transform_line_adjustment(self):
        """スライダーを離した時にクオリティプレビューを一度だけ更新する。"""
        was_adjusting = self._transform_line_adjusting
        self._transform_line_adjusting = False
        if (
            was_adjusting
            and self.transform_active
            and self.transform_quality_active
        ):
            # マウスリリース処理を先に完了させ、クリックが確定操作の
            # ように見えないよう少し遅らせて一度だけ生成する。
            self._transform_line_preview_timer.start()
        self.update()

    def _render_deferred_transform_line_preview(self):
        if self.transform_active and self.transform_quality_active:
            self.request_quality_preview_counter(
                "実線の太さをプレビューへ反映しています"
            )

    def set_transform_line_colors(self, colors):
        normalized = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in (colors or set())
            if rgb is not None and len(rgb) >= 3
        }
        self.selected_used_color_rgbs = normalized
        if self.transform_active:
            self.transform_tp_line_colors = tuple(sorted(normalized))
            if self.transform_quality_active:
                target_width = self.active_layer.image.width()
                target_height = self.active_layer.image.height()
                self._clear_tp_transform_masks(clear_proxy=False)
                if not self._tp_uses_proxy(target_width, target_height):
                    self._prepare_tp_transform_masks()
                self.request_quality_preview_counter(
                    "実線色を反映しています"
                )
            else:
                self._invalidate_tp_preview_cache()
            self.update()

    def apply_selection_clip(self, painter, offset_x=0.0, offset_y=0.0):
        """Apply the active selection without relying on PySide overload-specific arguments."""
        if not self.selection_polygon:
            return
        if self.selection_mask_override is not None:
            device = painter.device()
            mask = self.selection_mask_bool(
                device.width(),
                device.height(),
                int(round(offset_x)),
                int(round(offset_y)),
            )
            region = QRegion()
            for y in range(mask.shape[0]):
                row = mask[y]
                changes = np.diff(
                    np.pad(row.astype(np.int8), (1, 1))
                )
                starts = np.flatnonzero(changes == 1)
                ends = np.flatnonzero(changes == -1)
                for start, end in zip(starts, ends):
                    region += QRegion(int(start), y, int(end - start), 1)
            painter.setClipRegion(region)
            return
        polygon = QPolygonF([
            QPointF(float(point.x()) - float(offset_x), float(point.y()) - float(offset_y))
            for point in self.selection_polygon
        ])
        if len(polygon) >= 3:
            path = QPainterPath()
            path.addPolygon(polygon)
            path.closeSubpath()
            painter.setClipPath(path)

    def pressure_size_scale(self, pressure):
        """筆圧から線幅倍率だけを返す。不透明度には使用しない。"""
        if not self.pressure_enabled:
            return 1.0
        pressure = max(
            0.0,
            min(1.0, float(pressure)),
        )
        points = getattr(
            self,
            "pressure_curve_points",
            None,
        )
        if points and len(points) >= 2:
            value = _pressure_bezier_at(
                points,
                pressure,
            )
        else:
            value = pressure ** self.pressure_curve
        return (
            max(self.pressure_min, value)
            * self.pressure_max
        )

    def pressure_value(self, pressure):
        """旧呼び出し互換。返す値は線幅倍率のみ。"""
        return self.pressure_size_scale(pressure)

    def _update_stroke_region(self, a, b, width):
        """Repaint only the widget area touched by a brush segment."""
        wa = self.canvas_to_widget(a)
        wb = self.canvas_to_widget(b)
        margin = max(4, int(math.ceil(float(width) * max(self.zoom, 0.01) / 2.0)) + 3)
        dirty = QRectF(wa, wb).normalized().adjusted(
            -margin, -margin, margin, margin
        ).toAlignedRect()
        self.update(dirty)

    def draw_line(self,a,b,pressure):
        """完全な非AAマスクで、下地色と均一にRGB合成する。"""
        painter_tool = self.effective_tool()
        source_color = self.paint_source_color()
        # 筆圧はここで線幅にだけ使用する。
        width = max(
            0.5,
            self.pen_size
            * self.pressure_size_scale(pressure)
        )
        draw_width = max(0.5, float(width))
        raster_a = QPointF(float(a.x()), float(a.y()))
        raster_b = QPointF(float(b.x()), float(b.y()))

        margin = int(math.ceil(draw_width / 2.0)) + 3
        left = max(
            0,
            int(math.floor(min(a.x(), b.x()))) - margin,
        )
        top = max(
            0,
            int(math.floor(min(a.y(), b.y()))) - margin,
        )
        right = min(
            self.active_layer.image.width(),
            int(math.ceil(max(a.x(), b.x()))) + margin + 1,
        )
        bottom = min(
            self.active_layer.image.height(),
            int(math.ceil(max(a.y(), b.y()))) + margin + 1,
        )
        if right <= left or bottom <= top:
            return

        rect = QRectF(
            left,
            top,
            right - left,
            bottom - top,
        ).toAlignedRect()
        overlay = QImage(
            rect.width(),
            rect.height(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        overlay.fill(Qt.GlobalColor.transparent)

        painter = QPainter(overlay)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing,
            False,
        )
        self.apply_selection_clip(
            painter,
            rect.x(),
            rect.y(),
        )
        local_a = QPointF(
            raster_a.x() - rect.x(),
            raster_a.y() - rect.y(),
        )
        local_b = QPointF(
            raster_b.x() - rect.x(),
            raster_b.y() - rect.y(),
        )
        painter.setPen(
            QPen(
                source_color,
                draw_width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawLine(local_a, local_b)
        painter.end()

        base_image = getattr(
            self,
            "_brush_blend_base_image",
            None,
        )
        if base_image is None or base_image.isNull():
            base_image = self.active_layer.image.copy()

        sub_only = bool(
            painter_tool == "brush"
            and not getattr(self, "mask_all_enabled", True)
        )
        if sub_only:
            overlay_rgba = self._qimage_rgba_array(overlay)
            base_region = base_image.copy(
                rect.x(),
                rect.y(),
                rect.width(),
                rect.height(),
            )
            base_rgba = self._qimage_rgba_array(base_region)

            selected_rgbs = self.selected_mask_colors()
            eligible = np.zeros(
                base_rgba.shape[:2],
                dtype=bool,
            )
            if selected_rgbs:
                packed = (
                    (
                        base_rgba[:, :, 0].astype(np.uint32)
                        << 16
                    )
                    | (
                        base_rgba[:, :, 1].astype(np.uint32)
                        << 8
                    )
                    | base_rgba[:, :, 2].astype(np.uint32)
                )
                selected_values = np.fromiter(
                    (
                        (r << 16) | (g << 8) | b
                        for r, g, b in selected_rgbs
                    ),
                    dtype=np.uint32,
                    count=len(selected_rgbs),
                )
                eligible |= (
                    (base_rgba[:, :, 3] > 0)
                    & np.isin(packed, selected_values)
                )
                if self.background_mask_rgb in selected_rgbs:
                    eligible |= (
                        (base_rgba[:, :, 3] == 0)
                        | np.all(
                            base_rgba[:, :, :3] == 255,
                            axis=2,
                        )
                    )

            overlay_rgba[:, :, 3][~eligible] = 0
            overlay = self._rgba_array_to_qimage(
                overlay_rgba
            )

        # 筆圧とは無関係な、ストローク開始時の固定不透明度。
        fixed_opacity = float(
            getattr(
                self,
                "_brush_stroke_opacity",
                self.paint_opacity_value(),
            )
        )
        colors = self._blend_overlay_into_active_layer(
            overlay,
            rect.topLeft(),
            base_image=base_image,
            opacity=fixed_opacity,
            exact_colors=(source_color,),
        )
        color_set = getattr(
            self,
            "_brush_blended_colors",
            None,
        )
        if color_set is not None:
            color_set.update(colors)

        self.active_layer.has_content = True
        self._update_stroke_region(a, b, draw_width)

    @staticmethod
    def _scanline_connected_region(passable, starts):
        """Fast 4-connected flood fill using horizontal runs instead of per-pixel stacks."""
        height, width = passable.shape
        region = np.zeros((height, width), dtype=bool)
        if isinstance(starts, tuple) and len(starts) == 2 and isinstance(starts[0], (int, np.integer)):
            stack = [(int(starts[0]), int(starts[1]))]
        else:
            stack = [(int(x), int(y)) for x, y in starts]
        while stack:
            x, y = stack.pop()
            if (
                x < 0 or y < 0 or x >= width or y >= height
                or region[y, x] or not passable[y, x]
            ):
                continue
            left = x
            while left > 0 and passable[y, left - 1] and not region[y, left - 1]:
                left -= 1
            right = x
            while right + 1 < width and passable[y, right + 1] and not region[y, right + 1]:
                right += 1
            region[y, left:right + 1] = True
            for next_y in (y - 1, y + 1):
                if next_y < 0 or next_y >= height:
                    continue
                scan_x = left
                while scan_x <= right:
                    if passable[next_y, scan_x] and not region[next_y, scan_x]:
                        stack.append((scan_x, next_y))
                        scan_x += 1
                        while (
                            scan_x <= right
                            and passable[next_y, scan_x]
                            and not region[next_y, scan_x]
                        ):
                            scan_x += 1
                    scan_x += 1
        return region

    def flood_fill(self,p):
        self.push_layer_undo()
        image = self.active_layer.image.convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        width, height = image.width(), image.height()
        start_x, start_y = int(p.x()), int(p.y())
        if not (0 <= start_x < width and 0 <= start_y < height):
            if self.undo_stack:
                self.undo_stack.pop()
            return

        selection_mask = self.selection_mask_bool(width, height)
        if selection_mask is not None and not selection_mask[start_y, start_x]:
            if self.undo_stack:
                self.undo_stack.pop()
            self.status_message.emit("選択範囲の外側なので塗りを開始しませんでした。")
            return

        ptr = image.bits()
        try:
            ptr.setsize(image.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, image.bytesPerLine())
        )
        pixels = rows[:, :width * 4].reshape((height, width, 4))

        target = pixels[start_y, start_x].copy()
        replacement = self.paint_source_color()
        paint_opacity = self.paint_opacity_value()

        window = self.window()
        include_masks = bool(
            hasattr(window, "tools")
            and window.tools.bucket_include_sub.isChecked()
        )
        close_gap = bool(
            hasattr(window, "tools")
            and window.tools.bucket_close_gap.isChecked()
        )
        adjacent_fill = bool(
            not hasattr(window, "tools")
            or window.tools.bucket_adjacent.isChecked()
        )

        pseudo_background = (
            (pixels[:, :, 3] == 0)
            | np.all(pixels[:, :, :3] == 255, axis=2)
        )
        target_is_background = bool(
            int(target[3]) == 0
            or (
                int(target[0]) == 255
                and int(target[1]) == 255
                and int(target[2]) == 255
            )
        )
        if target_is_background:
            target_mask = pseudo_background.copy()
            same_color_mask = pseudo_background.copy()
        else:
            target_mask = (
                (pixels[:, :, 3] > 0)
                & np.all(pixels[:, :, :3] == target[:3], axis=2)
            )
            same_color_mask = target_mask.copy()
        if selection_mask is not None:
            target_mask &= selection_mask
            same_color_mask &= selection_mask

        def dilate(mask, iterations):
            result = mask.copy()
            for _ in range(iterations):
                source = result.copy()
                result[1:, :] |= source[:-1, :]
                result[:-1, :] |= source[1:, :]
                result[:, 1:] |= source[:, :-1]
                result[:, :-1] |= source[:, 1:]
                result[1:, 1:] |= source[:-1, :-1]
                result[:-1, :-1] |= source[1:, 1:]
                result[1:, :-1] |= source[:-1, 1:]
                result[:-1, 1:] |= source[1:, :-1]
            return result

        def erode(mask, iterations):
            result = mask.copy()
            for _ in range(iterations):
                padded = np.pad(
                    result,
                    ((1, 1), (1, 1)),
                    mode="constant",
                    constant_values=False,
                )
                result = (
                    padded[0:-2, 0:-2]
                    & padded[0:-2, 1:-1]
                    & padded[0:-2, 2:]
                    & padded[1:-1, 0:-2]
                    & padded[1:-1, 1:-1]
                    & padded[1:-1, 2:]
                    & padded[2:, 0:-2]
                    & padded[2:, 1:-1]
                    & padded[2:, 2:]
                )
            return result

        if adjacent_fill:
            boundary = ~target_mask
            gap_recovery_mask = None
            if close_gap:
                gap_radius = max(
                    1,
                    int(window.tools.bucket_gap_width.value())
                    if hasattr(window, "tools")
                    else 4,
                )
                virtual_boundary = erode(
                    dilate(boundary, gap_radius),
                    gap_radius,
                )
                # 仮想境界で漏れだけを止め、元画像では塗れる細い領域を
                # 最終描画時に回収する。線そのものは target_mask 外なので
                # 回収対象にはならない。
                gap_recovery_mask = virtual_boundary & target_mask
            else:
                virtual_boundary = boundary

            passable = target_mask & ~virtual_boundary
            if selection_mask is not None:
                passable &= selection_mask
            passable[start_y, start_x] = bool(target_mask[start_y, start_x])
            region = self._scanline_connected_region(
                passable, (start_x, start_y)
            )

            if not np.any(region):
                if self.undo_stack:
                    self.undo_stack.pop()
                return

            require_closed = bool(
                hasattr(window, "tools")
                and window.tools.bucket_require_closed.isChecked()
            )
            # A selection itself acts as a closed boundary.
            if selection_mask is None and require_closed and (
                np.any(region[0, :]) or np.any(region[-1, :])
                or np.any(region[:, 0]) or np.any(region[:, -1])
            ):
                if self.undo_stack:
                    self.undo_stack.pop()
                self.status_message.emit(
                    "領域がキャンバス端まで開いているため、塗りを開始しませんでした。"
                )
                return

            if close_gap and gap_recovery_mask is not None:
                touching = dilate(region, 1) & gap_recovery_mask
                start_y_values, start_x_values = np.nonzero(touching)
                if start_x_values.size:
                    recovered = self._scanline_connected_region(
                        gap_recovery_mask,
                        zip(start_x_values.tolist(), start_y_values.tolist()),
                    )
                    region |= recovered
        else:
            # 「隣接」OFFでは、クリック位置と同じRGBAを持つ全ピクセルを対象にする。
            region = same_color_mask.copy()

        final_region = region.copy()
        if include_masks:
            selected_colors = self.selected_used_colors()
            rgb = pixels[:, :, :3].astype(np.int32)
            opaque_pixels = pixels[:, :, 3] > 0
            mask_family = np.zeros((height, width), dtype=bool)
            for selected_color in selected_colors:
                target_rgb = np.array(selected_color, dtype=np.int32)
                delta = rgb - target_rgb
                mask_family |= (
                    opaque_pixels
                    & (np.sum(delta * delta, axis=2) <= 56 * 56)
                )
            if self.background_mask_rgb in selected_colors:
                mask_family |= pseudo_background
            if selection_mask is not None:
                mask_family &= selection_mask

            if adjacent_fill:
                adjacent = np.zeros_like(region)
                adjacent[1:, :] |= region[:-1, :]
                adjacent[:-1, :] |= region[1:, :]
                adjacent[:, 1:] |= region[:, :-1]
                adjacent[:, :-1] |= region[:, 1:]
                seed_points = np.argwhere(mask_family & adjacent)
                if seed_points.size:
                    starts = [
                        (int(point[1]), int(point[0]))
                        for point in seed_points
                    ]
                    final_region |= self._scanline_connected_region(
                        mask_family, starts
                    )
            else:
                # 非隣接モードでは、選択された使用色もレイヤー全体から一括対象にする。
                final_region |= mask_family

        if selection_mask is not None:
            final_region &= selection_mask
        if not np.any(final_region):
            if self.undo_stack:
                self.undo_stack.pop()
            return

        ys, xs = np.nonzero(final_region)
        if len(xs) == 0:
            if self.undo_stack:
                self.undo_stack.pop()
            return

        source_rgb = np.asarray(
            [
                replacement.red(),
                replacement.green(),
                replacement.blue(),
            ],
            dtype=np.uint8,
        )

        if paint_opacity >= 0.999999:
            # 100%は正規RGBをそのまま書き込み、近似色を生成しない。
            pixels[ys, xs, :3] = source_rgb
            written_rgb = source_rgb.reshape(
                (1, 3)
            )
        else:
            # 不透明度チェックONかつ100%未満の場合だけ通常混色。
            destination_rgb = pixels[
                ys,
                xs,
                :3,
            ].astype(np.float32)
            transparent_pixels = (
                pixels[ys, xs, 3] == 0
            )
            destination_rgb[
                transparent_pixels
            ] = 255.0
            source_float = source_rgb.astype(
                np.float32
            )
            written_rgb = np.clip(
                np.rint(
                    destination_rgb
                    * (1.0 - paint_opacity)
                    + source_float[None, :]
                    * paint_opacity
                ),
                0,
                255,
            ).astype(np.uint8)
            pixels[ys, xs, :3] = written_rgb

        pixels[ys, xs, 3] = 255

        self.active_layer.image = image.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        self.active_layer.has_content = True
        self._emit_actual_paint_colors(
            (
                (
                    int(source_rgb[0]),
                    int(source_rgb[1]),
                    int(source_rgb[2]),
                ),
            )
            if paint_opacity >= 0.999999
            else np.unique(written_rgb, axis=0)
        )
        self.cellChanged.emit(self.current_frame, self.active_layer_index)
        self.update()

    def auto_select_region(self, point, modifiers=Qt.KeyboardModifier.NoModifier):
        """バケツと同じ連続領域判定で選択マスクを作成・加減算する。"""
        image = self.active_layer.image.convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        width, height = image.width(), image.height()
        start_x, start_y = int(point.x()), int(point.y())
        if not (0 <= start_x < width and 0 <= start_y < height):
            return False

        ptr = image.bits()
        try:
            ptr.setsize(image.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, image.bytesPerLine())
        )
        pixels = rows[:, :width * 4].reshape((height, width, 4))
        target = pixels[start_y, start_x]
        pseudo_background = (
            (pixels[:, :, 3] == 0)
            | np.all(pixels[:, :, :3] == 255, axis=2)
        )
        target_is_background = bool(
            int(target[3]) == 0
            or np.all(target[:3] == 255)
        )
        target_mask = (
            pseudo_background.copy()
            if target_is_background
            else (
                (pixels[:, :, 3] > 0)
                & np.all(pixels[:, :, :3] == target[:3], axis=2)
            )
        )

        window = self.window()
        tools = getattr(window, "tools", None)
        adjacent = bool(
            tools is None or tools.bucket_adjacent.isChecked()
        )
        close_gap = bool(
            tools is not None
            and tools.bucket_close_gap.isChecked()
            and adjacent
        )

        if adjacent:
            passable = target_mask
            if close_gap:
                radius = max(1, int(tools.bucket_gap_width.value()))
                boundary = ~target_mask
                expanded = boundary.copy()
                for _ in range(radius):
                    source = expanded.copy()
                    expanded[1:, :] |= source[:-1, :]
                    expanded[:-1, :] |= source[1:, :]
                    expanded[:, 1:] |= source[:, :-1]
                    expanded[:, :-1] |= source[:, 1:]
                    expanded[1:, 1:] |= source[:-1, :-1]
                    expanded[:-1, :-1] |= source[1:, 1:]
                    expanded[1:, :-1] |= source[:-1, 1:]
                    expanded[:-1, 1:] |= source[1:, :-1]
                virtual_boundary = expanded
                for _ in range(radius):
                    padded = np.pad(
                        virtual_boundary,
                        ((1, 1), (1, 1)),
                        mode="constant",
                        constant_values=False,
                    )
                    virtual_boundary = (
                        padded[0:-2, 0:-2]
                        & padded[0:-2, 1:-1]
                        & padded[0:-2, 2:]
                        & padded[1:-1, 0:-2]
                        & padded[1:-1, 1:-1]
                        & padded[1:-1, 2:]
                        & padded[2:, 0:-2]
                        & padded[2:, 1:-1]
                        & padded[2:, 2:]
                    )
                passable = target_mask & ~virtual_boundary
                passable[start_y, start_x] = bool(
                    target_mask[start_y, start_x]
                )
            region = self._scanline_connected_region(
                passable, (start_x, start_y)
            )
            require_closed = bool(
                tools is not None
                and tools.bucket_require_closed.isChecked()
            )
            if require_closed and (
                np.any(region[0, :]) or np.any(region[-1, :])
                or np.any(region[:, 0]) or np.any(region[:, -1])
            ):
                self.status_message.emit(
                    "領域がキャンバス端まで開いているため選択しませんでした。"
                )
                return False
        else:
            region = target_mask.copy()

        current = self.selection_mask_bool(width, height)
        if current is None:
            current = np.zeros((height, width), dtype=bool)
        if modifiers & Qt.KeyboardModifier.AltModifier:
            combined = current & ~region
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            combined = current | region
        else:
            combined = region

        if not np.any(combined):
            self.clear_selection()
            return True

        contour_owner = self.window()
        contours = contour_owner._mask_contours(combined)
        contour = contour_owner._largest_contour(contours)
        if not contour:
            return False
        ys, xs = np.nonzero(combined)
        self.selection_polygon = contour
        self.selection_mask_override = combined
        self.selection_outline_polygons = contours
        self.selection_mask_rect = QRectF(
            int(xs.min()), int(ys.min()),
            int(xs.max() - xs.min() + 1),
            int(ys.max() - ys.min() + 1),
        )
        self._selection_fade_started = time.monotonic()
        self.lasso = []
        self.rect_start = None
        self.rect_end = None
        self.selectionChanged.emit()
        self.update()
        return True
    def init_mesh(self):
        w,h=workspace_size(); self.mesh_original=self.active_layer.image.copy(); self.mesh_points=[]
        for gy in range(self.mesh_grid):
            for gx in range(self.mesh_grid): self.mesh_points.append(QPointF(gx*w/(self.mesh_grid-1),gy*h/(self.mesh_grid-1)))
    def nearest_mesh(self,p):
        if not self.mesh_points:return -1
        d=[(q.x()-p.x())**2+(q.y()-p.y())**2 for q in self.mesh_points]; i=int(np.argmin(d)); return i if d[i]<(40/max(self.zoom,.01))**2 else -1
    def commit_mesh(self):
        if self.mesh_original is None or not self.mesh_points:return
        self.apply_mesh_release()

    def apply_mesh_release(self):
        if self.mesh_original is None:return
        img=self.mesh_original.convertToFormat(QImage.Format.Format_RGBA8888); w,h=img.width(),img.height()
        ptr=img.bits(); arr=np.frombuffer(ptr,dtype=np.uint8,count=w*h*4).reshape((h,w,4)).copy()
        yy,xx=np.mgrid[0:h,0:w].astype(np.float32); dx=np.zeros_like(xx);dy=np.zeros_like(yy);ws=np.zeros_like(xx)
        originals=[]
        for gy in range(self.mesh_grid):
            for gx in range(self.mesh_grid): originals.append(QPointF(gx*w/(self.mesh_grid-1),gy*h/(self.mesh_grid-1)))
        radius=max(w,h)/2.0
        for o,n in zip(originals,self.mesh_points):
            dist2=(xx-o.x())**2+(yy-o.y())**2; weight=np.exp(-dist2/(2*(radius/2.5)**2)); dx+=(n.x()-o.x())*weight;dy+=(n.y()-o.y())*weight;ws+=weight
        sx=np.clip(np.rint(xx-dx/np.maximum(ws,.001)),0,w-1).astype(np.int32); sy=np.clip(np.rint(yy-dy/np.maximum(ws,.001)),0,h-1).astype(np.int32)
        out=arr[sy,sx]; q=QImage(out.data,w,h,out.strides[0],QImage.Format.Format_RGBA8888).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self.active_layer.image=q; self.active_layer.has_content=True; self.mesh_original=None; self.mesh_points=[]; self.cellChanged.emit(self.current_frame,self.active_layer_index); self.update()
    def cancel_mesh(self):
        self.mesh_original=None; self.mesh_points=[]; self.mesh_active=-1; self.update()

    def flip_active_layer(self, horizontal=True):
        if self.active_layer.is_paper:
            return
        self.ensure_editable_key()
        self.push_layer_undo()
        self.active_layer.image = self.active_layer.image.mirrored(horizontal, not horizontal)
        self.active_layer.has_content = True
        self.cellChanged.emit(self.current_frame, self.active_layer_index)
        self.update()

    def move_timeline_cell(self, source_frame, source_layer, destination_frame, destination_layer):
        source_frame = int(source_frame)
        destination_frame = int(destination_frame)
        source_layer = int(source_layer)
        destination_layer = int(destination_layer)
        if not (0 <= source_frame < len(self.frames)) or destination_frame < 0:
            return False
        if not (0 <= source_layer < len(self.frames[source_frame].layers)):
            return False
        source = self.frames[source_frame].layers[source_layer]
        source_is_blank = bool(
            getattr(source, "is_blank_key", False)
        )
        if not (source.has_content or source_is_blank):
            return False

        self.push_doc_undo()
        moving = source.clone()
        moving.exposure = max(1, int(source.exposure))

        # 同一レイヤー内で後方へ移動するときは、元セルを抜いた分だけ座標を補正。
        source_span = max(1, int(source.exposure))
        self._clear_timeline_layer_cell(source)

        # 隣接する「●ーー」の直後のキーを移動した場合、抜けた位置は
        # 空セルにせず直前キーの保持区間（ー）として埋める。
        previous_key = None
        for index in range(source_frame - 1, -1, -1):
            candidate = self.frames[index].layers[source_layer]
            if (
                candidate.has_content
                or getattr(candidate, "is_blank_key", False)
            ):
                previous_key = index
                break
        if previous_key is not None:
            previous = self.frames[previous_key].layers[source_layer]
            previous_end = previous_key + max(1, int(previous.exposure))
            if previous_end >= source_frame:
                fill_until = source_frame + source_span
                if (
                    source_layer == destination_layer
                    and destination_frame > source_frame
                ):
                    # キーを右へずらした距離全体を直前キーの保持で埋める。
                    # 移動元の1コマ分だけ延長すると途中が未使用になる。
                    fill_until = max(fill_until, destination_frame)
                previous.exposure = max(
                    int(previous.exposure),
                    fill_until - previous_key,
                )

        self._ensure_frame_count(destination_frame + 1)
        if not (0 <= destination_layer < len(self.frames[destination_frame].layers)):
            return False

        occupying_block = self.timeline_block_at(
            destination_frame,
            destination_layer,
        )
        if occupying_block is not None:
            _occupying_kind, key, _occupying_exposure = (
                occupying_block
            )
            if int(key) != source_frame:
                occupied = self.frames[key].layers[
                    destination_layer
                ]
                if key < destination_frame:
                    # 保持区間上へ落とした場合は落下位置で切る。
                    occupied.exposure = max(
                        1,
                        destination_frame - key,
                    )
                elif key == destination_frame:
                    # 既存の●／○以降を右へ1セル送る。
                    keys = [
                        index
                        for index in range(len(self.frames))
                        if (
                            (
                                self.frames[index]
                                .layers[destination_layer]
                                .has_content
                            )
                            or getattr(
                                self.frames[index]
                                .layers[destination_layer],
                                "is_blank_key",
                                False,
                            )
                        )
                        and index >= destination_frame
                    ]
                    self._ensure_frame_count(
                        len(self.frames) + 1
                    )
                    for index in reversed(keys):
                        target = index + 1
                        self._ensure_frame_count(target + 1)
                        src = self.frames[index].layers[
                            destination_layer
                        ]
                        dst = self.frames[target].layers[
                            destination_layer
                        ]
                        copied = src.clone()
                        dst.image = copied.image.copy()
                        dst.has_content = bool(
                            copied.has_content
                        )
                        dst.is_blank_key = bool(
                            getattr(
                                copied,
                                "is_blank_key",
                                False,
                            )
                        )
                        dst.exposure = int(copied.exposure)
                        dst.visible = bool(copied.visible)
                        dst.opacity = float(copied.opacity)
                        dst.alpha_locked = bool(
                            copied.alpha_locked
                        )
                        dst.color_filter_enabled = bool(
                            copied.color_filter_enabled
                        )
                        dst.color_filter_rgb = (
                            tuple(copied.color_filter_rgb)
                            if copied.color_filter_rgb is not None
                            else None
                        )
                        dst.sequence_number = copied.sequence_number
                        dst.sequence_only = bool(copied.sequence_only)
                        self._clear_timeline_layer_cell(src)

        destination = self.frames[destination_frame].layers[destination_layer]
        destination.image = (
            moving.image.copy()
            if not source_is_blank
            else blank_image()
        )
        destination.has_content = not source_is_blank
        destination.is_blank_key = source_is_blank
        destination.sequence_number = (
            None if source_is_blank else moving.sequence_number
        )
        destination.sequence_only = bool(moving.sequence_only)
        destination.exposure = moving.exposure
        destination.visible = moving.visible
        destination.opacity = moving.opacity
        destination.color_filter_enabled = moving.color_filter_enabled
        destination.color_filter_rgb = (
            tuple(moving.color_filter_rgb)
            if moving.color_filter_rgb is not None else None
        )
        self.current_frame = destination_frame
        self.active_layer_index = destination_layer
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def _read_image_file(self, path):
        path = str(path)
        image = QImage()
        errors = []
        reader = QImageReader(path)
        reader.setAutoTransform(False)
        try:
            reader.setDecideFormatFromContent(True)
        except AttributeError:
            pass
        declared_size = reader.size()
        if declared_size.isValid():
            declared_width = int(declared_size.width())
            declared_height = int(declared_size.height())
            if (
                declared_width < 1
                or declared_height < 1
                or declared_width > MAX_IMAGE_DIMENSION
                or declared_height > MAX_IMAGE_DIMENSION
                or declared_width * declared_height > MAX_SINGLE_IMAGE_PIXELS
            ):
                return None, (
                    "画像サイズが上限を超えています。\n"
                    f"{declared_width} × {declared_height}px"
                )
        image = reader.read()
        if image.isNull():
            errors.append("Qt: " + (reader.errorString() or "画像データを解釈できませんでした"))
        if image.isNull() and PILImage is not None:
            try:
                with PILImage.open(path) as pil:
                    pil_width, pil_height = map(int, pil.size)
                    if (
                        pil_width < 1
                        or pil_height < 1
                        or pil_width > MAX_IMAGE_DIMENSION
                        or pil_height > MAX_IMAGE_DIMENSION
                        or pil_width * pil_height > MAX_SINGLE_IMAGE_PIXELS
                    ):
                        raise ValueError(
                            "画像サイズが上限を超えています。"
                            f" ({pil_width} × {pil_height}px)"
                        )
                    pil.load()
                    pil = pil.convert("RGBA")
                    raw = pil.tobytes("raw", "RGBA")
                    converted = QImage(raw, pil.width, pil.height, pil.width * 4, QImage.Format.Format_RGBA8888)
                    image = converted.copy()
            except Exception as exc:
                errors.append(f"Pillow: {exc}")
        if image.isNull():
            suffix = Path(path).suffix.lower() or "拡張子なし"
            return None, f"形式: {suffix}\n" + "\n".join(errors)
        return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied), ""

    @staticmethod
    def _natural_path_key(path):
        import re
        return [int(part) if part.isdigit() else part.lower()
                for part in re.split(r"(\d+)", Path(path).name)]

    def _ensure_frame_count(self, count):
        if count <= len(self.frames):
            return
        names = [layer.name for layer in self.frames[0].layers]
        template = self.frames[0].layers
        while len(self.frames) < count:
            frame = make_frame(names)
            for index, layer in enumerate(frame.layers):
                layer.visible = template[index].visible
                layer.opacity = template[index].opacity
                layer.alpha_locked = template[index].alpha_locked
            self.frames.append(frame)

    def _place_imported_image(self, image, target):
        x = OUTSIDE_MARGIN + (CANVAS_WIDTH - image.width()) // 2
        y = OUTSIDE_MARGIN + (CANVAS_HEIGHT - image.height()) // 2
        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(QPoint(x, y), image)
        painter.end()

    def _make_white_transparent(self, image):
        """Convert exact #FFFFFF pixels to fully transparent before placement."""
        if image is None or image.isNull():
            return image
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = rgba.width(), rgba.height()
        if width <= 0 or height <= 0:
            return rgba

        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, rgba.bytesPerLine())
        )
        pixels = rows[:, :width * 4].reshape((height, width, 4))
        white = (
            (pixels[:, :, 0] == 255)
            & (pixels[:, :, 1] == 255)
            & (pixels[:, :, 2] == 255)
            & (pixels[:, :, 3] != 0)
        )
        pixels[:, :, 3][white] = 0
        return rgba.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    @classmethod
    def image_alpha_statistics(cls, image):
        """透明・半透明・不透明画素数を返す。"""
        if image is None or image.isNull():
            return {
                "transparent": 0,
                "semi_transparent": 0,
                "opaque": 0,
            }
        rgba = cls._qimage_rgba_array(image)
        if rgba.size == 0:
            return {
                "transparent": 0,
                "semi_transparent": 0,
                "opaque": 0,
            }
        alpha = rgba[:, :, 3]
        return {
            "transparent": int(np.count_nonzero(alpha == 0)),
            "semi_transparent": int(
                np.count_nonzero((alpha > 0) & (alpha < 255))
            ),
            "opaque": int(np.count_nonzero(alpha == 255)),
        }

    @classmethod
    def binarize_alpha_for_pixel_art(
        cls,
        image,
        alpha_threshold=128,
    ):
        """半透明を完全透明／完全不透明へ二値化する。"""
        if image is None or image.isNull():
            return QImage()

        rgba = cls._qimage_rgba_array(image)
        if rgba.size == 0:
            return image.copy()

        threshold = max(
            1,
            min(254, int(alpha_threshold)),
        )
        alpha = rgba[:, :, 3]
        keep = alpha >= threshold

        result = np.zeros_like(rgba)
        result_rgb = result[:, :, :3]
        source_rgb = rgba[:, :, :3]
        result_rgb[keep] = source_rgb[keep]
        result[:, :, 3][keep] = 255
        return cls._rgba_array_to_qimage(result)

    @classmethod
    def opaque_rgb_color_count(cls, image):
        """透明画素を除外したRGB色数を返す。"""
        if image is None or image.isNull():
            return 0
        rgba = cls._qimage_rgba_array(image)
        if rgba.size == 0:
            return 0
        visible = rgba[:, :, 3] > 0
        if not np.any(visible):
            return 0
        rgb = rgba[:, :, :3][visible].astype(np.uint32)
        packed = (
            (rgb[:, 0] << 16)
            | (rgb[:, 1] << 8)
            | rgb[:, 2]
        )
        return int(np.unique(packed).size)

    @classmethod
    def detect_opaque_border_background(cls, image):
        """外周につながる不透明な明色背景を検出する。"""
        if image is None or image.isNull():
            return None

        rgba = cls._qimage_rgba_array(image)
        if rgba.size == 0:
            return None

        alpha = rgba[:, :, 3]
        # 半透明・透明を含む画像は従来のアルファ二値化を使う。
        if np.any(alpha < 250):
            return None

        rgb = rgba[:, :, :3]
        height, width = rgb.shape[:2]
        if width <= 0 or height <= 0:
            return None

        border_parts = [
            rgb[0, :, :],
            rgb[height - 1, :, :],
        ]
        if height > 2:
            border_parts.extend([
                rgb[1:height - 1, 0, :],
                rgb[1:height - 1, width - 1, :],
            ])
        border = np.concatenate(border_parts, axis=0)
        if border.size == 0:
            return None

        colors, counts = np.unique(
            border.astype(np.uint8),
            axis=0,
            return_counts=True,
        )
        dominant = colors[int(np.argmax(counts))].astype(np.int32)

        # 白～明るい無彩色背景だけを自動背景として扱う。
        if int(dominant.min()) < 218:
            return None
        if int(dominant.max() - dominant.min()) > 24:
            return None

        delta = border.astype(np.int32) - dominant[None, :]
        near = np.sum(delta * delta, axis=1) <= (26 * 26 * 3)
        if float(np.count_nonzero(near)) / max(1, len(border)) < 0.52:
            return None

        return tuple(int(value) for value in dominant)

    @classmethod
    def _local_color_variation(cls, rgb):
        """上下左右との差が大きい境界画素を抽出する。"""
        source = rgb.astype(np.int16)
        variation = np.zeros(source.shape[:2], dtype=np.int16)

        if source.shape[0] > 1:
            vertical = np.max(
                np.abs(source[1:, :, :] - source[:-1, :, :]),
                axis=2,
            )
            variation[1:, :] = np.maximum(
                variation[1:, :], vertical
            )
            variation[:-1, :] = np.maximum(
                variation[:-1, :], vertical
            )

        if source.shape[1] > 1:
            horizontal = np.max(
                np.abs(source[:, 1:, :] - source[:, :-1, :]),
                axis=2,
            )
            variation[:, 1:] = np.maximum(
                variation[:, 1:], horizontal
            )
            variation[:, :-1] = np.maximum(
                variation[:, :-1], horizontal
            )

        return variation

    @classmethod
    def estimate_mixed_boundary_pixels(
        cls,
        image,
        background_rgb=None,
    ):
        """白背景上のアンチエイリアス境界のおおよその画素数。"""
        if image is None or image.isNull():
            return 0

        rgba = cls._qimage_rgba_array(image)
        if rgba.size == 0:
            return 0

        rgb = rgba[:, :, :3]
        alpha = rgba[:, :, 3]
        variation = cls._local_color_variation(rgb)
        candidate = (alpha >= 250) & (variation >= 10)

        if background_rgb is not None:
            background = np.asarray(
                background_rgb,
                dtype=np.int16,
            )
            delta = rgb.astype(np.int16) - background
            distance = np.sqrt(
                np.sum(
                    delta.astype(np.int32)
                    * delta.astype(np.int32),
                    axis=2,
                )
            )
            # 背景そのものと十分離れた内部色は除き、
            # 背景との混合が疑われる帯域を数える。
            candidate &= (distance > 5.0) & (distance < 190.0)

        return int(np.count_nonzero(candidate))

    @staticmethod
    def _median_cut_palette_from_samples(
        sample,
        color_count,
    ):
        """頻度付きMedian-Cutで代表色を作る。"""
        color_count = max(1, int(color_count))
        sample = np.asarray(
            sample,
            dtype=np.uint8,
        ).reshape((-1, 3))
        if sample.size == 0:
            return np.zeros((0, 3), dtype=np.uint8)

        colors, counts = np.unique(
            sample,
            axis=0,
            return_counts=True,
        )
        if len(colors) <= color_count:
            return colors.astype(np.uint8, copy=True)

        colors_i32 = colors.astype(np.int32)
        counts_i64 = counts.astype(np.int64)
        boxes = [np.arange(len(colors_i32), dtype=np.int32)]

        while len(boxes) < color_count:
            best_box_index = -1
            best_score = -1
            best_axis = 0

            for box_index, indices in enumerate(boxes):
                if len(indices) <= 1:
                    continue
                box_colors = colors_i32[indices]
                ranges = (
                    box_colors.max(axis=0)
                    - box_colors.min(axis=0)
                )
                axis = int(np.argmax(ranges))
                population = int(counts_i64[indices].sum())
                score = int(ranges[axis]) * max(1, population)
                if score > best_score:
                    best_score = score
                    best_box_index = box_index
                    best_axis = axis

            if best_box_index < 0:
                break

            indices = boxes.pop(best_box_index)
            order = np.argsort(
                colors_i32[indices, best_axis],
                kind="stable",
            )
            ordered = indices[order]
            ordered_counts = counts_i64[ordered]
            cumulative = np.cumsum(ordered_counts)
            half = cumulative[-1] / 2.0
            split = int(np.searchsorted(cumulative, half)) + 1
            split = max(1, min(len(ordered) - 1, split))
            boxes.append(ordered[:split])
            boxes.append(ordered[split:])

        palette = []
        for indices in boxes:
            weights = counts_i64[indices].astype(np.float64)
            total = max(1.0, float(weights.sum()))
            averaged = (
                colors_i32[indices].astype(np.float64)
                * weights[:, None]
            ).sum(axis=0) / total
            palette.append(
                np.clip(
                    np.rint(averaged),
                    0,
                    255,
                ).astype(np.uint8)
            )

        return np.asarray(palette, dtype=np.uint8)

    @classmethod
    def _surface_guided_palette_labels(
        cls,
        rgb,
        visible,
        palette,
        spatial_refinement=True,
    ):
        """色面内部を種にし、混合境界を隣接する2色へ分割する。"""
        height, width = visible.shape
        palette_array = np.asarray(
            palette,
            dtype=np.uint8,
        ).reshape((-1, 3))
        palette_features = cls._rgb_hsv_features(
            palette_array
        )

        flat_rgb = rgb.reshape((-1, 3))
        flat_visible = visible.reshape(-1)
        nearest_labels = np.full(
            flat_visible.shape,
            -1,
            dtype=np.int16,
        )
        nearest_distance = np.full(
            flat_visible.shape,
            np.inf,
            dtype=np.float32,
        )

        visible_indices = np.flatnonzero(flat_visible)
        chunk_size = 8192
        for chunk_start in range(
            0, len(visible_indices), chunk_size
        ):
            indices = visible_indices[
                chunk_start:chunk_start + chunk_size
            ]
            pixel_features = cls._rgb_hsv_features(
                flat_rgb[indices]
            )
            distance = np.sum(
                (
                    pixel_features[:, None, :]
                    - palette_features[None, :, :]
                ) ** 2,
                axis=2,
            )
            labels = np.argmin(distance, axis=1)
            nearest_labels[indices] = labels.astype(np.int16)
            nearest_distance[indices] = distance[
                np.arange(len(indices)),
                labels,
            ]

        nearest_labels = nearest_labels.reshape(
            (height, width)
        )
        nearest_distance = nearest_distance.reshape(
            (height, width)
        )
        if not spatial_refinement:
            return nearest_labels

        variation = cls._local_color_variation(rgb)
        # 平坦な色面を確定し、境界の混合色は未確定にする。
        core = (
            visible
            & (variation <= 12)
            & (nearest_distance <= 0.12)
        )
        labels = np.full(
            (height, width),
            -1,
            dtype=np.int16,
        )
        labels[core] = nearest_labels[core]

        exactish = (
            visible
            & (labels < 0)
            & (nearest_distance <= 0.015)
        )
        labels[exactish] = nearest_labels[exactish]

        pixel_features_all = cls._rgb_hsv_features(
            rgb.reshape((-1, 3))
        ).reshape((height, width, -1))

        # 隣接する色面候補だけを比較するため、
        # 2色の混合帯は中間位置で明確に二分される。
        for _iteration in range(12):
            unresolved = visible & (labels < 0)
            if not np.any(unresolved):
                break

            candidates = []
            up = np.full_like(labels, -1)
            up[1:, :] = labels[:-1, :]
            candidates.append(up)
            down = np.full_like(labels, -1)
            down[:-1, :] = labels[1:, :]
            candidates.append(down)
            left = np.full_like(labels, -1)
            left[:, 1:] = labels[:, :-1]
            candidates.append(left)
            right = np.full_like(labels, -1)
            right[:, :-1] = labels[:, 1:]
            candidates.append(right)

            best_label = np.full_like(labels, -1)
            best_distance = np.full(
                (height, width),
                np.inf,
                dtype=np.float32,
            )

            for candidate in candidates:
                valid = unresolved & (candidate >= 0)
                if not np.any(valid):
                    continue
                candidate_features = palette_features[
                    np.maximum(candidate, 0)
                ]
                distance = np.sum(
                    (
                        pixel_features_all
                        - candidate_features
                    ) ** 2,
                    axis=2,
                )
                better = valid & (distance < best_distance)
                best_distance[better] = distance[better]
                best_label[better] = candidate[better]

            assign = unresolved & (best_label >= 0)
            if not np.any(assign):
                break
            labels[assign] = best_label[assign]

        unresolved = visible & (labels < 0)
        labels[unresolved] = nearest_labels[unresolved]
        return labels

    @staticmethod
    def _line_palette_labels(rgb, visible, palette):
        """ライン抽出用。色相よりRGB差と明度差を優先して実線を保つ。"""
        palette_array = np.asarray(palette, dtype=np.uint8).reshape((-1, 3))
        source = rgb.reshape((-1, 3)).astype(np.float32) / 255.0
        targets = palette_array.astype(np.float32) / 255.0
        source_luma = (
            source[:, 0] * 0.2126
            + source[:, 1] * 0.7152
            + source[:, 2] * 0.0722
        )
        target_luma = (
            targets[:, 0] * 0.2126
            + targets[:, 1] * 0.7152
            + targets[:, 2] * 0.0722
        )
        source_value = source.max(axis=1)
        source_saturation = (
            (source_value - source.min(axis=1))
            / np.maximum(source_value, 1e-6)
        )
        target_value = targets.max(axis=1)
        target_saturation = (
            (target_value - targets.min(axis=1))
            / np.maximum(target_value, 1e-6)
        )
        flat_visible = visible.reshape(-1)
        labels = np.full(flat_visible.shape, -1, dtype=np.int16)
        indices = np.flatnonzero(flat_visible)
        for start in range(0, len(indices), 8192):
            chunk = indices[start:start + 8192]
            rgb_distance = np.sum(
                (source[chunk, None, :] - targets[None, :, :]) ** 2,
                axis=2,
            )
            luma_distance = (
                source_luma[chunk, None] - target_luma[None, :]
            ) ** 2
            saturation_excess = np.maximum(
                target_saturation[None, :]
                - source_saturation[chunk, None]
                - 0.12,
                0.0,
            )
            distance = (
                rgb_distance
                + luma_distance * 2.4
                + saturation_excess * saturation_excess * 4.0
            )
            labels[chunk] = np.argmin(distance, axis=1).astype(np.int16)

        # 暗い実線はアンチエイリアス周辺の色相へ吸収させない。
        darkest_index = int(np.argmin(target_luma))
        darkest_luma = float(target_luma[darkest_index])
        if darkest_luma <= 0.32:
            source_luma_2d = source_luma.reshape(visible.shape)
            variation = PaintCanvas._local_color_variation(rgb)
            strong_dark_line = visible & (
                (source_luma_2d <= max(0.20, darkest_luma + 0.12))
                & ((variation >= 28) | (source_luma_2d <= 0.10))
            )
            labels.reshape(visible.shape)[strong_dark_line] = darkest_index
        return labels.reshape(visible.shape)


    @staticmethod
    def normalize_tone_curve_points(points):
        normalized = []
        try:
            iterable = list(points)
        except TypeError:
            iterable = []

        for point in iterable:
            try:
                x, y = point[:2]
                normalized.append((
                    max(0.0, min(1.0, float(x))),
                    max(0.0, min(1.0, float(y))),
                ))
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
        merged[0] = (0.0, 0.0)
        merged[-1] = (1.0, 1.0)
        return merged

    @classmethod
    def tone_curve_samples(
        cls,
        tone_curve_points,
        input_values,
    ):
        """制御点を通る形状保持型3次曲線を評価する。"""
        points = cls.normalize_tone_curve_points(
            tone_curve_points
            if tone_curve_points is not None
            else [(0.0, 0.0), (1.0, 1.0)]
        )
        xs = np.asarray(
            [point[0] for point in points],
            dtype=np.float64,
        )
        ys = np.asarray(
            [point[1] for point in points],
            dtype=np.float64,
        )
        values = np.asarray(
            input_values,
            dtype=np.float64,
        )

        if len(xs) <= 2:
            return np.clip(
                np.interp(values, xs, ys),
                0.0,
                1.0,
            )

        intervals = np.diff(xs)
        slopes = np.diff(ys) / np.maximum(
            intervals,
            1e-12,
        )
        tangents = np.zeros_like(xs)

        # 内部点はFritsch-Carlsonの重み付き調和平均。
        for index in range(1, len(xs) - 1):
            left_slope = slopes[index - 1]
            right_slope = slopes[index]
            if (
                left_slope == 0.0
                or right_slope == 0.0
                or left_slope * right_slope <= 0.0
            ):
                tangents[index] = 0.0
            else:
                left_weight = (
                    2.0 * intervals[index]
                    + intervals[index - 1]
                )
                right_weight = (
                    intervals[index]
                    + 2.0 * intervals[index - 1]
                )
                tangents[index] = (
                    left_weight + right_weight
                ) / (
                    left_weight / left_slope
                    + right_weight / right_slope
                )

        def endpoint_tangent(
            first_interval,
            second_interval,
            first_slope,
            second_slope,
        ):
            tangent = (
                (2.0 * first_interval + second_interval)
                * first_slope
                - first_interval * second_slope
            ) / max(
                first_interval + second_interval,
                1e-12,
            )
            if tangent * first_slope <= 0.0:
                return 0.0
            if (
                first_slope * second_slope < 0.0
                and abs(tangent) > abs(3.0 * first_slope)
            ):
                return 3.0 * first_slope
            return tangent

        tangents[0] = endpoint_tangent(
            intervals[0],
            intervals[1],
            slopes[0],
            slopes[1],
        )
        tangents[-1] = endpoint_tangent(
            intervals[-1],
            intervals[-2],
            slopes[-1],
            slopes[-2],
        )

        segment_indices = np.searchsorted(
            xs,
            values,
            side="right",
        ) - 1
        segment_indices = np.clip(
            segment_indices,
            0,
            len(xs) - 2,
        )
        x0 = xs[segment_indices]
        x1 = xs[segment_indices + 1]
        interval = np.maximum(x1 - x0, 1e-12)
        t = np.clip(
            (values - x0) / interval,
            0.0,
            1.0,
        )
        t2 = t * t
        t3 = t2 * t

        y0 = ys[segment_indices]
        y1 = ys[segment_indices + 1]
        m0 = tangents[segment_indices]
        m1 = tangents[segment_indices + 1]

        outputs = (
            (2.0 * t3 - 3.0 * t2 + 1.0) * y0
            + (t3 - 2.0 * t2 + t) * interval * m0
            + (-2.0 * t3 + 3.0 * t2) * y1
            + (t3 - t2) * interval * m1
        )
        return np.clip(outputs, 0.0, 1.0)

    @classmethod
    def tone_curve_lut(cls, tone_curve_points=None):
        inputs = (
            np.arange(256, dtype=np.float64) / 255.0
        )
        outputs = cls.tone_curve_samples(
            tone_curve_points,
            inputs,
        )
        return np.clip(
            np.rint(outputs * 255.0),
            0,
            255,
        ).astype(np.uint8)

    @classmethod
    def apply_tone_curve(
        cls,
        image,
        tone_curve_points=None,
        background_rgb=None,
    ):
        """256段階LUTだけで高速に複数点トーンカーブを適用する。"""
        if image is None or image.isNull():
            return image

        points = cls.normalize_tone_curve_points(
            tone_curve_points
            if tone_curve_points is not None
            else [(0.0, 0.0), (1.0, 1.0)]
        )
        if points == [(0.0, 0.0), (1.0, 1.0)]:
            return image.copy()

        rgba = cls._qimage_rgba_array(image)
        if rgba.size == 0:
            return image.copy()

        lut = cls.tone_curve_lut(points)
        result = rgba.copy()
        result[:, :, :3] = lut[result[:, :, :3]]

        # 白背景の完全な白は端点固定でも変化しないが、
        # 自動検出した背景色も念のためそのまま保持する。
        if background_rgb is not None:
            background = np.asarray(
                background_rgb,
                dtype=np.int16,
            )
            difference = (
                rgba[:, :, :3].astype(np.int16)
                - background
            )
            near_background = np.sum(
                difference.astype(np.int32)
                * difference.astype(np.int32),
                axis=2,
            ) <= (5 * 5 * 3)
            result[:, :, :3][near_background] = (
                np.asarray(background_rgb, dtype=np.uint8)
            )

        return cls._rgba_array_to_qimage(result)

    @staticmethod
    def _rgb_hsv_features(rgb):
        """近い色相を近いベクトルへ写像する。"""
        values = np.asarray(
            rgb,
            dtype=np.float32,
        ).reshape((-1, 3)) / 255.0
        maximum = values.max(axis=1)
        minimum = values.min(axis=1)
        delta = maximum - minimum

        saturation = np.divide(
            delta,
            np.maximum(maximum, 1e-6),
        )
        hue = np.zeros_like(maximum)
        chromatic = delta > 1e-6

        red_mask = chromatic & (
            values[:, 0] >= values[:, 1]
        ) & (
            values[:, 0] >= values[:, 2]
        )
        green_mask = chromatic & ~red_mask & (
            values[:, 1] >= values[:, 2]
        )
        blue_mask = chromatic & ~red_mask & ~green_mask

        hue[red_mask] = (
            (values[red_mask, 1] - values[red_mask, 2])
            / delta[red_mask]
        ) % 6.0
        hue[green_mask] = (
            (values[green_mask, 2] - values[green_mask, 0])
            / delta[green_mask]
        ) + 2.0
        hue[blue_mask] = (
            (values[blue_mask, 0] - values[blue_mask, 1])
            / delta[blue_mask]
        ) + 4.0
        hue = (hue / 6.0) % 1.0

        angle = hue * (2.0 * math.pi)
        hue_strength = np.power(saturation, 0.72)
        return np.column_stack([
            np.cos(angle) * hue_strength * 2.35,
            np.sin(angle) * hue_strength * 2.35,
            saturation * 0.45,
            maximum * 0.52,
        ]).astype(np.float32)

    @classmethod
    def _hue_cluster_palette_from_samples(
        cls,
        sample,
        color_count,
    ):
        """色相を主軸に、近い色を同じ原色面へまとめる。"""
        sample = np.asarray(
            sample,
            dtype=np.uint8,
        ).reshape((-1, 3))
        color_count = max(1, int(color_count))
        if sample.size == 0:
            return np.zeros((0, 3), dtype=np.uint8)

        colors, counts = np.unique(
            sample,
            axis=0,
            return_counts=True,
        )
        if len(colors) <= color_count:
            return colors.astype(np.uint8, copy=True)

        features = cls._rgb_hsv_features(colors)
        weights = counts.astype(np.float64)

        first_index = int(np.argmax(weights))
        centers = [features[first_index].copy()]
        minimum_distance = np.sum(
            (features - centers[0]) ** 2,
            axis=1,
        )

        while len(centers) < color_count:
            score = minimum_distance * (
                1.0 + np.log1p(weights)
            )
            next_index = int(np.argmax(score))
            centers.append(features[next_index].copy())
            distance = np.sum(
                (features - centers[-1]) ** 2,
                axis=1,
            )
            minimum_distance = np.minimum(
                minimum_distance,
                distance,
            )

        centers = np.asarray(centers, dtype=np.float32)
        labels = np.zeros(len(colors), dtype=np.int32)

        for _iteration in range(12):
            distance = np.sum(
                (
                    features[:, None, :]
                    - centers[None, :, :]
                ) ** 2,
                axis=2,
            )
            new_labels = np.argmin(distance, axis=1)
            if np.array_equal(new_labels, labels) and _iteration > 0:
                break
            labels = new_labels

            for cluster_index in range(len(centers)):
                members = labels == cluster_index
                if not np.any(members):
                    farthest = int(
                        np.argmax(np.min(distance, axis=1))
                    )
                    centers[cluster_index] = features[farthest]
                    continue
                cluster_weights = weights[members]
                centers[cluster_index] = np.average(
                    features[members],
                    axis=0,
                    weights=cluster_weights,
                )

        palette = []
        for cluster_index in range(len(centers)):
            members = labels == cluster_index
            if not np.any(members):
                continue
            member_indices = np.flatnonzero(members)
            cluster_weights = weights[members]
            rgb_center = np.average(
                colors[members].astype(np.float64),
                axis=0,
                weights=cluster_weights,
            )
            feature_distance = np.sum(
                (
                    features[members]
                    - centers[cluster_index][None, :]
                ) ** 2,
                axis=1,
            )
            rgb_distance = np.sum(
                (
                    colors[members].astype(np.float64)
                    - rgb_center[None, :]
                ) ** 2,
                axis=1,
            ) / (255.0 * 255.0 * 3.0)
            # RGB平均色を新しく作ると、異なる色相の中間色へ変化してしまう。
            # 色相クラスタ中心に近く、かつ画像内で支持の多い実在色を代表にする。
            frequency_bonus = 1.0 + 0.12 * np.log1p(cluster_weights)
            score = (
                feature_distance + rgb_distance * 0.08
            ) / frequency_bonus
            representative_index = member_indices[int(np.argmin(score))]
            palette.append(
                colors[representative_index].astype(np.uint8, copy=True)
            )

        return np.asarray(palette, dtype=np.uint8)

    @staticmethod
    def _priority_palette_colors_from_samples(
        sample,
        maximum,
    ):
        """少面積でも残したい黒・主要色相を実在色から選ぶ。"""
        sample = np.asarray(
            sample,
            dtype=np.uint8,
        ).reshape((-1, 3))
        maximum = max(0, int(maximum))
        if sample.size == 0 or maximum <= 0:
            return np.zeros((0, 3), dtype=np.uint8)

        colors, counts = np.unique(
            sample,
            axis=0,
            return_counts=True,
        )
        values = colors.astype(np.float32) / 255.0
        value = values.max(axis=1)
        minimum = values.min(axis=1)
        delta = value - minimum
        saturation = np.divide(
            delta,
            np.maximum(value, 1e-6),
        )

        hue = np.zeros_like(value)
        chromatic = delta > 1e-6
        red_mask = chromatic & (
            values[:, 0] >= values[:, 1]
        ) & (
            values[:, 0] >= values[:, 2]
        )
        green_mask = chromatic & ~red_mask & (
            values[:, 1] >= values[:, 2]
        )
        blue_mask = chromatic & ~red_mask & ~green_mask
        hue[red_mask] = (
            (values[red_mask, 1] - values[red_mask, 2])
            / delta[red_mask]
        ) % 6.0
        hue[green_mask] = (
            (values[green_mask, 2] - values[green_mask, 0])
            / delta[green_mask]
        ) + 2.0
        hue[blue_mask] = (
            (values[blue_mask, 0] - values[blue_mask, 1])
            / delta[blue_mask]
        ) + 4.0
        hue = (hue / 6.0) % 1.0

        minimum_support = max(
            2,
            int(math.ceil(len(sample) * 0.00005)),
        )
        chromatic_minimum_support = max(
            8,
            int(math.ceil(len(sample) * 0.001)),
        )
        chromatic_mask = (
            (saturation >= 0.38)
            & (value >= 0.18)
        )
        color_groups = [
            (np.asarray([0, 0, 0]), value <= 0.24),
            (np.asarray([255, 0, 0]), None),
            (np.asarray([0, 0, 255]), None),
            (np.asarray([0, 255, 0]), None),
            (np.asarray([255, 0, 255]), None),
            (np.asarray([255, 255, 0]), None),
        ]
        target_hues = (None, 0.0, 2.0 / 3.0, 1.0 / 3.0, 5.0 / 6.0, 1.0 / 6.0)
        candidates_by_group = []

        for group_index, (target_rgb, fixed_mask) in enumerate(color_groups):
            if fixed_mask is None:
                target_hue = target_hues[group_index]
                hue_distance = np.abs(hue - target_hue)
                hue_distance = np.minimum(
                    hue_distance,
                    1.0 - hue_distance,
                )
                hue_tolerance = (
                    1.0 / 8.0
                    if group_index == 3
                    else 1.0 / 12.0
                )
                mask = chromatic_mask & (hue_distance <= hue_tolerance)
            else:
                mask = fixed_mask

            group_support = int(counts[mask].sum())
            required_support = (
                minimum_support
                if group_index == 0
                else chromatic_minimum_support
            )
            if group_support < required_support:
                continue
            candidates = colors[mask].astype(np.int32)
            candidate_counts = counts[mask].astype(np.float64)
            difference = candidates - target_rgb[None, :]
            distance = np.sum(difference * difference, axis=1)
            if group_index == 0:
                # 黒線は面積が小さくても、暗い有彩色ではなく実在する
                # 最暗色を選ぶ。頻度ボーナスで青へ寄るのを防ぐ。
                luminance = (
                    candidates[:, 0] * 0.2126
                    + candidates[:, 1] * 0.7152
                    + candidates[:, 2] * 0.0722
                )
                chroma = candidates.max(axis=1) - candidates.min(axis=1)
                best = int(np.argmin(luminance + chroma * 0.20))
            else:
                frequency_bonus = 1.0 + 0.30 * np.log1p(candidate_counts)
                best = int(np.argmin(distance / frequency_bonus))
            # 固定された色相順で枠を使い切らず、画像内での支持画素数が
            # 多い主要色を優先する。低色数でも実在する緑が脱落しにくい。
            candidates_by_group.append((
                group_support,
                group_index,
                candidates[best].astype(np.uint8),
            ))

        candidates_by_group.sort(
            key=lambda item: (-item[0], item[1])
        )
        selected = [
            color for _support, _group, color
            in candidates_by_group[:maximum]
        ]

        return np.asarray(selected, dtype=np.uint8).reshape((-1, 3))


    @classmethod
    def build_color_reduction_palette(
        cls,
        image,
        color_count,
        alpha_threshold=128,
        max_samples=262144,
        opaque_background=False,
        background_rgb=None,
        tone_curve_points=None,
        extraction_mode="surface",
    ):
        """近い色相を同じ原色面としてまとめる共通パレット。"""
        color_count = max(2, min(100, int(color_count)))
        threshold = max(1, min(254, int(alpha_threshold)))
        rgba = cls._qimage_rgba_array(image)
        if rgba.size == 0:
            return np.asarray([[0, 0, 0]], dtype=np.uint8)

        rgb = rgba[:, :, :3]
        alpha = rgba[:, :, 3]
        visible = (
            np.ones(alpha.shape, dtype=bool)
            if opaque_background
            else alpha >= threshold
        )
        if not np.any(visible):
            return np.asarray([[0, 0, 0]], dtype=np.uint8)

        variation = cls._local_color_variation(rgb)
        stable = visible & (variation <= 12)

        reserved_background = None
        foreground_mask = stable.copy()
        if opaque_background and background_rgb is not None:
            reserved_background = np.asarray(
                background_rgb,
                dtype=np.uint8,
            )
            difference = (
                rgb.astype(np.int16)
                - reserved_background.astype(np.int16)
            )
            background_distance = np.sum(
                difference.astype(np.int32)
                * difference.astype(np.int32),
                axis=2,
            )
            foreground_mask &= (
                background_distance > (18 * 18 * 3)
            )

        if np.count_nonzero(foreground_mask) < 32:
            foreground_mask = visible.copy()
            if reserved_background is not None:
                difference = (
                    rgb.astype(np.int16)
                    - reserved_background.astype(np.int16)
                )
                background_distance = np.sum(
                    difference.astype(np.int32)
                    * difference.astype(np.int32),
                    axis=2,
                )
                foreground_mask &= (
                    background_distance > (26 * 26 * 3)
                )

        training_rgb = rgb[foreground_mask]
        if training_rgb.size == 0:
            training_rgb = rgb[visible]

        if len(training_rgb) > int(max_samples):
            sample_indices = np.linspace(
                0,
                len(training_rgb) - 1,
                int(max_samples),
                dtype=np.int64,
            )
            training_rgb = training_rgb[sample_indices]

        foreground_count = (
            color_count - 1
            if reserved_background is not None
            else color_count
        )
        foreground_count = max(1, foreground_count)
        extraction_mode = (
            "line" if extraction_mode == "line" else "surface"
        )
        if extraction_mode == "line":
            priority_palette = cls._priority_palette_colors_from_samples(
                training_rgb,
                foreground_count,
            )
        else:
            priority_palette = cls._priority_palette_colors_from_samples(
                training_rgb,
                foreground_count if foreground_count >= 6 else 0,
            )
        clustered_palette = cls._hue_cluster_palette_from_samples(
            training_rgb,
            foreground_count,
        )
        foreground_colors = []
        for color in np.vstack([
            priority_palette,
            clustered_palette,
        ]):
            color_i32 = color.astype(np.int32)
            if extraction_mode == "line":
                color_value = float(color_i32.max())
                color_chroma = float(
                    color_i32.max() - color_i32.min()
                )
                color_saturation = (
                    color_chroma / max(1.0, color_value)
                )
                if color_saturation >= 0.25:
                    differences = (
                        training_rgb.astype(np.int16)
                        - color_i32.astype(np.int16)
                    )
                    nearby = np.sum(
                        differences.astype(np.int32)
                        * differences.astype(np.int32),
                        axis=1,
                    ) <= (55 * 55 * 3)
                    required = max(
                        8,
                        int(math.ceil(len(training_rgb) * 0.001)),
                    )
                    if int(np.count_nonzero(nearby)) < required:
                        continue
            if any(
                np.sum(
                    (color_i32 - existing.astype(np.int32)) ** 2
                ) <= (10 * 10 * 3)
                for existing in foreground_colors
            ):
                continue
            foreground_colors.append(color)
            if len(foreground_colors) >= foreground_count:
                break
        foreground_palette = np.asarray(
            foreground_colors,
            dtype=np.uint8,
        ).reshape((-1, 3))

        if reserved_background is None:
            palette = foreground_palette
        else:
            palette = np.vstack([
                reserved_background.reshape((1, 3)),
                foreground_palette,
            ])

        unique_palette = []
        seen = set()
        for color in palette:
            key = tuple(int(value) for value in color)
            if key in seen:
                continue
            seen.add(key)
            unique_palette.append(color)

        return np.asarray(
            unique_palette[:color_count],
            dtype=np.uint8,
        )



    @classmethod
    def apply_color_reduction_palette(
        cls,
        image,
        palette,
        alpha_threshold=128,
        opaque_background=False,
        background_rgb=None,
        tone_curve_points=None,
        extraction_mode="surface",
    ):
        """色相面を確定し、境界混合色を二分してからトーンを適用する。"""
        if image is None or image.isNull():
            return image

        rgba = cls._qimage_rgba_array(image)
        if rgba.size == 0:
            return image.copy()

        palette_array = np.asarray(
            palette,
            dtype=np.uint8,
        ).reshape((-1, 3))
        if palette_array.size == 0:
            return image.copy()

        threshold = max(1, min(254, int(alpha_threshold)))
        rgb = rgba[:, :, :3]
        visible = (
            np.ones(
                rgba.shape[:2],
                dtype=bool,
            )
            if opaque_background
            else rgba[:, :, 3] >= threshold
        )

        if extraction_mode == "line":
            labels = cls._line_palette_labels(
                rgb, visible, palette_array
            )
        else:
            labels = cls._surface_guided_palette_labels(
                rgb,
                visible,
                palette_array,
                spatial_refinement=True,
            )

        result = np.zeros_like(rgba)
        valid = visible & (labels >= 0)
        result[:, :, :3][valid] = palette_array[
            labels[valid]
        ]

        if opaque_background:
            if background_rgb is not None:
                background = np.asarray(
                    background_rgb,
                    dtype=np.uint8,
                )
                result[:, :, :3][~valid] = background
            result[:, :, 3] = 255
        else:
            result[:, :, 3][valid] = 255

        quantized = cls._rgba_array_to_qimage(result)
        return cls.apply_tone_curve(
            quantized,
            tone_curve_points=tone_curve_points,
            background_rgb=background_rgb,
        )



    def import_image(self, path, color_reduction=None):
        return self.import_image_sequence(
            [path],
            color_reduction=color_reduction,
        )

    def import_image_sequence(
        self,
        paths,
        progress_callback=None,
        color_reduction=None,
        layer_name=None,
    ):
        paths = sorted([str(path) for path in paths], key=self._natural_path_key)
        if not paths:
            return False, "画像ファイルがありません。"
        decoded = []
        for index, path in enumerate(paths, 1):
            if progress_callback:
                progress_callback(index - 1, len(paths), f"{Path(path).name} を読み込んでいます")
            image, error = self._read_image_file(path)
            if image is None:
                return False, f"{Path(path).name}\n{error}"
            if color_reduction:
                palette = color_reduction.get("palette")
                extraction_mode = color_reduction.get(
                    "extraction_mode",
                    "surface",
                )
                line_extraction = extraction_mode == "line"
                reduction_background = (
                    (255, 255, 255)
                    if line_extraction
                    else color_reduction.get("background_rgb")
                )
                alpha_threshold = int(
                    color_reduction.get(
                        "alpha_threshold",
                        128,
                    )
                )
                tone_curve_points = color_reduction.get(
                    "tone_curve_points",
                    [(0.0, 0.0), (1.0, 1.0)],
                )
                if tone_curve_points:
                    image = self.apply_tone_curve(
                        image,
                        tone_curve_points=tone_curve_points,
                        background_rgb=reduction_background,
                    )
                if palette is not None:
                    if progress_callback:
                        progress_callback(
                            index - 1,
                            len(paths),
                            f"{Path(path).name} の元画像へトーンカーブを適用し、"
                            "共通パレットで2値化しています",
                        )
                    image = self.apply_color_reduction_palette(
                        image,
                        palette,
                        alpha_threshold=alpha_threshold,
                        opaque_background=(
                            True
                            if line_extraction
                            else bool(
                                color_reduction.get(
                                    "opaque_background",
                                    False,
                                )
                            )
                        ),
                        background_rgb=reduction_background,
                        tone_curve_points=[
                            (0.0, 0.0),
                            (1.0, 1.0),
                        ],
                        extraction_mode=extraction_mode,
                    )
            else:
                # JPEGにはアルファチャンネルがないため、白を透明化すると
                # 圧縮後に白となった画素まで欠けて見える。デコード済みの
                # RGBをそのまま保持し、PNG／TGAの既存透明化だけを維持する。
                if Path(path).suffix.lower() not in (".jpg", ".jpeg"):
                    image = self._make_white_transparent(image)
            decoded.append((path, image))
        if progress_callback:
            progress_callback(len(paths), len(paths), "画像の配置を準備しています")
        self.push_doc_undo()
        start_frame = self.current_frame
        self._ensure_frame_count(start_frame + len(decoded))
        name = str(layer_name).strip() if layer_name is not None else ""
        if not name:
            name = Path(decoded[0][0]).stem or f"Image {len(self.layers)}"
        insert_index = len(self.frames[0].layers)
        for frame in self.frames:
            frame.layers.append(Layer(name, blank_image()))
        for offset, (_, image) in enumerate(decoded):
            if progress_callback:
                progress_callback(offset, len(decoded), f"{offset + 1} / {len(decoded)} 枚を配置しています")
            layer = self.frames[start_frame + offset].layers[insert_index]
            self._place_imported_image(image, layer.image)
            layer.has_content = True
            layer.exposure = 1
            layer.sequence_number = offset + 1
        self._sequence_source_bank = [
            self.frames[start_frame + offset]
            .layers[insert_index].image.copy()
            for offset in range(len(decoded))
        ]
        self._sequence_source_bank_layer_index = int(insert_index)
        self._sequence_source_bank_layer_name = str(name)
        self.active_layer_index = insert_index
        self.current_frame = start_frame
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        if progress_callback:
            progress_callback(len(decoded), len(decoded), "読み込み完了")
        return True, ""

    def dragEnterEvent(self,e):
        urls=e.mimeData().urls() if e.mimeData().hasUrls() else []
        valid=any(Path(u.toLocalFile()).suffix.lower() in (".pman",".xdts",".xtds",".jpg",".jpeg",".png",".tga") for u in urls)
        if valid:e.acceptProposedAction()
        else:e.ignore()

    def dragMoveEvent(self,e):
        e.acceptProposedAction()

    def dropEvent(self,e):
        project_paths=[]
        remap_paths=[]
        paths=[]
        for u in e.mimeData().urls():
            path=u.toLocalFile()
            suffix=Path(path).suffix.lower()
            if suffix==".pman":
                project_paths.append(path)
            elif suffix in (".xdts", ".xtds"):
                remap_paths.append(path)
            elif suffix in (".jpg",".jpeg",".png",".tga"):
                paths.append(path)
        if project_paths:
            self.projectDropped.emit(project_paths[0])
        elif remap_paths:
            self.timeRemapDropped.emit(remap_paths[0])
        elif paths:
            self.imagesDropped.emit(paths)
        e.acceptProposedAction()

    def selection_mask_bool(self, width=None, height=None, offset_x=0, offset_y=0):
        """Return a boolean mask for the active selection in the requested local area."""
        if not self.selection_polygon:
            return None
        width = int(width if width is not None else self.active_layer.image.width())
        height = int(height if height is not None else self.active_layer.image.height())
        if width <= 0 or height <= 0:
            return np.zeros((max(0, height), max(0, width)), dtype=bool)
        if self.selection_mask_override is not None:
            source = np.asarray(self.selection_mask_override, dtype=bool)
            result = np.zeros((height, width), dtype=bool)
            source_x1 = max(0, int(offset_x))
            source_y1 = max(0, int(offset_y))
            source_x2 = min(source.shape[1], int(offset_x) + width)
            source_y2 = min(source.shape[0], int(offset_y) + height)
            if source_x2 > source_x1 and source_y2 > source_y1:
                target_x1 = source_x1 - int(offset_x)
                target_y1 = source_y1 - int(offset_y)
                result[
                    target_y1:target_y1 + source_y2 - source_y1,
                    target_x1:target_x1 + source_x2 - source_x1,
                ] = source[source_y1:source_y2, source_x1:source_x2]
            return result
        mask = QImage(width, height, QImage.Format.Format_RGBA8888)
        mask.fill(Qt.GlobalColor.transparent)
        local_polygon = QPolygonF([
            QPointF(point.x() - offset_x, point.y() - offset_y)
            for point in self.selection_polygon
        ])
        painter = QPainter(mask)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 255))
        painter.drawPolygon(local_polygon)
        painter.end()
        ptr = mask.bits()
        try:
            ptr.setsize(mask.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, mask.bytesPerLine())
        )
        return rows[:, :width * 4].reshape((height, width, 4))[:, :, 3].copy() > 0

    @staticmethod
    def _bool_mask_image(mask):
        mask = np.asarray(mask, dtype=bool)
        height, width = mask.shape
        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[:, :, :3] = 255
        rgba[:, :, 3] = mask.astype(np.uint8) * 255
        return QImage(
            rgba.data,
            width,
            height,
            rgba.strides[0],
            QImage.Format.Format_RGBA8888,
        ).copy()

    def _update_selection_fade(self):
        phase = (
            (time.monotonic() - self._selection_fade_started) % 2.0
        ) / 2.0
        self._selection_fade_opacity = (
            0.08 + 0.92 * (0.5 + 0.5 * math.cos(phase * math.tau))
        )
        if self.selection_polygon:
            if self.transform_active and not self.transform_outer_polygon().isEmpty():
                selection_rect = self.transform_outer_polygon().boundingRect()
            else:
                selection_rect = (
                    self.selection_mask_rect
                    if self.selection_mask_rect is not None
                    else QRectF(self.selection_bounds())
                )
            corners = [
                self.canvas_to_widget(selection_rect.topLeft()),
                self.canvas_to_widget(selection_rect.topRight()),
                self.canvas_to_widget(selection_rect.bottomRight()),
                self.canvas_to_widget(selection_rect.bottomLeft()),
            ]
            xs = [point.x() for point in corners]
            ys = [point.y() for point in corners]
            dirty = QRectF(
                min(xs), min(ys),
                max(xs) - min(xs), max(ys) - min(ys),
            ).adjusted(-4, -4, 4, 4).toAlignedRect()
            region = QRegion(dirty.intersected(self.rect()))
            if self._brush_cursor_inside:
                cursor_radius = max(
                    10,
                    int(math.ceil(
                        float(self.pen_size) * max(self.zoom, 0.01) / 2.0
                    )) + 6,
                )
                cursor_rect = QRectF(
                    self._brush_cursor_widget_pos,
                    self._brush_cursor_widget_pos,
                ).adjusted(
                    -cursor_radius, -cursor_radius,
                    cursor_radius, cursor_radius,
                ).toAlignedRect()
                region -= QRegion(cursor_rect)
            if not region.isEmpty():
                self.update(region)

    def _clear_selection_state(self):
        if self.transform_active:
            self.cancel_selection_transform()
        self.selection_polygon = []
        self.selection_mask_override = None
        self.selection_outline_polygons = []
        self.selection_mask_rect = None
        self.lasso = []
        self.rect_start = None
        self.rect_end = None
        self.selectionChanged.emit()
        self.update()

    def clear_selection(self):
        self._clear_selection_state()
        self.selectionCleared.emit()

    def clear_selection_preserving_used_colors(self):
        """内部処理用。使用色の選択を維持したまま画像選択だけ解除する。"""
        self._clear_selection_state()

    def _masked_selection_source(self, rect):
        source = self.active_layer.image.copy(rect)
        if not self.selection_polygon:
            return source
        if self.selection_mask_override is not None:
            mask = self._bool_mask_image(self.selection_mask_bool(
                rect.width(), rect.height(), rect.x(), rect.y()
            ))
            painter = QPainter(source)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_DestinationIn
            )
            painter.drawImage(0, 0, mask)
            painter.end()
            return source
        mask = QImage(rect.width(), rect.height(), QImage.Format.Format_ARGB32_Premultiplied)
        mask.fill(Qt.GlobalColor.transparent)
        painter = QPainter(mask)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 255))
        painter.drawPolygon(QPolygonF([
            QPointF(point.x() - rect.x(), point.y() - rect.y())
            for point in self.selection_polygon
        ]))
        painter.end()
        painter = QPainter(source)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, mask)
        painter.end()
        return source

    @staticmethod
    def _regular_grid_points(rect, cols, rows=None):
        cols = max(2, int(cols))
        rows = max(2, int(rows if rows is not None else cols))
        width = max(0.0, rect.width())
        height = max(0.0, rect.height())
        return [
            QPointF(
                rect.left() + width * gx / (cols - 1),
                rect.top() + height * gy / (rows - 1),
            )
            for gy in range(rows)
            for gx in range(cols)
        ]

    def _regular_mesh_reference_points(self, rect, cols, rows):
        cols = max(2, int(cols))
        rows = max(2, int(rows))
        source = getattr(self, "transform_source", None)

        if source is not None and not source.isNull():
            pixel_width = max(0.0, float(source.width() - 1))
            pixel_height = max(0.0, float(source.height() - 1))
        else:
            pixel_width = max(0.0, float(rect.width()) - 1.0)
            pixel_height = max(0.0, float(rect.height()) - 1.0)

        return [
            QPointF(
                float(rect.left())
                + pixel_width * gx / float(cols - 1),
                float(rect.top())
                + pixel_height * gy / float(rows - 1),
            )
            for gy in range(rows)
            for gx in range(cols)
        ]

    def auto_select_used_area(self):
        """Select the bounding area of every opaque pixel, including gray colors."""
        if not self.frames or not self.layers:
            return False
        self.ensure_editable_key()
        image = self.active_layer.image.convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        width, height = image.width(), image.height()
        if width <= 0 or height <= 0:
            return False
        ptr = image.bits()
        try:
            ptr.setsize(image.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, image.bytesPerLine())
        )
        pixels = rows[:, :width * 4].reshape((height, width, 4))
        used = pixels[:, :, 3] > 0
        ys, xs = np.nonzero(used)
        if len(xs) == 0:
            return False
        left = int(xs.min())
        right = int(xs.max()) + 1
        top = int(ys.min())
        bottom = int(ys.max()) + 1
        self.selection_polygon = [
            QPointF(left, top),
            QPointF(right, top),
            QPointF(right, bottom),
            QPointF(left, bottom),
        ]
        self.selectionChanged.emit()
        self.update()
        return True

    def begin_selection_transform(self, mode):
        if not self.selection_polygon:
            return False
        selection_rect = self.selection_bounds().intersected(
            self.active_layer.image.rect()
        )
        # 選択境界ぴったりで切り出すと、縮小時のサンプリングで
        # 外周画素が透明側へ丸められて欠ける。変形元だけに透明な
        # 安全余白を持たせ、選択マスク自体の形状は変更しない。
        rect = selection_rect.adjusted(-2, -2, 2, 2).intersected(
            self.active_layer.image.rect()
        )
        if rect.isEmpty():
            return False
        if self.transform_active:
            self.cancel_selection_transform()

        mode = mode if mode in ("scale", "free", "mesh") else "free"
        self.transform_active = True
        self.transform_mode = mode
        self.transform_quality_active = bool(getattr(self, "transform_quality", False))
        self.transform_tp_line_colors = tuple(sorted(self.selected_used_colors()))
        if self.transform_quality_active:
            self.transform_apply_all_frames = False
        self._invalidate_tp_preview_cache()
        self.transform_frame_index = self.current_frame
        self.transform_layer_index = self.active_layer_index
        self.transform_original_layer = self.active_layer.image.copy()
        self.transform_original_has_content = self.active_layer.has_content
        self.transform_source_rect = QRectF(rect)
        self.transform_source = self._masked_selection_source(rect)
        if self.transform_quality_active:
            target_width = self.active_layer.image.width()
            target_height = self.active_layer.image.height()
            use_proxy = self._tp_uses_proxy(target_width, target_height)
            if PILImage is None or PILImageFilter is None:
                # Keep the transform usable even when Pillow is unavailable,
                # but do not pretend that the TP_mask method is active.
                self.transform_quality_active = False
                self.status_message.emit(
                    "クオリティ変形には Pillow が必要です。通常変形へ切り替えました。"
                )
            elif use_proxy:
                self._clear_tp_transform_masks(clear_proxy=False)
                self.status_message.emit(
                    "5000px以上の画像は軽量TpMaskプレビューで表示し、"
                    "確定時に原寸で処理します。"
                )
            elif not self._prepare_tp_transform_masks():
                self.transform_quality_active = False
                self.status_message.emit(
                    "TpMaskを準備できなかったため、通常変形へ切り替えました。"
                )
        self.transform_drag_kind = None
        self.transform_drag_start = QPointF()
        self.transform_drag_points = []

        painter = QPainter(self.active_layer.image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 255))
        if self.selection_mask_override is not None:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_DestinationOut
            )
            local_mask = self.selection_mask_bool(
                rect.width(), rect.height(), rect.x(), rect.y()
            )
            painter.drawImage(
                rect.topLeft(), self._bool_mask_image(local_mask)
            )
        else:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
            )
            painter.drawPolygon(QPolygonF(self.selection_polygon))
        painter.end()

        if mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
            self.transform_mesh_cols = cols
            self.transform_mesh_rows = rows
            self.transform_mesh_grid = cols
            self.transform_points = (
                self._regular_mesh_reference_points(
                    QRectF(rect),
                    cols,
                    rows,
                )
            )
            self.transform_mesh_reference_points = [
                QPointF(point) for point in self.transform_points
            ]
            self._active_mesh_cols = cols
            self._active_mesh_rows = rows
        else:
            self.transform_mesh_reference_points = []
            left, top = float(rect.left()), float(rect.top())
            right, bottom = float(rect.right() + 1), float(rect.bottom() + 1)
            self.transform_points = [
                QPointF(left, top), QPointF(right, top),
                QPointF(right, bottom), QPointF(left, bottom),
            ]
        self.transform_handle = -1
        if self.transform_quality_active:
            self.request_quality_preview_counter(
                "最初のクオリティプレビューを生成しています"
            )
        self.update()
        return True

    def set_transform_mesh_grid(self, cols, rows=None):
        cols = max(2, min(12, int(cols)))
        rows = max(2, min(12, int(rows if rows is not None else cols)))
        self.transform_mesh_cols = cols
        self.transform_mesh_rows = rows
        self.transform_mesh_grid = cols
        if not self.transform_active or self.transform_mode != "mesh":
            return

        old_points = list(self.transform_points)
        old_cols = max(2, int(getattr(self, "_active_mesh_cols", 0) or 0))
        old_rows = max(2, int(getattr(self, "_active_mesh_rows", 0) or 0))
        if old_cols * old_rows != len(old_points):
            old_cols = max(2, int(round(math.sqrt(len(old_points))))) if old_points else 0
            old_rows = old_cols if old_cols and old_cols * old_cols == len(old_points) else 0

        if old_cols < 2 or old_rows < 2 or old_cols * old_rows != len(old_points):
            self.transform_points = self._regular_grid_points(
                self.transform_source_rect, cols, rows
            )
        else:
            def sample(u, v):
                x = max(0.0, min(old_cols - 1.0, u * (old_cols - 1)))
                y = max(0.0, min(old_rows - 1.0, v * (old_rows - 1)))
                x0, y0 = int(math.floor(x)), int(math.floor(y))
                x1, y1 = min(old_cols - 1, x0 + 1), min(old_rows - 1, y0 + 1)
                tx, ty = x - x0, y - y0
                p00 = old_points[y0 * old_cols + x0]
                p10 = old_points[y0 * old_cols + x1]
                p01 = old_points[y1 * old_cols + x0]
                p11 = old_points[y1 * old_cols + x1]
                return QPointF(
                    (1 - ty) * ((1 - tx) * p00.x() + tx * p10.x())
                    + ty * ((1 - tx) * p01.x() + tx * p11.x()),
                    (1 - ty) * ((1 - tx) * p00.y() + tx * p10.y())
                    + ty * ((1 - tx) * p01.y() + tx * p11.y()),
                )

            self.transform_points = [
                sample(gx / (cols - 1), gy / (rows - 1))
                for gy in range(rows)
                for gx in range(cols)
            ]

        self.transform_mesh_reference_points = (
            self._regular_mesh_reference_points(
                self.transform_source_rect,
                cols,
                rows,
            )
        )
        self._active_mesh_cols = cols
        self._active_mesh_rows = rows
        self.transform_handle = -1
        self._invalidate_tp_preview_cache()
        if self.transform_quality_active:
            self.request_quality_preview_counter(
                "メッシュ格子のプレビューを生成しています"
            )
        self.update()

    def nearest_transform_handle(self, point):
        if not self.transform_active or not self.transform_points:
            return -1
        radius = 14 / max(self.zoom, 0.01)
        distances = [
            (handle.x() - point.x()) ** 2 + (handle.y() - point.y()) ** 2
            for handle in self.transform_points
        ]
        index = int(np.argmin(distances))
        return index if distances[index] <= radius * radius else -1

    def transform_center(self):
        if not self.transform_points:
            return QPointF()
        return QPointF(
            sum(point.x() for point in self.transform_points) / len(self.transform_points),
            sum(point.y() for point in self.transform_points) / len(self.transform_points),
        )

    def transform_rotation_handle(self):
        if not self.transform_points:
            return None
        if self.transform_mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            if len(self.transform_points) < cols:
                return None
            p0, p1 = self.transform_points[0], self.transform_points[cols - 1]
        elif len(self.transform_points) == 4:
            p0, p1 = self.transform_points[0], self.transform_points[1]
        else:
            return None
        midpoint = QPointF((p0.x() + p1.x()) / 2.0, (p0.y() + p1.y()) / 2.0)
        center = self.transform_center()
        dx, dy = midpoint.x() - center.x(), midpoint.y() - center.y()
        length = math.hypot(dx, dy) or 1.0
        distance = 36.0 / max(self.zoom, 0.01)
        return QPointF(
            midpoint.x() + dx / length * distance,
            midpoint.y() + dy / length * distance,
        )

    def transform_outer_polygon(self):
        if not self.transform_points:
            return QPolygonF()
        if self.transform_mode != "mesh":
            return QPolygonF(self.transform_points[:4])
        cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
        rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
        points = self.transform_points
        if len(points) != cols * rows:
            return QPolygonF()
        subdivisions = 8
        outline = []

        for step in range((cols - 1) * subdivisions + 1):
            outline.append(
                self._mesh_curve_point(
                    points, cols, rows,
                    step / float(subdivisions), 0.0,
                )
            )
        for step in range(1, (rows - 1) * subdivisions + 1):
            outline.append(
                self._mesh_curve_point(
                    points, cols, rows,
                    float(cols - 1),
                    step / float(subdivisions),
                )
            )
        for step in range(
            (cols - 1) * subdivisions - 1, -1, -1
        ):
            outline.append(
                self._mesh_curve_point(
                    points, cols, rows,
                    step / float(subdivisions),
                    float(rows - 1),
                )
            )
        for step in range(
            (rows - 1) * subdivisions - 1, 0, -1
        ):
            outline.append(
                self._mesh_curve_point(
                    points, cols, rows,
                    0.0,
                    step / float(subdivisions),
                )
            )
        return QPolygonF(outline)

    def transformed_selection_outline(self, outline):
        """選択輪郭を現在の変形プレビューと同じ座標へ写像する。"""
        if (
            not self.transform_active
            or self.transform_source_rect is None
            or not self.transform_points
        ):
            return [QPointF(point) for point in outline]
        rect = QRectF(self.transform_source_rect)
        width = max(1.0, float(rect.width()))
        height = max(1.0, float(rect.height()))
        if self.transform_mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
            if len(self.transform_points) != cols * rows:
                return [QPointF(point) for point in outline]
            return [
                self._mesh_curve_point(
                    self.transform_points,
                    cols,
                    rows,
                    (float(point.x()) - rect.left()) / width * (cols - 1),
                    (float(point.y()) - rect.top()) / height * (rows - 1),
                )
                for point in outline
            ]
        if len(self.transform_points) != 4:
            return [QPointF(point) for point in outline]
        transform = self._quad_homography(
            [
                QPointF(0, 0), QPointF(width, 0),
                QPointF(width, height), QPointF(0, height),
            ],
            self.transform_points,
        )
        return [
            transform.map(QPointF(
                float(point.x()) - rect.left(),
                float(point.y()) - rect.top(),
            ))
            for point in outline
        ]

    def begin_transform_drag(self, point):
        if not self.transform_active:
            return False
        rotation_handle = self.transform_rotation_handle()
        radius = 16 / max(self.zoom, 0.01)
        if rotation_handle is not None:
            if (
                (rotation_handle.x() - point.x()) ** 2
                + (rotation_handle.y() - point.y()) ** 2
                <= radius * radius
            ):
                self.transform_drag_kind = ("rotate", -1)
                self.transform_drag_start = QPointF(point)
                self.transform_drag_points = [QPointF(p) for p in self.transform_points]
                return True
        handle = self.nearest_transform_handle(point)
        if handle >= 0:
            self.transform_drag_kind = ("handle", handle)
            self.transform_drag_start = QPointF(point)
            self.transform_drag_points = [QPointF(p) for p in self.transform_points]
            self.transform_handle = handle
            return True
        if self.transform_outer_polygon().containsPoint(
            point, Qt.FillRule.OddEvenFill
        ):
            self.transform_drag_kind = ("move", -1)
            self.transform_drag_start = QPointF(point)
            self.transform_drag_points = [QPointF(p) for p in self.transform_points]
            return True
        self.transform_drag_kind = None
        return False

    def update_transform_drag(self, point):
        if not self.transform_drag_kind:
            return
        kind, index = self.transform_drag_kind
        start_points = self.transform_drag_points
        if kind == "move":
            delta = point - self.transform_drag_start
            self.transform_points = [p + delta for p in start_points]
        elif kind == "rotate":
            center = QPointF(
                sum(p.x() for p in start_points) / len(start_points),
                sum(p.y() for p in start_points) / len(start_points),
            )
            a0 = math.atan2(
                self.transform_drag_start.y() - center.y(),
                self.transform_drag_start.x() - center.x(),
            )
            a1 = math.atan2(point.y() - center.y(), point.x() - center.x())
            angle = a1 - a0
            cs, sn = math.cos(angle), math.sin(angle)
            self.transform_points = [
                QPointF(
                    center.x() + (p.x() - center.x()) * cs - (p.y() - center.y()) * sn,
                    center.y() + (p.x() - center.x()) * sn + (p.y() - center.y()) * cs,
                )
                for p in start_points
            ]
        elif kind == "handle":
            if self.transform_mode in ("free", "mesh"):
                self.transform_points[index] = QPointF(point)
            else:
                opposite_index = (index + 2) % 4
                opposite = start_points[opposite_index]
                original = start_points[index] - opposite
                current = point - opposite
                denominator = original.x() ** 2 + original.y() ** 2
                scale = (
                    (current.x() * original.x() + current.y() * original.y())
                    / denominator
                    if denominator > 1e-8 else 1.0
                )
                if abs(scale) < 0.02:
                    scale = 0.02 if scale >= 0 else -0.02
                self.transform_points = [
                    opposite + (p - opposite) * scale
                    for p in start_points
                ]
        self._invalidate_tp_preview_cache()
        self.update()

    def end_transform_drag(self):
        self.transform_drag_kind = None
        self.transform_handle = -1
        self.transform_drag_points = []
        if self.transform_quality_active:
            self.request_quality_preview_counter(
                "変形後のクオリティプレビューを生成しています"
            )
        self.update()

    def rotate_selection_transform(self, degrees):
        if not self.transform_active or not self.transform_points:
            return
        center = self.transform_center()
        angle = math.radians(float(degrees))
        cs, sn = math.cos(angle), math.sin(angle)
        self.transform_points = [
            QPointF(
                center.x() + (p.x() - center.x()) * cs - (p.y() - center.y()) * sn,
                center.y() + (p.x() - center.x()) * sn + (p.y() - center.y()) * cs,
            )
            for p in self.transform_points
        ]
        self._invalidate_tp_preview_cache()
        if self.transform_quality_active:
            self.request_quality_preview_counter(
                "回転後のクオリティプレビューを生成しています"
            )
        self.update()

    @staticmethod
    def _quad_homography(source_points, target_points):
        matrix = []
        values = []
        for source, target in zip(source_points, target_points):
            x, y = float(source.x()), float(source.y())
            u, v = float(target.x()), float(target.y())
            matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
            values.append(u)
            matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
            values.append(v)
        try:
            a, b, c, d, e, f, g, h = np.linalg.solve(
                np.asarray(matrix, dtype=np.float64),
                np.asarray(values, dtype=np.float64),
            )
        except np.linalg.LinAlgError:
            return QTransform()
        return QTransform(a, d, g, b, e, h, c, f, 1.0)

    @staticmethod
    def _mesh_catmull_scalar(p0, p1, p2, p3, t):
        """制御点を通過するCatmull-Rom補間。"""
        t = max(0.0, min(1.0, float(t)))
        t2 = t * t
        t3 = t2 * t
        return 0.5 * (
            2.0 * p1
            + (-p0 + p2) * t
            + (2.0*p0 - 5.0*p1 + 4.0*p2 - p3) * t2
            + (-p0 + 3.0*p1 - 3.0*p2 + p3) * t3
        )

    def _mesh_reference_grid(self, cols, rows):
        reference = list(
            getattr(
                self,
                "transform_mesh_reference_points",
                [],
            )
        )
        if len(reference) == cols * rows:
            return [QPointF(point) for point in reference]

        rect = self.transform_source_rect
        if rect is None:
            return []
        return self._regular_mesh_reference_points(
            QRectF(rect),
            cols,
            rows,
        )

    def _mesh_curve_point(self, points, cols, rows, grid_x, grid_y):
        """基準格子からの変位だけを滑らかに補間する。"""
        if not points or cols < 2 or rows < 2:
            return QPointF()

        reference = self._mesh_reference_grid(cols, rows)
        if len(reference) != cols * rows:
            return QPointF()

        grid_x = max(
            0.0,
            min(float(cols - 1), float(grid_x)),
        )
        grid_y = max(
            0.0,
            min(float(rows - 1), float(grid_y)),
        )
        cell_x = min(cols - 2, int(math.floor(grid_x)))
        cell_y = min(rows - 2, int(math.floor(grid_y)))
        local_x = grid_x - cell_x
        local_y = grid_y - cell_y

        def clamped_index(row, col):
            row = max(0, min(rows - 1, int(row)))
            col = max(0, min(cols - 1, int(col)))
            return row * cols + col

        def displacement_at(row, col):
            index = clamped_index(row, col)
            return QPointF(
                points[index].x() - reference[index].x(),
                points[index].y() - reference[index].y(),
            )

        horizontal_displacements = []
        for row in range(cell_y - 1, cell_y + 3):
            p0 = displacement_at(row, cell_x - 1)
            p1 = displacement_at(row, cell_x)
            p2 = displacement_at(row, cell_x + 1)
            p3 = displacement_at(row, cell_x + 2)
            horizontal_displacements.append(QPointF(
                self._mesh_catmull_scalar(
                    p0.x(),
                    p1.x(),
                    p2.x(),
                    p3.x(),
                    local_x,
                ),
                self._mesh_catmull_scalar(
                    p0.y(),
                    p1.y(),
                    p2.y(),
                    p3.y(),
                    local_x,
                ),
            ))

        displacement = QPointF(
            self._mesh_catmull_scalar(
                horizontal_displacements[0].x(),
                horizontal_displacements[1].x(),
                horizontal_displacements[2].x(),
                horizontal_displacements[3].x(),
                local_y,
            ),
            self._mesh_catmull_scalar(
                horizontal_displacements[0].y(),
                horizontal_displacements[1].y(),
                horizontal_displacements[2].y(),
                horizontal_displacements[3].y(),
                local_y,
            ),
        )

        # 基準格子上の位置は双線形補間する。
        i00 = cell_y * cols + cell_x
        i10 = cell_y * cols + cell_x + 1
        i01 = (cell_y + 1) * cols + cell_x
        i11 = (cell_y + 1) * cols + cell_x + 1
        r00 = reference[i00]
        r10 = reference[i10]
        r01 = reference[i01]
        r11 = reference[i11]

        base = QPointF(
            (1.0 - local_y)
            * (
                (1.0 - local_x) * r00.x()
                + local_x * r10.x()
            )
            + local_y
            * (
                (1.0 - local_x) * r01.x()
                + local_x * r11.x()
            ),
            (1.0 - local_y)
            * (
                (1.0 - local_x) * r00.y()
                + local_x * r10.y()
            )
            + local_y
            * (
                (1.0 - local_x) * r01.y()
                + local_x * r11.y()
            ),
        )

        return QPointF(
            base.x() + displacement.x(),
            base.y() + displacement.y(),
        )

    def _curved_mesh_points(
        self, points, cols, rows, subdivisions=8
    ):
        """基準格子に滑らかな変位を加えた高密度メッシュを生成する。"""
        subdivisions = max(2, int(subdivisions))
        dense_cols = (cols - 1) * subdivisions + 1
        dense_rows = (rows - 1) * subdivisions + 1
        dense = []
        for dense_y in range(dense_rows):
            grid_y = dense_y / float(subdivisions)
            for dense_x in range(dense_cols):
                grid_x = dense_x / float(subdivisions)
                dense.append(
                    self._mesh_curve_point(
                        points,
                        cols,
                        rows,
                        grid_x,
                        grid_y,
                    )
                )
        return dense, dense_cols, dense_rows

    def _mesh_preview_image(
        self, source_image=None, target_width=None, target_height=None, smooth=False
    ):
        # 変形では補間色を生成しない。呼び出し側の指定に関係なく最近傍に固定する。
        smooth = False
        source_image = source_image if source_image is not None else self.transform_source
        if source_image is None:
            return None
        source = source_image.convertToFormat(QImage.Format.Format_RGBA8888)
        sw, sh = source.width(), source.height()
        cw = int(target_width if target_width is not None else self.active_layer.image.width())
        ch = int(target_height if target_height is not None else self.active_layer.image.height())
        if sw <= 0 or sh <= 0 or cw <= 0 or ch <= 0:
            return None
        sp = source.bits()
        try:
            sp.setsize(source.sizeInBytes())
        except AttributeError:
            pass
        source_rows = np.frombuffer(sp, dtype=np.uint8).reshape(
            (sh, source.bytesPerLine())
        )
        source_pixels = source_rows[:, :sw * 4].reshape((sh, sw, 4)).copy()
        output = np.zeros((ch, cw, 4), dtype=np.uint8)
        cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
        rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
        if len(self.transform_points) != cols * rows:
            return None

        def raster_triangle(target_triangle, source_triangle):
            tx = np.array([p.x() for p in target_triangle], dtype=np.float64)
            ty = np.array([p.y() for p in target_triangle], dtype=np.float64)
            min_x = max(0, int(math.floor(float(tx.min()))))
            max_x = min(cw - 1, int(math.ceil(float(tx.max()))))
            min_y = max(0, int(math.floor(float(ty.min()))))
            max_y = min(ch - 1, int(math.ceil(float(ty.max()))))
            if max_x < min_x or max_y < min_y:
                return
            denominator = (
                (ty[1] - ty[2]) * (tx[0] - tx[2])
                + (tx[2] - tx[1]) * (ty[0] - ty[2])
            )
            if abs(denominator) < 1e-8:
                return
            yy, xx = np.mgrid[min_y:max_y + 1, min_x:max_x + 1]
            w0 = (
                (ty[1] - ty[2]) * (xx - tx[2])
                + (tx[2] - tx[1]) * (yy - ty[2])
            ) / denominator
            w1 = (
                (ty[2] - ty[0]) * (xx - tx[2])
                + (tx[0] - tx[2]) * (yy - ty[2])
            ) / denominator
            w2 = 1.0 - w0 - w1
            inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not np.any(inside):
                return
            sx_float = (
                w0 * source_triangle[0][0]
                + w1 * source_triangle[1][0]
                + w2 * source_triangle[2][0]
            )
            sy_float = (
                w0 * source_triangle[0][1]
                + w1 * source_triangle[1][1]
                + w2 * source_triangle[2][1]
            )
            region = output[min_y:max_y + 1, min_x:max_x + 1]
            if smooth:
                sx_float = np.clip(sx_float, 0.0, sw - 1.0)
                sy_float = np.clip(sy_float, 0.0, sh - 1.0)
                x0 = np.floor(sx_float).astype(np.int32)
                y0 = np.floor(sy_float).astype(np.int32)
                x1 = np.minimum(sw - 1, x0 + 1)
                y1 = np.minimum(sh - 1, y0 + 1)
                tx = (sx_float - x0)[..., None]
                ty = (sy_float - y0)[..., None]
                sampled = (
                    source_pixels[y0, x0].astype(np.float32) * (1.0 - tx) * (1.0 - ty)
                    + source_pixels[y0, x1].astype(np.float32) * tx * (1.0 - ty)
                    + source_pixels[y1, x0].astype(np.float32) * (1.0 - tx) * ty
                    + source_pixels[y1, x1].astype(np.float32) * tx * ty
                )
                region[inside] = np.clip(
                    sampled[inside] + 0.5, 0, 255
                ).astype(np.uint8)
            else:
                sx = np.clip(np.rint(sx_float).astype(np.int32), 0, sw - 1)
                sy = np.clip(np.rint(sy_float).astype(np.int32), 0, sh - 1)
                region[inside] = source_pixels[sy[inside], sx[inside]]

        curved, dense_cols, dense_rows = self._curved_mesh_points(
            self.transform_points, cols, rows, subdivisions=8
        )
        for dense_y in range(dense_rows - 1):
            source_y0 = (
                (sh - 1) * dense_y / max(1, dense_rows - 1)
            )
            source_y1 = (
                (sh - 1) * (dense_y + 1) / max(1, dense_rows - 1)
            )
            for dense_x in range(dense_cols - 1):
                source_x0 = (
                    (sw - 1) * dense_x / max(1, dense_cols - 1)
                )
                source_x1 = (
                    (sw - 1) * (dense_x + 1)
                    / max(1, dense_cols - 1)
                )
                index = dense_y * dense_cols + dense_x
                p00 = curved[index]
                p10 = curved[index + 1]
                p01 = curved[index + dense_cols]
                p11 = curved[index + dense_cols + 1]
                raster_triangle(
                    (p00, p10, p11),
                    (
                        (source_x0, source_y0),
                        (source_x1, source_y0),
                        (source_x1, source_y1),
                    ),
                )
                raster_triangle(
                    (p00, p11, p01),
                    (
                        (source_x0, source_y0),
                        (source_x1, source_y1),
                        (source_x0, source_y1),
                    ),
                )
        return QImage(
            output.data, cw, ch, output.strides[0], QImage.Format.Format_RGBA8888
        ).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    @staticmethod
    def _qimage_rgba_array(image):
        """PySide6の版差に依存しないQImage→NumPy変換。"""
        converted = image.convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        width, height = converted.width(), converted.height()
        if width <= 0 or height <= 0:
            return np.zeros((0, 0, 4), dtype=np.uint8)

        byte_count = int(converted.sizeInBytes())
        ptr = converted.constBits()
        try:
            ptr.setsize(byte_count)
        except (AttributeError, TypeError):
            pass

        try:
            flat = np.frombuffer(
                ptr,
                dtype=np.uint8,
                count=byte_count,
            )
        except (TypeError, BufferError, ValueError):
            # Python 3.14／一部のPySide6でShibokenのバッファ公開形式が
            #異なる場合に、bytesへ固定して読み取る。
            flat = np.frombuffer(
                bytes(ptr),
                dtype=np.uint8,
                count=byte_count,
            )

        bytes_per_line = int(converted.bytesPerLine())
        expected = height * bytes_per_line
        if flat.size < expected:
            raise ValueError(
                "画像バッファのサイズが不足しています。"
            )

        rows = flat[:expected].reshape(
            (height, bytes_per_line)
        )
        return rows[:, :width * 4].reshape(
            (height, width, 4)
        ).copy()

    @staticmethod
    def _rgba_array_to_qimage(rgba):
        """NumPyの寿命やbuffer仕様に依存しないQImage変換。"""
        rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
        if rgba.ndim != 3 or rgba.shape[2] != 4:
            raise ValueError(
                "RGBA配列は高さ×幅×4である必要があります。"
            )
        height, width = rgba.shape[:2]
        if width <= 0 or height <= 0:
            return QImage()

        stride = int(rgba.strides[0])
        raw = rgba.tobytes(order="C")
        image = QImage(
            raw,
            width,
            height,
            stride,
            QImage.Format.Format_RGBA8888,
        )
        if image.isNull():
            raise ValueError(
                "階調化画像をQImageへ変換できませんでした。"
            )
        return image.copy().convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )

    @staticmethod
    def _pil_l_to_qimage(mask):
        gray = mask.convert("L")
        width, height = gray.size
        raw = gray.tobytes()
        return QImage(
            raw, width, height, width, QImage.Format.Format_Grayscale8
        ).copy()

    @staticmethod
    def _qimage_gray_array(image):
        gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
        width, height = gray.width(), gray.height()
        if width <= 0 or height <= 0:
            return np.zeros((0, 0), dtype=np.uint8)
        ptr = gray.bits()
        try:
            ptr.setsize(gray.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, gray.bytesPerLine())
        )
        return rows[:, :width].copy()

    @classmethod
    def _qimage_to_pil_rgba(cls, image):
        if PILImage is None:
            return None
        rgba = cls._qimage_rgba_array(image)
        if rgba.size == 0:
            return PILImage.new("RGBA", (1, 1), (0, 0, 0, 0))
        return PILImage.fromarray(rgba, "RGBA")

    @classmethod
    def _pil_rgba_to_qimage(cls, image):
        rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
        return cls._rgba_array_to_qimage(rgba)

    @staticmethod
    def _tp_transparent_to_white(image):
        """v0.7 rule: transparent source pixels become opaque #FFFFFF masks."""
        rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
        transparent = rgba[:, :, 3] == 0
        rgba[transparent] = (255, 255, 255, 255)
        return PILImage.fromarray(rgba, "RGBA")

    @staticmethod
    def _tp_white_to_transparent(image):
        """v0.7 rule: exact #FFFFFF is transparent in the TP result."""
        rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
        white = np.all(rgba[:, :, :3] == 255, axis=2)
        rgba[white, 3] = 0
        return PILImage.fromarray(rgba, "RGBA")

    @staticmethod
    def _tp_prepare_palette_image(image, max_colors=64):
        """Port of prepare_palette_image() from the v0.7 prototype."""
        source = image.convert("RGBA")
        colors = source.getcolors(maxcolors=max_colors + 1)
        opaque = (
            [(count, rgba) for count, rgba in colors if rgba[3] > 0]
            if colors is not None
            else []
        )
        if colors is not None and len(opaque) <= max_colors:
            palette = [rgba for _count, rgba in sorted(opaque, reverse=True)]
            return source, palette

        alpha = source.getchannel("A")
        rgb = source.convert("RGB").quantize(
            colors=max_colors,
            method=PILImage.Quantize.MEDIANCUT,
            dither=PILImage.Dither.NONE,
        ).convert("RGBA")
        rgb.putalpha(alpha.point(lambda value: 255 if value >= 96 else 0))
        colors = rgb.getcolors(maxcolors=1_000_000) or []
        palette = [
            rgba for _count, rgba in sorted(
                ((count, rgba) for count, rgba in colors if rgba[3] > 0),
                reverse=True,
            )
        ]
        return rgb, palette

    @staticmethod
    def _tp_make_color_masks(image, palette):
        rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
        return [
            PILImage.fromarray(
                (np.all(rgba == np.asarray(color, dtype=np.uint8), axis=2)
                 * 255).astype(np.uint8),
                "L",
            )
            for color in palette
        ]

    def _prepare_tp_transform_masks(self, source_image=None):
        """Build the same exact-color masks used by TP Mask Transform v0.7."""
        self._clear_tp_transform_masks(clear_proxy=False)
        source = source_image if source_image is not None else self.transform_source
        if (
            PILImage is None or PILImageFilter is None
            or source is None or source.isNull()
        ):
            return False

        source_rgba = self._qimage_to_pil_rgba(source).convert("RGBA")
        prepared = self._tp_transparent_to_white(source_rgba)
        exact_color_counts = prepared.getcolors(maxcolors=257)
        if exact_color_counts is None:
            # This intentionally mirrors v0.7: color-rich images are reduced
            # without dithering.  The large-image proxy uses fewer colors to
            # keep its temporary mask memory bounded.
            prepared, palette_rgba = self._tp_prepare_palette_image(
                source_rgba,
                (
                    TP_MASK_PROXY_MAX_COLORS
                    if self._tp_proxy_rendering
                    else 64
                ),
            )
        else:
            palette_rgba = sorted(
                tuple(int(channel) for channel in color)
                for _count, color in exact_color_counts
                if color[3] > 0
            )

        prepared_array = np.asarray(prepared.convert("RGBA"), dtype=np.uint8)
        line_masks = []
        for line_color in self.transform_tp_line_colors:
            red, green, blue = line_color
            selected = (
                (prepared_array[:, :, 3] > 0)
                & np.all(
                    prepared_array[:, :, :3]
                    == np.asarray((red, green, blue), dtype=np.uint8),
                    axis=2,
                )
            )
            candidate = PILImage.fromarray(
                (selected * 255).astype(np.uint8), "L"
            )
            if candidate.getbbox() is not None:
                line_masks.append((tuple(line_color), candidate))
                palette_rgba = [
                    color for color in palette_rgba
                    if tuple(color[:3]) != tuple(line_color)
                ]

        self.transform_tp_palette = list(palette_rgba)
        self.transform_tp_masks = self._tp_make_color_masks(
            prepared, self.transform_tp_palette
        )
        self.transform_tp_line_masks = line_masks
        self.transform_tp_prepared_preview = self._pil_rgba_to_qimage(
            self._tp_white_to_transparent(prepared)
        )
        self._tp_mask_source_key = self._tp_mask_key(source)
        self._invalidate_tp_preview_cache()
        return bool(self.transform_tp_masks or self.transform_tp_line_masks)

    def _project_transform_source(
        self, source, target_width, target_height, smooth=False
    ):
        canvas_rect = QRectF(0, 0, target_width, target_height)
        if self.transform_mode == "mesh":
            return (
                self._mesh_preview_image(
                    source, target_width, target_height, smooth=smooth
                ),
                canvas_rect,
            )
        if len(self.transform_points) != 4:
            return None, canvas_rect
        width, height = source.width(), source.height()
        if width <= 0 or height <= 0:
            return None, canvas_rect
        source_quad = [
            QPointF(0, 0), QPointF(width, 0),
            QPointF(width, height), QPointF(0, height),
        ]
        transform = self._quad_homography(source_quad, self.transform_points)
        preview = QImage(
            target_width,
            target_height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        preview.fill(Qt.GlobalColor.transparent)
        painter = QPainter(preview)
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform, False
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setTransform(transform)
        painter.drawImage(0, 0, source)
        painter.end()
        return preview, canvas_rect

    def _transform_is_reducing(self, source=None):
        """変形のどこかに縮小があり、補間が必要かを返す。"""
        source = source if source is not None else self.transform_source
        if source is None or source.isNull() or not self.transform_points:
            return False

        if self.transform_mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
            reference = self._mesh_reference_grid(cols, rows)
            if (
                len(self.transform_points) != cols * rows
                or len(reference) != cols * rows
            ):
                return False
            pairs = []
            for row in range(rows):
                for col in range(cols - 1):
                    index = row * cols + col
                    pairs.append((index, index + 1))
            for row in range(rows - 1):
                for col in range(cols):
                    index = row * cols + col
                    pairs.append((index, index + cols))
            for first, second in pairs:
                current = math.hypot(
                    self.transform_points[second].x()
                    - self.transform_points[first].x(),
                    self.transform_points[second].y()
                    - self.transform_points[first].y(),
                )
                original = math.hypot(
                    reference[second].x() - reference[first].x(),
                    reference[second].y() - reference[first].y(),
                )
                if original > 1e-8 and current < original * 0.9999:
                    return True
            return False

        if len(self.transform_points) != 4:
            return False
        source_width = max(1.0, float(source.width()))
        source_height = max(1.0, float(source.height()))
        p0, p1, p2, p3 = self.transform_points[:4]
        horizontal_edges = (
            math.hypot(p1.x() - p0.x(), p1.y() - p0.y()),
            math.hypot(p2.x() - p3.x(), p2.y() - p3.y()),
        )
        vertical_edges = (
            math.hypot(p3.x() - p0.x(), p3.y() - p0.y()),
            math.hypot(p2.x() - p1.x(), p2.y() - p1.y()),
        )
        return (
            min(horizontal_edges) < source_width * 0.9999
            or min(vertical_edges) < source_height * 0.9999
        )

    def _tp_is_enlarging(self):
        source = self.transform_source
        if source is None or source.isNull() or not self.transform_points:
            return False
        source_width = max(1.0, float(source.width()))
        source_height = max(1.0, float(source.height()))

        if self.transform_mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
            if len(self.transform_points) != cols * rows:
                return False
            horizontal = []
            vertical = []
            for row in range(rows):
                for col in range(cols - 1):
                    p0 = self.transform_points[row * cols + col]
                    p1 = self.transform_points[row * cols + col + 1]
                    horizontal.append(math.hypot(p1.x()-p0.x(), p1.y()-p0.y()))
            for row in range(rows - 1):
                for col in range(cols):
                    p0 = self.transform_points[row * cols + col]
                    p1 = self.transform_points[(row + 1) * cols + col]
                    vertical.append(math.hypot(p1.x()-p0.x(), p1.y()-p0.y()))
            expected_x = source_width / max(1, cols - 1)
            expected_y = source_height / max(1, rows - 1)
            scale_x = (sum(horizontal) / len(horizontal)) / expected_x if horizontal else 1.0
            scale_y = (sum(vertical) / len(vertical)) / expected_y if vertical else 1.0
            return scale_x > 1.0001 or scale_y > 1.0001

        if len(self.transform_points) != 4:
            return False
        p0, p1, p2, p3 = self.transform_points[:4]
        scale_x = (
            math.hypot(p1.x()-p0.x(), p1.y()-p0.y())
            + math.hypot(p2.x()-p3.x(), p2.y()-p3.y())
        ) / (2.0 * source_width)
        scale_y = (
            math.hypot(p3.x()-p0.x(), p3.y()-p0.y())
            + math.hypot(p2.x()-p1.x(), p2.y()-p1.y())
        ) / (2.0 * source_height)
        return scale_x > 1.0001 or scale_y > 1.0001

    def _tp_transform_bbox(self, target_width, target_height):
        outline = self.transform_outer_polygon()
        if outline.isEmpty():
            return QRectF(), QRectF()
        raw_bounds = outline.boundingRect()
        canvas = QRectF(0, 0, target_width, target_height)
        expanded = raw_bounds.adjusted(-3, -3, 3, 3).intersected(canvas)
        if expanded.isEmpty():
            return QRectF(), raw_bounds.intersected(canvas)
        left = max(0, int(math.floor(expanded.left())))
        top = max(0, int(math.floor(expanded.top())))
        right = min(target_width, int(math.ceil(expanded.right())))
        bottom = min(target_height, int(math.ceil(expanded.bottom())))
        if right <= left or bottom <= top:
            return QRectF(), raw_bounds.intersected(canvas)
        return (
            QRectF(left, top, right-left, bottom-top),
            raw_bounds.intersected(canvas),
        )

    def _tp_mesh_render_mask(self, source_array, bbox, render_scale, smooth):
        # クオリティ変形も画素補間は行わず、色境界を最近傍で保持する。
        smooth = False
        source_array = np.asarray(source_array, dtype=np.uint8)
        source_height, source_width = source_array.shape
        out_width = max(1, int(math.ceil(bbox.width() * render_scale)))
        out_height = max(1, int(math.ceil(bbox.height() * render_scale)))
        output = np.zeros((out_height, out_width), dtype=np.uint8)
        cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
        rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
        if len(self.transform_points) != cols * rows:
            return output

        points = [
            QPointF(
                (point.x() - bbox.left()) * render_scale,
                (point.y() - bbox.top()) * render_scale,
            )
            for point in self.transform_points
        ]

        def raster_triangle(target_triangle, source_triangle):
            target_x = np.array(
                [point.x() for point in target_triangle], dtype=np.float64
            )
            target_y = np.array(
                [point.y() for point in target_triangle], dtype=np.float64
            )
            min_x = max(0, int(math.floor(float(target_x.min()))))
            max_x = min(out_width - 1, int(math.ceil(float(target_x.max()))))
            min_y = max(0, int(math.floor(float(target_y.min()))))
            max_y = min(out_height - 1, int(math.ceil(float(target_y.max()))))
            if max_x < min_x or max_y < min_y:
                return
            denominator = (
                (target_y[1] - target_y[2]) * (target_x[0] - target_x[2])
                + (target_x[2] - target_x[1]) * (target_y[0] - target_y[2])
            )
            if abs(denominator) < 1e-8:
                return
            yy, xx = np.mgrid[min_y:max_y + 1, min_x:max_x + 1]
            weight0 = (
                (target_y[1] - target_y[2]) * (xx - target_x[2])
                + (target_x[2] - target_x[1]) * (yy - target_y[2])
            ) / denominator
            weight1 = (
                (target_y[2] - target_y[0]) * (xx - target_x[2])
                + (target_x[0] - target_x[2]) * (yy - target_y[2])
            ) / denominator
            weight2 = 1.0 - weight0 - weight1
            inside = (
                (weight0 >= -1e-6)
                & (weight1 >= -1e-6)
                & (weight2 >= -1e-6)
            )
            if not np.any(inside):
                return
            source_x = (
                weight0 * source_triangle[0][0]
                + weight1 * source_triangle[1][0]
                + weight2 * source_triangle[2][0]
            )
            source_y = (
                weight0 * source_triangle[0][1]
                + weight1 * source_triangle[1][1]
                + weight2 * source_triangle[2][1]
            )
            region = output[min_y:max_y + 1, min_x:max_x + 1]
            if smooth:
                source_x = np.clip(source_x, 0.0, source_width - 1.0)
                source_y = np.clip(source_y, 0.0, source_height - 1.0)
                x0 = np.floor(source_x).astype(np.int32)
                y0 = np.floor(source_y).astype(np.int32)
                x1 = np.minimum(source_width - 1, x0 + 1)
                y1 = np.minimum(source_height - 1, y0 + 1)
                fraction_x = source_x - x0
                fraction_y = source_y - y0
                sampled = (
                    source_array[y0, x0].astype(np.float32)
                    * (1.0-fraction_x) * (1.0-fraction_y)
                    + source_array[y0, x1].astype(np.float32)
                    * fraction_x * (1.0-fraction_y)
                    + source_array[y1, x0].astype(np.float32)
                    * (1.0-fraction_x) * fraction_y
                    + source_array[y1, x1].astype(np.float32)
                    * fraction_x * fraction_y
                )
                region[inside] = np.clip(
                    sampled[inside] + 0.5, 0, 255
                ).astype(np.uint8)
            else:
                sample_x = np.clip(
                    np.rint(source_x).astype(np.int32), 0, source_width - 1
                )
                sample_y = np.clip(
                    np.rint(source_y).astype(np.int32), 0, source_height - 1
                )
                region[inside] = source_array[
                    sample_y[inside], sample_x[inside]
                ]

        curved, dense_cols, dense_rows = self._curved_mesh_points(
            points, cols, rows, subdivisions=8
        )
        for dense_y in range(dense_rows - 1):
            source_y0 = (
                (source_height - 1)
                * dense_y / max(1, dense_rows - 1)
            )
            source_y1 = (
                (source_height - 1)
                * (dense_y + 1) / max(1, dense_rows - 1)
            )
            for dense_x in range(dense_cols - 1):
                source_x0 = (
                    (source_width - 1)
                    * dense_x / max(1, dense_cols - 1)
                )
                source_x1 = (
                    (source_width - 1)
                    * (dense_x + 1) / max(1, dense_cols - 1)
                )
                index = dense_y * dense_cols + dense_x
                p00 = curved[index]
                p10 = curved[index + 1]
                p01 = curved[index + dense_cols]
                p11 = curved[index + dense_cols + 1]
                raster_triangle(
                    (p00, p10, p11),
                    (
                        (source_x0, source_y0),
                        (source_x1, source_y0),
                        (source_x1, source_y1),
                    ),
                )
                raster_triangle(
                    (p00, p11, p01),
                    (
                        (source_x0, source_y0),
                        (source_x1, source_y1),
                        (source_x0, source_y1),
                    ),
                )
        return output

    def _tp_project_mask_to_bbox(self, mask, bbox, render_scale=1):
        out_width = max(1, int(math.ceil(bbox.width() * render_scale)))
        out_height = max(1, int(math.ceil(bbox.height() * render_scale)))
        if self.transform_mode == "mesh":
            source_array = np.asarray(mask.convert("L"), dtype=np.uint8)
            rendered = self._tp_mesh_render_mask(
                source_array, bbox, render_scale, smooth=False
            )
            return PILImage.fromarray(rendered, "L")

        output = QImage(
            out_width, out_height, QImage.Format.Format_Grayscale8
        )
        output.fill(0)
        source_width, source_height = mask.size
        source_quad = [
            QPointF(0, 0), QPointF(source_width, 0),
            QPointF(source_width, source_height), QPointF(0, source_height),
        ]
        target_quad = [
            QPointF(
                (point.x() - bbox.left()) * render_scale,
                (point.y() - bbox.top()) * render_scale,
            )
            for point in self.transform_points[:4]
        ]
        transform = self._quad_homography(source_quad, target_quad)
        painter = QPainter(output)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setTransform(transform)
        painter.drawImage(QPointF(0, 0), self._pil_l_to_qimage(mask))
        painter.end()
        return PILImage.fromarray(self._qimage_gray_array(output), "L")

    def _tp_render_mask_to_bbox(
        self, mask, bbox, smoothing=0.0, supersample=4
    ):
        """v0.7 mask renderer, extended to free and mesh transformations."""
        if self._tp_proxy_rendering:
            supersample = 1
        out_width = max(1, int(math.ceil(bbox.width())))
        out_height = max(1, int(math.ceil(bbox.height())))
        if self._tp_is_enlarging():
            source = mask.convert("L")
            if smoothing > 0.001:
                source = source.filter(
                    PILImageFilter.GaussianBlur(radius=float(smoothing))
                )
            return self._tp_project_mask_to_bbox(source, bbox, 1)

        high = self._tp_project_mask_to_bbox(mask.convert("L"), bbox, supersample)
        high = high.filter(
            PILImageFilter.GaussianBlur(radius=0.65 * supersample)
        )
        return high.resize(
            (out_width, out_height), PILImage.Resampling.LANCZOS
        )

    def _tp_geometry_key(self, source, target_width, target_height, bbox):
        points = tuple(
            (round(point.x(), 5), round(point.y(), 5))
            for point in self.transform_points
        )
        return (
            int(source.cacheKey()),
            int(target_width), int(target_height),
            self.transform_mode,
            points,
            int(getattr(self, "transform_mesh_cols", 4)),
            int(getattr(self, "transform_mesh_rows", 4)),
            int(bbox.left()), int(bbox.top()),
            int(bbox.width()), int(bbox.height()),
            tuple(tuple(color) for color in self.transform_tp_palette),
            tuple(tuple(color) for color, _mask in self.transform_tp_line_masks),
        )

    def _build_tp_geometry_cache(
        self, source, target_width, target_height, progress_callback=None
    ):
        bbox, _raw_selection = self._tp_transform_bbox(
            target_width, target_height
        )
        if bbox.isEmpty():
            self._tp_geometry_cache_bbox = QRectF()
            self._tp_geometry_cache_fill_overlay = PILImage.new(
                "RGBA", (1, 1), (0, 0, 0, 0)
            )
            self._tp_geometry_cache_line_soft = []
            return bbox

        cache_key = self._tp_geometry_key(
            source, target_width, target_height, bbox
        )
        if (
            self._tp_geometry_cache_key == cache_key
            and self._tp_geometry_cache_fill_overlay is not None
        ):
            return QRectF(self._tp_geometry_cache_bbox)

        total = max(
            1,
            len(self.transform_tp_masks)
            + len(self.transform_tp_line_masks)
            + 2,
        )
        completed = 0
        if progress_callback is not None:
            progress_callback(
                completed, total, "色マスクの変形を準備しています"
            )

        width = max(1, int(math.ceil(bbox.width())))
        height = max(1, int(math.ceil(bbox.height())))
        best_value = np.zeros((height, width), dtype=np.uint8)
        best_rgba = np.zeros((height, width, 4), dtype=np.uint8)

        # Every exact color, including the temporary white background, competes
        # for each output pixel.  This is the defining v0.7 TP_mask behavior.
        fill_count = len(self.transform_tp_masks)
        for mask_index, (color, mask) in enumerate(zip(
            self.transform_tp_palette, self.transform_tp_masks
        ), 1):
            rendered = self._tp_render_mask_to_bbox(
                mask, bbox, self.transform_tp_fill_smoothing
            )
            values = np.asarray(rendered, dtype=np.uint8)
            replace = values > best_value
            if np.any(replace):
                best_value[replace] = values[replace]
                best_rgba[replace] = np.asarray(color, dtype=np.uint8)
            completed += 1
            if progress_callback is not None:
                progress_callback(
                    completed,
                    total,
                    f"色マスクを変形しています（{mask_index} / {fill_count}）",
                )

        output = np.zeros((height, width, 4), dtype=np.uint8)
        visible = best_value > 0
        if np.any(visible):
            output[visible, :3] = best_rgba[visible, :3]
            output[visible, 3] = 255
        white = visible & np.all(output[:, :, :3] == 255, axis=2)
        output[white, 3] = 0
        completed += 1
        if progress_callback is not None:
            progress_callback(
                completed, total, "色マスクを再合成しています"
            )

        line_soft = []
        line_count = len(self.transform_tp_line_masks)
        for line_index, (line_color, line_mask) in enumerate(
            self.transform_tp_line_masks, 1
        ):
            line_soft.append((
                tuple(line_color),
                self._tp_render_mask_to_bbox(
                    line_mask, bbox, self.transform_tp_line_smoothing
                ),
            ))
            completed += 1
            if progress_callback is not None:
                progress_callback(
                    completed,
                    total,
                    f"実線マスクを変形しています（{line_index} / {line_count}）",
                )

        self._tp_geometry_cache_key = cache_key
        self._tp_geometry_cache_bbox = QRectF(bbox)
        self._tp_geometry_cache_fill_overlay = PILImage.fromarray(
            output, "RGBA"
        )
        self._tp_geometry_cache_line_soft = line_soft
        completed += 1
        if progress_callback is not None:
            progress_callback(
                min(completed, total), total, "プレビューを仕上げています"
            )
        return bbox

    def _tp_mask_preview_image(
        self, source_image=None, target_width=None, target_height=None,
        progress_callback=None
    ):
        source = (
            source_image if source_image is not None else self.transform_source
        )
        if source is None or source.isNull():
            return None, QRectF()
        target_width = int(
            target_width
            if target_width is not None
            else self.active_layer.image.width()
        )
        target_height = int(
            target_height
            if target_height is not None
            else self.active_layer.image.height()
        )
        canvas_rect = QRectF(0, 0, target_width, target_height)

        expected_mask_key = self._tp_mask_key(source)
        if (
            self._tp_mask_source_key != expected_mask_key
            or (
                not self.transform_tp_masks
                and not self.transform_tp_line_masks
            )
        ):
            if not self._prepare_tp_transform_masks(source):
                return self._project_transform_source(
                    source, target_width, target_height, smooth=False
                )

        threshold = max(
            1, min(254, int(getattr(self, "transform_line_threshold", 96)))
        )
        bbox, raw_selection = self._tp_transform_bbox(
            target_width, target_height
        )
        point_key = tuple(
            (round(point.x(), 5), round(point.y(), 5))
            for point in self.transform_points
        )
        preview_key = (
            int(source.cacheKey()), target_width, target_height,
            self.transform_mode, point_key,
            int(getattr(self, "transform_mesh_cols", 4)),
            int(getattr(self, "transform_mesh_rows", 4)),
            threshold,
            tuple(self.transform_tp_line_colors),
        )
        if (
            source is self.transform_source
            and self._tp_preview_cache_key == preview_key
            and self._tp_preview_cache_image is not None
        ):
            return self._tp_preview_cache_image, canvas_rect

        bbox = self._build_tp_geometry_cache(
            source, target_width, target_height, progress_callback
        )
        full = QImage(
            target_width, target_height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        full.fill(Qt.GlobalColor.transparent)
        if not bbox.isEmpty():
            width = max(1, int(math.ceil(bbox.width())))
            height = max(1, int(math.ceil(bbox.height())))
            overlay = self._tp_geometry_cache_fill_overlay.copy()
            for line_color, line_soft in self._tp_geometry_cache_line_soft:
                binary = line_soft.point(
                    lambda value, limit=threshold:
                    255 if value >= limit else 0
                )
                red, green, blue = line_color
                line_fill = PILImage.new(
                    "RGBA", (width, height), (red, green, blue, 255)
                )
                overlay.paste(line_fill, (0, 0), binary)
            painter = QPainter(full)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )
            painter.drawImage(
                QPointF(math.floor(bbox.left()), math.floor(bbox.top())),
                self._pil_rgba_to_qimage(overlay),
            )
            painter.end()

        if source is self.transform_source:
            self._tp_preview_cache_key = preview_key
            self._tp_preview_cache_image = full
        return full, canvas_rect

    def _proxy_transform_preview_image(
        self,
        source,
        target_width,
        target_height,
        quality,
        progress_callback=None,
    ):
        """大画像の表示用変形を縮小座標で生成し、UIの負荷を抑える。"""
        scale = min(
            1.0,
            TP_MASK_PROXY_MAX_DIMENSION
            / float(max(1, target_width, target_height)),
        )
        proxy_width = max(1, int(round(target_width * scale)))
        proxy_height = max(1, int(round(target_height * scale)))
        scale_x = proxy_width / float(max(1, target_width))
        scale_y = proxy_height / float(max(1, target_height))
        source_width = max(1, int(round(source.width() * scale_x)))
        source_height = max(1, int(round(source.height() * scale_y)))
        proxy_source_key = (
            int(source.cacheKey()),
            source_width,
            source_height,
        )
        if (
            self._tp_proxy_source_key != proxy_source_key
            or self._tp_proxy_source_image is None
            or self._tp_proxy_source_image.isNull()
        ):
            self._tp_proxy_source_image = source.scaled(
                source_width,
                source_height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._tp_proxy_source_key = proxy_source_key

        proxy_source = self._tp_proxy_source_image
        original_source = self.transform_source
        original_points = self.transform_points
        original_source_rect = self.transform_source_rect
        original_proxy_rendering = self._tp_proxy_rendering
        self.transform_source = proxy_source
        self.transform_points = [
            QPointF(point.x() * scale_x, point.y() * scale_y)
            for point in original_points
        ]
        if original_source_rect is not None:
            self.transform_source_rect = QRectF(
                original_source_rect.x() * scale_x,
                original_source_rect.y() * scale_y,
                original_source_rect.width() * scale_x,
                original_source_rect.height() * scale_y,
            )
        self._tp_proxy_rendering = True
        try:
            if quality:
                return self._tp_mask_preview_image(
                    proxy_source,
                    proxy_width,
                    proxy_height,
                    progress_callback=progress_callback,
                )
            return self._project_transform_source(
                proxy_source,
                proxy_width,
                proxy_height,
                smooth=False,
            )
        finally:
            self.transform_source = original_source
            self.transform_points = original_points
            self.transform_source_rect = original_source_rect
            self._tp_proxy_rendering = original_proxy_rendering

    def transform_preview_image(
        self,
        source_image=None,
        target_width=None,
        target_height=None,
        quality=None,
        preview_only=False,
        progress_callback=None,
    ):
        source = source_image if source_image is not None else self.transform_source
        if not self.transform_active or source is None:
            return None, QRectF()
        target_width = int(
            target_width if target_width is not None else self.active_layer.image.width()
        )
        target_height = int(
            target_height if target_height is not None else self.active_layer.image.height()
        )
        if quality is None:
            quality = bool(getattr(self, "transform_quality_active", False))
        if (
            preview_only
            and self._tp_uses_proxy(target_width, target_height)
        ):
            return self._proxy_transform_preview_image(
                source,
                target_width,
                target_height,
                bool(quality),
                progress_callback=progress_callback,
            )
        if quality:
            return self._tp_mask_preview_image(
                source,
                target_width,
                target_height,
                progress_callback=progress_callback,
            )
        return self._project_transform_source(
            source,
            target_width,
            target_height,
            smooth=False,
        )

    def _selection_source_from_image(self, image):
        rect = self.transform_source_rect.toAlignedRect().intersected(image.rect())
        if rect.isEmpty():
            return None
        source = image.copy(rect)
        if self.selection_polygon:
            if self.selection_mask_override is not None:
                mask = self._bool_mask_image(self.selection_mask_bool(
                    rect.width(), rect.height(), rect.x(), rect.y()
                ))
                painter = QPainter(source)
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_DestinationIn
                )
                painter.drawImage(0, 0, mask)
                painter.end()
                return source
            mask = QImage(rect.width(), rect.height(), QImage.Format.Format_ARGB32_Premultiplied)
            mask.fill(Qt.GlobalColor.transparent)
            painter = QPainter(mask)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 255))
            painter.drawPolygon(QPolygonF([
                QPointF(point.x() - rect.x(), point.y() - rect.y())
                for point in self.selection_polygon
            ]))
            painter.end()
            painter = QPainter(source)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.drawImage(0, 0, mask)
            painter.end()
        return source

    def _cleared_selection_base(self, image):
        base = image.copy()
        painter = QPainter(base)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 255))
        if self.selection_mask_override is not None:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_DestinationOut
            )
            painter.drawImage(
                0, 0, self._bool_mask_image(self.selection_mask_override)
            )
        elif self.selection_polygon:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
            )
            painter.drawPolygon(QPolygonF(self.selection_polygon))
        elif self.transform_source_rect is not None:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
            )
            painter.fillRect(self.transform_source_rect, QColor(0, 0, 0, 255))
        painter.end()
        return base

    def _reset_selection_transform(self):
        self.transform_active = False
        self.transform_mode = None
        self.transform_original_layer = None
        self.transform_original_has_content = False
        self.transform_source = None
        self.transform_source_rect = None
        self.transform_points = []
        self.transform_mesh_reference_points = []
        self.transform_handle = -1
        self.transform_drag_kind = None
        self.transform_drag_points = []
        self.transform_quality_active = False
        self.transform_tp_line_colors = ()
        self._clear_tp_transform_masks()

    def commit_selection_transform(self, all_frames=None):
        if not self.transform_active:
            return
        window = self.window()
        quality_active = bool(getattr(self, "transform_quality_active", False))
        if all_frames is None:
            all_frames = bool(
                window.tools.selection_all_frames.isChecked()
                if hasattr(window, "tools")
                else getattr(self, "transform_apply_all_frames", False)
            )
        if quality_active:
            all_frames = False
            target_width = self.active_layer.image.width()
            target_height = self.active_layer.image.height()
            if self._tp_uses_proxy(target_width, target_height):
                # 大画像の表示中キャッシュは縮小代理画像なので、確定時だけ
                # 原寸マスクを生成する。代理画像を確定結果へ流用しない。
                self._clear_tp_transform_masks(clear_proxy=False)
                self.refresh_quality_preview_with_counter(
                    "変形確定用の原寸クオリティ画像を生成しています",
                    full_resolution=True,
                )
            elif self._tp_preview_cache_image is None:
                self.refresh_quality_preview_with_counter(
                    "変形確定用のクオリティ画像を生成しています"
                )
            if self._tp_preview_cache_image is None:
                self.status_message.emit(
                    "クオリティ画像を生成できなかったため、変形確定を中止しました。"
                )
                return

        layer_index = int(getattr(self, "transform_layer_index", self.active_layer_index))
        current_index = int(getattr(self, "transform_frame_index", self.current_frame))
        original_current = self.transform_original_layer
        original_current_has_content = getattr(
            self, "transform_original_has_content", self.active_layer.has_content
        )
        frame_indices = list(
            range(len(self.frames)) if all_frames else [current_index]
        )
        undo_cells = []
        changed_frames = []
        progress = None
        if all_frames and hasattr(window, "create_progress_counter"):
            progress = window.create_progress_counter(
                "すべてのコマに変形",
                len(frame_indices),
                "変形を準備しています",
            )

        for progress_index, frame_index in enumerate(frame_indices, 1):
            if progress is not None:
                window.update_progress_counter(
                    progress,
                    progress_index - 1,
                    len(frame_indices),
                    f"コマ {frame_index + 1} を変形しています",
                )
            if not (0 <= frame_index < len(self.frames)):
                continue
            frame = self.frames[frame_index]
            if not (0 <= layer_index < len(frame.layers)):
                continue
            layer = frame.layers[layer_index]
            if frame_index == current_index and original_current is not None:
                original = original_current.copy()
                original_has_content = original_current_has_content
            else:
                if not layer.has_content:
                    continue
                original = layer.image.copy()
                original_has_content = layer.has_content

            source = (
                self.transform_source
                if quality_active and frame_index == current_index
                and self.transform_source is not None
                else self._selection_source_from_image(original)
            )
            if source is None or source.isNull():
                continue
            preview, _target = self.transform_preview_image(
                source, original.width(), original.height(),
                quality=quality_active,
            )
            if preview is None:
                continue

            base = self._cleared_selection_base(original)
            painter = QPainter(base)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawImage(0, 0, preview)
            painter.end()

            undo_cells.append((frame_index, original.copy(), original_has_content))
            layer.image = base
            layer.has_content = True
            changed_frames.append(frame_index)

        if progress is not None:
            window.close_progress_counter(progress)

        for frame_index in changed_frames:
            self.sync_numbered_image_from_cell(
                frame_index,
                layer_index,
            )

        if undo_cells:
            if len(undo_cells) == 1:
                frame_index, image, has_content = undo_cells[0]
                self.undo_stack.append((
                    "layer", frame_index, layer_index, image, has_content
                ))
            else:
                self.undo_stack.append(("layer_batch", layer_index, undo_cells))
            self.undo_stack = self.undo_stack[-MAX_UNDO:]
            self.redo_stack.clear()
            # 変形ではコマ構造と使用色の種類は変わらないため、
            # タイムライン全再構築・使用色全走査は行わない。
        elif (
            original_current is not None
            and 0 <= current_index < len(self.frames)
            and 0 <= layer_index < len(self.frames[current_index].layers)
        ):
            layer = self.frames[current_index].layers[layer_index]
            layer.image = original_current
            layer.has_content = original_current_has_content

        self._reset_selection_transform()
        self.transform_apply_all_frames = False
        # V62: a confirmed transform finishes the operation completely.
        self.selection_polygon = []
        self.selection_mask_override = None
        self.selection_outline_polygons = []
        self.selection_mask_rect = None
        self.lasso = []
        self.rect_start = None
        self.rect_end = None
        self.selectionChanged.emit()
        self._update_selection_clear_overlay()
        self.update()

    def cancel_selection_transform(self):
        if not self.transform_active:
            return
        if self.transform_original_layer is not None:
            frame_index = int(getattr(self, "transform_frame_index", self.current_frame))
            layer_index = int(getattr(self, "transform_layer_index", self.active_layer_index))
            if (
                0 <= frame_index < len(self.frames)
                and 0 <= layer_index < len(self.frames[frame_index].layers)
            ):
                layer = self.frames[frame_index].layers[layer_index]
                layer.image = self.transform_original_layer
                layer.has_content = getattr(
                    self, "transform_original_has_content", layer.has_content
                )
        self._reset_selection_transform()
        self.transform_apply_all_frames = False
        self.update()

    def selection_bounds(self):
        if not self.selection_polygon:
            return QRectF(0,0,self.active_layer.image.width(),self.active_layer.image.height()).toAlignedRect()
        if self.selection_mask_rect is not None:
            return QRectF(self.selection_mask_rect).toAlignedRect()
        xs=[p.x() for p in self.selection_polygon]; ys=[p.y() for p in self.selection_polygon]
        return QRectF(min(xs),min(ys),max(xs)-min(xs),max(ys)-min(ys)).toAlignedRect()

    def copy_selection(self):
        rect=self.selection_bounds().intersected(self.active_layer.image.rect())
        if rect.isEmpty(): return
        QApplication.clipboard().setImage(self.active_layer.image.copy(rect))

    def cut_selection(self):
        rect=self.selection_bounds().intersected(self.active_layer.image.rect())
        if rect.isEmpty(): return
        self.push_layer_undo()
        QApplication.clipboard().setImage(self.active_layer.image.copy(rect))
        p=QPainter(self.active_layer.image)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        if self.selection_polygon:
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(0,0,0,255));p.drawPolygon(QPolygonF(self.selection_polygon))
        else:p.fillRect(rect,QColor(0,0,0,255))
        p.end();self.cellChanged.emit(self.current_frame,self.active_layer_index);self.update()

    def paste_clipboard(self):
        image=QApplication.clipboard().image()
        if image.isNull(): return
        self.ensure_editable_key()
        self.push_layer_undo()
        rect=self.selection_bounds()
        pos=rect.topLeft() if self.selection_polygon else QPoint(OUTSIDE_MARGIN,OUTSIDE_MARGIN)
        p=QPainter(self.active_layer.image);p.drawImage(pos,image);p.end()
        self.active_layer.has_content=True
        self.cellChanged.emit(self.current_frame,self.active_layer_index);self.update()

    def sample_color(self, p, include_canvas_background=False):
        x, y = int(p.x()), int(p.y())
        width, height = workspace_size()
        if not (0 <= x < width and 0 <= y < height):
            return

        # 上から順に実画像を調べる。レイヤー表示不透明度は色データへ
        # 混ぜず、スポイトでは元のRGBA色を取得する。
        color = None
        layer_count = len(self.frames[self.current_frame].layers)
        for layer_index in range(layer_count - 1, -1, -1):
            key_frame = self.resolve_key_frame(
                self.current_frame,
                layer_index,
            )
            if key_frame is None:
                continue
            layer = self.frames[key_frame].layers[layer_index]
            if not layer.visible or float(layer.opacity) <= 0.0:
                continue
            source = layer.image
            if not (0 <= x < source.width() and 0 <= y < source.height()):
                continue
            # 使用色の表示OFFなどで見えていない画素は飛ばすが、
            # 表示不透明度やシルエット色はスポイト色へ混ぜない。
            display_source = self._display_layer_image(layer, layer_index)
            if display_source.pixelColor(x, y).alpha() == 0:
                continue
            candidate = source.pixelColor(x, y)
            if candidate.alpha() > 0:
                color = QColor(candidate)
                break

        if color is None or color.alpha() == 0:
            if not include_canvas_background:
                return
            color = QColor(self.transparent_display_color)
            if not color.isValid():
                return
        color.setAlpha(255)
        if self.color_mode == "sub":
            self.sub_color = QColor(color)
        else:
            self.main_color = QColor(color)
            if self.color_mode == "transparent":
                self.color_mode = "main"
        self.colorSampled.emit(QColor(color))
        self.update()

    def _pseudo_transparent_display_image(self, image):
        """#FFFFFFを表示上だけ透明化し、他の可視画素はα255で表示する。"""
        if image is None or image.isNull():
            return image
        try:
            key = (
                int(image.cacheKey()),
                image.width(),
                image.height(),
            )
        except Exception:
            key = (id(image), image.width(), image.height())

        cached = self._pseudo_transparency_cache.get(key)
        if cached is not None:
            return cached

        rgba = image.convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        width, height = rgba.width(), rgba.height()
        if width <= 0 or height <= 0:
            return rgba

        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(
            ptr,
            dtype=np.uint8,
        ).reshape((height, rgba.bytesPerLine()))
        pixels = rows[:, :width * 4].reshape(
            (height, width, 4)
        )
        alpha = pixels[:, :, 3]
        present = alpha > 0
        white = (
            present
            & (pixels[:, :, 0] == 255)
            & (pixels[:, :, 1] == 255)
            & (pixels[:, :, 2] == 255)
        )
        alpha[white] = 0
        alpha[present & ~white] = 255

        result = rgba.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        if len(self._pseudo_transparency_cache) >= 96:
            self._pseudo_transparency_cache.pop(
                next(iter(self._pseudo_transparency_cache))
            )
        self._pseudo_transparency_cache[key] = result
        return result

    def silhouette_layer_image(self, layer, apply_palette_filter=True):
        base = self.filtered_layer_image(
            layer,
            apply_palette_filter,
        )
        if not layer.is_paper:
            base = self._pseudo_transparent_display_image(base)
        if not self.silhouette_non_background:
            return base
        try:
            key = (int(base.cacheKey()), base.width(), base.height())
        except Exception:
            key = (id(base), base.width(), base.height())
        cached = self._silhouette_cache.get(key)
        if cached is not None:
            return cached
        rgba = base.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = rgba.width(), rgba.height()
        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape((height, rgba.bytesPerLine()))
        pixels = rows[:, :width * 4].reshape((height, width, 4))
        visible = pixels[:, :, 3] > 0
        pixels[:, :, 0][visible] = 0
        pixels[:, :, 1][visible] = 0
        pixels[:, :, 2][visible] = 0
        result = rgba.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        if len(self._silhouette_cache) >= 64:
            self._silhouette_cache.clear()
        self._silhouette_cache[key] = result
        return result

    def filtered_layer_image(self, layer, apply_palette_filter=True):
        """Return a cached display-only image honoring this layer's palette visibility."""
        visible = (
            getattr(self, "visible_color_rgbs", None)
            if apply_palette_filter else None
        )
        legacy_rgb = (
            tuple(int(value) for value in layer.color_filter_rgb[:3])
            if getattr(layer, "color_filter_enabled", False) and layer.color_filter_rgb is not None
            else None
        )
        if visible is None and legacy_rgb is None:
            return layer.image
        rgb = legacy_rgb
        try:
            image_key = int(layer.image.cacheKey())
        except Exception:
            image_key = id(layer.image)
        width, height = layer.image.width(), layer.image.height()
        visible_key = None if visible is None else tuple(sorted(visible))
        key = (image_key, width, height, rgb, visible_key)
        cached = self._color_filter_cache.get(key)
        if cached is not None:
            return cached

        index_key = (image_key, width, height)
        indexed = self._color_index_cache.get(index_key)
        if indexed is None:
            base_rgba = layer.image.convertToFormat(QImage.Format.Format_RGBA8888)
            if width <= 0 or height <= 0:
                return base_rgba
            base_ptr = base_rgba.bits()
            try:
                base_ptr.setsize(base_rgba.sizeInBytes())
            except AttributeError:
                pass
            base_rows = np.frombuffer(base_ptr, dtype=np.uint8).reshape(
                (height, base_rgba.bytesPerLine())
            )
            base_pixels = base_rows[:, :width * 4].reshape((height, width, 4))
            opaque = (base_pixels[:, :, 3] > 0).copy()
            packed = (
                (base_pixels[:, :, 0].astype(np.uint32) << 16)
                | (base_pixels[:, :, 1].astype(np.uint32) << 8)
                | base_pixels[:, :, 2].astype(np.uint32)
            )
            indexed = (base_rgba.copy(), packed, opaque)
            if len(self._color_index_cache) >= 24:
                self._color_index_cache.pop(next(iter(self._color_index_cache)))
            self._color_index_cache[index_key] = indexed

        base_rgba, packed, opaque = indexed
        rgba = base_rgba.copy()
        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape((height, rgba.bytesPerLine()))
        pixels = rows[:, :width * 4].reshape((height, width, 4))

        if visible is not None:
            selected_values = np.fromiter(
                ((r << 16) | (g << 8) | b for r, g, b in visible),
                dtype=np.uint32,
                count=len(visible),
            )
            keep = opaque & np.isin(packed, selected_values)
        else:
            keep = opaque.copy()
        if rgb is not None:
            packed_target = (
                (int(rgb[0]) << 16) | (int(rgb[1]) << 8) | int(rgb[2])
            )
            keep &= opaque & (packed == packed_target)
        pixels[:, :, 3][~keep] = 0

        filtered = rgba.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        if len(self._color_filter_cache) >= 96:
            self._color_filter_cache.pop(next(iter(self._color_filter_cache)))
        self._color_filter_cache[key] = filtered
        return filtered

    def _display_layer_image(self, layer, layer_index):
        """白を疑似透明化した、表示専用のレイヤー画像を返す。"""
        apply_palette_filter = (
            layer_index == self.active_layer_index
        )
        if (
            self.silhouette_non_background
            and not layer.is_paper
        ):
            return self.silhouette_layer_image(
                layer,
                apply_palette_filter,
            )

        draw_image = self.filtered_layer_image(
            layer,
            apply_palette_filter,
        )
        if not layer.is_paper:
            draw_image = self._pseudo_transparent_display_image(
                draw_image
            )
        return draw_image

    def composite(self, fi, white=True):
        result = blank_image(QColor("white") if white else Qt.GlobalColor.transparent)
        painter = QPainter(result)
        layer_count = len(self.frames[fi].layers)
        for layer_index in range(layer_count):
            key_frame = self.resolve_key_frame(fi, layer_index)
            if key_frame is None:
                continue
            layer = self.frames[key_frame].layers[layer_index]
            if layer.visible:
                painter.setOpacity(layer.opacity)
                painter.drawImage(
                    0, 0, self._display_layer_image(layer, layer_index)
                )
        painter.end()
        return result

    def _build_playback_key_map(self):
        """各フレームで表示するキーフレームを事前解決する。"""
        frame_count = len(self.frames)
        if frame_count <= 0:
            self._playback_resolved_keys = []
            return

        layer_count = max(
            (len(frame.layers) for frame in self.frames),
            default=0,
        )
        resolved = [
            [None] * layer_count
            for _ in range(frame_count)
        ]

        for layer_index in range(layer_count):
            current_key = None
            exposure_end = -1
            for frame_index in range(frame_count):
                if layer_index >= len(
                    self.frames[frame_index].layers
                ):
                    current_key = None
                    exposure_end = -1
                    continue

                layer = self.frames[
                    frame_index
                ].layers[layer_index]
                if layer.has_content:
                    current_key = frame_index
                    exposure_end = (
                        frame_index
                        + max(1, int(layer.exposure))
                    )
                elif frame_index >= exposure_end:
                    current_key = None

                resolved[frame_index][layer_index] = current_key

        self._playback_resolved_keys = resolved

    def set_playback_active(self, active):
        self._playback_active = bool(active)
        self._playback_frame_cache.clear()
        if self._playback_active:
            self._build_playback_key_map()
        else:
            self._playback_resolved_keys = []
        self.update()

    def playback_advance(self, steps=1):
        """UI全体を再構築せず、再生フレームだけを進める。"""
        if not self.frames:
            return
        self.current_frame = (
            self.current_frame + max(1, int(steps))
        ) % len(self.frames)
        self.update()

    def _playback_cache_dimensions(self, target_rect):
        source_width = max(1, int(round(target_rect.width())))
        source_height = max(1, int(round(target_rect.height())))

        # 大画像でも再生用キャッシュは表示領域程度までに制限する。
        max_width = max(480, min(1920, int(self.width() * 1.25)))
        max_height = max(320, min(1080, int(self.height() * 1.25)))
        scale = min(
            1.0,
            max_width / float(source_width),
            max_height / float(source_height),
        )
        return (
            max(1, int(round(source_width * scale))),
            max(1, int(round(source_height * scale))),
        )

    def playback_frame_image(self, frame_index, target_rect):
        if not (0 <= frame_index < len(self.frames)):
            return None

        cache_width, cache_height = (
            self._playback_cache_dimensions(target_rect)
        )
        cache_key = (
            int(frame_index),
            int(cache_width),
            int(cache_height),
            int(self.active_layer_index),
            bool(self.flip_horizontal),
            bool(self.silhouette_non_background),
        )
        cached = self._playback_frame_cache.get(cache_key)
        if cached is not None:
            # 最近使ったフレームを末尾へ移す簡易LRU。
            self._playback_frame_cache.pop(cache_key, None)
            self._playback_frame_cache[cache_key] = cached
            return cached

        image = QImage(
            cache_width,
            cache_height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            False,
        )
        destination = QRectF(
            0.0,
            0.0,
            float(cache_width),
            float(cache_height),
        )

        layer_count = len(self.frames[frame_index].layers)
        resolved_row = (
            self._playback_resolved_keys[frame_index]
            if frame_index < len(self._playback_resolved_keys)
            else []
        )
        for layer_index in range(layer_count):
            key_frame = (
                resolved_row[layer_index]
                if layer_index < len(resolved_row)
                else self.resolve_key_frame(
                    frame_index, layer_index
                )
            )
            if key_frame is None:
                continue

            layer = self.frames[
                key_frame
            ].layers[layer_index]
            if not layer.visible:
                continue

            draw_image = self._display_layer_image(
                layer, layer_index
            )
            if self.flip_horizontal:
                draw_image = draw_image.mirrored(True, False)
            painter.setOpacity(float(layer.opacity))
            painter.drawImage(destination, draw_image)

        painter.end()

        self._playback_frame_cache[cache_key] = image
        while (
            len(self._playback_frame_cache)
            > self._playback_cache_limit
        ):
            oldest_key = next(iter(self._playback_frame_cache))
            self._playback_frame_cache.pop(oldest_key, None)
        return image

    def draw_frame_direct(self, painter, target_rect, frame_index):
        """Draw the current frame directly to the widget, avoiding a full-size
        temporary composite on every brush mouse move."""
        layer_count = len(self.frames[frame_index].layers)
        for layer_index in range(layer_count):
            key_frame = self.resolve_key_frame(frame_index, layer_index)
            if key_frame is None:
                continue
            layer = self.frames[key_frame].layers[layer_index]
            if not layer.visible:
                continue
            draw_image = self._display_layer_image(layer, layer_index)
            if self.flip_horizontal:
                draw_image = draw_image.mirrored(True, False)
            painter.setOpacity(layer.opacity)
            painter.drawImage(target_rect, draw_image)
        painter.setOpacity(1.0)
    def wheelEvent(self,e):
        old=self.zoom; factor=1.15 if e.angleDelta().y()>0 else 1/1.15; self.zoom=max(.05,min(8.0,self.zoom*factor)); cur=e.position(); self.pan=cur-(cur-self.pan)*(self.zoom/old); self.update(); e.accept()
    def keyPressEvent(self,e):
        if (
            self._onion_interaction_mode is not None
            and e.key() == Qt.Key.Key_Escape
        ):
            self.cancel_onion_interaction(restore=True)
            e.accept()
            return

        if self.transform_active and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            window = self.window()
            if hasattr(window, "commit_transform_or_tween"):
                window.commit_transform_or_tween()
            else:
                self.commit_selection_transform()
            e.accept()
            return
        if self.transform_active and e.key() == Qt.Key.Key_Escape:
            window = self.window()
            if hasattr(window, "cancel_transform_or_tween"):
                window.cancel_transform_or_tween()
            else:
                self.cancel_selection_transform()
            e.accept()
            return
        if self.effective_tool() == "line" and self.line_curve_stage == 2:
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.commit_line_tool()
                e.accept()
                return
            if e.key() == Qt.Key.Key_Escape:
                self.line_start = None
                self.line_end = None
                self.line_control = None
                self.line_curve_stage = 0
                self.drawing = False
                self.update()
                e.accept()
                return
        super().keyPressEvent(e)
    def keyReleaseEvent(self,e):
        super().keyReleaseEvent(e)
    def _active_tool_panel(self):
        window = self.window()
        return window.tools if hasattr(window, "tools") else None

    def _prepare_draw_painter(self, painter, transparent=False):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.apply_selection_clip(painter)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Source
        )

    def _current_stroke_color(self):
        color = self.opaque_paint_color()
        return color, self.is_pseudo_transparent_color(color)

    def _line_preview_path(self):
        if self.line_start is None or self.line_end is None:
            return None
        path = QPainterPath(QPointF(self.line_start))
        if self.line_curve_stage == 2 and self.line_control is not None:
            path.quadTo(QPointF(self.line_control), QPointF(self.line_end))
        else:
            path.lineTo(QPointF(self.line_end))
        return path

    def _draw_tapered_path(
        self,
        painter,
        path,
        color,
        base_width,
        start_width=None,
        end_width=None,
        start_curve=1.0,
        end_curve=1.0,
    ):
        """入り抜き幅を変化させながら、非AA線分として描画する。"""
        try:
            length = max(1.0, float(path.length()))
        except Exception:
            length = max(
                1.0,
                math.hypot(
                    self.line_end.x() - self.line_start.x(),
                    self.line_end.y() - self.line_start.y(),
                ),
            )
        steps = max(2, min(4096, int(math.ceil(length * 1.5))))
        previous = path.pointAtPercent(0.0)
        for index in range(1, steps + 1):
            ratio = index / steps
            point = path.pointAtPercent(ratio)
            width = float(base_width)
            if start_width is not None and ratio <= 0.5:
                local = max(0.0, min(1.0, ratio * 2.0))
                eased = local ** max(0.05, float(start_curve))
                width = float(start_width) + (
                    float(base_width) - float(start_width)
                ) * eased
            elif end_width is not None and ratio >= 0.5:
                local = max(0.0, min(1.0, (ratio - 0.5) * 2.0))
                eased = local ** max(0.05, float(end_curve))
                width = float(base_width) + (
                    float(end_width) - float(base_width)
                ) * eased
            width = max(0.5, width)
            pen = QPen(
                color,
                width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            painter.drawLine(previous, point)
            previous = point

    def commit_line_tool(self):
        path = self._line_preview_path()
        if path is None:
            return
        self.ensure_editable_key()
        self.push_layer_undo()
        panel = self._active_tool_panel()
        base_width = max(0.5, float(self.pen_size))
        start_width = None
        end_width = None
        start_curve = 1.0
        end_curve = 1.0
        if panel is not None:
            if panel.line_taper_in.isChecked():
                start_width = (
                    panel.line_taper_in_size.value() / 2.0
                )
                start_curve = (
                    panel.line_taper_in_curve.value() / 100.0
                )
            if panel.line_taper_out.isChecked():
                end_width = (
                    panel.line_taper_out_size.value() / 2.0
                )
                end_curve = (
                    1.0
                    / max(
                        0.05,
                        panel.line_taper_out_curve.value() / 100.0,
                    )
                )

        source_color = self.paint_source_color()
        base_image = self.active_layer.image.copy()
        overlay = QImage(
            self.active_layer.image.width(),
            self.active_layer.image.height(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        overlay.fill(Qt.GlobalColor.transparent)

        painter = QPainter(overlay)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            False,
        )
        self.apply_selection_clip(painter)
        if start_width is None and end_width is None:
            painter.setPen(
                QPen(
                    source_color,
                    base_width,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.drawPath(path)
        else:
            self._draw_tapered_path(
                painter,
                path,
                source_color,
                base_width,
                start_width,
                end_width,
                start_curve,
                end_curve,
            )
        painter.end()

        colors = self._blend_overlay_into_active_layer(
            overlay,
            QPoint(0, 0),
            base_image=base_image,
            opacity=self.pen_opacity,
            exact_colors=(source_color,),
        )
        self._emit_actual_paint_colors(colors)

        self.active_layer.has_content = True
        self.cellChanged.emit(
            self.current_frame,
            self.active_layer_index,
        )
        self.line_start = None
        self.line_end = None
        self.line_control = None
        self.line_curve_stage = 0
        self.drawing = False
        self.update()

    def _shape_rect(self):
        if self.shape_start is None or self.shape_end is None:
            return QRectF()
        start = QPointF(self.shape_start)
        end = QPointF(self.shape_end)
        panel = self._active_tool_panel()
        if panel is not None and panel.shape_lock_ratio.isChecked():
            dx = end.x() - start.x()
            dy = end.y() - start.y()
            size = max(abs(dx), abs(dy))
            end = QPointF(
                start.x() + (size if dx >= 0 else -size),
                start.y() + (size if dy >= 0 else -size),
            )
        return QRectF(start, end).normalized()

    def _shape_path(self):
        rect = self._shape_rect()
        if rect.isEmpty():
            return None
        panel = self._active_tool_panel()
        shape_type = panel.shape_type.currentText() if panel is not None else "多角形"
        path = QPainterPath()
        if shape_type == "楕円":
            path.addEllipse(rect)
            return path

        corners = max(3, int(panel.shape_corners.value()) if panel is not None else 4)
        center = rect.center()
        radius_x = rect.width() / 2.0
        radius_y = rect.height() / 2.0
        if corners == 4:
            # 4角はひし形ではなく、ドラッグ範囲に沿う□（長方形）にする。
            points = [
                QPointF(rect.left(), rect.top()),
                QPointF(rect.right(), rect.top()),
                QPointF(rect.right(), rect.bottom()),
                QPointF(rect.left(), rect.bottom()),
            ]
        else:
            points = []
            for index in range(corners):
                angle = -math.pi / 2.0 + (2.0 * math.pi * index / corners)
                points.append(
                    QPointF(
                        center.x() + math.cos(angle) * radius_x,
                        center.y() + math.sin(angle) * radius_y,
                    )
                )
        if points:
            path.moveTo(points[0])
            for point in points[1:]:
                path.lineTo(point)
            path.closeSubpath()
        return path

    def commit_shape_tool(self):
        path = self._shape_path()
        if path is None:
            self.shape_start = None
            self.shape_end = None
            self.drawing = False
            self.update()
            return

        self.ensure_editable_key()
        self.push_layer_undo()
        panel = self._active_tool_panel()
        use_split_colors = bool(
            panel is not None
            and panel.shape_sub_outline_main_fill.isChecked()
        )
        fill_inside = bool(
            panel is not None
            and panel.shape_fill_inside.isChecked()
        )
        outline_width = (
            panel.shape_outline_width.value() / 2.0
            if panel is not None
            else 1.0
        )

        if use_split_colors:
            outline_color = self.paint_source_color(
                self.sub_color
            )
            fill_color = self.paint_source_color(
                self.main_color
            )
        else:
            outline_color = self.paint_source_color()
            fill_color = QColor(outline_color)

        base_image = self.active_layer.image.copy()
        overlay = QImage(
            self.active_layer.image.width(),
            self.active_layer.image.height(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        overlay.fill(Qt.GlobalColor.transparent)

        painter = QPainter(overlay)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            False,
        )
        self.apply_selection_clip(painter)
        if fill_inside:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill_color)
            painter.drawPath(path)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                outline_color,
                max(0.5, float(outline_width)),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawPath(path)
        painter.end()

        exact_shape_colors = (
            (outline_color, fill_color)
            if fill_inside
            else (outline_color,)
        )
        colors = self._blend_overlay_into_active_layer(
            overlay,
            QPoint(0, 0),
            base_image=base_image,
            opacity=self.pen_opacity,
            exact_colors=exact_shape_colors,
        )
        self._emit_actual_paint_colors(colors)

        self.active_layer.has_content = True
        self.cellChanged.emit(
            self.current_frame,
            self.active_layer_index,
        )
        self.shape_start = None
        self.shape_end = None
        self.drawing = False
        self.update()

    def fill_lasso_polygon(self, points):
        if len(points) < 3:
            return

        self.ensure_editable_key()
        self.push_layer_undo()

        window = self.window()
        outline_and_fill = bool(
            hasattr(window, "tools")
            and window.tools.lasso_main_outline_sub_fill.isChecked()
        )
        mask_only = not getattr(self, "mask_all_enabled", True)
        inside_boundary = bool(
            hasattr(window, "tools")
            and window.tools.lasso_inside_boundary.isChecked()
        )
        outline_width = (
            window.tools.lasso_outline_width.value() / 2.0
            if outline_and_fill and hasattr(window, "tools")
            else 0.0
        )

        polygon = QPolygonF(points)
        margin = int(math.ceil(outline_width / 2.0)) + 2
        rect = polygon.boundingRect().adjusted(
            -margin, -margin, margin, margin
        ).toAlignedRect().intersected(self.active_layer.image.rect())
        if rect.isEmpty():
            if self.undo_stack:
                self.undo_stack.pop()
            return

        local_polygon = QPolygonF([
            QPointF(point.x() - rect.x(), point.y() - rect.y())
            for point in points
        ])
        overlay = QImage(
            rect.width(),
            rect.height(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        overlay.fill(Qt.GlobalColor.transparent)

        fill_color = (
            self.paint_source_color(self.main_color)
            if outline_and_fill
            else self.paint_source_color()
        )

        painter = QPainter(overlay)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.apply_selection_clip(painter, rect.x(), rect.y())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill_color)
        painter.drawPolygon(local_polygon)

        line_color = None
        if outline_and_fill:
            line_color = self.paint_source_color(self.sub_color)
            line_pen = QPen(line_color)
            line_pen.setWidthF(max(0.5, float(outline_width)))
            line_pen.setStyle(Qt.PenStyle.SolidLine)
            line_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(line_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(local_polygon)
        painter.end()

        width, height = rect.width(), rect.height()
        source = self.active_layer.image.copy(rect).convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        mask_image = overlay.convertToFormat(
            QImage.Format.Format_RGBA8888
        )

        source_ptr = source.bits()
        mask_ptr = mask_image.bits()
        try:
            source_ptr.setsize(source.sizeInBytes())
            mask_ptr.setsize(mask_image.sizeInBytes())
        except AttributeError:
            pass
        source_rows = np.frombuffer(
            source_ptr, dtype=np.uint8
        ).reshape((height, source.bytesPerLine()))
        mask_rows = np.frombuffer(
            mask_ptr, dtype=np.uint8
        ).reshape((height, mask_image.bytesPerLine()))
        source_pixels = source_rows[:, :width * 4].reshape(
            (height, width, 4)
        )
        mask_pixels = mask_rows[:, :width * 4].reshape(
            (height, width, 4)
        )

        if mask_only:
            selected_rgbs = self.selected_mask_colors()
            if not selected_rgbs:
                target_mask = np.zeros((height, width), dtype=bool)
            else:
                packed_source = (
                    (source_pixels[:, :, 0].astype(np.uint32) << 16)
                    | (source_pixels[:, :, 1].astype(np.uint32) << 8)
                    | source_pixels[:, :, 2].astype(np.uint32)
                )
                selected_values = np.fromiter(
                    ((r << 16) | (g << 8) | b for r, g, b in selected_rgbs),
                    dtype=np.uint32,
                    count=len(selected_rgbs),
                )
                target_mask = (
                    (source_pixels[:, :, 3] > 0)
                    & np.isin(packed_source, selected_values)
                )
                if self.background_mask_rgb in selected_rgbs:
                    target_mask |= (
                        (source_pixels[:, :, 3] == 0)
                        | np.all(
                            source_pixels[:, :, :3] == 255,
                            axis=2,
                        )
                    )
            mask_pixels[:, :, 3][~target_mask] = 0

        if inside_boundary:
            polygon_area = mask_pixels[:, :, 3] > 0
            passable = polygon_area & (
                (source_pixels[:, :, 3] == 0)
                | np.all(
                    source_pixels[:, :, :3] == 255,
                    axis=2,
                )
            )
            candidates = np.argwhere(passable)
            if candidates.size:
                center = np.mean(
                    np.array([[point.x() - rect.x(), point.y() - rect.y()]
                              for point in points]),
                    axis=0,
                )
                distances = (
                    (candidates[:, 1] - center[0]) ** 2
                    + (candidates[:, 0] - center[1]) ** 2
                )
                seed_y, seed_x = candidates[int(np.argmin(distances))]
                region = self._scanline_connected_region(
                    passable, (int(seed_x), int(seed_y))
                )
                mask_pixels[:, :, 3][~region] = 0
            else:
                mask_pixels[:, :, 3] = 0

        overlay = mask_image.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        exact_lasso_colors = (
            (fill_color, line_color)
            if line_color is not None
            else (fill_color,)
        )
        colors = self._blend_overlay_into_active_layer(
            overlay,
            rect.topLeft(),
            base_image=self.active_layer.image.copy(),
            opacity=self.pen_opacity,
            exact_colors=exact_lasso_colors,
        )

        self.active_layer.has_content = True
        self._emit_actual_paint_colors(colors)
        self.cellChanged.emit(
            self.current_frame,
            self.active_layer_index,
        )
        self.update()

    def mousePressEvent(self,e):
        self.setFocus()
        self.last_widget = e.position()
        self._brush_cursor_widget_pos = QPointF(e.position())
        self._brush_cursor_inside = self.inside(
            self.widget_to_canvas(e.position())
        )
        self.update_tool_cursor()
        if e.button() == Qt.MouseButton.MiddleButton:
            self.middle_hand = True
            self.drawing = True
            self.update_tool_cursor()
            return

        if (
            self._onion_interaction_mode is not None
            and e.button() in (
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.RightButton,
            )
        ):
            self._begin_onion_interaction_drag(
                e.position(),
                e.button(),
                e.modifiers(),
            )
            e.accept()
            return

        if e.button() == Qt.MouseButton.RightButton:
            p = self.widget_to_canvas(e.position())
            if self.inside(p):
                self.sample_color(p)
                e.accept()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return

        t = self.effective_tool()
        p = self.widget_to_canvas(e.position())
        if t == "hand":
            self.drawing = True
            self.update_tool_cursor()
            return
        if t in ("zoom", "rotate"):
            self.drawing = True
            self.last_widget = e.position()
            self.update_tool_cursor()
            return
        if self.transform_active:
            self.drawing = self.begin_transform_drag(p)
            if self.drawing:
                self.update()
            return
        if (
            not self.inside(p)
            and t not in ("lasso", "lasso_fill", "rect_select")
        ):
            return
        if t == "eyedropper":
            self.sample_color(
                p,
                include_canvas_background=(self.temp_tool == "eyedropper"),
            )
            return
        if t == "line":
            if self.line_curve_stage == 2:
                self.line_control = QPointF(p)
                self.commit_line_tool()
                return
            self.line_start = QPointF(p)
            self.line_end = QPointF(p)
            self.line_control = None
            self.line_curve_stage = 1
            self.drawing = True
            self.update()
            return
        if t == "shape":
            self.shape_start = QPointF(p)
            self.shape_end = QPointF(p)
            self.drawing = True
            self.update()
            return
        if t == "brush":
            self._brush_started_with_content = bool(self.active_layer.has_content)
            self.ensure_editable_key()
            self.push_layer_undo()
            self._begin_opaque_brush_stroke()
            self.drawing = True
            self._reset_brush_stabilizer(p)
            self._start_brush_follow_timer()
            self.last_canvas = QPointF(p)
            self.draw_line(p, p, 1.0)
            used_color = self.opaque_paint_color(
                opacity=self._brush_stroke_opacity
            )
            if not self.is_pseudo_transparent_color(used_color):
                self.colorUsed.emit(used_color)
        elif t == "bucket":
            self.ensure_editable_key()
            self.flood_fill(QPoint(int(p.x()), int(p.y())))
        elif t == "auto_select":
            self.auto_select_region(
                QPoint(int(p.x()), int(p.y())),
                e.modifiers(),
            )
        elif t in ("lasso", "lasso_fill"):
            self.ensure_editable_key()
            self.drawing = True
            self.lasso = [p]
        elif t == "rect_select":
            self.ensure_editable_key()
            self.drawing = True
            self.rect_start = p
            self.rect_end = p
        elif t == "mesh":
            self.ensure_editable_key()
            if not self.mesh_points:
                self.push_layer_undo()
                self.init_mesh()
            self.mesh_active = self.nearest_mesh(p)
            self.drawing = self.mesh_active >= 0
            self.update()
    def mouseMoveEvent(self,e):
        old_cursor = QPointF(self._brush_cursor_widget_pos)
        self._brush_cursor_widget_pos = QPointF(e.position())
        self._brush_cursor_inside = self.inside(
            self.widget_to_canvas(e.position())
        )
        self.update_tool_cursor()
        cursor_radius = max(
            8,
            int(math.ceil(float(self.pen_size) * max(self.zoom, 0.01) / 2.0)) + 4,
        )
        self.update(
            QRectF(old_cursor, old_cursor).adjusted(
                -cursor_radius, -cursor_radius,
                cursor_radius, cursor_radius,
            ).toAlignedRect()
        )
        self.update(
            QRectF(self._brush_cursor_widget_pos, self._brush_cursor_widget_pos).adjusted(
                -cursor_radius, -cursor_radius,
                cursor_radius, cursor_radius,
            ).toAlignedRect()
        )
        t = self.effective_tool()
        p = self.widget_to_canvas(e.position())

        if self._onion_interaction_mode is not None:
            if self._onion_interaction_dragging:
                self._update_onion_interaction_drag(e.position())
            return

        # 曲線の2段階目はボタンを押していなくても曲率をプレビューする。
        if t == "line" and self.line_curve_stage == 2 and not self.drawing:
            if self.inside(p):
                self.line_control = QPointF(p)
                self.update()
            return
        if not self.drawing:
            return
        if self.middle_hand or t == "hand":
            delta = e.position() - self.last_widget
            self.pan += delta
            self.last_widget = e.position()
            self.update()
            return
        if t == "zoom":
            delta = e.position().y() - self.last_widget.y()
            factor = math.pow(1.01, -delta)
            old_zoom = self.zoom
            self.zoom = max(0.05, min(8.0, self.zoom * factor))
            anchor = e.position()
            if old_zoom > 0:
                self.pan = anchor - (anchor - self.pan) * (self.zoom / old_zoom)
            self.last_widget = e.position()
            self.viewChanged.emit(float(self.zoom), float(self.rotation))
            self.update()
            return
        if t == "rotate":
            delta = e.position().x() - self.last_widget.x()
            self.rotation = ((self.rotation + delta * 0.35 + 180.0) % 360.0) - 180.0
            self.last_widget = e.position()
            self.viewChanged.emit(float(self.zoom), float(self.rotation))
            self.update()
            return
        if self.transform_active and self.drawing:
            self.update_transform_drag(p)
            return
        if t == "eyedropper" and self.inside(p):
            self.sample_color(
                p,
                include_canvas_background=(self.temp_tool == "eyedropper"),
            )
            return
        if t == "brush" and self.inside(p):
            self._draw_stabilized_brush_to(p, 1.0)
        elif t == "line" and self.line_curve_stage == 1 and self.inside(p):
            self.line_end = QPointF(p)
            self.update()
        elif t == "shape" and self.inside(p):
            self.shape_end = QPointF(p)
            self.update()
        elif t in ("lasso", "lasso_fill"):
            # キャンバス外から開始／通過しても軌跡を保持する。
            self.lasso.append(QPointF(p))
            self.update()
        elif t == "rect_select":
            # キャンバス外の座標も保持し、確定時に画像領域へクリップする。
            self.rect_end = QPointF(p)
            self.update()
        elif t == "mesh" and self.mesh_active >= 0:
            self.mesh_points[self.mesh_active] = p
            self.update()
    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.effective_tool() == "rotate":
            self.rotation = 0.0
            self.viewChanged.emit(float(self.zoom), 0.0)
            self.update()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def mouseReleaseEvent(self,e):
        if e.button() == Qt.MouseButton.MiddleButton:
            self.middle_hand = False
            self.drawing = False
            self.update_tool_cursor()
            return

        if (
            self._onion_interaction_mode is not None
            and e.button() in (
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.RightButton,
            )
        ):
            if self._onion_interaction_dragging:
                self._update_onion_interaction_drag(e.position())
            self.cancel_onion_interaction(restore=False)
            e.accept()
            return

        if e.button() != Qt.MouseButton.LeftButton:
            return

        t = self.effective_tool()
        if self.transform_active and self.drawing:
            self.end_transform_drag()
            self.drawing = False
            self.update()
            return

        if t == "brush":
            raw_release = self.widget_to_canvas(e.position())
            if (
                self.drawing
                and self.inside(raw_release)
                and self.last_canvas is not None
            ):
                self._finish_stabilized_brush(
                    raw_release,
                    1.0,
                )
            self._stop_brush_follow_timer()
            self._finish_opaque_brush_stroke()
            self._reset_brush_stabilizer()
            self._cell_structure_dirty = (
                not self._brush_started_with_content
                and bool(self.active_layer.has_content)
            )
            self.cellChanged.emit(self.current_frame, self.active_layer_index)
        elif t == "line" and self.line_curve_stage == 1:
            self.drawing = False
            panel = self._active_tool_panel()
            if panel is not None and panel.line_type.currentText() == "曲線":
                self.line_curve_stage = 2
                self.line_control = QPointF(
                    (self.line_start.x() + self.line_end.x()) / 2.0,
                    (self.line_start.y() + self.line_end.y()) / 2.0,
                )
                self.status_message.emit(
                    "マウスを動かしてカーブを調整し、クリックで確定します。Escで取消。"
                )
                self.update()
                return
            self.commit_line_tool()
            return
        elif t == "shape":
            self.commit_shape_tool()
            return
        elif t == "lasso" and len(self.lasso) >= 3:
            self.selection_polygon = list(self.lasso)
            self.selection_mask_override = None
            self.selection_outline_polygons = []
            self.selection_mask_rect = None
            self.lasso = []
            self.selectionChanged.emit()
        elif t == "lasso_fill" and len(self.lasso) >= 3:
            polygon = list(self.lasso)
            self.lasso = []
            self.fill_lasso_polygon(polygon)
        elif t == "rect_select" and self.rect_start is not None and self.rect_end is not None:
            x1, x2 = sorted((self.rect_start.x(), self.rect_end.x()))
            y1, y2 = sorted((self.rect_start.y(), self.rect_end.y()))
            self.selection_polygon = [
                QPointF(x1, y1), QPointF(x2, y1),
                QPointF(x2, y2), QPointF(x1, y2),
            ]
            self.selection_mask_override = None
            self.selection_outline_polygons = []
            self.selection_mask_rect = None
            self.rect_start = None
            self.rect_end = None
            self.selectionChanged.emit()
        elif t == "mesh" and self.mesh_active >= 0:
            self.mesh_active = -1
        self.drawing = False
        self.update_tool_cursor()
        self.update()
    def tabletEvent(self,e):
        self._brush_cursor_widget_pos = QPointF(e.position())
        self._brush_cursor_inside = self.inside(
            self.widget_to_canvas(e.position())
        )
        self.update_tool_cursor()
        self.update()

        # オニオン操作中はブラシ等の通常ツールを一切動作させない。
        # ペンを離した時点で操作を確定し、元のツールへ戻る。
        if self._onion_interaction_mode is not None:
            event_type = e.type()
            if event_type == e.Type.TabletPress:
                tablet_button = (
                    Qt.MouseButton.RightButton
                    if e.buttons() & Qt.MouseButton.RightButton
                    else Qt.MouseButton.LeftButton
                )
                self._begin_onion_interaction_drag(
                    e.position(),
                    tablet_button,
                    e.modifiers(),
                )
            elif (
                event_type == e.Type.TabletMove
                and self._onion_interaction_dragging
            ):
                self._update_onion_interaction_drag(e.position())
            elif event_type == e.Type.TabletRelease:
                if self._onion_interaction_dragging:
                    self._update_onion_interaction_drag(e.position())
                self.cancel_onion_interaction(restore=False)
            e.accept()
            return

        if self.effective_tool() != "brush":e.ignore();return
        p=self.widget_to_canvas(e.position()); pressure=max(.001,float(e.pressure()))
        if e.type()==e.Type.TabletPress and self.inside(p):
            self._brush_started_with_content = bool(self.active_layer.has_content)
            self.ensure_editable_key()
            self.push_layer_undo()
            self._begin_opaque_brush_stroke()
            self.drawing = True
            self._reset_brush_stabilizer(p)
            self._start_brush_follow_timer()
            self.last_canvas = QPointF(p)
            pressure = self._smooth_brush_pressure(pressure)
            self._last_brush_pressure = pressure
            self.draw_line(p, p, pressure)
            used_color = self.opaque_paint_color(
                opacity=self._brush_stroke_opacity
            )
            if not self.is_pseudo_transparent_color(used_color):
                self.colorUsed.emit(used_color)
            e.accept()
        elif e.type()==e.Type.TabletMove and self.drawing:
            if self.inside(p):
                pressure = self._smooth_brush_pressure(
                    pressure
                )
                self._last_brush_pressure = pressure
                self._draw_stabilized_brush_to(
                    p,
                    pressure,
                )
            e.accept()
        elif e.type()==e.Type.TabletRelease:
            if (
                self.drawing
                and self.inside(p)
                and self.last_canvas is not None
            ):
                self._finish_stabilized_brush(
                    p,
                    max(
                        0.001,
                        float(self._last_brush_pressure),
                    ),
                )
            self._stop_brush_follow_timer()
            self._finish_opaque_brush_stroke()
            self._reset_brush_stabilizer()
            self.drawing=False
            self._cell_structure_dirty = (
                not self._brush_started_with_content
                and bool(self.active_layer.has_content)
            )
            self.cellChanged.emit(self.current_frame,self.active_layer_index)
            e.accept()
        else:e.ignore()
    def _tinted_onion_image(
        self, frame_index, color, color_enabled=True, selected_colors_only=False
    ):
        signature = []
        for layer_index in range(len(self.frames[frame_index].layers)):
            key_frame = self.resolve_key_frame(frame_index, layer_index)
            if key_frame is None:
                signature.append(None)
                continue
            layer = self.frames[key_frame].layers[layer_index]
            signature.append((
                key_frame, int(layer.image.cacheKey()), bool(layer.visible),
                round(float(layer.opacity), 4),
                bool(layer.color_filter_enabled),
                tuple(layer.color_filter_rgb) if layer.color_filter_rgb is not None else None,
            ))
        visible_signature = (
            None if self.visible_color_rgbs is None
            else tuple(sorted(self.visible_color_rgbs))
        )
        selected_signature = tuple(sorted(self.selected_used_color_rgbs))
        cache_key = (
            int(frame_index), int(QColor(color).rgba()), bool(color_enabled),
            bool(selected_colors_only), bool(self.onion_all_layers),
            int(self.active_layer_index), selected_signature,
            tuple(signature), bool(self.silhouette_non_background),
            visible_signature,
        )
        cached = self._onion_cache.get(cache_key)
        if cached is not None:
            return cached

        if self.onion_all_layers:
            image = self.composite(frame_index, False)
        else:
            image = blank_image()
            key_frame = self.resolve_key_frame(frame_index, self.active_layer_index)
            if key_frame is not None:
                layer = self.frames[key_frame].layers[self.active_layer_index]
                if layer.visible:
                    painter = QPainter(image)
                    painter.setOpacity(max(0.0, min(1.0, float(layer.opacity))))
                    painter.drawImage(
                        0,
                        0,
                        self._display_layer_image(
                            layer,
                            self.active_layer_index,
                        ),
                    )
                    painter.end()
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = image.width(), image.height()
        ptr = image.bits()
        try:
            ptr.setsize(image.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, image.bytesPerLine())
        )
        rgba = rows[:, :width * 4].reshape((height, width, 4)).copy()

        # 使用色パネルで選択されている親・子の色だけを残す。
        # 選択が空の場合は、チェックの意味を明確にするため全て透明にする。
        if selected_colors_only:
            selected = tuple(self.selected_used_color_rgbs)
            keep = np.zeros((height, width), dtype=bool)
            for rgb_value in selected:
                rgb_value = tuple(int(v) for v in rgb_value[:3])
                keep |= np.all(
                    rgba[:, :, :3] == np.asarray(rgb_value, dtype=np.uint8),
                    axis=2,
                )
            rgba[~keep, 3] = 0

        if color_enabled:
            # CLIP STUDIOのレイヤーカラーに近い処理:
            # 黒を指定色、白を白へ割り当て、中間調はその間を補間する。
            luminance = (
                rgba[:, :, 0].astype(np.float32) * 0.299
                + rgba[:, :, 1].astype(np.float32) * 0.587
                + rgba[:, :, 2].astype(np.float32) * 0.114
            ) / 255.0
            base = np.array(
                [QColor(color).red(), QColor(color).green(), QColor(color).blue()],
                dtype=np.float32,
            )
            mapped = base[None, None, :] * (1.0 - luminance[:, :, None])
            mapped += 255.0 * luminance[:, :, None]
            rgba[:, :, :3] = np.clip(mapped, 0, 255).astype(np.uint8)

        result = QImage(
            rgba.data, width, height, rgba.strides[0],
            QImage.Format.Format_RGBA8888,
        ).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        if len(self._onion_cache) >= 48:
            self._onion_cache.pop(next(iter(self._onion_cache)))
        self._onion_cache[cache_key] = result
        return result

    def onion_shift_values(self, direction):
        if int(direction) < 0:
            return (
                float(self.onion_previous_shift_x),
                float(self.onion_previous_shift_y),
                float(self.onion_previous_rotation),
            )
        return (
            float(self.onion_next_shift_x),
            float(self.onion_next_shift_y),
            float(self.onion_next_rotation),
        )

    def onion_transform_values(self, direction):
        shift_x, shift_y, rotation = self.onion_shift_values(
            direction
        )
        scale = (
            float(self.onion_previous_scale)
            if int(direction) < 0
            else float(self.onion_next_scale)
        )
        return (
            shift_x,
            shift_y,
            rotation,
            max(0.01, scale / 100.0),
        )

    def _onion_frame_identity(self, frame_index):
        """指定位置で表示される絵を、保持フレームと区別せず識別する。"""
        frame_index = int(frame_index)
        if not (0 <= frame_index < len(self.frames)):
            return None

        if not self.onion_all_layers:
            layer_index = int(self.active_layer_index)
            key_frame = self.resolve_key_frame(frame_index, layer_index)
            if key_frame is None:
                return None
            layer = self.frames[key_frame].layers[layer_index]
            return (layer_index, int(key_frame)) if layer.visible else None

        identity = []
        layer_count = len(self.frames[frame_index].layers)
        for layer_index in range(layer_count):
            key_frame = self.resolve_key_frame(frame_index, layer_index)
            if key_frame is None:
                continue
            layer = self.frames[key_frame].layers[layer_index]
            if layer.visible:
                identity.append((layer_index, int(key_frame)))
        return tuple(identity) if identity else None

    def _onion_frame_indices(self, direction, count):
        """空セルと同じ絵の保持区間を飛ばし、前後の実画像位置を返す。"""
        direction = -1 if int(direction) < 0 else 1
        count = max(0, int(count))
        if count <= 0:
            return []

        indices = []
        previous_identity = self._onion_frame_identity(
            self.current_frame
        )
        frame_index = int(self.current_frame) + direction
        while 0 <= frame_index < len(self.frames) and len(indices) < count:
            identity = self._onion_frame_identity(frame_index)
            if identity is not None and identity != previous_identity:
                indices.append(frame_index)
                previous_identity = identity
            frame_index += direction
        return indices

    def _draw_onion_range(
        self, painter, work_rect, direction, count, opacity, color,
        color_enabled=True, selected_colors_only=False
    ):
        count = max(0, int(count))
        shift_x, shift_y, local_rotation, local_scale = (
            self.onion_transform_values(direction)
        )
        levels = (
            list(self.onion_previous_levels)
            if int(direction) < 0
            else list(self.onion_next_levels)
        )
        transform_center = work_rect.center()
        outline_rect = QRectF(self.canvas_rect()).adjusted(
            -0.5, -0.5, 0.5, 0.5
        )
        frame_indices = self._onion_frame_indices(direction, count)

        # 遠い絵から描き、近い絵を手前へ重ねる。
        for distance, frame_index in reversed(list(enumerate(
            frame_indices,
            start=1,
        ))):

            onion = self._tinted_onion_image(
                frame_index,
                color,
                color_enabled,
                selected_colors_only,
            )
            if self.flip_horizontal:
                onion = onion.mirrored(True, False)

            if distance - 1 < len(levels):
                level = max(
                    0.0,
                    min(1.0, float(levels[distance - 1]) / 100.0),
                )
            else:
                level = max(
                    0.0,
                    min(
                        1.0,
                        1.0
                        - ((distance - 1) / max(1, count)) * 0.55,
                    ),
                )
            draw_opacity = max(
                0.0,
                min(1.0, float(opacity) * level),
            )

            if draw_opacity > 0.0:
                painter.save()
                painter.translate(
                    transform_center.x()
                    + shift_x * float(self.zoom),
                    transform_center.y()
                    + shift_y * float(self.zoom),
                )
                painter.rotate(local_rotation)
                painter.scale(local_scale, local_scale)
                painter.translate(
                    -transform_center.x(),
                    -transform_center.y(),
                )
                painter.setOpacity(draw_opacity)
                painter.drawImage(work_rect, onion)
                painter.restore()

        if frame_indices:
            # 外周は全体濃度・コマ別濃度・色適用設定とは分離し、
            # 常に100%不透明度の1px線で表示する。
            painter.save()
            painter.translate(
                transform_center.x()
                + shift_x * float(self.zoom),
                transform_center.y()
                + shift_y * float(self.zoom),
            )
            painter.rotate(local_rotation)
            painter.scale(local_scale, local_scale)
            painter.translate(
                -transform_center.x(),
                -transform_center.y(),
            )
            painter.setOpacity(1.0)
            outline_color = QColor(color)
            outline_color.setAlpha(255)
            outline_pen = QPen(outline_color, 1.0)
            outline_pen.setCosmetic(True)
            outline_pen.setStyle(Qt.PenStyle.SolidLine)
            painter.setPen(outline_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(outline_rect)
            painter.restore()

    def paintEvent(self,e):
        p=QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.fillRect(self.rect(),QColor(42,42,42)); wr=self.work_rect(); cr=self.canvas_rect()
        p.save(); c=wr.center(); p.translate(c); p.rotate(self.rotation); p.scale(max(0.0001,float(self.onion_tu_tb_scale)),max(0.0001,float(self.onion_tu_tb_scale))); p.translate(-c)
        p.fillRect(wr,QColor(72,72,72))
        p.fillRect(cr, self.transparent_display_color)
        p.setOpacity(1)
        # 再生中は表示領域サイズの合成キャッシュを使用し、
        # 大画像をレイヤーごとに毎フレーム再縮小しない。
        if self._playback_active:
            playback_image = self.playback_frame_image(
                self.current_frame,
                wr,
            )
            if playback_image is not None:
                p.drawImage(wr, playback_image)
        else:
            # 編集時は元解像度のレイヤーを直接描画する。
            self.draw_frame_direct(p, wr, self.current_frame)
        # オニオンスキンは現在コマより手前へ重ねる。
        if self.onion_skin:
            self._draw_onion_range(
                p, wr, -1, self.onion_previous_count,
                self.onion_previous_opacity, self.onion_previous_color,
                self.onion_previous_color_enabled,
                self.onion_selected_colors_only,
            )
            self._draw_onion_range(
                p, wr, 1, self.onion_next_count,
                self.onion_next_opacity, self.onion_next_color,
                self.onion_next_color_enabled,
                self.onion_selected_colors_only,
            )
        p.setOpacity(1)
        if self.transform_active and self.transform_points:
            quality_requested = (
                bool(getattr(self, "transform_quality_active", False))
                and self.transform_drag_kind is None
                and not self._transform_line_adjusting
            )
            quality_ready = (
                quality_requested
                and self._tp_preview_cache_image is not None
                and not self._tp_preview_progress_busy
            )
            if quality_requested and not quality_ready:
                if not self._tp_preview_progress_busy:
                    self.request_quality_preview_counter()
                # Dragging/repainting stays responsive while the scheduled
                # TP_mask job displays its counter popup.
                transform_preview, _ = self.transform_preview_image(
                    quality=False,
                    preview_only=True,
                )
            else:
                transform_preview, _ = self.transform_preview_image(
                    quality=quality_ready,
                    preview_only=True,
                )
            if transform_preview is not None:
                transform_preview = self._pseudo_transparent_display_image(
                    transform_preview
                )
                transform_preview = (
                    transform_preview.mirrored(True, False)
                    if self.flip_horizontal else transform_preview
                )
                p.setOpacity(.85)
                p.drawImage(wr, transform_preview)
                p.setOpacity(1)
        p.setPen(QPen(QColor(15,15,15),1));p.drawRect(cr);p.restore()
        # ライン／図形の確定前プレビュー
        if self.line_start is not None and self.line_end is not None:
            preview_color = self.opaque_paint_color()
            preview_color.setAlpha(220)
            p.setPen(QPen(
                preview_color,
                max(0.5, float(self.pen_size) * self.zoom),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            ))
            line_path = QPainterPath(self.canvas_to_widget(self.line_start))
            if self.line_curve_stage == 2 and self.line_control is not None:
                line_path.quadTo(
                    self.canvas_to_widget(self.line_control),
                    self.canvas_to_widget(self.line_end),
                )
            else:
                line_path.lineTo(self.canvas_to_widget(self.line_end))
            p.drawPath(line_path)

        if self.shape_start is not None and self.shape_end is not None:
            rect_preview = self._shape_rect()
            panel = self._active_tool_panel()
            shape_type = panel.shape_type.currentText() if panel is not None else "多角形"
            preview_points = []
            if not rect_preview.isEmpty():
                center = rect_preview.center()
                if shape_type == "楕円":
                    point_count = 64
                    for index in range(point_count):
                        angle = -math.pi / 2.0 + 2.0 * math.pi * index / point_count
                        canvas_point = QPointF(
                            center.x() + math.cos(angle) * rect_preview.width() / 2.0,
                            center.y() + math.sin(angle) * rect_preview.height() / 2.0,
                        )
                        preview_points.append(self.canvas_to_widget(canvas_point))
                else:
                    point_count = max(
                        3,
                        int(panel.shape_corners.value()) if panel is not None else 4,
                    )
                    if point_count == 4:
                        canvas_points = [
                            QPointF(rect_preview.left(), rect_preview.top()),
                            QPointF(rect_preview.right(), rect_preview.top()),
                            QPointF(rect_preview.right(), rect_preview.bottom()),
                            QPointF(rect_preview.left(), rect_preview.bottom()),
                        ]
                        preview_points.extend(
                            self.canvas_to_widget(point) for point in canvas_points
                        )
                    else:
                        for index in range(point_count):
                            angle = -math.pi / 2.0 + 2.0 * math.pi * index / point_count
                            canvas_point = QPointF(
                                center.x() + math.cos(angle) * rect_preview.width() / 2.0,
                                center.y() + math.sin(angle) * rect_preview.height() / 2.0,
                            )
                            preview_points.append(self.canvas_to_widget(canvas_point))
            if preview_points:
                preview_color = self.opaque_paint_color()
                preview_color.setAlpha(220)
                width = (
                    panel.shape_outline_width.value() / 2.0
                    if panel is not None else 1.0
                )
                p.setPen(QPen(
                    preview_color,
                    max(0.5, float(width) * self.zoom),
                    Qt.PenStyle.SolidLine,
                ))
                p.setBrush(Qt.BrushStyle.NoBrush)
                preview_path = QPainterPath(preview_points[0])
                for point in preview_points[1:]:
                    preview_path.lineTo(point)
                preview_path.closeSubpath()
                p.drawPath(preview_path)

        # overlays are drawn in widget coordinates so rotation is applied only once
        preview_selection = self.lasso
        if preview_selection:
            p.setPen(QPen(QColor(255,70,70),2,Qt.PenStyle.DashLine));
            pts=[self.canvas_to_widget(q) for q in preview_selection]
            for a,b in zip(pts,pts[1:]):p.drawLine(a,b)
        elif self.selection_polygon:
            outlines = (
                self.selection_outline_polygons
                if self.selection_outline_polygons
                else [self.selection_polygon]
            )
            p.save()
            p.setOpacity(self._selection_fade_opacity)
            p.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Difference
            )
            p.setPen(QPen(QColor(255,255,255),2,Qt.PenStyle.DashLine))
            for outline in outlines:
                display_outline = (
                    self.transformed_selection_outline(outline)
                    if self.transform_active else outline
                )
                pts = [
                    self.canvas_to_widget(point)
                    for point in display_outline
                ]
                for first, second in zip(pts, pts[1:]):
                    p.drawLine(first, second)
                if len(pts) > 2:
                    p.drawLine(pts[-1], pts[0])
            p.restore()
        if self.rect_start is not None and self.rect_end is not None:
            a=self.canvas_to_widget(self.rect_start); b=self.canvas_to_widget(self.rect_end)
            p.setPen(QPen(QColor(255,70,70),2,Qt.PenStyle.DashLine))
            p.drawRect(QRectF(a,b).normalized())
        if self.transform_active and self.transform_points:
            p.setPen(QPen(QColor(0,220,255),2))
            points=[self.canvas_to_widget(q) for q in self.transform_points]
            if self.transform_mode=="mesh":
                cols=max(2,int(getattr(self, "transform_mesh_cols", 4)))
                rows=max(2,int(getattr(self, "transform_mesh_rows", 4)))
                if len(points)==cols*rows:
                    for gy in range(rows):
                        for gx in range(cols-1):
                            p.drawLine(points[gy*cols+gx],points[gy*cols+gx+1])
                    for gx in range(cols):
                        for gy in range(rows-1):
                            p.drawLine(points[gy*cols+gx],points[(gy+1)*cols+gx])
            elif len(points)==4:
                for index in range(4):
                    p.drawLine(points[index],points[(index+1)%4])
            rotation_handle=self.transform_rotation_handle()
            if rotation_handle is not None:
                rotation_widget=self.canvas_to_widget(rotation_handle)
                if self.transform_mode == "mesh":
                    cols=max(2,int(getattr(self, "transform_mesh_cols", 4)))
                    left=self.transform_points[0]
                    right=self.transform_points[cols-1]
                else:
                    left=self.transform_points[0]
                    right=self.transform_points[1]
                top_mid=self.canvas_to_widget(QPointF(
                    (left.x()+right.x())/2,
                    (left.y()+right.y())/2,
                ))
                p.drawLine(top_mid,rotation_widget)
                p.setBrush(QColor(255,220,70))
                p.drawEllipse(rotation_widget,6,6)
            p.setBrush(QColor("white"))
            for point in points:p.drawEllipse(point,5,5)
        if self.mesh_points:
            p.setPen(QPen(QColor(0,210,255),1)); g=self.mesh_grid
            for gy in range(g):
                for gx in range(g-1):p.drawLine(self.canvas_to_widget(self.mesh_points[gy*g+gx]),self.canvas_to_widget(self.mesh_points[gy*g+gx+1]))
            for gx in range(g):
                for gy in range(g-1):p.drawLine(self.canvas_to_widget(self.mesh_points[gy*g+gx]),self.canvas_to_widget(self.mesh_points[(gy+1)*g+gx]))
            p.setBrush(QColor("white"));
            for q in self.mesh_points:
                wq=self.canvas_to_widget(q);p.drawEllipse(wq,5,5)
        if (
            self.effective_tool() in ("brush", "line")
            and self._brush_cursor_inside
            and self.rect().contains(self._brush_cursor_widget_pos.toPoint())
        ):
            diameter = max(1.0, float(self.pen_size) * max(self.zoom, 0.01))
            center = QPointF(self._brush_cursor_widget_pos)
            ring = QRectF(
                center.x() - diameter / 2.0,
                center.y() - diameter / 2.0,
                diameter,
                diameter,
            )
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            # 白を Difference 合成すると、円の下にある色がネガ反転する。
            p.save()
            p.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Difference
            )
            cursor_pen = QPen(QColor(255, 255, 255, 255), 1.0)
            cursor_pen.setCosmetic(True)
            p.setPen(cursor_pen)
            p.drawEllipse(ring)
            p.restore()

        tool_labels = dict(ToolPanel.TOOLS)
        tool_labels.update({
            "hand": "ハンド",
            "zoom": "拡大縮小",
            "rotate": "回転",
            "eyedropper": "スポイト",
        })
        tool_name = tool_labels.get(self.effective_tool(), str(self.effective_tool()))
        p.setPen(QColor("white"))
        p.drawText(
            10, self.height()-10,
            f"{tool_name} | {self.zoom*100:.0f}% | Frame {self.current_frame+1}"
        )
        p.end()



class ScreenEyedropButton(_ScreenColorDragMixin, QToolButton):
    colorPicked = Signal(QColor)
    clearRequested = Signal()  # 旧形式との互換用
    colorEditorRequested = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_screen_color_drag()
        self._right_click_candidate = False
        self._right_click_press_global = None
        self.setContextMenuPolicy(
            Qt.ContextMenuPolicy.PreventContextMenu
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._right_click_candidate = True
            self._right_click_press_global = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._right_click_candidate
            and self._right_click_press_global is not None
            and event.buttons() & Qt.MouseButton.RightButton
        ):
            distance = (
                event.globalPosition().toPoint() - self._right_click_press_global
            ).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._right_click_candidate = False
                self._begin_screen_pick(Qt.MouseButton.RightButton)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.MouseButton.RightButton
            and self._right_click_candidate
            and not self._screen_pick_active
        ):
            self._right_click_candidate = False
            global_position = event.globalPosition().toPoint()
            self._right_click_press_global = None
            self.colorEditorRequested.emit(global_position)
            event.accept()
            return
        self._right_click_candidate = False
        self._right_click_press_global = None
        super().mouseReleaseEvent(event)



class ReplacementColorPopup(QDialog):
    """置換色をその場で編集する、ライブ反映式のRGB/HSVスライダー。"""

    colorChanged = Signal(QColor)

    def __init__(self, color, source_rgb=None, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint,
        )
        self._color = QColor(color)
        if not self._color.isValid():
            self._color = QColor("black")
        self._updating = False
        self.setWindowTitle("置換色")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumWidth(280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 9, 9, 9)
        layout.setSpacing(5)

        title = "置換色"
        if source_rgb is not None:
            title += "  元色 #{:02X}{:02X}{:02X}".format(*source_rgb)
        layout.addWidget(QLabel(f"<b>{title}</b>"))

        self.preview = QLabel()
        self.preview.setFixedHeight(28)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview)

        self.mode = QComboBox()
        self.mode.addItems(["RGB", "HSV"])
        self.mode.currentTextChanged.connect(self._rebuild_sliders)
        layout.addWidget(self.mode)

        self.slider_widget = QWidget()
        self.slider_layout = QFormLayout(self.slider_widget)
        self.slider_layout.setContentsMargins(0, 0, 0, 0)
        self.slider_layout.setSpacing(4)
        layout.addWidget(self.slider_widget)

        note = QLabel("変更は即時反映されます。右クリックをもう一度行うと閉じます。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#666;")
        layout.addWidget(note)

        self.sliders = []
        self.value_labels = []
        self._rebuild_sliders("RGB")

    def color(self):
        return QColor(self._color)

    def _clear_slider_layout(self):
        while self.slider_layout.rowCount():
            self.slider_layout.removeRow(0)
        self.sliders.clear()
        self.value_labels.clear()

    def _rebuild_sliders(self, mode):
        self._clear_slider_layout()
        specs = (
            [("R", 0, 255), ("G", 0, 255), ("B", 0, 255)]
            if mode == "RGB"
            else [("H", 0, 359), ("S", 0, 255), ("V", 0, 255)]
        )
        for name, minimum, maximum in specs:
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(minimum, maximum)
            label = QLabel("0")
            label.setFixedWidth(34)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            row_layout.addWidget(slider, 1)
            row_layout.addWidget(label)
            slider.valueChanged.connect(
                lambda value, value_label=label: value_label.setText(str(value))
            )
            slider.valueChanged.connect(self._sliders_changed)
            self.slider_layout.addRow(name, row)
            self.sliders.append(slider)
            self.value_labels.append(label)
        self._sync_from_color()

    def _sync_from_color(self):
        if len(self.sliders) != 3:
            return
        if self.mode.currentText() == "RGB":
            values = self._color.getRgb()[:3]
        else:
            hue, saturation, value, _alpha = self._color.getHsv()
            values = (max(0, hue), saturation, value)
        self._updating = True
        try:
            for slider, label, value in zip(
                self.sliders, self.value_labels, values
            ):
                slider.setValue(max(slider.minimum(), min(slider.maximum(), int(value))))
                label.setText(str(slider.value()))
        finally:
            self._updating = False
        self._refresh_preview()

    def _sliders_changed(self):
        if self._updating or len(self.sliders) != 3:
            return
        first, second, third = [slider.value() for slider in self.sliders]
        if self.mode.currentText() == "RGB":
            self._color = QColor(first, second, third)
        else:
            self._color = QColor.fromHsv(first, second, third)
        self._refresh_preview()
        self.colorChanged.emit(QColor(self._color))

    def _refresh_preview(self):
        foreground = "#111" if self._color.lightness() >= 150 else "#fff"
        self.preview.setText(self._color.name(QColor.NameFormat.HexRgb).upper())
        self.preview.setStyleSheet(
            f"background:{self._color.name()};color:{foreground};"
            "border:1px solid #777;padding:3px;"
        )


class SourceColorButton(_ScreenColorDragMixin, QToolButton):
    screenColorPicked = Signal(QColor)
    contextMenuRequested = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_screen_color_drag()
        self._right_click_candidate = False
        self._right_click_press_global = None
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._right_click_candidate = True
            self._right_click_press_global = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._right_click_candidate
            and self._right_click_press_global is not None
            and event.buttons() & Qt.MouseButton.RightButton
        ):
            distance = (
                event.globalPosition().toPoint() - self._right_click_press_global
            ).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._right_click_candidate = False
                self._screen_pick_press_global = self._right_click_press_global
                self._begin_screen_pick(Qt.MouseButton.RightButton)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.MouseButton.RightButton
            and self._right_click_candidate
            and not self._screen_pick_active
        ):
            self._right_click_candidate = False
            global_pos = event.globalPosition().toPoint()
            self._right_click_press_global = None
            self.contextMenuRequested.emit(global_pos)
            event.accept()
            return
        self._right_click_candidate = False
        self._right_click_press_global = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self.contextMenuRequested.emit(event.globalPos())
        event.accept()


class ColorVisibilityCheckBox(QCheckBox):
    """固定幅 [●]/[-] と、複数行をなぞる表示ON/OFFに対応。"""
    altClicked = Signal()
    sweepStarted = Signal(bool)
    sweepMoved = Signal(QPoint)
    sweepFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sweeping = False
        self._consume_release = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(30, 24)
        self.setStyleSheet(
            "QCheckBox{background:transparent;border:none;padding:0px;}"
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        color = (
            self.palette().text().color()
            if self.isEnabled() else self.palette().mid().color()
        )
        painter.setPen(color)
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            "[●]" if self.isChecked() else "[-]",
        )
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self._consume_release = True
            self.altClicked.emit()
            event.accept()
            return

        self._consume_release = False
        self._sweeping = True
        try:
            self.grabMouse()
        except Exception:
            pass
        self.sweepStarted.emit(not self.isChecked())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._sweeping:
            self.sweepMoved.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._consume_release and event.button() == Qt.MouseButton.LeftButton:
            self._consume_release = False
            event.accept()
            return
        if self._sweeping and event.button() == Qt.MouseButton.LeftButton:
            self._sweeping = False
            try:
                self.releaseMouse()
            except Exception:
                pass
            self.sweepFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)




class MaskColorCheckBox(QCheckBox):
    """使用色マスク専用。クリックと上下スライドのON/OFFに対応。"""
    altClicked = Signal()
    sweepStarted = Signal(bool)
    sweepMoved = Signal(QPoint)
    sweepFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sweeping = False
        self._consume_release = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(30, 24)
        self.setStyleSheet("QCheckBox{background:transparent;border:none;padding:0;}")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        color = (
            self.palette().text().color()
            if self.isEnabled() else self.palette().mid().color()
        )
        painter.setPen(color)
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            "[●]" if self.isChecked() else "[-]",
        )
        painter.end()

    def nextCheckState(self):
        self.setChecked(not self.isChecked())
        self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self._consume_release = True
            self.altClicked.emit()
            event.accept()
            return
        self._consume_release = False
        self._sweeping = True
        try:
            self.grabMouse()
        except Exception:
            pass
        self.sweepStarted.emit(not self.isChecked())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._sweeping:
            self.sweepMoved.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._consume_release and event.button() == Qt.MouseButton.LeftButton:
            self._consume_release = False
            event.accept()
            return
        if self._sweeping and event.button() == Qt.MouseButton.LeftButton:
            self._sweeping = False
            try:
                self.releaseMouse()
            except Exception:
                pass
            self.sweepFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CheckClickArea(QWidget):
    """薄い判定領域全体でチェックを操作できるラッパー。"""

    def __init__(self, checkbox, parent=None):
        super().__init__(parent)
        self.checkbox = checkbox
        self._sweeping = False
        self.setFixedWidth(32)
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "CheckClickArea{background:transparent;border:none;}"
            "CheckClickArea:hover{background:#F0F0F0;border:none;}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(checkbox)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                self.checkbox.altClicked.emit()
            elif self.checkbox.isEnabled() and hasattr(self.checkbox, "sweepStarted"):
                self._sweeping = True
                try:
                    self.grabMouse()
                except Exception:
                    pass
                self.checkbox.sweepStarted.emit(not self.checkbox.isChecked())
            elif self.checkbox.isEnabled():
                self.checkbox.setChecked(not self.checkbox.isChecked())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._sweeping and event.buttons() & Qt.MouseButton.LeftButton:
            self.checkbox.sweepMoved.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._sweeping and event.button() == Qt.MouseButton.LeftButton:
            self._sweeping = False
            try:
                self.releaseMouse()
            except Exception:
                pass
            self.checkbox.sweepFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ColorSelectionArea(QWidget):
    """使用色行／使用色枠。クリック、Shift選択、ドラッグ選択に対応。"""

    clicked = Signal(object)
    dragStarted = Signal(object)
    dragMoved = Signal(QPoint)
    dragFinished = Signal()
    contextMenuRequested = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._select_dragging = False
        self._selection_role = ""
        self._selection_frame_enabled = False
        self.setMouseTracking(True)

    def setSelectionRole(self, role):
        """親は赤枠、子は青枠、未選択は初期版と同じ白枠で描画する。"""
        role = str(role or "")
        if role not in ("parent", "child", ""):
            role = ""
        changed = self._selection_role != role or not self._selection_frame_enabled
        self._selection_role = role
        self._selection_frame_enabled = True
        if changed:
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._selection_frame_enabled:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect().adjusted(1, 1, -2, -2)

        if self._selection_role == "parent":
            frame_color = QColor("#e53935")
            side_width = 8
            horizontal_width = 3
        elif self._selection_role == "child":
            frame_color = QColor("#2979ff")
            side_width = 8
            horizontal_width = 3
        else:
            frame_color = QColor("#ffffff")
            side_width = 2
            horizontal_width = 2

        # 親・子選択時は左右の縦線を太くして視認性を上げる。
        painter.fillRect(
            rect.left(),
            rect.top(),
            side_width,
            rect.height(),
            frame_color,
        )
        painter.fillRect(
            rect.right() - side_width + 1,
            rect.top(),
            side_width,
            rect.height(),
            frame_color,
        )
        painter.fillRect(
            rect.left(),
            rect.top(),
            rect.width(),
            horizontal_width,
            frame_color,
        )
        painter.fillRect(
            rect.left(),
            rect.bottom() - horizontal_width + 1,
            rect.width(),
            horizontal_width,
            frame_color,
        )
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._select_dragging = True
            try:
                self.grabMouse()
            except Exception:
                pass
            # ドラッグ対象のON/OFFは、クリックで状態が変わる前に確定する。
            self.dragStarted.emit(event.modifiers())
            self.clicked.emit(event.modifiers())
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.contextMenuRequested.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._select_dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.dragMoved.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._select_dragging and event.button() == Qt.MouseButton.LeftButton:
            self._select_dragging = False
            try:
                self.releaseMouse()
            except Exception:
                pass
            self.dragFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self.contextMenuRequested.emit(event.globalPos())
        event.accept()


class UsedColorPanel(QWidget):
    mainColorRequested = Signal(QColor)
    isolateColorClicked = Signal(QColor)
    clearIsolateRequested = Signal()
    sourceScreenColorPicked = Signal(QColor)
    applyReplacementRequested = Signal(object)
    mergeColorsRequested = Signal(object, object)
    deleteColorsRequested = Signal(object)
    adjustLineThicknessRequested = Signal(object)
    focusColorRequested = Signal(object)
    maskColorsChanged = Signal(object)
    selectedColorsChanged = Signal(object)
    visibleColorsChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setMaximumWidth(16777215)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.setMinimumHeight(0)
        self.background_rgb = (255, 255, 255)
        self.colors = []
        self.replacements = {}
        self.enabled_colors = {self.background_rgb: True}
        self.mask_rgbs = {self.background_rgb}
        self._mask_all_mode = True

        # Used-color selection is separate from the drawing mask.
        # The most recently selected color is the parent; the others are children.
        self.selected_rgbs = []
        self.parent_rgb = None
        self._selection_anchor_rgb = None
        self._selection_drag_active = False
        self._selection_drag_state = True
        self._selection_drag_touched = set()

        self.visibility_checks = {}
        self.mask_checks = {}
        self.source_buttons = {}
        self.source_wrappers = {}
        self.replacement_buttons = {}
        self.row_widgets = {}
        self._replacement_popup = None
        self._replacement_popup_rgb = None

        self._visibility_sweep_active = False
        self._visibility_sweep_state = True
        self._visibility_sweep_touched = set()
        self._visibility_sweep_changed = False
        self._mask_sweep_active = False
        self._mask_sweep_state = True
        self._mask_sweep_touched = set()
        self._mask_sweep_changed = False
        self._isolated_rgb = None
        self._pre_isolate_enabled = None
        self._mask_alt_rgb = None
        self._pre_mask_alt = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        layout.addWidget(QLabel("<b>使用色</b>"))
        note = QLabel(
            "使用色：左クリックで親・子を選択／再クリックで解除。"
            "右クリックで統合・削除、上下ドラッグで選択を連続ON/OFFします。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#666;")
        layout.addWidget(note)

        self.count_label = QLabel("0色")
        layout.addWidget(self.count_label)

        header_widget = QWidget()
        header = QGridLayout(header_widget)
        scrollbar_width = self.style().pixelMetric(
            QStyle.PixelMetric.PM_ScrollBarExtent
        )
        header.setContentsMargins(1, 0, 1 + scrollbar_width, 0)
        header.setHorizontalSpacing(0)
        header.setColumnMinimumWidth(0, 32)
        header.setColumnMinimumWidth(1, 32)
        visibility_header = QLabel("表示")
        visibility_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        visibility_header.setStyleSheet("font-size:10px;")
        visibility_header.setToolTip("各行の薄い背景セル全体を右クリックして表示メニューを開けます。")
        header.addWidget(visibility_header, 0, 0, Qt.AlignmentFlag.AlignCenter)
        mask_header = QLabel("マスク")
        mask_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mask_header.setStyleSheet("font-size:10px;")
        mask_header.setToolTip("各行の薄い背景セル全体を右クリックしてマスクメニューを開けます。")
        header.addWidget(mask_header, 0, 1, Qt.AlignmentFlag.AlignCenter)
        header.addWidget(QLabel("選択"), 0, 2)
        header.addWidget(QLabel("置換色"), 0, 3)
        header.setColumnMinimumWidth(2, 72)
        header.setColumnMinimumWidth(3, 72)
        header.setColumnStretch(2, 1)
        header.setColumnStretch(3, 1)
        layout.addWidget(header_widget)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(0)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.content = QWidget()
        self.rows = QVBoxLayout(self.content)
        self.rows.setContentsMargins(2, 2, 2, 2)
        self.rows.setSpacing(1)
        self.rows.addStretch(1)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

        # 見出しと同じ4列に配置し、各ボタンの意味と位置を揃える。
        button_row = QGridLayout()
        button_row.setHorizontalSpacing(0)
        button_row.setContentsMargins(1, 0, 1 + scrollbar_width, 0)
        button_row.setColumnMinimumWidth(0, 32)
        button_row.setColumnMinimumWidth(1, 32)
        button_row.setColumnMinimumWidth(2, 72)
        button_row.setColumnMinimumWidth(3, 72)
        button_row.setColumnStretch(2, 1)
        button_row.setColumnStretch(3, 1)

        self.show_all_button = QPushButton("全表示")
        self.show_all_button.setToolTip(
            "非表示にした使用色と背景色をすべて表示します。"
        )
        self.show_all_button.clicked.connect(self._show_all_colors)
        self.show_all_button.setFixedWidth(32)
        self.show_all_button.setStyleSheet("font-size:9px;padding:0px;")

        self.clear_masks_button = QPushButton("全体")
        self.clear_masks_button.setToolTip(
            "すべての使用色と背景をマスクON（描画可能）にします。"
        )
        self.clear_masks_button.clicked.connect(self._set_all_masks_on)
        self.clear_masks_button.setFixedWidth(32)
        self.clear_masks_button.setStyleSheet("font-size:9px;padding:0px;")

        self.merge_button = QPushButton("統合")
        self.merge_button.setToolTip(
            "選択した子の色を、最後に選択した親の色へ統合します。"
        )
        self.merge_button.clicked.connect(self._emit_merge)
        self.merge_button.setMinimumWidth(72)
        self.merge_button.setMaximumWidth(16777215)
        self.merge_button.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.merge_button.setStyleSheet("font-size:10px;padding:1px;")

        self.apply_button = QPushButton("色置換")
        self.apply_button.setToolTip(
            "右側に登録した置換色を、選択中レイヤーのすべてのコマへ適用します。"
        )
        self.apply_button.clicked.connect(self._emit_replacements)
        self.apply_button.setMinimumWidth(72)
        self.apply_button.setMaximumWidth(16777215)
        self.apply_button.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.apply_button.setStyleSheet("font-size:10px;padding:1px;")

        for column, button in enumerate((
            self.show_all_button,
            self.clear_masks_button,
            self.merge_button,
            self.apply_button,
        )):
            button.setMinimumWidth(0)
            button_row.addWidget(button, 0, column)
        layout.addLayout(button_row)

    @staticmethod
    def _rgb_key(color):
        qc = QColor(color)
        return (qc.red(), qc.green(), qc.blue())

    @staticmethod
    def _text_color(rgb):
        luminance = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        return "#111111" if luminance >= 150 else "#ffffff"

    def _set_source_button_style(self, rgb):
        button = self.source_buttons.get(rgb)
        wrapper = self.source_wrappers.get(rgb)
        if button is None:
            return

        color = QColor(*rgb)
        text_color = self._text_color(rgb)
        if rgb == self.parent_rgb:
            inner_border = "1px solid #ffffff"
            selection_role = "parent"
            role = "親"
        elif rgb in self.selected_rgbs:
            inner_border = "1px solid #ffffff"
            selection_role = "child"
            role = "子"
        else:
            inner_border = "1px solid #ffffff"
            selection_role = ""
            role = ""

        button.setStyleSheet(
            "QToolButton{"
            f"background:{color.name()};color:{text_color};border:{inner_border};"
            "padding:2px;"
            "}"
            "QToolButton:hover{"
            f"background:{color.name()};color:{text_color};border:{inner_border};"
            "}"
        )
        if wrapper is not None:
            if hasattr(wrapper, "setSelectionRole"):
                wrapper.setSelectionRole(selection_role)
            else:
                # 旧ウィジェットとの互換用。
                fallback = (
                    "#e53935" if selection_role == "parent"
                    else "#2979ff" if selection_role == "child"
                    else "#777777"
                )
                wrapper.setStyleSheet(
                    f"background:{fallback};border:2px solid #ffffff;"
                )

        if rgb == self.background_rgb:
            button.setToolTip(
                "背景色 #FFFFFF。背景色の置換はできません。"
            )
        else:
            role_note = f"現在は{role}です。" if role else ""
            button.setToolTip(
                "クリック：統合用の使用色として選択／再クリックで解除。"
                "最後に選んだ色が親になります。上下ドラッグ：選択を連続ON/OFF。"
                + role_note
            )

    def _set_replacement_button_style(self, source_rgb):
        button = self.replacement_buttons.get(source_rgb)
        if button is None:
            return
        replacement_color = self.replacements.get(source_rgb)
        if replacement_color is None:
            button.setText("未設定")
            button.setStyleSheet(
                "QToolButton{background:#f2f2f2;border:1px dashed #888;}"
                "QToolButton:hover{border:1px dashed #888;}"
            )
        else:
            button.setText(
                replacement_color.name(QColor.NameFormat.HexRgb).upper()
            )
            text_color = self._text_color((
                replacement_color.red(),
                replacement_color.green(),
                replacement_color.blue(),
            ))
            button.setStyleSheet(
                "QToolButton{"
                f"background:{replacement_color.name()};color:{text_color};"
                "border:1px solid #777;}"
                "QToolButton:hover{border:1px solid #777;}"
            )

    def _clear_rows(self):
        self._close_replacement_editor()
        self.visibility_checks.clear()
        self.mask_checks.clear()
        self.source_buttons.clear()
        self.source_wrappers.clear()
        self.replacement_buttons.clear()
        self.row_widgets.clear()
        while self.rows.count() > 1:
            item = self.rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _remove_color_row(self, rgb):
        """Remove only one row so palette updates do not rebuild every widget."""
        if self._replacement_popup_rgb == rgb:
            self._close_replacement_editor()
        widget = self.row_widgets.pop(rgb, None)
        if widget is not None:
            self.rows.removeWidget(widget)
            widget.deleteLater()
        self.visibility_checks.pop(rgb, None)
        self.mask_checks.pop(rgb, None)
        self.source_buttons.pop(rgb, None)
        self.source_wrappers.pop(rgb, None)
        self.replacement_buttons.pop(rgb, None)

    def _make_source_wrapper(self, source, source_rgb):
        wrapper = ColorSelectionArea()
        wrapper.clicked.connect(
            lambda modifiers, rgb=source_rgb:
            self._select_used_color(rgb, modifiers)
        )
        wrapper.dragStarted.connect(
            lambda modifiers, rgb=source_rgb:
            self._begin_used_color_drag(rgb, modifiers)
        )
        wrapper.dragMoved.connect(self._move_used_color_drag)
        wrapper.dragFinished.connect(self._end_used_color_drag)
        wrapper.contextMenuRequested.connect(
            lambda global_pos, rgb=source_rgb:
            self._show_used_color_context_menu(rgb, global_pos)
        )
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(8, 3, 8, 3)
        wrapper_layout.setSpacing(0)
        wrapper_layout.addWidget(source)
        self.source_wrappers[source_rgb] = wrapper
        return wrapper

    def _append_color_row(self, color):
        qc = QColor(color)
        source_rgb = self._rgb_key(qc)
        is_background = source_rgb == self.background_rgb

        # 行全体では選択操作を受けない。
        # 「選択」列の source_wrapper だけが親子選択メニューを担当する。
        row_widget = QWidget()
        row = QGridLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setHorizontalSpacing(0)
        row.setColumnMinimumWidth(0, 32)
        row.setColumnMinimumWidth(1, 32)
        row.setColumnMinimumWidth(2, 72)
        row.setColumnMinimumWidth(3, 72)
        row.setColumnStretch(2, 1)
        row.setColumnStretch(3, 1)

        visible_check = ColorVisibilityCheckBox()
        visible_check.setChecked(
            self.enabled_colors.get(source_rgb, True)
        )
        visible_check.setToolTip(
            (
                "クリック：背景表示ON/OFF／上下になぞる：一括ON/OFF／"
                "Alt＋クリック：背景だけ表示／右クリック：表示メニュー"
            )
            if is_background else
            (
                "クリック：表示ON/OFF／上下になぞる：一括ON/OFF／"
                "Alt＋クリック：この色だけ表示／右クリック：表示メニュー"
            )
        )
        visible_check.toggled.connect(
            lambda checked, rgb=source_rgb: self._set_color_visible(rgb, checked)
        )
        visible_check.altClicked.connect(
            lambda rgb=source_rgb: self._isolate_visible_color(rgb)
        )
        visible_check.sweepStarted.connect(
            lambda checked, rgb=source_rgb: self._begin_visibility_sweep(rgb, checked)
        )
        visible_check.sweepMoved.connect(self._move_visibility_sweep)
        visible_check.sweepFinished.connect(self._end_visibility_sweep)
        self.visibility_checks[source_rgb] = visible_check

        if self._mask_all_mode:
            self.mask_rgbs.add(source_rgb)
        mask_check = MaskColorCheckBox()
        mask_check.setChecked(source_rgb in self.mask_rgbs)
        mask_check.setToolTip(
            (
                "クリック：ON/OFF／上下になぞる：一括ON/OFF／"
                "Alt＋クリック：この色だけON／右クリック：マスクメニュー"
            )
            if not is_background else
            (
                "クリック：背景マスクON/OFF／上下になぞる：一括ON/OFF／"
                "Alt＋クリック：背景だけON／右クリック：マスクメニュー"
            )
        )
        mask_check.toggled.connect(
            lambda checked, rgb=source_rgb: self._set_mask_color(rgb, checked)
        )
        mask_check.altClicked.connect(
            lambda rgb=source_rgb: self._isolate_mask_color(rgb)
        )
        mask_check.sweepStarted.connect(
            lambda checked, rgb=source_rgb: self._begin_mask_sweep(rgb, checked)
        )
        mask_check.sweepMoved.connect(self._move_mask_sweep)
        mask_check.sweepFinished.connect(self._end_mask_sweep)
        self.mask_checks[source_rgb] = mask_check

        source = SourceColorButton()
        source.setFixedHeight(26)
        source.setMinimumWidth(72)
        source.setText(
            "背景色 #FFFFFF"
            if is_background
            else qc.name(QColor.NameFormat.HexRgb).upper()
        )
        # 「選択」列のマウス操作はすべて外側の ColorSelectionArea が担当する。
        # これにより、ドラッグはスポイトではなく選択ON/OFFの連続切替になる。
        source.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.source_buttons[source_rgb] = source
        source_wrapper = self._make_source_wrapper(source, source_rgb)
        source_wrapper.setMinimumWidth(72)
        source_wrapper.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        source.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._set_source_button_style(source_rgb)

        # チェックボックスだけでなく、薄いグレーのセル全体をクリック領域にする。
        visible_area = CheckClickArea(visible_check)
        mask_area = CheckClickArea(mask_check)
        for widget in (visible_check, visible_area):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, w=widget, rgb=source_rgb:
                self._show_visibility_context_menu(rgb, w.mapToGlobal(pos))
            )
        for widget in (mask_check, mask_area):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, w=widget, rgb=source_rgb:
                self._show_mask_context_menu(rgb, w.mapToGlobal(pos))
            )
        row.addWidget(visible_area, 0, 0)
        row.addWidget(mask_area, 0, 1)

        if is_background:
            # Background does not need a replacement column, so its swatch spans both.
            row.addWidget(source_wrapper, 0, 2, 1, 2)
        else:
            replacement = ScreenEyedropButton()
            replacement.setFixedHeight(26)
            replacement.setMinimumWidth(72)
            replacement.setMaximumWidth(16777215)
            replacement.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )
            replacement.setStyleSheet("font-size:10px;padding:1px;")
            replacement.setToolTip(
                "クリック：現在のメイン／サブ色を登録。"
                "登録済みでもう一度クリック：未設定へ戻す。"
                "左または右へドラッグ：その位置をスポイト。"
                "右クリック：置換色のカラースライダーを開く／もう一度で閉じる。"
            )
            replacement.clicked.connect(
                lambda _=False, rgb=source_rgb: self._toggle_replacement(rgb)
            )
            replacement.colorPicked.connect(
                lambda c, rgb=source_rgb: self._set_replacement_color(rgb, c)
            )
            replacement.colorEditorRequested.connect(
                lambda global_pos, rgb=source_rgb:
                self._toggle_replacement_editor(rgb, global_pos)
            )
            self.replacement_buttons[source_rgb] = replacement
            self._set_replacement_button_style(source_rgb)
            row.addWidget(source_wrapper, 0, 2)
            row.addWidget(replacement, 0, 3)

        self.row_widgets[source_rgb] = row_widget
        self.rows.insertWidget(max(0, self.rows.count() - 1), row_widget)

    def _normalized_colors(self, colors):
        seen = set()
        result = []
        for color in colors:
            qc = QColor(color)
            key = self._rgb_key(qc)
            if key not in seen:
                seen.add(key)
                result.append(qc)
        return result

    def set_colors(self, colors):
        normalized = [
            color for color in self._normalized_colors(colors)
            if self._rgb_key(color) != self.background_rgb
        ]
        incoming_by_key = {
            self._rgb_key(color): QColor(color) for color in normalized
        }
        incoming_keys = {self.background_rgb, *incoming_by_key.keys()}
        current_keys = {self._rgb_key(color) for color in self.colors}
        added = incoming_keys - current_keys
        removed = current_keys - incoming_keys
        palette_changed = bool(added or removed)

        old_mask_rgbs = set(self.mask_rgbs)
        old_selected = list(self.selected_rgbs)

        # 消えた色だけを削除する。全行再構築と色順の並べ替えを避ける。
        for rgb in tuple(removed):
            if rgb == self.background_rgb:
                continue
            self._remove_color_row(rgb)
            self.enabled_colors.pop(rgb, None)
            self.mask_rgbs.discard(rgb)
            self.replacements.pop(rgb, None)

        self.selected_rgbs = [
            rgb for rgb in self.selected_rgbs
            if rgb in incoming_keys and rgb != self.background_rgb
        ]
        self.parent_rgb = self.selected_rgbs[-1] if self.selected_rgbs else None

        # 既存の並びを維持し、新色だけ末尾へ追加する。
        retained = [
            QColor(color) for color in self.colors
            if self._rgb_key(color) in incoming_keys
            and self._rgb_key(color) != self.background_rgb
        ]
        self.colors = [QColor(*self.background_rgb), *retained]

        if self.background_rgb not in self.source_buttons:
            self.enabled_colors[self.background_rgb] = True
            self._append_color_row(QColor(*self.background_rgb))

        existing = {self._rgb_key(color) for color in self.colors}
        for color in normalized:
            rgb = self._rgb_key(color)
            if rgb in existing:
                continue
            self.colors.append(QColor(color))
            self.enabled_colors[rgb] = True
            self.mask_rgbs.add(rgb)
            self._append_color_row(color)
            existing.add(rgb)

        self.enabled_colors = {
            rgb: self.enabled_colors.get(rgb, True)
            for rgb in incoming_keys
        }
        for rgb in set(old_selected) | set(self.selected_rgbs):
            if rgb in self.source_buttons:
                self._set_source_button_style(rgb)
        self.replacements = {
            rgb: color for rgb, color in self.replacements.items()
            if rgb in incoming_keys and rgb != self.background_rgb
        }

        self.count_label.setText(f"{len(self.colors)}色")
        if self._isolated_rgb not in incoming_keys:
            self._isolated_rgb = None
            self._pre_isolate_enabled = None

        self._mask_all_mode = self.all_masks_enabled()
        if old_mask_rgbs != self.mask_rgbs:
            self.maskColorsChanged.emit(set(self.mask_rgbs))
        if old_selected != self.selected_rgbs:
            self.selectedColorsChanged.emit(set(self.selected_rgbs))
        if palette_changed:
            self.visibleColorsChanged.emit(self.enabled_rgb_set())
    def add_color(self, color):
        """Add a newly drawn color immediately, before a whole-image scan."""
        qc = QColor(color)
        if not qc.isValid() or qc.alpha() == 0:
            return
        key = self._rgb_key(qc)
        if key in self.source_buttons:
            return
        self.colors.append(qc)
        self.enabled_colors[key] = True
        self.mask_rgbs.add(key)
        self._mask_all_mode = True
        self._append_color_row(qc)
        self.count_label.setText(f"{len(self.colors)}色")
        self._emit_mask_state()
        self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def enabled_rgb_set(self):
        enabled = {
            rgb for rgb, is_enabled in self.enabled_colors.items() if is_enabled
        }
        return enabled

    def selected_rgb_set(self):
        return set(self.selected_rgbs)

    def select_matching_color(self, color):
        """スポイト色と完全一致する使用色を、単独の親色として選択する。"""
        qc = QColor(color)
        rgb = self._rgb_key(qc)
        if (
            not qc.isValid()
            or rgb == self.background_rgb
            or rgb not in self.source_buttons
        ):
            self._clear_used_color_selection()
            return False

        old = set(self.selected_rgbs)
        changed = self.selected_rgbs != [rgb] or self.parent_rgb != rgb
        self.selected_rgbs = [rgb]
        self.parent_rgb = rgb
        self._selection_anchor_rgb = rgb
        self._refresh_used_color_styles(old | {rgb})
        row_widget = self.row_widgets.get(rgb)
        if row_widget is not None:
            self.scroll.ensureWidgetVisible(row_widget)
        if changed:
            self.selectedColorsChanged.emit({rgb})
        return True

    def _show_all_colors(self):
        self._isolated_rgb = None
        self._pre_isolate_enabled = None
        for rgb in self.visibility_checks:
            self._set_checkbox_without_signal(rgb, True)
        self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def all_masks_enabled(self):
        keys = set(self.mask_checks)
        return bool(keys) and keys.issubset(self.mask_rgbs)

    def _emit_mask_state(self):
        self._mask_all_mode = self.all_masks_enabled()
        self.maskColorsChanged.emit(set(self.mask_rgbs))

    def _set_visibility_state(self, rgb, enabled):
        self._isolated_rgb = None
        self._pre_isolate_enabled = None
        self._set_checkbox_without_signal(rgb, enabled)
        self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _disable_other_visible_colors(self, rgb):
        """右クリック対象だけを表示し、それ以外をOFFにする。"""
        self._isolated_rgb = None
        self._pre_isolate_enabled = None
        for key in self.visibility_checks:
            self._set_checkbox_without_signal(
                key,
                key == rgb,
            )
        self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _show_visibility_context_menu(self, rgb, global_position):
        menu = QMenu(self)
        action_on = menu.addAction("ON")
        action_off = menu.addAction("OFF")
        menu.addSeparator()
        action_others = menu.addAction("対象以外をOFF")
        action_all = menu.addAction("全表示")
        chosen = menu.exec(global_position)
        if chosen is action_on:
            self._set_visibility_state(rgb, True)
        elif chosen is action_off:
            self._set_visibility_state(rgb, False)
        elif chosen is action_others:
            self._disable_other_visible_colors(rgb)
        elif chosen is action_all:
            self._show_all_colors()

    def _set_mask_color(self, rgb, checked):
        if checked:
            self.mask_rgbs.add(rgb)
        else:
            self.mask_rgbs.discard(rgb)
        self._emit_mask_state()

    def _set_mask_checkbox_without_signal(self, rgb, checked):
        checkbox = self.mask_checks.get(rgb)
        if checkbox is not None and checkbox.isChecked() != bool(checked):
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(checked))
            checkbox.blockSignals(False)

    def _set_all_masks_on(self):
        self.mask_rgbs = set(self.mask_checks)
        for rgb in self.mask_checks:
            self._set_mask_checkbox_without_signal(rgb, True)
        self._emit_mask_state()

    def _clear_mask_colors(self):
        """Compatibility helper: turn every mask OFF."""
        self.mask_rgbs.clear()
        for rgb in self.mask_checks:
            self._set_mask_checkbox_without_signal(rgb, False)
        self._emit_mask_state()

    def _isolate_mask_color(self, rgb):
        """Alt+click: enable only the clicked mask."""
        self.mask_rgbs = {rgb}
        for key in self.mask_checks:
            self._set_mask_checkbox_without_signal(key, key == rgb)
        self._emit_mask_state()

    def _disable_other_masks(self, rgb):
        """右クリック対象だけをONにし、それ以外のマスクをOFFにする。"""
        self.mask_rgbs = {rgb}
        for key in self.mask_checks:
            self._set_mask_checkbox_without_signal(key, key == rgb)
        self._emit_mask_state()

    def _set_single_mask_state(self, rgb, enabled):
        self._set_mask_checkbox_without_signal(rgb, enabled)
        if enabled:
            self.mask_rgbs.add(rgb)
        else:
            self.mask_rgbs.discard(rgb)
        self._emit_mask_state()

    def _begin_mask_sweep(self, source_rgb, checked):
        self._mask_sweep_active = True
        self._mask_sweep_state = bool(checked)
        self._mask_sweep_touched = {source_rgb}
        old = source_rgb in self.mask_rgbs
        self._set_mask_checkbox_without_signal(source_rgb, checked)
        if checked:
            self.mask_rgbs.add(source_rgb)
        else:
            self.mask_rgbs.discard(source_rgb)
        self._mask_sweep_changed = old != bool(checked)

    def _mask_checkbox_rgb_at_global(self, global_position):
        for rgb, checkbox in self.mask_checks.items():
            area = checkbox.parentWidget()
            target = area if isinstance(area, CheckClickArea) else checkbox
            if target.rect().contains(target.mapFromGlobal(global_position)):
                return rgb
        return None

    def _move_mask_sweep(self, global_position):
        if not self._mask_sweep_active:
            return
        rgb = self._mask_checkbox_rgb_at_global(global_position)
        if rgb is None or rgb in self._mask_sweep_touched:
            return
        self._mask_sweep_touched.add(rgb)
        old = rgb in self.mask_rgbs
        self._set_mask_checkbox_without_signal(rgb, self._mask_sweep_state)
        if self._mask_sweep_state:
            self.mask_rgbs.add(rgb)
        else:
            self.mask_rgbs.discard(rgb)
        self._mask_sweep_changed |= old != self._mask_sweep_state

    def _end_mask_sweep(self):
        if not self._mask_sweep_active:
            return
        self._mask_sweep_active = False
        if self._mask_sweep_changed:
            self._emit_mask_state()
        self._mask_sweep_touched.clear()
        self._mask_sweep_changed = False

    def _show_mask_context_menu(self, rgb, global_position):
        menu = QMenu(self)
        action_on = menu.addAction("ON")
        action_off = menu.addAction("OFF")
        menu.addSeparator()
        action_others = menu.addAction("対象以外をOFF")
        action_all = menu.addAction("全表示")
        chosen = menu.exec(global_position)
        if chosen is action_on:
            self._set_single_mask_state(rgb, True)
        elif chosen is action_off:
            self._set_single_mask_state(rgb, False)
        elif chosen is action_others:
            self._disable_other_masks(rgb)
        elif chosen is action_all:
            self._set_all_masks_on()


    def _refresh_used_color_styles(self, affected=None):
        keys = set(self.source_buttons) if affected is None else set(affected)
        for key in keys:
            if key in self.source_buttons:
                self._set_source_button_style(key)

    def _set_used_color_parent(self, rgb):
        if rgb == self.background_rgb:
            return
        old = set(self.selected_rgbs)
        self.selected_rgbs = [value for value in self.selected_rgbs if value != rgb]
        self.selected_rgbs.append(rgb)
        self.parent_rgb = rgb
        self._refresh_used_color_styles(old | set(self.selected_rgbs))
        self.selectedColorsChanged.emit(set(self.selected_rgbs))

    def _previous_selected_parent(self, rgb):
        """現在の色より前に選択された、直近の色を親候補として返す。"""
        for value in reversed(self.selected_rgbs):
            if value != rgb and value != self.background_rgb:
                return value
        return None

    def _set_used_color_child(self, rgb):
        if rgb == self.background_rgb:
            return False
        parent = self._previous_selected_parent(rgb)
        if parent is None:
            return False
        old = set(self.selected_rgbs)
        values = [
            value for value in self.selected_rgbs
            if value not in (rgb, parent)
        ]
        # 子を先、親を最後に置く。最後に選ばれたものが親という既存仕様を保つ。
        values.extend([rgb, parent])
        self.selected_rgbs = values
        self.parent_rgb = parent
        self._selection_anchor_rgb = rgb
        self._refresh_used_color_styles(old | set(values))
        self.selectedColorsChanged.emit(set(values))
        return True

    def _clear_used_color_selection(self):
        old = set(self.selected_rgbs)
        changed = bool(old) or self.parent_rgb is not None
        self.selected_rgbs = []
        self.parent_rgb = None
        self._selection_anchor_rgb = None
        self._refresh_used_color_styles(old)
        if changed:
            self.selectedColorsChanged.emit(set())

    def _show_used_color_context_menu(self, rgb, global_position):
        menu = QMenu(self)
        action_main_color = menu.addAction("メインカラーにする")
        menu.addSeparator()
        if rgb == self.background_rgb:
            chosen = menu.exec(global_position)
            if chosen is action_main_color:
                self.mainColorRequested.emit(QColor(*rgb))
            return
        action_parent = menu.addAction("親を選択")
        action_child = menu.addAction("子を選択")
        # 色一覧で一つ前ではなく、操作上で一つ前に選択された色を親にする。
        action_child.setEnabled(self._previous_selected_parent(rgb) is not None)
        action_merge_parent = menu.addAction("親と統合")
        parent = (
            tuple(self.parent_rgb)
            if self.parent_rgb is not None
            else None
        )
        if parent is None or parent == self.background_rgb:
            merge_sources = set()
        else:
            # 右クリックした色に関係なく、現在選択されている
            # 親色・子色のすべてを親色へ統合する。
            merge_sources = {
                tuple(value)
                for value in self.selected_rgbs
                if tuple(value) != self.background_rgb
            }
        action_merge_parent.setEnabled(
            parent is not None
            and parent in merge_sources
            and len(merge_sources) >= 2
        )
        action_merge_parent.setToolTip(
            "現在選択されているすべての色を、親色へ統合します。"
        )

        selected_line_colors = {
            tuple(value) for value in self.selected_rgbs
            if tuple(value) != self.background_rgb
        }

        # 右クリックした色が選択中なら、親・子を含む選択色全体を削除する。
        # 未選択の色を右クリックした場合は、その色だけを削除対象にする。
        clicked_rgb = tuple(rgb)
        delete_sources = (
            set(selected_line_colors)
            if clicked_rgb in selected_line_colors
            else {clicked_rgb}
        )
        delete_sources.discard(self.background_rgb)

        action_delete = menu.addAction("削除")
        action_delete.setEnabled(bool(delete_sources))
        action_delete.setToolTip(
            "選択した使用色を #FFFFFF へ統合します。"
        )

        action_thickness = menu.addAction("太さを調整")
        main_window = self.window()
        canvas = getattr(main_window, "canvas", None)
        tween_running = bool(
            getattr(canvas, "tween_pending", None)
        )
        action_thickness.setEnabled(
            not tween_running
            and parent is not None
            and tuple(rgb) == parent
            and parent in selected_line_colors
        )
        action_thickness.setToolTip(
            "選択中の親色・子色をまとめて調整します。"
            if not tween_running
            else "トゥイーン中は線の太さを変更できません。"
        )

        action_focus = menu.addAction("対象に注視")
        menu.addSeparator()
        action_clear = menu.addAction("全選択解除")

        chosen = menu.exec(global_position)
        if chosen is action_main_color:
            self.mainColorRequested.emit(QColor(*rgb))
        elif chosen is action_parent:
            self._set_used_color_parent(rgb)
        elif chosen is action_child:
            self._set_used_color_child(rgb)
        elif chosen is action_merge_parent:
            self.mergeColorsRequested.emit(
                parent,
                set(merge_sources),
            )
        elif chosen is action_delete:
            self.deleteColorsRequested.emit(
                set(delete_sources)
            )
        elif chosen is action_thickness:
            self.adjustLineThicknessRequested.emit(
                set(selected_line_colors)
            )
        elif chosen is action_focus:
            self._set_used_color_parent(rgb)
            self.focusColorRequested.emit(tuple(rgb))
        elif chosen is action_clear:
            self._clear_used_color_selection()

    def _ordered_non_background_rgbs(self):
        return [
            self._rgb_key(color) for color in self.colors
            if self._rgb_key(color) != self.background_rgb
        ]

    def _select_used_color(self, rgb, modifiers=Qt.KeyboardModifier.NoModifier):
        if rgb == self.background_rgb:
            return
        if modifiers & Qt.KeyboardModifier.ShiftModifier and self._selection_anchor_rgb:
            ordered = self._ordered_non_background_rgbs()
            if rgb in ordered and self._selection_anchor_rgb in ordered:
                start = ordered.index(self._selection_anchor_rgb)
                end = ordered.index(rgb)
                lo, hi = sorted((start, end))
                old = set(self.selected_rgbs)
                for value in ordered[lo:hi + 1]:
                    if value not in self.selected_rgbs:
                        self.selected_rgbs.append(value)
                self.parent_rgb = rgb
                self._refresh_used_color_styles(old | set(self.selected_rgbs))
                self.selectedColorsChanged.emit(set(self.selected_rgbs))
                return
        self._selection_anchor_rgb = rgb
        self._toggle_used_color_selection(rgb)

    def _begin_used_color_drag(self, rgb, modifiers):
        if rgb == self.background_rgb:
            return
        self._selection_drag_active = True
        self._selection_drag_touched = {rgb}
        # 未選択から開始＝ONへ、選択済みから開始＝OFFへなぞる。
        self._selection_drag_state = rgb not in self.selected_rgbs

    def _used_color_rgb_at_global(self, global_position):
        # 表示・マスク・置換色列へはみ出しても誤操作しないよう、
        # 「選択」列のラッパー内だけをドラッグ対象にする。
        for rgb, widget in self.source_wrappers.items():
            if rgb == self.background_rgb:
                continue
            if widget.rect().contains(widget.mapFromGlobal(global_position)):
                return rgb
        return None

    def _move_used_color_drag(self, global_position):
        if not self._selection_drag_active:
            return
        rgb = self._used_color_rgb_at_global(global_position)
        if rgb is None or rgb in self._selection_drag_touched:
            return
        self._selection_drag_touched.add(rgb)
        old = set(self.selected_rgbs)
        if self._selection_drag_state:
            if rgb not in self.selected_rgbs:
                self.selected_rgbs.append(rgb)
        elif rgb in self.selected_rgbs:
            self.selected_rgbs.remove(rgb)
        self.parent_rgb = self.selected_rgbs[-1] if self.selected_rgbs else None
        self._refresh_used_color_styles(old | set(self.selected_rgbs))
        self.selectedColorsChanged.emit(set(self.selected_rgbs))

    def _end_used_color_drag(self):
        self._selection_drag_active = False
        self._selection_drag_touched.clear()

    def _toggle_used_color_selection(self, rgb):
        if rgb == self.background_rgb:
            return
        affected = set(self.selected_rgbs)
        if rgb in self.selected_rgbs:
            self.selected_rgbs.remove(rgb)
        else:
            self.selected_rgbs.append(rgb)
        self.parent_rgb = self.selected_rgbs[-1] if self.selected_rgbs else None
        affected.update(self.selected_rgbs)
        for key in affected:
            self._set_source_button_style(key)
        self.selectedColorsChanged.emit(set(self.selected_rgbs))

    def _retain_parent_selection(self):
        old = set(self.selected_rgbs)
        self.selected_rgbs = [self.parent_rgb] if self.parent_rgb is not None else []
        for rgb in old | set(self.selected_rgbs):
            self._set_source_button_style(rgb)
        self.selectedColorsChanged.emit(set(self.selected_rgbs))

    def _emit_merge(self):
        if self.parent_rgb is None or len(self.selected_rgbs) < 2:
            window = self.window()
            if hasattr(window, "statusBar"):
                window.statusBar().showMessage(
                    "統合する使用色を2色以上選択してください。最後に選んだ色が親です。",
                    2800,
                )
            return
        selected = set(self.selected_rgbs)
        self.mergeColorsRequested.emit(tuple(self.parent_rgb), selected)

    def _set_checkbox_without_signal(self, rgb, enabled):
        checkbox = self.visibility_checks.get(rgb)
        self.enabled_colors[rgb] = bool(enabled)
        if checkbox is not None and checkbox.isChecked() != bool(enabled):
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(enabled))
            checkbox.blockSignals(False)

    def _isolate_visible_color(self, source_rgb):
        if (
            self._isolated_rgb == source_rgb
            and self._pre_isolate_enabled is not None
        ):
            restore = dict(self._pre_isolate_enabled)
            self._isolated_rgb = None
            self._pre_isolate_enabled = None
            for rgb in self.visibility_checks:
                self._set_checkbox_without_signal(
                    rgb,
                    restore.get(rgb, True),
                )
        else:
            self._pre_isolate_enabled = dict(self.enabled_colors)
            self._isolated_rgb = source_rgb
            for rgb in self.visibility_checks:
                self._set_checkbox_without_signal(
                    rgb,
                    rgb == source_rgb,
                )
        self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _begin_visibility_sweep(self, source_rgb, checked):
        self._isolated_rgb = None
        self._pre_isolate_enabled = None
        self._visibility_sweep_active = True
        self._visibility_sweep_state = bool(checked)
        self._visibility_sweep_touched = {source_rgb}
        old = self.enabled_colors.get(source_rgb, True)
        self._set_checkbox_without_signal(source_rgb, checked)
        self._visibility_sweep_changed = old != bool(checked)

    def _checkbox_rgb_at_global(self, global_position):
        for rgb, checkbox in self.visibility_checks.items():
            if checkbox.rect().contains(
                checkbox.mapFromGlobal(global_position)
            ):
                return rgb
        return None

    def _move_visibility_sweep(self, global_position):
        if not self._visibility_sweep_active:
            return
        rgb = self._checkbox_rgb_at_global(global_position)
        if (
            rgb is None
            or rgb in self._visibility_sweep_touched
        ):
            return
        self._visibility_sweep_touched.add(rgb)
        old = self.enabled_colors.get(rgb, True)
        self._set_checkbox_without_signal(
            rgb, self._visibility_sweep_state
        )
        self._visibility_sweep_changed |= (
            old != self._visibility_sweep_state
        )

    def _end_visibility_sweep(self):
        if not self._visibility_sweep_active:
            return
        self._visibility_sweep_active = False
        if self._visibility_sweep_changed:
            self.visibleColorsChanged.emit(self.enabled_rgb_set())
        self._visibility_sweep_touched.clear()
        self._visibility_sweep_changed = False

    def _set_color_visible(self, source_rgb, checked):
        if self._visibility_sweep_active:
            return
        self._isolated_rgb = None
        self._pre_isolate_enabled = None
        self.enabled_colors[source_rgb] = bool(checked)
        self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _toggle_replacement(self, source_rgb):
        """未設定なら登録、登録済みなら同じクリックで解除する。"""
        if source_rgb == self.background_rgb:
            return
        if source_rgb in self.replacements:
            self._clear_replacement(source_rgb)
        else:
            self._request_replacement(source_rgb)

    def _close_replacement_editor(self):
        popup = self._replacement_popup
        self._replacement_popup = None
        self._replacement_popup_rgb = None
        if popup is not None:
            popup.close()
            popup.deleteLater()

    def _replacement_popup_destroyed(self, popup):
        if self._replacement_popup is popup:
            self._replacement_popup = None
            self._replacement_popup_rgb = None

    def _toggle_replacement_editor(self, source_rgb, global_position):
        """右クリックで開き、同じ置換色をもう一度右クリックすると閉じる。"""
        if source_rgb == self.background_rgb:
            return
        if (
            self._replacement_popup is not None
            and self._replacement_popup.isVisible()
            and self._replacement_popup_rgb == source_rgb
        ):
            self._close_replacement_editor()
            return

        self._close_replacement_editor()
        color = self.replacements.get(source_rgb)
        if color is None:
            window = self.window()
            if hasattr(window, "canvas"):
                canvas = window.canvas
                color = (
                    canvas.sub_color
                    if canvas.color_mode == "sub"
                    else canvas.main_color
                )
            else:
                color = QColor(*source_rgb)
            # ポップアップを開いた時点で編集対象として登録する。
            self._set_replacement_color(source_rgb, color)

        popup = ReplacementColorPopup(
            QColor(color),
            source_rgb,
            self,
        )
        self._replacement_popup = popup
        self._replacement_popup_rgb = source_rgb
        popup.colorChanged.connect(
            lambda changed, rgb=source_rgb:
            self._set_replacement_color(rgb, changed)
        )
        popup.destroyed.connect(
            lambda _obj=None, current=popup:
            self._replacement_popup_destroyed(current)
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
        popup.activateWindow()

    def _request_replacement(self, source_rgb):
        if source_rgb == self.background_rgb:
            return
        window = self.window()
        if not hasattr(window, "canvas"):
            return
        canvas = window.canvas
        color = (
            canvas.sub_color
            if canvas.color_mode == "sub"
            else canvas.main_color
        )
        self.replacements[source_rgb] = QColor(color)
        self._set_replacement_button_style(source_rgb)

    def register_replacements(self, mapping):
        registered = 0
        for source_rgb, target_rgb in mapping.items():
            source_rgb = tuple(int(value) for value in source_rgb[:3])
            target_rgb = tuple(int(value) for value in target_rgb[:3])
            if (
                source_rgb == self.background_rgb
                or source_rgb not in self.replacement_buttons
            ):
                continue
            self.replacements[source_rgb] = QColor(*target_rgb)
            self._set_replacement_button_style(source_rgb)
            registered += 1
        return registered

    def _set_replacement_color(self, source_rgb, color):
        if source_rgb == self.background_rgb:
            return
        self.replacements[source_rgb] = QColor(color)
        self._set_replacement_button_style(source_rgb)

    def _clear_replacement(self, source_rgb):
        """登録済みの置換色を、同じ左クリックでもう一度押して解除する。"""
        if source_rgb == self.background_rgb:
            return
        if self._replacement_popup_rgb == source_rgb:
            self._close_replacement_editor()
        self.replacements.pop(source_rgb, None)
        self._set_replacement_button_style(source_rgb)

    def _emit_replacements(self):
        mapping = {
            tuple(source): (color.red(), color.green(), color.blue())
            for source, color in self.replacements.items()
            if tuple(source) != self.background_rgb
        }
        self.applyReplacementRequested.emit(mapping)


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
        note.setStyleSheet("color:#555;font-size:10px;")
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
        except Exception:
            # 操作中の簡易表示失敗は、確定済み表示へ静かに戻す。
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
                except Exception:
                    pass
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
                    except Exception:
                        pass
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
        except Exception:
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
        except Exception as exc:
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
        except Exception as exc:
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


class TimeRemapPasteDialog(QDialog):
    """コピー情報をタイムシート表へ貼り付けて確認する画面。"""

    def __init__(self, initial_text="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("タイムリマップをタイムシートへ貼り付け")
        self.resize(760, 650)
        self._parsed_preview = None
        self._parsed_source = None
        self._parsed_raw_text = None
        self._included_rows = []
        self._column_layer_bindings = {}
        self._updating_preview_items = False
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        header = QHBoxLayout()
        title = QLabel("<b>読み込みデータ</b>")
        paste_button = QPushButton(
            "クリップボードをタイムシートへ貼付"
        )
        xdts_button = QPushButton("XDTSを読み込む…")
        clear_button = QPushButton("クリア")
        paste_button.clicked.connect(self._paste_clipboard)
        xdts_button.clicked.connect(self._load_xdts_file)
        clear_button.clicked.connect(self.text_clear)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(xdts_button)
        header.addWidget(paste_button)
        header.addWidget(clear_button)
        layout.addLayout(header)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            "After Effects／ToeiDigitalTimeSheetのコピー情報を貼り付けるか、"
            "XDTSファイルを読み込みます。XDTSはこの画面へドロップできます。"
        )
        self.text_edit.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.NoWrap
        )
        self.text_edit.setMaximumHeight(150)
        # ファイルドロップはダイアログ全体で一貫して処理する。
        self.text_edit.setAcceptDrops(False)
        layout.addWidget(self.text_edit)

        sheet_header = QHBoxLayout()
        sheet_title = QLabel("<b>タイムシートプレビュー</b>")
        self.preview_status = QLabel("データ待機中")
        self.preview_status.setAlignment(
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignVCenter
        )
        sheet_header.addWidget(sheet_title)
        sheet_header.addStretch(1)
        sheet_header.addWidget(self.preview_status)
        layout.addLayout(sheet_header)

        self.preview_table = QTableWidget(0, 2)
        self.preview_table.setHorizontalHeaderLabels(["使用", "F"])
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.preview_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setShowGrid(True)
        self.preview_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self.preview_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Fixed
        )
        self.preview_table.setColumnWidth(0, 48)
        self.preview_table.setColumnWidth(1, 60)
        self.preview_table.horizontalHeader().setMinimumHeight(58)
        self.preview_table.horizontalHeader().setSectionsClickable(True)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.horizontalHeader().sectionClicked.connect(
            self._preview_header_clicked
        )
        self.preview_table.verticalHeader().setDefaultSectionSize(22)
        self.preview_table.setAcceptDrops(False)
        self.preview_table.viewport().setAcceptDrops(False)
        self.preview_table.setStyleSheet(
            "QTableWidget{gridline-color:#aeb8bf;"
            "background:#fafafa;alternate-background-color:#eef5f8;}"
            "QHeaderView::section{background:#dce8ed;"
            "padding:3px;border:1px solid #aeb8bf;}"
            "QTableWidget::item:selected{background:#b9dced;color:#111;}"
        )
        self.preview_table.itemChanged.connect(
            self._preview_item_changed
        )
        layout.addWidget(self.preview_table, 1)

        filter_row = QHBoxLayout()
        exclude_button = QPushButton("選択行を除外")
        include_button = QPushButton("選択行を使用")
        include_all_button = QPushButton("全て使用")
        exclude_button.clicked.connect(
            lambda: self._set_selected_rows_included(False)
        )
        include_button.clicked.connect(
            lambda: self._set_selected_rows_included(True)
        )
        include_all_button.clicked.connect(self._include_all_rows)
        filter_row.addWidget(exclude_button)
        filter_row.addWidget(include_button)
        filter_row.addWidget(include_all_button)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        note = QLabel(
            "ACTION・CELL・CAMをXDTSの列ごとに表示します。"
            "CELL名をクリックするとPaintMaskAnimatorのレイヤーへ紐づけできます。"
            "「使用」を外した行は取り除き、後続を詰めて反映します。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size:10px;color:#555;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(
            QDialogButtonBox.StandardButton.Ok
        ).setText("このタイムシートをタイムラインへ反映")
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(140)
        self._preview_timer.timeout.connect(
            self._update_timesheet_preview
        )
        self.text_edit.textChanged.connect(
            self._preview_timer.start
        )
        self.text_edit.setPlainText(str(initial_text or ""))
        QTimer.singleShot(0, self._update_timesheet_preview)

    def _paste_clipboard(self):
        self.text_edit.setPlainText(
            QApplication.clipboard().text()
        )
        self.text_edit.setFocus()

    @staticmethod
    def _dropped_xdts_path(mime_data):
        if mime_data is None or not mime_data.hasUrls():
            return None
        for url in mime_data.urls():
            path = str(url.toLocalFile() or "")
            if Path(path).suffix.lower() in (".xdts", ".xtds"):
                return path
        return None

    def dragEnterEvent(self, event):
        if self._dropped_xdts_path(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._dropped_xdts_path(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        path = self._dropped_xdts_path(event.mimeData())
        if path:
            self._load_xdts_path(path)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _load_xdts_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "XDTSタイムシートを読み込む",
            "",
            "XDTSタイムシート (*.xdts *.xtds);;すべてのファイル (*)",
        )
        if not path:
            return
        self._load_xdts_path(path)

    def _load_xdts_path(self, path):
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            QMessageBox.warning(
                self, "XDTS読み込み", f"読み込めませんでした。\n\n{exc}"
            )
            return False
        self.text_edit.setPlainText(text)
        self.text_edit.setFocus()
        self._update_timesheet_preview()
        return True

    def text_clear(self):
        self.text_edit.clear()
        self.preview_table.setRowCount(0)
        self.preview_table.setColumnCount(2)
        self.preview_table.setHorizontalHeaderLabels(["使用", "F"])
        self._parsed_preview = None
        self._parsed_source = None
        self._parsed_raw_text = None
        self._included_rows.clear()
        self._column_layer_bindings.clear()
        self.preview_status.setText("データ待機中")
        self.preview_status.setStyleSheet("color:#666;")

    def _parser_owner(self):
        owner = self.parent()
        return owner if owner is not None else None

    def _sheet_columns(self):
        if self._parsed_source is None:
            return []
        columns = list(self._parsed_source.get("sheet_columns", []))
        if columns:
            return columns
        tracks = list(self._parsed_source.get("tracks", []))
        if tracks:
            return [
                dict(
                    track,
                    uid=f"cell:{int(track.get('track_no', index))}",
                    field_id=0,
                    group="CELL",
                    bindable=True,
                    display_values=[
                        "×" if state is None else str(int(state))
                        for state in track.get("states", [])
                    ],
                )
                for index, track in enumerate(tracks)
            ]
        states = list(self._parsed_source.get("states", []))
        if not states:
            return []
        return [{
            "uid": "cell:0",
            "field_id": 0,
            "track_no": 0,
            "group": "CELL",
            "name": str(self._parsed_source.get("track_name", "セル")),
            "states": states,
            "display_values": [
                "×" if state is None else str(int(state))
                for state in states
            ],
            "bindable": True,
            "blank_label_count": int(
                self._parsed_source.get("blank_label_count", 0)
            ),
        }]

    @staticmethod
    def _column_uid(column):
        return str(column.get("uid", ""))

    def _available_layers(self):
        owner = self._parser_owner()
        canvas = getattr(owner, "canvas", None)
        frames = getattr(canvas, "frames", []) if canvas is not None else []
        if not frames:
            return []
        frame_index = max(
            0, min(int(canvas.current_frame), len(frames) - 1)
        )
        return [
            (index, str(layer.name))
            for index, layer in enumerate(frames[frame_index].layers)
        ]

    def _initialize_layer_bindings(self):
        self._column_layer_bindings.clear()
        layers = self._available_layers()
        bindable = [
            column for column in self._sheet_columns()
            if bool(column.get("bindable", False))
        ]
        used_layers = set()
        for column in bindable:
            name = str(column.get("name", "")).strip().casefold()
            match = next(
                (
                    index for index, layer_name in layers
                    if index not in used_layers
                    and layer_name.strip().casefold() == name
                ),
                None,
            )
            if match is not None:
                self._column_layer_bindings[
                    self._column_uid(column)
                ] = match
                used_layers.add(match)
        if not self._column_layer_bindings and bindable and layers:
            owner = self._parser_owner()
            active = int(getattr(owner.canvas, "active_layer_index", 0))
            if any(index == active for index, _name in layers):
                self._column_layer_bindings[
                    self._column_uid(bindable[0])
                ] = active

    def _save_current_included_rows(self):
        if self.preview_table.rowCount() <= 0:
            return
        self._included_rows = []
        for row in range(self.preview_table.rowCount()):
            item = self.preview_table.item(row, 0)
            self._included_rows.append(
                item is not None
                and item.checkState() == Qt.CheckState.Checked
            )

    def _set_column_layer_binding(self, uid, layer_index):
        uid = str(uid)
        if layer_index is None:
            self._column_layer_bindings.pop(uid, None)
        else:
            layer_index = int(layer_index)
            for other_uid, other_index in list(
                self._column_layer_bindings.items()
            ):
                if other_uid != uid and other_index == layer_index:
                    self._column_layer_bindings.pop(other_uid, None)
            self._column_layer_bindings[uid] = layer_index
        self._update_preview_headers()
        self._update_preview_status()

    def _preview_header_clicked(self, section):
        column_index = int(section) - 2
        columns = self._sheet_columns()
        if not (0 <= column_index < len(columns)):
            return
        column = columns[column_index]
        if not bool(column.get("bindable", False)):
            return
        uid = self._column_uid(column)
        current = self._column_layer_bindings.get(uid)
        menu = QMenu(self)
        title = menu.addAction(
            f"「{column.get('name', 'セル')}」の紐づけ先"
        )
        title.setEnabled(False)
        menu.addSeparator()
        clear_action = menu.addAction("紐づけを解除（読み込まない）")
        clear_action.setCheckable(True)
        clear_action.setChecked(current is None)
        clear_action.triggered.connect(
            lambda _checked=False, key=uid:
            self._set_column_layer_binding(key, None)
        )
        layers = self._available_layers()
        if layers:
            menu.addSeparator()
        for layer_index, layer_name in layers:
            action = menu.addAction(layer_name)
            action.setCheckable(True)
            action.setChecked(current == layer_index)
            action.triggered.connect(
                lambda _checked=False, key=uid, index=layer_index:
                self._set_column_layer_binding(key, index)
            )
        if not layers:
            unavailable = menu.addAction("紐づけ可能なレイヤーがありません")
            unavailable.setEnabled(False)
        menu.exec(QCursor.pos())

    def _update_preview_headers(self):
        columns = self._sheet_columns()
        layers = dict(self._available_layers())
        labels = ["使用", "F"]
        for column in columns:
            group = str(column.get("group", "CELL"))
            name = str(column.get("name", ""))
            uid = self._column_uid(column)
            layer_index = self._column_layer_bindings.get(uid)
            if bool(column.get("bindable", False)):
                linked = layers.get(layer_index)
                link_text = f"→ {linked}" if linked else "クリックで紐づけ"
                labels.append(f"{group}\n{name}\n{link_text}")
            else:
                labels.append(f"{group}\n{name}")
        self.preview_table.setHorizontalHeaderLabels(labels)
        colors = {
            "ACTION": QColor("#e5edf7"),
            "CELL": QColor("#f7dddd"),
            "CAM": QColor("#dff3df"),
        }
        for index, column in enumerate(columns, 2):
            item = self.preview_table.horizontalHeaderItem(index)
            if item is None:
                continue
            group = str(column.get("group", "CELL"))
            item.setBackground(colors.get(group, QColor("#e7ecef")))
            if bool(column.get("bindable", False)):
                item.setToolTip(
                    "クリックして読み込み先レイヤーを選択します。"
                )

    def _set_selected_rows_included(self, included):
        rows = sorted({index.row() for index in self.preview_table.selectedIndexes()})
        if not rows:
            row = self.preview_table.currentRow()
            rows = [row] if row >= 0 else []
        state = Qt.CheckState.Checked if included else Qt.CheckState.Unchecked
        for row in rows:
            item = self.preview_table.item(row, 0)
            if item is not None:
                item.setCheckState(state)

    def _include_all_rows(self):
        for row in range(self.preview_table.rowCount()):
            item = self.preview_table.item(row, 0)
            if item is not None:
                item.setCheckState(Qt.CheckState.Checked)

    def _preview_item_changed(self, item):
        if self._updating_preview_items or item.column() != 0:
            return
        self._save_current_included_rows()
        self._update_preview_status()

    def _update_preview_status(self):
        columns = self._sheet_columns()
        if not columns:
            return
        duration = max(
            [len(column.get("display_values", [])) for column in columns]
            + [len(self._included_rows)]
        )
        included = list(self._included_rows)
        if len(included) != duration:
            included = [True] * duration
        used_count = sum(bool(value) for value in included)
        linked_count = sum(
            1 for column in columns
            if self._column_uid(column) in self._column_layer_bindings
        )
        format_name = str(
            self._parsed_source.get("format", "タイムリマップ")
        )
        self.preview_status.setText(
            f"{format_name}／使用 {used_count}／"
            f"除外 {max(0, duration - used_count)}／"
            f"レイヤー紐づけ {linked_count}"
        )
        self.preview_status.setStyleSheet("color:#176b42;")

    def _populate_preview_table(self):
        columns = self._sheet_columns()
        if not columns:
            return
        duration = max(
            len(column.get("display_values", [])) for column in columns
        )
        start_frame = int(self._parsed_source.get("start_frame", 0))
        if len(self._included_rows) != duration:
            self._included_rows = [True] * duration

        self._updating_preview_items = True
        self.preview_table.setUpdatesEnabled(False)
        self.preview_table.blockSignals(True)
        try:
            self.preview_table.setColumnCount(2 + len(columns))
            self.preview_table.setRowCount(duration)
            self.preview_table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.ResizeMode.Fixed
            )
            self.preview_table.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.Fixed
            )
            for column_index in range(2, 2 + len(columns)):
                self.preview_table.horizontalHeader().setSectionResizeMode(
                    column_index, QHeaderView.ResizeMode.Interactive
                )
                self.preview_table.setColumnWidth(column_index, 112)
            self._update_preview_headers()
            for row in range(duration):
                use_item = QTableWidgetItem()
                use_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                use_item.setCheckState(
                    Qt.CheckState.Checked
                    if self._included_rows[row]
                    else Qt.CheckState.Unchecked
                )
                frame_item = QTableWidgetItem(str(start_frame + row + 1))
                for item in (use_item, frame_item):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.preview_table.setItem(row, 0, use_item)
                self.preview_table.setItem(row, 1, frame_item)
                for offset, column in enumerate(columns, 2):
                    values = list(column.get("display_values", []))
                    value = values[row] if row < len(values) else ""
                    cell_item = QTableWidgetItem(str(value))
                    cell_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if str(value) in ("×", "○", "●"):
                        cell_item.setForeground(QColor("#777777"))
                    self.preview_table.setItem(row, offset, cell_item)
                if not self._included_rows[row]:
                    for column_index in range(1, 2 + len(columns)):
                        item = self.preview_table.item(row, column_index)
                        if item is not None:
                            item.setForeground(QColor("#999999"))
                            item.setBackground(QColor("#eeeeee"))
        finally:
            self.preview_table.blockSignals(False)
            self.preview_table.setUpdatesEnabled(True)
            self._updating_preview_items = False
        self._update_preview_status()

    def _update_timesheet_preview(self):
        raw_text = self.text_edit.toPlainText()
        if not raw_text.strip():
            self.text_clear()
            return
        if (
            self._parsed_source is not None
            and raw_text == self._parsed_raw_text
        ):
            return

        owner = self._parser_owner()
        if owner is None or not hasattr(
            owner, "parse_time_remap_text"
        ):
            self.preview_status.setText("解析機能を取得できません")
            self.preview_status.setStyleSheet("color:#b00020;")
            return

        try:
            parsed = owner.parse_time_remap_text(raw_text)
        except Exception as exc:
            self._parsed_preview = None
            self._parsed_source = None
            self._parsed_raw_text = None
            self._included_rows.clear()
            self._column_layer_bindings.clear()
            self.preview_table.setRowCount(0)
            message = str(exc).splitlines()[0]
            self.preview_status.setText(message)
            self.preview_status.setStyleSheet("color:#b00020;")
            return
        self._parsed_source = parsed
        self._parsed_raw_text = raw_text
        self._parsed_preview = parsed
        columns = self._sheet_columns()
        duration = max(
            [len(column.get("display_values", [])) for column in columns]
            + [0]
        )
        self._included_rows = [True] * duration
        self._initialize_layer_bindings()
        self._populate_preview_table()

    def parsed_result(self):
        if self._parsed_source is None:
            return None
        self._save_current_included_rows()
        columns = self._sheet_columns()
        if not columns:
            return None
        if not any(self._included_rows):
            return None
        result = dict(self._parsed_source)
        filtered_columns = []
        for column in columns:
            filtered_column = dict(column)
            states = list(column.get("states", []))
            values = list(column.get("display_values", []))
            if states:
                filtered_column["states"] = [
                    state for state, keep in zip(states, self._included_rows)
                    if keep
                ]
            filtered_column["display_values"] = [
                value for value, keep in zip(values, self._included_rows)
                if keep
            ]
            filtered_columns.append(filtered_column)
        result["sheet_columns"] = filtered_columns
        result["layer_bindings"] = dict(self._column_layer_bindings)
        primary = next(
            (
                column for column in filtered_columns
                if self._column_uid(column) in self._column_layer_bindings
                and column.get("states")
            ),
            next(
                (
                    column for column in filtered_columns
                    if column.get("states")
                ),
                None,
            ),
        )
        if primary is not None:
            result["states"] = list(primary.get("states", []))
            result["blank_label_count"] = int(
                primary.get("blank_label_count", 0)
            )
            result["track_name"] = str(primary.get("name", "セル欄"))
        start_frame = int(self._parsed_source.get("start_frame", 0))
        result["start_frame"] = start_frame
        used_count = sum(bool(value) for value in self._included_rows)
        result["end_frame"] = start_frame + used_count - 1
        return result

    def _accept_if_valid(self):
        self._update_timesheet_preview()
        self._parsed_preview = self.parsed_result()
        if self._parsed_preview is None:
            QMessageBox.warning(
                self,
                "タイムリマップ",
                "有効なタイムシートを貼り付け、使用する行を残してください。",
            )
            return
        if (
            self._parsed_preview.get("format", "").startswith("XDTS")
            and not self._parsed_preview.get("layer_bindings")
        ):
            QMessageBox.warning(
                self,
                "XDTS読み込み",
                "読み込むCELL名をクリックし、紐づけ先レイヤーを選択してください。",
            )
            return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle(APP_DISPLAY_NAME);self.resize(1500,960);self.setAcceptDrops(True)
        self.canvas=PaintCanvas();self.tools=ToolPanel();self.timeline=TimelineWidget();self.palette=UsedColorPanel();self.timer=QTimer(self);self.timer.timeout.connect(self.advance)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._playback_started_at=None
        self._playback_emitted_steps=0
        self._used_color_cache = {}
        self._used_color_layer_cache = {}
        self._used_color_request = 0
        self._used_color_timer = QTimer(self)
        self._used_color_timer.setSingleShot(True)
        self._used_color_timer.setInterval(180)
        self._used_color_timer.timeout.connect(self.refresh_used_colors)
        self._pending_visible_colors = None
        self._suppress_used_color_refresh_once = False
        self._tween_command_popup = None
        self._onion_settings_browser = None
        self._held_canvas_shortcut_tokens = set()
        self._ui_hold_drag_mode = None
        self._ui_hold_scroll_area = None
        self._ui_hold_grab_widget = None
        self._ui_hold_start_global = QPointF()
        self._ui_hold_last_global = QPointF()
        self._ui_hold_start_scroll = (0, 0)
        self._visible_color_timer = QTimer(self)
        self._visible_color_timer.setSingleShot(True)
        self._visible_color_timer.setInterval(20)
        self._visible_color_timer.timeout.connect(self._apply_pending_visible_colors)
        self.current_project_path = None
        self.build_actions();self.build_menu();self.build_ui();self.connect();self.refresh_ui()
        QApplication.instance().installEventFilter(self)
        self.update_project_title()
        QTimer.singleShot(0,self.fit_canvas)
        QTimer.singleShot(
            0,
            lambda: disable_windows_ink_feedback(
                self,
                self.canvas,
            ),
        )
    def make_shortcut_action(self, name, callback, shortcut=""):
        action = QAction(name, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        action.triggered.connect(callback)
        self.addAction(action)
        return action

    def build_actions(self):
        self.a_new=QAction("新規作成…",self);self.a_new.setShortcut("Ctrl+N");self.a_new.triggered.connect(self.new_doc)
        self.a_resize=QAction("キャンバスサイズの変更…",self);self.a_resize.triggered.connect(self.resize_doc)
        self.a_undo=QAction("元に戻す",self);self.a_undo.setShortcut("Ctrl+Z");self.a_undo.triggered.connect(self.undo_with_used_colors)
        self.a_redo=QAction("やり直す",self);self.a_redo.setShortcut("Ctrl+Y");self.a_redo.triggered.connect(self.redo_with_used_colors)
        self.a_copy=QAction("コピー",self);self.a_copy.setShortcut("Ctrl+C");self.a_copy.triggered.connect(self.canvas.copy_selection)
        self.a_cut=QAction("切り取り",self);self.a_cut.setShortcut("Ctrl+X");self.a_cut.triggered.connect(self.canvas.cut_selection)
        self.a_paste=QAction("貼り付け",self);self.a_paste.setShortcut("Ctrl+V");self.a_paste.triggered.connect(self.canvas.paste_clipboard)
        self.a_open_project=QAction("プロジェクトを開く…",self)
        self.a_open_project.setShortcut("Ctrl+O")
        self.a_open_project.triggered.connect(self.open_project_dialog)

        self.a_import_images=QAction("画像を読み込む…",self)
        self.a_import_images.triggered.connect(self.import_images_dialog)
        self.a_import_folder=QAction("画像フォルダーを読み込む…",self)
        self.a_import_folder.triggered.connect(self.import_image_folder_dialog)
        self.a_import_psd=QAction("PSDを読み込む…",self)
        self.a_import_psd.triggered.connect(self.import_psd_dialog)

        self.a_save_project=QAction("上書き保存",self)
        self.a_save_project.setShortcut("Ctrl+S")
        self.a_save_project.triggered.connect(self.save_project)

        self.a_save_project_as=QAction("名前を付けて保存…",self)
        self.a_save_project_as.setShortcut("Ctrl+Shift+S")
        self.a_save_project_as.triggered.connect(self.save_project_as)

        self.a_save=QAction("現在のコマをPNG書き出し…",self)
        self.a_save.triggered.connect(self.save_png)
        self.a_save_tga=QAction("現在のコマをTGA書き出し…",self)
        self.a_save_tga.triggered.connect(self.save_tga)
        self.a_export_png_seq=QAction("連番PNG＋CSV書き出し…",self);self.a_export_png_seq.triggered.connect(lambda:self.export_key_sequence("PNG"))
        self.a_export_tga_seq=QAction("連番TGA＋CSV書き出し…",self);self.a_export_tga_seq.triggered.connect(lambda:self.export_key_sequence("TGA"))
        self.a_export_xdts=QAction("XDTSタイムシートを書き出す…",self)
        self.a_export_xdts.triggered.connect(self.export_xdts_dialog)
        self.a_export_psd=QAction("PSDを書き出す…",self)
        self.a_export_psd.triggered.connect(self.export_psd_dialog)
        self.a_prev=QAction("前のフレーム",self);self.a_prev.setShortcut("1");self.a_prev.triggered.connect(self.previous_timeline_frame)
        self.a_next=QAction("次のフレーム",self);self.a_next.setShortcut("2");self.a_next.triggered.connect(self.next_timeline_frame)
        self.a_pressure=QAction("筆圧設定…",self);self.a_pressure.triggered.connect(self.pressure)
        self.a_isolate_color=QAction("選択色だけ表示",self)
        self.a_isolate_color.triggered.connect(self.isolate_selected_color)
        self.a_clear_color_filter=QAction("特定色表示を解除",self)
        self.a_clear_color_filter.triggered.connect(self.clear_selected_color_filter)
        self.a_swap_main_sub=QAction("メインカラーとサブカラーを交換",self)
        self.a_swap_main_sub.triggered.connect(self.swap_main_sub)
        self.a_silhouette=QAction("背景以外を黒シルエット表示",self); self.a_silhouette.setCheckable(True)
        self.a_silhouette.triggered.connect(self.toggle_silhouette)
        self.a_remove_dust=QAction("ゴミ取り／塗り抜け…",self)
        self.a_remove_dust.triggered.connect(self.remove_dust_fill_surrounding)
        self.a_shortcuts=QAction("ショートカット設定…",self);self.a_shortcuts.triggered.connect(self.shortcuts)
        self.tool_actions={}
        defaults={"brush":"P","line":"U","shape":"O","bucket":"G","lasso_fill":"F","lasso":"L","rect_select":"R","auto_select":"W","eyedropper":"","dust":"D"}
        for tid,label in ToolPanel.TOOLS:
            a=QAction("ツール："+label,self);a.setShortcut(defaults.get(tid,""));a.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut);a.triggered.connect(lambda _=False,t=tid:self.tools.select_tool(t));self.addAction(a);self.tool_actions[tid]=a
        self.general_actions=[("新規作成",self.a_new),("プロジェクトを開く",self.a_open_project),("上書き保存",self.a_save_project),("元に戻す",self.a_undo),("やり直す",self.a_redo),("前のフレーム",self.a_prev),("次のフレーム",self.a_next)]
        self.tool_action_list=[("ツール："+label,self.tool_actions[tid]) for tid,label in ToolPanel.TOOLS]

        # 押している間だけ有効になるキャンバス操作。QActionは設定値の保持に使い、
        # 通常のアプリケーションショートカットとしては登録しない。
        self.a_hold_hand = QAction("ハンド（押している間）", self)
        self.a_hold_hand.setProperty("holdOperation", True)
        self.a_hold_hand.setProperty("holdShortcutText", "Space")
        self.a_hold_hand.setShortcut(QKeySequence("Space"))
        self.a_hold_zoom = QAction("拡大縮小（押している間）", self)
        self.a_hold_zoom.setProperty("holdOperation", True)
        self.a_hold_zoom.setProperty("holdShortcutText", "Ctrl+Space")
        self.a_hold_zoom.setShortcut(QKeySequence("Ctrl+Space"))
        self.a_hold_rotate = QAction("回転（押している間）", self)
        self.a_hold_rotate.setProperty("holdOperation", True)
        self.a_hold_rotate.setProperty("holdShortcutText", "Shift+Space")
        self.a_hold_rotate.setShortcut(QKeySequence("Shift+Space"))
        self.a_hold_eyedropper = QAction("スポイト（押している間）", self)
        self.a_hold_eyedropper.setProperty("holdOperation", True)
        self.a_hold_eyedropper.setProperty("holdShortcutText", "Alt")
        self.a_hold_eyedropper.setShortcut(QKeySequence("Alt"))
        self.canvas_operation_actions = [
            ("ハンド", self.a_hold_hand),
            ("拡大縮小", self.a_hold_zoom),
            ("回転", self.a_hold_rotate),
            ("スポイト", self.a_hold_eyedropper),
        ]

        # File and edit actions not previously exposed in the shortcut dialog.
        for action in (
            self.a_new, self.a_open_project, self.a_import_images,
            self.a_save_project, self.a_save_project_as,
            self.a_resize, self.a_undo, self.a_redo,
            self.a_cut, self.a_copy, self.a_paste, self.a_save, self.a_save_tga,
            self.a_export_png_seq, self.a_export_tga_seq, self.a_prev, self.a_next,
            self.a_pressure, self.a_isolate_color, self.a_clear_color_filter,
            self.a_swap_main_sub, self.a_silhouette, self.a_remove_dust,
        ):
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)

        self.a_export_mp4 = self.make_shortcut_action(
            "MP4書き出し", self.export_mp4
        )

        # Tool-panel commands.
        self.a_select_main = self.make_shortcut_action(
            "描画色：メインを選択", lambda: self.set_color_mode("main")
        )
        self.a_select_sub = self.make_shortcut_action(
            "描画色：サブを選択", lambda: self.set_color_mode("sub")
        )
        self.a_select_transparent = self.make_shortcut_action(
            "描画色：背景色を選択", lambda: self.set_color_mode("transparent")
        )
        self.a_toggle_draw_background = self.make_shortcut_action(
            "描画色と背景色を切り替え", self.toggle_draw_background_color, "C"
        )
        self.a_swap_main_sub = self.make_shortcut_action(
            "メイン色とサブ色を入れ替え", self.swap_main_sub, "X"
        )
        self.a_choose_background = self.make_shortcut_action(
            "背景色の表示色を変更", self.choose_background_color
        )
        self.a_mainline_repaint = self.make_shortcut_action(
            "MainLineRepaint", self.main_line_repaint
        )
        self.a_selection_transform = self.make_shortcut_action(
            "選択範囲：自由変形", lambda: self.start_wire_transform("free")
        )
        self.a_selection_scale = self.make_shortcut_action(
            "選択範囲：拡大縮小", lambda: self.start_wire_transform("scale")
        )
        self.a_selection_mesh = self.make_shortcut_action(
            "選択範囲：メッシュ変形", lambda: self.start_wire_transform("mesh")
        )
        self.a_selection_clear = self.make_shortcut_action(
            "選択範囲を解除", self.canvas.clear_selection, "Ctrl+Shift+A"
        )
        self.a_transform_rotate_left = self.make_shortcut_action(
            "変形：左へ90°回転",
            lambda: self.canvas.rotate_selection_transform(-90.0)
        )
        self.a_transform_rotate_right = self.make_shortcut_action(
            "変形：右へ90°回転",
            lambda: self.canvas.rotate_selection_transform(90.0)
        )
        self.a_transform_mesh_grid = self.make_shortcut_action(
            "メッシュ変形：格子数を変更…",
            self.choose_transform_mesh_grid
        )
        self.a_transform_commit = self.make_shortcut_action(
            "変形を確定", self.commit_transform_or_tween
        )
        self.a_transform_commit.setShortcuts([
            QKeySequence(Qt.Key.Key_Return),
            QKeySequence(Qt.Key.Key_Enter),
        ])
        self.a_transform_cancel = self.make_shortcut_action(
            "変形をキャンセル", self.cancel_transform_or_tween
        )
        self.a_bucket_include_sub = self.make_shortcut_action(
            "バケツ：含み塗り ON/OFF",
            lambda: self.tools.bucket_include_sub.toggle()
        )
        self.a_bucket_close_gap = self.make_shortcut_action(
            "バケツ：隙間閉じ ON/OFF",
            lambda: self.tools.bucket_close_gap.toggle()
        )
        self.a_dust_all_frames = self.make_shortcut_action(
            "ゴミ取り：すべてのコマ ON/OFF",
            lambda: self.tools.dust_all_frames.toggle()
        )
        self.a_dust_apply = self.make_shortcut_action(
            "ゴミ取り／塗り抜けを適用", self.remove_dust_fill_surrounding
        )
        self.a_selection_all_frames = self.make_shortcut_action(
            "選択変形：すべてのコマ ON/OFF",
            self.tools.toggle_selection_all_frames
        )

        # Timeline commands.
        self.a_tl_add_exposure = self.make_shortcut_action(
            "タイムライン：コマ数を1つ増やす",
            self.timeline._extend_current_exposure,
        )
        self.a_tl_delete_frame = self.make_shortcut_action(
            "タイムライン：コマを削除",
            self.delete_timeline_frame,
        )
        self.a_tl_previous = self.make_shortcut_action(
            "タイムライン：前のフレーム",
            self.previous_timeline_frame,
        )
        self.a_tl_next = self.make_shortcut_action(
            "タイムライン：次のフレーム",
            self.next_timeline_frame,
        )
        self.a_tl_previous_key = self.make_shortcut_action(
            "タイムライン：前のコマ",
            self.previous_timeline_key,
            "A",
        )
        self.a_tl_next_key = self.make_shortcut_action(
            "タイムライン：次のコマ",
            self.next_timeline_key,
            "S",
        )
        self.a_tl_play = self.make_shortcut_action(
            "タイムライン：再生／停止",
            lambda: self.timeline.play.toggle(),
        )
        self.a_tl_paste_time_remap = self.make_shortcut_action(
            "タイムライン：タイムリマップを貼り付け",
            self.show_time_remap_paste_dialog,
        )
        self.a_tl_onion = self.make_shortcut_action(
            "タイムライン：オニオンスキン設定",
            lambda: self.timeline.onion.toggle(),
        )
        self.a_tl_layer_add = self.make_shortcut_action(
            "タイムライン：レイヤー追加",
            self.canvas.add_layer,
        )
        self.a_tl_layer_delete = self.make_shortcut_action(
            "タイムライン：レイヤー削除",
            self.canvas.delete_layer,
        )
        self.a_tl_layer_rename = self.make_shortcut_action(
            "タイムライン：レイヤー名変更",
            self.timeline.rename_selected_layer,
        )
        self.file_edit_actions = [
            ("新規作成", self.a_new),
            ("プロジェクトを開く", self.a_open_project),
            ("画像を読み込む", self.a_import_images),
            ("画像フォルダーを読み込む", self.a_import_folder),
            ("上書き保存", self.a_save_project),
            ("名前を付けて保存", self.a_save_project_as),
            ("現在のコマをPNG書き出し", self.a_save),
            ("現在のコマをTGA書き出し", self.a_save_tga),
            ("連番PNG＋CSV書き出し", self.a_export_png_seq),
            ("連番TGA＋CSV書き出し", self.a_export_tga_seq),
            ("MP4書き出し", self.a_export_mp4),
            ("元に戻す", self.a_undo),
            ("やり直す", self.a_redo),
            ("切り取り", self.a_cut),
            ("コピー", self.a_copy),
            ("貼り付け", self.a_paste),
            ("キャンバスサイズ変更", self.a_resize),
            ("筆圧設定", self.a_pressure),
            ("背景以外を黒シルエット表示", self.a_silhouette),
            ("選択色だけ表示", self.a_isolate_color),
            ("特定色表示を解除", self.a_clear_color_filter),
            ("メイン色とサブ色を交換", self.a_swap_main_sub),
            ("ゴミ取り", self.a_remove_dust),
            ("選択範囲を解除", self.a_selection_clear),
        ]
        self.tool_command_actions = [
            ("描画色：メインを選択", self.a_select_main),
            ("描画色：サブを選択", self.a_select_sub),
            ("描画色：背景色を選択", self.a_select_transparent),
            ("描画色と背景色を切り替え", self.a_toggle_draw_background),
            ("メイン色とサブ色を入れ替え", self.a_swap_main_sub),
            ("背景色の表示色を変更", self.a_choose_background),
            ("MainLineRepaint", self.a_mainline_repaint),
            ("選択範囲：自由変形", self.a_selection_transform),
            ("選択範囲：拡大縮小", self.a_selection_scale),
            ("選択範囲：メッシュ変形", self.a_selection_mesh),
            ("選択範囲を解除", self.a_selection_clear),
            ("変形：左へ90°回転", self.a_transform_rotate_left),
            ("変形：右へ90°回転", self.a_transform_rotate_right),
            ("メッシュ変形：格子数を変更", self.a_transform_mesh_grid),
            ("変形を確定", self.a_transform_commit),
            ("変形をキャンセル", self.a_transform_cancel),
            ("バケツ：含み塗り ON/OFF", self.a_bucket_include_sub),
            ("バケツ：隙間閉じ ON/OFF", self.a_bucket_close_gap),
            ("ゴミ取り：すべてのコマ ON/OFF", self.a_dust_all_frames),
            ("ゴミ取り／塗り抜けを適用", self.a_dust_apply),
            ("選択変形：すべてのコマ ON/OFF", self.a_selection_all_frames),
        ]
        self.timeline_actions = [
            ("コマ数を1つ増やす", self.a_tl_add_exposure),
            ("コマを削除", self.a_tl_delete_frame),
            ("タイムリマップを貼り付け", self.a_tl_paste_time_remap),
            ("前のフレーム", self.a_tl_previous),
            ("前のコマ", self.a_tl_previous_key),
            ("再生／停止", self.a_tl_play),
            ("次のコマ", self.a_tl_next_key),
            ("次のフレーム", self.a_tl_next),
            ("オニオンスキン設定", self.a_tl_onion),
            ("レイヤー追加", self.a_tl_layer_add),
            ("レイヤー削除", self.a_tl_layer_delete),
            ("レイヤー名変更", self.a_tl_layer_rename),
        ]
    def build_menu(self):
        f=self.menuBar().addMenu("ファイル")
        f.addAction(self.a_new)
        f.addAction(self.a_open_project)
        f.addAction(self.a_import_images)
        f.addAction(self.a_import_folder)
        f.addAction(self.a_import_psd)
        f.addSeparator()
        f.addAction(self.a_save_project)
        f.addAction(self.a_save_project_as)
        f.addSeparator()
        f.addAction(self.a_save)
        f.addAction(self.a_save_tga)
        f.addAction(self.a_export_png_seq)
        f.addAction(self.a_export_tga_seq)
        f.addAction(self.a_export_xdts)
        f.addAction(self.a_export_psd)
        f.addAction(self.a_export_mp4)
        e=self.menuBar().addMenu("編集");e.addAction(self.a_undo);e.addAction(self.a_redo);e.addSeparator();e.addAction(self.a_cut);e.addAction(self.a_copy);e.addAction(self.a_paste);e.addSeparator();e.addAction(self.a_silhouette);e.addAction(self.a_isolate_color);e.addAction(self.a_clear_color_filter);e.addAction(self.a_swap_main_sub);e.addAction(self.a_remove_dust);e.addSeparator();e.addAction(self.a_resize);e.addAction(self.a_shortcuts);e.addAction(self.a_pressure)
        selection_menu=self.menuBar().addMenu("選択範囲")
        selection_menu.addAction(self.a_selection_clear)
        selection_menu.addSeparator()
        selection_menu.addAction(self.a_selection_transform)
        selection_menu.addAction(self.a_selection_scale)
        selection_menu.addAction(self.a_selection_mesh)
        selection_menu.addSeparator()
        selection_menu.addAction(self.a_transform_rotate_left)
        selection_menu.addAction(self.a_transform_rotate_right)
        selection_menu.addAction(self.a_transform_mesh_grid)
        selection_menu.addSeparator()
        selection_menu.addAction(self.a_transform_commit)
        selection_menu.addAction(self.a_transform_cancel)
        a=self.menuBar().addMenu("アニメーション")
        a.addAction(self.a_prev)
        a.addAction(self.a_next)
        a.addSeparator()
        a.addAction(self.a_tl_previous_key)
        a.addAction(self.a_tl_next_key)
        a.addSeparator()
        a.addAction(self.a_tl_paste_time_remap)
    def build_ui(self):
        self.setDockNestingEnabled(True)
        center=QWidget()
        cv=QVBoxLayout(center)
        cv.setContentsMargins(0,0,0,0)
        self.canvas.setMinimumSize(160, 40)
        cv.addWidget(self.canvas,1)
        bar=QHBoxLayout()
        self.zoom=QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(5,800)
        self.zoom.setValue(100)
        self.zoom_label=QLabel("100%")
        self.rot=QSlider(Qt.Orientation.Horizontal)
        self.rot.setRange(-180,180)
        self.rot_label=QLabel("0°")
        b100=QPushButton("100%表示")
        bfit=QPushButton("全体を表示")
        b0=QPushButton("0°")
        for w in (QLabel("拡大"),self.zoom,self.zoom_label,b100,bfit,QLabel("回転"),self.rot,self.rot_label,b0):
            bar.addWidget(w)
        cv.addLayout(bar)
        self.setCentralWidget(center)

        self.tools.setMinimumWidth(180)
        self.tools.setMaximumWidth(16777215)
        self.tools.setMinimumHeight(0)
        self.palette.setMinimumWidth(220)
        self.palette.setMaximumWidth(16777215)
        self.palette.setMinimumHeight(0)

        self.tools_scroll = QScrollArea()
        self.tools_scroll.setWidgetResizable(True)
        self.tools_scroll.setMinimumSize(0, 0)
        self.tools_scroll.setWidget(self.tools)

        self.palette_scroll = QScrollArea()
        self.palette_scroll.setWidgetResizable(True)
        self.palette_scroll.setMinimumSize(0, 0)
        self.palette_scroll.setWidget(self.palette)

        self.drawing_color_scroll = QScrollArea()
        self.drawing_color_scroll.setWidgetResizable(True)
        self.drawing_color_scroll.setMinimumSize(0, 0)
        self.drawing_color_scroll.setFrameShape(
            QScrollArea.Shape.NoFrame
        )
        self.drawing_color_scroll.setWidget(
            self.tools.drawing_color_box
        )

        self.tools_dock=QDockWidget("ツール", self)
        self.tools_dock.setObjectName("toolsDock")
        self.tools_dock.setWidget(self.tools_scroll)
        self.tools_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tools_dock)

        self.drawing_color_dock=QDockWidget("描画色", self)
        self.drawing_color_dock.setObjectName("drawingColorDock")
        self.drawing_color_dock.setWidget(self.drawing_color_scroll)
        self.drawing_color_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            self.drawing_color_dock,
        )

        self.palette_dock=QDockWidget("使用色", self)
        self.palette_dock.setObjectName("paletteDock")
        self.palette_dock.setWidget(self.palette_scroll)
        self.palette_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.palette_dock)
        self.splitDockWidget(
            self.drawing_color_dock,
            self.palette_dock,
            Qt.Orientation.Vertical,
        )

        self.timeline_dock=QDockWidget("タイムライン", self)
        self.timeline_dock.setObjectName("timelineDock")
        self.timeline.setMaximumHeight(16777215)
        self.timeline_dock.setWidget(self.timeline)
        self.timeline_dock.setMinimumHeight(70)
        self.timeline_dock.setMaximumHeight(16777215)
        self.timeline_dock.setAllowedAreas(
            Qt.DockWidgetArea.TopDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.timeline_dock)
        self.setCorner(
            Qt.Corner.BottomLeftCorner,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        self.setCorner(
            Qt.Corner.BottomRightCorner,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )

        view_menu=self.menuBar().addMenu("表示")
        view_menu.addAction(self.tools_dock.toggleViewAction())
        view_menu.addAction(self.drawing_color_dock.toggleViewAction())
        view_menu.addAction(self.palette_dock.toggleViewAction())
        view_menu.addAction(self.timeline_dock.toggleViewAction())

        self.resizeDocks(
            [self.tools_dock, self.drawing_color_dock, self.palette_dock],
            [220, 240, 260],
            Qt.Orientation.Horizontal,
        )
        self.resizeDocks(
            [self.drawing_color_dock, self.palette_dock],
            [390, 450],
            Qt.Orientation.Vertical,
        )
        self.resizeDocks(
            [self.timeline_dock],
            [210],
            Qt.Orientation.Vertical,
        )
        self.zoom.valueChanged.connect(self.set_zoom)
        b100.clicked.connect(lambda:self.set_zoom(100))
        bfit.clicked.connect(self.fit_canvas)
        self.rot.valueChanged.connect(self.set_rot)
        b0.clicked.connect(lambda:self.rot.setValue(0))

    def connect(self):
        self.tools.silhouette_btn.clicked.connect(
            lambda _checked=False: self.a_silhouette.trigger()
        )
        self.tools.toolChanged.connect(self.canvas.set_tool)
        self.tools.size.valueChanged.connect(self.canvas.set_pen_size)
        self.tools.brush_stabilizer.valueChanged.connect(
            self.canvas.set_brush_stabilizer
        )
        self.tools.size.pressureRequested.connect(self.pressure)
        self.tools.opacity.valueChanged.connect(
            lambda value: setattr(self.canvas, "pen_opacity", value / 100)
        )
        self.tools.colorModeChanged.connect(self.set_color_mode)
        self.tools.colorChanged.connect(self.set_color_value)
        self.tools.main_btn.colorPicked.connect(
            lambda color: self.apply_sampled_color_to_mode("main", color)
        )
        self.tools.sub_btn.colorPicked.connect(
            lambda color: self.apply_sampled_color_to_mode("sub", color)
        )
        self.tools.meshCommitRequested.connect(self.canvas.commit_mesh)
        self.tools.meshCancelRequested.connect(self.canvas.cancel_mesh)
        self.tools.selectionTransformRequested.connect(
            lambda: self.start_wire_transform("free")
        )
        self.tools.selectionScaleRequested.connect(
            lambda: self.start_wire_transform("scale")
        )
        self.tools.selectionMeshRequested.connect(
            lambda: self.start_wire_transform("mesh")
        )
        self.tools.selectionClearRequested.connect(self.canvas.clear_selection)
        self.tools.selectionRotateRequested.connect(
            self.canvas.rotate_selection_transform
        )
        self.tools.transformMeshGridChanged.connect(
            self.canvas.set_transform_mesh_grid
        )
        self.tools.transform_quality.toggled.connect(
            self.canvas.set_transform_quality
        )
        self.tools.transform_line_width.valueChanged.connect(
            self.canvas.set_transform_line_threshold
        )
        self.tools.transform_line_width.sliderPressed.connect(
            self.canvas.begin_transform_line_adjustment
        )
        self.tools.transform_line_width.sliderReleased.connect(
            self.canvas.finish_transform_line_adjustment
        )
        self.canvas.set_transform_quality(
            self.tools.transform_quality.isChecked()
        )
        self.canvas.set_transform_line_threshold(
            self.tools.transform_line_width.value()
        )
        self.tools.selectionCommitRequested.connect(
            self.commit_transform_or_tween
        )
        self.tools.selectionCancelRequested.connect(
            self.cancel_transform_or_tween
        )
        self.tools.flipLayerRequested.connect(self.canvas.flip_active_layer)
        self.tools.swapMainSubRequested.connect(self.swap_main_sub)
        self.tools.removeDustRequested.connect(self.remove_dust_fill_surrounding)
        self.tools.sameImageReplacementRequested.connect(
            self.register_same_image_replacements
        )
        self.tools.mainLineRepaintRequested.connect(self.main_line_repaint)
        self.tools.backgroundColorRequested.connect(self.choose_background_color)

        self.timeline.frameSelected.connect(self.select_timeline_exposure)
        self.timeline.addFrameRequested.connect(
            self.canvas.add_frame
        )
        self.timeline.extendExposureRequested.connect(
            lambda row, key, exposure:
                self.resize_timeline_exposure(
                    row,
                    key,
                    key + max(1, exposure),
                    "right",
                )
        )
        self.timeline.deleteFrameRequested.connect(
            self.delete_timeline_frame
        )
        self.timeline.previousRequested.connect(self.previous_timeline_frame)
        self.timeline.nextRequested.connect(self.next_timeline_frame)
        self.timeline.previousKeyRequested.connect(self.previous_timeline_key)
        self.timeline.nextKeyRequested.connect(self.next_timeline_key)
        self.timeline.durationChanged.connect(self.canvas.set_duration)
        self.timeline.cellMoveRequested.connect(self.move_timeline_cell)
        self.timeline.cellCopyRequested.connect(self.copy_timeline_cell)
        self.timeline.sequenceRecallRequested.connect(self.recall_sequence_number)
        self.timeline.multiCellMoveRequested.connect(
            self.move_timeline_selection
        )
        self.timeline.timelineModeChanged.connect(
            self.set_timeline_mode
        )
        self.timeline.normalizeNumbersRequested.connect(
            self.normalize_timeline_numbers
        )
        self.timeline.blankFrameRequested.connect(
            self.create_blank_timeline_key
        )
        self.timeline.exposureResizeRequested.connect(self.resize_timeline_exposure)
        self.timeline.tweenRequested.connect(
            lambda row, column: self.enable_tween(row, column, "free")
        )
        self.timeline.tweenMeshRequested.connect(
            lambda row, column: self.enable_tween(row, column, "mesh")
        )
        self.timeline.tweenCancelRequested.connect(
            self.cancel_transform_or_tween
        )
        self.timeline.onionPopupToggled.connect(
            self.toggle_onion_settings_popup
        )
        self.timeline.onionChanged.connect(self.set_onion_skin)
        self.timeline.timeRemapPasteRequested.connect(
            self.show_time_remap_paste_dialog
        )
        self.timeline.timeRemapFileDropped.connect(
            self.open_dropped_time_remap
        )
        self.timeline.playRequested.connect(self.play)

        self.canvas.changed.connect(self.refresh_ui)
        self.canvas.selectionChanged.connect(self.refresh_selection)
        self.canvas.selectionCleared.connect(
            self.palette._clear_used_color_selection
        )
        self.canvas.cellChanged.connect(self._on_canvas_cell_changed)
        self.canvas.colorUsed.connect(self.palette.add_color)

        self.timeline.layerSelected.connect(self.layer_selected)
        self.timeline.layerVisibilityChanged.connect(self.layer_visibility_row)
        self.timeline.layerOpacityChanged.connect(self.layer_opacity_row)
        self.timeline.layer_opacity_slider.sliderPressed.connect(
            self.canvas.push_doc_undo
        )
        self.timeline.layerNameChanged.connect(self.layer_name_row)
        self.timeline.layerMoveRequested.connect(self.move_layer_row)
        self.timeline.addLayerRequested.connect(self.add_layer_fast)
        self.timeline.deleteLayerRequested.connect(self.canvas.delete_layer)
        self.timeline.duplicateLayersRequested.connect(self.duplicate_layer_rows)
        self.timeline.mergeLayersRequested.connect(self.merge_layer_rows)
        self.timeline.deleteLayersRequested.connect(self.delete_layer_rows)

        self.canvas.imagesDropped.connect(self.import_dropped_images)
        self.canvas.projectDropped.connect(self.open_dropped_project)
        self.canvas.timeRemapDropped.connect(self.open_dropped_time_remap)
        self.canvas.colorSampled.connect(self.apply_sampled_color)
        self.canvas.status_message.connect(
            lambda message: self.statusBar().showMessage(message, 2500)
        )
        self.canvas.viewChanged.connect(self.sync_canvas_view_controls)
        self.canvas.onionInteractionChanged.connect(
            self.sync_onion_browser_from_canvas
        )
        self.canvas.onionInteractionFinished.connect(
            self.finish_onion_browser_interaction
        )

        self.palette.isolateColorClicked.connect(self.apply_palette_isolate_color)
        self.palette.mainColorRequested.connect(
            lambda color: self.apply_sampled_color_to_mode("main", color)
        )
        self.palette.sourceScreenColorPicked.connect(self.apply_sampled_color)
        self.palette.applyReplacementRequested.connect(
            self.apply_palette_replacements
        )
        self.palette.mergeColorsRequested.connect(self.apply_palette_merge)
        self.palette.deleteColorsRequested.connect(
            self.apply_palette_delete
        )
        self.palette.adjustLineThicknessRequested.connect(
            self.adjust_parent_line_thickness
        )
        self.palette.focusColorRequested.connect(self.focus_used_color)
        self.palette.clearIsolateRequested.connect(
            self.clear_selected_color_filter
        )
        self.palette.maskColorsChanged.connect(self.set_mask_colors)
        self.palette.selectedColorsChanged.connect(self.set_selected_used_colors)
        self.palette.visibleColorsChanged.connect(self.set_visible_colors)

    def _on_canvas_cell_changed(self, frame_index, layer_index):
        self.canvas.sync_numbered_image_from_cell(
            frame_index,
            layer_index,
        )
        # Existing brush cells do not change timeline structure. Rebuilding the
        # entire table here made brush release scale with the number of imported images.
        structure_dirty = bool(
            getattr(self.canvas, "_cell_structure_dirty", True)
        )
        self.canvas._cell_structure_dirty = True
        if structure_dirty:
            self.timeline.update_cell(
                self.canvas.frames, frame_index, layer_index
            )

        # Opaque brush colors are added immediately through colorUsed. Avoid a
        # full all-frame color scan after every normal 100% stroke.
        normal_opaque_brush = (
            self.canvas.effective_tool() == "brush"
            and float(self.canvas.pen_opacity) >= 0.999
            and not self.canvas.is_pseudo_transparent_color(
                self.canvas.paint_source_color()
            )
        )
        if not normal_opaque_brush:
            self.schedule_used_color_refresh()

    def refresh_ui(self):
        self.canvas.coalesce_numbered_images()
        self.timeline.sequence_archive = self.canvas._sequence_archive
        self.timeline.refresh(
            self.canvas.frames,
            self.canvas.current_frame,
            self.canvas.active_layer_index,
            getattr(self.canvas, "tween_pending", None),
        )
        if self._suppress_used_color_refresh_once:
            self._suppress_used_color_refresh_once = False
        else:
            self.schedule_used_color_refresh()

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
    def refresh_selection(self):
        if getattr(self, "_closing", False) or not self.canvas.frames:
            return
        self.canvas.current_frame = max(0, min(self.canvas.current_frame, len(self.canvas.frames) - 1))
        layers = self.canvas.layers
        if not layers:
            return
        self.canvas.active_layer_index = max(0, min(self.canvas.active_layer_index, len(layers) - 1))
        self.timeline.select_current(self.canvas.current_exposure(), self.canvas.active_layer_index)
        self.timeline.layer_list.blockSignals(True)
        self.timeline.layer_list.setCurrentRow(len(layers) - 1 - self.canvas.active_layer_index)
        self.timeline.layer_list.blockSignals(False)
        self.timeline.duration.blockSignals(True)
        layer = self.canvas.frames[self.canvas.current_frame].layers[self.canvas.active_layer_index]
        if not layer.has_content:
            source = self.canvas.resolve_key_frame(self.canvas.current_frame, self.canvas.active_layer_index)
            layer = self.canvas.frames[source].layers[self.canvas.active_layer_index] if source is not None else layer
        self.timeline.duration.setValue(max(1, layer.exposure))
        self.timeline.duration.blockSignals(False)
        self.timeline._sync_layer_opacity_slider(layer.opacity)
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
        except Exception:
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

    def schedule_used_color_refresh(self):
        # 描画直後は colorUsed で新色だけを即時追加し、全画像の色走査は
        # アイドル時にまとめる。投げ縄塗り・バケツ確定時の一拍停止を防ぐ。
        self._used_color_request += 1
        self._used_color_timer.start()

    def _refresh_used_colors_without_delay(self):
        self._used_color_timer.stop()
        self._used_color_request += 1
        self.refresh_used_colors(request=self._used_color_request)

    def undo_with_used_colors(self):
        if not self.canvas.undo_stack:
            return
        self.canvas.undo()
        self._refresh_used_colors_without_delay()

    def redo_with_used_colors(self):
        if not self.canvas.redo_stack:
            return
        self.canvas.redo()
        self._refresh_used_colors_without_delay()

    def _used_color_cache_key(self, image):
        try:
            return (int(image.cacheKey()), image.width(), image.height())
        except Exception:
            return (id(image), image.width(), image.height())

    def _used_color_layer_signature(self, layer_index):
        signature = []
        for frame in self.canvas.frames:
            if layer_index >= len(frame.layers):
                signature.append(None)
                continue
            layer = frame.layers[layer_index]
            signature.append(
                self._used_color_cache_key(layer.image)
                if layer.has_content else None
            )
        return tuple(signature)

    def _apply_used_color_result(self, colors, exceeded=False):
        ordered = [QColor(r, g, b) for r, g, b in colors[:100]]
        self.palette.set_colors(ordered)
        if exceeded:
            self.palette.count_label.setText("100色以上")

    def _extract_used_colors(self, image, limit=101):
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = rgba.width(), rgba.height()
        if width <= 0 or height <= 0:
            return []
        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        array = np.frombuffer(ptr, dtype=np.uint8).reshape((height, rgba.bytesPerLine()))[:, :width * 4]
        pixels = array.reshape((-1, 4))
        pixels = pixels[pixels[:, 3] > 0, :3]
        if pixels.size == 0:
            return []
        # np.unique is much faster than pixelColor() calls from Python.
        packed = (
            (pixels[:, 0].astype(np.uint32) << 16)
            | (pixels[:, 1].astype(np.uint32) << 8)
            | pixels[:, 2].astype(np.uint32)
        )
        unique = np.unique(packed)
        if len(unique) > limit:
            unique = unique[:limit]
        return [
            (
                int((value >> 16) & 255),
                int((value >> 8) & 255),
                int(value & 255),
            )
            for value in unique
        ]

    def refresh_used_colors(
        self,
        request=None,
        progress=None,
        progress_offset=0,
        progress_total=None,
        progress_label="使用色を認識しています",
    ):
        if request is not None and request != self._used_color_request:
            return
        if self.canvas.drawing:
            # Never let an all-frame palette scan interrupt a live brush stroke.
            self._used_color_timer.start(500)
            return
        if not self.canvas.frames:
            self.palette.set_colors([])
            if progress is not None:
                total = progress_total or max(1, progress_offset)
                self.update_progress_counter(
                    progress, progress_offset, total, progress_label
                )
            return

        layer_index = self.canvas.active_layer_index
        layer_signature = self._used_color_layer_signature(layer_index)
        layer_cache_key = (int(layer_index), layer_signature)
        cached_layer = self._used_color_layer_cache.get(layer_cache_key)
        if cached_layer is not None:
            colors, exceeded = cached_layer
            self._apply_used_color_result(colors, exceeded)
            if progress is not None:
                total = progress_total or max(1, progress_offset + len(self.canvas.frames))
                self.update_progress_counter(
                    progress, total, total, "使用色の認識が完了しました"
                )
            return
        all_colors = []
        seen_colors = set()
        exceeded = False
        frame_total = len(self.canvas.frames)
        combined_total = progress_total or max(1, progress_offset + frame_total)

        for scan_index, frame in enumerate(self.canvas.frames, 1):
            if progress is not None:
                self.update_progress_counter(
                    progress,
                    progress_offset + scan_index - 1,
                    combined_total,
                    f"{progress_label}（{scan_index}/{frame_total}コマ）",
                )

            if layer_index < len(frame.layers):
                layer = frame.layers[layer_index]
                if layer.has_content:
                    image = layer.image
                    key = self._used_color_cache_key(image)
                    cached = self._used_color_cache.get(key)
                    if cached is None:
                        colors = self._extract_used_colors(image, 101)
                        cached = (colors[:100], len(colors) > 100)
                        if len(self._used_color_cache) >= 512:
                            self._used_color_cache.pop(
                                next(iter(self._used_color_cache))
                            )
                        self._used_color_cache[key] = cached
                    colors, cell_exceeded = cached
                    for rgb in colors:
                        if rgb not in seen_colors:
                            seen_colors.add(rgb)
                            all_colors.append(rgb)
                    exceeded = (
                        exceeded or cell_exceeded or len(all_colors) > 100
                    )

            # 100色を超えた後も、進捗表示は最後まで進める。
            if len(all_colors) > 100:
                exceeded = True

        colors = all_colors[:100]
        if len(self._used_color_layer_cache) >= 128:
            self._used_color_layer_cache.pop(
                next(iter(self._used_color_layer_cache))
            )
        self._used_color_layer_cache[layer_cache_key] = (
            tuple(colors), bool(exceeded)
        )
        self._apply_used_color_result(colors, exceeded)

        if progress is not None:
            self.update_progress_counter(
                progress,
                progress_offset + frame_total,
                combined_total,
                "使用色の認識が完了しました",
            )

    def apply_palette_isolate_color(self, color):
        self.isolate_selected_color(QColor(color))

    def apply_palette_replacements(self, mapping, operation="色置換"):
        if not mapping:
            if operation == "色置換":
                message = "置換色が登録されていません。"
            elif operation == "色削除":
                message = "削除する使用色が選択されていません。"
            else:
                message = "統合する使用色が選択されていません。"
            self.statusBar().showMessage(message, 2200)
            return False

        packed_mapping = {}
        rgb_mapping = {}
        for source, destination in mapping.items():
            source = tuple(int(value) for value in source[:3])
            destination = tuple(int(value) for value in destination[:3])
            if source == (255, 255, 255) or source == destination:
                continue
            source_value = (source[0] << 16) | (source[1] << 8) | source[2]
            packed_mapping[source_value] = destination
            rgb_mapping[source] = destination

        if not packed_mapping:
            if operation == "色置換":
                message = "置換前と置換後が同じ色です。"
            elif operation == "色削除":
                message = "削除できる使用色が選択されていません。"
            else:
                message = "親以外の使用色を選択してください。"
            self.statusBar().showMessage(message, 2200)
            return False

        # 色ごとに画像全体を再走査せず、24bit RGBを一度だけ検索する。
        source_values = np.array(
            sorted(packed_mapping.keys()), dtype=np.uint32
        )
        destination_values = np.array(
            [packed_mapping[int(value)] for value in source_values],
            dtype=np.uint8,
        )

        layer_index = self.canvas.active_layer_index
        changed_pixels = 0
        changed_cells = 0
        undo_cells = []
        cache_updates = {}
        cache_removals = set()
        frame_items = list(enumerate(self.canvas.frames))
        progress = self.create_progress_counter(
            operation,
            len(frame_items),
            f"{operation}の対象コマを確認しています",
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for progress_index, (frame_index, frame) in enumerate(
                frame_items, 1
            ):
                self.update_progress_counter(
                    progress,
                    progress_index - 1,
                    len(frame_items),
                    f"コマ {frame_index + 1} に{operation}を適用しています",
                )
                if layer_index >= len(frame.layers):
                    continue
                layer = frame.layers[layer_index]
                if not layer.has_content:
                    continue

                old_cache_key = self._used_color_cache_key(layer.image)
                old_cached_colors = self._used_color_cache.get(old_cache_key)
                rgba = layer.image.convertToFormat(
                    QImage.Format.Format_RGBA8888
                )
                width, height = rgba.width(), rgba.height()
                if width <= 0 or height <= 0:
                    continue
                ptr = rgba.bits()
                try:
                    ptr.setsize(rgba.sizeInBytes())
                except AttributeError:
                    pass
                rows = np.frombuffer(
                    ptr, dtype=np.uint8
                ).reshape((height, rgba.bytesPerLine()))
                pixels = rows[:, :width * 4].reshape((height, width, 4))

                packed = (
                    (pixels[:, :, 0].astype(np.uint32) << 16)
                    | (pixels[:, :, 1].astype(np.uint32) << 8)
                    | pixels[:, :, 2].astype(np.uint32)
                )
                indices = np.searchsorted(source_values, packed)
                safe_indices = np.minimum(indices, len(source_values) - 1)
                matches = (
                    (indices < len(source_values))
                    & (source_values[safe_indices] == packed)
                    & (pixels[:, :, 3] != 0)
                )
                cell_count = int(np.count_nonzero(matches))
                if not cell_count:
                    continue

                undo_cells.append(
                    (frame_index, layer.image.copy(), layer.has_content)
                )
                pixels[:, :, :3][matches] = destination_values[
                    safe_indices[matches]
                ]
                layer.image = rgba.convertToFormat(
                    QImage.Format.Format_ARGB32_Premultiplied
                )
                cache_removals.add(old_cache_key)
                if old_cached_colors is not None:
                    old_colors, exceeded = old_cached_colors
                    transformed_colors = []
                    seen_transformed = set()
                    for rgb in old_colors:
                        new_rgb = tuple(rgb_mapping.get(tuple(rgb), tuple(rgb)))
                        if new_rgb in seen_transformed:
                            continue
                        seen_transformed.add(new_rgb)
                        transformed_colors.append(new_rgb)
                    cache_updates[
                        self._used_color_cache_key(layer.image)
                    ] = (transformed_colors[:100], exceeded)
                changed_pixels += cell_count
                changed_cells += 1
                self.update_progress_counter(
                    progress,
                    progress_index,
                    len(frame_items),
                    f"コマ {frame_index + 1} の{operation}が完了しました",
                )
        finally:
            QApplication.restoreOverrideCursor()
            self.close_progress_counter(progress)

        if not changed_pixels:
            self.statusBar().showMessage(
                "選択した使用色は画像内にありませんでした。",
                2400,
            )
            return False

        self.canvas.undo_stack.append(("layer_batch", layer_index, undo_cells))
        self.canvas.undo_stack = self.canvas.undo_stack[-MAX_UNDO:]
        self.canvas.redo_stack.clear()
        for cache_key in cache_removals:
            self._used_color_cache.pop(cache_key, None)
        self._used_color_cache.update(cache_updates)
        self.canvas._color_filter_cache.clear()
        self.canvas._color_index_cache.clear()
        self.canvas._silhouette_cache.clear()

        # コマ構造は変わらないためタイムラインを再構築しない。
        # 画像更新と、キャッシュを利用した使用色一覧の更新だけを行う。
        self.canvas.update()
        self.schedule_used_color_refresh()
        self.statusBar().showMessage(
            f"{changed_cells}セル・{changed_pixels:,}ピクセルへ{operation}を適用しました。",
            3000,
        )
        return True
    def apply_palette_delete(self, selected_rgbs):
        """選択した使用色を #FFFFFF へ統合する。"""
        selected = set()
        try:
            for rgb in selected_rgbs:
                if rgb is None or len(rgb) < 3:
                    continue
                color = tuple(
                    max(0, min(255, int(channel)))
                    for channel in rgb[:3]
                )
                if color != (255, 255, 255):
                    selected.add(color)
        except (TypeError, ValueError):
            selected = set()

        if not selected:
            self.statusBar().showMessage(
                "削除する使用色が選択されていません。",
                2400,
            )
            return

        mapping = {
            color: (255, 255, 255)
            for color in selected
        }
        if self.apply_palette_replacements(
            mapping,
            operation="色削除",
        ):
            # 削除後に存在しない親・子選択を残さない。
            self.palette._clear_used_color_selection()
            self.statusBar().showMessage(
                f"{len(selected)}色を #FFFFFF へ統合しました。",
                3200,
            )

    def apply_palette_merge(self, parent_rgb, selected_rgbs):
        parent = tuple(int(channel) for channel in parent_rgb[:3])
        selected = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in selected_rgbs
            if rgb is not None and len(rgb) >= 3
        }
        if parent == (255, 255, 255):
            self.statusBar().showMessage(
                "背景色は統合先にできません。",
                2400,
            )
            return
        mapping = {
            rgb: parent
            for rgb in selected
            if rgb != parent and rgb != (255, 255, 255)
        }
        if self.apply_palette_replacements(mapping, operation="色統合"):
            self.palette._retain_parent_selection()

    def _layer_indices_from_rows(self, rows):
        count = len(self.canvas.layers)
        result = []
        for row in rows:
            index = count - 1 - int(row)
            if 0 <= index < count and index not in result:
                result.append(index)
        return sorted(result)

    def refresh_used_colors_with_counter(self, title="使用色を更新しています"):
        self._used_color_timer.stop()
        self._used_color_request += 1
        total = max(1, len(self.canvas.frames))
        progress = self.create_progress_counter(
            title,
            total,
            "選択レイヤーの使用色を認識しています",
        )
        try:
            self.refresh_used_colors(
                progress=progress,
                progress_total=total,
                progress_label="選択レイヤーの使用色を認識しています",
            )
        finally:
            self.close_progress_counter(progress)

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

    def add_layer_fast(self):
        """新規空レイヤーでは全コマの使用色走査を行わず即時表示する。"""
        self._used_color_timer.stop()
        self._used_color_request += 1
        self._suppress_used_color_refresh_once = True
        self.canvas.add_layer()
        self.palette.set_colors([])

    def duplicate_layer_rows(self, rows):
        indices = self._layer_indices_from_rows(rows)
        if not indices:
            return
        self.canvas.push_doc_undo()
        for frame in self.canvas.frames:
            for index in sorted(indices, reverse=True):
                copied = frame.layers[index].clone()
                copied.name = f"{copied.name} コピー"
                frame.layers.insert(index + 1, copied)
        self.canvas.active_layer_index = min(
            len(self.canvas.layers) - 1,
            max(indices) + len(indices),
        )
        self.canvas._onion_cache.clear()
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def merge_layer_rows(self, rows):
        indices = self._layer_indices_from_rows(rows)
        if len(indices) < 2:
            self.statusBar().showMessage(
                "結合するレイヤーをShift＋クリックで2つ以上選択してください。",
                2600,
            )
            return
        if indices != list(range(indices[0], indices[-1] + 1)):
            self.statusBar().showMessage(
                "結合できるのは連続しているレイヤーです。",
                2600,
            )
            return

        base_index = indices[0]
        top_index = indices[-1]
        result_name = self.canvas.layers[top_index].name
        frame_count = len(self.canvas.frames)
        self.canvas.push_doc_undo()

        # 元レイヤーを削除する前に、各タイムライン位置で実際に表示される
        # キーフレームを解決する。これにより「ーーー｜」の保持区間が
        # 白紙へ置き換わる問題を防ぐ。
        merged_layers = [
            Layer(
                result_name,
                blank_image(),
                visible=True,
                opacity=1.0,
                has_content=False,
                exposure=1,
            )
            for _ in range(frame_count)
        ]

        previous_signature = None
        active_key_frame = None

        progress = self.create_progress_counter(
            "レイヤーを結合",
            max(1, frame_count),
            "保持コマを解析しています",
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for frame_index in range(frame_count):
                resolved = []
                signature_parts = []

                for layer_index in indices:
                    key_frame = self.canvas.resolve_key_frame(
                        frame_index, layer_index
                    )
                    if key_frame is None:
                        continue
                    source_layer = self.canvas.frames[
                        key_frame
                    ].layers[layer_index]
                    if (
                        not source_layer.has_content
                        or not source_layer.visible
                        or source_layer.opacity <= 0.0
                    ):
                        continue

                    resolved.append(source_layer)
                    signature_parts.append((
                        int(layer_index),
                        int(key_frame),
                        int(source_layer.image.cacheKey()),
                        round(float(source_layer.opacity), 6),
                    ))

                signature = tuple(signature_parts)

                if not resolved:
                    # 白紙区間では直前の露出を延長しない。
                    previous_signature = None
                    active_key_frame = None
                elif (
                    signature == previous_signature
                    and active_key_frame is not None
                ):
                    merged_layers[active_key_frame].exposure += 1
                else:
                    merged_image = blank_image()
                    painter = QPainter(merged_image)
                    painter.setCompositionMode(
                        QPainter.CompositionMode.CompositionMode_SourceOver
                    )
                    for source_layer in resolved:
                        painter.setOpacity(
                            max(
                                0.0,
                                min(1.0, float(source_layer.opacity)),
                            )
                        )
                        # 表示フィルターはデータへ焼き込まず、元画像を結合する。
                        painter.drawImage(0, 0, source_layer.image)
                    painter.end()

                    merged_layers[frame_index] = Layer(
                        result_name,
                        merged_image,
                        visible=True,
                        opacity=1.0,
                        has_content=True,
                        exposure=1,
                    )
                    previous_signature = signature
                    active_key_frame = frame_index

                self.update_progress_counter(
                    progress,
                    frame_index + 1,
                    max(1, frame_count),
                    f"{frame_index + 1} / {frame_count} コマを結合しています",
                )
        finally:
            QApplication.restoreOverrideCursor()
            self.close_progress_counter(progress)

        for frame_index, frame in enumerate(self.canvas.frames):
            for layer_index in reversed(indices):
                frame.layers.pop(layer_index)
            frame.layers.insert(base_index, merged_layers[frame_index])

        self.canvas.active_layer_index = base_index
        self.canvas._onion_cache.clear()
        self.canvas._color_filter_cache.clear()
        self.canvas._color_index_cache.clear()
        self.canvas._silhouette_cache.clear()
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        self.refresh_used_colors_with_counter(
            "結合後の使用色を更新しています"
        )

    def delete_layer_rows(self, rows):
        indices = self._layer_indices_from_rows(rows)
        if not indices:
            return
        if len(indices) >= len(self.canvas.layers):
            QMessageBox.warning(
                self,
                "レイヤー削除",
                "すべてのレイヤーは削除できません。1つ以上残してください。",
            )
            return
        self.canvas.push_doc_undo()
        for frame in self.canvas.frames:
            for index in sorted(indices, reverse=True):
                frame.layers.pop(index)
        self.canvas.active_layer_index = min(
            indices[0],
            len(self.canvas.layers) - 1,
        )
        self.canvas._onion_cache.clear()
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        self.refresh_used_colors_with_counter("削除後の使用色を更新しています")

    def move_layer_row(self, source_rows, destination_row):
        """レイヤー名と全コマのタイムラインデータを同じ順序で移動する。"""
        if not self.canvas.frames:
            return

        count = len(self.canvas.layers)
        try:
            rows = sorted({int(row) for row in source_rows})
        except TypeError:
            rows = [int(source_rows)]

        if (
            not rows
            or any(row < 0 or row >= count for row in rows)
            or rows != list(range(rows[0], rows[-1] + 1))
        ):
            self.refresh_ui()
            return

        block_count = len(rows)
        destination_row = max(
            0,
            min(int(destination_row), count - block_count),
        )
        if destination_row == rows[0]:
            return

        # 現在レイヤーを視覚行番号で記憶し、移動後も同じレイヤーを選択する。
        active_visual_row = count - 1 - self.canvas.active_layer_index
        visual_order = list(range(count))
        moved_order = visual_order[rows[0]:rows[-1] + 1]
        del visual_order[rows[0]:rows[-1] + 1]
        visual_order[destination_row:destination_row] = moved_order
        new_active_visual_row = visual_order.index(active_visual_row)

        self.canvas.push_doc_undo()
        for frame in self.canvas.frames:
            visual_layers = list(reversed(frame.layers))
            moved_layers = visual_layers[rows[0]:rows[-1] + 1]
            del visual_layers[rows[0]:rows[-1] + 1]
            visual_layers[destination_row:destination_row] = moved_layers
            frame.layers = list(reversed(visual_layers))

        self.canvas.active_layer_index = count - 1 - new_active_visual_row
        self.canvas._onion_cache.clear()
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

    def layer_name_row(self, row, name):
        if getattr(self, "_closing", False) or row < 0:
            return
        layers = self.canvas.layers
        index = len(layers) - 1 - row
        if not (0 <= index < len(layers)):
            return
        name = name.strip() or f"Layer {index + 1}"
        self.canvas.push_doc_undo()
        for frame in self.canvas.frames:
            if index < len(frame.layers):
                frame.layers[index].name = name
        self.canvas.changed.emit()

    def layer_selected(self, row):
        if getattr(self, "_closing", False) or row < 0:
            return
        layers = self.canvas.layers
        index = len(layers) - 1 - row
        if 0 <= index < len(layers):
            if self.canvas.active_layer_index == index:
                return
            self.canvas.active_layer_index = index
            self.canvas._onion_cache.clear()
            self.timeline.select_current(self.canvas.current_exposure(), index)
            self._refresh_used_colors_without_delay()
            self.canvas.update()

    def layer_visibility_row(self, row, on):
        if getattr(self, "_closing", False) or row < 0:
            return
        layers = self.canvas.layers
        index = len(layers) - 1 - row
        if 0 <= index < len(layers):
            self.canvas.set_layer_visibility(index, on)

    def layer_opacity_row(self, row, opacity):
        if getattr(self, "_closing", False) or row < 0:
            return
        layers = self.canvas.layers
        index = len(layers) - 1 - row
        if 0 <= index < len(layers):
            self.canvas.set_layer_opacity(index, opacity)
            # 一覧の保持値もその場で更新し、別レイヤー選択時に正しく復元する。
            item = self.timeline.layer_list.item(row)
            if item is not None:
                item.setData(
                    Qt.ItemDataRole.UserRole + 5,
                    float(opacity),
                )

    def set_mask_colors(self, colors):
        normalized = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in colors
            if rgb is not None and len(rgb) >= 3
        }
        self.canvas.mask_color_rgbs = normalized
        self.canvas.mask_all_enabled = self.palette.all_masks_enabled()
        # 古い単色属性は互換用に残すが、複数選択時の判定には使用しない。
        self.canvas.mask_color_rgb = (
            next(iter(normalized)) if len(normalized) == 1 else None
        )
        if self.canvas.mask_all_enabled:
            self.statusBar().showMessage(
                "マスクは「全体」です。すべての領域に描画できます。", 1800
            )
        elif normalized:
            self.statusBar().showMessage(
                f"描画可能なマスクを {len(normalized)}色選択しています。", 1800
            )
        else:
            self.statusBar().showMessage(
                "すべてのマスクがOFFです。描画できません。", 1800
            )

    @staticmethod
    def _image_contains_rgb(image, rgb):
        if image is None or image.isNull():
            return False
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = rgba.width(), rgba.height()
        if width <= 0 or height <= 0:
            return False
        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, rgba.bytesPerLine())
        )
        pixels = rows[:, :width * 4].reshape((height, width, 4))
        target = np.array(
            [int(rgb[0]), int(rgb[1]), int(rgb[2])],
            dtype=np.uint8,
        )
        return bool(
            np.any(
                (pixels[:, :, 3] > 0)
                & np.all(pixels[:, :, :3] == target, axis=2)
            )
        )

    @staticmethod
    def _mask_contours(mask):
        """2値マスクの外周と穴の輪郭を画素境界上のポリゴンとして返す。"""
        mask = np.asarray(mask, dtype=bool)
        if mask.ndim != 2 or not np.any(mask):
            return []

        top = mask.copy()
        top[1:, :] &= ~mask[:-1, :]
        right = mask.copy()
        right[:, :-1] &= ~mask[:, 1:]
        bottom = mask.copy()
        bottom[:-1, :] &= ~mask[1:, :]
        left = mask.copy()
        left[:, 1:] &= ~mask[:, :-1]
        edges = set()
        for y, x in zip(*np.nonzero(top)):
            edges.add(((int(x), int(y)), (int(x) + 1, int(y))))
        for y, x in zip(*np.nonzero(right)):
            edges.add(((int(x) + 1, int(y)), (int(x) + 1, int(y) + 1)))
        for y, x in zip(*np.nonzero(bottom)):
            edges.add(((int(x) + 1, int(y) + 1), (int(x), int(y) + 1)))
        for y, x in zip(*np.nonzero(left)):
            edges.add(((int(x), int(y) + 1), (int(x), int(y))))

        outgoing = {}
        for start, end in edges:
            outgoing.setdefault(start, []).append(end)

        direction_index = {
            (1, 0): 0,
            (0, 1): 1,
            (-1, 0): 2,
            (0, -1): 3,
        }
        unused = set(edges)
        contours = []
        while unused:
            start_edge = min(unused)
            start, current = start_edge
            unused.remove(start_edge)
            contour = [start, current]
            previous = start

            while current != start:
                candidates = [
                    end for end in outgoing.get(current, ())
                    if (current, end) in unused
                ]
                if not candidates:
                    contour = []
                    break
                previous_direction = direction_index[
                    (current[0] - previous[0], current[1] - previous[1])
                ]

                def turn_priority(
                    end,
                    current=current,
                    previous_direction=previous_direction,
                ):
                    next_direction = direction_index[
                        (end[0] - current[0], end[1] - current[1])
                    ]
                    turn = (next_direction - previous_direction) % 4
                    return ({1: 0, 0: 1, 3: 2, 2: 3}[turn], end)

                next_point = min(candidates, key=turn_priority)
                unused.remove((current, next_point))
                previous, current = current, next_point
                contour.append(current)

            if len(contour) >= 4 and contour[-1] == contour[0]:
                contours.append(contour[:-1])

        simplified_contours = []
        for contour in contours:
            simplified = []
            for index, point in enumerate(contour):
                previous = contour[index - 1]
                following = contour[(index + 1) % len(contour)]
                if (
                    (previous[0] == point[0] == following[0])
                    or (previous[1] == point[1] == following[1])
                ):
                    continue
                simplified.append(QPointF(point[0], point[1]))
            if len(simplified) >= 3:
                simplified_contours.append(simplified)
        return simplified_contours

    @classmethod
    def _largest_mask_outer_contour(cls, mask):
        contours = cls._mask_contours(mask)
        return cls._largest_contour(contours)

    @staticmethod
    def _largest_contour(contours):
        if not contours:
            return []

        def area(points):
            values = [(point.x(), point.y()) for point in points]
            return abs(0.5 * sum(
                x1 * y2 - x2 * y1
                for (x1, y1), (x2, y2) in zip(
                    values,
                    values[1:] + values[:1],
                )
            ))

        return max(contours, key=area)

    def focus_used_color(self, rgb):
        """指定色の最初のコマへ移動し、その色全体を選択範囲で囲む。"""
        rgb = tuple(int(value) for value in rgb[:3])
        layer_index = int(self.canvas.active_layer_index)
        target_frame = None

        for frame_index, frame in enumerate(self.canvas.frames):
            if not (0 <= layer_index < len(frame.layers)):
                continue
            layer = frame.layers[layer_index]
            if not layer.has_content:
                continue
            cache_key = self._used_color_cache_key(layer.image)
            cached = self._used_color_cache.get(cache_key)
            if cached is not None:
                cached_colors, cached_exceeded = cached
                if rgb in cached_colors:
                    target_frame = frame_index
                    break
                if not cached_exceeded:
                    continue
            if self._image_contains_rgb(layer.image, rgb):
                target_frame = frame_index
                break

        if target_frame is None:
            QMessageBox.information(
                self,
                "対象に注視",
                "現在のレイヤー内に、この色が使われているコマはありません。",
            )
            return

        visual_row = len(self.canvas.layers) - 1 - layer_index
        self.canvas.select_exposure(target_frame, visual_row)
        self.timeline.select_current(target_frame, layer_index)
        self.set_selected_used_colors({rgb})
        layer = self.canvas.frames[target_frame].layers[layer_index]
        rgba = self.canvas._qimage_rgba_array(layer.image)
        target = np.asarray(rgb, dtype=np.uint8)
        matching = (
            (rgba[:, :, 3] > 0)
            & np.all(rgba[:, :, :3] == target, axis=2)
        )
        contours = self._mask_contours(matching)
        contour = self._largest_contour(contours)
        if contour:
            self.canvas.selection_polygon = contour
            self.canvas.selection_mask_override = matching.copy()
            self.canvas.selection_outline_polygons = contours
            ys, xs = np.nonzero(matching)
            self.canvas.selection_mask_rect = QRectF(
                int(xs.min()),
                int(ys.min()),
                int(xs.max() - xs.min() + 1),
                int(ys.max() - ys.min() + 1),
            )
            self.canvas.lasso = []
            self.canvas.rect_start = None
            self.canvas.rect_end = None
            self.canvas.selectionChanged.emit()
            self.canvas.update()
        self.canvas.setFocus()
        self.statusBar().showMessage(
            f"使用色 {rgb} が最初に現れる {target_frame + 1} コマ目へ移動しました。",
            3200,
        )

    def adjust_parent_line_thickness(self, colors):
        """●ーーーー｜全体を1つの画像として、選択色の線幅を調整する。"""
        if getattr(self.canvas, "tween_pending", None):
            self.statusBar().showMessage(
                "トゥイーン中は線の太さを変更できません。",
                2600,
            )
            return

        line_colors = set()
        try:
            for color in colors:
                if color is not None and len(color) >= 3:
                    line_colors.add(tuple(
                        int(value) for value in color[:3]
                    ))
        except (TypeError, ValueError):
            line_colors = set()

        parent_rgb = tuple(self.palette.parent_rgb or ())
        if not line_colors:
            line_colors = {
                tuple(value)
                for value in self.palette.selected_rgbs
            }

        if not parent_rgb or parent_rgb not in line_colors:
            QMessageBox.information(
                self,
                "太さを調整",
                "親として選択している色で右クリックしてください。",
            )
            return

        if self.canvas.transform_active:
            QMessageBox.warning(
                self,
                "太さを調整",
                "別の変形処理を確定またはキャンセルしてから"
                "実行してください。",
            )
            return

        original_frame = int(self.canvas.current_frame)
        layer_index = int(self.canvas.active_layer_index)
        block = self.canvas.resolve_exposure_block(
            original_frame,
            layer_index,
        )
        if block is None:
            QMessageBox.information(
                self,
                "太さを調整",
                "現在位置には調整できるキーフレームがありません。",
            )
            return

        key_frame, block_end, exposure = block
        key_layer = self.canvas.frames[
            key_frame
        ].layers[layer_index]
        if not key_layer.has_content:
            QMessageBox.information(
                self,
                "太さを調整",
                "現在の露出ブロックに画像がありません。",
            )
            return

        previous_tool = self.tools.active_tool
        previous_quality = (
            self.tools.transform_quality.isChecked()
        )
        previous_threshold = (
            self.tools.transform_line_width.value()
        )
        previous_selected = set(self.palette.selected_rgbs)

        # 保持セルから実行しても、必ず●の画像本体へ移動して処理する。
        # 既存の部分選択は使わず、キーフレーム画像全体を対象にする。
        self.canvas.current_frame = key_frame
        self.canvas.active_layer_index = layer_index
        self.canvas.clear_selection_preserving_used_colors()

        if not self.canvas.auto_select_used_area():
            self.canvas.current_frame = original_frame
            QMessageBox.warning(
                self,
                "太さを調整",
                "キーフレーム全体から描画領域を検出できません。",
            )
            return

        self.tools.select_tool("rect_select")
        self.tools.transform_quality.setChecked(True)
        self.tools.transform_line_width.setValue(
            previous_threshold
        )
        self.start_wire_transform(
            "free",
            line_colors_override=set(line_colors),
        )

        if not self.canvas.transform_active:
            self.canvas.current_frame = original_frame
            self.tools.transform_quality.setChecked(
                previous_quality
            )
            self.tools.select_tool(previous_tool)
            return

        dialog = TransformLineThicknessDialog(
            self.tools.transform_line_width.value(),
            self,
        )
        dialog.slider.valueChanged.connect(
            self.tools.transform_line_width.setValue
        )
        dialog.slider.sliderPressed.connect(
            self.canvas.begin_transform_line_adjustment
        )
        dialog.slider.sliderReleased.connect(
            self.canvas.finish_transform_line_adjustment
        )

        accepted = (
            dialog.exec() == QDialog.DialogCode.Accepted
        )
        if accepted:
            value = dialog.value()
            self.tools.transform_line_width.setValue(value)
            self.canvas.set_transform_line_threshold(value)
            self.canvas.commit_selection_transform(
                all_frames=False
            )
            # 画像は●にだけ保存し、ーーーー｜は同じ露出を参照する。
            key_layer = self.canvas.frames[
                key_frame
            ].layers[layer_index]
            key_layer.exposure = max(1, int(exposure))
            self.canvas.current_frame = min(
                original_frame,
                block_end,
            )
            self.canvas.active_layer_index = layer_index
            self.canvas._onion_cache.clear()
            self.canvas._color_filter_cache.clear()
            self.canvas._color_index_cache.clear()
            self.canvas._silhouette_cache.clear()
            self._used_color_cache.clear()
            self.canvas.cellChanged.emit(
                key_frame,
                layer_index,
            )
            self.canvas.changed.emit()
            self.statusBar().showMessage(
                f"キーフレーム {key_frame + 1}～"
                f"{block_end + 1}（{exposure}コマ）全体へ、"
                f"選択中の{len(line_colors)}色の太さ "
                f"{255 - value} を適用しました。",
                4200,
            )
        else:
            self.canvas.cancel_selection_transform()
            self.tools.transform_line_width.setValue(
                previous_threshold
            )
            self.canvas.current_frame = original_frame
            self.canvas.active_layer_index = layer_index

        self.canvas.clear_selection_preserving_used_colors()
        self.set_selected_used_colors(previous_selected)
        self.tools.transform_quality.setChecked(
            previous_quality
        )
        self.tools.select_tool(previous_tool)
        self.canvas.selectionChanged.emit()
        self.canvas.update()


    def set_selected_used_colors(self, colors):
        normalized = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in colors
            if rgb is not None and len(rgb) >= 3
        }
        self.canvas.set_transform_line_colors(normalized)
        self.tools.set_transform_line_colors_available(bool(normalized))

    def set_visible_colors(self, colors):
        self._pending_visible_colors = set(colors)
        self._visible_color_timer.start()

    def _apply_pending_visible_colors(self):
        colors = self._pending_visible_colors
        self._pending_visible_colors = None
        if colors is None:
            return

        palette_colors = {
            (color.red(), color.green(), color.blue())
            for color in self.palette.colors
        }
        # 全色ONならフィルター処理そのものを行わない。
        effective_colors = (
            None if not palette_colors or palette_colors.issubset(colors)
            else set(colors)
        )
        if effective_colors == self.canvas.visible_color_rgbs:
            return
        self.canvas.visible_color_rgbs = effective_colors
        # 可視色セットはキャッシュキーに含まれるため全消去しない。
        # 以前の表示状態へ戻した時は既存キャッシュを再利用できる。
        self.canvas.update()

    def apply_sampled_color_to_mode(self, mode, color):
        qc = QColor(color)
        if mode == "sub":
            self.canvas.sub_color = qc
        else:
            self.canvas.main_color = qc
        self.canvas.color_mode = mode
        self.tools.set_colors(
            self.canvas.main_color, self.canvas.sub_color, self.canvas.color_mode
        )

    def apply_sampled_color(self, color):
        mode = self.canvas.color_mode
        if mode == "sub":
            self.canvas.sub_color = QColor(color)
        else:
            self.canvas.main_color = QColor(color)
            mode = "main"
            self.canvas.color_mode = "main"
        self.tools.set_colors(self.canvas.main_color, self.canvas.sub_color, mode)
        self.palette.select_matching_color(color)

    @staticmethod
    def _parse_after_effects_time_remap(raw_text):
        text_value = str(raw_text or "").replace("\r", "")
        fps_match = re.search(
            r"Units\s+Per\s+Second\s+([0-9]+(?:\.[0-9]+)?)",
            text_value,
            re.IGNORECASE,
        )
        fps = float(fps_match.group(1)) if fps_match else 24.0
        if fps <= 0.0:
            fps = 24.0

        time_entries = []
        opacity_entries = []
        section = None
        number_pattern = re.compile(
            r"^\s*(-?\d+(?:\.\d+)?)\s+"
            r"(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
        )

        for line in text_value.splitlines():
            lowered = line.strip().lower()
            if lowered.startswith("time remap"):
                section = "time"
                continue
            if (
                lowered.startswith("transform")
                and "opacity" in lowered
            ):
                section = "opacity"
                continue
            if lowered.startswith("end of keyframe data"):
                section = None
                continue

            match = number_pattern.match(line)
            if match is None or section is None:
                continue
            frame = int(round(float(match.group(1))))
            value = float(match.group(2))
            if frame < 0:
                continue
            if section == "time":
                time_entries.append((frame, value))
            else:
                opacity_entries.append((frame, value))

        if not time_entries:
            raise ValueError(
                "Time RemapのFrame／secondsデータが見つかりません。"
            )

        # 同じフレームが複数ある場合は、後から書かれた値を優先。
        time_map = {}
        for frame, value in time_entries:
            time_map[int(frame)] = float(value)
        opacity_map = {}
        for frame, value in opacity_entries:
            opacity_map[int(frame)] = float(value)
        time_entries = sorted(time_map.items())
        opacity_entries = sorted(opacity_map.items())

        all_frames = [frame for frame, _value in time_entries]
        all_frames.extend(
            frame for frame, _value in opacity_entries
        )
        start_frame = max(0, min(all_frames))
        end_frame = max(all_frames)

        time_index = 0
        current_seconds = float(time_entries[0][1])
        opacity_index = 0
        current_opacity = (
            float(opacity_entries[0][1])
            if opacity_entries else 100.0
        )
        states = []

        for frame in range(start_frame, end_frame + 1):
            while (
                time_index + 1 < len(time_entries)
                and time_entries[time_index + 1][0] <= frame
            ):
                time_index += 1
                current_seconds = float(
                    time_entries[time_index][1]
                )
            while (
                opacity_entries
                and opacity_index + 1 < len(opacity_entries)
                and opacity_entries[opacity_index + 1][0] <= frame
            ):
                opacity_index += 1
                current_opacity = float(
                    opacity_entries[opacity_index][1]
                )

            if current_seconds < 0.0 or current_opacity <= 0.0:
                states.append(None)
            else:
                # AEの0秒は連番1番、1/fps秒は連番2番。
                source_index = int(
                    math.floor(current_seconds * fps + 0.5)
                ) + 1
                states.append(max(1, source_index))

        return {
            "format": "Adobe After Effects",
            "fps": fps,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "states": states,
            "blank_label_count": 0,
        }

    @staticmethod
    def _parse_toei_timesheet(raw_text):
        text_value = str(raw_text or "").strip()
        json_start = text_value.find("{")
        if json_start < 0:
            raise ValueError("JSONデータが見つかりません。")
        try:
            payload = json.loads(text_value[json_start:])
        except json.JSONDecodeError as exc:
            raise ValueError(
                "ToeiDigitalTimeSheetのJSONを解析できません。"
                f"\n{exc}"
            ) from exc

        layers = payload.get("layers")
        if not isinstance(layers, list) or not layers:
            raise ValueError("layersデータが見つかりません。")

        selected_layer = None
        for layer in layers:
            frames = (
                layer.get("frames")
                if isinstance(layer, dict) else None
            )
            if isinstance(frames, list) and frames:
                selected_layer = layer
                break
        if selected_layer is None:
            raise ValueError("framesデータが見つかりません。")

        parsed_entries = {}
        for entry in selected_layer.get("frames", []):
            if not isinstance(entry, dict):
                continue
            try:
                frame = int(entry.get("frame"))
            except (TypeError, ValueError):
                continue
            if frame < 0:
                continue

            values = []
            data_items = entry.get("data", [])
            if isinstance(data_items, list):
                for data_item in data_items:
                    if not isinstance(data_item, dict):
                        continue
                    item_values = data_item.get("values", [])
                    if isinstance(item_values, list):
                        values.extend(item_values)
                    elif item_values not in (None, ""):
                        values.append(item_values)

            token = None
            for value in values:
                candidate = str(value).strip()
                if candidate:
                    token = candidate
                    break
            parsed_entries[frame] = token

        if not parsed_entries:
            raise ValueError(
                "有効なToeiDigitalTimeSheetフレームがありません。"
            )

        start_frame = min(parsed_entries)
        end_frame = max(parsed_entries)
        current_state = None
        states = []
        blank_label_count = 0

        for frame in range(start_frame, end_frame + 1):
            if frame in parsed_entries:
                token = parsed_entries[frame]
                if token is None:
                    # 値なしセルは直前セルの状態を保持。
                    pass
                elif re.fullmatch(r"[0-9]+", token):
                    current_state = max(1, int(token))
                else:
                    # 中割トラックラベル／記号セルは空フレーム。
                    current_state = None
                    blank_label_count += 1
            states.append(current_state)

        return {
            "format": "ToeiDigitalTimeSheet",
            "fps": None,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "states": states,
            "blank_label_count": blank_label_count,
        }

    @staticmethod
    def _parse_xdts_timesheet(raw_text):
        text_value = str(raw_text or "").lstrip("\ufeff")
        lines = text_value.splitlines()
        if not lines or lines[0].strip() != "exchangeDigitalTimeSheet Save Data":
            raise ValueError("XDTSの先頭識別文字列が一致しません。")
        try:
            payload = json.loads("\n".join(lines[1:]))
        except json.JSONDecodeError as exc:
            raise ValueError(f"XDTSのJSONを解析できません。\n{exc}") from exc
        if int(payload.get("version", -1)) != 5:
            raise ValueError("対応しているXDTSバージョンは5です。")
        time_tables = payload.get("timeTables") or []
        if not time_tables:
            raise ValueError("XDTSにタイムシート情報がありません。")
        time_table = time_tables[0]
        duration = max(1, int(time_table.get("duration", 1)))
        headers = {}
        for item in time_table.get("timeTableHeaders", []):
            if not isinstance(item, dict):
                continue
            field_id = int(item.get("fieldId", -1))
            headers.setdefault(field_id, list(item.get("names", [])))

        def column_group(field_id, name):
            if field_id == 3:
                return "ACTION"
            if field_id == 5:
                return "CAM"
            normalized = str(name).strip().casefold()
            action_words = ("memo", "action", "act", "camera", "cam", "pan")
            if (
                normalized.startswith(("_", "◆", "-"))
                or normalized in ("ts", "タイムシート")
                or any(word in normalized for word in action_words)
            ):
                return "ACTION"
            return "CELL"

        symbol_labels = {
            "SYMBOL_HYPHEN": "｜",
            "SYMBOL_NULL_CELL": "×",
            "SYMBOL_TICK_1": "○",
            "SYMBOL_TICK_2": "●",
        }
        sheet_columns = []
        parsed_tracks = []
        supported_fields = {0, 3, 5}
        for field_index, field in enumerate(time_table.get("fields", [])):
            if not isinstance(field, dict):
                continue
            field_id = int(field.get("fieldId", -1))
            if field_id not in supported_fields:
                continue
            names = headers.get(field_id, [])
            action_boundary = next(
                (
                    index for index, header_name in enumerate(names[:-1])
                    if "memo" in str(header_name).strip().casefold()
                    or "メモ" in str(header_name).strip()
                ),
                None,
            ) if field_id == 0 else None
            for track in sorted(
                field.get("tracks", []),
                key=lambda item: int(item.get("trackNo", 0)),
            ):
                if not isinstance(track, dict):
                    continue
                track_no = int(track.get("trackNo", 0))
                default_names = {
                    0: "セル欄",
                    3: "アクション",
                    5: "カメラ",
                }
                name = (
                    str(names[track_no]).strip()
                    if 0 <= track_no < len(names)
                    and str(names[track_no]).strip()
                    else f"{default_names[field_id]} {track_no + 1}"
                )
                entries = {
                    int(item.get("frame", 0)): item
                    for item in track.get("frames", [])
                    if isinstance(item, dict)
                }
                states = []
                display_values = []
                current_state = None
                blank_label_count = 0
                for frame in range(duration):
                    item = entries.get(frame)
                    values = []
                    if item is not None:
                        instruction = next(
                            (
                                data for data in item.get("data", [])
                                if isinstance(data, dict)
                                and int(data.get("id", -1)) == 0
                            ),
                            None,
                        )
                        if instruction is not None:
                            raw_values = instruction.get("values", [])
                            values = (
                                list(raw_values)
                                if isinstance(raw_values, list)
                                else [raw_values]
                            )
                    tokens = [str(value).strip() for value in values]
                    token = tokens[0] if tokens else None
                    if token in symbol_labels:
                        display = symbol_labels[token]
                    elif field_id == 3 and tokens:
                        dialogue = [value for value in tokens[:2] if value]
                        display = "：".join(dialogue)
                    elif tokens:
                        display = " / ".join(value for value in tokens if value)
                    else:
                        display = ""

                    if field_id == 0:
                        if token in (None, "SYMBOL_HYPHEN"):
                            if current_state is not None:
                                display = "｜"
                        elif token == "SYMBOL_NULL_CELL":
                            current_state = None
                        elif token in ("SYMBOL_TICK_1", "SYMBOL_TICK_2"):
                            current_state = None
                            blank_label_count += 1
                        elif token.isdecimal():
                            current_state = max(1, int(token))
                            display = str(current_state)
                        else:
                            current_state = None
                            blank_label_count += 1
                        states.append(current_state)
                    display_values.append(display)

                group = column_group(field_id, name)
                if (
                    field_id == 0
                    and action_boundary is not None
                    and track_no <= action_boundary
                ):
                    group = "ACTION"
                column = {
                    "uid": f"{field_id}:{field_index}:{track_no}",
                    "field_id": field_id,
                    "track_no": track_no,
                    "name": name,
                    "group": group,
                    "start_frame": 0,
                    "end_frame": duration - 1,
                    "states": states,
                    "display_values": display_values,
                    "blank_label_count": blank_label_count,
                    "bindable": field_id == 0 and group == "CELL",
                }
                sheet_columns.append(column)
                if field_id == 0:
                    parsed_tracks.append(dict(column))

        if not parsed_tracks:
            raise ValueError("XDTSにセル欄（fieldId 0）がありません。")
        group_order = {"ACTION": 0, "CELL": 1, "CAM": 2}
        sheet_columns.sort(
            key=lambda item: (
                group_order.get(str(item.get("group", "CELL")), 9),
                int(item.get("field_id", 0)),
                int(item.get("track_no", 0)),
            )
        )
        primary = parsed_tracks[0]
        return {
            "format": "XDTS version 5",
            "fps": None,
            "start_frame": 0,
            "end_frame": duration - 1,
            "states": list(primary["states"]),
            "blank_label_count": int(primary["blank_label_count"]),
            "tracks": parsed_tracks,
            "sheet_columns": sheet_columns,
        }

    @classmethod
    def parse_time_remap_text(cls, raw_text):
        text_value = (
            str(raw_text or "")
            .lstrip("\ufeff")
            .strip()
        )
        if not text_value:
            raise ValueError("貼り付けデータが空です。")

        lowered = text_value.lower()
        if text_value.startswith("exchangeDigitalTimeSheet Save Data"):
            return cls._parse_xdts_timesheet(text_value)
        if (
            "toeidigitaltimesheet copy data" in lowered
            or (
                text_value.startswith("{")
                and '"layers"' in text_value
                and '"frames"' in text_value
            )
        ):
            return cls._parse_toei_timesheet(text_value)

        if (
            "adobe after effects" in lowered
            or "time remap" in lowered
        ):
            return cls._parse_after_effects_time_remap(text_value)

        raise ValueError(
            "Adobe After Effects、ToeiDigitalTimeSheet、XDTS形式を"
            "判別できませんでした。"
        )

    def _time_remap_source_bank(self, layer_index):
        stored_bank = getattr(
            self.canvas,
            "_sequence_source_bank",
            [],
        )
        stored_index = int(
            getattr(
                self.canvas,
                "_sequence_source_bank_layer_index",
                -1,
            )
        )
        stored_name = str(
            getattr(
                self.canvas,
                "_sequence_source_bank_layer_name",
                "",
            )
        )

        active_name = ""
        if (
            self.canvas.frames
            and 0 <= self.canvas.current_frame
            < len(self.canvas.frames)
            and 0 <= layer_index
            < len(
                self.canvas.frames[
                    self.canvas.current_frame
                ].layers
            )
        ):
            active_name = str(
                self.canvas.frames[
                    self.canvas.current_frame
                ].layers[layer_index].name
            )

        if stored_bank and (
            stored_index == layer_index
            or (
                stored_name
                and stored_name == active_name
            )
        ):
            return [image.copy() for image in stored_bank]

        numbered_bank = {}
        for frame in self.canvas.frames:
            if layer_index >= len(frame.layers):
                continue
            layer = frame.layers[layer_index]
            number = layer.sequence_number
            if (
                layer.has_content
                and number is not None
                and int(number) >= 1
                and int(number) not in numbered_bank
            ):
                numbered_bank[int(number)] = layer.image.copy()
        if numbered_bank and set(numbered_bank) == set(
            range(1, max(numbered_bank) + 1)
        ):
            return [
                numbered_bank[number]
                for number in range(1, max(numbered_bank) + 1)
            ]

        # 番号情報のない旧プロジェクトでは、選択レイヤー内の
        # 内容キーを左から連番ソースとして採用する。
        bank = []
        for frame in self.canvas.frames:
            if layer_index >= len(frame.layers):
                continue
            layer = frame.layers[layer_index]
            if layer.has_content and not layer.image.isNull():
                bank.append(layer.image.copy())
        return bank

    @staticmethod
    def _copy_layer_display_properties(source, target):
        target.name = str(source.name)
        target.visible = bool(source.visible)
        target.opacity = float(source.opacity)
        target.is_paper = bool(source.is_paper)
        target.alpha_locked = bool(source.alpha_locked)
        target.color_filter_enabled = bool(
            source.color_filter_enabled
        )
        target.color_filter_rgb = (
            tuple(source.color_filter_rgb)
            if source.color_filter_rgb is not None
            else None
        )

    def _apply_time_remap_states_to_layer(
        self,
        states,
        start_frame,
        end_frame,
        layer_index,
        source_bank,
        current_frame,
    ):
        """検証済みのセル番号列を1レイヤーのシートへ展開する。"""
        self.canvas._ensure_frame_count(end_frame + 1)
        template = self.canvas.frames[
            current_frame
        ].layers[layer_index].clone()

        # 対象範囲より前のキーの露出が入り込まないよう切る。
        for prior in range(start_frame - 1, -1, -1):
            prior_layer = self.canvas.frames[prior].layers[layer_index]
            exposure = max(1, int(prior_layer.exposure))
            if prior + exposure > start_frame:
                prior_layer.exposure = max(1, start_frame - prior)
                break
            if prior_layer.has_content:
                break

        # 対象範囲をいったん明示空セルに戻す。
        for frame_index in range(start_frame, end_frame + 1):
            layer = self.canvas.frames[frame_index].layers[layer_index]
            self._copy_layer_display_properties(template, layer)
            layer.image = blank_image()
            layer.has_content = False
            layer.is_blank_key = False
            layer.sequence_number = None
            layer.exposure = 1

        # 同じ絵番号／空フレームが連続する区間を露出へ圧縮。
        run_start = start_frame
        run_state = states[0]
        sentinel = object()
        for offset in range(1, len(states) + 1):
            next_state = states[offset] if offset < len(states) else sentinel
            if offset < len(states) and next_state == run_state:
                continue
            run_end = start_frame + offset - 1
            target = self.canvas.frames[run_start].layers[layer_index]
            self._copy_layer_display_properties(template, target)
            target.exposure = max(1, run_end - run_start + 1)
            if run_state is None:
                target.image = blank_image()
                target.has_content = False
                target.is_blank_key = True
                target.sequence_number = None
            else:
                target.image = source_bank[int(run_state) - 1].copy()
                target.has_content = True
                target.is_blank_key = False
                target.sequence_number = int(run_state)
            if offset < len(states):
                run_start = start_frame + offset
                run_state = next_state

    def apply_time_remap_to_active_layer(self, parsed):
        states = list(parsed.get("states", []))
        start_frame = int(parsed.get("start_frame", 0))
        end_frame = int(parsed.get("end_frame", -1))
        if (
            not states
            or start_frame < 0
            or end_frame < start_frame
            or len(states) != end_frame - start_frame + 1
        ):
            raise ValueError("解析したフレーム範囲が不正です。")

        if not self.canvas.frames:
            raise ValueError("タイムラインがありません。")
        layer_index = int(self.canvas.active_layer_index)
        current_frame = max(
            0,
            min(
                int(self.canvas.current_frame),
                len(self.canvas.frames) - 1,
            ),
        )
        if not (
            0 <= layer_index
            < len(self.canvas.frames[current_frame].layers)
        ):
            raise ValueError("対象レイヤーを選択してください。")

        source_bank = self._time_remap_source_bank(layer_index)
        if not source_bank:
            raise ValueError(
                "選択レイヤーに連番画像がありません。\n"
                "先に画像連番を読み込んでください。"
            )

        referenced = sorted({
            int(state)
            for state in states
            if state is not None
        })
        missing = [
            index
            for index in referenced
            if index < 1 or index > len(source_bank)
        ]
        if missing:
            preview = ", ".join(
                str(value) for value in missing[:12]
            )
            if len(missing) > 12:
                preview += "…"
            raise ValueError(
                f"連番画像は{len(source_bank)}枚ですが、"
                "存在しない絵番号が参照されています。\n"
                f"{preview}"
            )

        format_name = str(
            parsed.get("format", "タイムリマップ")
        )
        blank_count = sum(
            1 for state in states if state is None
        )
        label_blanks = int(
            parsed.get("blank_label_count", 0)
        )
        message = (
            f"形式：{format_name}\n"
            f"反映範囲：{start_frame + 1}～"
            f"{end_frame + 1}フレーム\n"
            f"連番画像：{len(source_bank)}枚\n"
            f"空フレーム：{blank_count}フレーム"
        )
        if label_blanks:
            message += (
                f"\n中割・記号ラベル：{label_blanks}セル"
            )
        message += (
            "\n\n選択レイヤーの対象範囲を置き換えます。"
        )

        answer = QMessageBox.question(
            self,
            "タイムリマップを反映",
            message,
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False

        self.set_timeline_mode("sheet")
        self.canvas.push_doc_undo()
        self._apply_time_remap_states_to_layer(
            states,
            start_frame,
            end_frame,
            layer_index,
            source_bank,
            current_frame,
        )

        self.canvas.current_frame = start_frame
        self.canvas.active_layer_index = layer_index
        self.canvas._onion_cache.clear()
        self.canvas._color_filter_cache.clear()
        self.canvas._color_index_cache.clear()
        self.canvas._silhouette_cache.clear()
        self._used_color_cache.clear()
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        self.schedule_used_color_refresh()

        self.statusBar().showMessage(
            f"{format_name}を{start_frame + 1}～"
            f"{end_frame + 1}フレームへ反映しました。",
            4200,
        )
        return True

    def apply_xdts_layer_bindings(self, parsed):
        columns = {
            str(column.get("uid", "")): column
            for column in parsed.get("sheet_columns", [])
            if isinstance(column, dict)
        }
        bindings = dict(parsed.get("layer_bindings", {}))
        start_frame = int(parsed.get("start_frame", 0))
        end_frame = int(parsed.get("end_frame", -1))
        duration = end_frame - start_frame + 1
        if duration <= 0 or not bindings:
            raise ValueError("読み込むCELLとレイヤーの紐づけがありません。")
        if not self.canvas.frames:
            raise ValueError("タイムラインがありません。")
        current_frame = max(
            0,
            min(int(self.canvas.current_frame), len(self.canvas.frames) - 1),
        )
        current_layers = self.canvas.frames[current_frame].layers
        prepared = []
        for uid, layer_index_value in bindings.items():
            column = columns.get(str(uid))
            if column is None or not bool(column.get("bindable", False)):
                continue
            states = list(column.get("states", []))
            if len(states) != duration:
                raise ValueError(
                    f"CELL「{column.get('name', '')}」のフレーム数が不正です。"
                )
            layer_index = int(layer_index_value)
            if not (0 <= layer_index < len(current_layers)):
                raise ValueError(
                    f"CELL「{column.get('name', '')}」の紐づけ先レイヤーがありません。"
                )
            source_bank = self._time_remap_source_bank(layer_index)
            if not source_bank:
                raise ValueError(
                    f"レイヤー「{current_layers[layer_index].name}」に"
                    "連番画像がありません。"
                )
            referenced = sorted({
                int(state) for state in states if state is not None
            })
            missing = [
                number for number in referenced
                if number < 1 or number > len(source_bank)
            ]
            if missing:
                preview = ", ".join(str(value) for value in missing[:12])
                if len(missing) > 12:
                    preview += "…"
                raise ValueError(
                    f"CELL「{column.get('name', '')}」は存在しない絵番号を"
                    f"参照しています（レイヤー画像 {len(source_bank)}枚）。\n"
                    f"{preview}"
                )
            prepared.append((
                column,
                layer_index,
                states,
                source_bank,
            ))
        if not prepared:
            raise ValueError("読み込めるCELLの紐づけがありません。")

        links = "\n".join(
            f"・{column.get('name', 'CELL')} → "
            f"{current_layers[layer_index].name}"
            for column, layer_index, _states, _bank in prepared
        )
        answer = QMessageBox.question(
            self,
            "XDTSタイムシートを反映",
            f"反映範囲：{start_frame + 1}～{end_frame + 1}フレーム\n"
            f"紐づけ：\n{links}\n\n"
            "紐づけたレイヤーの対象範囲を置き換えます。",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False

        self.set_timeline_mode("sheet")
        self.canvas.push_doc_undo()
        for _column, layer_index, states, source_bank in prepared:
            self._apply_time_remap_states_to_layer(
                states,
                start_frame,
                end_frame,
                layer_index,
                source_bank,
                current_frame,
            )
        self.canvas.current_frame = start_frame
        self.canvas.active_layer_index = prepared[0][1]
        self.canvas._onion_cache.clear()
        self.canvas._color_filter_cache.clear()
        self.canvas._color_index_cache.clear()
        self.canvas._silhouette_cache.clear()
        self._used_color_cache.clear()
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        self.schedule_used_color_refresh()
        self.statusBar().showMessage(
            f"XDTSの{len(prepared)}個のCELLを{start_frame + 1}～"
            f"{end_frame + 1}フレームへ反映しました。",
            4200,
        )
        return True

    def show_time_remap_paste_dialog(self, file_path=None):
        clipboard_text = QApplication.clipboard().text()
        if file_path:
            try:
                clipboard_text = Path(file_path).read_text(
                    encoding="utf-8-sig"
                )
            except (OSError, UnicodeError) as exc:
                QMessageBox.warning(
                    self,
                    "XDTS読み込み",
                    f"読み込めませんでした。\n\n{exc}",
                )
                return False
        dialog = TimeRemapPasteDialog(
            clipboard_text,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        try:
            parsed = dialog.parsed_result()
            if parsed is None:
                raise ValueError("使用するタイムシート行がありません。")
            if (
                str(parsed.get("format", "")).startswith("XDTS")
                and parsed.get("sheet_columns")
            ):
                return self.apply_xdts_layer_bindings(parsed)
            return self.apply_time_remap_to_active_layer(parsed)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "タイムリマップ貼り付け",
                "タイムラインへ反映できませんでした。\n\n"
                f"{exc}",
            )
            return False


    def prepare_color_reduction(self, paths):
        paths = sorted(
            [str(path) for path in paths],
            key=self.canvas._natural_path_key,
        )
        if not paths:
            return None, ""

        first_image, error = self.canvas._read_image_file(
            paths[0]
        )
        if first_image is None:
            return None, (
                f"{Path(paths[0]).name}\n{error}"
            )

        try:
            color_count = (
                self.canvas.opaque_rgb_color_count(first_image)
            )
            alpha_statistics = (
                self.canvas.image_alpha_statistics(first_image)
            )
            background_rgb = (
                self.canvas.detect_opaque_border_background(
                    first_image
                )
            )
            opaque_background = background_rgb is not None
        except Exception as exc:
            return None, (
                "1枚目の色と透明度を確認できませんでした。\n"
                f"{exc}"
            )

        semi_transparent_count = int(
            alpha_statistics["semi_transparent"]
        )

        # 多色、半透明AA、または白背景画像を調整対象にする。
        if (
            color_count < 100
            and semi_transparent_count <= 0
            and not opaque_background
        ):
            return None, ""

        try:
            dialog = ColorReductionDialog(
                first_image,
                color_count,
                semi_transparent_count,
                opaque_background,
                background_rgb,
                self,
            )
            if (
                dialog.exec()
                != QDialog.DialogCode.Accepted
            ):
                return None, "__cancelled__"
            if not dialog.reduction_enabled:
                return None, ""
            palette = dialog.selected_palette()
        except Exception as exc:
            return None, (
                "2値化の準備中に"
                "エラーが発生しました。\n"
                f"{exc}"
            )

        return {
            "palette": palette,
            "extraction_mode": dialog.selected_extraction_mode(),
            "target_colors": int(
                dialog.color_count.value()
            ),
            "source_color_count": int(color_count),
            "alpha_threshold": int(
                dialog.alpha_threshold_255()
            ),
            "semi_transparent_source_pixels": (
                semi_transparent_count
            ),
            "opaque_background": bool(
                dialog.opaque_background
            ),
            "background_rgb": (
                tuple(dialog.background_rgb)
                if dialog.background_rgb is not None
                else None
            ),
            "tone_curve_points": [
                (float(x), float(y))
                for x, y in dialog.tone_curve_points()
            ],
        }, ""

    def prepare_image_import(self, paths):
        # Keep this step light: inspect dimensions only. Color analysis is deferred
        # until after the image is visible and is cached per cell.
        max_width = CANVAS_WIDTH
        max_height = CANVAS_HEIGHT
        for path in paths:
            reader = QImageReader(str(path))
            try:
                reader.setDecideFormatFromContent(True)
            except AttributeError:
                pass
            size = reader.size()
            if size.isValid():
                width, height = size.width(), size.height()
            elif PILImage is not None:
                try:
                    with PILImage.open(path) as pil:
                        width, height = pil.size
                except Exception as exc:
                    return False, f"{Path(path).name}\n画像サイズを取得できませんでした。\n{exc}"
            else:
                image, error = self.canvas._read_image_file(path)
                if image is None:
                    return False, f"{Path(path).name}\n{error}"
                width, height = image.width(), image.height()
            max_width = max(max_width, width)
            max_height = max(max_height, height)
        if max_width > CANVAS_WIDTH or max_height > CANVAS_HEIGHT:
            self.canvas.push_doc_undo()
            self.replace_doc(max_width, max_height, preserve=True)
            self.statusBar().showMessage(
                f"画像に合わせてキャンバスを {max_width} × {max_height}px に拡張しました。", 3500)
        return True, ""

    def import_dropped_image(self, path):
        color_reduction, error = (
            self.prepare_color_reduction([path])
        )
        if error:
            if error != "__cancelled__":
                QMessageBox.warning(
                    self,
                    "画像読み込み",
                    f"{Path(path).name} を読み込めませんでした。"
                    f"\n\n{error}",
                )
            return

        prepared, error = self.prepare_image_import([path])
        if not prepared:
            if error != "__cancelled__":
                QMessageBox.warning(self, "画像読み込み", f"{Path(path).name} を読み込めませんでした。\n\n{error}")
            return
        ok, error = self.canvas.import_image(
            path,
            color_reduction=color_reduction,
        )
        if not ok:
            QMessageBox.warning(self,"画像読み込み",f"{Path(path).name} を読み込めませんでした。\n\n{error}")
        else:
            self.set_timeline_mode("sequence")
            self._used_color_cache.clear()
            self.schedule_used_color_refresh()

    def import_dropped_images(self, paths, layer_name=None):
        color_reduction, error = (
            self.prepare_color_reduction(paths)
        )
        if error:
            if error != "__cancelled__":
                QMessageBox.warning(
                    self,
                    "連番画像読み込み",
                    "画像を読み込めませんでした。"
                    f"\n\n{error}",
                )
            return

        prepared, error = self.prepare_image_import(paths)
        if not prepared:
            if error != "__cancelled__":
                QMessageBox.warning(self, "連番画像読み込み", f"画像を読み込めませんでした。\n\n{error}")
            return
        # 画像配置だけでなく、その直後の使用色認識まで同じカウンターで表示する。
        estimated_frames = max(1, len(self.canvas.frames) + len(paths))
        combined_total = max(1, len(paths) + estimated_frames)
        progress = self.create_progress_counter(
            "連番画像読み込み",
            combined_total,
            "画像を読み込んでいます",
        )
        ok = False
        error = ""
        try:
            ok, error = self.canvas.import_image_sequence(
                paths,
                lambda value, total, label: self.update_progress_counter(
                    progress,
                    value,
                    combined_total,
                    f"{label}（画像 {value}/{total}）",
                ),
                color_reduction=color_reduction,
                layer_name=layer_name,
            )
            if ok:
                self.set_timeline_mode("sequence")
                self._used_color_timer.stop()
                self._used_color_request += 1
                self._used_color_cache.clear()

                actual_frames = len(self.canvas.frames)
                combined_total = max(1, len(paths) + actual_frames)
                progress.setMaximum(combined_total)
                self.update_progress_counter(
                    progress,
                    len(paths),
                    combined_total,
                    "画像配置完了。使用色を認識しています",
                )
                self.refresh_used_colors(
                    progress=progress,
                    progress_offset=len(paths),
                    progress_total=combined_total,
                    progress_label="使用色を認識しています",
                )
        finally:
            self.close_progress_counter(progress)

        if not ok:
            QMessageBox.warning(
                self,
                "連番画像読み込み",
                f"画像を読み込めませんでした。\n\n{error}",
            )
        elif len(paths) > 1:
            reduction_note = ""
            if color_reduction:
                reduction_method = (
                    "元画像へトーンカーブ適用後に2値化"
                    if color_reduction.get("opaque_background")
                    else "半透明を二値化"
                )
                reduction_note = (
                    f"、{reduction_method}して1枚目の共通パレット"
                    f"{int(color_reduction['target_colors'])}色を適用"
                )
            self.statusBar().showMessage(
                f"{len(paths)}枚の画像をタイムラインへ連番配置"
                f"{reduction_note}し、使用色認識まで完了しました。",
                3600,
            )

    def isolate_selected_color(self, selected_color=None):
        if not self.canvas.frames:
            return
        if selected_color is None:
            if self.canvas.color_mode == "transparent":
                QMessageBox.information(
                    self,
                    "特定色だけ表示",
                    "背景色では特定色表示を設定できません。メイン色またはサブ色を選択してください。",
                )
                return
            color = self.canvas.main_color if self.canvas.color_mode == "main" else self.canvas.sub_color
        else:
            color = QColor(selected_color)
        rgb = (color.red(), color.green(), color.blue())
        layer_index = self.canvas.active_layer_index
        changed = False

        for frame in self.canvas.frames:
            if layer_index >= len(frame.layers):
                continue
            layer = frame.layers[layer_index]
            if (not layer.color_filter_enabled) or layer.color_filter_rgb != rgb:
                layer.color_filter_enabled = True
                layer.color_filter_rgb = rgb
                changed = True

        if changed:
            self.canvas._color_filter_cache.clear()
            self.canvas.changed.emit()
            self.canvas.update()
        self.statusBar().showMessage(
            f"選択レイヤーを #{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X} だけ表示しています。元画像は変更されません。",
            3500,
        )

    def clear_selected_color_filter(self):
        if not self.canvas.frames:
            return
        layer_index = self.canvas.active_layer_index
        changed = False
        for frame in self.canvas.frames:
            if layer_index >= len(frame.layers):
                continue
            layer = frame.layers[layer_index]
            if layer.color_filter_enabled or layer.color_filter_rgb is not None:
                layer.color_filter_enabled = False
                layer.color_filter_rgb = None
                changed = True

        if changed:
            self.canvas._color_filter_cache.clear()
            self.canvas.changed.emit()
            self.canvas.update()
            self.statusBar().showMessage("選択レイヤーの特定色表示を解除しました。", 2500)
        else:
            self.statusBar().showMessage("選択レイヤーには特定色表示が設定されていません。", 2500)

    def toggle_draw_background_color(self):
        previous = getattr(self, "_previous_draw_color_mode", "main")
        if self.canvas.color_mode == "transparent":
            self.set_color_mode(previous if previous in ("main", "sub") else "main")
        else:
            self._previous_draw_color_mode = self.canvas.color_mode
            self.set_color_mode("transparent")

    def swap_main_sub(self):
        self.canvas.main_color, self.canvas.sub_color = (
            QColor(self.canvas.sub_color),
            QColor(self.canvas.main_color),
        )
        self.tools.set_colors(
            self.canvas.main_color,
            self.canvas.sub_color,
            self.canvas.color_mode,
            self.canvas.transparent_display_color,
        )
        self.statusBar().showMessage("メインカラーとサブカラーを交換しました。", 1800)

    def toggle_silhouette(self, checked=None):
        if checked is None:
            checked = not self.canvas.silhouette_non_background
        checked = bool(checked)
        self.canvas.silhouette_non_background = checked

        if hasattr(self, "a_silhouette"):
            self.a_silhouette.blockSignals(True)
            self.a_silhouette.setChecked(checked)
            self.a_silhouette.blockSignals(False)

        if hasattr(self.tools, "silhouette_btn"):
            self.tools.silhouette_btn.blockSignals(True)
            self.tools.silhouette_btn.setChecked(checked)
            self.tools.silhouette_btn.blockSignals(False)

        self.canvas._silhouette_cache.clear()
        self.canvas.update()
        self.statusBar().showMessage(
            "背景以外を黒シルエット表示しています。"
            if checked else
            "黒シルエット表示を解除しました。",
            2200,
        )

    def choose_transform_mesh_grid(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("メッシュ格子数")
        form = QFormLayout(dialog)

        cols_spin = QSpinBox(dialog)
        rows_spin = QSpinBox(dialog)
        for spin in (cols_spin, rows_spin):
            spin.setRange(2, 12)
        cols_spin.setValue(
            max(2, int(getattr(self.canvas, "transform_mesh_cols", 4)))
        )
        rows_spin.setValue(
            max(2, int(getattr(self.canvas, "transform_mesh_rows", 4)))
        )
        form.addRow("横の格子数", cols_spin)
        form.addRow("縦の格子数", rows_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        cols = cols_spin.value()
        rows = rows_spin.value()
        for spin, value in (
            (self.tools.transform_mesh_grid_x, cols),
            (self.tools.transform_mesh_grid_y, rows),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self.canvas.set_transform_mesh_grid(cols, rows)
        if not self.canvas.transform_active:
            self.statusBar().showMessage(
                f"次回のメッシュ変形を 横{cols}×縦{rows} 格子に設定しました。",
                2200,
            )

    def _refresh_timeline_tween_marker(self):
        self.timeline.refresh(
            self.canvas.frames,
            self.canvas.current_frame,
            self.canvas.active_layer_index,
            getattr(self.canvas, "tween_pending", None),
        )

    def _close_tween_command_popup(self):
        popup = self._tween_command_popup
        self._tween_command_popup = None
        if popup is not None:
            popup.close()
            popup.deleteLater()

    def _show_tween_command_popup(self, mode=None):
        self._close_tween_command_popup()
        pending = getattr(self.canvas, "tween_pending", None) or {}
        tween_mode = (
            "mesh"
            if (mode or pending.get("transform_mode")) == "mesh"
            else "free"
        )
        popup = TweenCommandPopup(
            tween_mode,
            pending.get(
                "mesh_cols",
                getattr(self.canvas, "transform_mesh_cols", 4),
            ),
            pending.get(
                "mesh_rows",
                getattr(self.canvas, "transform_mesh_rows", 4),
            ),
            self,
            reverse=bool(
                pending.get(
                    "reverse_generation",
                    False,
                )
            ),
        )
        self._tween_command_popup = popup
        popup.commitRequested.connect(self.commit_transform_or_tween)
        popup.cancelRequested.connect(self.cancel_transform_or_tween)
        popup.reverseChanged.connect(
            self.set_tween_reverse_generation
        )
        popup.rotateLeftRequested.connect(
            lambda: self.canvas.rotate_selection_transform(-90.0)
        )
        popup.rotateRightRequested.connect(
            lambda: self.canvas.rotate_selection_transform(90.0)
        )
        popup.destroyed.connect(
            lambda _obj=None, current=popup:
            setattr(
                self,
                "_tween_command_popup",
                None if self._tween_command_popup is current
                else self._tween_command_popup,
            )
        )
        popup.adjustSize()
        anchor = self.timeline_dock.mapToGlobal(
            QPoint(
                max(0, self.timeline_dock.width() - popup.sizeHint().width() - 16),
                24,
            )
        )
        popup.move(anchor)
        popup.show()
        popup.raise_()
        popup.activateWindow()

    def set_tween_reverse_generation(self, enabled):
        """生成方向を切り替え、タイムライン記号を即時更新する。"""
        pending = getattr(
            self.canvas,
            "tween_pending",
            None,
        )
        if not isinstance(pending, dict):
            return

        reverse = bool(enabled)
        pending["reverse_generation"] = reverse
        self._refresh_timeline_tween_marker()
        self.canvas.setFocus()

        transform_mode = (
            "メッシュ変形"
            if pending.get("transform_mode") == "mesh"
            else "自由変形"
        )
        if reverse:
            self.statusBar().showMessage(
                f"◆ {transform_mode}の逆生成："
                "キーフレーム側を変形形状、"
                "ラストコマ側を元の初期形状として生成します。",
                5000,
            )
        else:
            self.statusBar().showMessage(
                f"♦ {transform_mode}の通常生成："
                "キーフレーム側を元の初期形状、"
                "ラストコマ側を変形形状として生成します。",
                5000,
            )

    def enable_tween(self, visual_row, key_column, mode="free"):
        """タイムライン終端の｜を、自由／メッシュトゥイーンの♦へ切り替える。"""
        mode = "mesh" if mode == "mesh" else "free"
        mode_name = "メッシュ変形" if mode == "mesh" else "自由変形"
        if not self.canvas.frames:
            return
        layer_count = len(self.canvas.layers)
        layer_index = layer_count - 1 - int(visual_row)
        key_column = int(key_column)
        if not (
            0 <= layer_index < layer_count
            and 0 <= key_column < len(self.canvas.frames)
        ):
            return
        key_layer = self.canvas.frames[key_column].layers[layer_index]
        exposure = max(1, int(key_layer.exposure))
        if not key_layer.has_content or exposure < 2:
            QMessageBox.warning(
                self,
                "トゥイーン",
                "2コマ以上の表示区間を持つ画像キーフレームで実行してください。",
            )
            return

        answer = QMessageBox.question(
            self,
            "トゥイーンを有効にする",
            (
                f"{exposure}コマの{mode_name}トゥイーンを開始します。\n"
                "確定後は区間内の各コマが画像キーフレームになります。"
            ),
            QMessageBox.StandardButton.Ok
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return

        if self.canvas.transform_active:
            self.cancel_transform_or_tween()

        # 自由変形が画像を一時的に消去する前の文書全体を保存する。
        # Ctrl+Zではこの状態へ戻すため、元画像が消えることはない。
        tween_undo_snapshot = self.canvas.document_snapshot()

        self.canvas.current_frame = key_column
        self.canvas.active_layer_index = layer_index
        self.canvas.tween_pending = None
        self.tools.set_tween_active(False)
        self.palette.setProperty("tweenActive", False)
        self.canvas.selection_polygon = []
        self.canvas.selection_mask_override = None
        self.canvas.selection_outline_polygons = []
        self.canvas.selection_mask_rect = None
        self.canvas.lasso = []
        self.canvas.rect_start = None
        self.canvas.rect_end = None

        # トゥイーンでも現在のTPクオリティ、選択色、線幅を維持する。
        # 「すべてのコマに適用」だけはトゥイーン生成と分離する。
        self.tools.selection_all_frames.setChecked(False)
        self.set_selected_used_colors(set(self.palette.selected_rgbs))
        self.tools.select_tool("rect_select")

        if not self.canvas.auto_select_used_area():
            QMessageBox.warning(
                self,
                "トゥイーン",
                "画像内に変形対象となる描画領域がありません。",
            )
            return

        self.start_wire_transform(mode)
        if not self.canvas.transform_active:
            return

        end_column = key_column + exposure - 1
        self.canvas.tween_pending = {
            "layer_index": layer_index,
            "key_col": key_column,
            "end_col": end_column,
            "exposure": exposure,
            "undo_snapshot": tween_undo_snapshot,
            "quality_active": bool(
                self.canvas.transform_quality_active
            ),
            "line_threshold": int(
                self.canvas.transform_line_threshold
            ),
            "line_colors": tuple(
                self.canvas.transform_tp_line_colors
            ),
            "transform_mode": mode,
            "mesh_cols": int(
                getattr(self.canvas, "transform_mesh_cols", 4)
            ),
            "mesh_rows": int(
                getattr(self.canvas, "transform_mesh_rows", 4)
            ),
            "mesh_reference_points": [
                QPointF(point)
                for point in getattr(
                    self.canvas,
                    "transform_mesh_reference_points",
                    [],
                )
            ],
            "start_points": [
                QPointF(point) for point in self.canvas.transform_points
            ],
            "reverse_generation": False,
        }
        self.tools.set_tween_active(True)
        self.palette.setProperty("tweenActive", True)
        self._refresh_timeline_tween_marker()
        self._show_tween_command_popup(mode)
        self.canvas.setFocus()
        self.statusBar().showMessage(
            f"♦ {mode_name}トゥイーン中です。"
            "変形形状を指定し、ポップアップの"
            "「逆生成」で生成方向を選べます。",
            5000,
        )

    def commit_transform_or_tween(self):
        if getattr(self.canvas, "tween_pending", None):
            self.commit_tween_transform()
        else:
            self.canvas.commit_selection_transform()

    def cancel_transform_or_tween(self):
        had_tween = bool(getattr(self.canvas, "tween_pending", None))
        if self.canvas.transform_active:
            self.canvas.cancel_selection_transform()
        self.canvas.tween_pending = None
        self.tools.set_tween_active(False)
        self.palette.setProperty("tweenActive", False)
        self._close_tween_command_popup()
        if had_tween:
            self._refresh_timeline_tween_marker()
            self.statusBar().showMessage(
                "トゥイーンをキャンセルしました。",
                2200,
            )

    def commit_tween_transform(self):
        """通常生成または逆生成で自由／メッシュ変形形状を補間する。"""
        pending = getattr(self.canvas, "tween_pending", None)
        if not pending or not self.canvas.transform_active:
            return

        layer_index = int(pending["layer_index"])
        key_column = int(pending["key_col"])
        end_column = int(pending["end_col"])
        exposure = max(2, int(pending["exposure"]))
        reverse_generation = bool(
            pending.get(
                "reverse_generation",
                False,
            )
        )
        transform_mode = (
            "mesh"
            if pending.get("transform_mode") == "mesh"
            else "free"
        )
        mode_name = (
            "メッシュ変形" if transform_mode == "mesh" else "自由変形"
        )
        self.canvas.transform_mode = transform_mode
        if transform_mode == "mesh":
            self.canvas.transform_mesh_cols = max(
                2, int(pending.get("mesh_cols", 4))
            )
            self.canvas.transform_mesh_rows = max(
                2, int(pending.get("mesh_rows", 4))
            )
            self.canvas.transform_mesh_grid = (
                self.canvas.transform_mesh_cols
            )
            self.canvas._active_mesh_cols = (
                self.canvas.transform_mesh_cols
            )
            self.canvas._active_mesh_rows = (
                self.canvas.transform_mesh_rows
            )
            reference_points = pending.get(
                "mesh_reference_points",
                [],
            )
            if len(reference_points) == (
                self.canvas.transform_mesh_cols
                * self.canvas.transform_mesh_rows
            ):
                self.canvas.transform_mesh_reference_points = [
                    QPointF(point) for point in reference_points
                ]
            else:
                self.canvas.transform_mesh_reference_points = (
                    self.canvas._regular_mesh_reference_points(
                        self.canvas.transform_source_rect,
                        self.canvas.transform_mesh_cols,
                        self.canvas.transform_mesh_rows,
                    )
                )
        start_points = [
            QPointF(point) for point in pending.get("start_points", [])
        ]
        final_points = [
            QPointF(point) for point in self.canvas.transform_points
        ]
        if (
            len(start_points) != len(final_points)
            or not start_points
            or self.canvas.transform_original_layer is None
            or self.canvas.transform_source is None
        ):
            QMessageBox.warning(
                self,
                "トゥイーン",
                "変形情報を取得できないため、トゥイーンを確定できません。",
            )
            return

        undo_snapshot = pending.get("undo_snapshot")

        # トゥイーン開始後に「クオリティ」をON/OFFした場合も、
        # 確定時の現在設定をそのまま使用する。
        quality_active = bool(
            self.tools.transform_quality.isChecked()
        )
        current_line_threshold = max(
            1,
            min(
                254,
                int(self.tools.transform_line_width.value()),
            ),
        )
        current_line_colors = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in self.palette.selected_rgbs
            if rgb is not None and len(rgb) >= 3
        }

        self.canvas.transform_quality = quality_active
        self.canvas.transform_quality_active = quality_active
        self.canvas.transform_apply_all_frames = False
        self.canvas.transform_line_threshold = current_line_threshold
        self.canvas.selected_used_color_rgbs = set(
            current_line_colors
        )
        self.canvas.transform_tp_line_colors = tuple(
            sorted(current_line_colors)
        )

        # 保存済み設定も現在値へ更新し、確定処理の全経路で同じ値を使う。
        pending["quality_active"] = quality_active
        pending["line_threshold"] = current_line_threshold
        pending["line_colors"] = tuple(
            sorted(current_line_colors)
        )

        self.canvas._invalidate_tp_preview_cache()

        # クオリティ確定では、通常変形への暗黙フォールバックを許可しない。
        # 元画像から色マスクを作り直し、各トゥイーンコマへ確実に適用する。
        if quality_active:
            if not self.canvas._prepare_tp_transform_masks():
                QMessageBox.warning(
                    self,
                    "トゥイーン確定",
                    "TPクオリティ用の色マスクを生成できませんでした。\n"
                    "Pillowが利用できることと、変形対象に色があることを"
                    "確認してください。",
                )
                return

        original = self.canvas.transform_original_layer.copy()
        source = self.canvas.transform_source
        generated_images = []
        total_steps = exposure + 2
        progress = self.create_progress_counter(
            f"{mode_name}トゥイーンを確定",
            total_steps,
            f"{mode_name}トゥイーン画像を準備しています",
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.update_progress_counter(
                progress,
                1,
                total_steps,
                "開始キーフレームを準備しています",
            )
            for index in range(exposure):
                self.update_progress_counter(
                    progress,
                    index + 1,
                    total_steps,
                    f"{mode_name}の補間コマ {index + 1}/{exposure} を生成しています",
                )

                timeline_ratio = (
                    index / float(exposure - 1)
                )
                transform_ratio = (
                    1.0 - timeline_ratio
                    if reverse_generation
                    else timeline_ratio
                )

                # 変形率0は元画像をそのまま使用する。
                # 通常生成では先頭、逆生成ではラストコマが初期形状になる。
                if transform_ratio <= 1e-9:
                    image = original.copy()
                else:
                    self.canvas.transform_points = [
                        QPointF(
                            start.x()
                            + (
                                finish.x()
                                - start.x()
                            ) * transform_ratio,
                            start.y()
                            + (
                                finish.y()
                                - start.y()
                            ) * transform_ratio,
                        )
                        for start, finish in zip(
                            start_points,
                            final_points,
                        )
                    ]
                    self.canvas._invalidate_tp_preview_cache()
                    if quality_active:
                        preview, _target = (
                            self.canvas._tp_mask_preview_image(
                                source,
                                original.width(),
                                original.height(),
                            )
                        )
                    else:
                        preview, _target = (
                            self.canvas._project_transform_source(
                                source,
                                original.width(),
                                original.height(),
                                smooth=False,
                            )
                        )
                    if preview is None:
                        raise RuntimeError(
                            f"{index + 1}コマ目の変形画像を生成できませんでした。"
                        )
                    image = self.canvas._cleared_selection_base(
                        original
                    )
                    painter = QPainter(image)
                    painter.setCompositionMode(
                        QPainter.CompositionMode.CompositionMode_SourceOver
                    )
                    painter.drawImage(0, 0, preview)
                    painter.end()

                generated_images.append(image)

            self.canvas.transform_points = [
                QPointF(point) for point in final_points
            ]
            self.update_progress_counter(
                progress,
                exposure + 1,
                total_steps,
                "生成した画像をタイムラインへ登録しています",
            )
            self.canvas._ensure_frame_count(end_column + 1)
            for offset, image in enumerate(generated_images):
                frame_index = key_column + offset
                layer = self.canvas.frames[frame_index].layers[layer_index]
                layer.image = image
                layer.has_content = True
                layer.exposure = 1

            if undo_snapshot is None:
                raise RuntimeError(
                    "トゥイーン開始前のUndo情報を取得できませんでした。"
                )
            self.canvas.undo_stack.append(("doc", undo_snapshot))
            self.canvas.undo_stack = self.canvas.undo_stack[-MAX_UNDO:]
            self.canvas.redo_stack.clear()

            self.update_progress_counter(
                progress,
                total_steps,
                total_steps,
                "トゥイーンのキーフレーム化が完了しました",
            )
        except Exception as exc:
            # 途中生成に失敗した場合も、開始前の元画像へ戻す。
            if undo_snapshot is not None:
                self.canvas.apply_undo_entry(("doc", undo_snapshot))
            else:
                self.canvas.transform_points = [
                    QPointF(point) for point in final_points
                ]
                self.canvas.cancel_selection_transform()
            self.canvas.tween_pending = None
            self.tools.set_tween_active(False)
            self.palette.setProperty("tweenActive", False)
            self._close_tween_command_popup()
            QMessageBox.warning(
                self,
                "トゥイーン確定エラー",
                str(exc),
            )
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.close_progress_counter(progress)

        self.canvas._reset_selection_transform()
        self.canvas.transform_apply_all_frames = False
        self.canvas.tween_pending = None
        self.canvas.selection_polygon = []
        self.canvas.selection_mask_override = None
        self.canvas.selection_outline_polygons = []
        self.canvas.selection_mask_rect = None
        self.canvas.lasso = []
        self.canvas.rect_start = None
        self.canvas.rect_end = None
        self.canvas.current_frame = (
            key_column
            if reverse_generation
            else end_column
        )
        self.canvas.active_layer_index = layer_index
        self.canvas._onion_cache.clear()
        self.canvas._color_filter_cache.clear()
        self.canvas._color_index_cache.clear()
        self.canvas._silhouette_cache.clear()
        self._close_tween_command_popup()

        # 補間では使用色自体は増えないため、全コマ色走査は省略する。
        self._suppress_used_color_refresh_once = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas._update_selection_clear_overlay()
        self.canvas.update()
        quality_note = (
            "（TPクオリティ適用）"
            if quality_active else ""
        )
        direction_note = (
            "逆生成（◆側が変形／右端が初期）"
            if reverse_generation
            else "通常生成（先頭が初期／♦側が変形）"
        )
        self.statusBar().showMessage(
            f"{exposure}コマの{mode_name}トゥイーンを"
            f"{direction_note}でキーフレーム化しました。"
            f"{quality_note}",
            4200,
        )

    def start_wire_transform(self, mode, line_colors_override=None):
        if not self.canvas.selection_polygon:
            if not self.canvas.auto_select_used_area():
                QMessageBox.warning(
                    self,
                    "変形",
                    "使用色がある領域を検出できないため、変形を開始できません。",
                )
                return
            self.statusBar().showMessage(
                "選択範囲がなかったため、使用色がある領域を自動選択しました。",
                2600,
            )
        self.canvas.transform_mesh_cols = self.tools.transform_mesh_grid_x.value()
        self.canvas.transform_mesh_rows = self.tools.transform_mesh_grid_y.value()
        self.canvas.transform_mesh_grid = self.canvas.transform_mesh_cols

        active_line_colors = (
            {
                tuple(int(channel) for channel in rgb[:3])
                for rgb in line_colors_override
            }
            if line_colors_override is not None
            else {
                tuple(int(channel) for channel in rgb[:3])
                for rgb in self.palette.selected_rgbs
            }
        )
        self.canvas.set_transform_line_colors(active_line_colors)
        self.tools.set_transform_line_colors_available(
            bool(active_line_colors)
        )

        quality_active = self.tools.transform_quality.isChecked()
        self.canvas.set_transform_quality(quality_active)
        self.canvas.set_transform_line_threshold(
            self.tools.transform_line_width.value()
        )
        self.canvas.transform_apply_all_frames = (
            False if quality_active
            else self.tools.selection_all_frames.isChecked()
        )
        if not self.canvas.begin_selection_transform(mode):
            self.canvas.transform_apply_all_frames = False
            QMessageBox.warning(
                self, "変形",
                "選択範囲から変形対象を作成できませんでした。"
            )
            return
        self.canvas.setFocus()
        target_note = (
            "Tp_mask v0.7クオリティ方式／現在のコマへ適用します。"
            if quality_active else
            (
                "すべてのコマへ適用します。"
                if self.canvas.transform_apply_all_frames
                else "現在のコマへ適用します。"
            )
        )
        line_note = (
            f" 選択中の使用色{len(self.canvas.transform_tp_line_colors)}色を実線として処理します。"
            if quality_active and self.canvas.transform_tp_line_colors else ""
        )
        self.statusBar().showMessage(
            "白い点：変形／枠内：移動／黄色い点：回転。"
            f"Enterで確定、Escでキャンセルできます。{target_note}{line_note}",
            5000,
        )

    @staticmethod
    def _qimage_rgba_array(image):
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = rgba.width(), rgba.height()
        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, rgba.bytesPerLine())
        )
        return rows[:, :width * 4].reshape((height, width, 4)).copy()

    def _replacement_mapping_for_aligned_images(self, source_image, target_image):
        """Return source->target RGB mapping when every aligned pixel is consistent."""
        if (
            source_image.isNull()
            or target_image.isNull()
            or source_image.size() != target_image.size()
        ):
            return None
        source = self._qimage_rgba_array(source_image)
        target = self._qimage_rgba_array(target_image)

        # A palette-swapped copy must occupy exactly the same pixels and retain
        # the same alpha values. Color values may differ.
        if not np.array_equal(source[:, :, 3], target[:, :, 3]):
            return None
        opaque = source[:, :, 3] > 0
        if not np.any(opaque):
            return None

        source_rgb = source[:, :, :3][opaque]
        target_rgb = target[:, :, :3][opaque]
        source_packed = (
            source_rgb[:, 0].astype(np.uint32) << 16
            | source_rgb[:, 1].astype(np.uint32) << 8
            | source_rgb[:, 2].astype(np.uint32)
        )
        target_packed = (
            target_rgb[:, 0].astype(np.uint32) << 16
            | target_rgb[:, 1].astype(np.uint32) << 8
            | target_rgb[:, 2].astype(np.uint32)
        )

        mapping = {}
        for packed in np.unique(source_packed):
            mapped_values = np.unique(target_packed[source_packed == packed])
            if len(mapped_values) != 1:
                return None
            mapped = int(mapped_values[0])
            source_value = int(packed)
            mapping[(
                (source_value >> 16) & 255,
                (source_value >> 8) & 255,
                source_value & 255,
            )] = (
                (mapped >> 16) & 255,
                (mapped >> 8) & 255,
                mapped & 255,
            )
        return mapping

    def register_same_image_replacements(self):
        if not self.canvas.frames or not self.canvas.layers:
            return
        frame_index = self.canvas.current_frame
        source_index = self.canvas.active_layer_index
        source_key = self.canvas.resolve_key_frame(frame_index, source_index)
        if source_key is None:
            QMessageBox.warning(
                self,
                "同一画像を置換色に登録",
                "選択レイヤーの現在位置に画像がありません。",
            )
            return
        source_layer = self.canvas.frames[source_key].layers[source_index]
        if not source_layer.has_content:
            QMessageBox.warning(
                self,
                "同一画像を置換色に登録",
                "選択レイヤーの現在位置に画像がありません。",
            )
            return

        candidates = []
        for layer_index in range(source_index + 1, len(self.canvas.layers)):
            key_frame = self.canvas.resolve_key_frame(frame_index, layer_index)
            if key_frame is None:
                continue
            layer = self.canvas.frames[key_frame].layers[layer_index]
            if layer.has_content:
                candidates.append((layer_index, key_frame, layer))

        if not candidates:
            QMessageBox.warning(
                self,
                "同一画像を置換色に登録",
                "同じタイムライン位置の上側レイヤーに、比較できる画像がありません。",
            )
            return

        for _layer_index, _key_frame, target_layer in candidates:
            mapping = self._replacement_mapping_for_aligned_images(
                source_layer.image,
                target_layer.image,
            )
            if mapping is None:
                continue
            registered = self.palette.register_replacements(mapping)
            if registered <= 0:
                QMessageBox.warning(
                    self,
                    "同一画像を置換色に登録",
                    "一致する画像は見つかりましたが、登録できる使用色がありません。",
                )
                return
            self.statusBar().showMessage(
                f"上側レイヤー「{target_layer.name}」から"
                f"{registered}色を置換色に登録しました。",
                4000,
            )
            return

        QMessageBox.warning(
            self,
            "同一画像を置換色に登録",
            "上側レイヤーの画像とピクセルが一致していないため、"
            "置換色には登録できません。\n"
            "画像サイズ、透明度、色領域の形が同じか確認してください。",
        )

    def main_line_repaint(self):
        if not self.canvas.frames:
            return
        answer = QMessageBox.question(
            self,
            "MainLineRepaint",
            "選択した使用色と、それ以外の部分を別レイヤーに分離します。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return

        selected = list(self.palette.selected_rgb_set())
        if not selected:
            QMessageBox.information(
                self, "MainLineRepaint",
                "使用色欄で分離したい色を1色以上選択してください。"
            )
            return
        selected_set = {tuple(map(int, rgb)) for rgb in selected}

        source_index = self.canvas.active_layer_index
        frame_total = max(1, len(self.canvas.frames))
        progress = self.create_progress_counter(
            "MainLineRepaint",
            frame_total * 2,
            "対象色を確認しています",
        )

        found_target = False
        for scan_index, frame in enumerate(self.canvas.frames, 1):
            self.update_progress_counter(
                progress,
                scan_index - 1,
                frame_total * 2,
                f"コマ {scan_index} の対象色を確認しています",
            )
            source = frame.layers[source_index]
            if not source.has_content:
                continue
            image = source.image.convertToFormat(QImage.Format.Format_RGBA8888)
            width, height = image.width(), image.height()
            ptr = image.bits()
            try:
                ptr.setsize(image.sizeInBytes())
            except AttributeError:
                pass
            rows = np.frombuffer(ptr, dtype=np.uint8).reshape((height, image.bytesPerLine()))
            pixels = rows[:, :width*4].reshape((height, width, 4))
            visible = pixels[:, :, 3] > 0
            rgb24 = (
                pixels[:, :, 0].astype(np.uint32) << 16
                | pixels[:, :, 1].astype(np.uint32) << 8
                | pixels[:, :, 2].astype(np.uint32)
            )
            selected24 = np.array(
                [(r << 16) | (g << 8) | b for r, g, b in selected_set],
                dtype=np.uint32,
            )
            match = visible & np.isin(rgb24, selected24)
            if np.any(match):
                found_target = True
                break

        if not found_target:
            self.close_progress_counter(progress)
            QMessageBox.warning(
                self,
                "MainLineRepaint",
                "選択した使用色が選択レイヤー内に見つかりませんでした。\n処理は適用しません。",
            )
            return

        self.canvas.push_doc_undo()

        # Add two layers directly above the source layer:
        # source (hidden) -> Paint -> LINE
        paint_index = source_index + 1
        line_index = source_index + 2

        for frame in self.canvas.frames:
            source_layer = frame.layers[source_index]
            source_layer.visible = False

            paint_layer = Layer(
                "Paint",
                blank_image(),
                visible=True,
                opacity=1.0,
                has_content=False,
            )
            line_layer = Layer(
                "LINE",
                blank_image(),
                visible=True,
                opacity=1.0,
                has_content=False,
            )
            frame.layers.insert(paint_index, paint_layer)
            frame.layers.insert(line_index, line_layer)

        for frame_index, frame in enumerate(self.canvas.frames):
            self.update_progress_counter(
                progress,
                frame_total + frame_index,
                frame_total * 2,
                f"コマ {frame_index + 1} をレイヤー分離しています",
            )
            source = frame.layers[source_index]
            if not source.has_content:
                continue

            rgba = source.image.convertToFormat(QImage.Format.Format_RGBA8888)
            width, height = rgba.width(), rgba.height()
            ptr = rgba.bits()
            try:
                ptr.setsize(rgba.sizeInBytes())
            except AttributeError:
                pass

            rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
                (height, rgba.bytesPerLine())
            )
            pixels = rows[:, :width * 4].reshape((height, width, 4))

            visible = pixels[:, :, 3] > 0
            rgb24 = (
                pixels[:, :, 0].astype(np.uint32) << 16
                | pixels[:, :, 1].astype(np.uint32) << 8
                | pixels[:, :, 2].astype(np.uint32)
            )
            selected24 = np.array(
                [(r << 16) | (g << 8) | b for r, g, b in selected_set],
                dtype=np.uint32,
            )
            line_mask = visible & np.isin(rgb24, selected24)
            line_pixels = np.zeros_like(pixels)
            line_pixels[line_mask] = pixels[line_mask]

            # Paint: remove the line pixels, then fill those gaps using
            # the most frequent adjacent existing color. No intermediate colors.
            paint_pixels = pixels.copy()
            paint_pixels[line_mask, 3] = 0

            pending = [tuple(v) for v in np.argwhere(line_mask)]
            for _pass in range(12):
                if not pending:
                    break
                remaining = []
                for y, x in pending:
                    neighbors = []
                    for nx, ny in (
                        (x - 1, y),
                        (x + 1, y),
                        (x, y - 1),
                        (x, y + 1),
                    ):
                        if (
                            0 <= nx < width
                            and 0 <= ny < height
                            and paint_pixels[ny, nx, 3] > 0
                        ):
                            neighbors.append(
                                (
                                    int(paint_pixels[ny, nx, 0]),
                                    int(paint_pixels[ny, nx, 1]),
                                    int(paint_pixels[ny, nx, 2]),
                                )
                            )
                    if not neighbors:
                        remaining.append((y, x))
                        continue

                    counts = {}
                    for color in neighbors:
                        counts[color] = counts.get(color, 0) + 1
                    fill_color = max(counts, key=counts.get)
                    paint_pixels[y, x, 0] = fill_color[0]
                    paint_pixels[y, x, 1] = fill_color[1]
                    paint_pixels[y, x, 2] = fill_color[2]
                    paint_pixels[y, x, 3] = 255
                pending = remaining

            line_image = QImage(
                line_pixels.data,
                width,
                height,
                line_pixels.strides[0],
                QImage.Format.Format_RGBA8888,
            ).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

            paint_image = QImage(
                paint_pixels.data,
                width,
                height,
                paint_pixels.strides[0],
                QImage.Format.Format_RGBA8888,
            ).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

            frame.layers[line_index].image = line_image
            frame.layers[line_index].has_content = bool(np.any(line_mask))
            frame.layers[line_index].exposure = source.exposure

            frame.layers[paint_index].image = paint_image
            frame.layers[paint_index].has_content = bool(np.any(paint_pixels[:, :, 3] > 0))
            frame.layers[paint_index].exposure = source.exposure

        self.close_progress_counter(progress)
        self.canvas.active_layer_index = line_index
        self._used_color_cache.clear()
        self.canvas._color_filter_cache.clear()
        self.canvas._silhouette_cache.clear()
        self._suppress_used_color_refresh_once = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()

        self.statusBar().showMessage(
            "LINE／Paintを作成し、元レイヤーを非表示にしました。",
            3800,
        )

    def create_progress_counter(self, title, total, label=None):
        total = max(1, int(total))
        dialog = QProgressDialog(
            label or title,
            "",
            0,
            total,
            self,
        )
        dialog.setWindowTitle(title)
        dialog.setCancelButton(None)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumWidth(330)
        dialog.setValue(0)
        dialog.show()
        QApplication.processEvents()
        return dialog

    def update_progress_counter(self, dialog, value, total, label):
        if dialog is None:
            return
        value = max(0, min(int(total), int(value)))
        dialog.setLabelText(f"{label}\n{value} / {int(total)}")
        dialog.setValue(value)
        QApplication.processEvents()

    def close_progress_counter(self, dialog):
        if dialog is None:
            return
        dialog.setValue(dialog.maximum())
        dialog.close()
        dialog.deleteLater()
        QApplication.processEvents()

    def set_onion_all_layers(self, enabled):
        self.canvas.onion_all_layers = bool(enabled)
        self.canvas._onion_cache.clear()
        self.canvas.update()

    def set_onion_skin(self, enabled):
        self.canvas.onion_skin = bool(enabled)
        self.canvas._onion_cache.clear()
        self.canvas.update()

    def toggle_onion_settings_popup(self, checked):
        checked = bool(checked)
        if checked:
            self.show_onion_settings()
        else:
            browser = self._onion_settings_browser
            if browser is not None:
                browser.close()
        self.canvas.update()

    def show_onion_settings(self):
        browser = self._onion_settings_browser
        if browser is not None:
            browser.show()
            browser.raise_()
            return

        browser = OnionSkinSettingsBrowser(
            self.canvas.onion_previous_count,
            self.canvas.onion_next_count,
            self.canvas.onion_previous_opacity,
            self.canvas.onion_next_opacity,
            self.canvas.onion_previous_color,
            self.canvas.onion_next_color,
            self.canvas.onion_previous_color_enabled,
            self.canvas.onion_next_color_enabled,
            self.canvas.onion_selected_colors_only,
            self.canvas.onion_previous_shift_x,
            self.canvas.onion_previous_shift_y,
            self.canvas.onion_previous_rotation,
            self.canvas.onion_next_shift_x,
            self.canvas.onion_next_shift_y,
            self.canvas.onion_next_rotation,
            self.canvas.onion_previous_scale,
            self.canvas.onion_next_scale,
            self.canvas.onion_previous_levels,
            self.canvas.onion_next_levels,
            self.canvas.onion_center_percent,
            self.canvas.rotation,
            self,
        )
        self._onion_settings_browser = browser
        browser.settingsChanged.connect(
            self.apply_onion_browser_settings
        )
        browser.shiftEditRequested.connect(
            self.canvas.begin_onion_shift_interaction
        )
        browser.canvasPositionEditRequested.connect(
            self.canvas.begin_onion_canvas_position_interaction
        )
        browser.canvasRotationRequested.connect(
            self.canvas.set_onion_canvas_view_rotation
        )
        browser.centerCanvasRequested.connect(
            self.center_canvas_between_onion_shifts
        )
        browser.destroyed.connect(
            self._onion_settings_browser_destroyed
        )

        self.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            browser,
        )
        if (
            hasattr(self, "palette_dock")
            and self.palette_dock is not None
        ):
            self.tabifyDockWidget(self.palette_dock, browser)
        browser.show()
        browser.raise_()

    def _onion_settings_browser_destroyed(self, *_args):
        self._onion_settings_browser = None
        self.canvas.cancel_onion_interaction(restore=False)
        if self.timeline.onion_settings.isChecked():
            self.timeline.onion_settings.blockSignals(True)
            self.timeline.onion_settings.setChecked(False)
            self.timeline.onion_settings.blockSignals(False)

    def apply_onion_browser_settings(self):
        browser = self._onion_settings_browser
        if browser is None:
            return

        self.canvas.onion_previous_count = (
            browser.previous_count.value()
        )
        self.canvas.onion_next_count = (
            browser.next_count.value()
        )
        self.canvas.onion_previous_opacity = (
            browser.previous_opacity.value() / 100.0
        )
        self.canvas.onion_next_opacity = (
            browser.next_opacity.value() / 100.0
        )
        self.canvas.onion_previous_levels = (
            browser.previous_levels()
        )
        self.canvas.onion_next_levels = (
            browser.next_levels()
        )
        self.canvas.onion_previous_color = QColor(
            browser.previous_color
        )
        self.canvas.onion_next_color = QColor(
            browser.next_color
        )
        self.canvas.onion_previous_color_enabled = (
            browser.previous_color_enabled.isChecked()
        )
        self.canvas.onion_next_color_enabled = (
            browser.next_color_enabled.isChecked()
        )
        self.canvas.onion_selected_colors_only = (
            browser.selected_colors_only.isChecked()
        )
        self.canvas.onion_previous_shift_x = float(
            browser.previous_shift_x.value()
        )
        self.canvas.onion_previous_shift_y = float(
            browser.previous_shift_y.value()
        )
        self.canvas.onion_previous_rotation = float(
            browser.previous_rotation.value()
        )
        self.canvas.onion_next_shift_x = float(
            browser.next_shift_x.value()
        )
        self.canvas.onion_next_shift_y = float(
            browser.next_shift_y.value()
        )
        self.canvas.onion_next_rotation = float(
            browser.next_rotation.value()
        )
        self.canvas.onion_previous_scale = float(
            browser.previous_scale.value()
        )
        self.canvas.onion_next_scale = float(
            browser.next_scale.value()
        )
        self.canvas.onion_center_percent = float(
            browser.center_percent_value.value()
        )

        self.canvas._onion_cache.clear()
        self.canvas.update()

    def sync_onion_browser_from_canvas(self):
        browser = self._onion_settings_browser
        if browser is None:
            return
        browser.set_transform_values(
            self.canvas.onion_previous_shift_x,
            self.canvas.onion_previous_shift_y,
            self.canvas.onion_previous_rotation,
            self.canvas.onion_previous_scale,
            self.canvas.onion_next_shift_x,
            self.canvas.onion_next_shift_y,
            self.canvas.onion_next_rotation,
            self.canvas.onion_next_scale,
        )
        browser.set_canvas_rotation_value(
            self.canvas.rotation
        )

    def finish_onion_browser_interaction(self):
        browser = self._onion_settings_browser
        if browser is not None:
            browser.clear_interaction_buttons()

    def center_canvas_between_onion_shifts(self, percent):
        """前後間のTU/TB変形を現在キャンバスの基準へ取り込む。"""
        self.apply_onion_browser_settings()
        percent = max(0.0, min(100.0, float(percent)))
        self.canvas.onion_center_percent = percent
        ratio = percent / 100.0

        previous_x = float(self.canvas.onion_previous_shift_x)
        previous_y = float(self.canvas.onion_previous_shift_y)
        next_x = float(self.canvas.onion_next_shift_x)
        next_y = float(self.canvas.onion_next_shift_y)
        previous_rotation = float(
            self.canvas.onion_previous_rotation
        )
        next_rotation = float(
            self.canvas.onion_next_rotation
        )
        previous_scale = max(
            0.01,
            float(self.canvas.onion_previous_scale) / 100.0,
        )
        next_scale = max(
            0.01,
            float(self.canvas.onion_next_scale) / 100.0,
        )

        target_x = previous_x + (next_x - previous_x) * ratio
        target_y = previous_y + (next_y - previous_y) * ratio

        # 回転は最短方向、TU/TB拡大率は撮影倍率として線形補間する。
        rotation_delta = (
            (next_rotation - previous_rotation + 180.0)
            % 360.0
        ) - 180.0
        target_rotation = self.canvas._normalized_angle(
            previous_rotation + rotation_delta * ratio
        )
        target_scale = max(
            0.01,
            previous_scale + (next_scale - previous_scale) * ratio,
        )

        # 表示用デジタルズームself.canvas.zoomは変更しない。
        digital_zoom = max(0.01, float(self.canvas.zoom))
        current_tu_tb = max(
            0.0001,
            float(self.canvas.onion_tu_tb_scale),
        )
        old_view_rotation = float(self.canvas.rotation)
        view_angle = math.radians(old_view_rotation)
        local_display_x = (
            target_x * digital_zoom * current_tu_tb
        )
        local_display_y = (
            target_y * digital_zoom * current_tu_tb
        )
        display_x = (
            local_display_x * math.cos(view_angle)
            - local_display_y * math.sin(view_angle)
        )
        display_y = (
            local_display_x * math.sin(view_angle)
            + local_display_y * math.cos(view_angle)
        )

        self.canvas.pan += QPointF(display_x, display_y)
        self.canvas.rotation = self.canvas._normalized_angle(
            old_view_rotation + target_rotation
        )
        self.canvas.onion_tu_tb_scale = max(
            0.05,
            min(20.0, current_tu_tb * target_scale),
        )

        # H_target^-1 × H_eachで相対位置・回転・倍率を再取得。
        angle = math.radians(-target_rotation)
        cos_angle = math.cos(angle)
        sin_angle = math.sin(angle)

        def relative_shift(source_x, source_y):
            dx = float(source_x) - target_x
            dy = float(source_y) - target_y
            return QPointF(
                (dx * cos_angle - dy * sin_angle)
                / target_scale,
                (dx * sin_angle + dy * cos_angle)
                / target_scale,
            )

        previous_relative = relative_shift(
            previous_x,
            previous_y,
        )
        next_relative = relative_shift(next_x, next_y)

        # 選択した中央％の回転を新しい0°基準として取得する。
        # キャンバス表示角度は0°へ戻し、前後の角度は
        # 取得した中央回転との差分として再設定する。
        absorbed_view_rotation = float(self.canvas.rotation)
        previous_screen_relative = self.canvas._rotated_vector(
            previous_relative.x(),
            previous_relative.y(),
            absorbed_view_rotation,
        )
        next_screen_relative = self.canvas._rotated_vector(
            next_relative.x(),
            next_relative.y(),
            absorbed_view_rotation,
        )

        self.canvas.onion_previous_shift_x = (
            previous_screen_relative.x()
        )
        self.canvas.onion_previous_shift_y = (
            previous_screen_relative.y()
        )
        self.canvas.onion_next_shift_x = (
            next_screen_relative.x()
        )
        self.canvas.onion_next_shift_y = (
            next_screen_relative.y()
        )
        self.canvas.onion_previous_rotation = (
            self.canvas._normalized_angle(
                previous_rotation - target_rotation
            )
        )
        self.canvas.onion_next_rotation = (
            self.canvas._normalized_angle(
                next_rotation - target_rotation
            )
        )
        self.canvas.rotation = 0.0
        self.canvas.onion_previous_scale = max(
            1.0,
            min(199.0, previous_scale / target_scale * 100.0),
        )
        self.canvas.onion_next_scale = max(
            1.0,
            min(199.0, next_scale / target_scale * 100.0),
        )

        self.sync_onion_browser_from_canvas()
        self.canvas.viewChanged.emit(
            float(self.canvas.zoom),
            float(self.canvas.rotation),
        )
        self.canvas.update()
        self.statusBar().showMessage(
            f"前後の位置・回転・TU/TB拡大率間の{percent:g}%を"
            "キャンバス基準へ移しました。"
            f"（中央回転 {target_rotation:.1f}°を基準として取得／"
            f"撮影倍率 {target_scale * 100.0:.1f}%／"
            "キャンバス回転 0°／デジタルズーム変更なし）",
            4200,
        )

    def remove_dust_fill_surrounding(self):
        """選択レイヤー内でゴミ取り／塗り抜けを実行する。"""
        mode = self.tools.dust_mode.currentText()
        max_area = max(
            1,
            min(100, int(self.tools.dust_size.value())),
        )
        selected_only = (
            self.tools.dust_selected_only.isChecked()
        )
        selected_colors = self.canvas.selected_used_colors()
        selected_colors.discard((255,255,255))

        if selected_only and not selected_colors:
            self.statusBar().showMessage(
                "使用色パネルで対象色を選択してください。",
                2800,
            )
            return

        layer_index = int(
            self.canvas.active_layer_index
        )
        requested_frames = (
            range(len(self.canvas.frames))
            if self.tools.dust_all_frames.isChecked()
            else (self.canvas.current_frame,)
        )

        # 保持区間は同じキーフレーム画像を指すため1回だけ処理する。
        frame_indices = []
        seen_keys = set()
        for frame_index in requested_frames:
            key_frame = self.canvas.resolve_key_frame(
                int(frame_index),
                layer_index,
            )
            if key_frame is None:
                continue
            if key_frame in seen_keys:
                continue
            seen_keys.add(key_frame)
            frame_indices.append(key_frame)

        if not frame_indices:
            self.statusBar().showMessage(
                "選択レイヤーに処理できるキーフレームがありません。",
                2800,
            )
            return

        def component_list(mask):
            """4方向接続成分を返す。各画素は1度だけ走査する。"""
            pending = np.asarray(
                mask,
                dtype=bool,
            ).copy()
            candidate_y, candidate_x = np.nonzero(
                pending
            )
            height, width = pending.shape

            for y0, x0 in zip(
                candidate_y,
                candidate_x,
            ):
                y0 = int(y0)
                x0 = int(x0)
                if not pending[y0, x0]:
                    continue

                stack = [(x0, y0)]
                pending[y0, x0] = False
                component = []
                touches_edge = False

                while stack:
                    x, y = stack.pop()
                    component.append((y, x))
                    if (
                        x == 0 or y == 0
                        or x == width - 1
                        or y == height - 1
                    ):
                        touches_edge = True

                    if x > 0 and pending[y, x - 1]:
                        pending[y, x - 1] = False
                        stack.append((x - 1, y))
                    if x + 1 < width and pending[y, x + 1]:
                        pending[y, x + 1] = False
                        stack.append((x + 1, y))
                    if y > 0 and pending[y - 1, x]:
                        pending[y - 1, x] = False
                        stack.append((x, y - 1))
                    if y + 1 < height and pending[y + 1, x]:
                        pending[y + 1, x] = False
                        stack.append((x, y + 1))

                yield component, touches_edge

        changed_cells = 0
        changed_pixels = 0
        changed_frame_indices = []
        undo_cells = []

        progress = self.create_progress_counter(
            mode,
            max(1, len(frame_indices)),
            f"{mode}対象を解析しています",
        )
        QApplication.setOverrideCursor(
            Qt.CursorShape.WaitCursor
        )
        try:
            for counter, frame_index in enumerate(
                frame_indices,
                1,
            ):
                self.update_progress_counter(
                    progress,
                    counter - 1,
                    max(1, len(frame_indices)),
                    f"コマ {frame_index + 1} を解析しています",
                )
                frame = self.canvas.frames[frame_index]
                if layer_index >= len(frame.layers):
                    continue
                layer = frame.layers[layer_index]
                if not layer.has_content:
                    continue

                rgba = layer.image.convertToFormat(
                    QImage.Format.Format_RGBA8888
                )
                width = rgba.width()
                height = rgba.height()
                if width <= 0 or height <= 0:
                    continue

                ptr = rgba.bits()
                try:
                    ptr.setsize(rgba.sizeInBytes())
                except AttributeError:
                    pass
                rows = np.frombuffer(
                    ptr,
                    dtype=np.uint8,
                ).reshape(
                    (height, rgba.bytesPerLine())
                )
                pixels = rows[:,:width * 4].reshape(
                    (height, width, 4)
                )
                rgb = pixels[:,:,:3]
                pseudo_white = (
                    (pixels[:,:,3] == 0)
                    | np.all(rgb == 255, axis=2)
                )
                cell_changed = 0

                if mode == "塗り抜け":
                    # 外周につながらない小さな白領域を周囲色で埋める。
                    starts = []
                    starts.extend(
                        (int(x), 0)
                        for x in np.flatnonzero(
                            pseudo_white[0,:]
                        )
                    )
                    if height > 1:
                        starts.extend(
                            (int(x), height - 1)
                            for x in np.flatnonzero(
                                pseudo_white[-1,:]
                            )
                        )
                    if width > 1:
                        starts.extend(
                            (0, int(y))
                            for y in np.flatnonzero(
                                pseudo_white[:,0]
                            )
                        )
                        starts.extend(
                            (width - 1, int(y))
                            for y in np.flatnonzero(
                                pseudo_white[:,-1]
                            )
                        )

                    outside = (
                        self.canvas._scanline_connected_region(
                            pseudo_white,
                            starts,
                        )
                        if starts
                        else np.zeros_like(pseudo_white)
                    )
                    holes = pseudo_white & ~outside

                    for component, _touches_edge in component_list(
                        holes
                    ):
                        area = len(component)
                        if area == 0 or area > max_area:
                            continue

                        border_positions = set()
                        for y, x in component:
                            for ny in range(
                                max(0, y - 1),
                                min(height, y + 2),
                            ):
                                for nx in range(
                                    max(0, x - 1),
                                    min(width, x + 2),
                                ):
                                    if (
                                        (ny != y or nx != x)
                                        and not pseudo_white[ny, nx]
                                    ):
                                        border_positions.add(
                                            (ny, nx)
                                        )

                        if not border_positions:
                            continue

                        border_yx = np.asarray(
                            tuple(border_positions),
                            dtype=np.int32,
                        )
                        border_rgb = pixels[
                            border_yx[:,0],
                            border_yx[:,1],
                            :3,
                        ]

                        if selected_only:
                            keep = np.zeros(
                                len(border_rgb),
                                dtype=bool,
                            )
                            for selected_color in selected_colors:
                                keep |= np.all(
                                    border_rgb
                                    == np.asarray(
                                        selected_color,
                                        dtype=np.uint8,
                                    ),
                                    axis=1,
                                )
                            border_rgb = border_rgb[keep]
                            if border_rgb.size == 0:
                                continue

                        packed = (
                            (
                                border_rgb[:,0].astype(
                                    np.uint32
                                ) << 16
                            )
                            | (
                                border_rgb[:,1].astype(
                                    np.uint32
                                ) << 8
                            )
                            | border_rgb[:,2].astype(
                                np.uint32
                            )
                        )
                        values, counts = np.unique(
                            packed,
                            return_counts=True,
                        )
                        selected = int(
                            values[int(np.argmax(counts))]
                        )
                        fill = np.asarray(
                            [
                                (selected >> 16) & 255,
                                (selected >> 8) & 255,
                                selected & 255,
                            ],
                            dtype=np.uint8,
                        )
                        coordinates = np.asarray(
                            component,
                            dtype=np.int32,
                        )
                        cy = coordinates[:,0]
                        cx = coordinates[:,1]
                        pixels[cy,cx,:3] = fill
                        pixels[cy,cx,3] = 255
                        cell_changed += area

                else:
                    # ゴミ取り：小さな色点を白へ変更する。
                    removal_mask = np.zeros(
                        (height, width),
                        dtype=bool,
                    )

                    if selected_only:
                        # 選択色ごとに独立判定する。
                        # 青1pxが黒に接していても青成分は1pxとして消える。
                        for selected_color in selected_colors:
                            color_mask = (
                                ~pseudo_white
                                & np.all(
                                    rgb
                                    == np.asarray(
                                        selected_color,
                                        dtype=np.uint8,
                                    ),
                                    axis=2,
                                )
                            )
                            for component, _edge in component_list(
                                color_mask
                            ):
                                if (
                                    0 < len(component)
                                    <= max_area
                                ):
                                    coordinates = np.asarray(
                                        component,
                                        dtype=np.int32,
                                    )
                                    removal_mask[
                                        coordinates[:,0],
                                        coordinates[:,1],
                                    ] = True
                    else:
                        # 未選択時は、白背景から独立した小さな色塊を削除。
                        foreground = ~pseudo_white
                        for component, touches_edge in component_list(
                            foreground
                        ):
                            if (
                                not touches_edge
                                and 0 < len(component)
                                <= max_area
                            ):
                                coordinates = np.asarray(
                                    component,
                                    dtype=np.int32,
                                )
                                removal_mask[
                                    coordinates[:,0],
                                    coordinates[:,1],
                                ] = True

                    cell_changed = int(
                        np.count_nonzero(removal_mask)
                    )
                    if cell_changed:
                        pixels[removal_mask,:3] = 255
                        pixels[removal_mask,3] = 255

                if cell_changed:
                    undo_cells.append(
                        (
                            frame_index,
                            layer.image.copy(),
                            bool(layer.has_content),
                        )
                    )
                    layer.image = rgba.convertToFormat(
                        QImage.Format.Format_ARGB32_Premultiplied
                    )
                    # ○化や未使用化はせず、キーフレーム構造を維持する。
                    layer.has_content = True
                    changed_cells += 1
                    changed_pixels += cell_changed
                    changed_frame_indices.append(
                        int(frame_index)
                    )

                self.update_progress_counter(
                    progress,
                    counter,
                    max(1, len(frame_indices)),
                    f"コマ {frame_index + 1} の処理が完了しました",
                )
        finally:
            QApplication.restoreOverrideCursor()
            self.close_progress_counter(progress)

        if not changed_pixels:
            target_text = (
                "選択色の" if selected_only else ""
            )
            self.statusBar().showMessage(
                f"指定サイズ以内の{target_text}{mode}対象は"
                "見つかりませんでした。",
                3000,
            )
            return

        self.canvas.undo_stack.append(
            ("layer_batch", layer_index, undo_cells)
        )
        self.canvas.undo_stack = (
            self.canvas.undo_stack[-MAX_UNDO:]
        )
        self.canvas.redo_stack.clear()
        # 表示専用キャッシュも含めてすべて破棄する。
        # ゴミ取り／塗り抜けは画像オブジェクトを差し替えるため、
        # ここを更新しないと表示／非表示切替まで旧画像が残る場合がある。
        self.canvas._color_filter_cache.clear()
        self.canvas._color_index_cache.clear()
        self.canvas._pseudo_transparency_cache.clear()
        self.canvas._silhouette_cache.clear()
        self.canvas._onion_cache.clear()
        self.canvas._playback_frame_cache.clear()

        # 変更したセルを通知し、現在表示中の保持コマも即時再描画する。
        for changed_frame_index in changed_frame_indices:
            self.canvas.cellChanged.emit(
                int(changed_frame_index),
                int(layer_index),
            )

        self.canvas.changed.emit()
        self.canvas.update()
        self.canvas.repaint()
        QApplication.processEvents()

        self.refresh_used_colors_with_counter(
            f"{mode}後の使用色を更新しています"
        )

        # 使用色の再走査後にも再描画を予約し、進捗ダイアログの
        # 閉鎖後に旧表示へ戻ることを防ぐ。
        self.canvas.update()
        self.statusBar().showMessage(
            f"選択レイヤーの{changed_cells}コマで"
            f"{changed_pixels:,}ピクセルへ{mode}を適用しました。",
            3600,
        )

    def set_color_value(self,mode,color):
        setattr(self.canvas,mode+"_color",QColor(color));self.set_color_mode(mode)
    def choose_background_color(self):
        color = QColorDialog.getColor(
            self.canvas.transparent_display_color,
            self,
            "背景色の表示色を選択",
        )
        if not color.isValid():
            return
        self.canvas.transparent_display_color = QColor(color)
        self.canvas.checker_light = QColor(color)
        self.canvas.checker_dark = QColor(color)
        self.canvas.color_mode = "transparent"
        self.tools.set_colors(
            self.canvas.main_color,
            self.canvas.sub_color,
            "transparent",
            self.canvas.transparent_display_color,
        )
        self.canvas.update()

    def choose_color(self,mode):
        base=self.canvas.main_color if mode=="main" else self.canvas.sub_color;c=QColorDialog.getColor(base,self,"色を選択")
        if c.isValid():setattr(self.canvas,mode+"_color",c);self.set_color_mode(mode)
    def set_color_mode(self,mode):self.canvas.color_mode=mode;self.tools.set_colors(self.canvas.main_color,self.canvas.sub_color,mode,self.canvas.transparent_display_color)
    def sync_canvas_view_controls(self, zoom_value, rotation_value):
        zoom_percent = max(self.zoom.minimum(), min(self.zoom.maximum(), int(round(float(zoom_value) * 100))))
        rotation_degrees = max(-180, min(180, int(round(float(rotation_value)))))
        self.zoom.blockSignals(True); self.zoom.setValue(zoom_percent); self.zoom.blockSignals(False)
        self.rot.blockSignals(True); self.rot.setValue(rotation_degrees); self.rot.blockSignals(False)
        self.zoom_label.setText(f"{zoom_percent}%")
        self.rot_label.setText(f"{rotation_degrees}°")

    def set_zoom(self,v):
        self.canvas.zoom=v/100
        self.sync_canvas_view_controls(self.canvas.zoom, self.canvas.rotation)
        self.canvas.update()
    def set_rot(self,v):
        self.canvas.rotation=float(v)
        self.sync_canvas_view_controls(self.canvas.zoom, self.canvas.rotation)
        self.canvas.update()
    def fit_canvas(self):
        w,h=workspace_size();availw=max(100,self.canvas.width()-40);availh=max(100,self.canvas.height()-40);z=min(availw/w,availh/h);self.canvas.zoom=z;self.canvas.pan=QPointF((self.canvas.width()-w*z)/2,(self.canvas.height()-h*z)/2);self.zoom.blockSignals(True);self.zoom.setValue(int(z*100));self.zoom.blockSignals(False);self.zoom_label.setText(f"{z*100:.0f}%");self.canvas.update()

    def new_doc(self):
        d=CanvasSizeDialog(CANVAS_WIDTH,CANVAS_HEIGHT,"新規作成",self)
        if d.exec():
            self.replace_doc(*d.values())
            self.current_project_path = None
            self.update_project_title()
    def update_project_title(self):
        if self.current_project_path:
            name = Path(self.current_project_path).name
            self.setWindowTitle(f"{APP_DISPLAY_NAME} — {name}")
        else:
            self.setWindowTitle(f"{APP_DISPLAY_NAME} — 新規プロジェクト")

    def image_color_hex(self, color):
        return QColor(color).name(QColor.NameFormat.HexRgb).upper()

    def save_project(self):
        if (
            not self.current_project_path
            or Path(self.current_project_path).suffix.lower() != ".pman"
        ):
            return self.save_project_as()
        return self.write_project(self.current_project_path)

    def save_project_as(self):
        initial = (
            str(Path(self.current_project_path).with_suffix(".pman"))
            if self.current_project_path
            else "untitled.pman"
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "名前を付けて保存",
            initial,
            "PaintMaskAnimator Project (*.pman)",
        )
        if not path:
            return False
        if not path.lower().endswith(".pman"):
            path = str(Path(path).with_suffix(".pman"))
        if self.write_project(path):
            self.current_project_path = Path(path)
            self.update_project_title()
            return True
        return False

    def write_project(self, path):
        project_path = Path(path)
        project_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = project_path.with_suffix(project_path.suffix + ".tmp")

        metadata = {
            "format": "PaintMaskAnimatorProject",
            "format_version": 1,
            "application_version": 100,
            "canvas": {
                "width": int(CANVAS_WIDTH),
                "height": int(CANVAS_HEIGHT),
            },
            "fps": int(self.timeline.fps.value()),
            "timeline_mode": str(self.canvas.timeline_mode),
            "current_frame": int(self.canvas.current_frame),
            "active_layer_index": int(self.canvas.active_layer_index),
            "colors": {
                "main": self.image_color_hex(self.canvas.main_color),
                "sub": self.image_color_hex(self.canvas.sub_color),
                "mode": self.canvas.color_mode,
                "background": self.image_color_hex(
                    self.canvas.transparent_display_color
                ),
            },
            "display": {
                "silhouette_non_background": bool(
                    self.canvas.silhouette_non_background
                ),
                "onion_skin": bool(self.canvas.onion_skin),
                "onion_previous_count": int(self.canvas.onion_previous_count),
                "onion_next_count": int(self.canvas.onion_next_count),
                "onion_previous_opacity": float(self.canvas.onion_previous_opacity),
                "onion_next_opacity": float(self.canvas.onion_next_opacity),
                "onion_previous_levels": [
                    int(value)
                    for value in self.canvas.onion_previous_levels
                ],
                "onion_next_levels": [
                    int(value)
                    for value in self.canvas.onion_next_levels
                ],
                "onion_center_percent": float(
                    self.canvas.onion_center_percent
                ),
                "onion_previous_color": self.image_color_hex(
                    self.canvas.onion_previous_color
                ),
                "onion_next_color": self.image_color_hex(
                    self.canvas.onion_next_color
                ),
                "onion_previous_color_enabled": bool(
                    self.canvas.onion_previous_color_enabled
                ),
                "onion_next_color_enabled": bool(
                    self.canvas.onion_next_color_enabled
                ),
                "onion_selected_colors_only": bool(
                    self.canvas.onion_selected_colors_only
                ),
                "onion_previous_shift_x": float(
                    self.canvas.onion_previous_shift_x
                ),
                "onion_previous_shift_y": float(
                    self.canvas.onion_previous_shift_y
                ),
                "onion_previous_rotation": float(
                    self.canvas.onion_previous_rotation
                ),
                "onion_previous_scale": float(
                    self.canvas.onion_previous_scale
                ),
                "onion_next_shift_x": float(
                    self.canvas.onion_next_shift_x
                ),
                "onion_next_shift_y": float(
                    self.canvas.onion_next_shift_y
                ),
                "onion_next_rotation": float(
                    self.canvas.onion_next_rotation
                ),
                "onion_next_scale": float(
                    self.canvas.onion_next_scale
                ),
                "onion_tu_tb_scale": float(
                    self.canvas.onion_tu_tb_scale
                ),
            },
            "pressure": {
                "enabled": bool(self.canvas.pressure_enabled),
                "minimum": float(self.canvas.pressure_min),
                "maximum": float(self.canvas.pressure_max),
                "curve": float(self.canvas.pressure_curve),
                "points": getattr(self.canvas, "pressure_curve_points", [[0.0,0.0],[1.0,1.0]]),
            },
            "frames": [],
        }

        try:
            with tempfile.TemporaryDirectory() as temp_directory:
                temp_root = Path(temp_directory)
                for frame_index, frame in enumerate(self.canvas.frames):
                    frame_data = {
                        "duration": int(frame.duration),
                        "layers": [],
                    }
                    for layer_index, layer in enumerate(frame.layers):
                        image_name = (
                            f"layers/layer_{layer_index:04d}/"
                            f"frame_{frame_index:06d}.png"
                        )
                        local_image = temp_root / image_name
                        local_image.parent.mkdir(parents=True, exist_ok=True)
                        if not layer.image.save(str(local_image), "PNG"):
                            raise RuntimeError(
                                f"画像を書き出せませんでした: {image_name}"
                            )

                        frame_data["layers"].append(
                            {
                                "name": layer.name,
                                "image": image_name,
                                "visible": bool(layer.visible),
                                "opacity": float(layer.opacity),
                                "is_paper": bool(layer.is_paper),
                                "has_content": bool(layer.has_content),
                                "exposure": int(layer.exposure),
                                "is_blank_key": bool(
                                    getattr(layer, "is_blank_key", False)
                                ),
                                "sequence_number": layer.sequence_number,
                                "sequence_only": bool(layer.sequence_only),
                                "color_filter_enabled": bool(
                                    layer.color_filter_enabled
                                ),
                                "color_filter_rgb": (
                                    list(layer.color_filter_rgb)
                                    if layer.color_filter_rgb is not None
                                    else None
                                ),
                            }
                        )
                    metadata["frames"].append(frame_data)

                with zipfile.ZipFile(
                    temporary_path,
                    "w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                ) as archive:
                    archive.writestr(
                        "project.json",
                        json.dumps(
                            metadata,
                            ensure_ascii=False,
                            indent=2,
                        ).encode("utf-8"),
                    )
                    for local_file in temp_root.rglob("*.png"):
                        archive.write(
                            local_file,
                            local_file.relative_to(temp_root).as_posix(),
                        )

            temporary_path.replace(project_path)
            self.current_project_path = project_path
            self.update_project_title()
            self.statusBar().showMessage(
                f"プロジェクトを保存しました：{project_path.name}",
                3000,
            )
            return True
        except Exception as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except Exception:
                pass
            QMessageBox.critical(
                self,
                "プロジェクト保存エラー",
                f"保存できませんでした。\n\n{error}",
            )
            return False

    def open_project_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "プロジェクトを開く",
            "",
            "PaintMaskAnimator Project (*.pman);;"
            "旧Oekaki Animation Project (*.oap)",
        )
        if path:
            self.open_project(path)

    def confirm_save_before_dropped_project(self):
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle("プロジェクトを開く")
        dialog.setText(
            "現在のキャンバスを保存してから、"
            "ドロップしたプロジェクトを開きますか？"
        )
        save_button = dialog.addButton(
            "保存する", QMessageBox.ButtonRole.AcceptRole
        )
        discard_button = dialog.addButton(
            "保存しない", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = dialog.addButton(
            "キャンセル", QMessageBox.ButtonRole.RejectRole
        )
        dialog.setDefaultButton(save_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is cancel_button or clicked is None:
            return False
        if clicked is save_button:
            return bool(self.save_project())
        return clicked is discard_button

    def open_dropped_project(self, path):
        project_path = Path(path)
        if project_path.suffix.lower() != ".pman":
            return False
        if not self.confirm_save_before_dropped_project():
            return False
        return self.open_project(project_path)

    def open_dropped_time_remap(self, path):
        remap_path = Path(path)
        if remap_path.suffix.lower() not in (".xdts", ".xtds"):
            return False
        return bool(self.show_time_remap_paste_dialog(str(remap_path)))

    def dragEnterEvent(self, event):
        urls = (
            event.mimeData().urls()
            if event.mimeData().hasUrls() else []
        )
        if any(
            Path(url.toLocalFile()).suffix.lower()
            in (".pman", ".xdts", ".xtds")
            for url in urls
        ):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        project_paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if Path(url.toLocalFile()).suffix.lower() == ".pman"
        ] if event.mimeData().hasUrls() else []
        remap_paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if Path(url.toLocalFile()).suffix.lower()
            in (".xdts", ".xtds")
        ] if event.mimeData().hasUrls() else []
        if project_paths:
            self.open_dropped_project(project_paths[0])
            event.acceptProposedAction()
            return
        if remap_paths:
            self.open_dropped_time_remap(remap_paths[0])
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def open_project(self, path):
        global CANVAS_WIDTH, CANVAS_HEIGHT
        project_path = Path(path)
        try:
            with zipfile.ZipFile(project_path, "r") as archive:
                archive_entries = archive.infolist()
                if len(archive_entries) > MAX_PROJECT_LAYER_CELLS + 1:
                    raise ValueError("プロジェクト内のファイル数が多すぎます。")
                if any(info.flag_bits & 0x1 for info in archive_entries):
                    raise ValueError("暗号化されたプロジェクトには対応していません。")
                if sum(int(info.file_size) for info in archive_entries) > MAX_PROJECT_ARCHIVE_BYTES:
                    raise ValueError("プロジェクトの展開後サイズが大きすぎます。")
                entry_names = [info.filename for info in archive_entries]
                if len(entry_names) != len(set(entry_names)):
                    raise ValueError("プロジェクト内に重複したファイル名があります。")
                try:
                    metadata_info = archive.getinfo("project.json")
                except KeyError as error:
                    raise ValueError("project.jsonがありません。") from error
                if metadata_info.file_size > MAX_PROJECT_METADATA_BYTES:
                    raise ValueError("プロジェクト情報が大きすぎます。")
                metadata = json.loads(
                    archive.read("project.json").decode("utf-8")
                )
                if metadata.get("format") not in (
                    "PaintMaskAnimatorProject",
                    "OekakiAnimationProject",
                ):
                    raise ValueError("対応していないプロジェクト形式です。")

                canvas_data = metadata.get("canvas", {})
                width = int(canvas_data.get("width", 1280))
                height = int(canvas_data.get("height", 720))
                if not (1 <= width <= 16384 and 1 <= height <= 16384):
                    raise ValueError("キャンバスサイズが不正です。")

                loaded_frames = []
                frame_entries = metadata.get("frames", [])
                if not isinstance(frame_entries, list) or not frame_entries:
                    raise ValueError("フレーム情報がありません。")
                if len(frame_entries) > MAX_PROJECT_FRAMES:
                    raise ValueError("フレーム数が上限を超えています。")

                expected_layer_count = None
                layer_cell_count = 0
                for frame_data in frame_entries:
                    if not isinstance(frame_data, dict):
                        raise ValueError("フレーム情報が不正です。")
                    layer_entries = frame_data.get("layers", [])
                    if not isinstance(layer_entries, list) or not layer_entries:
                        raise ValueError("レイヤー情報がありません。")
                    if len(layer_entries) > MAX_PROJECT_LAYERS:
                        raise ValueError("レイヤー数が上限を超えています。")
                    if expected_layer_count is None:
                        expected_layer_count = len(layer_entries)
                    elif len(layer_entries) != expected_layer_count:
                        raise ValueError("フレームごとのレイヤー数が一致していません。")
                    layer_cell_count += len(layer_entries)
                if layer_cell_count > MAX_PROJECT_LAYER_CELLS:
                    raise ValueError("プロジェクトのセル数が上限を超えています。")
                if width * height * layer_cell_count > MAX_PROJECT_DECODED_PIXELS:
                    raise ValueError("プロジェクトの展開後画像サイズが大きすぎます。")

                for frame_data in frame_entries:
                    loaded_layers = []
                    for layer_data in frame_data.get("layers", []):
                        if not isinstance(layer_data, dict):
                            raise ValueError("レイヤー情報が不正です。")
                        image_path = layer_data.get("image")
                        if not image_path:
                            raise ValueError("レイヤー画像の参照がありません。")
                        image_path = str(image_path)
                        normalized_path = PurePosixPath(image_path)
                        if (
                            normalized_path.is_absolute()
                            or ".." in normalized_path.parts
                            or normalized_path.suffix.lower() != ".png"
                        ):
                            raise ValueError("レイヤー画像の参照パスが不正です。")
                        try:
                            image_info = archive.getinfo(image_path)
                        except KeyError as error:
                            raise ValueError(
                                f"レイヤー画像がありません: {image_path}"
                            ) from error
                        if image_info.file_size > MAX_PROJECT_IMAGE_BYTES:
                            raise ValueError(
                                f"レイヤー画像が大きすぎます: {image_path}"
                            )
                        image_bytes = archive.read(image_path)
                        image = QImage.fromData(image_bytes, "PNG")
                        if image.isNull():
                            raise ValueError(
                                f"レイヤー画像を復元できません: {image_path}"
                            )
                        if image.width() != width or image.height() != height:
                            raise ValueError(
                                f"レイヤー画像のサイズが不正です: {image_path}"
                            )
                        image = image.convertToFormat(
                            QImage.Format.Format_ARGB32_Premultiplied
                        )

                        filter_rgb = layer_data.get("color_filter_rgb")
                        exposure = int(layer_data.get("exposure", 1))
                        if not (1 <= exposure <= MAX_PROJECT_FRAMES):
                            raise ValueError("レイヤーの露出フレーム数が不正です。")
                        sequence_number = layer_data.get("sequence_number")
                        if sequence_number is not None:
                            sequence_number = int(sequence_number)
                            if not (1 <= sequence_number <= MAX_PROJECT_FRAMES):
                                raise ValueError("絵番号が範囲外です。")
                        opacity = float(layer_data.get("opacity", 1.0))
                        if not math.isfinite(opacity):
                            raise ValueError("レイヤー不透明度が不正です。")
                        loaded_layers.append(
                            Layer(
                                str(layer_data.get("name", "Layer")),
                                image,
                                bool(layer_data.get("visible", True)),
                                max(0.0, min(1.0, opacity)),
                                bool(layer_data.get("is_paper", False)),
                                bool(layer_data.get("has_content", False)),
                                False,  # legacy alpha lock is intentionally ignored
                                exposure,
                                bool(
                                    layer_data.get(
                                        "color_filter_enabled",
                                        False,
                                    )
                                ),
                                (
                                    tuple(int(v) for v in filter_rgb[:3])
                                    if filter_rgb is not None
                                    else None
                                ),
                                bool(
                                    layer_data.get(
                                        "is_blank_key",
                                        (
                                            not bool(
                                                layer_data.get(
                                                    "has_content",
                                                    False,
                                                )
                                            )
                                            and max(
                                                1,
                                                int(
                                                    layer_data.get(
                                                        "exposure",
                                                        1,
                                                    )
                                                ),
                                            ) > 1
                                        ),
                                    )
                                ),
                                (
                                    sequence_number
                                ),
                                bool(layer_data.get("sequence_only", False)),
                            )
                        )

                    if not loaded_layers:
                        loaded_layers = [Layer("Layer 1", blank_image())]
                    loaded_frames.append(
                        Frame(
                            loaded_layers,
                            max(
                                1,
                                min(
                                    MAX_PROJECT_FRAMES,
                                    int(frame_data.get("duration", 1)),
                                ),
                            ),
                        )
                    )

            CANVAS_WIDTH = width
            CANVAS_HEIGHT = height
            self.canvas.frames = loaded_frames
            self.canvas.current_frame = max(
                0,
                min(
                    int(metadata.get("current_frame", 0)),
                    len(loaded_frames) - 1,
                ),
            )
            layer_count = len(
                loaded_frames[self.canvas.current_frame].layers
            )
            self.canvas.active_layer_index = max(
                0,
                min(
                    int(metadata.get("active_layer_index", 0)),
                    layer_count - 1,
                ),
            )

            colors = metadata.get("colors", {})
            self.canvas.main_color = QColor(
                colors.get("main", "#000000")
            )
            self.canvas.sub_color = QColor(
                colors.get("sub", "#FF0000")
            )
            mode = colors.get("mode", "main")
            self.canvas.color_mode = (
                mode if mode in ("main", "sub", "transparent") else "main"
            )
            self.canvas.transparent_display_color = QColor(
                colors.get("background", "#FFFFFF")
            )

            display = metadata.get("display", {})
            self.canvas.silhouette_non_background = bool(
                display.get("silhouette_non_background", False)
            )
            self.canvas.onion_skin = bool(display.get("onion_skin", False))
            self.canvas.onion_previous_count = max(
                0, min(12, int(display.get("onion_previous_count", 1)))
            )
            self.canvas.onion_next_count = max(
                0, min(12, int(display.get("onion_next_count", 1)))
            )
            self.canvas.onion_previous_opacity = max(
                0.01,
                min(1.0, float(display.get("onion_previous_opacity", 0.22))),
            )
            self.canvas.onion_next_opacity = max(
                0.01,
                min(1.0, float(display.get("onion_next_opacity", 0.22))),
            )

            def normalized_onion_levels(raw_values, count):
                values = []
                if isinstance(raw_values, list):
                    for value in raw_values[:12]:
                        try:
                            values.append(
                                max(0, min(100, int(value)))
                            )
                        except (TypeError, ValueError):
                            values.append(100)
                count = max(0, min(12, int(count)))
                while len(values) < count:
                    index = len(values)
                    if count <= 1:
                        default_value = 100
                    else:
                        default_value = int(round(
                            100.0
                            - 55.0
                            * index
                            / float(max(1, count - 1))
                        ))
                    values.append(
                        max(10, min(100, default_value))
                    )
                return values[:count]

            self.canvas.onion_previous_levels = (
                normalized_onion_levels(
                    display.get("onion_previous_levels"),
                    self.canvas.onion_previous_count,
                )
            )
            self.canvas.onion_next_levels = (
                normalized_onion_levels(
                    display.get("onion_next_levels"),
                    self.canvas.onion_next_count,
                )
            )
            self.canvas.onion_center_percent = max(
                0.0,
                min(
                    100.0,
                    float(
                        display.get(
                            "onion_center_percent",
                            50.0,
                        )
                    ),
                ),
            )
            self.canvas.onion_previous_color = QColor(
                display.get("onion_previous_color", "#FF5C5C")
            )
            self.canvas.onion_next_color = QColor(
                display.get("onion_next_color", "#5CA0FF")
            )
            self.canvas.onion_previous_color_enabled = bool(
                display.get("onion_previous_color_enabled", False)
            )
            self.canvas.onion_next_color_enabled = bool(
                display.get("onion_next_color_enabled", False)
            )
            self.canvas.onion_selected_colors_only = bool(
                display.get("onion_selected_colors_only", False)
            )
            self.canvas.onion_previous_shift_x = max(
                -10000.0,
                min(
                    10000.0,
                    float(
                        display.get(
                            "onion_previous_shift_x",
                            0.0,
                        )
                    ),
                ),
            )
            self.canvas.onion_previous_shift_y = max(
                -10000.0,
                min(
                    10000.0,
                    float(
                        display.get(
                            "onion_previous_shift_y",
                            0.0,
                        )
                    ),
                ),
            )
            self.canvas.onion_previous_rotation = (
                (
                    float(
                        display.get(
                            "onion_previous_rotation",
                            0.0,
                        )
                    )
                    + 180.0
                )
                % 360.0
            ) - 180.0
            self.canvas.onion_previous_scale = max(
                1.0,
                min(
                    199.0,
                    float(
                        display.get(
                            "onion_previous_scale",
                            100.0,
                        )
                    ),
                ),
            )
            self.canvas.onion_next_shift_x = max(
                -10000.0,
                min(
                    10000.0,
                    float(
                        display.get(
                            "onion_next_shift_x",
                            0.0,
                        )
                    ),
                ),
            )
            self.canvas.onion_next_shift_y = max(
                -10000.0,
                min(
                    10000.0,
                    float(
                        display.get(
                            "onion_next_shift_y",
                            0.0,
                        )
                    ),
                ),
            )
            self.canvas.onion_next_rotation = (
                (
                    float(
                        display.get(
                            "onion_next_rotation",
                            0.0,
                        )
                    )
                    + 180.0
                )
                % 360.0
            ) - 180.0
            self.canvas.onion_next_scale = max(
                1.0,
                min(
                    199.0,
                    float(
                        display.get(
                            "onion_next_scale",
                            100.0,
                        )
                    ),
                ),
            )
            self.canvas.onion_tu_tb_scale = max(
                0.05,
                min(
                    20.0,
                    float(
                        display.get(
                            "onion_tu_tb_scale",
                            1.0,
                        )
                    ),
                ),
            )

            pressure = metadata.get("pressure", {})
            self.canvas.pressure_enabled = bool(
                pressure.get("enabled", True)
            )
            self.canvas.pressure_min = float(
                pressure.get("minimum", 0.05)
            )
            self.canvas.pressure_max = float(
                pressure.get("maximum", 1.0)
            )
            self.canvas.pressure_curve = float(
                pressure.get("curve", 1.0)
            )
            saved_points = pressure.get("points")
            if isinstance(saved_points, list) and len(saved_points) >= 2:
                self.canvas.pressure_curve_points = saved_points
            else:
                exponent = self.canvas.pressure_curve
                self.canvas.pressure_curve_points = [
                    [0.0, 0.0], [0.5, 0.5 ** exponent], [1.0, 1.0]
                ]

            self.timeline.fps.setValue(
                max(1, min(60, int(metadata.get("fps", 24))))
            )
            self.canvas.undo_stack.clear()
            self.canvas.redo_stack.clear()
            self.canvas._color_filter_cache.clear()
            self.canvas._silhouette_cache.clear()
            self._used_color_cache.clear()
            self.current_project_path = project_path
            self.update_project_title()
            self.tools.set_colors(
                self.canvas.main_color,
                self.canvas.sub_color,
                self.canvas.color_mode,
                self.canvas.transparent_display_color,
            )
            self.a_silhouette.setChecked(
                self.canvas.silhouette_non_background
            )
            self.tools.silhouette_btn.setChecked(
                self.canvas.silhouette_non_background
            )
            self.timeline.onion.blockSignals(True)
            self.timeline.onion.setChecked(self.canvas.onion_skin)
            self.timeline.onion.blockSignals(False)
            if not any(
                layer.sequence_number is not None
                for frame in self.canvas.frames
                for layer in frame.layers
                if layer.has_content
            ):
                self.canvas.normalize_sequence_numbers()
            self.canvas.apply_sequence_only_entries()
            self.set_timeline_mode("sheet")
            self.refresh_ui()
            self.schedule_used_color_refresh()
            QTimer.singleShot(0, self.fit_canvas)
            self.statusBar().showMessage(
                f"プロジェクトを開きました：{project_path.name}",
                3000,
            )
            return True
        except Exception as error:
            QMessageBox.critical(
                self,
                "プロジェクト読込エラー",
                f"プロジェクトを開けませんでした。\n\n{error}",
            )
            return False

    def import_images_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "画像を読み込む",
            "",
            "画像 (*.png *.jpg *.jpeg *.tga);;"
            "PNG (*.png);;JPEG (*.jpg *.jpeg);;TGA (*.tga)",
        )
        if not paths:
            return
        if len(paths) == 1:
            self.import_dropped_image(paths[0])
        else:
            self.import_dropped_images(paths)

    def import_image_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "画像フォルダーを読み込む",
            "",
        )
        if not folder:
            return

        folder_path = Path(folder)
        supported_suffixes = {".png", ".jpg", ".jpeg", ".tga"}
        paths = sorted(
            (
                path
                for path in folder_path.iterdir()
                if path.is_file()
                and path.suffix.lower() in supported_suffixes
            ),
            key=self.canvas._natural_path_key,
        )
        if not paths:
            QMessageBox.information(
                self,
                "画像フォルダーを読み込む",
                "選択したフォルダーに対応画像がありません。\n\n"
                "対応形式：PNG、JPEG、TGA",
            )
            return

        self.import_dropped_images(
            [str(path) for path in paths],
            layer_name=folder_path.name,
        )

    def import_psd_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "PSDを読み込む", "", "Photoshop Document (*.psd)"
        )
        if not path:
            return
        if PSDImage is None or PILImage is None:
            QMessageBox.warning(
                self,
                "PSD読み込み",
                "PSDの読み込みには psd-tools と Pillow が必要です。\n"
                "requirements.txtをインストールしてください。",
            )
            return
        try:
            psd = PSDImage.open(path)
            psd_width = int(psd.width)
            psd_height = int(psd.height)
            if (
                psd_width < 1
                or psd_height < 1
                or psd_width > MAX_IMAGE_DIMENSION
                or psd_height > MAX_IMAGE_DIMENSION
                or psd_width * psd_height > MAX_SINGLE_IMAGE_PIXELS
            ):
                raise ValueError(
                    "PSDの画像サイズが上限を超えています。"
                    f" ({psd_width} × {psd_height}px)"
                )
            if len(psd) > MAX_PROJECT_LAYERS:
                raise ValueError("PSDの最上位レイヤー数が上限を超えています。")
            imported = []
            skipped = 0
            imported_cell_count = 0
            viewport = (0, 0, psd_width, psd_height)
            for top_layer in psd:
                frame_layers = list(top_layer) if top_layer.is_group() else [top_layer]
                imported_cell_count += len(frame_layers)
                if imported_cell_count > MAX_PROJECT_LAYER_CELLS:
                    raise ValueError("PSDのレイヤー項目数が上限を超えています。")
                key_images = []
                for psd_layer in frame_layers:
                    try:
                        rendered = psd_layer.composite(
                            viewport=viewport,
                            force=True,
                        )
                    except Exception:
                        rendered = None
                    if rendered is None:
                        skipped += 1
                        continue
                    key_images.append(
                        PaintCanvas._pil_rgba_to_qimage(rendered)
                    )
                if key_images:
                    imported.append((
                        str(top_layer.name or "Layer"),
                        bool(top_layer.is_visible()),
                        max(0.0, min(1.0, float(top_layer.opacity) / 255.0)),
                        key_images,
                    ))
                else:
                    skipped += 1
            if not imported:
                raise ValueError("読み込める画像レイヤーがありません。")
        except Exception as exc:
            QMessageBox.critical(
                self, "PSD読み込み", f"PSDを読み込めませんでした。\n\n{exc}"
            )
            return

        if int(psd.width) > CANVAS_WIDTH or int(psd.height) > CANVAS_HEIGHT:
            self.canvas.push_doc_undo()
            self.replace_doc(
                max(CANVAS_WIDTH, int(psd.width)),
                max(CANVAS_HEIGHT, int(psd.height)),
                preserve=True,
            )
        self.canvas.push_doc_undo()
        start_frame = int(self.canvas.current_frame)
        maximum_keys = max(len(images) for _name, _visible, _opacity, images in imported)
        self.canvas._ensure_frame_count(start_frame + maximum_keys)
        first_new_layer = len(self.canvas.frames[0].layers)
        for name, visible, opacity, key_images in imported:
            layer_index = len(self.canvas.frames[0].layers)
            for frame in self.canvas.frames:
                frame.layers.append(Layer(
                    name,
                    blank_image(),
                    visible=visible,
                    opacity=opacity,
                ))
            for offset, image in enumerate(key_images):
                target = self.canvas.frames[start_frame + offset].layers[layer_index]
                self.canvas._place_imported_image(image, target.image)
                target.has_content = True
                target.exposure = 1
                target.sequence_number = offset + 1
        self.canvas.current_frame = start_frame
        self.canvas.active_layer_index = first_new_layer
        self.canvas.timeline_mode = "sheet"
        self.timeline.set_timeline_mode("sheet")
        self.canvas._cell_structure_dirty = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        message = f"PSDから{len(imported)}レイヤーを読み込みました。"
        if skipped:
            message += f"\n調整レイヤーなど{skipped}項目は破棄しました。"
        QMessageBox.information(self, "PSD読み込み", message)

    def resize_doc(self):
        d=CanvasSizeDialog(CANVAS_WIDTH,CANVAS_HEIGHT,"キャンバスサイズの変更",self)
        if d.exec():self.canvas.push_doc_undo();self.replace_doc(*d.values(),preserve=True)
    def replace_doc(self,w,h,preserve=False):
        global CANVAS_WIDTH,CANVAS_HEIGHT
        old_frames=self.canvas.frames if preserve else None;oldw,oldh=CANVAS_WIDTH,CANVAS_HEIGHT;CANVAS_WIDTH,CANVAS_HEIGHT=w,h
        if preserve:
            new=[]
            for f in old_frames:
                ls=[]
                for l in f.layers:
                    ni=blank_image();p=QPainter(ni);p.drawImage(QRectF(OUTSIDE_MARGIN, OUTSIDE_MARGIN, min(oldw, w), min(oldh, h)), l.image, QRectF(OUTSIDE_MARGIN, OUTSIDE_MARGIN, min(oldw, w), min(oldh, h)));p.end();ls.append(Layer(l.name,ni,l.visible,l.opacity,l.is_paper,l.has_content,l.alpha_locked,l.exposure,l.color_filter_enabled,tuple(l.color_filter_rgb) if l.color_filter_rgb is not None else None,bool(l.is_blank_key),l.sequence_number,bool(l.sequence_only)))
                new.append(Frame(ls,f.duration))
            self.canvas.frames=new
        else:self.canvas.frames=[make_frame()];self.canvas.undo_stack.clear();self.canvas.redo_stack.clear();self.set_timeline_mode("sheet")
        self.canvas.current_frame=0
        self.canvas.active_layer_index=0
        if not preserve:
            self.current_project_path = None
            self.update_project_title()
        self.refresh_ui()
        QTimer.singleShot(0,self.fit_canvas)
    @staticmethod
    def _normalize_shortcut_token(token):
        aliases = {
            "Control": "Ctrl",
            "CTRL": "Ctrl",
            "SHIFT": "Shift",
            "ALT": "Alt",
            "META": "Meta",
            " ": "Space",
        }
        token = str(token).strip()
        return aliases.get(token, token)

    def _shortcut_tokens(self, action, fallback):
        stored = action.property("holdShortcutText")
        if stored is not None:
            text = str(stored)
        else:
            text = action.shortcut().toString(
                QKeySequence.SequenceFormat.PortableText
            )
            if not text:
                text = fallback
        # Hold operations use the first chord only.
        text = text.split(",", 1)[0]
        return {
            self._normalize_shortcut_token(token)
            for token in text.split("+")
            if token.strip()
        }

    def _event_key_token(self, event):
        key_map = {
            Qt.Key.Key_Control: "Ctrl",
            Qt.Key.Key_Shift: "Shift",
            Qt.Key.Key_Alt: "Alt",
            Qt.Key.Key_Meta: "Meta",
            Qt.Key.Key_Space: "Space",
        }
        if event.key() in key_map:
            return key_map[event.key()]
        text = QKeySequence(int(event.key())).toString(
            QKeySequence.SequenceFormat.PortableText
        )
        return self._normalize_shortcut_token(text) if text else None

    def _sync_modifier_tokens(self, modifiers):
        mapping = (
            (Qt.KeyboardModifier.ControlModifier, "Ctrl"),
            (Qt.KeyboardModifier.ShiftModifier, "Shift"),
            (Qt.KeyboardModifier.AltModifier, "Alt"),
            (Qt.KeyboardModifier.MetaModifier, "Meta"),
        )
        for flag, token in mapping:
            if modifiers & flag:
                self._held_canvas_shortcut_tokens.add(token)
            else:
                self._held_canvas_shortcut_tokens.discard(token)

    def _widget_in_timeline(self, widget):
        return bool(
            isinstance(widget, QWidget)
            and (
                widget is self.timeline
                or self.timeline.isAncestorOf(widget)
            )
        )

    def _hand_scroll_area_for_widget(self, widget):
        if not isinstance(widget, QWidget):
            return None
        if self._widget_in_timeline(widget):
            return self.timeline.table
        candidates = (
            self.palette.scroll,
            self.tools_scroll,
            self.drawing_color_scroll,
            self.palette_scroll,
        )
        for area in candidates:
            if widget is area or area.isAncestorOf(widget):
                return area
        return None

    @staticmethod
    def _mouse_global_position(event):
        if hasattr(event, "globalPosition"):
            return QPointF(event.globalPosition())
        return QPointF(QCursor.pos())

    def _auxiliary_cursor_targets(self):
        return (
            self.timeline,
            self.timeline.table.viewport(),
            self.tools_dock,
            self.tools_scroll.viewport(),
            self.drawing_color_dock,
            self.drawing_color_scroll.viewport(),
            self.palette_dock,
            self.palette_scroll.viewport(),
            self.palette.scroll.viewport(),
        )

    def _update_auxiliary_hold_cursors(self):
        tool = self.canvas.temp_tool
        for widget in self._auxiliary_cursor_targets():
            try:
                widget.unsetCursor()
            except RuntimeError:
                pass
        if tool == "hand":
            for widget in self._auxiliary_cursor_targets():
                try:
                    widget.setCursor(Qt.CursorShape.OpenHandCursor)
                except RuntimeError:
                    pass
        elif tool == "zoom":
            try:
                self.timeline.setCursor(Qt.CursorShape.SizeVerCursor)
                self.timeline.table.viewport().setCursor(
                    Qt.CursorShape.SizeVerCursor
                )
            except RuntimeError:
                pass

    def _finish_auxiliary_hold_drag(self):
        grab_widget = self._ui_hold_grab_widget
        self._ui_hold_drag_mode = None
        self._ui_hold_scroll_area = None
        self._ui_hold_grab_widget = None
        if grab_widget is not None:
            try:
                grab_widget.releaseMouse()
            except RuntimeError:
                pass
        self._update_auxiliary_hold_cursors()

    def _handle_auxiliary_hold_event(self, watched, event):
        event_type = event.type()
        tool = self.canvas.temp_tool

        if self._ui_hold_drag_mode is not None:
            if event_type == QEvent.Type.MouseMove:
                current = self._mouse_global_position(event)
                if self._ui_hold_drag_mode == "hand":
                    area = self._ui_hold_scroll_area
                    if area is not None:
                        delta = current - self._ui_hold_start_global
                        horizontal, vertical = self._ui_hold_start_scroll
                        area.horizontalScrollBar().setValue(
                            int(round(horizontal - delta.x()))
                        )
                        area.verticalScrollBar().setValue(
                            int(round(vertical - delta.y()))
                        )
                        area.viewport().setCursor(
                            Qt.CursorShape.ClosedHandCursor
                        )
                elif self._ui_hold_drag_mode == "timeline_zoom":
                    delta_y = current.y() - self._ui_hold_last_global.y()
                    factor = math.pow(1.01, -delta_y)
                    viewport = self.timeline.table.viewport()
                    anchor = viewport.mapFromGlobal(
                        current.toPoint()
                    )
                    self.timeline.adjust_timeline_zoom(
                        factor,
                        anchor.x(),
                        anchor.y(),
                    )
                    self._ui_hold_last_global = current
                event.accept()
                return True
            if event_type == QEvent.Type.MouseButtonRelease:
                self._finish_auxiliary_hold_drag()
                event.accept()
                return True

        if event_type == QEvent.Type.Wheel and tool == "zoom":
            if self._widget_in_timeline(watched):
                delta = event.angleDelta().y()
                if delta:
                    viewport = self.timeline.table.viewport()
                    global_point = self._mouse_global_position(event)
                    anchor = viewport.mapFromGlobal(global_point.toPoint())
                    self.timeline.adjust_timeline_zoom(
                        1.15 if delta > 0 else 1.0 / 1.15,
                        anchor.x(),
                        anchor.y(),
                    )
                    event.accept()
                    return True

        if event_type == QEvent.Type.MouseButtonPress:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            area = self._hand_scroll_area_for_widget(watched)
            if tool == "hand" and area is not None:
                self._ui_hold_drag_mode = "hand"
                self._ui_hold_scroll_area = area
                self._ui_hold_start_global = self._mouse_global_position(event)
                self._ui_hold_last_global = QPointF(
                    self._ui_hold_start_global
                )
                self._ui_hold_start_scroll = (
                    area.horizontalScrollBar().value(),
                    area.verticalScrollBar().value(),
                )
                self._ui_hold_grab_widget = (
                    watched if isinstance(watched, QWidget) else area.viewport()
                )
                try:
                    self._ui_hold_grab_widget.grabMouse()
                except RuntimeError:
                    self._ui_hold_grab_widget = None
                area.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return True
            if tool == "zoom" and self._widget_in_timeline(watched):
                current = self._mouse_global_position(event)
                self._ui_hold_drag_mode = "timeline_zoom"
                self._ui_hold_scroll_area = self.timeline.table
                self._ui_hold_start_global = current
                self._ui_hold_last_global = QPointF(current)
                self._ui_hold_grab_widget = (
                    watched
                    if isinstance(watched, QWidget)
                    else self.timeline.table.viewport()
                )
                try:
                    self._ui_hold_grab_widget.grabMouse()
                except RuntimeError:
                    self._ui_hold_grab_widget = None
                event.accept()
                return True

        if event_type == QEvent.Type.MouseMove:
            if tool == "hand" and self._hand_scroll_area_for_widget(watched):
                event.accept()
                return True
            if tool == "zoom" and self._widget_in_timeline(watched):
                event.accept()
                return True
        return False

    def _update_canvas_hold_operation(self):
        held = set(self._held_canvas_shortcut_tokens)
        bindings = [
            ("zoom", self.a_hold_zoom, "Ctrl+Space"),
            ("rotate", self.a_hold_rotate, "Shift+Space"),
            ("hand", self.a_hold_hand, "Space"),
            ("eyedropper", self.a_hold_eyedropper, "Alt"),
        ]
        matches = []
        for priority, (tool, action, fallback) in enumerate(bindings):
            required = self._shortcut_tokens(action, fallback)
            if required and required.issubset(held):
                matches.append((len(required), -priority, tool))
        new_tool = max(matches)[2] if matches else None
        if self.canvas.temp_tool != new_tool:
            self.canvas.temp_tool = new_tool
            self.canvas.drawing = False
            self.canvas.middle_hand = False
            self.canvas.update_tool_cursor()
            self.canvas.update()
        if (
            self._ui_hold_drag_mode == "hand"
            and new_tool != "hand"
        ) or (
            self._ui_hold_drag_mode == "timeline_zoom"
            and new_tool != "zoom"
        ):
            self._finish_auxiliary_hold_drag()
        else:
            self._update_auxiliary_hold_cursors()

    def eventFilter(self, watched, event):
        if event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseMove,
            QEvent.Type.Wheel,
        ) and self._handle_auxiliary_hold_event(watched, event):
            return True
        if (
            event.type() == QEvent.Type.KeyPress
            and self.canvas.transform_active
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            self.canvas.commit_selection_transform()
            return True
        if (
            event.type() == QEvent.Type.KeyPress
            and self.canvas.transform_active
            and event.key() == Qt.Key.Key_Escape
        ):
            self.canvas.cancel_selection_transform()
            return True

        if event.type() in (
            QEvent.Type.ApplicationDeactivate,
            QEvent.Type.WindowDeactivate,
        ):
            self._held_canvas_shortcut_tokens.clear()
            self._update_canvas_hold_operation()
            return super().eventFilter(watched, event)

        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            # ショートカット編集ダイアログ内では、入力したキーをキャンバス操作に使わない。
            modal = QApplication.activeModalWidget()
            if isinstance(modal, ShortcutDialog):
                return super().eventFilter(watched, event)
            if getattr(event, "isAutoRepeat", lambda: False)():
                return super().eventFilter(watched, event)
            token = self._event_key_token(event)
            if event.type() == QEvent.Type.KeyPress:
                if token:
                    self._held_canvas_shortcut_tokens.add(token)
                self._sync_modifier_tokens(event.modifiers())
            else:
                if token:
                    self._held_canvas_shortcut_tokens.discard(token)
                self._sync_modifier_tokens(event.modifiers())
                # Qtの環境によっては解放イベントのmodifiersに解放前のキーが残る。
                if token in ("Ctrl", "Shift", "Alt", "Meta"):
                    self._held_canvas_shortcut_tokens.discard(token)
            self._update_canvas_hold_operation()
            if token in ("Space", "Ctrl", "Shift", "Alt", "Meta"):
                return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        """Stop active timers and UI signals before Qt destroys child widgets."""
        self._closing = True
        try:
            self.timer.stop()
            self._used_color_timer.stop()
            self._visible_color_timer.stop()
            self.timeline.blockSignals(True)
            self.timeline.table.blockSignals(True)
            self.timeline.layer_list.blockSignals(True)
            self.canvas.blockSignals(True)
        except RuntimeError:
            pass
        event.accept()

    def pressure(self):
        d=PressureDialog(self.canvas.pressure_enabled,self.canvas.pressure_min,self.canvas.pressure_max,getattr(self.canvas,"pressure_curve_points",self.canvas.pressure_curve),self)
        if d.exec():
            self.canvas.pressure_enabled = d.enabled.isChecked()
            self.canvas.pressure_min = d.minimum.value() / 100.0
            self.canvas.pressure_max = d.maximum.value() / 100.0
            self.canvas.pressure_curve_points = d.curve.points()
            self.canvas.pressure_curve = 1.0
    def shortcuts(self):
        categories = [
            ("ファイル・編集", self.file_edit_actions),
            ("ツール", self.tool_action_list),
            ("キャンバス操作", self.canvas_operation_actions),
            ("ツールコマンド", self.tool_command_actions),
            ("タイムライン", self.timeline_actions),
        ]
        ShortcutDialog(categories, self).exec()
    def crop_image(self,fi):return self.canvas.composite(fi,True).copy(OUTSIDE_MARGIN,OUTSIDE_MARGIN,CANVAS_WIDTH,CANVAS_HEIGHT)
    def save_png(self):
        path,_=QFileDialog.getSaveFileName(self,"PNG保存","frame.png","PNG (*.png)");
        if path:self.crop_image(self.canvas.current_frame).save(path if path.lower().endswith('.png') else path+'.png','PNG')
    def exposure_images(self):
        out=[]
        for i,f in enumerate(self.canvas.frames):
            im=self.crop_image(i)
            for _ in range(f.duration):out.append(im.copy())
        return out
    def save_tga_image(self, image, path):
        if PILImage is not None:
            rgba=image.convertToFormat(QImage.Format.Format_RGBA8888)
            width,height=rgba.width(),rgba.height()
            ptr=rgba.bits()
            try:ptr.setsize(rgba.sizeInBytes())
            except AttributeError:pass
            rows=np.frombuffer(ptr,dtype=np.uint8).reshape((height,rgba.bytesPerLine()))
            pixels=rows[:,:width*4].reshape((height,width,4)).copy()
            pil=PILImage.fromarray(pixels,"RGBA")
            pil.save(str(path),format="TGA",compression="tga_rle")
            return True
        return image.save(str(path),"TGA")

    def save_tga(self):
        path,_=QFileDialog.getSaveFileName(self,"TGA保存","frame.tga","TGA (*.tga)")
        if not path:return
        if not path.lower().endswith(".tga"):path+=".tga"
        if not self.save_tga_image(self.crop_image(self.canvas.current_frame),Path(path)):
            QMessageBox.warning(self,"TGA保存","TGAを保存できませんでした。Pillowの導入を確認してください。")

    def _sheet_duration(self):
        duration = 1
        for column, frame in enumerate(self.canvas.frames):
            for layer in frame.layers:
                if layer.sequence_only:
                    continue
                if layer.has_content or layer.is_blank_key:
                    duration = max(
                        duration,
                        column + max(1, int(layer.exposure)),
                    )
        return duration

    def export_xdts_dialog(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "XDTSタイムシートを書き出す",
            "PaintMaskAnimator.xdts",
            "XDTSタイムシート (*.xdts)",
        )
        if not path:
            return
        if not path.lower().endswith(".xdts"):
            path += ".xdts"

        duration = self._sheet_duration()
        tracks = []
        names = []
        layer_count = len(self.canvas.frames[0].layers)
        for layer_index in range(layer_count):
            names.append(self.canvas.frames[0].layers[layer_index].name)
            frame_data = []
            for column in range(duration):
                kind, key_column, _exposure = TimelineWidget.timeline_span_at(
                    self.canvas.frames, layer_index, column
                )
                if kind == "content":
                    if column == key_column:
                        number = self.canvas.frames[key_column].layers[
                            layer_index
                        ].sequence_number
                        value = str(number) if number is not None else "SYMBOL_NULL_CELL"
                    else:
                        value = "SYMBOL_HYPHEN"
                elif kind == "blank":
                    value = (
                        "SYMBOL_NULL_CELL"
                        if column == key_column else "SYMBOL_HYPHEN"
                    )
                else:
                    value = "SYMBOL_NULL_CELL"
                frame_data.append({
                    "frame": column,
                    "data": [{"id": 0, "values": [value]}],
                })
            tracks.append({"trackNo": layer_index, "frames": frame_data})

        payload = {
            "timeTables": [{
                "duration": duration,
                "name": "PaintMaskAnimator",
                "timeTableHeaders": [{"fieldId": 0, "names": names}],
                "fields": [{"fieldId": 0, "tracks": tracks}],
            }],
            "version": 5,
        }
        try:
            text = (
                "exchangeDigitalTimeSheet Save Data\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
                + "\n"
            )
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "XDTS書き出し", str(exc))
            return
        QMessageBox.information(
            self, "XDTS書き出し", f"タイムシートを書き出しました。\n\n{path}"
        )

    def export_psd_dialog(self):
        if PSDImage is None or PILImage is None:
            QMessageBox.warning(
                self,
                "PSD書き出し",
                "PSDの書き出しには psd-tools と Pillow が必要です。\n"
                "requirements.txtをインストールしてください。",
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "PSDを書き出す",
            "PaintMaskAnimator.psd",
            "Photoshop Document (*.psd)",
        )
        if not path:
            return
        if not path.lower().endswith(".psd"):
            path += ".psd"
        try:
            psd = PSDImage.new(
                "RGB",
                (int(CANVAS_WIDTH), int(CANVAS_HEIGHT)),
                color=(255, 255, 255),
            )
            layer_count = len(self.canvas.frames[0].layers)
            exported_keys = 0
            for layer_index in range(layer_count):
                template = self.canvas.frames[0].layers[layer_index]
                folder_name = str(template.name or f"Layer {layer_index + 1}")
                group = psd.create_group(
                    name=folder_name,
                    opacity=max(0, min(255, int(round(template.opacity * 255)))),
                )
                group.visible = bool(template.visible)
                key_number = 0
                for frame in self.canvas.frames:
                    layer = frame.layers[layer_index]
                    if not layer.has_content or layer.sequence_only:
                        continue
                    key_number += 1
                    source = layer.image.copy(
                        OUTSIDE_MARGIN,
                        OUTSIDE_MARGIN,
                        CANVAS_WIDTH,
                        CANVAS_HEIGHT,
                    )
                    pil_image = PaintCanvas._qimage_to_pil_rgba(source)
                    pixel_layer = psd.create_pixel_layer(
                        pil_image,
                        name=f"{folder_name}{key_number:04d}",
                    )
                    group.append(pixel_layer)
                    exported_keys += 1
            if exported_keys == 0:
                raise ValueError("書き出せるキーフレームがありません。")
            psd.save(path)
        except Exception as exc:
            QMessageBox.critical(
                self, "PSD書き出し", f"PSDを書き出せませんでした。\n\n{exc}"
            )
            return
        QMessageBox.information(
            self,
            "PSD書き出し",
            f"{exported_keys}個のキーフレームを書き出しました。\n\n{path}",
        )

    def import_xdts_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "XDTSタイムシートを読み込む",
            "",
            "XDTSタイムシート (*.xdts *.xtds);;すべてのファイル (*)",
        )
        if not path:
            return
        try:
            raw = Path(path).read_text(encoding="utf-8-sig")
            first_line, json_text = raw.split("\n", 1)
            if first_line.rstrip("\r") != "exchangeDigitalTimeSheet Save Data":
                raise ValueError("XDTSの先頭識別文字列が一致しません。")
            payload = json.loads(json_text)
            if int(payload.get("version", -1)) != 5:
                raise ValueError("対応しているXDTSバージョンは5です。")
            time_tables = payload.get("timeTables") or []
            if not time_tables:
                raise ValueError("タイムシート情報がありません。")
            time_table = time_tables[0]
            duration = max(1, int(time_table.get("duration", 1)))
            cell_field = next(
                (field for field in time_table.get("fields", [])
                 if int(field.get("fieldId", -1)) == 0),
                None,
            )
            if cell_field is None:
                raise ValueError("セル欄（fieldId 0）がありません。")
            tracks = sorted(
                cell_field.get("tracks", []),
                key=lambda track: int(track.get("trackNo", 0)),
            )
            header = next(
                (item for item in time_table.get("timeTableHeaders", [])
                 if int(item.get("fieldId", -1)) == 0),
                {},
            )
            names = list(header.get("names", []))
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self, "XDTS読み込み", f"読み込めませんでした。\n\n{exc}")
            return

        self.canvas.push_doc_undo()
        maximum_track = max(
            (int(track.get("trackNo", 0)) for track in tracks), default=0
        )
        required_layers = maximum_track + 1
        for frame in self.canvas.frames:
            while len(frame.layers) < required_layers:
                index = len(frame.layers)
                name = names[index] if index < len(names) else f"Layer {index + 1}"
                frame.layers.append(Layer(name, blank_image()))
        self.canvas._ensure_frame_count(duration)

        missing_images = set()
        for track in tracks:
            layer_index = int(track.get("trackNo", 0))
            image_bank = {}
            for frame in self.canvas.frames:
                layer = frame.layers[layer_index]
                if layer.has_content and layer.sequence_number is not None:
                    image_bank.setdefault(int(layer.sequence_number), layer.image.copy())
            for frame in self.canvas.frames:
                self.canvas._clear_timeline_layer_cell(frame.layers[layer_index])
            values = {int(item.get("frame", 0)): item for item in track.get("frames", [])}
            states = []
            previous = None
            for frame_number in range(duration):
                item = values.get(frame_number, {})
                instruction = next(
                    (data for data in item.get("data", []) if int(data.get("id", -1)) == 0),
                    {},
                )
                raw_values = instruction.get("values", [])
                value = str(raw_values[0]) if raw_values else "SYMBOL_NULL_CELL"
                if value == "SYMBOL_HYPHEN":
                    state = previous
                elif value in ("SYMBOL_NULL_CELL", "SYMBOL_TICK_1", "SYMBOL_TICK_2"):
                    state = None
                else:
                    try:
                        state = int(value)
                    except ValueError:
                        state = None
                states.append(state)
                previous = state

            run_start = 0
            for end in range(1, duration + 1):
                if end < duration and states[end] == states[run_start]:
                    continue
                state = states[run_start]
                target = self.canvas.frames[run_start].layers[layer_index]
                target.exposure = end - run_start
                if state is None:
                    target.image = blank_image()
                    target.has_content = False
                    target.is_blank_key = True
                else:
                    target.image = image_bank.get(state, blank_image()).copy()
                    target.has_content = True
                    target.is_blank_key = False
                    target.sequence_number = state
                    if state not in image_bank:
                        missing_images.add((layer_index, state))
                run_start = end
            if layer_index < len(names):
                for frame in self.canvas.frames:
                    frame.layers[layer_index].name = names[layer_index]

        self.canvas.current_frame = 0
        self.canvas.active_layer_index = 0
        self.canvas.timeline_mode = "sheet"
        self.timeline.set_timeline_mode("sheet")
        self.canvas._cell_structure_dirty = True
        self.canvas.changed.emit()
        self.canvas.selectionChanged.emit()
        self.canvas.update()
        message = "XDTSタイムシートを読み込みました。"
        if missing_images:
            message += f"\n対応画像がない番号：{len(missing_images)}件（白画像で配置）"
        QMessageBox.information(self, "XDTS読み込み", message)

    def export_key_sequence(self, image_format):
        invalid = '<>:"/\\|?*'
        safe_layer_names = []
        used_names = set()
        for layer_index, layer in enumerate(self.canvas.layers):
            base_name = "".join(
                "_" if character in invalid else character
                for character in (layer.name.strip() or f"Layer {layer_index + 1}")
            ).strip(" .") or f"Layer {layer_index + 1}"
            safe_name = base_name
            suffix = 2
            while safe_name.casefold() in used_names:
                safe_name = f"{base_name}_{suffix}"
                suffix += 1
            used_names.add(safe_name.casefold())
            safe_layer_names.append(safe_name)
        default_folder_name = f"PaintMaskAnimator_{image_format}_CSV"

        # 最初のダイアログで保存場所と親フォルダー名を同時に指定する。
        folder_dialog = QFileDialog(
            self,
            f"連番{image_format}＋CSVの書き出しフォルダー",
        )
        folder_dialog.setOption(
            QFileDialog.Option.DontUseNativeDialog,
            True,
        )
        folder_dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        folder_dialog.setAcceptMode(
            QFileDialog.AcceptMode.AcceptSave
        )
        folder_dialog.setLabelText(
            QFileDialog.DialogLabel.FileName,
            "フォルダー名：",
        )
        folder_dialog.setLabelText(
            QFileDialog.DialogLabel.Accept,
            "この名前で作成",
        )
        folder_dialog.selectFile(default_folder_name)

        if folder_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = folder_dialog.selectedFiles()
        if not selected:
            return

        destination = Path(selected[0])
        folder_name = destination.name.strip()
        folder_name = "".join(
            "_" if character in invalid else character
            for character in folder_name
        ).strip(" .")
        if not folder_name or folder_name in (".", ".."):
            QMessageBox.warning(
                self,
                "フォルダー名",
                "使用できるフォルダー名を指定してください。",
            )
            return
        destination = destination.parent / folder_name

        try:
            if destination.exists() and not destination.is_dir():
                QMessageBox.warning(
                    self,
                    "書き出し先",
                    "同じ名前のファイルが存在するため、"
                    "フォルダーを作成できません。",
                )
                return
            if destination.exists() and any(destination.iterdir()):
                answer = QMessageBox.question(
                    self,
                    "同名フォルダー",
                    f"「{folder_name}」には既存のファイルがあります。\n"
                    "このフォルダーへ書き出しますか？",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            destination.mkdir(parents=True, exist_ok=True)
            for safe_layer_name in safe_layer_names:
                (destination / safe_layer_name).mkdir(
                    parents=True,
                    exist_ok=True,
                )
        except OSError as exc:
            QMessageBox.critical(
                self,
                "フォルダー作成エラー",
                f"書き出しフォルダーを作成できません。\n\n{exc}",
            )
            return

        keys = []
        for layer_index, safe_layer_name in enumerate(safe_layer_names):
            for frame_index, frame in enumerate(self.canvas.frames):
                if (
                    layer_index < len(frame.layers)
                    and frame.layers[layer_index].has_content
                ):
                    keys.append((
                        layer_index,
                        safe_layer_name,
                        frame_index,
                        frame.layers[layer_index],
                    ))

        if not keys:
            QMessageBox.warning(
                self,
                "連番書き出し",
                "書き出せるキーフレームがありません。",
            )
            return

        timing_rows = []
        progress = self.create_progress_counter(
            f"連番{image_format}書き出し",
            len(keys),
            f"「{folder_name}」へ書き出しています",
        )
        try:
            layer_numbers = {}
            for number, (
                layer_index,
                safe_layer_name,
                frame_index,
                layer,
            ) in enumerate(keys, 1):
                self.update_progress_counter(
                    progress,
                    number - 1,
                    len(keys),
                    f"{number}枚目を書き出しています",
                )
                image = layer.image.copy(
                    OUTSIDE_MARGIN,
                    OUTSIDE_MARGIN,
                    CANVAS_WIDTH,
                    CANVAS_HEIGHT,
                )
                layer_number = layer_numbers.get(layer_index, 0) + 1
                layer_numbers[layer_index] = layer_number
                filename = f"{safe_layer_name}{layer_number:04d}"
                image_destination = destination / safe_layer_name
                if image_format == "PNG":
                    output_path = image_destination / (
                        filename + ".png"
                    )
                    if not image.save(str(output_path), "PNG"):
                        raise OSError(
                            f"{output_path.name}を保存できませんでした。"
                        )
                else:
                    output_path = image_destination / (
                        filename + ".tga"
                    )
                    self.save_tga_image(image, output_path)

                timing_rows.append([
                    filename,
                    frame_index + 1,
                    frame_index + max(1, layer.exposure),
                    max(1, layer.exposure),
                ])
                self.update_progress_counter(
                    progress,
                    number,
                    len(keys),
                    f"{number}枚目の書き出しが完了しました",
                )

            csv_path = destination / "TS.csv"
            with csv_path.open(
                "w", newline="", encoding="utf-8-sig"
            ) as file:
                writer = csv.writer(file)
                writer.writerow([
                    "name",
                    "start_frame",
                    "end_frame",
                    "duration",
                ])
                writer.writerows(timing_rows)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "連番書き出しエラー",
                f"書き出し中にエラーが発生しました。\n\n{exc}",
            )
            return
        finally:
            self.close_progress_counter(progress)

        self.statusBar().showMessage(
            f"「{folder_name}」へ{len(keys)}枚と"
            "TS.csvを書き出しました。",
            4000,
        )
        QMessageBox.information(
            self,
            "連番書き出し完了",
            "次の構成で書き出しました。\n\n"
            f"{destination}\n"
            "├─ 各レイヤー名のフォルダー\n"
            "│  └─ レイヤー名0001...\n"
            "└─ TS.csv",
        )


    def export_mp4(self):
        app_file = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)
        bundled_ffmpeg = app_file.with_name("ffmpeg.exe")
        ff = str(bundled_ffmpeg) if bundled_ffmpeg.exists() else shutil.which("ffmpeg")
        if not ff:QMessageBox.warning(self,'FFmpeg','ffmpegが必要です。');return
        path,_=QFileDialog.getSaveFileName(self,'MP4書き出し','animation.mp4','MP4 (*.mp4)')
        if not path:return
        if not path.lower().endswith('.mp4'):path+='.mp4'
        with tempfile.TemporaryDirectory() as td:
            total = sum(max(1, int(frame.duration)) for frame in self.canvas.frames)
            progress = self.create_progress_counter(
                "MP4書き出し",
                total,
                "動画用フレームを準備しています",
            )
            output_index = 0
            for frame_index, frame in enumerate(self.canvas.frames):
                image = self.crop_image(frame_index)
                for _ in range(max(1, int(frame.duration))):
                    output_index += 1
                    self.update_progress_counter(
                        progress,
                        output_index - 1,
                        total,
                        f"フレーム {output_index} / {total} を準備しています",
                    )
                    image.save(
                        str(Path(td) / f"f_{output_index:06}.png"),
                        "PNG",
                    )
            self.close_progress_counter(progress)
            encoding = self.create_progress_counter(
                "MP4書き出し",
                1,
                "FFmpegで動画へ変換しています",
            )
            QApplication.processEvents()
            r=subprocess.run([ff,'-y','-framerate',str(self.timeline.fps.value()),'-i',str(Path(td)/'f_%06d.png'),'-c:v','libx264','-pix_fmt','yuv420p',path],capture_output=True,text=True)
            self.close_progress_counter(encoding)
            if r.returncode:QMessageBox.critical(self,'MP4エラー',r.stderr[-1500:])
    def play(self, on):
        if on:
            fps = max(1, int(self.timeline.fps.value()))
            self._playback_started_at = time.perf_counter()
            self._playback_emitted_steps = 0
            self.canvas.set_playback_active(True)

            # 本来のフレーム間隔より細かく確認し、遅延時は
            # 経過時間に合わせてフレームを追いつかせる。
            poll_interval = max(
                4,
                min(16, int(round(500.0 / fps))),
            )
            self.timer.start(poll_interval)
        else:
            self.timer.stop()
            self._playback_started_at = None
            self._playback_emitted_steps = 0
            self.canvas.set_playback_active(False)

            # 再生中は省略していたタイムライン選択同期を停止時に1回だけ行う。
            self.canvas.selectionChanged.emit()
            self.canvas.update()

    def advance(self):
        if self._playback_started_at is None:
            return
        fps = max(1, int(self.timeline.fps.value()))
        elapsed = max(
            0.0,
            time.perf_counter() - self._playback_started_at,
        )
        expected_steps = int(math.floor(elapsed * fps))
        delta = expected_steps - self._playback_emitted_steps
        if delta <= 0:
            return

        self._playback_emitted_steps = expected_steps
        self.canvas.playback_advance(delta)


def _show_unhandled_exception(exc_type, exc_value, exc_tb):
    import traceback
    details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        app_file = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)
        log_path = app_file.with_name("oekaki_animator_crash.log")
        log_path.write_text(details, encoding="utf-8")
    except Exception:
        log_path = None
    text = "起動中または実行中にエラーが発生しました。"
    if log_path:
        text += f"\n\n詳細を保存しました：\n{log_path}"
    text += f"\n\n{exc_value}"
    app = QApplication.instance()
    # Never open another modal error dialog while Qt is shutting down. Doing so
    # can create an endless loop of errors from already-destroyed widgets.
    if app is not None and not app.closingDown():
        try:
            QMessageBox.critical(None, f"{APP_DISPLAY_NAME} エラー", text)
        except RuntimeError:
            print(details)
    else:
        print(details)


def main():
    sys.excepthook = _show_unhandled_exception
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationVersion(APP_VERSION)
    window = MainWindow()
    window.show()
    QTimer.singleShot(0, window.fit_canvas)
    return app.exec()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        sys.excepthook(*sys.exc_info())
        raise SystemExit(1) from None
