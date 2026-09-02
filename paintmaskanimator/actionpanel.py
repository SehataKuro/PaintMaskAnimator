"""Action panel with built-in and user-defined Python actions."""

import hashlib
import importlib.util
import json
import os
import traceback

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView

from . import config
import shutil
import subprocess
import sys
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


# Built-in actions ship as editable default scripts. They are written into the
# actions folder on first run (see ``_seed_builtin_scripts``) so users can edit
# or delete them like any other action. These files also serve as copyable API
# examples, so callbacks are deliberately named and commented.
BUILTIN_SCRIPTS = {
    "builtin_silhouette.py": '''"""チェック式アクションの例: 背景以外を黒シルエット表示。"""


def register_actions(panel, window):
    # checkable=True のコールバックには、ボタンの新しい状態が渡ります。
    def set_silhouette(checked):
        action = window.a_silhouette
        # QAction.trigger() は状態を反転するので、必要な場合だけ呼びます。
        if action.isChecked() != checked:
            action.trigger()

    panel.add_action(
        "silhouette",  # 他のスクリプトと重複しないID
        "背景以外を黒シルエット表示",
        set_silhouette,
        tooltip="背景以外の色を黒で表示します。もう一度押すと解除します。",
        checkable=True,
    )
''',
    "builtin_same_image_replacement.py": '''"""通常のアクションの例: 同一画像から色置換。"""


def register_actions(panel, window):
    def replace_colors():
        # window は MainWindow です。既存の操作を呼び出せます。
        window.line_ops.register_same_image_replacements()
        window.status_bar.showMessage("同一画像から色置換を実行しました", 3000)

    panel.add_action(
        "same_image_replacement",
        "同一画像から色置換",
        replace_colors,
        tooltip=(
            "同じタイムライン位置にある上のレイヤーと画素配置を比較し、"
            "一致した色対応をそのまま実画像へ適用します。"
        ),
    )
''',
    "builtin_main_line_repaint.py": '''"""処理を関数に分ける例: MainLineRepaint。"""


def register_actions(panel, window):
    def repaint_main_line():
        # 通常ボタンのコールバックには引数が渡りません。
        window.line_ops.main_line_repaint()

    panel.add_action(
        "main_line_repaint",
        "MainLineRepaint",
        repaint_main_line,
        tooltip=(
            "メイン色・サブ色を線レイヤーへ分離し、"
            "抜けた面を周囲の最多色で埋めます。"
        ),
    )
''',
}

# 初期版を一字も編集していないファイルだけ、新しい参考例へ更新します。
LEGACY_BUILTIN_HASHES = {
    "builtin_silhouette.py": "f4da2226d3af2cb0e7a230f100551be3a0edb8d98b3a3a1e45a486f5157c982e",
    "builtin_same_image_replacement.py": "d6116d312479ea89bc167c84886fedbd7f9680b5ba70f0fd25447c2b2f3d9e8b",
    "builtin_main_line_repaint.py": "70855b6e6f7bed6562f9f25b24149a54d66aad9ec55ed7a5264fcd2fed2772d2",
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
                path = actions_dir / name
                try:
                    current = path.read_text(encoding="utf-8")
                    digest = hashlib.sha256(current.encode()).hexdigest()
                    if digest == LEGACY_BUILTIN_HASHES.get(name):
                        path.write_text(content, encoding="utf-8")
                except (OSError, UnicodeError):
                    pass
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
                os.startfile(path)  # noqa: S606 - user-requested local folder
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "フォルダを開けません", str(error))


NEW_SCRIPT_TEMPLATE = '''"""ユーザー定義アクションのひな形。

register_actions(panel, window) は読み込み時に1回呼ばれます。
window は PaintMaskAnimator のメインウィンドウです。
"""


def register_actions(panel, window):
    def run():  # 通常ボタンのコールバックは引数なし
        # ここに実行したい処理を書きます。
        window.status_bar.showMessage("カスタムアクションを実行しました", 3000)

    panel.add_action(
        "my_action.run",             # 必須: 重複しないID
        "マイアクション",            # 必須: ボタンに表示する名前
        run,                          # 必須: run() ではなく関数自体
        tooltip="この処理の説明です",  # 任意
    )

    # 切り替えボタンにする場合:
    # def toggle(checked):
    #     window.status_bar.showMessage(f"状態: {checked}", 3000)
    # panel.add_action("my_action.toggle", "切り替え", toggle, checkable=True)
'''


class _MonacoBridge(QObject):
    """Receive editor events from Monaco through Qt WebChannel."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    @Slot(str)
    def contentChanged(self, text):  # noqa: N802 - called from JavaScript
        self.editor._content_changed(text)

    @Slot()
    def editorReady(self):  # noqa: N802 - called from JavaScript
        self.editor._editor_ready()


class MonacoEditor(QWebEngineView):
    """Monaco Editor embedded in Qt, with a small text-widget-compatible API."""

    textChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._ready = False
        self._bridge = _MonacoBridge(self)
        self._channel = QWebChannel(self.page())
        self._channel.registerObject("bridge", self._bridge)
        self.page().setWebChannel(self._channel)
        html_path = Path(__file__).with_name("assets") / "monaco_editor.html"
        self.setUrl(QUrl.fromLocalFile(str(html_path.resolve())))

    def _editor_ready(self):
        self._ready = True
        self._send_text()

    def _content_changed(self, text):
        if text == self._text:
            return
        self._text = text
        self.textChanged.emit()

    def _send_text(self):
        if self._ready:
            encoded = json.dumps(self._text, ensure_ascii=False)
            self.page().runJavaScript(f"window.setEditorText({encoded})")

    def setPlainText(self, text):
        self._text = str(text)
        self._send_text()

    def toPlainText(self):
        return self._text

    def focus_editor(self):
        if self._ready:
            self.page().runJavaScript("window.focusEditor()")


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
        self.editor = MonacoEditor()
        self.editor.textChanged.connect(self._on_text_changed)
        right_layout.addWidget(self.editor, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)

        buttons = QHBoxLayout()
        self.new_button = QPushButton("新規")
        self.delete_button = QPushButton("削除")
        self.save_button = QPushButton("保存")
        self.save_reload_button = QPushButton("保存して再読み込み")
        self.vscode_button = QPushButton("VS Codeで開く")
        self.close_button = QPushButton("閉じる")
        self.new_button.clicked.connect(self._new_script)
        self.delete_button.clicked.connect(self._delete_script)
        self.save_button.clicked.connect(lambda: self._save_current())
        self.save_reload_button.clicked.connect(self._save_and_reload)
        self.vscode_button.clicked.connect(self._open_in_vscode)
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch()
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.save_reload_button)
        buttons.addWidget(self.vscode_button)
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

    def _open_in_vscode(self):
        if self._current_path is None:
            return
        if self._dirty and not self._save_current():
            return
        executable = shutil.which("code") or shutil.which("code-insiders")
        if sys.platform == "win32" and executable is None:
            candidates = []
            for variable in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
                root = os.environ.get(variable)
                if root:
                    candidates.extend([
                        Path(root) / "Programs/Microsoft VS Code/Code.exe",
                        Path(root) / "Microsoft VS Code/Code.exe",
                    ])
            executable = next((str(path) for path in candidates if path.is_file()), None)
        if executable is None:
            QMessageBox.warning(
                self,
                "VS Codeが見つかりません",
                "Visual Studio Codeをインストールするか、codeコマンドをPATHへ追加してください。",
            )
            return
        try:
            subprocess.Popen([executable, "--goto", str(self._current_path.resolve())])
        except OSError as error:
            QMessageBox.warning(self, "VS Codeを開けません", str(error))

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
