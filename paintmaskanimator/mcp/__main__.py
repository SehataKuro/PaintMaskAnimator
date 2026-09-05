"""``python -m paintmaskanimator.mcp`` — MCP サーバーの起動と、導入の補助。

引数なしで起動すると stdio の MCP サーバーになる。標準出力はプロトコルが使うので、
GUI は一切開かず、案内も標準エラーへ出す。

``--doctor`` / ``--print-config`` / ``--install`` は導入用で、サーバーは起動しない。
設定ファイルを手で書かずに済ませるためのもので、中身は :mod:`.setup` にある。
"""
from __future__ import annotations

import argparse
import os
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m paintmaskanimator.mcp",
        description="PaintMaskAnimator を MCP サーバーとして起動する。",
        epilog=(
            "はじめて使うときは --doctor で導入状況を確認し、"
            "--install で Claude Desktop へ登録してください。"
        ),
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

    setup_group = parser.add_argument_group("導入の補助（サーバーは起動しない）")
    setup_group.add_argument(
        "--doctor",
        action="store_true",
        help="導入がどこで止まっているかを実際に試して確認する。",
    )
    setup_group.add_argument(
        "--print-config",
        action="store_true",
        help="Claude Desktop に貼り付ける設定 JSON を表示する。",
    )
    setup_group.add_argument(
        "--print-command",
        action="store_true",
        help="Claude Code 用の claude mcp add コマンドを表示する。",
    )
    setup_group.add_argument(
        "--install",
        action="store_true",
        help=(
            "Claude Desktop の設定へ登録する。既存の設定はマージし、"
            "上書き前に .bak を作る。"
        ),
    )
    setup_group.add_argument(
        "--uninstall",
        action="store_true",
        help="Claude Desktop の設定から登録を取り消す。",
    )
    setup_group.add_argument(
        "--client",
        choices=["claude-desktop", "codex"],
        default="claude-desktop",
        help="登録先のクライアント（既定: claude-desktop）。",
    )
    setup_group.add_argument(
        "--config-path",
        help="設定ファイルの場所を明示する（既定はクライアント標準の場所）。",
    )
    return parser


def _run_setup(args) -> int:
    """導入用のサブコマンド。何か1つでも指定されていれば真を返す。"""
    from . import setup

    target = setup.client_target(args.client)

    if args.doctor:
        diagnosis = setup.diagnose(config_path=args.config_path, client=args.client)
        print(diagnosis.as_text())
        if not diagnosis.ok:
            print("\n必要な項目が揃っていません。上の → の手順を実行してください。")
            return 1
        print("\nMCP サーバーを起動できます。")
        return 0

    if args.print_config:
        print(target.render(args.project, allow_write=args.allow_write))
        print(
            f"\n貼り付け先（{target.format_name}）: {target.config_path()}",
            file=sys.stderr,
        )
        return 0

    if args.print_command:
        print(target.cli_command(args.project, allow_write=args.allow_write))
        return 0

    if args.uninstall:
        report = target.uninstall(config_path=args.config_path)
        if report["removed"]:
            print(f"登録を取り消しました: {report['config_path']}")
        else:
            print(f"登録されていませんでした: {report['config_path']}")
        return 0

    if args.install:
        try:
            report = target.install(
                args.project,
                allow_write=args.allow_write,
                config_path=args.config_path,
            )
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        action = "更新しました" if report["replaced"] else "登録しました"
        print(f"{action}: {report['config_path']}")
        if report["backup_path"]:
            print(f"バックアップ: {report['backup_path']}")
        if report["other_servers"]:
            print("既存のサーバー設定はそのまま残しました: " + ", ".join(report["other_servers"]))
        if target.needs_restart:
            print(f"\n{target.label} を再起動すると反映されます。")
        else:
            print(f"\n次に {target.label} を起動したときから使えます。")
        return 0

    return -1


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    status = _run_setup(args)
    if status >= 0:
        return status

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
