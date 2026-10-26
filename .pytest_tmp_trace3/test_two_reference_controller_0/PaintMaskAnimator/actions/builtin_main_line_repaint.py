"""処理を関数に分ける例: MainLineRepaint。"""


def register_actions(panel, window):
    def repaint_main_line():
        # 通常ボタンのコールバックには引数が渡りません。
        window.line_ops.main_line_repaint()

    panel.add_action(
        "main_line_repaint",
        "MainLineRepaint",
        repaint_main_line,
        tooltip=(
            "メイン色・サブ色を線レイヤーへ分離し、"
            "抜けた面を周囲の最多色で埋めます。"
        ),
    )
