"""Copy ownership contracts; no widget or full-size image allocations."""
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter

from paintmaskanimator.document import Document
from paintmaskanimator.models import Cell, Frame, Layer, Track


def image(color="red"):
    result = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(color))
    return result


def layer():
    return Layer("A", image(), has_content=True, sequence_number=3,
                 cell_name="drawing", initial_image=image("green"),
                 tween={"nested": [1]}, exposure=4)


def document():
    source = layer()
    doc = object.__new__(Document)
    doc.frames = [Frame([source]), Frame([source.clone_slot()])]
    doc.current_frame = 1
    doc.active_layer_index = 0
    return doc, {(0, 3): source.clone_slot()}


def test_slot_shares_drawing_and_display():
    source = layer()
    copied = source.clone_slot()
    assert copied.cell_id == source.cell_id
    assert copied.track_id == source.track_id
    copied.image.fill(QColor("blue"))
    assert source.image.pixelColor(0, 0) == QColor("blue")
    copied.opacity = 0.5
    assert source.opacity == 0.5


def test_slot_metadata_is_independent():
    source = layer()
    copied = source.clone_slot()
    copied.sequence_number = 9
    copied.exposure = 2
    copied.has_content = False
    copied.is_blank_key = True
    copied.sequence_only = True
    copied.tween["nested"].append(2)
    copied.tween_member = "other"
    assert (source.sequence_number, source.exposure) == (3, 4)
    assert source.has_content and not source.is_blank_key and not source.sequence_only
    assert source.tween == {"nested": [1]}
    assert source.tween_member is None


def test_independent_clone_fill_and_settings():
    source = layer()
    copied = source.clone()
    assert copied.cell_id != source.cell_id
    assert copied.track_id != source.track_id
    assert copied.image is not source.image
    assert copied.initial_image is not source.initial_image
    copied.image.fill(QColor("blue"))
    copied.initial_image.fill(QColor("black"))
    copied.cell_name = "changed"
    copied.name = "B"
    copied.opacity = 0.2
    copied.tween["nested"].append(2)
    assert source.image.pixelColor(0, 0) == QColor("red")
    assert source.initial_image.pixelColor(0, 0) == QColor("green")
    assert (source.cell_name, source.name, source.opacity) == ("drawing", "A", 1.0)
    assert source.tween == {"nested": [1]}


def test_independent_clone_painter():
    source = layer()
    copied = source.clone()
    for target in (copied.image, copied.initial_image):
        painter = QPainter(target)
        painter.fillRect(QRect(0, 0, 4, 4), QColor("blue"))
        painter.end()
    assert source.image.pixelColor(0, 0) == QColor("red")
    assert source.initial_image.pixelColor(0, 0) == QColor("green")


def test_graph_preserves_sharing_and_archive():
    doc, archive = document()
    copied, copied_archive = doc.clone_graph(archive)
    first, second = [frame.layers[0] for frame in copied.frames]
    assert first.cell_id == second.cell_id == copied_archive[(0, 3)].cell_id
    assert first.cell_id != doc.active_layer.cell_id
    assert first.track_id == second.track_id == copied_archive[(0, 3)].track_id
    assert first.track_id != doc.active_layer.track_id
    assert (copied.current_frame, copied.active_layer_index) == (1, 0)
    first.tween["nested"].append(2)
    assert second.tween == {"nested": [1]}


def test_graph_is_independent_from_live_edits():
    doc, archive = document()
    copied, copied_archive = doc.clone_graph(archive)
    doc.active_layer.image.fill(QColor("black"))
    doc.active_layer.image = image("blue")
    doc.active_layer.initial_image.fill(QColor("black"))
    doc.active_layer.initial_image = image("blue")
    doc.active_layer.cell_name = "changed"
    doc.active_layer.opacity = 0.2
    archive.clear()
    archive[(0, 9)] = layer()
    target = copied.active_layer
    assert target.image.pixelColor(0, 0) == QColor("red")
    assert target.initial_image.pixelColor(0, 0) == QColor("green")
    assert (target.cell_name, target.opacity) == ("drawing", 1.0)
    assert list(copied_archive) == [(0, 3)]


def test_graph_allocates_once_per_cell_and_track(monkeypatch):
    doc, archive = document()
    doc.frames.extend(Frame([doc.active_layer.clone_slot()]) for _ in range(100))
    created, created_tracks = [], []
    original_track = Track.__init__
    original = Cell.__init__

    def counted(self, *args, **kwargs):
        created.append(self)
        original(self, *args, **kwargs)

    def counted_track(self, *args, **kwargs):
        created_tracks.append(self)
        original_track(self, *args, **kwargs)

    monkeypatch.setattr(Track, "__init__", counted_track)
    monkeypatch.setattr(Cell, "__init__", counted)
    copied, _ = doc.clone_graph(archive)
    assert len(created) == 1
    assert len(created_tracks) == 1
    assert len({frame.layers[0].track_id for frame in copied.frames}) == 1


def test_graph_does_not_coalesce_legacy_conflicts():
    doc, archive = document()
    conflicting = layer()
    conflicting.image = image("blue")
    doc.frames.append(Frame([conflicting]))
    copied, _ = doc.clone_graph(archive)
    assert copied.frames[0].layers[0].cell_id != copied.frames[2].layers[0].cell_id
    assert copied.frames[2].layers[0].image.pixelColor(0, 0) == QColor("blue")
    settings = Track(name="settings", color_filter_rgb=(1, 2, 3))
    assert settings.copy_settings() is not settings
    assert settings.copy().color_filter_rgb == settings.copy_settings().color_filter_rgb
