"""Canvas-level guarantees for the TP quality transform preview."""
import math

import numpy as np
import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import QApplication

from paintmaskanimator import imaging
from paintmaskanimator.canvas import PaintCanvas

SIZE = 120
FILL = (200, 40, 40)
LINE = (0, 0, 0)


@pytest.fixture
def canvas():
    app = QApplication.instance() or QApplication([])
    widget = PaintCanvas()
    yield widget
    widget.deleteLater()
    app.processEvents()


def make_source(line_width=2):
    """White is the transform's transparency, so it doubles as the background.

    The fill carries a cross of thin lines: line and fill compete along both
    axes, which is where a resampler gives away that it is losing pixels.
    """
    rgba = np.zeros((SIZE, SIZE, 4), np.uint8)
    rgba[:, :] = (255, 255, 255, 255)
    rgba[20:SIZE - 20, 20:SIZE - 20] = (*FILL, 255)
    middle = SIZE // 2
    rgba[20:SIZE - 20, middle:middle + line_width] = (*LINE, 255)
    rgba[middle:middle + line_width, 20:SIZE - 20] = (*LINE, 255)
    return imaging.rgba_array_to_qimage(rgba)


def connected_components(mask):
    """Number of 8-connected regions, used to detect a line breaking apart."""
    seen = np.zeros(mask.shape, dtype=bool)
    height, width = mask.shape
    found = 0
    for start_y in range(height):
        for start_x in range(width):
            if not mask[start_y, start_x] or seen[start_y, start_x]:
                continue
            found += 1
            stack = [(start_y, start_x)]
            seen[start_y, start_x] = True
            while stack:
                y, x = stack.pop()
                for step_y in (-1, 0, 1):
                    for step_x in (-1, 0, 1):
                        next_y, next_x = y + step_y, x + step_x
                        if (
                            0 <= next_y < height and 0 <= next_x < width
                            and mask[next_y, next_x] and not seen[next_y, next_x]
                        ):
                            seen[next_y, next_x] = True
                            stack.append((next_y, next_x))
    return found


def rotate(degrees, scale=1.0, center=None):
    center_x, center_y = center or (SIZE, SIZE)
    radians = math.radians(degrees)
    cos = math.cos(radians) * scale
    sin = math.sin(radians) * scale
    return [
        QPointF(
            center_x + (x - SIZE / 2) * cos - (y - SIZE / 2) * sin,
            center_y + (x - SIZE / 2) * sin + (y - SIZE / 2) * cos,
        )
        for x, y in ((0, 0), (SIZE, 0), (SIZE, SIZE), (0, SIZE))
    ]


def render(canvas, points, mode="free"):
    source = make_source()
    canvas.transform_active = True
    canvas.transform_mode = mode
    canvas.transform_source = source
    canvas.transform_source_rect = QRectF(0, 0, SIZE, SIZE)
    canvas.transform_points = points
    canvas.transform_tp_line_colors = (LINE,)
    canvas.transform_quality_active = True
    canvas._clear_tp_transform_masks()
    assert canvas._prepare_tp_transform_masks(source)
    image, _rect = canvas._tp_mask_preview_image(source, SIZE * 2, SIZE * 2)
    return imaging.qimage_rgba_array(image), imaging.qimage_rgba_array(source)


@pytest.mark.parametrize("degrees", [15, 30, 45, 63])
def test_rotation_output_stays_binary(canvas, degrees):
    """No partial alpha and no color the source did not already contain."""
    rendered, _source = render(canvas, rotate(degrees))
    alpha = rendered[:, :, 3]
    assert not np.any((alpha > 0) & (alpha < 255))
    present = {
        tuple(int(channel) for channel in color)
        for color in np.unique(rendered[alpha == 255][:, :3], axis=0)
    }
    assert present <= {FILL, LINE}


def test_rotation_leaves_no_gaps_between_colors(canvas):
    """A transparent pixel fully surrounded by opaque ones is a seam artifact."""
    rendered, _source = render(canvas, rotate(37))
    opaque = rendered[:, :, 3] > 0
    neighbours = (
        opaque[:-2, 1:-1].astype(int) + opaque[2:, 1:-1]
        + opaque[1:-1, :-2] + opaque[1:-1, 2:]
    )
    assert not np.any(~opaque[1:-1, 1:-1] & (neighbours == 4))


@pytest.mark.parametrize("degrees", [90, 180, 270])
def test_quarter_turns_keep_every_pixel(canvas, degrees):
    rendered, source = render(canvas, rotate(degrees))
    for color in (FILL, LINE):
        before = np.count_nonzero(np.all(source[:, :, :3] == color, axis=2))
        after = np.count_nonzero(
            np.all(rendered[:, :, :3] == color, axis=2) & (rendered[:, :, 3] > 0)
        )
        assert after == before, color


@pytest.mark.parametrize("degrees", [15, 30])
def test_rotated_edge_advances_in_regular_steps(canvas, degrees):
    """Step lengths may only be the two integers bracketing the ideal step."""
    rendered, _source = render(canvas, rotate(degrees))
    # The silhouette's left edge is one straight rotated line, so its steps are
    # directly comparable with the ideal staircase for this angle.
    silhouette = rendered[:, :, 3] > 0
    leftmost = np.array([
        row[0] if row.size else -1
        for row in (np.flatnonzero(line) for line in silhouette)
    ])
    present = leftmost[leftmost >= 0]
    segment = present[present.size // 4:3 * present.size // 4]
    runs = []
    current = 1
    for delta in np.diff(segment.astype(int)):
        if delta == 0:
            current += 1
        else:
            runs.append(current)
            current = 1
    runs.append(current)
    ideal = 1.0 / math.tan(math.radians(degrees))
    assert set(runs[1:-1]) <= {math.floor(ideal), math.ceil(ideal)}


@pytest.mark.parametrize("scale", [0.3, 0.2])
def test_line_survives_a_heavy_shrink_unbroken(canvas, scale):
    """A 2px line shrunk well below one pixel must not dissolve."""
    rendered, _source = render(canvas, rotate(20, scale=scale, center=(60, 60)))
    line = np.all(rendered[:, :, :3] == LINE, axis=2) & (rendered[:, :, 3] > 0)
    # The line is 80px long, so after the shrink it still spans about
    # 80 * scale * cos(20) rows.
    expected_rows = 80 * scale * math.cos(math.radians(20))
    assert np.count_nonzero(line.any(axis=1)) >= expected_rows * 0.8
    assert connected_components(line) == 1


def test_line_width_threshold_reuses_the_transformed_geometry(canvas):
    """Moving the line-width slider must not re-run the transform."""
    rendered, _source = render(canvas, rotate(23))
    cache_key = canvas._tp_geometry_cache_key
    assert cache_key is not None
    widths = []
    for threshold in (40, 96, 200):
        canvas.transform_line_threshold = threshold
        canvas._invalidate_tp_preview_cache(geometry=False)
        image, _rect = canvas._tp_mask_preview_image(
            canvas.transform_source, SIZE * 2, SIZE * 2
        )
        result = imaging.qimage_rgba_array(image)
        assert not np.any(
            (result[:, :, 3] > 0) & (result[:, :, 3] < 255)
        )
        widths.append(int(np.count_nonzero(
            np.all(result[:, :, :3] == LINE, axis=2) & (result[:, :, 3] > 0)
        )))
    assert canvas._tp_geometry_cache_key == cache_key
    assert widths[0] >= widths[1] >= widths[2]


def test_mesh_transform_is_binary_too(canvas):
    reference = canvas._regular_mesh_reference_points(
        QRectF(0, 0, SIZE, SIZE), 3, 3
    )
    canvas.transform_mesh_cols = canvas.transform_mesh_rows = 3
    canvas.transform_mesh_reference_points = reference
    points = [
        QPointF(point.x() + 40 + (18 if index % 3 == 1 else 0), point.y() + 40)
        for index, point in enumerate(reference)
    ]
    rendered, _source = render(canvas, points, mode="mesh")
    alpha = rendered[:, :, 3]
    assert not np.any((alpha > 0) & (alpha < 255))
    present = {
        tuple(int(channel) for channel in color)
        for color in np.unique(rendered[alpha == 255][:, :3], axis=0)
    }
    assert present <= {FILL, LINE}


def test_degenerate_quad_does_not_raise(canvas):
    source = make_source()
    canvas.transform_active = True
    canvas.transform_mode = "free"
    canvas.transform_source = source
    canvas.transform_source_rect = QRectF(0, 0, SIZE, SIZE)
    canvas.transform_tp_line_colors = (LINE,)
    canvas.transform_quality_active = True
    canvas._clear_tp_transform_masks()
    canvas._prepare_tp_transform_masks(source)
    canvas.transform_points = [QPointF(10, 10)] * 4
    canvas._invalidate_tp_preview_cache()
    image, _rect = canvas._tp_mask_preview_image(source, SIZE * 2, SIZE * 2)
    assert image is not None
