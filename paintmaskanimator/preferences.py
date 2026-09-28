"""User preferences shown in the Preferences window (Qt-independent).

Each setting is read from the per-user :mod:`config` store and clamped to a
safe range, so a hand-edited or stale ``config.json`` can never push the app
into an unusable state (a zero-second autosave, a 0-step undo). Theme, accent
and language keep living in :mod:`theme` / :mod:`i18n`; this module holds the
settings that used to be hard-coded constants.
"""
from . import config, constants

AUTOSAVE_ENABLED_KEY = "autosave_enabled"
AUTOSAVE_INTERVAL_KEY = "autosave_interval_minutes"
UNDO_STEPS_KEY = "undo_max_steps"
UNDO_MEMORY_KEY = "undo_max_memory_mb"
NEW_CANVAS_SIZE_KEY = "new_canvas_size"

DEFAULT_AUTOSAVE_INTERVAL = 3
AUTOSAVE_INTERVAL_RANGE = (1, 60)
DEFAULT_UNDO_STEPS = constants.MAX_UNDO
UNDO_STEPS_RANGE = (5, 500)
DEFAULT_UNDO_MEMORY_MB = constants.MAX_UNDO_BYTES // (1024 * 1024)
UNDO_MEMORY_RANGE = (128, 16384)
DEFAULT_CANVAS_SIZE = (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)
CANVAS_SIZE_RANGE = (64, constants.MAX_IMAGE_DIMENSION)


def _clamped_int(key, default, bounds):
    try:
        value = int(config.get_value(key, default))
    except (TypeError, ValueError):
        value = default
    return max(bounds[0], min(bounds[1], value))


def autosave_enabled():
    return bool(config.get_value(AUTOSAVE_ENABLED_KEY, True))


def set_autosave_enabled(enabled):
    config.set_value(AUTOSAVE_ENABLED_KEY, bool(enabled))


def autosave_interval_minutes():
    return _clamped_int(
        AUTOSAVE_INTERVAL_KEY, DEFAULT_AUTOSAVE_INTERVAL, AUTOSAVE_INTERVAL_RANGE
    )


def set_autosave_interval_minutes(minutes):
    config.set_value(AUTOSAVE_INTERVAL_KEY, int(minutes))


def undo_max_steps():
    return _clamped_int(UNDO_STEPS_KEY, DEFAULT_UNDO_STEPS, UNDO_STEPS_RANGE)


def set_undo_max_steps(steps):
    config.set_value(UNDO_STEPS_KEY, int(steps))


def undo_max_memory_mb():
    return _clamped_int(UNDO_MEMORY_KEY, DEFAULT_UNDO_MEMORY_MB, UNDO_MEMORY_RANGE)


def set_undo_max_memory_mb(megabytes):
    config.set_value(UNDO_MEMORY_KEY, int(megabytes))


def new_canvas_size():
    """Return the ``(width, height)`` used for a new document."""
    stored = config.get_value(NEW_CANVAS_SIZE_KEY)
    try:
        width, height = (int(v) for v in stored)
    except (TypeError, ValueError):
        return DEFAULT_CANVAS_SIZE
    low, high = CANVAS_SIZE_RANGE
    return (max(low, min(high, width)), max(low, min(high, height)))


def set_new_canvas_size(width, height):
    config.set_value(NEW_CANVAS_SIZE_KEY, [int(width), int(height)])
