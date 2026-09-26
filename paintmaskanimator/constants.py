"""Application-wide constants.

Note: ``CANVAS_WIDTH`` / ``CANVAS_HEIGHT`` are *mutable* runtime state — they
change when the project canvas is resized. They are deliberately excluded from
``__all__`` so that ``from .constants import *`` does NOT create per-module
copies that would go stale after a resize. Always read/write them through the
module, e.g. ``constants.CANVAS_WIDTH``.
"""

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    # tomllib is stdlib only from 3.11, and requires-python allows 3.10. It is
    # needed solely to read the version out of pyproject.toml in an
    # *uninstalled* source checkout, so importing the package must not depend
    # on it -- that path just degrades to "development" below.
    tomllib = None


APP_NAME = "PaintMaskAnimator"


def _application_version() -> str:
    """Read the canonical version, including from a frozen application.

    The build bundles ``pyproject.toml`` beside the package.  Prefer it over
    distribution metadata so a stale editable-install ``egg-info`` directory
    can never leak the previous release number into the UI or updater.
    """
    if tomllib is not None:
        project_file = Path(__file__).resolve().parent.parent / "pyproject.toml"
        try:
            project = tomllib.loads(project_file.read_text(encoding="utf-8"))
            return str(project["project"]["version"])
        except (OSError, KeyError, tomllib.TOMLDecodeError):
            pass

    try:
        return version("paintmaskanimator")
    except PackageNotFoundError:
        return "development"


APP_VERSION = _application_version()
APP_DISPLAY_NAME = f"{APP_NAME} V{APP_VERSION}"
# GitHub repository: releases are distributed from here, and the in-app
# updater reads the latest one through the public REST API (no credentials).
GITHUB_REPO = "SehataKuro/PaintMaskAnimator"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
UPDATE_RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
# 「拡大縮小（押している間）」の既定キー。macOSのQtでは "Ctrl" が⌘になるが、
# ⌘SpaceはSpotlight、⌃Space("Meta")は入力ソース切り替えにOSが既定で使って
# いて届かないので、⌥Spaceにする。WindowsのAlt+Spaceはウィンドウメニュー。
HOLD_ZOOM_SHORTCUT = "Alt+Space" if sys.platform == "darwin" else "Ctrl+Space"
# 操作説明に出す修飾キー名。macOSのQtでは Ctrl 扱いのキーは⌘、Alt は⌥で、
# 物理的なControl＋クリックは右クリックになってしまう。
CTRL_KEY_LABEL = "⌘" if sys.platform == "darwin" else "Ctrl"
ALT_KEY_LABEL = "⌥" if sys.platform == "darwin" else "Alt"
HOLD_ZOOM_KEY_LABEL = f"{ALT_KEY_LABEL if sys.platform == 'darwin' else CTRL_KEY_LABEL}＋Space"
CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720
OUTSIDE_MARGIN = 0
MAX_UNDO = 30
# Entry count alone does not bound undo memory: 30 full-layer snapshots of a
# 4K canvas across several layers is multiple gigabytes, while 30 stroke tile
# patches are a few megabytes. The stack is trimmed by whichever limit binds
# first, and always keeps at least one entry so undo never becomes a no-op.
MAX_UNDO_BYTES = 768 * 1024 * 1024
MAX_PROJECT_METADATA_BYTES = 16 * 1024 * 1024
MAX_PROJECT_FRAMES = 10000
MAX_PROJECT_LAYERS = 256
MAX_PROJECT_LAYER_CELLS = 100000
MAX_PROJECT_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024
MAX_PROJECT_IMAGE_BYTES = 512 * 1024 * 1024
MAX_PROJECT_DECODED_PIXELS = 2 * 1024 * 1024 * 1024
MAX_IMAGE_DIMENSION = 16384
MAX_SINGLE_IMAGE_PIXELS = MAX_IMAGE_DIMENSION * MAX_IMAGE_DIMENSION
TP_MASK_PROXY_THRESHOLD = 5000
TP_MASK_PROXY_MAX_DIMENSION = 1280
TP_MASK_PROXY_MAX_COLORS = 32
# Exact colors the quality transform can carry through untouched.  Colors are
# one label id per pixel rather than one mask each, so the ceiling is about the
# source still being flat-colored art, not about memory.
TP_MASK_MAX_TRANSFORM_COLORS = 4096
# Coverage probes per axis while dragging the low-resolution proxy: 2x2 keeps
# the preview responsive, the committed render uses the full grid.
TP_MASK_PROXY_SUBSAMPLES = 2

# Everything except the mutable canvas dimensions is safe to star-import.
__all__ = [  # pyright: ignore[reportUnsupportedDunderAll]
    _n for _n in list(globals())
    if _n.isupper() and _n not in ("CANVAS_WIDTH", "CANVAS_HEIGHT")
]
