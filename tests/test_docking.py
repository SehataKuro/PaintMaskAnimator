import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PySide6QtAds as QtAds  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QSizePolicy, QWidget,
)

from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.onion import OnionSkinSettingsBrowser  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


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
            window.drawing_color_dock,
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
        assert window._split_drop_timer.isActive()
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
