"""取り込み連番の再変換（元ファイルから設定だけ変えて作り直す）のテスト。"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from paintmaskanimator import despeckle, imaging
from paintmaskanimator.canvas import PaintCanvas


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _write_sequence(tmp_path, count=3):
    paths = []
    for index in range(count):
        rgba = np.zeros((24, 24, 4), dtype=np.uint8)
        rgba[:, :, :3] = 255
        rgba[:, :, 3] = 255
        rgba[6:18, 6:18, :3] = (20 + index * 40, 30, 40)
        # 孤立した1pxのゴミ。
        rgba[2, 21, :3] = (0, 0, 0)
        path = tmp_path / f"cut_{index:03d}.png"
        imaging.rgba_array_to_qimage(rgba).save(str(path))
        paths.append(str(path))
    return paths


def _layer_pixels(canvas, frame_index, layer_index):
    return imaging.qimage_rgba_array(
        canvas.frames[frame_index].layers[layer_index].image
    )


def test_import_records_reconversion_state(qapp, tmp_path):
    canvas = PaintCanvas()
    try:
        paths = _write_sequence(tmp_path)
        ok, error = canvas.import_image_sequence(paths)
        assert ok, error
        assert canvas._sequence_import_paths == paths
        assert canvas._sequence_import_start_frame == 0
        assert canvas._sequence_source_bank_layer_index >= 0
    finally:
        canvas.deleteLater()


def test_reconvert_applies_new_despeckle_stage_in_place(qapp, tmp_path):
    canvas = PaintCanvas()
    try:
        paths = _write_sequence(tmp_path)
        assert canvas.import_image_sequence(paths)[0]
        layer_index = canvas._sequence_source_bank_layer_index
        layer_count = len(canvas.frames[0].layers)

        pixels = _layer_pixels(canvas, 0, layer_index)
        speck = np.argwhere(
            np.all(pixels[:, :, :3] == 0, axis=2) & (pixels[:, :, 3] > 0)
        )
        assert len(speck) == 1

        ok, error = canvas.reconvert_imported_sequence(
            {
                "despeckle": {
                    "enabled": True,
                    "mode": despeckle.DUST_MODE,
                    "max_area": 4,
                },
                "despeckle_removal_rgba": (0, 0, 0, 0),
            }
        )
        assert ok, error

        # レイヤーは増えず、同じコマが置き換わる。
        assert len(canvas.frames[0].layers) == layer_count
        for frame_index in range(len(paths)):
            pixels = _layer_pixels(canvas, frame_index, layer_index)
            assert not np.any(np.all(pixels[:, :, :3] == 0, axis=2) & (pixels[:, :, 3] > 0))
            # 本体の塗りは残っている。
            assert np.any(pixels[:, :, 3] > 0)
    finally:
        canvas.deleteLater()


def test_reconvert_without_import_reports_error(qapp):
    canvas = PaintCanvas()
    try:
        ok, error = canvas.reconvert_imported_sequence({})
        assert not ok
        assert "再変換できる" in error
    finally:
        canvas.deleteLater()


def test_reconvert_reports_missing_source_files(qapp, tmp_path):
    canvas = PaintCanvas()
    try:
        paths = _write_sequence(tmp_path, count=2)
        assert canvas.import_image_sequence(paths)[0]
        for path in paths:
            (tmp_path / path.rsplit("/", 1)[-1]).unlink()
        ok, error = canvas.reconvert_imported_sequence({})
        assert not ok
        assert "見つかりません" in error
    finally:
        canvas.deleteLater()
