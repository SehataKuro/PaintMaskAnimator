"""Frozen v2 semantics and canonical pixels; pytest never regenerates fixtures."""
import json
from zipfile import ZipFile

import pytest

from paintmaskanimator import project_io
from p5_6_support import observe
from test_project_boundaries import window as _window
from v2_golden_support import CASES, V2_ROOT, low_observation

window = _window


@pytest.mark.parametrize("case", CASES)
def test_v2_golden(window, tmp_path, case):
    path = V2_ROOT / f"{case}.pman"
    expected = json.loads((V2_ROOT / "expected" / f"{case}.json").read_text(encoding="utf-8"))
    with ZipFile(path) as archive:
        metadata = json.loads(archive.read("project.json"))
        assert metadata["format_version"] == metadata["mask_format"] == 2
        if case == "pending-compat":
            assert len(expected["low"]["durations"]) == 7
            assert expected["low"]["sheet_duration"] == 8
            assert len(expected["ui"]["durations"]) == 4
            assert expected["ui"]["sheet_duration"] == 4
            assert [p["number"] for p in metadata["tracks"][0]["pending"]] == [2, 2]
            assert metadata["tracks"][1]["pending"][0]["number"] is None
        assert all(info.date_time == (2000, 1, 1, 0, 0, 0) for info in archive.infolist())
    # Low-level roundtrip includes pending before UI placement.
    for generation in range(3):
        loaded = project_io.load_project(path)
        assert low_observation(window, loaded.document) == expected["low"]
        path = tmp_path / f"low-{generation}.pman"
        project_io.write_project_snapshot(path, project_io.ProjectSnapshot(
            loaded.metadata, loaded.document, loaded.width, loaded.height))
    path = V2_ROOT / f"{case}.pman"
    for generation in range(3):
        assert window.project.open(path)
        assert json.loads(json.dumps(observe(window))) == expected["ui"]
        path = tmp_path / f"ui-{generation}.pman"
        assert window.project.write(path)
