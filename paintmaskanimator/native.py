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
    "RgbaImage", "TimelineCell", "resolve_timeline", "SheetEntry", "sheet_entries", "ClipAnimationReader", "GuideSampler",
    "tint_onion", "unique_colors", "filter_palette",
    "image_alpha_statistics", "binarize_alpha_for_pixel_art", "opaque_rgb_color_count",
    "border_background", "estimate_mixed_boundary_pixels", "snap_overlay_to_exact_rgbs",
    "local_color_variation", "median_cut_palette_from_samples", "normalize_tone_curve_points",
    "tone_curve_samples", "tone_curve_lut", "apply_tone_curve",
    "tone_preview_background_mask", "apply_tone_preview_lut",
    "palette_color_counts", "hue_cluster_palette", "palette_training_samples",
    "select_palette_colors", "line_palette_labels", "apply_color_reduction_palette",
    "line_flow_scores", "line_flow_interpolate", "connect_runs", "label_runs", "refine_face", "thin",
    "reference_color_stats", "reference_line_art", "reference_region_stats", "reference_region_under",
    "render_quad_labels", "render_mesh_labels", "splat_mask_labels", "mask_labels_to_rgba",
    "solve_local_shift", "prepare_local_shift",
    "tiled_part_color",
    "flow_outline_matches",
    "flow_part_color",
    "surface_guided_palette_labels", "map_known_colors", "region_neighbors",
    "history_trim_count", "history_shift_branch_positions", "history_branch_overflow",
    "history_switch_steps", "history_retained_positions", "history_branch_columns",
    "FillSession", "LineCache", "compute_fill_region_once", "fill_target_mask", "extend_line_ends", "flood_fill", "dilate", "erode", "label_regions", "remove_line_noise", "alpha_runs", "decode_png", "encode_png", "ZipReader", "ZipWriter", "decode_v2_plan",
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


RgbaImage = _native.RgbaImage


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
    # Rust が正本の文書の並び（document_live）なら、Rust の文書から一度で求める。
    resolve_sources = getattr(frames, "_resolve_sources", None)
    if resolve_sources is not None:
        result = resolve_sources()
        if result is not None:
            return result
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


def _reference_rgba(pixels):
    return (type(pixels) is np.ndarray and pixels.ndim == 3 and pixels.shape[2] == 4
            and pixels.dtype == np.uint8)


def reference_color_stats(pixels):
    """色ごとの画素数と外接矩形を返す。対応外の入力では None。"""
    if not _reference_rgba(pixels) or not np.little_endian:
        return None
    return _native.reference_color_stats(_rgba_bytes(pixels), pixels.shape[1])


def reference_line_art(pixels, line_max):
    """RGB がしきい値以下の不透明画素のマスクを返す。対応外の入力では None。"""
    if not _reference_rgba(pixels) or not 0 <= line_max <= 255:
        return None
    data = _native.reference_line_art(_rgba_bytes(pixels), line_max)
    return np.frombuffer(data, dtype=bool).reshape(pixels.shape[:2])


def reference_region_stats(runs, count, width, height):
    """ランから領域ごとの画素数、外接矩形、画像の端に接するかを返す。対応外の型では None。"""
    ys, starts, ends, labels = runs
    if any(array.dtype != np.int64 for array in (ys, starts, ends)) or labels.dtype != np.int32:
        return None
    return _native.reference_region_stats(
        tuple(array.tobytes() for array in runs), count, width, height,
    )


def reference_region_sampler(labels, lines):
    """矩形内のマスクで主線を除いた最多ラベルと割合を求める関数を返す。対応外の入力では None。"""
    if (type(labels) is not np.ndarray or labels.dtype != np.int32
            or type(lines) is not np.ndarray or lines.dtype != np.bool_
            or labels.ndim != 2 or labels.shape != lines.shape):
        return None
    # 変更できないバッファは、GIL を解放して各候補の票を数えるときに使い回せる。
    label_bytes, line_bytes = labels.tobytes(), lines.tobytes()
    shape = labels.shape[::-1]

    def sample(local, bounds):
        return _native.reference_region_under(label_bytes, line_bytes, shape, local.tobytes(), bounds)

    return sample


def reference_context_similarity(previous, previous_local, previous_bounds,
                                 current, current_local, current_bounds, radius):
    """外接矩形内のマスクを使い、二つの部分のまわりの色の並びの類似度を返す。"""
    def context(pixels, local, bounds):
        y, x = _tracking_center(local)
        x0, y0, x1, y1 = bounds
        cy, cx = y + y0, x + x0
        height, width = pixels.shape[:2]
        top, left = max(0, cy - radius), max(0, cx - radius)
        bottom, right = min(height, cy + radius + 1), min(width, cx + radius + 1)
        region = np.zeros((bottom - top, right - left), dtype=bool)
        sy, sx = max(top, y0), max(left, x0)
        ey, ex = min(bottom, y1), min(right, x1)
        region[sy - top:ey - top, sx - left:ex - left] = local[sy - y0:ey - y0, sx - x0:ex - x0]
        return _tracking_context(pixels[top:bottom, left:right], region,
                                 (cy - top, cx - left), radius)

    return _native.context_similarity(
        context(previous, previous_local, previous_bounds),
        context(current, current_local, current_bounds),
    )


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


def _tiled_window(bounds, shape, margin=0):
    """画像内に切り詰めた窓を、矩形と配列のスライスで返す。"""
    x0, y0, x1, y1 = bounds
    height, width = shape
    left, right = max(0, min(width, x0 - margin)), max(0, min(width, x1 + margin))
    top, bottom = max(0, min(height, y0 - margin)), max(0, min(height, y1 + margin))
    return (left, top, right - left, bottom - top), np.s_[top:bottom, left:right]


def tiled_part_color(target, filled, guide, tiles, paths, size):
    """採色に必要な区画窓をまとめて渡し、部位の RGB または None を返す。"""
    source = guide.initial
    wide = (source.wide_boundary, source.wide_primary)
    excluded = (source.boundary, guide.open, guide.undecided, guide.black, guide.primary)
    confirmed = guide.confirmed
    masks = (target.black, source.black, *wide, *excluded)
    if confirmed is not None:
        masks += (confirmed,)
    shape = target.black.shape
    values, labels = guide.values, guide.regions.labels
    if (values.dtype != np.int64 or labels.dtype != np.int32
            or values.shape != shape or labels.shape != shape
            or filled.local.dtype != np.bool_
            or any(mask.dtype != np.bool_ or mask.shape != shape for mask in masks)
            or (size < 4096 and guide.regions.sizes.dtype != np.int64)):
        return NotImplemented

    windows = []
    for (tx, ty, sub, oy, ox, bounds), path in zip(tiles, paths):
        if path is None:
            continue
        shift = tuple(map(int, path))
        if any(not -(1 << 31) <= delta < (1 << 31) for delta in shift):
            return NotImplemented
        dy, dx, _, _ = shift
        _, _, x1, y1 = bounds
        moved = tx + dx, ty + dy, x1 + dx, y1 + dy
        dest, roi = _tiled_window(moved, shape)
        if not dest[2] or not dest[3]:
            continue
        target_black, target_roi = _tiled_window(bounds, shape, 1)
        source_black, source_roi = _tiled_window(moved, shape, 1)
        outline = np.zeros(sub.shape, dtype=np.bool_)
        outline[oy, ox] = True
        windows.append((
            (tx, ty, x1 - tx, y1 - ty), sub.tobytes(), outline.tobytes(), dest, shift,
            (target_black, target.black[target_roi].tobytes()),
            (source_black, source.black[source_roi].tobytes()),
            tuple(mask[roi].tobytes() for mask in wide),
            tuple(mask[roi].tobytes() for mask in excluded),
            None if confirmed is None else confirmed[roi].tobytes(),
            values[roi].tobytes(), labels[roi].tobytes(),
        ))
    if not windows:
        return None
    x0, y0, x1, y1 = filled.bounds
    return _native.tiled_part_color(
        windows, ((x0, y0, x1 - x0, y1 - y0), filled.local.tobytes()), size,
        guide.regions.sizes.tobytes() if size < 4096 else b"",
    )


def has_flow_part_color():
    """変形場の採色を行う拡張の入口が利用できるかを返す。"""
    return (callable(getattr(_native, "flow_outline_matches", None))
            and callable(getattr(_native, "flow_part_color", None)))


def _flow_window(coordinates, shape, margin=0):
    """画像内の有効な往路座標の外接窓を返す。"""
    sy, sx, _by, _bx, consistent = coordinates
    height, width = shape
    inside = consistent & (sy >= 0) & (sy < height) & (sx >= 0) & (sx < width)
    if not inside.any():
        return _tiled_window((0, 0, 0, 0), shape)
    y, x = sy[inside], sx[inside]
    bounds = int(x.min()), int(y.min()), int(x.max()) + 1, int(y.max()) + 1
    return _tiled_window(bounds, shape, margin)


def _flow_coordinates(pair, ys, xs):
    """点順を保って補間し、入口が読める座標の dtype を確認する。"""
    arrays = pair.coordinates(ys, xs)
    if (len(arrays) != 5 or any(a.shape != ys.shape for a in arrays)
            or any(a.dtype != np.int64 for a in arrays[:4])
            or arrays[4].dtype != np.bool_):
        return None
    return arrays


def _flow_coordinate_bytes(coordinates):
    """往復の4座標と経路の有効マスクを行優先で渡す。"""
    return tuple(a.tobytes() for a in coordinates[:4]), coordinates[4].tobytes()


def flow_part_color(shape, target, filled, guide, pair):
    """輪郭照合を通った部位の内部を補間し、採用色の RGB を返す。"""
    from . import bucket_fill

    source = guide.initial
    wide = (source.wide_boundary, source.wide_primary)
    excluded = (source.boundary, guide.open, guide.undecided, guide.black, guide.primary)
    confirmed = guide.confirmed
    masks = (target.boundary, target.black, source.black, *wide, *excluded)
    if confirmed is not None:
        masks += (confirmed,)
    values = guide.values
    if (values.dtype != np.int64 or values.shape != shape or filled.local.dtype != np.bool_
            or any(mask.dtype != np.bool_ or mask.shape != shape for mask in masks)):
        return NotImplemented

    rect, roi = _tiled_window(filled.bounds, shape, 1)
    ax0, ay0, aw, ah = rect
    x0, y0, x1, y1 = filled.bounds
    local = np.zeros((ah, aw), dtype=bool)
    local[y0 - ay0:y1 - ay0, x0 - ax0:x1 - ax0] = filled.local
    dilated = bucket_fill.dilate(local, 1)
    # 補間する点を決めるため、この窓で target.boundary との積を取る。
    outline = dilated & ~local
    boundary = target.boundary[roi]
    oy, ox = np.nonzero(outline & boundary)
    if oy.size < 8:
        return None
    outline_xy = _flow_coordinates(pair, oy + ay0, ox + ax0)
    if outline_xy is None:
        return NotImplemented
    source_outline, outline_roi = _flow_window(outline_xy, shape)
    bx, by, bw, bh = source_outline
    black_rect, black_roi = source_outline, outline_roi
    if bw:
        black_rect, black_roi = _tiled_window((bx, by, bx + bw, by + bh), shape, 1)
    band = dilated & ~bucket_fill.erode(local, 2)
    part = (x0, y0, x1 - x0, y1 - y0)
    if not _native.flow_outline_matches(
        ((rect, outline.tobytes()), _flow_coordinate_bytes(outline_xy),
         (rect, band.tobytes()), (boundary.tobytes(), target.black[roi].tobytes()),
         source_outline, tuple(mask[outline_roi].tobytes() for mask in wide),
         (black_rect, source.black[black_roi].tobytes())),
        part,
    ):
        return None

    ys, xs = np.nonzero(filled.local)
    labels, sizes = b"", b""
    if ys.size < 4096:
        regions = guide.regions
        if (regions.labels.dtype != np.int32 or regions.labels.shape != shape
                or regions.sizes.dtype != np.int64 or regions.sizes.ndim != 1):
            return NotImplemented
        sizes = regions.sizes.tobytes()

    inside_xy = _flow_coordinates(pair, ys + y0, xs + x0)
    if inside_xy is None:
        return NotImplemented
    source_inside, inside_roi = _flow_window(inside_xy, shape)
    if ys.size < 4096:
        labels = regions.labels[inside_roi].tobytes()
    return _native.flow_part_color(
        ((part, filled.local.tobytes()),
         _flow_coordinate_bytes(inside_xy), source_inside,
         tuple(mask[inside_roi].tobytes() for mask in excluded),
         None if confirmed is None else confirmed[inside_roi].tobytes(),
         values[inside_roi].tobytes(), labels),
        sizes,
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


def splat_mask_labels(label_image, out_width, out_height, protect_labels, *, matrix=None, grid=None):
    """保護番号の順に、前向き写像が命中した画素の平坦な添字を返す。"""
    height, width = label_image.shape
    if matrix is not None:
        matrix = matrix.reshape(-1).tolist()
    if grid is not None:
        cols, rows, points = grid
        grid = cols, rows, [tuple(point) for point in points.tolist()]
    planes = _native.splat_mask_labels(
        label_image.tobytes(), width, height, out_width, out_height,
        protect_labels, matrix, grid,
    )
    return [np.frombuffer(plane, dtype=np.intp) for plane in planes]


def mask_labels_to_rgba(labels, palette, transparent_white):
    """uint16 のラベル画像と uint8 のパレットを、書き込み可能な RGBA に展開する。"""
    height, width = labels.shape
    data = _native.mask_labels_to_rgba(
        labels.tobytes(), width, height, palette.tobytes(), transparent_white,
    )
    return np.frombuffer(data, dtype=np.uint8).reshape(height, width, 4).copy()


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


def alpha_runs(data, width, height, stride, alpha_offset):
    """1画素4バイトの画像で、``alpha_offset`` 番目のバイトが 0 でない横の並び ``(y, left, right)`` の一覧。"""
    return _native.alpha_runs(bytes(data), int(width), int(height), int(stride), int(alpha_offset))


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

def prepare_local_shift(target: bytes, target_shape: tuple[int, int],
                        wide: bytes, wide_shape: tuple[int, int],
                        offset: tuple[int, int], limit: int):
    """間引き済みの行優先二値窓から相関寸法と探索インデックスを返す。"""
    return _native.prepare_local_shift(target, target_shape, wide, wide_shape, offset, limit)


def solve_local_shift(before: bytes, exact: bytes, wide: bytes, width: int, height: int,
                      fast_size: tuple[int, int], dys: list[int], dxs: list[int],
                      total: float, step: int, motion_penalty: float, near_line_weight: float,
                      share_motion: bool) -> tuple[tuple[int, int], float, float]:
    return _native.solve_local_shift(
        before, exact, wide, width, height, fast_size, dys, dxs, total, step,
        motion_penalty, near_line_weight, share_motion)


def history_trim_count(entry_bytes, max_entries: int, max_bytes: int) -> int:
    """Undo 列の古い側から捨てる件数（件数→論理バイトの順、最低1件は残す）。"""
    return _native.history_trim_count(entry_bytes, max_entries, max_bytes)


def history_shift_branch_positions(positions, removed: int) -> list:
    """古い側が ``removed`` 件削られたあとの分岐点。捨てる分岐は ``None``。"""
    return _native.history_shift_branch_positions(positions, removed)


def history_branch_overflow(count: int, max_branches: int) -> int:
    """分岐を足したあと、上限を超えて古い側から捨てる本数（上限 0 なら捨てない）。"""
    return _native.history_branch_overflow(count, max_branches)


def history_switch_steps(position: int, undo_len: int):
    """分岐へ切り替えるときに戻す手数。分岐点が今の長さを超える（到達できない）なら ``None``。"""
    return _native.history_switch_steps(position, undo_len)


def history_retained_positions(keep, positions) -> list:
    """一部の件を除いたあとの分岐点：分岐点より前に残る件の数（負の分岐点は末尾から数える）。"""
    return _native.history_retained_positions(keep, positions)


def history_branch_columns(branches) -> list:
    """ヒストリーパネルで分岐を置く列。``(分岐点, 件数)`` の並びから、並べた順に ``(添字, 列)``。"""
    return _native.history_branch_columns(branches)
