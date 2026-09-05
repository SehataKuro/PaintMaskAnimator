"""PaintMaskAnimator を MCP サーバーとして公開するためのパッケージ。

外部の AI クライアント（Claude Desktop / Claude Code など）から ``.pma`` の
構成を読み、コマを画像として受け取り、閉領域を数え、領域に色を置くための
ツールを提供する。**推論はクライアント側にあるので、PMAn は API キーも
ネットワークも持たない。**

- :mod:`~paintmaskanimator.mcp.session` … プロトコル非依存の中身。``mcp``
  パッケージがなくても import でき、単体テストできる。
- :mod:`~paintmaskanimator.mcp.server` … MCP のツール定義。``mcp`` が必要。

``mcp`` は任意依存（``pip install paintmaskanimator[mcp]``）。未導入でも
アプリ本体の動作には影響しない。
"""
from .session import ProjectSession, ReadOnlyError, Region

__all__ = ["ProjectSession", "ReadOnlyError", "Region"]
