"""Saved workspaces (dock layouts); owned by ``MainWindow`` as ``window.workspace``.

Persists and restores named dock/window layouts through :mod:`.config`, using the
window's ``dock_manager`` and menu bar.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from typing import TYPE_CHECKING

from .i18n import tr
from PySide6.QtCore import QByteArray, QTimer
from PySide6.QtWidgets import QInputDialog

from . import config


if TYPE_CHECKING:
    from .main_window import MainWindow


class WorkspaceController:
    """Owned by ``MainWindow`` as ``window.workspace``.

    A collaborator rather than a mixin -- see main_window_export.py for why.
    """

    def __init__(self, window: "MainWindow"):
        self.window = window
        # 起動時に組み立てた既定の配置。保存済みワークスペースとは別に、
        # メニューの「初期設定」からいつでも戻れるようにする。
        self._default_state: QByteArray | None = None

    """Persist, apply, and manage named dock-layout workspaces."""

    def records(self):
        records = config.get_value("workspaces", {})
        return records if isinstance(records, dict) else {}

    def capture(self):
        return {
            "dock_state": bytes(
                self.window.dock_manager.saveState().toBase64()
            ).decode("ascii"),
            "window_geometry": bytes(
                self.window.saveGeometry().toBase64().data()
            ).decode("ascii"),
        }

    def save(self, name):
        name = str(name).strip()
        if not name:
            return False
        records = self.records()
        records[name] = self.capture()
        config.set_value("workspaces", records)
        config.set_value("active_workspace", name)
        self.refresh_menu()
        return True

    def apply(self, name, restore_geometry=True):
        record = self.records().get(name)
        if not isinstance(record, dict):
            return False
        state = QByteArray.fromBase64(
            str(record.get("dock_state", "")).encode("ascii")
        )
        if state.isEmpty() or not self.window.dock_manager.restoreState(state):
            return False
        if restore_geometry:
            geometry = QByteArray.fromBase64(
                str(record.get("window_geometry", "")).encode("ascii")
            )
            if not geometry.isEmpty():
                self.window.restoreGeometry(geometry)
        config.set_value("active_workspace", name)
        QTimer.singleShot(0, self.window._sync_all_area_hamburgers)
        self.refresh_menu()
        return True

    def remember_default(self, state):
        if state is not None and not state.isEmpty():
            self._default_state = QByteArray(state)
            self.refresh_menu()

    def apply_default(self):
        """起動直後と同じ既定のパネル配置に戻す。"""
        if self._default_state is None:
            return False
        if not self.window.dock_manager.restoreState(self._default_state):
            return False
        config.set_value("active_workspace", None)
        # 覚えておいた状態はパネルが最小幅に潰れているので、初回起動と同じく
        # 実際のウィンドウサイズから幅と高さを配り直す。
        layout = self.window.layout()
        if layout is not None:
            layout.activate()
        self.window._apply_default_dock_layout()
        QTimer.singleShot(0, self.window._sync_all_area_hamburgers)
        self.refresh_menu()
        return True

    def delete(self, name):
        records = self.records()
        if name not in records:
            return False
        del records[name]
        config.set_value("workspaces", records or None)
        if config.get_value("active_workspace") == name:
            config.set_value("active_workspace", None)
        self.refresh_menu()
        return True

    def prompt_save(self):
        name, accepted = QInputDialog.getText(
            self.window, tr("ワークスペースを保存"), tr("ワークスペース名：")
        )
        if accepted and name.strip():
            self.save(name)

    def prompt_delete(self):
        names = sorted(self.records())
        if not names:
            return
        name, accepted = QInputDialog.getItem(
            self.window, tr("ワークスペースを削除"), tr("削除するワークスペース："),
            names, 0, False,
        )
        if accepted:
            self.delete(name)

    def build_menu(self):
        self.window.workspace_menu = self.window.menuBar().addMenu(tr("ワークスペース"))
        self.refresh_menu()

    def refresh_menu(self):
        menu = getattr(self.window, "workspace_menu", None)
        if menu is None:
            return
        menu.clear()
        save_action = menu.addAction(tr("現在の配置を保存…"))
        save_action.triggered.connect(self.prompt_save)
        records = self.records()
        active = config.get_value("active_workspace")
        menu.addSeparator()
        default_action = menu.addAction(tr("初期設定"))
        default_action.setCheckable(True)
        default_action.setChecked(not active or active not in records)
        default_action.setEnabled(self._default_state is not None)
        default_action.triggered.connect(
            lambda _checked=False: self.apply_default()
        )
        if records:
            for name in sorted(records):
                action = menu.addAction(name)
                action.setCheckable(True)
                action.setChecked(name == active)
                action.triggered.connect(
                    lambda _checked=False, n=name: self.apply(n)
                )
            menu.addSeparator()
            delete_action = menu.addAction(tr("ワークスペースを削除…"))
            delete_action.triggered.connect(self.prompt_delete)
