"""連番を本体にする移行（Issue #31）の基準値。

この移行は**速くするためのものではない**（docs/sequence-first-data-model.md §1）。
重複処理は frame_scope が既に畳んでいる。ここで測るのは「遅くなっていない」
ことを確かめるための基準値で、移行の各段階のあとに取り直して比べる。

Run: python -m scripts.benchmark_sequence_model
"""
import json
import os
import statistics
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import constants  # noqa: E402
from paintmaskanimator.canvas import PaintCanvas  # noqa: E402
from paintmaskanimator.frame_scope import FrameScope, resolve_cells  # noqa: E402
from paintmaskanimator.models import Frame, Layer  # noqa: E402

#: 測るときのカットの大きさ。長めのカットを1本。
FRAMES = 600
LAYERS = 4
#: 絵の枚数。同じ番号が何コマにも出る状態にする。
DRAWINGS = 60


def median_ms(function, repeats=7):
    function()
    values = []
    for _ in range(repeats):
        start = time.perf_counter()
        function()
        values.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(values), 3)


def build_canvas():
    """``DRAWINGS`` 枚の絵が ``FRAMES`` コマに繰り返し出るシートを作る。"""
    canvas = PaintCanvas()
    image = QImage(
        constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(QColor(0, 0, 0, 0))

    frames = []
    for column in range(FRAMES):
        layers = []
        for layer_index in range(LAYERS):
            layer = Layer(chr(ord("A") + layer_index), QImage(image))
            # 10コマごとに次の絵へ移り、番号は DRAWINGS で一周する。
            if column % 10 == 0:
                layer.has_content = True
                layer.exposure = 10
                layer.sequence_number = (column // 10) % DRAWINGS + 1
            layers.append(layer)
        frames.append(Frame(layers))
    canvas.frames = frames
    canvas.current_frame = 0
    canvas.active_layer_index = 0
    return canvas


def main():
    app = QApplication.instance() or QApplication([])
    canvas = build_canvas()
    all_layers = tuple(range(LAYERS))
    whole_cut = FrameScope(
        frames=tuple(range(FRAMES)), layers=all_layers, key_frames_only=True
    )

    def coalesce():
        canvas.coalesce_numbered_images()

    def resolve_whole_cut():
        resolve_cells(canvas, whole_cut)

    def resolve_exposures():
        canvas._invalidate_exposure_index()
        for column in range(0, FRAMES, 7):
            for layer_index in range(LAYERS):
                canvas.resolve_key_frame(column, layer_index)

    def seek():
        for column in range(0, FRAMES, 13):
            canvas.current_frame = column
            canvas.resolve_key_frame(column, 0)

    results = {
        "shape": {
            "frames": FRAMES,
            "layers": LAYERS,
            "drawings_per_layer": DRAWINGS,
            "canvas": [constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT],
        },
        # 同じ番号へ同じ画像を結び直す。連番が本体になれば不要になる処理。
        "coalesce_numbered_images_ms": median_ms(coalesce),
        # 一括処理の対象セルを決める。すでに絵ごと1回へ畳んでいる。
        "resolve_cells_whole_cut_ms": median_ms(resolve_whole_cut),
        # 露出の解決（Rust 側）。exposure を「同じ番号の連続」へ変える影響先。
        "resolve_key_frame_cold_ms": median_ms(resolve_exposures),
        "seek_every_13th_frame_ms": median_ms(seek),
        "cells_in_whole_cut": len(resolve_cells(canvas, whole_cut)),
    }
    print(json.dumps(results, indent=2))
    canvas.close()
    app.processEvents()


if __name__ == "__main__":
    main()
