"""Translate document changes into the canvas's existing notifications."""
from .document_changes import (
    ChangeSet, Cursor, ExposureIndexReset, FrameStructure, Selection, SheetSpans,
    TrackProperties, TrackStructure,
)


def publish_frame_changes(canvas, changes: ChangeSet, *, notify_selection=False, clear_onion=True,
                          mark_dirty=True, before_signals=None):
    """Publish a duplicate, deletion, or forward frame step."""
    if not changes:
        return
    sheet_edit = False
    structure = None
    selection = notify_selection
    for change in changes.changes:
        if isinstance(change, ExposureIndexReset):
            canvas._invalidate_exposure_index()
        elif isinstance(change, Cursor):
            canvas.current_frame = change.frame
            selection = True
        elif isinstance(change, Selection):
            canvas.active_layer_index = change.layer_index
            selection = True
        elif isinstance(change, FrameStructure):
            structure = change
        elif isinstance(change, SheetSpans):
            sheet_edit = True

    if mark_dirty and (sheet_edit or (structure is not None and structure.operation == "extend")):
        canvas._cell_structure_dirty = True
    if sheet_edit and clear_onion:
        canvas._onion_cache.clear()
    if before_signals is not None:
        before_signals()
    if structure is not None or sheet_edit:
        canvas.changed.emit()
    if selection:
        canvas.selectionChanged.emit()
    canvas.update()


def publish_track_changes(canvas, changes: ChangeSet, *, window=None):
    """Publish one track edit after its existing undo entry has been recorded."""
    structure = next((change for change in changes.changes
                      if isinstance(change, TrackStructure)), None)
    selection = next((change for change in changes.changes
                      if isinstance(change, Selection)), None)
    fields = {field for change in changes.changes if isinstance(change, TrackProperties)
              for field in change.fields}
    merged = structure is not None and structure.operation == "merge"
    if merged:
        canvas.install_legacy_frames()
    if selection is not None:
        canvas.active_layer_index = selection.layer_index

    draft = "is_draft" in fields
    if structure is not None or draft:
        canvas._onion_cache.clear()
        if draft or merged:
            canvas._color_filter_cache.clear()
            canvas._color_index_cache.clear()
            canvas._silhouette_cache.clear()
        if draft and window is not None:
            window._used_color_cache.clear()
            window._used_color_layer_cache.clear()
        canvas.changed.emit()
        canvas.selectionChanged.emit()
        canvas.update()
    elif fields & {"color_filter_enabled", "color_filter_rgb"}:
        canvas._color_filter_cache.clear()
        canvas.changed.emit()
        canvas.update()
    elif fields & {"visible", "opacity"}:
        # Onion cache keys already include these settings.
        canvas._playback_frame_cache.clear()
        canvas.update()
    elif fields:
        canvas.changed.emit()


def publish_duration_change(canvas, changes: ChangeSet):
    # The duration control emits even when no block is selected.
    canvas.changed.emit()


def publish_editable_cursor(canvas, changes: ChangeSet):
    for change in changes.changes:
        if isinstance(change, Cursor):
            canvas.current_frame = change.frame
            if change.notify:
                canvas.selectionChanged.emit()


def publish_editable_position(canvas, changes: ChangeSet):
    if changes:
        canvas._cell_structure_dirty = True


def publish_remap_changes(window, changes: ChangeSet):
    def clear_caches():
        canvas = window.canvas
        canvas._onion_cache.clear()
        canvas._color_filter_cache.clear()
        canvas._color_index_cache.clear()
        canvas._silhouette_cache.clear()
        window._used_color_cache.clear()

    publish_frame_changes(window.canvas, changes, clear_onion=False, mark_dirty=False,
                          before_signals=clear_caches)
    if changes:
        window.used_color.schedule_refresh()


def publish_tween_commit(window, changes: ChangeSet, close_popup):
    if not changes:
        return
    canvas = window.canvas
    for change in changes.changes:
        if isinstance(change, Cursor):
            canvas.current_frame = change.frame
        elif isinstance(change, Selection):
            canvas.active_layer_index = change.layer_index
    canvas._onion_cache.clear()
    canvas._color_filter_cache.clear()
    canvas._color_index_cache.clear()
    canvas._silhouette_cache.clear()
    close_popup()
    # Interpolation does not introduce colors. Skip scanning the full sheet.
    window._suppress_used_color_refresh_once = True
    canvas.changed.emit()
    canvas.selectionChanged.emit()
    canvas._update_selection_clear_overlay()
    canvas.update()
