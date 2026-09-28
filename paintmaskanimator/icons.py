"""Theme-aware line icons for the application chrome.

Every icon is a small 24×24 SVG drawn with a single stroke weight, so the
toolbars, timeline and panel headers share one visual language. The SVGs use
two placeholder colours that are substituted at render time:

* ``currentColor`` — the foreground (the theme's text colour by default).
* ``ACCENT`` — the theme accent, used sparingly for a highlight detail.

Icons are rendered at several pixel sizes so they stay crisp on Retina /
high-DPI screens, and cached per (name, colours) because the same icon is
requested from many widgets.
"""
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme

_STROKE = (
    'fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"'
)

# Paths are drawn for this project (no third-party icon set is bundled).
_PATHS = {
    # --- drawing tools -----------------------------------------------------
    "brush": (
        '<path d="M19.5 4.5 10 14"/>'
        '<path d="M9.5 14.5c-2.4-.4-4.3 1.1-4.5 3.4-.1 1.2-.6 1.9-1.5 2.4 '
        '3.2.9 7.2-.2 7.5-3.6" fill="ACCENT" stroke="ACCENT"/>'
    ),
    "line": (
        '<path d="M6 18 18 6"/>'
        '<circle cx="5" cy="19" r="1.8" fill="ACCENT" stroke="ACCENT"/>'
        '<circle cx="19" cy="5" r="1.8" fill="ACCENT" stroke="ACCENT"/>'
    ),
    "shape": '<path d="M12 3.5 20.5 12 12 20.5 3.5 12Z"/>',
    "bucket": (
        '<path d="m4.5 11 7-7 7.5 7.5-7 7a2 2 0 0 1-2.8 0L4.5 13.8a2 2 0 0 1 0-2.8Z"/>'
        '<path d="M4.6 12.4h14"/>'
        '<path d="M20.5 15.5s1.5 2 1.5 3a1.5 1.5 0 0 1-3 0c0-1 1.5-3 1.5-3Z" '
        'fill="ACCENT" stroke="ACCENT"/>'
    ),
    "lasso": (
        '<path d="M7 17.5C4.3 16.2 3 14.4 3 12c0-4.4 4-7.5 9-7.5s9 3.1 9 7.5-4 7.5-9 7.5"/>'
        '<path d="M8.5 15.5a2 2 0 1 0 0 4c1.4 0 1.5 2 .5 3"/>'
    ),
    "lasso_fill": (
        '<path d="M7 17.5C4.3 16.2 3 14.4 3 12c0-4.4 4-7.5 9-7.5s9 3.1 9 7.5-4 7.5-9 7.5" '
        'fill="ACCENT" fill-opacity="0.45"/>'
        '<path d="M8.5 15.5a2 2 0 1 0 0 4c1.4 0 1.5 2 .5 3"/>'
    ),
    "rect_select": (
        '<path d="M4 7V5a1 1 0 0 1 1-1h2M11 4h2M17 4h2a1 1 0 0 1 1 1v2M20 11v2'
        'M20 17v2a1 1 0 0 1-1 1h-2M13 20h-2M7 20H5a1 1 0 0 1-1-1v-2M4 13v-2"/>'
    ),
    "auto_select": (
        '<path d="m4 20 10-10"/>'
        '<path d="m12.5 11.5 2 2"/>'
        '<path d="M17 3v3M20.5 4.5l-2 2M21 9h-3M15 5.5l-1-1" stroke="ACCENT"/>'
    ),
    "eyedropper": (
        '<path d="m14.5 6.5 3 3"/>'
        '<path d="m16 8-9.5 9.5-2.5.5.5-2.5L14 6"/>'
        '<path d="M14.5 4.5a2.1 2.1 0 0 1 3 0l2 2a2.1 2.1 0 0 1 0 3l-1.5 1.5-5-5Z" '
        'fill="ACCENT" stroke="ACCENT"/>'
    ),
    "dust": (
        '<circle cx="7" cy="8" r="2.2" fill="ACCENT" stroke="ACCENT"/>'
        '<circle cx="16.5" cy="7" r="1.6" fill="ACCENT" stroke="ACCENT"/>'
        '<circle cx="12" cy="16" r="2.8" fill="ACCENT" stroke="ACCENT"/>'
        '<path d="M19 14.5v.01M5 17.5v.01"/>'
    ),
    # --- timeline ----------------------------------------------------------
    "play": '<path d="M7.5 5.2v13.6a.8.8 0 0 0 1.2.7l11-6.8a.8.8 0 0 0 0-1.4l-11-6.8a.8.8 0 0 0-1.2.7Z" fill="currentColor"/>',
    "pause": (
        '<rect x="6.5" y="5" width="3.5" height="14" rx="1" fill="currentColor"/>'
        '<rect x="14" y="5" width="3.5" height="14" rx="1" fill="currentColor"/>'
    ),
    "prev_frame": '<path d="m14.5 6-6 6 6 6"/>',
    "next_frame": '<path d="m9.5 6 6 6-6 6"/>',
    "prev_key": '<path d="M6 5.5v13"/><path d="m17 6-6.5 6 6.5 6Z" fill="currentColor"/>',
    "next_key": '<path d="M18 5.5v13"/><path d="m7 6 6.5 6L7 18Z" fill="currentColor"/>',
    "frame_blank": (
        '<rect x="4" y="4" width="16" height="16" rx="3" stroke-dasharray="3 2.6"/>'
        '<path d="M12 9v6M9 12h6"/>'
    ),
    "frame_extend": (
        '<rect x="3.5" y="6" width="9" height="12" rx="2"/>'
        '<path d="M15.5 12h5M18 9.5v5"/>'
    ),
    "trash": (
        '<path d="M4.5 6.5h15"/>'
        '<path d="M9.5 6.5V5a1.5 1.5 0 0 1 1.5-1.5h2A1.5 1.5 0 0 1 14.5 5v1.5"/>'
        '<path d="M6.5 6.5 7.3 19a1.6 1.6 0 0 0 1.6 1.5h6.2a1.6 1.6 0 0 0 1.6-1.5l.8-12.5"/>'
    ),
    "sort_numbers": (
        '<path d="M7 5v14"/><path d="m4 16 3 3 3-3"/>'
        '<path d="M12.5 6.5h7.5M12.5 12h5.5M12.5 17.5h3.5"/>'
    ),
    "remap": (
        '<rect x="5" y="4.5" width="14" height="16" rx="2"/>'
        '<path d="M9 3.5h6v2.5H9Z"/>'
        '<path d="M8.5 11h7M8.5 14.5h4.5"/>'
    ),
    "onion": (
        '<rect x="8" y="8" width="12" height="12" rx="2.5"/>'
        '<path d="M5 15.5A2.5 2.5 0 0 1 4 13.5v-7A2.5 2.5 0 0 1 6.5 4h7a2.5 2.5 0 0 1 2 1" '
        'stroke-opacity="0.5"/>'
    ),
    "settings": (
        '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/>'
        '<circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>'
    ),
    # --- generic chrome ----------------------------------------------------
    "menu": '<path d="M4.5 7h15M4.5 12h15M4.5 17h15"/>',
    "close": '<path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>',
    "plus": '<path d="M12 5.5v13M5.5 12h13"/>',
    "minus": '<path d="M5.5 12h13"/>',
    "chevron_down": '<path d="m6.5 9.5 5.5 5.5 5.5-5.5"/>',
    "chevron_right": '<path d="m9.5 6.5 5.5 5.5-5.5 5.5"/>',
    "chevron_left": '<path d="m14.5 6.5-5.5 5.5 5.5 5.5"/>',
    "eye": (
        '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/>'
        '<circle cx="12" cy="12" r="2.8"/>'
    ),
    "eye_off": (
        '<path d="M10 5.7a8.8 8.8 0 0 1 2-.2c6 0 9.5 6.5 9.5 6.5a17 17 0 0 1-2.3 3.1'
        'M6.6 6.7C3.9 8.4 2.5 12 2.5 12S6 18.5 12 18.5a8.7 8.7 0 0 0 5.4-1.8"/>'
        '<path d="m3.5 3.5 17 17"/>'
    ),
    "draft": (
        '<path d="M15.5 4.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"/>'
        '<path d="M13.5 6.5l3 3"/>'
    ),
    "folder_plus": (
        '<path d="M3.5 7.5v10a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-6.5l-2-2.5h-4.5a2 2 0 0 0-2 2Z"/>'
        '<path d="M12 11v5M9.5 13.5h5"/>'
    ),
    "fit": (
        '<path d="M4 9V5.5A1.5 1.5 0 0 1 5.5 4H9M15 4h3.5A1.5 1.5 0 0 1 20 5.5V9'
        'M20 15v3.5a1.5 1.5 0 0 1-1.5 1.5H15M9 20H5.5A1.5 1.5 0 0 1 4 18.5V15"/>'
    ),
    "zoom_actual": (
        '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/>'
        '<path d="M9 8.5 10.5 7.5v6"/>'
    ),
    "rotate_reset": (
        '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4.5 4.5v4h4"/>'
    ),
    "swap": '<path d="M7 4.5 4 7.5l3 3"/><path d="M4 7.5h11a4 4 0 0 1 4 4v1"/><path d="m17 19.5 3-3-3-3"/><path d="M20 16.5H9a4 4 0 0 1-4-4v-1"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.01"/>',
    "success": '<circle cx="12" cy="12" r="8.5"/><path d="m8.2 12.2 2.6 2.6 5-5.2"/>',
    "warning": '<path d="M10.3 4.3 2.9 17.2A2 2 0 0 0 4.6 20h14.8a2 2 0 0 0 1.7-2.8L13.7 4.3a2 2 0 0 0-3.4 0Z"/><path d="M12 9.5v4M12 16.8v.01"/>',
    "error": '<circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6M15 9l-6 6"/>',
}

# Pixel sizes pre-rendered into every QIcon. Qt picks the closest one for the
# requested logical size × device pixel ratio.
_RENDER_SIZES = (16, 20, 24, 32, 40, 48, 64)
_cache: dict = {}


def names():
    return tuple(_PATHS)


def _svg(name, color, accent):
    body = _PATHS[name].replace("currentColor", color).replace("ACCENT", accent)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'{_STROKE.replace("currentColor", color)}>{body}</svg>'
    )


def pixmap(name, size, color=None, accent=None, device_pixel_ratio=1.0):
    """Render one icon to a transparent pixmap of ``size`` logical pixels."""
    c = theme.palette()
    color = QColor(color or c["text"]).name()
    accent = QColor(accent or c["accent"]).name()
    physical = max(1, round(size * device_pixel_ratio))
    renderer = QSvgRenderer(QByteArray(_svg(name, color, accent).encode("utf-8")))
    image = QPixmap(physical, physical)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, physical, physical))
    painter.end()
    image.setDevicePixelRatio(device_pixel_ratio)
    return image


def icon(name, color=None, accent=None, active_color=None):
    """Return a cached, multi-resolution QIcon for ``name``.

    ``color`` defaults to the theme text colour and ``accent`` to the theme
    accent. Disabled states use the muted text colour; ``active_color`` (when
    given) is used for the checked/``On`` state, e.g. the pause glyph colour on
    an accent-filled play button.
    """
    c = theme.palette()
    color = QColor(color or c["text"]).name()
    accent = QColor(accent or c["accent"]).name()
    muted = QColor(c["text_muted"]).name()
    key = (name, color, accent, muted, active_color)
    cached = _cache.get(key)
    if cached is not None:
        # Hand out copies: some bindings (e.g. the QtAds icon provider) take
        # ownership of the QIcon passed to them and would delete the cached one.
        return QIcon(cached)
    result = QIcon()
    for size in _RENDER_SIZES:
        result.addPixmap(pixmap(name, size, color, accent), QIcon.Mode.Normal)
        result.addPixmap(pixmap(name, size, muted, muted), QIcon.Mode.Disabled)
        if active_color:
            result.addPixmap(
                pixmap(name, size, active_color, active_color),
                QIcon.Mode.Normal, QIcon.State.On,
            )
    _cache[key] = result
    return QIcon(result)


def clear_cache():
    """Drop cached icons (call after a theme or accent change)."""
    _cache.clear()
