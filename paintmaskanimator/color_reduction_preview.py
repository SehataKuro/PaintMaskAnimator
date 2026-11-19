"""減色プレビューの設定と、ウィジェットに依存しない画像処理。"""

import numpy as np

from . import color_ops
from .errors import OperationError
from .i18n import tr


def tone_curve_points_key(points):
    """制御点を丸め、減色プレビューのハッシュ可能なキーを返す。"""
    normalized = []
    for point in points:
        try:
            x, y = point[:2]
            normalized.append((round(float(x), 5), round(float(y), 5)))
        except (TypeError, ValueError, IndexError):
            continue
    if not normalized:
        normalized = [(0.0, 0.0), (1.0, 1.0)]
    return tuple(normalized)


def resolve_tone_preview_points(tone_points, points_key, log):
    """トーンキーの取得に失敗した場合はログを残して恒等曲線を返す。"""
    try:
        tone_key = points_key()
    except (TypeError, ValueError, IndexError, AttributeError) as exc:
        log.debug("tone_curve_points_key() failed, using identity: %s", exc)
        tone_points = [(0.0, 0.0), (1.0, 1.0)]
        tone_key = tuple(tone_points)
    return tone_points, tone_key


def resolve_color_preview_settings(
    mode, target, tone_key, opaque_background, background_rgb
):
    """キャッシュキーと、抽出モードに応じた背景設定を返す。"""
    extraction_mode = "line" if mode == "line" else "surface"
    cache_key = (extraction_mode, target, tone_key)
    if extraction_mode == "line":
        opaque_background = True
        background_rgb = (255, 255, 255)
    return cache_key, opaque_background, background_rgb


def tone_preview_background_mask(rgba, background_rgb):
    """元画像の RGB が指定背景色に近い画素のマスクを返す。"""
    if background_rgb is None:
        return None

    background = np.asarray(background_rgb, dtype=np.int16)
    difference = rgba[:, :, :3].astype(np.int16) - background
    return np.sum(
        difference.astype(np.int32) * difference.astype(np.int32),
        axis=2,
    ) <= (5 * 5 * 3)


def apply_tone_preview_lut(source_rgba, lut, background_mask, background_rgb):
    """RGB に LUT を適用し、背景色を復元した RGBA のコピーを返す。"""
    result = source_rgba.copy()
    result[:, :, :3] = lut[result[:, :, :3]]
    if background_mask is not None and background_rgb is not None:
        result[:, :, :3][background_mask] = np.asarray(background_rgb, dtype=np.uint8)
    return result


def prepare_color_preview_source(source_image, tone_points, background_rgb):
    """元画像にトーンカーブを適用し、結果を検証する。"""
    adjusted = color_ops.apply_tone_curve(
        source_image,
        tone_curve_points=tone_points,
        background_rgb=background_rgb,
    )
    if adjusted is None or adjusted.isNull():
        raise OperationError(tr("トーンカーブ適用後の画像を生成できませんでした。"))
    return adjusted


def reduce_color_preview(
    tone_adjusted_source,
    reference_images,
    target,
    alpha_threshold,
    tone_points,
    extraction_mode,
    opaque_background,
    background_rgb,
    report_progress,
):
    """補正済み元画像と参照画像からパレットを作り、減色画像とともに返す。"""
    base_palette = color_ops.build_color_reduction_palette(
        tone_adjusted_source,
        target,
        alpha_threshold=alpha_threshold,
        max_samples=131072,
        opaque_background=opaque_background,
        background_rgb=background_rgb,
        extraction_mode=extraction_mode,
        # 代表フレームにも同じトーンカーブをかけて手本にする。
        extra_images=[
            color_ops.apply_tone_curve(
                reference,
                tone_curve_points=tone_points,
                background_rgb=background_rgb,
            )
            for reference in reference_images
        ],
    )

    report_progress(
        2,
        (
            tr("線のアンチエイリアスを線色と白背景へ分けています")
            if extraction_mode == "line"
            else tr("色面の境界を2色へ分けています")
        ),
    )
    base_reduced = color_ops.apply_color_reduction_palette(
        tone_adjusted_source,
        base_palette,
        alpha_threshold=alpha_threshold,
        opaque_background=opaque_background,
        background_rgb=background_rgb,
        tone_curve_points=[(0.0, 0.0), (1.0, 1.0)],
        extraction_mode=extraction_mode,
    )
    if base_reduced is None or base_reduced.isNull():
        raise OperationError(tr("2値化画像を生成できませんでした。"))
    return base_palette, base_reduced
