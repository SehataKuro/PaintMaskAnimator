"""Cut-folder export dialog and its block-template editor.

:class:`TemplateEditor` is a single-line, wrapping editor whose content is a
list of :class:`~.cut_folder.Token` -- literal characters and field blocks.
Typing filters a suggestion list of blocks; text that matches no block is kept
as literal separators. The caret moves through characters and whole blocks,
and the platform's word-movement keys (⌥←/→ on macOS, Ctrl+←/→ elsewhere) jump
by *chunk*: a block, a run of letters/digits, or a run of one symbol.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFontMetrics, QKeySequence, QPainter, QPalette, QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QDialog, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSizePolicy, QStyle,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import config
from . import cut_folder as cf
from .cut_folder import Token
from .i18n import tr

CONFIG_KEY = "cut_folder_export"


def field_label(name):
    return {
        "title": tr("作品名"),
        "scene": tr("シーン"),
        "episode": tr("話数"),
        "cut": tr("カット名"),
        "cell": tr("セル名"),
        "number": tr("セル番号"),
    }[name]


def _chunk_kind(token):
    if token.is_field:
        return ("block", id(token))
    character = token.text or ""
    if character.isalnum():
        return ("word",)
    return ("symbol", character)


# --- editor ------------------------------------------------------------------

class _SuggestionList(QListWidget):
    """Overlay list of blocks; never takes focus so the editor keeps typing."""

    picked = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.itemClicked.connect(self._clicked)
        self.hide()

    def _clicked(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            self.picked.emit(name)

    def fill(self, header, entries):
        self.clear()
        head = QListWidgetItem(header)
        head.setFlags(Qt.ItemFlag.NoItemFlags)
        muted = self.palette().color(QPalette.ColorRole.PlaceholderText)
        head.setForeground(muted)
        self.addItem(head)
        for name, label, sample in entries:
            item = QListWidgetItem(f"{label}    {sample}" if sample else label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.addItem(item)
        self.setCurrentRow(1 if entries else -1)
        rows = self.count()
        height = sum(self.sizeHintForRow(r) for r in range(rows)) + 2 * self.frameWidth() + 4
        width = max(200, self.sizeHintForColumn(0) + 24)
        self.resize(width, height)

    def move_selection(self, step):
        rows = self.count()
        if rows <= 1:
            return
        row = self.currentRow()
        row = 1 + ((max(row, 1) - 1 + step) % (rows - 1))
        self.setCurrentRow(row)

    def current_name(self):
        item = self.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


class TemplateEditor(QWidget):
    """Edits one template as characters and blocks with a free caret."""

    changed = Signal()
    blockClicked = Signal(int, QRect)

    PAD_X = 6
    PAD_Y = 4
    CHIP_PAD = 7
    GAP = 2

    def __init__(self, allowed_fields, sample_for=None, is_missing=None, parent=None):
        super().__init__(parent)
        self.allowed_fields = tuple(allowed_fields)
        self.sample_for = sample_for or (lambda _name: "")
        self.is_missing = is_missing or (lambda _name: False)
        self.tokens = []
        self.caret = 0
        self.pending = ""
        self.preedit = ""
        self._suggestions = None
        self._layout_cache = None
        self.placeholder = ""
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    # model ------------------------------------------------------------------
    def set_template(self, template):
        self.tokens = cf.explode(template)
        self.caret = len(self.tokens)
        self.pending = ""
        self._relayout()

    def template(self):
        return cf.compact(self.tokens)

    def replace_token(self, index, token):
        if 0 <= index < len(self.tokens):
            self.tokens[index] = token
            self._relayout()
            self.changed.emit()

    def remove_token(self, index):
        if 0 <= index < len(self.tokens):
            del self.tokens[index]
            self.caret = min(index, len(self.tokens))
            self._relayout()
            self.changed.emit()

    def _insert(self, tokens):
        self.tokens[self.caret:self.caret] = tokens
        self.caret += len(tokens)
        self._relayout()
        self.changed.emit()

    def commit_pending(self):
        if self.pending:
            text, self.pending = self.pending, ""
            self._insert([Token(text=character) for character in text])
            self._update_suggestions()

    def insert_field(self, name):
        if self.pending and not self._matches(self.pending):
            self.commit_pending()
        self.pending = ""
        defaults = {"episode": 2, "cut": 3, "number": 4}
        self._insert([Token(field=name, digits=defaults.get(name))])
        self._update_suggestions()

    # geometry ---------------------------------------------------------------
    def _chip_label(self, token):
        label = field_label(token.field)
        details = []
        if token.field in cf.NUMERIC_FIELDS:
            details.append(tr("{n}桁").format(n=token.digits) if token.digits else tr("可変"))
        if token.case == "upper":
            details.append(tr("大文字"))
        elif token.case == "lower":
            details.append(tr("小文字"))
        return label, "・".join(details)

    def _chip_width(self, token, fm):
        label, detail = self._chip_label(token)
        width = fm.horizontalAdvance(label) + 2 * self.CHIP_PAD
        for affix in (token.prefix, token.suffix):
            if affix:
                width += fm.horizontalAdvance(affix) + 10
        if detail:
            width += fm.horizontalAdvance(detail) + 10
        return width

    @staticmethod
    def _shown_char(token):
        return "␣" if token.text == " " else (token.text or "")

    def _compute_layout(self, width):
        fm = QFontMetrics(self.font())
        line_h = fm.height() + 8
        x, y = self.PAD_X, self.PAD_Y
        right = max(width - self.PAD_X, self.PAD_X + 40)
        items = []  # (kind, index, rect)
        carets = {}
        pending_text = self.pending + self.preedit
        pending_rect = None

        def place(w):
            nonlocal x, y
            if x + w > right and x > self.PAD_X:
                x = self.PAD_X
                y += line_h
            rect = QRect(x, y, w, line_h - 2)
            x += w
            return rect

        for index in range(len(self.tokens) + 1):
            if index == self.caret and pending_text and self.hasFocus():
                pending_rect = place(fm.horizontalAdvance(pending_text) + 2)
            if index == len(self.tokens):
                carets[index] = QPoint(x, y)
                break
            token = self.tokens[index]
            if token.is_field:
                rect = place(self._chip_width(token, fm) + 2 * self.GAP)
                items.append(("chip", index, rect.adjusted(self.GAP, 1, -self.GAP, -1)))
            else:
                rect = place(fm.horizontalAdvance(self._shown_char(token)) + 1)
                items.append(("char", index, rect))
            # The caret *before* a token sits at its left edge -- on the line
            # the token wrapped to -- unless pending text is shown there.
            carets[index] = rect.topLeft()
        if pending_rect is not None:
            carets[self.caret] = QPoint(pending_rect.right() + 1, pending_rect.top())
        height = y + line_h + self.PAD_Y
        return {
            "items": items, "carets": carets, "pending": pending_rect,
            "height": max(height, line_h + 2 * self.PAD_Y), "line_h": line_h,
        }

    def _layout(self):
        if self._layout_cache is None or self._layout_cache[0] != self.width():
            self._layout_cache = (self.width(), self._compute_layout(self.width()))
        return self._layout_cache[1]

    def _relayout(self):
        self._layout_cache = None
        self.updateGeometry()
        self.update()
        self._place_suggestions()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._compute_layout(width)["height"]

    def sizeHint(self):
        return QSize(240, self.heightForWidth(max(self.width(), 240)))

    def minimumSizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(120, fm.height() + 8 + 2 * self.PAD_Y)

    def resizeEvent(self, event):
        self._layout_cache = None
        super().resizeEvent(event)
        self._place_suggestions()

    def caret_rect(self):
        layout = self._layout()
        point = layout["carets"].get(self.caret, QPoint(self.PAD_X, self.PAD_Y))
        return QRect(point.x(), point.y(), 1, layout["line_h"] - 2)

    # painting ---------------------------------------------------------------
    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        fm = QFontMetrics(self.font())
        frame = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(pal.color(QPalette.ColorRole.Highlight) if self.hasFocus()
                            else pal.color(QPalette.ColorRole.Mid), 1.5 if self.hasFocus() else 1))
        painter.setBrush(pal.color(QPalette.ColorRole.Base))
        painter.drawRoundedRect(frame, 6, 6)

        accent = pal.color(QPalette.ColorRole.Highlight)
        text_color = pal.color(QPalette.ColorRole.Text)
        chip_bg = QColor(accent)
        chip_bg.setAlpha(55)
        warn_bg = QColor(230, 160, 40, 90)
        char_bg = QColor(text_color)
        char_bg.setAlpha(18)
        layout = self._layout()
        baseline_off = (layout["line_h"] - 2 + fm.ascent() - fm.descent()) // 2

        for kind, index, rect in layout["items"]:
            token = self.tokens[index]
            if kind == "char":
                painter.fillRect(rect, char_bg)
                painter.setPen(text_color)
                painter.drawText(rect.left(), rect.top() + baseline_off, self._shown_char(token))
                continue
            missing = token.field in cf.VALUE_FIELDS and self.is_missing(token.field)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(warn_bg if missing else chip_bg)
            painter.drawRoundedRect(QRectF(rect), 5, 5)
            x = rect.left() + self.CHIP_PAD
            base = rect.top() + baseline_off - 1
            label, detail = self._chip_label(token)
            if token.prefix:
                x = self._draw_affix(painter, fm, token.prefix, x, rect, base, text_color)
            painter.setPen(text_color)
            painter.drawText(x, base, label)
            x += fm.horizontalAdvance(label)
            if token.suffix:
                x = self._draw_affix(painter, fm, token.suffix, x + 5, rect, base, text_color) - 5
            if detail:
                x += 5
                muted = QColor(text_color)
                muted.setAlpha(150)
                painter.setPen(QPen(muted, 1))
                painter.drawLine(x, rect.top() + 5, x, rect.bottom() - 5)
                painter.drawText(x + 5, base, detail)

        if not self.tokens and not self.pending and not self.preedit and self.placeholder:
            painter.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
            painter.drawText(self.PAD_X + 2, self.PAD_Y + baseline_off, self.placeholder)

        pending_rect = layout["pending"]
        if pending_rect is not None:
            painter.setPen(text_color)
            painter.drawText(pending_rect.left() + 1, pending_rect.top() + baseline_off,
                             self.pending + self.preedit)
            underline = QPen(accent, 1, Qt.PenStyle.DotLine)
            painter.setPen(underline)
            painter.drawLine(pending_rect.left(), pending_rect.bottom() - 1,
                             pending_rect.right(), pending_rect.bottom() - 1)

        if self.hasFocus():
            caret = self.caret_rect()
            painter.fillRect(QRect(caret.left(), caret.top() + 2, 1, caret.height() - 4), text_color)

    def _draw_affix(self, painter, fm, affix, x, rect, base, color):
        width = fm.horizontalAdvance(affix) + 6
        box = QRectF(x - 1, rect.top() + 3, width, rect.height() - 6)
        dashed = QColor(color)
        dashed.setAlpha(140)
        painter.setPen(QPen(dashed, 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box, 3, 3)
        painter.setPen(color)
        painter.drawText(x + 2, base, affix)
        return x + width + 4

    # mouse ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.commit_pending()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        pos = event.position().toPoint()
        for kind, index, rect in self._layout()["items"]:
            if not rect.contains(pos):
                continue
            if kind == "chip":
                self.caret = index + 1
                self._hide_suggestions()
                self.update()
                self.blockClicked.emit(index, QRect(self.mapToGlobal(rect.topLeft()), rect.size()))
                return
            self.caret = index if pos.x() < rect.center().x() else index + 1
            break
        else:
            self.caret = self._caret_from_point(pos)
        self.update()
        self._update_suggestions()

    def _caret_from_point(self, pos):
        layout = self._layout()
        line_h = layout["line_h"]
        best = len(self.tokens)
        best_distance = None
        for index, point in layout["carets"].items():
            if not (point.y() <= pos.y() < point.y() + line_h):
                continue
            distance = abs(point.x() - pos.x())
            if best_distance is None or distance < best_distance:
                best, best_distance = index, distance
        return best

    # keyboard ---------------------------------------------------------------
    def _chunk_left(self, index):
        if index <= 0:
            return 0
        kind = _chunk_kind(self.tokens[index - 1])
        if kind[0] == "block":
            return index - 1
        while index > 0 and _chunk_kind(self.tokens[index - 1]) == kind:
            index -= 1
        return index

    def _chunk_right(self, index):
        if index >= len(self.tokens):
            return len(self.tokens)
        kind = _chunk_kind(self.tokens[index])
        if kind[0] == "block":
            return index + 1
        while index < len(self.tokens) and _chunk_kind(self.tokens[index]) == kind:
            index += 1
        return index

    def _move(self, index):
        self.caret = max(0, min(index, len(self.tokens)))
        self.update()
        self._update_suggestions()

    def keyPressEvent(self, event):
        key = event.key()
        suggestions_open = self._suggestions is not None and self._suggestions.isVisible()
        movement = [
            (QKeySequence.StandardKey.MoveToPreviousWord, lambda: self._chunk_left(self.caret)),
            (QKeySequence.StandardKey.MoveToNextWord, lambda: self._chunk_right(self.caret)),
            (QKeySequence.StandardKey.MoveToStartOfLine, lambda: 0),
            (QKeySequence.StandardKey.MoveToEndOfLine, lambda: len(self.tokens)),
            (QKeySequence.StandardKey.MoveToStartOfBlock, lambda: 0),
            (QKeySequence.StandardKey.MoveToEndOfBlock, lambda: len(self.tokens)),
            (QKeySequence.StandardKey.MoveToPreviousChar, lambda: self.caret - 1),
            (QKeySequence.StandardKey.MoveToNextChar, lambda: self.caret + 1),
        ]
        for sequence, target in movement:
            if event.matches(sequence):
                self.commit_pending()
                self._move(target())
                return
        if key in (Qt.Key.Key_Home, Qt.Key.Key_End):
            self.commit_pending()
            self._move(0 if key == Qt.Key.Key_Home else len(self.tokens))
            return
        if event.matches(QKeySequence.StandardKey.DeleteStartOfWord) and not self.pending:
            start = self._chunk_left(self.caret)
            self._delete_range(start, self.caret)
            return
        if event.matches(QKeySequence.StandardKey.DeleteEndOfWord) and not self.pending:
            self._delete_range(self.caret, self._chunk_right(self.caret))
            return
        if key == Qt.Key.Key_Backspace:
            if self.pending:
                self.pending = self.pending[:-1]
                self._relayout()
                self._update_suggestions()
            elif self.caret > 0:
                self._delete_range(self.caret - 1, self.caret)
            return
        if key == Qt.Key.Key_Delete:
            if not self.pending and self.caret < len(self.tokens):
                self._delete_range(self.caret, self.caret + 1)
            return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            if not suggestions_open:
                self._update_suggestions(force=True)
            elif self._suggestions is not None:
                self._suggestions.move_selection(1 if key == Qt.Key.Key_Down else -1)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.pending and not self._matches(self.pending):
                self.commit_pending()
            elif suggestions_open and self._suggestions.current_name():
                self.insert_field(self._suggestions.current_name())
            return
        if key == Qt.Key.Key_Escape:
            if suggestions_open or self.pending:
                self.pending = ""
                self._hide_suggestions()
                self._relayout()
                return
            return super().keyPressEvent(event)
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.commit_pending()
            return super().keyPressEvent(event)
        typed = event.text()
        modifiers = event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
        )
        if typed and typed.isprintable() and not modifiers:
            self.pending += typed
            self._relayout()
            self._update_suggestions()
            return
        super().keyPressEvent(event)

    def _delete_range(self, start, end):
        if start >= end:
            return
        del self.tokens[start:end]
        self.caret = start
        self._relayout()
        self.changed.emit()
        self._update_suggestions()

    def inputMethodEvent(self, event):
        if event.commitString():
            self.pending += event.commitString()
        self.preedit = event.preeditString()
        self._relayout()
        self._update_suggestions()
        event.accept()

    def inputMethodQuery(self, query):
        if query == Qt.InputMethodQuery.ImCursorRectangle:
            return self.caret_rect()
        if query == Qt.InputMethodQuery.ImEnabled:
            return True
        return super().inputMethodQuery(query)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()
        if event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
            self.caret = len(self.tokens)
            self._update_suggestions()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.preedit = ""
        self.commit_pending()
        self._hide_suggestions()
        self._relayout()

    # suggestions ------------------------------------------------------------
    def _matches(self, query):
        query = query.casefold()
        return [
            name for name in self.allowed_fields
            if query in field_label(name).casefold() or name.startswith(query)
        ]

    def _update_suggestions(self, force=False):
        if not self.hasFocus() or self.window() is None:
            return
        if self._suggestions is None:
            self._suggestions = _SuggestionList(self.window())
            self._suggestions.picked.connect(self._picked)
        query = self.pending + self.preedit
        matches = self._matches(query) if query else list(self.allowed_fields)
        if query and not matches:
            header = tr("「{text}」の後に追加").format(text=query.replace(" ", "␣"))
            matches = list(self.allowed_fields)
        else:
            header = tr("ブロック")
        entries = [(name, field_label(name), self.sample_for(name)) for name in matches]
        self._suggestions.fill(header, entries)
        self._place_suggestions()
        self._suggestions.show()
        self._suggestions.raise_()

    def _picked(self, name):
        self.setFocus()
        self.insert_field(name)

    def _place_suggestions(self):
        if self._suggestions is None:
            return
        caret = self.caret_rect()
        top_left = self.mapTo(self.window(), QPoint(caret.left() - 4, caret.bottom() + 4))
        window_rect = self.window().rect()
        x = min(top_left.x(), window_rect.width() - self._suggestions.width() - 4)
        self._suggestions.move(max(4, x), top_left.y())

    def _hide_suggestions(self):
        if self._suggestions is not None:
            self._suggestions.hide()


# --- block settings popup ----------------------------------------------------

class BlockSettingsPopup(QFrame):
    """Popup for one block: prefix/suffix, digit count or case, delete."""

    def __init__(self, editor, index, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.editor = editor
        self.index = index
        self.setFrameShape(QFrame.Shape.StyledPanel)
        token = editor.tokens[index]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        title = QLabel(field_label(token.field))
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        affix_row = QHBoxLayout()
        self.prefix = QLineEdit(token.prefix)
        self.prefix.setPlaceholderText(tr("前 (例: c)"))
        self.suffix = QLineEdit(token.suffix)
        self.suffix.setPlaceholderText(tr("後"))
        for line in (self.prefix, self.suffix):
            line.textChanged.connect(self._apply)
            line.returnPressed.connect(self.close)
            affix_row.addWidget(line)
        layout.addWidget(QLabel(tr("前後に付ける文字")))
        layout.addLayout(affix_row)

        self.choice_group = None
        if token.field in cf.NUMERIC_FIELDS:
            layout.addWidget(QLabel(tr("桁数")))
            choices = [(tr("可変") if d is None else tr("{n}桁").format(n=d), d)
                       for d in cf.DIGIT_CHOICES]
            self._add_choices(layout, choices, token.digits, "digits")
        elif token.field in ("title", "cell"):
            layout.addWidget(QLabel(tr("表記")))
            choices = [(tr("そのまま"), "keep"), (tr("大文字"), "upper"), (tr("小文字"), "lower")]
            self._add_choices(layout, choices, token.case, "case")

        remove = QPushButton(tr("ブロックを削除"))
        remove.setAutoDefault(False)
        remove.clicked.connect(self._remove)
        layout.addWidget(remove)

    def _add_choices(self, layout, choices, current, attribute):
        row = QHBoxLayout()
        row.setSpacing(3)
        self.choice_group = QButtonGroup(self)
        self.choice_attribute = attribute
        self.choice_values = {}
        for number, (label, value) in enumerate(choices):
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setChecked(value == current)
            self.choice_group.addButton(button, number)
            self.choice_values[number] = value
            row.addWidget(button)
        row.addStretch(1)
        self.choice_group.idClicked.connect(lambda _id: self._apply())
        layout.addLayout(row)

    def _apply(self):
        token = self.editor.tokens[self.index]
        changes = {"prefix": self.prefix.text(), "suffix": self.suffix.text()}
        if self.choice_group is not None and self.choice_group.checkedId() >= 0:
            changes[self.choice_attribute] = self.choice_values[self.choice_group.checkedId()]
        self.editor.replace_token(self.index, replace(token, **changes))

    def _remove(self):
        self.editor.remove_token(self.index)
        self.close()


# --- dialog ------------------------------------------------------------------

class CutFolderExportDialog(QDialog):
    """Configure a cut-folder export while watching the resulting tree."""

    def __init__(self, cels, cut_hint="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("カットフォルダーへ書き出し"))
        self.cels = list(cels)
        self._loading = False
        saved = config.get_value(CONFIG_KEY, {}) or {}
        self.user_presets = {
            str(name): data for name, data in (saved.get("presets") or {}).items()
        }

        root = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addStretch(1)
        self.preset_combo = QComboBox()
        self.preset_combo.activated.connect(self._preset_activated)
        header.addWidget(self.preset_combo)
        root.addLayout(header)

        body = QHBoxLayout()
        root.addLayout(body, 1)
        left = QVBoxLayout()
        body.addLayout(left, 1)
        right = QVBoxLayout()
        body.addLayout(right, 2)

        # values
        values_form = QFormLayout()
        self.value_edits = {}
        saved_values = saved.get("values") or {}
        for name in ("title", "scene", "episode", "cut"):
            line = QLineEdit(str(saved_values.get(name, "")))
            if name in cf.NUMERIC_FIELDS:
                line.setPlaceholderText(tr("番号のみ"))
            self.value_edits[name] = line
            values_form.addRow(field_label(name), line)
        if cut_hint:
            self.value_edits["cut"].setText(cut_hint)
        left.addLayout(values_form)
        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFrameShadow(QFrame.Shadow.Sunken)
        left.addWidget(rule)

        cut_fields = [f for f in cf.FIELD_IDS if f not in cf.CEL_FIELDS]
        self.folder_editor = self._editor(cut_fields)
        self.cell_folder_editor = self._editor(cf.FIELD_IDS)
        self.cell_file_editor = self._editor(cf.FIELD_IDS)
        self.timesheet_editor = self._editor(cut_fields)
        self.timesheet_folder_editor = self._editor(cut_fields)
        self.timesheet_folder_editor.placeholder = tr("空欄ならカットフォルダー直下")

        left.addWidget(QLabel(tr("カットフォルダー")))
        left.addWidget(self.folder_editor)
        left.addWidget(QLabel(tr("セルフォルダー")))
        left.addWidget(self.cell_folder_editor)

        cell_row = QHBoxLayout()
        cell_row.addWidget(QLabel(tr("セル画像")))
        cell_row.addStretch(1)
        self.format_combo = QComboBox()
        for extension in cf.IMAGE_EXTENSIONS:
            self.format_combo.addItem(f".{extension}", extension)
        self.format_combo.currentIndexChanged.connect(self._layout_changed)
        cell_row.addWidget(self.format_combo)
        left.addLayout(cell_row)
        left.addWidget(self.cell_file_editor)

        left.addWidget(QLabel(tr("タイムシートのフォルダー")))
        left.addWidget(self.timesheet_folder_editor)
        left.addWidget(QLabel(tr("タイムシート (.xdts)")))
        left.addWidget(self.timesheet_editor)

        extra_head = QHBoxLayout()
        extra_head.addWidget(QLabel(tr("空のフォルダー")))
        extra_head.addStretch(1)
        add_extra = QPushButton(tr("＋ 追加"))
        add_extra.setAutoDefault(False)
        add_extra.clicked.connect(lambda: self._add_extra_folder(()))
        extra_head.addWidget(add_extra)
        left.addLayout(extra_head)
        self.extra_box = QVBoxLayout()
        left.addLayout(self.extra_box)
        self.extra_editors = []

        left.addStretch(1)

        # destination + preview
        destination_row = QHBoxLayout()
        destination_row.addWidget(QLabel(tr("保存先")))
        self.destination = QLineEdit(str(saved.get("destination", "") or ""))
        self.destination.setPlaceholderText(tr("カットフォルダーを作る場所"))
        self.destination.textChanged.connect(self._refresh_preview)
        destination_row.addWidget(self.destination, 1)
        browse = QPushButton(tr("参照…"))
        browse.setAutoDefault(False)
        browse.clicked.connect(self._browse)
        destination_row.addWidget(browse)
        right.addLayout(destination_row)

        right.addWidget(QLabel(tr("プレビュー")))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        right.addWidget(self.tree, 1)
        self.problem_label = QLabel()
        self.problem_label.setWordWrap(True)
        problem_palette = self.problem_label.palette()
        problem_palette.setColor(
            QPalette.ColorRole.WindowText,
            problem_palette.color(QPalette.ColorRole.BrightText),
        )
        self.problem_label.setPalette(problem_palette)
        right.addWidget(self.problem_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("キャンセル"))
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        self.export_button = QPushButton(tr("書き出す"))
        self.export_button.setAutoDefault(False)
        self.export_button.clicked.connect(self._accept_if_valid)
        buttons.addWidget(cancel)
        buttons.addWidget(self.export_button)
        root.addLayout(buttons)

        layout = None
        if saved.get("layout"):
            try:
                layout = cf.CutFolderLayout.from_json(saved["layout"])
            except (ValueError, TypeError, KeyError, AttributeError):
                layout = None
        self._rebuild_preset_combo(saved.get("preset") or "PMA標準")
        self.set_layout(layout or cf.pma_standard_layout())
        for line in self.value_edits.values():
            line.textChanged.connect(self._values_changed)
        self.resize(1040, 680)

    # construction helpers ---------------------------------------------------
    def _editor(self, allowed):
        editor = TemplateEditor(allowed, self._sample_for, self._is_missing, self)
        editor.changed.connect(self._layout_changed)
        editor.blockClicked.connect(
            lambda index, rect, e=editor: self._open_block_settings(e, index, rect)
        )
        return editor

    def _add_extra_folder(self, template):
        row = QHBoxLayout()
        editor = self._editor([f for f in cf.FIELD_IDS if f not in cf.CEL_FIELDS])
        editor.set_template(template)
        remove = QToolButton()
        remove.setText("×")
        remove.setToolTip(tr("このフォルダーを削除"))
        row.addWidget(editor, 1)
        row.addWidget(remove)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.extra_box.addWidget(holder)
        self.extra_editors.append((holder, editor))

        def drop():
            self.extra_editors[:] = [(h, e) for h, e in self.extra_editors if h is not holder]
            holder.deleteLater()
            self._layout_changed()

        remove.clicked.connect(drop)
        if not self._loading:
            editor.setFocus()
            self._layout_changed()

    def _open_block_settings(self, editor, index, rect):
        popup = BlockSettingsPopup(editor, index, self)
        popup.adjustSize()
        popup.move(rect.bottomLeft() + QPoint(0, 4))
        popup.show()
        popup.prefix.setFocus()

    # values -----------------------------------------------------------------
    def values(self):
        return {name: line.text().strip() for name, line in self.value_edits.items()}

    def _sample_for(self, name):
        if name == "cell":
            return self.cels[0].cell if self.cels else "A"
        if name == "number":
            return "1"
        return self.value_edits[name].text().strip() or tr("未入力")

    def _is_missing(self, name):
        return not self.value_edits[name].text().strip()

    def _values_changed(self):
        for editor in self._all_editors():
            editor.update()
        self._refresh_preview()

    def _all_editors(self):
        return [
            self.folder_editor, self.cell_folder_editor, self.cell_file_editor,
            self.timesheet_editor, self.timesheet_folder_editor,
            *[e for _h, e in self.extra_editors],
        ]

    # layout -----------------------------------------------------------------
    def current_layout(self):
        return cf.CutFolderLayout(
            folder=self.folder_editor.template(),
            cell_folder=self.cell_folder_editor.template(),
            cell_file=self.cell_file_editor.template(),
            timesheet=self.timesheet_editor.template(),
            timesheet_folder=self.timesheet_folder_editor.template(),
            extra_folders=[e.template() for _h, e in self.extra_editors if e.template()],
            image_format=self.format_combo.currentData(),
        )

    def set_layout(self, layout):
        self._loading = True
        try:
            self.folder_editor.set_template(layout.folder)
            self.cell_folder_editor.set_template(layout.cell_folder)
            self.cell_file_editor.set_template(layout.cell_file)
            self.timesheet_editor.set_template(layout.timesheet)
            self.timesheet_folder_editor.set_template(layout.timesheet_folder)
            index = self.format_combo.findData(layout.image_format)
            self.format_combo.setCurrentIndex(max(0, index))
            for holder, _editor in self.extra_editors:
                holder.deleteLater()
            self.extra_editors = []
            for template in layout.extra_folders:
                self._add_extra_folder(template)
        finally:
            self._loading = False
        self._refresh_preview()

    def _layout_changed(self, *_args):
        if not self._loading:
            self._refresh_preview()

    # presets ----------------------------------------------------------------
    def _rebuild_preset_combo(self, select=None):
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for name in cf.BUILTIN_PRESETS:
            self.preset_combo.addItem(tr("プリセット: {name}").format(name=tr(name)), ("builtin", name))
        for name in self.user_presets:
            self.preset_combo.addItem(tr("プリセット: {name}").format(name=name), ("user", name))
        self.preset_combo.insertSeparator(self.preset_combo.count())
        self.preset_combo.addItem(tr("現在の構成をプリセットに保存…"), ("save", None))
        if self.user_presets:
            self.preset_combo.addItem(tr("選択中のプリセットを削除"), ("delete", None))
        for row in range(self.preset_combo.count()):
            data = self.preset_combo.itemData(row)
            if data and data[1] == select:
                self.preset_combo.setCurrentIndex(row)
                break
        self._current_preset = self.preset_combo.currentData()
        self.preset_combo.blockSignals(False)

    def _preset_activated(self, row):
        kind, name = self.preset_combo.itemData(row) or (None, None)
        if kind == "builtin":
            self.set_layout(cf.BUILTIN_PRESETS[name]())
        elif kind == "user":
            self.set_layout(cf.CutFolderLayout.from_json(self.user_presets[name]))
        elif kind == "save":
            name, ok = QInputDialog.getText(self, tr("プリセットを保存"), tr("プリセット名"))
            name = name.strip()
            if ok and name:
                if name in cf.BUILTIN_PRESETS:
                    QMessageBox.warning(self, tr("プリセットを保存"), tr("組み込みプリセットと同じ名前は使えません。"))
                else:
                    self.user_presets[name] = self.current_layout().to_json()
                    self._save_presets()
                    self._rebuild_preset_combo(name)
                    return
        elif kind == "delete":
            current = self._current_preset
            if current and current[0] == "user":
                self.user_presets.pop(current[1], None)
                self._save_presets()
                self._rebuild_preset_combo("PMA標準")
                self.set_layout(cf.pma_standard_layout())
                return
        if kind in ("builtin", "user"):
            self._current_preset = (kind, name)
        else:
            self._rebuild_preset_combo(self._current_preset[1] if self._current_preset else None)

    def _save_presets(self):
        data = config.get_value(CONFIG_KEY, {}) or {}
        data["presets"] = self.user_presets
        config.set_value(CONFIG_KEY, data)

    def remember(self):
        """Persist the layout and values used, for the next export."""
        data = config.get_value(CONFIG_KEY, {}) or {}
        values = self.values()
        values.pop("cut", None)
        data.update({
            "layout": self.current_layout().to_json(),
            "values": values,
            "destination": self.destination.text().strip(),
            "preset": self._current_preset[1] if self._current_preset else None,
            "presets": self.user_presets,
        })
        config.set_value(CONFIG_KEY, data)

    # preview ----------------------------------------------------------------
    def plan(self):
        return cf.plan_export(self.current_layout(), self.values(), self.cels)

    def _problem_text(self, problem):
        kind = problem[0]
        if kind == "missing_value":
            return tr("「{name}」を入力してください。").format(name=field_label(problem[1]))
        if kind == "no_number":
            return tr("セル画像にセル番号のブロックがありません。")
        if kind == "duplicate":
            return tr("同じ名前のファイルができます：{path}").format(path=problem[1])
        if kind == "bad_name":
            where = {
                "folder": tr("カットフォルダー"),
                "cell_file": tr("セル画像"),
                "timesheet": tr("タイムシート"),
                "timesheet_folder": tr("タイムシートのフォルダー"),
                "extra_folder": tr("空のフォルダー"),
            }.get(problem[2], "")
            return tr("{where}の名前「{name}」は使えません。").format(where=where, name=problem[1])
        return str(problem)

    def _refresh_preview(self, *_args):
        plan = self.plan()
        self.tree.clear()
        style = self.style()
        dir_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        destination = self.destination.text().strip()
        existing = bool(destination) and (Path(destination) / plan.folder_name).exists()
        root_label = f"{plan.folder_name or '〈' + tr('カットフォルダー') + '〉'}/"
        if existing:
            root_label += "  " + tr("（既存のフォルダー）")
        root = QTreeWidgetItem([root_label])
        root.setIcon(0, dir_icon)
        self.tree.addTopLevelItem(root)

        folders = {}

        def folder_item(path):
            if not path:
                return root
            if path in folders:
                return folders[path]
            parent_path, _, name = path.rpartition("/")
            item = QTreeWidgetItem([name + "/"])
            item.setIcon(0, dir_icon)
            folder_item(parent_path).addChild(item)
            folders[path] = item
            return item

        for path in plan.folders:
            folder_item(path)
        for relative, _cel in plan.files:
            directory, _, name = relative.rpartition("/")
            item = QTreeWidgetItem([name])
            item.setIcon(0, file_icon)
            folder_item(directory).addChild(item)
        sheet_directory, _, sheet_name = plan.timesheet_path.rpartition("/")
        sheet = QTreeWidgetItem([sheet_name])
        sheet.setIcon(0, file_icon)
        folder_item(sheet_directory).addChild(sheet)
        self.tree.expandAll()

        messages = [self._problem_text(p) for p in plan.problems]
        if not self.cels:
            messages.append(tr("書き出せるセルがありません。"))
        if not destination:
            messages.append(tr("保存先を指定してください。"))
        self.problem_label.setText("\n".join(messages))
        self.problem_label.setVisible(bool(messages))
        self.export_button.setEnabled(not messages)

    def _browse(self):
        start = self.destination.text().strip()
        path = QFileDialog.getExistingDirectory(self, tr("保存先を選ぶ"), start)
        if path:
            self.destination.setText(path)

    def _accept_if_valid(self):
        if self.plan().ok and self.destination.text().strip() and self.cels:
            self.accept()
