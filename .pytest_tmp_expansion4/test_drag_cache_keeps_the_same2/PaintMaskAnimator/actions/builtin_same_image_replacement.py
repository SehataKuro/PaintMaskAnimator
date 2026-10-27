"""通常のアクションの例: 同一画像から色置換。"""


def register_actions(panel, window):
    def replace_colors():
        # window は MainWindow です。既存の操作を呼び出せます。
        window.line_ops.register_same_image_replacements()
        window.status_bar.showMessage("同一画像から色置換を実行しました", 3000)

    panel.add_action(
        "same_image_replacement",
        "同一画像から色置換",
        replace_colors,
        tooltip=(
            "同じタイムライン位置にある上のレイヤーと画素配置を比較し、"
            "一致した色対応をそのまま実画像へ適用します。"
        ),
    )
