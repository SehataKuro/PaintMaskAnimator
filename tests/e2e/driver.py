"""E2Eシナリオ用のドライバー。

テスト本文が「ブラシで線を引く」「フレームを追加する」といった *ユーザー操作の言葉*
だけで書けるように、QTest によるイベント注入・ダイアログの差し替え・座標変換を
すべてこの層に閉じ込める。ウィジェットの内部APIを直接叩くのはここだけにする。
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QPointingDevice, QTabletEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QFileDialog


from paintmaskanimator.main_window import MainWindow


class AppDriver:
    """起動済み MainWindow に対するユーザー操作のファサード。"""

    def __init__(self, window: MainWindow, qtbot, messages):
        self.window = window
        self.canvas = window.canvas
        self.messages = messages
        self._qtbot = qtbot

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def select_tool(self, tool: str) -> None:
        self.canvas.set_tool(tool)
        self.process_events()

    def set_main_color(self, color: QColor) -> None:
        self.canvas.main_color = QColor(color)
        self.canvas.color_mode = "main"

    def _widget_point(self, canvas_point: tuple[float, float]) -> QPoint:
        """キャンバス座標（作業領域座標）をウィジェット座標へ変換する。"""
        x, y = canvas_point
        return self.canvas.canvas_to_widget(QPointF(x, y)).toPoint()

    def stroke(self, points, steps: int = 4) -> None:
        """左ドラッグによるストロークを実イベントとして注入する。

        points はキャンバス座標のリスト。区間ごとに steps 個の中間点を送り、
        実際のマウス移動に近いイベント列にする。
        """
        assert len(points) >= 2, "ストロークには2点以上必要"
        widget_points = [self._widget_point(p) for p in points]
        QTest.mousePress(
            self.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            widget_points[0],
        )
        previous = widget_points[0]
        for target in widget_points[1:]:
            for step in range(1, steps + 1):
                ratio = step / steps
                QTest.mouseMove(
                    self.canvas,
                    QPoint(
                        round(previous.x() + (target.x() - previous.x()) * ratio),
                        round(previous.y() + (target.y() - previous.y()) * ratio),
                    ),
                )
            previous = target
        QTest.mouseRelease(
            self.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            widget_points[-1],
        )
        self.process_events()

    def tablet_stroke(self, points, pressures, steps: int = 4) -> None:
        """筆圧付きのペンストロークを QTabletEvent として注入する。

        QTest はタブレットイベントを合成できないため、ここで直接組み立てる。
        pressures は points と同じ長さで、各点での筆圧（0.0〜1.0）。
        """
        assert len(points) == len(pressures) >= 2
        device = QPointingDevice.primaryPointingDevice()
        widget_points = [self._widget_point(p) for p in points]

        def send(event_type, widget_point, pressure):
            event = QTabletEvent(
                event_type,
                device,
                QPointF(widget_point),
                self.canvas.mapToGlobal(QPointF(widget_point)),
                float(pressure),
                0, 0, 0.0, 0.0, 0.0,
                Qt.KeyboardModifier.NoModifier,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
            )
            self.canvas.tabletEvent(event)

        send(QTabletEvent.Type.TabletPress, widget_points[0], pressures[0])
        for index in range(1, len(widget_points)):
            previous, target = widget_points[index - 1], widget_points[index]
            from_pressure, to_pressure = pressures[index - 1], pressures[index]
            for step in range(1, steps + 1):
                ratio = step / steps
                send(
                    QTabletEvent.Type.TabletMove,
                    QPoint(
                        round(previous.x() + (target.x() - previous.x()) * ratio),
                        round(previous.y() + (target.y() - previous.y()) * ratio),
                    ),
                    from_pressure + (to_pressure - from_pressure) * ratio,
                )
        send(QTabletEvent.Type.TabletRelease, widget_points[-1], pressures[-1])
        self.process_events()

    def trigger_action(self, name: str) -> None:
        """MainWindow が持つ QAction を名前で発火する。"""
        action = getattr(self.window, name, None)
        assert action is not None, f"アクションが見つからない: {name}"
        action.trigger()
        self.process_events()

    def new_document(self) -> None:
        """「新規作成」をサイズ指定ダイアログごと承認して実行する。"""
        from paintmaskanimator.new_document_dialog import NewDocumentDialog

        with self.accept_dialog(NewDocumentDialog):
            self.window.new_doc()
        self.process_events()

    # -- 選択と変形 ---------------------------------------------------
    def select_rect(self, top_left, bottom_right) -> None:
        """矩形選択ツールでドラッグして選択範囲を作る。"""
        self.select_tool("rect_select")
        self.stroke([top_left, bottom_right])

    def start_transform(self, mode: str = "free") -> None:
        self.window.line_ops.start_wire_transform(mode)
        self.process_events()

    def drag_transform(self, start, end) -> None:
        """変形枠の内側をつかんで動かす（移動ドラッグ）。"""
        assert self.canvas.transform_active, "変形が開始されていない"
        self.stroke([start, end])

    def commit_transform(self) -> None:
        self.window.tween.commit_transform_or_tween()
        self.process_events()

    # -- 使用色パネル -------------------------------------------------
    def refresh_used_colors(self) -> None:
        self.window.used_color._refresh_without_delay()
        self.process_events()

    def used_color_rgbs(self) -> set[tuple[int, int, int]]:
        return {
            (color.red(), color.green(), color.blue())
            for color in self.window.palette.colors
        }

    def show_only_colors(self, rgbs) -> None:
        """使用色パネルの表示フィルターを指定色だけに絞る。"""
        self.window.colors.set_visible_colors(set(rgbs))
        self.window.colors._apply_pending_visible_colors()
        self.process_events()

    # -- タイムライン -------------------------------------------------
    def add_frame(self, duplicate: bool = False) -> None:
        self.canvas.add_frame(duplicate)
        self.process_events()

    def go_to_frame(self, index: int) -> None:
        self.canvas.current_frame = index
        self.process_events()

    def export_key_sequence(self, image_format: str, destination: Path) -> None:
        """連番書き出しを、書き出し先フォルダーを固定応答にして実行する。"""
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                QFileDialog, "exec",
                lambda self: QDialog.DialogCode.Accepted.value,
            )
            patch.setattr(
                QFileDialog, "selectedFiles",
                lambda self: [str(destination)],
            )
            self.window.export.key_sequence(image_format)
        self.process_events()

    def save_project_as(self, path: Path) -> bool:
        """「名前を付けて保存」をファイルダイアログごと差し替えて実行する。"""
        with self._answer_save_dialog(path):
            return self.window.project.save_as()

    def open_project(self, path: Path) -> None:
        with self._answer_open_dialog(path):
            self.window.project.open_dialog()
        self.process_events()

    # ------------------------------------------------------------------
    # 観測
    # ------------------------------------------------------------------
    def pixel(self, canvas_point: tuple[float, float], layer_index: int = 0) -> QColor:
        x, y = canvas_point
        layer = self.canvas.layers[layer_index]
        return layer.image.pixelColor(round(x), round(y))

    def canvas_center(self) -> tuple[float, float]:
        from paintmaskanimator.utils import workspace_size

        width, height = workspace_size()
        return (width / 2, height / 2)

    @property
    def frame_count(self) -> int:
        return len(self.canvas.frames)

    def set_exposure(self, frame: int, layer_index: int, exposure: int) -> None:
        """キーの表示コマ数（露出）を変える。足りないコマは補う。"""
        self.canvas._ensure_frame_count(frame + exposure)
        self.canvas.frames[frame].layers[layer_index].exposure = exposure
        self.process_events()

    def enable_tween(self, layer_index: int, key_column: int, mode: str = "free") -> None:
        """タイムラインの行番号ではなく、レイヤー番号で指定できるようにする。"""
        visual_row = len(self.canvas.layers) - 1 - layer_index
        self.window.tween.enable(visual_row, key_column, mode)
        self.process_events()

    def import_image_sequence(self, paths) -> None:
        ok, error = self.canvas.import_image_sequence([str(p) for p in paths])
        assert ok, f"連番読み込みに失敗した: {error}"
        self.process_events()

    def apply_time_remap(self, raw_text: str) -> None:
        """タイムシートのテキストを貼り付けたのと同じ経路で適用する。"""
        parsed = self.window.time_remap.parse_text(raw_text)
        self.window.time_remap.apply_to_active_layer(parsed)
        self.process_events()

    def sequence_numbers(self, layer_index: int) -> list:
        return [
            frame.layers[layer_index].sequence_number
            for frame in self.canvas.frames
        ]

    def undo_depth(self) -> int:
        return len(self.canvas.undo_stack)

    def redo_depth(self) -> int:
        return len(self.canvas.redo_stack)

    def status_message(self) -> str:
        return self.window.statusBar().currentMessage()

    def process_events(self) -> None:
        self._qtbot.wait(0)

    # ------------------------------------------------------------------
    # ダイアログ差し替え
    # ------------------------------------------------------------------
    @contextmanager
    def accept_dialog(self, dialog_cls):
        """このブロックの間だけ、指定ダイアログの exec() を「OK」応答にする。

        E2Eではモーダルダイアログが出た時点でテストが固まるため、開くことを
        想定しているダイアログは必ずこの明示的な許可を通す。
        """
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                dialog_cls, "exec",
                lambda self: QDialog.DialogCode.Accepted.value,
            )
            yield

    @contextmanager
    def _answer_file_dialog(self, method: str, path: Path):
        """静的ファイルダイアログをこのブロックの間だけ固定応答に差し替える。"""
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                QFileDialog, method,
                staticmethod(lambda *a, **k: (str(path), "")),
            )
            yield

    def _answer_save_dialog(self, path: Path):
        return self._answer_file_dialog("getSaveFileName", path)

    def _answer_open_dialog(self, path: Path):
        return self._answer_file_dialog("getOpenFileName", path)


__all__ = ["AppDriver"]
