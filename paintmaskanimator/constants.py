"""Application-wide constants.

Note: ``CANVAS_WIDTH`` / ``CANVAS_HEIGHT`` are *mutable* runtime state — they
change when the project canvas is resized. They are deliberately excluded from
``__all__`` so that ``from .constants import *`` does NOT create per-module
copies that would go stale after a resize. Always read/write them through the
module, e.g. ``constants.CANVAS_WIDTH``.
"""

APP_NAME = "PaintMaskAnimator"
APP_VERSION = "0.6.1"
APP_DISPLAY_NAME = f"{APP_NAME} V{APP_VERSION}"
# GitHub repository used by the in-app updater (Releases are fetched from here).
GITHUB_REPO = "SehataKuro/PaintMaskAnimator"
CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720
OUTSIDE_MARGIN = 0
MAX_UNDO = 30
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

# Everything except the mutable canvas dimensions is safe to star-import.
__all__ = [
    _n for _n in list(globals())
    if _n.isupper() and _n not in ("CANVAS_WIDTH", "CANVAS_HEIGHT")
]
