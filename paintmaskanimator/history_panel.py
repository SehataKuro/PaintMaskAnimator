"""History panel: a branching, block-graph view of the undo/redo stacks.

The canvas keeps a delta-based ``undo_stack`` / ``redo_stack`` plus the futures
that were split off when a new edit followed an undo (``history_branches``).
This panel does not own any state of its own; it lays the history out on a big
2-D canvas of blocks. The main line runs straight down column 0 (oldest past
state at the top, the current state highlighted, future redo states dimmed
below). Each split-off future becomes its own downward column of blocks,
diverging from the block it branched off; several branches sharing one
divergence point fan out into separate columns side by side. Clicking a
main-line block jumps there by replaying the right number of undo/redo steps;
clicking a branch block switches the canvas over to that future instead.
"""
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget
from .i18n import tr
from .undo_entries import history_label_for
from .logging_setup import get_logger

log = get_logger(__name__)

_KIND_MAIN = "main"       # 値＝移動量（負=Undo、正=Redo）
_KIND_BRANCH = "branch"   # 値＝canvas.history_branches の添字

# ブロック（1状態）とグリッドの寸法。
_BLOCK_W = 128   # ブロックの横幅。
_BLOCK_H = 26    # ブロックの高さ。
_COL_GAP = 28    # 列（レーン）どうしの隙間。
_ROW_GAP = 16    # 行どうしの隙間。
_COL_PITCH = _BLOCK_W + _COL_GAP
_ROW_PITCH = _BLOCK_H + _ROW_GAP
_MARGIN = 14     # キャンバス端からの余白。
_RADIUS = 6      # ブロック角の丸み。


def _block_rect(col, row):
    """列・行のグリッド座標を、そのブロックの矩形に変換する。"""
    x = _MARGIN + col * _COL_PITCH
    y = _MARGIN + row * _ROW_PITCH
    return QRectF(x, y, _BLOCK_W, _BLOCK_H)


class _HistoryGraph(QWidget):
    """undo/redo/分岐を、ブロックと接続線で描く2Dキャンバス。"""

    jumpRequested = Signal(int)
    # (分岐の添字, 切り替え後に進めるRedo回数)。クリックしたブロックまで進む。
    branchSwitchRequested = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nodes = []          # 描画・当たり判定用のノード情報。
        self._current_index = -1  # 現在ノードの添字。
        self.setMouseTracking(True)
        self.setMinimumWidth(_MARGIN * 2 + _BLOCK_W)

    def set_nodes(self, nodes, current_index):
        self._nodes = nodes
        self._current_index = current_index
        max_col = max((n["col"] for n in nodes), default=0)
        max_row = max((n["row"] for n in nodes), default=0)
        width = _MARGIN * 2 + (max_col + 1) * _BLOCK_W + max_col * _COL_GAP
        height = _MARGIN * 2 + (max_row + 1) * _BLOCK_H + max_row * _ROW_GAP
        self.setMinimumSize(int(width), int(height))
        self.resize(int(max(width, self.width())), int(height))
        self.update()

    def _clickable(self, node):
        return node["kind"] == _KIND_BRANCH or int(node["value"]) != 0

    # --- 当たり判定・操作 -------------------------------------------------
    def _node_at(self, pos):
        for node in self._nodes:
            if _block_rect(node["col"], node["row"]).contains(pos):
                return node
        return None

    def mousePressEvent(self, event):
        node = self._node_at(event.position())
        if node is None:
            return
        if node["kind"] == _KIND_MAIN:
            delta = int(node["value"])
            if delta != 0:
                self.jumpRequested.emit(delta)
        elif node["kind"] == _KIND_BRANCH:
            self.branchSwitchRequested.emit(
                int(node["value"]), int(node["steps"])
            )

    def mouseMoveEvent(self, event):
        node = self._node_at(event.position())
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if node is not None and self._clickable(node)
            else Qt.CursorShape.ArrowCursor
        )

    # --- 描画 -------------------------------------------------------------
    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        pal = self.palette()
        text_color = pal.text().color()
        mid_color = pal.mid().color()
        accent = pal.highlight().color()
        accent_text = pal.highlightedText().color()
        past_fill = QColor(mid_color)
        past_fill.setAlpha(60)

        # まず接続線を下地として描く（ブロックの背面）。
        for node in self._nodes:
            src = node.get("from")
            if src is None:
                continue
            parent = _block_rect(src[0], src[1])
            child = _block_rect(node["col"], node["row"])
            p0x = parent.center().x()
            p0y = parent.bottom()
            p1x = child.center().x()
            p1y = child.top()
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if node["curve"]:
                # 別の列へ枝分かれ：S字カーブでつなぐ。
                painter.setPen(QPen(mid_color, 2))
                path = QPainterPath()
                path.moveTo(p0x, p0y)
                mid_y = (p0y + p1y) / 2
                path.cubicTo(p0x, mid_y, p1x, mid_y, p1x, p1y)
                painter.drawPath(path)
            else:
                # 同じ列の連続：まっすぐ縦線。
                pen = QPen(mid_color, 2)
                painter.setPen(pen)
                painter.drawLine(int(p0x), int(p0y), int(p1x), int(p1y))

        # ブロック本体。
        normal_font = self.font()
        bold_font = self.font()
        bold_font.setBold(True)
        for index, node in enumerate(self._nodes):
            rect = _block_rect(node["col"], node["row"])
            is_current = (
                node["kind"] == _KIND_MAIN and index == self._current_index
            )
            is_branch = node["kind"] == _KIND_BRANCH

            if is_current:
                painter.setPen(QPen(accent, 2))
                painter.setBrush(accent)
                text_pen = accent_text
                font = bold_font
            elif is_branch:
                pen = QPen(mid_color, 1.5)
                pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                text_pen = mid_color
                font = normal_font
            elif node["is_future"]:
                painter.setPen(QPen(mid_color, 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                text_pen = mid_color
                font = normal_font
            else:
                painter.setPen(QPen(mid_color, 1.5))
                painter.setBrush(past_fill)
                text_pen = text_color
                font = normal_font

            painter.drawRoundedRect(rect, _RADIUS, _RADIUS)

            painter.setFont(font)
            painter.setPen(text_pen)
            fm = painter.fontMetrics()
            text_rect = rect.adjusted(8, 0, -8, 0)
            label = fm.elidedText(
                node["label"],
                Qt.TextElideMode.ElideRight,
                int(text_rect.width()),
            )
            painter.drawText(
                text_rect,
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                label,
            )

        painter.end()


class HistoryPanel(QWidget):
    # クリックされた行までの移動量。負ならUndo、正ならRedoの回数。
    jumpRequested = Signal(int)
    # クリックされたブランチの添字と、切り替え後に進めるRedo回数。
    branchSwitchRequested = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(180)
        self._canvas = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        layout.addWidget(QLabel(tr("<b>ヒストリー</b>")))
        note = QLabel(
            tr("操作の履歴です。ブロックをクリックすると、その状態まで一気に戻る／"
            "進むします。戻ってから編集し直したときの元の履歴は右隣の列に"
            "分岐として残り、同じ地点から複数分岐したときは横に並びます。"
            "点線のブロックをクリックでそちらへ戻せます。")
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size:10px;")
        layout.addWidget(note)

        self.graph = _HistoryGraph()
        self.graph.jumpRequested.connect(self.jumpRequested)
        self.graph.branchSwitchRequested.connect(self.branchSwitchRequested)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setWidget(self.graph)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        layout.addWidget(self._scroll, 1)

    def set_canvas(self, canvas):
        self._canvas = canvas
        self.refresh()

    def _main_rows(self, undo_stack, redo_stack):
        """本線の行を ``(ラベル, 移動量, 分岐位置, 未来か)`` の並びで返す。

        分岐位置はその行が表す状態でのundo_stackの長さ。この値がそのまま
        本線ブロックの行番号（row）になり、分岐はこの行から下へ伸びる。
        """
        n = len(undo_stack)
        # 何も積まれていない最初の状態。全部Undoすればここへ戻れる。
        rows = [(tr("開始状態"), -n, 0, False)]
        for i, entry in enumerate(undo_stack):
            # 最後のUndoエントリで到達するのが現在の状態（移動量0）。
            rows.append((history_label_for(entry), i - (n - 1), i + 1, False))
        # 未来（Redo）。スタック末尾が次に進む先なので、近い順に並べる。
        for k in range(len(redo_stack)):
            entry = redo_stack[len(redo_stack) - 1 - k]
            rows.append((history_label_for(entry), k + 1, n + k + 1, True))
        return rows

    def refresh(self):
        """undo/redoスタックと分岐の現在の内容からブロック配置を作り直す。"""
        canvas = self._canvas
        if canvas is None:
            return
        undo_stack = getattr(canvas, "undo_stack", []) or []
        redo_stack = getattr(canvas, "redo_stack", []) or []
        branches = getattr(canvas, "history_branches", []) or []

        rows = self._main_rows(undo_stack, redo_stack)
        # 先頭に「開始状態」行があるぶん、現在行はundo段数と一致する。
        current_row = len(undo_stack)

        nodes = []
        current_index = -1

        # 本線は列0にまっすぐ並ぶ。行番号はその状態の分岐位置に一致する。
        for label, delta, position, is_future in rows:
            display = tr("{label}（現在）").format(label=label) if position == current_row else label
            src = None if position == 0 else (0, position - 1)
            if position == current_row:
                current_index = len(nodes)
            nodes.append({
                "kind": _KIND_MAIN,
                "value": int(delta),
                "label": display,
                "col": 0,
                "row": position,
                "is_future": is_future,
                "from": src,
                "curve": False,
            })

        # 分岐を列に割り当てる。行範囲が重ならない分岐は同じ列を使い回すが、
        # 同じ分岐点から出た複数の分岐は必ず別の列になり、横に並ぶ。
        col_spans = {}  # col -> [(row_start, row_end), ...]

        def alloc_col(start, end):
            col = 1
            while True:
                spans = col_spans.setdefault(col, [])
                if all(end < s or start > e for s, e in spans):
                    spans.append((start, end))
                    return col
                col += 1

        indexed = sorted(
            enumerate(branches),
            key=lambda pair: (int(pair[1].position), pair[0]),
        )
        for branch_index, branch in indexed:
            entries = branch.entries
            total = len(entries)
            if total == 0:
                continue
            position = int(branch.position)
            start_row = position + 1
            end_row = position + total
            col = alloc_col(start_row, end_row)

            # entries は redo_stack のコピー。末尾が分岐直後の1手なので、
            # 分岐点に近い順（末尾→先頭）に上から並べる。
            prev = (0, position)  # 先頭ブロックは本線の分岐点から枝分かれ。
            for k in range(total):
                entry = entries[total - 1 - k]
                row = start_row + k
                label = history_label_for(entry)
                if k == 0:
                    label = (
                        tr("{label}（分岐 {total}件）").format(label=label, total=total)
                        if total > 1 else tr("{label}（分岐）").format(label=label)
                    )
                nodes.append({
                    "kind": _KIND_BRANCH,
                    "value": int(branch_index),
                    # 分岐へ切り替えたあと、このブロックまで進むRedo回数。
                    "steps": k + 1,
                    "label": label,
                    "col": col,
                    "row": row,
                    "is_future": True,
                    "from": prev,
                    "curve": (k == 0),
                })
                prev = (col, row)

        self.graph.set_nodes(nodes, current_index)

        # 現在ブロックが見えるようスクロールする。
        if current_index >= 0:
            rect = _block_rect(
                nodes[current_index]["col"], nodes[current_index]["row"]
            )
            self._scroll.ensureVisible(
                int(rect.center().x()), int(rect.center().y()),
                _BLOCK_W, _ROW_PITCH,
            )
