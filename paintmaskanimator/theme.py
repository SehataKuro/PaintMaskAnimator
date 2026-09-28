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
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QStatusBar, QLabel, QHBoxLayout, QWidget, QFrame, QSizePolicy,
)

from . import config
from .i18n import tr
from .logging_setup import get_logger

log = get_logger(__name__)

IS_MAC = sys.platform == "darwin"

CONFIG_KEY = "ui_theme"
ACCENT_KEY = "ui_accent"
# Palette used when nothing else applies.
DEFAULT_THEME = "light"
DEFAULT_ACCENT = "#2f6fed"
# "system" follows the OS appearance / accent colour. It is the default on
# macOS, where apps are expected to follow System Settings; elsewhere the
# previous fixed defaults are kept so existing users see no change.
SYSTEM = "system"
DEFAULT_THEME_SETTING = SYSTEM if IS_MAC else DEFAULT_THEME
DEFAULT_ACCENT_SETTING = SYSTEM if IS_MAC else DEFAULT_ACCENT

# The OS accent colour, read from the platform palette before the app installs
# its own palette (after that, QApplication.palette() returns ours).
_system_accent = None

# Named accent presets offered in the "表示 > アクセントカラー" menu. The
# custom picker can still choose any colour; these are just quick presets.
#
# A function rather than a constant: ``tr()`` at module level would run at import
# time, before the translator is installed, and freeze the source language in.
def accent_presets():
    """(display name, hex) for the accent-colour menu, translated at call time."""
    return (
        (tr("ブルー"), "#2f6fed"),
        (tr("ティール"), "#0d9488"),
        (tr("グリーン"), "#1a8a4a"),
        (tr("パープル"), "#7c4dff"),
        (tr("オレンジ"), "#e0730a"),
        (tr("レッド"), "#d64545"),
    )

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
        "timeline_key": "#bfe7f4",
        "timeline_key_text": "#0d6694",
        "timeline_hold": "#d9f0f7",
        "timeline_hold_text": "#256b88",
        "timeline_sheet_key": "#ffe08a",
        "timeline_sheet_key_text": "#795300",
        "timeline_sheet_hold": "#fff1b8",
        "timeline_sheet_hold_text": "#80621a",
        "timeline_blank": "#eaf7fb",
        "timeline_blank_text": "#648b9b",
        "timeline_uncreated": "#f5f5f5",
        "timeline_uncreated_text": "#777d85",
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
        "timeline_key": "#16445a",
        "timeline_key_text": "#8ed8f5",
        "timeline_hold": "#213c49",
        "timeline_hold_text": "#a8d5e8",
        "timeline_sheet_key": "#594514",
        "timeline_sheet_key_text": "#ffd86a",
        "timeline_sheet_hold": "#463b21",
        "timeline_sheet_hold_text": "#ead28b",
        "timeline_blank": "#283840",
        "timeline_blank_text": "#82aeba",
        "timeline_uncreated": "#292d34",
        "timeline_uncreated_text": "#9aa2ad",
    },
}


# macOS: neutral greys closer to the system's own window, sidebar and
# separator colours, so the app sits naturally next to native apps.
MAC_OVERRIDES = {
    "light": {
        "window": "#ececec",
        "surface": "#ffffff",
        "surface_alt": "#f5f5f5",
        "border": "#d6d6d6",
        "text": "#1d1d1f",
        "text_muted": "#6e6e73",
        "hover": "#e2e2e4",
        "statusbar": "#ececec",
    },
    "dark": {
        "window": "#1e1e1e",
        "surface": "#2b2b2b",
        "surface_alt": "#323232",
        "border": "#3f3f3f",
        "text": "#e8e8ea",
        "text_muted": "#98989d",
        "hover": "#3a3a3c",
        "statusbar": "#1e1e1e",
    },
}


def available_themes():
    """Theme settings offered in the menu: follow the OS, or a fixed palette."""
    return (SYSTEM,) + tuple(PALETTES.keys())


def system_theme():
    """``"dark"`` or ``"light"`` from the OS appearance (light if unknown)."""
    app = QGuiApplication.instance()
    if app is None:
        return DEFAULT_THEME
    scheme = QGuiApplication.styleHints().colorScheme()
    return "dark" if scheme == Qt.ColorScheme.Dark else "light"


def resolved_theme(name=None):
    """The palette name actually used for a theme setting."""
    if name is None:
        name = current_theme()
    if name == SYSTEM:
        return system_theme()
    return name if name in PALETTES else DEFAULT_THEME


def system_accent():
    """The OS accent colour, or ``DEFAULT_ACCENT`` when it is unavailable."""
    global _system_accent
    if _system_accent is None:
        app = QGuiApplication.instance()
        if app is None:
            return DEFAULT_ACCENT
        role = getattr(QPalette.ColorRole, "Accent", QPalette.ColorRole.Highlight)
        color = QGuiApplication.palette().color(role)
        _system_accent = color.name() if color.isValid() else DEFAULT_ACCENT
    return _system_accent


def _mix(a, b, ratio):
    """Blend two hex colours; ``ratio`` is the weight of ``a`` (0..1)."""
    ca, cb = QColor(a), QColor(b)
    r = round(ca.red() * ratio + cb.red() * (1 - ratio))
    g = round(ca.green() * ratio + cb.green() * (1 - ratio))
    bl = round(ca.blue() * ratio + cb.blue() * (1 - ratio))
    return QColor(r, g, bl).name()


def _contrast_text(background):
    """Return black or white, whichever is more legible on ``background``."""
    color = QColor(background)

    def luminance(channel):
        value = channel / 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    level = (
        0.2126 * luminance(color.red())
        + 0.7152 * luminance(color.green())
        + 0.0722 * luminance(color.blue())
    )
    white_ratio = 1.05 / (level + 0.05)
    black_ratio = (level + 0.05) / 0.05
    return "#ffffff" if white_ratio >= black_ratio else "#000000"


def palette(name=None):
    """Return the colour dict for ``name`` (or the active theme).

    A copy is returned with the user's accent colour blended in, so callers
    always read a consistent, up-to-date accent.
    """
    name = resolved_theme(name)
    c = dict(PALETTES.get(name, PALETTES[DEFAULT_THEME]))
    if IS_MAC:
        c.update(MAC_OVERRIDES.get(name, {}))
    accent = str(current_accent())
    c["accent"] = accent
    c["accent_text"] = _contrast_text(accent)
    # Hover = accent nudged toward the window colour so it reads as "pressed".
    c["accent_hover"] = _mix(accent, c["window"], 0.82)
    # Selection = accent softened into the surface for subtle highlights.
    c["selection"] = _mix(accent, c["surface"], 0.28)
    # The neutral "info" severity follows the accent so the app feels unified.
    c["info"] = accent
    return c


def current_theme():
    """The stored theme setting: ``"system"``, ``"light"`` or ``"dark"``."""
    name = config.get_value(CONFIG_KEY, DEFAULT_THEME_SETTING)
    return name if name in available_themes() else DEFAULT_THEME_SETTING


def accent_setting():
    """The stored accent setting: ``"system"`` or a hex colour."""
    value = config.get_value(ACCENT_KEY, DEFAULT_ACCENT_SETTING)
    if value == SYSTEM or QColor(value).isValid():
        return value
    return DEFAULT_ACCENT_SETTING


def current_accent():
    value = accent_setting()
    return system_accent() if value == SYSTEM else value


def accent():
    """Convenience alias for the active accent colour (hex string)."""
    return current_accent()


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
    /* Fallback for tooltips the custom popup (tooltip.py) does not handle.
       A native tooltip window is rectangular, so no rounded corners here. */
    QToolTip {{
        background-color: {c['surface']};
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 5px 8px;
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
        border-radius: 8px;
        padding: 5px;
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

    /* Icon-only buttons (setProperty("iconButton", True)): no chrome until
       hovered, a soft accent wash when checked. */
    QPushButton[iconButton="true"], QToolButton[iconButton="true"] {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 0;
    }}
    QPushButton[iconButton="true"]:hover, QToolButton[iconButton="true"]:hover {{
        background-color: {c['hover']};
    }}
    QPushButton[iconButton="true"]:pressed, QToolButton[iconButton="true"]:pressed,
    QPushButton[iconButton="true"]:checked, QToolButton[iconButton="true"]:checked {{
        background-color: {c['selection']};
        border-color: {c['accent']};
    }}
    /* Primary round action (e.g. timeline play). */
    QPushButton[accentButton="true"] {{
        background-color: {c['accent']};
        border: none;
        border-radius: 12px;
        padding: 0;
    }}
    QPushButton[accentButton="true"]:hover {{ background-color: {c['accent_hover']}; }}
    QPushButton[accentButton="true"]:checked {{ background-color: {c['accent_hover']}; }}
    /* On/off chip (e.g. onion skin). */
    QPushButton[toggleChip="true"] {{
        background: transparent;
        border: 1px solid {c['border']};
        border-radius: 12px;
        padding: 0 10px 0 8px;
        color: {c['text_muted']};
    }}
    QPushButton[toggleChip="true"]:hover {{ background-color: {c['hover']}; color: {c['text']}; }}
    QPushButton[toggleChip="true"]:checked {{
        background-color: {c['selection']};
        border-color: {c['accent']};
        color: {c['text']};
        font-weight: 600;
    }}

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
    QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled,
    QDoubleSpinBox:disabled, QComboBox:disabled {{
        background-color: {c['surface_alt']};
        color: {c['text_muted']};
    }}
    QComboBox QAbstractItemView {{
        background-color: {c['surface']};
        color: {c['text']};
        border: 1px solid {c['border']};
        selection-background-color: {c['accent']};
        selection-color: {c['accent_text']};
        outline: 0;
    }}

    /* Generic containers and item views. */
    QFrame[frameShape="4"], QFrame[frameShape="5"] {{ color: {c['border']}; }}
    QGroupBox {{
        border: 1px solid {c['border']};
        border-radius: 7px;
        margin-top: 8px;
        padding-top: 7px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 8px;
        padding: 0 4px;
        color: {c['text']};
    }}
    QAbstractItemView {{
        background-color: {c['surface']};
        alternate-background-color: {c['surface_alt']};
        color: {c['text']};
        border: 1px solid {c['border']};
        selection-background-color: {c['accent']};
        selection-color: {c['accent_text']};
        outline: 0;
    }}
    QHeaderView::section {{
        background-color: {c['surface_alt']};
        color: {c['text']};
        border: none;
        border-right: 1px solid {c['border']};
        border-bottom: 1px solid {c['border']};
        padding: 4px 6px;
    }}
    QTableCornerButton::section {{
        background-color: {c['surface_alt']};
        border: none;
        border-right: 1px solid {c['border']};
        border-bottom: 1px solid {c['border']};
    }}
    QToolButton {{
        background-color: transparent;
        color: {c['text']};
        border: 1px solid transparent;
        border-radius: 5px;
        padding: 3px;
    }}
    QToolButton:hover {{ background-color: {c['hover']}; border-color: {c['border']}; }}
    QToolButton:pressed, QToolButton:checked {{ background-color: {c['selection']}; border-color: {c['accent']}; }}
    QToolButton:disabled {{ color: {c['text_muted']}; }}
    QProgressBar {{
        background-color: {c['surface_alt']};
        border: 1px solid {c['border']};
        border-radius: 5px;
        color: {c['text']};
        text-align: center;
    }}
    QProgressBar::chunk {{ background-color: {c['accent']}; border-radius: 4px; }}

    /* Sliders — thin rounded track, accent fill, clean circular knob. */
    QSlider:horizontal {{ min-height: 20px; }}
    QSlider::groove:horizontal {{
        height: 6px;
        background: {c['surface_alt']};
        border: 1px solid {c['border']};
        border-radius: 4px;
    }}
    QSlider::sub-page:horizontal {{
        background: {c['accent']};
        border: 1px solid {c['accent']};
        border-radius: 4px;
    }}
    QSlider::add-page:horizontal {{
        background: {c['surface_alt']};
        border: 1px solid {c['border']};
        border-radius: 4px;
    }}
    QSlider::handle:horizontal {{
        background: {c['surface']};
        border: 2px solid {c['accent']};
        width: 14px;
        height: 14px;
        /* Centre the 18px knob box over the 8px groove box. */
        margin: -6px 0;
        border-radius: 9px;
    }}
    QSlider::handle:horizontal:hover {{ background: {c['selection']}; }}
    QSlider::handle:horizontal:pressed {{ background: {c['accent']}; }}
    QSlider:vertical {{ min-width: 20px; }}
    QSlider::groove:vertical {{
        width: 6px;
        background: {c['surface_alt']};
        border: 1px solid {c['border']};
        border-radius: 4px;
    }}
    QSlider::sub-page:vertical {{
        background: {c['surface_alt']};
        border: 1px solid {c['border']};
        border-radius: 4px;
    }}
    QSlider::add-page:vertical {{
        background: {c['accent']};
        border: 1px solid {c['accent']};
        border-radius: 4px;
    }}
    QSlider::handle:vertical {{
        background: {c['surface']};
        border: 2px solid {c['accent']};
        width: 14px;
        height: 14px;
        margin: 0 -6px;
        border-radius: 9px;
    }}
    QSlider::handle:vertical:hover {{ background: {c['selection']}; }}

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
        background: transparent;
        color: {c['text_muted']};
        border: none;
        border-bottom: 2px solid transparent;
        padding: 5px 12px;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        color: {c['text']};
        border-bottom-color: {c['accent']};
    }}
    QTabBar::tab:hover:!selected {{ color: {c['text']}; background: {c['hover']}; }}

    /* Keep Fusion's native checkbox/radio glyphs. Styling their indicator
       background in QSS hides the check mark on Windows. */
    QCheckBox:disabled, QRadioButton:disabled {{ color: {c['text_muted']}; }}
    QTabWidget::pane {{
        background: {c['surface']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        top: -1px;
    }}
    QSplitter::handle {{ background-color: {c['border']}; }}
    QSplitter::handle:hover {{ background-color: {c['accent']}; }}
    QMessageBox, QFileDialog, QColorDialog, QInputDialog {{ background-color: {c['window']}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {c['accent']};
    }}
    """ + (_mac_stylesheet(c) if IS_MAC else "")


def _mac_stylesheet(c):
    """macOS additions: menus, scroll bars and controls closer to AppKit."""
    return f"""
    /* Context menus: rounded panel, inset rounded highlight, compact rows. */
    QMenu {{
        border-radius: 10px;
        padding: 5px;
    }}
    QMenu::item {{
        padding: 4px 18px 4px 12px;
        border-radius: 5px;
    }}
    QMenu::separator {{ margin: 5px 10px; }}
    /* Thin overlay-style scroll bars with no visible track. */
    QScrollBar:vertical {{ width: 10px; background: transparent; }}
    QScrollBar:horizontal {{ height: 10px; background: transparent; }}
    QScrollBar::handle:vertical {{
        background: {_mix(c['text_muted'], c['window'], 0.55)};
        border: 2px solid transparent;
        border-radius: 5px;
        min-height: 28px;
        margin: 1px;
    }}
    QScrollBar::handle:horizontal {{
        background: {_mix(c['text_muted'], c['window'], 0.55)};
        border-radius: 5px;
        min-width: 28px;
        margin: 1px;
    }}
    QScrollBar::handle:hover {{ background: {c['text_muted']}; }}
    /* Push buttons: AppKit-like height and radius. */
    QPushButton {{
        border-radius: 6px;
        padding: 4px 12px;
    }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ border-radius: 6px; }}
    QGroupBox {{ border-radius: 9px; }}
    """


def build_qpalette(name):
    """Build a QPalette so unstyled widget backgrounds follow the theme.

    The global style sheet only paints the widgets it names; container
    backgrounds (scroll areas, list/table viewports, dialogs, input bases)
    come from the QPalette. Setting both is what makes dark mode complete.
    """
    c = palette(name)
    pal = QPalette()
    window = QColor(c["window"])
    surface = QColor(c["surface"])
    text = QColor(c["text"])
    muted = QColor(c["text_muted"])
    disabled = QColor(_mix(c["text"], c["window"], 0.45))
    pal.setColor(QPalette.ColorRole.Window, window)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, surface)
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c["surface_alt"]))
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, surface)
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.ToolTipBase, surface)
    pal.setColor(QPalette.ColorRole.ToolTipText, text)
    pal.setColor(QPalette.ColorRole.PlaceholderText, muted)
    pal.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(c["accent_text"]))
    pal.setColor(QPalette.ColorRole.Link, QColor(c["accent"]))
    pal.setColor(QPalette.ColorRole.LinkVisited, QColor(c["accent_hover"]))
    pal.setColor(QPalette.ColorRole.BrightText, QColor(c["error"]))
    pal.setColor(QPalette.ColorRole.Light, QColor(_mix(c["surface"], "#ffffff", 0.78)))
    pal.setColor(QPalette.ColorRole.Midlight, QColor(c["surface_alt"]))
    pal.setColor(QPalette.ColorRole.Mid, QColor(c["border"]))
    pal.setColor(QPalette.ColorRole.Dark, QColor(_mix(c["border"], "#000000", 0.72)))
    pal.setColor(QPalette.ColorRole.Shadow, QColor("#000000"))
    for role in (
        QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        pal.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return pal


def apply_theme(app, name=None, persist=False):
    """Apply the theme to the ``QApplication`` and optionally persist it."""
    if name is None:
        name = current_theme()
    if name not in available_themes():
        name = DEFAULT_THEME
    if persist:
        config.set_value(CONFIG_KEY, name)
    # Read the OS accent while the platform palette is still in place.
    system_accent()
    # Fusion honours QPalette consistently across platforms, which the native
    # Windows style does not — required for a complete dark theme.
    try:
        app.setStyle("Fusion")
    except Exception as exc:  # noqa: BLE001 - Fusion is optional; theming degrades but must not crash startup
        log.debug("could not apply Fusion style, using platform default: %s", exc)
    app.setPalette(build_qpalette(name))
    app.setStyleSheet(build_stylesheet(name))
    app.setProperty("ui_theme", resolved_theme(name))
    return name


def set_accent(app, color, persist=True):
    """Persist a new accent colour (or ``"system"``) and re-apply the styles."""
    hexval = QColor(color).name() if not isinstance(color, str) else color
    if hexval != SYSTEM and not QColor(hexval).isValid():
        return current_accent()
    if persist:
        config.set_value(ACCENT_KEY, hexval)
    app.setPalette(build_qpalette(current_theme()))
    app.setStyleSheet(build_stylesheet(current_theme()))
    return hexval


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
        self._sync_idle()

    # -- public API --------------------------------------------------------
    def show_message(self, message, level="info", timeout=4000):
        """Show ``message`` styled for ``level`` for ``timeout`` ms (0 = keep)."""
        if level not in _SEVERITY_ICON:
            level = "info"
        self._current_level = level
        self._apply_level(level)
        self._text.setText(message or "")
        self._sync_idle()
        self._clear_timer.stop()
        if timeout and timeout > 0:
            self._clear_timer.start(int(timeout))

    def clear_message(self):
        self._clear_timer.stop()
        self._text.clear()
        self._current_level = "info"
        self._apply_level("info")
        self._sync_idle()

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
        self._sync_idle()

    def _sync_idle(self):
        # With no message, hide the stripe and icon so the bar stays quiet.
        has_text = bool(self._text.text())
        self._stripe.setVisible(has_text)
        self._icon.setVisible(has_text)

    def _apply_level(self, level):
        from . import icons  # icons imports this module for the palette

        c = palette()
        color = c.get(level, c["info"])
        self._stripe.setStyleSheet(f"background-color: {color}; border-radius: 2px;")
        dpr = self.devicePixelRatioF()
        self._icon.setPixmap(icons.pixmap(level, 14, color, color, dpr))
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
