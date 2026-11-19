"""Required Rust backend for timeline state and image computations.

Missing or outdated extensions fail at startup with a rebuild instruction.
NumPy remains in use for array conversion and algorithms not yet moved to Rust.
"""
from __future__ import annotations

import numpy as np

from .logging_setup import get_logger

log = get_logger(__name__)

REQUIRED_API = (
    "enclosed_by", "enclosed_islands", "guess_target_categories", "skewer_seed",
    "track_fill_region", "estimate_shifts", "warp_region", "estimate_color_shifts", "tracking_region_center", "context_similarity",
    "TimelineCell", "resolve_timeline", "SheetEntry", "sheet_entries", "ClipAnimationReader", "GuideSampler",
    "tint_onion", "unique_colors", "filter_palette",
    "image_alpha_statistics", "binarize_alpha_for_pixel_art", "opaque_rgb_color_count",
    "border_background", "estimate_mixed_boundary_pixels", "snap_overlay_to_exact_rgbs",
    "local_color_variation", "median_cut_palette_from_samples", "normalize_tone_curve_points",
    "tone_curve_samples", "tone_curve_lut", "apply_tone_curve",
    "tone_preview_background_mask", "apply_tone_preview_lut",
    "palette_color_counts", "hue_cluster_palette", "palette_training_samples",
    "select_palette_colors", "line_palette_labels", "apply_color_reduction_palette",
    "line_flow_scores", "line_flow_interpolate", "connect_runs", "label_runs", "refine_face", "thin",
    "render_quad_labels", "render_mesh_labels", "solve_local_shift",
    "surface_guided_palette_labels", "map_known_colors", "region_neighbors",
    "FillSession", "LineCache", "compute_fill_region_once", "fill_target_mask", "extend_line_ends", "flood_fill", "dilate", "erode", "label_regions", "remove_line_noise", "decode_png", "encode_png", "ZipReader", "ZipWriter", "decode_v2_plan",
)
TIMELINE_API_VERSION = 2

REBUILD_MESSAGE = (
    "PaintMaskAnimator requires its Rust extension. "
    "Install or rebuild it from the project directory: python -m pip install ./native"
)


def validate_backend(module):
    version = getattr(module, "TIMELINE_API_VERSION", None)
    if version != TIMELINE_API_VERSION:
        raise ImportError(
            f"{REBUILD_MESSAGE} (timeline API: expected {TIMELINE_API_VERSION}, got {version!r})"
        )
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


# このプロセスで一度でも位置に _dense_import が付いたか。付けるのは
# timeline_metadata.set_dense_import だけで、外しても戻さない（一方向）。
# 立っていなければどの位置にも _dense_import は無い（写しの元も持っていない）。
_dense_import_seen = False


def note_dense_import():
    global _dense_import_seen
    _dense_import_seen = True


def resolve_timeline(frames):
    """Read Rust-owned cell metadata directly, without packing NumPy inputs."""
    if not _dense_import_seen:
        # どの位置にも _dense_import が無いので、下の行づくりはどの位置でも
        # メタデータをそのまま渡すのと同じになる（メタデータの無い位置を除く）。
        # 索引は全コマ × 全レイヤーを読むので、Phase 0 と同じ形で読む（#31 §7）。
        rows = [[layer.__dict__.get("_timeline_metadata") for layer in frame.layers]
                for frame in frames]
        try:
            data = _native.resolve_timeline(rows)
        except TypeError:
            # メタデータがまだ無い位置がある。下の従来の経路で作らせる。
            pass
        else:
            layers = max(map(len, rows), default=0)
            return np.frombuffer(data, dtype=np.int64).reshape(len(rows), layers)
    from .timeline_metadata import snapshot_metadata
    imported_columns = set()

    def imported(column, layer):
        imported_columns.add(column)
        return snapshot_metadata(layer, resolve=True)

    # Rust の resolve が見るのは has_content と（絵のある位置の）sequence_number だけ。
    # _dense_import の無い位置では、snapshot_metadata(resolve=True) は保留の位置でも
    # この2つを Layer のメタデータのまま写すので、メタデータをそのまま渡して同じ結果になる。
    # 索引は全コマ × 全レイヤーを読むので、セルごとの保留の照会（sequence_only）と
    # 2回目の走査を省く（#31 §7：Phase 0 より遅くしない）。
    # メタデータがまだ無い位置は、従来どおり snapshot_metadata に作らせる。
    rows = [[(values.get("_timeline_metadata") or snapshot_metadata(layer, resolve=True))
             if "_dense_import" not in (values := layer.__dict__)
             else imported(column, layer)
             for layer in frame.layers]
            for column, frame in enumerate(frames)]
    layers = max(map(len, rows), default=0)
    data = _native.resolve_timeline(rows)
    result = np.frombuffer(data, dtype=np.int64).reshape(len(rows), layers)
    # v2 の読込元の指定は _dense_import を持つ位置にしか効かない（下の条件）。
    overrides = [(column, li, layer) for column in sorted(imported_columns)
                 for li, layer in enumerate(frames[column].layers)
                 if "_v2_resolve_source" in layer.__dict__]
    if overrides:
        result = result.copy()
        for column, li, layer in overrides:
            signature, source, original_column, original_count = layer.__dict__["_v2_resolve_source"]
            if ("_dense_import" in layer.__dict__
                    and column == original_column and len(frames) == original_count
                    and signature == (layer.has_content, layer.is_blank_key, layer.sequence_number,
                                      layer.sequence_only, layer.blank_start)):
                result[column, li] = -1 if source is None else source
    return result


def sheet_entries(frames):
    """Rust が持つメタデータから、コマ × レイヤーの参照表を読み出す。"""
    from .timeline_metadata import snapshot_metadata
    rows = [[snapshot_metadata(layer) for layer in frame.layers]
            for frame in frames]
    return _native.sheet_entries(rows)


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


def _rgba_bytes(rgba):
    # qimage_rgba_array(readonly=True) already packed the pixels into bytes.
    base = rgba.base
    if isinstance(base, bytes) and rgba.flags.c_contiguous and len(base) == rgba.nbytes:
        return base
    return rgba.tobytes()


def image_alpha_statistics(rgba):
    height, width = rgba.shape[:2]
    return _native.image_alpha_statistics(_rgba_bytes(rgba), width, height)


def binarize_alpha_for_pixel_art(rgba, threshold):
    height, width = rgba.shape[:2]
    result = _native.binarize_alpha_for_pixel_art(_rgba_bytes(rgba), width, height, threshold)
    return np.frombuffer(result, dtype=np.uint8).reshape(rgba.shape)


def opaque_rgb_color_count(rgba):
    height, width = rgba.shape[:2]
    return _native.opaque_rgb_color_count(_rgba_bytes(rgba), width, height)


def border_background(border):
    return _native.border_background(border.tobytes())


def estimate_mixed_boundary_pixels(rgba, background):
    height, width = rgba.shape[:2]
    return _native.estimate_mixed_boundary_pixels(_rgba_bytes(rgba), width, height, background)


def snap_overlay_to_exact_rgbs(rgba, mask, palette):
    height, width = rgba.shape[:2]
    result = _native.snap_overlay_to_exact_rgbs(
        _rgba_bytes(rgba), width, height, mask.tobytes(), palette.tobytes(),
    )
    return np.frombuffer(result, dtype=np.uint8).reshape(rgba.shape).copy()


def local_color_variation(rgb):
    height, width = rgb.shape[:2]
    data = _native.local_color_variation(rgb.tobytes(), width, height)
    return np.frombuffer(data, dtype=np.uint8).astype(np.int16).reshape(height, width)


def median_cut_palette_from_samples(sample, color_count):
    data = _native.median_cut_palette_from_samples(sample.tobytes(), color_count)
    return np.frombuffer(data, dtype=np.uint8).copy().reshape(-1, 3)


def tone_curve_samples(points, values):
    data = _native.tone_curve_samples(points, values.tobytes())
    return None if data is None else np.frombuffer(data, dtype=np.float64).copy()


def tone_curve_lut(points):
    return np.frombuffer(_native.tone_curve_lut(points), dtype=np.uint8).copy()


def apply_tone_curve(rgba, points, background_rgb):
    height, width = rgba.shape[:2]
    data = _native.apply_tone_curve(_rgba_bytes(rgba), width, height, points, background_rgb)
    return np.ndarray(rgba.shape, dtype=np.uint8, buffer=data)


def tone_preview_background_mask(rgba, background_rgb):
    height, width = rgba.shape[:2]
    data = _native.tone_preview_background_mask(_rgba_bytes(rgba), width, height, background_rgb)
    return np.frombuffer(data, dtype=bool).reshape(height, width)


def apply_tone_preview_lut(rgba, lut, background_rgb, mask=None):
    height, width = rgba.shape[:2]
    data = _native.apply_tone_preview_lut(
        _rgba_bytes(rgba), width, height, lut.tobytes(), background_rgb,
        None if mask is None else _rgba_bytes(mask),
    )
    return np.ndarray(rgba.shape, dtype=np.uint8, buffer=data)


def normalize_tone_curve_points(points):
    return _native.normalize_tone_curve_points(points)


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


def line_flow_interpolate(dy, dx, block, ys, xs):
    """broadcast 済みの座標で縦横のずれを補間する。"""
    height, width = dy.shape
    # NumPy の int64 + 0.5 と同様に、まず int64 を f64 に丸める。
    result = _native.line_flow_interpolate(
        dy.astype("<f8", copy=False).tobytes(), dx.astype("<f8", copy=False).tobytes(),
        width, height, block,
        ys.astype("<f8", copy=False).tobytes(), xs.astype("<f8", copy=False).tobytes(),
    )
    if result is None:
        return None
    return tuple(np.frombuffer(data, dtype="<f8").astype(np.float64).reshape(ys.shape)
                 for data in result)


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


def render_quad_labels(label_image, matrix, out_width, out_height, subsamples, coverage_labels):
    """平面四角形の逆写像で番号の画像を変形する。

    :param label_image: uint16 の2次元配列。0 は「何もない」。
    :param matrix: 出力座標から元座標への 3x3（逆写像）。
    :param coverage_labels: 被覆（0-255）も返す番号の並び。
    :returns: ``(uint16 の番号, 番号 -> uint8 の被覆)``。
    """
    height, width = label_image.shape
    labels = np.ascontiguousarray(label_image, dtype=np.uint16)
    coverage_labels = [int(label) for label in coverage_labels]
    data, coverage = _native.render_quad_labels(
        labels.tobytes(), width, height,
        [float(value) for value in np.asarray(matrix, dtype=np.float64).reshape(9)],
        int(out_width), int(out_height), int(subsamples), coverage_labels,
    )
    result = np.frombuffer(data, dtype=np.uint16).reshape(out_height, out_width)
    return result.copy(), {
        label: np.frombuffer(plane, dtype=np.uint8).reshape(out_height, out_width).copy()
        for label, plane in zip(coverage_labels, coverage)
    }


def render_mesh_labels(
    label_image, points, dense_cols, dense_rows, out_width, out_height,
    subsamples, band_rows, coverage_labels,
):
    """曲がった格子（メッシュ変形）で番号の画像を変形する。

    :param points: 出力座標の格子点 ``(x, y)`` を行優先で並べたもの。
    :param band_rows: 一度に解く行数。使う記憶領域の上限になる。
    :returns: ``(uint16 の番号, 番号 -> uint8 の被覆)``。
    """
    height, width = label_image.shape
    labels = np.ascontiguousarray(label_image, dtype=np.uint16)
    coverage_labels = [int(label) for label in coverage_labels]
    grid = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    data, coverage = _native.render_mesh_labels(
        labels.tobytes(), width, height,
        [(float(x), float(y)) for x, y in grid],
        int(dense_cols), int(dense_rows), int(out_width), int(out_height),
        int(subsamples), int(band_rows), coverage_labels,
    )
    result = np.frombuffer(data, dtype=np.uint16).reshape(out_height, out_width)
    return result.copy(), {
        label: np.frombuffer(plane, dtype=np.uint8).reshape(out_height, out_width).copy()
        for label, plane in zip(coverage_labels, coverage)
    }


def surface_guided_palette_labels(rgb, visible, palette, spatial_refinement=True):
    """画素ごとに割り当てるパレットの番号を決める（色面を種に境界を二分する）。

    :param rgb: ``(高さ, 幅, 3)`` の uint8 配列。
    :param visible: ``(高さ, 幅)`` の真偽配列。偽の画素は -1 のまま。
    :param palette: ``(色数, 3)`` の uint8 配列。
    :returns: ``(高さ, 幅)`` の int16 配列。未確定は -1。
    """
    height, width = visible.shape
    pixels = np.ascontiguousarray(rgb, dtype=np.uint8).reshape(height, width, 3)
    colors = np.ascontiguousarray(palette, dtype=np.uint8).reshape(-1, 3)
    data = _native.surface_guided_palette_labels(
        pixels.tobytes(), width, height,
        np.ascontiguousarray(visible, dtype=bool).tobytes(), colors.tobytes(),
        bool(spatial_refinement),
    )
    return np.frombuffer(data, dtype=np.int16).reshape(height, width).copy()


def map_known_colors(pixels, palette, color_mapping, line_max):
    """隣接色分けの仮の色を、割り当て済みの対応表どおりに塗る。

    :param pixels: ``(高さ, 幅, 4)`` の RGBA8888 配列。
    :param color_mapping: 元の RGB をまとめた値から仮の色の番号への辞書。
    :returns: 塗った ``(高さ, 幅, 4)`` の配列。対応のない色があれば ``None``。
    """
    height, width = pixels.shape[:2]
    codes = sorted(int(code) for code in color_mapping)
    data = _native.map_known_colors(
        np.ascontiguousarray(pixels, dtype=np.uint8).tobytes(),
        codes, [int(color_mapping[code]) for code in codes],
        [(int(color[0]), int(color[1]), int(color[2])) for color in palette],
        int(line_max),
    )
    if data is None:
        return None
    # 読み取り専用のまま返す。呼び出し側が自分の配列へ書き写すので複製は要らない。
    return np.frombuffer(data, dtype=np.uint8).reshape(height, width, 4)


def region_neighbors(labels, gap):
    """``gap`` 画素以内で向かい合う領域の組 ``(a, b)``（``a < b``）の配列。"""
    height, width = labels.shape
    pairs = _native.region_neighbors(
        np.ascontiguousarray(labels, dtype=np.int32).tobytes(), width, height, int(gap)
    )
    return np.asarray(pairs, dtype=np.int64).reshape(-1, 2)


def remove_line_noise(rgba, line_luma, radius, majority, isolation):
    """線近傍の孤立色を除去し、書き換え可能な RGBA 配列と変更画素数を返す。"""
    pixels = np.ascontiguousarray(rgba, dtype=np.uint8)
    height, width = pixels.shape[:2]
    data, changed = _native.remove_line_noise(
        pixels.tobytes(), width, height, int(line_luma), max(0, int(radius)),
        int(majority), int(isolation),
    )
    if not changed:
        return pixels.copy(), 0
    return np.frombuffer(data, dtype=np.uint8).reshape(height, width, 4).copy(), changed


def decode_png(data, max_bytes):
    """PNG を幅・高さ・RGBA8888 にする。ValueError はそのまま呼び出し側へ渡す。"""
    return _native.decode_png(data, max_bytes)


def encode_png(rgba, width, height, dots_per_meter_x, dots_per_meter_y):
    """乗算していない RGBA8888 を PNG にする。失敗は ValueError。"""
    return _native.encode_png(rgba, width, height, dots_per_meter_x, dots_per_meter_y)


def ZipReader(path):
    """Rust で ZIP を開く。未対応・破損は ValueError、ファイル操作失敗は OSError。"""
    return _native.ZipReader(path)


def decode_v2_plan(payload, archive_names, limits=None):
    """v2 の構造計算と要求順の画像一覧を Rust から受け取る。"""
    return _native.decode_v2_plan(payload, archive_names, limits)


def ZipWriter(path):
    """ZIP writer を作る。"""
    return _native.ZipWriter(path)


def extend_line_ends(boundary, strips, max_length):
    """細線化済みの帯から端点を検出し、端までの延長マスクを返す。"""
    height, width = boundary.shape
    data = _native.extend_line_ends(
        _as_bytes(boundary), width, height,
        [(y0, y1, x0, x1, _as_bytes(skeleton))
         for y0, y1, x0, x1, skeleton in strips], int(max_length),
    )
    return _as_mask(data, height, width)


def fill_target_mask(pixels, target_color, category):
    """RGBA 配列から対象色の真偽マスクを返す。"""
    height, width = pixels.shape[:2]
    data = _native.fill_target_mask(
        np.ascontiguousarray(pixels, dtype=np.uint8).tobytes(), width, height, target_color,
        None if category is None else tuple(int(c) for c in category),
    )
    return _as_mask(data, height, width)


def _fill_options(options):
    replacements = options.island_replacements
    return (options.target_color, options.extend_lines, max(1, int(options.extend_length)),
            options.close_gap, max(1, int(options.gap_width)), options.adjacent,
            options.require_closed, max(0, int(options.island_max_width)),
            options.include_masks, options.include_background_mask, options.mask_colors,
            _replacements(replacements) if replacements else ())


LineCache = _native.LineCache


class FillSession:
    """Rust が画像と前処理を所有するセッション。"""
    def __init__(self, pixels, options, indexed=False):
        height, width = pixels.shape[:2]
        self.shape = (height, width)
        self._state = _native.FillSession(
            np.ascontiguousarray(pixels, dtype=np.uint8).tobytes(), width, height,
            _fill_options(options), indexed)

    def compute(self, x, y):
        return self._state.compute(x, y)

    def prepared(self, category):
        return tuple(None if m is None else np.frombuffer(m, dtype=np.uint8).view(bool).reshape(self.shape)
                     for m in self._state.prepared(category))

    def index(self, category):
        labels, boxes = self._state.index(category)
        return (np.frombuffer(labels, dtype=np.uint32).reshape(self.shape),
                np.frombuffer(boxes, dtype=np.uintp).reshape(-1, 4))

    def background(self):
        return np.frombuffer(self._state.background(), dtype=np.uint8).view(bool).reshape(self.shape)

    def categories(self):
        return [None if c is None else tuple(c) for c in self._state.categories()]

    def nbytes(self):
        return self._state.nbytes()


def compute_fill_region_once(pixels, x, y, options, selection=None, line_cache=None):
    """画像・選択範囲の前処理と領域決定を一度に行う。"""
    height, width = pixels.shape[:2]
    if line_cache is not None:
        if "_native_line_cache" not in line_cache:
            line_cache["_native_line_cache"] = LineCache()
        line_cache = line_cache["_native_line_cache"]
    return _native.compute_fill_region_once(
        np.ascontiguousarray(pixels, dtype=np.uint8).tobytes(), width, height, x, y,
        _fill_options(options), None if selection is None else _as_bytes(selection), line_cache)


def track_fill_region(pixels, previous, reference, options, selection=None,
                      previous_pixels=None, previous_lines=None):
    """画像と手がかりを渡し、採用した領域と一致した手がかりを受け取る。"""
    height, width = previous.shape
    return _native.track_fill_region(
        pixels.tobytes(), _as_bytes(previous), width, height,
        None if reference is None else tuple(int(c) for c in reference),
        _fill_options(options),
        None if selection is None else _as_bytes(selection),
        None if previous_pixels is None else previous_pixels.tobytes(),
        None if previous_lines is None else _as_bytes(previous_lines),
    )


def _replacements(replacements):
    return tuple((tuple(map(int, source[:3])), tuple(map(int, destination[:3])))
                 for source, destination in replacements)


def _expand_local(data, bounds, shape):
    out = np.zeros(shape, dtype=bool)
    x0, y0, x1, y1 = bounds
    out[y0:y1, x0:x1] = np.frombuffer(data, dtype=bool).reshape(y1 - y0, x1 - x0)
    return out


def fill_region_output(output, shape, *, include_island_bounds=True):
    """Rust の矩形内マスクを Python の全体マスクへ展開する。"""
    reason, bounds, data, category, local_islands = output
    region = None if data is None else _expand_local(data, bounds, shape)
    if not local_islands:
        return reason, region, bounds, category, ()
    islands = tuple((color, _expand_local(mask, rect, shape))
                    for color, rect, mask in local_islands)
    if include_island_bounds:
        for _, (x0, y0, x1, y1), _ in local_islands:
            bounds = (min(bounds[0], x0), min(bounds[1], y0),
                      max(bounds[2], x1), max(bounds[3], y1))
    return reason, region, bounds, category, islands


def enclosed_by(region, bounds):
    if bounds is None:
        return np.zeros_like(region)
    x0, y0, x1, y1 = bounds
    return _expand_local(
        _native.enclosed_by(_as_bytes(region[y0:y1, x0:x1]), x1-x0, y1-y0),
        bounds, region.shape,
    )


def enclosed_islands(pixels, region, bounds, replacements, selection, min_share):
    x0, y0, x1, y1 = bounds
    h, w = region.shape
    left, top = max(0, x0 - 1), max(0, y0 - 1)
    right, bottom = min(w, x1 + 1), min(h, y1 + 1)
    window = np.s_[top:bottom, left:right]
    output = _native.enclosed_islands(
        pixels[window].tobytes(), (right - left, bottom - top),
        _as_bytes(region[y0:y1, x0:x1]), (x0-left, y0-top, x1-left, y1-top),
        _replacements(replacements), float(min_share),
        None if selection is None else _as_bytes(selection[window]),
    )
    return tuple((color, _expand_local(mask, (a+left, b+top, c+left, d+top), region.shape))
                 for color, (a, b, c, d), mask in output)


def guess_target_categories(pixels, previous, previous_pixels, bounds, radius):
    h, w = previous.shape
    x0, y0, x1, y1 = bounds
    extent = max(x1 - x0, y1 - y0)
    margin = max(64, 3 * extent)
    # 色相関のピークは部分的な重なりも候補にする。移動後に参照し得る画素まで含める。
    reach = 2 * margin + 2 * extent + 3 * radius
    window = np.s_[max(0, y0-reach):min(h, y1+reach), max(0, x0-reach):min(w, x1+reach)]
    template = np.s_[max(0, y0-radius):min(h, y1+radius), max(0, x0-radius):min(w, x1+radius)]
    local = previous[window]
    height, width = local.shape
    return [None if rgb is None else tuple(rgb) for rgb in _native.guess_target_categories(
        pixels[window].tobytes(), _as_bytes(local), previous_pixels[template].tobytes(), width, height,
    )]


def skewer_seed(pixels, reference):
    height, width = pixels.shape[:2]
    if not width:
        return None
    # 行優先で順に調べる。参照色が無いときも全画像の bytes を確保せずに済む。
    rows = max(1, (256 * 1024) // (width * 4))
    for top in range(0, height, rows):
        block = pixels[top:top + rows]
        seed = _native.skewer_seed(block.tobytes(), width, len(block), reference)
        if seed is not None:
            x, y = seed
            return x, y + top
    return None


def estimate_shifts(previous, current, region, count, margin):
    height, width = region.shape
    return _native.estimate_shifts(
        _as_bytes(previous), _as_bytes(current), _as_bytes(region),
        width, height, max(0, int(count)), None if margin is None else int(margin),
    )


def warp_region(previous, current, region, shift, origin):
    height, width = region.shape
    data = _native.warp_region(
        _as_bytes(previous), _as_bytes(current), _as_bytes(region),
        width, height, shift, origin,
    )
    return None if data is None else np.frombuffer(data, dtype=bool).reshape(height, width)


def estimate_color_shifts(previous, current, region, bounds, count, radius, margin):
    height, width = region.shape
    x0, y0, x1, y1 = bounds
    if margin is None:
        margin = max(64, max(x1 - x0, y1 - y0) * 3)
    tx0, ty0 = max(0, x0 - radius), max(0, y0 - radius)
    tx1, ty1 = min(width, x1 + radius), min(height, y1 + radius)
    wx0, wy0 = max(0, tx0 - margin), max(0, ty0 - margin)
    wx1, wy1 = min(width, tx1 + margin), min(height, ty1 + margin)
    before = previous[ty0:ty1, tx0:tx1]
    after = current[wy0:wy1, wx0:wx1]
    return _native.estimate_color_shifts(
        before.tobytes(), after.tobytes(), _as_bytes(region[ty0:ty1, tx0:tx1]),
        (before.shape[1], before.shape[0]), (after.shape[1], after.shape[0]),
        (wy0 - ty0, wx0 - tx0), int(count),
    )


def _tracking_center(region):
    height, width = region.shape
    return _native.tracking_region_center(_as_bytes(region), width, height)


def _tracking_context(pixels, region, center, radius):
    height, width = region.shape
    cy, cx = center
    y0, x0 = max(0, cy - radius), max(0, cx - radius)
    y1, x1 = min(height, cy + radius + 1), min(width, cx + radius + 1)
    return (
        pixels[y0:y1, x0:x1].tobytes(), _as_bytes(region[y0:y1, x0:x1]),
        (x1 - x0, y1 - y0), (y0 - cy, x0 - cx),
    )


def context_similarity(previous, previous_region, current, current_region, radius):
    before_center = _tracking_center(previous_region)
    if before_center is None:
        return 0.0
    after_center = _tracking_center(current_region)
    if after_center is None:
        return 0.0
    if radius < 0:
        raise ValueError("negative dimensions are not allowed")
    return _native.context_similarity(
        _tracking_context(previous, previous_region, before_center, radius),
        _tracking_context(current, current_region, after_center, radius),
    )


def palette_color_counts(sample):
    colors, weights = _native.palette_color_counts(_rgba_bytes(sample))
    return np.frombuffer(colors, dtype=np.uint8).reshape(-1, 3), np.frombuffer(weights, dtype=np.float64)


def hue_cluster_palette(colors, features, weights, log_weights, color_count):
    data = _native.hue_cluster_palette(
        _rgba_bytes(colors), features.tobytes(), _rgba_bytes(weights), log_weights.tobytes(), color_count,
    )
    return np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).copy()


def palette_training_samples(rgba, threshold, opaque, background):
    height, width = rgba.shape[:2]
    data = _native.palette_training_samples(_rgba_bytes(rgba), width, height, threshold, opaque, background)
    return np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).copy()


def select_palette_colors(training, priority, clustered, color_count, line, background):
    data = _native.select_palette_colors(
        _rgba_bytes(training), _rgba_bytes(priority), _rgba_bytes(clustered), color_count, line, background,
    )
    colors = np.frombuffer(data, dtype=np.uint8).copy()
    return colors.reshape(-1, 3) if colors.size else colors


def line_palette_labels(rgb, visible, palette):
    height, width = visible.shape
    data = _native.line_palette_labels(
        _rgba_bytes(rgb), width, height, _rgba_bytes(visible), _rgba_bytes(palette),
    )
    return np.frombuffer(data, dtype=np.int16).reshape(height, width).copy()


def apply_color_reduction_palette(rgba, palette, threshold, opaque, background, points, line):
    height, width = rgba.shape[:2]
    data = _native.apply_color_reduction_palette(
        _rgba_bytes(rgba), width, height, _rgba_bytes(palette), threshold, opaque, background, points, line,
    )
    return np.ndarray(rgba.shape, dtype=np.uint8, buffer=data)

def solve_local_shift(before: bytes, exact: bytes, wide: bytes, width: int, height: int,
                      fast_size: tuple[int, int], dys: list[int], dxs: list[int],
                      total: float, step: int, motion_penalty: float, near_line_weight: float,
                      share_motion: bool) -> tuple[tuple[int, int], float, float]:
    return _native.solve_local_shift(
        before, exact, wide, width, height, fast_size, dys, dxs, total, step,
        motion_penalty, near_line_weight, share_motion)
