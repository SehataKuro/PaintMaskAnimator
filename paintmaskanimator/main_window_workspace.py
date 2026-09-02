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
        if records:
            menu.addSeparator()
            active = config.get_value("active_workspace")
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
