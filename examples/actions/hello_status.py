"""Example PaintMaskAnimator user action.

Open the in-app editor (Action panel > ☰ > スクリプトを編集), create a new
script, and paste this content — or copy this file into the actions folder and
press 保存して再読み込み. See docs/actions.md for details.
"""


def register_actions(panel, window):
    panel.add_action(
        "example.hello",
        "Hello from Python",
        lambda: window.statusBar().showMessage(
            "Pythonアクションを実行しました。", 3000
        ),
        tooltip="ユーザーPythonアクションのサンプルです。",
    )
