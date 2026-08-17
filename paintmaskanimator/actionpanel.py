"""Action panel with built-in and user-defined Python actions."""

import importlib.util
import json
import re as _re
import traceback

from PySide6.QtGui import (
    QColor, QFont, QPalette, QSyntaxHighlighter, QTextCharFormat, QTextFormat,
)
from PySide6.QtWidgets import QTextEdit

from . import config
from .common import *  # noqa: F401,F403


# Built-in actions ship as editable default scripts. They are written into the
# actions folder on first run (see ``_seed_builtin_scripts``) so users can edit
# or delete them like any other action. Each references methods on ``window``.
BUILTIN_SCRIPTS = {
    "builtin_silhouette.py": '''"""組み込みアクション: 背景以外を黒シルエット表示。"""


def register_actions(panel, window):
    panel.add_action(
        "silhouette",
        "背景以外を黒シルエット表示",
        lambda checked=False: window.a_silhouette.trigger(),
        checkable=True,
    )
''',
    "builtin_same_image_replacement.py": '''"""組み込みアクション: 同一画像から色置換。"""


def register_actions(panel, window):
    panel.add_action(
        "same_image_replacement",
        "同一画像から色置換",
        lambda: window.register_same_image_replacements(),
        tooltip=(
            "同じタイムライン位置にある上のレイヤーと画素配置を比較し、"
            "一致した色対応をそのまま実画像へ適用します。"
        ),
    )
''',
    "builtin_main_line_repaint.py": '''"""組み込みアクション: MainLineRepaint。"""


def register_actions(panel, window):
    panel.add_action(
        "main_line_repaint",
        "MainLineRepaint",
        lambda: window.main_line_repaint(),
        tooltip=(
            "メイン色・サブ色を線レイヤーへ分離し、"
            "抜けた面を周囲の最多色で埋めます。"
        ),
    )
''',
}


class ActionPanel(QWidget):
    """A button list extensible by ``*.py`` files in the user config folder.

    A script must define ``register_actions(panel, window)`` and call
    ``panel.add_action(...)``. Scripts are regular Python and therefore run
    with the user's permissions; only files deliberately placed in the action
    directory are loaded.
    """

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self._buttons = {}
        self._sources = {}
        self._loading_source = "builtin"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)
        self.action_layout = QVBoxLayout()
        self.action_layout.setSpacing(3)
        layout.addLayout(self.action_layout)
        layout.addStretch()

        controls = QHBoxLayout()
        self.menu_button = QToolButton()
        self.menu_button.setText("☰")  # hamburger ☰
        self.menu_button.setToolTip("アクションメニュー")
        self.menu_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        menu = QMenu(self.menu_button)
        edit_action = menu.addAction("スクリプトを編集")
        edit_action.triggered.connect(self.open_script_editor)
        self.menu_button.setMenu(menu)
        controls.addStretch()
        controls.addWidget(self.menu_button)
        layout.addLayout(controls)

    def open_script_editor(self):
        dialog = ScriptEditorDialog(self)
        dialog.exec()

    @staticmethod
    def actions_dir():
        path = config.config_dir() / "actions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _seed_builtin_scripts(self):
        """Write default built-in action scripts into the actions folder once.

        A marker file records which built-ins have been seeded so user edits
        are never overwritten and deleted built-ins are not resurrected.
        """
        actions_dir = self.actions_dir()
        marker = actions_dir / ".builtin_seeded.json"
        try:
            seeded = set(json.loads(marker.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 - missing or corrupt marker: reseed all
            seeded = set()
        changed = False
        for name, content in BUILTIN_SCRIPTS.items():
            if name in seeded:
                continue
            path = actions_dir / name
            if not path.exists():
                try:
                    path.write_text(content, encoding="utf-8")
                except Exception as error:  # noqa: BLE001 - non-fatal
                    QMessageBox.warning(
                        self, "組み込みアクション生成エラー",
                        f"{name}: {error}",
                    )
                    continue
            seeded.add(name)
            changed = True
        if changed:
            try:
                marker.write_text(
                    json.dumps(sorted(seeded)), encoding="utf-8"
                )
            except Exception:  # noqa: BLE001 - marker is best-effort
                pass

    def add_action(
        self, action_id, label, callback, *, tooltip="", checkable=False,
        source=None,
    ):
        """Add an action button and return it.

        ``callback`` receives the checked state for checkable buttons and no
        arguments for regular buttons.
        """
        action_id = str(action_id).strip()
        if not action_id or not callable(callback):
            raise ValueError("action_id and a callable callback are required")
        if action_id in self._buttons:
            raise ValueError(f"Action ID already exists: {action_id}")
        source = source or self._loading_source
        button = QPushButton(str(label))
        button.setCheckable(bool(checkable))
        if tooltip:
            button.setToolTip(str(tooltip))

        def invoke(checked=False):
            try:
                if checkable:
                    callback(bool(checked))
                else:
                    callback()
            except Exception as error:  # noqa: BLE001 - report extension errors in UI
                QMessageBox.critical(
                    self,
                    "Pythonアクション エラー",
                    f"{label}\n\n{error}\n\n{traceback.format_exc()}",
                )

        button.clicked.connect(invoke)
        self.action_layout.addWidget(button)
        self._buttons[action_id] = button
        self._sources[action_id] = source
        return button

    def button(self, action_id):
        return self._buttons.get(action_id)

    def _remove_python_actions(self):
        for action_id in list(self._buttons):
            if self._sources.get(action_id) == "builtin":
                continue
            button = self._buttons.pop(action_id)
            self._sources.pop(action_id, None)
            self.action_layout.removeWidget(button)
            button.deleteLater()

    def reload_python_actions(self):
        self._seed_builtin_scripts()
        self._remove_python_actions()
        errors = []
        for index, path in enumerate(sorted(self.actions_dir().glob("*.py"))):
            if path.name.startswith("_"):
                continue
            source = str(path.resolve())
            try:
                module_name = f"paintmaskanimator_user_action_{index}_{path.stem}"
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    raise ImportError(f"読み込めません: {path.name}")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                register = getattr(module, "register_actions", None)
                if not callable(register):
                    raise AttributeError("register_actions(panel, window) が必要です")
                self._loading_source = source
                register(self, self.main_window)
            except Exception as error:  # noqa: BLE001 - isolate individual scripts
                errors.append(f"{path.name}: {error}")
            finally:
                self._loading_source = "builtin"
        if errors:
            QMessageBox.warning(
                self,
                "Pythonアクション読み込みエラー",
                "\n".join(errors),
            )

    def open_actions_folder(self):
        path = self.actions_dir()
        try:
            if sys.platform == "win32":
                import os
                os.startfile(path)  # noqa: S606 - user-requested local folder
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "フォルダを開けません", str(error))


NEW_SCRIPT_TEMPLATE = '''"""ユーザー定義アクション。

``register_actions(panel, window)`` を定義し、``panel.add_action(...)`` を
呼び出してください。``window`` はメインウィンドウです。
"""


def register_actions(panel, window):
    def run():
        window.status_bar.showMessage("Hello from a custom action", 3000)

    panel.add_action("example.hello", "サンプルアクション", run)
'''


class PythonHighlighter(QSyntaxHighlighter):
    """Minimal Python syntax highlighter for the action editor."""

    KEYWORDS = (
        "and as assert async await break class continue def del elif else "
        "except finally for from global if import in is lambda nonlocal not "
        "or pass raise return try while with yield True False None self"
    ).split()

    def __init__(self, document, dark):
        super().__init__(document)
        palette = {
            "keyword": "#c586c0" if dark else "#0000ff",
            "builtin": "#569cd6" if dark else "#267f99",
            "string": "#ce9178" if dark else "#a31515",
            "comment": "#6a9955" if dark else "#008000",
            "number": "#b5cea8" if dark else "#098658",
            "func": "#dcdcaa" if dark else "#795e26",
            "decorator": "#dcdcaa" if dark else "#795e26",
        }

        def fmt(color, *, italic=False, bold=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if italic:
                f.setFontItalic(True)
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            return f

        kw_fmt = fmt(palette["keyword"], bold=True)
        self._rules = [
            (_re.compile(r"\b" + kw + r"\b"), kw_fmt) for kw in self.KEYWORDS
        ]
        self._rules += [
            (_re.compile(r"@\w+"), fmt(palette["decorator"])),
            (_re.compile(r"\bdef\s+(\w+)"), fmt(palette["func"])),
            (_re.compile(r"\bclass\s+(\w+)"), fmt(palette["func"])),
            (_re.compile(r"\b\d+(?:\.\d+)?\b"), fmt(palette["number"])),
        ]
        self._string_fmt = fmt(palette["string"])
        self._comment_fmt = fmt(palette["comment"], italic=True)
        self._string_re = _re.compile(
            r"'[^'\\]*(?:\\.[^'\\]*)*'|\"[^\"\\]*(?:\\.[^\"\\]*)*\""
        )
        self._comment_re = _re.compile(r"#[^\n]*")

    def highlightBlock(self, text):  # noqa: N802 - Qt override
        for pattern, char_fmt in self._rules:
            for match in pattern.finditer(text):
                start = match.start(1) if match.groups() else match.start()
                end = match.end(1) if match.groups() else match.end()
                self.setFormat(start, end - start, char_fmt)
        for match in self._string_re.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(),
                           self._string_fmt)
        comment = self._comment_re.search(text)
        if comment:
            self.setFormat(comment.start(),
                           len(text) - comment.start(), self._comment_fmt)


class _LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):  # noqa: N802 - Qt override
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        self._editor.line_number_area_paint(event)


class CodeEditor(QPlainTextEdit):
    """A monospace editor with a line-number gutter and current-line highlight."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(11)
        self.setFont(mono)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))

        base = self.palette().color(QPalette.ColorRole.Base)
        self._dark = base.lightness() < 128
        self._gutter_bg = base.darker(115) if not self._dark else base.lighter(140)
        self._gutter_fg = QColor("#858585")
        self._current_line_bg = (
            base.lighter(118) if self._dark else base.darker(104)
        )

        self._line_numbers = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_gutter_width(0)
        self._highlight_current_line()

    def line_number_area_width(self):
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, _count):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self._line_numbers.scroll(0, dy)
        else:
            self._line_numbers.update(
                0, rect.y(), self._line_numbers.width(), rect.height()
            )
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width(0)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_numbers.setGeometry(
            cr.left(), cr.top(), self.line_number_area_width(), cr.height()
        )

    def _highlight_current_line(self):
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(self._current_line_bg)
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])

    def line_number_area_paint(self, event):
        painter = QPainter(self._line_numbers)
        painter.fillRect(event.rect(), self._gutter_bg)
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(
            self.contentOffset()
        ).top()
        bottom = top + self.blockBoundingRect(block).height()
        painter.setPen(self._gutter_fg)
        width = self._line_numbers.width() - 6
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0, int(top), width, height,
                    Qt.AlignmentFlag.AlignRight, str(block_number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            block_number += 1


class ScriptEditorDialog(QDialog):
    """In-app editor for the ``*.py`` action scripts in the actions folder."""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self._current_path = None
        self._dirty = False

        self.setWindowTitle("Pythonアクションの編集")
        self.resize(820, 560)

        outer = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)

        self.file_list = QListWidget()
        self.file_list.setMinimumWidth(180)
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        splitter.addWidget(self.file_list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.editor = CodeEditor()
        self._highlighter = PythonHighlighter(
            self.editor.document(), self.editor._dark
        )
        self.editor.textChanged.connect(self._on_text_changed)
        right_layout.addWidget(self.editor, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)

        buttons = QHBoxLayout()
        self.new_button = QPushButton("新規")
        self.delete_button = QPushButton("削除")
        self.save_button = QPushButton("保存")
        self.save_reload_button = QPushButton("保存して再読み込み")
        self.close_button = QPushButton("閉じる")
        self.new_button.clicked.connect(self._new_script)
        self.delete_button.clicked.connect(self._delete_script)
        self.save_button.clicked.connect(lambda: self._save_current())
        self.save_reload_button.clicked.connect(self._save_and_reload)
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch()
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.save_reload_button)
        buttons.addWidget(self.close_button)
        outer.addLayout(buttons)

        self._refresh_file_list()

    def _scripts(self):
        return sorted(self.panel.actions_dir().glob("*.py"))

    def _refresh_file_list(self, select=None):
        self.file_list.blockSignals(True)
        self.file_list.clear()
        target_row = -1
        for row, path in enumerate(self._scripts()):
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.file_list.addItem(item)
            if select is not None and path == select:
                target_row = row
        self.file_list.blockSignals(False)
        if self.file_list.count() == 0:
            self._current_path = None
            self.editor.blockSignals(True)
            self.editor.setPlainText("")
            self.editor.blockSignals(False)
            self._dirty = False
            self.editor.setEnabled(False)
            return
        self.editor.setEnabled(True)
        if target_row < 0:
            target_row = 0
        self.file_list.setCurrentRow(target_row)

    def _on_file_selected(self, current, _previous):
        if current is None:
            return
        if not self._confirm_discard():
            # Revert selection to the still-loaded file.
            self._reselect_current()
            return
        path = Path(current.data(Qt.ItemDataRole.UserRole))
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "読み込めません", str(error))
            return
        self._current_path = path
        self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(False)
        self._dirty = False

    def _reselect_current(self):
        if self._current_path is None:
            return
        self.file_list.blockSignals(True)
        for row in range(self.file_list.count()):
            item = self.file_list.item(row)
            if Path(item.data(Qt.ItemDataRole.UserRole)) == self._current_path:
                self.file_list.setCurrentRow(row)
                break
        self.file_list.blockSignals(False)

    def _on_text_changed(self):
        self._dirty = True

    def _confirm_discard(self):
        if not self._dirty or self._current_path is None:
            return True
        choice = QMessageBox.question(
            self,
            "未保存の変更",
            f"{self._current_path.name} の変更を保存しますか？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Save:
            return self._save_current()
        return True

    def _save_current(self):
        if self._current_path is None:
            return False
        try:
            self._current_path.write_text(
                self.editor.toPlainText(), encoding="utf-8"
            )
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "保存できません", str(error))
            return False
        self._dirty = False
        return True

    def _save_and_reload(self):
        if self._current_path is not None and not self._save_current():
            return
        self.panel.reload_python_actions()
        QMessageBox.information(
            self, "再読み込み", "Pythonアクションを再読み込みしました。"
        )

    def _new_script(self):
        if not self._confirm_discard():
            return
        name, ok = QInputDialog.getText(
            self, "新規スクリプト", "ファイル名（.py）"
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if not name.endswith(".py"):
            name += ".py"
        path = self.panel.actions_dir() / name
        if path.exists():
            QMessageBox.warning(self, "作成できません", f"既に存在します: {name}")
            return
        try:
            path.write_text(NEW_SCRIPT_TEMPLATE, encoding="utf-8")
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "作成できません", str(error))
            return
        self._dirty = False
        self._refresh_file_list(select=path)

    def _delete_script(self):
        if self._current_path is None:
            return
        name = self._current_path.name
        if QMessageBox.question(
            self, "削除", f"{name} を削除しますか？"
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._current_path.unlink()
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "削除できません", str(error))
            return
        self._current_path = None
        self._dirty = False
        self._refresh_file_list()

    def closeEvent(self, event):
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()
