"""画像コンテナの暗黙共有と RGBA 変換を検証する。"""

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage, QPainter

from paintmaskanimator import imaging
from paintmaskanimator.pma_image import PmaImage, as_qimage, copy_image, image_rgba


def _blank(width, height):
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    return PmaImage.from_qimage(image)


def test_copy_shares_pixels_until_write():
    original = _blank(3, 2)
    duplicate = original.copy()

    assert (original.width, original.height) == (3, 2)
    assert duplicate.to_qimage() is not original.to_qimage()
    assert duplicate.to_qimage().cacheKey() == original.to_qimage().cacheKey()

    duplicate.write_rgba(np.array([[[255, 0, 0, 255]]], dtype=np.uint8), x=2, y=1)

    assert duplicate.to_qimage().cacheKey() != original.to_qimage().cacheKey()
    assert not original.rgba().any()
    assert duplicate.rgba()[1, 2].tolist() == [255, 0, 0, 255]


def test_rgba_is_readonly_red_snapshot():
    source = QImage(1, 1, QImage.Format.Format_ARGB32)
    source.fill(QColor(255, 0, 0))
    image = PmaImage.from_qimage(source)
    pixels = image.rgba()
    assert image.to_qimage().format() == QImage.Format.Format_ARGB32_Premultiplied
    assert pixels.dtype == np.uint8
    assert pixels.shape == (1, 1, 4)
    assert pixels.tolist() == [[[255, 0, 0, 255]]]
    assert not pixels.flags.writeable
    with pytest.raises(ValueError):
        pixels[0, 0, 0] = 0

    image.write_rgba(np.array([[[0, 0, 255, 255]]], dtype=np.uint8))
    assert pixels.tolist() == [[[255, 0, 0, 255]]]


def test_to_qimage_supports_in_place_painting():
    image = _blank(1, 1)
    qimage = image.to_qimage()
    painter = QPainter(qimage)
    painter.fillRect(qimage.rect(), QColor(0, 0, 255))
    painter.end()

    assert image.to_qimage() is qimage
    assert image.rgba().tolist() == [[[0, 0, 255, 255]]]


@pytest.mark.parametrize("width", [1, 63, 64, 65])
def test_write_rgba_at_edges(width):
    image = _blank(width + 1, 3)
    patch = np.arange(2 * width * 4, dtype=np.uint8).reshape(2, width, 4)[:, ::-1]
    patch[..., 3] = 255
    image.write_rgba(patch, x=1, y=1)

    expected = np.zeros((3, width + 1, 4), dtype=np.uint8)
    expected[1:, 1:] = patch
    np.testing.assert_array_equal(image.rgba(), expected)

    single = _blank(width, 1)
    single.write_rgba(patch[:1])
    np.testing.assert_array_equal(single.rgba(), patch[:1])


def test_translucent_round_trip_matches_imaging():
    straight = np.array([[[235, 51, 19, 129], [29, 67, 211, 53]]], dtype=np.uint8)
    source = imaging.rgba_array_to_qimage(straight)
    snapshot = PmaImage.from_qimage(source).rgba()
    expected = imaging.rgba_array_to_qimage(imaging.qimage_rgba_array(source))
    destination = _blank(2, 1)
    destination.write_rgba(snapshot)

    np.testing.assert_array_equal(destination.rgba(), snapshot)
    assert destination.to_qimage() == expected
    assert destination.to_qimage() == source


def test_null_image_returns_readonly_rgba():
    image = PmaImage.from_qimage(QImage())
    pixels = image.rgba()
    assert pixels.shape == (0, 0, 4)
    assert pixels.dtype == np.uint8
    assert not pixels.flags.writeable
    image.write_rgba(pixels)


@pytest.mark.parametrize(
    ("patch", "x", "y"),
    [
        (np.zeros((1, 1, 4), dtype=np.float32), 0, 0),
        (np.zeros((1, 1, 3), dtype=np.uint8), 0, 0),
        (np.zeros((1, 4), dtype=np.uint8), 0, 0),
        ([], 0, 0),
        (np.zeros((1, 1, 4), dtype=np.uint8), -1, 0),
        (np.zeros((1, 1, 4), dtype=np.uint8), 0, -1),
        (np.zeros((1, 2, 4), dtype=np.uint8), 1, 0),
        (np.zeros((2, 1, 4), dtype=np.uint8), 0, 1),
        (np.zeros((1, 1, 4), dtype=np.uint8), 2, 0),
        (np.zeros((1, 1, 4), dtype=np.uint8), 0, 2),
        (np.zeros((1, 1, 4), dtype=np.uint8), 0.5, 0),
    ],
)
def test_write_rgba_rejects_invalid_input(patch, x, y):
    image = _blank(2, 2)
    key = image.to_qimage().cacheKey()
    with pytest.raises(ValueError):
        image.write_rgba(patch, x=x, y=y)
    assert image.to_qimage().cacheKey() == key


def test_helpers_preserve_qimage_identity_copy_and_rgba():
    image = QImage(2, 1, QImage.Format.Format_ARGB32)
    image.fill(QColor(201, 18, 63, 128))

    assert as_qimage(image) is image
    duplicate = copy_image(image)
    assert isinstance(duplicate, QImage)
    assert duplicate is not image
    assert duplicate.cacheKey() == QImage(image).cacheKey()
    assert duplicate.format() == image.format()

    pixels = image_rgba(image)
    np.testing.assert_array_equal(pixels, imaging.qimage_rgba_array(image))
    assert pixels.flags.writeable

    duplicate.fill(0)
    assert image.pixelColor(0, 0) == QColor(201, 18, 63, 128)


def test_helpers_use_pma_image_operations():
    image = _blank(2, 1)
    image.write_rgba(np.array([[[255, 0, 0, 255]]], dtype=np.uint8))

    assert as_qimage(image) is image.to_qimage()
    duplicate = copy_image(image)
    assert isinstance(duplicate, PmaImage)
    assert duplicate is not image
    assert as_qimage(duplicate) is not as_qimage(image)
    assert as_qimage(duplicate).cacheKey() == as_qimage(image).cacheKey()

    pixels = image_rgba(image)
    np.testing.assert_array_equal(pixels, image.rgba())
    assert not pixels.flags.writeable

    duplicate.write_rgba(np.zeros((1, 1, 4), dtype=np.uint8))
    assert image.rgba()[0, 0].tolist() == [255, 0, 0, 255]


@pytest.mark.parametrize("helper", [as_qimage, copy_image, image_rgba])
def test_helpers_accept_none(helper):
    assert helper(None) is None
