"""原画の印（#24）：セルに付き、保存・Undo・番号の移動についていく。"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from paintmaskanimator import project_io
from paintmaskanimator.document import create_drawing
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.timeline import TimelineCellDelegate


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def prepare(window, count=3):
    canvas = window.canvas
    canvas._ensure_frame_count(count)
    for index in range(count):
        layer = canvas.frames[index].layers[0]
        image = layer.image.to_qimage()
        image.fill(QColor(0, 0, 0, 0))
        image.setPixelColor(5 + index, 5, QColor("black"))
        create_drawing(layer, image, number=index + 1)
    canvas._cell_structure_dirty = True
    window.refresh_ui()
    return canvas


def visual_row(window, layer_index=0):
    return len(window.canvas.layers) - 1 - layer_index


def test_toggling_marks_the_drawing_and_undo_restores_it(window):
    canvas = prepare(window)
    assert window.timeline_ops.toggle_genga(visual_row(window), 0)
    assert canvas.frames[0].layers[0].is_genga
    assert not canvas.frames[1].layers[0].is_genga

    canvas.undo()
    assert not canvas.frames[0].layers[0].is_genga
    canvas.redo()
    assert canvas.frames[0].layers[0].is_genga

    assert window.timeline_ops.toggle_genga(visual_row(window), 0)
    assert not canvas.frames[0].layers[0].is_genga


def test_the_timeline_shows_the_mark(window):
    prepare(window)
    window.timeline_ops.toggle_genga(visual_row(window), 1)
    window.refresh_ui()
    table = window.timeline.table
    item = table.item(visual_row(window), 1)
    assert item.data(TimelineCellDelegate.GENGA_ROLE) is True
    assert "原画" in item.toolTip()
    assert not table.item(visual_row(window), 0).data(TimelineCellDelegate.GENGA_ROLE)


def test_the_mark_is_saved_and_loaded(window, tmp_path):
    import json
    import zipfile

    canvas = prepare(window)
    window.timeline_ops.toggle_genga(visual_row(window), 2)
    path = tmp_path / "genga.pman"
    snapshot = project_io.ProjectSnapshot({}, canvas._document, canvas.frames[0].layers[0].image.width,
                                          canvas.frames[0].layers[0].image.height)
    project_io.write_project_snapshot(path, snapshot)

    with zipfile.ZipFile(path) as archive:
        data = json.loads(archive.read("project.json"))
    cells = {cell["number"]: cell for cell in data["tracks"][0]["cells"]}
    assert cells[3].get("genga") is True
    assert "genga" not in cells[1], "印の無いセルには書かない"

    reopened = project_io.load_project(path)
    track = reopened.document.tracks[0]
    assert track.cells[3].is_genga and not track.cells[1].is_genga


def test_the_mark_follows_the_drawing_when_it_is_cloned(window):
    canvas = prepare(window)
    layer = canvas.frames[0].layers[0]
    layer.is_genga = True
    assert layer.clone().is_genga


def test_importing_genga_marks_every_imported_cell(window, tmp_path, monkeypatch):
    from paintmaskanimator import constants

    paths = []
    for index in range(2):
        image = QImage(constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        for x in range(10, 30):
            image.setPixelColor(x + index, 10, QColor("black"))
        path = tmp_path / f"G{index + 1:04d}.png"
        assert image.save(str(path))
        paths.append(str(path))

    def answer(box):
        wanted = {"importMaterialQuestion": "デジタル（TP）"}.get(box.objectName(), "保存しない")
        next(button for button in box.buttons() if button.text() == wanted).click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", lambda box: answer(box))
    other_layer = window.canvas.frames[0].layers[0]

    assert window.import_genga(paths) == 2

    layer_index = window.canvas.active_layer_index
    marked = [frame.layers[layer_index].is_genga
              for frame in window.canvas.frames if frame.layers[layer_index].has_content]
    assert marked and all(marked)
    assert window.canvas.frames[0].layers[layer_index].name == "原画"
    assert not other_layer.is_genga
