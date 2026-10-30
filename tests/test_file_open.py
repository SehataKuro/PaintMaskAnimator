"""Opening .pman and .clip files handed over by the OS (double-click)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtGui import QFileOpenEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.file_open import (  # noqa: E402
    ProjectFileOpener,
    openable_paths_from_argv,
)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class _Project:
    def __init__(self):
        self.calls = []

    def open(self, path):
        self.calls.append(("open", str(path)))

    def open_dropped(self, path):
        self.calls.append(("open_dropped", str(path)))


class _Importer:
    def __init__(self):
        self.calls = []

    def clip_animation(self, path):
        self.calls.append(("clip_animation", str(path)))
        return True


class _Window:
    def __init__(self, current_project_path=None):
        self.project = _Project()
        self.importer = _Importer()
        self.current_project_path = current_project_path


def _flush():
    QCoreApplication.processEvents()


def test_argv_keeps_only_existing_openable_files(tmp_path):
    project = tmp_path / "a.PMAN"
    project.write_bytes(b"")
    clip = tmp_path / "c.Clip"
    clip.write_bytes(b"")
    other = tmp_path / "b.png"
    other.write_bytes(b"")
    argv = [
        "app", str(project), str(clip), str(other),
        str(tmp_path / "missing.pman"), "-psn_0_1",
    ]
    assert openable_paths_from_argv(argv) == [str(project), str(clip)]


def test_launch_file_opens_directly(qapp):
    opener = ProjectFileOpener(qapp, ["/x/first.pman"])
    try:
        QCoreApplication.sendEvent(qapp, QFileOpenEvent("/x/second.pman"))
        window = _Window()
        opener.attach(window)
        _flush()
        # One project at a time: the last request wins.
        assert window.project.calls == [("open", "/x/second.pman")]
    finally:
        qapp.removeEventFilter(opener)


def test_launch_file_asks_after_autosave_restore(qapp):
    opener = ProjectFileOpener(qapp, ["/x/a.pman"])
    try:
        window = _Window(current_project_path="/autosave.pman")
        opener.attach(window)
        _flush()
        assert window.project.calls == [("open_dropped", "/x/a.pman")]
    finally:
        qapp.removeEventFilter(opener)


def test_file_while_running_asks_to_save(qapp):
    opener = ProjectFileOpener(qapp)
    try:
        window = _Window()
        opener.attach(window)
        _flush()
        assert QCoreApplication.sendEvent(qapp, QFileOpenEvent("/x/b.pman"))
        assert window.project.calls == [("open_dropped", "/x/b.pman")]
        # Other file types are left for Qt / other handlers.
        assert not QCoreApplication.sendEvent(qapp, QFileOpenEvent("/x/c.png"))
        assert len(window.project.calls) == 1
    finally:
        qapp.removeEventFilter(opener)


def test_launch_clip_imports_without_touching_the_project(qapp):
    opener = ProjectFileOpener(qapp, ["/x/a.clip"])
    try:
        window = _Window()
        opener.attach(window)
        _flush()
        assert window.importer.calls == [("clip_animation", "/x/a.clip")]
        assert window.project.calls == []
    finally:
        qapp.removeEventFilter(opener)


def test_clip_while_running_imports(qapp):
    opener = ProjectFileOpener(qapp)
    try:
        window = _Window(current_project_path="/open.pman")
        opener.attach(window)
        _flush()
        assert QCoreApplication.sendEvent(qapp, QFileOpenEvent("/x/b.clip"))
        assert window.importer.calls == [("clip_animation", "/x/b.clip")]
        assert window.project.calls == []
    finally:
        qapp.removeEventFilter(opener)
