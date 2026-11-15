"""Onion-skin drag interactions for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods drive the interactive
drag editing of onion-skin shift/rotation/scale and canvas view rotation
(started from the onion settings browser). They run against a live
``PaintCanvas`` instance and reuse its geometry helpers and onion state.
"""
import math
from PySide6.QtCore import QPointF, Qt
from .i18n import tr
from ._canvas_members import CanvasMembers
from .logging_setup import get_logger

log = get_logger(__name__)


class OnionInteractionMixin(CanvasMembers):
    """Interactive drag editing of onion-skin transforms."""

    def begin_onion_transform_interaction(self, direction):
        """旧UI互換：前／後オニオンをXY移動モードにする。"""
        self.begin_onion_shift_interaction(direction, "xy")

    def begin_onion_shift_interaction(self, direction, axis="x"):
        """前／後オニオンのXYシフトをキャンバス上で調整する。"""
        axis = str(axis).lower()
        if axis not in ("x", "y", "xy"):
            axis = "xy"
        self._onion_interaction_mode = "onion_shift"
        self._onion_interaction_direction = (
            -1 if int(direction) < 0 else 1
        )
        self._onion_interaction_axis = axis
        self._onion_interaction_operation = "move"
        self._onion_interaction_dragging = False
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update_tool_cursor()
        label = (
            tr("前") if self._onion_interaction_direction < 0 else tr("後")
        )
        axis_label = (
            tr("X方向") if axis == "x"
            else tr("Y方向") if axis == "y"
            else tr("XY方向")
        )
        self.status_message.emit(
            tr("{label}のオニオンスキン：ドラッグで{label2}へ移動します。Escで解除します。").format(label=label, label2=axis_label)
        )

    def begin_onion_canvas_position_interaction(self):
        """キャンバスの移動と回転を行い、前後の相対値を補正する。"""
        self._onion_interaction_mode = "canvas"
        self._onion_interaction_direction = 0
        self._onion_interaction_axis = None
        self._onion_interaction_operation = None
        self._onion_interaction_dragging = False
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update_tool_cursor()
        self.status_message.emit(
            tr("表示位置：左ドラッグでキャンバス移動、"
            "Shift＋左ドラッグまたは右ドラッグで回転します。"
            "TU／TB拡大率は維持され、前後オニオンの相対値へ"
            "反映されます。Escで解除します。")
        )

    def set_onion_canvas_view_rotation(self, value):
        """表示位置の回転UIからキャンバス角度を変更する。"""
        target = self._normalized_angle(value)
        current = self._normalized_angle(self.rotation)
        delta = self._normalized_angle(target - current)
        if abs(delta) < 1e-9:
            return

        # キャンバス回転分を前後の相対値から差し引き、
        # オニオンの画面上の位置・角度を保つ。
        previous_shift = self._rotated_vector(
            self.onion_previous_shift_x,
            self.onion_previous_shift_y,
            -delta,
        )
        next_shift = self._rotated_vector(
            self.onion_next_shift_x,
            self.onion_next_shift_y,
            -delta,
        )
        self.onion_previous_shift_x = previous_shift.x()
        self.onion_previous_shift_y = previous_shift.y()
        self.onion_next_shift_x = next_shift.x()
        self.onion_next_shift_y = next_shift.y()
        self.onion_previous_rotation = self._normalized_angle(
            self.onion_previous_rotation - delta
        )
        self.onion_next_rotation = self._normalized_angle(
            self.onion_next_rotation - delta
        )
        self.rotation = target
        self.viewChanged.emit(
            float(self.zoom),
            float(self.rotation),
        )
        self.onionInteractionChanged.emit()
        self.update()

    def cancel_onion_interaction(self, restore=False):
        if self._onion_interaction_mode is None:
            return
        if restore and self._onion_interaction_dragging:
            self.pan = QPointF(self._onion_interaction_start_pan)
            self.rotation = float(
                self._onion_interaction_start_view_rotation
            )
            (
                self.onion_previous_shift_x,
                self.onion_previous_shift_y,
            ) = self._onion_interaction_start_previous
            (
                self.onion_next_shift_x,
                self.onion_next_shift_y,
            ) = self._onion_interaction_start_next
            self.onion_previous_rotation = float(
                self._onion_interaction_start_previous_rotation
            )
            self.onion_next_rotation = float(
                self._onion_interaction_start_next_rotation
            )
            self.onion_previous_scale = float(
                self._onion_interaction_start_previous_scale
            )
            self.onion_next_scale = float(
                self._onion_interaction_start_next_scale
            )
            self.onion_tu_tb_scale = float(
                self._onion_interaction_start_tu_tb_scale
            )
            self.onionInteractionChanged.emit()
            self.viewChanged.emit(
                float(self.zoom),
                float(self.rotation),
            )
        self._onion_interaction_mode = None
        self._onion_interaction_axis = None
        self._onion_interaction_operation = None
        self._onion_interaction_dragging = False
        self.drawing = False
        self.update_tool_cursor()
        self.update()
        self.onionInteractionFinished.emit()

    def _onion_widget_delta_to_canvas(self, delta):
        zoom = max(
            0.0001,
            float(self.zoom) * float(self.onion_tu_tb_scale),
        )
        dx = float(delta.x())
        dy = float(delta.y())
        angle = math.radians(float(self.rotation))
        canvas_dx = (
            dx * math.cos(angle) + dy * math.sin(angle)
        ) / zoom
        canvas_dy = (
            -dx * math.sin(angle) + dy * math.cos(angle)
        ) / zoom
        return QPointF(canvas_dx, canvas_dy)

    def _begin_onion_interaction_drag(
        self,
        widget_position,
        button=Qt.MouseButton.LeftButton,
        modifiers=Qt.KeyboardModifier.NoModifier,
    ):
        self._onion_interaction_dragging = True
        self._onion_interaction_start_widget = QPointF(
            widget_position
        )
        self._onion_interaction_start_pan = QPointF(self.pan)
        self._onion_interaction_start_view_rotation = float(
            self.rotation
        )
        self._onion_interaction_start_previous = (
            float(self.onion_previous_shift_x),
            float(self.onion_previous_shift_y),
        )
        self._onion_interaction_start_next = (
            float(self.onion_next_shift_x),
            float(self.onion_next_shift_y),
        )
        self._onion_interaction_start_previous_rotation = float(
            self.onion_previous_rotation
        )
        self._onion_interaction_start_next_rotation = float(
            self.onion_next_rotation
        )
        self._onion_interaction_start_previous_scale = float(
            self.onion_previous_scale
        )
        self._onion_interaction_start_next_scale = float(
            self.onion_next_scale
        )
        self._onion_interaction_start_tu_tb_scale = float(
            self.onion_tu_tb_scale
        )
        if self._onion_interaction_mode == "onion_shift":
            self._onion_interaction_operation = "move"
        else:
            self._onion_interaction_operation = (
                "rotate"
                if (
                    button == Qt.MouseButton.RightButton
                    or modifiers
                    & Qt.KeyboardModifier.ShiftModifier
                )
                else "move"
            )
        self.drawing = True
        self.update_tool_cursor()

    def _update_onion_interaction_drag(self, widget_position):
        if not self._onion_interaction_dragging:
            return
        widget_delta = (
            QPointF(widget_position)
            - self._onion_interaction_start_widget
        )
        canvas_delta = self._onion_widget_delta_to_canvas(
            widget_delta
        )

        previous_x, previous_y = (
            self._onion_interaction_start_previous
        )
        next_x, next_y = self._onion_interaction_start_next
        operation = self._onion_interaction_operation or "move"

        if self._onion_interaction_mode == "onion_shift":
            direction = self._onion_interaction_direction
            axis = self._onion_interaction_axis or "x"
            delta_x = (
                canvas_delta.x() if axis in ("x", "xy") else 0.0
            )
            delta_y = (
                canvas_delta.y() if axis in ("y", "xy") else 0.0
            )
            if direction < 0:
                self.onion_previous_shift_x = (
                    previous_x + delta_x
                )
                self.onion_previous_shift_y = (
                    previous_y + delta_y
                )
            else:
                self.onion_next_shift_x = next_x + delta_x
                self.onion_next_shift_y = next_y + delta_y

        elif self._onion_interaction_mode == "canvas":
            if operation == "rotate":
                angle_delta = float(widget_delta.x()) * 0.35
                self.rotation = self._normalized_angle(
                    self._onion_interaction_start_view_rotation
                    + angle_delta
                )

                # キャンバスの回転分をシフト座標とオニオン回転から
                # 差し引き、オニオンスキンの画面上の位置・角度を保つ。
                previous_shift = self._rotated_vector(
                    previous_x,
                    previous_y,
                    -angle_delta,
                )
                next_shift = self._rotated_vector(
                    next_x,
                    next_y,
                    -angle_delta,
                )
                self.onion_previous_shift_x = previous_shift.x()
                self.onion_previous_shift_y = previous_shift.y()
                self.onion_next_shift_x = next_shift.x()
                self.onion_next_shift_y = next_shift.y()
                self.onion_previous_rotation = (
                    self._normalized_angle(
                        self._onion_interaction_start_previous_rotation
                        - angle_delta
                    )
                )
                self.onion_next_rotation = (
                    self._normalized_angle(
                        self._onion_interaction_start_next_rotation
                        - angle_delta
                    )
                )
                self.viewChanged.emit(
                    float(self.zoom),
                    float(self.rotation),
                )
            else:
                # キャンバスは画面上でドラッグし、前後のオニオンは
                # 画面上の位置を保つよう逆向きにシフト値を補正する。
                self.pan = (
                    self._onion_interaction_start_pan
                    + widget_delta
                )
                self.onion_previous_shift_x = (
                    previous_x - canvas_delta.x()
                )
                self.onion_previous_shift_y = (
                    previous_y - canvas_delta.y()
                )
                self.onion_next_shift_x = (
                    next_x - canvas_delta.x()
                )
                self.onion_next_shift_y = (
                    next_y - canvas_delta.y()
                )
                self.viewChanged.emit(
                    float(self.zoom),
                    float(self.rotation),
                )

        self.onionInteractionChanged.emit()
        self.update()

    # ------------------------------------------------------------------
    # オニオンのコマをクリックして直接編集する（#60）
    # ------------------------------------------------------------------
    def _onion_source_point(self, point, direction):
        """キャンバス上の ``point`` に重なって見える、前／後オニオンの絵の中の位置。

        オニオンは画面の中心を軸に ``シフト → 回転 → 拡大`` して重ねているので、
        その逆をかける。
        """
        from .utils import workspace_size

        shift_x, shift_y, rotation, scale = self.onion_transform_values(direction)
        width, height = workspace_size()
        cx, cy = width / 2.0, height / 2.0
        dx = float(point.x()) - cx - shift_x
        dy = float(point.y()) - cy - shift_y
        angle = math.radians(-rotation)
        rx = dx * math.cos(angle) - dy * math.sin(angle)
        ry = dx * math.sin(angle) + dy * math.cos(angle)
        scale = max(0.01, float(scale))
        return QPointF(cx + rx / scale, cy + ry / scale)

    def _drawn_layer_at(self, frame_index, point, layers):
        """``frame_index`` で ``point`` に絵がある最初のレイヤー（無ければ None）。"""
        x, y = int(math.floor(point.x())), int(math.floor(point.y()))
        for layer_index in layers:
            key = self.resolve_key_frame(int(frame_index), int(layer_index))
            if key is None:
                continue
            layer = self.frames[key].layers[layer_index]
            image = layer.image
            if not layer.visible or not (0 <= x < image.width() and 0 <= y < image.height()):
                continue
            if image.pixelColor(x, y).alpha() > 0:
                return int(layer_index)
        return None

    def onion_frame_at(self, point):
        """``point`` に見えているオニオンのコマ ``(コマ, レイヤー)``。無ければ None。

        近いコマから順に調べる（近い絵ほど手前に描いているので）。同じ近さなら、
        あとから描く後ろのコマを先にする。今のコマに絵がある所は、今のコマを優先して
        None を返す。
        """
        if not self.onion_skin or not self.frames:
            return None
        active = int(self.active_layer_index)
        if self.onion_all_layers:
            layer_count = len(self.frames[int(self.current_frame)].layers)
            # 上のレイヤーほど手前に描かれる。今のレイヤーを先に調べる。
            layers = [active] + [i for i in reversed(range(layer_count)) if i != active]
        else:
            layers = [active]
        if self._drawn_layer_at(self.current_frame, point, [active]) is not None:
            return None
        previous = self._onion_frame_indices(-1, self.onion_previous_count)
        following = self._onion_frame_indices(1, self.onion_next_count)
        for distance in range(max(len(previous), len(following))):
            for direction, frames in ((1, following), (-1, previous)):
                if distance >= len(frames):
                    continue
                frame_index = frames[distance]
                source = self._onion_source_point(point, direction)
                layer_index = self._drawn_layer_at(frame_index, source, layers)
                if layer_index is not None:
                    return int(frame_index), layer_index
        return None

    def pick_onion_frame(self, point):
        """``point`` のオニオンのコマへ編集対象を切り替える。切り替えたら真。

        前の今のコマは、切り替えた先から見たオニオンとして薄く残る。
        """
        found = self.onion_frame_at(point)
        if found is None:
            self.status_message.emit(
                tr("オニオン：クリックした所に前後のコマの絵がありません。")
            )
            return False
        frame_index, layer_index = found
        self.current_frame = int(frame_index)
        self.active_layer_index = int(layer_index)
        self.selectionChanged.emit()
        self.update()
        self.status_message.emit(
            tr("オニオン：{frame}コマ目を編集します。").format(frame=int(frame_index) + 1)
        )
        return True
