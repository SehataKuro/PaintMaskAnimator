"""レイヤーやセルの画素を保持する画像コンテナ。"""

from operator import index

import numpy as np
from PySide6.QtGui import QImage

from . import imaging


class PmaImage:
    """段階7-1では Qt の暗黙共有を使って画像を保持する。"""

    __slots__ = ("_image",)

    def __init__(self, image: QImage):
        """内部形式に揃えた画像を保持する。"""
        self._image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    @property
    def width(self) -> int:
        """画像の幅を画素単位で返す。"""
        return self._image.width()

    @property
    def height(self) -> int:
        """画像の高さを画素単位で返す。"""
        return self._image.height()

    def copy(self) -> "PmaImage":
        """画素を暗黙共有し、書き込み時に分離する複製を返す。"""
        result = type(self).__new__(type(self))
        result._image = QImage(self._image)
        return result

    def rgba(self) -> np.ndarray:
        """読み取り専用の RGBA 配列を (高さ, 幅, 4) の uint8 で返す。"""
        rgba = imaging.qimage_rgba_array(self._image, readonly=True)
        rgba.setflags(write=False)
        return rgba

    def write_rgba(self, rgba: np.ndarray, x: int = 0, y: int = 0) -> None:
        """左上 (x, y) から RGBA を上書きする。範囲内の空領域は何もしない。"""
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

        patch = imaging.rgba_array_to_qimage(rgba)
        source = np.frombuffer(imaging.qimage_buffer(patch, const=True), dtype=np.uint8)
        source = source.reshape(height, patch.bytesPerLine())
        # bits() が共有中の画素を分離する。形式を揃えてから行単位で上書きする。
        destination = np.frombuffer(imaging.qimage_buffer(self._image), dtype=np.uint8)
        destination = destination.reshape(self.height, self._image.bytesPerLine())
        destination[y:y + height, x * 4:(x + width) * 4] = source[:, :width * 4]

    def to_qimage(self) -> QImage:
        """内部の QImage を返す。段階7-3では複製を返す仕様に変わる。"""
        return self._image

    @classmethod
    def from_qimage(cls, image: QImage) -> "PmaImage":
        """QImage から画像を作る。内部形式が同じなら画素を暗黙共有する。"""
        return cls(image)


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
def image_rgba(image: QImage | PmaImage | None) -> np.ndarray | None:
    """読み取り用途の RGBA 配列を返す。QImage は従来の変換を使う。"""
    if image is None:
        return None
    if isinstance(image, PmaImage):
        return image.rgba()
    return imaging.qimage_rgba_array(image)
