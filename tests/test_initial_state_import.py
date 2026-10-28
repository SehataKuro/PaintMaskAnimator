"""Import an external line-art cut without replacing painted cels."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from paintmaskanimator import constants, project_io
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.models import Frame, Layer
from paintmaskanimator.undo_entries import InitialStateUndo


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def image(color, size=(16, 16)):
    result = QImage(*size, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(color))
    return result


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 16)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 16)
    result = MainWindow()
    result.canvas.frames = [
        Frame([Layer("A", image("red"), has_content=True, sequence_number=2, exposure=2),
               Layer("B", image("blue"), has_content=True, sequence_number=1)]),
        Frame([Layer("A", image("transparent")), Layer("B", image("transparent"))]),
        Frame([Layer("A", image("red"), has_content=True, sequence_number=2),
               Layer("B", image("transparent"))]),
    ]
    result.canvas._sequence_archive = {(0, 3): Layer("A", image("green"), has_content=True, sequence_number=3)}
    result.canvas.frames[0].layers[1].initial_image = image("yellow")
    result.refresh_ui()
    yield result
    result.close()
    result.deleteLater()
    qapp.processEvents()


def write(root, relative, color="black", size=(16, 16)):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    assert image(color, size).save(str(path))
    return path


def test_import_matches_numbers_not_frame_positions_and_preserves_work(window, tmp_path):
    write(tmp_path, "a/A0002.png")
    write(tmp_path, "a/A0003.png", "white")
    write(tmp_path, "a/A9999.png", "blue")  # unused source must not add cells
    canvas = window.canvas
    canvas.current_frame = 2
    old_frames = canvas.frames
    old_keys = [(cell.sequence_number, cell.exposure, cell.image.cacheKey())
                for frame in canvas.frames for cell in frame.layers]
    assert window.initial_state.import_line_art(tmp_path) == 3
    assert canvas.frames is old_frames
    assert canvas.current_frame == 2
    assert [(cell.sequence_number, cell.exposure, cell.image.cacheKey())
            for frame in canvas.frames for cell in frame.layers] == old_keys
    assert canvas.frames[0].layers[0].initial_image.pixelColor(0, 0) == QColor("black")
    assert canvas.frames[2].layers[0].initial_image.cacheKey() == canvas.frames[0].layers[0].initial_image.cacheKey()
    assert canvas.frames[1].layers[0].initial_image is None
    assert canvas.frames[0].layers[1].initial_image.pixelColor(0, 0) == QColor("yellow")
    assert canvas._sequence_archive[(0, 3)].initial_image.pixelColor(0, 0) == QColor("white")
    assert isinstance(canvas.undo_stack[-1], InitialStateUndo)
    window.initial_state.set_compare(True)
    assert canvas.show_initial_state


def test_import_is_one_undo_and_redo_restores_previous_initials(window, tmp_path):
    write(tmp_path, "A/A2.png")
    write(tmp_path, "A/A3.png")
    canvas = window.canvas
    canvas.frames[0].layers[0].initial_image = image("magenta")
    before = len(canvas.undo_stack)
    assert window.initial_state.import_line_art(tmp_path) == 3
    assert len(canvas.undo_stack) == before + 1
    canvas.undo()
    assert canvas.frames[0].layers[0].initial_image.pixelColor(0, 0) == QColor("magenta")
    assert canvas.frames[2].layers[0].initial_image is None
    assert canvas._sequence_archive[(0, 3)].initial_image is None
    canvas.redo()
    assert canvas.frames[0].layers[0].initial_image.pixelColor(0, 0) == QColor("black")
    assert canvas._sequence_archive[(0, 3)].initial_image is not None
    assert canvas.frames[0].layers[0].image.pixelColor(0, 0) == QColor("red")


@pytest.mark.parametrize("failure", ["size", "broken", "missing_folder"])
def test_import_failure_is_atomic(window, tmp_path, monkeypatch, failure):
    write(tmp_path, "A/A2.png")
    if failure == "size":
        write(tmp_path, "A/A3.png", size=(32, 16))
    elif failure == "broken":
        (tmp_path / "A/A3.png").write_bytes(b"invalid png")
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args))
    before = len(window.canvas.undo_stack)
    root = tmp_path / "absent" if failure == "missing_folder" else tmp_path
    assert window.initial_state.import_line_art(root) == 0
    assert len(errors) == 1
    assert len(window.canvas.undo_stack) == before
    assert window.canvas.frames[0].layers[0].initial_image is None
    assert window.canvas.frames[2].layers[0].initial_image is None
    assert QApplication.overrideCursor() is None


def test_no_matches_is_a_noop_and_explains_matching(window, tmp_path, monkeypatch):
    write(tmp_path, "C/C2.png")
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args))
    before = len(window.canvas.undo_stack)
    assert window.initial_state.import_line_art(tmp_path) == 0
    assert len(window.canvas.undo_stack) == before
    assert messages and "セル番号" in messages[0][-1]


def test_dialog_and_menu_action(window, tmp_path, monkeypatch):
    write(tmp_path, "A/A2.png")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(tmp_path))
    action = next(action for action in window.menuBar().actions() if action.text() == "自動塗り")
    menu = action.menu()
    assert menu is not None
    assert window.a_import_initial_state in menu.actions()
    window.a_import_initial_state.trigger()
    assert window.canvas.frames[0].layers[0].initial_image is not None
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: "")
    assert window.initial_state.import_line_art_dialog() == 0


def test_sanitized_layer_name_and_top_level_cels(window, tmp_path):
    window.canvas.frames[0].layers[0].name = "A/B"
    write(tmp_path, "A_B0002.png")
    assert window.initial_state.import_line_art(tmp_path) == 1


def test_imported_initials_survive_project_save(window, tmp_path):
    write(tmp_path, "lines/A/A2.png")
    write(tmp_path, "lines/A/A3.png", "white")
    assert window.initial_state.import_line_art(tmp_path / "lines") == 3
    path = tmp_path / "initials.pman"
    assert window.project.write(path)
    metadata, frames, _w, _h = project_io.read_project_archive(path)
    assert frames[0].layers[0].image.pixelColor(0, 0) == QColor("red")
    assert frames[0].layers[0].initial_image.pixelColor(0, 0) == QColor("black")
    assert metadata["_loaded_sequence_archive"][(0, 3)].initial_image.pixelColor(0, 0) == QColor("white")


def test_archived_initial_is_retained_when_placing_cel_on_sheet(window, tmp_path):
    write(tmp_path, "A/A3.png", "white")
    assert window.initial_state.import_line_art(tmp_path) == 1
    window.canvas.timeline_mode = "sheet"
    window.timeline_ops.recall_sequence_number(1, 1, 3)
    placed = window.canvas.frames[1].layers[0]
    assert placed.sequence_number == 3
    assert placed.image.pixelColor(0, 0) == QColor("green")
    assert placed.initial_image.pixelColor(0, 0) == QColor("white")


def test_draft_paper_and_unnumbered_cels_are_not_matched_by_position(window, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    write(tmp_path, "A/A1.png")
    write(tmp_path, "A/A2.png")
    write(tmp_path, "B/B1.png")
    window.canvas.frames[0].layers[0].is_draft = True
    window.canvas.frames[0].layers[1].is_paper = True
    window.canvas.frames[2].layers[0].sequence_number = None
    assert window.initial_state.import_line_art(tmp_path) == 0
