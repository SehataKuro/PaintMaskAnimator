"""Scrubbing caches must retain editing/blank-key/display semantics."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from paintmaskanimator import native, timeline_resolution
from paintmaskanimator.canvas import PaintCanvas
from paintmaskanimator.models import Frame, Layer


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def image(color="red", width=64, height=32):
    result = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(color))
    return result


def reference(frames, column, index):
    for key in range(column, -1, -1):
        if index >= len(frames[key].layers):
            continue
        layer = frames[key].layers[index]
        if layer.has_content:
            return key if column < key + max(1, layer.exposure) else -1
        if layer.is_blank_key:
            return -1
    return -1


@pytest.mark.parametrize("fallback", [False, True])
def test_exposure_index_matches_backwards_search(monkeypatch, fallback):
    if fallback:
        monkeypatch.setenv("PMA_NO_NATIVE", "1")
    rng = np.random.default_rng(46)
    for _ in range(12):
        frames = [Frame([
            Layer("A", QImage(), has_content=bool(rng.integers(0, 4) == 0),
                  is_blank_key=bool(rng.integers(0, 6) == 0),
                  exposure=int(rng.integers(-2, 25)))
            for _ in range(int(rng.integers(0, 6)))
        ]) for _ in range(50)]
        expected = [[reference(frames, column, index) for index in range(5)]
                    for column in range(50)]
        assert timeline_resolution.resolve_exposures(frames).tolist() == expected


def test_key_index_reused_and_invalidated_by_metadata_and_structure(qapp, monkeypatch):
    canvas = PaintCanvas()
    canvas.frames = [Frame([Layer("A", QImage())]) for _ in range(8)]
    first = canvas.frames[0].layers[0]
    first.has_content, first.exposure = True, 8
    original = timeline_resolution.resolve_exposures
    calls = []

    def count(frames):
        calls.append(1)
        return original(frames)

    monkeypatch.setattr(timeline_resolution, "resolve_exposures", count)
    for column in range(8):
        assert canvas.resolve_key_frame(column, 0) == 0
    assert len(calls) == 1
    canvas.frames[4].layers[0].is_blank_key = True
    assert canvas.resolve_key_frame(7, 0) is None
    first.exposure = 2
    assert canvas.resolve_key_frame(3, 0) is None
    canvas.frames[1], canvas.frames[0] = canvas.frames[0], canvas.frames[1]
    canvas.changed.emit()
    assert canvas.resolve_key_frame(2, 0) == 1
    assert canvas.resolve_key_frame(3, 0) is None


def test_playback_blank_keys_cancel_previous_exposure(qapp):
    canvas = PaintCanvas()
    canvas.frames = [Frame([Layer("A", QImage())]) for _ in range(5)]
    canvas.frames[0].layers[0].has_content = True
    canvas.frames[0].layers[0].exposure = 5
    canvas.frames[2].layers[0].is_blank_key = True
    canvas._build_playback_key_map()
    assert canvas._playback_resolved_keys == [[0], [0], [None], [None], [None]]


def test_display_preview_reused_across_opacity_visibility_and_invalidated_by_pixels(qapp):
    canvas = PaintCanvas()
    source = image()
    canvas.frames = [Frame([Layer("A", source, has_content=True)])]
    rect = QRectF(0, 0, 16, 8)
    first = canvas._display_image_for_rect(source, rect)
    assert (first.width(), first.height()) == (16, 8)
    canvas.set_layer_visibility(0, False)
    canvas.set_layer_visibility(0, True)
    assert canvas._display_image_for_rect(source, rect) is first
    canvas.set_layer_opacity(0, 0.5)
    assert canvas._display_image_for_rect(source, rect) is first
    painter = QPainter(source)
    painter.fillRect(source.rect(), QColor("blue"))
    painter.end()
    second = canvas._display_image_for_rect(source, rect)
    assert second is not first
    assert second.pixelColor(0, 0) == QColor("blue")
    canvas.drawing = True
    assert canvas._display_image_for_rect(source, rect) is source


def test_display_cache_is_bounded_and_mirror_is_in_key(qapp):
    canvas = PaintCanvas()
    source = image()
    first = canvas._display_image_for_rect(source, QRectF(0, 0, 16, 8))
    canvas.flip_horizontal = True
    assert canvas._display_image_for_rect(source, QRectF(0, 0, 16, 8)) is not first
    canvas.flip_horizontal = False
    for _ in range(110):
        canvas._display_image_for_rect(image(), QRectF(0, 0, 16, 8))
    assert len(canvas._display_image_cache) <= 96
    assert canvas._display_image_cache_bytes == sum(
        item.sizeInBytes() for item in canvas._display_image_cache.values()
    )


@pytest.mark.skipif(not native.available(), reason="native extension unavailable")
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("selected", [None, [], [(1, 2, 3), (255, 255, 255)]])
def test_native_onion_exactly_matches_numpy(enabled, selected):
    rng = np.random.default_rng(24)
    rgba = rng.integers(0, 256, (301, 997, 4), dtype=np.uint8)
    rgba[0, 0, :3] = (1, 2, 3)
    rgba[0, 1, :3] = (255, 255, 255)
    for color in [(0, 0, 0), (255, 255, 255), (0, 100, 255), (27, 187, 48)]:
        expected = rgba.copy()
        if selected is not None:
            keep = np.zeros(rgba.shape[:2], dtype=bool)
            for rgb in selected:
                keep |= np.all(rgba[:, :, :3] == rgb, axis=2)
            expected[~keep, 3] = 0
        if enabled:
            lum = (rgba[:, :, 0].astype(np.float32) * 0.299
                   + rgba[:, :, 1].astype(np.float32) * 0.587
                   + rgba[:, :, 2].astype(np.float32) * 0.114) / 255.0
            mapped = np.array(color, dtype=np.float32)[None, None, :] * (1 - lum[:, :, None])
            mapped += 255 * lum[:, :, None]
            expected[:, :, :3] = np.clip(mapped, 0, 255).astype(np.uint8)
        actual = native.tint_onion(rgba, color, enabled, selected)
        assert actual is not None
        np.testing.assert_array_equal(actual, expected)


def test_new_native_functions_fall_back_on_older_extension(monkeypatch):
    monkeypatch.setattr(native, "_native", object())
    assert native.resolve_exposures(np.zeros((1, 1), np.uint8), np.ones((1, 1), np.int64)) is None
    assert native.tint_onion(np.zeros((1, 1, 4), np.uint8), (0, 0, 0), True, None) is None


def test_onion_cache_survives_unrelated_visibility_and_restores_previous_state(qapp):
    canvas = PaintCanvas()
    canvas.frames = [Frame([Layer("A", image(), has_content=True),
                            Layer("B", image("blue"), has_content=True)])]
    canvas.active_layer_index = 0
    canvas.onion_all_layers = False
    red = QColor("red")
    first = canvas._tinted_onion_image(0, red)
    canvas.set_layer_visibility(1, False)
    assert canvas._tinted_onion_image(0, red) is first
    canvas.set_layer_visibility(0, False)
    hidden = canvas._tinted_onion_image(0, red)
    assert hidden is not first
    assert hidden.pixelColor(0, 0).alpha() == 0
    canvas.set_layer_visibility(0, True)
    assert canvas._tinted_onion_image(0, red) is first


@pytest.mark.skipif(not native.available(), reason="native extension unavailable")
@pytest.mark.parametrize("all_layers", [False, True])
@pytest.mark.parametrize("selected_only", [False, True])
def test_onion_native_and_fallback_render_identically(qapp, monkeypatch, all_layers, selected_only):
    canvas = PaintCanvas()
    canvas.frames = [Frame([Layer("A", image(), opacity=0.6, has_content=True),
                            Layer("B", image("blue"), has_content=True)])]
    canvas.active_layer_index = 0
    canvas.onion_all_layers = all_layers
    canvas.selected_used_color_rgbs = {(255, 0, 0)}
    actual = canvas._tinted_onion_image(0, QColor(27, 187, 48), True, selected_only)
    canvas._onion_cache.clear()
    monkeypatch.setenv("PMA_NO_NATIVE", "1")
    expected = canvas._tinted_onion_image(0, QColor(27, 187, 48), True, selected_only)
    assert actual == expected


def test_native_display_validates_buffer_lengths():
    if not native.available() or not hasattr(native._native, "tint_onion"):
        pytest.skip("native extension unavailable")
    with pytest.raises(ValueError, match="RGBA"):
        native._native.tint_onion(b"abc", (0, 0, 0), True, None)
    with pytest.raises(ValueError, match="dimensions"):
        native._native.resolve_exposures(b"a", b"", 1, 1)
    assert native._native.resolve_exposures(b"", b"", 0, 0) == b""
