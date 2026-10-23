"""PaintMaskAnimator の重い画像計算（Rust 実装）の型。

マスクは1画素1バイト（0 か 1）の行優先の並び（numpy の bool 配列の tobytes()）。
"""

__version__: str

def flood_fill(
    passable: bytes, width: int, height: int, seeds: list[tuple[int, int]]
) -> bytes:
    """4近傍で ``seeds`` からつながる ``passable`` の領域。"""

def dilate(mask: bytes, width: int, height: int, iterations: int) -> bytes:
    """8近傍で ``iterations`` 回膨張させる。"""

def erode(mask: bytes, width: int, height: int, iterations: int) -> bytes:
    """8近傍で ``iterations`` 回収縮させる（範囲外は偽）。"""
