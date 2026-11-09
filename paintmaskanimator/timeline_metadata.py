"""Rust-owned timeline metadata; Qt images remain on Layer."""
from . import native


METADATA_FIELDS = (
    "has_content", "is_blank_key", "exposure", "sequence_number", "sequence_only",
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
