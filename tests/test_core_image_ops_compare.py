"""Rust の線・色の操作（pma_core::image_ops）を、Python の画面の経路と画素で突き合わせる。

ゴミ取り・線整え・色置換（置換・削除・統合）・色相彩度明度・色の選択範囲・
MainLineRepaint・同一画像の置換色を、同じ入力で Python（numpy・Qt）と Rust に通し、
層に書き戻される画素（PmaImage の正規化を通した値）と数が一致することを確かめる。
"""

import os
import random
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import pma_native  # noqa: E402
from paintmaskanimator import hsv_adjust_dialog, imaging  # noqa: E402
from paintmaskanimator import main_window_line_ops as line_ops_module  # noqa: E402
from paintmaskanimator.main_window_line_ops import LineOpsController  # noqa: E402
from paintmaskanimator.main_window_used_color import UsedColorController  # noqa: E402
from paintmaskanimator.pma_image import PmaImage, as_qimage  # noqa: E402

WHITE = (255, 255, 255)
COLOURS = [(0, 0, 0), (255, 0, 0), (0, 0, 255), (20, 200, 60), (128, 128, 128), (250, 240, 10)]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# --- 入力 ---------------------------------------------------------------------


def _normalized(rgba: np.ndarray) -> np.ndarray:
    """層に置いたときの画素（Qt の乗算済み形式で往復した値）。"""
    return PmaImage.from_qimage(imaging.rgba_array_to_qimage(rgba)).rgba().copy()


def _random_image(rng: random.Random, width: int, height: int, *, semi=False) -> np.ndarray:
    """少ない色の塊・線・点が混ざった画像。"""
    palette = rng.sample(COLOURS, rng.randint(1, len(COLOURS)))
    background = rng.choice([WHITE, None])
    rgba = np.zeros((height, width, 4), np.uint8)
    if background is not None:
        rgba[:, :] = (*background, 255)
    for _ in range(rng.randint(0, 6)):
        colour = rng.choice(palette)
        x0, y0 = rng.randrange(width), rng.randrange(height)
        x1, y1 = min(width, x0 + rng.randint(1, 8)), min(height, y0 + rng.randint(1, 8))
        rgba[y0:y1, x0:x1] = (*colour, 255)
    for _ in range(rng.randint(0, 4)):
        colour = rng.choice(palette)
        x, y = rng.randrange(width), rng.randrange(height)
        thick = rng.choice([1, 1, 2])
        for _step in range(rng.randint(3, 30)):
            rgba[y : y + thick, x : x + thick] = (*colour, 255)
            x = min(width - 1, max(0, x + rng.choice([-1, 0, 1, 1])))
            y = min(height - 1, max(0, y + rng.choice([-1, 0, 1])))
    for _ in range(rng.randint(0, 12)):
        rgba[rng.randrange(height), rng.randrange(width)] = (*rng.choice(palette + [WHITE]), 255)
    if rng.random() < 0.3:
        rgba[rng.randrange(height), rng.randrange(width)] = (0, 0, 0, 0)
    if semi:
        for _ in range(rng.randint(0, 6)):
            rgba[rng.randrange(height), rng.randrange(width)] = (
                rng.randrange(256),
                rng.randrange(256),
                rng.randrange(256),
                rng.randrange(1, 255),
            )
    return _normalized(rgba)


def _core(rgba: np.ndarray) -> "pma_native.RgbaImage":
    height, width = rgba.shape[:2]
    return pma_native.RgbaImage(width, height, np.ascontiguousarray(rgba).tobytes())


def _core_rgba(image) -> np.ndarray:
    return np.frombuffer(image.rgba_bytes(), np.uint8).reshape(image.height, image.width, 4)


def _layer(rgba: np.ndarray) -> PmaImage:
    return PmaImage.from_qimage(imaging.rgba_array_to_qimage(rgba))


def _changed_rect(before: np.ndarray, after: np.ndarray):
    ys, xs = np.nonzero((before != after).any(axis=2))
    if not len(ys):
        return None
    return (
        int(xs.min()),
        int(ys.min()),
        int(xs.max() - xs.min() + 1),
        int(ys.max() - ys.min() + 1),
    )


class _Runner:
    """``window.scope.run_over`` の代わり：1 セルに op をかけ、書き戻す画素を覚える。"""

    def __init__(self, rgba):
        self.rgba = rgba
        self.stored = None

    def current_frame(self, *_args, **_kwargs):
        return "scope"

    def run_over(self, _scope, op, label=None, count_pixels=None, **_kwargs):
        context = SimpleNamespace(
            layer=SimpleNamespace(image=_layer(self.rgba)), frame=0, layer_index=0
        )
        produced = op(context)
        pixels = 0
        if produced is not None:
            self.stored = (
                PmaImage.from_qimage(as_qimage(produced)).rgba().copy()  # pyright: ignore[reportArgumentType]
                if not isinstance(produced, PmaImage)
                else produced.rgba().copy()
            )
            pixels = int(count_pixels(context, produced)) if count_pixels is not None else 0
        return SimpleNamespace(
            changed_cells=int(produced is not None),
            changed_pixels=pixels,
            cancelled=False,
        )


def _window(rgba, **tools):
    window = MagicMock()
    runner = _Runner(rgba)
    window.scope = runner
    window._used_color_cache = {}
    for name, value in tools.items():
        setattr(window, name, value)
    return window, runner


def _assert_edit(result, before, expected, expected_pixels):
    """Rust の結果 ``(画像, 矩形, 画素数)`` と Python の書き戻し画素を比べる。"""
    if expected is None:
        assert result is None
        return
    assert result is not None
    image, rect, pixels = result
    got = _core_rgba(image)
    np.testing.assert_array_equal(got, expected)
    assert rect == _changed_rect(before, expected)
    assert pixels == expected_pixels


# --- ゴミ取り -----------------------------------------------------------------


def _python_dust(rgba, mode, size, selected, monkeypatch):
    tools = MagicMock()
    tools.dust_mode.currentData.return_value = mode
    tools.dust_mode.currentText.return_value = mode
    tools.dust_size.value.return_value = size
    tools.dust_selected_only.isChecked.return_value = selected is not None
    tools.dust_all_frames.isChecked.return_value = False
    window, runner = _window(rgba, tools=tools)
    window.canvas.selected_used_colors.return_value = set(selected or ())
    window.canvas._scanline_connected_region = imaging.scanline_connected_region
    counted = {}
    original = runner.run_over

    def run_over(scope, op, **kwargs):
        result = original(scope, op, **kwargs)
        counted["pixels"] = result.changed_pixels
        return result

    runner.run_over = run_over  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setattr(line_ops_module, "resolve_cells", lambda _canvas, _scope: [object()])
    LineOpsController(window).remove_dust_fill_surrounding()
    return runner.stored, counted.get("pixels", 0)


@pytest.mark.parametrize("mode", ["despeckle", "fill_holes", "line_noise"])
def test_dust_matches_python(app, monkeypatch, mode):
    rng = random.Random(hash(mode) & 0xFFFF)
    for case in range(220):
        width, height = rng.randint(1, 40), rng.randint(1, 40)
        rgba = _random_image(rng, width, height, semi=case % 5 == 0)
        size = rng.choice([1, 2, 3, 5, 9, 16, 50, 100])
        selected = None
        if rng.random() < 0.4:
            selected = sorted({rng.choice(COLOURS) for _ in range(rng.randint(1, 3))})
        expected, pixels = _python_dust(rgba, mode, size, selected, monkeypatch)
        result = pma_native.image_ops_remove_dust(_core(rgba), mode, size, selected)
        _assert_edit(result, rgba, expected, pixels)


# --- 線整え -------------------------------------------------------------------


def _line_image(rng, width, height):
    """2 値の線画（黒と色トレス線）。階段・45°・2×2 の塊・ヒゲを含む。"""
    rgba = np.zeros((height, width, 4), np.uint8)
    rgba[:, :] = (255, 255, 255, 255) if rng.random() < 0.7 else (0, 0, 0, 0)
    for _ in range(rng.randint(1, 5)):
        colour = rng.choice([(0, 0, 0), (0, 0, 0), (255, 0, 0), (0, 0, 255)])
        x, y = rng.randrange(width), rng.randrange(height)
        dx, dy = rng.choice([-1, 1]), rng.choice([-1, 1])
        run = rng.randint(1, 4)
        for step in range(rng.randint(4, 40)):
            rgba[y, x] = (*colour, 255)
            if rng.random() < 0.15:
                rgba[min(height - 1, y + 1), min(width - 1, x + 1)] = (*colour, 255)
            if step % run == run - 1:
                y += dy
                if rng.random() < 0.5:
                    x += dx
            else:
                x += dx
            x, y = min(width - 1, max(0, x)), min(height - 1, max(0, y))
    for _ in range(rng.randint(0, 3)):
        x, y = rng.randrange(width - 1), rng.randrange(height - 1)
        rgba[y : y + 2, x : x + 2] = (0, 0, 0, 255)
    if rng.random() < 0.2:
        rgba[rng.randrange(height), rng.randrange(width)] = (90, 90, 90, 128)
    return _normalized(rgba)


def _python_tidy(rgba, colours, diagonal, region):
    window, runner = _window(rgba)
    controller = LineOpsController(window)
    changed = controller.tidy_lines(colours, all_frames=False, diagonal=diagonal, region=region)
    return runner.stored if changed else None


def test_tidy_lines_matches_python(app):
    rng = random.Random(64)
    for case in range(400):
        width, height = rng.randint(2, 36), rng.randint(2, 36)
        rgba = _line_image(rng, width, height)
        colours = rng.choice([[(0, 0, 0)], [(0, 0, 0), (255, 0, 0)], [(0, 0, 255), (0, 0, 0)]])
        diagonal = rng.choice(["add", "remove", None])
        region = None
        region_args = (None, None)
        if case % 3 == 0:
            rh, rw = rng.randint(1, height + 3), rng.randint(1, width + 3)
            region = np.array([[rng.random() < 0.7 for _ in range(rw)] for _ in range(rh)])
            region_args = (region.astype(np.uint8).tobytes(), (rw, rh))
        expected = _python_tidy(rgba, colours, diagonal, region)
        from paintmaskanimator import line_tidy

        _out, added, removed = line_tidy.tidy_lines(
            rgba,
            colours,
            diagonal,
            line_ops_module._fit_region(region, rgba.shape[:2]),
        )
        result, got_added, got_removed = pma_native.image_ops_tidy_lines(
            _core(rgba),
            colours,
            diagonal,
            *region_args,
        )
        assert (got_added, got_removed) == (int(added.sum()), int(removed.sum()))
        _assert_edit(result, rgba, expected, int(added.sum()) + int(removed.sum()))


def test_tidy_preview_and_colours_match_python(app):
    rng = random.Random(65)
    for _case in range(150):
        width, height = rng.randint(2, 70), rng.randint(2, 70)
        rgba = _line_image(rng, width, height)
        diagonal = rng.choice(["add", "remove", None])
        window, _runner = _window(rgba)
        controller = LineOpsController(window)
        selected = set() if rng.random() < 0.6 else {rng.choice(COLOURS + [WHITE])}
        window.canvas.selected_used_colors.return_value = selected
        window.canvas.active_layer.image = _layer(rgba)
        colours = controller.tidy_line_colours()
        assert pma_native.image_ops_tidy_line_colours(_core(rgba), sorted(selected)) == colours
        if not colours:
            continue
        scale, limit = rng.choice([(8, 480), (3, 40), (20, 64)])
        image, added, removed = controller.line_tidy_preview(
            rgba,
            colours,
            scale=scale,
            limit=limit,
            diagonal=diagonal,  # pyright: ignore[reportArgumentType]
        )
        got = pma_native.image_ops_tidy_preview(
            _core(rgba),
            colours,
            diagonal,
            scale=scale,
            limit=limit,
        )
        if image is None:
            assert got is None
            continue
        assert got is not None
        preview, _crop, _scale, got_added, got_removed = got
        assert (got_added, got_removed) == (added, removed)
        shown = imaging.qimage_rgba_array(imaging.rgba_array_to_qimage(_core_rgba(preview)))
        np.testing.assert_array_equal(shown, imaging.qimage_rgba_array(image))


# --- 色置換・削除・統合 ---------------------------------------------------------


def _used_color(rgba, selection):
    window, runner = _window(rgba)
    window.canvas.selection_mask_bool.return_value = selection
    window.canvas.undo_stack = []
    controller = UsedColorController(window)
    controller.schedule_refresh = lambda: None
    counted = {}
    original = runner.run_over

    def run_over(scope, op, **kwargs):
        result = original(scope, op, **kwargs)
        counted["pixels"] = result.changed_pixels
        return result

    runner.run_over = run_over  # pyright: ignore[reportAttributeAccessIssue]
    return controller, runner, counted


def _selection(rng, rgba):
    if rng.random() < 0.6:
        return None, (None, None)
    height, width = rgba.shape[:2]
    if rng.random() < 0.15:
        height += 1  # 大きさが違う選択範囲は使わない。
    selection = np.array([[rng.random() < 0.5 for _ in range(width)] for _ in range(height)])
    return selection, (selection.astype(np.uint8).tobytes(), (width, height))


def test_palette_replacements_match_python(app):
    rng = random.Random(22)
    for case in range(300):
        width, height = rng.randint(1, 30), rng.randint(1, 30)
        rgba = _random_image(rng, width, height, semi=case % 4 == 0)
        selection, selection_args = _selection(rng, rgba)
        operation = rng.choice(["replace", "delete", "merge"])
        controller, runner, counted = _used_color(rgba, selection)
        picked = sorted({rng.choice(COLOURS + [WHITE]) for _ in range(rng.randint(1, 4))})
        if operation == "replace":
            mapping = {source: rng.choice(COLOURS + [WHITE]) for source in picked}
            changed = controller.apply_palette_replacements(mapping)
            pairs = list(mapping.items())
        elif operation == "delete":
            controller.apply_palette_delete(picked)
            changed = runner.stored is not None
            pairs = pma_native.image_ops_delete_mapping(picked)
        else:
            parent = rng.choice(COLOURS + [WHITE])
            controller.apply_palette_merge(parent, picked)
            changed = runner.stored is not None
            pairs = pma_native.image_ops_merge_mapping(parent, picked)
            if pairs is None:
                assert runner.stored is None
                continue
        result = pma_native.image_ops_replace_colors(_core(rgba), pairs, *selection_args)
        _assert_edit(result, rgba, runner.stored if changed else None, counted.get("pixels", 0))


def test_hsv_shift_matches_qcolor(app):
    rng = random.Random(23)
    for _ in range(40000):
        rgb = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        hue = rng.choice([0, rng.randint(-180, 180)])
        saturation = rng.choice([0, rng.randint(-100, 100)])
        value = rng.choice([0, rng.randint(-100, 100)])
        assert pma_native.hsv_shifted_color(
            rgb, hue, saturation, value
        ) == hsv_adjust_dialog.shifted_color(rgb, hue, saturation, value)
    for _ in range(200):
        rgbs = sorted(
            {(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(5)}
            | {WHITE}
        )
        args = (rng.randint(-180, 180), rng.randint(-100, 100), rng.randint(-100, 100))
        expected = hsv_adjust_dialog.shifted_mapping(rgbs, *args)
        assert pma_native.hsv_shifted_mapping(rgbs, *args) == list(expected.items())


# --- 色の選択範囲・MainLineRepaint・同一画像の置換色 -----------------------------


def test_color_selection_matches_python(app):
    rng = random.Random(127)
    for _ in range(250):
        width, height = rng.randint(1, 30), rng.randint(1, 30)
        rgba = _random_image(rng, width, height)
        rgb = rng.choice(COLOURS)
        matching = (rgba[:, :, 3] > 0) & np.all(rgba[:, :, :3] == np.array(rgb, np.uint8), axis=2)
        assert pma_native.image_ops_contains_rgb(
            _core(rgba), rgb
        ) == LineOpsController._image_contains_rgb(as_qimage(_layer(rgba)), rgb)
        contours = LineOpsController._mask_contours(matching)
        expected = [[(int(p.x()), int(p.y())) for p in contour] for contour in contours]
        assert (
            pma_native.image_ops_mask_contours(matching.astype(np.uint8).tobytes(), width, height)
            == expected
        )
        got = pma_native.image_ops_color_selection(_core(rgba), rgb)
        largest = LineOpsController._largest_contour(contours)
        if not largest:
            assert got is None
            continue
        assert got is not None
        mask, got_contours, index, bounds = got
        assert (
            np.frombuffer(mask, np.uint8).reshape(height, width).astype(bool).tolist()
            == matching.tolist()
        )
        assert got_contours == expected
        assert got_contours[index] == [(int(p.x()), int(p.y())) for p in largest]
        ys, xs = np.nonzero(matching)
        assert bounds == (
            int(xs.min()),
            int(ys.min()),
            int(xs.max() - xs.min() + 1),
            int(ys.max() - ys.min() + 1),
        )


def test_aligned_replacement_mapping_matches_python(app):
    rng = random.Random(48)
    controller = LineOpsController(MagicMock())
    for _ in range(300):
        width, height = rng.randint(1, 20), rng.randint(1, 20)
        source = _random_image(rng, width, height)
        target = source.copy()
        swap = {c: rng.choice(COLOURS + [WHITE]) for c in COLOURS + [WHITE]}
        for y in range(height):
            for x in range(width):
                if target[y, x, 3] and rng.random() < 0.97:
                    target[y, x, :3] = swap[
                        (int(target[y, x, 0]), int(target[y, x, 1]), int(target[y, x, 2]))
                    ]
        if rng.random() < 0.1:
            target[0, 0, 3] = 255 - target[0, 0, 3]
        target = _normalized(target)
        expected = controller._replacement_mapping_for_aligned_images(
            _layer(source), _layer(target)
        )
        got = pma_native.image_ops_aligned_mapping(_core(source), _core(target))
        assert got == (None if expected is None else sorted(expected.items()))


def test_main_line_repaint_matches_python(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from paintmaskanimator.main_window import MainWindow

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Ok)
    rng = random.Random(1300)
    window = MainWindow()
    try:
        canvas = window.canvas
        width, height = (
            canvas.frames[0].layers[0].image.width,
            canvas.frames[0].layers[0].image.height,
        )
        for _case in range(6):
            rgba = np.zeros((height, width, 4), np.uint8)
            small = _random_image(rng, 48, 40, semi=True)
            x0, y0 = rng.randrange(width - 48), rng.randrange(height - 40)
            rgba[y0 : y0 + 40, x0 : x0 + 48] = small
            rgba = _normalized(rgba)
            selected = sorted({rng.choice(COLOURS) for _ in range(2)})
            source_index = canvas.active_layer_index
            from paintmaskanimator.document import create_drawing

            layer = canvas.frames[canvas.current_frame].layers[source_index]
            create_drawing(layer, _layer(rgba), number=layer.sequence_number or 1)
            monkeypatch.setattr(
                window.palette, "selected_rgb_set", lambda selected=selected: set(selected)
            )
            line, paint, has_line, has_paint = pma_native.image_ops_split_line(
                _core(rgba), selected
            )
            if not has_line:
                continue  # Python 版は警告を出して何もしない。
            window.line_ops.main_line_repaint()
            frame = canvas.frames[canvas.current_frame]
            np.testing.assert_array_equal(
                frame.layers[source_index + 2].image.rgba(), _core_rgba(line)
            )
            np.testing.assert_array_equal(
                frame.layers[source_index + 1].image.rgba(), _core_rgba(paint)
            )
            assert has_paint == bool((_core_rgba(paint)[:, :, 3] > 0).any())
            canvas.active_layer_index = source_index
    finally:
        window.deleteLater()
