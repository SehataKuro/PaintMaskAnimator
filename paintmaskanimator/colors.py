"""Pure color / palette / tone-curve helpers extracted from PaintCanvas.

Stateless numeric functions over colors and pixel samples (median-cut and
priority palettes, HSV features, tone-curve normalization, palette painting).
No widget or document state. PaintCanvas keeps delegating wrappers so its call
sites are unchanged.
"""
import math

import numpy as np
from PySide6.QtGui import QColor

from . import constants  # noqa: F401


def is_pseudo_transparent_color(color):
    color = QColor(color)
    return (
        color.red() == 255
        and color.green() == 255
        and color.blue() == 255
    )


def paint_rgb_palette(colors):
    """QColor／RGB列を重複のないuint8パレットへ変換する。"""
    normalized = []
    seen = set()
    for value in colors or ():
        try:
            if isinstance(value, QColor):
                rgb = (
                    int(value.red()),
                    int(value.green()),
                    int(value.blue()),
                )
            else:
                rgb = tuple(
                    max(0, min(255, int(channel)))
                    for channel in value[:3]
                )
        except (
            TypeError,
            ValueError,
            IndexError,
        ):
            continue
        if len(rgb) != 3 or rgb in seen:
            continue
        seen.add(rgb)
        normalized.append(rgb)

    if not normalized:
        return np.zeros((0, 3), dtype=np.uint8)
    return np.asarray(
        normalized,
        dtype=np.uint8,
    )


def local_color_variation(rgb):
    """上下左右との差が大きい境界画素を抽出する。"""
    source = rgb.astype(np.int16)
    variation = np.zeros(source.shape[:2], dtype=np.int16)

    if source.shape[0] > 1:
        vertical = np.max(
            np.abs(source[1:, :, :] - source[:-1, :, :]),
            axis=2,
        )
        variation[1:, :] = np.maximum(
            variation[1:, :], vertical
        )
        variation[:-1, :] = np.maximum(
            variation[:-1, :], vertical
        )

    if source.shape[1] > 1:
        horizontal = np.max(
            np.abs(source[:, 1:, :] - source[:, :-1, :]),
            axis=2,
        )
        variation[:, 1:] = np.maximum(
            variation[:, 1:], horizontal
        )
        variation[:, :-1] = np.maximum(
            variation[:, :-1], horizontal
        )

    return variation


def median_cut_palette_from_samples(
    sample,
    color_count,
):
    """頻度付きMedian-Cutで代表色を作る。"""
    color_count = max(1, int(color_count))
    sample = np.asarray(
        sample,
        dtype=np.uint8,
    ).reshape((-1, 3))
    if sample.size == 0:
        return np.zeros((0, 3), dtype=np.uint8)

    colors, counts = np.unique(
        sample,
        axis=0,
        return_counts=True,
    )
    if len(colors) <= color_count:
        return colors.astype(np.uint8, copy=True)

    colors_i32 = colors.astype(np.int32)
    counts_i64 = counts.astype(np.int64)
    boxes = [np.arange(len(colors_i32), dtype=np.int32)]

    while len(boxes) < color_count:
        best_box_index = -1
        best_score = -1
        best_axis = 0

        for box_index, indices in enumerate(boxes):
            if len(indices) <= 1:
                continue
            box_colors = colors_i32[indices]
            ranges = (
                box_colors.max(axis=0)
                - box_colors.min(axis=0)
            )
            axis = int(np.argmax(ranges))
            population = int(counts_i64[indices].sum())
            score = int(ranges[axis]) * max(1, population)
            if score > best_score:
                best_score = score
                best_box_index = box_index
                best_axis = axis

        if best_box_index < 0:
            break

        indices = boxes.pop(best_box_index)
        order = np.argsort(
            colors_i32[indices, best_axis],
            kind="stable",
        )
        ordered = indices[order]
        ordered_counts = counts_i64[ordered]
        cumulative = np.cumsum(ordered_counts)
        half = cumulative[-1] / 2.0
        split = int(np.searchsorted(cumulative, half)) + 1
        split = max(1, min(len(ordered) - 1, split))
        boxes.append(ordered[:split])
        boxes.append(ordered[split:])

    palette = []
    for indices in boxes:
        weights = counts_i64[indices].astype(np.float64)
        total = max(1.0, float(weights.sum()))
        averaged = (
            colors_i32[indices].astype(np.float64)
            * weights[:, None]
        ).sum(axis=0) / total
        palette.append(
            np.clip(
                np.rint(averaged),
                0,
                255,
            ).astype(np.uint8)
        )

    return np.asarray(palette, dtype=np.uint8)


def normalize_tone_curve_points(points):
    normalized = []
    try:
        iterable = list(points)
    except TypeError:
        iterable = []

    for point in iterable:
        try:
            x, y = point[:2]
            normalized.append((
                max(0.0, min(1.0, float(x))),
                max(0.0, min(1.0, float(y))),
            ))
        except (TypeError, ValueError, IndexError):
            continue

    normalized.extend([(0.0, 0.0), (1.0, 1.0)])
    normalized.sort(key=lambda value: value[0])
    merged = []
    for x, y in normalized:
        if merged and abs(x - merged[-1][0]) < 1e-5:
            merged[-1] = (x, y)
        else:
            merged.append((x, y))
    merged[0] = (0.0, 0.0)
    merged[-1] = (1.0, 1.0)
    return merged


def rgb_hsv_features(rgb):
    """近い色相を近いベクトルへ写像する。"""
    values = np.asarray(
        rgb,
        dtype=np.float32,
    ).reshape((-1, 3)) / 255.0
    maximum = values.max(axis=1)
    minimum = values.min(axis=1)
    delta = maximum - minimum

    saturation = np.divide(
        delta,
        np.maximum(maximum, 1e-6),
    )
    hue = np.zeros_like(maximum)
    chromatic = delta > 1e-6

    red_mask = chromatic & (
        values[:, 0] >= values[:, 1]
    ) & (
        values[:, 0] >= values[:, 2]
    )
    green_mask = chromatic & ~red_mask & (
        values[:, 1] >= values[:, 2]
    )
    blue_mask = chromatic & ~red_mask & ~green_mask

    hue[red_mask] = (
        (values[red_mask, 1] - values[red_mask, 2])
        / delta[red_mask]
    ) % 6.0
    hue[green_mask] = (
        (values[green_mask, 2] - values[green_mask, 0])
        / delta[green_mask]
    ) + 2.0
    hue[blue_mask] = (
        (values[blue_mask, 0] - values[blue_mask, 1])
        / delta[blue_mask]
    ) + 4.0
    hue = (hue / 6.0) % 1.0

    angle = hue * (2.0 * math.pi)
    hue_strength = np.power(saturation, 0.72)
    return np.column_stack([
        np.cos(angle) * hue_strength * 2.35,
        np.sin(angle) * hue_strength * 2.35,
        saturation * 0.45,
        maximum * 0.52,
    ]).astype(np.float32)


def priority_palette_colors_from_samples(
    sample,
    maximum,
):
    """少面積でも残したい黒・主要色相を実在色から選ぶ。"""
    sample = np.asarray(
        sample,
        dtype=np.uint8,
    ).reshape((-1, 3))
    maximum = max(0, int(maximum))
    if sample.size == 0 or maximum <= 0:
        return np.zeros((0, 3), dtype=np.uint8)

    colors, counts = np.unique(
        sample,
        axis=0,
        return_counts=True,
    )
    values = colors.astype(np.float32) / 255.0
    value = values.max(axis=1)
    minimum = values.min(axis=1)
    delta = value - minimum
    saturation = np.divide(
        delta,
        np.maximum(value, 1e-6),
    )

    hue = np.zeros_like(value)
    chromatic = delta > 1e-6
    red_mask = chromatic & (
        values[:, 0] >= values[:, 1]
    ) & (
        values[:, 0] >= values[:, 2]
    )
    green_mask = chromatic & ~red_mask & (
        values[:, 1] >= values[:, 2]
    )
    blue_mask = chromatic & ~red_mask & ~green_mask
    hue[red_mask] = (
        (values[red_mask, 1] - values[red_mask, 2])
        / delta[red_mask]
    ) % 6.0
    hue[green_mask] = (
        (values[green_mask, 2] - values[green_mask, 0])
        / delta[green_mask]
    ) + 2.0
    hue[blue_mask] = (
        (values[blue_mask, 0] - values[blue_mask, 1])
        / delta[blue_mask]
    ) + 4.0
    hue = (hue / 6.0) % 1.0

    minimum_support = max(
        2,
        int(math.ceil(len(sample) * 0.00005)),
    )
    chromatic_minimum_support = max(
        8,
        int(math.ceil(len(sample) * 0.001)),
    )
    chromatic_mask = (
        (saturation >= 0.38)
        & (value >= 0.18)
    )
    color_groups = [
        (np.asarray([0, 0, 0]), value <= 0.24),
        (np.asarray([255, 0, 0]), None),
        (np.asarray([0, 0, 255]), None),
        (np.asarray([0, 255, 0]), None),
        (np.asarray([255, 0, 255]), None),
        (np.asarray([255, 255, 0]), None),
    ]
    target_hues = (None, 0.0, 2.0 / 3.0, 1.0 / 3.0, 5.0 / 6.0, 1.0 / 6.0)
    candidates_by_group = []

    for group_index, (target_rgb, fixed_mask) in enumerate(color_groups):
        if fixed_mask is None:
            target_hue = target_hues[group_index]
            hue_distance = np.abs(hue - target_hue)
            hue_distance = np.minimum(
                hue_distance,
                1.0 - hue_distance,
            )
            hue_tolerance = (
                1.0 / 8.0
                if group_index == 3
                else 1.0 / 12.0
            )
            mask = chromatic_mask & (hue_distance <= hue_tolerance)
        else:
            mask = fixed_mask

        group_support = int(counts[mask].sum())
        required_support = (
            minimum_support
            if group_index == 0
            else chromatic_minimum_support
        )
        if group_support < required_support:
            continue
        candidates = colors[mask].astype(np.int32)
        candidate_counts = counts[mask].astype(np.float64)
        difference = candidates - target_rgb[None, :]
        distance = np.sum(difference * difference, axis=1)
        if group_index == 0:
            # 黒線は面積が小さくても、暗い有彩色ではなく実在する
            # 最暗色を選ぶ。頻度ボーナスで青へ寄るのを防ぐ。
            luminance = (
                candidates[:, 0] * 0.2126
                + candidates[:, 1] * 0.7152
                + candidates[:, 2] * 0.0722
            )
            chroma = candidates.max(axis=1) - candidates.min(axis=1)
            best = int(np.argmin(luminance + chroma * 0.20))
        else:
            frequency_bonus = 1.0 + 0.30 * np.log1p(candidate_counts)
            best = int(np.argmin(distance / frequency_bonus))
        # 固定された色相順で枠を使い切らず、画像内での支持画素数が
        # 多い主要色を優先する。低色数でも実在する緑が脱落しにくい。
        candidates_by_group.append((
            group_support,
            group_index,
            candidates[best].astype(np.uint8),
        ))

    candidates_by_group.sort(
        key=lambda item: (-item[0], item[1])
    )
    selected = [
        color for _support, _group, color
        in candidates_by_group[:maximum]
    ]

    return np.asarray(selected, dtype=np.uint8).reshape((-1, 3))
