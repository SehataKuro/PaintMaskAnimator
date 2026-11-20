"""レイヤーやセルの画素を保持する画像コンテナ。"""

from collections.abc import Iterable
from dataclasses import dataclass
from operator import index

import numpy as np
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColorSpace, QImage, QPainter

from . import imaging
from .display_image_cache import display_images, model_image_key
from .native import RgbaImage

_PREMULTIPLIED = QImage.Format.Format_ARGB32_Premultiplied
# 属性を持たない画像を Qt で作ったときの解像度（to_qimage の結果と同じ値）。
_DEFAULT_DOTS_PER_METER = QImage(1, 1, _PREMULTIPLIED).dotsPerMeterX()
# 直前に取り込んだ QImage（cacheKey・画素・属性）。同じ QImage の連続取り込みで画素を共有する。
_last_import = None


@dataclass(frozen=True)
class _ImageAttributes:
    color_space: QColorSpace
    device_pixel_ratio: float
    dots_per_meter_x: int
    dots_per_meter_y: int
    offset: QPoint
    text: tuple[tuple[str, str], ...]

    @classmethod
    def from_qimage(cls, image: QImage) -> "_ImageAttributes":
        return cls(
            image.colorSpace(), image.devicePixelRatio(),
            image.dotsPerMeterX(), image.dotsPerMeterY(), image.offset(),
            tuple((key, image.text(key)) for key in image.textKeys()),
        )

    def apply(self, image: QImage) -> None:
        if self.color_space.isValid():
            image.setColorSpace(self.color_space)
        image.setDevicePixelRatio(self.device_pixel_ratio)
        image.setDotsPerMeterX(self.dots_per_meter_x)
        image.setDotsPerMeterY(self.dots_per_meter_y)
        image.setOffset(self.offset)
        for key, value in self.text:
            image.setText(key, value)


def _straight_bytes(image: QImage) -> bytes:
    if image.isNull():
        return b""
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return bytes(rgba.constBits())


class PmaImage:
    """Qt で正規化したストレート RGBA を Rust に保持する。"""

    __slots__ = ("_image", "_attributes")

    def __init__(self, image: QImage):
        """Qt で乗算済み形式を経由した画素と画像属性を取り込む。

        Qt で画素を共有している同じ QImage を続けて取り込んだときは、Rust の画素も
        共有する（Qt の暗黙共有だった頃と同じく、同じ絵を多くのセルに入れても画素は1枚）。
        """
        global _last_import
        normalized = image.convertToFormat(_PREMULTIPLIED)
        if normalized.isNull():
            self._image = RgbaImage(0, 0, b"")
            self._attributes = None
            return
        key = normalized.cacheKey()
        attributes = _ImageAttributes.from_qimage(normalized)
        if _last_import is not None and _last_import[0] == key and _last_import[2] == attributes:
            self._image = _last_import[1].copy()
        else:
            self._image = RgbaImage(normalized.width(), normalized.height(), _straight_bytes(normalized))
            _last_import = (key, self._image.copy(), attributes)
        self._attributes = attributes

    @classmethod
    def blank(cls, width: int, height: int) -> "PmaImage":
        """指定寸法の透明画像を作る。"""
        result = cls.__new__(cls)
        width, height = index(width), index(height)
        if width <= 0 or height <= 0:
            width = height = 0
        result._image = RgbaImage.blank(width, height)
        result._attributes = None
        return result

    @property
    def width(self) -> int:
        """画像の幅を画素単位で返す。"""
        return self._image.width

    @property
    def height(self) -> int:
        """画像の高さを画素単位で返す。"""
        return self._image.height

    @property
    def generation(self) -> int:
        """画素の世代を返す。複製は書き込みまで同じ世代を共有する。"""
        return self._image.generation

    def copy(self) -> "PmaImage":
        """画素を暗黙共有し、書き込み時に分離する複製を返す。"""
        result = type(self).__new__(type(self))
        result._image = self._image.copy()
        result._attributes = self._attributes
        return result

    def __copy__(self) -> "PmaImage":
        """汎用の浅い複製でも、書き込み時に画素を分離できるようにする。"""
        return self.copy()

    def rgba(self) -> np.ndarray:
        """読み取り専用の RGBA 配列を (高さ, 幅, 4) の uint8 で返す。"""
        return np.frombuffer(self._image.rgba_bytes(), dtype=np.uint8).reshape(self.height, self.width, 4)

    def _qimage(self, pixels: bytes, width: int, height: int) -> QImage:
        if not width or not height:
            return QImage()
        straight = QImage(pixels, width, height, width * 4, QImage.Format.Format_RGBA8888)
        image = straight.convertToFormat(_PREMULTIPLIED)
        if self._attributes is not None:
            self._attributes.apply(image)
        return image

    def read_qimage(self, x: int, y: int, w: int, h: int) -> QImage:
        """乗算済み ARGB の独立した矩形を返す。画像外は透明で、空矩形は null。"""
        if w <= 0 or h <= 0 or not self.width:
            return QImage()
        if x >= 0 and y >= 0 and x + w <= self.width and y + h <= self.height:
            pixels = self._image.read_rect(x, y, w, h)
        else:
            pixels = self.read_rgba(x, y, w, h).tobytes()
        return self._qimage(pixels, w, h)

    def read_rgba(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        """変更可能なストレート RGBA uint8 (h, w, 4) の複製を返す。画像外は透明。"""
        if w <= 0 or h <= 0:
            return np.empty((max(0, h), max(0, w), 4), dtype=np.uint8)
        left, top = max(x, 0), max(y, 0)
        width, height = min(x + w, self.width) - left, min(y + h, self.height) - top
        if width == w and height == h:
            return np.frombuffer(self._image.read_rect(left, top, w, h), dtype=np.uint8).reshape(h, w, 4).copy()

        result = np.zeros((h, w, 4), dtype=np.uint8)
        if width > 0 and height > 0:
            pixels = self._image.read_rect(left, top, width, height)
            result[top - y:top - y + height, left - x:left - x + width] = (
                np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 4)
            )
        return result

    def write_qimage(self, patch: QImage, x: int, y: int) -> None:
        """画素座標 (x, y) から Source で置換する。画像外はクリップし、null は無視する。"""
        self._write_patch(patch, x, y)

    def _write_patch(self, patch: QImage, x: int, y: int) -> None:
        if patch.isNull():
            return
        left, top = max(x, 0), max(y, 0)
        width = min(x + patch.width(), self.width) - left
        height = min(y + patch.height(), self.height) - top
        if width <= 0 or height <= 0:
            return

        if patch.format() != _PREMULTIPLIED:
            # Qt の Source 描画による書き込み境界の丸めを維持する。
            source_rect = QRect(left - x, top - y, width, height)
            normalized = QImage(width, height, _PREMULTIPLIED)
            painter = QPainter(normalized)
            try:
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                painter.drawImage(normalized.rect(), patch, source_rect)
            finally:
                painter.end()
            patch = normalized
        elif width != patch.width() or height != patch.height():
            patch = patch.copy(left - x, top - y, width, height)
        global _last_import
        if _last_import is not None and _last_import[1].generation == self.generation:
            # 取り込みの共有を手放してから書き、画素全体の複製を起こさない。
            _last_import = None
        old_key = model_image_key(self)
        straight = _straight_bytes(patch)
        self._image.write_rect(left, top, width, height, straight)
        if old_key in display_images:
            # 表示用の画像を全体で作り直さず、書いた矩形だけ差し替える。
            rect = QImage(straight, width, height, width * 4, QImage.Format.Format_RGBA8888)
            display_images.replace_rect(old_key, model_image_key(self),
                                        rect.convertToFormat(_PREMULTIPLIED), left, top)

    def write_qimages(self, patches: Iterable[tuple[int, int, QImage]]) -> None:
        """(x, y, QImage) の列を順に置換する。"""
        for x, y, patch in patches:
            self._write_patch(patch, x, y)

    def write_rgba(self, rgba: np.ndarray, x: int = 0, y: int = 0) -> None:
        """RGBA を Qt の乗算済み形式に丸めて書く。範囲外は ValueError、範囲内の空領域は no-op。"""
        if (
            not isinstance(rgba, np.ndarray)
            or rgba.dtype != np.uint8
            or rgba.ndim != 3
            or rgba.shape[2] != 4
        ):
            raise ValueError("RGBA配列は (高さ, 幅, 4) の uint8 である必要があります。")
        try:
            x, y = index(x), index(y)
        except TypeError as exc:
            raise ValueError("座標は整数である必要があります。") from exc
        height, width = rgba.shape[:2]
        if x < 0 or y < 0 or x + width > self.width or y + height > self.height:
            raise ValueError("書き込み範囲が画像の外にはみ出しています。")
        if width == 0 or height == 0:
            return

        self.write_qimage(imaging.rgba_array_to_qimage(rgba), x, y)

    def _png_source(self) -> tuple[bytes | None, tuple[int, int]]:
        """PNG 保存に渡すストレート RGBA と解像度。テキストを持つ画像は (None, …) で Qt に回す。"""
        attributes = self._attributes
        if attributes is None:
            return self._image.rgba_bytes(), (_DEFAULT_DOTS_PER_METER, _DEFAULT_DOTS_PER_METER)
        if attributes.text:
            return None, (0, 0)
        return self._image.rgba_bytes(), (attributes.dots_per_meter_x, attributes.dots_per_meter_y)

    def to_qimage(self) -> QImage:
        """独立した乗算済み ARGB の QImage を返す。"""
        return self._qimage(self._image.rgba_bytes(), self.width, self.height)

    @classmethod
    def from_qimage(cls, image: QImage) -> "PmaImage":
        """QImage から画素と属性を複製する。"""
        return cls(image)


ImageInput = QImage | PmaImage | None


def image_key(image: QImage | PmaImage) -> tuple[str, int]:
    """Identify shared pixels without converting a model image to Qt."""
    if isinstance(image, PmaImage):
        return ("pma", image.generation)
    return ("qt", image.cacheKey())


def to_pma_image(image: QImage | PmaImage | None) -> PmaImage | None:
    """モデルへ渡された画像を格納形式に揃える。"""
    if image is None or isinstance(image, PmaImage):
        return image
    return PmaImage.from_qimage(image)


def is_transparent_image(image: QImage) -> bool:
    """同じ形式の透明画像と比較する。"""
    transparent = QImage(image.size(), image.format())
    transparent.fill(0)
    return image == transparent


# 7-2f の後に削除または見直す。
def as_qimage(image: QImage | PmaImage | None) -> QImage | None:
    """PmaImage を独立した QImage に変換する。QImage と None はそのまま返す。"""
    return image.to_qimage() if isinstance(image, PmaImage) else image


# 7-2f の後に削除または見直す。
def copy_image(image: QImage | PmaImage | None) -> QImage | PmaImage | None:
    """画素を暗黙共有する同じ型の複製を返す。None はそのまま返す。"""
    if image is None:
        return None
    return image.copy() if isinstance(image, PmaImage) else QImage(image)


# 7-2f の後に削除または見直す。
def snapshot_image(image: QImage | PmaImage | None) -> QImage | PmaImage | None:
    """画素を複製する。同じ型を返し、None はそのまま返す。"""
    if image is None:
        return None
    if isinstance(image, PmaImage):
        return PmaImage.from_qimage(image.read_qimage(0, 0, image.width, image.height))
    return image.copy()


# 7-2f の後に削除または見直す。
def image_rgba(image: QImage | PmaImage | None) -> np.ndarray | None:
    """従来と同じ、書き込み可能な RGBA 配列の複製を返す。"""
    if image is None:
        return None
    if isinstance(image, PmaImage):
        return image.rgba().copy()
    return imaging.qimage_rgba_array(image)
