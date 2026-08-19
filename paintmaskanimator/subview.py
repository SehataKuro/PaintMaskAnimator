"""Linked reference-image viewer used by the dockable sub-view panel."""

from .common import *  # noqa: F401,F403
from . import imaging


SUBVIEW_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tga", ".psd"}


class SubViewWidget(QWidget):
    """画像ファイルをコピーせず、元パスへのリンクとして表示する。"""

    colorPicked = Signal(QColor)
    pathChanged = Signal(str)
    visibilityChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(180, 140)
        self.setWindowTitle("サブビュー")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.resize(620, 520)
        self._image = QImage()
        self._path = None
        self._files = []
        self._index = -1
        self._zoom = 1.0
        self._rotation = 0.0
        self._pan = QPointF()
        self._space_down = False
        self._control_down = False
        self._drag_mode = None
        self._press_pos = QPointF()
        self._last_pos = QPointF()
        self._moved = False
        self._color_provider = None
        self._pick_position = None
        self._pick_before_color = QColor()
        self._pick_after_color = QColor()

        self.previous_button = QToolButton(self)
        self.previous_button.setText("◀")
        self.previous_button.setToolTip("前の画像")
        self.next_button = QToolButton(self)
        self.next_button.setText("▶")
        self.next_button.setToolTip("次の画像")
        self.open_button = QToolButton(self)
        self.open_button.setText("開く")
        self.open_button.setToolTip("画像を開く")
        self.fit_button = QToolButton(self)
        self.fit_button.setText("全体")
        self.fit_button.setToolTip("全体を表示")
        self.name_label = QLabel("画像またはフォルダをドロップ", self)
        self.name_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(4, 3, 4, 3)
        toolbar.addWidget(self.previous_button)
        toolbar.addWidget(self.next_button)
        toolbar.addWidget(self.open_button)
        toolbar.addWidget(self.fit_button)
        toolbar.addWidget(self.name_label, 1)
        self.viewer = _SubViewCanvas(self)
        self._update_viewer_cursor()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(toolbar)
        layout.addWidget(self.viewer, 1)

        controls = QHBoxLayout()
        controls.setContentsMargins(5, 3, 5, 4)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(5, 800)
        self.zoom_slider.setValue(100)
        self.zoom_label = QLabel("100%")
        self.zoom_100_button = QPushButton("100%表示")
        self.rotation_slider = QSlider(Qt.Orientation.Horizontal)
        self.rotation_slider.setRange(-180, 180)
        self.rotation_slider.setValue(0)
        self.rotation_label = QLabel("0°")
        self.rotation_0_button = QPushButton("0°")
        for widget in (
            QLabel("拡大"), self.zoom_slider, self.zoom_label,
            self.zoom_100_button, QLabel("回転"), self.rotation_slider,
            self.rotation_label, self.rotation_0_button,
        ):
            controls.addWidget(widget)
        layout.addLayout(controls)
        self.previous_button.clicked.connect(lambda: self.step(-1))
        self.next_button.clicked.connect(lambda: self.step(1))
        self.open_button.clicked.connect(self.choose_image)
        self.fit_button.clicked.connect(self.reset_view)
        self.zoom_slider.valueChanged.connect(self.set_zoom_percent)
        self.zoom_100_button.clicked.connect(
            lambda: self.zoom_slider.setValue(100)
        )
        self.rotation_slider.valueChanged.connect(self.set_rotation)
        self.rotation_0_button.clicked.connect(
            lambda: self.rotation_slider.setValue(0)
        )
        self._update_navigation()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched, event):
        """サブビューが前面なら、子部品のフォーカスに関係なくSpaceを捕捉する。"""
        app = QApplication.instance()
        active = app.activeWindow() if app is not None else None
        belongs_to_subview = bool(
            isinstance(watched, QWidget)
            and (watched is self or self.isAncestorOf(watched))
        )
        if active is self or belongs_to_subview:
            if event.type() == QEvent.Type.ShortcutOverride and event.key() in (
                Qt.Key.Key_Space, Qt.Key.Key_Control
            ):
                event.accept()
                return True
            if (
                event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
                and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Control)
                and not event.isAutoRepeat()
            ):
                self.handle_hold_key_event(event)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def handle_hold_key_event(self, event):
        pressed = event.type() == QEvent.Type.KeyPress
        if event.key() == Qt.Key.Key_Space:
            self._space_down = pressed
        elif event.key() == Qt.Key.Key_Control:
            self._control_down = pressed
        self._update_viewer_cursor()

    def _eyedropper_cursor(self):
        cached = getattr(self, "_eyedropper_cursor_cache", None)
        if cached is not None:
            return cached
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("black"), 2))
        painter.drawLine(5, 18, 16, 7)
        painter.drawLine(8, 21, 19, 10)
        painter.drawLine(15, 6, 20, 11)
        painter.drawEllipse(QPointF(5, 19), 2, 2)
        painter.end()
        self._eyedropper_cursor_cache = QCursor(pixmap, 5, 19)
        return self._eyedropper_cursor_cache

    def _update_viewer_cursor(self):
        if self._drag_mode == "pan":
            self.viewer.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._drag_mode == "zoom" or (
            self._space_down and self._control_down
        ):
            self.viewer.setCursor(Qt.CursorShape.SizeAllCursor)
        elif self._space_down:
            self.viewer.setCursor(Qt.CursorShape.OpenHandCursor)
        elif self._drag_mode == "pick":
            self.viewer.setCursor(self._eyedropper_cursor())
        else:
            self.viewer.unsetCursor()

    def set_color_provider(self, provider):
        """スポイト確定前の描画色を取得するコールバックを設定する。"""
        self._color_provider = provider

    def current_paint_color(self):
        if callable(self._color_provider):
            try:
                return QColor(self._color_provider())
            except (RuntimeError, TypeError, AttributeError):
                pass
        return QColor()

    @staticmethod
    def supported_extensions():
        return set(SUBVIEW_EXTENSIONS)

    def choose_image(self):
        path, _selected = QFileDialog.getOpenFileName(
            self, "サブビュー画像を開く", "",
            "対応画像 (*.png *.jpg *.jpeg *.tga *.psd);;"
            "PNG (*.png);;JPEG (*.jpg *.jpeg);;TGA (*.tga);;PSD (*.psd);;"
            "すべてのファイル (*)",
        )
        if path:
            self.load_path(path)

    def load_path(self, path):
        candidate = Path(path)
        if candidate.is_dir():
            files = self._images_in_folder(candidate)
            if not files:
                self.name_label.setText("画像が見つかりません")
                return False
            target = files[0]
        else:
            target = candidate
            files = self._images_in_folder(candidate.parent)
            if candidate not in files:
                files.append(candidate)
                files.sort(key=lambda item: item.name.casefold())
        image = self._read_image(target)
        if image.isNull():
            self.name_label.setText(f"読み込めません: {target.name}")
            return False
        self._files = files
        self._index = files.index(target)
        self._set_image(target, image)
        return True

    @staticmethod
    def _read_image(path):
        """対応形式を読み込み、PSDは表示レイヤーの合成結果を返す。"""
        path = Path(path)
        if path.suffix.lower() != ".psd":
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            image = reader.read()
            if not image.isNull() or PILImage is None:
                return image
            # QtのTGAプラグインが対応しない圧縮・ピクセル形式はPillowで補完する。
            try:
                with PILImage.open(path) as source:
                    source.load()
                    return imaging.pil_rgba_to_qimage(source.convert("RGBA"))
            except (ValueError, TypeError, OSError, RuntimeError, AttributeError):
                return QImage()
        if PSDImage is None or PILImage is None:
            return QImage()
        try:
            psd = PSDImage.open(path)
            # PSDImage.composite() は非表示レイヤーを除外した表示合成を返す。
            rendered = psd.composite()
            if rendered is None:
                return QImage()
            return imaging.pil_rgba_to_qimage(rendered)
        except (
            ValueError, KeyError, IndexError, TypeError, OSError,
            RuntimeError, AttributeError,
        ):
            return QImage()

    def _images_in_folder(self, folder):
        extensions = self.supported_extensions()
        try:
            return sorted(
                (item for item in folder.iterdir()
                 if item.is_file() and item.suffix.lower() in extensions),
                key=lambda item: item.name.casefold(),
            )
        except OSError:
            return []

    def _set_image(self, path, image):
        self._path = Path(path)
        self._image = image.convertToFormat(QImage.Format.Format_ARGB32)
        self.name_label.setText(self._path.name)
        self.name_label.setToolTip(str(self._path))
        self.reset_view()
        self._update_navigation()
        self.pathChanged.emit(str(self._path))

    def step(self, offset):
        if not self._files:
            return
        index = self._index + int(offset)
        if not 0 <= index < len(self._files):
            return
        target = self._files[index]
        image = self._read_image(target)
        if image.isNull():
            return
        self._index = index
        self._set_image(target, image)

    def _update_navigation(self):
        self.previous_button.setEnabled(self._index > 0)
        self.next_button.setEnabled(0 <= self._index < len(self._files) - 1)

    def reset_view(self):
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(100)
        self.zoom_slider.blockSignals(False)
        self._zoom = 1.0
        self.zoom_label.setText("100%")
        self._pan = QPointF()
        self.viewer.update()

    def set_zoom_percent(self, value):
        self._zoom = max(0.05, min(8.0, int(value) / 100.0))
        self.zoom_label.setText(f"{int(value)}%")
        self.viewer.update()

    def set_rotation(self, value):
        self._rotation = float(value)
        self.rotation_label.setText(f"{int(value)}°")
        self.viewer.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.visibilityChanged.emit(True)

    def closeEvent(self, event):
        self.visibilityChanged.emit(False)
        super().closeEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile() and self.load_path(url.toLocalFile()):
                event.acceptProposedAction()
                return


class _SubViewCanvas(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    def enterEvent(self, event):
        self.owner._update_viewer_cursor()
        super().enterEvent(event)

    def _fit_scale(self):
        image = self.owner._image
        if image.isNull() or self.width() <= 0 or self.height() <= 0:
            return 0.0
        angle = math.radians(self.owner._rotation)
        cosine, sine = abs(math.cos(angle)), abs(math.sin(angle))
        rotated_width = image.width() * cosine + image.height() * sine
        rotated_height = image.width() * sine + image.height() * cosine
        return min(self.width() / rotated_width, self.height() / rotated_height)

    def _image_transform(self):
        image = self.owner._image
        transform = QTransform()
        if image.isNull():
            return transform
        center = QPointF(self.rect().center()) + self.owner._pan
        scale = max(0.01, self._fit_scale() * self.owner._zoom)
        transform.translate(center.x(), center.y())
        transform.rotate(self.owner._rotation)
        transform.scale(scale, scale)
        transform.translate(-image.width() / 2, -image.height() / 2)
        return transform

    def _image_rect(self):
        image = self.owner._image
        if image.isNull():
            return QRectF()
        return self._image_transform().mapRect(
            QRectF(0, 0, image.width(), image.height())
        )

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#30343a"))
        if self.owner._image.isNull():
            painter.setPen(QColor("#c3c8cc"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter,
                "画像またはフォルダを\nここにドロップ",
            )
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.setTransform(self._image_transform())
        painter.drawImage(QPointF(0, 0), self.owner._image)
        painter.resetTransform()
        if self.owner._drag_mode == "pick" and self.owner._pick_position is not None:
            self._draw_color_loupe(painter)

    def _draw_color_loupe(self, painter):
        position = QPointF(self.owner._pick_position)
        image_point = self._widget_to_image(position)
        if image_point is None:
            return
        radius = 42.0
        center = position + QPointF(58, -58)
        center.setX(max(radius + 4, min(self.width() - radius - 4, center.x())))
        center.setY(max(radius + 4, min(self.height() - radius - 30, center.y())))
        circle = QRectF(
            center.x() - radius, center.y() - radius,
            radius * 2, radius * 2,
        )
        painter.save()
        clip = QPainterPath()
        clip.addEllipse(circle)
        painter.setClipPath(clip)
        painter.fillRect(circle, QColor("#20242a"))
        magnification = 10.0
        transform = QTransform()
        transform.translate(center.x(), center.y())
        transform.scale(magnification, magnification)
        transform.translate(-image_point.x(), -image_point.y())
        painter.setTransform(transform)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawImage(QPointF(0, 0), self.owner._image)
        painter.restore()
        painter.setPen(QPen(QColor("white"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(circle)
        painter.drawLine(
            QPointF(center.x() - 7, center.y()),
            QPointF(center.x() + 7, center.y()),
        )
        painter.drawLine(
            QPointF(center.x(), center.y() - 7),
            QPointF(center.x(), center.y() + 7),
        )

        swatch_y = circle.bottom() + 5
        before_rect = QRectF(center.x() - 40, swatch_y, 38, 20)
        after_rect = QRectF(center.x() + 2, swatch_y, 38, 20)
        before = self.owner._pick_before_color
        after = self.owner._pick_after_color
        painter.fillRect(before_rect, before if before.isValid() else QColor("#000000"))
        painter.fillRect(after_rect, after if after.isValid() else QColor("#000000"))
        painter.setPen(QPen(QColor("white"), 1))
        painter.drawRect(before_rect)
        painter.drawRect(after_rect)
        painter.drawText(before_rect, Qt.AlignmentFlag.AlignCenter, "前")
        painter.drawText(after_rect, Qt.AlignmentFlag.AlignCenter, "後")

    def dragEnterEvent(self, event):
        self.owner.dragEnterEvent(event)

    def dropEvent(self, event):
        self.owner.dropEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Control) and not event.isAutoRepeat():
            self.owner.handle_hold_key_event(event)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Control) and not event.isAutoRepeat():
            self.owner.handle_hold_key_event(event)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button() not in (
            Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton
        ):
            return
        self.owner._press_pos = event.position()
        self.owner._last_pos = event.position()
        self.owner._moved = False
        if event.button() == Qt.MouseButton.MiddleButton:
            self.owner._drag_mode = "pan"
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self.owner._space_down:
            modifiers = (
                event.modifiers() | QApplication.keyboardModifiers()
            )
            self.owner._drag_mode = (
                "zoom" if (
                    self.owner._control_down
                    or modifiers & Qt.KeyboardModifier.ControlModifier
                )
                else "pan"
            )
        else:
            self.owner._drag_mode = "pick"
            self.owner._pick_position = QPointF(event.position())
            self.owner._pick_before_color = self.owner.current_paint_color()
            sampled = self._color_at(event.position())
            self.owner._pick_after_color = sampled or QColor()
        self.owner._update_viewer_cursor()
        self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        mode = self.owner._drag_mode
        if mode is None:
            return
        delta = event.position() - self.owner._last_pos
        if (event.position() - self.owner._press_pos).manhattanLength() > 3:
            self.owner._moved = True
        if mode == "pan":
            self.owner._pan += delta
            self.update()
        elif mode == "zoom":
            factor = math.exp(-delta.y() * 0.012)
            percent = int(round(self.owner._zoom * factor * 100))
            self.owner.zoom_slider.setValue(max(5, min(800, percent)))
        elif mode == "pick":
            self.owner._pick_position = QPointF(event.position())
            sampled = self._color_at(event.position())
            self.owner._pick_after_color = sampled or QColor()
            self.update()
        self.owner._last_pos = event.position()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() not in (
            Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton
        ):
            return
        if self.owner._drag_mode == "pick":
            self._pick_color(event.position())
        self.owner._drag_mode = None
        self.owner._pick_position = None
        self.owner._update_viewer_cursor()
        self.update()
        event.accept()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        factor = 1.15 if delta > 0 else 1.0 / 1.15
        percent = int(round(self.owner._zoom * factor * 100))
        self.owner.zoom_slider.setValue(max(5, min(800, percent)))
        event.accept()

    def _pick_color(self, position):
        color = self._color_at(position)
        if color is not None:
            self.owner.colorPicked.emit(color)

    def _widget_to_image(self, position):
        image = self.owner._image
        if image.isNull():
            return None
        inverse, invertible = self._image_transform().inverted()
        if not invertible:
            return None
        point = inverse.map(position)
        x, y = int(point.x()), int(point.y())
        if not (0 <= x < image.width() and 0 <= y < image.height()):
            return None
        return QPointF(x + 0.5, y + 0.5)

    def _color_at(self, position):
        image = self.owner._image
        point = self._widget_to_image(position)
        if point is None:
            return None
        x, y = int(point.x()), int(point.y())
        x = max(0, min(image.width() - 1, x))
        y = max(0, min(image.height() - 1, y))
        return QColor.fromRgba(image.pixel(x, y))
