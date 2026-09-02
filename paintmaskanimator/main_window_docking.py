"""Dock hamburger menus, split-drop overlays, and floating-window chrome.

Split out of ``main_window.py`` as a mixin. These methods build the per-dock
"hamburger" menus, drive the custom split-drop hit zones / overlay feedback
while dragging docks, and manage floating-window titles and tool-selector
snapping. They run against a live ``MainWindow`` instance and its
``dock_manager``.
"""
from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMenu,
    QSizeGrip,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QWidget,
)
from .i18n import tr
from ._main_window_members import MainWindowMembers
import ctypes
import sys
import PySide6QtAds as QtAds
from PySide6.QtCore import QEasingCurve, QEventLoop, QObject, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect
from . import config, theme
from .widgets import HSVColorWheel
from .logging_setup import get_logger

log = get_logger(__name__)


class _FloatingGripTracker(QObject):
    """Anchor and drive every resize edge of a frameless floating container."""

    def __init__(self, floating, grip, handles=None):
        super().__init__(floating)
        self.floating = floating
        self.grip = grip
        self.handles = dict(handles or {})
        self.handles["bottom_right"] = grip
        self._manual_resize = False
        self._resize_origin = QPoint()
        self._resize_geometry = QRect()
        self._resize_edges = Qt.Edge.RightEdge | Qt.Edge.BottomEdge
        self._resize_widget = None

    def position_handles(self):
        width = max(0, self.floating.width())
        height = max(0, self.floating.height())
        edge = 6
        corner = 14
        horizontal_width = max(0, width - corner * 2)
        vertical_height = max(0, height - corner * 2)
        geometries = {
            "top": QRect(corner, 0, horizontal_width, edge),
            "bottom": QRect(
                corner, max(0, height - edge), horizontal_width, edge
            ),
            "left": QRect(0, corner, edge, vertical_height),
            "right": QRect(
                max(0, width - edge), corner, edge, vertical_height
            ),
            "top_left": QRect(0, 0, corner, corner),
            "top_right": QRect(max(0, width - corner), 0, corner, corner),
            "bottom_left": QRect(
                0, max(0, height - corner), corner, corner
            ),
        }
        for name, geometry in geometries.items():
            handle = self.handles.get(name)
            if handle is not None:
                handle.setGeometry(geometry)
                handle.raise_()
        self.grip.move(
            max(0, width - self.grip.width()),
            max(0, height - self.grip.height()),
        )
        self.grip.raise_()

    @staticmethod
    def _clamp_dimension(value, minimum, maximum):
        maximum = max(minimum, maximum)
        return max(minimum, min(maximum, value))

    def _apply_manual_resize(self, delta):
        geometry = self._resize_geometry
        minimum = self.floating.minimumSize().expandedTo(
            self.floating.minimumSizeHint()
        )
        maximum = self.floating.maximumSize()
        x, y = geometry.x(), geometry.y()
        width, height = geometry.width(), geometry.height()

        if self._resize_edges & Qt.Edge.LeftEdge:
            resized = self._clamp_dimension(
                width - delta.x(), minimum.width(), maximum.width()
            )
            x += width - resized
            width = resized
        elif self._resize_edges & Qt.Edge.RightEdge:
            width = self._clamp_dimension(
                width + delta.x(), minimum.width(), maximum.width()
            )

        if self._resize_edges & Qt.Edge.TopEdge:
            resized = self._clamp_dimension(
                height - delta.y(), minimum.height(), maximum.height()
            )
            y += height - resized
            height = resized
        elif self._resize_edges & Qt.Edge.BottomEdge:
            height = self._clamp_dimension(
                height + delta.y(), minimum.height(), maximum.height()
            )

        self.floating.setGeometry(x, y, width, height)

    def eventFilter(self, watched, event):
        if watched is self.floating and event.type() in (
            QEvent.Type.Resize, QEvent.Type.Show
        ):
            self.position_handles()
        if watched not in self.handles.values():
            return False
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._resize_edges = getattr(
                watched,
                "_paintmask_resize_edges",
                Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
            )
            handle = (
                self.floating.windowHandle()
                if self.floating.isWindow() else None
            )
            if (
                handle is not None
                and handle.isTopLevel()
                and handle.startSystemResize(self._resize_edges)
            ):
                event.accept()
                return True
            self._manual_resize = True
            self._resize_origin = event.globalPosition().toPoint()
            self._resize_geometry = QRect(self.floating.geometry())
            self._resize_widget = watched
            try:
                watched.grabMouse()
            except RuntimeError:
                pass
            event.accept()
            return True
        if event.type() == QEvent.Type.MouseMove and self._manual_resize:
            delta = event.globalPosition().toPoint() - self._resize_origin
            self._apply_manual_resize(delta)
            event.accept()
            return True
        if event.type() == QEvent.Type.MouseButtonRelease:
            resize_widget = self._resize_widget
            self._manual_resize = False
            self._resize_widget = None
            if resize_widget is not None:
                try:
                    resize_widget.releaseMouse()
                except RuntimeError:
                    pass
            event.accept()
            return True
        return False


class DockingMixin(MainWindowMembers):
    def _finalize_startup_dock_ui(self):
        active = config.get_value("active_workspace")
        if not active or not self.workspace.apply(active):
            # A save/restore cycle makes the initial areas follow the same ADS
            # reconstruction path used after tabs are stacked.
            state = self.dock_manager.saveState()
            self.dock_manager.restoreState(state)
        self._sync_all_area_hamburgers()

    def _hamburger_icon(self):
        """フォントに依存しない3本線アイコンを生成して使い回す。"""
        cached = getattr(self, "_hamburger_icon_cache", None)
        if cached is not None:
            return cached
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(QColor("#53606a"), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        for y in (7, 12, 17):
            painter.drawLine(5, y, 19, y)
        painter.end()
        icon = QIcon(pixmap)
        self._hamburger_icon_cache = icon
        return icon

    def _add_dock_hamburger_old(self, dock, specific_builder=None):
        """ドックのタブ左端にハンバーガーメニューボタンを設置する。

        ボタンはドック所有とし、フロート／再ドッキングでタブが作り直され
        ても失われないよう、その都度タブへ再挿入する。
        """
        button = QToolButton(dock)
        button.setObjectName("dockHamburger")
        button.setIcon(self._hamburger_icon())
        button.setIconSize(QSize(12, 12))
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tr("パネルメニュー"))
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setFixedSize(18, 18)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setStyleSheet(
            "QToolButton{border:none;background:transparent;padding:0;margin:0 2px;}"
            "QToolButton::menu-indicator{image:none;width:0;}"
        )
        menu = QMenu(button)
        menu.aboutToShow.connect(
            lambda m=menu, d=dock, b=specific_builder:
            self._rebuild_dock_menu(m, d, b)
        )
        button.setMenu(menu)
        dock._hamburger_button = button
        dock._hamburger_builder = specific_builder
        self._attach_dock_hamburger(dock)
        # タブが生成済みでない構築初期でも確実に挿入されるよう遅延実行する。
        QTimer.singleShot(0, lambda d=dock: self._attach_dock_hamburger(d))
        dock.topLevelChanged.connect(
            lambda _floating, d=dock:
            QTimer.singleShot(0, lambda: self._attach_dock_hamburger(d))
        )

    def _attach_dock_hamburger(self, dock):
        button = getattr(dock, "_hamburger_button", None)
        if button is None:
            return
        tab = dock.tabWidget()
        if tab is None:
            return
        layout = tab.layout()
        if layout is None:
            return
        for index in range(layout.count()):
            if layout.itemAt(index).widget() is button:
                return
        button.setParent(tab)
        layout.insertWidget(0, button)
        button.show()

    def _add_dock_hamburger(self, dock, specific_builder=None):
        self._dock_menu_builders[dock] = specific_builder
        self._attach_area_hamburger(dock)
        QTimer.singleShot(0, lambda d=dock: self._attach_area_hamburger(d))
        dock.topLevelChanged.connect(
            lambda _floating, d=dock:
            QTimer.singleShot(0, lambda: self._attach_area_hamburger(d))
        )

    def _attach_area_hamburger(self, dock):
        # Reached from queued singleShot / topLevelChanged callbacks; the dock's
        # C++ object may already be gone by the time they run.
        try:
            area = dock.dockAreaWidget()
        except RuntimeError as exc:
            log.debug("_attach_area_hamburger on deleted dock: %s", exc)
            return
        if area is None:
            return
        title_bar = area.titleBar()
        buttons = title_bar.findChildren(
            QToolButton,
            "dockHamburger",
            Qt.FindChildOption.FindDirectChildrenOnly,
        )
        cached = getattr(area, "_hamburger_button", None)
        button = cached if cached in buttons else (buttons[0] if buttons else None)
        for duplicate in buttons:
            if duplicate is button:
                continue
            title_bar.layout().removeWidget(duplicate)
            duplicate.hide()
            duplicate.setParent(None)
            duplicate.deleteLater()
        if button is None:
            button = QToolButton(title_bar)
            button.setObjectName("dockHamburger")
            button.setIcon(self._hamburger_icon())
            button.setIconSize(QSize(12, 12))
            button.setAutoRaise(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(tr("パネルメニュー"))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedSize(18, 18)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            button.setStyleSheet(
                "QToolButton{border:none;background:transparent;"
                "padding:0;margin:0;}"
                "QToolButton::menu-indicator{image:none;width:0;}"
            )
            menu = QMenu(button)
            menu.aboutToShow.connect(
                lambda m=menu, a=area: self._rebuild_area_dock_menu(m, a)
            )
            button.setMenu(menu)
            title_bar.layout().insertWidget(0, button)
            area._hamburger_button = button
            area.currentChanged.connect(
                lambda _index, a=area: self._sync_area_hamburger(a)
            )
        if title_bar.layout().indexOf(button) != 0:
            title_bar.layout().removeWidget(button)
            title_bar.layout().insertWidget(0, button)
        area._hamburger_button = button
        self._sync_area_hamburger(area)

    def _sync_all_area_hamburgers(self):
        """Restore shared buttons after ADS has regrouped or rebuilt areas."""
        for area in self.dock_manager.openedDockAreas():
            if any(
                dock in self._dock_menu_builders
                for dock in area.openedDockWidgets()
            ):
                current = area.currentDockWidget()
                source = (
                    current if current in self._dock_menu_builders
                    else next(
                        dock for dock in area.openedDockWidgets()
                        if dock in self._dock_menu_builders
                    )
                )
                self._attach_area_hamburger(source)

    def _sync_area_hamburger(self, area):
        # These run from queued signals (currentChanged / topLevelChanged); ADS
        # may have already destroyed the underlying C++ CDockAreaWidget by the
        # time they fire, so any attribute access raises RuntimeError. Bail out
        # instead of crashing.
        try:
            title_bar = area.titleBar()
            title_bar.setFixedHeight(22)
            title_bar.tabBar().setFixedHeight(22)
            for dock in area.openedDockWidgets():
                tab = dock.tabWidget()
                tab.ensurePolished()
                tab.setFixedHeight(20)
                tab.setSizePolicy(
                    tab.sizePolicy().horizontalPolicy(),
                    QSizePolicy.Policy.Fixed,
                )
            button = getattr(area, "_hamburger_button", None)
            if button is not None:
                button.setVisible(
                    area.currentDockWidget() in self._dock_menu_builders
                )
        except RuntimeError as exc:
            log.debug("_sync_area_hamburger on deleted area: %s", exc)

    def _rebuild_area_dock_menu(self, menu, area):
        # aboutToShow can fire after ADS deleted the area's C++ object.
        try:
            dock = area.currentDockWidget()
        except RuntimeError as exc:
            log.debug("_rebuild_area_dock_menu on deleted area: %s", exc)
            menu.clear()
            return
        if dock not in self._dock_menu_builders:
            menu.clear()
            return
        self._rebuild_dock_menu(
            menu, dock, self._dock_menu_builders.get(dock)
        )

    def _rebuild_dock_menu(self, menu, dock, specific_builder):
        menu.clear()
        if specific_builder is not None:
            specific_builder(menu)
            menu.addSeparator()
        self._build_default_dock_menu(menu, dock)

    def _build_default_dock_menu(self, menu, dock):
        float_action = menu.addAction(tr("フロート表示"))
        float_action.setEnabled(not dock.isFloating())
        float_action.triggered.connect(lambda _c=False, d=dock: d.setFloating())
        close_action = menu.addAction(tr("パネルを閉じる"))
        close_action.triggered.connect(
            lambda _c=False, d=dock: d.closeDockWidget()
        )

    def _build_action_panel_menu(self, menu):
        edit_action = menu.addAction(tr("スクリプトを編集"))
        edit_action.triggered.connect(self.action_panel.open_script_editor)

    def _build_color_wheel_menu(self, menu):
        wheel = self.tools.hsv_wheel
        hue_menu = menu.addMenu(tr("色相の形"))
        hue_labels = {"RING": tr("リング"), "BAR": tr("バー")}
        for mode in HSVColorWheel.HUE_MODES:
            action = hue_menu.addAction(hue_labels[mode])
            action.setCheckable(True)
            action.setChecked(mode == wheel.hueMode())
            action.triggered.connect(
                lambda _c=False, m=mode: self.tools.set_wheel_hue_mode(m)
            )

        inner_menu = menu.addMenu(tr("内側の形"))
        labels = {"HSV": tr("四角（HSV）"), "HLS": tr("三角（HLS）")}
        for mode in HSVColorWheel.MODES:
            action = inner_menu.addAction(labels[mode])
            action.setCheckable(True)
            action.setChecked(mode == wheel.mode())
            action.triggered.connect(
                lambda _c=False, m=mode: self.tools.set_wheel_mode(m)
            )

    def _build_color_slider_menu(self, menu):
        current = self.tools.slider_mode
        for mode in ("RGB", "HLS", "CMYK"):
            action = menu.addAction(mode)
            action.setCheckable(True)
            action.setChecked(mode == current)
            action.triggered.connect(
                lambda _c=False, m=mode: self.tools.set_slider_mode(m)
            )

    def _customize_docking_hover(self):
        """Apply richer hover feedback while keeping ADS drop geometry intact."""
        c = theme.palette()
        self.dock_manager.setStyleSheet(
            "ads--CDockAreaWidget{border:0;background:%(window)s;}"
            "ads--CDockAreaWidget:hover{border:0;}"
            "ads--CDockAreaTitleBar{background:%(surface_alt)s;"
            "border-bottom:1px solid %(border)s;min-height:22px;max-height:22px;}"
            "ads--CDockAreaTitleBar:hover{background:%(hover)s;border-bottom-color:%(border)s;}"
            "ads--CDockWidgetTab{background:%(surface_alt)s;color:%(text_muted)s;"
            "border:1px solid transparent;border-radius:5px 5px 0 0;"
            "padding:0 9px;min-height:20px;max-height:20px;}"
            "ads--CDockWidgetTab:hover{background:%(hover)s;color:%(text)s;"
            "border-color:transparent;}"
            "ads--CDockWidgetTab[activeTab=\"true\"]{background:%(surface)s;"
            "color:%(text)s;border-color:%(border)s;border-bottom-color:%(surface)s;}"
            "ads--CDockWidgetTab[activeTab=\"true\"]:hover{background:%(surface)s;"
            "color:%(text)s;border-color:%(accent)s;}"
            "ads--CDockSplitter::handle{background:%(border)s;}"
            "ads--CDockSplitter::handle:hover{background:%(text_muted)s;}"
            % c
        )

        # Keep the standard ADS target calculation and drop-area preview, but
        # make its central cross glyph fully transparent.
        for overlay in (
            self.dock_manager.containerOverlay(),
            self.dock_manager.dockAreaOverlay(),
        ):
            overlay.ensurePolished()
            overlay.enableDropPreview(True)
            overlay.setWindowOpacity(0.42)
        for cross in self.dock_manager.findChildren(QtAds.CDockOverlayCross):
            cross.setWindowOpacity(0.0)
            self._expand_docking_hit_zones(cross)

    def _expand_docking_hit_zones(self, cross):
        """Divide the complete hovered panel into five ADS drop targets."""
        layout = cross.layout()
        targets = {
            int(target.property("dockWidgetArea")): target
            for target in cross.findChildren(QLabel, "DockWidgetAreaLabel")
        }
        placements = {
            int(QtAds.TopDockWidgetArea): (0, 0, 1, 3),
            int(QtAds.LeftDockWidgetArea): (1, 0, 1, 1),
            int(QtAds.CenterDockWidgetArea): (1, 1, 1, 1),
            int(QtAds.RightDockWidgetArea): (1, 2, 1, 1),
            int(QtAds.BottomDockWidgetArea): (2, 0, 1, 3),
        }
        for target in targets.values():
            layout.removeWidget(target)
            target.setMinimumSize(0, 0)
            target.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
        layout.setContentsMargins(0, 0, 0, 0)
        for row in range(5):
            layout.setRowStretch(row, 1 if row < 3 else 0)
            layout.setColumnStretch(row, 1 if row < 3 else 0)
        for area, placement in placements.items():
            target = targets.get(area)
            if target is not None:
                layout.addWidget(target, *placement)

    def _setup_split_drop_overlay(self):
        """Create the insertion marker used for drops between dock areas."""
        self._manual_drop_preview = QWidget(None)
        self._manual_drop_preview.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self._manual_drop_preview.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self._manual_drop_preview.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )
        self._manual_drop_preview.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating
        )
        self._manual_drop_preview.setStyleSheet(
            "background:rgba(82,145,190,72);"
            "border:2px solid rgba(105,180,230,210);"
            "border-radius:4px;"
        )
        self._manual_drop_animation = QPropertyAnimation(
            self._manual_drop_preview, b"windowOpacity", self
        )
        self._manual_drop_animation.setDuration(160)
        self._manual_drop_animation.setStartValue(0.45)
        self._manual_drop_animation.setEndValue(1.0)
        self._manual_drop_animation.setEasingCurve(
            QEasingCurve.Type.OutCubic
        )
        self._manual_drop_last_candidate = None
        self._manual_center_effect_target = None
        self._manual_center_effect = None
        self._manual_center_animation = None
        # One stable, unclipped overlay for every dock area.  Parenting this to
        # an individual area clips bottom/top previews when the eventual dock
        # branch is wider than that area.
        self._manual_center_overlay = QWidget(self.dock_manager)
        self._manual_center_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self._manual_center_overlay.setStyleSheet(
            "background:rgba(112,92,210,125);"
            "border:2px solid rgba(145,125,245,240);"
            "border-radius:4px;"
        )
        self._manual_center_overlay.hide()
        self._manual_tab_drag_timer = QTimer(self)
        self._manual_tab_drag_timer.setInterval(16)
        self._manual_tab_drag_timer.timeout.connect(
            self._poll_manual_tab_drag
        )
        self._split_drop_overlay = QWidget(None)
        self._split_drop_overlay.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self._split_drop_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self._split_drop_overlay.setStyleSheet(
            "background:#657680;border-radius:2px;"
        )
        self._split_drop_skeletons = []
        for _index in range(2):
            skeleton = QWidget(None)
            skeleton.setWindowFlags(
                Qt.WindowType.Tool
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
            )
            skeleton.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents
            )
            skeleton.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            skeleton.setStyleSheet(
                "background:rgba(90,110,122,38);"
                "border:2px solid rgba(90,110,122,150);"
                "border-radius:3px;"
            )
            self._split_drop_skeletons.append(skeleton)
        self._split_drop_timer = QTimer(self)
        self._split_drop_timer.setInterval(33)
        self._split_drop_timer.timeout.connect(self._update_split_drop_target)

    def _start_split_drop_monitor(self):
        if not self._split_drop_timer.isActive():
            self._split_drop_timer.start()

    def _active_dragged_dock(self):
        for floating in self.dock_manager.floatingWidgets():
            if floating.isDraggingActive():
                return floating.topLevelDockWidget()
        if self._split_drop_dragged_dock is not None:
            return self._split_drop_dragged_dock
        return None

    def _update_split_drop_target(self):
        dragged = self._active_dragged_dock()
        if dragged is None:
            if not self.dock_manager.floatingWidgets():
                self._split_drop_timer.stop()
            self._hide_split_drop_feedback()
            # Native window dragging does not always deliver the release to
            # QApplication. Commit on the active -> released transition too.
            if (
                self._split_drop_candidate is not None
                and QApplication.mouseButtons() == Qt.MouseButton.NoButton
            ):
                QTimer.singleShot(0, self._commit_split_drop)
            return
        cursor = QCursor.pos()
        areas = [
            area for area in self.dock_manager.findChildren(QtAds.CDockAreaWidget)
            if area.isVisible() and area.openDockWidgetsCount() > 0
        ]
        boundaries = []
        best = None
        for first in areas:
            first_rect = QRectF(
                first.mapToGlobal(first.rect().topLeft()), first.size()
            ).toRect()
            for second in areas:
                if first is second:
                    continue
                second_rect = QRectF(
                    second.mapToGlobal(second.rect().topLeft()), second.size()
                ).toRect()
                vertical_gap = abs(first_rect.right() - second_rect.left())
                overlap_top = max(first_rect.top(), second_rect.top())
                overlap_bottom = min(first_rect.bottom(), second_rect.bottom())
                if overlap_bottom > overlap_top and vertical_gap <= 8:
                    boundary = (first_rect.right() + second_rect.left()) // 2
                    boundaries.append(("v", boundary, overlap_top,
                                       overlap_bottom, first, first_rect,
                                       second, second_rect))
                horizontal_gap = abs(first_rect.bottom() - second_rect.top())
                overlap_left = max(first_rect.left(), second_rect.left())
                overlap_right = min(first_rect.right(), second_rect.right())
                if overlap_right > overlap_left and horizontal_gap <= 8:
                    boundary = (first_rect.bottom() + second_rect.top()) // 2
                    boundaries.append(("h", boundary, overlap_left,
                                       overlap_right, first, first_rect,
                                       second, second_rect))
        for (
            orientation, boundary, _start, _end, first, first_rect,
            _second, _second_rect,
        ) in boundaries:
            group = [
                entry for entry in boundaries
                if entry[0] == orientation and abs(entry[1] - boundary) <= 4
            ]
            group_start = min(entry[2] for entry in group)
            group_end = max(entry[3] for entry in group)
            tolerance = 26 if len(group) > 1 else 14
            distance = abs(
                cursor.x() - boundary if orientation == "v"
                else cursor.y() - boundary
            )
            along = cursor.y() if orientation == "v" else cursor.x()
            if not (distance <= tolerance and group_start <= along <= group_end):
                continue
            containing = [entry for entry in group if entry[2] <= along <= entry[3]]
            chosen = min(
                containing or group,
                key=lambda entry: 0 if entry[2] <= along <= entry[3]
                else min(abs(along - entry[2]), abs(along - entry[3])),
            )
            first, first_rect = chosen[4], chosen[5]
            second, second_rect = chosen[6], chosen[7]
            dragged_in_first = dragged in first.dockWidgets()
            if orientation == "v":
                marker = QRect(boundary - 2, group_start, 4,
                               group_end - group_start + 1)
                if dragged_in_first:
                    side = QtAds.LeftDockWidgetArea
                    target_area, target_rect = second, second_rect
                else:
                    side = QtAds.RightDockWidgetArea
                    target_area, target_rect = first, first_rect
            else:
                marker = QRect(group_start, boundary - 2,
                               group_end - group_start + 1, 4)
                if dragged_in_first:
                    side = QtAds.TopDockWidgetArea
                    target_area, target_rect = second, second_rect
                else:
                    side = QtAds.BottomDockWidgetArea
                    target_area, target_rect = first, first_rect
            score = (distance, -len(group))
            if best is None or score < best[0]:
                best = (score, side, target_area, marker, target_rect)
        if best is None:
            self._split_drop_candidate = None
            self._split_drop_dragged_dock = dragged
            self._hide_split_drop_feedback()
            return
        _, side, target_area, marker, target_rect = best
        self._split_drop_candidate = (side, target_area)
        self._split_drop_dragged_dock = dragged
        if self._split_drop_source_area is None:
            self._split_drop_source_area = dragged.dockAreaWidget()
        self._split_drop_overlay.setGeometry(marker)
        self._split_drop_overlay.show()
        self._split_drop_overlay.raise_()
        self._show_split_skeleton(side, target_rect)

    def _show_split_skeleton(self, side, target_rect):
        first = QRect(target_rect)
        inserted = QRect(target_rect)
        if side == QtAds.RightDockWidgetArea:
            inserted_width = max(48, int(target_rect.width() * 0.35))
            inserted.setLeft(target_rect.right() - inserted_width + 1)
            first.setRight(inserted.left() - 3)
        else:
            inserted_height = max(48, int(target_rect.height() * 0.35))
            inserted.setTop(target_rect.bottom() - inserted_height + 1)
            first.setBottom(inserted.top() - 3)
        for widget, geometry in zip(
            self._split_drop_skeletons, (first, inserted)
        ):
            widget.setGeometry(geometry)
            widget.show()
            widget.raise_()

    def _hide_split_drop_feedback(self):
        self._split_drop_overlay.hide()
        for skeleton in self._split_drop_skeletons:
            skeleton.hide()

    def _update_manual_tab_drop(self, global_pos):
        """Show the native ADS area overlay during a frameless tab drag."""
        overlay = self.dock_manager.dockAreaOverlay()
        target = None
        target_rect = None
        for area in self.dock_manager.openedDockAreas():
            if area.window() is not self.window() or not area.isVisible():
                continue
            rect = QRect(area.mapToGlobal(QPoint()), area.size())
            if rect.contains(global_pos):
                target = area
                target_rect = rect
                break
        if target is None:
            overlay.hideOverlay()
            self._clear_manual_center_effect()
            self._manual_drop_animation.stop()
            self._manual_drop_preview.hide()
            self._manual_drop_last_candidate = None
            self._manual_tab_drop_target = None
            self._manual_tab_drop_extent = None
            return
        assert target_rect is not None
        title_bar = target.titleBar()
        title_rect = QRect(
            title_bar.mapToGlobal(QPoint()), title_bar.size()
        )
        if title_rect.contains(global_pos):
            side = QtAds.CenterDockWidgetArea
        else:
            x_ratio = (global_pos.x() - target_rect.left()) / max(
                1, target_rect.width()
            )
            y_ratio = (global_pos.y() - target_rect.top()) / max(
                1, target_rect.height()
            )
            edge = min(x_ratio, 1 - x_ratio, y_ratio, 1 - y_ratio)
            if edge >= 0.25:
                side = QtAds.CenterDockWidgetArea
            elif edge == x_ratio:
                side = QtAds.LeftDockWidgetArea
            elif edge == 1 - x_ratio:
                side = QtAds.RightDockWidgetArea
            elif edge == y_ratio:
                side = QtAds.TopDockWidgetArea
            else:
                side = QtAds.BottomDockWidgetArea
        # The ADS overlay has its own palette and briefly paints underneath our
        # preview (most noticeably for the bottom target).  The custom preview
        # is the sole feedback for this manual drag path.
        overlay.hideOverlay()
        preview_rect = QRect(target_rect)
        extent = None
        dragged = getattr(self, "_floating_tab_drag_dock", None)
        minimum = QSize(1, 1)
        if dragged is not None:
            try:
                dragged_area = dragged.dockAreaWidget()
                hints = (
                    dragged_area.minimumSizeHint(),
                    dragged_area.sizeHint(),
                    dragged_area.titleBar().minimumSizeHint(),
                    dragged_area.titleBar().sizeHint(),
                    dragged.minimumSizeHint(),
                    dragged.sizeHint(),
                    dragged.tabWidget().minimumSizeHint(),
                    dragged.tabWidget().sizeHint(),
                )
                minimum = QSize(
                    max(1, dragged.window().width(),
                        *(hint.width() for hint in hints)),
                    max(1, *(hint.height() for hint in hints)),
                )
            except RuntimeError:
                pass
        target_minimum_width = 1
        try:
            target_dock = target.currentDockWidget()
            target_hints = (
                target.minimumSizeHint(),
                target.sizeHint(),
                target.titleBar().minimumSizeHint(),
                target.titleBar().sizeHint(),
                target_dock.minimumSizeHint(),
                target_dock.sizeHint(),
                target_dock.tabWidget().minimumSizeHint(),
                target_dock.tabWidget().sizeHint(),
                target_dock.widget().minimumSizeHint(),
                target_dock.widget().sizeHint(),
            )
            target_minimum_width = max(
                1, target_dock.width(), target_dock.widget().width(),
                *(hint.width() for hint in target_hints)
            )
        except (AttributeError, RuntimeError):
            pass
        target_branch_width = target_rect.width()
        try:
            branch = target
            parent = branch.parentWidget()
            while parent is not None:
                if (
                    isinstance(parent, QSplitter)
                    and parent.orientation() == Qt.Orientation.Horizontal
                ):
                    target_branch_width = branch.width()
                    break
                branch = parent
                parent = branch.parentWidget()
        except RuntimeError:
            pass
        if side == QtAds.LeftDockWidgetArea:
            preview_rect.setWidth(max(minimum.width(), target_rect.width() // 2))
            extent = (Qt.Orientation.Horizontal, preview_rect.width())
        elif side == QtAds.RightDockWidgetArea:
            width = max(minimum.width(), target_rect.width() // 2)
            preview_rect.setLeft(target_rect.right() - width + 1)
            extent = (Qt.Orientation.Horizontal, width)
        elif side == QtAds.TopDockWidgetArea:
            preview_rect.setHeight(max(minimum.height(), target_rect.height() // 2))
            preview_rect.setWidth(max(
                minimum.width(), target_minimum_width,
                target_rect.width(), target_branch_width,
            ))
            extent = (Qt.Orientation.Vertical, preview_rect.height())
        elif side == QtAds.BottomDockWidgetArea:
            height = max(minimum.height(), target_rect.height() // 2)
            preview_rect.setTop(target_rect.bottom() - height + 1)
            preview_rect.setWidth(max(
                minimum.width(), target_minimum_width,
                target_rect.width(), target_branch_width,
            ))
            extent = (Qt.Orientation.Vertical, height)
        if extent is not None:
            extent = (*extent, QSize(preview_rect.size()))
        candidate_key = (int(side), id(target))
        local_preview = QRect(
            self.dock_manager.mapFromGlobal(preview_rect.topLeft()),
            preview_rect.size(),
        )
        self._show_manual_center_effect(
            target, local_preview, candidate_key, side
        )
        self._manual_drop_preview.hide()
        self._manual_tab_drop_target = (side, target)
        self._manual_tab_drop_extent = extent

    def _show_manual_center_effect(self, target, geometry, candidate_key, side):
        if (
            self._manual_center_effect_target is target
            and self._manual_center_overlay.isVisible()
        ):
            # Switching between center and an edge on the same area must not
            # hide/recreate the overlay. Recreating resets opacity to zero and
            # produces a one-frame flash, especially at the bottom boundary.
            self._manual_center_overlay.setGeometry(geometry)
            self._manual_center_overlay.raise_()
            self._manual_drop_last_candidate = candidate_key
            return
        self._clear_manual_center_effect()
        overlay = self._manual_center_overlay
        overlay.setGeometry(geometry)
        overlay.setStyleSheet(
            "background:rgba(112,92,210,125);"
            "border:2px solid rgba(145,125,245,240);"
            "border-radius:4px;"
        )
        effect = QGraphicsOpacityEffect(overlay)
        effect.setOpacity(0.0)
        overlay.setGraphicsEffect(effect)
        overlay.show()
        overlay.raise_()
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(180)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._manual_center_effect_target = target
        self._manual_center_effect = effect
        self._manual_center_animation = animation
        self._manual_drop_last_candidate = candidate_key
        animation.start()

    def _clear_manual_center_effect(self):
        animation = getattr(self, "_manual_center_animation", None)
        if animation is not None:
            animation.stop()
        overlay = getattr(self, "_manual_center_overlay", None)
        if overlay is not None:
            overlay.hide()
            overlay.setGraphicsEffect(None)
        self._manual_center_effect_target = None
        self._manual_center_effect = None
        self._manual_center_animation = None

    def _finish_manual_tab_drop(self, global_pos):
        overlay = self.dock_manager.dockAreaOverlay()
        candidate = getattr(self, "_manual_tab_drop_target", None)
        extent = getattr(self, "_manual_tab_drop_extent", None)
        dock = getattr(self, "_floating_tab_drag_dock", None)
        side = candidate[0] if candidate is not None else None
        freeze_layout = (
            candidate is not None
            and dock is not None
            and side != QtAds.CenterDockWidgetArea
        )
        if freeze_layout:
            # Preserve the last preview frame until the final splitter geometry
            # is ready; otherwise clearing it exposes one intermediate layout.
            self.setUpdatesEnabled(False)
        overlay.hideOverlay()
        self._clear_manual_center_effect()
        self._manual_drop_animation.stop()
        self._manual_drop_preview.hide()
        self._manual_drop_last_candidate = None
        self._manual_tab_drop_target = None
        self._manual_tab_drop_extent = None
        if candidate is None or dock is None:
            return
        side, target = candidate
        if side == QtAds.CenterDockWidgetArea:
            self.dock_manager.addDockWidgetTabToArea(dock, target)
        else:
            try:
                self.dock_manager.addDockWidget(side, dock, target)
                QApplication.processEvents(
                    QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                )
                self._apply_manual_drop_extent(dock, target, extent)
            except Exception:
                self.setUpdatesEnabled(True)
                self.update()
                raise
            # ADS queues more than one splitter layout pass after insertion.
            # Keep painting suspended until those passes have settled, and
            # re-apply the preview extent before exposing the final layout.
            for delay in (0, 20, 80, 180):
                QTimer.singleShot(
                    delay,
                    lambda d=dock, t=target, e=extent:
                    self._apply_manual_drop_extent(d, t, e),
                )
            QTimer.singleShot(
                200,
                lambda d=dock, t=target, e=extent:
                self._finish_manual_drop_layout(d, t, e),
            )
        for delay in (0, 20, 80, 180):
            QTimer.singleShot(
                delay,
                lambda d=dock: self._sync_floating_title(d, False),
            )
        QTimer.singleShot(0, self._sync_all_area_hamburgers)

    def _start_manual_tab_drag_polling(self):
        if not self._manual_tab_drag_timer.isActive():
            self._manual_tab_drag_timer.start()

    def _poll_manual_tab_drag(self):
        floating = getattr(self, "_floating_move_window", None)
        dock = getattr(self, "_floating_tab_drag_dock", None)
        if floating is None or dock is None:
            self._manual_tab_drag_timer.stop()
            return
        global_pos = QCursor.pos()
        if self._physical_left_button_pressed():
            offset = getattr(self, "_floating_move_offset", QPoint())
            try:
                floating.move(global_pos - offset)
            except RuntimeError:
                self._manual_tab_drag_timer.stop()
                return
            self._update_manual_tab_drop(global_pos)
            return
        self._manual_tab_drag_timer.stop()
        self._finish_manual_tab_drop(global_pos)
        self._floating_drag_grabber = None
        self._floating_move_window = None
        self._floating_tab_drag_dock = None

    @staticmethod
    def _physical_left_button_pressed():
        if sys.platform == "win32":
            try:
                return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
            except (AttributeError, OSError):
                pass
        return bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)

    @staticmethod
    def _apply_manual_drop_extent(dock, target_area, extent):
        """Match the new splitter branch to the size shown in the preview."""
        if extent is None:
            return
        try:
            new_area = dock.dockAreaWidget()
            splitter = new_area.parentWidget()
            if not isinstance(splitter, QSplitter):
                return
            orientation, requested = extent[:2]
            if splitter.orientation() != orientation:
                return

            def direct_branch(widget):
                branch = widget
                while branch is not None and branch.parentWidget() is not splitter:
                    branch = branch.parentWidget()
                return branch

            new_branch = direct_branch(new_area)
            target_branch = direct_branch(target_area)
            if new_branch is None or target_branch is None:
                return
            new_index = splitter.indexOf(new_branch)
            target_index = splitter.indexOf(target_branch)
            if new_index < 0 or target_index < 0 or new_index == target_index:
                return
            sizes = splitter.sizes()
            combined = sizes[new_index] + sizes[target_index]
            desired = max(1, min(int(requested), combined - 1))
            sizes[new_index] = desired
            sizes[target_index] = combined - desired
            splitter.setSizes(sizes)
            if len(extent) >= 3:
                preview_size = extent[2]
                perpendicular = (
                    Qt.Orientation.Vertical
                    if orientation == Qt.Orientation.Horizontal
                    else Qt.Orientation.Horizontal
                )
                perpendicular_extent = (
                    preview_size.height()
                    if perpendicular == Qt.Orientation.Vertical
                    else preview_size.width()
                )
                DockingMixin._resize_ancestor_splitter_branch(
                    new_area, perpendicular, perpendicular_extent
                )
        except RuntimeError:
            return

    @staticmethod
    def _resize_ancestor_splitter_branch(widget, orientation, requested):
        """Resize the outer branch that controls a dock's other dimension."""
        branch = widget
        parent = branch.parentWidget()
        while parent is not None:
            if isinstance(parent, QSplitter) and parent.orientation() == orientation:
                index = parent.indexOf(branch)
                sizes = parent.sizes()
                if index < 0 or len(sizes) < 2:
                    return
                desired = max(1, int(requested))
                delta = sizes[index] - desired
                recipients = [i for i in range(len(sizes)) if i != index]
                recipient = max(recipients, key=lambda i: sizes[i])
                sizes[index] = desired
                sizes[recipient] = max(1, sizes[recipient] + delta)
                parent.setSizes(sizes)
                return
            branch = parent
            parent = branch.parentWidget()

    def _finish_manual_drop_layout(self, dock, target_area, extent):
        """Expose a manual drop only after ADS' queued splitter passes."""
        try:
            self._apply_manual_drop_extent(dock, target_area, extent)
        finally:
            self.setUpdatesEnabled(True)
            self.update()

    def _commit_split_drop(self):
        """Finish custom boundary feedback without performing a second drop.

        QtAds owns the actual mouse-release drop. Calling addDockWidget here as
        well races its internal floating-container cleanup and can orphan the
        dragged tab, especially with native Windows title-bar dragging.
        """
        self._split_drop_candidate = None
        self._split_drop_dragged_dock = None
        self._split_drop_source_area = None
        self._split_drop_press_pos = None
        self._hide_split_drop_feedback()
        QTimer.singleShot(0, self._sync_all_area_hamburgers)

    def _snap_tool_selector_width(self, content_width):
        """Queue a snap after resizing settles to avoid fighting the drag."""
        if (
            QApplication.mouseButtons() == Qt.MouseButton.NoButton
            and not self._tool_selector_resize_drag_active
        ):
            # ADS also resizes dock contents while docking, tabbing, restoring
            # layouts, and resizing the main window. Those layout-driven
            # changes must not make the tool panel resize itself again.
            return
        self._pending_tool_selector_snap = int(content_width)
        self._tool_selector_snap_timer.start()

    def _sync_floating_title(self, dock, floating):
        """Keep panel menus reachable while avoiding empty floating chrome."""
        def update():
            try:
                area = dock.dockAreaWidget()
                floating_now = dock.isFloating()
            except RuntimeError:
                return
            if area is None:
                return
            # The floating container is frameless, so this is its only drag bar.
            title_bar = area.titleBar()
            title_bar.setVisible(True)
            if floating_now:
                title_bar._paintmask_move_container = dock.window()
            elif hasattr(title_bar, "_paintmask_move_container"):
                del title_bar._paintmask_move_container
            close_button = title_bar.findChild(
                QToolButton,
                "floatingCloseButton",
                Qt.FindChildOption.FindDirectChildrenOnly,
            )
            if close_button is None:
                close_button = QToolButton(title_bar)
                close_button.setObjectName("floatingCloseButton")
                close_button.setText("×")
                close_button.setToolTip(tr("パネルを閉じる"))
                close_button.setAutoRaise(True)
                close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                close_button.setFixedSize(18, 18)
                close_button.setStyleSheet(
                    "QToolButton{border:none;background:transparent;"
                    "padding:0;margin:0;font-size:16px;}"
                    "QToolButton:hover{background:rgba(220,60,60,120);}"
                )
                close_button.clicked.connect(
                    lambda _checked=False, a=area:
                    self._close_current_area_dock(a)
                )
                title_bar.layout().addWidget(close_button)
            close_button.setVisible(True)
            if dock in self._dock_menu_builders:
                self._attach_area_hamburger(dock)
        # ADS can rebuild the area after topLevelChanged, especially on the
        # second and later float cycles. Re-apply chrome after both phases.
        update()
        for delay in (10, 50):
            QTimer.singleShot(delay, update)

    @staticmethod
    def _close_current_area_dock(area):
        try:
            dock = area.currentDockWidget()
            if dock is not None:
                dock.closeDockWidget()
        except RuntimeError:
            return

    def _configure_floating_window(self, floating):
        """Use the ADS panel bar as the sole chrome for floating groups."""
        floating.setWindowIcon(QIcon())
        floating.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
        )
        grip = QSizeGrip(floating)
        grip.setObjectName("floatingResizeGrip")
        grip.setFixedSize(16, 16)
        grip.setCursor(Qt.CursorShape.SizeFDiagCursor)
        grip._paintmask_resize_edges = (
            Qt.Edge.RightEdge | Qt.Edge.BottomEdge
        )
        grip.raise_()
        grip.show()
        handle_specs = {
            "top": (Qt.Edge.TopEdge, Qt.CursorShape.SizeVerCursor),
            "bottom": (Qt.Edge.BottomEdge, Qt.CursorShape.SizeVerCursor),
            "left": (Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor),
            "right": (Qt.Edge.RightEdge, Qt.CursorShape.SizeHorCursor),
            "top_left": (
                Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
                Qt.CursorShape.SizeFDiagCursor,
            ),
            "top_right": (
                Qt.Edge.TopEdge | Qt.Edge.RightEdge,
                Qt.CursorShape.SizeBDiagCursor,
            ),
            "bottom_left": (
                Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
                Qt.CursorShape.SizeBDiagCursor,
            ),
        }
        resize_handles = {}
        for name, (edges, cursor) in handle_specs.items():
            handle = QWidget(floating)
            handle.setObjectName(
                "floatingResize" + "".join(
                    part.title() for part in name.split("_")
                )
            )
            handle.setCursor(cursor)
            handle.setStyleSheet("background:transparent;")
            handle._paintmask_resize_edges = edges
            handle.show()
            resize_handles[name] = handle
        floating._paintmask_resize_grip = grip
        floating._paintmask_resize_handles = resize_handles
        tracker = _FloatingGripTracker(floating, grip, resize_handles)
        floating._paintmask_grip_tracker = tracker
        floating.installEventFilter(tracker)
        grip.installEventFilter(tracker)
        for handle in resize_handles.values():
            handle.installEventFilter(tracker)
        self._position_floating_resize_grip(floating)
        QTimer.singleShot(
            0, lambda f=floating: self._position_floating_resize_grip(f)
        )
        def sync_floating_docks():
            try:
                docks = tuple(floating.dockWidgets())
            except RuntimeError:
                return
            for current in docks:
                self._sync_floating_title(current, True)
        for delay in (0, 10, 50):
            QTimer.singleShot(delay, sync_floating_docks)

    @staticmethod
    def _position_floating_resize_grip(floating):
        try:
            grip = getattr(floating, "_paintmask_resize_grip", None)
        except RuntimeError:
            return
        if grip is None:
            return
        try:
            tracker = getattr(floating, "_paintmask_grip_tracker", None)
            if tracker is not None:
                tracker.position_handles()
            else:
                grip.move(
                    max(0, floating.width() - grip.width()),
                    max(0, floating.height() - grip.height()),
                )
                grip.raise_()
        except RuntimeError:
            return

    def _apply_tool_selector_snap(self):
        """Apply the last requested column width once per completed drag."""
        if self._pending_tool_selector_snap is None:
            return
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            # A pause while dragging is not the end of the resize. Keep the
            # request pending and retry only as a fallback for platforms where
            # the native resize release is not delivered to the app.
            self._tool_selector_snap_timer.start()
            return
        # Snap to the number of columns QListView actually rendered, rather
        # than guessing from which mathematical width is nearest.
        columns = self.tool_selector.displayed_column_count()
        self.tool_selector._column_count = columns
        self.tool_selector._resize_swatch_to_columns(columns)
        content_width = self.tool_selector.width_for_columns(columns)
        self._pending_tool_selector_snap = None
        dock = self.tool_selector_dock
        frame_width = max(0, dock.width() - self.tool_selector.width())
        target_width = int(content_width) + frame_width
        if dock.isFloating():
            floating_window = dock.window()
            floating_window.resize(target_width, floating_window.height())
        else:
            self._resize_tool_selector_area(content_width)
