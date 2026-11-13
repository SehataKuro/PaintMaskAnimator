"""Current user saves and dirty tracking, separate from frozen v1 boundaries."""
from dataclasses import replace
import json
from zipfile import ZipFile

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMessageBox

from paintmaskanimator import project_io
from p5_6_support import ROOT
from test_project_boundaries import window as _window

window = _window


def test_user_save_paths_write_only_v2(window, tmp_path, monkeypatch):
    assert window.project.open(ROOT / "v1" / "hidden-retained.pman")
    for mode in ("manual", "sync", "worker"):
        path = tmp_path / f"{mode}.pman"
        monkeypatch.setattr(window.autosave, "path", lambda path=path: path)
        if mode == "manual":
            assert window.project.write(path)
        elif mode == "sync":
            window.autosave.save()
        else:
            window.autosave.request_save()
            window.autosave._pending.result(timeout=10)
        with ZipFile(path) as archive:
            metadata = json.loads(archive.read("project.json"))
        assert metadata["format_version"] == 2
        assert "frames" not in metadata and "sequence_archive" not in metadata
        assert metadata["tracks"][0]["sheet"]
        assert project_io.load_project(path).format_version == 2
    window.autosave.clear()


def test_format_migration_does_not_leave_document_dirty(window, tmp_path):
    assert window.project.open(ROOT / "v1" / "pending-collision-2-2.pman")
    assert not window.project.has_unsaved_changes()
    path = tmp_path / "converted.pman"
    assert window.project.write(path)
    assert not window.project.has_unsaved_changes()
    assert window.project.open(path)
    assert not window.project.has_unsaved_changes()
    assert window.project.write(path)
    assert not window.project.has_unsaved_changes()


def test_signature_includes_live_local_artwork_and_compatibility(window):
    assert window.project.open(ROOT / "v1" / "hidden-retained.pman")
    slot = next(s for f in window.canvas.frames for s in f.layers if not s.has_content)
    signature = window.project._content_signature()
    slot._cell.image.fill(QColor("magenta"))
    assert window.project._content_signature() != signature
    window.project.mark_saved()
    assert window.project.open(ROOT / "v1" / "pending-collision-2-2.pman")
    payload, _ = project_io.build_v2_payload(project_io.ProjectSnapshot.capture(
        window.build_project_metadata(), window.canvas._document))
    assert payload["tracks"][0]["compatibility"]["length_overrides"]
    signature = window.project._content_signature()
    # The imported tail is not a live dataclass field or a bank Cell.
    for frame in window.canvas.frames:
        for slot in frame.layers:
            old = slot.__dict__.get("_v1_position")
            if old is not None and old.legacy is not None and old.legacy.has_content:
                slot._v1_position = replace(
                    old, legacy=replace(old.legacy, exposure=old.legacy.exposure + 1))
    assert window.project._content_signature() != signature


def test_signature_keeps_ui_exclusions(window):
    window.project.mark_saved()
    window.canvas.onion_skin = not window.canvas.onion_skin
    window.canvas.main_color = QColor("magenta")
    window.canvas.timeline_mode = "sequence"
    assert not window.project.has_unsaved_changes()


def test_v1_invalid_json_retains_original_dialog(window, tmp_path, monkeypatch):
    raw = '{"format": "PaintMaskAnimatorProject", "format_version": 1,'
    with pytest.raises(json.JSONDecodeError) as expected:
        json.loads(raw)
    path = tmp_path / "invalid.pman"
    with ZipFile(path, "w") as archive:
        archive.writestr("project.json", raw)
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args[-1]))
    assert not window.project.open(path)
    assert errors == ["プロジェクトを開けませんでした。\n\n" + str(expected.value)]


def test_signature_does_not_scan_or_encode_pixels(window, monkeypatch):
    from PySide6.QtGui import QImage

    def fail(*_args):
        pytest.fail("dirty tracking compared or encoded image pixels")

    for name in ("__eq__", "__ne__", "bits", "constBits", "scanLine",
                 "constScanLine", "pixel", "pixelColor", "save"):
        monkeypatch.setattr(QImage, name, fail)
    monkeypatch.setattr(project_io, "encode_png", fail)
    window.project.mark_saved()
    assert not window.project.has_unsaved_changes()
    window.canvas.frames[0].layers[0].image.fill(QColor("magenta"))
    assert window.project.has_unsaved_changes()


def test_signature_avoids_save_projection(window, monkeypatch):
    def fail(*_args, **_kwargs):
        pytest.fail("title timer built a save payload")

    monkeypatch.setattr(project_io, "build_v2_payload", fail)
    window.project.mark_saved()
    assert not window.project.has_unsaved_changes()


@pytest.mark.parametrize("change", ["input_length", "guard", "initial", "name"])
def test_signature_tracks_compatibility_inputs_and_local_state(window, change):
    assert window.project.open(ROOT / "v1" / "pending-collision-2-2.pman")
    window.project.mark_saved()
    if change in ("input_length", "guard"):
        slot = next(s for f in window.canvas.frames for s in f.layers
                    if s.__dict__.get("_v1_position") is not None
                    and s.__dict__.get("_dense_import") is not None
                    and s._v1_position.legacy.has_content)
        if change == "input_length":
            slot._v1_position = replace(slot._v1_position, input_length=123)
        else:
            slot.__dict__.pop("_dense_import")
    else:
        slot = next(s for f in window.canvas.frames for s in f.layers
                    if not ((s.has_content or s.sequence_only) and s.sequence_number is not None))
        if change == "initial":
            slot._cell.initial_image = slot._cell.image.copy()
        else:
            slot._cell.cell_name = "changed-local"
    assert window.project.has_unsaved_changes()
