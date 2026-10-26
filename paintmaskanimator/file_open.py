"""Open ``.pman`` files handed to the app by the OS (double-click in Finder or
Explorer).

Windows passes the file on the command line; macOS sends a ``QFileOpenEvent``
instead, both at launch and while the app is already running. Events can
arrive before the main window exists, so they are queued until ``attach``.
"""
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer

PROJECT_SUFFIX = ".pman"


def project_paths_from_argv(argv):
    """The ``.pman`` files named on the command line (``argv[0]`` skipped)."""
    return [
        arg for arg in argv[1:]
        if Path(arg).suffix.lower() == PROJECT_SUFFIX and Path(arg).is_file()
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
            if Path(path).suffix.lower() == PROJECT_SUFFIX:
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
        if self._window is None:
            self._pending.append(path)
        else:
            # Already running: the canvas may hold unsaved work.
            self._window.project.open_dropped(path)

    def _open_pending_at_launch(self):
        pending, self._pending = self._pending, []
        if not pending or self._window is None:
            return
        # Only the last one: the window holds a single project.
        path = pending[-1]
        if self._window.current_project_path is None:
            self._window.project.open(path)
        else:
            # An autosave was restored; ask before replacing it.
            self._window.project.open_dropped(path)
