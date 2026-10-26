"""完成したセルを絵で見比べて選ぶ、自動塗りの設定画面。"""
from typing import List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QItemSelectionModel, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QListView, QListWidget, QListWidgetItem, QPushButton, QSizePolicy, QSlider,
    QSplitter, QStyledItemDelegate, QStyle, QToolButton, QVBoxLayout, QWidget,
)

from .i18n import tr
from .imaging import qimage_rgba_array

Cell = Tuple[int, int]
TITLE_ROLE = int(Qt.ItemDataRole.UserRole) + 1
FRAME_ROLE = TITLE_ROLE + 1


def _content_rect(image: QImage) -> QRect:
    alpha = qimage_rgba_array(image)[:, :, 3] != 0
    ys, xs = np.flatnonzero(alpha.any(axis=1)), np.flatnonzero(alpha.any(axis=0))
    if not ys.size or not xs.size:
        return image.rect()
    return QRect(int(xs[0]), int(ys[0]), int(xs[-1] - xs[0] + 1), int(ys[-1] - ys[0] + 1))


def _draw_image(painter, bounds, image, source=None):
    """テーマによらず黒い線が見える、明るい透過背景。"""
    painter.fillRect(bounds, QColor("#ffffff"))
    painter.save()
    painter.setClipRect(bounds)
    for y in range(bounds.top(), bounds.bottom() + 1, 12):
        for x in range(bounds.left(), bounds.right() + 1, 12):
            if ((x - bounds.left()) // 12 + (y - bounds.top()) // 12) % 2 == 0:
                painter.fillRect(x, y, 12, 12, QColor("#eeeeee"))
    if not image.isNull():
        source = image.rect() if source is None else source
        size = source.size().scaled(bounds.size() - QSize(12, 12),
                                    Qt.AspectRatioMode.KeepAspectRatio)
        rect = QRectF(0, 0, size.width(), size.height())
        rect.moveCenter(QRectF(bounds).center())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(rect, image, QRectF(source))
    painter.restore()


class _ImagePreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.source = QRect()
        self.setMinimumSize(200, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_image(self, image, source=None):
        self.image = image
        self.source = image.rect() if source is None else source
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        painter = QPainter(self)
        _draw_image(painter, self.rect(), self.image, self.source)
        painter.end()


class _ReferenceGrid(QListWidget):
    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Space and self.currentItem() is not None:
            item = self.currentItem()
            item.setSelected(not item.isSelected())
            event.accept()
            return
        super().keyPressEvent(event)


class _SearchBox(QLineEdit):
    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        # 検索を確定するつもりのEnterで、自動塗りを実行しない。
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)


class _CardDelegate(QStyledItemDelegate):
    def __init__(self, grid):
        super().__init__(grid)
        self.grid = grid

    def sizeHint(self, option, index):  # noqa: N802 - Qt override
        return self.grid.gridSize() - QSize(2, 2)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(4, 4, -4, -4)
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        # QVariant may expose CheckStateRole as an int on older PySide6 builds.
        checked = checked or index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
        accent = option.palette.highlight().color()
        background = option.palette.base().color()
        border = option.palette.mid().color()
        if checked:
            background = option.palette.alternateBase().color()
            border = accent
        elif option.state & (QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_HasFocus):
            border = option.palette.text().color()
        painter.setBrush(background)
        painter.setPen(QPen(border, 2 if checked else 1))
        painter.drawRoundedRect(QRectF(rect), 9, 9)
        image_rect = rect.adjusted(9, 9, -9, -49)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon):
            icon.paint(painter, image_rect, Qt.AlignmentFlag.AlignCenter)
        font = option.font
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(option.palette.text().color())
        painter.drawText(rect.adjusted(10, rect.height() - 43, -10, -23),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         str(index.data(TITLE_ROLE)))
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(option.palette.placeholderText().color())
        painter.drawText(rect.adjusted(10, rect.height() - 22, -10, -3),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         str(index.data(FRAME_ROLE)))
        if checked:
            badge = QRectF(rect.right() - 29, rect.top() + 7, 23, 23)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(badge)
            painter.setPen(QPen(option.palette.highlightedText().color(), 2.2))
            x, y = badge.left(), badge.top()
            painter.drawLine(QPointF(x + 6, y + 12), QPointF(x + 10, y + 16))
            painter.drawLine(QPointF(x + 10, y + 16), QPointF(x + 17, y + 7))
        painter.restore()


class AutoPaintDialog(QDialog):
    def __init__(self, parent, layer_name: str, cells: List[Cell], labels: List[str],
                 current: int, note: str = "", images: Optional[List[QImage]] = None,
                 numbers: Optional[List[Optional[int]]] = None):
        super().__init__(parent)
        self.cells, self.labels = cells, labels
        # 全セルの切り抜き画像を複製せず、元画像と表示する矩形だけを保持する。
        self.images = images or []
        self.image_bounds = [_content_rect(image) for image in self.images]
        self.setWindowTitle(tr("自動で塗る"))
        self.resize(1060, 740)
        self.setMinimumSize(780, 580)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        title = QLabel(tr("見本を選ぶ"))
        font = title.font()
        font.setPointSize(font.pointSize() + 5)
        font.setBold(True)
        title.setFont(font)
        heading.addWidget(title)
        heading.addStretch()
        heading.addWidget(QLabel(tr("レイヤー {name} · {count}セル").format(
            name=layer_name, count=len(cells))))
        layout.addLayout(heading)
        layout.addWidget(QLabel(tr("完成したセルをクリックして選択。見本は何枚でも追加できます。")))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        gallery = QWidget()
        gallery_layout = QVBoxLayout(gallery)
        gallery_layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.search = _SearchBox()
        self.search.setPlaceholderText(tr("セル・コマ番号で検索"))
        self.search.setClearButtonEnabled(True)
        toolbar.addWidget(self.search, 1)
        for text, row in ((tr("先頭へ"), 0), (tr("末尾へ"), len(cells) - 1)):
            button = QPushButton(text)
            button.setAutoDefault(False)
            button.setObjectName("reference-first" if row == 0 else "reference-last")
            button.clicked.connect(lambda _checked=False, row=row: self._inspect(row, reveal=True))
            toolbar.addWidget(button)
        gallery_layout.addLayout(toolbar)
        self.references = _ReferenceGrid()
        self.references.setAccessibleName(tr("見本にするセル"))
        self.references.setViewMode(QListView.ViewMode.IconMode)
        self.references.setResizeMode(QListView.ResizeMode.Adjust)
        self.references.setMovement(QListView.Movement.Static)
        self.references.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.references.setMouseTracking(True)
        self.references.setItemDelegate(_CardDelegate(self.references))
        self.references.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._set_card_size(158)
        for index, label in enumerate(labels):
            item = QListWidgetItem(label, self.references)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            frame_text = tr("コマ {frame}").format(frame=cells[index][0] + 1)
            number = numbers[index] if numbers else None
            item.setData(TITLE_ROLE, tr("セル {number}").format(number=number)
                         if number is not None else frame_text)
            item.setData(FRAME_ROLE, frame_text if number is not None else "")
            item.setToolTip(label)
            item.setCheckState(Qt.CheckState.Checked if index == current else Qt.CheckState.Unchecked)
            if index < len(self.images):
                pixmap = QPixmap(280, 240)
                painter = QPainter(pixmap)
                _draw_image(painter, pixmap.rect(), self.images[index], self.image_bounds[index])
                painter.end()
                item.setIcon(QIcon(pixmap))
            item.setSelected(index == current)
        gallery_layout.addWidget(self.references, 1)
        zoom = QHBoxLayout()
        zoom.addWidget(QLabel(tr("表示サイズ")))
        self.card_size = QSlider(Qt.Orientation.Horizontal)
        self.card_size.setRange(130, 230)
        self.card_size.setValue(158)
        self.card_size.setMaximumWidth(180)
        self.card_size.setAccessibleName(tr("サムネイルの大きさ"))
        self.card_size.valueChanged.connect(self._set_card_size)
        zoom.addWidget(self.card_size)
        zoom.addStretch()
        self.visible_status = QLabel()
        zoom.addWidget(self.visible_status)
        gallery_layout.addLayout(zoom)
        splitter.addWidget(gallery)

        side = QWidget()
        side.setMinimumWidth(240)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 0, 0, 0)
        self.preview_title = QLabel()
        font = self.preview_title.font()
        font.setBold(True)
        self.preview_title.setFont(font)
        side_layout.addWidget(self.preview_title)
        self.preview = _ImagePreview()
        side_layout.addWidget(self.preview, 1)
        self.toggle_reference = QPushButton()
        self.toggle_reference.setAutoDefault(False)
        self.toggle_reference.clicked.connect(self._toggle_current)
        side_layout.addWidget(self.toggle_reference)
        selected_header = QHBoxLayout()
        self.selection_status = QLabel()
        selected_header.addWidget(self.selection_status, 1)
        self.clear_button = QPushButton(tr("すべて解除"))
        self.clear_button.setAutoDefault(False)
        self.clear_button.clicked.connect(self._clear)
        selected_header.addWidget(self.clear_button)
        side_layout.addLayout(selected_header)
        self.selected_refs = QListWidget()
        self.selected_refs.setAccessibleName(tr("選んだ見本"))
        self.selected_refs.setIconSize(QSize(42, 36))
        self.selected_refs.setMinimumHeight(100)
        self.selected_refs.setMaximumHeight(160)
        self.selected_refs.itemClicked.connect(
            lambda item, _column=0: self._inspect(item.data(Qt.ItemDataRole.UserRole), reveal=True))
        side_layout.addWidget(self.selected_refs)
        self.remove_button = QPushButton(tr("選んだ見本から外す"))
        self.remove_button.setAutoDefault(False)
        self.remove_button.clicked.connect(self._remove_selected)
        side_layout.addWidget(self.remove_button)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([710, 300])
        layout.addWidget(splitter, 1)

        if note:
            details = QToolButton()
            details.setText(tr("処理について"))
            details.setCheckable(True)
            details.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            details.setArrowType(Qt.ArrowType.RightArrow)
            hint = QLabel(note)
            hint.setWordWrap(True)
            hint.hide()
            details.toggled.connect(hint.setVisible)
            details.toggled.connect(lambda checked: details.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow))
            layout.addWidget(details)
            layout.addWidget(hint)
        footer = QHBoxLayout()
        self.summary = QLabel()
        footer.addWidget(self.summary, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("キャンセル"))
        self.run_button = buttons.addButton(tr("この見本で塗る"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.run_button.setMinimumSize(150, 34)
        self.run_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)
        self.references.itemSelectionChanged.connect(self._from_selection)
        self.references.itemChanged.connect(self._from_checks)
        self.references.currentRowChanged.connect(self._inspect)
        self.search.textChanged.connect(self._filter)
        self._selection_changed()
        self._inspect(current, reveal=True)
        self._filter("")

    def _set_card_size(self, width):
        self.references.setGridSize(QSize(width + 10, int(width * 0.83) + 60))
        self.references.setIconSize(QSize(width - 16, int(width * 0.83)))

    def chosen(self) -> List[Cell]:
        return [self.cells[i] for i in range(len(self.cells))
                if self.references.item(i).checkState() == Qt.CheckState.Checked]

    def _from_selection(self):
        self.references.blockSignals(True)
        for i in range(len(self.cells)):
            item = self.references.item(i)
            item.setCheckState(Qt.CheckState.Checked if item.isSelected() else Qt.CheckState.Unchecked)
        self.references.blockSignals(False)
        self._selection_changed()

    def _from_checks(self, item):
        self.references.blockSignals(True)
        item.setSelected(item.checkState() == Qt.CheckState.Checked)
        self.references.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self):
        indices = [i for i in range(len(self.cells))
                   if self.references.item(i).checkState() == Qt.CheckState.Checked]
        self.selected_refs.clear()
        for i in indices:
            entry = QListWidgetItem(self.references.item(i).icon(), self.labels[i], self.selected_refs)
            entry.setData(Qt.ItemDataRole.UserRole, i)
        count = len(indices)
        self.selection_status.setText(tr("選んだ見本 {count}枚").format(count=count))
        self.clear_button.setEnabled(count > 0)
        self.remove_button.setEnabled(count > 0)
        self.run_button.setEnabled(0 < count < len(self.cells))
        if count == 0:
            text = tr("完成した見本を1枚以上選んでください。")
        elif count == len(self.cells):
            text = tr("塗るセルを1枚以上残してください。")
        else:
            text = tr("見本 {references}枚 · 塗るセル {targets}枚").format(
                references=count, targets=len(self.cells) - count)
        self.summary.setText(text)
        self._update_toggle()
        self.references.viewport().update()

    def _inspect(self, row, reveal=False):
        if not 0 <= row < len(self.cells):
            return
        self.preview_title.setText(self.labels[row])
        if row < len(self.images):
            self.preview.set_image(self.images[row], self.image_bounds[row])
        else:
            self.preview.set_image(QImage())
        if reveal:
            self.search.clear()
            self.references.setCurrentRow(row, QItemSelectionModel.SelectionFlag.NoUpdate)
            self.references.scrollToItem(self.references.item(row))
        self._update_toggle()

    def _update_toggle(self):
        item = self.references.currentItem()
        checked = item is not None and item.checkState() == Qt.CheckState.Checked
        self.toggle_reference.setText(tr("見本から外す") if checked else tr("見本に追加"))
        self.toggle_reference.setEnabled(item is not None)

    def _toggle_current(self):
        item = self.references.currentItem()
        if item is not None:
            item.setSelected(not item.isSelected())

    def _remove_selected(self):
        item = self.selected_refs.currentItem()
        if item is None and self.selected_refs.count():
            item = self.selected_refs.item(0)
        if item is not None:
            self.references.item(item.data(Qt.ItemDataRole.UserRole)).setSelected(False)

    def _clear(self):
        self.references.clearSelection()

    def _filter(self, text):
        text = text.strip().casefold()
        visible = 0
        for i, label in enumerate(self.labels):
            hidden = text not in label.casefold()
            self.references.item(i).setHidden(hidden)
            visible += not hidden
        self.visible_status.setText(tr("表示 {visible} / {total}セル").format(
            visible=visible, total=len(self.cells)))
