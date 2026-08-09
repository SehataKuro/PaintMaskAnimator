#!/usr/bin/env python3
"""PaintMaskAnimator launcher.

The application source now lives in the ``paintmaskanimator`` package next to
this file. This thin launcher keeps the familiar double-click / ``py
PaintMaskAnimator.py`` entry point working.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paintmaskanimator.__main__ import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        sys.excepthook(*sys.exc_info())
        raise SystemExit(1) from None
