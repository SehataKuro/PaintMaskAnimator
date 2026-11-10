"""Explicit dense timeline setup for tests that formerly wrote Layer.exposure."""
from paintmaskanimator.document import create_drawing, replace_slot
from paintmaskanimator.models import Cell


def hold(canvas, column, length, layer_index=0):
    source = canvas.frames[column].layers[layer_index]
    if source.has_content and source.sequence_number is None:
        create_drawing(source, source.image, number=None)
    elif source.has_content:
        source._track.cells.setdefault(source.sequence_number, source.__dict__.get("_cell") or Cell())
    canvas._ensure_frame_count(column + length)
    source.blank_start = bool(source.is_blank_key and not source.has_content)
    for index in range(column + 1, column + length):
        replace_slot(canvas.frames[index], layer_index, source.clone_reference())
    return source


def length(canvas, column, layer_index=0):
    block = canvas.timeline_block_at(column, layer_index)
    return block[2] if block is not None else 1


def clear_range(canvas, start, end, layer_index=0):
    from paintmaskanimator.document import clear_slot
    for column in range(start, end):
        clear_slot(canvas.frames[column].layers[layer_index])


def dense_raw(frames):
    """Expand DTO input for pure readers, preserving unnumbered/ragged cases."""
    from PySide6.QtGui import QImage
    from paintmaskanimator.legacy_v1 import expand_legacy, snapshot_frames
    from paintmaskanimator.models import Frame, Layer
    expansion = expand_legacy(snapshot_frames(frames))
    result = []
    for column, row in enumerate(expansion.sheet):
        layers = []
        for index, ref in enumerate(row):
            original = frames[column].layers[index] if index < len(frames[column].layers) else None
            layer = Layer("A", QImage(), has_content=ref.kind == "CELL",
                          is_blank_key=ref.kind == "BLANK", sequence_number=ref.number)
            layer.blank_start = expansion.positions[column][index].blank_start
            if original is not None:
                layer.tween = original.tween
                layer.tween_member = original.tween_member
            layers.append(layer)
        result.append(Frame(layers))
    return result
