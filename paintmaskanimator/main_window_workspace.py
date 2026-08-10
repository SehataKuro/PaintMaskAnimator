"""Saved-workspace (dock layout) management for :class:`MainWindow`.

Split out of ``main_window.py`` as a mixin. These methods persist and restore
named dock/window layouts through :mod:`.config`; they run against a live
``MainWindow`` instance and rely on its ``dock_manager`` and menu bar.
"""
from PySide6.QtCore import QByteArray, QTimer
from PySide6.QtWidgets import QInputDialog

from . import config
from ._main_window_members import MainWindowMembers


class WorkspaceMixin(MainWindowMembers):
    """Persist, apply, and manage named dock-layout workspaces."""

    def _workspace_records(self):
        records = config.get_value("workspaces", {})
        return records if isinstance(records, dict) else {}

    def _capture_workspace(self):
        return {
            "dock_state": bytes(
                self.dock_manager.saveState().toBase64()
            ).decode("ascii"),
            "window_geometry": bytes(
                self.saveGeometry().toBase64()
            ).decode("ascii"),
        }

    def _save_workspace(self, name):
        name = str(name).strip()
        if not name:
            return False
        records = self._workspace_records()
        records[name] = self._capture_workspace()
        config.set_value("workspaces", records)
        config.set_value("active_workspace", name)
        self._refresh_workspace_menu()
        return True

    def _apply_workspace(self, name, restore_geometry=True):
        record = self._workspace_records().get(name)
        if not isinstance(record, dict):
            return False
        state = QByteArray.fromBase64(
            str(record.get("dock_state", "")).encode("ascii")
        )
        if state.isEmpty() or not self.dock_manager.restoreState(state):
            return False
        if restore_geometry:
            geometry = QByteArray.fromBase64(
                str(record.get("window_geometry", "")).encode("ascii")
            )
            if not geometry.isEmpty():
                self.restoreGeometry(geometry)
        config.set_value("active_workspace", name)
        QTimer.singleShot(0, self._sync_all_area_hamburgers)
        self._refresh_workspace_menu()
        return True

    def _delete_workspace(self, name):
        records = self._workspace_records()
        if name not in records:
            return False
        del records[name]
        config.set_value("workspaces", records or None)
        if config.get_value("active_workspace") == name:
            config.set_value("active_workspace", None)
        self._refresh_workspace_menu()
        return True

    def _prompt_save_workspace(self):
        name, accepted = QInputDialog.getText(
            self, "ワークスペースを保存", "ワークスペース名："
        )
        if accepted and name.strip():
            self._save_workspace(name)

    def _prompt_delete_workspace(self):
        names = sorted(self._workspace_records())
        if not names:
            return
        name, accepted = QInputDialog.getItem(
            self, "ワークスペースを削除", "削除するワークスペース：",
            names, 0, False,
        )
        if accepted:
            self._delete_workspace(name)

    def _build_workspace_menu(self):
        self.workspace_menu = self.menuBar().addMenu("ワークスペース")
        self._refresh_workspace_menu()

    def _refresh_workspace_menu(self):
        menu = getattr(self, "workspace_menu", None)
        if menu is None:
            return
        menu.clear()
        save_action = menu.addAction("現在の配置を保存…")
        save_action.triggered.connect(self._prompt_save_workspace)
        records = self._workspace_records()
        if records:
            menu.addSeparator()
            active = config.get_value("active_workspace")
            for name in sorted(records):
                action = menu.addAction(name)
                action.setCheckable(True)
                action.setChecked(name == active)
                action.triggered.connect(
                    lambda _checked=False, n=name: self._apply_workspace(n)
                )
            menu.addSeparator()
            delete_action = menu.addAction("ワークスペースを削除…")
            delete_action.triggered.connect(self._prompt_delete_workspace)
