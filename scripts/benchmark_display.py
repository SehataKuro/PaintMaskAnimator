"""Reproducible display microbenchmarks (not end-to-end UI latency).

Run: python -m scripts.benchmark_display
"""
import argparse
import json
import os
import statistics
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from paintmaskanimator import constants, native
from paintmaskanimator.canvas import PaintCanvas
from paintmaskanimator.models import Frame, Layer


def median_ms(function, repeats=7):
    function()
    values = []
    for _ in range(repeats):
        start = time.perf_counter()
        function()
        values.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(values), 3)


def numpy_tint(rgba):
    result = rgba.copy()
    lum = (rgba[:, :, 0].astype(np.float32) * 0.299
           + rgba[:, :, 1].astype(np.float32) * 0.587
           + rgba[:, :, 2].astype(np.float32) * 0.114) / 255.0
    mapped = np.array([0, 100, 255], np.float32)[None, None, :] * (1 - lum[:, :, None])
    mapped += 255 * lum[:, :, None]
    result[:, :, :3] = np.clip(mapped, 0, 255).astype(np.uint8)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="Run with small dense fixtures")
    args = parser.parse_args()
    frame_count, layer_count = (24, 2) if args.smoke else (1200, 8)
    source_width, source_height = (32, 24) if args.smoke else (3840, 2160)
    display_width, display_height = (16, 12) if args.smoke else (960, 540)
    pixel_width, pixel_height = (32, 24) if args.smoke else (1920, 1080)
    if args.smoke:
        constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = source_width, source_height
    app = QApplication.instance() or QApplication([])
    canvas = PaintCanvas()
    frames = [Frame([Layer("A", QImage()) for _ in range(layer_count)]) for _ in range(frame_count)]
    for frame in frames:
        for layer in frame.layers:
            layer.has_content, layer.sequence_number = True, 1
    canvas.frames = frames
    canvas.install_legacy_frames()
    assert canvas.resolve_key_frame(frame_count - 1, 0) == 0
    columns = list(range(max(0, frame_count - 64), frame_count))

    def backwards():
        for column in columns:
            for index in range(layer_count):
                for key in range(column, -1, -1):
                    layer = canvas.frames[key].layers[index]
                    if not layer.has_content or layer.sequence_number != 1:
                        break

    def indexed():
        for column in columns:
            for index in range(layer_count):
                canvas.resolve_key_frame(column, index)

    def build():
        canvas._invalidate_exposure_index()
        canvas.resolve_key_frame(frame_count - 1, 0)

    results = {
        "exposure_64_seeks_1200_frames_8_layers": {
            "before_ms": median_ms(backwards),
            "cached_ms": median_ms(indexed),
            "index_build_ms": median_ms(build),
        }
    }
    if args.smoke:
        results["smoke_shape"] = {
            "frames": frame_count, "layers": layer_count,
            "source": [source_width, source_height],
            "display": [display_width, display_height],
            "pixels": [pixel_width, pixel_height],
        }
    sources = []
    for index in range(4):
        source = QImage(source_width, source_height, QImage.Format.Format_ARGB32_Premultiplied)
        source.fill(QColor(index * 60, 100, 200, 180))
        sources.append(source)
    destination = QImage(display_width, display_height, QImage.Format.Format_ARGB32_Premultiplied)
    rect = QRectF(0, 0, display_width, display_height)

    def draw(cached):
        destination.fill(0)
        painter = QPainter(destination)
        for source in sources:
            display = canvas._display_image_for_rect(source, rect) if cached else source
            assert display is not None
            painter.drawImage(rect, display)
        painter.end()

    results["draw_4_layers_4k_to_960x540"] = {
        "before_ms": median_ms(lambda: draw(False)),
        "cached_ms": median_ms(lambda: draw(True)),
    }
    rgba = np.random.default_rng(24).integers(0, 256, (pixel_height, pixel_width, 4), np.uint8)
    results["onion_tint_1920x1080"] = {
        "numpy_ms": median_ms(lambda: numpy_tint(rgba)),
        "rust_ms": median_ms(lambda: native.tint_onion(rgba, (0, 100, 255), True, None)),
    }
    colors = np.random.default_rng(25).integers(0, 256, (100, 4), np.uint8)
    colors[:, 3] = 255
    pixels = colors[np.random.default_rng(26).integers(0, 100, (pixel_height, pixel_width))]
    source = QImage(pixels.data, pixel_width, pixel_height, pixel_width * 4, QImage.Format.Format_RGBA8888).copy()
    layer = Layer("palette", source)
    canvas.visible_color_rgbs = {tuple(map(int, color[:3])) for color in colors[:80]}
    canvas.preview_color_remap = {
        tuple(map(int, child[:3])): tuple(map(int, parent[:3]))
        for child, parent in zip(colors[:64], colors[1:65])
    }

    def filter_image():
        # Compare uncached output generation during repeated palette previews.
        canvas._color_filter_cache.clear()
        return canvas.filtered_layer_image(layer)

    results["palette_filter_1920x1080_64_remaps"] = {
        "rust_ms": median_ms(filter_image),
    }
    previous_size = constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT
    try:
        constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = source_width, source_height
        canvas.frames = [Frame([
            Layer(str(index), QImage(source), has_content=True, sequence_number=frame_index + 1)
            for index, source in enumerate(sources)
        ]) for frame_index in range(3)]
        canvas.install_legacy_frames()
        canvas.onion_all_layers = True

        def toggle_with_onions(size):
            canvas.set_layer_visibility(1, not canvas.frames[0].layers[1].visible)
            # Measure a newly encountered visibility state, not a cache hit.
            canvas._onion_cache.clear()
            canvas._tinted_onion_image(0, QColor("red"), target_size=size)
            canvas._tinted_onion_image(2, QColor("blue"), target_size=size)

        results["visibility_4k_4_layers_2_onions_960x540"] = {
            "full_resolution_ms": median_ms(lambda: toggle_with_onions(None)),
            "display_resolution_ms": median_ms(lambda: toggle_with_onions((display_width, display_height))),
        }
    finally:
        constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = previous_size
    print(json.dumps(results, indent=2))
    canvas.close()
    app.processEvents()


if __name__ == "__main__":
    main()
