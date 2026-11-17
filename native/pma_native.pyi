"""PaintMaskAnimator の重い画像計算（Rust 実装）の型。

マスクは1画素1バイト（0 か 1）の行優先の並び（numpy の bool 配列の tobytes()）。
"""

__version__: str
TIMELINE_API_VERSION: int

class TimelineCell:
    has_content: bool
    is_blank_key: bool
    blank_start: bool
    sequence_number: int | None
    sequence_only: bool
    def __init__(self) -> None: ...

class SheetEntry:
    @property
    def kind(self) -> int: ...
    @property
    def number(self) -> int | None: ...
    @property
    def key(self) -> int: ...

def sheet_entries(rows: list[list[TimelineCell]]) -> list[list[SheetEntry]]: ...

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
def map_known_colors(
    rgba: bytes, codes: list[int], indices: list[int],
    palette: list[tuple[int, int, int]], line_max: int,
) -> bytes | None:
    """対応表どおりに隣接色分けの仮の色を塗る。対応のない色があれば ``None``。

    ``codes`` は元の RGB をまとめた値の昇順、``indices`` はその仮の色の番号。
    """

def region_neighbors(labels: bytes, width: int, height: int, gap: int) -> list[tuple[int, int]]:
    """``gap`` 画素以内で向かい合う領域の組（番号の小さい順）。

    ``labels`` は int32 の並び。0 の画素は領域に数えない。
    """

def surface_guided_palette_labels(
    rgb: bytes, width: int, height: int, visible: bytes, palette: bytes,
    spatial_refinement: bool,
) -> bytes:
    """画素ごとに、割り当てるパレットの番号（int16 の並び、未確定は -1）を決める。

    ``rgb`` は 1 画素 3 バイト、``visible`` は 1 画素 1 バイト、``palette`` は
    1 色 3 バイトの並び。``spatial_refinement`` が偽なら最近傍の色だけを返す。
    """

def render_quad_labels(
    labels: bytes, source_width: int, source_height: int, matrix: list[float],
    out_width: int, out_height: int, subsamples: int, coverage_labels: list[int],
) -> tuple[bytes, list[bytes]]:
    """平面四角形の逆写像で番号の画像（uint16 の並び）を変形する。

    ``matrix`` は出力座標から元座標への 3x3 を行優先で並べた 9 個。番号と、
    ``coverage_labels`` ごとの被覆（0-255 の並び）を返す。
    """

def render_mesh_labels(
    labels: bytes, source_width: int, source_height: int,
    points: list[tuple[float, float]], dense_cols: int, dense_rows: int,
    out_width: int, out_height: int, subsamples: int, band_rows: int,
    coverage_labels: list[int],
) -> tuple[bytes, list[bytes]]:
    """曲がった格子（メッシュ変形）で番号の画像（uint16 の並び）を変形する。

    ``points`` は出力座標での格子点で、``dense_cols x dense_rows`` の行優先。
    ``band_rows`` は一度に解く行数（使う記憶領域の上限）。番号と、
    ``coverage_labels`` ごとの被覆（0-255 の並び）を返す。
    """

def filter_palette(rgba: bytes, visible: list[tuple[int, int, int]] | None,
                   legacy: tuple[int, int, int] | None,
                   remap: list[tuple[tuple[int, int, int], tuple[int, int, int]]]) -> bytes: ...
def tint_onion(rgba: bytes, color: tuple[int, int, int], enabled: bool,
               selected: list[tuple[int, int, int]] | None) -> bytes: ...


def remove_line_noise(
    rgba: bytes, width: int, height: int, line_luma: int,
    radius: int, majority: int, isolation: int,
) -> tuple[bytes, int]:
    """線近傍の孤立色を多数決で置き換えた RGBA8888 と変更画素数。"""


def decode_png(data: bytes, max_bytes: int) -> tuple[int, int, bytes]:
    """PNG を幅・高さ・乗算していない RGBA8888 にする。未対応・不正・上限超過は ValueError。"""


def encode_png(rgba: bytes, width: int, height: int, dots_per_meter_x: int, dots_per_meter_y: int) -> bytes:
    """乗算していない RGBA8888 を PNG にし、失敗は ValueError。"""


class ZipReader:
    """ZIP を GIL を手放して開き、宣言サイズ内で読み取る。"""
    def __init__(self, path: str) -> None: ...
    def entries(self) -> list[tuple[str, int, int]]: ...
    def read(self, index: int) -> bytes: ...
    def close(self) -> None: ...


def decode_v2_plan(payload: dict, archive_names: list[str], limits: tuple[int, int, int] | None = None) -> tuple[dict, list[list], str | None]:
    """v2 の構造計画、要求順の画像、構造エラーの理由を返す。"""


class ZipWriter:
    def __init__(self, path: str) -> None: ...
    def write(self, name: str, data: bytes, deflate: bool, date_time: tuple[int, int, int, int, int, int]) -> None: ...
    def close(self) -> None: ...


def extend_line_ends(boundary: bytes, width: int, height: int,
                     strips: list[tuple[int, int, int, int, bytes]], max_length: int) -> bytes:
    """細線化済みの帯から端点の延長マスクを返す。"""
    ...

def fill_target_mask(rgba: bytes, width: int, height: int, target_color: str,
                     category: tuple[int, int, int] | None = None) -> bytes: ...

class LineCache:
    def __init__(self) -> None: ...

class FillSession:
    def __init__(self, rgba: bytes, width: int, height: int, options: tuple, indexed: bool = False) -> None: ...
    def compute(self, x: int, y: int) -> tuple: ...
    def prepared(self, category: tuple[int, int, int] | None) -> tuple: ...
    def index(self, category: tuple[int, int, int] | None) -> tuple[bytes, bytes]: ...
    def background(self) -> bytes: ...
    def categories(self) -> list: ...
    def nbytes(self) -> int: ...

def compute_fill_region_once(rgba: bytes, width: int, height: int, x: int, y: int,
                             options: tuple, selection: bytes | None = None,
                             line_cache: LineCache | None = None) -> tuple: ...
