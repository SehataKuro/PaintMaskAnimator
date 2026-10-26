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

def label_regions(codes: bytes, width: int, height: int) -> tuple[bytes, int]:
    """同じ値で4近傍につながった画素に1からの番号を付ける（uint32 の並び）。

    ``codes`` は uint32 の並びで、``0xFFFFFFFF`` の画素は番号 0。
    番号は各領域の最初の画素（行優先）が現れた順。``(番号, 領域の数)`` を返す。
    """
