"""History panel: a linear, Photoshop-style view of the undo/redo stacks.

The canvas keeps a delta-based ``undo_stack`` / ``redo_stack``. This panel does
not own any state of its own; it renders those two stacks as one linear list of
states (oldest past state at the top, the current state highlighted, future
redo states dimmed below) and lets the user click a row to jump straight there
by replaying the right number of undo/redo steps.
"""
from .common import *  # noqa: F401,F403
from .undo_entries import history_label_for
from .logging_setup import get_logger

log = get_logger(__name__)


class HistoryPanel(QWidget):
    # クリックされた行までの移動量。負ならUndo、正ならRedoの回数。
    jumpRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(180)
        self._canvas = None
        self._refreshing = False
        # 各行の移動量（現在位置=0、過去は負、未来は正）を保持する。
        self._row_deltas = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        layout.addWidget(QLabel("<b>ヒストリー</b>"))
        note = QLabel(
            "操作の履歴です。行をクリックすると、その状態まで一気に戻る／進むします。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size:10px;")
        layout.addWidget(note)

        self.list = QListWidget()
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.list.setUniformItemSizes(True)
        self.list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list, 1)

    def set_canvas(self, canvas):
        self._canvas = canvas
        self.refresh()

    def _on_item_clicked(self, item):
        if self._refreshing:
            return
        row = self.list.row(item)
        if not (0 <= row < len(self._row_deltas)):
            return
        delta = self._row_deltas[row]
        if delta != 0:
            self.jumpRequested.emit(delta)

    def refresh(self):
        """undo/redoスタックの現在の内容から履歴一覧を作り直す。"""
        canvas = self._canvas
        if canvas is None:
            return
        undo_stack = getattr(canvas, "undo_stack", []) or []
        redo_stack = getattr(canvas, "redo_stack", []) or []

        text_color = self.palette().text().color()
        mid_color = self.palette().mid().color()

        rows = []  # (ラベル, 移動量, 未来かどうか)
        n = len(undo_stack)
        if n == 0:
            rows.append(("現在の状態", 0, False))
        else:
            for i, entry in enumerate(undo_stack):
                # 最後のUndoエントリで到達するのが現在の状態（移動量0）。
                rows.append((history_label_for(entry), i - (n - 1), False))
        # 未来（Redo）。スタック末尾が次に進む先なので、近い順に並べる。
        for k in range(len(redo_stack)):
            entry = redo_stack[len(redo_stack) - 1 - k]
            rows.append((history_label_for(entry), k + 1, True))

        current_row = 0 if n == 0 else n - 1

        self._refreshing = True
        self.list.clear()
        self._row_deltas = [delta for _label, delta, _future in rows]
        for index, (label, _delta, is_future) in enumerate(rows):
            if index == current_row:
                display = f"▶ {label}（現在）"
            elif is_future:
                display = f"　{label}"
            else:
                display = f"　{label}"
            list_item = QListWidgetItem(display)
            if index == current_row:
                font = list_item.font()
                font.setBold(True)
                list_item.setForeground(text_color)
                list_item.setFont(font)
            elif is_future:
                list_item.setForeground(mid_color)
            else:
                list_item.setForeground(text_color)
            self.list.addItem(list_item)
        # 現在行を選択状態＆可視にする。
        if 0 <= current_row < self.list.count():
            self.list.setCurrentRow(current_row)
            self.list.scrollToItem(self.list.item(current_row))
        self._refreshing = False
