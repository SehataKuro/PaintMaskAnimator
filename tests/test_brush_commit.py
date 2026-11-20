import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QFocusEvent, QMouseEvent, QPointingDevice, QTabletEvent
from PySide6.QtWidgets import QApplication

from dense_fixtures import hold
from paintmaskanimator import constants, project_io
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.models import make_frame
from paintmaskanimator.pma_image import PmaImage


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 64)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 64)
    window = MainWindow()
    canvas = window.canvas
    canvas.frames = [make_frame(["A", "B"]) for _ in range(2)]
    canvas.current_frame = canvas.active_layer_index = 0
    canvas.set_tool("brush")
    canvas.color_mode = "main"
    canvas.main_color = QColor("blue")
    canvas.pen_size = 5
    canvas.set_brush_stabilizer(0)
    try:
        yield window
    finally:
        window.project.mark_saved()
        window.close()
        window.deleteLater()
        app.processEvents()


POINT = constants.OUTSIDE_MARGIN + 20


def mouse(canvas, event_type, buttons, button=Qt.MouseButton.LeftButton,
          modifiers=Qt.KeyboardModifier.NoModifier):
    pos = canvas.canvas_to_widget(QPointF(POINT, POINT))
    event = QMouseEvent(
        event_type, pos, canvas.mapToGlobal(pos), button, buttons, modifiers,
    )
    QApplication.sendEvent(canvas, event)


def tablet(canvas, event_type, buttons):
    pos = canvas.canvas_to_widget(QPointF(POINT, POINT))
    event = QTabletEvent(
        event_type, QPointingDevice.primaryPointingDevice(), pos, canvas.mapToGlobal(pos),
        .8, 0, 0, 0, 0, 0, Qt.KeyboardModifier.NoModifier,
        Qt.MouseButton.LeftButton, buttons,
    )
    QApplication.sendEvent(canvas, event)


@pytest.mark.parametrize("end", [
    "mouse", "tablet", "tablet_leave", "tool", "frame", "layer",
    "focus", "deactivate", "application_deactivate", "temporary_tool",
    "middle", "shift_line", "undo", "save",
])
def test_stroke_commits_once_through_input_and_command_paths(window, monkeypatch, tmp_path, end):
    canvas = window.canvas
    pointer = tablet if end.startswith("tablet") else mouse
    press = QEvent.Type.TabletPress if pointer is tablet else QEvent.Type.MouseButtonPress
    pointer(canvas, press, Qt.MouseButton.LeftButton)
    image = canvas.active_layer.image
    assert image.read_qimage(POINT, POINT, 1, 1).pixelColor(0, 0).alpha() == 0
    writes = []
    write = PmaImage.write_qimage
    canvas.cellChanged.connect(lambda frame, index: writes.append(("notify", frame, index)))

    def track(target, patch, x, y):
        writes.append(("write", target))
        write(target, patch, x, y)

    monkeypatch.setattr(PmaImage, "write_qimage", track)
    if end == "mouse":
        mouse(canvas, QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)
    elif end == "tablet":
        tablet(canvas, QEvent.Type.TabletRelease, Qt.MouseButton.NoButton)
    elif end == "tablet_leave":
        QApplication.sendEvent(QApplication.instance(), QEvent(QEvent.Type.TabletLeaveProximity))
    elif end == "tool":
        canvas.set_tool("hand")
    elif end == "frame":
        canvas.current_frame = 1
    elif end == "layer":
        canvas.active_layer_index = 1
    elif end == "focus":
        QApplication.sendEvent(canvas, QFocusEvent(QEvent.Type.FocusOut))
    elif end == "deactivate":
        QApplication.sendEvent(window, QEvent(QEvent.Type.WindowDeactivate))
    elif end == "application_deactivate":
        QApplication.sendEvent(QApplication.instance(), QEvent(QEvent.Type.ApplicationDeactivate))
    elif end == "temporary_tool":
        window._held_canvas_shortcut_tokens.add("Space")
        window._update_canvas_hold_operation()
        assert canvas.temp_tool == "hand"
    elif end == "middle":
        middle = Qt.MouseButton.MiddleButton
        mouse(canvas, QEvent.Type.MouseButtonPress, middle | Qt.MouseButton.LeftButton, middle)
        assert canvas.middle_hand
    elif end == "shift_line":
        mouse(canvas, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton,
              modifiers=Qt.KeyboardModifier.ShiftModifier)
        assert canvas.effective_tool() == "line"
    elif end == "undo":
        window.used_color.undo()
        assert image.read_qimage(POINT, POINT, 1, 1).pixelColor(0, 0).alpha() == 0
        canvas.redo()
    elif end == "save":
        path = tmp_path / "brush.pman"
        assert window.project.write(path)
        saved = project_io.load_project(path).document.frames[0].layers[0].image
        assert saved.read_qimage(POINT, POINT, 1, 1).pixelColor(0, 0) == QColor("blue")
    assert writes[:2] == [("write", image), ("notify", 0, 0)]
    assert [entry for entry in writes if entry[0] == "write"] == [("write", image)]
    assert canvas._stroke_work_image is None
    if end not in ("middle", "shift_line"):
        assert not canvas.drawing
    assert image.read_qimage(POINT, POINT, 1, 1).pixelColor(0, 0) == QColor("blue")


def test_commit_and_tile_history_keep_the_shared_model_image(window, monkeypatch):
    canvas = window.canvas
    canvas.timeline_mode = "sheet"
    canvas.ensure_editable_key()
    hold(canvas, 0, 2)
    model = canvas.active_layer.image
    shared = canvas.frames[1].layers[0]
    assert shared.image is model

    to_qimage = PmaImage.to_qimage
    monkeypatch.setattr(PmaImage, "to_qimage", lambda image: to_qimage(image).copy())

    def pixel():
        assert shared.image is model
        return model.read_qimage(POINT, POINT, 1, 1).pixelColor(0, 0)

    mouse(canvas, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton)
    assert pixel().alpha() == 0
    mouse(canvas, QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)
    assert pixel() == QColor("blue")
    canvas.undo()
    assert pixel().alpha() == 0
    canvas.redo()
    assert pixel() == QColor("blue")


def test_delete_layer_commits_before_capturing_undo(window):
    canvas = window.canvas
    canvas.active_layer_index = 1
    mouse(canvas, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton)
    canvas.delete_layer()
    assert len(canvas.layers) == 1
    assert not canvas.drawing
    canvas.undo()
    restored = canvas.frames[0].layers[1].image
    assert restored.read_qimage(POINT, POINT, 1, 1).pixelColor(0, 0) == QColor("blue")


def test_brush_selection_clip_keeps_working_and_committed_pixels_equal(window):
    canvas = window.canvas
    canvas.selection_polygon = [
        QPointF(POINT - 4, POINT - 4), QPointF(POINT + 1, POINT - 4),
        QPointF(POINT + 1, POINT + 4), QPointF(POINT - 4, POINT + 4),
    ]
    mouse(canvas, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton)
    work = canvas._stroke_work_image
    assert work.pixelColor(POINT, POINT) == QColor("blue")
    assert work.pixelColor(POINT + 1, POINT).alpha() == 0
    mouse(canvas, QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)
    model = canvas.active_layer.image
    assert model.read_qimage(0, 0, model.width, model.height) == work


def test_commit_preserves_brush_pixels_in_high_dpi_image(window):
    canvas = window.canvas
    canvas.ensure_editable_key()
    layer = canvas.active_layer
    original = layer.image.read_qimage(0, 0, layer.image.width, layer.image.height)
    original.setDevicePixelRatio(2)
    layer.image = original
    canvas._begin_opaque_brush_stroke()
    position = QPointF(POINT, POINT)
    canvas.draw_line(position, position, 1.0)
    work = canvas._stroke_work_image
    assert work.pixelColor(POINT * 2, POINT * 2) == QColor("blue")
    canvas._finish_opaque_brush_stroke()
    model = layer.image
    assert model.read_qimage(0, 0, model.width, model.height) == work
