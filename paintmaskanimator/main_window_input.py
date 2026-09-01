"""Application-level keyboard/mouse input handling for MainWindow.

Split out of ``main_window.py`` as a mixin. This is the ``eventFilter`` installed
on the application and the machinery behind it: shortcut-token normalisation and
matching, the auxiliary hold-to-drag gestures (hand scroll, rotate, zoom) with
their cursor bookkeeping, and the canvas hold-operation sync. Keeping it separate
means the window shell no longer carries the event-loop hot path.
"""
from .common import *  # noqa: F401,F403
from ._main_window_members import MainWindowMembers
from .widgets import ShortcutDialog
from .errors import OPERATION_ERRORS
from .logging_setup import get_logger

log = get_logger(__name__)

_OPERATION_ERRORS = OPERATION_ERRORS


class InputMixin(MainWindowMembers):
    def _is_color_chart_widget(self, watched):
        """Return safely during Qt teardown when the chart is already gone."""
        if not isinstance(watched, QWidget):
            return False
        chart = getattr(self, "color_chart", None)
        if chart is None:
            return False
        try:
            return watched is chart or chart.isAncestorOf(watched)
        except RuntimeError:
            # Qt can deliver one final application event after ADS has deleted
            # the dock contents but before this Python event filter is removed.
            return False

    @staticmethod
    def _normalize_shortcut_token(token):
        aliases = {
            "Control": "Ctrl",
            "CTRL": "Ctrl",
            "SHIFT": "Shift",
            "ALT": "Alt",
            "META": "Meta",
            " ": "Space",
        }
        token = str(token).strip()
        return aliases.get(token, token)

    def _shortcut_tokens(self, action, fallback):
        stored = action.property("holdShortcutText")
        if stored is not None:
            text = str(stored)
        else:
            text = action.shortcut().toString(
                QKeySequence.SequenceFormat.PortableText
            )
            if not text:
                text = fallback
        # Hold operations use the first chord only.
        text = text.split(",", 1)[0]
        return {
            self._normalize_shortcut_token(token)
            for token in text.split("+")
            if token.strip()
        }

    def _event_key_token(self, event):
        key_map = {
            Qt.Key.Key_Control: "Ctrl",
            Qt.Key.Key_Shift: "Shift",
            Qt.Key.Key_Alt: "Alt",
            Qt.Key.Key_Meta: "Meta",
            Qt.Key.Key_Space: "Space",
        }
        if event.key() in key_map:
            return key_map[event.key()]
        text = QKeySequence(int(event.key())).toString(
            QKeySequence.SequenceFormat.PortableText
        )
        return self._normalize_shortcut_token(text) if text else None

    def _sync_modifier_tokens(self, modifiers):
        mapping = (
            (Qt.KeyboardModifier.ControlModifier, "Ctrl"),
            (Qt.KeyboardModifier.ShiftModifier, "Shift"),
            (Qt.KeyboardModifier.AltModifier, "Alt"),
            (Qt.KeyboardModifier.MetaModifier, "Meta"),
        )
        for flag, token in mapping:
            if modifiers & flag:
                self._held_canvas_shortcut_tokens.add(token)
            else:
                self._held_canvas_shortcut_tokens.discard(token)

    def _widget_in_timeline(self, widget):
        return bool(
            isinstance(widget, QWidget)
            and (
                widget is self.timeline
                or self.timeline.isAncestorOf(widget)
            )
        )

    def _hand_scroll_area_for_widget(self, widget):
        if not isinstance(widget, QWidget):
            return None
        if self._widget_in_timeline(widget):
            return self.timeline.table
        candidates = (
            self.palette.scroll,
            self.tools_scroll,
            self.color_wheel_scroll,
            self.color_slider_scroll,
            self.palette_scroll,
        )
        for area in candidates:
            if widget is area or area.isAncestorOf(widget):
                return area
        return None

    @staticmethod
    def _mouse_global_position(event):
        if hasattr(event, "globalPosition"):
            return QPointF(event.globalPosition())
        return QPointF(QCursor.pos())

    def _auxiliary_cursor_targets(self):
        return (
            self.timeline,
            self.timeline.table.viewport(),
            self.tools_dock,
            self.tools_scroll.viewport(),
            self.color_wheel_dock,
            self.color_wheel_scroll.viewport(),
            self.color_slider_dock,
            self.color_slider_scroll.viewport(),
            self.palette_dock,
            self.palette_scroll.viewport(),
            self.palette.scroll.viewport(),
        )

    def _update_auxiliary_hold_cursors(self):
        tool = self.canvas.temp_tool
        for widget in self._auxiliary_cursor_targets():
            try:
                widget.unsetCursor()
            except RuntimeError:
                log.debug("cursor target was deleted during cleanup", exc_info=True)
        if tool == "hand":
            for widget in self._auxiliary_cursor_targets():
                try:
                    widget.setCursor(Qt.CursorShape.OpenHandCursor)
                except RuntimeError:
                    log.debug("cursor target was deleted during update", exc_info=True)
        elif tool == "zoom":
            try:
                self.timeline.setCursor(Qt.CursorShape.SizeVerCursor)
                self.timeline.table.viewport().setCursor(
                    Qt.CursorShape.SizeVerCursor
                )
            except RuntimeError:
                log.debug("timeline was deleted during cursor update", exc_info=True)

    def _finish_auxiliary_hold_drag(self):
        grab_widget = self._ui_hold_grab_widget
        self._ui_hold_drag_mode = None
        self._ui_hold_scroll_area = None
        self._ui_hold_grab_widget = None
        if grab_widget is not None:
            try:
                grab_widget.releaseMouse()
            except RuntimeError:
                log.debug("mouse grab owner was deleted during release", exc_info=True)
        self._update_auxiliary_hold_cursors()

    def _handle_auxiliary_hold_event(self, watched, event):
        event_type = event.type()
        tool = self.canvas.temp_tool

        if self._ui_hold_drag_mode is not None:
            if event_type == QEvent.Type.MouseMove:
                current = self._mouse_global_position(event)
                if self._ui_hold_drag_mode == "hand":
                    area = self._ui_hold_scroll_area
                    if area is not None:
                        delta = current - self._ui_hold_start_global
                        horizontal, vertical = self._ui_hold_start_scroll
                        area.horizontalScrollBar().setValue(
                            int(round(horizontal - delta.x()))
                        )
                        area.verticalScrollBar().setValue(
                            int(round(vertical - delta.y()))
                        )
                        area.viewport().setCursor(
                            Qt.CursorShape.ClosedHandCursor
                        )
                elif self._ui_hold_drag_mode == "timeline_zoom":
                    delta_y = current.y() - self._ui_hold_last_global.y()
                    factor = math.pow(1.01, -delta_y)
                    viewport = self.timeline.table.viewport()
                    anchor = viewport.mapFromGlobal(
                        current.toPoint()
                    )
                    self.timeline.adjust_timeline_zoom(
                        factor,
                        anchor.x(),
                        anchor.y(),
                    )
                    self._ui_hold_last_global = current
                event.accept()
                return True
            if event_type == QEvent.Type.MouseButtonRelease:
                self._finish_auxiliary_hold_drag()
                event.accept()
                return True

        if event_type == QEvent.Type.Wheel and tool == "zoom":
            if self._widget_in_timeline(watched):
                delta = event.angleDelta().y()
                if delta:
                    viewport = self.timeline.table.viewport()
                    global_point = self._mouse_global_position(event)
                    anchor = viewport.mapFromGlobal(global_point.toPoint())
                    self.timeline.adjust_timeline_zoom(
                        1.15 if delta > 0 else 1.0 / 1.15,
                        anchor.x(),
                        anchor.y(),
                    )
                    event.accept()
                    return True

        if event_type == QEvent.Type.MouseButtonPress:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            area = self._hand_scroll_area_for_widget(watched)
            if tool == "hand" and area is not None:
                self._ui_hold_drag_mode = "hand"
                self._ui_hold_scroll_area = area
                self._ui_hold_start_global = self._mouse_global_position(event)
                self._ui_hold_last_global = QPointF(
                    self._ui_hold_start_global
                )
                self._ui_hold_start_scroll = (
                    area.horizontalScrollBar().value(),
                    area.verticalScrollBar().value(),
                )
                self._ui_hold_grab_widget = (
                    watched if isinstance(watched, QWidget) else area.viewport()
                )
                try:
                    self._ui_hold_grab_widget.grabMouse()
                except RuntimeError:
                    self._ui_hold_grab_widget = None
                area.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return True
            if tool == "zoom" and self._widget_in_timeline(watched):
                current = self._mouse_global_position(event)
                self._ui_hold_drag_mode = "timeline_zoom"
                self._ui_hold_scroll_area = self.timeline.table
                self._ui_hold_start_global = current
                self._ui_hold_last_global = QPointF(current)
                self._ui_hold_grab_widget = (
                    watched
                    if isinstance(watched, QWidget)
                    else self.timeline.table.viewport()
                )
                try:
                    self._ui_hold_grab_widget.grabMouse()
                except RuntimeError:
                    self._ui_hold_grab_widget = None
                event.accept()
                return True

        if event_type == QEvent.Type.MouseMove:
            if tool == "hand" and self._hand_scroll_area_for_widget(watched):
                event.accept()
                return True
            if tool == "zoom" and self._widget_in_timeline(watched):
                event.accept()
                return True
        return False

    def _update_canvas_hold_operation(self):
        held = set(self._held_canvas_shortcut_tokens)
        bindings = [
            ("zoom", self.a_hold_zoom, "Ctrl+Space"),
            ("rotate", self.a_hold_rotate, "Shift+Space"),
            ("hand", self.a_hold_hand, "Space"),
            ("eyedropper", self.a_hold_eyedropper, "Alt"),
        ]
        matches = []
        for priority, (tool, action, fallback) in enumerate(bindings):
            required = self._shortcut_tokens(action, fallback)
            if required and required.issubset(held):
                matches.append((len(required), -priority, tool))
        new_tool = max(matches)[2] if matches else None
        if self.canvas.temp_tool != new_tool:
            self.canvas.temp_tool = new_tool
            self.canvas.drawing = False
            self.canvas.middle_hand = False
            self.canvas.update_tool_cursor()
            self.canvas.update()
        if (
            self._ui_hold_drag_mode == "hand"
            and new_tool != "hand"
        ) or (
            self._ui_hold_drag_mode == "timeline_zoom"
            and new_tool != "zoom"
        ):
            self._finish_auxiliary_hold_drag()
        else:
            self._update_auxiliary_hold_cursors()

    def eventFilter(self, watched, event):
        if isinstance(watched, QWidget) and event.type() in (
            QEvent.Type.ContextMenu,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
        ):
            candidate = watched
            on_dock_chrome = False
            while candidate is not None:
                if (
                    isinstance(candidate, QTabBar)
                    or type(candidate).__name__ == "CDockWidgetTab"
                    or type(candidate).__name__ == "CDockAreaTitleBar"
                ):
                    on_dock_chrome = True
                    break
                candidate = candidate.parentWidget()
            if on_dock_chrome and (
                event.type() == QEvent.Type.ContextMenu
                or event.button() == Qt.MouseButton.RightButton
            ):
                event.accept()
                return True

        pending_tab_drag = getattr(self, "_pending_dock_tab_drag", None)
        if pending_tab_drag is not None:
            if event.type() == QEvent.Type.MouseMove and (
                event.buttons() & Qt.MouseButton.LeftButton
            ):
                dock, press_global, press_in_tab = pending_tab_drag
                current_global = event.globalPosition().toPoint()
                if (current_global - press_global).manhattanLength() >= 4:
                    self._pending_dock_tab_drag = None
                    dock.setFloating()
                    floating = dock.window()
                    self._floating_move_window = floating
                    self._floating_tab_drag_dock = dock
                    floating_tab = dock.tabWidget()
                    grab_point = QPoint(
                        max(0, min(press_in_tab.x(), floating_tab.width() - 1)),
                        max(0, min(press_in_tab.y(), floating_tab.height() - 1)),
                    )
                    self._floating_move_offset = (
                        floating_tab.mapToGlobal(grab_point)
                        - floating.frameGeometry().topLeft()
                    )
                    self._start_manual_tab_drag_polling()
                    floating.move(current_global - self._floating_move_offset)
                    self._update_manual_tab_drop(current_global)
                    event.accept()
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._pending_dock_tab_drag = None
                event.accept()
                return True

        moving_window = getattr(self, "_floating_move_window", None)
        if moving_window is not None:
            if event.type() == QEvent.Type.MouseMove and (
                event.buttons() & Qt.MouseButton.LeftButton
            ):
                offset = getattr(self, "_floating_move_offset", QPoint())
                moving_window.move(event.globalPosition().toPoint() - offset)
                if getattr(self, "_floating_tab_drag_dock", None) is not None:
                    self._update_manual_tab_drop(
                        event.globalPosition().toPoint()
                    )
                event.accept()
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._manual_tab_drag_timer.stop()
                if getattr(self, "_floating_tab_drag_dock", None) is not None:
                    self._finish_manual_tab_drop(
                        event.globalPosition().toPoint()
                    )
                self._floating_drag_grabber = None
                self._floating_move_window = None
                self._floating_tab_drag_dock = None
                event.accept()
                return True

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and isinstance(watched, QWidget)
        ):
            candidate = watched
            floating = None
            blocked_by_button = False
            tab_drag = False
            dock_tab = None
            on_dock_title_bar = False
            while candidate is not None:
                if isinstance(candidate, QAbstractButton):
                    blocked_by_button = True
                    break
                if isinstance(candidate, QTabBar):
                    tab_drag = True
                if type(candidate).__name__ == "CDockWidgetTab":
                    tab_drag = True
                    dock_tab = candidate
                if type(candidate).__name__ == "CDockAreaTitleBar":
                    on_dock_title_bar = True
                floating = getattr(
                    candidate, "_paintmask_move_container", None
                )
                if floating is not None:
                    break
                candidate = candidate.parentWidget()
            if floating is None and on_dock_title_bar:
                current_window = watched.window()
                if type(current_window).__name__ == "CFloatingDockContainer":
                    floating = current_window
            if (
                floating is None
                and dock_tab is not None
                and not blocked_by_button
            ):
                dock = dock_tab.dockWidget()
                area = dock.dockAreaWidget()
                if area is not None:
                    area.setCurrentDockWidget(dock)
                self._pending_dock_tab_drag = (
                    dock,
                    event.globalPosition().toPoint(),
                    dock_tab.mapFromGlobal(event.globalPosition().toPoint()),
                )
                event.accept()
                return True
            if floating is not None and not blocked_by_button:
                if tab_drag:
                    self._floating_move_window = floating
                    self._floating_tab_drag_dock = (
                        floating.topLevelDockWidget()
                    )
                    self._start_manual_tab_drag_polling()
                    self._floating_move_offset = (
                        event.globalPosition().toPoint()
                        - floating.frameGeometry().topLeft()
                    )
                    event.accept()
                    return True
                # CFloatingDockContainer can expose a QWidgetWindow handle
                # which is not an OS top-level window. startSystemMove() then
                # emits "must be a top level window" on a plain click. Manual
                # movement below works for both native and alien containers.
                self._floating_move_window = floating
                self._floating_move_offset = (
                    event.globalPosition().toPoint()
                    - floating.frameGeometry().topLeft()
                )
                event.accept()
                return True

        # カラーチャート上では、フォーカスがボタンやスクロールバーに
        # あってもSpace系操作をチャートキャンバスへ渡す。
        if (
            self._is_color_chart_widget(watched)
            and event.type() in (
                QEvent.Type.ShortcutOverride,
                QEvent.Type.KeyPress,
                QEvent.Type.KeyRelease,
            )
        ):
            if (
                event.type() == QEvent.Type.ShortcutOverride
                and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Control)
            ):
                event.accept()
                return True
            if (
                event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
                and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Control)
                and not event.isAutoRepeat()
            ):
                self.color_chart.tile_canvas.handle_hold_key_event(event)
                event.accept()
                return True

        # サブビューパネルのSpace系操作を、アプリ全体のキャンバス用
        # ショートカットへ横取りさせない。
        if (
            isinstance(watched, QWidget)
            and (
                watched is self.subview
                or self.subview.isAncestorOf(watched)
            )
            and event.type() in (
                QEvent.Type.ShortcutOverride,
                QEvent.Type.KeyPress,
                QEvent.Type.KeyRelease,
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseMove,
                QEvent.Type.Wheel,
            )
        ):
            if (
                event.type() == QEvent.Type.ShortcutOverride
                and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Control)
            ):
                event.accept()
                return True
            if (
                event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
                and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Control)
                and not event.isAutoRepeat()
            ):
                self.subview.handle_hold_key_event(event)
                event.accept()
                return True
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseButtonRelease:
            QTimer.singleShot(0, self._sync_all_area_hamburgers)
        if type(watched).__name__ == "QSplitterHandle":
            splitter = watched.parentWidget()
            area = self.tool_selector_dock.dockAreaWidget()
            if (
                isinstance(splitter, QSplitter)
                and splitter.orientation() == Qt.Orientation.Horizontal
                and area is not None
                and splitter.indexOf(area) >= 0
            ):
                if event.type() == QEvent.Type.MouseButtonPress:
                    self._tool_selector_resize_drag_active = True
                elif event.type() == QEvent.Type.MouseButtonRelease:
                    self._tool_selector_resize_drag_active = False
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and self._pending_tool_selector_snap is not None
        ):
            self._tool_selector_snap_timer.stop()
            QTimer.singleShot(0, self._apply_tool_selector_snap)
        if event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseMove,
            QEvent.Type.Wheel,
        ) and self._handle_auxiliary_hold_event(watched, event):
            return True
        if (
            event.type() == QEvent.Type.KeyPress
            and self.canvas.transform_active
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            self.canvas.commit_selection_transform()
            return True
        if (
            event.type() == QEvent.Type.KeyPress
            and self.canvas.transform_active
            and event.key() == Qt.Key.Key_Escape
        ):
            self.canvas.cancel_selection_transform()
            return True

        if event.type() in (
            QEvent.Type.ApplicationDeactivate,
            QEvent.Type.WindowDeactivate,
        ):
            self._held_canvas_shortcut_tokens.clear()
            try:
                self.color_chart.tile_canvas.clear_hold_keys()
            except RuntimeError:
                # The chart dock can already be destroyed during app teardown.
                pass
            self._update_canvas_hold_operation()
            return super().eventFilter(watched, event)

        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            # ショートカット編集ダイアログ内では、入力したキーをキャンバス操作に使わない。
            modal = QApplication.activeModalWidget()
            if isinstance(modal, ShortcutDialog):
                return super().eventFilter(watched, event)
            if getattr(event, "isAutoRepeat", lambda: False)():
                return super().eventFilter(watched, event)
            token = self._event_key_token(event)
            if event.type() == QEvent.Type.KeyPress:
                if token:
                    self._held_canvas_shortcut_tokens.add(token)
                self._sync_modifier_tokens(event.modifiers())
            else:
                if token:
                    self._held_canvas_shortcut_tokens.discard(token)
                self._sync_modifier_tokens(event.modifiers())
                # Qtの環境によっては解放イベントのmodifiersに解放前のキーが残る。
                if token in ("Ctrl", "Shift", "Alt", "Meta"):
                    self._held_canvas_shortcut_tokens.discard(token)
            self._update_canvas_hold_operation()
            if token in ("Space", "Ctrl", "Shift", "Alt", "Meta"):
                return True
        return super().eventFilter(watched, event)
