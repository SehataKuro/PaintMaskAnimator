import math
from pathlib import Path
from .i18n import tr
from .constants import CTRL_KEY_LABEL, HOLD_ZOOM_KEY_LABEL
from PySide6.QtCore import QEvent, QItemSelectionModel, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from . import icons, theme, tooltip
from .cell_numbering import changed_count, normalization_mapping
from .tween_groups import tween_groups


def _titled_tip(title, detail):
    """アイコンだけのボタン用に、1行目へ操作名を太字で置いたツールチップ。"""
    return tooltip.titled(title, detail)


class LayerListDelegate(QStyledItemDelegate):
    """レイヤー行を描画する。表示切替の目アイコンを固定幅に置き、名前の位置を変えない。

    下書きレイヤーは、行の左端の破線・鉛筆アイコン・「下書き」バッジで示し、
    ひと目で通常のレイヤーと区別できるようにする。
    """

    NAME_ROLE = Qt.ItemDataRole.UserRole + 3
    VISIBLE_ROLE = Qt.ItemDataRole.UserRole + 4
    DRAFT_ROLE = Qt.ItemDataRole.UserRole + 6
    EXPANDED_ROLE = Qt.ItemDataRole.UserRole + 7
    BAND_HEIGHT_ROLE = Qt.ItemDataRole.UserRole + 8
    # 左端から「＞（サムネイルの開閉）」「目（表示切替）」「名前」の順に並べる。
    CHEVRON_WIDTH = 18
    MARKER_WIDTH = 50
    ICON_SIZE = 16

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.palette()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        visible = bool(index.data(self.VISIBLE_ROLE))
        draft = bool(index.data(self.DRAFT_ROLE))
        rect = option.rect
        painter.fillRect(rect, QColor(c["selection"] if selected else c["surface_alt"]))
        if selected:
            # 選択行は薄いアクセント色の面に、左端のアクセント線で示す。
            painter.fillRect(rect.left(), rect.top(), 3, rect.height(), QColor(c["accent"]))
        elif draft:
            painter.setPen(QPen(QColor(c["warning"]), 3, Qt.PenStyle.DashLine))
            painter.drawLine(rect.left() + 1, rect.top() + 2, rect.left() + 1, rect.bottom() - 2)

        dpr = painter.device().devicePixelRatioF() if painter.device() else 1.0
        # サムネイルを開いた行でも、アイコンと名前は上の段にそろえる。
        band_height = int(index.data(self.BAND_HEIGHT_ROLE) or rect.height())
        band_height = min(rect.height(), max(1, band_height))
        rect = rect.adjusted(0, 0, 0, band_height - rect.height())
        expanded = bool(index.data(self.EXPANDED_ROLE))
        chevron_size = 12
        chevron = icons.pixmap(
            "chevron_down" if expanded else "chevron_right",
            chevron_size,
            c["text"] if expanded else c["text_muted"],
            device_pixel_ratio=dpr,
        )
        painter.drawPixmap(
            int(rect.left() + (self.CHEVRON_WIDTH - chevron_size) / 2 + 2),
            int(rect.top() + (rect.height() - chevron_size) / 2),
            chevron,
        )
        eye = icons.pixmap(
            "eye" if visible else "eye_off",
            self.ICON_SIZE,
            c["text"] if visible else c["text_muted"],
            device_pixel_ratio=dpr,
        )
        painter.drawPixmap(
            int(
                rect.left() + self.CHEVRON_WIDTH
                + (self.MARKER_WIDTH - self.CHEVRON_WIDTH - self.ICON_SIZE) / 2
            ),
            int(rect.top() + (rect.height() - self.ICON_SIZE) / 2),
            eye,
        )

        name = index.data(self.NAME_ROLE)
        if name is None:
            name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        name_left = rect.left() + self.MARKER_WIDTH
        right = rect.right() - 6
        font = QFont(option.font)
        if draft:
            # 右端に鉛筆アイコン付きの「下書き」バッジを置く。
            badge_font = QFont(option.font)
            badge_font.setPointSizeF(max(7.0, option.font.pointSizeF() * 0.78))
            badge_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(badge_font)
            label = tr("下書き")
            metrics = painter.fontMetrics()
            badge_h = min(rect.height() - 6, metrics.height() + 4)
            badge_w = metrics.horizontalAdvance(label) + 12 + 12
            badge = QRectF(right - badge_w, rect.top() + (rect.height() - badge_h) / 2, badge_w, badge_h)
            warning = QColor(c["warning"])
            fill = QColor(warning)
            fill.setAlpha(46)
            painter.setPen(QPen(warning, 1))
            painter.setBrush(fill)
            painter.drawRoundedRect(badge, badge_h / 2, badge_h / 2)
            pencil = icons.pixmap("draft", 10, c["warning"], c["warning"], dpr)
            painter.drawPixmap(
                int(badge.left() + 6), int(badge.center().y() - 5), pencil,
            )
            painter.setPen(warning)
            painter.drawText(
                badge.adjusted(18, 0, -5, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            right = badge.left() - 4
            font.setItalic(True)
        painter.setFont(font)
        painter.setPen(QColor(c["text"] if visible else c["text_muted"]))
        name_rect = QRectF(name_left, rect.top(), max(0, right - name_left), rect.height())
        painter.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            painter.fontMetrics().elidedText(
                str(name), Qt.TextElideMode.ElideRight, int(name_rect.width())
            ),
        )

        painter.setPen(QPen(QColor(c["border"]), 1))
        bottom = option.rect.bottom()
        painter.drawLine(rect.left(), bottom, rect.right(), bottom)
        painter.restore()

    def sizeHint(self, option, index):
        # 行の高さはタイムラインの行に合わせて項目ごとに設定される。
        hint = index.data(Qt.ItemDataRole.SizeHintRole)
        height = hint.height() if isinstance(hint, QSize) and hint.height() > 0 else 28
        return QSize(190, height)

    def updateEditorGeometry(self, editor, option, index):
        """表示切替記号を避け、レイヤー名の領域だけを編集欄にする。"""
        band_height = int(index.data(self.BAND_HEIGHT_ROLE) or option.rect.height())
        rect = option.rect.adjusted(
            0, 0, 0, min(0, band_height - option.rect.height())
        )
        editor.setGeometry(rect.adjusted(self.MARKER_WIDTH, 1, -4, -1))

    def eventFilter(self, editor, event):
        # MainWindow の「Enter＝変形確定」より名前編集の確定を優先する。
        if (
            event.type() == QEvent.Type.ShortcutOverride
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            event.accept()
            return True
        return super().eventFilter(editor, event)


class TimelineCellDelegate(QStyledItemDelegate):
    """タイムラインのセルを、クリップ状の帯として描く。

    キーの先頭は番号の箱、続くコマは色の面と横線、空セルは✗と波線、
    右端は伸縮の取っ手で示す。トゥイーン中の区間は紫の面と矢印にし、
    中割りのコマに点を置く。セルの文字（番号・ー・→・○・♦・◆）は
    操作の判定に使うため、描画とは別にそのまま持たせておく。
    """
    STATE_ROLE = Qt.ItemDataRole.UserRole + 20
    TWEEN_SPAN_ROLE = Qt.ItemDataRole.UserRole + 22
    LABEL_ROLE = Qt.ItemDataRole.UserRole + 23
    # セルとセルの間の空き（データ上は未使用）を、空セルとして見せるための
    # (区間の先頭, 長さ, ✗を描くか)。操作の判定には使わない。
    GAP_ROLE = Qt.ItemDataRole.UserRole + 24
    # 確定済みのトゥイーンの帯に含まれるセル（移動・伸縮はさせない）。
    TWEEN_GROUP_ROLE = Qt.ItemDataRole.UserRole + 25
    BAR_INSET = 3
    GRIP_WIDTH = 3

    @staticmethod
    def _state_colors(state_name):
        c = theme.palette()
        key = state_name if state_name in (
            "key", "hold", "sheet_key", "sheet_hold", "blank", "uncreated"
        ) else "uncreated"
        return QColor(c[f"timeline_{key}"]), QColor(c[f"timeline_{key}_text"])

    @staticmethod
    def _span_colors(state_name, tween):
        """(面の色, 箱の色, 線と文字の色) を返す。"""
        c = theme.palette()
        if tween:
            ink = QColor(c["timeline_tween_text"])
            return QColor(c["timeline_tween"]), QColor(c["timeline_tween"]), ink
        prefix = "timeline_sheet_" if str(state_name).startswith("sheet_") else "timeline_"
        return (
            QColor(c[prefix + "hold"]),
            QColor(c[prefix + "key"]),
            QColor(c[prefix + "key_text"]),
        )

    @staticmethod
    def _with_alpha(color, alpha):
        color = QColor(color)
        color.setAlpha(int(alpha))
        return color

    def _draw_grid(self, painter, rect, column):
        c = theme.palette()
        table = self.parent()
        fps = max(1, int(getattr(table, "_fps", 24) or 24))
        border = QColor(c["border"])
        second = (column + 1) % fps == 0
        painter.setPen(QPen(
            self._with_alpha(c["text_muted"], 150) if second else border, 1
        ))
        painter.drawLine(
            QPointF(rect.right() + 0.5, rect.top()),
            QPointF(rect.right() + 0.5, rect.bottom() + 1),
        )
        painter.setPen(QPen(border, 1))
        painter.drawLine(
            QPointF(rect.left(), rect.bottom() + 0.5),
            QPointF(rect.right() + 1, rect.bottom() + 0.5),
        )

    @staticmethod
    def _wave_path(x0, x1, y, amplitude=2.2, period=8.0):
        """左右のセルでつながるよう、ビューポートの x 座標から位相を決める。"""
        path = QPainterPath()
        x = float(x0)
        path.moveTo(x, y + amplitude * math.sin(2 * math.pi * x / period))
        while x < x1:
            x = min(float(x1), x + 1.0)
            path.lineTo(x, y + amplitude * math.sin(2 * math.pi * x / period))
        return path

    def _draw_cross(self, painter, center, size, color):
        painter.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        half = size / 2
        painter.drawLine(
            QPointF(center.x() - half, center.y() - half),
            QPointF(center.x() + half, center.y() + half),
        )
        painter.drawLine(
            QPointF(center.x() - half, center.y() + half),
            QPointF(center.x() + half, center.y() - half),
        )

    def _draw_arrow_head(self, painter, tip_x, y, direction, color):
        size = 5.0
        head = QPolygonF([
            QPointF(tip_x, y),
            QPointF(tip_x - direction * size * 1.2, y - size * 0.8),
            QPointF(tip_x - direction * size * 1.2, y + size * 0.8),
        ])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(head)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_gap(self, painter, rect, column, gap):
        """空きのコマを、空セルと同じ ✗ と波線で描く（取っ手は付けない）。"""
        start, _length, cross = gap
        muted = QColor(theme.palette()["text_muted"])
        table = self.parent()
        # サムネイルを開いた行では、上の段にそろえる。
        band_height = min(
            rect.height(),
            float(getattr(table, "_collapsed_row_height", rect.height())),
        )
        mid_y = rect.top() + band_height / 2
        line_left = rect.left()
        if column == start and cross:
            box = QRectF(
                rect.left() + 1, rect.top() + self.BAR_INSET,
                rect.width() - 2, band_height - 2 * self.BAR_INSET,
            )
            self._draw_cross(
                painter, box.center(), min(box.width(), box.height()) * 0.36, muted
            )
            line_left = box.right() + 1
        line_right = rect.right() + 1
        if line_right - line_left >= 3:
            painter.setPen(QPen(muted, 1.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(self._wave_path(line_left, line_right, mid_y))

    def paint(self, painter, option, index):
        c = theme.palette()
        table = self.parent()
        rect = QRectF(option.rect)
        column = index.column()
        state_name = index.data(self.STATE_ROLE) or "uncreated"
        key_col = index.data(Qt.ItemDataRole.UserRole)
        exposure = index.data(Qt.ItemDataRole.UserRole + 1)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, QColor(c["timeline_uncreated"]))
        self._draw_grid(painter, rect, column)

        gap = index.data(self.GAP_ROLE)
        if key_col is None and gap:
            self._paint_gap(painter, rect, column, gap)
        elif key_col is not None and exposure is not None:
            key_col = int(key_col)
            end_col = key_col + max(1, int(exposure)) - 1
            is_start = column == key_col
            is_end = column == end_col
            tween = index.data(self.TWEEN_SPAN_ROLE)
            blank = state_name == "blank"
            fill, box, ink = self._span_colors(state_name, tween)
            muted = QColor(c["text_muted"])
            # サムネイルを開いた行では、番号の箱と線を上の段に置き、
            # 帯の面だけを行の下まで伸ばす。
            band_height = rect.height()
            if index.row() in getattr(table, "_expanded_rows", ()):
                band_height = min(
                    rect.height(),
                    float(getattr(table, "_collapsed_row_height", rect.height())),
                )
            top = rect.top() + self.BAR_INSET
            bottom = rect.top() + band_height - self.BAR_INSET
            bar_bottom = rect.bottom() + 1 - self.BAR_INSET
            mid_y = (top + bottom) / 2

            # 帯の面。区間の途中では左右をセルの外まで伸ばし、角丸を両端だけにする。
            if not blank:
                bar = QRectF(
                    rect.left() + 1 if is_start else rect.left() - 8,
                    top,
                    0,
                    bar_bottom - top,
                )
                bar.setRight(rect.right() if is_end else rect.right() + 9)
                painter.save()
                painter.setClipRect(rect.adjusted(0, 0, 1, 0))
                painter.setPen(QPen(self._with_alpha(ink, 70), 1))
                painter.setBrush(fill)
                painter.drawRoundedRect(bar.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
                painter.restore()

            line_left = rect.left()
            if is_start:
                box_rect = QRectF(rect.left() + 1, top, rect.width() - 2, bottom - top)
                line_left = box_rect.right() + 1
                label = str(index.data(self.LABEL_ROLE) or "")
                if blank and not label:
                    self._draw_cross(
                        painter, box_rect.center(),
                        min(box_rect.width(), box_rect.height()) * 0.36, muted,
                    )
                elif blank:
                    # 連番の空き番号：点線の箱に薄い番号。
                    painter.setPen(QPen(muted, 1, Qt.PenStyle.DashLine))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(box_rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
                    painter.setPen(muted)
                    painter.drawText(box_rect, Qt.AlignmentFlag.AlignCenter, label)
                else:
                    painter.setPen(QPen(self._with_alpha(ink, 150), 1))
                    painter.setBrush(box)
                    painter.drawRoundedRect(box_rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
                    font = QFont(index.data(Qt.ItemDataRole.FontRole) or option.font)
                    font.setBold(True)
                    painter.setFont(font)
                    painter.setPen(ink)
                    painter.drawText(
                        box_rect.adjusted(2, 0, -2, 0),
                        Qt.AlignmentFlag.AlignCenter,
                        painter.fontMetrics().elidedText(
                            label, Qt.TextElideMode.ElideRight,
                            int(box_rect.width() - 4),
                        ),
                    )

            grip_right = rect.right() - 2
            line_right = rect.right() + 1
            if is_end and not is_start:
                line_right = grip_right - self.GRIP_WIDTH - 3
            if tween == "forward" and is_end and not is_start:
                line_right -= 8

            if line_right - line_left >= 3:
                if blank:
                    painter.setPen(QPen(muted, 1.4))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawPath(self._wave_path(line_left, line_right, mid_y))
                elif tween:
                    painter.setPen(QPen(ink, 1.6))
                    reverse_head = tween == "reverse" and column == key_col + 1
                    start_x = line_left + (7 if reverse_head else 0)
                    if line_right > start_x:
                        painter.drawLine(QPointF(start_x, mid_y), QPointF(line_right, mid_y))
                else:
                    painter.setPen(QPen(self._with_alpha(ink, 200), 2))
                    painter.drawLine(QPointF(line_left, mid_y), QPointF(line_right, mid_y))

            if tween:
                if tween == "forward" and is_end and not is_start:
                    self._draw_arrow_head(painter, line_right + 7, mid_y, 1, ink)
                elif tween == "reverse" and column == key_col + 1:
                    # 逆生成は、キーの箱の右隣から左向きの矢印にする。
                    self._draw_arrow_head(painter, rect.left() + 1, mid_y, -1, ink)
                if not is_start and not is_end:
                    # 中割りのコマ。再生位置のコマは赤で塗る。
                    current = column == getattr(table, "_playhead_column", None)
                    painter.setPen(QPen(QColor(c["error"]) if current else ink, 1.3))
                    painter.setBrush(QColor(c["error"]) if current else QColor(c["surface"]))
                    radius = 3.2 if current else 2.6
                    painter.drawEllipse(QPointF(rect.center().x(), mid_y), radius, radius)
                    painter.setBrush(Qt.BrushStyle.NoBrush)

            # 確定済みのトゥイーンの帯は伸縮できないので、取っ手を描かない。
            if is_end and not is_start and not index.data(self.TWEEN_GROUP_ROLE):
                grip = QRectF(
                    grip_right - self.GRIP_WIDTH, mid_y - 6, self.GRIP_WIDTH, 12
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(muted if blank else self._with_alpha(ink, 210))
                painter.drawRoundedRect(grip, 1.5, 1.5)
                painter.setBrush(Qt.BrushStyle.NoBrush)
        elif text:
            painter.setPen(QColor(c["timeline_uncreated_text"]))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

        if index.row() in getattr(table, "_draft_rows", ()):
            hatch = QColor(c["warning"])
            hatch.setAlpha(70)
            painter.fillRect(rect, QBrush(hatch, Qt.BrushStyle.BDiagPattern))
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(QPen(QColor(c["error"]), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 4, 4)
        painter.restore()


class TimelineRuler(QHeaderView):
    """コマ番号の見出し。毎コマの目盛り、秒の区切り、現在コマの札を描く。

    見出しの文字（6コマおきの番号）はモデルのものをそのまま使う。
    クリック・ドラッグでそのコマへ移動する（列の選択はしない）。
    """

    frameScrubbed = Signal(int)

    def _scrub(self, event):
        count = self.count()
        if count <= 0:
            return
        index = self.logicalIndexAt(int(event.position().x()))
        if index < 0:
            index = 0 if event.position().x() < 0 else count - 1
        self.frameScrubbed.emit(int(index))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._scrub(event)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._scrub(event)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintSection(self, painter, rect, logicalIndex):
        c = theme.palette()
        table = self.parent()
        fps = max(1, int(getattr(table, "_fps", 24) or 24))
        rect = QRectF(rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(rect, QColor(c["surface_alt"]))
        painter.setPen(QPen(QColor(c["border"]), 1))
        painter.drawLine(
            QPointF(rect.left(), rect.bottom() + 0.5),
            QPointF(rect.right() + 1, rect.bottom() + 0.5),
        )
        second = logicalIndex % fps == 0
        tick = QColor(c["text_muted"] if second else c["border"])
        tick_height = rect.height() * (0.5 if second else 0.22)
        painter.setPen(QPen(tick, 1))
        painter.drawLine(
            QPointF(rect.left() + 0.5, rect.bottom() + 1 - tick_height),
            QPointF(rect.left() + 0.5, rect.bottom() + 1),
        )
        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() * 0.85))
        painter.setFont(font)
        playhead = getattr(table, "_playhead_column", None)
        if playhead is not None and int(playhead) == logicalIndex:
            badge = QRectF(rect.left() + 1, rect.top() + 3, rect.width() - 2, rect.height() - 3)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c["error"]))
            painter.drawRoundedRect(badge, 4, 4)
            painter.fillRect(
                QRectF(badge.left(), badge.bottom() - 4, badge.width(), 4),
                QColor(c["error"]),
            )
            painter.setPen(QColor("#ffffff"))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(logicalIndex + 1))
        else:
            model = self.model()
            label = (
                model.headerData(logicalIndex, Qt.Orientation.Horizontal)
                if model is not None else None
            )
            if label:
                font.setBold(second)
                painter.setFont(font)
                painter.setPen(QColor(c["text"] if second else c["text_muted"]))
                painter.drawText(
                    rect.adjusted(3, 1, 0, -int(tick_height * 0.6)),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    str(label),
                )
        painter.restore()


class TimelineTable(QTableWidget):
    cellMoveRequested = Signal(int, int, int, int)
    cellCopyRequested = Signal(int, int, int, int)
    sequenceRecallRequested = Signal(int, int, int)
    multiCellMoveRequested = Signal(object, int, int, int, int)
    blankFrameRequested = Signal(int, int)
    exposureResizeRequested = Signal(int, int, int, str)
    headerFrameRequested = Signal(int)
    tweenRequested = Signal(int, int)
    tweenMeshRequested = Signal(int, int)
    tweenCancelRequested = Signal()
    tweenEditRequested = Signal(int, int)
    tweenReleaseRequested = Signal(int, int)
    addFrameRequested = Signal(bool)
    extendExposureRequested = Signal(int, int, int)

    END_HANDLE_ROLE = Qt.ItemDataRole.UserRole + 2
    START_HANDLE_ROLE = Qt.ItemDataRole.UserRole + 4
    TWEEN_PENDING_ROLE = Qt.ItemDataRole.UserRole + 21
    HANDLE_HIT_WIDTH = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        # Mirrors the owning TimelineWidget's frame count; the widget writes it
        # (``self.table._real_frame_count = ...``) and cell-hit tests read it.
        self._real_frame_count = 1
        self._drag_source = None
        self._drag_preview_dest = None
        self._duplicate_drag = False
        self._sequence_numbers_by_row = {}
        self._group_drag_cells = None
        self._group_drag_anchor = None
        self._group_drag_dest = None
        self._resize_source = None
        self._resize_preview_column = None
        # 秒の区切り線と再生ヘッドの描画に使う（TimelineWidget が書き込む）。
        self._fps = 24
        self._playhead_column: int | None = None
        # サムネイルを開いた行と、その行のキーの絵（TimelineWidget が書き込む）。
        self._collapsed_row_height = 28
        self._expanded_rows = set()
        self._thumb_sources = {}
        self._thumb_cache = {}
        self.setMouseTracking(True)
        self.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        ruler = TimelineRuler(Qt.Orientation.Horizontal, self)
        self.setHorizontalHeader(ruler)
        ruler.frameScrubbed.connect(self._ruler_scrubbed)
        self.horizontalHeader().setMouseTracking(True)
        self.horizontalHeader().installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.horizontalHeader():
            if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove):
                buttons = event.buttons() if hasattr(event, "buttons") else Qt.MouseButton.NoButton
                pressed = (
                    event.type() == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton
                )
                dragging = (
                    event.type() == QEvent.Type.MouseMove
                    and bool(buttons & Qt.MouseButton.LeftButton)
                )
                if pressed or dragging:
                    column = self.horizontalHeader().logicalIndexAt(
                        int(event.position().x())
                    )
                    if 0 <= column < self._real_frame_count:
                        self.headerFrameRequested.emit(column)
                        return True
        return super().eventFilter(watched, event)

    def _ruler_scrubbed(self, column):
        # 範囲の外（まだコマのない列）も指せる。受け手がコマを延ばす。
        column = max(0, min(int(column), self.columnCount() - 1))
        self.headerFrameRequested.emit(column)

    def _edge_source_at(self, point):
        index = self.indexAt(point)
        if not index.isValid():
            return None
        item = self.item(index.row(), index.column())
        if item is None or item.data(TimelineCellDelegate.TWEEN_GROUP_ROLE):
            return None
        key_col = item.data(Qt.ItemDataRole.UserRole)
        exposure = item.data(Qt.ItemDataRole.UserRole + 1)
        if key_col is None or exposure is None:
            return None
        key_col = int(key_col)
        exposure = max(1, int(exposure))
        end_col = key_col + exposure - 1
        rect = self.visualRect(index)
        x = point.x()
        if index.column() == key_col and x <= rect.left() + self.HANDLE_HIT_WIDTH:
            return (index.row(), key_col, "left")
        if index.column() == end_col and x >= rect.right() - self.HANDLE_HIT_WIDTH:
            return (index.row(), key_col, "right")
        return None

    def _column_from_x(self, x):
        column = self.columnAt(int(x))
        if column >= 0:
            return column
        count = self.columnCount()
        if count <= 0:
            return 0
        if x < 0:
            return 0
        last = count - 1
        last_right = self.columnViewportPosition(last) + self.columnWidth(last)
        if x >= last_right:
            width = max(1, self.columnWidth(last))
            return last + 1 + int((x - last_right) // width)
        return last

    def _set_cell_drag_cursor(self):
        """ALT複製中だけ、右下に＋の付いたコピーカーソルを表示する。"""
        self.setCursor(
            Qt.CursorShape.DragCopyCursor
            if self._duplicate_drag
            else Qt.CursorShape.ClosedHandCursor
        )

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        self._drag_source = None
        self._drag_preview_dest = None
        self._duplicate_drag = False
        self._group_drag_cells = None
        self._group_drag_anchor = None
        self._group_drag_dest = None
        self._resize_source = None
        self._resize_preview_column = None

        if event.button() == Qt.MouseButton.LeftButton and index.isValid():
            modifiers = event.modifiers()
            self._duplicate_drag = bool(
                modifiers & Qt.KeyboardModifier.AltModifier
            )
            multi_select = bool(
                modifiers
                & (
                    Qt.KeyboardModifier.ShiftModifier
                    | Qt.KeyboardModifier.ControlModifier
                )
            )

            selected = {
                (selected_index.row(), selected_index.column())
                for selected_index in self.selectedIndexes()
                if selected_index.isValid()
            }
            clicked = (index.row(), index.column())

            # 複数選択済みの範囲をそのままドラッグする。
            if (
                not multi_select
                and clicked in selected
                and len(selected) > 1
            ):
                self._group_drag_cells = tuple(sorted(selected))
                self._group_drag_anchor = clicked
                self._group_drag_dest = clicked
                self.setCurrentCell(
                    index.row(),
                    index.column(),
                    QItemSelectionModel.SelectionFlag.NoUpdate,
                )
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return

            edge_source = (
                None
                if multi_select
                else self._edge_source_at(
                    event.position().toPoint()
                )
            )
            if edge_source is not None:
                self._resize_source = edge_source
                self._resize_preview_column = edge_source[1]
                self.setCurrentCell(edge_source[0], edge_source[1])
                self.cellClicked.emit(edge_source[0], edge_source[1])
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                event.accept()
                return

            item = self.item(index.row(), index.column())
            if (
                not multi_select
                and item
                and not item.data(TimelineCellDelegate.TWEEN_GROUP_ROLE)
                and (
                    item.data(TimelineCellDelegate.STATE_ROLE)
                    in ("key", "sheet_key")
                    or item.text() == "○"
                )
            ):
                self._drag_source = (index.row(), index.column())
                self._drag_preview_dest = self._drag_source
                self.setCurrentCell(index.row(), index.column())
                self.cellClicked.emit(index.row(), index.column())
                self._set_cell_drag_cursor()
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._group_drag_cells is not None:
            index = self.indexAt(event.position().toPoint())
            if index.isValid():
                self._group_drag_dest = (
                    index.row(),
                    index.column(),
                )
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.viewport().update()
            event.accept()
            return
        if self._resize_source is not None:
            self._resize_preview_column = self._column_from_x(event.position().x())
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            self.viewport().update()
            event.accept()
            return
        if self._drag_source is not None:
            index = self.indexAt(event.position().toPoint())
            if index.isValid():
                self._drag_preview_dest = (index.row(), index.column())
            self._set_cell_drag_cursor()
            self.viewport().update()
            event.accept()
            return
        edge = self._edge_source_at(event.position().toPoint())
        if edge is not None:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            index = self.indexAt(event.position().toPoint())
            item = self.item(index.row(), index.column()) if index.isValid() else None
            self.setCursor(
                Qt.CursorShape.OpenHandCursor
                if item and not item.data(TimelineCellDelegate.TWEEN_GROUP_ROLE) and (
                    item.data(TimelineCellDelegate.STATE_ROLE)
                    in ("key", "sheet_key")
                    or item.text() == "○"
                )
                else Qt.CursorShape.ArrowCursor
            )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if (
            self._resize_source is None
            and self._drag_source is None
            and self._group_drag_cells is None
        ):
            self.unsetCursor()
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        if self._resize_source is not None and self._resize_preview_column is not None:
            row, key_col, edge = self._resize_source
            item = self.item(row, key_col)
            exposure = max(
                1,
                int(item.data(Qt.ItemDataRole.UserRole + 1) or 1)
                if item is not None else 1,
            )
            fixed_end = key_col + exposure - 1
            if edge == "left":
                start_col = max(0, min(self._resize_preview_column, fixed_end))
                end_col = fixed_end
            else:
                start_col = key_col
                end_col = max(key_col, self._resize_preview_column)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 215, 0, 110))
            for column in range(start_col, min(end_col + 1, self.columnCount())):
                index = self.model().index(row, column)
                if index.isValid():
                    painter.drawRect(self.visualRect(index).adjusted(1, 1, -1, -1))
        if (
            self._group_drag_cells is not None
            and self._group_drag_anchor is not None
            and self._group_drag_dest is not None
        ):
            row_delta = (
                self._group_drag_dest[0]
                - self._group_drag_anchor[0]
            )
            column_delta = (
                self._group_drag_dest[1]
                - self._group_drag_anchor[1]
            )
            painter.setPen(QPen(QColor(229, 57, 53), 2))
            painter.setBrush(QColor(255, 215, 0, 65))
            for source_row, source_column in self._group_drag_cells:
                row = source_row + row_delta
                column = source_column + column_delta
                index = self.model().index(row, column)
                if index.isValid():
                    painter.drawRect(
                        self.visualRect(index).adjusted(1, 1, -1, -1)
                    )

        if self._drag_source is not None and self._drag_preview_dest is not None:
            row, column = self._drag_preview_dest
            index = self.model().index(row, column)
            if index.isValid():
                rect = self.visualRect(index)
                painter.setPen(QPen(QColor(229, 57, 53), 3))
                painter.drawLine(rect.left(), rect.top() + 1, rect.left(), rect.bottom() - 1)
        self._paint_thumbnails(painter)
        self._paint_playhead(painter)
        painter.end()

    def _thumbnail(self, image, width, height, ratio):
        """縮小した絵をキャッシュする。絵が描き変わると cacheKey も変わる。"""
        key = (image.cacheKey(), int(width), int(height), round(ratio, 2))
        pixmap = self._thumb_cache.get(key)
        if pixmap is None:
            if len(self._thumb_cache) > 512:
                self._thumb_cache.clear()
            scaled = image.scaled(
                max(1, int(width * ratio)), max(1, int(height * ratio)),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pixmap = QPixmap.fromImage(scaled)
            pixmap.setDevicePixelRatio(ratio)
            self._thumb_cache[key] = pixmap
        return pixmap

    def _paint_thumbnails(self, painter):
        """開いた行のキーのセルに、区間をまたいでサムネイルを描く。"""
        if not self._thumb_sources:
            return
        c = theme.palette()
        ratio = self.devicePixelRatioF()
        viewport_width = self.viewport().width()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for (row, column), (image, exposure) in self._thumb_sources.items():
            if row not in self._expanded_rows or image is None or image.isNull():
                continue
            left = self.columnViewportPosition(column)
            span_width = self.columnWidth(column) * max(1, int(exposure))
            if left > viewport_width or left + span_width < 0:
                continue
            top = self.rowViewportPosition(row)
            if top < -self.rowHeight(row) or top > self.viewport().height():
                continue
            area_top = top + self._collapsed_row_height - 2
            area_height = self.rowHeight(row) - self._collapsed_row_height - 3
            # 右端の取っ手の分をあける（1コマだけのセルには取っ手がない）。
            area_width = span_width - (14 if int(exposure) > 1 else 7)
            if area_height < 10 or area_width < 10:
                continue
            aspect = image.width() / float(max(1, image.height()))
            height = float(area_height)
            width = height * aspect
            if width > area_width:
                width = float(area_width)
                height = width / aspect
            frame = QRectF(left + 4, area_top, width, height)
            painter.setPen(QPen(QColor(c["border"]), 1))
            painter.setBrush(QColor("#ffffff"))
            painter.drawRoundedRect(frame.adjusted(-0.5, -0.5, 0.5, 0.5), 3, 3)
            pixmap = self._thumbnail(image, width, height, ratio)
            size = pixmap.deviceIndependentSize()
            painter.drawPixmap(
                QPointF(
                    frame.left() + (frame.width() - size.width()) / 2,
                    frame.top() + (frame.height() - size.height()) / 2,
                ),
                pixmap,
            )
        painter.restore()

    def _paint_playhead(self, painter):
        """現在のコマに、全レイヤーを貫く赤い縦線を引く。"""
        column = self._playhead_column
        if column is None or not (0 <= int(column) < self.columnCount()):
            return
        x = self.columnViewportPosition(int(column))
        if x < -self.columnWidth(int(column)) or x > self.viewport().width():
            return
        # コマの中心に線を引くと番号や点に重なるので、現在のコマの列を
        # 薄く色付けし、左右の境目に細い線を引く。
        color = QColor(theme.palette()["error"])
        width = self.columnWidth(int(column))
        height = self.viewport().height()
        tint = QColor(color)
        tint.setAlpha(26)
        painter.fillRect(QRectF(x, 0, width, height), tint)
        edge = QColor(color)
        edge.setAlpha(170)
        painter.setPen(QPen(edge, 1))
        painter.drawLine(QPointF(x + 0.5, 0), QPointF(x + 0.5, height))
        painter.drawLine(
            QPointF(x + width - 0.5, 0), QPointF(x + width - 0.5, height)
        )

    def mouseReleaseEvent(self, event):
        group_cells = self._group_drag_cells
        group_anchor = self._group_drag_anchor
        group_destination = self._group_drag_dest
        self._group_drag_cells = None
        self._group_drag_anchor = None
        self._group_drag_dest = None

        if (
            group_cells
            and group_anchor
            and group_destination
            and event.button() == Qt.MouseButton.LeftButton
        ):
            if group_destination != group_anchor:
                self.multiCellMoveRequested.emit(
                    group_cells,
                    int(group_anchor[0]),
                    int(group_anchor[1]),
                    int(group_destination[0]),
                    int(group_destination[1]),
                )
            self.viewport().update()
            self.unsetCursor()
            event.accept()
            return

        resize_source = self._resize_source
        resize_column = self._resize_preview_column
        self._resize_source = None
        self._resize_preview_column = None
        self.viewport().update()
        if resize_source and event.button() == Qt.MouseButton.LeftButton:
            row = self.rowAt(int(event.position().y()))
            if row < 0:
                row = resize_source[0]
            if row == resize_source[0]:
                boundary = (
                    self._column_from_x(event.position().x())
                    if resize_column is None else int(resize_column)
                )
                self.exposureResizeRequested.emit(
                    resize_source[0], resize_source[1], boundary, resize_source[2]
                )
                self.unsetCursor()
                event.accept()
                return

        source = self._drag_source
        destination = self._drag_preview_dest
        duplicate_drag = self._duplicate_drag
        self._drag_source = None
        self._drag_preview_dest = None
        self._duplicate_drag = False
        self.viewport().update()
        if source and destination and event.button() == Qt.MouseButton.LeftButton:
            if destination != source:
                signal = (
                    self.cellCopyRequested
                    if duplicate_drag
                    else self.cellMoveRequested
                )
                signal.emit(
                    source[0], source[1], destination[0], destination[1]
                )
            self.unsetCursor()
            event.accept()
            return
        self.unsetCursor()
        super().mouseReleaseEvent(event)


    def _tween_group_item(self, index):
        if not index.isValid():
            return None
        item = self.item(index.row(), index.column())
        if item is None or not item.data(TimelineCellDelegate.TWEEN_GROUP_ROLE):
            return None
        return item

    def mouseDoubleClickEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        item = self._tween_group_item(index)
        if item is not None and event.button() == Qt.MouseButton.LeftButton:
            self.tweenEditRequested.emit(
                int(index.row()), int(item.data(Qt.ItemDataRole.UserRole))
            )
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        index = self.indexAt(event.pos())
        item = (
            self.item(index.row(), index.column())
            if index.isValid() else None
        )

        menu = QMenu(self)
        add_menu = menu.addMenu(tr("コマを増やす"))
        action_add_blank = add_menu.addAction(
            tr("空フレームを追加")
        )
        action_extend = add_menu.addAction(
            tr("表示コマを1コマ伸ばす")
        )
        key_column = (
            item.data(Qt.ItemDataRole.UserRole)
            if item is not None else None
        )
        exposure = (
            item.data(Qt.ItemDataRole.UserRole + 1)
            if item is not None else None
        )
        action_extend.setEnabled(
            index.isValid()
            and key_column is not None
            and exposure is not None
        )

        action_cancel = None
        action_free = None
        action_mesh = None
        action_tween_edit = None
        action_tween_release = None
        recall_actions = {}
        if index.isValid():
            numbers = self._sequence_numbers_by_row.get(int(index.row()), ())
            if numbers:
                recall_menu = menu.addMenu(tr("連番の番号を呼び出す"))
                for number in numbers:
                    recall_actions[recall_menu.addAction(str(number))] = int(number)

        item_tween_pending = bool(
            item is not None
            and item.data(
                TimelineTable.TWEEN_PENDING_ROLE
            )
        )
        if self._tween_group_item(index) is not None:
            menu.addSeparator()
            action_tween_edit = menu.addAction(tr("トゥイーンの形を直す"))
            action_tween_release = menu.addAction(
                tr("トゥイーンを解除（中割りを通常のセルにする）")
            )
        elif (
            item is not None
            and (
                item.text() in ("→", "♦", "◆")
                or item_tween_pending
            )
        ):
            menu.addSeparator()
            if (
                item_tween_pending
                or item.text() in ("♦", "◆")
            ):
                action_cancel = menu.addAction(
                    tr("トゥイーンをキャンセル")
                )
            else:
                key_col = item.data(Qt.ItemDataRole.UserRole)
                exposure = item.data(
                    Qt.ItemDataRole.UserRole + 1
                )
                tween_menu = menu.addMenu(
                    tr("トゥイーンを有効にする")
                )
                action_free = tween_menu.addAction(tr("自由変形"))
                action_mesh = tween_menu.addAction(tr("メッシュ変形"))
                tween_enabled = (
                    key_col is not None
                    and exposure is not None
                    and int(exposure) >= 2
                )
                action_free.setEnabled(tween_enabled)
                action_mesh.setEnabled(tween_enabled)

        chosen = menu.exec(event.globalPos())
        if chosen is action_add_blank:
            if index.isValid():
                self.blankFrameRequested.emit(
                    int(index.row()),
                    int(index.column()),
                )
        elif chosen is action_extend and key_column is not None and exposure is not None:
            self.extendExposureRequested.emit(
                int(index.row()),
                int(key_column),
                int(exposure),
            )
        elif action_tween_edit is not None and chosen is action_tween_edit and item is not None:
            self.tweenEditRequested.emit(
                int(index.row()), int(item.data(Qt.ItemDataRole.UserRole))
            )
        elif (
            action_tween_release is not None
            and chosen is action_tween_release
            and item is not None
        ):
            self.tweenReleaseRequested.emit(
                int(index.row()), int(item.data(Qt.ItemDataRole.UserRole))
            )
        elif action_cancel is not None and chosen is action_cancel:
            self.tweenCancelRequested.emit()
        elif action_free is not None and chosen is action_free and item is not None:
            self.tweenRequested.emit(
                int(index.row()),
                int(item.data(Qt.ItemDataRole.UserRole)),
            )
        elif action_mesh is not None and chosen is action_mesh and item is not None:
            self.tweenMeshRequested.emit(
                int(index.row()),
                int(item.data(Qt.ItemDataRole.UserRole)),
            )
        elif chosen in recall_actions:
            self.sequenceRecallRequested.emit(
                int(index.row()), int(index.column()), recall_actions[chosen]
            )
        event.accept()


class TimelineWidget(QWidget):
    frameSelected=Signal(int,int); addFrameRequested=Signal(bool); deleteFrameRequested=Signal(); playRequested=Signal(bool)
    onionChanged=Signal(bool); onionAllLayersChanged=Signal(bool); onionPopupToggled=Signal(bool); durationChanged=Signal(int); previousRequested=Signal(); nextRequested=Signal(); previousKeyRequested=Signal(); nextKeyRequested=Signal()
    layerSelected=Signal(int); layerVisibilityChanged=Signal(int,bool); layerOpacityChanged=Signal(int,float); layerNameChanged=Signal(int,str); layerMoveRequested=Signal(object,int); addLayerRequested=Signal(); deleteLayerRequested=Signal()
    duplicateLayersRequested=Signal(object); mergeLayersRequested=Signal(object); deleteLayersRequested=Signal(object)
    cellMoveRequested=Signal(int,int,int,int)
    cellCopyRequested=Signal(int,int,int,int)
    sequenceRecallRequested=Signal(int,int,int)
    multiCellMoveRequested=Signal(object,int,int,int,int)
    blankFrameRequested=Signal(int,int)
    exposureResizeRequested=Signal(int,int,int,str)
    tweenRequested=Signal(int,int)
    tweenMeshRequested=Signal(int,int)
    tweenCancelRequested=Signal()
    tweenEditRequested=Signal(int,int)
    tweenReleaseRequested=Signal(int,int)
    timeRemapPasteRequested=Signal()
    timeRemapFileDropped=Signal(str)
    extendExposureRequested=Signal(int,int,int)
    timelineModeChanged=Signal(str)
    normalizeNumbersRequested=Signal(object)
    toggleDraftLayersRequested=Signal(object)
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.sequence_archive = {}
        self._timeline_zoom_scale = 1.0
        self._timeline_base_cell_width = 36
        self._timeline_base_row_height = 28
        # サムネイルを開いているレイヤー（レイヤー番号）。
        self._expanded_layers = set()
        v=QVBoxLayout(self)
        v.setContentsMargins(2,2,2,2)
        v.setSpacing(2)
        c=QHBoxLayout()
        c.setContentsMargins(0,0,0,0)
        c.setSpacing(2)

        self.add_blank = QPushButton()
        self.add_exposure = QPushButton()
        self.add_exposure.setToolTip(_titled_tip(
            tr("コマを伸ばす"),
            tr("現在のキーフレーム／空フレームを1コマ伸ばします。"),
        ))
        self.delete=QPushButton()
        self.time_remap_paste=QPushButton()
        self.time_remap_paste.setToolTip(_titled_tip(
            tr("リマップを貼り付け"),
            tr("AEまたはToeiDigitalTimeSheetのコピー情報を"
            "タイムシートへ貼り付けます。XDTSはタイムラインへ"
            "ドラッグ＆ドロップできます。"),
        ))
        # 番号の順番とタイムラインの順番がずれたら、ずれている数をバッジで出す。
        self.normalize_button = QPushButton(tr("順番で正規化"))
        self.normalize_button.setToolTip(_titled_tip(
            tr("タイムラインの順番で正規化"),
            tr("左から最初に出てくる順に番号を振り直します。"
               "実行前に、振り直す番号の一覧を確認できます。"),
        ))
        self.normalize_button.setFixedHeight(24)
        self.normalize_button.setIconSize(QSize(15, 15))
        self.normalize_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.normalize_badge = QLabel()
        self.normalize_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.normalize_badge.setMinimumWidth(18)
        self.normalize_badge.setFixedHeight(16)
        self.normalize_badge.hide()
        self._normalize_pending = 0
        self.prev=QPushButton()
        self.prev.setToolTip(tr("前のフレーム（1）"))
        self.next=QPushButton()
        self.next.setToolTip(tr("次のフレーム（2）"))
        self.prev_key=QPushButton()
        self.prev_key.setToolTip(tr("前のコマ（A）"))
        self.next_key=QPushButton()
        self.next_key.setToolTip(tr("次のコマ（S）"))
        self.play=QPushButton()
        self.play.setToolTip(tr("再生／停止"))
        self.play.setCheckable(True)

        self.onion_all_layers=QCheckBox(tr("すべてのレイヤー"))
        self.onion_all_layers.setChecked(True)
        # オニオンスキンは設定ボタンと隣り合う切り替えチップにする。
        self.onion=QPushButton(tr("オニオンスキン"))
        self.onion.setCheckable(True)
        self.onion.setToolTip(tr("オニオンスキン表示のON／OFF"))
        self.onion.setProperty("toggleChip", True)
        self.onion_settings=QPushButton()
        self.onion_settings.setCheckable(True)
        self.onion_settings.setCursor(
            Qt.CursorShape.PointingHandCursor
        )
        self.onion_settings.setToolTip(
            tr("クリックでオニオンスキン設定を開き、"
            "再クリックで閉じます。")
        )
        self.duration=QSpinBox(); self.duration.setRange(1,240); self.duration.setSuffix(tr(" コマ"))
        self.duration.hide()
        self.fps=QSpinBox(); self.fps.setRange(1,60); self.fps.setValue(24); self.fps.setSuffix(" fps")
        self.fps.valueChanged.connect(self._fps_changed)
        self.fps.setFixedSize(72,24)

        # 文字のボタンをアイコンにし、編集・移動・再生・オニオンの
        # まとまりごとに区切る。
        self._icon_buttons = {
            self.add_blank: "frame_blank",
            self.add_exposure: "frame_extend",
            self.delete: "trash",
            self.time_remap_paste: "remap",
            self.prev: "prev_frame",
            self.prev_key: "prev_key",
            self.next_key: "next_key",
            self.next: "next_frame",
            self.onion_settings: "settings",
        }
        for button in self._icon_buttons:
            button.setFixedSize(26, 24)
            button.setIconSize(QSize(16, 16))
            button.setProperty("iconButton", True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.play.setFixedSize(30, 24)
        self.play.setIconSize(QSize(14, 14))
        self.play.setProperty("accentButton", True)
        self.play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.onion.setFixedHeight(24)
        self.onion.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.onion.setIconSize(QSize(15, 15))

        self.mode_tabs = QTabBar()
        self.mode_tabs.addTab(tr("シート"))
        self.mode_tabs.addTab(tr("連番"))
        self.mode_tabs.setCurrentIndex(0)
        self.mode_tabs.setExpanding(False)
        self.mode_tabs.setDrawBase(False)
        self.mode_tabs.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.mode_tabs.setToolTip(
            tr("連番：左から順番に自動採番／"
            "シート：タイムシートの絵番号を保持")
        )
        self.timeline_mode = "sheet"
        self.mode_tabs.currentChanged.connect(
            self._timeline_mode_tab_changed
        )
        self._update_timeline_mode_tab_style()

        self.onion_all_layers.hide()
        self.layer_opacity_text = QLabel(tr("不透明"))
        self.layer_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.layer_opacity_slider.setRange(0, 100)
        self.layer_opacity_slider.setValue(100)
        self.layer_opacity_slider.setFixedWidth(84)
        self.layer_opacity_slider.setToolTip(
            tr("選択レイヤーの表示不透明度です。画像の色データ自体は変更しません。")
        )
        self.layer_opacity_value = QLabel("100%")
        self.layer_opacity_value.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.layer_opacity_value.setFixedWidth(34)
        self.layer_opacity_value.setToolTip(
            tr("現在選択しているレイヤーの表示不透明度")
        )

        self._toolbar_separators = []

        def add_group(*widgets, spacing=1):
            if c.count():
                separator = QFrame()
                separator.setFrameShape(QFrame.Shape.VLine)
                separator.setFixedSize(9, 16)
                self._toolbar_separators.append(separator)
                c.addWidget(separator)
            for widget in widgets:
                c.addWidget(widget)
                if spacing > 1 and widget is not widgets[-1]:
                    c.addSpacing(spacing)

        c.setSpacing(1)
        c.addWidget(self.mode_tabs)
        add_group(
            self.layer_opacity_text, self.layer_opacity_slider,
            self.layer_opacity_value, spacing=4,
        )
        add_group(
            self.add_blank, self.add_exposure, self.delete,
            self.time_remap_paste,
        )
        add_group(self.normalize_button, self.normalize_badge, spacing=3)

        # 再生まわりは中央にまとめ、現在のコマを数字で常に見せる。
        self.frame_counter = QLabel()
        self.frame_counter.setToolTip(tr("現在のコマ / 全体のコマ数"))
        self.frame_counter.setMinimumWidth(66)
        self.frame_counter.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._frame_counter_values = (1, 1)
        c.addStretch()
        for widget in (self.prev, self.prev_key, self.play, self.next_key, self.next):
            c.addWidget(widget)
        c.addSpacing(6)
        c.addWidget(self.frame_counter)
        c.addStretch()
        c.addWidget(self.onion)
        c.addSpacing(2)
        c.addWidget(self.onion_settings)
        c.addSpacing(8)
        compact_hint = QLabel(tr("Shift/{ctrl}：複数選択").format(ctrl=CTRL_KEY_LABEL))
        self.compact_hint = compact_hint
        compact_hint.setToolTip(
            tr("ドラッグ・Shift＋クリック：複数選択／"
            "選択範囲をそのままドラッグ：まとめて移動／"
            "番号・✗の箱：移動／左右端：伸縮／"
            "Space：ハンド／{zoom}：拡大縮小").format(zoom=HOLD_ZOOM_KEY_LABEL)
        )
        c.addWidget(compact_hint)
        c.addSpacing(6)
        c.addWidget(self.fps)
        c.setContentsMargins(2, 1, 2, 3)
        v.addLayout(c)
        body=QHBoxLayout(); body.setContentsMargins(0,0,0,0); body.setSpacing(0)
        self.setMinimumHeight(82)
        self.layer_list=QListWidget()
        self.layer_list.setFixedWidth(190)
        self.layer_list.setMinimumHeight(70)
        self.layer_list.setFrameShape(QListWidget.Shape.NoFrame)
        self.layer_list.setItemDelegate(LayerListDelegate(self.layer_list))
        self.layer_list.setContentsMargins(0,0,0,0)
        self.layer_list.setSpacing(0)
        self.layer_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.layer_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.layer_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ContiguousSelection
        )
        self.layer_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.layer_list.customContextMenuRequested.connect(
            self._show_layer_context_menu
        )
        self.layer_list.viewport().installEventFilter(self)
        self._active_layer_index = 0

        # レイヤー名とタイムラインを同一面として見せる。
        # タイムライン見出しと同じ高さのヘッダー内に追加・削除ボタンを置き、
        # その直下からレイヤー名とタイムライン行を隙間なく隣接させる。
        layer_box=QVBoxLayout()
        layer_box.setContentsMargins(0,0,0,0)
        layer_box.setSpacing(0)
        self.layer_header_spacer = QWidget()
        self.layer_header_spacer.setFixedHeight(25)
        self.layer_header_spacer.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        layer_header_layout = QHBoxLayout(self.layer_header_spacer)
        layer_header_layout.setContentsMargins(2,0,2,0)
        layer_header_layout.setSpacing(2)
        self.expand_all_button = QPushButton()
        self.expand_all_button.setFixedSize(18, 22)
        self.expand_all_button.setIconSize(QSize(12, 12))
        self.expand_all_button.setProperty("iconButton", True)
        self.expand_all_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.expand_all_button.setToolTip(tr("すべてのレイヤーのサムネイルを開く／閉じる"))
        self.expand_all_button.clicked.connect(self._toggle_all_expanded)
        layer_header_layout.addWidget(self.expand_all_button)
        layer_header_layout.addWidget(QLabel(tr("レイヤー")))
        layer_header_layout.addStretch()
        self.layer_add=QPushButton()
        self.layer_add.setToolTip(tr("レイヤーを追加"))
        self.layer_del=QPushButton()
        self.layer_del.setToolTip(tr("レイヤーを削除"))
        for button in (self.layer_add, self.layer_del):
            button.setFixedSize(24,22)
            button.setIconSize(QSize(14, 14))
            button.setProperty("iconButton", True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setContentsMargins(0,0,0,0)
        layer_header_layout.addWidget(self.layer_add)
        layer_header_layout.addWidget(self.layer_del)
        layer_box.addWidget(self.layer_header_spacer)
        layer_box.addWidget(self.layer_list,1)

        layer_widget=QWidget()
        layer_widget.setContentsMargins(0,0,0,0)
        layer_widget.setLayout(layer_box)
        self.layer_widget = layer_widget
        body.addWidget(layer_widget,0)

        self.table=TimelineTable()
        self.table.setAcceptDrops(False)
        self.table.viewport().setAcceptDrops(False)
        self.table.setItemDelegate(TimelineCellDelegate(self.table))
        self.table.setRowCount(1)
        self.table.setColumnCount(1)
        self.table.setMinimumHeight(70)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.table.setFrameShape(QTableWidget.Shape.NoFrame)
        self.table.setContentsMargins(0,0,0,0)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionsMovable(False)
        self.table.verticalHeader().setSectionsMovable(False)
        self.table.verticalHeader().setVisible(False)
        # 格子線はセルの描画で引く（帯の面が格子で途切れないように）。
        self.table.setShowGrid(False)

        # どちらを縦スクロールしてもレイヤー名とタイムライン行を同時に動かす。
        self.table.verticalScrollBar().valueChanged.connect(
            self.layer_list.verticalScrollBar().setValue
        )
        self.layer_list.verticalScrollBar().valueChanged.connect(
            self.table.verticalScrollBar().setValue
        )

        self._sequence_frame_by_cell = {}
        self.table.cellClicked.connect(self._select_table_cell)
        self._real_frame_count = 1
        self.table._real_frame_count = 1
        self.table.headerFrameRequested.connect(self._scrub_header_frame)
        self.table.cellMoveRequested.connect(self.cellMoveRequested)
        self.table.cellCopyRequested.connect(self.cellCopyRequested)
        self.table.sequenceRecallRequested.connect(self.sequenceRecallRequested)
        self.table.multiCellMoveRequested.connect(
            self.multiCellMoveRequested
        )
        self.table.blankFrameRequested.connect(
            self.blankFrameRequested
        )
        self.table.exposureResizeRequested.connect(self.exposureResizeRequested)
        self.table.tweenRequested.connect(self.tweenRequested)
        self.table.tweenMeshRequested.connect(self.tweenMeshRequested)
        self.table.tweenCancelRequested.connect(self.tweenCancelRequested)
        self.table.tweenEditRequested.connect(self.tweenEditRequested)
        self.table.tweenReleaseRequested.connect(self.tweenReleaseRequested)
        self.table.addFrameRequested.connect(
            self.addFrameRequested
        )
        self.table.extendExposureRequested.connect(
            self.extendExposureRequested
        )
        body.addWidget(self.table,1)
        v.addLayout(body)
        self.layer_list.currentRowChanged.connect(self._layer_selected)
        self.layer_list.model().rowsMoved.connect(self._layer_rows_moved)
        self.layer_list.itemChanged.connect(self._layer_item_changed)
        self.layer_list.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
        )
        self.layer_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.layer_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.layer_add.clicked.connect(self.addLayerRequested); self.layer_del.clicked.connect(self.deleteLayerRequested)
        self.add_blank.clicked.connect(
            self._create_blank_at_current
        )
        self.add_exposure.clicked.connect(
            self._extend_current_exposure
        )
        self.delete.clicked.connect(self.deleteFrameRequested)
        self.normalize_button.clicked.connect(self._request_normalize)
        self.time_remap_paste.clicked.connect(
            self.timeRemapPasteRequested
        )
        self.prev.clicked.connect(self.previousRequested); self.next.clicked.connect(self.nextRequested)
        self.prev_key.clicked.connect(self.previousKeyRequested); self.next_key.clicked.connect(self.nextKeyRequested)
        self.play.toggled.connect(self._play)
        self.onion_all_layers.toggled.connect(self.onionAllLayersChanged)
        self.onion.toggled.connect(self._on_onion_toggled)
        self.onion_settings.toggled.connect(
            self.onionPopupToggled
        )
        self.duration.valueChanged.connect(self.durationChanged)
        self.layer_opacity_slider.valueChanged.connect(
            self._layer_opacity_slider_changed
        )
        self._sync_timeline_mode_controls()
        self.apply_theme()

    def _apply_icons(self):
        for button, name in self._icon_buttons.items():
            button.setIcon(icons.icon(name))
        c = theme.palette()
        play_icon = QIcon()
        for size in (14, 16, 28, 32):
            play_icon.addPixmap(icons.pixmap("play", size, c["accent_text"]))
            play_icon.addPixmap(
                icons.pixmap("pause", size, c["accent_text"]),
                QIcon.Mode.Normal, QIcon.State.On,
            )
        self.play.setIcon(play_icon)
        self.onion.setIcon(icons.icon("onion"))
        self.normalize_button.setIcon(
            icons.icon(
                "sort_numbers",
                theme.palette()["warning"] if self._normalize_pending else None,
            )
        )
        self.layer_add.setIcon(icons.icon("plus"))
        self.expand_all_button.setIcon(icons.icon(
            "chevron_down" if self._all_expanded() else "chevron_right"
        ))
        self.layer_del.setIcon(icons.icon("minus"))

    def apply_theme(self):
        """Palette-aware styling for the timeline surfaces.

        Keeps the timeline's design (compact rows, red selected-cell frame,
        amber/blue mode tabs) but sources neutral colours from the active theme
        so it reads well in both light and dark, with a touch more polish.
        """
        c = theme.palette()
        self._apply_icons()
        for separator in self._toolbar_separators:
            separator.setStyleSheet(
                "QFrame{color:%s;margin:0 4px;}" % c["border"]
            )
        panel = "border:0px;margin:0px;padding:0px;background:%s;" % c["surface_alt"]
        self.layer_widget.setStyleSheet(panel)
        self.layer_header_spacer.setStyleSheet(panel)
        self.layer_list.setStyleSheet(
            "QListWidget{border:0px;margin:0px;padding:0px;background:%s;outline:0;}"
            "QListWidget::item{border:0px;padding:2px 0px;background:%s;color:%s;}"
            "QListWidget::item:selected{background:%s;color:%s;}"
            % (
                c["surface_alt"], c["surface_alt"], c["text"],
                c["selection"], c["text"],
            )
        )
        self.table.setStyleSheet(
            "QTableWidget{border:0px;margin:0px;padding:0px;"
            "gridline-color:%s;background:%s;}"
            "QHeaderView{border:0px;margin:0px;padding:0px;}"
            "QHeaderView::section{background:%s;border:0px;"
            "border-bottom:1px solid %s;border-right:1px solid %s;"
            "padding:2px;color:%s;}"
            "QTableWidget::item{border:0px;color:%s;}"
            "QTableWidget::item:selected{background:transparent;color:%s;"
            "border:2px solid %s;}"
            % (
                c["border"], c["surface"],
                c["surface_alt"], c["border"], c["border"], c["text"],
                c["text"], c["text"], c["error"],
            )
        )
        self.compact_hint.setStyleSheet(
            "font-size:9px;color:%s;" % c["text_muted"]
        )
        self.layer_opacity_value.setStyleSheet(
            "font-size:10px;color:%s;padding:0px;" % c["text_muted"]
        )
        self.layer_opacity_text.setStyleSheet(
            "font-size:11px;color:%s;padding:0px;" % c["text_muted"]
        )
        self._update_timeline_mode_tab_style()
        self._update_normalize_badge_style()
        self.frame_counter.setStyleSheet(
            "QLabel{font-family:Menlo,Consolas,monospace;font-size:11px;}"
        )
        self._set_frame_counter()
        self.table.viewport().update()
        self.layer_list.viewport().update()

    def _update_normalize_badge_style(self):
        c = theme.palette()
        warning = QColor(c["warning"])
        self.normalize_badge.setStyleSheet(
            "QLabel{background:%s;color:%s;border-radius:8px;"
            "padding:0 5px;font-size:10px;font-weight:600;}"
            % (warning.name(), theme._contrast_text(warning.name()))
        )

    def _set_frame_counter(self, current=None, total=None):
        old_current, old_total = self._frame_counter_values
        current = old_current if current is None else max(1, int(current))
        total = old_total if total is None else max(1, int(total))
        self._frame_counter_values = (current, total)
        width = max(3, len(str(total)))
        c = theme.palette()
        self.frame_counter.setText(
            "<span style='color:%s;font-weight:600'>%s</span>"
            "<span style='color:%s'> / %s</span>"
            % (
                c["error"], str(current).zfill(width),
                c["text_muted"], str(total).zfill(width),
            )
        )

    def set_normalize_pending(self, count):
        """正規化で番号が変わる絵の数を、ボタン横のバッジに出す。"""
        count = max(0, int(count))
        if count == self._normalize_pending:
            return
        self._normalize_pending = count
        self.normalize_badge.setText(str(count))
        self.normalize_badge.setVisible(
            bool(count) and self.timeline_mode == "sheet"
        )
        self.normalize_badge.setToolTip(
            tr("タイムラインの順番とずれている番号：{count}個").format(count=count)
        )
        self._apply_icons()

    def _request_normalize(self):
        rows = tuple(self.selected_layer_rows())
        if not rows and self.layer_list.currentRow() >= 0:
            rows = (self.layer_list.currentRow(),)
        self.normalizeNumbersRequested.emit(rows)

    def _timeline_mode_tab_changed(self, index):
        self.timeline_mode = (
            "sequence" if int(index) == 1 else "sheet"
        )
        self._update_timeline_mode_tab_style()
        self._sync_timeline_mode_controls()
        self.timelineModeChanged.emit(self.timeline_mode)

    def _sync_timeline_mode_controls(self):
        sequence_mode = self.timeline_mode == "sequence"
        self.add_exposure.setVisible(not sequence_mode)
        self.normalize_button.setVisible(not sequence_mode)
        self.normalize_badge.setVisible(
            not sequence_mode and bool(self._normalize_pending)
        )
        self.add_blank.setToolTip(_titled_tip(
            tr("空フレームを追加"),
            tr("選択番号の直後へ、新しい空の番号画像を追加します。")
            if sequence_mode
            else
            tr("番号・✗の先頭セルでは、直後へ同じ長さの空セル（✗）を挿入。"
            "続きの部分では、選択位置から後ろを空セルへ分割します。"),
        ))
        self.delete.setToolTip(_titled_tip(
            tr("コマを削除"),
            tr("選択番号を削除し、シート側の対応セルを未使用にします。")
            if sequence_mode
            else tr("現在の表示コマを1コマ削除します。"),
        ))

    def _update_timeline_mode_tab_style(self):
        """シート／連番を角丸の切り替えボタン（セグメント）として描く。

        選択中の色はセルと同じ系統（シート＝黄、連番＝青）にして、
        どちらのモードかをタイムラインの色と対応させる。
        """
        c = theme.palette()
        if self.timeline_mode == "sheet":
            selected_background = c["timeline_sheet_key"]
            selected_foreground = c["timeline_sheet_key_text"]
        else:
            selected_background = c["timeline_key"]
            selected_foreground = c["timeline_key_text"]
        self.mode_tabs.setStyleSheet(
            "QTabBar{background:%(surface_alt)s;border:1px solid %(border)s;"
            "border-radius:7px;}"
            "QTabBar::tab{background:transparent;color:%(text_muted)s;"
            "border:none;margin:2px;padding:2px 12px;min-width:34px;"
            "border-radius:5px;font-size:11px;}"
            "QTabBar::tab:hover:!selected{background:%(hover)s;color:%(text)s;}"
            % c
            + "QTabBar::tab:selected{"
            f"background:{selected_background};color:{selected_foreground};"
            "font-weight:600;}"
        )

    def set_timeline_mode(self, mode):
        mode = "sheet" if str(mode) == "sheet" else "sequence"
        self.timeline_mode = mode
        target_index = 1 if mode == "sequence" else 0
        if self.mode_tabs.currentIndex() != target_index:
            self.mode_tabs.blockSignals(True)
            self.mode_tabs.setCurrentIndex(target_index)
            self.mode_tabs.blockSignals(False)
        self._update_timeline_mode_tab_style()
        self._sync_timeline_mode_controls()

    def expanded_row_height(self, row_height=None):
        """サムネイルを開いた行の高さ（上の段＋サムネイルの段）。"""
        if row_height is None:
            row_height = self.timeline_cell_metrics()[1]
        return int(row_height) + max(34, int(round(row_height * 1.4)))

    def _layer_index_for_row(self, visual_row):
        return self.layer_list.count() - 1 - int(visual_row)

    def _all_expanded(self):
        count = self.layer_list.count()
        return bool(count) and all(
            layer_index in self._expanded_layers for layer_index in range(count)
        )

    def _apply_row_heights(self):
        """開閉の状態に合わせて、レイヤー名と表の行の高さをそろえる。"""
        row_height = self.timeline_cell_metrics()[1]
        expanded_height = self.expanded_row_height(row_height)
        expanded_rows = set()
        for visual_row in range(self.layer_list.count()):
            expanded = self._layer_index_for_row(visual_row) in self._expanded_layers
            height = expanded_height if expanded else row_height
            if expanded:
                expanded_rows.add(visual_row)
            item = self.layer_list.item(visual_row)
            if item is not None:
                item.setData(LayerListDelegate.EXPANDED_ROLE, expanded)
                item.setData(LayerListDelegate.BAND_HEIGHT_ROLE, row_height)
                item.setSizeHint(QSize(0, height))
            if visual_row < self.table.rowCount():
                self.table.setRowHeight(visual_row, height)
        self.table._collapsed_row_height = row_height
        self.table._expanded_rows = expanded_rows
        self.layer_list.doItemsLayout()
        self.layer_list.viewport().update()
        self.table.viewport().update()
        self._apply_icons()

    def toggle_layer_expanded(self, visual_row):
        layer_index = self._layer_index_for_row(visual_row)
        if not (0 <= layer_index < self.layer_list.count()):
            return
        if layer_index in self._expanded_layers:
            self._expanded_layers.discard(layer_index)
        else:
            self._expanded_layers.add(layer_index)
        self._apply_row_heights()

    def _toggle_all_expanded(self):
        if self._all_expanded():
            self._expanded_layers.clear()
        else:
            self._expanded_layers = set(range(self.layer_list.count()))
        self._apply_row_heights()

    def timeline_cell_metrics(self):
        scale = max(0.5, min(3.0, float(self._timeline_zoom_scale)))
        return (
            max(18, int(round(self._timeline_base_cell_width * scale))),
            max(20, int(round(self._timeline_base_row_height * scale))),
        )

    def set_timeline_zoom_scale(self, scale, anchor_x=None, anchor_y=None):
        old_width, old_height = self.timeline_cell_metrics()
        new_scale = max(0.5, min(3.0, float(scale)))
        if abs(new_scale - self._timeline_zoom_scale) < 1e-6:
            return
        anchor_x = float(
            self.table.viewport().width() / 2
            if anchor_x is None else anchor_x
        )
        anchor_y = float(
            self.table.viewport().height() / 2
            if anchor_y is None else anchor_y
        )
        horizontal = self.table.horizontalScrollBar()
        vertical = self.table.verticalScrollBar()
        horizontal_position = (
            horizontal.value() + anchor_x
        ) / float(max(1, old_width))
        vertical_position = (
            vertical.value() + anchor_y
        ) / float(max(1, old_height))

        self._timeline_zoom_scale = new_scale
        new_width, new_height = self.timeline_cell_metrics()
        self.table.horizontalHeader().setDefaultSectionSize(new_width)
        self.table.verticalHeader().setDefaultSectionSize(new_height)
        for column in range(self.table.columnCount()):
            self.table.setColumnWidth(column, new_width)
        self._apply_row_heights()

        horizontal.setValue(
            int(round(horizontal_position * new_width - anchor_x))
        )
        vertical.setValue(
            int(round(vertical_position * new_height - anchor_y))
        )
        self.table.viewport().update()
        self.layer_list.viewport().update()

    def adjust_timeline_zoom(self, factor, anchor_x=None, anchor_y=None):
        self.set_timeline_zoom_scale(
            self._timeline_zoom_scale * float(factor),
            anchor_x,
            anchor_y,
        )

    def _create_blank_at_current(self):
        """現在のタイムライン位置へ明示的な○を作る。"""
        row = self.table.currentRow()
        column = self.table.currentColumn()

        if row < 0:
            active = int(
                getattr(self, "_active_layer_index", 0)
            )
            row = max(
                0,
                self.table.rowCount() - 1 - active,
            )
        if column < 0:
            column = 0

        if (
            row >= self.table.rowCount()
            or column >= self.table.columnCount()
        ):
            return

        self.blankFrameRequested.emit(
            int(row),
            int(column),
        )

    def _extend_current_exposure(self):
        if self.timeline_mode == "sequence":
            return
        row = self.table.currentRow()
        column = self.table.currentColumn()
        item = (
            self.table.item(row, column)
            if row >= 0 and column >= 0
            else None
        )
        if item is None:
            return
        key_column = item.data(Qt.ItemDataRole.UserRole)
        exposure = item.data(Qt.ItemDataRole.UserRole + 1)
        if key_column is None or exposure is None:
            return
        self.extendExposureRequested.emit(
            int(row),
            int(key_column),
            max(1, int(exposure)),
        )

    def _fps_changed(self, value):
        self.table._fps = max(1, int(value))
        self.table.viewport().update()
        self.table.horizontalHeader().viewport().update()

    def _on_onion_toggled(self, checked):
        self.onionChanged.emit(bool(checked))

    @staticmethod
    def _time_remap_drop_path(mime_data):
        if mime_data is None or not mime_data.hasUrls():
            return None
        for url in mime_data.urls():
            path = str(url.toLocalFile() or "")
            if Path(path).suffix.lower() in (".xdts", ".xtds"):
                return path
        return None

    def dragEnterEvent(self, event):
        if self._time_remap_drop_path(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._time_remap_drop_path(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        path = self._time_remap_drop_path(event.mimeData())
        if path:
            self.timeRemapFileDropped.emit(path)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def eventFilter(self, watched, event):
        if (
            watched is self.layer_list.viewport()
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            point = event.position().toPoint()
            item = self.layer_list.itemAt(point)
            if item is not None and point.x() <= LayerListDelegate.CHEVRON_WIDTH:
                self.toggle_layer_expanded(self.layer_list.row(item))
                event.accept()
                return True
            if item is not None and point.x() <= LayerListDelegate.MARKER_WIDTH:
                row = self.layer_list.row(item)
                visible = bool(
                    item.data(Qt.ItemDataRole.UserRole + 4)
                )
                visible = not visible
                item.setData(Qt.ItemDataRole.UserRole + 4, visible)
                self.layer_list.setCurrentItem(item)
                self.layer_list.viewport().update()
                self.layerVisibilityChanged.emit(row, visible)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _layer_opacity_slider_changed(self, value):
        value = max(0, min(100, int(value)))
        self.layer_opacity_value.setText(f"{value}%")
        row = self.layer_list.currentRow()
        if row >= 0:
            self.layerOpacityChanged.emit(row, value / 100.0)

    def _sync_layer_opacity_slider(self, opacity):
        value = max(0, min(100, int(round(float(opacity) * 100))))
        self.layer_opacity_slider.blockSignals(True)
        self.layer_opacity_slider.setValue(value)
        self.layer_opacity_slider.blockSignals(False)
        self.layer_opacity_value.setText(f"{value}%")

    def _scrub_header_frame(self, column):
        # シートでは範囲外のコマへも移動できる（連番は番号の範囲内だけ）。
        limit = (
            self._real_frame_count
            if self.timeline_mode == "sequence"
            else self.table.columnCount()
        )
        if not (0 <= int(column) < limit):
            return
        row = self.table.currentRow()
        if row < 0:
            row = 0
        self.table.setCurrentCell(row, int(column))
        self.frameSelected.emit(int(column), int(row))

    def _select_table_cell(self, row, column):
        if not (0 <= int(column) < self._real_frame_count):
            return
        # 連番の表示列から内部フレームへの変換は受け手側で一度だけ行う。
        self.frameSelected.emit(int(column), int(row))

    def selected_layer_rows(self):
        return sorted({
            index.row() for index in self.layer_list.selectedIndexes()
            if index.isValid()
        })

    def _rows_all_draft(self, rows):
        """選択した全レイヤー行が下書きレイヤーなら True。"""
        checked = False
        for row in rows:
            item = self.layer_list.item(int(row))
            if item is None:
                return False
            checked = True
            if not bool(item.data(Qt.ItemDataRole.UserRole + 6)):
                return False
        return checked

    def _show_layer_context_menu(self, position):
        item = self.layer_list.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self.layer_list.clearSelection()
            item.setSelected(True)
            self.layer_list.setCurrentItem(item)
        rows = tuple(self.selected_layer_rows())
        if not rows:
            return
        menu = QMenu(self)
        action_duplicate = menu.addAction(tr("複製"))
        action_merge = menu.addAction(tr("結合"))
        action_merge.setEnabled(len(rows) >= 2)
        action_delete = menu.addAction(tr("削除"))
        menu.addSeparator()
        action_draft = menu.addAction(tr("下書きレイヤー"))
        action_draft.setCheckable(True)
        action_draft.setChecked(bool(self._rows_all_draft(rows)))
        action_normalize = None
        if self.timeline_mode == "sheet":
            menu.addSeparator()
            action_normalize = menu.addAction(tr("番号の正規化"))
        chosen = menu.exec(self.layer_list.viewport().mapToGlobal(position))
        if chosen is action_duplicate:
            self.duplicateLayersRequested.emit(rows)
        elif chosen is action_merge:
            self.mergeLayersRequested.emit(rows)
        elif chosen is action_delete:
            self.deleteLayersRequested.emit(rows)
        elif chosen is action_draft:
            self.toggleDraftLayersRequested.emit(rows)
        elif action_normalize is not None and chosen is action_normalize:
            self.normalizeNumbersRequested.emit(rows)

    def rename_selected_layer(self):
        row = self.layer_list.currentRow()
        item = self.layer_list.item(row) if row >= 0 else None
        if item is None:
            return
        self.layer_list.setCurrentItem(item)
        self.layer_list.scrollToItem(item)
        self.layer_list.editItem(item)

    def _move_timeline_visual_rows(self, start, end, final_row):
        """レイヤー名のドラッグ中、対応するタイムライン行も同時に移動する。"""
        row_count = self.table.rowCount()
        if not (0 <= start <= end < row_count):
            return
        block_count = end - start + 1
        final_row = max(0, min(int(final_row), row_count - block_count))
        if final_row == start:
            return

        current_column = self.table.currentColumn()
        moved_rows = []
        self.table.blockSignals(True)
        try:
            for row in range(start, end + 1):
                moved_rows.append([
                    self.table.takeItem(row, column)
                    for column in range(self.table.columnCount())
                ])

            for row in range(end, start - 1, -1):
                self.table.removeRow(row)

            for offset, items in enumerate(moved_rows):
                target_row = final_row + offset
                self.table.insertRow(target_row)
                for column, item in enumerate(items):
                    if item is not None:
                        self.table.setItem(target_row, column, item)

            if current_column >= 0:
                self.table.setCurrentCell(final_row, current_column)
        finally:
            self.table.blockSignals(False)
        self._apply_row_heights()

    def _layer_rows_moved(self, parent, start, end, destination, destination_row):
        block_count = end - start + 1
        final_row = (
            destination_row - block_count
            if destination_row > start
            else destination_row
        )
        final_row = max(
            0,
            min(final_row, self.layer_list.count() - block_count),
        )
        if final_row == start:
            return

        # 名前だけ先に動いてタイムラインが残る状態を作らない。
        self._move_timeline_visual_rows(start, end, final_row)
        self.layerMoveRequested.emit(
            tuple(range(start, end + 1)),
            int(final_row),
        )

    def _layer_selected(self, row):
        item = self.layer_list.item(row) if row >= 0 else None
        if item is not None:
            opacity = item.data(Qt.ItemDataRole.UserRole + 5)
            if opacity is not None:
                self._sync_layer_opacity_slider(float(opacity))
        self.layerSelected.emit(row)

    def _layer_item_changed(self, item):
        row = self.layer_list.row(item)
        if row < 0:
            return
        previous_name = str(
            item.data(Qt.ItemDataRole.UserRole + 3) or ""
        ).strip()
        entered_name = item.text()
        current_name = entered_name.strip() or previous_name or "Layer"

        # 表示用の名前と比較用の保持値を揃える。ここで発生する itemChanged は
        # 再帰させず、実際に名前が変わった場合だけ外側へ通知する。
        was_blocked = self.layer_list.blockSignals(True)
        try:
            if entered_name != current_name:
                item.setText(current_name)
            item.setData(Qt.ItemDataRole.UserRole + 3, current_name)
        finally:
            self.layer_list.blockSignals(was_blocked)

        if current_name != previous_name:
            self.layerNameChanged.emit(row, current_name)
    def _play(self,on): self.playRequested.emit(on)
    @staticmethod
    def timeline_span_at(frames, layer_index, column):
        """表示中のコマ区間を返す。

        content は●、blank は○、None は未使用セル。
        """
        if (
            column < 0
            or column >= len(frames)
            or layer_index < 0
        ):
            return None, None, 0

        for start in range(column, -1, -1):
            if layer_index >= len(frames[start].layers):
                continue
            layer = frames[start].layers[layer_index]
            if getattr(layer, "sequence_only", False):
                continue
            is_content = bool(layer.has_content)
            is_blank = bool(
                getattr(layer, "is_blank_key", False)
            )
            if not (is_content or is_blank):
                continue

            exposure = max(1, int(layer.exposure))
            if column < start + exposure:
                return (
                    "content" if is_content else "blank",
                    start,
                    exposure,
                )
            # 直近の明示コマが届いていない場合、それ以前の露出は
            # この明示コマを越えて表示しない。
            break

        return None, None, 0

    @classmethod
    def timeline_gaps(cls, frames, layer_index):
        """セルとセルの間（と先頭）の空きコマを {コマ: (先頭, 長さ, ✗を描くか)} で返す。

        データ上は未使用のコマだが、書き出しでは空セルと同じ × になるので、
        タイムラインでも空セルとして見せる。直前が空セルなら、その続きとして
        ✗ は描かない。最後のセルより後ろの空きは対象にしない。
        """
        kinds = [
            cls.timeline_span_at(frames, layer_index, column)[0]
            for column in range(len(frames))
        ]
        covered = [column for column, kind in enumerate(kinds) if kind is not None]
        if not covered:
            return {}
        last = covered[-1]
        gaps = {}
        column = 0
        while column < last:
            if kinds[column] is not None:
                column += 1
                continue
            start = column
            while column < last and kinds[column] is None:
                column += 1
            length = column - start
            cross = not (start > 0 and kinds[start - 1] == "blank")
            for gap_column in range(start, column):
                gaps[gap_column] = (start, length, cross)
        return gaps

    def refresh(self, frames, current, active, tween_pending=None):
        if not frames:
            return

        selected_cells = {
            (index.row(), index.column())
            for index in self.table.selectedIndexes()
            if index.isValid()
        }
        previous_current = (
            self.table.currentRow(),
            self.table.currentColumn(),
        )

        current = max(0, min(current, len(frames) - 1))
        pending_tween = tween_pending if isinstance(tween_pending, dict) else {}
        pending_layer = int(pending_tween.get("layer_index", -1))
        pending_key = int(pending_tween.get("key_col", -1))
        pending_end = int(pending_tween.get("end_col", -1))
        pending_reverse = bool(
            pending_tween.get(
                "reverse_generation",
                False,
            )
        )
        rows = len(frames[current].layers)
        if self.timeline_mode == "sequence":
            real_frame_count = max(
                (
                    max({
                        int(layer.sequence_number)
                        for frame in frames
                        for layer in [frame.layers[layer_index]]
                        if layer.sequence_number is not None
                        and (layer.has_content or layer.is_blank_key)
                    } or {1})
                    for layer_index in range(len(frames[current].layers))
                ),
                default=1,
            )
            real_frame_count = max(
                real_frame_count,
                max(
                    (
                        int(number)
                        for _layer_index, number in self.sequence_archive
                    ),
                    default=1,
                ),
            )
            real_frame_count = max(1, real_frame_count)
        else:
            real_frame_count = len(frames)
        self._real_frame_count = real_frame_count
        self.table._real_frame_count = real_frame_count
        self.table._fps = max(1, int(self.fps.value()))
        self.table._playhead_column = (
            None if self.timeline_mode == "sequence" else int(current)
        )
        self._set_frame_counter(int(current) + 1, len(frames))
        cell_width, row_height = self.timeline_cell_metrics()
        visible_cols = max(
            1, self.table.viewport().width() // max(1, cell_width) + 4
        )
        cols = max(real_frame_count + 24, visible_cols)
        self.table.blockSignals(True)
        self.table._sequence_numbers_by_row = {}
        self._sequence_frame_by_cell = {}
        self.table.setRowCount(rows)
        self.table.setColumnCount(cols)
        self.table.setHorizontalHeaderLabels([str(i + 1) if i % 6 == 0 else "" for i in range(cols)])
        self.table.horizontalHeader().setVisible(True)
        self.table.horizontalHeader().setFixedHeight(25)
        self.layer_header_spacer.setFixedHeight(25)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setVerticalHeaderLabels(["" for _ in range(rows)])
        self.table.verticalHeader().setDefaultSectionSize(row_height)
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        self._expanded_layers = {
            layer_index for layer_index in self._expanded_layers
            if 0 <= layer_index < rows
        }
        self.table._thumb_sources = {}
        self._active_layer_index = int(active)
        # 下書きレイヤーの行は、タイムラインのセルにも斜線を重ねて示す。
        layers_top_first = list(reversed(frames[current].layers))
        self.table._draft_rows = {
            visual_row for visual_row, layer in enumerate(layers_top_first)
            if bool(getattr(layer, "is_draft", False))
        }
        for layer in reversed(frames[current].layers):
            is_draft = bool(getattr(layer, "is_draft", False))
            item = QListWidgetItem(layer.name)
            item.setFlags(
                (
                    item.flags()
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsDragEnabled
                    | Qt.ItemFlag.ItemIsEditable
                )
                & ~Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setData(Qt.ItemDataRole.UserRole + 2, False)
            item.setData(Qt.ItemDataRole.UserRole + 3, layer.name)
            item.setData(Qt.ItemDataRole.UserRole + 4, bool(layer.visible))
            item.setData(Qt.ItemDataRole.UserRole + 5, float(layer.opacity))
            item.setData(Qt.ItemDataRole.UserRole + 6, is_draft)
            if is_draft:
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
            item.setToolTip(
                (tr("下書きレイヤー（色数削減の対象外）。\n") if is_draft else "")
                + tr("左端の＞でサムネイルを開閉、目のアイコンで表示／非表示を切替、"
                     "ダブルクリックでレイヤー名を変更。")
            )
            item.setSizeHint(QSize(0, row_height))
            self.layer_list.addItem(item)
        self.layer_list.setCurrentRow(rows - 1 - active)
        if 0 <= active < len(frames[current].layers):
            self._sync_layer_opacity_slider(
                frames[current].layers[active].opacity
            )
        self.layer_list.blockSignals(False)
        for col in range(cols):
            self.table.setColumnWidth(col, cell_width)
        for visual_row in range(rows):
            self.table.setRowHeight(visual_row, row_height)
            layer_index = rows - 1 - visual_row
            sequence_columns = [
                column
                for _number, column in sorted(
                    {
                        int(frame.layers[layer_index].sequence_number): column
                        for column, frame in enumerate(frames)
                        if (
                            frame.layers[layer_index].sequence_number is not None
                            and (frame.layers[layer_index].has_content or frame.layers[layer_index].is_blank_key)
                        )
                    }.items()
                )
            ]
            sequence_by_number = {
                int(frames[column].layers[layer_index].sequence_number): column
                for column in sequence_columns
            }
            archive_by_number = {
                int(number): archived
                for (archived_layer, number), archived
                in self.sequence_archive.items()
                if int(archived_layer) == layer_index
            }
            for number, frame_column in sequence_by_number.items():
                self._sequence_frame_by_cell[(visual_row, number - 1)] = frame_column
            self.table._sequence_numbers_by_row[visual_row] = tuple(
                sorted(
                    set(sequence_by_number)
                    | {
                        int(number)
                        for archived_layer, number in self.sequence_archive
                        if int(archived_layer) == layer_index
                    }
                )
            )
            content_columns = [
                column
                for column in range(len(frames))
                if frames[column].layers[layer_index].has_content
            ]
            if self.timeline_mode == "sequence":
                content_key_numbers = {
                    number - 1: number
                    for number in set(sequence_by_number) | set(archive_by_number)
                }
            elif self.timeline_mode == "sheet":
                used_numbers = [
                    int(frames[column].layers[layer_index].sequence_number)
                    for column in content_columns
                    if frames[column].layers[layer_index].sequence_number
                    is not None
                ]
                next_number = max(used_numbers, default=0) + 1
                for column in content_columns:
                    layer = frames[column].layers[layer_index]
                    if layer.sequence_number is None:
                        layer.sequence_number = next_number
                        next_number += 1
                content_key_numbers = {
                    column: int(
                        frames[column].layers[layer_index].sequence_number
                    )
                    for column in content_columns
                }
            else:
                content_key_numbers = {
                    column: number
                    for number, column in enumerate(
                        content_columns,
                        start=1,
                    )
                }
            gap_cells = (
                {} if self.timeline_mode == "sequence"
                else self.timeline_gaps(frames, layer_index)
            )
            group_cells = {}
            if self.timeline_mode != "sequence":
                for group_start, (group_length, group_reverse) in tween_groups(
                    frames, layer_index
                ).items():
                    for group_column in range(group_start, group_start + group_length):
                        group_cells[group_column] = (
                            group_start, group_length, group_reverse,
                        )
            for col in range(cols):
                text = ""
                label = ""
                sequence_source = None
                if self.timeline_mode == "sequence":
                    number = col + 1
                    if number in sequence_by_number:
                        sequence_source = frames[
                            sequence_by_number[number]
                        ].layers[layer_index]
                    elif number in archive_by_number:
                        sequence_source = archive_by_number[number]
                    if sequence_source is not None:
                        span_kind = (
                            "content"
                            if sequence_source.has_content
                            else "sequence_blank"
                        )
                        key_col, exposure = col, 1
                    else:
                        span_kind, key_col, exposure = None, None, 0
                else:
                    span_kind, key_col, exposure = self.timeline_span_at(
                        frames, layer_index, col
                    )
                    if col in group_cells:
                        # 確定済みのトゥイーンは、区間全体を 1 つのセルとして見せる。
                        span_kind = "content"
                        key_col, exposure = group_cells[col][0], group_cells[col][1]
                is_handle = False
                is_start_handle = False
                is_pending_tween = False
                if key_col is not None:
                    end_col = key_col + exposure - 1
                    is_pending_tween = (
                        layer_index == pending_layer
                        and int(key_col) == pending_key
                        and int(end_col) == pending_end
                    )
                    if (
                        span_kind == "content"
                        and col == key_col
                    ):
                        source_key_layer = (
                            sequence_source
                            if self.timeline_mode == "sequence"
                            else frames[key_col].layers[layer_index]
                        )
                        source_cell_name = getattr(
                            source_key_layer, "cell_name", None
                        )
                        label = str(
                            source_cell_name
                            or content_key_numbers.get(key_col, "")
                        )
                        # 開閉のたびに集め直さずに済むよう、全行の絵を覚えておく。
                        if source_key_layer is not None:
                            self.table._thumb_sources[(visual_row, col)] = (
                                source_key_layer.image, int(exposure)
                            )
                        text = (
                            "◆"
                            if (
                                is_pending_tween
                                and pending_reverse
                            )
                            else str(
                                source_cell_name
                                or content_key_numbers.get(key_col, "")
                            )
                        )
                    elif (
                        span_kind == "blank"
                        and col == key_col
                    ):
                        text = "○"
                    elif col == end_col:
                        text = (
                            "♦"
                            if (
                                is_pending_tween
                                and not pending_reverse
                            )
                            else "→"
                        )
                    elif col > key_col:
                        text = "ー"
                    is_handle = col == end_col
                    is_start_handle = col == key_col
                if span_kind == "sequence_blank":
                    text = str(col + 1)
                    label = text
                item = QTableWidgetItem(text)
                item.setData(TimelineCellDelegate.LABEL_ROLE, label)
                if span_kind is None and col in gap_cells:
                    item.setData(TimelineCellDelegate.GAP_ROLE, gap_cells[col])
                if is_pending_tween:
                    item.setData(
                        TimelineCellDelegate.TWEEN_SPAN_ROLE,
                        "reverse" if pending_reverse else "forward",
                    )
                elif col in group_cells:
                    item.setData(
                        TimelineCellDelegate.TWEEN_SPAN_ROLE,
                        "reverse" if group_cells[col][2] else "forward",
                    )
                    item.setData(TimelineCellDelegate.TWEEN_GROUP_ROLE, True)
                if span_kind == "content":
                    state_name = (
                        "sheet_key"
                        if self.timeline_mode == "sheet" and col == key_col
                        else "sheet_hold"
                        if self.timeline_mode == "sheet"
                        else "key"
                        if col == key_col
                        else "hold"
                    )
                else:
                    state_name = (
                        "blank" if span_kind in ("blank", "sequence_blank") else "uncreated"
                    )
                item.setData(TimelineCellDelegate.STATE_ROLE, state_name)
                item.setFlags((item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled) & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                # 参考UIに合わせたタイムライン専用配色。
                # キーフレームと保持区間は薄い水色、空フレームはさらに薄く、
                # 未作成フレームはテーブル既定色のままにする。
                cell_colors = theme.palette()
                if span_kind == "content" and col == key_col:
                    cell_key = "sheet_key" if self.timeline_mode == "sheet" else "key"
                    item.setBackground(QColor(cell_colors[f"timeline_{cell_key}"]))
                    item.setForeground(QColor(cell_colors[f"timeline_{cell_key}_text"]))
                    key_font = item.font()
                    key_font.setBold(True)
                    key_font.setPointSize(max(10, key_font.pointSize()))
                    item.setFont(key_font)
                elif span_kind == "content":
                    cell_key = "sheet_hold" if self.timeline_mode == "sheet" else "hold"
                    item.setBackground(QColor(cell_colors[f"timeline_{cell_key}"]))
                    item.setForeground(QColor(cell_colors[f"timeline_{cell_key}_text"]))
                elif span_kind == "blank":
                    item.setBackground(QColor(cell_colors["timeline_blank"]))
                    item.setForeground(QColor(cell_colors["timeline_blank_text"]))
                    if text == "○":
                        blank_font = item.font()
                        blank_font.setBold(True)
                        blank_font.setPointSize(
                            max(10, blank_font.pointSize())
                        )
                        item.setFont(blank_font)
                else:
                    item.setForeground(QColor(cell_colors["timeline_uncreated_text"]))
                if text in ("→", "♦", "◆"):
                    item.setForeground(
                        QColor("#A43A9E")
                        if text in ("♦", "◆")
                        else QColor("#145F80")
                    )
                    marker_font = item.font()
                    marker_font.setBold(True)
                    marker_font.setPointSize(
                        max(10, marker_font.pointSize())
                    )
                    item.setFont(marker_font)
                if key_col is not None:
                    item.setData(Qt.ItemDataRole.UserRole, int(key_col))
                    item.setData(Qt.ItemDataRole.UserRole + 1, int(exposure))
                    item.setData(TimelineTable.END_HANDLE_ROLE, bool(is_handle))
                    item.setData(TimelineTable.START_HANDLE_ROLE, bool(is_start_handle))
                    item.setData(
                        TimelineTable.TWEEN_PENDING_ROLE,
                        bool(
                            is_pending_tween
                            and col in (key_col, end_col)
                        ),
                    )
                if is_handle:
                    if is_pending_tween:
                        item.setToolTip(
                            (
                                tr("逆生成トゥイーン中。"
                                "この右端は元の初期形状です。"
                                "右クリックでキャンセルできます。")
                            )
                            if pending_reverse
                            else
                            tr("トゥイーン変形中。"
                            "右クリックでキャンセルできます。")
                        )
                    else:
                        item.setToolTip(
                            (
                                tr("右端をドラッグして後方向の表示コマ数を変更／"
                                "右クリックでトゥイーンを有効化")
                            )
                            if span_kind == "content"
                            else tr("空フレームの右端をドラッグして表示コマ数を変更")
                        )
                elif text == "◆":
                    item.setToolTip(
                        tr("逆生成トゥイーン中。"
                        "キーフレーム側が操作中の変形形状、"
                        "右端側が元の初期形状になります。"
                        "右クリックでキャンセルできます。")
                    )
                elif (
                    span_kind == "content"
                    and col == key_col
                ):
                    source_cell_name = getattr(
                        (
                            sequence_source
                            if self.timeline_mode == "sequence"
                            else frames[key_col].layers[layer_index]
                        ),
                        "cell_name",
                        None,
                    )
                    item.setToolTip(
                        (
                            tr("CLIP STUDIOセル名：{name}\n").format(name=source_cell_name)
                            if source_cell_name
                            else ""
                        )
                        + tr("中央をドラッグして移動／左端をドラッグして前方向へ伸縮\nレイヤーセル {value} / {exposure}コマ").format(value=col + 1, exposure=exposure)
                    )
                elif text == "○":
                    item.setToolTip(
                        tr("空フレームの先頭です。"
                        "描画すると自動的にキーフレーム化します。")
                    )
                if (
                    (self.timeline_mode != "sequence" and col == current)
                    or (
                        self.timeline_mode == "sequence"
                        and self._sequence_frame_by_cell.get(
                            (visual_row, col)
                        ) == current
                    )
                ):
                    item.setForeground(QColor(190, 40, 40))
                if col in group_cells:
                    item.setToolTip(
                        tr("確定したトゥイーン（{exposure}コマ）。"
                           "ダブルクリックで形を直す／右クリックで解除").format(
                            exposure=group_cells[col][1]
                        )
                    )
                self.table.setItem(visual_row, col, item)
        self._apply_row_heights()
        selected_layer = frames[current].layers[active]
        self.duration.blockSignals(True)
        self.duration.setValue(max(1, selected_layer.exposure))
        self.duration.blockSignals(False)

        valid_selected = [
            (row, column)
            for row, column in selected_cells
            if (
                0 <= row < self.table.rowCount()
                and 0 <= column < self._real_frame_count
            )
        ]
        if valid_selected:
            self.table.clearSelection()
            for row, column in valid_selected:
                item = self.table.item(row, column)
                if item is not None:
                    item.setSelected(True)

            current_row, current_column = previous_current
            if not (
                0 <= current_row < self.table.rowCount()
                and 0 <= current_column < self._real_frame_count
            ):
                current_row, current_column = valid_selected[-1]
            current_index = self.table.model().index(
                current_row, current_column
            )
            self.table.selectionModel().setCurrentIndex(
                current_index,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
        else:
            self.select_current(current, active)
        self.table.blockSignals(False)
        self.set_normalize_pending(
            sum(
                changed_count(
                    normalization_mapping(frames, self.sequence_archive, layer_index)
                )
                for layer_index in range(rows)
            )
            if self.timeline_mode == "sheet"
            else 0
        )

    @staticmethod
    def key_at_or_before(frames, layer_index, column):
        for key_col in range(column, -1, -1):
            if layer_index >= len(frames[key_col].layers):
                continue
            layer = frames[key_col].layers[layer_index]
            if layer.has_content:
                if column < key_col + max(1, layer.exposure):
                    return key_col
                return None
        return None

    def select_current(self, column, active):
        row = max(0, self.table.rowCount() - 1 - active)
        playhead = None if self.timeline_mode == "sequence" else int(column)
        if self.timeline_mode != "sequence":
            self._set_frame_counter(int(column) + 1)
        if playhead != self.table._playhead_column:
            self.table._playhead_column = playhead
            self.table.viewport().update()
            self.table.horizontalHeader().viewport().update()
        if self.timeline_mode == "sequence":
            column = next(
                (
                    visual_column
                    for (visual_row, visual_column), frame_column
                    in self._sequence_frame_by_cell.items()
                    if visual_row == row and frame_column == int(column)
                ),
                0,
            )
        column = max(
            0,
            min(column, max(0, self._real_frame_count - 1)),
        )

        # ドラッグ／Shift選択中のセルを消さず、
        # 再生ヘッドに相当する現在セルだけを更新する。
        selected_count = len(self.table.selectedIndexes())
        modifiers = QApplication.keyboardModifiers()
        preserve_selection = (
            selected_count > 1
            or bool(
                modifiers
                & (
                    Qt.KeyboardModifier.ShiftModifier
                    | Qt.KeyboardModifier.ControlModifier
                )
            )
        )
        if preserve_selection:
            index = self.table.model().index(row, column)
            self.table.selectionModel().setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
        else:
            self.table.setCurrentCell(row, column)

    def update_cell(self, frames, frame_index, layer_index):
        if not frames or frame_index < 0 or frame_index >= len(frames):
            return
        if layer_index < 0 or layer_index >= len(frames[frame_index].layers):
            return
        self.refresh(frames, frame_index, layer_index)
