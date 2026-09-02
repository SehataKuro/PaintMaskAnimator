"""Optional third-party dependencies, resolved to ``None`` when unavailable.

Pillow and psd_tools are declared under the ``full`` extra: the application runs
without them, with the PIL-backed image paths and ``.psd`` import disabled. Every
consumer imports the shim from here and checks for ``None`` rather than wrapping
its own ``try``/``except ImportError``, so "is this feature available?" has one
answer in one place.

The *required* dependencies (PySide6, numpy) are checked by
``dependency_check.py`` at start-up and imported directly where they are used.
"""
from __future__ import annotations

try:
    from PIL import Image as PILImage, ImageFilter as PILImageFilter
except (ImportError, OSError):
    # Pillow is optional; a missing or broken install disables PIL-backed paths.
    PILImage = None
    PILImageFilter = None

try:
    from psd_tools import PSDImage  # pyright: ignore[reportMissingImports]
except (ImportError, OSError):
    # psd_tools is optional; absence disables .psd import only.
    PSDImage = None

__all__ = ["PILImage", "PILImageFilter", "PSDImage"]
