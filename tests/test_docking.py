import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PySide6QtAds as QtAds  # noqa: E402
from PySide6.QtCore import (  # noqa: E402
    QEvent, QPoint, QPointF, QPropertyAnimation, QRect, Qt,
)
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QSizeGrip, QSizePolicy, QToolButton, QWidget,
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
        window.subview_dock,
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
            window.subview_dock,
            window.timeline_dock,
        ):
            assert isinstance(dock, QtAds.CDockWidget)
            close_button = dock.dockAreaWidget().titleBar().findChild(
                QToolButton, "floatingCloseButton"
            )
            assert close_button is not None
            assert not close_button.isHidden()
        assert window.dock_manager.findDockWidget("toolsDock") is window.tools_dock
        assert window.dock_manager.findDockWidget("timelineDock") is window.timeline_dock
        assert window.dock_manager.findDockWidget("subviewDock") is window.subview_dock
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


def test_docked_tab_drag_enters_manual_preview_path(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_maybe_restore_autosave", lambda self: None)
    window = MainWindow()
    window.show()
    try:
        qapp.processEvents()
        dock = window.action_panel_dock
        tab = dock.tabWidget()
        start = tab.rect().center()
        QTest.mousePress(tab, Qt.MouseButton.LeftButton, pos=start)
        assert window._pending_dock_tab_drag[0] is dock

        moved = start + QPoint(12, 0)
        move_event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(moved),
            QPointF(tab.mapToGlobal(moved)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(tab, move_event)
        assert dock.isFloating()
        assert window._floating_tab_drag_dock is dock
        assert window._floating_move_window is dock.window()

        target_area = window.central_dock.dockAreaWidget()
        target_position = target_area.mapToGlobal(target_area.rect().center())
        floating_tab = dock.tabWidget()
        hover_event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(floating_tab.mapFromGlobal(target_position)),
            QPointF(target_position),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(floating_tab, hover_event)
        assert window._manual_tab_drop_target == (
            QtAds.CenterDockWidgetArea, target_area
        )
        assert window._manual_center_effect_target is target_area

        release_event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(floating_tab.mapFromGlobal(target_position)),
            QPointF(target_position),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(floating_tab, release_event)
        qapp.processEvents()
        assert dock.dockAreaWidget() is target_area
        assert getattr(window, "_floating_drag_grabber", None) is None
    finally:
        window.close()


def test_single_floating_panel_keeps_hamburger_menu(qapp, tmp_path, monkeypatch):
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
        assert flags & Qt.WindowType.FramelessWindowHint
        assert not flags & Qt.WindowType.WindowTitleHint
        assert not flags & Qt.WindowType.WindowCloseButtonHint
        assert not flags & Qt.WindowType.WindowMinimizeButtonHint
        assert not flags & Qt.WindowType.WindowMaximizeButtonHint
        grip = floating.findChild(QSizeGrip, "floatingResizeGrip")
        assert grip is not None
        assert grip.isVisible()
        assert grip.geometry().bottomRight() == floating.rect().bottomRight()
        floating.resize(floating.width() + 80, floating.height() + 60)
        qapp.processEvents()
        assert grip.geometry().bottomRight() == floating.rect().bottomRight()
        floating_title_bar = window.tools_dock.dockAreaWidget().titleBar()
        assert floating_title_bar.isVisible()
        hamburger = floating_title_bar.findChild(QToolButton, "dockHamburger")
        assert hamburger is not None
        assert hamburger.isVisible()
        assert floating_title_bar._paintmask_move_container is floating
        close_button = floating_title_bar.findChild(
            QToolButton, "floatingCloseButton"
        )
        assert close_button is not None
        assert close_button.isVisible()
        tab = window.tools_dock.tabWidget()
        QTest.mousePress(tab, Qt.MouseButton.LeftButton, pos=tab.rect().center())
        assert window._floating_move_window is floating
        assert window._floating_tab_drag_dock is window.tools_dock
        QTest.mouseRelease(tab, Qt.MouseButton.LeftButton, pos=tab.rect().center())
        assert window._floating_move_window is None
        assert window._floating_tab_drag_dock is None
        assert window.tool_selector_dock.tabWidget().maximumWidth() > 1000
        target_area = window.tool_selector_dock.dockAreaWidget()
        target_title = target_area.titleBar()
        drop_position = target_title.mapToGlobal(target_title.rect().center())
        window._floating_tab_drag_dock = window.tools_dock
        window._update_manual_tab_drop(drop_position)
        assert window._manual_tab_drop_target == (
            QtAds.CenterDockWidgetArea, target_area
        )
        target_rect = QRect(
            target_area.mapToGlobal(QPoint()), target_area.size()
        )
        assert not window._manual_drop_preview.isVisible()
        assert window._manual_center_effect_target is target_area
        assert window._manual_center_overlay.parentWidget() is target_area
        assert window._manual_center_overlay.geometry() == target_area.rect()
        assert window._manual_center_overlay.isVisible()
        assert (
            window._manual_center_overlay.graphicsEffect()
            is window._manual_center_effect
        )
        assert (
            window._manual_center_animation.state()
            == QPropertyAnimation.State.Running
        )
        center_preview_style = window._manual_center_overlay.styleSheet()
        center_effect = window._manual_center_effect
        QTest.qWait(200)
        qapp.processEvents()
        center_opacity = center_effect.opacity()
        bottom_position = QPoint(
            target_rect.center().x(), target_rect.bottom() - 2
        )
        window._update_manual_tab_drop(bottom_position)
        assert window._manual_tab_drop_target == (
            QtAds.BottomDockWidgetArea, target_area
        )
        bottom_preview = window._manual_center_overlay.geometry()
        assert bottom_preview.bottom() == target_area.rect().bottom()
        assert bottom_preview.height() == max(
            window.tools_dock.dockAreaWidget().minimumSizeHint().height(),
            target_area.height() // 2,
        )
        assert window._manual_center_overlay.isVisible()
        assert window._manual_center_overlay.styleSheet() == center_preview_style
        assert window._manual_center_effect is center_effect
        assert window._manual_center_effect.opacity() >= center_opacity - 0.01

        window._finish_manual_tab_drop(bottom_position)
        assert not window._manual_drop_preview.isVisible()
        assert window._manual_center_effect_target is None
        assert not window._manual_center_overlay.isVisible()
        QTest.qWait(210)
        qapp.processEvents()
        docked_area = window.tools_dock.dockAreaWidget()
        assert docked_area is not target_area
        assert abs(docked_area.width() - bottom_preview.width()) <= 5
        assert abs(docked_area.height() - bottom_preview.height()) <= 5
        settled_width = docked_area.width()
        QTest.qWait(300)
        qapp.processEvents()
        assert abs(docked_area.width() - settled_width) <= 1
        docked_title_bar = window.tools_dock.dockAreaWidget().titleBar()
        assert docked_title_bar.isVisible()
        docked_close_button = docked_title_bar.findChild(
            QToolButton, "floatingCloseButton"
        )
        assert docked_close_button is not None
        assert docked_close_button.isVisible()

        # ADS rebuilds the area/title objects after docking. A second float
        # must resolve its current floating container instead of stale chrome.
        window.tools_dock.setFloating()
        QTest.qWait(20)
        qapp.processEvents()
        second_floating = window.tools_dock.window()
        second_tab = window.tools_dock.tabWidget()
        second_title_bar = window.tools_dock.dockAreaWidget().titleBar()
        second_close_button = second_title_bar.findChild(
            QToolButton, "floatingCloseButton"
        )
        assert second_close_button is not None
        assert second_close_button.isVisible()
        QTest.mousePress(
            second_tab,
            Qt.MouseButton.LeftButton,
            pos=second_tab.rect().center(),
        )
        assert window._floating_move_window is second_floating
        assert window._floating_tab_drag_dock is window.tools_dock
        QTest.mouseRelease(
            second_tab,
            Qt.MouseButton.LeftButton,
            pos=second_tab.rect().center(),
        )
        assert window._floating_move_window is None

        split_target = window.tool_selector_dock.dockAreaWidget()
        split_rect = QRect(
            split_target.mapToGlobal(QPoint()), split_target.size()
        )
        split_position = QPoint(split_rect.left() + 2, split_rect.center().y())
        window._floating_tab_drag_dock = window.tools_dock
        window._update_manual_tab_drop(split_position)
        assert window._manual_tab_drop_target == (
            QtAds.LeftDockWidgetArea, split_target
        )
        expected_width = window._manual_tab_drop_extent[1]
        assert window._manual_center_overlay.width() == expected_width
        window._finish_manual_tab_drop(split_position)
        QTest.qWait(230)
        qapp.processEvents()
        assert abs(window.tools_dock.dockAreaWidget().width() - expected_width) <= 5
        split_close_button = (
            window.tools_dock.dockAreaWidget().titleBar().findChild(
                QToolButton, "floatingCloseButton"
            )
        )
        assert split_close_button is not None
        assert split_close_button.isVisible()

        window.central_dock.setFloating()
        QTest.qWait(20)
        qapp.processEvents()
        assert window.central_dock.isFloating()
        assert window.central_dock.dockAreaWidget().titleBar().isVisible()
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
