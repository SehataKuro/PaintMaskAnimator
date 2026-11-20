"""Translate document changes into the canvas's existing notifications."""
from .document_changes import ChangeSet, Selection, TrackProperties, TrackStructure


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
