"""Open files handed to the app by the OS (double-click in Finder or Explorer,
or a file dropped on the app icon).

``.pman`` projects open as the document; ``.clip`` files are imported as a
CLIP STUDIO animation, the same way an in-window drop does it.

Windows passes the file on the command line; macOS sends a ``QFileOpenEvent``
instead, both at launch and while the app is already running. Events can
arrive before the main window exists, so they are queued until ``attach``.
"""
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer

PROJECT_SUFFIX = ".pman"
CLIP_SUFFIX = ".clip"
#: What the OS may hand us. Order is irrelevant; only the last file opens.
OPENABLE_SUFFIXES = (PROJECT_SUFFIX, CLIP_SUFFIX)


def openable_paths_from_argv(argv):
    """The openable files named on the command line (``argv[0]`` skipped)."""
    return [
        arg for arg in argv[1:]
        if Path(arg).suffix.lower() in OPENABLE_SUFFIXES and Path(arg).is_file()
    ]


class ProjectFileOpener(QObject):
    """Application-wide filter that turns file-open requests into opens."""

    def __init__(self, app, paths=()):
        super().__init__(app)
        self._window = None
        self._pending = list(paths)
        app.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FileOpen:
            path = event.file()
            if Path(path).suffix.lower() in OPENABLE_SUFFIXES:
                self._request(path)
                return True
        return False

    def attach(self, window):
        """Start opening into ``window``; queued files open once it is idle.

        Scheduled after the window's own startup work (the autosave restore
        prompt), so a launch file never silently replaces restored work.
        """
        self._window = window
        QTimer.singleShot(0, self._open_pending_at_launch)

    def _request(self, path):
        window = self._window
        if window is None:
            self._pending.append(path)
        else:
            # Already running: the canvas may hold unsaved work.
            self._open_asking(window, path)

    def _open_pending_at_launch(self):
        pending, self._pending = self._pending, []
        window = self._window
        if not pending or window is None:
            return
        # Only the last one: the window holds a single project.
        path = pending[-1]
        if self._is_clip(path):
            # The importer asks by itself, and stays quiet on a pristine canvas.
            window.importer.clip_animation(path)
        elif window.current_project_path is None:
            window.project.open(path)
        else:
            # An autosave was restored; ask before replacing it.
            window.project.open_dropped(path)

    def _open_asking(self, window, path):
        if self._is_clip(path):
            window.importer.clip_animation(path)
        else:
            window.project.open_dropped(path)

    @staticmethod
    def _is_clip(path):
        return Path(path).suffix.lower() == CLIP_SUFFIX
