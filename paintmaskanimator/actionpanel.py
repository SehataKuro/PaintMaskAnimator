"""Action panel with built-in and user-defined Python actions."""

import importlib.util
import traceback

from . import config
from .common import *  # noqa: F401,F403


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
        self.open_folder_button = QPushButton("フォルダを開く")
        self.reload_button = QPushButton("再読み込み")
        self.open_folder_button.setToolTip(str(self.actions_dir()))
        self.reload_button.setToolTip("Pythonアクションを再読み込みします")
        self.open_folder_button.clicked.connect(self.open_actions_folder)
        self.reload_button.clicked.connect(self.reload_python_actions)
        controls.addWidget(self.open_folder_button)
        controls.addWidget(self.reload_button)
        layout.addLayout(controls)

    @staticmethod
    def actions_dir():
        path = config.config_dir() / "actions"
        path.mkdir(parents=True, exist_ok=True)
        return path

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
