"""別の窓に出す台本の写し（窓ごとの PNG と frames.jsonl）を、画面の上の位置に並べて動画の各フレームにする。

画面そのものは撮らない（ほかのアプリを写さない）。各窓が自分で撮った中身を、記録した位置に置き、
題名の帯・指先・説明だけをここで描く。

使い方：
    .venv/bin/python prototypes/bench/compose_popout.py <台本の出力> <出力の dir>
    （画面の境目を描くために、合成する Mac の画面の一覧を読む。録画した Mac と同じ画面の並びのときだけ正しい。窓は出さない）
    ffmpeg -framerate 30 -i <出力の dir>/f_%05d.png -pix_fmt yuv420p out.mp4
"""
import json
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPen


def main(src, dst):
    QGuiApplication.instance() or QGuiApplication([])
    src, dst = Path(src), Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    frames = [json.loads(line) for line in (src / "frames.jsonl").read_text().splitlines() if line.strip()]
    # 外枠は NSWindow の frame（題名の帯を含む）。egui は中身がその下にある。Tauri（tao）は macOS で
    # full-size content view を使うので、中身にも外枠と同じ位置を返し、WKWebView も窓全体に広がる。
    # ただし WebKit がページを題名の帯（32pt）の下から並べる（窓だけを撮った写しで確認）ので、
    # 写しの上から（高さ − 32pt）ぶんを帯の下に置く。
    for f in frames:
        for w in f["windows"]:
            if abs(w["outer"][1] - w["inner"][1]) < 1 and abs(w["outer"][3] - w["inner"][3]) < 1:
                w["bar"] = 32.0
            else:
                w["bar"] = w["inner"][1] - w["outer"][1]
    xs, ys = [], []
    for f in frames:
        for w in f["windows"]:
            x, y, width, height = w["outer"]
            xs += [x, x + width]
            ys += [y, y + height]
    margin = 30
    left, top = min(xs) - margin, min(ys) - margin
    width = int(max(xs) - left + margin) // 2 * 2
    height = int(max(ys) - top + margin + 40) // 2 * 2
    # 画面（モニター）の境目と名前を描く（QT_QPA_PLATFORM=offscreen のときは画面が取れないので描かない）。
    screens = [(sc.name(), sc.geometry()) for sc in QGuiApplication.screens()]
    latest = {}
    font = QFont("Hiragino Sans", 13)
    caption_font = QFont("Hiragino Sans", 17)
    for f in frames:
        n = f["n"]
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(QColor(54, 60, 72))
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor(150, 160, 175))
        p.setFont(font)
        for name, g in screens:
            r = QRectF(g.x() - left, g.y() - top, g.width(), g.height())
            p.setPen(QPen(QColor(120, 130, 150), 2, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            p.setPen(QColor(150, 160, 175))
            p.drawText(QPointF(r.x() + 12, r.bottom() - 10), f"画面：{name}")
        p.setPen(QColor(150, 160, 175))
        p.drawText(QPointF(12, 22), "デスクトップ（各窓の自前の写しを、OS の窓の位置に並べた合成）")
        for w in f["windows"]:
            label = w["label"]
            shot = src / f"{label}_{n:05d}.png"
            if shot.exists():
                latest[label] = QImage(str(shot))
            ox, oy, ow, oh = w["outer"]
            ix, iy, iw, ih = w["inner"]
            outer = QRectF(ox - left, oy - top, ow, oh)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawRoundedRect(outer.translated(0, 6), 10, 10)
            p.setBrush(QColor(42, 42, 46))
            p.drawRoundedRect(outer, 10, 10)
            content = latest.get(label)
            if content is not None:
                if abs(iy - oy) < 1:  # Tauri：写しの上の部分を帯の下へ
                    scale = content.height() / ih
                    source = QRectF(0, 0, content.width(), (ih - w["bar"]) * scale)
                    p.drawImage(QRectF(ix - left, iy - top + w["bar"], iw, ih - w["bar"]), content, source)
                else:
                    p.drawImage(QRectF(ix - left, iy - top, iw, ih), content)
            bar = QRectF(outer.x(), outer.y(), outer.width(), w["bar"])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(42, 42, 46))
            p.drawRect(bar)
            p.setPen(QColor(220, 220, 225))
            p.setFont(font)
            p.drawText(bar, Qt.AlignmentFlag.AlignCenter, w["title"])
            for i, color in enumerate([(255, 95, 87), (254, 188, 46), (40, 200, 64)]):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(*color))
                p.drawEllipse(QPointF(outer.x() + 18 + i * 20, bar.center().y()), 6, 6)
        x, y = f["pointer"]
        x, y = x - left, y - top
        arrow = QPainterPath(QPointF(x, y))
        for dx, dy in [(0, 18), (4.5, 14), (8, 21), (10.5, 20), (7, 13), (12.5, 13)]:
            arrow.lineTo(x + dx, y + dy)
        arrow.closeSubpath()
        p.setPen(QPen(QColor("black"), 1.2))
        p.setBrush(QColor(255, 210, 60) if f["dragging"] else QColor("white"))
        p.drawPath(arrow)
        band = QRectF(0, height - 40, width, 40)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 210))
        p.drawRect(band)
        p.setPen(QColor("white"))
        p.setFont(caption_font)
        p.drawText(band.adjusted(14, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, f["caption"])
        p.end()
        image.save(str(dst / f"f_{n:05d}.png"))
    print(f"{len(frames)} フレーム（{width}×{height}）を {dst} に保存")


if __name__ == "__main__":
    main(*sys.argv[1:3])
