"""UI chrome: theme-aware line icons and the draft-layer indicator."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import icons, theme  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _has_ink(pixmap):
    image = pixmap.toImage()
    return any(
        image.pixelColor(x, y).alpha() > 0
        for x in range(image.width())
        for y in range(image.height())
    )


def test_every_icon_renders_visible_pixels(qapp):
    for name in icons.names():
        assert _has_ink(icons.pixmap(name, 24)), name


def test_icon_follows_requested_colour(qapp):
    image = icons.pixmap("minus", 24, "#ff0000").toImage()
    inked = [
        image.pixelColor(x, y)
        for x in range(24) for y in range(24)
        if image.pixelColor(x, y).alpha() > 0
    ]
    assert inked
    # Antialiased edges vary in alpha, but the hue is always the requested red.
    assert all(
        color.red() > 200 and color.green() < 40 and color.blue() < 40
        for color in inked
    )


def test_cached_icon_survives_being_handed_out(qapp):
    first = icons.icon("close", theme.palette()["text_muted"])
    del first
    again = icons.icon("close", theme.palette()["text_muted"])
    assert not again.isNull()


def test_draft_layer_rows_are_marked_in_timeline(qapp):
    window = MainWindow()
    try:
        window.timeline.addLayerRequested.emit()
        frame = window.canvas.frames[window.canvas.current_frame]
        assert len(frame.layers) == 2
        window.layers.set_draft_rows([0])
        timeline = window.timeline
        drafts = [
            timeline.layer_list.item(row).data(
                timeline.layer_list.itemDelegate().DRAFT_ROLE
            )
            for row in range(timeline.layer_list.count())
        ]
        draft_rows = {row for row, draft in enumerate(drafts) if draft}
        assert len(draft_rows) == 1
        # The timeline cells for the same visual row get the hatch overlay.
        assert timeline.table._draft_rows == draft_rows
        layer_index = timeline.layer_list.count() - 1 - next(iter(draft_rows))
        assert frame.layers[layer_index].is_draft
    finally:
        window.close()
        window.deleteLater()


def test_titled_tooltip_splits_bold_title_from_body(qapp):
    from paintmaskanimator import tooltip

    popup = tooltip.ToolTipPopup()
    popup.set_text(tooltip.titled("空フレームを追加", "説明<1>\n2行目"))
    assert popup.title.text() == "空フレームを追加"
    assert not popup.title.isHidden()
    # The detail is escaped (no stray markup) and keeps its line break.
    assert "&lt;1&gt;" in popup.body.text()
    assert "<br>" in popup.body.text()


def test_plain_tooltip_has_no_title(qapp):
    from paintmaskanimator import tooltip

    popup = tooltip.ToolTipPopup()
    popup.set_text("再生／停止")
    assert popup.title.isHidden()
    assert popup.body.text() == "再生／停止"


def test_system_theme_resolves_to_a_palette(qapp, monkeypatch):
    monkeypatch.setattr(theme, "current_theme", lambda: theme.SYSTEM)
    assert theme.resolved_theme() in theme.PALETTES
    assert theme.palette()["window"]
    assert theme.SYSTEM in theme.available_themes()


def test_system_accent_setting_yields_a_colour(qapp, monkeypatch):
    monkeypatch.setattr(theme, "accent_setting", lambda: theme.SYSTEM)
    assert QColor(theme.current_accent()).isValid()
