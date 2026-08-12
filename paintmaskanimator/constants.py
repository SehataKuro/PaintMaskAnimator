"""Application-wide constants.

Note: ``CANVAS_WIDTH`` / ``CANVAS_HEIGHT`` are *mutable* runtime state — they
change when the project canvas is resized. They are deliberately excluded from
``__all__`` so that ``from .constants import *`` does NOT create per-module
copies that would go stale after a resize. Always read/write them through the
module, e.g. ``constants.CANVAS_WIDTH``.
"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tomllib


APP_NAME = "PaintMaskAnimator"
try:
    # pyproject.toml is the single source of truth.  Editable installs and
    # packaged builds both expose the value through distribution metadata.
    APP_VERSION = version("paintmaskanimator")
except PackageNotFoundError:
    # Running a source checkout without installing it is supported by the
    # legacy launcher.  Keep that mode useful without maintaining a second
    # version literal.
    project_file = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        APP_VERSION = str(
            tomllib.loads(project_file.read_text(encoding="utf-8"))["project"]["version"]
        )
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        APP_VERSION = "development"
APP_DISPLAY_NAME = f"{APP_NAME} V{APP_VERSION}"
# GitHub repository (used for release automation / links only).
GITHUB_REPO = "SehataKuro/PaintMaskAnimator"
# Update feed. The whole /paintmaskanimator/ path (installers + manifest
# included) sits behind the site's shared Basic-auth password, so the in-app
# updater authenticates with the SAME shared credentials baked in below. These
# must match the Cloudflare `SITE_USERNAME`/`SITE_PASSWORD` env vars. Note: a
# baked-in shared password is discoverable in the distributed binary — this is
# casual-visitor deterrence, not a strong secret.
UPDATE_BASE_URL = "https://jokomanato.com/paintmaskanimator"
UPDATE_MANIFEST_URL = f"{UPDATE_BASE_URL}/updates.json"
UPDATE_USERNAME = "guest"
UPDATE_PASSWORD = "6eCKEq"  # must equal Cloudflare SITE_PASSWORD
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
__all__ = [  # pyright: ignore[reportUnsupportedDunderAll]
    _n for _n in list(globals())
    if _n.isupper() and _n not in ("CANVAS_WIDTH", "CANVAS_HEIGHT")
]
