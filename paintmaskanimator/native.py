"""Required Rust backend for timeline state and image computations.

Missing or outdated extensions fail at startup with a rebuild instruction.
NumPy remains in use for array conversion and algorithms not yet moved to Rust.
"""
from __future__ import annotations

import numpy as np

from .logging_setup import get_logger

log = get_logger(__name__)

REQUIRED_API = (
    "TimelineCell", "resolve_timeline", "ClipAnimationReader", "GuideSampler",
    "resolve_exposures", "tint_onion", "unique_colors", "filter_palette",
    "line_flow_scores", "connect_runs", "label_runs", "refine_face", "thin",
    "flood_fill", "dilate", "erode", "label_regions",
)
REBUILD_MESSAGE = (
    "PaintMaskAnimator requires its Rust extension. "
    "Install or rebuild it from the project directory: python -m pip install ./native"
)


def validate_backend(module):
    missing = [name for name in REQUIRED_API if not callable(getattr(module, name, None))]
    if missing:
        raise ImportError(f"{REBUILD_MESSAGE} (missing API: {', '.join(missing)})")


try:
    import pma_native as _native
except ImportError as error:
    raise ImportError(REBUILD_MESSAGE) from error
validate_backend(_native)


def available() -> bool:
    """Compatibility probe: the required backend was validated on import."""
    return True


def clip_animation_reader(path: str, max_dimension: int, max_raster_bytes: int):
    """CLIP parsing has one implementation, supplied by clipfile-rs."""
    return _native.ClipAnimationReader(path, max_dimension, max_raster_bytes)


def resolve_exposures(states, exposures):
    count, layers = states.shape
    data = _native.resolve_exposures(
        states.tobytes(), exposures.tobytes(), count, layers,
    )
    return np.frombuffer(data, dtype=np.int64).reshape(count, layers)


def resolve_timeline(frames):
    """Read Rust-owned cell metadata directly, without packing NumPy inputs."""
    rows = [[layer.__dict__.get("_timeline_metadata") for layer in frame.layers]
            for frame in frames]
    layers = max(map(len, rows), default=0)
    data = _native.resolve_timeline(rows)
    return np.frombuffer(data, dtype=np.int64).reshape(len(rows), layers)


def tint_onion(rgba, color, color_enabled, selected):
    data = _native.tint_onion(
        np.ascontiguousarray(rgba).tobytes(), tuple(color), bool(color_enabled),
        None if selected is None else [(int(rgb[0]), int(rgb[1]), int(rgb[2])) for rgb in selected],
    )
    return np.frombuffer(data, dtype=np.uint8).reshape(rgba.shape)


def unique_colors(pixels, limit):
    """Return the smallest sorted packed RGB colors, using the required Rust backend."""
    return _native.unique_colors(
        np.ascontiguousarray(pixels, dtype=np.uint32).tobytes(), int(limit)
    )


def filter_palette(rgba, visible, legacy, remap):
    """Filter original RGB colors and remap visible pixels in one Rust pass."""
    def rgb_tuple(rgb):
        return (int(rgb[0]), int(rgb[1]), int(rgb[2]))

    data = _native.filter_palette(
        np.ascontiguousarray(rgba, dtype=np.uint8).tobytes(),
        None if visible is None else [rgb_tuple(rgb) for rgb in visible],
        None if legacy is None else rgb_tuple(legacy),
        [(rgb_tuple(child), rgb_tuple(parent)) for child, parent in remap.items()],
    )
    return np.frombuffer(data, dtype=np.uint8).reshape(rgba.shape)


def line_flow_scores(target, guide, block, base_y, base_x, reach):
    """位置合わせの一致数をRustで計算する。"""
    height, width = target.shape
    result = _native.line_flow_scores(
        _as_bytes(target), _as_bytes(guide), width, height, int(block),
        np.ascontiguousarray(base_y, dtype=np.int64).tobytes(),
        np.ascontiguousarray(base_x, dtype=np.int64).tobytes(), int(reach),
    )
    side = 2 * reach + 1
    return np.frombuffer(result, dtype=np.uint32).reshape(
        side * side, height // block, width // block,
    ).astype(np.float64)


def _as_bytes(mask):
    return np.ascontiguousarray(mask, dtype=bool).tobytes()


def connect_runs(starts, ends, kinds, rows):
    """同色の上下ランをRustで結合する。"""
    data, count = _native.connect_runs(*(
        np.ascontiguousarray(values, dtype=np.uint64).tobytes()
        for values in (starts, ends, kinds, rows)
    ))
    return np.frombuffer(data, dtype=np.int32).copy(), count


def label_runs(values):
    """連結領域の走査・結合・番号画像の作成をRustでまとめて行う。"""
    # 大画像は転送・番号画像の複製が支配的になるため、NumPyの走査と
    # connect_runsのRust結合を併用する方が速い（実データで確認）。
    if (values.dtype.kind not in "biu" or not values.dtype.isnative
            or values.size > 65536):
        return None
    height, width = values.shape
    labels, count, ys, xs, ends, runs = _native.label_runs(
        np.ascontiguousarray(values).tobytes(), width, height, values.dtype.itemsize,
    )
    return (np.frombuffer(labels, dtype=np.int32).reshape(height, width).copy(), count,
            (np.frombuffer(ys, dtype=np.int64).copy(), np.frombuffer(xs, dtype=np.int64).copy(),
             np.frombuffer(ends, dtype=np.int64).copy(), np.frombuffer(runs, dtype=np.int32).copy()))


def refine_face(guide, filled, shift, bounds, radius, thresholds):
    """必要な小矩形だけを渡し、ずれの探索・採色・面積比確認を一括実行する。"""
    ax0, ay0, ax1, ay1 = bounds
    ys, xs = np.nonzero(filled.local)
    points = np.column_stack((ys + filled.bounds[1] + shift[0] - ay0,
                              xs + filled.bounds[0] + shift[1] - ax0))
    roi = np.s_[ay0:ay1, ax0:ax1]
    return _native.refine_face(
        np.ascontiguousarray(guide.values[roi], dtype=np.uint64).tobytes(),
        np.ascontiguousarray(guide.regions.labels[roi], dtype=np.uint32).tobytes(),
        _as_bytes(~guide.initial.boundary[roi]),
        _as_bytes(guide.open[roi] | guide.undecided[roi]),
        np.ascontiguousarray(guide.regions.sizes, dtype=np.uint64).tobytes(),
        np.ascontiguousarray(points, dtype=np.int64).tobytes(),
        ax1 - ax0, ay1 - ay0, radius, thresholds,
    )


def sample_region(guide, filled, shift=(0, 0), *, ignore=0, require=0, by_label=False):
    """不変の見本をラン圧縮して保持し、面の重なりと色をRustで数える。"""
    immutable = getattr(guide, "_native_immutable", False)
    sampler = getattr(guide, "_native_sampler", None) if immutable else None
    if sampler is None:
        initial = guide.initial if guide.initial is not None else guide
        flags = guide.boundary.astype(np.uint8)
        flags |= initial.boundary.astype(np.uint8) * 2
        flags |= (guide.open | guide.undecided).astype(np.uint8) * 4
        confirmed = getattr(guide, "confirmed", None)
        flags |= 8 if confirmed is None else confirmed.astype(np.uint8) * 8
        sampler = _native.GuideSampler(
            np.ascontiguousarray(guide.values, dtype=np.uint64).tobytes(),
            np.ascontiguousarray(guide.regions.labels, dtype=np.uint32).tobytes(),
            flags.tobytes(),
            np.ascontiguousarray(guide.regions.sizes, dtype=np.uint64).tobytes(),
            guide.values.shape[1], guide.values.shape[0],
        )
        if immutable:
            guide._native_sampler = sampler
    runs = getattr(filled, "_native_runs", None)
    if runs is None:
        padded = np.pad(filled.local, ((0, 0), (1, 1)))
        ys, xs = np.nonzero(padded[:, 1:] != padded[:, :-1])
        points = np.column_stack((ys[::2] + filled.bounds[1], xs[::2] + filled.bounds[0],
                                  xs[1::2] + filled.bounds[0]))
        runs = np.ascontiguousarray(points, dtype=np.int64).tobytes()
        filled._native_runs = runs
    return sampler.sample(runs, int(shift[0]), int(shift[1]), ignore, require, by_label)


def thin(mask, iterations):
    """線端検出用の細線化をRustで計算する。"""
    height, width = mask.shape
    return _as_mask(_native.thin(_as_bytes(mask), width, height, int(iterations)), height, width)


def _as_mask(data, height, width):
    # 返ってきた bytes は読み取り専用なので、呼び出し側が書き換えられるよう複製する。
    return np.frombuffer(data, dtype=bool).reshape(height, width).copy()


def flood_fill(passable, seeds):
    """4近傍で ``seeds`` からつながる ``passable`` の領域。"""
    height, width = passable.shape
    seeds = [(int(x), int(y)) for x, y in seeds]
    return _as_mask(
        _native.flood_fill(_as_bytes(passable), width, height, seeds), height, width
    )


def dilate(mask, iterations):
    """8近傍で ``iterations`` 回膨張させる。"""
    height, width = mask.shape
    return _as_mask(
        _native.dilate(_as_bytes(mask), width, height, int(iterations)), height, width
    )


def erode(mask, iterations):
    """8近傍で ``iterations`` 回収縮させる（範囲外は偽）。"""
    height, width = mask.shape
    return _as_mask(
        _native.erode(_as_bytes(mask), width, height, int(iterations)), height, width
    )


#: :func:`label_regions` で番号を付けない画素の値。
INVALID_CODE = 0xFFFFFFFF


def label_regions(codes):
    """同じ値で4近傍につながった画素に1からの番号を付ける。

    :param codes: uint32 の2次元配列。:data:`INVALID_CODE` の画素は番号 0。
    :returns: ``(番号の int32 配列, 領域の数)``。
    """
    height, width = codes.shape
    data, count = _native.label_regions(
        np.ascontiguousarray(codes, dtype=np.uint32).tobytes(), width, height
    )
    labels = np.frombuffer(data, dtype=np.uint32).reshape(height, width)
    return labels.astype(np.int32), int(count)
