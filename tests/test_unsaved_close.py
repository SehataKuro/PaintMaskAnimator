import pytest
from PySide6.QtGui import QCloseEvent, QColor, QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.main_window_project_io import ProjectIOController


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    yield win
    win.project.mark_saved()
    win.close()
    assert app is not None


def test_title_tracks_edit_revert_and_save(window, tmp_path):
    project = window.project
    assert not window.windowTitle().endswith(" *")
    original = QImage(window.canvas.active_layer.image)
    window.canvas.active_layer.image.setPixelColor(1, 1, QColor("red"))
    window._title_timer.timeout.emit()
    assert window.windowTitle().endswith(" *")
    window.canvas.active_layer.image = original
    # QImage copies retain their cache key until modified.
    project.update_title()
    assert not window.windowTitle().endswith(" *")
    window.canvas.active_layer.name = "edited"
    project.update_title()
    assert window.windowTitle().endswith(" *")
    assert project.write(tmp_path / "saved.pman")
    assert window.windowTitle().endswith("saved.pman")


@pytest.mark.parametrize("choice,saved,expected", [
    ("save", True, True), ("save", False, False),
    ("discard", False, True), ("cancel", False, False),
    (None, False, False),
])
def test_close_choices(window, monkeypatch, choice, saved, expected):
    controller = ProjectIOController(window)
    monkeypatch.setattr(controller, "has_unsaved_changes", lambda: True)
    calls = []
    monkeypatch.setattr(controller, "save", lambda: calls.append("save") or saved)
    roles = {"save": QMessageBox.ButtonRole.AcceptRole,
             "discard": QMessageBox.ButtonRole.DestructiveRole,
             "cancel": QMessageBox.ButtonRole.RejectRole}
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: next(
        (b for b in self.buttons() if self.buttonRole(b) == roles.get(choice)), None
    ))
    assert controller.confirm_save_before_close() is expected
    assert calls == (["save"] if choice == "save" else [])


def test_cancel_preserves_running_window(window, monkeypatch):
    # The prohibition applies to the cancelled user action, not the test's
    # subsequent real shutdown.
    with monkeypatch.context() as cancelled:
        cancelled.setattr(window.project, "confirm_save_before_close", lambda: False)
        cancelled.setattr(window.autosave, "clear", lambda: pytest.fail("must retain autosave"))
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert not getattr(window, "_closing", False)
        assert window._title_timer.isActive()
