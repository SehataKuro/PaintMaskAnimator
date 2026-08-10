from PySide6.QtGui import QColor, QPalette

from paintmaskanimator import theme


def _contrast_ratio(first, second):
    def luminance(value):
        channels = []
        for channel in (value.redF(), value.greenF(), value.blueF()):
            channels.append(
                channel / 12.92
                if channel <= 0.04045
                else ((channel + 0.055) / 1.055) ** 2.4
            )
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_every_palette_has_the_same_semantic_colors():
    expected = set(theme.PALETTES[theme.DEFAULT_THEME])
    assert all(set(colors) == expected for colors in theme.PALETTES.values())


def test_dark_palette_text_contrast_is_readable(monkeypatch):
    monkeypatch.setattr(theme, "current_accent", lambda: theme.DEFAULT_ACCENT)
    colors = theme.palette("dark")
    assert _contrast_ratio(QColor(colors["text"]), QColor(colors["window"])) >= 7
    assert _contrast_ratio(QColor(colors["text"]), QColor(colors["surface"])) >= 7
    assert _contrast_ratio(QColor(colors["text_muted"]), QColor(colors["window"])) >= 4.5
    for state in ("key", "hold", "sheet_key", "sheet_hold", "blank", "uncreated"):
        assert _contrast_ratio(
            QColor(colors[f"timeline_{state}_text"]),
            QColor(colors[f"timeline_{state}"]),
        ) >= 4.5


def test_qpalette_covers_unstyled_and_disabled_widgets(monkeypatch):
    monkeypatch.setattr(theme, "current_accent", lambda: theme.DEFAULT_ACCENT)
    colors = theme.palette("dark")
    palette = theme.build_qpalette("dark")
    assert palette.color(QPalette.ColorRole.Window) == QColor(colors["window"])
    assert palette.color(QPalette.ColorRole.Base) == QColor(colors["surface"])
    assert palette.color(QPalette.ColorRole.Mid) == QColor(colors["border"])
    assert palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text) != QColor(colors["text"])


def test_custom_accent_always_gets_legible_text(monkeypatch):
    monkeypatch.setattr(theme, "current_accent", lambda: "#fff06a")
    assert theme.palette("dark")["accent_text"] == "#000000"
    monkeypatch.setattr(theme, "current_accent", lambda: "#17345f")
    assert theme.palette("light")["accent_text"] == "#ffffff"


def test_stylesheet_covers_popup_views_headers_and_interaction_states(monkeypatch):
    monkeypatch.setattr(theme, "current_accent", lambda: theme.DEFAULT_ACCENT)
    stylesheet = theme.build_stylesheet("dark")
    for selector in (
        "QComboBox QAbstractItemView",
        "QHeaderView::section",
        "QToolButton:hover",
        "QLineEdit:disabled",
        "QProgressBar::chunk",
        "QTabWidget::pane",
    ):
        assert selector in stylesheet
