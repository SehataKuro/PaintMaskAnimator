"""Example PaintMaskAnimator user action.

Copy this file to the folder opened by Action panel > Open Folder, then press
Reload in the Action panel.
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
