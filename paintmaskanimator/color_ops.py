"""Color-reduction, tone-curve and alpha-analysis algorithms.

A self-contained algorithmic subsystem extracted from PaintCanvas: it depends
only on the pure helpers in colors.py / imaging.py and on each other, never on
widget state. PaintCanvas keeps delegating class/static-method wrappers so its
callers (and color_reduction.py) are unchanged.
"""
import math
import numpy as np
from PySide6.QtGui import QImage
from . import imaging
from . import colors as _colors  # noqa: F401


def _snap_overlay_to_exact_rgbs(
    overlay_rgba,
    active_mask,
    exact_colors,
):
    """境界の丸め誤差を、指定された正規RGBへ吸着する。"""
    rgba = np.asarray(
        overlay_rgba,
        dtype=np.uint8,
    ).copy()
    mask = np.asarray(
        active_mask,
        dtype=bool,
    )
    palette = _colors.paint_rgb_palette(
        exact_colors
    )
    if (
        palette.size == 0
        or not np.any(mask)
    ):
        return rgba

    if len(palette) == 1:
        rgba[mask, :3] = palette[0]
        return rgba

    # 図形など複数色を使う場合は、丸め誤差を含む画素を
    # 最も近い正規RGBへ割り当てる。
    samples = rgba[mask, :3].astype(np.int16)
    palette_values = palette.astype(np.int16)
    delta = (
        samples[:, None, :]
        - palette_values[None, :, :]
    )
    distance = np.sum(
        delta * delta,
        axis=2,
        dtype=np.int32,
    )
    nearest = np.argmin(
        distance,
        axis=1,
    )
    rgba[mask, :3] = palette[nearest]
    return rgba


def image_alpha_statistics(image):
    """透明・半透明・不透明画素数を返す。"""
    if image is None or image.isNull():
        return {
            "transparent": 0,
            "semi_transparent": 0,
            "opaque": 0,
        }
    rgba = imaging.qimage_rgba_array(image)
    if rgba.size == 0:
        return {
            "transparent": 0,
            "semi_transparent": 0,
            "opaque": 0,
        }
    alpha = rgba[:, :, 3]
    return {
        "transparent": int(np.count_nonzero(alpha == 0)),
        "semi_transparent": int(
            np.count_nonzero((alpha > 0) & (alpha < 255))
        ),
        "opaque": int(np.count_nonzero(alpha == 255)),
    }


def binarize_alpha_for_pixel_art(
    image,
    alpha_threshold=128,
):
    """半透明を完全透明／完全不透明へ二値化する。"""
    if image is None or image.isNull():
        return QImage()

    rgba = imaging.qimage_rgba_array(image)
    if rgba.size == 0:
        return image.copy()

    threshold = max(
        1,
        min(254, int(alpha_threshold)),
    )
    alpha = rgba[:, :, 3]
    keep = alpha >= threshold

    result = np.zeros_like(rgba)
    result_rgb = result[:, :, :3]
    source_rgb = rgba[:, :, :3]
    result_rgb[keep] = source_rgb[keep]
    result[:, :, 3][keep] = 255
    return imaging.rgba_array_to_qimage(result)


def opaque_rgb_color_count(image):
    """透明画素を除外したRGB色数を返す。"""
    if image is None or image.isNull():
        return 0
    rgba = imaging.qimage_rgba_array(image)
    if rgba.size == 0:
        return 0
    visible = rgba[:, :, 3] > 0
    if not np.any(visible):
        return 0
    rgb = rgba[:, :, :3][visible].astype(np.uint32)
    packed = (
        (rgb[:, 0] << 16)
        | (rgb[:, 1] << 8)
        | rgb[:, 2]
    )
    return int(np.unique(packed).size)


def detect_opaque_border_background(image):
    """外周につながる不透明な明色背景を検出する。"""
    if image is None or image.isNull():
        return None

    rgba = imaging.qimage_rgba_array(image)
    if rgba.size == 0:
        return None

    alpha = rgba[:, :, 3]
    # 半透明・透明を含む画像は従来のアルファ二値化を使う。
    if np.any(alpha < 250):
        return None

    rgb = rgba[:, :, :3]
    height, width = rgb.shape[:2]
    if width <= 0 or height <= 0:
        return None

    border_parts = [
        rgb[0, :, :],
        rgb[height - 1, :, :],
    ]
    if height > 2:
        border_parts.extend([
            rgb[1:height - 1, 0, :],
            rgb[1:height - 1, width - 1, :],
        ])
    border = np.concatenate(border_parts, axis=0)
    if border.size == 0:
        return None

    colors, counts = np.unique(
        border.astype(np.uint8),
        axis=0,
        return_counts=True,
    )
    dominant = colors[int(np.argmax(counts))].astype(np.int32)

    # 白～明るい無彩色背景だけを自動背景として扱う。
    if int(dominant.min()) < 218:
        return None
    if int(dominant.max() - dominant.min()) > 24:
        return None

    delta = border.astype(np.int32) - dominant[None, :]
    near = np.sum(delta * delta, axis=1) <= (26 * 26 * 3)
    if float(np.count_nonzero(near)) / max(1, len(border)) < 0.52:
        return None

    return tuple(int(value) for value in dominant)


def estimate_mixed_boundary_pixels(
    image,
    background_rgb=None,
):
    """白背景上のアンチエイリアス境界のおおよその画素数。"""
    if image is None or image.isNull():
        return 0

    rgba = imaging.qimage_rgba_array(image)
    if rgba.size == 0:
        return 0

    rgb = rgba[:, :, :3]
    alpha = rgba[:, :, 3]
    variation = _colors.local_color_variation(rgb)
    candidate = (alpha >= 250) & (variation >= 10)

    if background_rgb is not None:
        background = np.asarray(
            background_rgb,
            dtype=np.int16,
        )
        delta = rgb.astype(np.int16) - background
        distance = np.sqrt(
            np.sum(
                delta.astype(np.int32)
                * delta.astype(np.int32),
                axis=2,
            )
        )
        # 背景そのものと十分離れた内部色は除き、
        # 背景との混合が疑われる帯域を数える。
        candidate &= (distance > 5.0) & (distance < 190.0)

    return int(np.count_nonzero(candidate))


def _surface_guided_palette_labels(
    rgb,
    visible,
    palette,
    spatial_refinement=True,
):
    """色面内部を種にし、混合境界を隣接する2色へ分割する。"""
    height, width = visible.shape
    palette_array = np.asarray(
        palette,
        dtype=np.uint8,
    ).reshape((-1, 3))
    palette_features = _colors.rgb_hsv_features(
        palette_array
    )

    flat_rgb = rgb.reshape((-1, 3))
    flat_visible = visible.reshape(-1)
    nearest_labels = np.full(
        flat_visible.shape,
        -1,
        dtype=np.int16,
    )
    nearest_distance = np.full(
        flat_visible.shape,
        np.inf,
        dtype=np.float32,
    )

    visible_indices = np.flatnonzero(flat_visible)
    chunk_size = 8192
    for chunk_start in range(
        0, len(visible_indices), chunk_size
    ):
        indices = visible_indices[
            chunk_start:chunk_start + chunk_size
        ]
        pixel_features = _colors.rgb_hsv_features(
            flat_rgb[indices]
        )
        distance = np.sum(
            (
                pixel_features[:, None, :]
                - palette_features[None, :, :]
            ) ** 2,
            axis=2,
        )
        labels = np.argmin(distance, axis=1)
        nearest_labels[indices] = labels.astype(np.int16)
        nearest_distance[indices] = distance[
            np.arange(len(indices)),
            labels,
        ]

    nearest_labels = nearest_labels.reshape(
        (height, width)
    )
    nearest_distance = nearest_distance.reshape(
        (height, width)
    )
    if not spatial_refinement:
        return nearest_labels

    variation = _colors.local_color_variation(rgb)
    # 平坦な色面を確定し、境界の混合色は未確定にする。
    core = (
        visible
        & (variation <= 12)
        & (nearest_distance <= 0.12)
    )
    labels = np.full(
        (height, width),
        -1,
        dtype=np.int16,
    )
    labels[core] = nearest_labels[core]

    exactish = (
        visible
        & (labels < 0)
        & (nearest_distance <= 0.015)
    )
    labels[exactish] = nearest_labels[exactish]

    pixel_features_all = _colors.rgb_hsv_features(
        rgb.reshape((-1, 3))
    ).reshape((height, width, -1))

    # 隣接する色面候補だけを比較するため、
    # 2色の混合帯は中間位置で明確に二分される。
    for _iteration in range(12):
        unresolved = visible & (labels < 0)
        if not np.any(unresolved):
            break

        candidates = []
        up = np.full_like(labels, -1)
        up[1:, :] = labels[:-1, :]
        candidates.append(up)
        down = np.full_like(labels, -1)
        down[:-1, :] = labels[1:, :]
        candidates.append(down)
        left = np.full_like(labels, -1)
        left[:, 1:] = labels[:, :-1]
        candidates.append(left)
        right = np.full_like(labels, -1)
        right[:, :-1] = labels[:, 1:]
        candidates.append(right)

        best_label = np.full_like(labels, -1)
        best_distance = np.full(
            (height, width),
            np.inf,
            dtype=np.float32,
        )

        for candidate in candidates:
            valid = unresolved & (candidate >= 0)
            if not np.any(valid):
                continue
            candidate_features = palette_features[
                np.maximum(candidate, 0)
            ]
            distance = np.sum(
                (
                    pixel_features_all
                    - candidate_features
                ) ** 2,
                axis=2,
            )
            better = valid & (distance < best_distance)
            best_distance[better] = distance[better]
            best_label[better] = candidate[better]

        assign = unresolved & (best_label >= 0)
        if not np.any(assign):
            break
        labels[assign] = best_label[assign]

    unresolved = visible & (labels < 0)
    labels[unresolved] = nearest_labels[unresolved]
    return labels


def _line_palette_labels(rgb, visible, palette):
    """ライン抽出用。色相よりRGB差と明度差を優先して実線を保つ。"""
    palette_array = np.asarray(palette, dtype=np.uint8).reshape((-1, 3))
    source = rgb.reshape((-1, 3)).astype(np.float32) / 255.0
    targets = palette_array.astype(np.float32) / 255.0
    source_luma = (
        source[:, 0] * 0.2126
        + source[:, 1] * 0.7152
        + source[:, 2] * 0.0722
    )
    target_luma = (
        targets[:, 0] * 0.2126
        + targets[:, 1] * 0.7152
        + targets[:, 2] * 0.0722
    )
    source_value = source.max(axis=1)
    source_saturation = (
        (source_value - source.min(axis=1))
        / np.maximum(source_value, 1e-6)
    )
    target_value = targets.max(axis=1)
    target_saturation = (
        (target_value - targets.min(axis=1))
        / np.maximum(target_value, 1e-6)
    )
    flat_visible = visible.reshape(-1)
    labels = np.full(flat_visible.shape, -1, dtype=np.int16)
    indices = np.flatnonzero(flat_visible)
    for start in range(0, len(indices), 8192):
        chunk = indices[start:start + 8192]
        rgb_distance = np.sum(
            (source[chunk, None, :] - targets[None, :, :]) ** 2,
            axis=2,
        )
        luma_distance = (
            source_luma[chunk, None] - target_luma[None, :]
        ) ** 2
        saturation_excess = np.maximum(
            target_saturation[None, :]
            - source_saturation[chunk, None]
            - 0.12,
            0.0,
        )
        distance = (
            rgb_distance
            + luma_distance * 2.4
            + saturation_excess * saturation_excess * 4.0
        )
        labels[chunk] = np.argmin(distance, axis=1).astype(np.int16)

    # 暗い実線はアンチエイリアス周辺の色相へ吸収させない。
    darkest_index = int(np.argmin(target_luma))
    darkest_luma = float(target_luma[darkest_index])
    if darkest_luma <= 0.32:
        source_luma_2d = source_luma.reshape(visible.shape)
        variation = _colors.local_color_variation(rgb)
        strong_dark_line = visible & (
            (source_luma_2d <= max(0.20, darkest_luma + 0.12))
            & ((variation >= 28) | (source_luma_2d <= 0.10))
        )
        labels.reshape(visible.shape)[strong_dark_line] = darkest_index
    return labels.reshape(visible.shape)


def tone_curve_samples(
    tone_curve_points,
    input_values,
):
    """制御点を通る形状保持型3次曲線を評価する。"""
    points = _colors.normalize_tone_curve_points(
        tone_curve_points
        if tone_curve_points is not None
        else [(0.0, 0.0), (1.0, 1.0)]
    )
    xs = np.asarray(
        [point[0] for point in points],
        dtype=np.float64,
    )
    ys = np.asarray(
        [point[1] for point in points],
        dtype=np.float64,
    )
    values = np.asarray(
        input_values,
        dtype=np.float64,
    )

    if len(xs) <= 2:
        return np.clip(
            np.interp(values, xs, ys),
            0.0,
            1.0,
        )

    intervals = np.diff(xs)
    slopes = np.diff(ys) / np.maximum(
        intervals,
        1e-12,
    )
    tangents = np.zeros_like(xs)

    # 内部点はFritsch-Carlsonの重み付き調和平均。
    for index in range(1, len(xs) - 1):
        left_slope = slopes[index - 1]
        right_slope = slopes[index]
        if (
            left_slope == 0.0
            or right_slope == 0.0
            or left_slope * right_slope <= 0.0
        ):
            tangents[index] = 0.0
        else:
            left_weight = (
                2.0 * intervals[index]
                + intervals[index - 1]
            )
            right_weight = (
                intervals[index]
                + 2.0 * intervals[index - 1]
            )
            tangents[index] = (
                left_weight + right_weight
            ) / (
                left_weight / left_slope
                + right_weight / right_slope
            )

    def endpoint_tangent(
        first_interval,
        second_interval,
        first_slope,
        second_slope,
    ):
        tangent = (
            (2.0 * first_interval + second_interval)
            * first_slope
            - first_interval * second_slope
        ) / max(
            first_interval + second_interval,
            1e-12,
        )
        if tangent * first_slope <= 0.0:
            return 0.0
        if (
            first_slope * second_slope < 0.0
            and abs(tangent) > abs(3.0 * first_slope)
        ):
            return 3.0 * first_slope
        return tangent

    tangents[0] = endpoint_tangent(
        intervals[0],
        intervals[1],
        slopes[0],
        slopes[1],
    )
    tangents[-1] = endpoint_tangent(
        intervals[-1],
        intervals[-2],
        slopes[-1],
        slopes[-2],
    )

    segment_indices = np.searchsorted(
        xs,
        values,
        side="right",
    ) - 1
    segment_indices = np.clip(
        segment_indices,
        0,
        len(xs) - 2,
    )
    x0 = xs[segment_indices]
    x1 = xs[segment_indices + 1]
    interval = np.maximum(x1 - x0, 1e-12)
    t = np.clip(
        (values - x0) / interval,
        0.0,
        1.0,
    )
    t2 = t * t
    t3 = t2 * t

    y0 = ys[segment_indices]
    y1 = ys[segment_indices + 1]
    m0 = tangents[segment_indices]
    m1 = tangents[segment_indices + 1]

    outputs = (
        (2.0 * t3 - 3.0 * t2 + 1.0) * y0
        + (t3 - 2.0 * t2 + t) * interval * m0
        + (-2.0 * t3 + 3.0 * t2) * y1
        + (t3 - t2) * interval * m1
    )
    return np.clip(outputs, 0.0, 1.0)


def tone_curve_lut(tone_curve_points=None):
    inputs = (
        np.arange(256, dtype=np.float64) / 255.0
    )
    outputs = tone_curve_samples(
        tone_curve_points,
        inputs,
    )
    return np.clip(
        np.rint(outputs * 255.0),
        0,
        255,
    ).astype(np.uint8)


def apply_tone_curve(
    image,
    tone_curve_points=None,
    background_rgb=None,
):
    """256段階LUTだけで高速に複数点トーンカーブを適用する。"""
    if image is None or image.isNull():
        return image

    points = _colors.normalize_tone_curve_points(
        tone_curve_points
        if tone_curve_points is not None
        else [(0.0, 0.0), (1.0, 1.0)]
    )
    if points == [(0.0, 0.0), (1.0, 1.0)]:
        return image.copy()

    rgba = imaging.qimage_rgba_array(image)
    if rgba.size == 0:
        return image.copy()

    lut = tone_curve_lut(points)
    result = rgba.copy()
    result[:, :, :3] = lut[result[:, :, :3]]

    # 白背景の完全な白は端点固定でも変化しないが、
    # 自動検出した背景色も念のためそのまま保持する。
    if background_rgb is not None:
        background = np.asarray(
            background_rgb,
            dtype=np.int16,
        )
        difference = (
            rgba[:, :, :3].astype(np.int16)
            - background
        )
        near_background = np.sum(
            difference.astype(np.int32)
            * difference.astype(np.int32),
            axis=2,
        ) <= (5 * 5 * 3)
        result[:, :, :3][near_background] = (
            np.asarray(background_rgb, dtype=np.uint8)
        )

    return imaging.rgba_array_to_qimage(result)


def _hue_cluster_palette_from_samples(
    sample,
    color_count,
):
    """色相を主軸に、近い色を同じ原色面へまとめる。"""
    sample = np.asarray(
        sample,
        dtype=np.uint8,
    ).reshape((-1, 3))
    color_count = max(1, int(color_count))
    if sample.size == 0:
        return np.zeros((0, 3), dtype=np.uint8)

    colors, counts = np.unique(
        sample,
        axis=0,
        return_counts=True,
    )
    if len(colors) <= color_count:
        return colors.astype(np.uint8, copy=True)

    features = _colors.rgb_hsv_features(colors)
    weights = counts.astype(np.float64)

    first_index = int(np.argmax(weights))
    centers = [features[first_index].copy()]
    minimum_distance = np.sum(
        (features - centers[0]) ** 2,
        axis=1,
    )

    while len(centers) < color_count:
        score = minimum_distance * (
            1.0 + np.log1p(weights)
        )
        next_index = int(np.argmax(score))
        centers.append(features[next_index].copy())
        distance = np.sum(
            (features - centers[-1]) ** 2,
            axis=1,
        )
        minimum_distance = np.minimum(
            minimum_distance,
            distance,
        )

    centers = np.asarray(centers, dtype=np.float32)
    labels = np.zeros(len(colors), dtype=np.int32)

    for _iteration in range(12):
        distance = np.sum(
            (
                features[:, None, :]
                - centers[None, :, :]
            ) ** 2,
            axis=2,
        )
        new_labels = np.argmin(distance, axis=1)
        if np.array_equal(new_labels, labels) and _iteration > 0:
            break
        labels = new_labels

        for cluster_index in range(len(centers)):
            members = labels == cluster_index
            if not np.any(members):
                farthest = int(
                    np.argmax(np.min(distance, axis=1))
                )
                centers[cluster_index] = features[farthest]
                continue
            cluster_weights = weights[members]
            centers[cluster_index] = np.average(
                features[members],
                axis=0,
                weights=cluster_weights,
            )

    palette = []
    for cluster_index in range(len(centers)):
        members = labels == cluster_index
        if not np.any(members):
            continue
        member_indices = np.flatnonzero(members)
        cluster_weights = weights[members]
        rgb_center = np.average(
            colors[members].astype(np.float64),
            axis=0,
            weights=cluster_weights,
        )
        feature_distance = np.sum(
            (
                features[members]
                - centers[cluster_index][None, :]
            ) ** 2,
            axis=1,
        )
        rgb_distance = np.sum(
            (
                colors[members].astype(np.float64)
                - rgb_center[None, :]
            ) ** 2,
            axis=1,
        ) / (255.0 * 255.0 * 3.0)
        # RGB平均色を新しく作ると、異なる色相の中間色へ変化してしまう。
        # 色相クラスタ中心に近く、かつ画像内で支持の多い実在色を代表にする。
        frequency_bonus = 1.0 + 0.12 * np.log1p(cluster_weights)
        score = (
            feature_distance + rgb_distance * 0.08
        ) / frequency_bonus
        representative_index = member_indices[int(np.argmin(score))]
        palette.append(
            colors[representative_index].astype(np.uint8, copy=True)
        )

    return np.asarray(palette, dtype=np.uint8)


def build_color_reduction_palette(
    image,
    color_count,
    alpha_threshold=128,
    max_samples=262144,
    opaque_background=False,
    background_rgb=None,
    tone_curve_points=None,
    extraction_mode="surface",
):
    """近い色相を同じ原色面としてまとめる共通パレット。"""
    color_count = max(2, min(100, int(color_count)))
    threshold = max(1, min(254, int(alpha_threshold)))
    rgba = imaging.qimage_rgba_array(image)
    if rgba.size == 0:
        return np.asarray([[0, 0, 0]], dtype=np.uint8)

    rgb = rgba[:, :, :3]
    alpha = rgba[:, :, 3]
    visible = (
        np.ones(alpha.shape, dtype=bool)
        if opaque_background
        else alpha >= threshold
    )
    if not np.any(visible):
        return np.asarray([[0, 0, 0]], dtype=np.uint8)

    variation = _colors.local_color_variation(rgb)
    stable = visible & (variation <= 12)

    reserved_background = None
    foreground_mask = stable.copy()
    if opaque_background and background_rgb is not None:
        reserved_background = np.asarray(
            background_rgb,
            dtype=np.uint8,
        )
        difference = (
            rgb.astype(np.int16)
            - reserved_background.astype(np.int16)
        )
        background_distance = np.sum(
            difference.astype(np.int32)
            * difference.astype(np.int32),
            axis=2,
        )
        foreground_mask &= (
            background_distance > (18 * 18 * 3)
        )

    if np.count_nonzero(foreground_mask) < 32:
        foreground_mask = visible.copy()
        if reserved_background is not None:
            difference = (
                rgb.astype(np.int16)
                - reserved_background.astype(np.int16)
            )
            background_distance = np.sum(
                difference.astype(np.int32)
                * difference.astype(np.int32),
                axis=2,
            )
            foreground_mask &= (
                background_distance > (26 * 26 * 3)
            )

    training_rgb = rgb[foreground_mask]
    if training_rgb.size == 0:
        training_rgb = rgb[visible]

    if len(training_rgb) > int(max_samples):
        sample_indices = np.linspace(
            0,
            len(training_rgb) - 1,
            int(max_samples),
            dtype=np.int64,
        )
        training_rgb = training_rgb[sample_indices]

    foreground_count = (
        color_count - 1
        if reserved_background is not None
        else color_count
    )
    foreground_count = max(1, foreground_count)
    extraction_mode = (
        "line" if extraction_mode == "line" else "surface"
    )
    if extraction_mode == "line":
        priority_palette = _colors.priority_palette_colors_from_samples(
            training_rgb,
            foreground_count,
        )
    else:
        priority_palette = _colors.priority_palette_colors_from_samples(
            training_rgb,
            foreground_count if foreground_count >= 6 else 0,
        )
    clustered_palette = _hue_cluster_palette_from_samples(
        training_rgb,
        foreground_count,
    )
    foreground_colors = []
    for color in np.vstack([
        priority_palette,
        clustered_palette,
    ]):
        color_i32 = color.astype(np.int32)
        if extraction_mode == "line":
            color_value = float(color_i32.max())
            color_chroma = float(
                color_i32.max() - color_i32.min()
            )
            color_saturation = (
                color_chroma / max(1.0, color_value)
            )
            if color_saturation >= 0.25:
                differences = (
                    training_rgb.astype(np.int16)
                    - color_i32.astype(np.int16)
                )
                nearby = np.sum(
                    differences.astype(np.int32)
                    * differences.astype(np.int32),
                    axis=1,
                ) <= (55 * 55 * 3)
                required = max(
                    8,
                    int(math.ceil(len(training_rgb) * 0.001)),
                )
                if int(np.count_nonzero(nearby)) < required:
                    continue
        if any(
            np.sum(
                (color_i32 - existing.astype(np.int32)) ** 2
            ) <= (10 * 10 * 3)
            for existing in foreground_colors
        ):
            continue
        foreground_colors.append(color)
        if len(foreground_colors) >= foreground_count:
            break
    foreground_palette = np.asarray(
        foreground_colors,
        dtype=np.uint8,
    ).reshape((-1, 3))

    if reserved_background is None:
        palette = foreground_palette
    else:
        palette = np.vstack([
            reserved_background.reshape((1, 3)),
            foreground_palette,
        ])

    unique_palette = []
    seen = set()
    for color in palette:
        key = tuple(int(value) for value in color)
        if key in seen:
            continue
        seen.add(key)
        unique_palette.append(color)

    return np.asarray(
        unique_palette[:color_count],
        dtype=np.uint8,
    )


def apply_color_reduction_palette(
    image,
    palette,
    alpha_threshold=128,
    opaque_background=False,
    background_rgb=None,
    tone_curve_points=None,
    extraction_mode="surface",
):
    """色相面を確定し、境界混合色を二分してからトーンを適用する。"""
    if image is None or image.isNull():
        return image

    rgba = imaging.qimage_rgba_array(image)
    if rgba.size == 0:
        return image.copy()

    palette_array = np.asarray(
        palette,
        dtype=np.uint8,
    ).reshape((-1, 3))
    if palette_array.size == 0:
        return image.copy()

    threshold = max(1, min(254, int(alpha_threshold)))
    rgb = rgba[:, :, :3]
    visible = (
        np.ones(
            rgba.shape[:2],
            dtype=bool,
        )
        if opaque_background
        else rgba[:, :, 3] >= threshold
    )

    if extraction_mode == "line":
        labels = _line_palette_labels(
            rgb, visible, palette_array
        )
    else:
        labels = _surface_guided_palette_labels(
            rgb,
            visible,
            palette_array,
            spatial_refinement=True,
        )

    result = np.zeros_like(rgba)
    valid = visible & (labels >= 0)
    result[:, :, :3][valid] = palette_array[
        labels[valid]
    ]

    if opaque_background:
        if background_rgb is not None:
            background = np.asarray(
                background_rgb,
                dtype=np.uint8,
            )
            result[:, :, :3][~valid] = background
        result[:, :, 3] = 255
    else:
        result[:, :, 3][valid] = 255

    quantized = imaging.rgba_array_to_qimage(result)
    return apply_tone_curve(
        quantized,
        tone_curve_points=tone_curve_points,
        background_rgb=background_rgb,
    )

