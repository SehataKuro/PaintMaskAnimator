"""Qt-independent exposure resolution, shared by editing and playback."""
from . import native


def resolve_exposures(frames):
    return native.resolve_timeline(frames)
