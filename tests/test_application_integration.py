"""Cross-module smoke tests for release metadata and project persistence."""
from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from paintmaskanimator import constants
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.project_io import read_project_archive, write_project_archive


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_runtime_version_matches_project_metadata():
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    expected = project["project"]["version"]
    assert importlib.metadata.version("paintmaskanimator") == expected
    assert constants.APP_VERSION == expected


def test_window_document_roundtrips_through_project_archive(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        layer = window.canvas.frames[0].layers[0]
        expected = QColor(12, 34, 56, 255)
        layer.image.setPixelColor(0, 0, expected)
        layer.has_content = True
        metadata = {
            "format": "PaintMaskAnimatorProject",
            "canvas": {
                "width": constants.CANVAS_WIDTH,
                "height": constants.CANVAS_HEIGHT,
            },
            "frames": [],
        }
        path = tmp_path / "smoke.pmap"

        write_project_archive(path, metadata, window.canvas.frames)
        loaded_metadata, frames, width, height = read_project_archive(path)

        assert loaded_metadata["format"] == "PaintMaskAnimatorProject"
        assert (width, height) == (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)
        assert frames[0].layers[0].has_content
        assert frames[0].layers[0].image.pixelColor(0, 0) == expected
    finally:
        window.close()
