"""Functional test for autosave write/clear (config dir redirected to tmp)."""
import os
from typing import Any
import logging
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("background", [False, True])
def test_autosave_writes_and_clears(qapp, tmp_path, monkeypatch, background):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    from paintmaskanimator.main_window import MainWindow
    window = MainWindow()
    try:
        window.canvas.add_frame(dup=True)
        if background:
            window.autosave.request_save()
            window.autosave._pending.result(timeout=10)
        else:
            window.autosave.save()

        autosave = window.autosave.path()
        assert autosave.exists()
        assert autosave.stat().st_size > 0

        # The autosave is a valid, reloadable project archive.
        from paintmaskanimator import project_io
        loaded = project_io.load_project(autosave)
        assert loaded.format_version == 2
        frames = loaded.document.frames
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
    no_window: Any = None
    owner = AutosaveController(window=no_window)
    monkeypatch.setattr(owner, "path", lambda: snapshot)
    monkeypatch.setattr(type(snapshot), "unlink", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("locked")))

    with caplog.at_level(logging.WARNING, logger="paintmaskanimator.main_window_autosave"):
        owner.clear()

    assert "failed to remove autosave snapshot" in caplog.text


def test_periodic_save_keeps_ui_responsive_and_captures_snapshot(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QColor, QImage
    from paintmaskanimator import project_io
    from paintmaskanimator.models import Frame, Layer
    from paintmaskanimator.main_window_autosave import AutosaveController

    image = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("red"))
    layer = Layer("before", image, has_content=True, sequence_only=True)
    archived = layer.clone()
    archived.initial_image = image.copy()
    archived.cell_name = "before"
    archived.tween = {"points": [1, 2]}
    from paintmaskanimator.document import Document
    doc = Document()
    doc.install_legacy_frames([Frame([layer])], {(0, 2): archived})
    archived = doc.tracks[0].cells[2]
    metadata = {"nested": {"value": "before"}}
    window = SimpleNamespace(
        canvas=SimpleNamespace(drawing=False, frames=doc.frames, _document=doc),
        build_project_metadata=lambda: metadata,
    )
    owner = AutosaveController(window)
    monkeypatch.setattr(owner, "path", lambda: tmp_path / "autosave.pmap")
    entered, release = Event(), Event()
    captured = []
    ui_thread = get_ident()

    def blocked_write(path, snapshot, cache):
        captured.append((get_ident(), snapshot.metadata, snapshot.document.frames, {}, cache))
        entered.set()
        assert release.wait(5)
        path.write_bytes(b"snapshot")

    monkeypatch.setattr(project_io, "write_project_snapshot", blocked_write)
    try:
        owner.request_save()
        assert entered.wait(5)
        # An additional timeout must not queue another save.
        first = owner._pending
        owner.request_save()
        assert owner._pending is first
        serviced = []
        QTimer.singleShot(0, lambda: serviced.append(True))
        qapp.processEvents()
        assert serviced
        layer.name = "after"
        layer.image.fill(QColor("blue"))
        archived.image.fill(QColor("green"))
        archived.initial_image.fill(QColor("blue"))
        archived.cell_name = "after"
        from paintmaskanimator.document import clear_pending
        clear_pending(layer)
        doc.tracks[0].legacy_archive_hints[2].tween["points"].append(3)
        doc.remove_archive_hint(doc.tracks[0], 2)
        metadata["nested"]["value"] = "after"
        worker_thread, saved_metadata, frames, archive, _cache = captured[0]
        assert worker_thread != ui_thread
        assert saved_metadata["nested"]["value"] == "before"
        assert frames[0].layers[0].name == "before"
        assert frames[0].layers[0].image.pixelColor(0, 0) == QColor("red")
        assert frames[0].layers[0]._track.cells[2].image.pixelColor(0, 0) == QColor("red")
        saved_track = frames[0].layers[0]._track
        assert saved_track.cells[2].initial_image.pixelColor(0, 0) == QColor("red")
        assert saved_track.cells[2].cell_name == "before"
        assert frames[0].layers[0].sequence_only
        assert saved_track.legacy_archive_hints[2].tween == {"points": [1, 2]}
        assert archive == {}
    finally:
        release.set()
        owner.clear()
    # clear waits for the writer before removing the crash-recovery file.
    assert not owner.path().exists()


def test_periodic_save_defers_during_stroke_and_ignores_closed_window(monkeypatch):
    from paintmaskanimator import main_window_autosave

    retries = []
    monkeypatch.setattr(main_window_autosave.QTimer, "singleShot",
                        lambda delay, callback: retries.append(callback))
    window = SimpleNamespace(canvas=SimpleNamespace(drawing=True), _closing=False)
    owner = main_window_autosave.AutosaveController(window)
    owner.request_save()
    assert len(retries) == 1
    assert owner._pending is None
    window._closing = True
    retries[0]()
    assert len(retries) == 1
    assert owner._executor is None


def test_autosave_snapshot_keeps_compatibility_and_local_independent(qapp, tmp_path, monkeypatch):
    import json
    from dataclasses import replace
    from zipfile import ZipFile
    from PySide6.QtGui import QColor
    from paintmaskanimator import constants, project_io
    from paintmaskanimator.document import Document
    from paintmaskanimator.main_window_autosave import AutosaveController
    from paintmaskanimator.models import Frame
    from p5_6_support import image, slot

    monkeypatch.setattr(constants, "CANVAS_WIDTH", 4)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 3)
    doc = Document()
    doc.install_legacy_frames([
        Frame([slot(1, "red", has_content=True, exposure=7)]),
        Frame([slot(None, "green", is_blank_key=True,
                    initial_image=image("blue"), cell_name="local")])])
    metadata = {"canvas": {"width": 4, "height": 3}}
    owner = AutosaveController(SimpleNamespace(
        canvas=SimpleNamespace(drawing=False, _document=doc),
        build_project_metadata=lambda: metadata))
    path = tmp_path / "snapshot.pman"
    monkeypatch.setattr(owner, "path", lambda: path)
    expected, _ = project_io.build_v2_payload(project_io.ProjectSnapshot.capture(metadata, doc))
    entered, release = Event(), Event()
    writer = project_io.write_project_snapshot

    def blocked_write(destination, snapshot, cache):
        entered.set()
        assert release.wait(5)
        writer(destination, snapshot, cache)

    monkeypatch.setattr(project_io, "write_project_snapshot", blocked_write)
    try:
        owner.request_save()
        assert entered.wait(5)
        first, local = (frame.layers[0] for frame in doc.frames)
        position = first.__dict__["_v1_position"]
        first.__dict__["_v1_position"] = replace(
            position, legacy=replace(position.legacy, exposure=11), input_length=13)
        first.__dict__.pop("_dense_import", None)
        local.image.fill(QColor("yellow"))
        local.initial_image.fill(QColor("red"))
        local.cell_name = "changed"
        release.set()
        owner._pending.result(timeout=10)
        with ZipFile(path) as archive:
            assert json.loads(archive.read("project.json")) == expected
        loaded = project_io.load_project(path)
        saved_local = loaded.document.frames[1].layers[0]
        assert saved_local.image.pixelColor(0, 0) == QColor("green")
        assert saved_local.initial_image.pixelColor(0, 0) == QColor("blue")
        assert saved_local.cell_name == "local"
        assert loaded.document.frames[0].layers[0].__dict__["_v1_position"].input_length == 7
    finally:
        release.set()
        owner.clear()
