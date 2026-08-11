import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PySide6QtAds as QtAds  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QSizePolicy, QToolButton, QWidget,
)

from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.onion import OnionSkinSettingsBrowser  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _panel_docks(window):
    return (
        window.tool_selector_dock,
        window.tools_dock,
        window.action_panel_dock,
        window.tools_dock,
        window.color_wheel_dock,
        window.color_slider_dock,
        window.palette_dock,
        window.history_dock,
        window.timeline_dock,
    )


def _assert_docking_invariants(window):
    """Check properties that must survive every docking state transition."""
    window._sync_all_area_hamburgers()
    managed = set(_panel_docks(window))
    for dock in managed:
        assert window.dock_manager.findDockWidget(dock.objectName()) is dock
        area = dock.dockAreaWidget()
        assert area is not None
        assert dock in area.dockWidgets()
        assert dock.tabWidget().height() == 20
        assert dock.tabWidget().findChild(QToolButton, "dockHamburger") is None

    for area in window.dock_manager.openedDockAreas():
        panel_tabs = [dock for dock in area.openedDockWidgets() if dock in managed]
        buttons = area.titleBar().findChildren(
            QToolButton, "dockHamburger",
            Qt.FindChildOption.FindDirectChildrenOnly,
        )
        assert len(buttons) == (1 if panel_tabs else 0)
        if buttons:
            assert area.titleBar().layout().itemAt(0).widget() is buttons[0]
            assert area.titleBar().height() == 22


def test_main_panels_use_qt_advanced_docking(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        assert isinstance(window.dock_manager, QtAds.CDockManager)
        for dock in (
            window.tool_selector_dock,
            window.tools_dock,
            window.action_panel_dock,
            window.tools_dock,
            window.palette_dock,
            window.timeline_dock,
        ):
            assert isinstance(dock, QtAds.CDockWidget)
        assert window.dock_manager.findDockWidget("toolsDock") is window.tools_dock
        assert window.dock_manager.findDockWidget("timelineDock") is window.timeline_dock
        assert window.tool_selector_dock.features() & (
            QtAds.CDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        assert window.central_dock.features() & (
            QtAds.CDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        assert window.central_dock.features() & (
            QtAds.CDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        assert window.dock_manager.testConfigFlag(
            QtAds.CDockManager.eConfigFlag.DoubleClickUndocksWidget
        )
        crosses = window.dock_manager.findChildren(QtAds.CDockOverlayCross)
        assert len(crosses) == 2
        assert all(cross.windowOpacity() == 0.0 for cross in crosses)
        targets = [
            target
            for cross in crosses
            for target in cross.findChildren(QLabel, "DockWidgetAreaLabel")
        ]
        assert len(targets) == 10
        assert all(
            target.sizePolicy().horizontalPolicy()
            == QSizePolicy.Policy.Expanding
            for target in targets
        )
        assert all(
            target.sizePolicy().verticalPolicy()
            == QSizePolicy.Policy.Expanding
            for target in targets
        )
        assert window.dock_manager.containerOverlay().dropPreviewEnabled()
        assert window.dock_manager.dockAreaOverlay().dropPreviewEnabled()
    finally:
        window.close()


def test_onion_settings_content_is_ads_compatible():
    assert issubclass(OnionSkinSettingsBrowser, QWidget)


def test_single_floating_panel_has_only_one_title(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    window.show()
    try:
        window.tools_dock.setFloating()
        QTest.qWait(20)
        qapp.processEvents()
        floating = window.tools_dock.window()
        flags = floating.windowFlags()
        assert floating.windowIcon().isNull()
        assert flags & Qt.WindowType.WindowTitleHint
        assert flags & Qt.WindowType.WindowCloseButtonHint
        assert not flags & Qt.WindowType.WindowMinimizeButtonHint
        assert not flags & Qt.WindowType.WindowMaximizeButtonHint
        assert not window.tools_dock.dockAreaWidget().titleBar().isVisible()
        assert window.tool_selector_dock.tabWidget().maximumWidth() > 1000
        window.dock_manager.addDockWidget(
            QtAds.LeftDockWidgetArea, window.tools_dock
        )
        QTest.qWait(20)
        qapp.processEvents()
        assert window.tools_dock.dockAreaWidget().titleBar().isVisible()

        window.central_dock.setFloating()
        QTest.qWait(20)
        qapp.processEvents()
        assert window.central_dock.isFloating()
        assert not window.central_dock.dockAreaWidget().titleBar().isVisible()
        assert not window._split_drop_timer.isActive()
    finally:
        window.close()


def test_tool_width_snaps_only_after_mouse_release(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    QTest.qWait(220)
    try:
        area = window.tool_selector_dock.dockAreaWidget()
        splitter = area.parentWidget()
        index = splitter.indexOf(area)
        handle = splitter.handle(index + 1)
        QTest.mousePress(
            handle, Qt.MouseButton.LeftButton, pos=handle.rect().center()
        )

        sizes = splitter.sizes()
        recipient = max(
            (position for position in range(len(sizes)) if position != index),
            key=lambda position: sizes[position],
        )
        delta = sizes[index] - 72
        sizes[index] = 72
        sizes[recipient] += delta
        splitter.setSizes(sizes)
        qapp.processEvents()

        QTest.qWait(300)
        qapp.processEvents()
        assert window.tool_selector.width() == 72
        assert window.tool_selector.displayed_column_count() == 2

        QTest.mouseRelease(
            handle, Qt.MouseButton.LeftButton, pos=handle.rect().center()
        )
        QTest.qWait(40)
        qapp.processEvents()
        assert window.tool_selector.width() == 65
    finally:
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton)
        window.close()


def test_layout_resize_does_not_trigger_tool_width_snap(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    QTest.qWait(220)
    try:
        area = window.tool_selector_dock.dockAreaWidget()
        splitter = area.parentWidget()
        index = splitter.indexOf(area)
        sizes = splitter.sizes()
        recipient = max(
            (position for position in range(len(sizes)) if position != index),
            key=lambda position: sizes[position],
        )
        delta = sizes[index] - 72
        sizes[index] = 72
        sizes[recipient] += delta
        splitter.setSizes(sizes)
        qapp.processEvents()

        QTest.qWait(300)
        qapp.processEvents()
        assert window.tool_selector.width() == 72
        assert window._pending_tool_selector_snap is None
    finally:
        window.close()


def test_tabbed_panels_share_left_aligned_hamburger(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    try:
        area = window.tools_dock.dockAreaWidget()
        window.dock_manager.addDockWidgetTabToArea(
            window.color_wheel_dock, area
        )
        QTest.qWait(20)
        qapp.processEvents()

        title_bar = area.titleBar()
        buttons = title_bar.findChildren(
            QWidget, "dockHamburger",
            Qt.FindChildOption.FindDirectChildrenOnly,
        )
        assert len(buttons) == 1
        assert title_bar.layout().itemAt(0).widget() is buttons[0]
        assert window.tools_dock.tabWidget().findChild(
            QWidget, "dockHamburger"
        ) is None

        def has_checkable(menu):
            # The wheel menu nests its mode toggles inside submenus
            # ("色相の形" / "内側の形"), so recurse into them.
            for action in menu.actions():
                if action.isCheckable():
                    return True
                submenu = action.menu()
                if submenu is not None and has_checkable(submenu):
                    return True
            return False

        area.setCurrentDockWidget(window.color_wheel_dock)
        menu = buttons[0].menu()
        menu.aboutToShow.emit()
        assert has_checkable(menu)

        area.setCurrentDockWidget(window.tools_dock)
        menu.aboutToShow.emit()
        assert not has_checkable(menu)
    finally:
        window.close()


def test_hamburger_is_added_when_tabbed_into_area_without_one(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    try:
        canvas_area = window.central_dock.dockAreaWidget()
        assert canvas_area.titleBar().findChild(QWidget, "dockHamburger") is None

        window.dock_manager.addDockWidgetTabToArea(
            window.action_panel_dock, canvas_area
        )
        window._sync_all_area_hamburgers()
        canvas_area.setCurrentDockWidget(window.action_panel_dock)
        qapp.processEvents()

        button = canvas_area.titleBar().findChild(QWidget, "dockHamburger")
        assert button is not None
        assert button.isVisible()
        assert canvas_area.titleBar().layout().itemAt(0).widget() is button
    finally:
        window.close()


def test_dock_tab_height_is_consistent_when_stacked(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    try:
        qapp.processEvents()
        area = window.tools_dock.dockAreaWidget()
        window._sync_all_area_hamburgers()
        qapp.processEvents()
        single_height = window.tools_dock.tabWidget().height()
        window.dock_manager.addDockWidgetTabToArea(
            window.color_wheel_dock, area
        )
        window._sync_all_area_hamburgers()
        qapp.processEvents()

        heights = {
            dock.tabWidget().height() for dock in area.openedDockWidgets()
        }
        assert heights == {single_height}
        assert single_height == 20
        assert area.titleBar().height() == 22
    finally:
        window.close()


def test_hamburger_sync_removes_duplicates(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    try:
        area = window.tools_dock.dockAreaWidget()
        title_bar = area.titleBar()
        duplicate = QToolButton(title_bar)
        duplicate.setObjectName("dockHamburger")
        title_bar.layout().insertWidget(0, duplicate)
        assert len(title_bar.findChildren(
            QToolButton, "dockHamburger",
            Qt.FindChildOption.FindDirectChildrenOnly,
        )) == 2

        window._sync_all_area_hamburgers()
        qapp.processEvents()
        buttons = title_bar.findChildren(
            QToolButton, "dockHamburger",
            Qt.FindChildOption.FindDirectChildrenOnly,
        )
        assert len(buttons) == 1
        assert title_bar.layout().itemAt(0).widget() is buttons[0]
    finally:
        window.close()


def test_named_workspace_restores_dock_layout(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    try:
        window._finalize_startup_dock_ui()
        area = window.tools_dock.dockAreaWidget()
        window.dock_manager.addDockWidgetTabToArea(
            window.color_wheel_dock, area
        )
        area.setCurrentDockWidget(window.color_wheel_dock)
        assert window._save_workspace("カラー作業")

        window.color_wheel_dock.closeDockWidget()
        assert not window.color_wheel_dock.isVisible()
        assert window._apply_workspace("カラー作業", restore_geometry=False)
        qapp.processEvents()

        assert window.color_wheel_dock.isVisible()
        assert (
            window.color_wheel_dock.dockAreaWidget()
            is window.tools_dock.dockAreaWidget()
        )
        assert window._delete_workspace("カラー作業")
        assert "カラー作業" not in window._workspace_records()
    finally:
        window.close()


def test_boundary_drop_does_not_move_tab_twice(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    try:
        dragged = window.action_panel_dock
        source = dragged.dockAreaWidget()
        target = window.tools_dock.dockAreaWidget()
        window._split_drop_dragged_dock = dragged
        window._split_drop_source_area = source
        window._split_drop_candidate = (QtAds.RightDockWidgetArea, target)

        # Simulate ADS completing its normal drop before the queued boundary
        # handler gets control.
        window.dock_manager.addDockWidgetTabToArea(dragged, target)
        window._commit_split_drop()

        assert dragged.dockAreaWidget() is target
        assert dragged in target.openedDockWidgets()
    finally:
        window.close()


def test_boundary_feedback_never_performs_the_drop_itself(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    try:
        dragged = window.action_panel_dock
        source = dragged.dockAreaWidget()
        target = window.tools_dock.dockAreaWidget()
        source_tabs = tuple(source.openedDockWidgets())
        target_tabs = tuple(target.openedDockWidgets())
        window._split_drop_dragged_dock = dragged
        window._split_drop_source_area = source
        window._split_drop_candidate = (QtAds.RightDockWidgetArea, target)

        window._commit_split_drop()
        qapp.processEvents()

        assert dragged.dockAreaWidget() is source
        assert tuple(source.openedDockWidgets()) == source_tabs
        assert tuple(target.openedDockWidgets()) == target_tabs
        assert dragged in source.openedDockWidgets()
    finally:
        window.close()


def test_all_initial_panels_satisfy_docking_invariants(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    try:
        qapp.processEvents()
        _assert_docking_invariants(window)
    finally:
        window.close()


def test_repeated_stack_and_split_keeps_every_panel(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    try:
        moving = (
            window.action_panel_dock,
            window.color_wheel_dock,
            window.color_slider_dock,
        )
        for _cycle in range(3):
            target = window.tools_dock.dockAreaWidget()
            for dock in moving:
                window.dock_manager.addDockWidgetTabToArea(dock, target)
            qapp.processEvents()
            assert set(moving).issubset(set(target.openedDockWidgets()))
            _assert_docking_invariants(window)

            for dock in moving:
                window.dock_manager.addDockWidget(
                    QtAds.RightDockWidgetArea,
                    dock,
                    window.central_dock.dockAreaWidget(),
                )
            qapp.processEvents()
            assert len({dock.dockAreaWidget() for dock in moving}) == len(moving)
            _assert_docking_invariants(window)
    finally:
        window.close()


def test_close_and_reopen_tab_preserves_shared_menu(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    try:
        area = window.tools_dock.dockAreaWidget()
        window.dock_manager.addDockWidgetTabToArea(window.color_wheel_dock, area)
        for _cycle in range(3):
            window.color_wheel_dock.closeDockWidget()
            qapp.processEvents()
            assert window.color_wheel_dock not in area.openedDockWidgets()
            window.color_wheel_dock.toggleView(True)
            qapp.processEvents()
            assert window.color_wheel_dock in area.openedDockWidgets()
            _assert_docking_invariants(window)
    finally:
        window.close()


def test_workspace_repeated_round_trip_preserves_all_tabs(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    try:
        area = window.tools_dock.dockAreaWidget()
        grouped = (
            window.tools_dock,
            window.color_wheel_dock,
            window.color_slider_dock,
            window.palette_dock,
        )
        for dock in grouped[1:]:
            window.dock_manager.addDockWidgetTabToArea(dock, area)
        area.setCurrentDockWidget(window.color_slider_dock)
        assert window._save_workspace("stress")

        for _cycle in range(3):
            window.dock_manager.addDockWidget(
                QtAds.LeftDockWidgetArea,
                window.color_wheel_dock,
                window.central_dock.dockAreaWidget(),
            )
            assert window._apply_workspace("stress", restore_geometry=False)
            qapp.processEvents()
            restored_area = window.tools_dock.dockAreaWidget()
            assert all(dock.dockAreaWidget() is restored_area for dock in grouped)
            assert restored_area.currentDockWidget() is window.color_slider_dock
            _assert_docking_invariants(window)
    finally:
        window.close()


@pytest.mark.parametrize(
    ("source_side", "expected_side"),
    (
        ("left", QtAds.LeftDockWidgetArea),
        ("right", QtAds.RightDockWidgetArea),
        ("top", QtAds.TopDockWidgetArea),
        ("bottom", QtAds.BottomDockWidgetArea),
    ),
)
def test_boundary_candidate_never_targets_drag_source(
    qapp, tmp_path, monkeypatch, source_side, expected_side
):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    try:
        dragged = window.action_panel_dock
        stationary = window.central_dock
        if source_side in ("left", "top"):
            area_side = (
                QtAds.LeftDockWidgetArea
                if source_side == "left" else QtAds.TopDockWidgetArea
            )
            window.dock_manager.addDockWidget(
                area_side, dragged, stationary.dockAreaWidget()
            )
        else:
            area_side = (
                QtAds.RightDockWidgetArea
                if source_side == "right" else QtAds.BottomDockWidgetArea
            )
            window.dock_manager.addDockWidget(
                area_side, dragged, stationary.dockAreaWidget()
            )
        qapp.processEvents()
        source = dragged.dockAreaWidget()
        target = stationary.dockAreaWidget()
        source_top_left = source.mapToGlobal(QPoint(0, 0))
        target_top_left = target.mapToGlobal(QPoint(0, 0))
        source_left, source_top = source_top_left.x(), source_top_left.y()
        target_left, target_top = target_top_left.x(), target_top_left.y()
        source_right = source_left + source.width() - 1
        source_bottom = source_top + source.height() - 1
        target_right = target_left + target.width() - 1
        target_bottom = target_top + target.height() - 1
        if source_side in ("left", "right"):
            x = (source_right + target_left) // 2 if source_side == "left" else (target_right + source_left) // 2
            point = QPoint(x, max(source_top, target_top) + 20)
        else:
            y = (source_bottom + target_top) // 2 if source_side == "top" else (target_bottom + source_top) // 2
            point = QPoint(max(source_left, target_left) + 20, y)

        # Exercise the direction rule directly through the real boundary scan.
        window._split_drop_dragged_dock = dragged
        monkeypatch.setattr(QApplication, "startDragDistance", lambda: 0)
        from PySide6.QtGui import QCursor
        monkeypatch.setattr(QCursor, "pos", lambda: point)
        window._update_split_drop_target()
        candidate = window._split_drop_candidate
        assert candidate is not None
        assert candidate[0] == expected_side
        assert candidate[1] is target
        assert candidate[1] is not source
    finally:
        window.close()
