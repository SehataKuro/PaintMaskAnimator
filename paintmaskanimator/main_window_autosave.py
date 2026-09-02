"""Autosave / crash recovery; owned by ``MainWindow`` as ``window.autosave``.

Owns the autosave timer and the crash-recovery flow, writing through the
window's ``canvas``, ``timeline`` and ``build_project_metadata``.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from . import config, project_io
from .logging_setup import get_logger

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)


class AutosaveController:
    """Owned by ``MainWindow`` as ``window.autosave``.

    A collaborator rather than a mixin -- see main_window_export.py for why.
    """

    def __init__(self, window: "MainWindow"):
        self.window = window

    """Periodic best-effort project snapshots + startup crash recovery."""

    def path(self):
        return config.config_dir() / "autosave.pmap"

    def start(self, interval_ms=180000):
        """Periodically snapshot the project so a crash doesn't lose work."""
        self.window._autosave_timer = QTimer(self.window)
        self.window._autosave_timer.setInterval(int(interval_ms))
        self.window._autosave_timer.timeout.connect(self.save)
        self.window._autosave_timer.start()

    def save(self):
        # Must never raise into the event loop — autosave is best-effort.
        try:
            project_io.write_project_archive(
                self.path(),
                self.window.build_project_metadata(),
                self.window.canvas.frames,
                self.window.canvas._sequence_archive,
            )
        except Exception:  # noqa: BLE001 - best-effort; must never raise into the event loop
            # Autosave is best-effort and must never raise into the event loop,
            # so the broad catch is intentional; log the traceback instead of
            # printing it so it lands in the app log.
            log.exception("autosave failed")

    def maybe_restore(self):
        """On startup, offer to restore a leftover autosave (likely a crash)."""
        path = self.path()
        try:
            if not path.exists() or path.stat().st_size == 0:
                return
        except OSError:
            return
        answer = QMessageBox.question(
            self.window,
            "作業の復元",
            "前回のセッションが正常に終了しなかった可能性があります。\n"
            "自動保存された作業を復元しますか？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.window.project.open(str(path))
        else:
            self.clear()

    def clear(self):
        try:
            self.path().unlink(missing_ok=True)
        except OSError:
            # A stale snapshot can trigger another recovery prompt on the next
            # launch, so preserve the failure details for diagnosis.
            log.warning("failed to remove autosave snapshot: %s", self.path(), exc_info=True)
