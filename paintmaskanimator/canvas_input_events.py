"""Pointer / tablet / keyboard event handlers for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These are the Qt event overrides that
turn mouse/tablet/keyboard input into painting, panning, zooming, and tool
shortcuts. They run against a live ``PaintCanvas`` instance; because the mixin
precedes ``QWidget`` in the MRO, these overrides win over the base class.
"""
from .common import *  # noqa: F401,F403
from ._canvas_members import CanvasMembers
from .logging_setup import get_logger

log = get_logger(__name__)


class InputEventMixin(CanvasMembers):
    def enterEvent(self, event):
        local = self.mapFromGlobal(QCursor.pos())
        self._brush_cursor_widget_pos = QPointF(local)
        self._brush_cursor_inside = self.inside(
            self.widget_to_canvas(self._brush_cursor_widget_pos)
        )
        self.update_tool_cursor()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._brush_cursor_inside = False
        self.update_tool_cursor()
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self,e):
        factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.set_zoom_around_canvas_center(self.zoom * factor)
        self.viewChanged.emit(float(self.zoom), float(self.rotation))
        self.update()
        e.accept()

    def keyPressEvent(self,e):
        if (
            self._onion_interaction_mode is not None
            and e.key() == Qt.Key.Key_Escape
        ):
            self.cancel_onion_interaction(restore=True)
            e.accept()
            return

        if self.transform_active and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            window = self.window()
            if hasattr(window, "commit_transform_or_tween"):
                window.commit_transform_or_tween()
            else:
                self.commit_selection_transform()
            e.accept()
            return
        if self.transform_active and e.key() == Qt.Key.Key_Escape:
            window = self.window()
            if hasattr(window, "cancel_transform_or_tween"):
                window.cancel_transform_or_tween()
            else:
                self.cancel_selection_transform()
            e.accept()
            return
        if self.effective_tool() == "line" and self.line_curve_stage == 2:
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.commit_line_tool()
                e.accept()
                return
            if e.key() == Qt.Key.Key_Escape:
                self.line_start = None
                self.line_end = None
                self.line_control = None
                self.line_curve_stage = 0
                self.drawing = False
                self.update()
                e.accept()
                return
        super().keyPressEvent(e)

    def keyReleaseEvent(self,e):
        super().keyReleaseEvent(e)

    def mousePressEvent(self,e):
        self.setFocus()
        self.last_widget = e.position()
        self._brush_cursor_widget_pos = QPointF(e.position())
        self._brush_cursor_inside = self.inside(
            self.widget_to_canvas(e.position())
        )
        self.update_tool_cursor()
        if e.button() == Qt.MouseButton.MiddleButton:
            self.middle_hand = True
            self.drawing = True
            self.update_tool_cursor()
            return

        if (
            self._onion_interaction_mode is not None
            and e.button() in (
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.RightButton,
            )
        ):
            self._begin_onion_interaction_drag(
                e.position(),
                e.button(),
                e.modifiers(),
            )
            e.accept()
            return

        if e.button() == Qt.MouseButton.RightButton:
            p = self.widget_to_canvas(e.position())
            if self.inside(p):
                self.sample_color(p)
                e.accept()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return

        t = self.effective_tool()
        p = self.widget_to_canvas(e.position())
        if t == "hand":
            self.drawing = True
            self.update_tool_cursor()
            return
        if t in ("zoom", "rotate"):
            self.drawing = True
            self.last_widget = e.position()
            self.update_tool_cursor()
            return
        if self.transform_active:
            self.drawing = self.begin_transform_drag(p)
            if self.drawing:
                self.update()
            return
        if (
            not self.inside(p)
            and t not in ("lasso", "lasso_fill", "rect_select")
        ):
            return
        if t == "eyedropper":
            self.sample_color(
                p,
                include_canvas_background=(self.temp_tool == "eyedropper"),
            )
            return
        if t == "line":
            if self.line_curve_stage == 2:
                self.line_control = QPointF(p)
                self.commit_line_tool()
                return
            self.line_start = QPointF(p)
            self.line_end = QPointF(p)
            self.line_control = None
            self.line_curve_stage = 1
            self.drawing = True
            self.update()
            return
        if t == "shape":
            self.shape_start = QPointF(p)
            self.shape_end = QPointF(p)
            self.drawing = True
            self.update()
            return
        if t == "brush":
            self._brush_started_with_content = bool(self.active_layer.has_content)
            self.ensure_editable_key()
            self._begin_opaque_brush_stroke()
            self.drawing = True
            self._reset_brush_stabilizer(p)
            self._start_brush_follow_timer()
            self.last_canvas = QPointF(p)
            self.draw_line(p, p, 1.0)
            used_color = self.opaque_paint_color(
                opacity=self._brush_stroke_opacity
            )
            if not self.is_pseudo_transparent_color(used_color):
                self.colorUsed.emit(used_color)
        elif t == "bucket":
            self.ensure_editable_key()
            self.flood_fill(QPoint(int(p.x()), int(p.y())))
        elif t == "auto_select":
            self.auto_select_region(
                QPoint(int(p.x()), int(p.y())),
                e.modifiers(),
            )
        elif t in ("lasso", "lasso_fill"):
            self.ensure_editable_key()
            self.drawing = True
            self.lasso = [p]
        elif t == "rect_select":
            self.ensure_editable_key()
            self.drawing = True
            self.rect_start = p
            self.rect_end = p
        elif t == "mesh":
            self.ensure_editable_key()
            if not self.mesh_points:
                self.push_layer_undo()
                self.init_mesh()
            self.mesh_active = self.nearest_mesh(p)
            self.drawing = self.mesh_active >= 0
            self.update()

    def mouseMoveEvent(self,e):
        old_cursor = QPointF(self._brush_cursor_widget_pos)
        self._brush_cursor_widget_pos = QPointF(e.position())
        self._brush_cursor_inside = self.inside(
            self.widget_to_canvas(e.position())
        )
        self.update_tool_cursor()
        cursor_radius = max(
            8,
            int(math.ceil(float(self.pen_size) * max(self.zoom, 0.01) / 2.0)) + 4,
        )
        self.update(
            QRectF(old_cursor, old_cursor).adjusted(
                -cursor_radius, -cursor_radius,
                cursor_radius, cursor_radius,
            ).toAlignedRect()
        )
        self.update(
            QRectF(self._brush_cursor_widget_pos, self._brush_cursor_widget_pos).adjusted(
                -cursor_radius, -cursor_radius,
                cursor_radius, cursor_radius,
            ).toAlignedRect()
        )
        t = self.effective_tool()
        p = self.widget_to_canvas(e.position())

        if self._onion_interaction_mode is not None:
            if self._onion_interaction_dragging:
                self._update_onion_interaction_drag(e.position())
            return

        # 曲線の2段階目はボタンを押していなくても曲率をプレビューする。
        if t == "line" and self.line_curve_stage == 2 and not self.drawing:
            if self.inside(p):
                self.line_control = QPointF(p)
                self.update()
            return
        if not self.drawing:
            return
        if self.middle_hand or t == "hand":
            delta = e.position() - self.last_widget
            self.pan += delta
            self.last_widget = e.position()
            self.update()
            return
        if t == "zoom":
            delta = e.position().y() - self.last_widget.y()
            factor = math.pow(1.01, -delta)
            self.set_zoom_around_canvas_center(self.zoom * factor)
            self.last_widget = e.position()
            self.viewChanged.emit(float(self.zoom), float(self.rotation))
            self.update()
            return
        if t == "rotate":
            delta = e.position().x() - self.last_widget.x()
            self.rotation = ((self.rotation + delta * 0.35 + 180.0) % 360.0) - 180.0
            self.last_widget = e.position()
            self.viewChanged.emit(float(self.zoom), float(self.rotation))
            self.update()
            return
        if self.transform_active and self.drawing:
            self.update_transform_drag(p)
            return
        if t == "eyedropper" and self.inside(p):
            self.sample_color(
                p,
                include_canvas_background=(self.temp_tool == "eyedropper"),
            )
            return
        if t == "brush" and self.inside(p):
            self._draw_stabilized_brush_to(p, 1.0)
        elif t == "line" and self.line_curve_stage == 1 and self.inside(p):
            self.line_end = QPointF(p)
            self.update()
        elif t == "shape" and self.inside(p):
            self.shape_end = QPointF(p)
            self.update()
        elif t in ("lasso", "lasso_fill"):
            # キャンバス外から開始／通過しても軌跡を保持する。
            self.lasso.append(QPointF(p))
            self.update()
        elif t == "rect_select":
            # キャンバス外の座標も保持し、確定時に画像領域へクリップする。
            self.rect_end = QPointF(p)
            self.update()
        elif t == "mesh" and self.mesh_active >= 0:
            self.mesh_points[self.mesh_active] = p
            self.update()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.effective_tool() == "rotate":
            self.rotation = 0.0
            self.viewChanged.emit(float(self.zoom), 0.0)
            self.update()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def mouseReleaseEvent(self,e):
        if e.button() == Qt.MouseButton.MiddleButton:
            self.middle_hand = False
            self.drawing = False
            self.update_tool_cursor()
            return

        if (
            self._onion_interaction_mode is not None
            and e.button() in (
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.RightButton,
            )
        ):
            if self._onion_interaction_dragging:
                self._update_onion_interaction_drag(e.position())
            self.cancel_onion_interaction(restore=False)
            e.accept()
            return

        if e.button() != Qt.MouseButton.LeftButton:
            return

        t = self.effective_tool()
        if self.transform_active and self.drawing:
            self.end_transform_drag()
            self.drawing = False
            self.update()
            return

        if t == "brush":
            raw_release = self.widget_to_canvas(e.position())
            if (
                self.drawing
                and self.inside(raw_release)
                and self.last_canvas is not None
            ):
                self._finish_stabilized_brush(
                    raw_release,
                    1.0,
                )
            self._stop_brush_follow_timer()
            self._finish_opaque_brush_stroke()
            self._reset_brush_stabilizer()
            self._cell_structure_dirty = (
                not self._brush_started_with_content
                and bool(self.active_layer.has_content)
            )
            self.cellChanged.emit(self.current_frame, self.active_layer_index)
        elif t == "line" and self.line_curve_stage == 1:
            self.drawing = False
            panel = self._active_tool_panel()
            if panel is not None and panel.line_type.currentText() == "曲線":
                self.line_curve_stage = 2
                self.line_control = QPointF(
                    (self.line_start.x() + self.line_end.x()) / 2.0,
                    (self.line_start.y() + self.line_end.y()) / 2.0,
                )
                self.status_message.emit(
                    "マウスを動かしてカーブを調整し、クリックで確定します。Escで取消。"
                )
                self.update()
                return
            self.commit_line_tool()
            return
        elif t == "shape":
            self.commit_shape_tool()
            return
        elif t == "lasso" and len(self.lasso) >= 3:
            self.selection_polygon = list(self.lasso)
            self.selection_mask_override = None
            self.selection_outline_polygons = []
            self.selection_mask_rect = None
            self.lasso = []
            self.selectionChanged.emit()
        elif t == "lasso_fill" and len(self.lasso) >= 3:
            polygon = list(self.lasso)
            self.lasso = []
            self.fill_lasso_polygon(polygon)
        elif t == "rect_select" and self.rect_start is not None and self.rect_end is not None:
            x1, x2 = sorted((self.rect_start.x(), self.rect_end.x()))
            y1, y2 = sorted((self.rect_start.y(), self.rect_end.y()))
            self.selection_polygon = [
                QPointF(x1, y1), QPointF(x2, y1),
                QPointF(x2, y2), QPointF(x1, y2),
            ]
            self.selection_mask_override = None
            self.selection_outline_polygons = []
            self.selection_mask_rect = None
            self.rect_start = None
            self.rect_end = None
            self.selectionChanged.emit()
        elif t == "mesh" and self.mesh_active >= 0:
            self.mesh_active = -1
        self.drawing = False
        self.update_tool_cursor()
        self.update()

    def tabletEvent(self,e):
        self._brush_cursor_widget_pos = QPointF(e.position())
        self._brush_cursor_inside = self.inside(
            self.widget_to_canvas(e.position())
        )
        self.update_tool_cursor()
        self.update()

        # オニオン操作中はブラシ等の通常ツールを一切動作させない。
        # ペンを離した時点で操作を確定し、元のツールへ戻る。
        if self._onion_interaction_mode is not None:
            event_type = e.type()
            if event_type == e.Type.TabletPress:
                tablet_button = (
                    Qt.MouseButton.RightButton
                    if e.buttons() & Qt.MouseButton.RightButton
                    else Qt.MouseButton.LeftButton
                )
                self._begin_onion_interaction_drag(
                    e.position(),
                    tablet_button,
                    e.modifiers(),
                )
            elif (
                event_type == e.Type.TabletMove
                and self._onion_interaction_dragging
            ):
                self._update_onion_interaction_drag(e.position())
            elif event_type == e.Type.TabletRelease:
                if self._onion_interaction_dragging:
                    self._update_onion_interaction_drag(e.position())
                self.cancel_onion_interaction(restore=False)
            e.accept()
            return

        if self.effective_tool() != "brush":e.ignore();return
        p=self.widget_to_canvas(e.position()); pressure=max(.001,float(e.pressure()))
        if e.type()==e.Type.TabletPress and self.inside(p):
            self._brush_started_with_content = bool(self.active_layer.has_content)
            self.ensure_editable_key()
            self._begin_opaque_brush_stroke()
            self.drawing = True
            self._reset_brush_stabilizer(p)
            self._start_brush_follow_timer()
            self.last_canvas = QPointF(p)
            pressure = self._smooth_brush_pressure(pressure)
            self._last_brush_pressure = pressure
            self.draw_line(p, p, pressure)
            used_color = self.opaque_paint_color(
                opacity=self._brush_stroke_opacity
            )
            if not self.is_pseudo_transparent_color(used_color):
                self.colorUsed.emit(used_color)
            e.accept()
        elif e.type()==e.Type.TabletMove and self.drawing:
            if self.inside(p):
                pressure = self._smooth_brush_pressure(
                    pressure
                )
                self._last_brush_pressure = pressure
                self._draw_stabilized_brush_to(
                    p,
                    pressure,
                )
            e.accept()
        elif e.type()==e.Type.TabletRelease:
            if (
                self.drawing
                and self.inside(p)
                and self.last_canvas is not None
            ):
                self._finish_stabilized_brush(
                    p,
                    max(
                        0.001,
                        float(self._last_brush_pressure),
                    ),
                )
            self._stop_brush_follow_timer()
            self._finish_opaque_brush_stroke()
            self._reset_brush_stabilizer()
            self.drawing=False
            self._cell_structure_dirty = (
                not self._brush_started_with_content
                and bool(self.active_layer.has_content)
            )
            self.cellChanged.emit(self.current_frame,self.active_layer_index)
            e.accept()
        else:e.ignore()
