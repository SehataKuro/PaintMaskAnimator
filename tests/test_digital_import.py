"""読み込みのデジタル／アナログの分岐と、デジタルの全コマ検査（#63）。"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from paintmaskanimator import imaging, preferences
from paintmaskanimator.main_window import MainWindow

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


def save(tmp_path, frames):
    paths = []
    for index, pixels in enumerate(frames):
        path = tmp_path / f"A{index + 1:04d}.png"
        assert imaging.rgba_array_to_qimage(pixels).save(str(path))
        paths.append(str(path))
    return paths


class Answers:
    """QMessageBox を objectName ごとに決めたボタンで答える（``patch`` で差し込む）。"""

    def __init__(self, **choices):
        self.choices = choices
        self.seen = {}

    def __call__(self, box):
        name = box.objectName()
        self.seen[name] = box
        label = self.choices.get(name)
        if label is None:
            raise AssertionError(f"unexpected box {name!r}: {box.text()!r}")
        next(button for button in box.buttons() if button.text() == label).click()
        return 0


def patch(monkeypatch, answers):
    monkeypatch.setattr(QMessageBox, "exec", lambda box: answers(box))
    return answers


def test_the_material_choice_is_remembered(window, monkeypatch):
    patch(monkeypatch, Answers(importMaterialQuestion="アナログ（スキャン）"))
    assert window.ask_import_material() == "analog"
    assert preferences.import_material_kind() == "analog"

    answers = Answers(importMaterialQuestion="キャンセル")
    patch(monkeypatch, answers)
    assert window.ask_import_material() is None
    box = answers.seen["importMaterialQuestion"]
    assert box.defaultButton().text() == "アナログ（スキャン）", "前回の選択が既定"


def test_a_clean_digital_sequence_imports_without_asking(window, tmp_path, monkeypatch):
    paths = save(tmp_path, [samples.tp(), samples.tp()])
    patch(monkeypatch, Answers(importMaterialQuestion="デジタル（TP）"))

    assert window.prepare_color_reduction(paths) == (None, "")


def test_antialias_only_on_the_second_frame_is_found_and_listed(window, tmp_path, monkeypatch):
    paths = save(tmp_path, [samples.tp(), samples.antialiased(), samples.tp()])
    answers = Answers(
        importMaterialQuestion="デジタル（TP）", digitalAntialiasReport="取り除いて読み込む",
    )
    patch(monkeypatch, answers)

    settings, error = window.prepare_color_reduction(paths)

    assert error == ""
    assert settings["extraction_mode"] == "digital"
    report = answers.seen["digitalAntialiasReport"]
    assert "1/3" in report.text()
    assert "2枚目" in report.detailedText() and "1枚目" not in report.detailedText()


def test_removing_antialias_on_import_keeps_the_original_colours(window, tmp_path, monkeypatch):
    paths = save(tmp_path, [samples.antialiased()])
    patch(monkeypatch, Answers(
        importMaterialQuestion="デジタル（TP）", digitalAntialiasReport="取り除いて読み込む",
    ))
    settings, _error = window.prepare_color_reduction(paths)

    image, _error = window.canvas._read_image_file(paths[0])
    reduced = imaging.qimage_rgba_array(window.canvas.reduce_image_with_settings(image, settings))

    assert samples.colours(reduced) == {
        samples.SKIN, samples.HAIR, samples.RED, samples.BLUE, samples.GREEN, samples.BLACK,
    }
    assert set(np.unique(reduced[:, :, 3]).tolist()) <= {0, 255}


def test_keeping_the_antialias_imports_as_is(window, tmp_path, monkeypatch):
    paths = save(tmp_path, [samples.antialiased()])
    patch(monkeypatch, Answers(
        importMaterialQuestion="デジタル（TP）", digitalAntialiasReport="そのまま読み込む",
    ))
    assert window.prepare_color_reduction(paths) == (None, "")


def test_analog_uses_the_binarize_dialog(window, tmp_path, monkeypatch):
    paths = save(tmp_path, [samples.antialiased()])
    patch(monkeypatch, Answers(importMaterialQuestion="アナログ（スキャン）"))
    opened = []

    def reject(dialog):
        opened.append(type(dialog).__name__)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", reject)

    assert window.prepare_color_reduction(paths) == (None, "__cancelled__")
    assert opened == ["ColorReductionDialog"]
