"""カット袋・シートのメモ欄（Issue #25）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from paintmaskanimator import cut_folder as cf  # noqa: E402
from paintmaskanimator.cut_folder import text  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.memo_window import MEMO_FILE_NAME  # noqa: E402


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


def draw_cels(window, count):
    window.canvas._ensure_frame_count(count)
    for index in range(count):
        layer = window.canvas.frames[index].layers[0]
        layer.image.setPixelColor(10, 10, QColor(0, 0, 0))
        layer.has_content = True
        layer.sequence_number = index + 1
        layer.exposure = 1


def test_the_window_opens_once_and_is_reused(window):
    first = window.show_memo_window()
    second = window.show_memo_window()

    assert first is second


def test_typing_keeps_the_memo_on_the_window(window):
    memo = window.show_memo_window()

    memo.editor.setPlainText("3コマ目のタイミング要確認")

    assert window.project_memo == "3コマ目のタイミング要確認"


def test_setting_the_memo_shows_in_an_open_window(window):
    memo = window.show_memo_window()

    window.set_project_memo("作打ち合わせ済み")

    assert memo.text() == "作打ち合わせ済み"
    # 復元では textChanged を出さないので、書き戻しは起きない。
    assert window.project_memo == "作打ち合わせ済み"


def test_the_memo_travels_in_the_project_file(window, tmp_path):
    window.set_project_memo("ラフ参照のこと")
    path = tmp_path / "memo.pman"

    assert window.project.write(str(path))

    window.set_project_memo("")
    assert window.project.open(str(path))
    assert window.project_memo == "ラフ参照のこと"


def test_the_memo_is_written_next_to_the_timesheet(window, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: None)
    draw_cels(window, 1)
    window.set_project_memo("タップ位置に注意")
    cels, images, _skipped = window.export.cut_folder_cels()
    layout = cf.pma_standard_layout()
    layout.timesheet_folder = (text("_ts"),)
    plan = cf.plan_export(
        layout, {"title": "PMA", "scene": "", "episode": "1", "cut": "12"}, cels
    )
    assert plan.ok, plan.problems

    assert window.export.write_cut_folder(plan, tmp_path, images, "png")

    memo = tmp_path / "PMA_01_c012" / "_ts" / MEMO_FILE_NAME
    assert memo.is_file()
    assert memo.read_text(encoding="utf-8").strip() == "タップ位置に注意"


def test_no_memo_file_when_the_memo_is_blank(window, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: None)
    draw_cels(window, 1)
    window.set_project_memo("   ")
    cels, images, _skipped = window.export.cut_folder_cels()
    layout = cf.pma_standard_layout()
    plan = cf.plan_export(
        layout, {"title": "PMA", "scene": "", "episode": "1", "cut": "12"}, cels
    )

    assert window.export.write_cut_folder(plan, tmp_path, images, "png")

    assert not (tmp_path / "PMA_01_c012" / MEMO_FILE_NAME).exists()
