import pytest
from PySide6.QtGui import QCloseEvent, QColor, QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from image_edit_helpers import edit_qimage
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
    original = window.canvas.active_layer.image.copy()
    with edit_qimage(window.canvas.active_layer.image) as patch:
        patch.setPixelColor(1, 1, QColor("red"))
    window._title_timer.timeout.emit()
    assert window.windowTitle().endswith(" *")
    window.canvas.active_layer.image = original
    # Model copies retain their generation until modified.
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


def test_signature_uses_generations_across_writes_and_history(window, monkeypatch):
    import numpy as np

    from paintmaskanimator.pma_image import PmaImage

    canvas = window.canvas
    project = window.project
    canvas.ensure_editable_key()
    layer = canvas.active_layer
    layer.initial_image = layer.image.copy()
    original = project._content_signature()
    layer.image = layer.image.copy()
    layer.initial_image = layer.initial_image.copy()
    layer.image.write_rgba(np.empty((0, 1, 4), dtype=np.uint8))
    assert project._content_signature() == original

    to_qimage = PmaImage.to_qimage
    with monkeypatch.context() as patch:
        patch.setattr(PmaImage, "to_qimage", lambda image: to_qimage(image).copy())
        assert project._content_signature() == original
    layer.initial_image = PmaImage.from_qimage(layer.initial_image.to_qimage())
    assert project._content_signature() != original
    original = project._content_signature()

    canvas.push_layer_undo()
    with edit_qimage(layer.image) as patch:
        patch.setPixelColor(0, 0, QColor("red"))
    edited = project._content_signature()
    assert edited != original
    canvas.undo()
    undone = project._content_signature()
    assert undone != edited
    assert canvas.active_layer.image.to_qimage().pixelColor(0, 0).alpha() == 0
    canvas.redo()
    assert project._content_signature() != undone
    assert canvas.active_layer.image.to_qimage().pixelColor(0, 0) == QColor("red")


def test_signature_and_png_cache_distinguish_qt_keys_from_generations(monkeypatch):
    from types import SimpleNamespace

    from paintmaskanimator.models import Frame
    from paintmaskanimator.pma_image import PmaImage
    from paintmaskanimator.project_io import PngCache

    image = PmaImage.blank(1, 1)
    qt_image = QImage(image.to_qimage())
    monkeypatch.setattr(QImage, "cacheKey", lambda self: image.generation)

    # Exercise the generic dataclass field in the signature with both types.
    from dataclasses import dataclass

    @dataclass
    class Slot:
        artwork: object
        has_content = False
        sequence_only = False
        sequence_number = None

    slot = Slot(image)
    slot._track = SimpleNamespace(cells={})
    slot._cell = SimpleNamespace(image=image, initial_image=None, cell_name=None)
    canvas = SimpleNamespace(frames=[Frame([slot])], _document=SimpleNamespace(tracks=[]))
    project = ProjectIOController(SimpleNamespace(canvas=canvas, build_project_metadata=dict))
    original = project._content_signature()
    slot.artwork = qt_image
    assert project._content_signature() != original

    cache = PngCache()
    cache.remember(image, b"model")
    cache.remember(qt_image, b"qt")
    assert set(cache.encode_many([image, qt_image]).values()) == {b"model", b"qt"}
