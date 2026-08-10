"""Application-wide visual theme (light / dark) and a status-bar helper.

This module owns two things:

* A pair of colour palettes (``light`` / ``dark``) and a function that turns
  the active palette into a global Qt style sheet applied to the whole
  ``QApplication``. Only the main window chrome is themed here (menu bar,
  buttons, sliders, scroll bars, docks, the status bar); individual dialogs
  keep their own local styles.
* :class:`StatusBar`, a ``QStatusBar`` subclass that shows severity-coloured
  messages (info / success / warning / error) at the very bottom of the
  window, with a coloured accent stripe and a small status icon.

The chosen theme is persisted through :mod:`.config` so it survives restarts.
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QStatusBar, QLabel, QHBoxLayout, QWidget, QFrame, QSizePolicy,
)

from . import config

CONFIG_KEY = "ui_theme"
DEFAULT_THEME = "light"

# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------
# Each palette is a flat dict of named colours. Severity colours (info/
# success/warning/error) are shared by the status bar. Keeping them here means
# both the global style sheet and the status bar read from one source.
PALETTES = {
    "light": {
        "window": "#f4f5f7",
        "surface": "#ffffff",
        "surface_alt": "#eceef1",
        "border": "#d3d7de",
        "text": "#1f2329",
        "text_muted": "#6b7280",
        "accent": "#2f6fed",
        "accent_hover": "#245ad0",
        "accent_text": "#ffffff",
        "hover": "#e4e8ef",
        "selection": "#cfe0ff",
        "statusbar": "#ffffff",
        "info": "#2f6fed",
        "success": "#1a8a4a",
        "warning": "#b8730a",
        "error": "#c62828",
    },
    "dark": {
        "window": "#22252b",
        "surface": "#2b2f37",
        "surface_alt": "#31363f",
        "border": "#3c424c",
        "text": "#e6e9ee",
        "text_muted": "#9aa2ad",
        "accent": "#4d8bff",
        "accent_hover": "#3f79e6",
        "accent_text": "#ffffff",
        "hover": "#3a404a",
        "selection": "#33507f",
        "statusbar": "#1d2025",
        "info": "#6ba0ff",
        "success": "#4cc47c",
        "warning": "#e0a63a",
        "error": "#ff6b6b",
    },
}


def available_themes():
    return tuple(PALETTES.keys())


def palette(name=None):
    """Return the colour dict for ``name`` (or the active theme)."""
    if name is None:
        name = current_theme()
    return PALETTES.get(name, PALETTES[DEFAULT_THEME])


def current_theme():
    name = config.get_value(CONFIG_KEY, DEFAULT_THEME)
    return name if name in PALETTES else DEFAULT_THEME


def build_stylesheet(name):
    """Build the global Qt style sheet string for the given palette name."""
    c = palette(name)
    return f"""
    QMainWindow, QDialog {{
        background-color: {c['window']};
    }}
    QWidget {{
        color: {c['text']};
    }}
    QToolTip {{
        background-color: {c['surface']};
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 4px 6px;
    }}

    /* Menu bar & menus */
    QMenuBar {{
        background-color: {c['surface']};
        border-bottom: 1px solid {c['border']};
        padding: 2px 4px;
    }}
    QMenuBar::item {{
        background: transparent;
        padding: 5px 10px;
        border-radius: 6px;
    }}
    QMenuBar::item:selected {{ background-color: {c['hover']}; }}
    QMenuBar::item:pressed {{ background-color: {c['selection']}; }}
    QMenu {{
        background-color: {c['surface']};
        border: 1px solid {c['border']};
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 22px;
        border-radius: 5px;
    }}
    QMenu::item:selected {{ background-color: {c['accent']}; color: {c['accent_text']}; }}
    QMenu::separator {{
        height: 1px;
        background: {c['border']};
        margin: 4px 8px;
    }}

    /* Buttons */
    QPushButton {{
        background-color: {c['surface']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 5px 12px;
        color: {c['text']};
    }}
    QPushButton:hover {{ background-color: {c['hover']}; }}
    QPushButton:pressed {{ background-color: {c['selection']}; }}
    QPushButton:disabled {{ color: {c['text_muted']}; border-color: {c['border']}; }}
    QPushButton:default {{
        background-color: {c['accent']};
        border-color: {c['accent']};
        color: {c['accent_text']};
    }}
    QPushButton:default:hover {{ background-color: {c['accent_hover']}; }}

    /* Text entry */
    QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background-color: {c['surface']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 3px 6px;
        selection-background-color: {c['accent']};
        selection-color: {c['accent_text']};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}

    /* Sliders */
    QSlider::groove:horizontal {{
        height: 4px;
        background: {c['border']};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {c['accent']};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {c['surface']};
        border: 2px solid {c['accent']};
        width: 14px;
        height: 14px;
        margin: -6px 0;
        border-radius: 8px;
    }}
    QSlider::handle:horizontal:hover {{ background: {c['hover']}; }}

    /* Scroll bars */
    QScrollBar:vertical {{
        background: transparent;
        width: 12px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {c['border']};
        min-height: 24px;
        border-radius: 6px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {c['text_muted']}; }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 12px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {c['border']};
        min-width: 24px;
        border-radius: 6px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {c['text_muted']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

    /* Tabs */
    QTabBar::tab {{
        background: {c['surface_alt']};
        border: 1px solid {c['border']};
        padding: 5px 12px;
        margin-right: 2px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
    }}
    QTabBar::tab:selected {{
        background: {c['surface']};
        border-bottom-color: {c['surface']};
    }}
    QTabBar::tab:hover {{ background: {c['hover']}; }}

    /* Checkboxes */
    QCheckBox::indicator {{
        width: 15px; height: 15px;
        border: 1px solid {c['border']};
        border-radius: 4px;
        background: {c['surface']};
    }}
    QCheckBox::indicator:checked {{
        background: {c['accent']};
        border-color: {c['accent']};
    }}
    """


def apply_theme(app, name=None, persist=False):
    """Apply the theme to the ``QApplication`` and optionally persist it."""
    if name is None:
        name = current_theme()
    if name not in PALETTES:
        name = DEFAULT_THEME
    app.setStyleSheet(build_stylesheet(name))
    app.setProperty("ui_theme", name)
    if persist:
        config.set_value(CONFIG_KEY, name)
    return name


# ---------------------------------------------------------------------------
# Status bar
# ---------------------------------------------------------------------------
_SEVERITY_ICON = {
    "info": "ℹ",
    "success": "✓",
    "warning": "⚠",
    "error": "✕",
}


class StatusBar(QStatusBar):
    """Status bar with severity-coloured messages and an accent stripe.

    Use :meth:`show_message` for level-aware messages. Plain
    ``showMessage(text, timeout)`` calls (used throughout the app) still work
    and are treated as ``info``.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizeGripEnabled(False)

        # Accent stripe on the far left signals severity at a glance.
        self._stripe = QFrame(self)
        self._stripe.setFixedWidth(4)
        self._stripe.setFrameShape(QFrame.Shape.NoFrame)

        self._icon = QLabel(self)
        self._icon.setFixedWidth(16)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._text = QLabel(self)
        self._text.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        container = QWidget(self)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self._stripe)
        row.addWidget(self._icon)
        row.addWidget(self._text, 1)
        # Permanent (not "normal") so Qt never hides it while a plain
        # showMessage() is active; the stretch squeezes out Qt's own text area
        # so legacy showMessage() calls surface through our label instead.
        self.addPermanentWidget(container, 1)

        # Qt clears showMessage() text on timeout; mirror that into our label.
        self.messageChanged.connect(self._on_message_changed)

        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(self.clear_message)

        self._current_level = "info"
        self._apply_level("info")

    # -- public API --------------------------------------------------------
    def show_message(self, message, level="info", timeout=4000):
        """Show ``message`` styled for ``level`` for ``timeout`` ms (0 = keep)."""
        if level not in _SEVERITY_ICON:
            level = "info"
        self._current_level = level
        self._apply_level(level)
        self._text.setText(message or "")
        self._clear_timer.stop()
        if timeout and timeout > 0:
            self._clear_timer.start(int(timeout))

    def clear_message(self):
        self._clear_timer.stop()
        self._text.clear()
        self._current_level = "info"
        self._apply_level("info")

    def refresh_palette(self):
        """Re-read colours after a theme switch."""
        self._apply_level(self._current_level)

    # -- internals ---------------------------------------------------------
    def _on_message_changed(self, text):
        # Fires when other code calls showMessage()/clearMessage() directly.
        # Route it through our label so those legacy call sites keep working.
        if text:
            if self._text.text() != text:
                self._current_level = "info"
                self._apply_level("info")
                self._text.setText(text)
        else:
            if not self._clear_timer.isActive():
                self._text.clear()

    def _apply_level(self, level):
        c = palette()
        color = c.get(level, c["info"])
        self._stripe.setStyleSheet(f"background-color: {color};")
        self._icon.setText(_SEVERITY_ICON.get(level, ""))
        self._icon.setStyleSheet(
            f"color: {color}; font-weight: bold; font-size: 13px;"
        )
        weight = "600" if level in ("warning", "error") else "500"
        self._text.setStyleSheet(
            f"color: {c['text'] if level == 'info' else color}; "
            f"font-weight: {weight};"
        )
        self.setStyleSheet(
            f"QStatusBar {{ background-color: {c['statusbar']}; "
            f"border-top: 1px solid {c['border']}; }}"
            "QStatusBar::item { border: none; }"
        )
