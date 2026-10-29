"""Restore painting controls and the linked reference view across launches."""

import math

from PySide6.QtCore import QPointF

from . import config


def _number(record, key, default, minimum, maximum):
    try:
        value = float(record.get(key, default))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(maximum, value)) if math.isfinite(value) else default


def save(window):
    view = window.subview
    config.set_value("last_paint_session", {
        "brush_size": window.tools.brush_size_spinbox.value(),
        "subview": {
            "path": str(view._path.resolve()) if view._path is not None else None,
            "zoom": view.zoom_slider.value(),
            "rotation": view.rotation_slider.value(),
            "pan_x": view._pan.x(),
            "pan_y": view._pan.y(),
            "open": not window.subview_dock.isClosed(),
        },
    })


def restore(window):
    record = config.get_value("last_paint_session", {})
    if not isinstance(record, dict):
        return
    spin = window.tools.brush_size_spinbox
    spin.setValue(_number(record, "brush_size", 8.0, spin.minimum(), spin.maximum()))
    reference = record.get("subview")
    if not isinstance(reference, dict):
        return
    view = window.subview
    path = reference.get("path")
    if isinstance(path, str) and path and view.load_path(path):
        view.zoom_slider.setValue(int(_number(reference, "zoom", 100, 5, 1600)))
        slider = view.rotation_slider
        slider.setValue(int(_number(reference, "rotation", 0, slider.minimum(), slider.maximum())))
        view._pan = QPointF(
            _number(reference, "pan_x", 0, -1e7, 1e7),
            _number(reference, "pan_y", 0, -1e7, 1e7),
        )
        view.viewer.update()
    if isinstance(reference.get("open"), bool):
        window.subview_dock.toggleView(reference["open"])
