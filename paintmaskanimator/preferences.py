"""User preferences shown in the Preferences window (Qt-independent).

Each setting is read from the per-user :mod:`config` store and clamped to a
safe range, so a hand-edited or stale ``config.json`` can never push the app
into an unusable state (a zero-second autosave, a 0-step undo). Theme, accent
and language keep living in :mod:`theme` / :mod:`i18n`; this module holds the
settings that used to be hard-coded constants.
"""
from . import config, constants, view_preview

AUTOSAVE_ENABLED_KEY = "autosave_enabled"
AUTOSAVE_INTERVAL_KEY = "autosave_interval_minutes"
UNDO_STEPS_KEY = "undo_max_steps"
UNDO_MEMORY_KEY = "undo_max_memory_mb"
NEW_CANVAS_SIZE_KEY = "new_canvas_size"
KEEP_INITIAL_STATE_KEY = "keep_initial_state_on_cut_folder"
MAP_PALETTE_KEY = "map_color_palette"
TOOL_HOLD_ENABLED_KEY = "tool_hold_switch_enabled"
TOOL_HOLD_MS_KEY = "tool_hold_switch_ms"
AUTO_UPDATE_CHECK_KEY = "check_updates_automatically"
SKIPPED_UPDATE_KEY = "skipped_update_version"

DEFAULT_AUTOSAVE_INTERVAL = 3
AUTOSAVE_INTERVAL_RANGE = (1, 60)
DEFAULT_UNDO_STEPS = constants.MAX_UNDO
UNDO_STEPS_RANGE = (5, 500)
DEFAULT_UNDO_MEMORY_MB = constants.MAX_UNDO_BYTES // (1024 * 1024)
UNDO_MEMORY_RANGE = (128, 16384)
DEFAULT_CANVAS_SIZE = (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)
CANVAS_SIZE_RANGE = (64, constants.MAX_IMAGE_DIMENSION)
DEFAULT_TOOL_HOLD_MS = 0
TOOL_HOLD_MS_RANGE = (0, 3000)


def _clamped_int(key, default, bounds):
    raw = config.get_value(key, default)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(bounds[0], min(bounds[1], value))


def autosave_enabled():
    return bool(config.get_value(AUTOSAVE_ENABLED_KEY, True))


def set_autosave_enabled(enabled):
    config.set_value(AUTOSAVE_ENABLED_KEY, bool(enabled))


def keep_initial_state_on_cut_folder():
    """カットフォルダーを読み込んだら、尋ねずに色塗り前の初期状態として保持するか。"""
    return bool(config.get_value(KEEP_INITIAL_STATE_KEY, False))


def set_keep_initial_state_on_cut_folder(enabled):
    config.set_value(KEEP_INITIAL_STATE_KEY, bool(enabled))


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
    if stored is None:
        return DEFAULT_CANVAS_SIZE
    try:
        width, height = (int(v) for v in stored)
    except (TypeError, ValueError):
        return DEFAULT_CANVAS_SIZE
    low, high = CANVAS_SIZE_RANGE
    return (max(low, min(high, width)), max(low, min(high, height)))


def set_new_canvas_size(width, height):
    config.set_value(NEW_CANVAS_SIZE_KEY, [int(width), int(height)])


def _rgb_from_hex(value):
    text = str(value).strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(value)
    return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))


def map_palette():
    """隣接色分けの仮の色（RGB のタプルを :data:`view_preview.MAP_PALETTE` と同じ数）。

    保存されていない色・読めない色は、その番号の既定の色にする。
    """
    defaults = view_preview.MAP_PALETTE
    stored = config.get_value(MAP_PALETTE_KEY)
    if not isinstance(stored, list):
        return defaults
    colors = []
    for index, default in enumerate(defaults):
        try:
            colors.append(_rgb_from_hex(stored[index]))
        except (IndexError, TypeError, ValueError):
            colors.append(default)
    return tuple(colors)


def set_map_palette(colors):
    """隣接色分けの仮の色を保存する。既定と同じなら設定から消す。"""
    colors = tuple(tuple(int(channel) for channel in rgb[:3]) for rgb in colors)
    if colors == view_preview.MAP_PALETTE:
        config.set_value(MAP_PALETTE_KEY, None)
        return
    config.set_value(
        MAP_PALETTE_KEY, ["#{:02x}{:02x}{:02x}".format(*rgb) for rgb in colors]
    )


def tool_hold_enabled():
    """ツールのキーを長押ししたら、離したときに元のツールへ戻すか（一時切り替え）。"""
    return bool(config.get_value(TOOL_HOLD_ENABLED_KEY, False))


def set_tool_hold_enabled(enabled):
    config.set_value(TOOL_HOLD_ENABLED_KEY, bool(enabled))


def tool_hold_ms():
    """一時切り替えになる長押しの長さ（ミリ秒）。0 なら押せば必ず一時切り替え。"""
    return _clamped_int(TOOL_HOLD_MS_KEY, DEFAULT_TOOL_HOLD_MS, TOOL_HOLD_MS_RANGE)


def set_tool_hold_ms(milliseconds):
    config.set_value(TOOL_HOLD_MS_KEY, int(milliseconds))


def check_updates_automatically():
    """起動したあと裏で新しい版を確かめ、メニューバーで知らせるか。"""
    return bool(config.get_value(AUTO_UPDATE_CHECK_KEY, True))


def set_check_updates_automatically(enabled):
    config.set_value(AUTO_UPDATE_CHECK_KEY, bool(enabled))


def skipped_update_version():
    """「この版は知らせない」にした版（無ければ空）。"""
    return str(config.get_value(SKIPPED_UPDATE_KEY, "") or "")


def set_skipped_update_version(version):
    config.set_value(SKIPPED_UPDATE_KEY, str(version or "") or None)
