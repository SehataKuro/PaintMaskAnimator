"""色塗り前の初期状態の保持と比較表示。``window.initial_state`` が持つ。

カットフォルダーから読み込んだままの絵を、セルごとに「色塗り前の初期状態」として
プロジェクトに保持する。自動塗りは初期状態から黒の主線と色トレス線を取り出して
部分を分けるので、塗りかけの色に惑わされず精度が上がる。キャンバス下の比較ボタンで、
今の絵と初期状態を切り替えて見比べられる。

A collaborator rather than a mixin -- see main_window_export.py for why.
"""
from typing import TYPE_CHECKING, List, Tuple

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QCheckBox, QMessageBox

from . import preferences
from .frame_scope import FrameScope, resolve_cells
from .i18n import tr

if TYPE_CHECKING:
    from .main_window import MainWindow

Cell = Tuple[int, int]


class InitialStateController:
    """Owned by ``MainWindow`` as ``window.initial_state``."""

    def __init__(self, window: "MainWindow"):
        self.window = window

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
