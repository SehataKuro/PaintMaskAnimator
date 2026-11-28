"""Compare pma-core's PSD and MP4 frames (pma_core::export::psd・movie) with the Python app.

PSD は psd-tools（1.19.0）で書いた Qt 版とバイト単位で同じになる（レイヤー名が MacRoman で
書けるとき。書けない名前では Qt 版は失敗し、Rust 版は `?` と Unicode の名前で書く）。

MP4 は符号化（ffmpeg）の前の絵の列を比べる。コマの絵は画素が同じ。頭のカット名は、Qt が
QPainter で絵に直接描くのに対し、Rust は透明な画像に描いた文字（Tauri ではブラウザが描く）を
重ねるので、文字の縁の画素だけ丸めの違い（±2）を許す。文字のない画素は同じ。
"""
import io
import os
import shutil
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

import pma_native as native  # noqa: E402
from image_edit_helpers import edit_qimage  # noqa: E402
from paintmaskanimator import config, imaging, movie_slate  # noqa: E402
from paintmaskanimator.document_live import _live_of  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402

psd_tools = pytest.importorskip("psd_tools")
# PSD のバイト列の基準は配布した 0.8.0 の psd-tools（pyproject の dev で固定）。Python 3.10 には入らない。
same_psd_tools = pytest.mark.skipif(
    psd_tools.__version__ != "1.26.1",
    reason=f"psd-tools {psd_tools.__version__} は 0.8.0 の 1.26.1 と書き出すバイト列が違う",
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    store = {}
    monkeypatch.setattr(config, "get_value", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(config, "set_value", lambda key, value: store.__setitem__(key, value))
    return store


@pytest.fixture
def messages(monkeypatch):
    shown = []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(
            QMessageBox, kind,
            lambda *a, _kind=kind, **_k: shown.append((_kind, a[2] if len(a) > 2 else "")),
        )
    return shown


@pytest.fixture
def window(qapp, messages):
    window = MainWindow()
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()


def _core(window):
    document = window.canvas._document
    assert document._promote()
    return _live_of(document)["state"].core


def _paint(layer, number, seed):
    """番号ごとに違う、半透明を含む絵。"""
    rng = np.random.default_rng(seed)
    with edit_qimage(layer.image) as edited:
        edited.fill(QColor(0, 0, 0, 0))
        width, height = edited.width(), edited.height()
        for _ in range(60):
            x, y = int(rng.integers(0, width - 40)), int(rng.integers(0, height - 30))
            color = QColor(*(int(v) for v in rng.integers(0, 256, 3)), int(rng.integers(1, 256)))
            for dy in range(0, 30, 3):
                for dx in range(0, 40, 2):
                    edited.setPixelColor(x + dx, y + dy, color)
    layer.has_content = True
    layer.is_blank_key = False
    layer._track.cells[number] = layer._cell
    layer.sequence_number = number


def _build(window, names=("A", "B", "C")):
    """3列・4コマ。B は半透明、C は非表示で絵なし。"""
    canvas = window.canvas
    while len(canvas.frames[0].layers) < len(names):
        canvas.add_layer()
    canvas._ensure_frame_count(4)
    for layer_index, numbers in enumerate([(1, 2, 2, 3), (1, None, 2, None)]):
        for frame_index, number in enumerate(numbers):
            if number is not None:
                _paint(canvas.frames[frame_index].layers[layer_index], number, 10 * layer_index + number)
    for index, name in enumerate(names):
        canvas.frames[0].layers[index].name = name
    canvas.frames[0].layers[1].opacity = 0.55
    canvas.frames[0].layers[2].visible = False


def _qt_psd(window, monkeypatch, tmp_path):
    path = tmp_path / "qt.psd"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(path), ""))
    window.export.psd_dialog()
    return path


@same_psd_tools
def test_psd_matches_psd_tools(window, monkeypatch, tmp_path, messages):
    _build(window)
    path = _qt_psd(window, monkeypatch, tmp_path)
    assert messages and messages[-1][0] == "information", messages
    result = native.export_psd_bytes(_core(window))
    assert result is not None
    data, keys = result
    assert keys == 6
    expected = path.read_bytes()
    first = next((i for i, (a, b) in enumerate(zip(data, expected)) if a != b), None)
    assert first is None and len(data) == len(expected), (first, len(data), len(expected))


@same_psd_tools
def test_psd_hidden_layer_and_opacity_variants(window, monkeypatch, tmp_path):
    _build(window)
    window.canvas.frames[0].layers[0].visible = False
    window.canvas.frames[0].layers[1].opacity = 0.3
    path = _qt_psd(window, monkeypatch, tmp_path)
    data, _keys = native.export_psd_bytes(_core(window))
    assert data == path.read_bytes()


def test_psd_without_keys(window, monkeypatch, tmp_path, messages):
    path = _qt_psd(window, monkeypatch, tmp_path)
    assert not path.exists()
    assert messages[-1][0] == "critical"
    assert native.export_psd_bytes(_core(window)) is None


def test_psd_names_qt_cannot_encode(window, monkeypatch, tmp_path, messages):
    """日本語の名前：Qt 版（psd-tools）は書けない。Rust 版は Unicode の名前で書く。"""
    _build(window, names=("線画", "B", "C"))
    _qt_psd(window, monkeypatch, tmp_path)
    assert messages[-1][0] == "critical"
    data, _keys = native.export_psd_bytes(_core(window))
    psd = psd_tools.PSDImage.open(io.BytesIO(data))
    assert [layer.name for layer in psd] == ["線画", "B", "C"]
    assert [layer.name for layer in psd[0]] == ["線画0001", "線画0002", "線画0003", "線画0004"]


def _qt_movie_frames(window, monkeypatch, tmp_path):
    """Qt 版の mp4() が ffmpeg に渡す PNG の列を横取りする。"""
    captured = tmp_path / "frames"
    monkeypatch.setattr(shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(tmp_path / "out.mp4"), "")
    )

    def fake_run(args, **_kwargs):
        source = Path(args[args.index("-i") + 1]).parent
        shutil.copytree(source, captured)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    window.export.mp4()
    return sorted(captured.glob("f_*.png"))


def _rgba(image):
    width, height = image.width(), image.height()
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    rows = np.frombuffer(imaging.qimage_buffer(rgba), np.uint8).reshape(height, rgba.bytesPerLine())
    data = rows[:, : width * 4].reshape(height, width, 4)
    return data.copy()


def _slate_overlay(width, height, name):
    """Qt でカット名だけを透明な画像に描く（Tauri では画面の canvas が描くもの）。"""
    blank = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    blank.fill(Qt.GlobalColor.transparent)
    drawn = movie_slate.draw_cut_name(blank, name)
    drawn = drawn.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    rows = np.frombuffer(imaging.qimage_buffer(drawn), np.uint8).reshape(height, drawn.bytesPerLine())
    return rows[:, : width * 4].tobytes()


@pytest.mark.parametrize("project", [None, "c012_v2.pman"])
def test_movie_frames_match(window, monkeypatch, tmp_path, project):
    _build(window)
    window.canvas.frames[1].duration = 3
    window.canvas.frames[3].duration = 2
    window.current_project_path = str(tmp_path / project) if project else None
    paths = _qt_movie_frames(window, monkeypatch, tmp_path)
    core = _core(window)
    order = native.export_movie_frames(core)
    assert len(order) == len(paths) == 1 + 3 + 1 + 2

    first = QImage(str(paths[0]))
    width, height = first.width(), first.height()
    name = movie_slate.cut_name_for(window.current_project_path)
    overlay = _slate_overlay(width, height, name) if name else None
    for index, (frame, path) in enumerate(zip(order, paths)):
        expected = _rgba(QImage(str(path)))
        actual = np.frombuffer(
            native.export_movie_frame(core, frame, index, overlay), np.uint8
        ).reshape(height, width, 4)
        if overlay is None or index >= movie_slate.LEADING_FRAMES:
            np.testing.assert_array_equal(actual, expected, err_msg=f"frame {index}")
            continue
        text = np.frombuffer(overlay, np.uint8).reshape(height, width, 4)[..., 3] > 0
        assert text.any()
        np.testing.assert_array_equal(actual[~text], expected[~text], err_msg=f"frame {index}")
        diff = np.abs(actual.astype(int) - expected.astype(int))[text]
        assert diff.max(initial=0) <= 2, f"frame {index}: {diff.max()}"
