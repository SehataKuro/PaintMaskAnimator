"""Autosave / crash-recovery behaviour for :class:`MainWindow`.

Split out of ``main_window.py`` as a mixin to shrink that module. The methods
here run against a live ``MainWindow`` instance, so they rely on attributes and
methods defined on the main class (``self.canvas``, ``self.timeline``,
``self.build_project_metadata``, ``self.open_project``); the mixin only owns the
autosave timer and the crash-recovery flow.
"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from . import config, project_io
from .logging_setup import get_logger
from ._main_window_members import MainWindowMembers

log = get_logger(__name__)


class AutosaveMixin(MainWindowMembers):
    """Periodic best-effort project snapshots + startup crash recovery."""

    def _autosave_path(self):
        return config.config_dir() / "autosave.pmap"

    def _setup_autosave(self, interval_ms=180000):
        """Periodically snapshot the project so a crash doesn't lose work."""
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(int(interval_ms))
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

    def _autosave(self):
        # Must never raise into the event loop — autosave is best-effort.
        try:
            project_io.write_project_archive(
                self._autosave_path(),
                self.build_project_metadata(),
                self.canvas.frames,
            )
        except Exception:  # noqa: BLE001 - best-effort; must never raise into the event loop
            # Autosave is best-effort and must never raise into the event loop,
            # so the broad catch is intentional; log the traceback instead of
            # printing it so it lands in the app log.
            log.exception("autosave failed")

    def _maybe_restore_autosave(self):
        """On startup, offer to restore a leftover autosave (likely a crash)."""
        path = self._autosave_path()
        try:
            if not path.exists() or path.stat().st_size == 0:
                return
        except OSError:
            return
        answer = QMessageBox.question(
            self,
            "作業の復元",
            "前回のセッションが正常に終了しなかった可能性があります。\n"
            "自動保存された作業を復元しますか？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.open_project(str(path))
        else:
            self._clear_autosave()

    def _clear_autosave(self):
        try:
            self._autosave_path().unlink(missing_ok=True)
        except OSError:
            pass
