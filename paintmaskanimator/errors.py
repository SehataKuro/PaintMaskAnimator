"""Shared exception tuples for top-level user-action handlers."""
import zipfile

# Realistic failure set for the top-level user-action handlers (file I/O,
# PIL/numpy/Qt image pipelines): everything expected while still letting
# non-Exception control-flow (KeyboardInterrupt/SystemExit) propagate.
OPERATION_ERRORS = (
    OSError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    RuntimeError,
    AttributeError,
    MemoryError,
    zipfile.BadZipFile,  # archive read/write paths (projects, PSD) — not an OSError
)
