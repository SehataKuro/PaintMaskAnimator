"""Compare pma-core's pixel selection (selection) with the Python/Qt canvas.

同じ選択範囲を、今のキャンバス（QPainter の多角形の塗り・クリップ、numpy のマスク）と
pma-core の selection で画素にして、マスクと画素を比べる。

画素の比較は乗算済みの RGBA（Qt の ARGB32_Premultiplied と同じ値）で行う。Qt は
選択の外の半透明の画素も乗算済みとストレートの間で往復させるが、乗算済みの値は
変わらない（Rust は元のストレートの値を保つ）。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from paintmaskanimator import imaging
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.main_window_line_ops import LineOpsController
from paintmaskanimator.pma_image import image_rgba
import pma_native as native

PALETTE = [(255, 255, 255), (20, 20, 20), (230, 40, 60), (40, 200, 90), (30, 90, 220)]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def window(qapp, tmp_path_factory):
    config = tmp_path_factory.mktemp("config")
    previous = {key: os.environ.get(key) for key in ("APPDATA", "XDG_CONFIG_HOME")}
    os.environ["APPDATA"] = str(config)
    os.environ["XDG_CONFIG_HOME"] = str(config)
    window = MainWindow()
    window.canvas.ensure_editable_key()
    try:
        yield window
    finally:
        window.deleteLater()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture
def canvas(window):
    canvas = window.canvas
    canvas.clear_selection()
    yield canvas
    canvas.clear_selection()


def _size(canvas):
    image = canvas.active_layer.image
    return image.width, image.height


def _random_picture(rng, width, height, semi=0.1):
    """使用色を主にした絵。半透明の画素も少し混ぜる。"""
    choice = rng.integers(0, len(PALETTE), (height, width))
    rgb = np.asarray(PALETTE, dtype=np.uint8)[choice]
    roll = rng.random((height, width))
    alpha = np.where(roll < 0.3, 0, np.where(roll < 1.0 - semi, 255,
                                              rng.integers(1, 255, (height, width))))
    return np.ascontiguousarray(np.concatenate([rgb, alpha[..., None].astype(np.uint8)], axis=2))


def _set_layer(canvas, rgba):
    height, width = rgba.shape[:2]
    canvas._write_rgba_patch(np.ascontiguousarray(rgba), QRect(0, 0, width, height))


def _layer_rgba(canvas) -> np.ndarray:
    rgba = image_rgba(canvas.active_layer.image)
    assert rgba is not None
    return rgba


def _core_image(rgba):
    height, width = rgba.shape[:2]
    return native.RgbaImage(width, height, np.ascontiguousarray(rgba).tobytes())


def _core_rgba(image):
    return np.frombuffer(image.rgba_bytes(), dtype=np.uint8).reshape(image.height, image.width, 4)


def _premultiply(rgba):
    """Qt の qPremultiply と同じ丸めで乗算済みにする。"""
    rgba = np.asarray(rgba, dtype=np.uint32)
    alpha = rgba[..., 3:4]
    t = rgba[..., :3] * alpha
    rgb = (t + (t >> 8) + 0x80) >> 8
    return np.concatenate([rgb, alpha], axis=-1).astype(np.uint8)


def _qimage_premultiplied(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    width, height = image.width(), image.height()
    rows = np.frombuffer(imaging.qimage_buffer(image), dtype=np.uint8).reshape(
        (height, image.bytesPerLine()))
    return rows[:, :width * 4].reshape((height, width, 4)).copy()


def _polygon(points):
    selection = native.Selection.polygon(points)
    assert selection is not None
    return selection


def _mask(data, width, height):
    return np.frombuffer(data, dtype=np.uint8).reshape(height, width).astype(bool)


def _set_polygon(canvas, points):
    canvas.selection_polygon = [QPointF(x, y) for x, y in points]
    canvas.selection_mask_override = None
    canvas.selection_outline_polygons = []
    canvas.selection_mask_rect = None


def _random_polygons(rng, left, top, width, height, count=60):
    """整数・半画素・実数の頂点、軸に沿った長方形、範囲をはみ出す多角形。"""
    polygons = []
    for index in range(count):
        kind = index % 5
        n = int(rng.integers(3, 10))
        if kind == 0:
            xs = rng.integers(left - 6, left + width + 6, n).astype(float)
            ys = rng.integers(top - 6, top + height + 6, n).astype(float)
        elif kind == 1:
            xs = rng.integers((left - 6) * 2, (left + width + 6) * 2, n) / 2
            ys = rng.integers((top - 6) * 2, (top + height + 6) * 2, n) / 2
        elif kind == 2:
            xs = rng.uniform(left - 6, left + width + 6, n)
            ys = rng.uniform(top - 6, top + height + 6, n)
        elif kind == 3:
            x1, x2 = sorted(rng.uniform(left - 3, left + width + 3, 2))
            y1, y2 = sorted(rng.uniform(top - 3, top + height + 3, 2))
            polygons.append([(x1, y1), (x2, y1), (x2, y2), (x1, y2)])
            continue
        else:
            offsets = np.array([0, 1 / 128, 1 / 64, 0.5, 3 / 128])
            xs = rng.integers(left - 30, left + width + 30, n) + rng.choice(offsets, n)
            ys = rng.integers(top - 30, top + height + 30, n) + rng.choice(offsets, n)
        polygons.append([(float(x), float(y)) for x, y in zip(xs, ys)])
    return polygons


def test_polygon_mask_matches_selection_mask_bool(canvas):
    rng = np.random.default_rng(11)
    width, height, left, top = 48, 36, 30, 20
    for points in _random_polygons(rng, left, top, width, height):
        _set_polygon(canvas, points)
        expected = canvas.selection_mask_bool(width, height, left, top)
        selection = _polygon(points)
        actual = _mask(selection.mask(width, height, left, top), width, height)
        assert np.array_equal(actual, expected), points
        direct = native.selection_polygon_mask(points, width, height, left, top)
        assert np.array_equal(_mask(direct, width, height), expected), points


def test_rect_select_matches_the_release_handler(canvas):
    """矩形選択の確定（canvas_input_events）と同じ4点と画素になる。"""
    rng = np.random.default_rng(5)
    width, height = _size(canvas)
    for _ in range(20):
        start = (float(rng.uniform(-10, 120)), float(rng.uniform(-10, 120)))
        end = (float(rng.uniform(-10, 120)), float(rng.uniform(-10, 120)))
        x1, x2 = sorted((start[0], end[0]))
        y1, y2 = sorted((start[1], end[1]))
        _set_polygon(canvas, [(x1, y1), (x2, y1), (x2, y2), (x1, y2)])
        expected = canvas.selection_mask_bool(140, 140, 0, 0)
        selection = native.Selection.rect(start, end)
        assert np.array_equal(_mask(selection.mask(140, 140), 140, 140), expected)
        bounds = canvas.selection_bounds()
        assert selection.bounds() == (bounds.x(), bounds.y(), bounds.width(), bounds.height())
    assert width > 0 and height > 0


def _clip_pixels(canvas, width, height, offset_x, offset_y):
    """apply_selection_clip を付けた画家で塗りつぶし、塗れた画素を返す。"""
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    canvas.apply_selection_clip(painter, offset_x, offset_y)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    painter.fillRect(0, 0, width, height, QColor(255, 255, 255, 255))
    painter.end()
    return _qimage_premultiplied(image)[:, :, 3] > 0


def test_polygon_clip_matches_apply_selection_clip(canvas):
    rng = np.random.default_rng(23)
    width, height, left, top = 40, 30, 12, 8
    offsets = [(left, top), (left + 0.25, top - 0.5), (left - 0.75, top + 0.5)]
    for index, points in enumerate(_random_polygons(rng, left, top, width, height, 40)):
        _set_polygon(canvas, points)
        offset_x, offset_y = offsets[index % len(offsets)]
        expected = _clip_pixels(canvas, width, height, offset_x, offset_y)
        clip = _polygon(points).clip_mask(width, height, offset_x, offset_y)
        assert clip is not None
        assert np.array_equal(_mask(clip, width, height), expected), (points, offset_x, offset_y)
    _set_polygon(canvas, [(1.0, 1.0), (9.0, 9.0)])
    assert _clip_pixels(canvas, 8, 8, 0, 0).all(), "2点の多角形ではクリップしない"
    assert _polygon([(1.0, 1.0), (9.0, 9.0)]).clip_mask(8, 8) is None


def _random_mask(rng, width, height, density=0.5):
    """塊を持つ二値マスク（穴・斜めの接し方を含む）。"""
    small = rng.random((height // 3 + 1, width // 3 + 1)) < density
    mask = np.kron(small, np.ones((3, 3), dtype=bool))[:height, :width]
    noise = rng.random((height, width)) < 0.08
    return mask ^ noise


def test_mask_selection_matches_set_selection_mask(canvas):
    rng = np.random.default_rng(3)
    width, height = _size(canvas)
    for _ in range(4):
        mask = np.zeros((height, width), dtype=bool)
        mask[40:100, 50:130] = _random_mask(rng, 80, 60)
        assert canvas.set_selection_mask(mask)
        selection = native.Selection.from_mask(mask.astype(np.uint8).tobytes(), width, height)
        assert selection is not None and selection.is_mask
        bounds = canvas.selection_bounds()
        assert selection.bounds() == (bounds.x(), bounds.y(), bounds.width(), bounds.height())
        expected = [[(p.x(), p.y()) for p in outline] for outline in canvas.selection_outline_polygons]
        assert selection.outlines() == expected
        for offset in [(45, 35), (120, 90), (-5, -5)]:
            local = canvas.selection_mask_bool(30, 20, *offset)
            assert np.array_equal(_mask(selection.mask(30, 20, *offset), 30, 20), local)
            for shift in (0.0, 0.5, 1.5):
                expected_clip = _clip_pixels(canvas, 30, 20, offset[0] + shift, offset[1] + shift)
                clip = selection.clip_mask(30, 20, offset[0] + shift, offset[1] + shift)
                assert np.array_equal(_mask(clip, 30, 20), expected_clip)
    assert native.Selection.from_mask(bytes(width * height), width, height) is None


def test_contours_match_mask_contours():
    rng = np.random.default_rng(7)
    for size in [(1, 1), (5, 4), (17, 13), (40, 31)]:
        width, height = size
        for _ in range(6):
            mask = _random_mask(rng, width, height, float(rng.uniform(0.2, 0.8)))
            expected = LineOpsController._mask_contours(mask)
            contours = native.selection_mask_contours(mask.astype(np.uint8).tobytes(), width, height)
            assert contours == [[(int(p.x()), int(p.y())) for p in c] for c in expected]
            largest = LineOpsController._largest_contour(expected)
            index = native.selection_largest_contour(contours)
            if expected:
                assert index is not None and contours[index] == [
                    (int(p.x()), int(p.y())) for p in largest]
            else:
                assert index is None


def test_invert_matches_invert_selection(canvas):
    width, height = _size(canvas)
    area = canvas._canvas_area_rect()
    rect = (area.x(), area.y(), area.width(), area.height())
    for points in ([(10.5, 10.0), (300.0, 40.25), (120.0, 260.0)],
                   [(-20.0, -20.0), (60.0, -20.0), (60.0, 50.0), (-20.0, 50.0)]):
        _set_polygon(canvas, points)
        assert canvas.invert_selection()
        expected = canvas.selection_mask_bool(width, height)
        inverted = _polygon(points).inverted(width, height, rect)
        assert inverted is not None
        assert np.array_equal(_mask(inverted.mask(width, height), width, height), expected)
        bounds = canvas.selection_bounds()
        assert inverted.bounds() == (bounds.x(), bounds.y(), bounds.width(), bounds.height())
        # もう一度反転すると、元の多角形の画素（キャンバスの中）に戻る。
        again = inverted.inverted(width, height, rect)
        assert again is not None
        assert canvas.invert_selection()
        assert np.array_equal(_mask(again.mask(width, height), width, height),
                              canvas.selection_mask_bool(width, height))


def _ring_picture(width, height):
    """隙間のある黒い輪・白い島・赤い塊を描いた絵（ほかは透明）。"""
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    black = (20, 20, 20, 255)
    rgba[20:22, 20:80] = black
    rgba[78:80, 20:80] = black
    rgba[20:80, 20:22] = black
    rgba[20:80, 78:80] = black
    rgba[48:50, 78:80] = 0  # 右の辺に2画素の隙間
    rgba[40:50, 40:50] = (255, 255, 255, 255)
    rgba[30:35, 60:66] = (230, 40, 60, 255)
    rgba[100:104, 10:14] = (230, 40, 60, 255)
    rgba[60:62, 30:32] = (230, 40, 60, 128)
    return rgba


@pytest.mark.parametrize("adjacent, close_gap, gap, require_closed", [
    (True, False, 1, False),
    (True, False, 1, True),
    (True, True, 1, True),
    (True, True, 2, False),
    (False, False, 1, False),
])
def test_auto_select_matches_auto_select_region(window, canvas, adjacent, close_gap, gap,
                                                require_closed):
    width, height = _size(canvas)
    rgba = _ring_picture(width, height)
    _set_layer(canvas, rgba)
    image = _core_image(_layer_rgba(canvas))
    tools = window.tools
    tools.bucket_adjacent.setChecked(adjacent)
    tools.bucket_close_gap.setChecked(close_gap)
    tools.bucket_gap_width.setValue(gap)
    tools.bucket_require_closed.setChecked(require_closed)
    modifiers = Qt.KeyboardModifier
    clicks = [
        ((50, 60), modifiers.NoModifier),
        ((45, 45), modifiers.ShiftModifier),
        ((62, 32), modifiers.ShiftModifier),
        ((21, 50), modifiers.AltModifier),
        ((5, 5), modifiers.ShiftModifier),
        ((60, 30), modifiers.AltModifier | modifiers.ShiftModifier),
        ((width + 3, 5), modifiers.NoModifier),
    ]
    current = None
    for (x, y), modifier in clicks:
        before = canvas.selection_mask_bool(width, height)
        result = canvas.auto_select_region(QPointF(x, y), modifier)
        status, region = native.selection_contiguous_region(
            image, x, y, adjacent, gap if (close_gap and adjacent) else None,
            require_closed and adjacent)
        assert (status == "region") == (region is not None), status
        if region is None:
            assert not result
            assert np.array_equal(
                canvas.selection_mask_bool(width, height)
                if before is not None else np.zeros((height, width), bool),
                before if before is not None else np.zeros((height, width), bool))
            continue
        mode = ("subtract" if modifier & modifiers.AltModifier
                else "add" if modifier & modifiers.ShiftModifier else "replace")
        current = native.Selection.combine(current, region, width, height, mode)
        expected = canvas.selection_mask_bool(width, height)
        if current is None:
            assert expected is None
        else:
            assert np.array_equal(_mask(current.mask(width, height), width, height), expected)
    tools.bucket_adjacent.setChecked(True)
    tools.bucket_close_gap.setChecked(False)
    tools.bucket_require_closed.setChecked(False)


def test_color_mask_and_used_area_match(window, canvas):
    width, height = _size(canvas)
    _set_layer(canvas, _ring_picture(width, height))
    rgba = _layer_rgba(canvas)
    image = _core_image(rgba)
    # main_window_color_interaction の「この色を選択範囲にする」と同じ式。
    expected = (rgba[:, :, 3] > 0) & np.all(rgba[:, :, :3] == (230, 40, 60), axis=2)
    assert np.array_equal(_mask(native.selection_color_mask(image, (230, 40, 60)), width, height),
                          expected)
    assert canvas.auto_select_used_area()
    used = native.selection_used_area(image)
    assert used is not None
    expected_used = canvas.selection_mask_bool(width, height)
    assert np.array_equal(_mask(used.mask(width, height), width, height), expected_used)
    assert used.outlines() == [[(p.x(), p.y()) for p in canvas.selection_polygon]]
    assert native.selection_used_area(native.RgbaImage.blank(4, 4)) is None


def _selections(canvas):
    """多角形（投げ縄・矩形）とマスク（反転）の選択。"""
    width, height = _size(canvas)
    lasso = [(40.3, 30.7), (210.0, 50.5), (150.25, 160.0), (60.0, 140.0), (100.0, 90.0)]
    rect = [(25.5, 35.5), (90.0, 35.5), (90.0, 80.0), (25.5, 80.0)]
    mask = np.zeros((height, width), dtype=bool)
    mask[30:90, 30:120] = _random_mask(np.random.default_rng(9), 90, 60)
    return [("lasso", lasso), ("rect", rect), ("mask", mask)]


def _apply(canvas, kind, data):
    width, height = _size(canvas)
    if kind == "mask":
        assert canvas.set_selection_mask(data)
        selection = native.Selection.from_mask(data.astype(np.uint8).tobytes(), width, height)
        assert selection is not None
        return selection
    _set_polygon(canvas, data)
    return _polygon(data)


def _assert_same_pixels(actual_straight, expected_premultiplied):
    assert np.array_equal(_premultiply(actual_straight), expected_premultiplied)


def _assert_layer_equals(canvas, core_image):
    """レイヤーの画素（乗算済み）を比べる。完全に不透明・透明な画素はストレートでも同じ。"""
    layer = _layer_rgba(canvas)
    actual = _core_rgba(core_image)
    assert np.array_equal(_premultiply(actual), _premultiply(layer))
    exact = (layer[:, :, 3] == 0) | (layer[:, :, 3] == 255)
    assert np.array_equal(actual[exact], layer[exact])


@pytest.mark.parametrize("index", [0, 1, 2])
def test_copy_matches_copy_selection(canvas, index):
    width, height = _size(canvas)
    rgba = _random_picture(np.random.default_rng(index), width, height)
    _set_layer(canvas, rgba)
    image = _core_image(_layer_rgba(canvas))
    kind, data = _selections(canvas)[index]
    selection = _apply(canvas, kind, data)
    assert canvas.copy_selection()
    clip = QApplication.clipboard().image()
    copied_result = native.selection_copy(image, selection)
    assert copied_result is not None
    rect, copied = copied_result
    bounds = canvas.selection_bounds().intersected(QRect(0, 0, width, height))
    assert rect == (bounds.x(), bounds.y(), bounds.width(), bounds.height())
    _assert_same_pixels(_core_rgba(copied), _qimage_premultiplied(clip))


@pytest.mark.parametrize("index", [0, 1, 2])
def test_delete_and_cut_match(canvas, index):
    width, height = _size(canvas)
    rgba = _random_picture(np.random.default_rng(10 + index), width, height)
    _set_layer(canvas, rgba)
    original = _core_image(_layer_rgba(canvas))
    kind, data = _selections(canvas)[index]
    selection = _apply(canvas, kind, data)

    assert canvas.delete_selection_contents()
    undo = canvas.undo_stack[-1]
    result = native.selection_delete(original, selection)
    assert result is not None
    deleted, rect, before = result
    assert rect == (undo.rect.x(), undo.rect.y(), undo.rect.width(), undo.rect.height())
    before_rgba = np.frombuffer(before, dtype=np.uint8).reshape(rect[3], rect[2], 4)
    _assert_same_pixels(before_rgba, _qimage_premultiplied(undo.image))
    _assert_layer_equals(canvas, deleted)
    canvas.undo()

    selection = _apply(canvas, kind, data)
    assert canvas.cut_selection()
    clip = QApplication.clipboard().image()
    cut = native.selection_cut(original, selection)
    assert cut is not None
    clipboard, _copy_rect, after, edit = cut
    _assert_same_pixels(_core_rgba(clipboard), _qimage_premultiplied(clip))
    _assert_layer_equals(canvas, after)
    assert edit is not None and edit[0] == rect
    canvas.undo()


def test_cut_without_a_selection_matches(canvas):
    width, height = _size(canvas)
    _set_layer(canvas, _random_picture(np.random.default_rng(4), width, height))
    original = _core_image(_layer_rgba(canvas))
    assert canvas.cut_selection()
    clip = QApplication.clipboard().image()
    cut = native.selection_cut(original, None)
    assert cut is not None
    clipboard, rect, after, edit = cut
    assert rect == (0, 0, width, height)
    assert edit is not None and edit[0] == (0, 0, width, height)
    _assert_same_pixels(_core_rgba(clipboard), _qimage_premultiplied(clip))
    _assert_layer_equals(canvas, after)
    canvas.undo()


@pytest.mark.parametrize("index", [0, 1, 2, None])
def test_paste_matches_paste_clipboard(canvas, index):
    from paintmaskanimator.constants import OUTSIDE_MARGIN

    width, height = _size(canvas)
    rng = np.random.default_rng(20 + (index or 0))
    _set_layer(canvas, _random_picture(rng, width, height, semi=0.3))
    original = _core_image(_layer_rgba(canvas))
    clip_rgba = _random_picture(rng, 37, 29, semi=0.4)
    clip = QImage(clip_rgba.data, 37, 29, 37 * 4, QImage.Format.Format_RGBA8888).copy()
    QApplication.clipboard().setImage(clip)
    selection = None
    if index is not None:
        kind, data = _selections(canvas)[index]
        selection = _apply(canvas, kind, data)
    canvas.paste_clipboard()
    undo = canvas.undo_stack[-1]
    # Qt のクリップボードを通った画像（乗算済みに変わることがある）を Rust にも渡す。
    pasted = imaging.qimage_rgba_array(QApplication.clipboard().image())
    result = native.selection_paste(original, _core_image(pasted), selection,
                                    (OUTSIDE_MARGIN, OUTSIDE_MARGIN))
    assert result is not None
    after, rect, _before = result
    assert rect == (undo.rect.x(), undo.rect.y(), undo.rect.width(), undo.rect.height())
    _assert_layer_equals(canvas, after)
    canvas.undo()


@pytest.mark.parametrize("index", [0, 1, 2])
def test_lift_matches_begin_selection_transform(canvas, index):
    width, height = _size(canvas)
    rgba = _random_picture(np.random.default_rng(30 + index), width, height)
    _set_layer(canvas, rgba)
    original = _core_image(_layer_rgba(canvas))
    kind, data = _selections(canvas)[index]
    selection = _apply(canvas, kind, data)
    base = canvas._cleared_selection_base(canvas.active_layer.image)
    cleared = native.selection_cleared(original, selection)
    _assert_same_pixels(_core_rgba(cleared), _qimage_premultiplied(base))

    canvas.transform_quality = False
    assert canvas.begin_selection_transform("free")
    try:
        source_rect = canvas.transform_source_rect.toAlignedRect()
        expected_rect = (source_rect.x(), source_rect.y(), source_rect.width(), source_rect.height())
        assert native.selection_transform_source_rect(selection, width, height) == expected_rect
        lifted = native.selection_lift(original, selection)
        assert lifted is not None
        after, source, rect, _before = lifted
        assert rect == expected_rect
        _assert_same_pixels(_core_rgba(source), _qimage_premultiplied(canvas.transform_source))
        _assert_layer_equals(canvas, after)
    finally:
        canvas.cancel_selection_transform()


def test_clip_write_matches_a_clipped_drawing(canvas):
    """描画ツールと同じく、クリップした画家で塗った結果と clip_write が一致する。"""
    width, height = _size(canvas)
    rng = np.random.default_rng(41)
    _set_layer(canvas, _random_picture(rng, width, height))
    layer = _layer_rgba(canvas)
    for kind, data in _selections(canvas):
        selection = _apply(canvas, kind, data)
        rect = QRect(20, 25, 150, 120)
        patch = canvas.active_layer.image.read_qimage(
            rect.x(), rect.y(), rect.width(), rect.height())
        painter = QPainter(patch)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        canvas.apply_selection_clip(painter, rect.x(), rect.y())
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(0, 0, rect.width(), rect.height(), QColor(250, 210, 40, 255))
        painter.end()

        drawn = np.zeros((rect.height(), rect.width(), 4), dtype=np.uint8)
        drawn[:] = (250, 210, 40, 255)
        clip = selection.clip_mask(rect.width(), rect.height(), rect.x(), rect.y())
        area = (rect.x(), rect.y(), rect.width(), rect.height())
        result = native.selection_clip_write(_core_image(layer), area, drawn.tobytes(), clip)
        assert result is not None
        after, _changed, _before = result
        actual = _core_rgba(after)[rect.y():rect.y() + rect.height(),
                                   rect.x():rect.x() + rect.width()]
        _assert_same_pixels(actual, _qimage_premultiplied(patch))
