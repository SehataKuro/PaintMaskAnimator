import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from paintmaskanimator import config, cut_folder as cf  # noqa: E402
from paintmaskanimator.cut_folder import Token, block, text  # noqa: E402
from paintmaskanimator.cut_folder_dialog import (  # noqa: E402
    CutFolderExportDialog, TemplateEditor,
)
from paintmaskanimator.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    store = {}
    monkeypatch.setattr(config, "get_value", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(config, "set_value", lambda key, value: store.__setitem__(key, value))
    return store


@pytest.fixture
def window(qapp):
    window = MainWindow()
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()


def _draw_cels(window, count):
    canvas = window.canvas
    canvas._ensure_frame_count(count)
    for index in range(count):
        layer = canvas.frames[index].layers[0]
        layer.image.fill(QColor(255, 0, 0, 255))
        layer.has_content = True
        layer.is_blank_key = False
        layer.sequence_number = index + 1
        layer.exposure = 1
    canvas.frames[0].layers[0].name = "A"


def test_cut_folder_export_writes_cels_timesheet_and_empty_folders(window, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: None)
    _draw_cels(window, 2)
    cels, images, skipped = window.export.cut_folder_cels()
    assert [(c.cell, c.number) for c in cels] == [("A", 1), ("A", 2)]
    assert skipped == 0

    layout = cf.pma_standard_layout()
    layout.extra_folders = [(text("BG"),)]
    values = {"title": "PMA", "scene": "", "episode": "1", "cut": "12"}
    plan = cf.plan_export(layout, values, cels)
    assert plan.ok, plan.problems
    assert window.export.write_cut_folder(plan, tmp_path, images, "png")

    root = tmp_path / "PMA_01_c012"
    assert (root / "A" / "A0001.png").is_file()
    assert (root / "A" / "A0002.png").is_file()
    assert (root / "BG").is_dir() and not any((root / "BG").iterdir())
    sheet = (root / "PMA_01_c012.xdts").read_text(encoding="utf-8")
    payload = json.loads(sheet.split("\n", 1)[1])
    assert payload["timeTables"][0]["name"] == "PMA_01_c012"
    assert payload["timeTables"][0]["timeTableHeaders"][0]["names"][0] == "A"


def test_timesheet_folder_puts_the_sheet_in_a_subfolder(window, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: None)
    _draw_cels(window, 1)
    cels, images, _ = window.export.cut_folder_cels()
    layout = cf.pma_standard_layout()
    layout.timesheet_folder = (text("_ts"),)
    plan = cf.plan_export(layout, {"title": "PMA", "episode": "1", "cut": "3"}, cels)
    assert plan.timesheet_path == "_ts/PMA_01_c003.xdts"
    assert window.export.write_cut_folder(plan, tmp_path, images, "png")
    assert (tmp_path / "PMA_01_c003" / "_ts" / "PMA_01_c003.xdts").is_file()


def test_dialog_preview_and_validation(window, tmp_path):
    _draw_cels(window, 2)
    cels, _images, _ = window.export.cut_folder_cels()
    dialog = CutFolderExportDialog(cels, cut_hint="12", parent=window)
    try:
        dialog.value_edits["title"].setText("PMA")
        assert not dialog.export_button.isEnabled()  # 話数 and 保存先 missing
        dialog.value_edits["episode"].setText("4")
        dialog.destination.setText(str(tmp_path))
        assert dialog.export_button.isEnabled()
        root = dialog.tree.topLevelItem(0)
        assert root is not None
        assert root.text(0).startswith("PMA_04_c012/")
        names = [root.child(i).text(0) for i in range(root.childCount())]
        assert "A/" in names and "PMA_04_c012.xdts" in names

        dialog.timesheet_folder_editor.set_template((text("_sheet"),))
        dialog._refresh_preview()
        root = dialog.tree.topLevelItem(0)
        assert root is not None
        names = [root.child(i).text(0) for i in range(root.childCount())]
        assert "_sheet/" in names and "PMA_04_c012.xdts" not in names

        dialog.remember()
        saved = config.get_value("cut_folder_export")
        assert saved is not None
        assert saved["values"]["title"] == "PMA"
        assert "cut" not in saved["values"]
        assert saved["layout"]["timesheet_folder"] == [{"text": "_sheet"}]
    finally:
        dialog.deleteLater()


def test_editor_typing_blocks_and_separators(qapp):
    editor = TemplateEditor(cf.FIELD_IDS)
    editor.resize(300, 40)
    editor.show()
    editor.setFocus()
    qapp.processEvents()
    try:
        QTest.keyClicks(editor, "_")
        # "_" matches no block: picking one keeps "_" as a separator first.
        editor.insert_field("cut")
        QTest.keyClicks(editor, "x")
        QTest.keyClick(editor, Qt.Key.Key_Return)
        assert editor.template() == (text("_"), block("cut", digits=3), text("x"))

        # Caret to the start, delete the leading separator.
        editor.caret = 1
        QTest.keyClick(editor, Qt.Key.Key_Backspace)
        assert editor.template() == (block("cut", digits=3), text("x"))
    finally:
        editor.close()


def test_editor_chunk_boundaries():
    editor = TemplateEditor(cf.FIELD_IDS)
    editor.set_template((block("cut"), text("_timesheet.xdts")))
    end = len(editor.tokens)
    stops = []
    position = end
    while position > 0:
        position = editor._chunk_left(position)
        stops.append(position)
    # xdts | . | timesheet | _ | [cut]
    assert stops == [end - 4, end - 5, 2, 1, 0]
    assert editor._chunk_right(0) == 1
    assert editor._chunk_right(1) == 2


def test_empty_leading_token_list_round_trips():
    editor = TemplateEditor(cf.FIELD_IDS)
    editor.set_template(())
    assert editor.template() == ()
    editor.replace_token(0, Token(text="x"))  # out of range: ignored
    assert editor.template() == ()
