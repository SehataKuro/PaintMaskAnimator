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
