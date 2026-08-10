"""Brush stabilizer (pointer smoothing) for :class:`PaintCanvas`.

Split out of ``canvas.py`` as a mixin. These methods smooth raw pointer input
into a settled brush position/pressure, drive the follow-up settle timer, and
finish the stabilized stroke. They run against a live ``PaintCanvas`` instance.
"""
from .common import *  # noqa: F401,F403
from .logging_setup import get_logger

log = get_logger(__name__)


class BrushStabilizerMixin:
    def set_brush_stabilizer(self, value):
        self.brush_stabilizer_strength = max(
            0, min(300, int(value))
        )

    def _brush_stabilizer_window(self):
        strength = max(
            0, min(300, int(self.brush_stabilizer_strength))
        )
        if strength <= 0:
            return 1
        return max(2, min(64, 2 + int(round(strength * 0.20))))

    def _reset_brush_stabilizer(self, point=None):
        initial = QPointF(point) if point is not None else None
        self._stabilized_canvas = (
            QPointF(initial) if initial is not None else None
        )
        self._last_raw_canvas = (
            QPointF(initial) if initial is not None else None
        )
        self._brush_stabilizer_history = (
            [QPointF(initial)] if initial is not None else []
        )
        self._last_brush_pressure = 1.0
        self._pressure_input_history = []

    def _smooth_brush_pressure(self, pressure):
        """直近の筆圧を重み付き平均し、サイズ変化の段差を抑える。"""
        value = max(0.001, min(1.0, float(pressure)))
        history = self._pressure_input_history
        history.append(value)
        if len(history) > 7:
            del history[:-7]
        weights = list(range(1, len(history) + 1))
        return sum(
            sample * weight
            for sample, weight in zip(history, weights)
        ) / float(sum(weights))

    def _stabilized_brush_point(self, raw_point, settle=0.0):
        """移動平均＋遅延半径で、実際に描画する座標を返す。"""
        raw = QPointF(raw_point)
        strength = max(
            0, min(300, int(self.brush_stabilizer_strength))
        )
        settle = max(0.0, min(1.0, float(settle)))
        self._last_raw_canvas = QPointF(raw)

        if strength <= 0:
            self._brush_stabilizer_history = [QPointF(raw)]
            self._stabilized_canvas = QPointF(raw)
            return QPointF(raw)

        history = self._brush_stabilizer_history
        history.append(QPointF(raw))
        window = self._brush_stabilizer_window()
        if len(history) > window:
            del history[:-window]

        # 新しい点を少し強くしつつ、直近の細かな揺れを平均化する。
        weights = list(range(1, len(history) + 1))
        weight_sum = float(sum(weights))
        target = QPointF(
            sum(point.x() * weight for point, weight in zip(history, weights))
            / weight_sum,
            sum(point.y() * weight for point, weight in zip(history, weights))
            / weight_sum,
        )

        if self._stabilized_canvas is None:
            self._stabilized_canvas = QPointF(target)
            return QPointF(target)

        current = QPointF(self._stabilized_canvas)
        dx = target.x() - current.x()
        dy = target.y() - current.y()
        distance = math.hypot(dx, dy)

        # 画面上の約0～20pxを補正半径として使う。
        # 強度が大きいほど、小さな手振れでは描画点が動かない。
        radius_screen = (
            60.0 * math.pow(strength / 300.0, 1.35)
        )
        radius_canvas = (
            radius_screen / max(0.05, float(self.zoom))
        )
        radius_canvas *= (1.0 - settle)

        if distance <= radius_canvas or distance <= 1e-9:
            filtered = current
        else:
            move_distance = distance - radius_canvas
            ratio = move_distance / distance
            filtered = QPointF(
                current.x() + dx * ratio,
                current.y() + dy * ratio,
            )

        self._stabilized_canvas = QPointF(filtered)
        return filtered

    def _draw_stabilized_brush_to(self, raw_point, pressure):
        """入力イベント間も補間し、補正後の軌跡を連続描画する。"""
        raw = QPointF(raw_point)
        self._brush_follow_settle = 0.0
        self._start_brush_follow_timer()
        if self.last_canvas is None:
            self.last_canvas = QPointF(raw)

        strength = max(
            0, min(300, int(self.brush_stabilizer_strength))
        )
        if strength <= 0:
            self.draw_line(self.last_canvas, raw, pressure)
            self.last_canvas = QPointF(raw)
            self._last_raw_canvas = QPointF(raw)
            return

        start_raw = (
            QPointF(self._last_raw_canvas)
            if self._last_raw_canvas is not None
            else QPointF(raw)
        )
        distance = math.hypot(
            raw.x() - start_raw.x(),
            raw.y() - start_raw.y(),
        )
        step_canvas = max(
            0.5,
            min(
                2.5,
                1.5 / max(0.05, float(self.zoom)),
            ),
        )
        steps = max(
            1,
            min(96, int(math.ceil(distance / step_canvas))),
        )

        for index in range(1, steps + 1):
            ratio = index / float(steps)
            intermediate = QPointF(
                start_raw.x() + (raw.x() - start_raw.x()) * ratio,
                start_raw.y() + (raw.y() - start_raw.y()) * ratio,
            )
            filtered = self._stabilized_brush_point(intermediate)
            if (
                abs(filtered.x() - self.last_canvas.x()) > 0.005
                or abs(filtered.y() - self.last_canvas.y()) > 0.005
            ):
                self.draw_line(
                    self.last_canvas,
                    filtered,
                    pressure,
                )
                self.last_canvas = QPointF(filtered)

    def _start_brush_follow_timer(self):
        self._brush_follow_settle = 0.0
        if (
            int(self.brush_stabilizer_strength) > 0
            and not self._brush_follow_timer.isActive()
        ):
            self._brush_follow_timer.start()

    def _stop_brush_follow_timer(self):
        self._brush_follow_timer.stop()
        self._brush_follow_settle = 0.0

    def _advance_stabilized_brush(self):
        """入力が止まっていても、描画点をカーソル終点へ追従させる。"""
        if (
            not self.drawing
            or self.effective_tool() != "brush"
            or self._last_raw_canvas is None
            or self.last_canvas is None
        ):
            self._stop_brush_follow_timer()
            return

        raw = QPointF(self._last_raw_canvas)
        distance = math.hypot(
            raw.x() - self.last_canvas.x(),
            raw.y() - self.last_canvas.y(),
        )
        if distance <= 0.01:
            return

        strength = max(
            0,
            min(300, int(self.brush_stabilizer_strength)),
        )
        if strength <= 0:
            self.draw_line(
                self.last_canvas,
                raw,
                self._last_brush_pressure,
            )
            self.last_canvas = QPointF(raw)
            return

        self._brush_follow_settle = min(
            1.0,
            self._brush_follow_settle
            + 0.035
            + 0.055 * (strength / 300.0),
        )
        filtered = self._stabilized_brush_point(
            raw,
            settle=self._brush_follow_settle,
        )
        if (
            abs(filtered.x() - self.last_canvas.x()) > 0.005
            or abs(filtered.y() - self.last_canvas.y()) > 0.005
        ):
            self.draw_line(
                self.last_canvas,
                filtered,
                self._last_brush_pressure,
            )
            self.last_canvas = QPointF(filtered)

    def _finish_stabilized_brush(self, raw_point, pressure):
        """ペンを離したとき、補正点を終点へ滑らかに収束させる。"""
        raw = QPointF(raw_point)
        self._draw_stabilized_brush_to(raw, pressure)

        strength = max(
            0, min(300, int(self.brush_stabilizer_strength))
        )
        if strength <= 0:
            return

        settle_steps = self._brush_stabilizer_window() + 8
        for index in range(settle_steps):
            settle = (index + 1) / float(settle_steps)
            filtered = self._stabilized_brush_point(
                raw,
                settle=settle,
            )
            if (
                abs(filtered.x() - self.last_canvas.x()) > 0.005
                or abs(filtered.y() - self.last_canvas.y()) > 0.005
            ):
                self.draw_line(
                    self.last_canvas,
                    filtered,
                    pressure,
                )
                self.last_canvas = QPointF(filtered)

        # 数値誤差だけが残った場合も、終点を確実に一致させる。
        if (
            abs(raw.x() - self.last_canvas.x()) > 0.01
            or abs(raw.y() - self.last_canvas.y()) > 0.01
        ):
            self.draw_line(self.last_canvas, raw, pressure)
            self.last_canvas = QPointF(raw)
