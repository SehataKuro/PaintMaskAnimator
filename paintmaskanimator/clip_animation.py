"""CLIP animation import using clipfile-rs through the native PyO3 bridge.

File/container, SQLite, animation curves and raster decoding belong to clipfile.
This module adapts decoded data to Qt and flattens cel layers for PMA.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QPainter

from . import native
from .i18n import tr
from .constants import (
    MAX_IMAGE_DIMENSION, MAX_PROJECT_DECODED_PIXELS, MAX_PROJECT_FRAMES,
    MAX_PROJECT_LAYER_CELLS, MAX_PROJECT_LAYERS, MAX_PROJECT_IMAGE_BYTES,
)
from .imaging import qimage_rgba_array, rgba_array_to_qimage
from .models import Frame, Layer


class ClipImportError(ValueError):
    """A safe, user-displayable failure while reading a .clip file."""


@dataclass(frozen=True)
class ClipCelKey:
    frame: int
    tag: str


@dataclass(frozen=True)
class ClipAnimationLayer:
    name: str
    keys: tuple[ClipCelKey, ...]
    cell_images: dict[str, QImage]


@dataclass(frozen=True)
class ClipAnimationDocument:
    source_name: str
    timeline_name: str
    width: int
    height: int
    fps: float
    start_frame: int
    end_frame: int
    layers: tuple[ClipAnimationLayer, ...]
    frames: tuple[Frame, ...]

    @property
    def folder_count(self) -> int:
        return len(self.layers)

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    def source_metadata(self) -> dict:
        return {
            "file_name": self.source_name,
            "timeline_name": self.timeline_name,
            "fps": float(self.fps),
            "start_frame": int(self.start_frame),
            "end_frame": int(self.end_frame),
        }


def _row_number(row: dict, name: str, default: int = 0) -> int:
    value = row[name] if name in row.keys() else default
    return default if value is None else int(value)


def _csp_color_byte(value: int | float | str | None) -> int:
    if value is None:
        return 0
    component = max(0, int(value))
    if component <= 255:
        return component
    return min(255, component >> 24)


def _single_channel_layer_color(layer: dict) -> tuple[int, int, int]:
    if _row_number(layer, "DrawColorEnable"):
        names = ("DrawColorMainRed", "DrawColorMainGreen", "DrawColorMainBlue")
    elif _row_number(layer, "LayerUsePaletteColor"):
        names = ("LayerPaletteRed", "LayerPaletteGreen", "LayerPaletteBlue")
    else:
        return (0, 0, 0)
    red, green, blue = (
        _csp_color_byte(layer[name] if name in layer.keys() else 0)
        for name in names
    )
    return (red, green, blue)


def _blank_canvas(width: int, height: int) -> QImage:
    image = QImage(
        width, height, QImage.Format.Format_ARGB32_Premultiplied
    )
    image.fill(QColor(0, 0, 0, 0))
    return image


def _native_raster(reader, mipmap_id: int, color=(0, 0, 0)):
    """Convert upstream decoded pixels to a detached QImage; no format parsing."""
    width, height, channels, alpha_only, default_white, data = reader.raster(mipmap_id)
    pixels = np.frombuffer(data, dtype=np.uint8).reshape(height, width, channels)
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    if channels == 4:
        rgba[:] = pixels
    elif alpha_only:
        rgba[..., :3] = color
        rgba[..., 3] = pixels[..., 0]
    else:
        rgba[..., :3] = pixels[..., :1]
        rgba[..., 3] = pixels[..., 1] if channels == 2 else 255
    return rgba_array_to_qimage(rgba), alpha_only, default_white


def _render_cached_layer(
    reader,
    layer: dict,
    canvas_width: int,
    canvas_height: int,
) -> QImage:
    """Place a leaf layer's rendered mipmap on the canvas, before opacity."""

    mipmap_id = layer["LayerRenderMipmap"]
    if not mipmap_id:
        raise ClipImportError(
            tr("セル「{LayerName}」の表示合成画像が.clip内にありません。").format(LayerName=layer['LayerName'])
        )
    raster, _alpha_only, _default_white = _native_raster(
        reader, mipmap_id, _single_channel_layer_color(layer)
    )
    result = _blank_canvas(canvas_width, canvas_height)
    x = _row_number(layer, "LayerOffsetX") + _row_number(
        layer, "LayerRenderOffscrOffsetX"
    )
    y = _row_number(layer, "LayerOffsetY") + _row_number(
        layer, "LayerRenderOffscrOffsetY"
    )
    painter = QPainter(result)
    try:
        painter.drawImage(QPoint(x, y), raster)
    finally:
        painter.end()
    return result


def _child_layer_rows(
    layers_by_id: dict[int, dict], layer: dict
) -> list[dict]:
    child_id = _row_number(layer, "LayerFirstChildIndex")
    children: list[dict] = []
    visited: set[int] = set()
    while child_id:
        if child_id in visited:
            raise ClipImportError(tr("セル内のレイヤー構造が循環しています。"))
        visited.add(child_id)
        child = layers_by_id.get(child_id)
        if child is None:
            raise ClipImportError(tr("セル内の子レイヤーが見つかりません。"))
        children.append(child)
        child_id = _row_number(child, "LayerNextIndex")
    return children


# CLIP STUDIO's ``LayerComposite`` values.  The numbering follows the PSD
# blend-mode table in ``dobrokot/clip_to_psd``.
_BLEND_NORMAL = 0
_BLEND_DARKEN = 1
_BLEND_MULTIPLY = 2
_BLEND_COLOR_BURN = 3
_BLEND_LINEAR_BURN = 4
_BLEND_SUBTRACT = 5
_BLEND_DARKER_COLOR = 6
_BLEND_LIGHTEN = 7
_BLEND_SCREEN = 8
_BLEND_COLOR_DODGE = 9
_BLEND_GLOW_DODGE = 10
_BLEND_ADD = 11
_BLEND_ADD_GLOW = 12
_BLEND_LIGHTER_COLOR = 13
_BLEND_OVERLAY = 14
_BLEND_SOFT_LIGHT = 15
_BLEND_HARD_LIGHT = 16
_BLEND_VIVID_LIGHT = 17
_BLEND_LINEAR_LIGHT = 18
_BLEND_PIN_LIGHT = 19
_BLEND_HARD_MIX = 20
_BLEND_DIFFERENCE = 21
_BLEND_EXCLUSION = 22
_BLEND_HUE = 23
_BLEND_SATURATION = 24
_BLEND_COLOR = 25
_BLEND_LUMINOSITY = 26
_BLEND_PASS_THROUGH = 30
_BLEND_DIVIDE = 36


def _safe_divide(numerator, denominator, fallback):
    with np.errstate(divide="ignore", invalid="ignore"):
        result = numerator / denominator
    return np.where(denominator > 0, result, fallback)


def _luminosity(color: np.ndarray) -> np.ndarray:
    return (
        color[..., 0:1] * 0.3 + color[..., 1:2] * 0.59 + color[..., 2:3] * 0.11
    )


def _set_luminosity(color: np.ndarray, luminosity: np.ndarray) -> np.ndarray:
    color = color + (luminosity - _luminosity(color))
    lum = _luminosity(color)
    low = color.min(axis=-1, keepdims=True)
    high = color.max(axis=-1, keepdims=True)
    color = np.where(
        low < 0, lum + (color - lum) * _safe_divide(lum, lum - low, 0.0), color
    )
    color = np.where(
        high > 1,
        lum + (color - lum) * _safe_divide(1 - lum, high - lum, 0.0),
        color,
    )
    return color


def _saturation(color: np.ndarray) -> np.ndarray:
    return color.max(axis=-1, keepdims=True) - color.min(axis=-1, keepdims=True)


def _set_saturation(color: np.ndarray, saturation: np.ndarray) -> np.ndarray:
    low = color.min(axis=-1, keepdims=True)
    return _safe_divide((color - low) * saturation, _saturation(color), 0.0)


def _color_burn(backdrop, source):
    return np.where(
        backdrop >= 1,
        1.0,
        1 - np.minimum(1.0, _safe_divide(1 - backdrop, source, np.inf)),
    )


def _color_dodge(backdrop, source):
    return np.where(
        backdrop <= 0,
        0.0,
        np.minimum(1.0, _safe_divide(backdrop, 1 - source, np.inf)),
    )


def _hard_light(backdrop, source):
    doubled = source * 2
    return np.where(
        source <= 0.5,
        backdrop * doubled,
        backdrop + (doubled - 1) - backdrop * (doubled - 1),
    )


def _soft_light(backdrop, source):
    darken = np.where(
        backdrop <= 0.25,
        ((16 * backdrop - 12) * backdrop + 4) * backdrop,
        np.sqrt(backdrop),
    )
    return np.where(
        source <= 0.5,
        backdrop - (1 - 2 * source) * backdrop * (1 - backdrop),
        backdrop + (2 * source - 1) * (darken - backdrop),
    )


def _blend_colors(mode: int, backdrop: np.ndarray, source: np.ndarray) -> np.ndarray:
    """Return B(Cb, Cs) for straight (non-premultiplied) RGB in 0..1."""

    if mode == _BLEND_DARKEN:
        return np.minimum(backdrop, source)
    if mode == _BLEND_MULTIPLY:
        return backdrop * source
    if mode == _BLEND_COLOR_BURN:
        return _color_burn(backdrop, source)
    if mode == _BLEND_LINEAR_BURN:
        return np.maximum(0.0, backdrop + source - 1)
    if mode == _BLEND_SUBTRACT:
        return np.maximum(0.0, backdrop - source)
    if mode == _BLEND_DARKER_COLOR:
        return np.where(
            _luminosity(source) < _luminosity(backdrop), source, backdrop
        )
    if mode == _BLEND_LIGHTEN:
        return np.maximum(backdrop, source)
    if mode == _BLEND_SCREEN:
        return backdrop + source - backdrop * source
    # Glow dodge and add (glow) also skip CLIP's transparency shape; only
    # their colour formula is reproduced here.
    if mode in (_BLEND_COLOR_DODGE, _BLEND_GLOW_DODGE):
        return _color_dodge(backdrop, source)
    if mode in (_BLEND_ADD, _BLEND_ADD_GLOW):
        return np.minimum(1.0, backdrop + source)
    if mode == _BLEND_LIGHTER_COLOR:
        return np.where(
            _luminosity(source) > _luminosity(backdrop), source, backdrop
        )
    if mode == _BLEND_OVERLAY:
        return _hard_light(source, backdrop)
    if mode == _BLEND_SOFT_LIGHT:
        return _soft_light(backdrop, source)
    if mode == _BLEND_HARD_LIGHT:
        return _hard_light(backdrop, source)
    if mode == _BLEND_VIVID_LIGHT:
        return np.where(
            source <= 0.5,
            _color_burn(backdrop, source * 2),
            _color_dodge(backdrop, source * 2 - 1),
        )
    if mode == _BLEND_LINEAR_LIGHT:
        return np.clip(backdrop + source * 2 - 1, 0.0, 1.0)
    if mode == _BLEND_PIN_LIGHT:
        return np.where(
            source <= 0.5,
            np.minimum(backdrop, source * 2),
            np.maximum(backdrop, source * 2 - 1),
        )
    if mode == _BLEND_HARD_MIX:
        return np.where(backdrop + source >= 1, 1.0, 0.0)
    if mode == _BLEND_DIFFERENCE:
        return np.abs(backdrop - source)
    if mode == _BLEND_EXCLUSION:
        return backdrop + source - 2 * backdrop * source
    if mode == _BLEND_HUE:
        return _set_luminosity(
            _set_saturation(source, _saturation(backdrop)),
            _luminosity(backdrop),
        )
    if mode == _BLEND_SATURATION:
        return _set_luminosity(
            _set_saturation(backdrop, _saturation(source)),
            _luminosity(backdrop),
        )
    if mode == _BLEND_COLOR:
        return _set_luminosity(source, _luminosity(backdrop))
    if mode == _BLEND_LUMINOSITY:
        return _set_luminosity(backdrop, _luminosity(source))
    if mode == _BLEND_DIVIDE:
        return np.where(
            source > 0,
            np.minimum(1.0, _safe_divide(backdrop, source, 1.0)),
            np.where(backdrop > 0, 1.0, 0.0),
        )
    # Normal, pass-through and unknown values fall back to plain "over".
    return source


def _composite(
    backdrop: np.ndarray,
    source: np.ndarray,
    mode: int,
    *,
    atop: bool = False,
) -> np.ndarray:
    """Blend premultiplied RGBA ``source`` onto ``backdrop``.

    Uses the W3C compositing model: ``B(Cb, Cs)`` replaces the source colour
    where both layers overlap.  ``atop`` keeps the backdrop's alpha, which is
    how a layer clipped to the layer below is drawn.
    """

    backdrop_alpha = backdrop[..., 3:4]
    source_alpha = source[..., 3:4]
    backdrop_color = backdrop[..., :3]
    source_color = source[..., :3]
    if mode == _BLEND_NORMAL or mode == _BLEND_PASS_THROUGH:
        mixed = source_color * backdrop_alpha
    else:
        straight_backdrop = _safe_divide(backdrop_color, backdrop_alpha, 0.0)
        straight_source = _safe_divide(source_color, source_alpha, 0.0)
        blended = np.nan_to_num(
            _blend_colors(mode, straight_backdrop, straight_source),
            nan=0.0,
            posinf=1.0,
            neginf=0.0,
        )
        mixed = source_alpha * backdrop_alpha * np.clip(blended, 0.0, 1.0)
    if atop:
        color = mixed + backdrop_color * (1 - source_alpha)
        alpha = backdrop_alpha
    else:
        color = (
            source_color * (1 - backdrop_alpha)
            + backdrop_color * (1 - source_alpha)
            + mixed
        )
        alpha = source_alpha + backdrop_alpha * (1 - source_alpha)
    result = np.empty_like(backdrop)
    result[..., :3] = np.clip(color, 0.0, alpha)
    result[..., 3:4] = alpha
    return result


def _image_to_premultiplied(image: QImage) -> np.ndarray:
    rgba = qimage_rgba_array(image).astype(np.float32) / 255.0
    rgba[..., :3] *= rgba[..., 3:4]
    return rgba


def _premultiplied_to_image(pixels: np.ndarray) -> QImage:
    alpha = pixels[..., 3:4]
    straight = np.empty_like(pixels)
    straight[..., :3] = _safe_divide(pixels[..., :3], alpha, 0.0)
    straight[..., 3:4] = alpha
    return rgba_array_to_qimage(
        np.clip(np.rint(straight * 255.0), 0, 255).astype(np.uint8)
    )


def _shift_pixels(pixels: np.ndarray, dx: int, dy: int) -> np.ndarray:
    if dx == 0 and dy == 0:
        return pixels
    height, width = pixels.shape[:2]
    result = np.zeros_like(pixels)
    if abs(dx) >= width or abs(dy) >= height:
        return result
    result[max(0, dy):height + min(0, dy), max(0, dx):width + min(0, dx)] = pixels[
        max(0, -dy):height + min(0, -dy), max(0, -dx):width + min(0, -dx)
    ]
    return result


class _CelRenderer:
    """Flatten one CLIP cel (a leaf layer or a folder tree) into one image.

    PMA keeps one flat image per cel, so folder structure, blend modes,
    clipping and layer masks are resolved here instead of being imported.
    """

    def __init__(
        self,
        reader,
        layers_by_id: dict[int, dict],
        width: int,
        height: int,
    ):
        self.reader = reader
        self.layers_by_id = layers_by_id
        self.width = width
        self.height = height

    def render(self, cel: dict) -> QImage:
        # The cel itself is drawn on an empty canvas, so its own blend mode
        # and clipping flag have nothing to act on. Its visibility only means
        # "the current frame" inside CLIP and is ignored as well.
        pixels = self._content(cel, frozenset()) * self._opacity(cel)
        return _premultiplied_to_image(pixels)

    def _blank(self) -> np.ndarray:
        return np.zeros((self.height, self.width, 4), dtype=np.float32)

    @staticmethod
    def _visible(layer: dict) -> bool:
        return bool(_row_number(layer, "LayerVisibility", 1) & 1)

    @staticmethod
    def _opacity(layer: dict) -> float:
        return max(0.0, min(1.0, _row_number(layer, "LayerOpacity", 256) / 256.0))

    @staticmethod
    def _is_folder(layer: dict) -> bool:
        return bool(
            _row_number(layer, "LayerFirstChildIndex")
            or _row_number(layer, "LayerFolder")
        )

    def _children(
        self, layer: dict, stack: frozenset[int]
    ) -> tuple[list[dict], frozenset[int]]:
        layer_id = _row_number(layer, "MainId")
        if layer_id in stack:
            raise ClipImportError(tr("セル内のレイヤー構造が循環しています。"))
        return _child_layer_rows(self.layers_by_id, layer), stack | {layer_id}

    def _content(self, layer: dict, stack: frozenset[int]) -> np.ndarray:
        """Pixels of one layer on its own, with its mask but before opacity."""

        if self._is_folder(layer):
            # CLIP STUDIO can omit a folder cache or leave one that represents
            # only part of the folder, so folders are always rebuilt from
            # their children.
            children, child_stack = self._children(layer, stack)
            pixels = _shift_pixels(
                self._composite_children(children, self._blank(), child_stack),
                _row_number(layer, "LayerOffsetX"),
                _row_number(layer, "LayerOffsetY"),
            )
        else:
            pixels = _image_to_premultiplied(
                _render_cached_layer(
                    self.reader, layer, self.width, self.height
                )
            )
        mask = self._mask(layer)
        if mask is not None:
            pixels *= mask[..., None]
        return pixels

    def _composite_children(
        self,
        children: list[dict],
        backdrop: np.ndarray,
        stack: frozenset[int],
    ) -> np.ndarray:
        # Children are listed top to bottom. A clipped layer belongs to the
        # nearest unclipped layer below it; that base and its clipped layers
        # are combined first and then blended with the base's mode/opacity.
        groups: list[tuple[dict, list[dict]]] = []
        for child in reversed(children):
            if _row_number(child, "LayerClip") and groups:
                groups[-1][1].append(child)
            else:
                groups.append((child, []))
        result = backdrop
        for base, clipped in groups:
            if not self._visible(base):
                continue
            mode = _row_number(base, "LayerComposite")
            if mode == _BLEND_PASS_THROUGH and not clipped and self._is_folder(base):
                result = self._pass_through(base, result, stack)
                continue
            group = self._content(base, stack)
            for layer in clipped:
                if not self._visible(layer):
                    continue
                group = _composite(
                    group,
                    self._content(layer, stack) * self._opacity(layer),
                    _row_number(layer, "LayerComposite"),
                    atop=True,
                )
            result = _composite(result, group * self._opacity(base), mode)
        return result

    def _pass_through(
        self, folder: dict, backdrop: np.ndarray, stack: frozenset[int]
    ) -> np.ndarray:
        """Draw a pass-through folder's children straight onto the backdrop."""

        children, child_stack = self._children(folder, stack)
        mixed = self._composite_children(children, backdrop.copy(), child_stack)
        weight = self._opacity(folder)
        mask = self._mask(folder)
        if mask is None and weight >= 1.0:
            return mixed
        amount = weight if mask is None else mask[..., None] * weight
        return backdrop + (mixed - backdrop) * amount

    def _mask(self, layer: dict) -> Optional[np.ndarray]:
        """Return the layer mask on the canvas (1 = visible), if enabled."""

        mipmap_id = _row_number(layer, "LayerLayerMaskMipmap")
        if not mipmap_id or not (_row_number(layer, "LayerVisibility", 1) & 2):
            return None
        image, alpha_only, default_white = _native_raster(self.reader, mipmap_id)
        rgba = qimage_rgba_array(image)
        channel = 3 if alpha_only else 0
        values = rgba[..., channel].astype(np.float32) / 255.0
        mask = np.full(
            (self.height, self.width),
            1.0 if default_white else 0.0,
            dtype=np.float32,
        )
        x = (
            _row_number(layer, "LayerMaskOffsetX")
            + _row_number(layer, "LayerOffsetX")
            + _row_number(layer, "LayerMaskOffscrOffsetX")
        )
        y = (
            _row_number(layer, "LayerMaskOffsetY")
            + _row_number(layer, "LayerOffsetY")
            + _row_number(layer, "LayerMaskOffscrOffsetY")
        )
        left, top = max(0, x), max(0, y)
        right = min(self.width, x + values.shape[1])
        bottom = min(self.height, y + values.shape[0])
        if left < right and top < bottom:
            mask[top:bottom, left:right] = values[
                top - y:bottom - y, left - x:right - x
            ]
        return mask


def _numeric_cell_name(name: str) -> Optional[int]:
    stripped = str(name).strip()
    if not stripped or not stripped.isdecimal():
        return None
    try:
        value = int(stripped)
    except ValueError:
        return None
    return value if 1 <= value <= MAX_PROJECT_FRAMES else None


def cell_sequence_numbers(layer: ClipAnimationLayer) -> dict[str, int]:
    """Return stable PMA sequence numbers for every CLIP cel in a folder."""

    result: dict[str, int] = {}
    used: set[int] = set()
    deferred: list[str] = []
    for tag in layer.cell_images:
        number = _numeric_cell_name(tag)
        if number is None or number in used:
            deferred.append(tag)
            continue
        result[tag] = number
        used.add(number)
    next_number = 1
    for tag in deferred:
        while next_number in used:
            next_number += 1
        if next_number > MAX_PROJECT_FRAMES:
            raise ClipImportError(tr("アニメーションセルの絵番号がPMAの上限外です。"))
        result[tag] = next_number
        used.add(next_number)
    return result


def _layer_runs(
    keys: tuple[ClipCelKey, ...],
    start_frame: int,
    end_frame: int,
) -> list[tuple[int, int, str]]:
    duration = end_frame - start_frame + 1
    active = ""
    events: dict[int, str] = {}
    for key in keys:
        if key.frame <= start_frame:
            active = key.tag
        elif key.frame <= end_frame:
            events[key.frame - start_frame] = key.tag
    events[0] = active
    runs: list[tuple[int, int, str]] = []
    current_start = 0
    current_tag = events[0]
    for position in sorted(value for value in events if value > 0):
        next_tag = events[position]
        if next_tag == current_tag:
            continue
        runs.append((current_start, position - current_start, current_tag))
        current_start = position
        current_tag = next_tag
    runs.append((current_start, duration - current_start, current_tag))
    return runs


def build_pma_frames(
    width: int,
    height: int,
    start_frame: int,
    end_frame: int,
    layers: tuple[ClipAnimationLayer, ...],
) -> tuple[Frame, ...]:
    """Convert parsed CLIP tracks into PMA keyframes plus exposure spans."""

    duration = end_frame - start_frame + 1
    if not (1 <= duration <= MAX_PROJECT_FRAMES):
        raise ClipImportError(tr("タイムラインのフレーム数がPMAの上限外です。"))
    if not (1 <= len(layers) <= MAX_PROJECT_LAYERS):
        raise ClipImportError(tr("アニメーションフォルダー数がPMAの上限外です。"))
    cell_count = duration * len(layers)
    if cell_count > MAX_PROJECT_LAYER_CELLS:
        raise ClipImportError(tr("タイムラインのレイヤーセル数がPMAの上限を超えています。"))
    if width * height * cell_count > MAX_PROJECT_DECODED_PIXELS:
        raise ClipImportError(tr("タイムライン展開後の画像サイズがPMAの上限を超えています。"))
    transparent = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    transparent.fill(QColor(0, 0, 0, 0))
    frames = [
        Frame([
            Layer(layer.name, QImage(transparent))
            for layer in layers
        ])
        for _ in range(duration)
    ]
    for layer_index, source_layer in enumerate(layers):
        sequence_numbers = cell_sequence_numbers(source_layer)
        for run_start, exposure, tag in _layer_runs(
            source_layer.keys, start_frame, end_frame
        ):
            target = frames[run_start].layers[layer_index]
            target.exposure = exposure
            if not tag:
                target.cell_name = None
                target.is_blank_key = True
                target.has_content = False
                continue
            image = source_layer.cell_images.get(tag)
            if image is None:
                raise ClipImportError(
                    tr("アニメーションフォルダー「{name}」のセル「{tag}」を画像化できません。").format(name=source_layer.name, tag=tag)
                )
            target.image = QImage(image)
            target.has_content = True
            target.is_blank_key = False
            target.sequence_number = sequence_numbers[tag]
            target.cell_name = (
                None if _numeric_cell_name(tag) == target.sequence_number else tag
            )
    return tuple(frames)


def _direct_children(
    layers_by_id: dict[int, dict], folder: dict
) -> dict[str, dict]:
    child_id = _row_number(folder, "LayerFirstChildIndex")
    result: dict[str, dict] = {}
    visited: set[int] = set()
    while child_id:
        if child_id in visited:
            raise ClipImportError(tr("アニメーションフォルダーのレイヤー構造が循環しています。"))
        visited.add(child_id)
        child = layers_by_id.get(child_id)
        if child is None:
            raise ClipImportError(tr("アニメーションフォルダーの子レイヤーがありません。"))
        name = str(child["LayerName"] or "")
        if name in result:
            raise ClipImportError(
                tr("アニメーションフォルダー内でセル名「{name}」が重複しています。").format(name=name)
            )
        result[name] = child
        child_id = _row_number(child, "LayerNextIndex")
    return result


def read_clip_animation(path: str | os.PathLike[str]) -> ClipAnimationDocument:
    """Read using clipfile-rs without modifying the source file."""
    if Path(path).suffix.lower() != ".clip":
        raise ClipImportError(tr("CLIP STUDIOの.clipファイルを指定してください。"))
    try:
        reader = native.clip_animation_reader(
            str(path), MAX_IMAGE_DIMENSION, MAX_PROJECT_IMAGE_BYTES
        )
        metadata = reader.metadata()
        width, height = (int(round(metadata[key])) for key in ("width", "height"))
        if not (1 <= width <= MAX_IMAGE_DIMENSION and 1 <= height <= MAX_IMAGE_DIMENSION):
            raise ClipImportError(tr(".clipのキャンバスサイズがPMAの上限外です。"))
        fps = metadata["fps"]
        start_frame = max(0, int(round(metadata["start_frame"])))
        end_frame = max(0, int(round(metadata["end_frame"])))
        layers_by_id = {row["MainId"]: row for row in metadata["layers"]}
        renderer = _CelRenderer(reader, layers_by_id, width, height)
        parsed_layers: list[ClipAnimationLayer] = []
        tracks = [
            (layers_by_id[layer_id], curves)
            for layer_id, curves in metadata["tracks"]
            if "AnimationFolder" not in layers_by_id[layer_id]
            or layers_by_id[layer_id]["AnimationFolder"]
        ]
        # Check allocation budgets before decoding cel images or building Qt frames.
        duration = end_frame - start_frame + 1
        if not 1 <= duration <= MAX_PROJECT_FRAMES:
            raise ClipImportError(tr("タイムラインのフレーム数がPMAの上限外です。"))
        if not 1 <= len(tracks) <= MAX_PROJECT_LAYERS:
            raise ClipImportError(tr("アニメーションフォルダー数がPMAの上限外です。"))
        cells = duration * len(tracks)
        if cells > MAX_PROJECT_LAYER_CELLS:
            raise ClipImportError(tr("タイムラインのレイヤーセル数がPMAの上限を超えています。"))
        if width * height * cells > MAX_PROJECT_DECODED_PIXELS:
            raise ClipImportError(tr("タイムライン展開後の画像サイズがPMAの上限を超えています。"))
        decoded_cels = 0
        for folder, candidates in tracks:
            children = _direct_children(layers_by_id, folder)
            decoded_cels += len(children)
            if (decoded_cels > MAX_PROJECT_LAYER_CELLS
                    or width * height * decoded_cels > MAX_PROJECT_DECODED_PIXELS):
                raise ClipImportError(tr("タイムライン展開後の画像サイズがPMAの上限を超えています。"))
            matching = [
                curve for curve in candidates
                if all(not tag or tag in children for _time, tag in curve)
            ]
            raw_keys = max(matching, key=len) if matching else (candidates[0] if candidates else [])
            # Negative keys are not carried forward to frame zero. Their
            # images remain available in the sequence-cell archive below.
            keys = tuple(
                ClipCelKey(frame, str(tag))
                for time_60hz, tag in raw_keys
                for frame in (int(round(time_60hz * fps / 60.0)),)
                if frame >= 0
            )
            required_tags = {
                key.tag
                for key in keys
                if key.tag and start_frame <= key.frame <= end_frame
            }
            missing_tags = required_tags.difference(children)
            if missing_tags:
                missing = sorted(missing_tags)[0]
                raise ClipImportError(
                    tr("アニメーションフォルダー「{LayerName}」にセル「{missing}」がありません。").format(LayerName=folder['LayerName'], missing=missing)
                )
            images: dict[str, QImage] = {}
            # Render every cel, including unused and negative-pre-roll-only
            # cels, so they survive as PMA sequence entries.
            for tag, child in children.items():
                images[tag] = renderer.render(child)
            parsed_layers.append(
                ClipAnimationLayer(
                    str(folder["LayerName"] or f"Animation {len(parsed_layers) + 1}"),
                    keys,
                    images,
                )
            )
        if not parsed_layers:
            raise ClipImportError(tr(".clip内にアニメーションフォルダーがありません。"))
        layer_tuple = tuple(parsed_layers)
        frames = build_pma_frames(width, height, start_frame, end_frame, layer_tuple)
        return ClipAnimationDocument(
            Path(path).name, metadata["timeline_name"], width, height, fps,
            start_frame, end_frame, layer_tuple, frames,
        )
    except (ValueError, OSError) as exc:
        if isinstance(exc, ClipImportError):
            raise
        raise ClipImportError(str(exc)) from exc


__all__ = [
    "ClipAnimationDocument", "ClipAnimationLayer", "ClipCelKey", "ClipImportError",
    "build_pma_frames", "cell_sequence_numbers", "read_clip_animation",
]
