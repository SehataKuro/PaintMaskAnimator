"""E2Eシナリオ共通のフィクスチャ。

各テストは実際のアプリ起動と同じ経路で MainWindow を組み立て、終了時に必ず破棄する。
設定ファイルの読み書き先はルートの conftest.py が tmp_path に隔離し、ユーザーの実環境を汚さない。

モーダルダイアログはヘッドレスE2Eの最大の障害なので、ここで一括して制御する:
- QMessageBox の静的呼び出しは記録して素通しする（テストから内容を検証できる）
- QDialog.exec() は既定で失敗させ、仕様として開くものだけ個別に許可する
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from paintmaskanimator.main_window import MainWindow

from .driver import AppDriver


@dataclass
class ShownMessage:
    level: str
    title: str
    text: str


@dataclass
class MessageRecorder:
    """アプリが出した QMessageBox を記録する。"""

    shown: list[ShownMessage] = field(default_factory=list)

    def titles(self) -> list[str]:
        return [message.title for message in self.shown]

    def of_level(self, level: str) -> list[ShownMessage]:
        return [message for message in self.shown if message.level == level]


def _affirmative_answer(buttons):
    """呼び出し側が提示した選択肢のうち「進める」ボタンを選ぶ。

    確認ダイアログは Ok/Cancel と Yes/No が混在しているため、渡された
    ボタン群から肯定側を選ばないと、シナリオが黙って中断されてしまう。
    """
    preferred = (
        QMessageBox.StandardButton.Ok,
        QMessageBox.StandardButton.Yes,
        QMessageBox.StandardButton.Save,
    )
    if isinstance(buttons, QMessageBox.StandardButton):
        for candidate in preferred:
            if buttons & candidate:
                return candidate
    return QMessageBox.StandardButton.Ok


@pytest.fixture(autouse=True)
def messages(monkeypatch):
    """QMessageBox の静的呼び出しを記録して即座に既定応答を返す。"""
    recorder = MessageRecorder()
    for level in ("information", "warning", "critical", "question"):
        def _record(parent, title, text, *args, _level=level, **kwargs):
            recorder.shown.append(ShownMessage(_level, title, text))
            return _affirmative_answer(args[0] if args else None)

        monkeypatch.setattr(QMessageBox, level, staticmethod(_record))
    return recorder


@pytest.fixture(autouse=True)
def no_unexpected_modal_dialogs(monkeypatch):
    """想定外のモーダルダイアログでテストが無限に固まるのを防ぐ。

    開くことが仕様のダイアログは AppDriver.accept_dialog で個別に許可する。
    ここを通ってしまうのは「ユーザー操作の途中で予期しない確認が出た」という
    不具合なので、待たずに失敗させる。
    """
    def _fail(self):
        raise AssertionError(
            f"予期しないモーダルダイアログが開かれた: {type(self).__name__} / "
            f"{self.windowTitle()!r}"
        )

    monkeypatch.setattr(QDialog, "exec", _fail)


@pytest.fixture
def app(qtbot, messages):
    """起動済みアプリを操作する AppDriver を返す。

    設定フォルダーの隔離はルートの conftest.py が全テストに対して行う。
    """
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    try:
        yield AppDriver(window, qtbot, messages)
    finally:
        window.close()
