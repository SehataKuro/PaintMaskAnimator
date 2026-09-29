"""Time remap (After Effects timesheets); owned as ``window.time_remap``.

Parses After Effects time-remap text, rebuilds the per-layer source bank, and
applies the remapped exposure states to the active layer.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from typing import TYPE_CHECKING, Any

import json
import math
import re
from pathlib import Path
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from . import timesheet_file
from .i18n import tr
from .main_window_import import ImportController
from .errors import OPERATION_ERRORS as _OPERATION_ERRORS, OperationError
from .utils import blank_image
from .widgets import TimeRemapPasteDialog
from .logging_setup import get_logger

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)


class TimeRemapController:
    """Owned by ``MainWindow`` as ``window.time_remap``.

    A collaborator rather than a mixin -- see main_window_export.py for why.
    """

    def __init__(self, window: "MainWindow"):
        self.window = window

    @staticmethod
    def _parse_toei_timesheet(raw_text):
        text_value = str(raw_text or "").strip()
        json_start = text_value.find("{")
        if json_start < 0:
            raise OperationError(tr("JSONデータが見つかりません。"))
        try:
            payload = json.loads(text_value[json_start:])
        except json.JSONDecodeError as exc:
            raise OperationError(
                tr("ToeiDigitalTimeSheetのJSONを解析できません。\n{exc}").format(exc=exc)
            ) from exc

        layers = payload.get("layers")
        if not isinstance(layers, list) or not layers:
            raise OperationError(tr("layersデータが見つかりません。"))

        selected_layer = None
        for layer in layers:
            frames = (
                layer.get("frames")
                if isinstance(layer, dict) else None
            )
            if isinstance(frames, list) and frames:
                selected_layer = layer
                break
        if selected_layer is None:
            raise OperationError(tr("framesデータが見つかりません。"))

        parsed_entries = {}
        for entry in selected_layer.get("frames", []):
            if not isinstance(entry, dict):
                continue
            try:
                frame = int(entry.get("frame"))  # pyright: ignore[reportArgumentType]  # None/str caught below
            except (TypeError, ValueError):
                continue
            if frame < 0:
                continue

            values = []
            data_items = entry.get("data", [])
            if isinstance(data_items, list):
                for data_item in data_items:
                    if not isinstance(data_item, dict):
                        continue
                    item_values = data_item.get("values", [])
                    if isinstance(item_values, list):
                        values.extend(item_values)
                    elif item_values not in (None, ""):
                        values.append(item_values)

            token = None
            for value in values:
                candidate = str(value).strip()
                if candidate:
                    token = candidate
                    break
            parsed_entries[frame] = token

        if not parsed_entries:
            raise OperationError(
                tr("有効なToeiDigitalTimeSheetフレームがありません。")
            )

        start_frame = min(parsed_entries)
        end_frame = max(parsed_entries)
        current_state = None
        states = []
        blank_label_count = 0

        for frame in range(start_frame, end_frame + 1):
            if frame in parsed_entries:
                token = parsed_entries[frame]
                if token is None:
                    # 値なしセルは直前セルの状態を保持。
                    pass
                elif re.fullmatch(r"[0-9]+", token):
                    current_state = max(1, int(token))
                else:
                    # 中割トラックラベル／記号セルは空フレーム。
                    current_state = None
                    blank_label_count += 1
            states.append(current_state)

        return {
            "format": "ToeiDigitalTimeSheet",
            "fps": None,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "states": states,
            "blank_label_count": blank_label_count,
        }

    @staticmethod
    def _parse_after_effects_time_remap(raw_text):
        text_value = str(raw_text or "").replace("\r", "")
        fps_match = re.search(
            r"Units\s+Per\s+Second\s+([0-9]+(?:\.[0-9]+)?)",
            text_value,
            re.IGNORECASE,
        )
        fps = float(fps_match.group(1)) if fps_match else 24.0
        if fps <= 0.0:
            fps = 24.0

        time_entries = []
        opacity_entries = []
        section = None
        number_pattern = re.compile(
            r"^\s*(-?\d+(?:\.\d+)?)\s+"
            r"(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
        )

        for line in text_value.splitlines():
            lowered = line.strip().lower()
            if lowered.startswith("time remap"):
                section = "time"
                continue
            if (
                lowered.startswith("transform")
                and "opacity" in lowered
            ):
                section = "opacity"
                continue
            if lowered.startswith("end of keyframe data"):
                section = None
                continue

            match = number_pattern.match(line)
            if match is None or section is None:
                continue
            frame = int(round(float(match.group(1))))
            value = float(match.group(2))
            if frame < 0:
                continue
            if section == "time":
                time_entries.append((frame, value))
            else:
                opacity_entries.append((frame, value))

        if not time_entries:
            raise OperationError(
                tr("Time RemapのFrame／secondsデータが見つかりません。")
            )

        # 同じフレームが複数ある場合は、後から書かれた値を優先。
        time_map = {}
        for frame, value in time_entries:
            time_map[int(frame)] = float(value)
        opacity_map = {}
        for frame, value in opacity_entries:
            opacity_map[int(frame)] = float(value)
        time_entries = sorted(time_map.items())
        opacity_entries = sorted(opacity_map.items())

        all_frames = [frame for frame, _value in time_entries]
        all_frames.extend(
            frame for frame, _value in opacity_entries
        )
        start_frame = max(0, min(all_frames))
        end_frame = max(all_frames)

        time_index = 0
        current_seconds = float(time_entries[0][1])
        opacity_index = 0
        current_opacity = (
            float(opacity_entries[0][1])
            if opacity_entries else 100.0
        )
        states = []

        for frame in range(start_frame, end_frame + 1):
            while (
                time_index + 1 < len(time_entries)
                and time_entries[time_index + 1][0] <= frame
            ):
                time_index += 1
                current_seconds = float(
                    time_entries[time_index][1]
                )
            while (
                opacity_entries
                and opacity_index + 1 < len(opacity_entries)
                and opacity_entries[opacity_index + 1][0] <= frame
            ):
                opacity_index += 1
                current_opacity = float(
                    opacity_entries[opacity_index][1]
                )

            if current_seconds < 0.0 or current_opacity <= 0.0:
                states.append(None)
            else:
                # AEの0秒は連番1番、1/fps秒は連番2番。
                source_index = int(
                    math.floor(current_seconds * fps + 0.5)
                ) + 1
                states.append(max(1, source_index))

        return {
            "format": "Adobe After Effects",
            "fps": fps,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "states": states,
            "blank_label_count": 0,
        }

    @classmethod
    def parse_text(cls, raw_text):
        text_value = (
            str(raw_text or "")
            .lstrip("\ufeff")
            .strip()
        )
        if not text_value:
            raise OperationError(tr("貼り付けデータが空です。"))

        lowered = text_value.lower()
        if text_value.startswith((timesheet_file.XDTS_SIGNATURE, timesheet_file.TDTS_SIGNATURE)):
            return ImportController._parse_xdts_timesheet(text_value)
        if (
            "toeidigitaltimesheet copy data" in lowered
            or (
                text_value.startswith("{")
                and '"layers"' in text_value
                and '"frames"' in text_value
            )
        ):
            return cls._parse_toei_timesheet(text_value)

        if (
            "adobe after effects" in lowered
            or "time remap" in lowered
        ):
            return cls._parse_after_effects_time_remap(text_value)

        raise OperationError(
            tr("Adobe After Effects、ToeiDigitalTimeSheet、XDTS形式を"
            "判別できませんでした。")
        )

    def _time_remap_source_bank(self, layer_index):
        stored_bank = getattr(
            self.window.canvas,
            "_sequence_source_bank",
            [],
        )
        stored_index = int(
            getattr(
                self.window.canvas,
                "_sequence_source_bank_layer_index",
                -1,
            )
        )
        stored_name = str(
            getattr(
                self.window.canvas,
                "_sequence_source_bank_layer_name",
                "",
            )
        )

        active_name = ""
        if (
            self.window.canvas.frames
            and 0 <= self.window.canvas.current_frame
            < len(self.window.canvas.frames)
            and 0 <= layer_index
            < len(
                self.window.canvas.frames[
                    self.window.canvas.current_frame
                ].layers
            )
        ):
            active_name = str(
                self.window.canvas.frames[
                    self.window.canvas.current_frame
                ].layers[layer_index].name
            )

        if stored_bank and (
            stored_index == layer_index
            or (
                stored_name
                and stored_name == active_name
            )
        ):
            return [image.copy() for image in stored_bank]

        numbered_bank = {}
        for frame in self.window.canvas.frames:
            if layer_index >= len(frame.layers):
                continue
            layer = frame.layers[layer_index]
            number = layer.sequence_number
            if (
                layer.has_content
                and number is not None
                and int(number) >= 1
                and int(number) not in numbered_bank
            ):
                numbered_bank[int(number)] = layer.image.copy()
        if numbered_bank and set(numbered_bank) == set(
            range(1, max(numbered_bank) + 1)
        ):
            return [
                numbered_bank[number]
                for number in range(1, max(numbered_bank) + 1)
            ]

        # 番号情報のない旧プロジェクトでは、選択レイヤー内の
        # 内容キーを左から連番ソースとして採用する。
        bank = []
        for frame in self.window.canvas.frames:
            if layer_index >= len(frame.layers):
                continue
            layer = frame.layers[layer_index]
            if layer.has_content and not layer.image.isNull():
                bank.append(layer.image.copy())
        return bank

    def _apply_time_remap_states_to_layer(
        self,
        states,
        start_frame,
        end_frame,
        layer_index,
        source_bank,
        current_frame,
    ):
        """検証済みのセル番号列を1レイヤーのシートへ展開する。"""
        self.window.canvas._ensure_frame_count(end_frame + 1)
        template = self.window.canvas.frames[
            current_frame
        ].layers[layer_index].clone()

        # 対象範囲より前のキーの露出が入り込まないよう切る。
        for prior in range(start_frame - 1, -1, -1):
            prior_layer = self.window.canvas.frames[prior].layers[layer_index]
            exposure = max(1, int(prior_layer.exposure))
            if prior + exposure > start_frame:
                prior_layer.exposure = max(1, start_frame - prior)
                break
            if prior_layer.has_content:
                break

        # 対象範囲をいったん明示空セルに戻す。
        for frame_index in range(start_frame, end_frame + 1):
            layer = self.window.canvas.frames[frame_index].layers[layer_index]
            self.window.layers._copy_display_properties(template, layer)
            layer.image = blank_image()
            layer.has_content = False
            layer.is_blank_key = False
            layer.sequence_number = None
            layer.cell_name = None
            layer.exposure = 1

        # 同じ絵番号／空フレームが連続する区間を露出へ圧縮。
        run_start = start_frame
        run_state: Any = states[0]
        sentinel = object()
        for offset in range(1, len(states) + 1):
            next_state = states[offset] if offset < len(states) else sentinel
            if offset < len(states) and next_state == run_state:
                continue
            run_end = start_frame + offset - 1
            target = self.window.canvas.frames[run_start].layers[layer_index]
            self.window.layers._copy_display_properties(template, target)
            target.exposure = max(1, run_end - run_start + 1)
            if run_state is None:
                target.image = blank_image()
                target.has_content = False
                target.is_blank_key = True
                target.sequence_number = None
                target.cell_name = None
            else:
                target.image = source_bank[int(run_state) - 1].copy()
                target.has_content = True
                target.is_blank_key = False
                target.sequence_number = int(run_state)
                target.cell_name = None
            if offset < len(states):
                run_start = start_frame + offset
                run_state = next_state

    def apply_to_active_layer(self, parsed):
        states = list(parsed.get("states", []))
        start_frame = int(parsed.get("start_frame", 0))
        end_frame = int(parsed.get("end_frame", -1))
        if (
            not states
            or start_frame < 0
            or end_frame < start_frame
            or len(states) != end_frame - start_frame + 1
        ):
            raise OperationError(tr("解析したフレーム範囲が不正です。"))

        if not self.window.canvas.frames:
            raise OperationError(tr("タイムラインがありません。"))
        layer_index = int(self.window.canvas.active_layer_index)
        current_frame = max(
            0,
            min(
                int(self.window.canvas.current_frame),
                len(self.window.canvas.frames) - 1,
            ),
        )
        if not (
            0 <= layer_index
            < len(self.window.canvas.frames[current_frame].layers)
        ):
            raise OperationError(tr("対象レイヤーを選択してください。"))

        source_bank = self._time_remap_source_bank(layer_index)
        if not source_bank:
            raise OperationError(
                tr("選択レイヤーに連番画像がありません。\n"
                "先に画像連番を読み込んでください。")
            )

        referenced = sorted({
            int(state)
            for state in states
            if state is not None
        })
        missing = [
            index
            for index in referenced
            if index < 1 or index > len(source_bank)
        ]
        if missing:
            preview = ", ".join(
                str(value) for value in missing[:12]
            )
            if len(missing) > 12:
                preview += "…"
            raise OperationError(
                tr("連番画像は{len}枚ですが、存在しない絵番号が参照されています。\n{preview}").format(len=len(source_bank), preview=preview)
            )

        format_name = str(
            parsed.get("format", tr("タイムリマップ"))
        )
        blank_count = sum(
            1 for state in states if state is None
        )
        label_blanks = int(
            parsed.get("blank_label_count", 0)
        )
        message = (
            tr("形式：{name}\n反映範囲：{value}～{value2}フレーム\n連番画像：{len}枚\n空フレーム：{count}フレーム").format(name=format_name, value=start_frame + 1, value2=end_frame + 1, len=len(source_bank), count=blank_count)
        )
        if label_blanks:
            message += (
                tr("\n中割・記号ラベル：{blanks}セル").format(blanks=label_blanks)
            )
        message += (
            "\n\n選択レイヤーの対象範囲を置き換えます。"
        )

        answer = QMessageBox.question(
            self.window,
            tr("タイムリマップを反映"),
            message,
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False

        self.window.timeline_ops.set_mode("sheet")
        self.window.canvas.push_doc_undo()
        self._apply_time_remap_states_to_layer(
            states,
            start_frame,
            end_frame,
            layer_index,
            source_bank,
            current_frame,
        )

        self.window.canvas.current_frame = start_frame
        self.window.canvas.active_layer_index = layer_index
        self.window.canvas._onion_cache.clear()
        self.window.canvas._color_filter_cache.clear()
        self.window.canvas._color_index_cache.clear()
        self.window.canvas._silhouette_cache.clear()
        self.window._used_color_cache.clear()
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()
        self.window.used_color.schedule_refresh()

        self.window.statusBar().showMessage(
            tr("{name}を{value}～{value2}フレームへ反映しました。").format(name=format_name, value=start_frame + 1, value2=end_frame + 1),
            4200,
        )
        return True

    def show_paste_dialog(self, file_path=None):
        clipboard_text = QApplication.clipboard().text()
        if file_path:
            try:
                clipboard_text = Path(file_path).read_text(
                    encoding="utf-8-sig"
                )
            except (OSError, UnicodeError) as exc:
                QMessageBox.warning(
                    self.window,
                    tr("XDTS読み込み"),
                    tr("読み込めませんでした。\n\n{exc}").format(exc=exc),
                )
                return False
        dialog = TimeRemapPasteDialog(
            clipboard_text,
            self.window,
            parse_text=self.parse_text,
            canvas=self.window.canvas,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        try:
            parsed = dialog.parsed_result()
            if parsed is None:
                raise OperationError(tr("使用するタイムシート行がありません。"))
            if (
                str(parsed.get("format", "")).startswith("XDTS")
                and parsed.get("sheet_columns")
            ):
                return self.window.importer.apply_xdts_layer_bindings(parsed)
            return self.apply_to_active_layer(parsed)
        except _OPERATION_ERRORS as exc:
            log.warning("time-remap paste failed: %s", exc, exc_info=True)
            QMessageBox.warning(
                self.window,
                tr("タイムリマップ貼り付け"),
                tr("タイムラインへ反映できませんでした。\n\n{exc}").format(exc=exc),
            )
            return False

    def open_dropped(self, path):
        remap_path = Path(path)
        if remap_path.suffix.lower() not in (".xdts", ".xtds"):
            return False
        return bool(self.show_paste_dialog(str(remap_path)))
