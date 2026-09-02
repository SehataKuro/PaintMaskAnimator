"""Export dialogs (XDTS / PSD / key-sequence images / MP4).

A *collaborator* of ``MainWindow`` rather than a mixin: the window owns one as
``window.export`` and the dependency runs one way only, through the ``window``
handle taken in ``__init__``. That handle is also the parent for the file and
message dialogs raised here.

Being a plain object (not part of the window's namespace) means these methods
cannot collide with a sibling's, and the attributes they rely on -- ``canvas``,
``timeline``, the progress-counter helpers -- are visible as ``self.window.…``
instead of appearing out of nowhere. This is the shape the remaining
``main_window_*`` mixins are being migrated to.
"""
from typing import TYPE_CHECKING

import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox
from .optional_deps import PILImage, PSDImage
from .constants import OUTSIDE_MARGIN
from . import constants
from .canvas import PaintCanvas
from .errors import OPERATION_ERRORS as _OPERATION_ERRORS, OperationError
from .timeline import TimelineWidget
from .progress import close_counter, create_counter, update_counter
from .logging_setup import get_logger

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)


class ExportController:
    """Exports the current document; owned by ``MainWindow`` as ``window.export``."""

    def __init__(self, window: "MainWindow"):
        self.window = window

    def xdts_dialog(self):
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            "XDTSタイムシートを書き出す",
            "PaintMaskAnimator.xdts",
            "XDTSタイムシート (*.xdts)",
        )
        if not path:
            return
        if not path.lower().endswith(".xdts"):
            path += ".xdts"

        duration = self.window._sheet_duration()
        tracks = []
        names = []
        layer_count = len(self.window.canvas.frames[0].layers)
        for layer_index in range(layer_count):
            names.append(self.window.canvas.frames[0].layers[layer_index].name)
            frame_data = []
            for column in range(duration):
                kind, key_column, _exposure = TimelineWidget.timeline_span_at(
                    self.window.canvas.frames, layer_index, column
                )
                if kind == "content":
                    if key_column is None:
                        log.warning(
                            "timeline reported content without a key frame: layer=%s frame=%s",
                            layer_index,
                            column,
                        )
                        value = "SYMBOL_NULL_CELL"
                        frame_data.append({
                            "frame": column,
                            "data": [{"id": 0, "values": [value]}],
                        })
                        continue
                    if column == key_column:
                        number = self.window.canvas.frames[key_column].layers[
                            layer_index
                        ].sequence_number
                        value = str(number) if number is not None else "SYMBOL_NULL_CELL"
                    else:
                        value = "SYMBOL_HYPHEN"
                elif kind == "blank":
                    value = (
                        "SYMBOL_NULL_CELL"
                        if column == key_column else "SYMBOL_HYPHEN"
                    )
                else:
                    value = "SYMBOL_NULL_CELL"
                frame_data.append({
                    "frame": column,
                    "data": [{"id": 0, "values": [value]}],
                })
            tracks.append({"trackNo": layer_index, "frames": frame_data})

        payload = {
            "timeTables": [{
                "duration": duration,
                "name": "PaintMaskAnimator",
                "timeTableHeaders": [{"fieldId": 0, "names": names}],
                "fields": [{"fieldId": 0, "tracks": tracks}],
            }],
            "version": 5,
        }
        try:
            text = (
                "exchangeDigitalTimeSheet Save Data\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
                + "\n"
            )
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self.window, "XDTS書き出し", str(exc))
            return
        QMessageBox.information(
            self.window, "XDTS書き出し", f"タイムシートを書き出しました。\n\n{path}"
        )

    def psd_dialog(self):
        if PSDImage is None or PILImage is None:
            QMessageBox.warning(
                self.window,
                "PSD書き出し",
                "PSDの書き出しには psd-tools と Pillow が必要です。\n"
                "requirements.txtをインストールしてください。",
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            "PSDを書き出す",
            "PaintMaskAnimator.psd",
            "Photoshop Document (*.psd)",
        )
        if not path:
            return
        if not path.lower().endswith(".psd"):
            path += ".psd"
        try:
            psd = PSDImage.new(
                "RGB",
                (int(constants.CANVAS_WIDTH), int(constants.CANVAS_HEIGHT)),
                color=(255, 255, 255),
            )
            layer_count = len(self.window.canvas.frames[0].layers)
            exported_keys = 0
            for layer_index in range(layer_count):
                template = self.window.canvas.frames[0].layers[layer_index]
                folder_name = str(template.name or f"Layer {layer_index + 1}")
                group = psd.create_group(
                    name=folder_name,
                    opacity=max(0, min(255, int(round(template.opacity * 255)))),
                )
                group.visible = bool(template.visible)
                key_number = 0
                for frame in self.window.canvas.frames:
                    layer = frame.layers[layer_index]
                    if not layer.has_content or layer.sequence_only:
                        continue
                    key_number += 1
                    source = layer.image.copy(
                        OUTSIDE_MARGIN,
                        OUTSIDE_MARGIN,
                        constants.CANVAS_WIDTH,
                        constants.CANVAS_HEIGHT,
                    )
                    pil_image = PaintCanvas._qimage_to_pil_rgba(source)
                    if pil_image is None:
                        raise OperationError("PSD書き出しに必要な画像変換を利用できません。")
                    pixel_layer = psd.create_pixel_layer(
                        pil_image,
                        name=f"{folder_name}{key_number:04d}",
                    )
                    group.append(pixel_layer)
                    exported_keys += 1
            if exported_keys == 0:
                raise OperationError("書き出せるキーフレームがありません。")
            psd.save(path)
        except _OPERATION_ERRORS as exc:
            log.error("PSD export failed: %s", exc, exc_info=True)
            QMessageBox.critical(
                self.window, "PSD書き出し", f"PSDを書き出せませんでした。\n\n{exc}"
            )
            return
        QMessageBox.information(
            self.window,
            "PSD書き出し",
            f"{exported_keys}個のキーフレームを書き出しました。\n\n{path}",
        )

    def key_sequence(self, image_format):
        invalid = '<>:"/\\|?*'
        safe_layer_names = []
        used_names = set()
        for layer_index, layer in enumerate(self.window.canvas.layers):
            base_name = "".join(
                "_" if character in invalid else character
                for character in (layer.name.strip() or f"Layer {layer_index + 1}")
            ).strip(" .") or f"Layer {layer_index + 1}"
            safe_name = base_name
            suffix = 2
            while safe_name.casefold() in used_names:
                safe_name = f"{base_name}_{suffix}"
                suffix += 1
            used_names.add(safe_name.casefold())
            safe_layer_names.append(safe_name)
        default_folder_name = f"PaintMaskAnimator_{image_format}_CSV"

        # 最初のダイアログで保存場所と親フォルダー名を同時に指定する。
        folder_dialog = QFileDialog(
            self.window,
            f"連番{image_format}＋CSVの書き出しフォルダー",
        )
        folder_dialog.setOption(
            QFileDialog.Option.DontUseNativeDialog,
            True,
        )
        folder_dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        folder_dialog.setAcceptMode(
            QFileDialog.AcceptMode.AcceptSave
        )
        folder_dialog.setLabelText(
            QFileDialog.DialogLabel.FileName,
            "フォルダー名：",
        )
        folder_dialog.setLabelText(
            QFileDialog.DialogLabel.Accept,
            "この名前で作成",
        )
        folder_dialog.selectFile(default_folder_name)

        if folder_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = folder_dialog.selectedFiles()
        if not selected:
            return

        destination = Path(selected[0])
        folder_name = destination.name.strip()
        folder_name = "".join(
            "_" if character in invalid else character
            for character in folder_name
        ).strip(" .")
        if not folder_name or folder_name in (".", ".."):
            QMessageBox.warning(
                self.window,
                "フォルダー名",
                "使用できるフォルダー名を指定してください。",
            )
            return
        destination = destination.parent / folder_name

        try:
            if destination.exists() and not destination.is_dir():
                QMessageBox.warning(
                    self.window,
                    "書き出し先",
                    "同じ名前のファイルが存在するため、"
                    "フォルダーを作成できません。",
                )
                return
            if destination.exists() and any(destination.iterdir()):
                answer = QMessageBox.question(
                    self.window,
                    "同名フォルダー",
                    f"「{folder_name}」には既存のファイルがあります。\n"
                    "このフォルダーへ書き出しますか？",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            destination.mkdir(parents=True, exist_ok=True)
            for safe_layer_name in safe_layer_names:
                (destination / safe_layer_name).mkdir(
                    parents=True,
                    exist_ok=True,
                )
        except OSError as exc:
            QMessageBox.critical(
                self.window,
                "フォルダー作成エラー",
                f"書き出しフォルダーを作成できません。\n\n{exc}",
            )
            return

        keys = []
        for layer_index, safe_layer_name in enumerate(safe_layer_names):
            for frame_index, frame in enumerate(self.window.canvas.frames):
                if (
                    layer_index < len(frame.layers)
                    and frame.layers[layer_index].has_content
                ):
                    keys.append((
                        layer_index,
                        safe_layer_name,
                        frame_index,
                        frame.layers[layer_index],
                    ))

        if not keys:
            QMessageBox.warning(
                self.window,
                "連番書き出し",
                "書き出せるキーフレームがありません。",
            )
            return

        timing_rows = []
        progress = create_counter(
            self.window,
            f"連番{image_format}書き出し",
            len(keys),
            f"「{folder_name}」へ書き出しています",
        )
        try:
            layer_numbers = {}
            for number, (
                layer_index,
                safe_layer_name,
                frame_index,
                layer,
            ) in enumerate(keys, 1):
                update_counter(
                    progress,
                    number - 1,
                    len(keys),
                    f"{number}枚目を書き出しています",
                )
                image = layer.image.copy(
                    OUTSIDE_MARGIN,
                    OUTSIDE_MARGIN,
                    constants.CANVAS_WIDTH,
                    constants.CANVAS_HEIGHT,
                )
                layer_number = layer_numbers.get(layer_index, 0) + 1
                layer_numbers[layer_index] = layer_number
                filename = f"{safe_layer_name}{layer_number:04d}"
                image_destination = destination / safe_layer_name
                if image_format == "PNG":
                    output_path = image_destination / (
                        filename + ".png"
                    )
                    if not image.save(str(output_path), "PNG"):
                        raise OSError(
                            f"{output_path.name}を保存できませんでした。"
                        )
                else:
                    output_path = image_destination / (
                        filename + ".tga"
                    )
                    self.window.save_tga_image(image, output_path)

                timing_rows.append([
                    filename,
                    frame_index + 1,
                    frame_index + max(1, layer.exposure),
                    max(1, layer.exposure),
                ])
                update_counter(
                    progress,
                    number,
                    len(keys),
                    f"{number}枚目の書き出しが完了しました",
                )

            csv_path = destination / "TS.csv"
            with csv_path.open(
                "w", newline="", encoding="utf-8-sig"
            ) as file:
                writer = csv.writer(file)
                writer.writerow([
                    "name",
                    "start_frame",
                    "end_frame",
                    "duration",
                ])
                writer.writerows(timing_rows)
        except (_OPERATION_ERRORS + (csv.Error,)) as exc:
            log.error("sequence/CSV export failed: %s", exc, exc_info=True)
            QMessageBox.critical(
                self.window,
                "連番書き出しエラー",
                f"書き出し中にエラーが発生しました。\n\n{exc}",
            )
            return
        finally:
            close_counter(progress)

        self.window.statusBar().showMessage(
            f"「{folder_name}」へ{len(keys)}枚と"
            "TS.csvを書き出しました。",
            4000,
        )
        QMessageBox.information(
            self.window,
            "連番書き出し完了",
            "次の構成で書き出しました。\n\n"
            f"{destination}\n"
            "├─ 各レイヤー名のフォルダー\n"
            "│  └─ レイヤー名0001...\n"
            "└─ TS.csv",
        )

    def mp4(self):
        app_file = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)
        bundled_ffmpeg = app_file.with_name("ffmpeg.exe")
        ff = str(bundled_ffmpeg) if bundled_ffmpeg.exists() else shutil.which("ffmpeg")
        if not ff:QMessageBox.warning(self.window,'FFmpeg','ffmpegが必要です。');return
        path,_=QFileDialog.getSaveFileName(self.window,'MP4書き出し','animation.mp4','MP4 (*.mp4)')
        if not path:return
        if not path.lower().endswith('.mp4'):path+='.mp4'
        with tempfile.TemporaryDirectory() as td:
            total = sum(max(1, int(frame.duration)) for frame in self.window.canvas.frames)
            progress = create_counter(
                self.window,
                "MP4書き出し",
                total,
                "動画用フレームを準備しています",
            )
            output_index = 0
            for frame_index, frame in enumerate(self.window.canvas.frames):
                image = self.window.crop_image(frame_index)
                for _ in range(max(1, int(frame.duration))):
                    output_index += 1
                    update_counter(
                        progress,
                        output_index - 1,
                        total,
                        f"フレーム {output_index} / {total} を準備しています",
                    )
                    image.save(
                        str(Path(td) / f"f_{output_index:06}.png"),
                        "PNG",
                    )
            close_counter(progress)
            encoding = create_counter(
                self.window,
                "MP4書き出し",
                1,
                "FFmpegで動画へ変換しています",
            )
            QApplication.processEvents()
            r=subprocess.run([ff,'-y','-framerate',str(self.window.timeline.fps.value()),'-i',str(Path(td)/'f_%06d.png'),'-c:v','libx264','-pix_fmt','yuv420p',path],capture_output=True,text=True)
            close_counter(encoding)
            if r.returncode:QMessageBox.critical(self.window,'MP4エラー',r.stderr[-1500:])
