"""Pen-pressure settings kept per user (Qt-widget independent).

Pressure depends on the person and the pen, not on the drawing, so it lives in
the user config instead of the project file:

* **Presets** -- named settings edited in Preferences › 筆圧. The one chosen
  there is the *global* pressure setting.
* **Brush** -- the brush tool's own setting, and whether the brush follows the
  global preset (the default) or uses its own.

A settings dict is ``{"enabled", "minimum", "maximum", "points"}`` with the
same meaning as the canvas attributes ``pressure_enabled`` / ``pressure_min``
/ ``pressure_max`` / ``pressure_curve_points``.
"""
import copy

from . import config
from .i18n import tr

PRESETS_KEY = "pressure_presets"
ACTIVE_KEY = "pressure_active_preset"
BRUSH_KEY = "brush_pressure"
BRUSH_USES_GLOBAL_KEY = "brush_pressure_uses_global"

MINIMUM_RANGE = (0.01, 1.0)
MAXIMUM_RANGE = (0.1, 3.0)
DEFAULT_SETTINGS = {
    "enabled": True,
    "minimum": 0.1,
    "maximum": 1.0,
    "points": [[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]],
}


def _clamp(value, bounds, default):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return max(bounds[0], min(bounds[1], value))


def normalize(settings):
    """Return a complete, in-range copy of ``settings`` (missing keys -> default)."""
    settings = settings if isinstance(settings, dict) else {}
    points = []
    for point in settings.get("points") or []:
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        points.append([max(0.0, min(1.0, x)), max(0.0, min(1.0, y))])
    points.sort(key=lambda p: p[0])
    if len(points) < 2:
        points = copy.deepcopy(DEFAULT_SETTINGS["points"])
    return {
        "enabled": bool(settings.get("enabled", DEFAULT_SETTINGS["enabled"])),
        "minimum": _clamp(settings.get("minimum"), MINIMUM_RANGE, DEFAULT_SETTINGS["minimum"]),
        "maximum": _clamp(settings.get("maximum"), MAXIMUM_RANGE, DEFAULT_SETTINGS["maximum"]),
        "points": points,
    }


def default_preset_name():
    return tr("標準")


def presets():
    """All presets as ``[{"name": str, **settings}]``; never empty."""
    result = []
    seen = set()
    for entry in config.get_value(PRESETS_KEY, []) or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append({"name": name, **normalize(entry)})
    if not result:
        result.append({"name": default_preset_name(), **normalize(DEFAULT_SETTINGS)})
    return result


def save_presets(entries):
    config.set_value(
        PRESETS_KEY,
        [{"name": str(e["name"]), **normalize(e)} for e in entries],
    )


def preset_names():
    return [entry["name"] for entry in presets()]


def active_preset_name():
    names = preset_names()
    name = config.get_value(ACTIVE_KEY)
    return name if name in names else names[0]


def set_active_preset(name):
    config.set_value(ACTIVE_KEY, str(name))


def preset(name):
    for entry in presets():
        if entry["name"] == name:
            return normalize(entry)
    return None


def update_preset(name, settings):
    entries = presets()
    for entry in entries:
        if entry["name"] == name:
            entry.update(normalize(settings))
    save_presets(entries)


def add_preset(name, settings):
    """Add a preset (renamed ``name 2`` etc. if taken); return the used name."""
    entries = presets()
    names = {e["name"] for e in entries}
    base = str(name).strip() or default_preset_name()
    unique, n = base, 2
    while unique in names:
        unique, n = f"{base} {n}", n + 1
    entries.append({"name": unique, **normalize(settings)})
    save_presets(entries)
    return unique


def rename_preset(old, new):
    """Rename a preset; return False when ``new`` is empty or already used."""
    new = str(new).strip()
    entries = presets()
    if not new or any(e["name"] == new for e in entries):
        return False
    for entry in entries:
        if entry["name"] == old:
            entry["name"] = new
    save_presets(entries)
    if config.get_value(ACTIVE_KEY) == old:
        set_active_preset(new)
    return True


def remove_preset(name):
    """Remove a preset; the last one cannot be removed."""
    entries = [e for e in presets() if e["name"] != name]
    if not entries:
        return False
    save_presets(entries)
    return True


def global_settings():
    settings = preset(active_preset_name())
    # active_preset_name() always names one of presets(), so this is never None.
    assert settings is not None
    return settings


def brush_settings():
    return normalize(config.get_value(BRUSH_KEY, DEFAULT_SETTINGS))


def set_brush_settings(settings):
    config.set_value(BRUSH_KEY, normalize(settings))


def brush_uses_global():
    return bool(config.get_value(BRUSH_USES_GLOBAL_KEY, True))


def set_brush_uses_global(uses_global):
    config.set_value(BRUSH_USES_GLOBAL_KEY, bool(uses_global))


def effective_settings():
    """The setting the brush draws with right now."""
    return global_settings() if brush_uses_global() else brush_settings()
