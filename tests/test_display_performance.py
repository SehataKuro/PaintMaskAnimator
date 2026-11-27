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
from paintmaskanimator.pma_image import as_qimage


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def image(color="red", width=64, height=32):
    result = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(color))
    return result


def reference(frames, column, index):
    if index >= len(frames[column].layers):
        return -1
    slot = frames[column].layers[index]
    if not slot.has_content:
        return -1
    key = column
    while key > 0:
        if index >= len(frames[key - 1].layers):
            break
        previous = frames[key - 1].layers[index]
        if not previous.has_content or previous.sequence_number != slot.sequence_number:
            break
        key -= 1
    return key


def test_exposure_index_matches_backwards_search():
    rng = np.random.default_rng(46)
    for _ in range(12):
        frames = [Frame([
            Layer("A", QImage(), has_content=bool(rng.integers(0, 4) == 0),
                  is_blank_key=bool(rng.integers(0, 6) == 0),
                  sequence_number=int(rng.integers(1, 4)))
            for _ in range(int(rng.integers(0, 6)))
        ]) for _ in range(50)]
        expected = [[reference(frames, column, index) for index in range(5)]
                    for column in range(50)]
        assert timeline_resolution.resolve_exposures(frames).tolist() == expected


def test_key_index_reused_and_invalidated_by_metadata_and_structure(qapp, monkeypatch):
    canvas = PaintCanvas()
    canvas.frames = [Frame([Layer("A", QImage())]) for _ in range(8)]
    for frame in canvas.frames:
        frame.layers[0].has_content = True
        frame.layers[0].sequence_number = 1
    original = timeline_resolution.resolve_exposures
    calls = []

    def count(frames):
        calls.append(1)
        return original(frames)

    monkeypatch.setattr(timeline_resolution, "resolve_exposures", count)
    for column in range(8):
        assert canvas.resolve_key_frame(column, 0) == 0
    assert len(calls) == 1
    for frame in canvas.frames[4:]:
        frame.layers[0].has_content = False
        frame.layers[0].is_blank_key = True
    assert canvas.resolve_key_frame(7, 0) is None
    for frame in canvas.frames[2:4]:
        frame.layers[0].has_content = False
    assert canvas.resolve_key_frame(3, 0) is None
    canvas.frames[1], canvas.frames[0] = canvas.frames[0], canvas.frames[1]
    canvas.changed.emit()
    assert canvas.resolve_key_frame(1, 0) == 0
    assert canvas.resolve_key_frame(3, 0) is None


def test_playback_blank_keys_cancel_previous_exposure(qapp):
    canvas = PaintCanvas()
    canvas.frames = [Frame([Layer("A", QImage())]) for _ in range(5)]
    for column, frame in enumerate(canvas.frames):
        frame.layers[0].has_content = column < 2
        frame.layers[0].sequence_number = 1 if column < 2 else None
        frame.layers[0].is_blank_key = column >= 2
        frame.layers[0].blank_start = column == 2
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


def test_backend_contract_includes_display_functions():
    assert {"resolve_timeline", "tint_onion", "filter_palette"} <= set(native.REQUIRED_API)


@pytest.mark.skipif(not native.available(), reason="native extension unavailable")
@pytest.mark.parametrize("visible", [None, set(), {(255, 0, 0), (0, 255, 0)}])
@pytest.mark.parametrize("legacy", [None, (255, 0, 0), (0, 0, 255)])
def test_palette_filter_native_and_numpy_render_identically(qapp, monkeypatch, visible, legacy):
    rng = np.random.default_rng(49)
    palette = np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255], [0, 0, 0],
                        [255, 255, 255]], dtype=np.uint8)
    rgba = np.empty((31, 65, 4), dtype=np.uint8)
    rgba[:, :, :3] = palette[rng.integers(0, len(palette), rgba.shape[:2])]
    rgba[:, :, 3] = rng.choice([0, 1, 127, 254, 255], rgba.shape[:2])
    source = QImage(rgba.data, 65, 31, 65 * 4, QImage.Format.Format_RGBA8888).copy()
    canvas = PaintCanvas()
    canvas.visible_color_rgbs = visible
    # Includes a cycle and a chain: replacements must use original pixels.
    canvas.preview_color_remap = {(255, 0, 0): (0, 255, 0),
                                  (0, 255, 0): (0, 0, 255),
                                  (0, 0, 255): (255, 0, 0)}
    layer = Layer("A", source, color_filter_enabled=legacy is not None,
                  color_filter_rgb=legacy)
    original = QImage(source)
    actual = canvas.filtered_layer_image(layer)
    assert canvas.filtered_layer_image(layer) is actual
    # Rust avoids allocating the full RGB/alpha index used by the NumPy reference.
    assert not canvas._color_index_cache
    canvas._color_filter_cache.clear()
    monkeypatch.setattr(native, "filter_palette", filter_palette_reference)
    expected = canvas.filtered_layer_image(layer)
    assert actual == expected
    assert source == original
    canvas.close()


@pytest.mark.skipif(not native.available(), reason="native extension unavailable")
def test_native_palette_filter_empty_strided_and_invalid_input():
    rgba = np.array([[[255, 0, 0, 127], [0, 255, 0, 0], [0, 0, 255, 255]]], np.uint8)
    remap = {(255, 0, 0): (1, 2, 3)}
    actual = native.filter_palette(rgba[:, ::2], None, None, remap)
    np.testing.assert_array_equal(actual, [[[1, 2, 3, 127], [0, 0, 255, 255]]])
    assert native.filter_palette(np.empty((0, 0, 4), np.uint8), None, None, {}).size == 0
    with pytest.raises(ValueError, match="RGBA"):
        native._native.filter_palette(b"abc", None, None, [])


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
def test_onion_native_and_reference_render_identically(qapp, monkeypatch, all_layers, selected_only):
    canvas = PaintCanvas()
    canvas.frames = [Frame([Layer("A", image(), opacity=0.6, has_content=True),
                            Layer("B", image("blue"), has_content=True)])]
    canvas.active_layer_index = 0
    canvas.onion_all_layers = all_layers
    canvas.selected_used_color_rgbs = {(255, 0, 0)}
    actual = canvas._tinted_onion_image(0, QColor(27, 187, 48), True, selected_only)
    canvas._onion_cache.clear()
    monkeypatch.setattr(native, "tint_onion", tint_onion_reference)
    expected = canvas._tinted_onion_image(0, QColor(27, 187, 48), True, selected_only)
    assert actual == expected


def test_native_display_validates_buffer_lengths():
    if not native.available() or not hasattr(native._native, "tint_onion"):
        pytest.skip("native extension unavailable")
    with pytest.raises(ValueError, match="RGBA"):
        native._native.tint_onion(b"abc", (0, 0, 0), True, None)
    assert not hasattr(native._native, "resolve_exposures")
    assert native.resolve_timeline([]).shape == (0, 0)
    assert native.resolve_timeline([Frame([])]).shape == (1, 0)


@pytest.mark.parametrize("all_layers", [False, True])
@pytest.mark.parametrize("selected_only", [False, True])
def test_onion_preview_preserves_full_size_render_and_visibility(
    qapp, monkeypatch, all_layers, selected_only
):
    from paintmaskanimator import constants
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 64)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 32)
    canvas = PaintCanvas()
    patterned = image()
    painter = QPainter(patterned)
    painter.fillRect(32, 0, 32, 32, QColor("green"))
    painter.end()
    overlay = image("transparent")
    painter = QPainter(overlay)
    painter.fillRect(0, 16, 64, 16, QColor("blue"))
    painter.end()
    canvas.frames = [Frame([Layer("A", patterned, has_content=True),
                            Layer("B", overlay, opacity=0.6, has_content=True)])]
    canvas.active_layer_index = 0
    canvas.onion_all_layers = all_layers
    canvas.selected_used_color_rgbs = {(255, 0, 0)}
    color = QColor(27, 187, 48)
    original = QImage(canvas.frames[0].layers[0].image.to_qimage())
    full = canvas._tinted_onion_image(0, color, True, selected_only)
    same_size = canvas._tinted_onion_image(0, color, True, selected_only, (64, 32))
    assert same_size == full
    preview = canvas._tinted_onion_image(0, color, True, selected_only, (16, 8))
    assert preview.size().width() == 16
    assert preview.size().height() == 8
    assert preview == full.scaled(16, 8)
    assert canvas._tinted_onion_image(0, color, True, selected_only, (16, 8)) is preview
    canvas.set_layer_visibility(0, False)
    hidden = canvas._tinted_onion_image(0, color, True, selected_only, (16, 8))
    assert hidden is not preview
    if not all_layers:
        assert hidden.pixelColor(0, 0).alpha() == 0
    canvas.set_layer_visibility(0, True)
    assert canvas._tinted_onion_image(0, color, True, selected_only, (16, 8)) is preview
    assert as_qimage(canvas.frames[0].layers[0].image) == original
    canvas.close()


def test_onion_draw_uses_screen_resolution_and_skips_zero_opacity(qapp, monkeypatch):
    canvas = PaintCanvas()
    canvas.frames = [Frame([Layer("A", image(), has_content=True)]) for _ in range(3)]
    canvas.current_frame = 1
    canvas.onion_all_layers = True
    canvas.onion_previous_levels = [100]
    monkeypatch.setattr(canvas, "devicePixelRatioF", lambda: 2.0)
    calls = []

    def preview(*args, **kwargs):
        calls.append(kwargs["target_size"])
        return image()

    monkeypatch.setattr(canvas, "_tinted_onion_image", preview)
    output = image()
    painter = QPainter(output)
    try:
        canvas._draw_onion_range(painter, QRectF(0, 0, 16, 8), -1, 1, 0.5, QColor("red"))
        assert calls == [(32, 16)]
        calls.clear()
        canvas.onion_previous_scale = 200
        canvas._draw_onion_range(painter, QRectF(0, 0, 16, 8), -1, 1, 0.5, QColor("red"))
        assert calls == [(64, 32)]
        calls.clear()
        canvas._draw_onion_range(painter, QRectF(0, 0, 16, 8), -1, 1, 0, QColor("red"))
        assert not calls
    finally:
        painter.end()
        canvas.close()


def filter_palette_reference(rgba, visible, legacy, remap):
    result = rgba.copy()
    keep = rgba[:, :, 3] != 0
    if visible is not None:
        selected = np.zeros(keep.shape, bool)
        for rgb in visible:
            selected |= np.all(rgba[:, :, :3] == rgb, axis=2)
        keep &= selected
    if legacy is not None:
        keep &= np.all(rgba[:, :, :3] == legacy, axis=2)
    result[~keep, 3] = 0
    for child, parent in remap.items():
        result[keep & np.all(rgba[:, :, :3] == child, axis=2), :3] = parent
    return result


def tint_onion_reference(rgba, color, enabled, selected):
    result = rgba.copy()
    if selected is not None:
        keep = np.zeros(rgba.shape[:2], bool)
        for rgb in selected:
            keep |= np.all(rgba[:, :, :3] == rgb, axis=2)
        result[~keep, 3] = 0
    if enabled:
        lum = (rgba[:, :, 0].astype(np.float32) * 0.299
               + rgba[:, :, 1].astype(np.float32) * 0.587
               + rgba[:, :, 2].astype(np.float32) * 0.114) / 255.0
        mapped = np.asarray(color, np.float32)[None, None, :] * (1 - lum[:, :, None])
        mapped += 255 * lum[:, :, None]
        result[:, :, :3] = np.clip(mapped, 0, 255).astype(np.uint8)
    return result


def test_model_display_and_onion_hits_skip_qimage_conversion(qapp, monkeypatch):
    from paintmaskanimator.display_image_cache import display_images
    from paintmaskanimator.pma_image import PmaImage

    original_to_qimage = PmaImage.to_qimage
    conversions = []
    display_images.clear()

    def detached(source):
        conversions.append(source.width * source.height * 4)
        return original_to_qimage(source).copy()

    monkeypatch.setattr(PmaImage, "to_qimage", detached)
    canvas = PaintCanvas()
    canvas.frames = [Frame([Layer("A", image(), has_content=True)])]
    model = canvas.active_layer.image
    rect = QRectF(0, 0, 16, 8)

    assert canvas._display_image_for_rect(model, rect).pixelColor(0, 0) == QColor("red")
    assert conversions == [8192]
    display_images.clear()
    assert canvas._display_image_for_rect(model, rect).pixelColor(0, 0) == QColor("red")
    assert conversions == [8192]

    def onion():
        return canvas._tinted_onion_image(0, QColor("white"), color_enabled=False, target_size=(16, 8))

    first = onion()
    assert onion() is first
    assert conversions == [8192, 8192]
    canvas.push_layer_undo()
    model.write_qimage(image("blue"), 0, 0)
    blue = onion()
    assert blue != first
    canvas.undo()
    assert onion() == first
    canvas.redo()
    assert onion() == blue
    assert all(size == 8192 for size in conversions)


def test_filtered_preview_hits_skip_evicted_upstream_images(qapp, monkeypatch):
    from paintmaskanimator.display_image_cache import display_images
    from paintmaskanimator.pma_image import PmaImage

    canvas = PaintCanvas()
    canvas.frames = [Frame([Layer("A", image(), has_content=True)])]
    canvas.visible_color_rgbs = {(255, 0, 0)}
    canvas.silhouette_non_background = True
    canvas.set_view_preview(exposure=1.0)
    rect = QRectF(0, 0, 16, 8)

    def render():
        surface = image("transparent", 16, 8)
        painter = QPainter(surface)
        try:
            canvas.draw_frame_direct(painter, rect, 0)
        finally:
            painter.end()
        return surface

    original = PmaImage.to_qimage
    reads = []

    def detached(source):
        reads.append(source.generation)
        return original(source).copy()

    monkeypatch.setattr(PmaImage, "to_qimage", detached)
    display_images.clear()
    first = render()
    assert first.pixelColor(0, 0) == QColor("black")
    assert reads == [canvas.active_layer.image.generation]

    display_images.clear()
    canvas._color_filter_cache.clear()
    canvas._silhouette_cache.clear()
    canvas._display_image_cache.clear()
    canvas._display_image_cache_bytes = 0
    assert render() == first  # The preview still hits.

    canvas._view_preview_cache.clear()
    assert render() == first  # Only the scaled image still hits.
    assert reads == [canvas.active_layer.image.generation]

    canvas.active_layer.image.write_qimage(image("blue"), 0, 0)
    assert render().pixelColor(0, 0) == QColor("transparent")
    assert reads[-1] == canvas.active_layer.image.generation
    assert len(reads) == 2


@pytest.mark.parametrize("write", ["write_qimage", "write_qimages", "write_rgba"])
def test_display_cache_moves_written_generation_without_reconverting(qapp, write):
    from paintmaskanimator.display_image_cache import display_images, model_image_key
    from paintmaskanimator.pma_image import PmaImage

    source = PmaImage.blank(64, 32)
    other = PmaImage.blank(32, 16)
    display_images.clear()
    try:
        baseline = display_images.get(other).sizeInBytes()
        for color in ("red", "blue", "green"):
            key = model_image_key(source)
            size = display_images.get(source).sizeInBytes()
            assert display_images.bytes_used == baseline + size
            patch = image(color, 1, 1)
            if write == "write_qimage":
                source.write_qimage(patch, 3, 4)
            elif write == "write_qimages":
                source.write_qimages(((3, 4, patch), (4, 4, patch)))
            else:
                rgba = np.array([[QColor(color).getRgb()]], dtype=np.uint8)
                source.write_rgba(rgba, 3, 4)
            # 古い世代は残さず、書いた矩形だけ差し替えた画像を新しい世代で持つ。
            assert key not in display_images._entries
            assert model_image_key(source) in display_images._entries
            assert display_images.bytes_used == baseline + size
            misses = display_images.misses
            assert display_images.get(source).pixelColor(3, 4) == QColor(color)
            assert display_images.misses == misses
            assert display_images.get(source) == source.to_qimage()
    finally:
        display_images.clear()


def test_single_pixel_mirror_cache_owns_its_pixels(qapp):
    from paintmaskanimator.display_image_cache import display_images
    from paintmaskanimator.pma_image import PmaImage

    canvas = PaintCanvas()
    canvas.flip_horizontal = True
    source = PmaImage.from_qimage(image("red", 1, 1))
    try:
        mirrored = canvas._display_image_for_rect(source, QRectF(0, 0, 1, 1))
        assert mirrored.cacheKey() != source.to_qimage().cacheKey()
        source.write_qimage(image("blue", 1, 1), 0, 0)
        assert mirrored.pixelColor(0, 0) == QColor("red")
        assert canvas._display_image_for_rect(source, QRectF(0, 0, 1, 1)).pixelColor(0, 0) == QColor("blue")
    finally:
        display_images.clear()
        canvas.close()


def test_display_cache_rect_update_matches_full_conversion(qapp):
    from paintmaskanimator.display_image_cache import display_images
    from paintmaskanimator.imaging import qimage_rgba_array
    from paintmaskanimator.pma_image import PmaImage

    rng = np.random.default_rng(7)
    source = PmaImage.blank(40, 30)
    display_images.clear()
    try:
        display_images.get(source)
        for x, y, w, h in ((0, 0, 40, 30), (3, 5, 7, 9), (33, 21, 7, 9)):
            source.write_rgba(rng.integers(0, 256, (h, w, 4), dtype=np.uint8), x, y)
            misses = display_images.misses
            shown = display_images.get(source)
            assert display_images.misses == misses
            assert np.array_equal(qimage_rgba_array(shown), qimage_rgba_array(source.to_qimage()))
            assert bytes(shown.constBits()) == bytes(source.to_qimage().constBits())
    finally:
        display_images.clear()
