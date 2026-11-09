"""Cell の3属性と、シート順での旧データ統一（Issue #31 Phase 5-4）。"""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator import constants, project_io
from paintmaskanimator.canvas_timeline_ops import TimelineStructureMixin
from paintmaskanimator.document import Document
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.models import Frame, Layer


def image(color):
    result = QImage(16, 16, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(color))
    return result


def layer(number=1, **kwargs):
    return Layer("A", image("red"), has_content=True, sequence_number=number, **kwargs)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def canvas(layers, archive=None):
    doc = Document()
    doc.frames = [Frame([item]) for item in layers]
    doc.tracks = [layers[0]._track]
    doc.sequence_archive = archive or {}
    obj = SimpleNamespace(frames=[Frame([item]) for item in layers], current_frame=0,
                          _sequence_archive=doc.sequence_archive, _document=doc)
    def install():
        doc.current_frame = obj.current_frame
        doc.install_legacy_frames(obj.frames, obj._sequence_archive)
        obj._sequence_archive = doc.sequence_archive
    obj.install_legacy_frames = install
    obj._numbered_cell_attributes = (
        lambda *args: TimelineStructureMixin._numbered_cell_attributes(obj, *args)
    )
    return obj


def test_shared_attributes():
    first, second = layer(), layer()
    obj = canvas([first, second])
    TimelineStructureMixin.coalesce_numbered_images(obj)
    second.initial_image = image("blue")
    second.cell_name = "原画"
    assert first.initial_image.pixelColor(0, 0) == QColor("blue")
    assert first.cell_name == "原画"


@pytest.mark.parametrize("number,content", [(None, True), (1, False)])
def test_unnumbered_and_unused_are_independent(number, content):
    first, second = layer(number), layer(number)
    second.has_content = content
    obj = canvas([first, second])
    TimelineStructureMixin.coalesce_numbered_images(obj)
    TimelineStructureMixin.sync_numbered_image_from_cell(obj, 0, 0)
    first.initial_image = image("blue")
    first.cell_name = "原画"
    assert second.initial_image is None
    assert second.cell_name is None
    assert second.has_content == content


@pytest.mark.parametrize("operation", ["detach", "clone"])
def test_all_attributes_survive_independent_copy(operation):
    original = layer(initial_image=image("blue"), cell_name="原画")
    other = layer()
    other.share_cell_with(original)
    if operation == "detach":
        other.detach_cell()
    else:
        other = original.clone()
    assert other.image.cacheKey() == original.image.cacheKey()
    assert other.initial_image.cacheKey() == original.initial_image.cacheKey()
    assert other.cell_name == "原画"
    other.image.setPixelColor(0, 0, QColor("green"))
    other.initial_image.setPixelColor(0, 0, QColor("yellow"))
    other.cell_name = "別"
    assert original.image.pixelColor(0, 0) == QColor("red")
    assert original.initial_image.pixelColor(0, 0) == QColor("blue")
    assert original.cell_name == "原画"


@pytest.mark.parametrize("sync", [False, True])
def test_sheet_first_values_are_independent_of_image_owner(sync):
    layers = [layer(), layer(2), layer(initial_image=image("blue")), layer(3),
              layer(initial_image=image("green"), cell_name="後")]
    layers[0].cell_name = "先"
    layers[4].image = image("yellow")
    obj = canvas(layers)
    obj.current_frame = 4
    if sync:
        TimelineStructureMixin.sync_numbered_image_from_cell(obj, 4, 0)
    else:
        TimelineStructureMixin.coalesce_numbered_images(obj)
    for index in (0, 2, 4):
        assert layers[index].initial_image.pixelColor(0, 0) == QColor("blue")
        assert layers[index].cell_name == "先"
        assert layers[index].image.pixelColor(0, 0) == QColor("yellow")


def test_archive_fallback():
    first = layer()
    stored = layer(initial_image=image("blue"), cell_name="保管")
    obj = canvas([first], {(0, 1): stored})
    TimelineStructureMixin.coalesce_numbered_images(obj)
    assert first.initial_image.pixelColor(0, 0) == QColor("blue")
    assert first.cell_name == "保管"
    assert first.cell_id == stored.cell_id


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 16)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 16)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = MainWindow()
    yield result
    result.deleteLater()
    qapp.processEvents()


def test_ui_initial_import_undo_redo(window, tmp_path):
    first, second = layer(initial_image=image("green")), layer()
    window.canvas.frames = [Frame([first]), Frame([second])]
    window.refresh_ui()
    folder = tmp_path / "A"
    folder.mkdir()
    assert image("blue").save(str(folder / "A1.png"))
    assert window.initial_state.import_line_art(tmp_path) == 2
    for color, action in [("blue", None), ("green", window.canvas.undo),
                          ("blue", window.canvas.redo)]:
        if action:
            action()
        assert all(item.initial_image.pixelColor(0, 0) == QColor(color)
                   for item in (first, second))


def test_conflicting_v1_load_and_roundtrip(window, tmp_path):
    frames = [Frame([layer()]), Frame([layer(initial_image=image("blue"), cell_name="先")]),
              Frame([layer(initial_image=image("green"), cell_name="後")])]
    path = tmp_path / "old.pman"
    metadata = {"format": "PaintMaskAnimatorProject", "format_version": 1,
                "mask_format": 2, "canvas": {"width": 16, "height": 16}, "frames": []}
    project_io.write_project_archive(path, metadata, frames, {})
    assert window.project.open(path)
    for attempt in range(2):
        assert all(frame.layers[0].initial_image.pixelColor(0, 0) == QColor("blue")
                   and frame.layers[0].cell_name == "先" for frame in window.canvas.frames)
        if attempt == 0:
            saved = tmp_path / "saved.pman"
            assert window.project.write(saved)
            assert window.project.open(saved)


def test_drawing_does_not_revive_blank_or_unused_with_same_number(window):
    drawing = layer(3)
    blank = Layer("A", image("green"), sequence_number=3, is_blank_key=True)
    unused = Layer("A", image("yellow"), sequence_number=3)
    before = [item.image.copy() for item in (blank, unused)]
    c = window.canvas
    c.frames = [Frame([item]) for item in (drawing, blank, unused)]
    c.current_frame = 0
    c.active_layer_index = 0
    window.refresh_ui()
    drawing_before = drawing.image.copy()
    c.line_start = QPointF(2, 2)
    c.line_end = QPointF(12, 12)
    c.line_control = None
    c.commit_line_tool()
    assert drawing.image != drawing_before
    for item, original in zip((blank, unused), before):
        assert not item.has_content
        assert item.sequence_number == 3
        assert item.image == original
    assert blank.is_blank_key
    assert not unused.is_blank_key
