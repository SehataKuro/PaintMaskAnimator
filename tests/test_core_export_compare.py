"""Compare pma-core's export/import (pma_core::export・import, pma-io) with the Python app.

同じ文書を Python 版（Qt・Pillow）と Rust 版で書き出し・読み込みして突き合わせる。
タイムシート（XDTS・TDTS）・メモ・TGA はバイト単位で一致する。PNG は Qt（libpng）と
Rust（png crate）で圧縮が違うのでバイト列は一致しないが、読み戻した画素は一致する。
"""
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import pma_native as native  # noqa: E402
from image_edit_helpers import edit_qimage  # noqa: E402
from paintmaskanimator import config, cut_folder as cf, imaging  # noqa: E402
from paintmaskanimator import project_io  # noqa: E402
from paintmaskanimator.document_live import _live_of  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.main_window_import import ImportController  # noqa: E402
from paintmaskanimator.memo_window import MEMO_FILE_NAME  # noqa: E402
from paintmaskanimator.optional_deps import PILImage  # noqa: E402
from paintmaskanimator.pma_image import PmaImage  # noqa: E402
from paintmaskanimator.pma_image_io import thumbnail_png  # noqa: E402


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
def window(qapp, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: None)
    window = MainWindow()
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()


def _core(window):
    document = window.canvas._document
    # 素のグラフで編集したあとは Rust の状態へ戻してから読む。
    assert document._promote()
    return _live_of(document)["state"].core


def _pixels(image):
    """QImage をストレート RGBA のバイト列にする。"""
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = rgba.width(), rgba.height()
    rows = np.frombuffer(imaging.qimage_buffer(rgba), dtype=np.uint8).reshape(
        (height, rgba.bytesPerLine())
    )
    return width, height, rows[:, : width * 4].tobytes()


def _premultiplied(image):
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    width, height = rgba.width(), rgba.height()
    rows = np.frombuffer(imaging.qimage_buffer(rgba), dtype=np.uint8).reshape(
        (height, rgba.bytesPerLine())
    )
    return width, height, rows[:, : width * 4].tobytes()


def _draw(window, numbers):
    """1列目に、numbers のセル番号ごとに違う色・半透明の絵を描く。"""
    canvas = window.canvas
    canvas._ensure_frame_count(len(numbers))
    colors = {}
    for index, number in enumerate(numbers):
        layer = canvas.frames[index].layers[0]
        color = colors.setdefault(number, QColor(30 * number % 256, 80, 200, 90 + 40 * number))
        with edit_qimage(layer.image) as edited:
            edited.fill(QColor(0, 0, 0, 0))
            for y in range(0, edited.height(), 7):
                for x in range(0, edited.width(), 5):
                    edited.setPixelColor(x, y, color)
        layer.has_content = True
        layer.is_blank_key = False
        layer._track.cells[number] = layer._cell
        layer.sequence_number = number
    canvas.frames[0].layers[0].name = "A"


def _tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.mark.parametrize("image_format", ["png", "tga"])
@pytest.mark.parametrize("write_timesheet", [True, False])
def test_cut_folder_export_matches(window, tmp_path, image_format, write_timesheet):
    _draw(window, [1, 2, 2, 3])
    window.project_memo = "  メモ\n二行目  "
    cels, images, skipped = window.export.cut_folder_cels()
    rust_cels, rust_skipped = native.export_cut_folder_cels(_core(window))
    assert [(c.layer_index, c.cell, c.number) for c in cels] == [c[:3] for c in rust_cels]
    assert skipped == rust_skipped

    layout = cf.pma_standard_layout()
    layout.image_format = image_format
    layout.extra_folders = [(cf.text("BG"),)]
    values = {"title": "PMA", "scene": "", "episode": "1", "cut": "12"}
    layout_json = layout.to_json()
    if not write_timesheet:
        # Issue #115：タイムシートを書かない構成（Rust のみ。Python 版は常に書く）。
        layout_json["write_timesheet"] = False
    plan = cf.plan_export(layout, values, cels)
    rust_plan = native.export_plan_cut_folder(json.dumps(layout_json), values, rust_cels)
    assert rust_plan["folder_name"] == plan.folder_name
    assert rust_plan["files"] == [
        (path, cel.layer_index, cel.cell, cel.number) for path, cel in plan.files
    ]
    assert rust_plan["problems"] == []

    python_root = tmp_path / "python"
    rust_root = tmp_path / "rust"
    python_root.mkdir()
    rust_root.mkdir()
    assert window.export.write_cut_folder(plan, python_root, images, image_format)
    written = native.export_write_cut_folder(
        _core(window), rust_root, json.dumps(layout_json), values, window.project_memo,
    )
    assert Path(written) == rust_root / plan.folder_name

    python_folder = python_root / plan.folder_name
    rust_folder = rust_root / plan.folder_name
    if write_timesheet:
        assert _tree(python_folder) == _tree(rust_folder)
    else:
        sheet = python_folder / plan.timesheet_path
        expected = [
            p for p in _tree(python_folder)
            if p != str(Path(plan.timesheet_path)) and not p.endswith(MEMO_FILE_NAME)
        ] + [MEMO_FILE_NAME]
        assert _tree(rust_folder) == sorted(expected)
        assert sheet.is_file()
        return
    for relative in _tree(python_folder):
        python_file = python_folder / relative
        rust_file = rust_folder / relative
        if python_file.is_dir():
            continue
        if python_file.suffix == ".png":
            assert _pixels(QImage(str(rust_file))) == _pixels(QImage(str(python_file))), relative
        else:
            assert rust_file.read_bytes() == python_file.read_bytes(), relative


@pytest.mark.parametrize("fmt", ["xdts", "tdts"])
def test_sheet_text_matches(window, fmt):
    _draw(window, [1, 1, 2, 3, 3, 2])
    header = {"cut": "12", "episode": "1", "scene": "3"}
    expected = window.export.sheet_text(fmt, "PMA_01_c012", **header)
    actual = native.export_sheet_text(
        _core(window), fmt, "PMA_01_c012", cut="12", episode="1", scene="3",
    )
    assert actual == expected
    parsed = ImportController._parse_xdts_timesheet(expected)
    assert native.import_parse_timesheet(expected) == parsed


def test_project_thumbnail_matches(window):
    _draw(window, [1, 2])
    frame = 1
    expected = project_io._thumbnail_png(window.crop_image(frame))
    actual = native.export_thumbnail_png(_core(window), frame)
    assert expected is not None
    assert _pixels(QImage.fromData(actual)) == _pixels(QImage.fromData(expected))


@pytest.mark.parametrize("size", [(1280, 720), (700, 1300), (513, 2), (600, 600)])
def test_thumbnail_scale_matches_qt(qapp, size):
    width, height = size
    rng = np.random.default_rng(width * 7 + height)
    rgba = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
    rgba[..., 3] = np.where(rng.random((height, width)) < 0.3, 0, rgba[..., 3])
    image = QImage(rgba.tobytes(), width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
    premul = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    encoded = thumbnail_png(premul, 512)
    assert encoded is not None
    expected = QImage.fromData(encoded)
    _, _, source = _premultiplied(premul)
    w, h, scaled = native.export_thumbnail_scale(width, height, source, 512)
    assert (w, h) == (expected.width(), expected.height())
    assert scaled == _premultiplied(expected)[2]


def test_tga_encoding_matches_pillow(window, tmp_path):
    assert PILImage is not None
    rng = np.random.default_rng(3)
    rgba = rng.integers(0, 4, (9, 300, 4), dtype=np.uint8) * 60
    image = QImage(rgba.tobytes(), 300, 9, 1200, QImage.Format.Format_RGBA8888).copy()
    path = tmp_path / "frame.tga"
    assert window.save_tga_image(image, path)
    assert native.export_encode_tga(300, 9, rgba.tobytes()) == path.read_bytes()


def test_guess_cut_number_matches():
    for name in ["c012", "PMA_01_c012", "s1c3_v2", "カット１２", "no digits", "", "cut-007b"]:
        assert native.export_guess_cut_number(name) == cf.guess_cut_number(name)


def _write_image(path, rgba, fmt):
    height, width = rgba.shape[:2]
    image = QImage(rgba.tobytes(), width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
    if fmt == "tga":
        PILImage.fromarray(rgba, "RGBA").save(str(path), format="TGA", compression="tga_rle")
    else:
        assert image.save(str(path), fmt.upper())


@pytest.mark.parametrize("fmt", [
    "png",
    "tga",
    # zune-jpeg と Qt（libjpeg-turbo）は色差の補間が違い、画素が最大 ±11 ずれる。
    pytest.param("jpg", marks=pytest.mark.xfail(strict=True, reason="JPEG の展開が libjpeg と違う")),
])
def test_image_import_matches(window, tmp_path, fmt):
    canvas = window.canvas
    rng = np.random.default_rng(11)
    rgba = rng.integers(0, 256, (31, 47, 4), dtype=np.uint8)
    rgba[:4] = 255
    rgba[5:9, ..., 3] = np.where(rng.random((4, 47)) < 0.5, 0, rgba[5:9, ..., 3])
    path = tmp_path / f"image.{fmt}"
    _write_image(path, rgba, fmt)

    image, error = canvas._read_image_file(path)
    assert image is not None, error
    width, height, pixels = native.import_read_image(path.read_bytes())
    assert (width, height, pixels) == _premultiplied(image)

    for draft in (False, True):
        converted = canvas.convert_import_image(str(path), image, draft=draft)
        expected = PmaImage.from_qimage(canvas._placed_imported_image(converted))
        actual = native.import_image_file(
            path, expected.width, expected.height, draft=draft,
        )
        assert actual == expected.rgba().tobytes(), draft


def test_scan_cut_folder_matches(window, tmp_path):
    _draw(window, [1, 2, 3])
    layout = cf.pma_standard_layout()
    values = {"title": "PMA", "scene": "", "episode": "1", "cut": "12"}
    cels, _ = native.export_cut_folder_cels(_core(window))
    written = Path(native.export_write_cut_folder(
        _core(window), tmp_path, json.dumps(layout.to_json()), values,
    ))
    expected = cf.scan_cut_folder(written)
    actual = native.import_scan_cut_folder(written)
    assert actual["timesheets"] == [str(p.relative_to(written)) for p in expected.timesheets]
    assert actual["names"] == dict(expected.names)
    assert actual["cels"] == {
        key: {n: str(p.relative_to(written)) for n, p in drawings.items()}
        for key, drawings in expected.cels.items()
    }
    assert cels
