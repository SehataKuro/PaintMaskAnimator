"""Roundtrip tests for the extracted project serializer — no widget needed."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from paintmaskanimator import constants, project_io  # noqa: E402
from paintmaskanimator.models import make_frame  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _metadata():
    return {
        "format": "PaintMaskAnimatorProject",
        "format_version": 1,
        "canvas": {
            "width": int(constants.CANVAS_WIDTH),
            "height": int(constants.CANVAS_HEIGHT),
        },
        "current_frame": 1,
        "active_layer_index": 0,
        "frames": [],
    }


def test_write_read_roundtrip(qapp, tmp_path):
    frames = [make_frame(), make_frame()]
    frames[0].layers[0].name = "Custom"
    frames[0].layers[0].visible = False
    frames[0].layers[0].opacity = 0.5
    frames[1].duration = 3

    path = tmp_path / "proj.pmap"
    project_io.write_project_archive(path, _metadata(), frames)
    assert path.exists()

    metadata, loaded, width, height = project_io.read_project_archive(path)
    assert (width, height) == (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)
    assert len(loaded) == 2
    assert loaded[0].layers[0].name == "Custom"
    assert loaded[0].layers[0].visible is False
    assert abs(loaded[0].layers[0].opacity - 0.5) < 1e-6
    assert loaded[1].duration == 3
    assert metadata["current_frame"] == 1


def test_legacy_white_migrates_to_transparent(qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage

    frames = [make_frame()]
    # Non-paper content layer with a legacy opaque-white erased pixel plus a
    # real coloured pixel; and the paper layer which must stay white.
    content = next(
        layer for layer in frames[0].layers
        if not getattr(layer, "is_paper", False)
    )
    paper = next(
        (layer for layer in frames[0].layers if getattr(layer, "is_paper", False)),
        None,
    )
    content.image.setPixelColor(0, 0, QColor(255, 255, 255, 255))
    content.image.setPixelColor(1, 0, QColor(0, 0, 255, 255))
    content.has_content = True

    # Simulate a legacy file: write, then strip the mask_format marker.
    metadata = _metadata()
    path = tmp_path / "legacy.pmap"
    project_io.write_project_archive(path, metadata, frames)

    import json
    import zipfile
    raw = tmp_path / "legacy_raw.pmap"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(raw, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "project.json":
                meta = json.loads(data.decode("utf-8"))
                meta.pop("mask_format", None)
                data = json.dumps(meta).encode("utf-8")
            zout.writestr(info, data)

    _meta, loaded, _w, _h = project_io.read_project_archive(raw)
    out_content = next(
        layer for layer in loaded[0].layers
        if not getattr(layer, "is_paper", False)
    )
    rgba = out_content.image.convertToFormat(QImage.Format.Format_RGBA8888)
    assert rgba.pixelColor(0, 0).alpha() == 0  # legacy white erased
    assert rgba.pixelColor(1, 0) == QColor(0, 0, 255, 255)  # colour preserved
    if paper is not None:
        out_paper = next(
            layer for layer in loaded[0].layers
            if getattr(layer, "is_paper", False)
        )
        assert out_paper.image.pixelColor(
            constants.CANVAS_WIDTH // 2, constants.CANVAS_HEIGHT // 2
        ).alpha() == 255  # paper stays opaque white


def test_current_save_is_mask_format_2(qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage

    frames = [make_frame()]
    content = next(
        layer for layer in frames[0].layers
        if not getattr(layer, "is_paper", False)
    )
    content.image.setPixelColor(2, 2, QColor(255, 255, 255, 255))
    content.has_content = True

    metadata = dict(_metadata())
    metadata["mask_format"] = project_io.CURRENT_MASK_FORMAT
    path = tmp_path / "modern.pmap"
    project_io.write_project_archive(path, metadata, frames)

    _meta, loaded, _w, _h = project_io.read_project_archive(path)
    out_content = next(
        layer for layer in loaded[0].layers
        if not getattr(layer, "is_paper", False)
    )
    rgba = out_content.image.convertToFormat(QImage.Format.Format_RGBA8888)
    # No legacy migration on a current-format file: white stays as stored.
    assert rgba.pixelColor(2, 2) == QColor(255, 255, 255, 255)


def test_read_rejects_non_project(qapp, tmp_path):
    import zipfile
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("project.json", '{"format": "Nope", "frames": []}')
    with pytest.raises(ValueError):
        project_io.read_project_archive(path)


def test_read_rejects_missing_metadata(qapp, tmp_path):
    import zipfile
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("nothing.txt", "x")
    with pytest.raises(ValueError):
        project_io.read_project_archive(path)


def test_write_fills_metadata_frames(qapp, tmp_path):
    """write_project_archive should populate metadata['frames'] from the
    domain objects (2 frames -> 2 entries)."""
    md = _metadata()
    project_io.write_project_archive(tmp_path / "p.pmap", md, [make_frame()])
    assert len(md["frames"]) == 1
    assert md["frames"][0]["layers"][0]["name"]


def test_failed_write_preserves_existing_project(qapp, tmp_path, monkeypatch):
    path = tmp_path / "important.pmap"
    original = b"existing project data"
    path.write_bytes(original)

    class BrokenArchive:
        def __init__(self, *_args, **_kwargs):
            raise OSError("disk full")

    monkeypatch.setattr(project_io.zipfile, "ZipFile", BrokenArchive)

    with pytest.raises(OSError, match="disk full"):
        project_io.write_project_archive(path, _metadata(), [make_frame()])

    assert path.read_bytes() == original
    assert not path.with_suffix(".pmap.tmp").exists()
