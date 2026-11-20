"""レイヤーやセルの画素を保持する画像コンテナ。"""

from collections.abc import Iterable
from itertools import count
from operator import index

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter

from . import imaging
from .display_image_cache import display_images

_generations = count(1)


class PmaImage:
    """Qt の暗黙共有を使って画素を保持する。"""

    __slots__ = ("_image", "_generation")

    def __init__(self, image: QImage):
        """内部形式に揃えた画像を保持する。"""
        self._image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self._generation = next(_generations)

    @classmethod
    def blank(cls, width: int, height: int) -> "PmaImage":
        """指定寸法の透明画像を作る。"""
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        return cls(image)

    @property
    def width(self) -> int:
        """画像の幅を画素単位で返す。"""
        return self._image.width()

    @property
    def height(self) -> int:
        """画像の高さを画素単位で返す。"""
        return self._image.height()

    @property
    def generation(self) -> int:
        """画素の世代を返す。複製は書き込みまで同じ世代を共有する。"""
        return self._generation

    def copy(self) -> "PmaImage":
        """画素を暗黙共有し、書き込み時に分離する複製を返す。"""
        result = type(self).__new__(type(self))
        result._image = QImage(self._image)
        result._generation = self._generation
        return result

    # 7-2f の後に消すか見直す。legacy_v1 の画像ペイロードの複製に使う。
    def __copy__(self) -> "PmaImage":
        """汎用の浅い複製でも、書き込み時に画素を分離できるようにする。"""
        return self.copy()

    def rgba(self) -> np.ndarray:
        """読み取り専用の RGBA 配列を (高さ, 幅, 4) の uint8 で返す。"""
        rgba = imaging.qimage_rgba_array(self._image, readonly=True)
        rgba.setflags(write=False)
        return rgba

    def read_qimage(self, x: int, y: int, w: int, h: int) -> QImage:
        """乗算済み ARGB の独立した矩形を返す。画像外は透明で、空矩形は null。"""
        if w <= 0 or h <= 0:
            return QImage()
        return self._image.copy(x, y, w, h)

    def read_rgba(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        """変更可能なストレート RGBA uint8 (h, w, 4) の複製を返す。画像外は透明。"""
        if w <= 0 or h <= 0:
            return np.empty((max(0, h), max(0, w), 4), dtype=np.uint8)
        if self._image.isNull():
            return np.zeros((h, w, 4), dtype=np.uint8)
        return imaging.qimage_rgba_array(self.read_qimage(x, y, w, h))

    def write_qimage(self, patch: QImage, x: int, y: int) -> None:
        """画素座標 (x, y) から Source で置換する。画像外はクリップし、null は無視する。"""
        self.write_qimages(((x, y, patch),))

    def write_qimages(self, patches: Iterable[tuple[int, int, QImage]]) -> None:
        """(x, y, QImage) の列を順に置換する。Undo のタイルは一つの painter で処理する。"""
        painter = QPainter()
        width, height = self.width, self.height
        display_key = (self.generation, width, height)
        try:
            for x, y, patch in patches:
                if (
                    patch.isNull() or not width or not height
                    or x >= width or y >= height
                    or x + patch.width() <= 0 or y + patch.height() <= 0
                ):
                    continue
                if not painter.isActive():
                    # Drop the shared display handle before Qt makes the image writable.
                    display_images.forget(display_key)
                    if not painter.begin(self._image):
                        return
                    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                    scale = self._image.devicePixelRatio()
                    if scale != 1:
                        painter.scale(1 / scale, 1 / scale)
                if patch.devicePixelRatio() == 1:
                    painter.drawImage(x, y, patch)
                else:
                    painter.drawImage(QRect(x, y, patch.width(), patch.height()), patch, patch.rect())
        finally:
            if painter.isActive():
                painter.end()
                # The patch iterable may have looked up this generation during the batch.
                display_images.forget(display_key)
                self._generation = next(_generations)

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

    def to_qimage(self) -> QImage:
        """読み取り用の QImage を返す。画素更新は write_qimage / write_rgba を使う。"""
        return self._image

    @classmethod
    def from_qimage(cls, image: QImage) -> "PmaImage":
        """QImage から画像を作る。内部形式が同じなら画素を暗黙共有する。"""
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
    """内部の QImage を返す。QImage と None はそのまま返す。"""
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
    return imaging.qimage_rgba_array(as_qimage(image))
