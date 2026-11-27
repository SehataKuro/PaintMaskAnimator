"""色塗り前の初期状態の保持と比較表示。``window.initial_state`` が持つ。

カットフォルダーやCLIPから読み込んだ線画を、セルごとに「色塗り前の初期状態」として
プロジェクトに保持する。自動塗りは初期状態から黒の主線と色トレス線を取り出して
部分を分けるので、塗りかけの色に惑わされず精度が上がる。キャンバス下の比較ボタンで、
今の絵と初期状態を切り替えて見比べられる。

A collaborator rather than a mixin -- see main_window_export.py for why.
"""
from typing import TYPE_CHECKING, List, Optional, Tuple, cast
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QMessageBox

from .canvas_changes import publish_initial_images
from .pma_image import as_qimage, copy_image
from . import clip_animation, cut_folder, preferences
from .errors import OPERATION_ERRORS, OperationError
from .frame_scope import FrameScope, resolve_cells
from .i18n import tr
from .undo_entries import InitialStateUndo

if TYPE_CHECKING:
    from .main_window import MainWindow
    from .pma_image import PmaImage

Cell = Tuple[int, int]


class InitialStateController:
    """Owned by ``MainWindow`` as ``window.initial_state``."""

    def __init__(self, window: "MainWindow"):
        self.window = window

    def import_line_art_dialog(self) -> int:
        folder = QFileDialog.getExistingDirectory(
            self.window, tr("線画カットフォルダーを初期状態として読み込む"), ""
        )
        return self.import_line_art(folder) if folder else 0

    def import_line_art(self, path) -> int:
        return self._import_line_art(path, from_clip=False)

    def import_clip_line_art_dialog(self) -> int:
        path, _ = QFileDialog.getOpenFileName(
            self.window, tr("CLIPの線画を初期状態として読み込む"), "",
            tr("CLIP STUDIO PAINT (*.clip);;すべてのファイル (*)"),
        )
        return self.import_clip_line_art(path) if path else 0

    def import_clip_line_art(self, path) -> int:
        return self._import_line_art(path, from_clip=True)

    def _import_line_art(self, path, *, from_clip: bool) -> int:
        """Match layer names/cel numbers, retaining painted pixels and timing.

        No timesheet is needed. Read and validate every matching image before
        changing anything, then retain only initial-image references for Undo.
        """
        canvas = self.window.canvas
        title = (tr("CLIPの線画を初期状態として読み込む") if from_clip
                 else tr("線画カットフォルダーを初期状態として読み込む"))
        status = self.window.statusBar()
        status.showMessage(tr("線画の初期状態を読み込んでいます…"), 0)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            clip_cels: dict[str, dict[int, tuple[str, QImage]]] = {}
            if from_clip:
                document = clip_animation.read_clip_animation(path)
                for source_layer in document.layers:
                    key = source_layer.name.strip().casefold()
                    if key in clip_cels:
                        raise OperationError(tr(
                            "CLIP内のアニメーションフォルダー名が重複しています：{name}"
                        ).format(name=source_layer.name))
                    numbers = clip_animation.cell_sequence_numbers(source_layer)
                    clip_cels[key] = {
                        numbers[tag]: (f"{source_layer.name}/{tag}", image)
                        for tag, image in source_layer.cell_images.items()
                    }
                file_cels = {}
            else:
                file_cels = cut_folder.scan_cut_folder(path).cels
            loaded = {}
            cells, archived = [], []
            missing = 0

            def match(layer):
                nonlocal missing
                if not layer.has_content or layer.is_paper or layer.is_draft:
                    return None
                name = layer.name.strip()
                key = name.casefold()
                if not from_clip and key not in file_cels:
                    key = "".join(
                        "_" if char in cut_folder.INVALID_NAME_CHARS + "/" else char
                        for char in name
                    ).strip(" .").casefold()
                source = (clip_cels.get(key, {}).get(layer.sequence_number) if from_clip
                          else file_cels.get(key, {}).get(layer.sequence_number))
                if source is None:
                    missing += 1
                    return None
                if from_clip:
                    source_name, image = cast(tuple[str, QImage], source)
                    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                else:
                    source = cast(Path, source)
                    source_name = Path(source).name
                    if source not in loaded:
                        image, error = canvas._read_image_file(source)
                        if image is None or image.isNull():
                            raise OperationError(tr("{name} を読み込めません。\n{error}").format(
                                name=source_name, error=error,
                            ))
                        loaded[source] = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                    image = loaded[source]
                # Layer も DrawingTarget も、絵は PmaImage で持つ。
                layer_image: PmaImage = layer.image
                cell_size = as_qimage(layer_image).size()
                if image.size() != cell_size:
                    raise OperationError(tr(
                        "線画のサイズが現在のセルと一致しません：{name}\n"
                        "線画：{width} × {height}px／現在のセル：{expected_width} × {expected_height}px"
                    ).format(name=source_name, width=image.width(), height=image.height(),
                             expected_width=cell_size.width(), expected_height=cell_size.height()))
                return image

            # Dense holds share artwork, but import's count and history retain
            # the old explicit-position policy, including saved same-number keys.
            from .document import project_v1_state
            positions, _ = project_v1_state(canvas.frames)
            for fi, frame in enumerate(canvas.frames):
                for li, layer in enumerate(frame.layers):
                    if li >= len(positions[fi].layers) or not positions[fi].layers[li].has_content:
                        continue
                    image = match(layer)
                    if image is not None:
                        cells.append((fi, li, image))
            from .document import DrawingTarget
            for li, track in enumerate(canvas._document.tracks):
                for number in track.legacy_archive_hints:
                    cell = track.cells.get(number)
                    if cell is None:
                        continue
                    image = match(DrawingTarget(cell, track, None, None, number))
                    if image is not None:
                        archived.append((li, number, image))
        except OPERATION_ERRORS as exc:
            status.clearMessage()
            # Restore the cursor before a modal error dialog appears.
            error_text = str(exc)
        else:
            error_text = None
        finally:
            QApplication.restoreOverrideCursor()
        if error_text is not None:
            QMessageBox.critical(self.window, title, tr(
                "線画を読み込めませんでした。現在の絵と初期状態は変更していません。\n\n{error}"
            ).format(error=error_text))
            return 0
        if not cells and not archived:
            status.clearMessage()
            QMessageBox.information(self.window, title, tr(
                "一致するセルがありません。読み込み元のレイヤー名とセル番号を確認してください。"
            ))
            return 0

        def snapshot(image):
            return as_qimage(copy_image(image))

        canvas.push_undo(InitialStateUndo(
            [(fi, li, snapshot(canvas.frames[fi].layers[li].initial_image)) for fi, li, _ in cells],
            [(li, number, snapshot(canvas._document.tracks[li].cells[number].initial_image))
             for li, number, _ in archived],
        ))
        changes = canvas._document.set_initial_images(
            [(fi, li, QImage(image)) for fi, li, image in cells],
            [(li, number, QImage(image)) for li, number, image in archived],
        )
        publish_initial_images(canvas, changes)
        count = len(cells) + len(archived)
        status.showMessage(tr(
            "{count}セルの線画を初期状態として読み込みました。対応する線画がない{missing}セルは変更していません。"
        ).format(count=count, missing=missing), 8000)
        return count

    def cells(self) -> List[Cell]:
        """全レイヤーの、絵のあるセル（保持はキーフレームへ畳む）。"""
        canvas = self.window.canvas
        if not canvas.frames:
            return []
        layers = range(len(canvas.frames[0].layers))
        scope = FrameScope.all_frames(canvas, layers=layers)
        return [
            (frame, layer) for frame, layer in resolve_cells(canvas, scope)
            if canvas.frames[frame].layers[layer].has_content
        ]

    def count(self, cells: List[Cell]) -> int:
        """``cells`` のうち、初期状態を保持しているセルの数。"""
        frames = self.window.canvas.frames
        return sum(
            1 for frame, layer in cells if frames[frame].layers[layer].initial_image is not None
        )

    def keep(self, confirm: bool = True) -> int:
        """全レイヤーの今の絵を、色塗り前の初期状態として保持する。保持したセル数を返す。

        ``confirm`` なら、すでに保持しているセルを上書きしてよいか確かめる。
        """
        canvas = self.window.canvas
        status = self.window.statusBar()
        cells = self.cells()
        if not cells:
            status.showMessage(tr("初期状態として保持できるセルがありません。"), 3000)
            return 0
        kept = self.count(cells)
        if kept and confirm:
            answer = QMessageBox.question(
                self.window,
                tr("今の絵を初期状態として保持"),
                tr("{count}セルはすでに色塗り前の初期状態を保持しています。今の絵で"
                   "上書きしますか？\n塗ったあとの絵で上書きすると、色塗り前の状態には"
                   "戻せなくなります。").format(count=kept),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return 0
        canvas._document.keep_initial_images(cells)
        status.showMessage(
            tr("{count}セルを色塗り前の初期状態として保持しました。保存するとプロジェクトに"
               "入ります。").format(count=len(cells)),
            6000,
        )
        return len(cells)

    def clear(self) -> int:
        """全レイヤーで保持している初期状態を消す。消したセル数を返す。"""
        canvas = self.window.canvas
        status = self.window.statusBar()
        cells = self.cells()
        kept = self.count(cells)
        if not kept:
            status.showMessage(tr("色塗り前の初期状態を保持していません。"), 3000)
            return 0
        answer = QMessageBox.question(
            self.window,
            tr("保持した初期状態を消す"),
            tr("{count}セルで保持している色塗り前の初期状態を消しますか？").format(count=kept),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return 0
        canvas._document.set_initial_images([(frame, layer, None) for frame, layer in cells])
        self.set_compare(False)
        status.showMessage(tr("{count}セルの初期状態を消しました。").format(count=kept), 4000)
        return kept

    def keep_cells(self, cells: List[Cell]) -> int:
        """``cells`` のうち、初期状態をまだ持たないセルの今の絵を保持する。保持した数を返す。

        連番画像を足したときに使う。ほかのレイヤーや、もう保持しているセル
        （塗ったあとの絵で上書きすると戻せなくなる）には触れない。
        """
        canvas = self.window.canvas
        todo = [
            (frame, layer) for frame, layer in cells
            if canvas.frames[frame].layers[layer].initial_image is None
            and canvas.frames[frame].layers[layer].has_content
        ]
        kept = len(todo)
        if kept:
            canvas._document.keep_initial_images(todo)
            self.window.statusBar().showMessage(
                tr("{count}セルを色塗り前の初期状態として保持しました。保存するとプロジェクトに"
                   "入ります。").format(count=kept),
                6000,
            )
        return kept

    def layer_cells(self, layer_index: int) -> List[Cell]:
        """1つのレイヤーの、絵のあるセル（保持はキーフレームへ畳む）。"""
        return [cell for cell in self.cells() if cell[1] == int(layer_index)]

    def offer_after_import(self, cells: Optional[List[Cell]] = None) -> bool:
        """カットフォルダーや連番画像を読み込んだあと、初期状態として保持するか尋ねる。

        ``cells`` を渡すと、そのセル（読み込んだレイヤー）だけを保持する。省略時は
        全レイヤー（カットフォルダーは文書ごと置き換えるため）。環境設定で「常に
        保持する」なら尋ねずに保持する。保持したら真。
        """
        def keep() -> bool:
            if cells is None:
                return bool(self.keep(confirm=False))
            return bool(self.keep_cells(cells))

        if cells is not None and not any(
            self.window.canvas.frames[frame].layers[layer].initial_image is None
            for frame, layer in cells
        ):
            return False
        if preferences.keep_initial_state_on_cut_folder():
            return keep()
        box = QMessageBox(self.window)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("色塗り前の初期状態"))
        box.setText(tr("読み込んだ絵を、色塗り前の初期状態として保存しますか？"))
        box.setInformativeText(tr(
            "保存すると自動色塗りの精度が上がります。自動塗りは初期状態の線で部分を分ける"
            "ので、塗りかけの色に惑わされません。キャンバス下のボタンで初期状態と見比べる"
            "こともできます。"
        ))
        always = QCheckBox(tr("今後、読み込んだ絵は常に初期状態として保存する"))
        always.setToolTip(tr("環境設定の「一般」から外せます。"))
        box.setCheckBox(always)
        keep_button = box.addButton(tr("保存する"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("保存しない"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep_button)
        box.exec()
        if box.clickedButton() is not keep_button:
            return False
        if always.isChecked():
            preferences.set_keep_initial_state_on_cut_folder(True)
        return keep()

    # -- 比較表示 -------------------------------------------------------------
    def set_compare(self, enabled: bool) -> None:
        """キャンバスに今の絵の代わりに初期状態を表示する（保持していないセルは今の絵）。"""
        canvas = self.window.canvas
        enabled = bool(enabled)
        if enabled and not self.count(self.cells()):
            enabled = False
            self.window.statusBar().showMessage(
                tr("色塗り前の初期状態を保持していないので、比べられません。"), 4000
            )
        canvas.show_initial_state = enabled
        button = getattr(self.window, "compare_initial_button", None)
        if button is not None and button.isChecked() != enabled:
            button.blockSignals(True)
            button.setChecked(enabled)
            button.blockSignals(False)
        if enabled:
            self.window.statusBar().showMessage(
                tr("色塗り前の初期状態を表示しています。もう一度押すと今の絵に戻ります。"), 4000
            )
        canvas.update()
