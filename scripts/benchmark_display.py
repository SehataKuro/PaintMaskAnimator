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

from paintmaskanimator import native
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
    print(json.dumps(results, indent=2))
    canvas.close()
    app.processEvents()


if __name__ == "__main__":
    main()
