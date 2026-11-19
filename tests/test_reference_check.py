"""見本チェックの結果が Rust 版と NumPy 版で一致することを確かめる。"""
import numpy as np
import pytest

from paintmaskanimator import reference_check as rc


def assert_findings(actual, expected):
    assert len(actual) == len(expected)
    for left, right in zip(actual, expected):
        for field in ("kind", "rgb", "pixels", "bounds", "expected"):
            assert getattr(left, field) == getattr(right, field)
        if right.mask is None:
            assert left.mask is None
        else:
            assert left.mask.dtype == right.mask.dtype
            assert np.array_equal(left.mask, right.mask)


def assert_regions(actual, expected):
    for field in ("labels", "lines", "sizes", "x0", "x1", "y0", "y1"):
        left, right = getattr(actual, field), getattr(expected, field)
        assert left.dtype == right.dtype
        assert np.array_equal(left, right)
    assert actual.on_edge == expected.on_edge
    for index in range(len(expected.sizes)):
        assert actual.bounds(index) == expected.bounds(index)
        assert actual.value(index) == expected.value(index)


@pytest.mark.parametrize("seed,shape,colors,rooms", [
    (16, (1, 1), 3, 0), (5, (1, 31), 4, 0), (4, (53, 79), 10, 8),
    (12, (110, 161), 16, 23), (17, (260, 311), 45, 150),
])
def test_random_painted_cells(seed, shape, colors, rooms, monkeypatch):
    rng = np.random.default_rng(seed)
    palette = rng.integers(55, 255, (colors, 4), dtype=np.uint8)
    palette[:, 3] = 255
    height, width = shape
    original = np.zeros((height, width, 4), dtype=np.uint8)
    original[1:-1, 1:-1] = (255, 255, 255, 255)
    for _ in range(rooms):
        x, y = rng.integers([1, 1], [width - 12, height - 12])
        w, h = rng.integers(8, 12, size=2)
        original[y:y+h, x:x+w] = (12, 21, 26, 255)
        original[y+1:y+h-1, x+1:x+w-1] = palette[rng.integers(colors)]
    original[0, 0] = (*palette[0, :3], 127)
    current = original.copy()
    if rooms:
        current[y+1:y+h-1, x+1:x+w-1] = (61, 99, 249, 255)
    following = np.roll(original, (0, 1), axis=(0, 1))
    for pixels in (original, current, following, current[:, ::-1]):
        assert list(rc.color_counts(pixels).items()) == list(rc.color_counts_numpy(pixels).items())
        for minimum in (1, 5, 12):
            assert_findings(rc.compare_colors(original, pixels, minimum),
                            rc.compare_colors_numpy(original, pixels, minimum))
        assert_regions(rc.CellRegions(pixels), rc.CellRegions(pixels, use_native=False))

    cells = [rc.CellRegions(p) for p in (original, current, following)]
    numpy_cells = [rc.CellRegions(p, use_native=False) for p in (original, current, following)]
    actual = rc.find_mismatches(*cells)
    assert_findings(actual, rc.find_mismatches_numpy(*numpy_cells))
    # 色の不一致と判定できるよう、周囲に十分な種類の色を置く。
    if seed in (4, 17):
        assert any(f.kind == rc.KIND_MISMATCH for f in actual)

    monkeypatch.setattr(rc, "native", None)
    assert_findings(rc.find_mismatches(*numpy_cells), actual)


def test_partial_alpha_overwrite_and_extra_bounds():
    red = (220, 80, 80)
    green = (30, 180, 70)
    pixels = np.array([[(*red, 20), (*green, 42), (*red, 20), (*red, 255)]], dtype=np.uint8)
    clear = np.zeros_like(pixels)
    assert list(rc.color_counts(pixels).items()) == [(red, 1), (green, 1)]
    findings = rc.compare_colors(clear, pixels, 1)
    assert_findings(findings, rc.compare_colors_numpy(clear, pixels, 1))
    assert [f.bounds for f in findings] == [(3, 0, 4, 1), None]


def test_reference_without_backend(monkeypatch):
    pixels = np.full((8, 8, 4), (100, 150, 200, 255), dtype=np.uint8)
    expected = rc.CellRegions(pixels)
    monkeypatch.setattr(rc, "native", None)
    assert_regions(rc.CellRegions(pixels), expected)
    assert rc.color_counts(pixels) == {(100, 150, 200): 64}
    cells = [rc.CellRegions(pixels) for _ in range(3)]
    assert rc.find_mismatches(*cells) == []


def test_local_votes_match_full_canvas_at_edge():
    pixels = np.zeros((4, 5, 4), dtype=np.uint8)
    pixels[1:, :2] = (100, 120, 150, 255)
    pixels[1, 2] = (40, 30, 20, 1)
    cell = rc.CellRegions(pixels)
    vote = rc.native.reference_region_sampler(cell.labels, cell.lines)
    local = np.array([[False, True, True], [True, True, False]])
    bounds = (1, 1, 4, 3)
    full = np.zeros(cell.labels.shape, dtype=bool)
    full[1:3, 1:4] = local
    assert vote(local, bounds) == cell.region_under(full)
