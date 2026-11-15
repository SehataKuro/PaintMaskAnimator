"""取り込みの前処理としての弱いぼかし（#10、旧 #17）。

スキャンや JPEG のざらつき（1画素ごとの細かな明るさのゆれ）は、2値化すると線のふちが
ギザギザになったり、小さな点として残ったりする。トーンカーブの前に、半径 1〜2 画素
ほどの弱いガウスぼかしをかけて、ゆれだけをならす。線そのものの形はほとんど変えない。

純粋な numpy の処理（縦横に分けたガウスの重みの畳み込み）。透明度も同じにぼかすと
ふちに半透明が増えるので、RGB だけにかけ、alpha は変えない。
"""
import numpy as np

#: ぼかしの半径（σ、px）の上限。
MAX_SIGMA = 3.0


def _kernel(sigma: float) -> np.ndarray:
    radius = max(1, int(np.ceil(sigma * 3)))
    xs = np.arange(-radius, radius + 1, dtype=np.float64)
    weights = np.exp(-(xs * xs) / (2 * sigma * sigma))
    return weights / weights.sum()


def _convolve(values: np.ndarray, kernel: np.ndarray, axis: int) -> np.ndarray:
    radius = len(kernel) // 2
    pad = [(0, 0)] * values.ndim
    pad[axis] = (radius, radius)
    padded = np.pad(values, pad, mode="edge")
    out = np.zeros_like(values)
    size = values.shape[axis]
    for offset, weight in enumerate(kernel):
        index = [slice(None)] * values.ndim
        index[axis] = slice(offset, offset + size)
        out += weight * padded[tuple(index)]
    return out


def soft_blur(rgba: np.ndarray, sigma: float) -> np.ndarray:
    """RGB に半径 ``sigma`` のガウスぼかしをかけた RGBA（0 以下ならコピーを返す）。"""
    sigma = min(MAX_SIGMA, float(sigma))
    if sigma <= 0:
        return rgba.copy()
    kernel = _kernel(sigma)
    rgb = rgba[:, :, :3].astype(np.float64)
    rgb = _convolve(_convolve(rgb, kernel, 0), kernel, 1)
    out = rgba.copy()
    out[:, :, :3] = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
    return out
