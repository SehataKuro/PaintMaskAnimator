"""MCP のツール定義。

ここが持つのは「どんなツールをどんな引数で公開するか」だけで、実際の処理は
:class:`~paintmaskanimator.mcp.session.ProjectSession` にある。公開するツールは
後から消せない API 契約になるので、最小限から始める方針を取っている。

安全側の既定：
- 起動時は**読み取り専用**。``--allow-write`` を付けたときだけ書き込み系が動く。
- 保存は**別名保存**が既定。開いたファイルは明示しない限り上書きしない。
"""
# ``from __future__ import annotations`` は入れない。MCP はツール関数の注釈を
# 実体として読んでスキーマを組み立てるので、文字列化されると解決できなくなる。
import base64
import json
from typing import Any, Dict, List, Optional

from ..logging_setup import get_logger
from .session import DEFAULT_REGION_LIMIT, ProjectSession, ReadOnlyError

log = get_logger(__name__)

SERVER_NAME = "paintmaskanimator"

INSTRUCTIONS = """\
PaintMaskAnimator (PMAn) のアニメーションプロジェクト (.pma) を操作します。

典型的な流れ:
1. open_project でプロジェクトを開く
2. describe_project / list_frames で構成を掴む
3. render_frame でコマを画像として見る（設定画との突き合わせ・パーツ抜けの確認）
4. list_regions で線に囲まれた閉領域を取り、get_color_chart の色と役割を突き合わせる
5. apply_region_colors で領域に色を置く（--allow-write 起動時のみ）
6. save_project で保存する（既定は別名保存）

list_regions が返す seed は「そこをクリックしたらバケツが塗る範囲」を指す座標で、
apply_region_colors にそのまま渡せます。
2コマ目以降はアプリ側の串刺し塗りが担当するので、ここでは 1 コマ目の役割判定に
集中してください。
"""


def _require_mcp():
    """``mcp`` を遅延 import する。

    未導入のときに import エラーを素通しせず、入れ方まで案内する。この
    パッケージは任意依存なので、ここが唯一の依存点になる。
    """
    try:
        from mcp.server.mcpserver import MCPServer
    except ModuleNotFoundError as error:  # pragma: no cover - 依存の有無で分岐
        raise SystemExit(
            "MCP サーバーには 'mcp' パッケージが必要です:\n"
            "    pip install paintmaskanimator[mcp]\n"
            f"（元のエラー: {error}）"
        ) from error
    return MCPServer


def build_server(session: ProjectSession, *, project=None):
    """``session`` を操作する MCP サーバーを組み立てて返す。"""
    MCPServer = _require_mcp()
    from mcp.types import ImageContent

    server = MCPServer(name=SERVER_NAME, instructions=INSTRUCTIONS)

    if project is not None:
        session.open(project)

    def _json(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _read_only(error: ReadOnlyError) -> str:
        """読み取り専用は想定内の状態なので、例外ではなく理由として返す。

        例外にすると MCP のエラー文言に潰されて「なぜ書けなかったか」が
        クライアントへ届かない。
        """
        return _json({"error": str(error), "writable": False})

    @server.tool()
    def open_project(path: str) -> str:
        """.pma プロジェクトを開き、構成の概要を返す。"""
        return _json(session.open(path))

    @server.tool()
    def describe_project() -> str:
        """開いているプロジェクトのキャンバスサイズ・コマ数・レイヤー構成を返す。"""
        return _json(session.describe())

    @server.tool()
    def list_frames(start: int = 0, limit: int = 200) -> str:
        """コマごとの尺とレイヤー属性（可視・露出・セル名・保持セルの解決先）を返す。"""
        return _json(session.list_frames(start=start, limit=limit))

    @server.tool()
    def get_color_chart() -> str:
        """プロジェクトの色チャート（色・親子関係・役割タグ）を返す。

        「この領域は肌」「これは影1」と判断するための語彙がここにある。
        """
        return _json(session.color_chart())

    @server.tool()
    def render_frame(
        frame: int,
        white_background: bool = True,
        max_size: int = 1280,
    ) -> ImageContent:
        """コマを合成して PNG 画像として返す。

        レイヤーの可視・不透明度・保持セルは解決するが、色フィルタなどの
        画面表示専用の設定は反映しない。
        """
        data = session.render_png(
            frame, white_background=white_background, max_size=max_size
        )
        return ImageContent(
            type="image",
            data=base64.b64encode(data).decode("ascii"),
            mime_type="image/png",
        )

    @server.tool()
    def list_regions(
        frame: int,
        layer: int = 0,
        min_area: int = 16,
        limit: int = DEFAULT_REGION_LIMIT,
        include_painted: bool = True,
    ) -> str:
        """レイヤーを閉領域へ分解し、面積の大きい順に返す。

        各領域の seed はそのまま apply_region_colors に渡せる。color が null の
        領域は未塗り。
        """
        regions = session.list_regions(
            frame,
            layer,
            min_area=min_area,
            limit=limit,
            include_painted=include_painted,
        )
        return _json(
            {
                "frame": frame,
                "layer": layer,
                "count": len(regions),
                "regions": [region.as_dict() for region in regions],
            }
        )

    @server.tool()
    def apply_region_colors(assignments: List[Dict[str, Any]]) -> str:
        """領域に色を置く。--allow-write を付けて起動したときだけ動く。

        assignments は {"frame": 0, "layer": 0, "seed": [x, y], "color": "#RRGGBB"}
        の配列。1件でも色が解釈できなければ何も書かずに失敗する。
        """
        try:
            return _json(session.apply_region_colors(assignments))
        except ReadOnlyError as error:
            return _read_only(error)

    @server.tool()
    def save_project(path: Optional[str] = None) -> str:
        """保存する。path を省略すると ``<元の名前>_mcp.pma`` へ別名保存する。"""
        try:
            return _json(session.save(path))
        except ReadOnlyError as error:
            return _read_only(error)

    return server
