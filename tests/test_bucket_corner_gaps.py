"""方向・細さ・角度を組み合わせた塗り残し拾いの正解マスク。"""
import numpy as np
import pytest

from paintmaskanimator import auto_bucket, bucket_fill


def corner_case(acute=True, diagonal=False):
    yy, xx = np.indices((160, 160))
    spread = (60 - xx) / 3 if acute else (60 - xx) * 2
    main = (xx >= 10) & (xx <= 60) & (np.abs(yy - 80) <= spread)
    # 額縁に触れないよう切る。
    main[:5] = main[-5:] = False
    gap = np.zeros_like(main)
    gap[82 if diagonal else 80, 62] = True
    return main, main | gap, main.copy()


def long_slit_case():
    main = np.zeros((160, 200), dtype=bool)
    main[70:130, 10:85] = True
    target = main.copy()
    for x in range(86, 158):
        y = 127 - (x - 86)
        target[y:y+2, x] = True
    expected = main.copy()
    # 線の向こうのベージュ相当。同じ対象色でも幅のある面は保護する。
    target[64:69, 40:65] = True
    target[64:69, 75:80] = True  # 25画素しかない別の面も保護
    return main, target, expected


def staircase_case():
    main = np.zeros((120, 120), dtype=bool)
    main[70:110, 5:30] = True
    target = main.copy()
    for step in range(65):
        target[69-step, 30+step] = True
    return main, target, target.copy()


def slit_continuation_case():
    main = np.zeros((160, 200), dtype=bool)
    for x in range(20, 30):
        main[150-x:152-x, x] = True
    target = main.copy()
    for x in range(30, 150):
        target[150-x, x] = True
    expected = target.copy()
    # 近くの広い面も、角で触れない別の細い領域も拾わない。
    target[132:150, 60:110] = True
    target[40:65, 15] = True
    return main, target, expected


CASES = {
    "acute_cardinal": lambda: corner_case(True, False),
    "obtuse_cardinal_protected": lambda: corner_case(False, False),
    "obtuse_diagonal": lambda: corner_case(False, True),
    "long_slit_and_protected_faces": long_slit_case,
    "diagonal_staircase": staircase_case,
    "slit_continuation": slit_continuation_case,
}


@pytest.mark.parametrize("name", CASES)
@pytest.mark.parametrize("turns", range(4))
@pytest.mark.parametrize("mirror", [False, True])
def test_corner_gap_expected_pixels(name, turns, mirror):
    arrays = CASES[name]()
    main, target, expected = [
        np.rot90(a[:, ::-1] if mirror else a, turns).copy() for a in arrays
    ]
    actual = bucket_fill.collect_small_islands(main, target, 1)
    np.testing.assert_array_equal(actual, expected)
    assert not (actual & ~target).any(), "線の画素を変更しない"


@pytest.mark.parametrize("indexed", [False, True])
def test_long_slit_through_bucket_session(indexed):
    main, target, expected = long_slit_case()
    pixels = np.zeros((*target.shape, 4), dtype=np.uint8)
    pixels[:, :, 3] = 255
    pixels[target, :3] = 255
    session = bucket_fill.BucketSession(
        pixels, bucket_fill.BucketOptions(island_max_width=1), indexed=indexed,
    )
    actual = session.compact(20, 90).materialize(target.shape).mask
    np.testing.assert_array_equal(actual, expected)
    assert not (actual & ~main).any(), "四角から線の向こうの斜め領域を拾わない"


def test_picked_slit_does_not_jump_another_line():
    main, target, expected = long_slit_case()
    target[53, 160] = True  # 追加した隙間だけから線を越えて届く点
    actual = bucket_fill.collect_small_islands(main, target, 1)
    np.testing.assert_array_equal(actual, expected)


def test_gap_disabled_and_input_masks_unchanged():
    main, target, _ = long_slit_case()
    original = main.copy(), target.copy()
    np.testing.assert_array_equal(bucket_fill.collect_small_islands(main, target, 0), main)
    bucket_fill.collect_small_islands(main, target, 1)
    np.testing.assert_array_equal(main, original[0])
    np.testing.assert_array_equal(target, original[1])


@pytest.mark.parametrize("name", CASES)
@pytest.mark.parametrize("scale", [2, 3])
def test_scaled_line_width(name, scale):
    main, target, expected = [
        a.repeat(scale, axis=0).repeat(scale, axis=1) for a in CASES[name]()
    ]
    actual = bucket_fill.collect_small_islands(main, target, scale)
    np.testing.assert_array_equal(actual, expected)


def test_selection_stops_at_its_boundary():
    _main, target, expected = slit_continuation_case()
    pixels = np.zeros((*target.shape, 4), dtype=np.uint8)
    pixels[:, :, 3] = 255
    pixels[target, :3] = 255
    selection = np.ones(target.shape, dtype=bool)
    selection[:, 130:] = False
    result = bucket_fill.compute_fill_region(
        pixels, 20, 130, bucket_fill.BucketOptions(island_max_width=1), selection,
    )
    np.testing.assert_array_equal(result.mask, expected & selection)


@pytest.mark.parametrize("indexed", [False, True])
def test_clicked_slit_reaches_tip_without_painting_other_faces(indexed):
    main, target, expected = slit_continuation_case()
    pixels = np.zeros((*target.shape, 4), dtype=np.uint8)
    pixels[:, :, 3] = 255
    pixels[target, :3] = 255
    session = bucket_fill.BucketSession(
        pixels, bucket_fill.BucketOptions(island_max_width=1), indexed=indexed,
    )
    actual = session.compact(20, 130).materialize(target.shape).mask
    np.testing.assert_array_equal(actual, expected)
    assert (expected & ~main).sum() > 64
    filled, _paints = auto_bucket.execute_click(
        pixels, auto_bucket.BucketClick(20, 130, (0, 190, 200)),
        bucket_fill.BucketOptions(island_max_width=1),
    )
    np.testing.assert_array_equal(filled.mask, expected)


@pytest.mark.parametrize("width", [1, 2, 5, 20])
def test_width_never_allows_jumping_a_line(width):
    main, target, expected = long_slit_case()
    actual = bucket_fill.collect_small_islands(main, target, width)
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("width", range(1, 6))
@pytest.mark.parametrize("limit", range(1, 6))
@pytest.mark.parametrize("indexed", [False, True])
def test_maximum_width_is_a_pixel_size_not_a_distance(width, limit, indexed):
    main = np.zeros((60, 60), dtype=bool)
    main[20:40, 5:20] = True
    island = np.zeros_like(main)
    island[20-width:20, 20:20+width] = True  # 主領域と一つの角で接する
    pixels = np.zeros((*main.shape, 4), dtype=np.uint8)
    pixels[:, :, 3] = 255
    pixels[main | island, :3] = 255
    options = bucket_fill.BucketOptions(island_max_width=limit)
    actual = bucket_fill.BucketSession(pixels, options, indexed=indexed)
    result = actual.compact(10, 30).materialize(main.shape).mask
    np.testing.assert_array_equal(result, main | island if width <= limit else main)


@pytest.mark.parametrize("length", [30, 300])
def test_one_pixel_slit_is_allowed_regardless_of_length(length):
    main = np.zeros((60, length + 40), dtype=bool)
    main[20:40, 5:20] = True
    target = main.copy()
    target[19, 20:20+length] = True
    actual = bucket_fill.collect_small_islands(main, target, 1)
    np.testing.assert_array_equal(actual, target)
