"""PSD / XDTS / CLIP import; owned by ``MainWindow`` as ``window.importer``.

Drives the file dialogs and the parsing that brings external PSD layers, XDTS
timesheets and CLIP STUDIO animations into the current project.

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from typing import TYPE_CHECKING, Any, cast

import json
from pathlib import Path
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from .i18n import tr
from .optional_deps import PILImage, PSDImage
from .constants import (
    MAX_IMAGE_DIMENSION,
    MAX_PROJECT_LAYERS,
    MAX_PROJECT_LAYER_CELLS,
    MAX_SINGLE_IMAGE_PIXELS,
)
import sqlite3
from . import constants
from .canvas import PaintCanvas
from .clip_animation import (
    ClipImportError,
    cell_sequence_numbers,
    read_clip_animation,
)
from .errors import OPERATION_ERRORS as _OPERATION_ERRORS, OperationError
from .models import Layer
from .utils import blank_image
from .logging_setup import get_logger

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)


class ImportController:
    """Owned by ``MainWindow`` as ``window.import_``.

    A collaborator rather than a mixin -- see main_window_export.py for why.
    """

    def __init__(self, window: "MainWindow"):
        self.window = window

    def clip_animation_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self.window,
            tr("CLIP STUDIOアニメーションを読み込む"),
            "",
            tr("CLIP STUDIO PAINT (*.clip);;すべてのファイル (*)"),
        )
        if path:
            return self.clip_animation(path)
        return False

    def _clip_import_needs_confirmation(self):
        if self.window.current_project_path:
            return True
        if len(self.window.canvas.frames) != 1:
            return True
        layers = self.window.canvas.frames[0].layers if self.window.canvas.frames else []
        return (
            len(layers) != 1
            or any(layer.has_content or layer.is_blank_key for layer in layers)
        )

    def _confirm_replace_for_clip_import(self):
        if not self._clip_import_needs_confirmation():
            return True
        dialog = QMessageBox(self.window)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle(tr("CLIP STUDIOアニメーションを読み込む"))
        dialog.setText(
            tr("現在のキャンバスをCLIP STUDIOアニメーションで置き換えます。\n"
            "先に現在のプロジェクトを保存しますか？")
        )
        save_button = dialog.addButton(
            tr("保存する"), QMessageBox.ButtonRole.AcceptRole
        )
        discard_button = dialog.addButton(
            tr("保存せず読み込む"), QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = dialog.addButton(
            tr("キャンセル"), QMessageBox.ButtonRole.RejectRole
        )
        dialog.setDefaultButton(save_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is cancel_button or clicked is None:
            return False
        if clicked is save_button:
            return bool(self.window.project.save())
        return clicked is discard_button

    def clip_animation(self, path, confirm_replace=True):
        source = Path(path)
        if source.suffix.lower() != ".clip":
            return False
        self.window.statusBar().showMessage(
            tr("CLIP STUDIOアニメーションを解析しています：{name}").format(name=source.name),
            0,
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            parsed = read_clip_animation(source)
        except (ClipImportError, OSError, ValueError, sqlite3.Error) as exc:
            log.error("CLIP STUDIO animation import failed: %s", exc, exc_info=True)
            self.window.statusBar().clearMessage()
            QMessageBox.critical(
                self.window,
                tr("CLIP STUDIOアニメーション読み込み"),
                tr("読み込めませんでした。現在のドキュメントは変更されていません。\n\n{exc}").format(exc=exc),
            )
            return False
        finally:
            QApplication.restoreOverrideCursor()
        if confirm_replace and not self._confirm_replace_for_clip_import():
            self.window.statusBar().clearMessage()
            return False

        old_state = {
            "width": constants.CANVAS_WIDTH,
            "height": constants.CANVAS_HEIGHT,
            "frames": self.window.canvas.frames,
            "current_frame": self.window.canvas.current_frame,
            "active_layer_index": self.window.canvas.active_layer_index,
            "timeline_mode": self.window.canvas.timeline_mode,
            "fps": self.window.timeline.fps.value(),
            "project_path": self.window.current_project_path,
            "clip_metadata": getattr(
                self.window.canvas, "clip_studio_source_metadata", None
            ),
            "palette": self.window.palette.serialize_categories(),
            "undo": list(self.window.canvas.undo_stack),
            "redo": list(self.window.canvas.redo_stack),
            "branches": list(self.window.canvas.history_branches),
            "sequence_archive": self.window.canvas._sequence_archive,
            "sequence_bank": self.window.canvas._sequence_source_bank,
            "sequence_bank_index": self.window.canvas._sequence_source_bank_layer_index,
            "sequence_bank_name": self.window.canvas._sequence_source_bank_layer_name,
        }
        try:
            if self.window.canvas._playback_active:
                self.window.timeline.play.setChecked(False)
                self.window.play(False)
            self.window.tween.cancel_transform_or_tween()
            self.window.canvas.clear_selection()
            constants.CANVAS_WIDTH = int(parsed.width)
            constants.CANVAS_HEIGHT = int(parsed.height)
            self.window.canvas.frames = list(parsed.frames)
            self.window.canvas.current_frame = 0
            self.window.canvas.active_layer_index = 0
            self.window.canvas.timeline_mode = "sheet"
            self.window.timeline.set_timeline_mode("sheet")
            self.window.timeline.fps.setValue(
                max(1, min(60, int(round(parsed.fps))))
            )
            self.window.canvas.clip_studio_source_metadata = parsed.source_metadata()
            self.window.canvas._sequence_source_bank = []
            self.window.canvas._sequence_source_bank_layer_index = -1
            self.window.canvas._sequence_source_bank_layer_name = ""
            sequence_archive = {}
            for layer_index, source_layer in enumerate(parsed.layers):
                numbers = cell_sequence_numbers(source_layer)
                placed_numbers = {
                    int(frame.layers[layer_index].sequence_number)
                    for frame in self.window.canvas.frames
                    if (
                        frame.layers[layer_index].has_content
                        and frame.layers[layer_index].sequence_number is not None
                    )
                }
                for tag, image in source_layer.cell_images.items():
                    number = numbers[tag]
                    if number in placed_numbers:
                        continue
                    stripped_tag = str(tag).strip()
                    normalized_number = stripped_tag.lstrip("0") or "0"
                    sequence_archive[(layer_index, number)] = Layer(
                        source_layer.name,
                        QImage(image),
                        has_content=True,
                        exposure=1,
                        sequence_number=number,
                        cell_name=(
                            None
                            if stripped_tag.isdecimal()
                            and normalized_number == str(number)
                            else str(tag)
                        ),
                    )
            self.window.canvas._sequence_archive = sequence_archive
            self.window.timeline.sequence_archive = self.window.canvas._sequence_archive
            self.window.canvas.undo_stack.clear()
            self.window.canvas.redo_stack.clear()
            self.window.canvas.clear_history_branches()
            self.window.canvas._onion_cache.clear()
            self.window.canvas._color_filter_cache.clear()
            self.window.canvas._color_index_cache.clear()
            self.window.canvas._silhouette_cache.clear()
            self.window._used_color_cache.clear()
            self.window.palette.clear_categories()
            self.window.current_project_path = None
            self.window.project.update_title()
            self.window.refresh_ui()
            self.window.used_color.schedule_refresh()
            QTimer.singleShot(0, self.window.fit_canvas)
        except _OPERATION_ERRORS as exc:
            constants.CANVAS_WIDTH = old_state["width"]
            constants.CANVAS_HEIGHT = old_state["height"]
            self.window.canvas.frames = old_state["frames"]
            self.window.canvas.current_frame = old_state["current_frame"]
            self.window.canvas.active_layer_index = old_state["active_layer_index"]
            self.window.canvas.timeline_mode = old_state["timeline_mode"]
            self.window.timeline.set_timeline_mode(old_state["timeline_mode"])
            self.window.timeline.fps.setValue(old_state["fps"])
            self.window.current_project_path = old_state["project_path"]
            self.window.canvas.clip_studio_source_metadata = old_state["clip_metadata"]
            self.window.palette.restore_categories(old_state["palette"])
            self.window.canvas.undo_stack[:] = old_state["undo"]
            self.window.canvas.redo_stack[:] = old_state["redo"]
            self.window.canvas.history_branches[:] = old_state["branches"]
            self.window.canvas._sequence_archive = old_state["sequence_archive"]
            self.window.timeline.sequence_archive = self.window.canvas._sequence_archive
            self.window.canvas._sequence_source_bank = old_state["sequence_bank"]
            self.window.canvas._sequence_source_bank_layer_index = old_state[
                "sequence_bank_index"
            ]
            self.window.canvas._sequence_source_bank_layer_name = old_state[
                "sequence_bank_name"
            ]
            self.window.project.update_title()
            self.window.refresh_ui()
            log.error("CLIP import apply failed and rolled back: %s", exc, exc_info=True)
            QMessageBox.critical(
                self.window,
                tr("CLIP STUDIOアニメーション読み込み"),
                tr("読み込み結果を反映できませんでした。現在のドキュメントは元の状態へ戻しました。\n\n{exc}").format(exc=exc),
            )
            return False

        fps_note = ""
        if abs(float(parsed.fps) - self.window.timeline.fps.value()) > 1e-6:
            fps_note = (
                tr("（元FPS {fps:g}、PMA表示 {value} fps）").format(fps=parsed.fps, value=self.window.timeline.fps.value())
            )
        archive_note = ""
        if self.window.canvas._sequence_archive:
            archive_note = (
                tr(" 未配置セル{len}枚は連番に保持しました。").format(len=len(self.window.canvas._sequence_archive))
            )
        self.window.statusBar().showMessage(
            tr("CLIP STUDIOから{count}フォルダー・{count2}フレームを読み込みました。{note}{note2}").format(count=parsed.folder_count, count2=parsed.frame_count, note=archive_note, note2=fps_note),
            6000,
        )
        return True

    @staticmethod
    def _parse_xdts_timesheet(raw_text):
        text_value = str(raw_text or "").lstrip("\ufeff")
        lines = text_value.splitlines()
        if not lines or lines[0].strip() != "exchangeDigitalTimeSheet Save Data":
            raise OperationError(tr("XDTSの先頭識別文字列が一致しません。"))
        try:
            payload = json.loads("\n".join(lines[1:]))
        except json.JSONDecodeError as exc:
            raise OperationError(tr("XDTSのJSONを解析できません。\n{exc}").format(exc=exc)) from exc
        if int(payload.get("version", -1)) != 5:
            raise OperationError(tr("対応しているXDTSバージョンは5です。"))
        time_tables = payload.get("timeTables") or []
        if not time_tables:
            raise OperationError(tr("XDTSにタイムシート情報がありません。"))
        time_table = time_tables[0]
        duration = max(1, int(time_table.get("duration", 1)))
        headers = {}
        for item in time_table.get("timeTableHeaders", []):
            if not isinstance(item, dict):
                continue
            field_id = int(item.get("fieldId", -1))
            headers.setdefault(field_id, list(item.get("names", [])))

        def column_group(field_id, name):
            if field_id == 3:
                return "ACTION"
            if field_id == 5:
                return "CAM"
            normalized = str(name).strip().casefold()
            action_words = ("memo", "action", "act", "camera", "cam", "pan")
            if (
                normalized.startswith(("_", "◆", "-"))
                or normalized in ("ts", "タイムシート")
                or any(word in normalized for word in action_words)
            ):
                return "ACTION"
            return "CELL"

        symbol_labels = {
            "SYMBOL_HYPHEN": "｜",
            "SYMBOL_NULL_CELL": "×",
            "SYMBOL_TICK_1": "○",
            "SYMBOL_TICK_2": "●",
        }
        sheet_columns = []
        parsed_tracks = []
        supported_fields = {0, 3, 5}
        for field_index, field in enumerate(time_table.get("fields", [])):
            if not isinstance(field, dict):
                continue
            field_id = int(field.get("fieldId", -1))
            if field_id not in supported_fields:
                continue
            names = headers.get(field_id, [])
            action_boundary = next(
                (
                    index for index, header_name in enumerate(names[:-1])
                    if "memo" in str(header_name).strip().casefold()
                    or "メモ" in str(header_name).strip()
                ),
                None,
            ) if field_id == 0 else None
            for track in sorted(
                field.get("tracks", []),
                key=lambda item: int(item.get("trackNo", 0)),
            ):
                if not isinstance(track, dict):
                    continue
                track_no = int(track.get("trackNo", 0))
                default_names = {
                    0: "セル欄",
                    3: "アクション",
                    5: "カメラ",
                }
                name = (
                    str(names[track_no]).strip()
                    if 0 <= track_no < len(names)
                    and str(names[track_no]).strip()
                    else f"{default_names[field_id]} {track_no + 1}"
                )
                entries = {
                    int(item.get("frame", 0)): item
                    for item in track.get("frames", [])
                    if isinstance(item, dict)
                }
                states = []
                display_values = []
                current_state = None
                blank_label_count = 0
                for frame in range(duration):
                    item = entries.get(frame)
                    values = []
                    if item is not None:
                        instruction = next(
                            (
                                data for data in item.get("data", [])
                                if isinstance(data, dict)
                                and int(data.get("id", -1)) == 0
                            ),
                            None,
                        )
                        if instruction is not None:
                            raw_values = instruction.get("values", [])
                            values = (
                                list(raw_values)
                                if isinstance(raw_values, list)
                                else [raw_values]
                            )
                    tokens = [str(value).strip() for value in values]
                    token = tokens[0] if tokens else None
                    if token in symbol_labels:
                        display = symbol_labels[token]
                    elif field_id == 3 and tokens:
                        dialogue = [value for value in tokens[:2] if value]
                        display = "：".join(dialogue)
                    elif tokens:
                        display = " / ".join(value for value in tokens if value)
                    else:
                        display = ""

                    if field_id == 0:
                        if token in (None, "SYMBOL_HYPHEN"):
                            if current_state is not None:
                                display = "｜"
                        elif token == "SYMBOL_NULL_CELL":
                            current_state = None
                        elif token in ("SYMBOL_TICK_1", "SYMBOL_TICK_2"):
                            current_state = None
                            blank_label_count += 1
                        elif token.isdecimal():
                            current_state = max(1, int(token))
                            display = str(current_state)
                        else:
                            current_state = None
                            blank_label_count += 1
                        states.append(current_state)
                    display_values.append(display)

                group = column_group(field_id, name)
                if (
                    field_id == 0
                    and action_boundary is not None
                    and track_no <= action_boundary
                ):
                    group = "ACTION"
                column = {
                    "uid": f"{field_id}:{field_index}:{track_no}",
                    "field_id": field_id,
                    "track_no": track_no,
                    "name": name,
                    "group": group,
                    "start_frame": 0,
                    "end_frame": duration - 1,
                    "states": states,
                    "display_values": display_values,
                    "blank_label_count": blank_label_count,
                    "bindable": field_id == 0 and group == "CELL",
                }
                sheet_columns.append(column)
                if field_id == 0:
                    parsed_tracks.append(dict(column))

        if not parsed_tracks:
            raise OperationError(tr("XDTSにセル欄（fieldId 0）がありません。"))
        group_order = {"ACTION": 0, "CELL": 1, "CAM": 2}
        sheet_columns.sort(
            key=lambda item: (
                group_order.get(str(item.get("group", "CELL")), 9),
                int(item.get("field_id", 0)),
                int(item.get("track_no", 0)),
            )
        )
        primary = parsed_tracks[0]
        return {
            "format": "XDTS version 5",
            "fps": None,
            "start_frame": 0,
            "end_frame": duration - 1,
            "states": list(primary["states"]),
            "blank_label_count": int(primary["blank_label_count"]),
            "tracks": parsed_tracks,
            "sheet_columns": sheet_columns,
        }

    def apply_xdts_layer_bindings(self, parsed):
        columns = {
            str(column.get("uid", "")): column
            for column in parsed.get("sheet_columns", [])
            if isinstance(column, dict)
        }
        bindings = dict(parsed.get("layer_bindings", {}))
        start_frame = int(parsed.get("start_frame", 0))
        end_frame = int(parsed.get("end_frame", -1))
        duration = end_frame - start_frame + 1
        if duration <= 0 or not bindings:
            raise OperationError(tr("読み込むCELLとレイヤーの紐づけがありません。"))
        if not self.window.canvas.frames:
            raise OperationError(tr("タイムラインがありません。"))
        current_frame = max(
            0,
            min(int(self.window.canvas.current_frame), len(self.window.canvas.frames) - 1),
        )
        current_layers = self.window.canvas.frames[current_frame].layers
        prepared = []
        for uid, layer_index_value in bindings.items():
            column = columns.get(str(uid))
            if column is None or not bool(column.get("bindable", False)):
                continue
            states = list(column.get("states", []))
            if len(states) != duration:
                raise OperationError(
                    tr("CELL「{get}」のフレーム数が不正です。").format(get=column.get('name', ''))
                )
            layer_index = int(layer_index_value)
            if not (0 <= layer_index < len(current_layers)):
                raise OperationError(
                    tr("CELL「{get}」の紐づけ先レイヤーがありません。").format(get=column.get('name', ''))
                )
            source_bank = self.window.time_remap._time_remap_source_bank(layer_index)
            if not source_bank:
                raise OperationError(
                    tr("レイヤー「{name}」に連番画像がありません。").format(name=current_layers[layer_index].name)
                )
            referenced = sorted({
                int(state) for state in states if state is not None
            })
            missing = [
                number for number in referenced
                if number < 1 or number > len(source_bank)
            ]
            if missing:
                preview = ", ".join(str(value) for value in missing[:12])
                if len(missing) > 12:
                    preview += "…"
                raise OperationError(
                    tr("CELL「{get}」は存在しない絵番号を参照しています（レイヤー画像 {len}枚）。\n{preview}").format(get=column.get('name', ''), len=len(source_bank), preview=preview)
                )
            prepared.append((
                column,
                layer_index,
                states,
                source_bank,
            ))
        if not prepared:
            raise OperationError(tr("読み込めるCELLの紐づけがありません。"))

        links = "\n".join(
            tr("・{get} → {name}").format(get=column.get('name', 'CELL'), name=current_layers[layer_index].name)
            for column, layer_index, _states, _bank in prepared
        )
        answer = QMessageBox.question(
            self.window,
            tr("XDTSタイムシートを反映"),
            tr("反映範囲：{value}～{value2}フレーム\n紐づけ：\n{links}\n\n紐づけたレイヤーの対象範囲を置き換えます。").format(value=start_frame + 1, value2=end_frame + 1, links=links),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False

        self.window.timeline_ops.set_mode("sheet")
        self.window.canvas.push_doc_undo()
        for _column, layer_index, states, source_bank in prepared:
            self.window.time_remap._apply_time_remap_states_to_layer(
                states,
                start_frame,
                end_frame,
                layer_index,
                source_bank,
                current_frame,
            )
        self.window.canvas.current_frame = start_frame
        self.window.canvas.active_layer_index = prepared[0][1]
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
            tr("XDTSの{len}個のCELLを{value}～{value2}フレームへ反映しました。").format(len=len(prepared), value=start_frame + 1, value2=end_frame + 1),
            4200,
        )
        return True

    def psd_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self.window, tr("PSDを読み込む"), "", "Photoshop Document (*.psd)"
        )
        if not path:
            return
        if PSDImage is None or PILImage is None:
            QMessageBox.warning(
                self.window,
                tr("PSD読み込み"),
                tr("PSDの読み込みには psd-tools と Pillow が必要です。\n"
                "requirements.txtをインストールしてください。"),
            )
            return
        try:
            psd = PSDImage.open(path)
            psd_width = int(psd.width)
            psd_height = int(psd.height)
            if (
                psd_width < 1
                or psd_height < 1
                or psd_width > MAX_IMAGE_DIMENSION
                or psd_height > MAX_IMAGE_DIMENSION
                or psd_width * psd_height > MAX_SINGLE_IMAGE_PIXELS
            ):
                raise OperationError(
                    tr("PSDの画像サイズが上限を超えています。 ({width} × {height}px)").format(width=psd_width, height=psd_height)
                )
            if len(psd) > MAX_PROJECT_LAYERS:
                raise OperationError(tr("PSDの最上位レイヤー数が上限を超えています。"))
            imported = []
            skipped = 0
            imported_cell_count = 0
            viewport = (0, 0, psd_width, psd_height)
            for top_layer in psd:
                frame_layers = (
                    list(cast(Any, top_layer))
                    if top_layer.is_group()
                    else [top_layer]
                )
                imported_cell_count += len(frame_layers)
                if imported_cell_count > MAX_PROJECT_LAYER_CELLS:
                    raise OperationError(tr("PSDのレイヤー項目数が上限を超えています。"))
                key_images = []
                for psd_layer in frame_layers:
                    try:
                        rendered = psd_layer.composite(
                            viewport=viewport,
                            force=True,
                        )
                    except (ValueError, KeyError, IndexError, TypeError, OSError, RuntimeError, AttributeError) as exc:
                        log.debug("PSD layer composite failed, skipping: %s", exc)
                        rendered = None
                    if rendered is None:
                        skipped += 1
                        continue
                    key_images.append(
                        PaintCanvas._pil_rgba_to_qimage(rendered)
                    )
                if key_images:
                    imported.append((
                        str(top_layer.name or "Layer"),
                        bool(top_layer.is_visible()),
                        max(0.0, min(1.0, float(top_layer.opacity) / 255.0)),
                        key_images,
                    ))
                else:
                    skipped += 1
            if not imported:
                raise OperationError(tr("読み込める画像レイヤーがありません。"))
        except _OPERATION_ERRORS as exc:
            log.error("PSD import failed: %s", exc, exc_info=True)
            QMessageBox.critical(
                self.window, tr("PSD読み込み"), tr("PSDを読み込めませんでした。\n\n{exc}").format(exc=exc)
            )
            return

        if int(psd.width) > constants.CANVAS_WIDTH or int(psd.height) > constants.CANVAS_HEIGHT:
            self.window.canvas.push_doc_undo()
            self.window.replace_doc(
                max(constants.CANVAS_WIDTH, int(psd.width)),
                max(constants.CANVAS_HEIGHT, int(psd.height)),
                preserve=True,
            )
        self.window.canvas.push_doc_undo()
        start_frame = int(self.window.canvas.current_frame)
        maximum_keys = max(len(images) for _name, _visible, _opacity, images in imported)
        self.window.canvas._ensure_frame_count(start_frame + maximum_keys)
        first_new_layer = len(self.window.canvas.frames[0].layers)
        for name, visible, opacity, key_images in imported:
            layer_index = len(self.window.canvas.frames[0].layers)
            for frame in self.window.canvas.frames:
                frame.layers.append(Layer(
                    name,
                    blank_image(),
                    visible=visible,
                    opacity=opacity,
                ))
            for offset, image in enumerate(key_images):
                target = self.window.canvas.frames[start_frame + offset].layers[layer_index]
                self.window.canvas._place_imported_image(image, target.image)
                target.has_content = True
                target.exposure = 1
                target.sequence_number = offset + 1
        self.window.canvas.current_frame = start_frame
        self.window.canvas.active_layer_index = first_new_layer
        self.window.canvas.timeline_mode = "sheet"
        self.window.timeline.set_timeline_mode("sheet")
        self.window.canvas._cell_structure_dirty = True
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()
        message = tr("PSDから{len}レイヤーを読み込みました。").format(len=len(imported))
        if skipped:
            message += tr("\n調整レイヤーなど{skipped}項目は破棄しました。").format(skipped=skipped)
        QMessageBox.information(self.window, tr("PSD読み込み"), message)

    def xdts_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self.window,
            tr("XDTSタイムシートを読み込む"),
            "",
            tr("XDTSタイムシート (*.xdts *.xtds);;すべてのファイル (*)"),
        )
        if not path:
            return
        try:
            raw = Path(path).read_text(encoding="utf-8-sig")
            first_line, json_text = raw.split("\n", 1)
            if first_line.rstrip("\r") != "exchangeDigitalTimeSheet Save Data":
                raise OperationError(tr("XDTSの先頭識別文字列が一致しません。"))
            payload = json.loads(json_text)
            if int(payload.get("version", -1)) != 5:
                raise OperationError(tr("対応しているXDTSバージョンは5です。"))
            time_tables = payload.get("timeTables") or []
            if not time_tables:
                raise OperationError(tr("タイムシート情報がありません。"))
            time_table = time_tables[0]
            duration = max(1, int(time_table.get("duration", 1)))
            cell_field = next(
                (field for field in time_table.get("fields", [])
                 if int(field.get("fieldId", -1)) == 0),
                None,
            )
            if cell_field is None:
                raise OperationError(tr("セル欄（fieldId 0）がありません。"))
            tracks = sorted(
                cell_field.get("tracks", []),
                key=lambda track: int(track.get("trackNo", 0)),
            )
            header = next(
                (item for item in time_table.get("timeTableHeaders", [])
                 if int(item.get("fieldId", -1)) == 0),
                {},
            )
            names = list(header.get("names", []))
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            QMessageBox.critical(self.window, tr("XDTS読み込み"), tr("読み込めませんでした。\n\n{exc}").format(exc=exc))
            return

        self.window.canvas.push_doc_undo()
        maximum_track = max(
            (int(track.get("trackNo", 0)) for track in tracks), default=0
        )
        required_layers = maximum_track + 1
        for frame in self.window.canvas.frames:
            while len(frame.layers) < required_layers:
                index = len(frame.layers)
                name = names[index] if index < len(names) else f"Layer {index + 1}"
                frame.layers.append(Layer(name, blank_image()))
        self.window.canvas._ensure_frame_count(duration)

        missing_images = set()
        for track in tracks:
            layer_index = int(track.get("trackNo", 0))
            image_bank = {}
            for frame in self.window.canvas.frames:
                layer = frame.layers[layer_index]
                if layer.has_content and layer.sequence_number is not None:
                    image_bank.setdefault(int(layer.sequence_number), layer.image.copy())
            for frame in self.window.canvas.frames:
                self.window.canvas._clear_timeline_layer_cell(frame.layers[layer_index])
            values = {int(item.get("frame", 0)): item for item in track.get("frames", [])}
            states = []
            previous = None
            for frame_number in range(duration):
                item = values.get(frame_number, {})
                instruction = next(
                    (data for data in item.get("data", []) if int(data.get("id", -1)) == 0),
                    {},
                )
                raw_values = instruction.get("values", [])
                value = str(raw_values[0]) if raw_values else "SYMBOL_NULL_CELL"
                if value == "SYMBOL_HYPHEN":
                    state = previous
                elif value in ("SYMBOL_NULL_CELL", "SYMBOL_TICK_1", "SYMBOL_TICK_2"):
                    state = None
                else:
                    try:
                        state = int(value)
                    except ValueError:
                        state = None
                states.append(state)
                previous = state

            run_start = 0
            for end in range(1, duration + 1):
                if end < duration and states[end] == states[run_start]:
                    continue
                state = states[run_start]
                target = self.window.canvas.frames[run_start].layers[layer_index]
                target.exposure = end - run_start
                if state is None:
                    target.image = blank_image()
                    target.has_content = False
                    target.is_blank_key = True
                else:
                    target.image = image_bank.get(state, blank_image()).copy()
                    target.has_content = True
                    target.is_blank_key = False
                    target.sequence_number = state
                    if state not in image_bank:
                        missing_images.add((layer_index, state))
                run_start = end
            if layer_index < len(names):
                for frame in self.window.canvas.frames:
                    frame.layers[layer_index].name = names[layer_index]

        self.window.canvas.current_frame = 0
        self.window.canvas.active_layer_index = 0
        self.window.canvas.timeline_mode = "sheet"
        self.window.timeline.set_timeline_mode("sheet")
        self.window.canvas._cell_structure_dirty = True
        self.window.canvas.changed.emit()
        self.window.canvas.selectionChanged.emit()
        self.window.canvas.update()
        message = tr("XDTSタイムシートを読み込みました。")
        if missing_images:
            message += tr("\n対応画像がない番号：{len}件（白画像で配置）").format(len=len(missing_images))
        QMessageBox.information(self.window, tr("XDTS読み込み"), message)
