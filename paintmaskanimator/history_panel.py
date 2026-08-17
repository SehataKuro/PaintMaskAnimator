"""History panel: a branching, node-graph view of the undo/redo stacks.

The canvas keeps a delta-based ``undo_stack`` / ``redo_stack`` plus the futures
that were split off when a new edit followed an undo (``history_branches``).
This panel does not own any state of its own; it draws the main line as a
vertical string of nodes (oldest past state at the top, the current state
highlighted, future redo states dimmed below) connected by a trunk, and hangs
each split-off future off the node it diverged from as a side branch. Clicking
a main-line node jumps there by replaying the right number of undo/redo steps;
clicking a branch node switches the canvas over to that future instead.
"""
from .common import *  # noqa: F401,F403
from .undo_entries import history_label_for
from .logging_setup import get_logger

log = get_logger(__name__)

_KIND_MAIN = "main"       # 値＝移動量（負=Undo、正=Redo）
_KIND_BRANCH = "branch"   # 値＝canvas.history_branches の添字

# ノード描画の寸法。
_ROW_H = 30       # 1行の高さ。
_LANE_W = 22      # レーン1段ぶんの横インデント。
_MARGIN_X = 16    # 左端から本線ノード中心までの距離。
_NODE_R = 6       # ノード（丸）の半径。
_TEXT_GAP = 12    # ノード中心からラベル先頭までの距離。


class _HistoryGraph(QWidget):
    """undo/redo/分岐を、線でつないだノード図として描くキャンバス。"""

    jumpRequested = Signal(int)
    branchSwitchRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nodes = []          # 描画・当たり判定用のノード情報。
        self._current_index = -1  # 本線の現在ノードの visual 添字。
        self.setMouseTracking(True)
        self.setMinimumWidth(180)

    def set_nodes(self, nodes, current_index):
        self._nodes = nodes
        self._current_index = current_index
        height = _MARGIN_X + len(nodes) * _ROW_H
        self.setMinimumHeight(height)
        self.resize(max(self.width(), self.minimumWidth()), height)
        self.update()

    # --- 当たり判定・操作 -------------------------------------------------
    def _node_at(self, y):
        for node in self._nodes:
            top = node["y"] - _ROW_H / 2
            if top <= y <= top + _ROW_H:
                return node
        return None

    def mousePressEvent(self, event):
        node = self._node_at(event.position().y())
        if node is None:
            return
        if node["kind"] == _KIND_MAIN:
            delta = int(node["value"])
            if delta != 0:
                self.jumpRequested.emit(delta)
        elif node["kind"] == _KIND_BRANCH:
            self.branchSwitchRequested.emit(int(node["value"]))

    def mouseMoveEvent(self, event):
        node = self._node_at(event.position().y())
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if node is not None and (
                node["kind"] == _KIND_BRANCH or int(node["value"]) != 0
            )
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
        trunk_color = QColor(mid_color)

        main_nodes = [n for n in self._nodes if n["kind"] == _KIND_MAIN]

        # 本線の幹（隣り合う本線ノードを縦線でつなぐ）。
        trunk_pen = QPen(trunk_color, 2)
        painter.setPen(trunk_pen)
        for prev, node in zip(main_nodes, main_nodes[1:]):
            x = _MARGIN_X
            painter.drawLine(int(x), int(prev["y"]), int(x), int(node["y"]))

        # 分岐は親ノードから曲線でぶら下げる。
        for node in self._nodes:
            if node["kind"] != _KIND_BRANCH:
                continue
            x0 = _MARGIN_X
            y0 = node["parent_y"]
            x1 = _MARGIN_X + node["lane"] * _LANE_W
            y1 = node["y"]
            path = QPainterPath()
            path.moveTo(x0, y0)
            path.cubicTo(x0, (y0 + y1) / 2, x1, y0, x1, y1)
            painter.setPen(QPen(mid_color, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        # ノード本体とラベル。self.font() は呼ぶたび別インスタンスを返すので、
        # 太字化しても通常フォントには影響しない。
        normal_font = self.font()
        bold_font = self.font()
        bold_font.setBold(True)
        for index, node in enumerate(self._nodes):
            cx = _MARGIN_X + node["lane"] * _LANE_W
            cy = node["y"]
            is_current = (
                node["kind"] == _KIND_MAIN and index == self._current_index
            )

            if node["kind"] == _KIND_BRANCH:
                # 分岐は中抜きのひし形で本線と区別する。
                self._draw_diamond(painter, cx, cy, _NODE_R, mid_color, hollow=True)
                label_color = mid_color
            elif is_current:
                # 現在位置はアクセント色の塗り＋外周リング。
                painter.setPen(QPen(accent, 2))
                painter.setBrush(accent)
                painter.drawEllipse(QPointF(cx, cy), _NODE_R, _NODE_R)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(accent, 2))
                painter.drawEllipse(QPointF(cx, cy), _NODE_R + 3, _NODE_R + 3)
                label_color = text_color
            elif node["is_future"]:
                # 未来（Redo先）は中抜きの丸で淡く。
                painter.setPen(QPen(mid_color, 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPointF(cx, cy), _NODE_R, _NODE_R)
                label_color = mid_color
            else:
                # 過去の確定状態は塗りつぶしの丸。
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(text_color)
                painter.drawEllipse(QPointF(cx, cy), _NODE_R, _NODE_R)
                label_color = text_color

            painter.setFont(bold_font if is_current else normal_font)
            painter.setPen(label_color)
            tx = cx + _NODE_R + _TEXT_GAP
            fm = painter.fontMetrics()
            ty = cy + fm.ascent() / 2 - 1
            painter.drawText(int(tx), int(ty), node["label"])

        painter.end()

    @staticmethod
    def _draw_diamond(painter, cx, cy, r, color, hollow=False):
        poly = QPolygonF([
            QPointF(cx, cy - r),
            QPointF(cx + r, cy),
            QPointF(cx, cy + r),
            QPointF(cx - r, cy),
        ])
        painter.setPen(QPen(color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush if hollow else color)
        painter.drawPolygon(poly)


class HistoryPanel(QWidget):
    # クリックされた行までの移動量。負ならUndo、正ならRedoの回数。
    jumpRequested = Signal(int)
    # クリックされたブランチの添字（canvas.history_branches内）。
    branchSwitchRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(180)
        self._canvas = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        layout.addWidget(QLabel("<b>ヒストリー</b>"))
        note = QLabel(
            "操作の履歴です。ノードをクリックすると、その状態まで一気に戻る／"
            "進むします。戻ってから編集し直したときの元の履歴は"
            "「◇」のブランチとして残り、クリックでそちらへ戻せます。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size:10px;")
        layout.addWidget(note)

        self.graph = _HistoryGraph()
        self.graph.jumpRequested.connect(self.jumpRequested)
        self.graph.branchSwitchRequested.connect(self.branchSwitchRequested)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self.graph)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        layout.addWidget(self._scroll, 1)

    def set_canvas(self, canvas):
        self._canvas = canvas
        self.refresh()

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
        """undo/redoスタックと分岐の現在の内容からノード図を作り直す。"""
        canvas = self._canvas
        if canvas is None:
            return
        undo_stack = getattr(canvas, "undo_stack", []) or []
        redo_stack = getattr(canvas, "redo_stack", []) or []
        branches = getattr(canvas, "history_branches", []) or []

        rows = self._main_rows(undo_stack, redo_stack)
        # 先頭に「開始状態」行があるぶん、現在行はundo段数と一致する。
        current_row = len(undo_stack)

        # 分岐位置ごとにブランチをまとめる。
        by_position = {}
        for index, branch in enumerate(branches):
            by_position.setdefault(int(branch.position), []).append(
                (index, branch)
            )

        # 本線ノードと分岐ノードを縦に並べて配置する。分岐は親行の直後に差し込む。
        nodes = []
        current_index = -1
        slot = 0

        def y_for(s):
            return _MARGIN_X + s * _ROW_H + _ROW_H / 2

        for label, delta, position, is_future in rows:
            main_y = y_for(slot)
            if position == current_row:
                display = f"{label}（現在）"
            elif is_future:
                display = label
            else:
                display = label
            main_index = len(nodes)
            nodes.append({
                "kind": _KIND_MAIN,
                "value": int(delta),
                "label": display,
                "lane": 0,
                "y": main_y,
                "is_future": is_future,
                "parent_y": main_y,
            })
            if position == current_row:
                current_index = main_index
            slot += 1

            for branch_index, branch in by_position.get(position, []):
                branch_y = y_for(slot)
                nodes.append({
                    "kind": _KIND_BRANCH,
                    "value": int(branch_index),
                    "label": f"{branch.label}（分岐 {len(branch.entries)}件）",
                    "lane": 1,
                    "y": branch_y,
                    "is_future": True,
                    "parent_y": main_y,
                })
                slot += 1

        self.graph.set_nodes(nodes, current_index)

        # 現在ノードが見えるようスクロールする。
        if current_index >= 0:
            target = int(nodes[current_index]["y"])
            self._scroll.ensureVisible(0, target, 0, _ROW_H)
