"""Rust自動塗りの判定を従来のNumPy経路と照合する。"""
from types import SimpleNamespace

import numpy as np
import pytest

from paintmaskanimator import auto_bucket, auto_paint, bucket_fill, native, reference_check


needs_kernels = pytest.mark.skipif(
    not native.available() or not hasattr(native._native, "refine_face"),
    reason="new automatic paint kernels not installed",
)


@needs_kernels
@pytest.mark.parametrize("dtype", [np.bool_, np.int8, np.uint16, np.int32,
                                 np.int64, np.uint64, np.float64, ">u8"])
def test_run_regions_and_numbering_match_numpy(monkeypatch, dtype):
    rng = np.random.default_rng(821)
    for shape in ((1, 1), (1, 90), (61, 1), (81, 93)):
        values = rng.integers(0, 6, shape).astype(dtype)
        if dtype == np.float64:
            values /= 4  # Fractional values must keep the general NumPy path.
        elif dtype == np.int64:
            values -= 2
        elif dtype == np.uint64:
            values += np.uint64(1 << 62)
        actual = reference_check._label_runs(values)
        with monkeypatch.context() as reference:
            reference.setattr(native, "label_runs", lambda *args: None)
            reference.setattr(native, "connect_runs", lambda *args: None)
            expected = reference_check._label_runs(values)
        assert actual[1] == expected[1]
        np.testing.assert_array_equal(actual[0], expected[0])
        for a, b in zip(actual[2], expected[2]):
            np.testing.assert_array_equal(a, b)


@needs_kernels
@pytest.mark.parametrize("unknown,mixed,offset", [
    (False, False, 0), (False, False, 3), (True, False, 0),
    (False, True, 0), (False, False, -3),
])
def test_refinement_matches_numpy_with_partial_and_mixed_sources(monkeypatch, unknown, mixed, offset):
    raw = np.zeros((48, 64, 4), dtype=np.uint8)
    raw[12, 18:38] = raw[30, 18:38] = (0, 0, 0, 255)
    raw[12:31, 18] = raw[12:31, 37] = (0, 0, 0, 255)
    target = auto_paint._Cell(raw, target=True)
    filled = bucket_fill.BucketSession(raw, bucket_fill.BucketOptions(), indexed=True).compact(25, 20)
    image = np.roll(raw, offset, axis=1)
    image[13:30, 19 + offset:37 + offset] = (255, 223, 212, 255)
    if mixed:
        image[13:30, 28 + offset:37 + offset] = (234, 191, 53, 255)
    guide = auto_paint._Cell(image)
    guide.initial = auto_paint._Cell(np.roll(raw, offset, axis=1))
    if unknown:
        guide.undecided[20, 26 + offset] = True
    work = auto_bucket.BucketWork(target, bucket_fill.BucketOptions())
    work.alignment = SimpleNamespace(shift=lambda *args: ((0, 0), 1.0))
    actual = work._refine_face_color(guide, filled, 24)
    monkeypatch.setattr(native, "refine_face", lambda *args: None)
    expected = work._refine_face_color(guide, filled, 24)
    assert actual == expected
    if not unknown and not mixed:
        assert actual == (255, 223, 212)


def test_backend_contract_includes_automatic_paint():
    assert {"connect_runs", "refine_face", "GuideSampler", "label_runs"} <= set(native.REQUIRED_API)


@needs_kernels
@pytest.mark.parametrize("ignore,require,by_label", [(0, 0, False), (1, 0, True),
                                                    (2, 0, False), (6, 8, False)])
def test_compressed_sampler_matches_pixel_counts_and_middle_seed(ignore, require, by_label):
    rng = np.random.default_rng(413)
    palette = np.array([(0, 0, 0, 0), (0, 0, 0, 255), (255, 0, 0, 255),
                        (249, 245, 231, 255), (255, 223, 212, 255)], dtype=np.uint8)
    for shift in ((0, 0), (3, -5), (-9, 7), (50, 50)):
        image = palette[rng.integers(0, len(palette), (23, 31))]
        guide = auto_paint._Cell(image)
        guide.initial = auto_paint._Cell(palette[rng.integers(0, len(palette), (23, 31))])
        guide.open[:] = rng.random((23, 31)) < 0.07
        guide.undecided[:] = rng.random((23, 31)) < 0.08
        guide.confirmed = rng.random((23, 31)) < 0.8
        local = rng.random((12, 15)) < 0.6
        filled = bucket_fill.CompactFill('ok', None, (4, 5, 19, 17), local, (), (4, 5, 19, 17))
        actual = native.sample_region(guide, filled, shift, ignore=ignore, require=require,
                                      by_label=by_label)
        sy, sx, inside = auto_paint._shifted(guide, local, filled.bounds, shift)
        flags = (guide.boundary.astype(np.uint8) | guide.initial.boundary.astype(np.uint8) * 2
                 | (guide.open | guide.undecided).astype(np.uint8) * 4
                 | guide.confirmed.astype(np.uint8) * 8)
        used = inside & ((flags[sy, sx] & ignore) == 0) & ((flags[sy, sx] & require) == require)
        values = guide.regions.labels if by_label else guide.values
        value, _share = auto_paint._majority(values[sy[used], sx[used]])
        unknown_any = bool(((flags[sy[used], sx[used]] & 4) != 0).any())
        if value is None:
            expected = (int(inside.sum()), 0, None, 0, 0, False, unknown_any, 0, 0)
        else:
            matched = used & (values[sy, sx] == value)
            labels = np.unique(guide.regions.labels[sy[matched], sx[matched]])
            other = int(guide.regions.sizes[labels].sum())
            positions = np.flatnonzero(matched)
            at = positions[len(positions) // 2]
            bad = bool(((flags[sy[matched], sx[matched]] & 4) != 0).any())
            expected = (int(inside.sum()), int(used.sum()), value, int(matched.sum()), other,
                        bad, unknown_any, int(sy[at]), int(sx[at]))
        assert actual == expected


@needs_kernels
def test_native_kernels_reject_invalid_buffers():
    module = native._native
    with pytest.raises(ValueError):
        module.connect_runs(b"x", b"", b"", b"")
    with pytest.raises(ValueError):
        module.connect_runs(b"", b"", b"", np.array([1], dtype=np.uint64).tobytes())
    with pytest.raises(ValueError):
        module.refine_face(b"", b"", b"", b"", b"", b"", 2, 2, 5, (0.55, 0.9, 3., 0.5))
    with pytest.raises(ValueError):
        module.label_runs(b"x", 2, 2, 8)
    with pytest.raises(ValueError):
        module.GuideSampler(b"", b"", b"", b"", 1, 1)
