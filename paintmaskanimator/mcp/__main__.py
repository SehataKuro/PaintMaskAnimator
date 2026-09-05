"""``python -m paintmaskanimator.mcp`` で MCP サーバーを起動する。

Claude Desktop などの設定に書く起動コマンドがこれ。stdio で話すので、
標準出力へ余計なものを書かないよう GUI は一切起動しない（オフスクリーンの
QGuiApplication だけを用意して QImage / QPainter を使えるようにする）。
"""
from __future__ import annotations

import argparse
import os
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m paintmaskanimator.mcp",
        description="PaintMaskAnimator を MCP サーバーとして起動する。",
    )
    parser.add_argument(
        "project",
        nargs="?",
        help=".pma プロジェクト。省略した場合は open_project ツールで開く。",
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help=(
            "領域への塗りと保存を許可する。既定は読み取り専用で、"
            "AI がプロジェクトを書き換えることはない。"
        ),
    )
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    # QImage/QPainter に必要な最小限の Qt だけを、画面なしで用意する。
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])

    from .server import build_server
    from .session import ProjectSession

    session = ProjectSession(allow_write=args.allow_write)
    server = build_server(session, project=args.project)
    server.run(transport="stdio")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
