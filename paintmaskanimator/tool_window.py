"""Small floating windows for occasional tools (colour chart, onion settings).

These used to be dock panels, but they are opened only now and then and need
more room than a panel column gives them. A :class:`ToolWindow` floats above
the main window (``Qt.Tool``: on macOS it hides while the app is in the
background, like a palette window), is not modal, and remembers where the user
left it.
"""
from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout

from . import config

_GEOMETRY_KEY = "tool_window_geometry"


class ToolWindow(QDialog):
    """Non-modal floating window hosting one content widget."""

    closed = Signal()

    def __init__(self, parent, title, key, content, default_size=None):
        super().__init__(parent, Qt.WindowType.Tool)
        self.setWindowTitle(title)
        self.setModal(False)
        self._key = key
        self.content = content
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(content)
        if not self._restore_geometry():
            if default_size is not None:
                self.resize(*default_size)
            self._place_beside_parent()

    def _restore_geometry(self):
        saved = (config.get_value(_GEOMETRY_KEY, {}) or {}).get(self._key)
        if not saved:
            return False
        try:
            return self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii")))
        except (AttributeError, TypeError, ValueError):
            return False

    def _save_geometry(self):
        stored = dict(config.get_value(_GEOMETRY_KEY, {}) or {})
        stored[self._key] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        config.set_value(_GEOMETRY_KEY, stored)

    def _place_beside_parent(self):
        parent = self.parentWidget()
        if parent is None:
            return
        frame = parent.frameGeometry()
        # Upper right of the main window, clear of the colour column's top.
        x = frame.right() - self.width() - 280
        y = frame.top() + 90
        self.move(max(frame.left(), x), y)

    def show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def reject(self):
        # Esc would only hide a QDialog; close it so ``closed`` always fires.
        self.close()

    def hideEvent(self, event):
        self._save_geometry()
        super().hideEvent(event)

    def closeEvent(self, event):
        # Not QDialog.closeEvent: it calls reject(), which here calls close().
        event.accept()
        self.closed.emit()
