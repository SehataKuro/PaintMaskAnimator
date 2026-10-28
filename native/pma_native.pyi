"""PaintMaskAnimator の重い画像計算（Rust 実装）の型。

マスクは1画素1バイト（0 か 1）の行優先の並び（numpy の bool 配列の tobytes()）。
"""

__version__: str

class TimelineCell:
    has_content: bool
    is_blank_key: bool
    exposure: int
    sequence_number: int | None
    sequence_only: bool
    cell_name: str | None
    def __init__(self) -> None: ...

def resolve_timeline(rows: list[list[TimelineCell]]) -> bytes: ...

class ClipAnimationReader:
    def __init__(self, path: str, max_dimension: int, max_raster_bytes: int) -> None: ...
    def metadata(self) -> dict: ...
    def raster(self, mipmap_id: int) -> tuple[int, int, int, bool, bool, bytes]: ...

class GuideSampler:
    def __init__(self, values: bytes, labels: bytes, flags: bytes, sizes: bytes,
                 width: int, height: int) -> None: ...
    def sample(self, runs: bytes, dy: int, dx: int, ignore: int, require: int,
               by_label: bool) -> tuple[int, int, int | None, int, int, bool, bool, int, int]: ...

def connect_runs(starts: bytes, ends: bytes, kinds: bytes, rows: bytes) -> tuple[bytes, int]: ...

def label_runs(data: bytes, width: int, height: int, itemsize: int
               ) -> tuple[bytes, int, bytes, bytes, bytes, bytes]: ...

def refine_face(
    values: bytes, labels: bytes, usable: bytes, excluded: bytes, sizes: bytes,
    points: bytes, width: int, height: int, radius: int,
    thresholds: tuple[float, float, float, float],
) -> list[tuple[int, float]]: ...

def thin(mask: bytes, width: int, height: int, iterations: int) -> bytes:
    """Zhang-Suen 細線化。線端の判定に使い、描画画像自体は変えない。"""

def line_flow_scores(
    target: bytes, guide: bytes, width: int, height: int, block: int,
    base_y: bytes, base_x: bytes, reach: int,
) -> bytes:
    """区画のずれごとの一致数（候補・行・列順の uint32）。base は int64。"""

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
def unique_colors(pixels: bytes, limit: int) -> list[int]: ...
def resolve_exposures(states: bytes, exposures: bytes, frames: int, layers: int) -> bytes: ...
def filter_palette(rgba: bytes, visible: list[tuple[int, int, int]] | None,
                   legacy: tuple[int, int, int] | None,
                   remap: list[tuple[tuple[int, int, int], tuple[int, int, int]]]) -> bytes: ...
def tint_onion(rgba: bytes, color: tuple[int, int, int], enabled: bool,
               selected: list[tuple[int, int, int]] | None) -> bytes: ...
