"""Unit tests for the binary-preserving mask transform core."""
import math

import numpy as np
import pytest

from paintmaskanimator import mask_transform


def rotation_quad(width, height, degrees, center_x, center_y, scale=1.0):
    radians = math.radians(degrees)
    cos = math.cos(radians) * scale
    sin = math.sin(radians) * scale
    return [
        (
            center_x + (x - width / 2) * cos - (y - height / 2) * sin,
            center_y + (x - width / 2) * sin + (y - height / 2) * cos,
        )
        for x, y in ((0, 0), (width, 0), (width, height), (0, height))
    ]


def connected_components(mask):
    """Number of 8-connected regions in a boolean mask."""
    labelled = np.zeros(mask.shape, dtype=bool)
    height, width = mask.shape
    found = 0
    for start_y in range(height):
        for start_x in range(width):
            if not mask[start_y, start_x] or labelled[start_y, start_x]:
                continue
            found += 1
            stack = [(start_y, start_x)]
            labelled[start_y, start_x] = True
            while stack:
                y, x = stack.pop()
                for step_y in (-1, 0, 1):
                    for step_x in (-1, 0, 1):
                        next_y, next_x = y + step_y, x + step_x
                        if (
                            0 <= next_y < height and 0 <= next_x < width
                            and mask[next_y, next_x]
                            and not labelled[next_y, next_x]
                        ):
                            labelled[next_y, next_x] = True
                            stack.append((next_y, next_x))
    return found


def solid_labels(width, height, label=1, margin=0):
    labels = np.zeros((height, width), dtype=np.uint16)
    labels[margin:height - margin or None, margin:width - margin or None] = label
    return labels


def test_homography_maps_the_given_corners():
    source = [(0, 0), (10, 0), (10, 20), (0, 20)]
    target = [(3, 5), (23, 9), (21, 33), (1, 29)]
    matrix = mask_transform.homography_matrix(source, target)
    for (x, y), (u, v) in zip(source, target):
        point = matrix @ np.array([x, y, 1.0])
        assert point[0] / point[2] == pytest.approx(u)
        assert point[1] / point[2] == pytest.approx(v)


def test_quarter_turns_are_lossless():
    """90/180/270 degrees map pixel centers exactly, so nothing may change."""
    rng = np.random.default_rng(3)
    labels = rng.integers(1, 5, (24, 24)).astype(np.uint16)
    for degrees in (90, 180, 270):
        mapper = mask_transform.QuadMapper(
            rotation_quad(24, 24, degrees, 12, 12), 24, 24
        )
        result, _coverage = mask_transform.render_labels(
            labels, mapper, 24, 24
        )
        expected = np.rot90(labels, k=-(degrees // 90))
        assert np.array_equal(result, expected)


def test_output_only_ever_contains_source_labels():
    labels = np.zeros((40, 40), dtype=np.uint16)
    labels[5:35, 5:20] = 1
    labels[5:35, 20:35] = 2
    mapper = mask_transform.QuadMapper(
        rotation_quad(40, 40, 33, 40, 40, scale=1.7), 40, 40
    )
    result, _coverage = mask_transform.render_labels(labels, mapper, 80, 80)
    assert set(np.unique(result)).issubset({0, 1, 2})


def test_rotated_edge_is_a_regular_staircase():
    """Step lengths must be the two integers bracketing the ideal step.

    A 15 degree edge advances one pixel every ``1/tan(15)`` = 3.73 rows, so a
    correct staircase only ever uses runs of 3 and 4.  Forward mapping or a
    blurred threshold scatters other run lengths in between.
    """
    labels = solid_labels(120, 120, margin=10)
    for degrees in (15, 30):
        mapper = mask_transform.QuadMapper(
            rotation_quad(120, 120, degrees, 150, 150), 120, 120
        )
        result, _coverage = mask_transform.render_labels(
            labels, mapper, 300, 300
        )
        filled = result == 1
        leftmost = np.array([
            row[0] if row.size else -1
            for row in (np.flatnonzero(line) for line in filled)
        ])
        present = leftmost[leftmost >= 0]
        # Middle half of the rows: one edge, without the corner transitions.
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
        runs = runs[1:-1]
        ideal = 1.0 / math.tan(math.radians(degrees))
        allowed = {math.floor(ideal), math.ceil(ideal)}
        assert set(runs) <= allowed, (degrees, sorted(set(runs)), allowed)


def test_coverage_is_true_area_not_a_blur():
    """A half-covering shift must report ~50% coverage for the covered label."""
    labels = np.ones((20, 20), dtype=np.uint16)
    # Shift the source half a pixel: every output pixel is half source, half
    # nothing along the seam row.
    target = [(0.0, 0.5), (20.0, 0.5), (20.0, 20.5), (0.0, 20.5)]
    mapper = mask_transform.QuadMapper(target, 20, 20)
    _result, coverage = mask_transform.render_labels(
        labels, mapper, 20, 21, coverage_labels=(1,)
    )
    seam = coverage[1][0]
    assert np.all(seam == seam[0])
    assert 100 <= int(seam[0]) <= 155


def test_single_pixel_details_are_not_silently_dropped():
    """Resampling must not double as dust removal: a 1px dot is artwork."""
    labels = np.ones((60, 60), dtype=np.uint16)
    for y, x in ((20, 20), (30, 42), (45, 15)):
        labels[y, x] = 2
    mapper = mask_transform.QuadMapper(
        rotation_quad(60, 60, 23, 50, 50), 60, 60
    )
    result, _coverage = mask_transform.render_labels(labels, mapper, 100, 100)
    assert np.count_nonzero(result == 2) >= 2


def test_lower_label_id_wins_a_tie():
    """Ids double as priority: line colors get the low ids and take ties."""
    values = np.array([[3, 3, 7, 7], [5, 5, 2, 2]], dtype=np.uint16)
    winner, count = mask_transform._majority(values)
    assert list(winner) == [3, 2]
    assert list(count) == [2, 2]


def test_thin_line_survives_a_heavy_shrink():
    """A 2px line shrunk to 0.7px must stay unbroken, not dash."""
    labels = np.ones((100, 100), dtype=np.uint16)
    labels[:, 49:51] = 2                   # a 2px line
    mapper = mask_transform.QuadMapper(
        rotation_quad(100, 100, 20, 40, 40, scale=0.35), 100, 100
    )
    unprotected, _coverage = mask_transform.render_labels(
        labels, mapper, 80, 80, coverage_labels=(2,)
    )
    protected, coverage = mask_transform.render_labels(
        labels, mapper, 80, 80, coverage_labels=(2,), protect_labels=(2,)
    )
    assert connected_components(unprotected == 2) > 1     # breaks into dashes
    assert connected_components(protected == 2) == 1      # one unbroken line
    # The pixels holding it together must also survive the caller's threshold.
    assert np.all(coverage[2][protected == 2] > 0)


def test_protection_does_not_widen_a_thick_line():
    """The ridge rule must not fatten lines that are already wider than 1px."""
    labels = np.ones((60, 60), dtype=np.uint16)
    labels[:, 26:34] = 2                   # an 8px line, kept at full size
    mapper = mask_transform.QuadMapper(
        rotation_quad(60, 60, 18, 45, 45), 60, 60
    )
    plain, _ = mask_transform.render_labels(
        labels, mapper, 90, 90, coverage_labels=(2,)
    )
    protected, _ = mask_transform.render_labels(
        labels, mapper, 90, 90, coverage_labels=(2,), protect_labels=(2,)
    )
    assert np.array_equal(plain, protected)


def test_bands_do_not_change_the_result():
    """Row banding is only a memory bound; it must not alter any pixel."""
    rng = np.random.default_rng(11)
    labels = rng.integers(0, 4, (60, 60)).astype(np.uint16)
    mapper = mask_transform.QuadMapper(
        rotation_quad(60, 60, 24, 50, 50), 60, 60
    )
    whole, _ = mask_transform.render_labels(
        labels, mapper, 100, 100, band_rows=1000
    )
    banded, _ = mask_transform.render_labels(
        labels, mapper, 100, 100, band_rows=7
    )
    assert np.array_equal(whole, banded)


def test_perspective_quad_still_maps_its_corners():
    labels = solid_labels(40, 40, margin=2)
    target = [(0.0, 0.0), (60.0, 8.0), (52.0, 58.0), (6.0, 40.0)]
    mapper = mask_transform.QuadMapper(target, 40, 40)
    result, _coverage = mask_transform.render_labels(labels, mapper, 70, 70)
    assert np.any(result == 1)
    filled = np.argwhere(result == 1)
    # The rendered shape stays inside the requested quad's bounding box.
    assert filled[:, 0].max() <= 58
    assert filled[:, 1].max() <= 60


def random_label_image(rng, width, height, colors=5):
    """Flat artwork: a few wide regions rather than per-pixel noise."""
    image = np.zeros((height, width), dtype=np.uint16)
    for label in range(1, colors + 1):
        x0 = int(rng.integers(0, width))
        y0 = int(rng.integers(0, height))
        x1 = min(width, x0 + int(rng.integers(1, width)))
        y1 = min(height, y0 + int(rng.integers(1, height)))
        image[y0:y1, x0:x1] = label
    return image


@pytest.mark.parametrize("degrees", [0.0, 7.5, 45.0, 90.0, 183.0])
@pytest.mark.parametrize("scale", [0.4, 1.0, 2.3])
def test_quad_render_matches_numpy(degrees, scale):
    rng = np.random.default_rng(int(degrees * 10) + int(scale * 100))
    width, height = 37, 23
    image = random_label_image(rng, width, height)
    quad = rotation_quad(width, height, degrees, width, height, scale)
    mapper = mask_transform.QuadMapper(quad, width, height)
    out_width, out_height = 61, 47
    coverage_labels = (1, 3)
    expected, expected_coverage = mask_transform.render_labels_numpy(
        image, mapper, out_width, out_height,
        coverage_labels=coverage_labels, band_rows=8,
    )
    actual, actual_coverage = mask_transform.render_labels(
        image, mapper, out_width, out_height, coverage_labels=coverage_labels,
    )
    assert actual.dtype == np.uint16
    np.testing.assert_array_equal(actual, expected)
    assert sorted(actual_coverage) == sorted(expected_coverage)
    for label, plane in expected_coverage.items():
        np.testing.assert_array_equal(actual_coverage[label], plane)


@pytest.mark.parametrize("subsamples", [1, 2, 4, 6])
def test_quad_render_matches_numpy_at_every_subsample_rate(subsamples):
    rng = np.random.default_rng(subsamples)
    image = random_label_image(rng, 29, 31)
    # A perspective quad, so the projective divisor is exercised too.
    quad = [(2.0, 1.0), (40.0, 6.0), (33.0, 38.0), (-3.0, 27.0)]
    mapper = mask_transform.QuadMapper(quad, 29, 31)
    expected, _coverage = mask_transform.render_labels_numpy(
        image, mapper, 44, 40, subsamples=subsamples, band_rows=7,
    )
    actual, _actual_coverage = mask_transform.render_labels(
        image, mapper, 44, 40, subsamples=subsamples,
    )
    np.testing.assert_array_equal(actual, expected)


def test_quad_render_keeps_protected_lines_connected():
    image = np.zeros((40, 40), dtype=np.uint16)
    image[20, :] = 1  # a one-pixel line that a strong shrink would dissolve
    quad = rotation_quad(40, 40, 0.0, 20, 20, 0.2)
    mapper = mask_transform.QuadMapper(quad, 40, 40)
    labels, coverage = mask_transform.render_labels(
        image, mapper, 40, 40, coverage_labels=(1,), protect_labels=(1,),
    )
    rows = np.flatnonzero((labels == 1).any(axis=1))
    assert rows.size == 1
    row = labels[rows[0]] == 1
    assert connected_components(row[None, :]) == 1
    assert coverage[1][rows[0]][row].min() == 255


def test_quad_render_reports_progress_once():
    image = np.ones((8, 8), dtype=np.uint16)
    mapper = mask_transform.QuadMapper(
        rotation_quad(8, 8, 0.0, 4, 4), 8, 8
    )
    seen = []
    mask_transform.render_labels(
        image, mapper, 8, 8, progress=lambda done, total: seen.append((done, total))
    )
    assert seen[0][0] == 0
    assert seen[-1][0] == seen[-1][1]
