"""ヘッドレスなプロジェクトセッション。MCP から見た PMAn の中身。

``mcp`` パッケージには依存しない。ここにあるのは「``.pma`` を開いて、コマや
レイヤーの構成を答え、コマを1枚の PNG に合成し、閉領域を数え、領域に色を置く」
という操作だけで、プロトコルの都合は :mod:`paintmaskanimator.mcp.server` が持つ。
分けてあるので、サーバーを起動しなくてもこの層だけを単体テストできる。

合成は「レイヤーの可視・不透明度・保持セルの解決」までで、色フィルタやマスク
表示といった *画面の見え方* の設定は反映しない。あれはドキュメントではなく
ビューの状態で、GUI を起動していないここには存在しないため。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from .. import bucket_fill, imaging
from ..color_chart import normalize_color_chart
from ..frame_scope import resolve_key_frame
from ..project_io import read_project_archive, write_project_archive

__all__ = ["ProjectSession", "Region", "ReadOnlyError"]

#: 領域一覧が1回に返す上限。AI に渡す量としても、走査時間としても現実的な範囲。
DEFAULT_REGION_LIMIT = 400


class ReadOnlyError(RuntimeError):
    """書き込みが許可されていないセッションに書き込もうとした。"""


@dataclass(frozen=True)
class Region:
    """線で閉じた1つの領域。

    ``seed`` はその領域に確実に含まれる座標で、``apply_region_colors`` に
    そのまま渡せる。「領域を指して色を決める」という往復を、座標1つで完結
    させるための鍵。
    """

    seed: Tuple[int, int]
    area: int
    bbox: Tuple[int, int, int, int]
    color: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "seed": list(self.seed),
            "area": self.area,
            "bbox": list(self.bbox),
            "color": self.color,
            "painted": self.color is not None,
        }


def _hex_color(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(int(value) for value in rgb))


def _parse_color(value) -> Tuple[int, int, int]:
    color = QColor(str(value))
    if not color.isValid():
        raise ValueError(f"色として解釈できません: {value!r}")
    return color.red(), color.green(), color.blue()


class ProjectSession:
    """開いている ``.pma`` 1つ分の状態。

    グローバル定数（``constants.CANVAS_WIDTH`` など）に触れないよう、
    キャンバスの大きさはアーカイブから読んだ値をこのオブジェクトが持つ。
    1プロセスで複数のプロジェクトを開いても互いに干渉しない。
    """

    def __init__(self, *, allow_write: bool = False):
        self.allow_write = bool(allow_write)
        self.path: Optional[Path] = None
        self.metadata: Dict[str, Any] = {}
        self.frames: List[Any] = []
        self.width = 0
        self.height = 0
        self._dirty = False

    # ------------------------------------------------------------------ 読み込み

    def open(self, path) -> Dict[str, Any]:
        metadata, frames, width, height = read_project_archive(path)
        self.path = Path(path)
        self.metadata = metadata
        self.frames = frames
        self.width = int(width)
        self.height = int(height)
        self._dirty = False
        return self.describe()

    def require_open(self):
        if not self.frames:
            raise RuntimeError(
                "プロジェクトが開かれていません。先に open_project を呼んでください。"
            )

    # ------------------------------------------------------------------ 参照

    def describe(self) -> Dict[str, Any]:
        self.require_open()
        return {
            "path": str(self.path) if self.path else None,
            "canvas": {"width": self.width, "height": self.height},
            "frame_count": len(self.frames),
            "layer_count": len(self.frames[0].layers) if self.frames else 0,
            "layers": [
                {"index": index, "name": layer.name, "is_paper": bool(layer.is_paper)}
                for index, layer in enumerate(self.frames[0].layers)
            ],
            "total_duration": sum(int(frame.duration) for frame in self.frames),
            "writable": self.allow_write,
            "unsaved_changes": self._dirty,
        }

    def list_frames(self, start: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
        self.require_open()
        start = max(0, int(start))
        end = min(len(self.frames), start + max(1, int(limit)))
        rows = []
        for frame_index in range(start, end):
            frame = self.frames[frame_index]
            layers = []
            for layer_index, layer in enumerate(frame.layers):
                key_frame = resolve_key_frame(self.frames, frame_index, layer_index)
                layers.append(
                    {
                        "index": layer_index,
                        "name": layer.name,
                        "visible": bool(layer.visible),
                        "opacity": float(layer.opacity),
                        "exposure": int(layer.exposure),
                        "has_content": bool(layer.has_content),
                        "cell_name": layer.cell_name,
                        "sequence_number": layer.sequence_number,
                        "is_draft": bool(layer.is_draft),
                        # 保持セルは手前のキーを指す。自分がキーなら自分の番号。
                        "key_frame": key_frame,
                    }
                )
            rows.append(
                {
                    "index": frame_index,
                    "duration": int(frame.duration),
                    "layers": layers,
                }
            )
        return rows

    def color_chart(self) -> Dict[str, Any]:
        """プロジェクトに保存された色チャート（``.pmag`` と同じ形）を返す。

        色そのものだけでなく、親子関係と役割タグを含む。「この領域は影1」と
        いう判断の語彙がここにある。
        """
        self.require_open()
        return normalize_color_chart(self.metadata.get("color_chart", {}))

    # ------------------------------------------------------------------ 合成

    def composite(self, frame_index: int, *, white_background: bool = True) -> QImage:
        self.require_open()
        frame_index = self._checked_frame(frame_index)
        result = QImage(self.width, self.height, QImage.Format.Format_ARGB32_Premultiplied)
        result.fill(QColor("white") if white_background else Qt.GlobalColor.transparent)
        painter = QPainter(result)
        try:
            for layer_index in range(len(self.frames[frame_index].layers)):
                key_frame = resolve_key_frame(self.frames, frame_index, layer_index)
                if key_frame is None:
                    continue
                layer = self.frames[key_frame].layers[layer_index]
                if not layer.visible:
                    continue
                painter.setOpacity(float(layer.opacity))
                painter.drawImage(0, 0, layer.image)
        finally:
            painter.end()
        return result

    def render_png(
        self,
        frame_index: int,
        *,
        white_background: bool = True,
        max_size: Optional[int] = None,
    ) -> bytes:
        """コマを PNG のバイト列にする。

        ``max_size`` は長辺の上限。AI へ画像を渡すときは原寸のままだと無駄に
        大きいので、既定で縮小できるようにしてある。
        """
        image = self.composite(frame_index, white_background=white_background)
        if max_size:
            max_size = int(max_size)
            if max(image.width(), image.height()) > max_size:
                mode = Qt.TransformationMode.SmoothTransformation
                image = (
                    image.scaledToWidth(max_size, mode)
                    if image.width() >= image.height()
                    else image.scaledToHeight(max_size, mode)
                )
        # QBuffer に QByteArray を渡すとその一時オブジェクトが先に解放されるため、
        # 内部バッファを持たせる無引数の形で開く。
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        try:
            if not image.save(buffer, "PNG"):
                raise RuntimeError("PNG へ書き出せませんでした。")
            return bytes(buffer.data().data())
        finally:
            buffer.close()

    # ------------------------------------------------------------------ 領域

    def _layer_pixels(
        self, frame_index: int, layer_index: int
    ) -> Tuple[Any, QImage, np.ndarray]:
        layer = self._layer(frame_index, layer_index)
        converted = layer.image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = converted.width(), converted.height()
        rows = np.frombuffer(
            imaging.qimage_buffer(converted), dtype=np.uint8
        ).reshape((height, converted.bytesPerLine()))
        return layer, converted, rows[:, : width * 4].reshape((height, width, 4))

    def list_regions(
        self,
        frame_index: int,
        layer_index: int = 0,
        *,
        min_area: int = 16,
        limit: int = DEFAULT_REGION_LIMIT,
        include_painted: bool = True,
    ) -> List[Region]:
        """レイヤーを閉領域へ分解する。

        領域の定義はバケツの「隣接」塗りと同じ――同じ色種別で 4 近傍につながる
        かたまり。つまりここで返る領域は「その座標をクリックしたらバケツが塗る
        範囲」そのもので、``seed`` をそのまま ``apply_region_colors`` に渡せる。
        判定を共有しているので、一覧と実際の塗りが食い違わない。
        """
        self.require_open()
        _layer, _converted, pixels = self._layer_pixels(
            self._checked_frame(frame_index), self._checked_layer(layer_index)
        )
        height, width = int(pixels.shape[0]), int(pixels.shape[1])
        background = bucket_fill.background_mask(pixels)
        rgb = pixels[:, :, :3]

        visited = np.zeros((height, width), dtype=bool)
        category_masks: Dict[Optional[Tuple[int, int, int]], np.ndarray] = {}
        regions: List[Region] = []
        min_area = max(1, int(min_area))

        for y in range(height):
            row_unvisited = ~visited[y]
            if not row_unvisited.any():
                continue
            for x in np.nonzero(row_unvisited)[0]:
                x = int(x)
                if visited[y, x]:
                    continue
                category = (
                    None
                    if background[y, x]
                    else (int(rgb[y, x, 0]), int(rgb[y, x, 1]), int(rgb[y, x, 2]))
                )
                mask = category_masks.get(category)
                if mask is None:
                    mask = (
                        background
                        if category is None
                        else (pixels[:, :, 3] > 0)
                        & np.all(rgb == np.asarray(category, dtype=np.uint8), axis=2)
                    )
                    category_masks[category] = mask
                region = imaging.scanline_connected_region(mask, (x, y))
                visited |= region
                area = int(region.sum())
                if area < min_area:
                    continue
                if category is not None and not include_painted:
                    continue
                ys, xs = np.nonzero(region)
                regions.append(
                    Region(
                        seed=(x, y),
                        area=area,
                        bbox=(
                            int(xs.min()),
                            int(ys.min()),
                            int(xs.max() - xs.min() + 1),
                            int(ys.max() - ys.min() + 1),
                        ),
                        color=None if category is None else _hex_color(category),
                    )
                )
                if len(regions) >= max(1, int(limit)):
                    regions.sort(key=lambda item: item.area, reverse=True)
                    return regions
        regions.sort(key=lambda item: item.area, reverse=True)
        return regions

    # ------------------------------------------------------------------ 書き込み

    def apply_region_colors(
        self,
        assignments: Sequence[Dict[str, Any]],
        *,
        options: Optional[bucket_fill.BucketOptions] = None,
    ) -> Dict[str, Any]:
        """``{"frame", "layer", "seed", "color"}`` の並びをまとめて塗る。

        1件ずつではなくまとめて受けるのは、AI に「このカットの割り当て」を一度に
        決めさせたいから。1件でも色が解釈できなければ何も書かずに失敗する
        （途中まで塗られた状態を残さない）。
        """
        self.require_open()
        if not self.allow_write:
            raise ReadOnlyError(
                "このセッションは読み取り専用です。--allow-write を付けて起動してください。"
            )
        options = options or bucket_fill.BucketOptions()

        planned = []
        for entry in assignments:
            frame_index = self._checked_frame(entry.get("frame", 0))
            layer_index = self._checked_layer(entry.get("layer", 0))
            seed = entry.get("seed") or ()
            if len(seed) != 2:
                raise ValueError(f"seed は [x, y] で指定してください: {seed!r}")
            planned.append(
                (
                    frame_index,
                    layer_index,
                    int(seed[0]),
                    int(seed[1]),
                    _parse_color(entry.get("color")),
                )
            )

        applied, skipped = [], []
        for frame_index, layer_index, x, y, rgb in planned:
            layer, converted, pixels = self._layer_pixels(frame_index, layer_index)
            result = bucket_fill.compute_fill_region(pixels, x, y, options)
            if not result.ok:
                skipped.append(
                    {
                        "frame": frame_index,
                        "layer": layer_index,
                        "seed": [x, y],
                        "reason": result.reason,
                    }
                )
                continue
            ys, _xs = bucket_fill.write_region(pixels, result.mask, rgb)
            layer.image = converted.convertToFormat(
                QImage.Format.Format_ARGB32_Premultiplied
            )
            layer.has_content = True
            self._dirty = True
            applied.append(
                {
                    "frame": frame_index,
                    "layer": layer_index,
                    "seed": [x, y],
                    "color": _hex_color(rgb),
                    "pixels": int(ys.size),
                }
            )
        return {"applied": applied, "skipped": skipped, "unsaved_changes": self._dirty}

    def save(self, path=None) -> Dict[str, Any]:
        """保存する。既定は別名保存で、開いたファイルは上書きしない。

        AI が書き込む前提なので、うっかり元データを壊さないほうを既定にする。
        元のファイルへ書きたいときは ``path`` に同じパスを明示する。
        """
        self.require_open()
        if not self.allow_write:
            raise ReadOnlyError("このセッションは読み取り専用です。")
        if path is None:
            if self.path is None:
                raise ValueError("保存先を指定してください。")
            target = self.path.with_name(f"{self.path.stem}_mcp{self.path.suffix}")
        else:
            target = Path(path)
        write_project_archive(target, dict(self.metadata), self.frames)
        self._dirty = False
        return {"saved_to": str(target)}

    # ------------------------------------------------------------------ 内部

    def _checked_frame(self, frame_index) -> int:
        frame_index = int(frame_index)
        if not 0 <= frame_index < len(self.frames):
            raise IndexError(
                f"コマ番号が範囲外です: {frame_index}（0..{len(self.frames) - 1}）"
            )
        return frame_index

    def _checked_layer(self, layer_index) -> int:
        layer_index = int(layer_index)
        count = len(self.frames[0].layers) if self.frames else 0
        if not 0 <= layer_index < count:
            raise IndexError(f"レイヤー番号が範囲外です: {layer_index}（0..{count - 1}）")
        return layer_index

    def _layer(self, frame_index: int, layer_index: int):
        key_frame = resolve_key_frame(self.frames, frame_index, layer_index)
        if key_frame is None:
            key_frame = frame_index
        return self.frames[key_frame].layers[layer_index]
