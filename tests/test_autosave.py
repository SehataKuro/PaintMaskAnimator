"""Functional test for autosave write/clear (config dir redirected to tmp)."""
import os
import logging

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
        window.autosave.save()

        autosave = window.autosave.path()
        assert autosave.exists()
        assert autosave.stat().st_size > 0

        # The autosave is a valid, reloadable project archive.
        from paintmaskanimator import project_io
        _metadata, frames, _w, _h = project_io.read_project_archive(autosave)
        assert len(frames) == len(window.canvas.frames)

        window.autosave.clear()
        assert not autosave.exists()
    finally:
        # Destroy the window here (not just close) and drain deferred events so
        # the shared QApplication doesn't carry a live MainWindow into the next
        # GUI test module, which would stall it.
        window.close()
        window.deleteLater()
        window = None
        qapp.processEvents()
        qapp.processEvents()


def test_clear_autosave_logs_unlink_failure(caplog, monkeypatch, tmp_path):
    from paintmaskanimator.main_window_autosave import AutosaveController

    snapshot = tmp_path / "autosave.pmap"
    snapshot.write_bytes(b"snapshot")
    owner = AutosaveController(window=None)
    monkeypatch.setattr(owner, "path", lambda: snapshot)
    monkeypatch.setattr(type(snapshot), "unlink", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("locked")))

    with caplog.at_level(logging.WARNING, logger="paintmaskanimator.main_window_autosave"):
        owner.clear()

    assert "failed to remove autosave snapshot" in caplog.text
