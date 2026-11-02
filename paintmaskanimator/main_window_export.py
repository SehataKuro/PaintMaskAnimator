"""Export dialogs (XDTS / PSD / key-sequence images / cut folder / MP4).

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
import datetime as _datetime
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QInputDialog,
                               QMessageBox)
from .i18n import tr
from .memo_window import MEMO_FILE_NAME
from . import movie_slate
from . import cut_folder, imaging, preferences, shadow_export, timesheet_file
from .models import default_layer_name
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

    # -- 今の絵の書き出し（キャンバス下のバー） --------------------------------

    def canvas_image(self):
        """今表示しているコマの、キャンバス範囲の合成画像。"""
        return self.window.crop_image(self.window.canvas.current_frame)

    @staticmethod
    def snapshot_path(folder):
        """``folder`` に置く、年月日時分秒（14桁）の .png のパス。

        同じ秒に2枚撮ると名前がぶつかるので、そのときだけ連番を足して
        前の1枚を黙って上書きしないようにする。
        """
        stamp = _datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        path = folder / f"{stamp}.png"
        suffix = 2
        while path.exists():
            path = folder / f"{stamp}_{suffix}.png"
            suffix += 1
        return path

    def copy_canvas_to_clipboard(self):
        """今の絵をクリップボードへ書き出す。"""
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setImage(self.canvas_image())
        self.window.statusBar().showMessage(
            tr("今の絵をクリップボードへ書き出しました。"), 4000
        )
        return True

    def choose_snapshot_folder(self):
        """カメラボタンの保存先を選び直す。選んだら真。"""
        folder = QFileDialog.getExistingDirectory(
            self.window,
            tr("画像の保存先フォルダー"),
            preferences.snapshot_folder() or "",
        )
        if not folder:
            return False
        preferences.set_snapshot_folder(folder)
        return True

    def save_canvas_snapshot(self, choose_folder=False):
        """今の絵を保存先フォルダーへ年月日時分秒の名前で書き出す。

        保存先が未設定・見つからない場合と ``choose_folder`` のときだけ尋ねる。
        あとはボタン1回で1枚ずつたまる。
        """
        folder = Path(preferences.snapshot_folder())
        if choose_folder or not preferences.snapshot_folder() or not folder.is_dir():
            if not self.choose_snapshot_folder():
                return False
            folder = Path(preferences.snapshot_folder())
        path = self.snapshot_path(folder)
        try:
            saved = self.canvas_image().save(str(path), "PNG")  # pyright: ignore[reportCallIssue,reportArgumentType]  # QImage.save accepts a str format at runtime
        except OSError as exc:
            log.error("snapshot save failed: %s", exc, exc_info=True)
            saved = False
        if not saved:
            QMessageBox.warning(
                self.window,
                tr("画像を保存"),
                tr("画像を保存できませんでした：{path}").format(path=path),
            )
            return False
        self.window.statusBar().showMessage(
            tr("「{name}」として保存しました。").format(name=path.name), 4000
        )
        return True

    def xdts_dialog(self):
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            tr("XDTSタイムシートを書き出す"),
            "PaintMaskAnimator.xdts",
            tr("XDTSタイムシート (*.xdts)"),
        )
        if not path:
            return
        if not path.lower().endswith(".xdts"):
            path += ".xdts"
        try:
            Path(path).write_text(self.xdts_text(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self.window, tr("XDTS書き出し"), str(exc))
            return
        QMessageBox.information(
            self.window, tr("XDTS書き出し"), tr("タイムシートを書き出しました。\n\n{path}").format(path=path)
        )

    def sheet_tracks(self):
        """``(layer_names, tracks, duration)`` for a time sheet.

        Each track holds one instruction per frame: a セル番号, or
        ``SYMBOL_HYPHEN`` / ``SYMBOL_NULL_CELL``.
        """
        duration = self.window._sheet_duration()
        tracks = []
        names = []
        layer_count = len(self.window.canvas.frames[0].layers)
        for layer_index in range(layer_count):
            names.append(self.window.canvas.frames[0].layers[layer_index].name)
            values = []
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
                        value = timesheet_file.NULL_CELL
                    elif column == key_column:
                        number = self.window.canvas.frames[key_column].layers[
                            layer_index
                        ].sequence_number
                        value = str(number) if number is not None else timesheet_file.NULL_CELL
                    else:
                        value = timesheet_file.HYPHEN
                elif kind == "blank":
                    value = (
                        timesheet_file.NULL_CELL
                        if column == key_column else timesheet_file.HYPHEN
                    )
                else:
                    value = timesheet_file.NULL_CELL
                values.append(value)
            tracks.append(values)
        return names, tracks, duration

    def xdts_text(self, sheet_name="PaintMaskAnimator"):
        """The current timeline as XDTS v5 text (header line + JSON)."""
        return self.sheet_text("xdts", sheet_name)

    def sheet_text(self, fmt, sheet_name, **header):
        """The current timeline as an XDTS or TDTS time sheet."""
        names, tracks, duration = self.sheet_tracks()
        return timesheet_file.sheet_text(fmt, sheet_name, names, tracks, duration, **header)

    def cut_folder_cels(self):
        """Distinct drawings to write: one per (layer, セル番号), first seen wins.

        Returns ``(cels, images, skipped)`` where ``images`` maps a cel to its
        canvas-sized image and ``skipped`` counts drawn keys that have no
        セル番号 (the XDTS could not reference them either).
        """
        invalid = cut_folder.INVALID_NAME_CHARS + "/"
        frames = self.window.canvas.frames
        cels = []
        images = {}
        skipped = 0
        if not frames:
            return cels, images, skipped
        for layer_index, template in enumerate(frames[0].layers):
            name = "".join(
                "_" if character in invalid else character
                for character in (template.name.strip() or default_layer_name(layer_index))
            ).strip(" .") or default_layer_name(layer_index)
            seen = set()
            for frame in frames:
                if layer_index >= len(frame.layers):
                    continue
                layer = frame.layers[layer_index]
                if not layer.has_content or layer.sequence_only:
                    continue
                if layer.sequence_number is None:
                    skipped += 1
                    continue
                number = int(layer.sequence_number)
                if number in seen:
                    continue
                seen.add(number)
                cel = cut_folder.CelSource(layer_index, name, number)
                cels.append(cel)
                images[cel] = layer.image
        return cels, images, skipped

    def cut_folder(self):
        from .cut_folder_dialog import CutFolderExportDialog

        cels, images, skipped = self.cut_folder_cels()
        project = self.window.current_project_path
        hint = cut_folder.guess_cut_number(Path(project).stem) if project else ""
        dialog = CutFolderExportDialog(cels, cut_hint=hint, parent=self.window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        dialog.remember()
        plan = dialog.plan()
        parent = Path(dialog.destination.text().strip())
        self.write_cut_folder(plan, parent, images, dialog.current_layout().image_format, skipped)

    def write_cut_folder(self, plan, parent, images, image_format, skipped=0):
        target = parent / plan.folder_name
        try:
            if target.exists() and not target.is_dir():
                raise OperationError(tr("同じ名前のファイルがあるため、フォルダーを作成できません。"))
            if target.exists() and any(target.iterdir()):
                answer = QMessageBox.question(
                    self.window,
                    tr("同名フォルダー"),
                    tr("「{name}」には既存のファイルがあります。\n同じ名前のファイルは上書きします。書き出しますか？").format(name=plan.folder_name),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return False
            target.mkdir(parents=True, exist_ok=True)
            for folder in plan.folders:
                (target / folder).mkdir(parents=True, exist_ok=True)
        except (OSError, OperationError) as exc:
            QMessageBox.critical(
                self.window,
                tr("カットフォルダー書き出し"),
                tr("書き出しフォルダーを作成できません。\n\n{exc}").format(exc=exc),
            )
            return False

        progress = create_counter(
            self.window,
            tr("カットフォルダー書き出し"),
            len(plan.files),
            tr("「{name}」へ書き出しています").format(name=plan.folder_name),
        )
        try:
            for number, (relative, cel) in enumerate(plan.files, 1):
                update_counter(
                    progress, number - 1, len(plan.files),
                    tr("{number}枚目を書き出しています").format(number=number),
                )
                image = images[cel].copy(
                    OUTSIDE_MARGIN,
                    OUTSIDE_MARGIN,
                    constants.CANVAS_WIDTH,
                    constants.CANVAS_HEIGHT,
                )
                output_path = target / relative
                if image_format == "tga":
                    saved = self.window.save_tga_image(image, output_path)
                else:
                    saved = image.save(str(output_path), "PNG")
                if not saved:
                    raise OSError(tr("{name}を保存できませんでした。").format(name=output_path.name))
            sheet_name = Path(plan.timesheet_path).stem
            (target / plan.timesheet_path).write_text(
                self.sheet_text(plan.timesheet_format, sheet_name, **plan.sheet_header),
                encoding="utf-8",
            )
            # メモはタイムシートの隣へ出す（Issue #25）。空なら作らない。
            memo = str(getattr(self.window, "project_memo", "") or "").strip()
            if memo:
                (
                    target / Path(plan.timesheet_path).parent / MEMO_FILE_NAME
                ).write_text(memo + chr(10), encoding="utf-8")
        except _OPERATION_ERRORS as exc:
            log.error("cut folder export failed: %s", exc, exc_info=True)
            QMessageBox.critical(
                self.window,
                tr("カットフォルダー書き出し"),
                tr("書き出し中にエラーが発生しました。\n\n{exc}").format(exc=exc),
            )
            return False
        finally:
            close_counter(progress)

        message = tr("「{name}」へセル{count}枚とタイムシートを書き出しました。").format(
            name=plan.folder_name, count=len(plan.files)
        )
        if skipped:
            message += "\n" + tr("セル番号のないキー{count}枚は書き出していません。").format(count=skipped)
        self.window.statusBar().showMessage(message.split("\n")[0], 4000)
        QMessageBox.information(
            self.window, tr("カットフォルダー書き出し"), f"{message}\n\n{target}"
        )
        return True

    def psd_dialog(self):
        if PSDImage is None or PILImage is None:
            QMessageBox.warning(
                self.window,
                tr("PSD書き出し"),
                tr("PSDの書き出しには psd-tools と Pillow が必要です。\n"
                "requirements.txtをインストールしてください。"),
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            tr("PSDを書き出す"),
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
                folder_name = str(template.name or default_layer_name(layer_index))
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
                        raise OperationError(tr("PSD書き出しに必要な画像変換を利用できません。"))
                    pixel_layer = psd.create_pixel_layer(
                        pil_image,
                        name=f"{folder_name}{key_number:04d}",
                    )
                    group.append(pixel_layer)
                    exported_keys += 1
            if exported_keys == 0:
                raise OperationError(tr("書き出せるキーフレームがありません。"))
            psd.save(path)
        except _OPERATION_ERRORS as exc:
            log.error("PSD export failed: %s", exc, exc_info=True)
            QMessageBox.critical(
                self.window, tr("PSD書き出し"), tr("PSDを書き出せませんでした。\n\n{exc}").format(exc=exc)
            )
            return
        QMessageBox.information(
            self.window,
            tr("PSD書き出し"),
            tr("{keys}個のキーフレームを書き出しました。\n\n{path}").format(keys=exported_keys, path=path),
        )

    def key_sequence(self, image_format):
        invalid = '<>:"/\\|?*'
        safe_layer_names = []
        used_names = set()
        for layer_index, layer in enumerate(self.window.canvas.layers):
            base_name = "".join(
                "_" if character in invalid else character
                for character in (layer.name.strip() or default_layer_name(layer_index))
            ).strip(" .") or default_layer_name(layer_index)
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
            tr("連番{format}＋CSVの書き出しフォルダー").format(format=image_format),
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
            tr("フォルダー名："),
        )
        folder_dialog.setLabelText(
            QFileDialog.DialogLabel.Accept,
            tr("この名前で作成"),
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
                tr("フォルダー名"),
                tr("使用できるフォルダー名を指定してください。"),
            )
            return
        destination = destination.parent / folder_name

        try:
            if destination.exists() and not destination.is_dir():
                QMessageBox.warning(
                    self.window,
                    tr("書き出し先"),
                    tr("同じ名前のファイルが存在するため、"
                    "フォルダーを作成できません。"),
                )
                return
            if destination.exists() and any(destination.iterdir()):
                answer = QMessageBox.question(
                    self.window,
                    tr("同名フォルダー"),
                    tr("「{name}」には既存のファイルがあります。\nこのフォルダーへ書き出しますか？").format(name=folder_name),
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
                tr("フォルダー作成エラー"),
                tr("書き出しフォルダーを作成できません。\n\n{exc}").format(exc=exc),
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
                tr("連番書き出し"),
                tr("書き出せるキーフレームがありません。"),
            )
            return

        timing_rows = []
        progress = create_counter(
            self.window,
            tr("連番{format}書き出し").format(format=image_format),
            len(keys),
            tr("「{name}」へ書き出しています").format(name=folder_name),
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
                    tr("{number}枚目を書き出しています").format(number=number),
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
                            tr("{name}を保存できませんでした。").format(name=output_path.name)
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
                    tr("{number}枚目の書き出しが完了しました").format(number=number),
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
                tr("連番書き出しエラー"),
                tr("書き出し中にエラーが発生しました。\n\n{exc}").format(exc=exc),
            )
            return
        finally:
            close_counter(progress)

        self.window.statusBar().showMessage(
            tr("「{name}」へ{len}枚とTS.csvを書き出しました。").format(name=folder_name, len=len(keys)),
            4000,
        )
        QMessageBox.information(
            self.window,
            tr("連番書き出し完了"),
            tr("次の構成で書き出しました。\n\n{destination}\n├─ 各レイヤー名のフォルダー\n│  └─ レイヤー名0001...\n└─ TS.csv").format(destination=destination),
        )

    def mp4(self):
        app_file = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)
        bundled_ffmpeg = app_file.with_name("ffmpeg.exe")
        ff = str(bundled_ffmpeg) if bundled_ffmpeg.exists() else shutil.which("ffmpeg")
        if not ff:QMessageBox.warning(self.window,'FFmpeg',tr('ffmpegが必要です。'));return
        path,_=QFileDialog.getSaveFileName(self.window,tr('MP4書き出し'),'animation.mp4','MP4 (*.mp4)')
        if not path:return
        if not path.lower().endswith('.mp4'):path+='.mp4'
        with tempfile.TemporaryDirectory() as td:
            total = sum(max(1, int(frame.duration)) for frame in self.window.canvas.frames)
            progress = create_counter(
                self.window,
                tr("MP4書き出し"),
                total,
                tr("動画用フレームを準備しています"),
            )
            output_index = 0
            # 頭の数コマだけカット名を重ねる（Issue #26）。コマは足さないので
            # タイミングは変わらない。
            cut_name = movie_slate.cut_name_for(
                self.window.current_project_path
            )
            for frame_index, frame in enumerate(self.window.canvas.frames):
                image = self.window.crop_image(frame_index)
                for _ in range(max(1, int(frame.duration))):
                    output_index += 1
                    written = (
                        movie_slate.draw_cut_name(image, cut_name)
                        if output_index <= movie_slate.LEADING_FRAMES
                        else image
                    )
                    update_counter(
                        progress,
                        output_index - 1,
                        total,
                        tr("フレーム {index} / {total} を準備しています").format(index=output_index, total=total),
                    )
                    written.save(
                        str(Path(td) / f"f_{output_index:06}.png"),
                    )
            close_counter(progress)
            encoding = create_counter(
                self.window,
                tr("MP4書き出し"),
                1,
                tr("FFmpegで動画へ変換しています"),
            )
            QApplication.processEvents()
            r=subprocess.run([ff,'-y','-framerate',str(self.window.timeline.fps.value()),'-i',str(Path(td)/'f_%06d.png'),'-c:v','libx264','-pix_fmt','yuv420p',path],capture_output=True,text=True)
            close_counter(encoding)
            if r.returncode:QMessageBox.critical(self.window,tr('MP4エラー'),r.stderr[-1500:])


    # ------------------------------------------------------------------
    # 影素材の書き出し（Issue #21）
    # ------------------------------------------------------------------
    def shadow_folder_names(self):
        """色が1つ以上入っている使用色フォルダーの名前。"""
        palette = self.window.palette
        return [
            name for name in palette.category_order
            if palette._category_members(name)
        ]

    def shadow_materials(self, folder_name):
        """``folder_name`` の色を影として、キーごとの3素材を作る。

        :returns: ``[(レイヤー名, 絵番号, {種類: QImage}), ...]``。
        """
        palette = self.window.palette
        shadow_rgbs = palette._category_members(folder_name)
        results = []
        if not shadow_rgbs:
            return results
        frames = self.window.canvas.frames
        if not frames:
            return results
        for layer_index, template in enumerate(frames[0].layers):
            layer_name = str(template.name or default_layer_name(layer_index))
            seen = set()
            for frame_index, frame in enumerate(frames):
                if layer_index >= len(frame.layers):
                    continue
                layer = frame.layers[layer_index]
                if not layer.has_content or layer.sequence_only:
                    continue
                # 同じ絵を何度も書き出さない。番号が無ければコマ番号を使う。
                number = (
                    int(layer.sequence_number)
                    if layer.sequence_number is not None
                    else frame_index + 1
                )
                if number in seen:
                    continue
                seen.add(number)
                pixels = imaging.qimage_rgba_array(layer.image)
                images = {
                    kind: imaging.rgba_array_to_qimage(array)
                    for kind, array in shadow_export.materials(
                        pixels, shadow_rgbs
                    ).items()
                }
                results.append((layer_name, number, images))
        return results

    def shadow_sequence(self):
        """使用色フォルダーを選び、影素材の連番を書き出す。"""
        folders = self.shadow_folder_names()
        if not folders:
            QMessageBox.warning(
                self.window,
                tr("影素材の書き出し"),
                tr("色の入った使用色フォルダーがありません。"
                   "影色をフォルダーにまとめてから実行してください。"),
            )
            return False
        folder_name, accepted = QInputDialog.getItem(
            self.window,
            tr("影素材の書き出し"),
            tr("影色をまとめたフォルダー："),
            folders,
            0,
            False,
        )
        if not accepted or not folder_name:
            return False
        destination = QFileDialog.getExistingDirectory(
            self.window, tr("書き出し先のフォルダー")
        )
        if not destination:
            return False
        return self.write_shadow_sequence(folder_name, Path(destination))

    def write_shadow_sequence(self, folder_name, destination):
        """``destination`` の下へ、種類ごとのフォルダーを作って書き出す。"""
        materials = self.shadow_materials(folder_name)
        if not materials:
            self.window.statusBar().showMessage(
                tr("書き出せるセルがありません。"), 2800
            )
            return False

        progress = create_counter(
            self.window,
            tr("影素材の書き出し"),
            len(materials),
            tr("「{name}」の影素材を書き出しています").format(name=folder_name),
        )
        written = 0
        try:
            for index, (layer_name, number, images) in enumerate(materials):
                update_counter(
                    progress, index, len(materials),
                    tr("{number}枚目を書き出しています").format(number=index + 1),
                )
                for kind, image in images.items():
                    target = destination / kind / layer_name
                    target.mkdir(parents=True, exist_ok=True)
                    cropped = image.copy(
                        OUTSIDE_MARGIN,
                        OUTSIDE_MARGIN,
                        constants.CANVAS_WIDTH,
                        constants.CANVAS_HEIGHT,
                    )
                    path = target / f"{layer_name}{number:04d}.png"
                    if not cropped.save(str(path), "PNG"):
                        raise OSError(
                            tr("{name}を保存できませんでした。").format(
                                name=path.name
                            )
                        )
                    written += 1
        except _OPERATION_ERRORS as exc:
            log.error("shadow export failed: %s", exc, exc_info=True)
            QMessageBox.critical(
                self.window,
                tr("影素材の書き出し"),
                tr("書き出し中にエラーが発生しました。" + chr(10) + chr(10) + "{exc}").format(exc=exc),
            )
            return False
        finally:
            close_counter(progress)

        self.window.statusBar().showMessage(
            tr("影素材を{count}枚書き出しました：{destination}").format(
                count=written, destination=destination
            ),
            4000,
        )
        return True
