"""History panel: a branching, Photoshop-style view of the undo/redo stacks.

The canvas keeps a delta-based ``undo_stack`` / ``redo_stack`` plus the futures
that were split off when a new edit followed an undo (``history_branches``).
This panel does not own any state of its own; it renders the main line as one
list of states (oldest past state at the top, the current state highlighted,
future redo states dimmed below) and hangs each split-off future under the row
it diverged from. Clicking a main-line row jumps there by replaying the right
number of undo/redo steps; clicking a branch switches the canvas over to that
future instead.
"""
from .common import *  # noqa: F401,F403
from .undo_entries import history_label_for
from .logging_setup import get_logger

log = get_logger(__name__)

# ツリー項目に持たせる情報の種別と値。
_ROLE_KIND = Qt.ItemDataRole.UserRole
_ROLE_VALUE = Qt.ItemDataRole.UserRole + 1
_KIND_MAIN = "main"       # 値＝移動量（負=Undo、正=Redo）
_KIND_BRANCH = "branch"   # 値＝canvas.history_branches の添字


class HistoryPanel(QWidget):
    # クリックされた行までの移動量。負ならUndo、正ならRedoの回数。
    jumpRequested = Signal(int)
    # クリックされたブランチの添字（canvas.history_branches内）。
    branchSwitchRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(180)
        self._canvas = None
        self._refreshing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        layout.addWidget(QLabel("<b>ヒストリー</b>"))
        note = QLabel(
            "操作の履歴です。行をクリックすると、その状態まで一気に戻る／"
            "進むします。戻ってから編集し直したときの元の履歴は"
            "「⑂」のブランチとして残り、クリックでそちらへ戻せます。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size:10px;")
        layout.addWidget(note)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree, 1)

    def set_canvas(self, canvas):
        self._canvas = canvas
        self.refresh()

    def _on_item_clicked(self, item, _column=0):
        if self._refreshing or item is None:
            return
        kind = item.data(0, _ROLE_KIND)
        value = item.data(0, _ROLE_VALUE)
        if kind == _KIND_MAIN:
            delta = int(value or 0)
            if delta != 0:
                self.jumpRequested.emit(delta)
        elif kind == _KIND_BRANCH:
            self.branchSwitchRequested.emit(int(value))

    def _main_rows(self, undo_stack, redo_stack):
        """本線の行を ``(ラベル, 移動量, 分岐位置, 未来か)`` の並びで返す。

        分岐位置はその行が表す状態でのundo_stackの長さ。ブランチはこの値で
        どの行にぶら下がるかが決まる。
        """
        n = len(undo_stack)
        # 何も積まれていない最初の状態。全部Undoすればここへ戻れる。
        rows = [("開始状態", -n, 0, False)]
        for i, entry in enumerate(undo_stack):
            # 最後のUndoエントリで到達するのが現在の状態（移動量0）。
            rows.append((history_label_for(entry), i - (n - 1), i + 1, False))
        # 未来（Redo）。スタック末尾が次に進む先なので、近い順に並べる。
        for k in range(len(redo_stack)):
            entry = redo_stack[len(redo_stack) - 1 - k]
            rows.append((history_label_for(entry), k + 1, n + k + 1, True))
        return rows

    def refresh(self):
        """undo/redoスタックと分岐の現在の内容から履歴一覧を作り直す。"""
        canvas = self._canvas
        if canvas is None:
            return
        undo_stack = getattr(canvas, "undo_stack", []) or []
        redo_stack = getattr(canvas, "redo_stack", []) or []
        branches = getattr(canvas, "history_branches", []) or []

        text_color = self.palette().text().color()
        mid_color = self.palette().mid().color()

        rows = self._main_rows(undo_stack, redo_stack)
        # 先頭に「開始状態」行があるぶん、現在行はundo段数と一致する。
        current_row = len(undo_stack)

        # 分岐位置ごとにブランチをまとめる。
        by_position = {}
        for index, branch in enumerate(branches):
            by_position.setdefault(int(branch.position), []).append(
                (index, branch)
            )

        self._refreshing = True
        self.tree.clear()
        current_item = None
        row_items = {}
        for index, (label, delta, position, is_future) in enumerate(rows):
            item = QTreeWidgetItem(self.tree)
            if index == current_row:
                item.setText(0, f"▶ {label}（現在）")
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                item.setForeground(0, text_color)
                current_item = item
            else:
                item.setText(0, f"　{label}")
                item.setForeground(0, mid_color if is_future else text_color)
            item.setData(0, _ROLE_KIND, _KIND_MAIN)
            item.setData(0, _ROLE_VALUE, int(delta))
            row_items[position] = item

        for position, entries in sorted(by_position.items()):
            parent = row_items.get(position)
            for branch_index, branch in entries:
                node = (
                    QTreeWidgetItem(parent)
                    if parent is not None
                    else QTreeWidgetItem(self.tree)
                )
                node.setText(
                    0,
                    f"⑂ {branch.label}（分岐 {len(branch.entries)}件）",
                )
                node.setForeground(0, mid_color)
                node.setToolTip(
                    0,
                    "クリックするとこの分岐の履歴に切り替えます。"
                    "いま辿っている履歴は分岐として残ります。",
                )
                node.setData(0, _ROLE_KIND, _KIND_BRANCH)
                node.setData(0, _ROLE_VALUE, int(branch_index))
                if parent is not None:
                    parent.setExpanded(True)

        # 現在行を選択状態＆可視にする。
        if current_item is not None:
            self.tree.setCurrentItem(current_item)
            self.tree.scrollToItem(current_item)
        self._refreshing = False
