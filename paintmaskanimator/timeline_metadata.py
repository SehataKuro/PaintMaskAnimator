"""Rust-owned timeline metadata; Qt images remain on Layer."""
from . import native


METADATA_FIELDS = (
    "has_content", "is_blank_key", "blank_start", "sequence_number", "sequence_only",
)


def metadata_for(layer):
    data = layer.__dict__.get("_timeline_metadata")
    if data is None:
        data = native._native.TimelineCell()
        layer.__dict__["_timeline_metadata"] = data
    track = layer.__dict__.get("_track")
    if (track is not None and track.connected
            and not layer.__dict__.get("_pending_detached", False)):
        data.sequence_only = layer.__dict__.get("_pending_token") in track.pending_slots
    return data


def timeline_metadata(cls):
    """Install properties after @dataclass has captured its public defaults."""
    def metadata_property(name):
        def get(layer):
            if name == "sequence_only":
                track = layer.__dict__.get("_track")
                if (track is not None and track.connected
                        and not layer.__dict__.get("_pending_detached", False)):
                    return layer.__dict__.get("_pending_token") in track.pending_slots
            return getattr(metadata_for(layer), name)

        def set_value(layer, value):
            # Out-of-range integers are invalid metadata, never a reason to
            # switch to a second runtime implementation.
            if (name == "sequence_only" and layer._track.connected
                    and not layer.__dict__.get("_pending_detached", False)):
                raise AttributeError("Use mark_pending/clear_pending for live positions")
            setattr(metadata_for(layer), name, value)

        return property(get, set_value)

    for name in METADATA_FIELDS:
        setattr(cls, name, metadata_property(name))
    return cls


def reference_for(layer, *, resolve=False):
    """Read dense truth, including the v1 pending display/image exception."""
    from .legacy_v1 import DenseReference
    imported = layer.__dict__.get("_dense_import")
    signature = (layer.has_content, layer.is_blank_key, layer.sequence_number,
                 layer.sequence_only, layer.blank_start)
    if imported is not None and imported[0] == signature:
        return imported[2 if resolve else 1]
    if layer.sequence_only and not resolve:
        return DenseReference()
    if layer.has_content:
        return DenseReference("CELL", layer.sequence_number)
    return DenseReference("BLANK" if layer.is_blank_key else "EMPTY")


def snapshot_metadata(layer, *, resolve=False):
    if "_dense_import" not in layer.__dict__ and not getattr(layer, "sequence_only", False):
        return layer.__dict__.get("_timeline_metadata")
    reference = reference_for(layer, resolve=resolve)
    data = native._native.TimelineCell()
    data.has_content = reference.kind == "CELL"
    data.is_blank_key = reference.kind == "BLANK"
    data.sequence_number = reference.number
    data.blank_start = layer.blank_start
    return data
