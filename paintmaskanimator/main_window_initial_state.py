"""色塗り前の初期状態の保持と比較表示。``window.initial_state`` が持つ。

カットフォルダーから読み込んだままの絵を、セルごとに「色塗り前の初期状態」として
プロジェクトに保持する。自動塗りは初期状態から黒の主線と色トレス線を取り出して
部分を分けるので、塗りかけの色に惑わされず精度が上がる。キャンバス下の比較ボタンで、
今の絵と初期状態を切り替えて見比べられる。

A collaborator rather than a mixin -- see main_window_export.py for why.
"""
from typing import TYPE_CHECKING, List, Tuple
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QMessageBox

from . import cut_folder, preferences
from .errors import OPERATION_ERRORS, OperationError
from .frame_scope import FrameScope, resolve_cells
from .i18n import tr
from .undo_entries import InitialStateUndo

if TYPE_CHECKING:
    from .main_window import MainWindow

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
        """Match layer names/cel numbers, retaining painted pixels and timing.

        No timesheet is needed. Read and validate every matching image before
        changing anything, then retain only initial-image references for Undo.
        """
        canvas = self.window.canvas
        title = tr("線画カットフォルダーを初期状態として読み込む")
        status = self.window.statusBar()
        status.showMessage(tr("線画の初期状態を読み込んでいます…"), 0)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            contents = cut_folder.scan_cut_folder(path)
            loaded = {}
            cells, archived = [], []
            missing = 0

            def match(layer):
                nonlocal missing
                if not layer.has_content or layer.is_paper or layer.is_draft:
                    return None
                name = layer.name.strip()
                key = name.casefold()
                if key not in contents.cels:
                    key = "".join(
                        "_" if char in cut_folder.INVALID_NAME_CHARS + "/" else char
                        for char in name
                    ).strip(" .").casefold()
                source = contents.cels.get(key, {}).get(layer.sequence_number)
                if source is None:
                    missing += 1
                    return None
                if source not in loaded:
                    image, error = canvas._read_image_file(source)
                    if image is None or image.isNull():
                        raise OperationError(tr("{name} を読み込めません。\n{error}").format(
                            name=Path(source).name, error=error,
                        ))
                    loaded[source] = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                image = loaded[source]
                if image.size() != layer.image.size():
                    raise OperationError(tr(
                        "線画のサイズが現在のセルと一致しません：{name}\n"
                        "線画：{width} × {height}px／現在のセル：{expected_width} × {expected_height}px"
                    ).format(name=Path(source).name, width=image.width(), height=image.height(),
                             expected_width=layer.image.width(), expected_height=layer.image.height()))
                return image

            for fi, frame in enumerate(canvas.frames):
                for li, layer in enumerate(frame.layers):
                    image = match(layer)
                    if image is not None:
                        cells.append((fi, li, image))
            for (li, number), layer in canvas._sequence_archive.items():
                image = match(layer)
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
                "一致するセルがありません。線画フォルダーのレイヤー名とセル番号を確認してください。"
            ))
            return 0

        def snapshot(image):
            return QImage(image) if image is not None else None

        canvas.push_undo(InitialStateUndo(
            [(fi, li, snapshot(canvas.frames[fi].layers[li].initial_image)) for fi, li, _ in cells],
            [(li, number, snapshot(canvas._sequence_archive[(li, number)].initial_image))
             for li, number, _ in archived],
        ))
        for fi, li, image in cells:
            canvas.frames[fi].layers[li].initial_image = QImage(image)
        for li, number, image in archived:
            canvas._sequence_archive[(li, number)].initial_image = QImage(image)
        canvas._playback_frame_cache.clear()
        canvas.changed.emit()
        canvas.update()
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
        for frame, layer in cells:
            cell = canvas.frames[frame].layers[layer]
            # 画素は書き込むまで共有される（コピーの費用はかからない）。
            cell.initial_image = QImage(cell.image)
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
        for frame, layer in cells:
            canvas.frames[frame].layers[layer].initial_image = None
        self.set_compare(False)
        status.showMessage(tr("{count}セルの初期状態を消しました。").format(count=kept), 4000)
        return kept

    def offer_after_import(self) -> bool:
        """カットフォルダーを読み込んだあと、初期状態として保持するか尋ねる。

        環境設定で「常に保持する」なら尋ねずに保持する。保持したら真。
        """
        if preferences.keep_initial_state_on_cut_folder():
            return bool(self.keep(confirm=False))
        box = QMessageBox(self.window)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("色塗り前の初期状態"))
        box.setText(tr("読み込んだ絵を、色塗り前の初期状態として保存しますか？"))
        box.setInformativeText(tr(
            "保存すると自動色塗りの精度が上がります。自動塗りは初期状態の線で部分を分ける"
            "ので、塗りかけの色に惑わされません。キャンバス下のボタンで初期状態と見比べる"
            "こともできます。"
        ))
        always = QCheckBox(tr("今後、カットフォルダーの読み込みは常に初期状態として保存する"))
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
        return bool(self.keep(confirm=False))

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
