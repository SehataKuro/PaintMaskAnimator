"""PaintMaskAnimator の重い画像計算（Rust 実装）の型。

マスクは1画素1バイト（0 か 1）の行優先の並び（numpy の bool 配列の tobytes()）。
"""

import os
from typing import Any

__version__: str
TIMELINE_API_VERSION: int

class RgbaImage:
    def __init__(self, width: int, height: int, pixels: bytes) -> None: ...
    @staticmethod
    def blank(width: int, height: int) -> RgbaImage: ...
    @property
    def width(self) -> int: ...
    @property
    def height(self) -> int: ...
    @property
    def generation(self) -> int: ...
    def copy(self) -> RgbaImage: ...
    def rgba_bytes(self) -> bytes: ...
    def read_rect(self, x: int, y: int, width: int, height: int) -> bytes: ...
    def write_rect(self, x: int, y: int, width: int, height: int, pixels: bytes) -> None: ...
    @property
    def tag(self) -> int: ...
    def with_tag(self, tag: int) -> RgbaImage: ...

class _CoreDocument:
    """Python の文書窓口が所有するコア文書。窓口が多いので、ここでは型を Any にする。"""

    @staticmethod
    def from_graph(graph_json: str, images: list[RgbaImage]) -> _CoreDocument: ...
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
    def __getattr__(self, name: str) -> Any: ...

class _CellImage:
    """文書のセルの画像を指す窓口。RgbaImage と同じ読み書きの口を持つ。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
    def __getattr__(self, name: str) -> Any: ...

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

def reference_color_stats(
    rgba: bytes, width: int,
) -> list[tuple[int, int, tuple[int, int, int, int]]]: ...

def reference_line_art(rgba: bytes, line_max: int) -> bytearray: ...

def reference_region_stats(
    runs: tuple[bytes, bytes, bytes, bytes], count: int, width: int, height: int,
) -> list[tuple[int, tuple[int, int, int, int], bool]]: ...

def reference_region_under(
    labels: bytes, lines: bytes, shape: tuple[int, int], mask: bytes,
    bounds: tuple[int, int, int, int],
) -> tuple[int | None, float]: ...

_TileRect = tuple[int, int, int, int]
_TileWindow = tuple[_TileRect, bytes]
_TileInput = tuple[
    _TileRect, bytes, bytes, _TileRect, tuple[int, int, int, int],
    _TileWindow, _TileWindow, tuple[bytes, bytes], tuple[bytes, bytes, bytes, bytes, bytes],
    bytes | None, bytes, bytes,
]
def tiled_part_color(
    tiles: list[_TileInput], local: _TileWindow, size: int, sizes: bytes,
) -> tuple[int, int, int] | None: ...

_FlowCoordinates = tuple[tuple[bytes, bytes, bytes, bytes], bytes]
_FlowOutline = tuple[
    _TileWindow, _FlowCoordinates, _TileWindow, tuple[bytes, bytes],
    _TileRect, tuple[bytes, bytes], _TileWindow,
]
_FlowInterior = tuple[
    _TileWindow, _FlowCoordinates, _TileRect, tuple[bytes, bytes, bytes, bytes, bytes],
    bytes | None, bytes, bytes,
]

def flow_outline_matches(outline: _FlowOutline, part: _TileRect) -> bool: ...

def flow_part_color(
    interior: _FlowInterior, sizes: bytes,
) -> tuple[int, int, int] | None: ...

def refine_face(
    values: bytes, labels: bytes, usable: bytes, excluded: bytes, sizes: bytes,
    points: bytes, width: int, height: int, radius: int,
    thresholds: tuple[float, float, float, float],
) -> list[tuple[int, float]]: ...

def thin(mask: bytes, width: int, height: int, iterations: int) -> bytes:
    """Zhang-Suen 細線化。線端の判定に使い、描画画像自体は変えない。"""

def line_flow_interpolate(
    dy: bytes, dx: bytes, width: int, height: int, block: int, ys: bytes, xs: bytes,
) -> tuple[bytes, bytes] | None: ...

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
def local_color_variation(rgb: bytes, width: int, height: int) -> bytes: ...
def median_cut_palette_from_samples(sample: bytes, color_count: int) -> bytes: ...
def normalize_tone_curve_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]: ...
def tone_curve_samples(points: list[tuple[float, float]], values: bytes) -> bytes | None: ...
def tone_curve_lut(points: list[tuple[float, float]]) -> bytes: ...
def apply_tone_curve(
    rgba: bytes, width: int, height: int, points: list[tuple[float, float]],
    background: tuple[int, int, int] | None = None,
) -> bytes: ...
def tone_preview_background_mask(
    rgba: bytes, width: int, height: int, background: tuple[int, int, int],
) -> bytes: ...
def apply_tone_preview_lut(
    rgba: bytes, width: int, height: int, lut: bytes,
    background: tuple[int, int, int] | None, mask: bytes | None,
) -> bytes: ...
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


def alpha_runs(data: bytes, width: int, height: int, stride: int, alpha_offset: int) -> list[tuple[int, int, int]]:
    """1画素4バイトの画像で、``alpha_offset`` 番目のバイトが 0 でない横の並び ``(y, left, right)``。"""


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


def _open_project(data: bytes) -> tuple[_CoreDocument, str, int, int, int, dict[int, bytes]]:
    """純 Rust で .pman を開く（文書, metadata の JSON, 幅, 高さ, 形式, {世代: PNG}）。

    失敗は ValueError((種類, 文言))。
    """


def _save_project(
    document: _CoreDocument,
    metadata_json: str,
    date_time: tuple[int, int, int, int, int, int],
    thumbnail: bytes | None = None,
    dots_per_meter: tuple[int, int] | None = None,
    cache: dict[int, bytes] | None = None,
) -> tuple[bytes, dict[int, bytes]]:
    """純 Rust で文書を .pman のバイト列にする（バイト列, 残した {世代: PNG}）。

    失敗は ValueError((種類, 文言))。
    """


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
    def __init__(self, rgba: bytes, width: int, height: int, options: _FillOptions, indexed: bool = False) -> None: ...
    def compute(self, x: int, y: int) -> _FillOutput: ...
    def prepared(self, category: tuple[int, int, int] | None) -> tuple: ...
    def index(self, category: tuple[int, int, int] | None) -> tuple[bytes, bytes]: ...
    def background(self) -> bytes: ...
    def categories(self) -> list: ...
    def nbytes(self) -> int: ...

def compute_fill_region_once(rgba: bytes, width: int, height: int, x: int, y: int,
                             options: _FillOptions, selection: bytes | None = None,
                             line_cache: LineCache | None = None) -> _FillOutput: ...


def estimate_shifts(
    previous: bytes, current: bytes, region: bytes, width: int, height: int,
    count: int, margin: int | None,
) -> list[tuple[int, int]]: ...
def warp_region(
    previous: bytes, current: bytes, region: bytes, width: int, height: int,
    shift: tuple[int, int], origin: tuple[int, int] = (0, 0),
) -> bytes | None: ...
def estimate_color_shifts(
    previous: bytes, current: bytes, region: bytes, before: tuple[int, int],
    after: tuple[int, int], offset: tuple[int, int], count: int,
) -> list[tuple[int, int]]: ...
def tracking_region_center(region: bytes, width: int, height: int) -> tuple[int, int] | None: ...
def context_similarity(
    before: tuple[bytes, bytes, tuple[int, int], tuple[int, int]],
    after: tuple[bytes, bytes, tuple[int, int], tuple[int, int]],
) -> float: ...

_Rgb = tuple[int, int, int]
_Bounds = tuple[int, int, int, int]
_Replacement = tuple[_Rgb, _Rgb]
_Island = tuple[_Rgb, _Bounds, bytes]
_FillOptions = tuple[str, bool, int, bool, int, bool, bool, int, bool, bool,
                     tuple[_Rgb, ...], tuple[_Replacement, ...]]
_FillOutput = tuple[str, _Bounds | None, bytes | None, _Rgb | None, list[_Island]]

def enclosed_by(region: bytes, width: int, height: int) -> bytes: ...
def enclosed_islands(
    rgba: bytes, dimensions: tuple[int, int], region: bytes, bounds: _Bounds,
    replacements: tuple[_Replacement, ...], min_share: float, selection: bytes | None = None,
) -> list[_Island]: ...
def guess_target_categories(
    rgba: bytes, previous: bytes, before: bytes, width: int, height: int,
) -> list[_Rgb | None]: ...
def skewer_seed(
    rgba: bytes, width: int, height: int, reference: _Rgb | None,
) -> tuple[int, int] | None: ...

def track_fill_region(
    rgba: bytes, previous: bytes, width: int, height: int,
    reference: tuple[int, int, int] | None, options: _FillOptions,
    selection: bytes | None = ..., previous_rgba: bytes | None = ...,
    previous_lines: bytes | None = ...,
) -> tuple[float | None, list[_FillOutput], tuple[int, int] | None, bytes | None]: ...


def image_alpha_statistics(rgba: bytes, width: int, height: int) -> tuple[int, int, int]: ...


def binarize_alpha_for_pixel_art(rgba: bytes, width: int, height: int, threshold: int) -> bytes: ...


def opaque_rgb_color_count(rgba: bytes, width: int, height: int) -> int: ...


def border_background(border: bytes) -> tuple[int, int, int] | None: ...


def estimate_mixed_boundary_pixels(rgba: bytes, width: int, height: int, background: tuple[int, int, int] | None = None) -> int: ...


def snap_overlay_to_exact_rgbs(rgba: bytes, width: int, height: int, mask: bytes, palette: bytes) -> bytes: ...

def palette_color_counts(sample: bytes) -> tuple[bytes, bytes]: ...
def hue_cluster_palette(colors: bytes, features: bytes, weights: bytes, log_weights: bytes, color_count: int) -> bytes: ...
def palette_training_samples(rgba: bytes, width: int, height: int, threshold: int, opaque: bool, background: tuple[int, int, int] | None) -> bytes: ...
def select_palette_colors(training: bytes, priority: bytes, clustered: bytes, color_count: int, line: bool, background: tuple[int, int, int] | None) -> bytes: ...
def line_palette_labels(rgb: bytes, width: int, height: int, visible: bytes, palette: bytes) -> bytes: ...
def apply_color_reduction_palette(rgba: bytes, width: int, height: int, palette: bytes, threshold: int, opaque: bool, background: tuple[int, int, int] | None, points: list[tuple[float, float]] | None, line: bool) -> bytes: ...

def prepare_local_shift(
    target: bytes, target_shape: tuple[int, int],
    wide: bytes, wide_shape: tuple[int, int],
    offset: tuple[int, int], limit: int,
) -> tuple[bytes, tuple[int, int], float, list[int], list[int], list[int], list[int]] | None: ...

def solve_local_shift(
    before: bytes, exact: bytes, wide: bytes, width: int, height: int,
    fast_size: tuple[int, int], dys: list[int], dxs: list[int], total: float,
    step: int, motion_penalty: float, near_line_weight: float, share_motion: bool,
) -> tuple[tuple[int, int], float, float]: ...


def splat_mask_labels(
    labels: bytes,
    source_width: int,
    source_height: int,
    out_width: int,
    out_height: int,
    protect_labels: list[int],
    matrix: list[float] | None,
    grid: tuple[int, int, list[tuple[float, float]]] | None,
) -> list[bytes]: ...

def mask_labels_to_rgba(
    labels: bytes, width: int, height: int, palette: bytes, transparent_white: bool,
) -> bytes: ...


def history_trim_count(entry_bytes: list[int], max_entries: int, max_bytes: int) -> int:
    """Undo 列の古い側から捨てる件数（件数→論理バイトの順、最低1件は残す）。"""

def history_shift_branch_positions(positions: list[int], removed: int) -> list[int | None]:
    """古い側が removed 件削られたあとの分岐点。捨てる分岐は None。"""

def history_branch_overflow(count: int, max_branches: int) -> int:
    """分岐を足したあと、上限を超えて古い側から捨てる本数（上限 0 なら捨てない）。"""

def history_switch_steps(position: int, undo_len: int) -> int | None:
    """分岐へ切り替えるときに戻す手数。到達できない分岐は None。"""

def history_retained_positions(keep: list[bool], positions: list[int]) -> list[int]:
    """一部の件を除いたあとの分岐点（分岐点より前に残る件の数）。"""

def history_branch_columns(branches: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """ヒストリーパネルで分岐を置く列。並べた順に (一覧の添字, 列)。"""


# キャンバスのレイヤー合成（pma-core の compose）。画素は既定で乗算済みの RGBA
# （R, G, B, A の順）、straight=True ならストレート。戻り値の末尾は (幅, 高さ, 画素)。
# options のキー: active_layer (int), visible_colors (list[RGB] | None),
#   color_remap (list[tuple[RGB, RGB]]), silhouette (bool)。
# view のキー: show_initial_state (bool), exposure (float), channel ("rgb"・"red"・
#   "green"・"blue"・"alpha"), map_colors (bool), palette (list[RGB])。
# mapping は隣接色分けの対応 {0xRRGGBB: 仮の色の番号}。増えた分を書き戻す。
# onion のキー: previous・next（count, opacity, color, color_enabled, levels, shift_x,
#   shift_y, rotation, scale）, all_layers, selected_colors_only, selected_colors, tu_tb_scale。
# background は "transparent"・"white"・(R, G, B)。
_RGB = tuple[int, int, int]
_Rendered = tuple[int, int, bytes]
_RenderedLayer = tuple[int, int, float, int, int, int, int, bytes]
_Background = str | _RGB | None

def compose_frame(
    document: _CoreDocument, frame: int, options: dict[str, Any] | None = None,
    background: _Background = None, rect: tuple[int, int, int, int] | None = None,
    straight: bool = False,
) -> _Rendered: ...
def compose_layer(
    document: _CoreDocument, frame: int, layer: int, options: dict[str, Any] | None = None,
    straight: bool = False,
) -> _RenderedLayer | None: ...
def compose_view_layers(
    document: _CoreDocument, frame: int, options: dict[str, Any] | None = None,
    view: dict[str, Any] | None = None, mapping: dict[int, int] | None = None,
    straight: bool = False,
) -> list[_RenderedLayer]: ...
def compose_view(
    document: _CoreDocument, frame: int, options: dict[str, Any] | None = None,
    view: dict[str, Any] | None = None, mapping: dict[int, int] | None = None,
    background: _Background = None, rect: tuple[int, int, int, int] | None = None,
    straight: bool = False,
) -> _Rendered: ...
def compose_display(
    document: _CoreDocument, frame: int, width: int, height: int,
    options: dict[str, Any] | None = None, view: dict[str, Any] | None = None,
    mapping: dict[int, int] | None = None, background: _Background = None,
    mode: str = "nearest", flip_horizontal: bool = False, flip_vertical: bool = False,
    onion: dict[str, Any] | None = None, straight: bool = False,
) -> _Rendered: ...
def compose_onion_draws(
    document: _CoreDocument, current: int, onion: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> list[tuple[int, int, int, float]]: ...
def compose_tinted_onion(
    document: _CoreDocument, frame: int, color: _RGB, color_enabled: bool,
    selected: list[_RGB] | None = None, all_layers: bool = True,
    target_size: tuple[int, int] | None = None, options: dict[str, Any] | None = None,
    straight: bool = False,
) -> _Rendered: ...
def compose_scale_for_display(
    width: int, height: int, pixels: bytes, target_width: int, target_height: int,
    mode: str = "nearest", flip_horizontal: bool = False, flip_vertical: bool = False,
    straight: bool = False,
) -> _Rendered: ...


# ブラシ・図形の線（pma_core::brush）。Qt の経路との突き合わせに使う。
# 経路の要素は [0, x, y]（moveTo）・[1, x, y]（lineTo）・[2, x1, y1, x2, y2, x, y]（cubicTo）・[3]（閉じる）。
_Rect = tuple[int, int, int, int]

class BrushStroke:
    """押してから離すまでのブラシ・消しゴムのストローク。"""

    @staticmethod
    def begin(
        layer: RgbaImage, x: float, y: float, pressure: float | None = None, time_ms: float = 0.0, *,
        size: float, color: tuple[int, int, int], eraser: bool = False,
        pressure_enabled: bool = False, pressure_min: float = 0.1, pressure_max: float = 1.0,
        pressure_points: list[tuple[float, float]] = ..., pressure_exponent: float = 1.0,
        stabilizer: int = 0, zoom: float = 1.0, alpha_lock: bool = False,
        mask_colors: list[tuple[int, int, int]] | None = None, mask_background: bool = False,
        selection_polygon: list[tuple[float, float]] | None = None, selection_mask: bytes | None = None,
    ) -> tuple[BrushStroke, list[_Rect]]:
        """押した点を描き、(ストローク, 画素が変わりうる矩形) を返す。"""
    def move_to(self, x: float, y: float, pressure: float | None = None, time_ms: float = 0.0) -> list[_Rect]: ...
    def tick(self) -> list[_Rect]: ...
    def advance_time(self, now_ms: float) -> list[_Rect]: ...
    def image(self) -> RgbaImage: ...
    @property
    def dirty_rect(self) -> _Rect | None: ...
    def finish(
        self, x: float | None = None, y: float | None = None, pressure: float | None = None,
        time_ms: float = 0.0,
    ) -> tuple[RgbaImage, tuple[_Rect, bytes, bytes] | None, list[tuple[int, int, int]]]:
        """(画像, (矩形, 前の画素, 後の画素) | None, 塗った色)。"""

def brush_segment_mask(
    start: tuple[float, float], end: tuple[float, float], pen_width: float, width: int, height: int,
) -> bytes:
    """paint_segment の画素（width × height の 0/1）。"""

def brush_stamp_mask(center: tuple[float, float], width: float) -> tuple[int, int, int, bytes]:
    """stamp_mask の (x, y, 一辺, 0/1 の並び)。"""

def fill_polygon_mask(polygon: list[tuple[float, float]], width: int, height: int, winding: bool = False) -> bytes: ...
def fill_path_mask(path: list[list[float]], width: int, height: int, winding: bool = False) -> bytes: ...
def stroke_path_mask(path: list[list[float]], pen_width: float, width: int, height: int) -> bytes | None: ...
def ellipse_path(rect: tuple[float, float, float, float]) -> list[list[float]]: ...
def pressure_bezier_at(points: list[tuple[float, float]], value: float) -> float: ...
def pressure_bezier_segments(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]: ...
def stabilizer_segments(
    strength: int, zoom: float, events: list[tuple[str, float, float, float | None]],
) -> list[tuple[float, float, float, float, float]]:
    """("press"|"move"|"tick"|"release", x, y, 筆圧 | None) を描く線分 (x1, y1, x2, y2, 筆圧) にする。"""
# 画素の選択範囲（pma-core の selection）。矩形は (x, y, 幅, 高さ)。
_Point = tuple[float, float]
_Rect = tuple[int, int, int, int]
_Edited = tuple[RgbaImage, _Rect, bytes]

class Selection:
    """1つの選択範囲（多角形かマスク）。"""

    @staticmethod
    def polygon(points: list[_Point]) -> Selection | None: ...
    @staticmethod
    def rect(start: _Point, end: _Point) -> Selection: ...
    @staticmethod
    def from_rect(rect: _Rect) -> Selection: ...
    @staticmethod
    def from_mask(mask: bytes, width: int, height: int) -> Selection | None: ...
    @staticmethod
    def combine(
        current: Selection | None, region: bytes, width: int, height: int, mode: str
    ) -> Selection | None:
        """mode は "replace"・"add"・"subtract"。"""
    @property
    def is_mask(self) -> bool: ...
    def mask(self, width: int, height: int, offset_x: int = 0, offset_y: int = 0) -> bytes: ...
    def clip_mask(
        self, width: int, height: int, offset_x: float = 0.0, offset_y: float = 0.0
    ) -> bytes | None: ...
    def bounds(self) -> _Rect: ...
    def outlines(self) -> list[list[_Point]]: ...
    def inverted(self, width: int, height: int, area: _Rect) -> Selection | None: ...

def selection_polygon_mask(
    points: list[_Point], width: int, height: int, offset_x: float = 0.0, offset_y: float = 0.0
) -> bytes:
    """多角形を Qt のアンチエイリアスなしの塗りと同じ画素で二値にする。"""
def selection_mask_contours(
    mask: bytes, width: int, height: int
) -> list[list[tuple[int, int]]]: ...
def selection_largest_contour(contours: list[list[tuple[int, int]]]) -> int | None: ...
def selection_color_mask(image: RgbaImage, rgb: tuple[int, int, int]) -> bytes: ...
def selection_contiguous_region(
    image: RgbaImage, x: int, y: int, adjacent: bool = True, close_gap: int | None = None,
    require_closed: bool = False,
) -> tuple[str, bytes | None]:
    """("region", マスク)・("out_of_bounds", None)・("open_to_edge", None) のどれか。"""
def selection_used_area(image: RgbaImage) -> Selection | None: ...
def selection_copy(
    image: RgbaImage, selection: Selection | None = None
) -> tuple[_Rect, RgbaImage] | None: ...
def selection_delete(image: RgbaImage, selection: Selection) -> _Edited | None: ...
def selection_cut(
    image: RgbaImage, selection: Selection | None = None
) -> tuple[RgbaImage, _Rect, RgbaImage, tuple[_Rect, bytes] | None] | None: ...
def selection_paste(
    image: RgbaImage, clip: RgbaImage, selection: Selection | None = None,
    origin: tuple[int, int] = (0, 0),
) -> _Edited | None: ...
def selection_clip_write(
    image: RgbaImage, rect: _Rect, pixels: bytes, clip: bytes | None = None
) -> _Edited | None: ...
def selection_transform_source_rect(
    selection: Selection, width: int, height: int
) -> _Rect | None: ...
def selection_lift(
    image: RgbaImage, selection: Selection
) -> tuple[RgbaImage, RgbaImage, _Rect, bytes] | None: ...
def selection_cleared(image: RgbaImage, selection: Selection) -> RgbaImage: ...
# バケツ・串刺し塗り（pma-core の tools）。settings は bucket_fill.BucketOptions の欄
# （adjacent, island_max_width, include_masks, mask_colors, include_background_mask,
#   close_gap, gap_width, extend_lines, extend_length, require_closed, target_color,
#   reference, island_replacements）と、塗る色 color (RGB)・不透明度 opacity (float)。
# 欄がなければ BucketOptions() の既定値。矩形は (x, y, 幅, 高さ)。selection は
# キャンバス全体の 0/1 の bytes。理由は bucket_fill.REASON_* の文字列。
_Rect = tuple[int, int, int, int]
_Cell = tuple[int, int]
_DragStep = tuple[str | None, list[tuple[_Rect, list[_RGB]]]]
_SkewerStatus = tuple[str, _Cell | None]
_SkewerEvent = tuple[str, Any]
_SkewerApplied = tuple[int, int, list[_RGB], int, list[tuple[_Cell, list[_Cell], _Rect]]]

def bucket_fill_at(
    document: _CoreDocument, frame: int, layer: int, settings: dict[str, Any],
    x: float, y: float, selection: bytes | None = None,
) -> tuple[str, _Rect | None, list[_RGB]]:
    """1か所を塗る。(理由, 書いた矩形か None, 書き込んだ RGB の列)。"""
def bucket_reference_pixels(
    document: _CoreDocument, frame: int, layer: int, reference: str,
) -> bytes:
    """塗る範囲の判定に使う絵（"layer"・"canvas"・"initial"）のストレート RGBA。"""
def bucket_replace_color_request(
    document: _CoreDocument, frame: int, layer: int, color: _RGB, x: float, y: float,
) -> tuple[str, tuple[_RGB, _RGB, bool] | None]:
    """色置換の判定。("ok", (元の RGB, 置き換える RGB, 線の色か)) か
    ("out_of_bounds"・"no_artwork"・"transparent"・"same_color", None)。"""
def skewer_cells(document: _CoreDocument, layer: int) -> list[_Cell]:
    """串刺し塗りで見るセル（露出元のコマ, レイヤー）。"""

class _BucketDrag:
    """なぞり塗り。begin で押した所を塗り、extend でなぞった先を塗る。"""

    @staticmethod
    def begin(
        document: _CoreDocument, frame: int, layer: int, settings: dict[str, Any],
        x: float, y: float, selection: bytes | None = None,
    ) -> tuple[_BucketDrag, _DragStep] | None: ...
    def extend(self, document: _CoreDocument, x: float, y: float) -> _DragStep: ...
    def finish(self, document: _CoreDocument) -> tuple[int, int, _Rect, bytes] | None:
        """(コマ, レイヤー, 塗った外接矩形, 押す前の画素) か None。"""

class _SkewerFill:
    """串刺し塗り。start のあと advance で追跡し、("ready", None) になったら apply。

    操作の結果 _SkewerEvent は ("invalidated"・"ignored", None)、("show_pending", セル)、
    ("failed", 理由)、("advanced", _SkewerStatus)。_SkewerStatus は ("pending", セル) か
    ("ready", None)。
    """

    @staticmethod
    def start(
        document: _CoreDocument, frame: int, layer: int, settings: dict[str, Any],
        x: float, y: float, selection: bytes | None = None,
    ) -> tuple[str, _SkewerFill | None, str | None]:
        """("ignored"・"no_artwork"・"failed"・"started", 串刺し塗りか None, 理由か None)。"""
    def advance(self, document: _CoreDocument) -> _SkewerStatus: ...
    def pending(self) -> _Cell | None: ...
    def has_guess(self) -> bool: ...
    def guide(self) -> tuple[bytes, tuple[int, int, int, int], bool] | None:
        """指定待ちのコマに重ねる (マスク, RGBA, 推測か)。"""
    def resume_click(
        self, document: _CoreDocument, frame: int, x: float, y: float,
    ) -> _SkewerEvent: ...
    def accept_guess(self, document: _CoreDocument) -> _SkewerEvent: ...
    def skip_frame(self, document: _CoreDocument) -> _SkewerEvent: ...
    def skip_direction(self, document: _CoreDocument) -> _SkewerEvent: ...
    def apply(self, document: _CoreDocument) -> tuple[str, _SkewerApplied | None]:
        """("done", (塗ったセル数, 飛ばしたコマ数, 書き込んだ RGB, 元のコマ,
        [(セル, 共有するセル, 矩形), ...])) か ("invalidated", None)。"""


# --- 線・色のメニューの操作（pma_core::image_ops） -------------------------------
# 編集の結果は (書き戻す画像, 変わった範囲 (x, y, w, h) | None, 変えた画素数)。
_Rgb = tuple[int, int, int]
_Edit = tuple[RgbaImage, _Rect | None, int]
_Contour = list[tuple[int, int]]

def image_ops_remove_dust(
    image: RgbaImage, mode: str, max_area: int, selected: list[_Rgb] | None = None,
) -> _Edit | None:
    """ゴミ取りツール（mode は "despeckle" / "fill_holes" / "line_noise"）。"""
def image_ops_tidy_lines(
    image: RgbaImage, colours: list[_Rgb], diagonal_mode: str | None = "add",
    region: bytes | None = None, region_size: tuple[int, int] | None = None,
) -> tuple[_Edit | None, int, int]:
    """線整え。(結果, 足した画素数, 消した画素数)。"""
def image_ops_tidy_preview(
    image: RgbaImage, colours: list[_Rgb], diagonal_mode: str | None = "add",
    region: bytes | None = None, region_size: tuple[int, int] | None = None,
    scale: int = 8, margin: int = 6, limit: int = 480,
) -> tuple[RgbaImage, _Rect, int, int, int] | None:
    """線整えのプレビュー。(画像, 切り出し, 拡大率, 足す数, 消す数)。"""
def image_ops_tidy_line_colours(image: RgbaImage, selected: list[_Rgb]) -> list[_Rgb]: ...
def image_ops_replace_colors(
    image: RgbaImage, mapping: list[tuple[_Rgb, _Rgb]],
    selection: bytes | None = None, selection_size: tuple[int, int] | None = None,
) -> _Edit | None:
    """使用色の置換・削除・統合（置換先が白なら透明）。"""
def image_ops_delete_mapping(selected: list[_Rgb]) -> list[tuple[_Rgb, _Rgb]]: ...
def image_ops_merge_mapping(parent: _Rgb, selected: list[_Rgb]) -> list[tuple[_Rgb, _Rgb]] | None: ...
def image_ops_aligned_mapping(source: RgbaImage, target: RgbaImage) -> list[tuple[_Rgb, _Rgb]] | None: ...
def hsv_shifted_color(rgb: _Rgb, hue: int, saturation: int, value: int) -> _Rgb: ...
def hsv_shifted_mapping(rgbs_in: list[_Rgb], hue: int, saturation: int, value: int) -> list[tuple[_Rgb, _Rgb]]: ...
def image_ops_mask_contours(mask: bytes, width: int, height: int) -> list[_Contour]: ...
def image_ops_color_selection(image: RgbaImage, rgb: _Rgb) -> tuple[bytes, list[_Contour], int, _Rect] | None:
    """(色のマスク, 輪郭, いちばん大きい輪郭の添字, 囲む矩形)。"""
def image_ops_contains_rgb(image: RgbaImage, rgb: _Rgb) -> bool: ...
def image_ops_split_line(image: RgbaImage, selected: list[_Rgb]) -> tuple[RgbaImage, RgbaImage, bool, bool]:
    """MainLineRepaint の 1 枚分。(LINE, Paint, 線があるか, 塗りが残ったか)。"""
def image_ops_count_antialias(rgba: bytes, width: int, height: int) -> int: ...
def image_ops_remove_antialias(rgba: bytes, width: int, height: int) -> tuple[bytes, int]: ...
def image_ops_remove_isolated_specks(rgba: bytes, width: int, height: int, max_pixels: int) -> tuple[bytes, int]: ...
def image_ops_soft_blur(rgba: bytes, width: int, height: int, sigma: float) -> bytes: ...
def image_ops_rgb_hsv_features(colours: list[_Rgb]) -> bytes:
    """単精度 4 つずつの特徴量（colors.rgb_hsv_features と同じ値）。"""
def image_ops_priority_palette_colors(sample: list[_Rgb], maximum: int) -> list[_Rgb]: ...
def image_ops_hue_cluster_palette(sample: list[_Rgb], color_count: int) -> list[_Rgb]: ...
def image_ops_build_palette(
    images: list[RgbaImage], color_count: int, alpha_threshold: int = 128, max_samples: int = 262144,
    opaque_background: bool = False, background: _Rgb | None = None, line: bool = False,
) -> list[_Rgb]: ...
def image_ops_binarize(
    image: RgbaImage, mode: str, palette: list[_Rgb] | None = None, blur: float = 0.0,
    despeckle_pixels: int = 0, alpha_threshold: int = 128, opaque_background: bool = False,
    background: _Rgb | None = None, tone_curve_points: list[tuple[float, float]] | None = None,
    flatten_paper: bool = False,
) -> _Edit | None:
    """2 値化の 1 セル分（mode は "surface" / "line" / "digital"）。flatten_paper は未移植で ValueError。"""
def image_ops_border_background_transparent(
    rgba: bytes, width: int, height: int, tolerance: int = 24,
) -> bytes | None: ...

# 書き出し・読み込み（pma_core::export・import と pma-io、native/src/export.rs）。
# 構成 layout_json は CutFolderLayout.to_json() の JSON 文字列。values は入力欄の辞書。
_Cel = tuple[int, str, int, int]
"""(列, セル名, セル番号, 最初のコマ)。"""

def export_cut_folder_cels(document: _CoreDocument) -> tuple[list[_Cel], int]:
    """書き出す絵と、セル番号のない絵の数（MainWindowExport.cut_folder_cels）。"""

def export_plan_cut_folder(layout_json: str, values_dict: dict[str, Any], cel_list: list[_Cel]) -> dict[str, Any]:
    """cut_folder.plan_export の結果（files は (パス, 列, セル名, セル番号)）。"""

def export_write_cut_folder(
    document: _CoreDocument, parent: str | os.PathLike[str], layout_json: str,
    values_dict: dict[str, Any], memo: str = "", playback_range_end: int | None = None,
) -> str:
    """カットフォルダーを parent の下に書き出し、そのパスを返す。計画に問題があれば ValueError。"""

def export_sheet_text(
    document: _CoreDocument, format: str, sheet_name: str, cut: str = "", episode: str = "",
    scene: str = "", playback_range_end: int | None = None,
) -> str: ...
def export_sheet_duration(document: _CoreDocument, playback_range_end: int | None = None) -> int: ...
def export_write_key_sequence(document: _CoreDocument, destination: str | os.PathLike[str], format: str) -> int | None: ...
def export_write_shadow_sequence(
    document: _CoreDocument, destination: str | os.PathLike[str], shadow_rgbs: list[tuple[int, int, int]],
) -> int | None: ...
def export_thumbnail_png(document: _CoreDocument, frame: int, options: dict[str, Any] | None = None) -> bytes:
    """.pman のサムネイルの PNG（project_io._thumbnail_png(window.crop_image(frame))）。"""

def export_thumbnail_scale(width: int, height: int, premultiplied: bytes, max_size: int) -> tuple[int, int, bytes]:
    """Qt の SmoothTransformation と同じ縮小（乗算済み RGBA）。"""

def export_frame_image(
    document: _CoreDocument, frame: int, format: str = "png", options: dict[str, Any] | None = None,
) -> bytes: ...
def export_after_effects_text(document: _CoreDocument, layer: int, fps: float) -> str | None: ...
def export_guess_cut_number(name: str) -> str: ...
def export_encode_tga(width: int, height: int, rgba: bytes) -> bytes:
    """ストレート RGBA を Pillow の tga_rle と同じバイト列にする。"""

def import_read_image(data: bytes) -> tuple[int, int, bytes]:
    """画像を Qt の ARGB32_Premultiplied と同じ値の乗算済み RGBA（R, G, B, A の順）にする。"""

def import_image_file(
    path: str | os.PathLike[str], canvas_width: int, canvas_height: int, draft: bool = False,
) -> bytes:
    """読み込み・白の透明化・中央への配置をした文書の絵（ストレート RGBA）。"""

def import_parse_timesheet(raw_text: str) -> dict[str, Any]:
    """ImportController._parse_xdts_timesheet と同じ辞書。"""

def import_scan_cut_folder(root: str | os.PathLike[str]) -> dict[str, Any]:
    """cut_folder.scan_cut_folder（パスは root からの相対）。"""
