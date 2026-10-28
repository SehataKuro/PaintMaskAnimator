from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from paintmaskanimator.canvas import PaintCanvas


def test_quality_transform_places_moved_art_over_existing_art():
    app = QApplication.instance() or QApplication([])
    canvas = PaintCanvas()
    original = QImage(80, 40, QImage.Format.Format_ARGB32_Premultiplied)
    # Erased/background areas are alpha=0 under the eraser model (issue #15),
    # not the old opaque #FFFFFF sentinel.
    original.fill(QColor(0, 0, 0, 0))
    original.setPixelColor(32, 15, QColor(0, 0, 255, 255))
    original.setPixelColor(35, 15, QColor(0, 255, 0, 255))
    preview = QImage(original.size(), original.format())
    preview.fill(QColor(0, 0, 0, 0))
    preview.setPixelColor(32, 15, QColor(255, 0, 0, 255))

    canvas.selection_polygon = [
        QPointF(10, 10), QPointF(20, 10),
        QPointF(20, 20), QPointF(10, 20),
    ]
    merged = canvas._merge_quality_transform(original, preview)

    assert merged.pixelColor(32, 15) == QColor(255, 0, 0, 255)
    assert merged.pixelColor(35, 15) == QColor(0, 255, 0, 255)
    assert merged.pixelColor(40, 15).alpha() == 0
    canvas.deleteLater()
    app.processEvents()


def test_transform_rotation_history_undo_redo_and_commit():
    app = QApplication.instance() or QApplication([])
    canvas = PaintCanvas()
    canvas.set_transform_quality(False)
    canvas.selection_polygon = [QPointF(10, 10), QPointF(20, 10),
                                QPointF(20, 20), QPointF(10, 20)]
    assert canvas.begin_selection_transform("free")
    before = list(canvas.transform_points)
    canvas.rotate_selection_transform(30)
    after = list(canvas.transform_points)
    assert before != after
    assert canvas.undo_stack[-1].history_label == "変形操作"
    canvas.undo()
    assert canvas.transform_active
    assert canvas.transform_points == before
    canvas.redo()
    assert canvas.transform_points == after
    canvas.commit_selection_transform(False)
    assert len(canvas.undo_stack) == 1
    assert not canvas.transform_active
    canvas.undo()
    canvas.redo()
    canvas.deleteLater()
    app.processEvents()


def test_mesh_history_restores_grid_and_cancel_discards_geometry():
    app = QApplication.instance() or QApplication([])
    canvas = PaintCanvas()
    canvas.set_transform_quality(False)
    canvas.selection_polygon = [QPointF(10, 10), QPointF(30, 10),
                                QPointF(30, 30), QPointF(10, 30)]
    original = canvas.active_layer.image.copy()
    assert canvas.begin_selection_transform("mesh")
    before = list(canvas.transform_points)
    old_cols, old_rows = canvas.transform_mesh_cols, canvas.transform_mesh_rows
    canvas.set_transform_mesh_grid(6, 5)
    assert len(canvas.transform_points) == 30
    canvas.undo()
    assert canvas.transform_points == before
    assert (canvas.transform_mesh_cols, canvas.transform_mesh_rows) == (old_cols, old_rows)
    canvas.redo()
    assert len(canvas.transform_points) == 30
    canvas.cancel_selection_transform()
    assert canvas.active_layer.image == original
    assert not canvas.undo_stack
    assert not canvas.redo_stack
    canvas.deleteLater()
    app.processEvents()


def test_drag_records_one_history_entry_and_ignores_unchanged_drag():
    app = QApplication.instance() or QApplication([])
    canvas = PaintCanvas()
    canvas.set_transform_quality(False)
    canvas.selection_polygon = [QPointF(10, 10), QPointF(100, 10),
                                QPointF(100, 100), QPointF(10, 100)]
    assert canvas.begin_selection_transform("free")
    before = list(canvas.transform_points)
    assert canvas.begin_transform_drag(QPointF(50, 50))
    canvas.update_transform_drag(QPointF(55, 55))
    canvas.update_transform_drag(QPointF(60, 60))
    canvas.end_transform_drag()
    assert len(canvas.undo_stack) == 1
    canvas.undo()
    assert canvas.transform_points == before
    assert canvas.begin_transform_drag(QPointF(50, 50))
    canvas.end_transform_drag()
    assert not canvas.undo_stack
    canvas.cancel_selection_transform()
    canvas.deleteLater()
    app.processEvents()
