"""前処理の再利用が通常バケツの結果を変えないことを確認する。"""
import numpy as np
import pytest

from paintmaskanimator import bucket_fill


@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("options", [
    bucket_fill.BucketOptions(),
    bucket_fill.BucketOptions(extend_lines=True),
    bucket_fill.BucketOptions(close_gap=True, gap_width=2),
    bucket_fill.BucketOptions(extend_lines=True, close_gap=True, gap_width=2),
    bucket_fill.BucketOptions(require_closed=True),
    bucket_fill.BucketOptions(adjacent=False),
    bucket_fill.BucketOptions(include_masks=True, mask_colors=((255, 0, 0),)),
    bucket_fill.BucketOptions(target_color=bucket_fill.TARGET_TRANSPARENT),
    bucket_fill.BucketOptions(island_replacements=(((255, 0, 0), (160, 80, 20)),)),
])
def test_session_equals_independent_clicks_including_gap_seeds(options, indexed):
    pixels = np.zeros((46, 62, 4), dtype=np.uint8)
    pixels[4, 4:58] = pixels[40, 4:58] = (0, 0, 0, 255)
    pixels[4:41, 4] = pixels[4:41, 57] = (0, 0, 0, 255)
    pixels[5:39, 30] = (255, 0, 0, 255)
    pixels[17:19, 30] = 0
    pixels[12:16, 15] = (255, 0, 0, 255)
    pixels[7:10, 45:48] = (255, 255, 255, 255)
    session = bucket_fill.BucketSession(pixels, options, indexed=indexed)
    original = pixels.copy()
    for _repeat in range(2):
        for x, y in ((0, 0), (15, 20), (45, 20), (30, 18), (30, 25), (15, 13), (4, 10)):
            expected = bucket_fill.compute_fill_region(pixels, x, y, options)
            actual = session.compute(x, y)
            assert (actual.reason, actual.target, actual.bounds) == (
                expected.reason, expected.target, expected.bounds,
            )
            np.testing.assert_array_equal(actual.region, expected.region)
            compact = session.compact(x, y).materialize(pixels.shape[:2])
            assert compact.reason == expected.reason
            assert compact.bounds == expected.bounds
            np.testing.assert_array_equal(compact.region, expected.region)
            assert len(actual.islands) == len(expected.islands)
            for (color, mask), (other_color, other_mask) in zip(actual.islands, expected.islands):
                assert color == other_color
                np.testing.assert_array_equal(mask, other_mask)
    np.testing.assert_array_equal(pixels, original)


def test_session_prepares_a_colour_only_once(monkeypatch):
    pixels = np.zeros((30, 40, 4), dtype=np.uint8)
    pixels[:, 20] = (255, 0, 0, 255)
    original = bucket_fill._prepare_bucket
    calls = []

    def prepare(*args):
        calls.append(args[1])
        return original(*args)

    monkeypatch.setattr(bucket_fill, "_prepare_bucket", prepare)
    session = bucket_fill.BucketSession(pixels, bucket_fill.BucketOptions(extend_lines=True))
    for x in (5, 25, 10, 35):
        assert session.compute(x, 10).ok
    assert calls == [None]


def test_index_replaces_repeated_component_searches(monkeypatch):
    pixels = np.zeros((40, 60, 4), dtype=np.uint8)
    pixels[:, 30] = (0, 0, 0, 255)
    session = bucket_fill.BucketSession(pixels, bucket_fill.BucketOptions(), indexed=True)

    def unexpected(*args, **kwargs):
        pytest.fail("An indexed component must not need another flood search")

    monkeypatch.setattr(bucket_fill, "scanline_connected_region", unexpected)
    left = session.compact(10, 10)
    assert session.compact(20, 20) is left
    assert session.compact(40, 20) is not left
    assert len(session._indices) == 1
