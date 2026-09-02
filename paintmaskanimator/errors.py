"""Application exception types and the shared catch tuple for user actions.

Top-level user-action handlers (open/save/import/export/…) must turn *expected*
failures into a message box, and must **not** swallow programmer errors.
``AttributeError``/``TypeError``/``KeyError``/``IndexError`` are deliberately
absent from :data:`OPERATION_ERRORS`: they signal a defect in this code base, and
catching them turned real bugs into a generic "操作に失敗しました" dialog that
neither the test suite nor CI could ever see.

When an operation needs to abort with a message meant for the user, raise
:class:`OperationError` rather than a bare ``ValueError``/``RuntimeError`` --
that makes the intent explicit and keeps the catch tuple honest.
"""
import zipfile


class PaintMaskAnimatorError(Exception):
    """Base class for every exception this application raises on purpose."""


class OperationError(PaintMaskAnimatorError):
    """A user action failed for an expected reason; ``str(exc)`` is shown as-is."""


# Expected failures for the top-level user-action handlers: our own deliberate
# aborts, file I/O, out-of-memory on large canvases, and the malformed-data /
# unsupported-backend signals raised by PIL, numpy, psd_tools and Qt.
OPERATION_ERRORS = (
    OperationError,
    OSError,
    MemoryError,
    ValueError,        # PIL/numpy/psd_tools: malformed or unsupported input data
    RuntimeError,      # PIL/Qt: unavailable codec or backend
    zipfile.BadZipFile,  # archive read/write paths (projects, PSD) — not an OSError
)
