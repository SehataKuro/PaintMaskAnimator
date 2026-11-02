"""カット袋・シートのメモ欄にあたるポップアップ（Issue #25）。

カットごとの申し送りを書き留めておくための小さな窓。書いた内容はプロジェクト
（.pman）に入れて持ち歩き、カットフォルダーへ書き出すときはタイムシートの隣へ
``_memo.txt`` として出す。

窓はモードレスなので、開いたまま描き続けられる。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QVBoxLayout

from .i18n import tr

#: 書き出すメモのファイル名。
MEMO_FILE_NAME = "_memo.txt"


class MemoWindow(QDialog):
    """メモを書く窓。打つたびに ``textChanged`` で中身を知らせる。"""

    textChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("メモ"))
        self.setWindowFlag(Qt.WindowType.Tool, True)
        # モードレス。描きながら書き足せるようにする。
        self.setModal(False)
        self.resize(360, 280)

        layout = QVBoxLayout(self)
        note = QLabel(
            tr("カット袋やシートのメモ欄として使えます。"
               "プロジェクトに保存され、カットフォルダー書き出しでは"
               "タイムシートの隣へ {name} として出ます。").format(
                name=MEMO_FILE_NAME
            )
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(tr("例：3コマ目のタイミング要確認"))
        self.editor.textChanged.connect(
            lambda: self.textChanged.emit(self.text())
        )
        layout.addWidget(self.editor, 1)

    def text(self):
        return self.editor.toPlainText()

    def set_text(self, value):
        """中身を差し替える。読み込み時の復元で ``textChanged`` を出さない。"""
        blocked = self.editor.blockSignals(True)
        try:
            self.editor.setPlainText(str(value or ""))
        finally:
            self.editor.blockSignals(blocked)
