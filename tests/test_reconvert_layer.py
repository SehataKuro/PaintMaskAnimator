"""読み込み直さずに、元のファイルから変換し直す（#10）。"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from paintmaskanimator import imaging
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.undo_entries import ScopedBatchUndo

import test_digital_antialias as samples


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def answer_with(monkeypatch, **choices):
    def answer(box):
        label = choices.get(box.objectName(), "保存しない")
        next(button for button in box.buttons() if button.text() == label).click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", lambda box: answer(box))


def import_antialiased(window, tmp_path, monkeypatch, count=2):
    paths = []
    for index in range(count):
        path = tmp_path / f"A{index + 1:04d}.png"
        assert imaging.rgba_array_to_qimage(samples.antialiased()).save(str(path))
        paths.append(str(path))
    answer_with(monkeypatch, importMaterialQuestion="デジタル（TP）",
                digitalAntialiasReport="そのまま読み込む")
    window.import_dropped_images(paths)
    return paths


def layer_colours(window, frame=0):
    layer = window.canvas.frames[frame].layers[window.canvas.active_layer_index]
    rgba = imaging.qimage_rgba_array(layer.image.to_qimage())
    visible = rgba[:, :, 3] > 0
    return {tuple(int(v) for v in rgb) for rgb in rgba[visible][:, :3]}


def test_the_imported_layer_remembers_its_files(window, tmp_path, monkeypatch):
    paths = import_antialiased(window, tmp_path, monkeypatch)
    assert window.canvas.active_layer._track.import_sources == paths


def test_reconverting_removes_the_antialias_kept_at_import_and_undoes_once(window, tmp_path, monkeypatch):
    import_antialiased(window, tmp_path, monkeypatch)
    before = layer_colours(window)
    assert len(before) > 6, "そのまま読み込んだのでアンチエイリアスが残っている"

    assert window.reconvert_layer({"extraction_mode": "digital", "palette": None})

    expected = {samples.SKIN, samples.HAIR, samples.RED, samples.BLUE, samples.GREEN, samples.BLACK}
    assert layer_colours(window, 0) == expected
    assert layer_colours(window, 1) == expected
    assert isinstance(window.canvas.undo_stack[-1], ScopedBatchUndo)
    window.canvas.undo()
    assert layer_colours(window) == before


def test_the_dialog_runs_the_import_flow_again(window, tmp_path, monkeypatch):
    import_antialiased(window, tmp_path, monkeypatch, count=1)
    answer_with(monkeypatch, importMaterialQuestion="デジタル（TP）",
                digitalAntialiasReport="取り除いて読み込む")
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes)

    assert window.reconvert_layer_dialog()
    assert len(layer_colours(window)) == 6


def test_layers_not_imported_as_a_sequence_cannot_be_reconverted(window):
    window.canvas.active_layer_index = 0
    assert window.reconvert_layer_dialog() is False


def test_missing_files_are_reported(window, tmp_path, monkeypatch):
    paths = import_antialiased(window, tmp_path, monkeypatch, count=1)
    for path in paths:
        __import__("os").remove(path)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **_k: warnings.append(args))
    assert window.reconvert_layer_dialog() is False
    assert warnings and "見つかりません" in warnings[0][2]


def test_import_sources_survive_a_document_undo(window, tmp_path, monkeypatch):
    paths = import_antialiased(window, tmp_path, monkeypatch, count=1)
    window.canvas.push_doc_undo()
    window.canvas.undo()
    assert np.array_equal(
        np.array(window.canvas.active_layer._track.import_sources or []), np.array(paths)
    )


def test_import_sources_are_saved_and_reopened(window, tmp_path, monkeypatch):
    from paintmaskanimator import project_io

    paths = import_antialiased(window, tmp_path, monkeypatch, count=2)
    canvas = window.canvas
    layer_index = canvas.active_layer_index
    image = canvas.frames[0].layers[0].image
    project = tmp_path / "with_sources.pman"
    project_io.write_project_snapshot(
        project, project_io.ProjectSnapshot({}, canvas._document, image.width, image.height)
    )

    reopened = project_io.load_project(project)
    assert reopened.document.tracks[layer_index].import_sources == paths
    assert reopened.document.tracks[0].import_sources is None, "読み込んでいないレイヤーには無い"
