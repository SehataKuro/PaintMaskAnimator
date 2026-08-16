"""Dock hamburger menus, split-drop overlays, and floating-window chrome.

Split out of ``main_window.py`` as a mixin. These methods build the per-dock
"hamburger" menus, drive the custom split-drop hit zones / overlay feedback
while dragging docks, and manage floating-window titles and tool-selector
snapping. They run against a live ``MainWindow`` instance and its
``dock_manager``.
"""
from .common import *  # noqa: F401,F403
from ._main_window_members import MainWindowMembers
import PySide6QtAds as QtAds
from . import config, theme
from .widgets import HSVColorWheel
from .logging_setup import get_logger

log = get_logger(__name__)


class DockingMixin(MainWindowMembers):
    def _finalize_startup_dock_ui(self):
        active = config.get_value("active_workspace")
        if not active or not self._apply_workspace(active):
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
        button.setToolTip("パネルメニュー")
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
        area = dock.dockAreaWidget()
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
            button.setToolTip("パネルメニュー")
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

    def _rebuild_area_dock_menu(self, menu, area):
        dock = area.currentDockWidget()
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
        float_action = menu.addAction("フロート表示")
        float_action.setEnabled(not dock.isFloating())
        float_action.triggered.connect(lambda _c=False, d=dock: d.setFloating())
        close_action = menu.addAction("パネルを閉じる")
        close_action.triggered.connect(
            lambda _c=False, d=dock: d.closeDockWidget()
        )

    def _build_color_wheel_menu(self, menu):
        wheel = self.tools.hsv_wheel
        hue_menu = menu.addMenu("色相の形")
        hue_labels = {"RING": "リング", "BAR": "バー"}
        for mode in HSVColorWheel.HUE_MODES:
            action = hue_menu.addAction(hue_labels[mode])
            action.setCheckable(True)
            action.setChecked(mode == wheel.hueMode())
            action.triggered.connect(
                lambda _c=False, m=mode: self.tools.set_wheel_hue_mode(m)
            )

        inner_menu = menu.addMenu("内側の形")
        labels = {"HSV": "四角（HSV）", "HLS": "三角（HLS）"}
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
        """Avoid duplicate titles without replacing ADS drag handling."""
        def update():
            area = dock.dockAreaWidget()
            if area is None:
                return
            hide_inner_tab = bool(floating) and area.openDockWidgetsCount() == 1
            area.titleBar().setVisible(not hide_inner_tab)
        QTimer.singleShot(0, update)

    def _configure_floating_window(self, floating):
        """Use a compact close-only title bar for floating panel groups."""
        floating.setWindowIcon(QIcon())
        floating.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )

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
