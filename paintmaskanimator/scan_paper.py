"""スキャンした紙の地色とムラを白へそろえる（#51）。

紙の白は #FFFFFF ではない。用紙はクリーム寄りで、スキャナーでも変わり、照明ムラ・
影・折り目で1枚の中でも明るさが連続して変わる。1つの背景色としきい値では、濃い側が
残り薄い側が抜けすぎる。

そこで、場所ごとの紙の色を見積もり、その色で割って白にそろえる（フラットフィールド
補正）。紙の色は「まわりで一番明るい色」とする。線は紙より暗いので、ブロックごとに
明るい側の色を取れば、線を避けて紙だけを拾える。ブロックの値をならしてから元の大きさへ
なめらかに広げるので、ムラや影の傾きにもついていく。

線の色（鉛筆の濃淡・色鉛筆の色味）は、紙の色との比のまま残る。白へそろえたあとは、
今までの2値化（トーンカーブ・ライン抽出）がそのまま使える。純粋な numpy の処理。
"""
import numpy as np

#: 紙の色を見積もるブロックの一辺（px）。線の太さより十分大きくする。
BLOCK = 24
#: ブロックの中で紙とみなす明るさの順位（上位から）。1.0 なら最大値。ゴミの白点に
#: 引っぱられないよう、少し下の順位を使う。
PAPER_QUANTILE = 0.9
#: 紙の色をこの数のブロックでならす（半径）。
SMOOTH_RADIUS = 2
#: これより暗い所は紙ではない（タップ穴・黒べたなど）。まわりの紙の色で埋めて見積もる。
MIN_PAPER = 96


def _blur_blocks(values: np.ndarray, radius: int) -> np.ndarray:
    """ブロック単位の配列を、半径 ``radius`` の箱型でならす（端は縁の値で延ばす）。"""
    if radius <= 0:
        return values
    padded = np.pad(values, ((radius, radius), (radius, radius), (0, 0)), mode="edge")
    out = np.zeros_like(values)
    size = 2 * radius + 1
    for dy in range(size):
        for dx in range(size):
            out += padded[dy:dy + values.shape[0], dx:dx + values.shape[1]]
    return out / (size * size)


def _upsample(blocks: np.ndarray, height: int, width: int, block: int) -> np.ndarray:
    """ブロックの中心を結ぶ双一次補間で、画素ごとの値へ広げる。"""
    rows, cols = blocks.shape[:2]
    ys = (np.arange(height) + 0.5) / block - 0.5
    xs = (np.arange(width) + 0.5) / block - 0.5
    y0 = np.clip(np.floor(ys).astype(int), 0, rows - 1)
    x0 = np.clip(np.floor(xs).astype(int), 0, cols - 1)
    y1 = np.clip(y0 + 1, 0, rows - 1)
    x1 = np.clip(x0 + 1, 0, cols - 1)
    wy = np.clip(ys - np.floor(ys), 0, 1)[:, None, None]
    wx = np.clip(xs - np.floor(xs), 0, 1)[None, :, None]
    top = blocks[y0][:, x0] * (1 - wx) + blocks[y0][:, x1] * wx
    bottom = blocks[y1][:, x0] * (1 - wx) + blocks[y1][:, x1] * wx
    return top * (1 - wy) + bottom * wy


def paper_colour(rgb: np.ndarray, block: int = BLOCK) -> np.ndarray:
    """画素ごとの紙の色の見積もり（float、0〜255）。"""
    height, width = rgb.shape[:2]
    rows, cols = -(-height // block), -(-width // block)
    padded = np.pad(
        rgb.astype(np.float64),
        ((0, rows * block - height), (0, cols * block - width), (0, 0)),
        mode="edge",
    )
    tiles = padded.reshape(rows, block, cols, block, 3).transpose(0, 2, 1, 3, 4)
    tiles = tiles.reshape(rows, cols, block * block, 3)
    brightness = tiles.sum(axis=3)
    order = np.argsort(brightness, axis=2)
    pick = order[:, :, int(round((block * block - 1) * PAPER_QUANTILE))]
    blocks = np.take_along_axis(tiles, pick[:, :, None, None], axis=2)[:, :, 0, :]
    # 暗すぎるブロック（紙が見えていない所）は、紙のブロックの平均で埋める。
    dark = blocks.min(axis=2) < MIN_PAPER
    if dark.all():
        return np.full(rgb.shape, 255.0)
    if dark.any():
        blocks[dark] = blocks[~dark].mean(axis=0)
    blocks = _blur_blocks(blocks, SMOOTH_RADIUS)
    return _upsample(blocks, height, width, block)


def flatten_paper(rgba: np.ndarray, block: int = BLOCK) -> np.ndarray:
    """紙の地色とムラを白（255）へそろえた RGBA。透明度は変えない。"""
    out = rgba.copy()
    paper = np.maximum(paper_colour(rgba[:, :, :3], block), 1.0)
    flattened = rgba[:, :, :3].astype(np.float64) * (255.0 / paper)
    out[:, :, :3] = np.clip(np.rint(flattened), 0, 255).astype(np.uint8)
    return out
