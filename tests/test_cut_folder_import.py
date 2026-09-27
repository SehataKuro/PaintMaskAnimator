"""カットフォルダーを開く: 書き出したフォルダーがそのまま読み戻せる。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from paintmaskanimator import config, constants, cut_folder as cf, timesheet_file  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def restore_canvas_size():
    # 読み込みはキャンバスの大きさ（モジュールの値）をセルに合わせて変える。
    saved = (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)
    yield
    constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = saved


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    store = {}
    monkeypatch.setattr(config, "get_value", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(config, "set_value", lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: None)
    return store


@pytest.fixture
def window(qapp):
    window = MainWindow()
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()


COLORS = {1: QColor(255, 0, 0), 2: QColor(0, 0, 255)}


def _draw_timeline(window):
    """A: セル1を2コマ → 空セル1コマ → セル2を1コマ。"""
    canvas = window.canvas
    canvas._ensure_frame_count(4)
    layers = [frame.layers[0] for frame in canvas.frames]
    for layer in layers:
        layer.has_content = False
        layer.is_blank_key = False
        layer.sequence_number = None
    for frame, number, exposure in ((0, 1, 2), (3, 2, 1)):
        layer = layers[frame]
        layer.image.fill(COLORS[number])
        layer.has_content = True
        layer.sequence_number = number
        layer.exposure = exposure
    layers[2].is_blank_key = True
    layers[2].exposure = 1
    canvas.frames[0].layers[0].name = "A"


def _export(window, parent, layout, values):
    cels, images, _ = window.export.cut_folder_cels()
    plan = cf.plan_export(layout, values, cels)
    assert plan.ok, plan.problems
    assert window.export.write_cut_folder(plan, parent, images, layout.image_format)
    return parent / plan.folder_name


def _shown(window, frame):
    """フレームに表示されるセル番号（空なら None）。"""
    from paintmaskanimator.timeline import TimelineWidget

    kind, key, _ = TimelineWidget.timeline_span_at(window.canvas.frames, 0, frame)
    if kind != "content":
        return None
    return window.canvas.frames[key].layers[0].sequence_number


@pytest.mark.parametrize(
    "layout, values",
    [
        (cf.pma_standard_layout(), {"title": "PMA", "episode": "1", "cut": "12"}),
        (cf.ts_pool_layout(), {"title": "PMA", "cut": "2"}),
    ],
)
def test_exported_cut_folder_opens_with_the_same_timeline(qapp, tmp_path, layout, values):
    source = MainWindow()
    try:
        _draw_timeline(source)
        root = _export(source, tmp_path, layout, values)
    finally:
        source.close()
        source.deleteLater()

    target = MainWindow()
    try:
        assert target.importer.cut_folder(root, confirm_replace=False)
        frames = target.canvas.frames
        assert frames[0].layers[0].name == "A"
        assert [_shown(target, frame) for frame in range(4)] == [1, 1, None, 2]
        for frame, number in ((0, 1), (3, 2)):
            image = frames[frame].layers[0].image
            assert QColor(image.pixel(5, 5)) == COLORS[number]
        assert target.current_project_path is None
    finally:
        target.close()
        target.deleteLater()
        qapp.processEvents()


def _write_png(path, color, size=(16, 16)):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(size[0], size[1], QImage.Format.Format_ARGB32)
    image.fill(color)
    assert image.save(str(path), "PNG")


def test_scan_reads_other_tools_naming_and_skips_underscore_folders(tmp_path):
    _write_png(tmp_path / "A" / "0001.png", QColor("red"))
    _write_png(tmp_path / "b" / "cut3_b_2.png", QColor("blue"))
    _write_png(tmp_path / "C_0005.png", QColor("green"))
    _write_png(tmp_path / "_pool" / "X0001.png", QColor("black"))
    (tmp_path / "_ts").mkdir()
    (tmp_path / "_ts" / "c003.tdts").write_text("", encoding="utf-8")

    contents = cf.scan_cut_folder(tmp_path)
    assert [path.name for path in contents.timesheets] == ["c003.tdts"]
    assert sorted(contents.column("a")) == [1]
    assert sorted(contents.column("B")) == [2]
    assert sorted(contents.column("C")) == [5]
    assert contents.column("X") == {}


def test_unused_cels_and_missing_cels_are_reported(window, tmp_path):
    _write_png(tmp_path / "A" / "A0001.png", QColor("red"))
    _write_png(tmp_path / "B" / "B0001.png", QColor("blue"))
    # A の 2 は画像がない。B はタイムシートにない。
    sheet = timesheet_file.xdts_text(
        "c001", ["A"], [["1", timesheet_file.HYPHEN, "2"]], 3
    )
    (tmp_path / "c001.xdts").write_text(sheet, encoding="utf-8")

    document, notes = window.importer.read_cut_folder(tmp_path)
    assert [layer.name for layer in document.layers] == ["A", "B"]
    assert any("A2" in note for note in notes)

    assert window.importer.cut_folder(tmp_path, confirm_replace=False)
    frames = window.canvas.frames
    assert frames[0].layers[0].sequence_number == 1
    assert not frames[2].layers[0].has_content
    # タイムシートにない B のセルは連番保管セルになる。
    assert (1, 1) in window.canvas._sequence_archive


def test_folder_without_timesheet_is_rejected(window, tmp_path, monkeypatch):
    _write_png(tmp_path / "A" / "A0001.png", QColor("red"))
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **_k: errors.append(args))
    before = window.canvas.frames
    assert not window.importer.cut_folder(tmp_path, confirm_replace=False)
    assert errors
    assert window.canvas.frames is before
