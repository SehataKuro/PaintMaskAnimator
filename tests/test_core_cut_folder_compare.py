"""Compare pma-core's cut-folder import with Qt's ``ImportController.cut_folder``.

Tauri の「カットフォルダーを開く」は pma-core（``import::cut_folder::read_cut_folder`` と
``Document::from_animation``）で読む。乱数で作ったカットフォルダー（列の順・抜けた
画像・タイムシートにない列・大きさの違うセル・空セル・複数のタイムシート）を、
Qt の読み込みが作る文書と文書グラフで比べ、知らせの文も比べる。
"""

import os
import random

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from paintmaskanimator import config, constants, timesheet_file
from paintmaskanimator.main_window import MainWindow
from pma_native import pma_native as core

from test_core_clip_compare import _graph

pytestmark = pytest.mark.skipif(
    not hasattr(core, "cut_folder_document"), reason="pma_native has no pma-core cut folder import"
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    saved = (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)
    store = {}
    monkeypatch.setattr(config, "get_value", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(config, "set_value", lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: None)
    # 読み込みのあとの「初期状態として保存しますか？」は「保存しない」。
    monkeypatch.setattr(QMessageBox, "exec", lambda _self: 0)
    try:
        yield
    finally:
        constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = saved


@pytest.fixture
def window(qapp):
    result = MainWindow()
    yield result
    result.close()
    result.deleteLater()
    qapp.processEvents()


def _write_png(path, rng, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(size[0], size[1], QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    for _ in range(rng.randint(1, size[0] * size[1])):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        image.setPixelColor(x, y, QColor(rng.randrange(256), rng.randrange(256),
                                         rng.randrange(256), rng.choice((0, 128, 255, 255))))
    assert image.save(str(path))


def _random_folder(root, rng):
    names = rng.sample(["A", "B", "C", "D"], rng.randint(1, 3))
    duration = rng.randint(1, 6)
    size = (rng.randint(2, 6), rng.randint(2, 5))
    tracks = []
    for name in names:
        numbers = sorted(rng.sample(range(1, 6), rng.randint(1, 3)))
        for number in numbers:
            # ときどき画像を抜く（空セルにして知らせる）・大きさを変える（中央に置く）。
            if rng.random() < 0.15:
                continue
            cel_size = size if rng.random() < 0.8 else (rng.randint(1, 7), rng.randint(1, 6))
            _write_png(root / name / f"{name}{number:04d}.png", rng, cel_size)
        track = []
        for _ in range(duration):
            roll = rng.random()
            if roll < 0.2:
                track.append(timesheet_file.NULL_CELL)
            elif roll < 0.45 and track:
                track.append(timesheet_file.HYPHEN)
            else:
                track.append(str(rng.choice(numbers)))
        tracks.append(track)
    # タイムシートにない列（連番に残る）。
    if rng.random() < 0.5:
        _write_png(root / "Z" / "Z0001.png", rng, size)
    sheet = timesheet_file.xdts_text("c001", names, tracks, duration)
    (root / "c001.xdts").write_text(sheet, encoding="utf-8")
    if rng.random() < 0.2:
        (root / "c002.xdts").write_text(sheet, encoding="utf-8")


@pytest.mark.parametrize("seed", range(16))
def test_cut_folder_document_matches_qt(window, tmp_path, seed):
    rng = random.Random(3100 + seed)
    root = tmp_path / f"cut{seed}"
    root.mkdir()
    _random_folder(root, rng)
    replaced = (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)
    _, qt_notes = window.importer.read_cut_folder(root)
    assert window.importer.cut_folder(root, confirm_replace=False)
    archived_qt = sum(len(t.legacy_archive_hints) for t in window.canvas._document.tracks)
    fps = float(window.timeline.fps.value())
    document, archived, notes = core.cut_folder_document(str(root), fps, replaced)
    assert _graph(document) == _graph(window.canvas._document._live()["core"])
    assert archived == archived_qt
    assert notes == list(qt_notes)


def test_cut_folder_errors_match_qt(window, tmp_path):
    rng = random.Random(9)
    root = tmp_path / "nosheet"
    _write_png(root / "A" / "A0001.png", rng, (3, 3))
    with pytest.raises(Exception, match="タイムシート（.xdts／.tdts）が見つかりません。"):
        window.importer.read_cut_folder(root)
    with pytest.raises(ValueError, match="タイムシート（.xdts／.tdts）が見つかりません。"):
        core.cut_folder_document(str(root), 24.0, (8, 8))
