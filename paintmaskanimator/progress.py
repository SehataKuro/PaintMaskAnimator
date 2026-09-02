"""Modal progress counter for long, frame-by-frame operations.

Shared UI plumbing, not a feature: exports, scope runs, tween generation, layer
merges and used-colour scans all show the same "n / total" dialog. It used to
live inside ``main_window_line_ops.py``, which meant eight unrelated modules
reached into the line-tool module for it. It is a free function set here so any
of them can use it without depending on a feature module.

``parent`` is the window the dialog is modal to.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QProgressDialog, QWidget

from .i18n import tr


def create_counter(
    parent: QWidget, title: str, total: int, label: str | None = None,
    cancellable: bool = False,
) -> QProgressDialog:
    """Show a modal counter dialog and return it."""
    total = max(1, int(total))
    dialog = QProgressDialog(
        label or title,
        tr("中止") if cancellable else "",
        0,
        total,
        parent,
    )
    dialog.setWindowTitle(title)
    if not cancellable:
        dialog.setCancelButton(None)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setMinimumWidth(330)
    dialog.setValue(0)
    dialog.show()
    QApplication.processEvents()
    return dialog


def update_counter(dialog: QProgressDialog | None, value: int, total: int, label: str) -> None:
    """Advance the counter. A ``None`` dialog is a no-op, so callers need no guard."""
    if dialog is None:
        return
    value = max(0, min(int(total), int(value)))
    dialog.setLabelText(f"{label}\n{value} / {int(total)}")
    dialog.setValue(value)
    QApplication.processEvents()


def close_counter(dialog: QProgressDialog | None) -> None:
    """Finish and dispose of the counter. A ``None`` dialog is a no-op."""
    if dialog is None:
        return
    dialog.setValue(dialog.maximum())
    dialog.close()
    dialog.deleteLater()
    QApplication.processEvents()
