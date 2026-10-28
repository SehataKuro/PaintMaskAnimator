"""Reproducible display microbenchmarks (not end-to-end UI latency).

Run: python -m scripts.benchmark_display
"""
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
    app = QApplication.instance() or QApplication([])
    canvas = PaintCanvas()
    canvas.frames = [Frame([Layer("A", QImage()) for _ in range(8)]) for _ in range(1200)]
    for layer in canvas.frames[0].layers:
        layer.has_content, layer.exposure = True, 1200
    columns = list(range(1136, 1200))

    def backwards():
        for column in columns:
            for index in range(8):
                for key in range(column, -1, -1):
                    layer = canvas.frames[key].layers[index]
                    if layer.has_content or layer.is_blank_key:
                        break

    def indexed():
        for column in columns:
            for index in range(8):
                canvas.resolve_key_frame(column, index)

    def build():
        canvas._invalidate_exposure_index()
        canvas.resolve_key_frame(1199, 0)

    results = {
        "exposure_64_seeks_1200_frames_8_layers": {
            "before_ms": median_ms(backwards),
            "cached_ms": median_ms(indexed),
            "index_build_ms": median_ms(build),
        }
    }
    sources = []
    for index in range(4):
        source = QImage(3840, 2160, QImage.Format.Format_ARGB32_Premultiplied)
        source.fill(QColor(index * 60, 100, 200, 180))
        sources.append(source)
    destination = QImage(960, 540, QImage.Format.Format_ARGB32_Premultiplied)
    rect = QRectF(0, 0, 960, 540)

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
    rgba = np.random.default_rng(24).integers(0, 256, (1080, 1920, 4), np.uint8)
    results["onion_tint_1920x1080"] = {
        "numpy_ms": median_ms(lambda: numpy_tint(rgba)),
        "rust_ms": median_ms(lambda: native.tint_onion(rgba, (0, 100, 255), True, None))
        if native.available() and hasattr(native._native, "tint_onion") else None,
    }
    colors = np.random.default_rng(25).integers(0, 256, (100, 4), np.uint8)
    colors[:, 3] = 255
    pixels = colors[np.random.default_rng(26).integers(0, 100, (1080, 1920))]
    source = QImage(pixels.data, 1920, 1080, 1920 * 4, QImage.Format.Format_RGBA8888).copy()
    layer = Layer("palette", source)
    canvas.visible_color_rgbs = {tuple(map(int, color[:3])) for color in colors[:80]}
    canvas.preview_color_remap = {
        tuple(map(int, child[:3])): tuple(map(int, parent[:3]))
        for child, parent in zip(colors[:64], colors[1:65])
    }

    def filter_image():
        # Compare uncached output generation, keeping the fallback's reusable
        # source index warm, as during repeated palette preview changes.
        canvas._color_filter_cache.clear()
        return canvas.filtered_layer_image(layer)

    previous = os.environ.get("PMA_NO_NATIVE")
    try:
        os.environ["PMA_NO_NATIVE"] = "1"
        numpy_ms = median_ms(filter_image)
        os.environ.pop("PMA_NO_NATIVE", None)
        rust_ms = median_ms(filter_image) if (
            native.available() and hasattr(native._native, "filter_palette")
        ) else None
    finally:
        if previous is None:
            os.environ.pop("PMA_NO_NATIVE", None)
        else:
            os.environ["PMA_NO_NATIVE"] = previous
    results["palette_filter_1920x1080_64_remaps"] = {
        "numpy_ms": numpy_ms, "rust_ms": rust_ms,
    }
    previous_size = constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT
    try:
        constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = 3840, 2160
        canvas.frames = [Frame([
            Layer(str(index), QImage(source), has_content=True)
            for index, source in enumerate(sources)
        ]) for _ in range(3)]
        canvas.onion_all_layers = True

        def toggle_with_onions(size):
            canvas.set_layer_visibility(1, not canvas.frames[0].layers[1].visible)
            # Measure a newly encountered visibility state, not a cache hit.
            canvas._onion_cache.clear()
            canvas._tinted_onion_image(0, QColor("red"), target_size=size)
            canvas._tinted_onion_image(2, QColor("blue"), target_size=size)

        results["visibility_4k_4_layers_2_onions_960x540"] = {
            "full_resolution_ms": median_ms(lambda: toggle_with_onions(None)),
            "display_resolution_ms": median_ms(lambda: toggle_with_onions((960, 540))),
        }
    finally:
        constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = previous_size
    print(json.dumps(results, indent=2))
    canvas.close()
    app.processEvents()


if __name__ == "__main__":
    main()
