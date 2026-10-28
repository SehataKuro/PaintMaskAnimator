"""User-facing frame-position formatting shared by the whole application."""

from . import preferences
from .i18n import tr


def frame_position(frame_index: int, fps: int = 24) -> str:
    """Format a zero-based frame index using the user's display preference."""
    index = max(0, int(frame_index))
    if preferences.time_display_mode() == preferences.TIME_DISPLAY_SECONDS_FRAMES:
        rate = max(1, int(fps))
        seconds, frame = divmod(index + 1, rate)
        return f"{seconds}+{frame}"
    return tr("{frame}コマ目").format(frame=index + 1)

