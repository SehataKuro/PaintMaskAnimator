"""色のプレビュー、使用色の背景色の表示、最近使用したファイル。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox  # noqa: E402

from paintmaskanimator import main_window_project_io  # noqa: E402
from paintmaskanimator.color_picker_dialog import ColorPickerDialog  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


# --- プレビュー -----------------------------------------------------------
def test_preview_applies_live_and_cancel_restores(qapp):
    seen = []
    dialog = ColorPickerDialog(QColor("#112233"), preview=seen.append)
    try:
        assert dialog.preview_check.isVisibleTo(dialog)
        dialog.set_color(QColor("#ff0000"))
        assert seen[-1] == QColor("#ff0000"), "チェックが入っていればその場で反映"
        dialog.reject()
        assert seen[-1] == QColor("#112233"), "キャンセルで元の色に戻す"
    finally:
        dialog.deleteLater()


def test_preview_off_applies_only_on_ok(qapp):
    seen = []
    dialog = ColorPickerDialog(QColor("#112233"), preview=seen.append)
    try:
        dialog.preview_check.setChecked(False)
        dialog.set_color(QColor("#00ff00"))
        assert seen == [], "チェックを外していれば反映しない"
        dialog.accept()
        assert seen == [QColor("#00ff00")], "OK で決めた色を1回だけ反映"
    finally:
        dialog.deleteLater()


def test_no_preview_callback_hides_the_checkbox(qapp):
    dialog = ColorPickerDialog(QColor("red"))
    try:
        assert not dialog.preview_check.isVisibleTo(dialog)
    finally:
        dialog.deleteLater()


def test_background_preview_then_cancel_restores_the_canvas(window, monkeypatch):
    original = QColor(window.canvas.transparent_display_color)

    def fake_get_color(initial=None, parent=None, title=None, preview=None):
        assert preview is not None
        preview(QColor("#204080"))
        assert window.canvas.transparent_display_color == QColor("#204080")
        assert window.palette.background_display_color == QColor("#204080")
        preview(QColor(initial))  # キャンセル時にダイアログが元の色へ戻す
        return QColor()

    monkeypatch.setattr(ColorPickerDialog, "get_color", staticmethod(fake_get_color))
    window.colors.choose_background_color()
    assert window.canvas.transparent_display_color == original
    assert window.canvas.color_mode != "transparent" or original == QColor("white")


def test_main_colour_preview_does_not_switch_mode_until_ok(window, monkeypatch):
    window.colors.set_color_mode("main")

    def fake_get_color(initial=None, parent=None, title=None, preview=None):
        assert preview is not None
        preview(QColor("#abcdef"))
        assert window.canvas.sub_color == QColor("#abcdef")
        assert window.canvas.color_mode == "main", "プレビュー中は描画色のモードを変えない"
        return QColor("#abcdef")

    monkeypatch.setattr(ColorPickerDialog, "get_color", staticmethod(fake_get_color))
    window.colors.choose_color("sub")
    assert window.canvas.color_mode == "sub"


# --- 使用色の背景色 -------------------------------------------------------
def test_used_colour_background_row_follows_the_display_colour(window, monkeypatch):
    panel = window.palette
    panel.set_colors([QColor("#112233")])
    monkeypatch.setattr(
        ColorPickerDialog, "get_color",
        staticmethod(lambda *args, **kwargs: QColor("#335577")),
    )
    window.colors.choose_background_color()
    button = panel.source_buttons[panel.background_rgb]
    assert "#335577" in button.text()
    assert "#335577" in button.styleSheet().lower() or "#335577" in button.styleSheet()


# --- 最近使用したファイル -------------------------------------------------
def test_recent_projects_are_remembered_newest_first(window, tmp_path, monkeypatch):
    project = window.project
    first = tmp_path / "a.pman"
    second = tmp_path / "b.pman"
    assert project.write(first)
    assert project.write(second)
    assert project.open(first)
    recent = project.recent_projects()
    assert recent[:2] == [str(first.resolve()), str(second.resolve())]

    menu = QMenu()
    project.fill_recent_menu(menu)
    labels = [action.text() for action in menu.actions() if action.text()]
    assert any("a.pman" in label for label in labels)
    assert any("b.pman" in label for label in labels)

    # 見つからないファイルは知らせて一覧から外す。
    second.unlink()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    assert not project.open_recent(str(second.resolve()))
    assert str(second.resolve()) not in project.recent_projects()

    project.clear_recent()
    assert project.recent_projects() == []
    project.fill_recent_menu(menu)
    assert not menu.actions()[0].isEnabled()


def test_recent_list_is_capped(window, tmp_path):
    project = window.project
    for index in range(main_window_project_io.RECENT_PROJECTS_LIMIT + 3):
        project.remember_recent(tmp_path / f"p{index}.pman")
    recent = project.recent_projects()
    assert len(recent) == main_window_project_io.RECENT_PROJECTS_LIMIT
    assert recent[0].endswith("p12.pman")
