"""Autosave / crash recovery; owned by ``MainWindow`` as ``window.autosave``.

Owns the autosave timer and the crash-recovery flow, writing through the
window's ``canvas``, ``timeline`` and ``build_project_metadata``.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from typing import TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor
import copy

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from . import config, preferences, project_io
from .i18n import tr
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
        self._executor = None
        self._pending = None
        self._png_cache = project_io.PngCache()

    """Periodic best-effort project snapshots + startup crash recovery."""

    def path(self):
        return config.config_dir() / "autosave.pmap"

    def start(self):
        """Periodically snapshot the project so a crash doesn't lose work."""
        self.window._autosave_timer = QTimer(self.window)
        self.window._autosave_timer.timeout.connect(self.request_save)
        self.apply_preferences()

    def apply_preferences(self):
        """(Re)start or stop the timer from the autosave preferences."""
        timer = self.window._autosave_timer
        timer.setInterval(preferences.autosave_interval_minutes() * 60 * 1000)
        if preferences.autosave_enabled():
            timer.start()
        else:
            timer.stop()

    def request_save(self):
        """Capture on the UI thread; compress and write outside the event loop."""
        if getattr(self.window, "_closing", False):
            return
        if self._pending is not None and not self._pending.done():
            return
        if self.window.canvas.drawing:
            # Do not capture a half-finished stroke or share an active painter.
            QTimer.singleShot(1000, self.request_save)
            return
        try:
            metadata = copy.deepcopy(self.window.build_project_metadata())
            frames = [frame.clone() for frame in self.window.canvas.frames]
            sequence_archive = {
                key: layer.clone()
                for key, layer in self.window.canvas._sequence_archive.items()
            }
            path = self.path()
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="pma-autosave"
                )
            # Cloned QImages share pixels until an edit detaches them. The worker
            # owns its PNG cache, so manual saves/opens cannot race with it.
            self._pending = self._executor.submit(
                self._write, path, metadata, frames, sequence_archive, self._png_cache
            )
        except Exception:
            log.exception("autosave snapshot failed")

    @staticmethod
    def _write(path, metadata, frames, sequence_archive, png_cache):
        try:
            project_io.write_project_archive(
                path, metadata, frames, sequence_archive, png_cache
            )
        except Exception:
            log.exception("autosave failed")

    def save(self):
        # Explicit synchronous saves must not collide with a periodic write.
        if self._pending is not None:
            self._pending.result()
        # Must never raise into the event loop — autosave is best-effort.
        try:
            project_io.write_project_archive(
                self.path(),
                self.window.build_project_metadata(),
                self.window.canvas.frames,
                self.window.canvas._sequence_archive,
                self.window.png_cache,
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
            tr("作業の復元"),
            tr("前回のセッションが正常に終了しなかった可能性があります。\n"
            "自動保存された作業を復元しますか？"),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.window.project.open(str(path))
        else:
            self.clear()

    def clear(self):
        # Finish an in-flight atomic replacement before deleting the snapshot;
        # otherwise it could reappear after a clean shutdown.
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
            self._pending = None
        self._png_cache.clear()
        try:
            self.path().unlink(missing_ok=True)
        except OSError:
            # A stale snapshot can trigger another recovery prompt on the next
            # launch, so preserve the failure details for diagnosis.
            log.warning("failed to remove autosave snapshot: %s", self.path(), exc_info=True)
