"""Preferences: stored values, clamping, and the window applying them live."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import canvas_undo, config, i18n, preferences, theme  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    from paintmaskanimator.main_window import MainWindow

    win = MainWindow()
    yield win
    win.close()
    win.deleteLater()
    qapp.processEvents()


@pytest.fixture
def restore_undo_limits():
    saved = (canvas_undo.MAX_UNDO, canvas_undo.MAX_UNDO_BYTES)
    yield
    canvas_undo.set_undo_limits(*saved)


def test_defaults_match_the_former_constants():
    assert preferences.autosave_enabled() is True
    assert preferences.autosave_interval_minutes() == 3
    assert preferences.undo_max_steps() == 30
    assert preferences.undo_max_memory_mb() == 768
    assert preferences.new_canvas_size() == (1280, 720)


def test_hand_edited_values_are_clamped():
    config.set_value(preferences.AUTOSAVE_INTERVAL_KEY, 0)
    config.set_value(preferences.UNDO_STEPS_KEY, "lots")
    config.set_value(preferences.UNDO_MEMORY_KEY, 10**9)
    config.set_value(preferences.NEW_CANVAS_SIZE_KEY, [1, 99999])
    assert preferences.autosave_interval_minutes() == 1
    assert preferences.undo_max_steps() == 30
    assert preferences.undo_max_memory_mb() == preferences.UNDO_MEMORY_RANGE[1]
    assert preferences.new_canvas_size() == (64, preferences.CANVAS_SIZE_RANGE[1])
    config.set_value(preferences.NEW_CANVAS_SIZE_KEY, "1280x720")
    assert preferences.new_canvas_size() == (1280, 720)


def test_undo_limits_trim_the_stack(restore_undo_limits):
    class Entry:
        nbytes = 1

    canvas_undo.set_undo_limits(5, 10**9)
    stack = [Entry() for _ in range(12)]
    canvas_undo.trim_undo_stack(stack)
    assert len(stack) == 5


def test_preferences_action_uses_the_mac_app_menu_slot(window):
    action = window.a_preferences
    assert action.menuRole() == QAction.MenuRole.PreferencesRole
    assert action.shortcut().toString() == "Ctrl+,"
    menu_titles = [a.text() for a in window.menuBar().actions()]
    assert "表示" not in menu_titles


def test_window_is_reused(window):
    window.show_preferences()
    first = window.preferences_dialog
    window.show_preferences()
    assert window.preferences_dialog is first
    assert not first.isModal()
    first.close()


def test_autosave_settings_apply_to_the_running_timer(window):
    window.show_preferences()
    dialog = window.preferences_dialog
    dialog.autosave_interval.setValue(10)
    assert window._autosave_timer.interval() == 10 * 60 * 1000
    assert window._autosave_timer.isActive()
    dialog.autosave_enabled.setChecked(False)
    assert not window._autosave_timer.isActive()
    assert not dialog.autosave_interval.isEnabled()
    assert preferences.autosave_enabled() is False
    assert preferences.autosave_interval_minutes() == 10
    dialog.close()


def test_undo_and_canvas_settings_are_saved(window, restore_undo_limits):
    window.show_preferences()
    dialog = window.preferences_dialog
    dialog.undo_steps.setValue(80)
    dialog.undo_memory.setValue(2048)
    assert canvas_undo.MAX_UNDO == 80
    assert canvas_undo.MAX_UNDO_BYTES == 2048 * 1024 * 1024
    dialog.canvas_width.setValue(1920)
    dialog.canvas_height.setValue(1080)
    assert preferences.new_canvas_size() == (1920, 1080)
    dialog.close()


def test_language_is_saved_for_next_launch(window):
    window.show_preferences()
    dialog = window.preferences_dialog
    dialog.language.setCurrentIndex(dialog.language.findData("ja"))
    assert i18n.preferred_language() == "ja"
    dialog.close()


def test_theme_and_accent_apply_immediately(window, monkeypatch):
    # Record instead of restyling the whole app: with the windows earlier
    # tests leave behind, app.setStyleSheet() takes minutes in a full run.
    applied = []
    monkeypatch.setattr(window, "set_theme", applied.append)

    def fake_set_accent(value):
        applied.append(value)
        config.set_value(theme.ACCENT_KEY, value)

    monkeypatch.setattr(window.colors, "set_accent", fake_set_accent)
    window.show_preferences()
    dialog = window.preferences_dialog
    dialog.theme.setCurrentIndex(dialog.theme.findData("dark"))
    assert applied == ["dark"]

    index = dialog.accent.findData("#0d9488")
    dialog.accent.setCurrentIndex(index)
    dialog.accent.activated.emit(index)
    assert applied == ["dark", "#0d9488"]
    assert dialog.accent.currentData() == "#0d9488"
    dialog.close()


def test_custom_accent_is_listed_and_selected(window, monkeypatch):
    config.set_value(theme.ACCENT_KEY, "#123456")
    window.show_preferences()
    dialog = window.preferences_dialog
    assert dialog.accent.currentData() == "#123456"
    dialog.close()


# --- 筆圧プリセット -----------------------------------------------------------


def test_pressure_presets_default_add_rename_remove():
    from paintmaskanimator import pressure_settings as ps

    assert ps.preset_names() == ["標準"]
    assert ps.global_settings() == ps.normalize(ps.DEFAULT_SETTINGS)
    name = ps.add_preset("標準", {"minimum": 0.4})
    assert name == "標準 2"
    ps.set_active_preset(name)
    assert ps.global_settings()["minimum"] == 0.4
    assert not ps.rename_preset(name, "標準")
    assert ps.rename_preset(name, "Gペン")
    assert ps.active_preset_name() == "Gペン"
    assert ps.remove_preset("Gペン")
    assert ps.active_preset_name() == "標準"
    assert not ps.remove_preset("標準")


def test_pressure_values_are_clamped():
    from paintmaskanimator import pressure_settings as ps

    settings = ps.normalize({"minimum": -3, "maximum": 99, "points": [[2, 2]]})
    assert settings["minimum"] == ps.MINIMUM_RANGE[0]
    assert settings["maximum"] == ps.MAXIMUM_RANGE[1]
    assert settings["points"] == ps.DEFAULT_SETTINGS["points"]


def test_brush_follows_global_preset_or_its_own(window):
    from paintmaskanimator import pressure_settings as ps

    ps.update_preset("標準", {"enabled": True, "minimum": 0.3, "maximum": 1.5})
    ps.set_brush_settings({"enabled": False, "minimum": 0.2, "maximum": 0.8})
    window.apply_pressure_settings()
    assert (window.canvas.pressure_min, window.canvas.pressure_max) == (0.3, 1.5)
    ps.set_brush_uses_global(False)
    window.apply_pressure_settings()
    assert window.canvas.pressure_enabled is False
    assert window.canvas.pressure_min == 0.2


def test_preferences_pressure_page_edits_the_active_preset(window):
    from paintmaskanimator import pressure_settings as ps

    window.show_preferences("pressure")
    dialog = window.preferences_dialog
    assert dialog.pages.currentWidget() is dialog.pressure_editor.parentWidget()
    dialog.pressure_editor.minimum.setValue(40)
    assert ps.global_settings()["minimum"] == 0.4
    assert window.canvas.pressure_min == 0.4

    dialog._add_pressure_preset()
    assert ps.active_preset_name() == "新しいプリセット"
    dialog.pressure_editor.maximum.setValue(200)
    assert ps.preset("標準")["maximum"] == 1.0
    assert window.canvas.pressure_max == 2.0

    dialog.pressure_preset.setCurrentText("標準")
    assert window.canvas.pressure_max == 1.0
    assert dialog.pressure_editor.minimum.value() == 40
    dialog.close()


def test_brush_pressure_dialog_switches_source(qapp):
    from paintmaskanimator import pressure_settings as ps
    from paintmaskanimator.pressure import PressureDialog

    ps.add_preset("強め", {"minimum": 0.5})
    dialog = PressureDialog(
        True, "標準", ps.preset_names(), ps.preset, {"minimum": 0.2}
    )
    assert not dialog.editor.isEnabled()
    dialog.preset.setCurrentText("強め")
    assert dialog.editor.minimum.value() == 50
    dialog.use_brush.setChecked(True)
    assert dialog.editor.isEnabled()
    assert dialog.editor.minimum.value() == 20
    dialog.editor.minimum.setValue(35)
    dialog.use_global.setChecked(True)
    assert dialog.brush_settings()["minimum"] == 0.35
    assert dialog.uses_global() and dialog.active_preset() == "強め"


def test_opening_a_project_keeps_the_users_pressure(window, tmp_path):
    from paintmaskanimator import pressure_settings as ps

    path = tmp_path / "p.pman"
    window.canvas.pressure_min = 0.9
    window.project.write(str(path))
    ps.update_preset("標準", {"minimum": 0.3})
    window.apply_pressure_settings()
    window.project.open(str(path))
    assert window.canvas.pressure_min == 0.3


# --- レイアウト用紙 -----------------------------------------------------------


def _paper_png(tmp_path, width=320, height=200):
    from PySide6.QtGui import QColor, QImage

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(200, 60, 60))
    path = tmp_path / "layout_A4.png"
    assert image.save(str(path))
    return path


def test_layout_papers_are_copied_and_managed(qapp, tmp_path):
    from paintmaskanimator import layout_paper

    source = _paper_png(tmp_path)
    entry = layout_paper.add_paper(source)
    source.unlink()
    assert layout_paper.papers() == [entry]
    assert entry["name"] == "layout_A4"
    assert layout_paper.image_size(entry) == (320, 200)
    layout_paper.rename_paper(entry["id"], "A4 横")
    assert layout_paper.paper(entry["id"])["name"] == "A4 横"
    layout_paper.remove_paper(entry["id"])
    assert layout_paper.papers() == []
    assert not layout_paper.image_path(entry).exists()
    bogus = tmp_path / "not_an_image.png"
    bogus.write_text("x")
    assert layout_paper.add_paper(bogus) is None


def test_new_document_dialog_fits_canvas_to_paper(qapp, tmp_path):
    from paintmaskanimator import layout_paper
    from paintmaskanimator.new_document_dialog import NewDocumentDialog

    entry = layout_paper.add_paper(_paper_png(tmp_path))
    dialog = NewDocumentDialog(1280, 720)
    assert dialog.paper_id() is None
    assert dialog.w.isEnabled() and not dialog.fit_to_paper.isEnabled()
    dialog.paper.setCurrentIndex(dialog.paper.findData(entry["id"]))
    assert dialog.values() == (320, 200)
    assert not dialog.w.isEnabled()
    dialog.fit_to_paper.setChecked(False)
    assert dialog.values() == (1280, 720)
    assert dialog.w.isEnabled()


def test_layout_paper_goes_under_all_layers_as_draft(window, tmp_path):
    from paintmaskanimator import constants, layout_paper

    saved = (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)
    try:
        entry = layout_paper.add_paper(_paper_png(tmp_path))
        window.replace_doc(320, 200)
        window.insert_layout_paper(entry)
        layers = window.canvas.frames[0].layers
        assert [layer.name for layer in layers] == ["レイアウト用紙", "A"]
        assert layers[0].is_draft and layers[0].has_content
        assert layers[0].image.pixelColor(10, 10).red() == 200
        assert window.canvas.active_layer_index == 1
        window.canvas.add_frame(dup=False)
        composite = window.canvas.composite(len(window.canvas.frames) - 1, True)
        assert composite.pixelColor(10, 10).red() == 200
    finally:
        constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = saved


# --- ワークスペース -----------------------------------------------------------


def _menu_workspaces(window):
    names = window.workspace.names()
    return [a.text() for a in window.workspace_menu.actions() if a.text() in names]


def test_workspaces_can_be_reordered_renamed_and_deleted(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    for name in ("線画", "彩色", "タイミング"):
        assert window.workspace.save(name)
    window.show_preferences("workspaces")
    dialog = window.preferences_dialog
    assert dialog._listed_workspaces() == ["線画", "彩色", "タイミング"]
    assert dialog.workspaces.item(2).text() == "タイミング（使用中）"

    dialog.workspaces.setCurrentRow(2)
    dialog._move_workspace(-1)
    dialog._move_workspace(-1)
    assert window.workspace.names() == ["タイミング", "線画", "彩色"]
    assert _menu_workspaces(window) == ["タイミング", "線画", "彩色"]
    assert dialog.workspaces.currentRow() == 0
    assert not dialog.workspace_up.isEnabled()

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("撮影", True)))
    dialog._rename_workspace()
    assert window.workspace.names() == ["撮影", "線画", "彩色"]
    assert config.get_value("active_workspace") == "撮影"

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("線画", True)))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    dialog._rename_workspace()
    assert window.workspace.names() == ["撮影", "線画", "彩色"]

    dialog.workspaces.setCurrentRow(1)
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    dialog._delete_workspace()
    assert "線画" in window.workspace.names()
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    dialog._delete_workspace()
    assert window.workspace.names() == ["撮影", "彩色"]
    assert dialog._listed_workspaces() == ["撮影", "彩色"]
    dialog.close()


def test_workspace_menu_links_to_preferences(window):
    window.workspace.save("線画")
    manage = next(
        a for a in window.workspace_menu.actions() if a.text() == "ワークスペースを管理…"
    )
    manage.trigger()
    dialog = window.preferences_dialog
    assert dialog.pages.currentWidget() is dialog.workspaces.parentWidget()
    dialog.close()
