"""Functional test for autosave write/clear (config dir redirected to tmp)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def test_autosave_writes_and_clears(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    from paintmaskanimator.main_window import MainWindow
    window = MainWindow()
    try:
        window.canvas.add_frame(dup=True)
        window._autosave()

        autosave = window._autosave_path()
        assert autosave.exists()
        assert autosave.stat().st_size > 0

        # The autosave is a valid, reloadable project archive.
        from paintmaskanimator import project_io
        _metadata, frames, _w, _h = project_io.read_project_archive(autosave)
        assert len(frames) == len(window.canvas.frames)

        window._clear_autosave()
        assert not autosave.exists()
    finally:
        window.close()
