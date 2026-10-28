"""Rust-owned timeline metadata; Qt images remain on Layer."""
from . import native


METADATA_FIELDS = (
    "has_content", "is_blank_key", "exposure", "sequence_number", "sequence_only", "cell_name",
)


def metadata_for(layer):
    data = layer.__dict__.get("_timeline_metadata")
    if data is None:
        data = native._native.TimelineCell()
        layer.__dict__["_timeline_metadata"] = data
    return data


def timeline_metadata(cls):
    """Install properties after @dataclass has captured its public defaults."""
    def metadata_property(name):
        def get(layer):
            return getattr(metadata_for(layer), name)

        def set_value(layer, value):
            # Out-of-range integers are invalid metadata, never a reason to
            # switch to a second runtime implementation.
            setattr(metadata_for(layer), name, value)

        return property(get, set_value)

    for name in METADATA_FIELDS:
        setattr(cls, name, metadata_property(name))
    return cls
