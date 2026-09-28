"""番号の正規化（タイムラインの順番で振り直す）の確認画面。"""
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .i18n import tr


def number_changes(mapping):
    """{旧番号: 新番号} から、実際に番号が変わるものだけを旧番号順で返す。"""
    return sorted(
        (int(old), int(new))
        for old, new in dict(mapping).items()
        if int(old) != int(new)
    )


class NormalizeNumbersDialog(QDialog):
    """振り直す番号の一覧と、撮影済みカットへの注意を見せてから実行する。

    ``layers`` は上から表示する順の ``(layer_index, name, mapping)`` の並び。
    ``selected`` は選択中のレイヤー番号の集合。
    """

    def __init__(self, layers, selected, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("タイムラインの順番で正規化"))
        self.setModal(True)
        self.setMinimumWidth(460)
        self._layers = [
            (int(index), str(name), number_changes(mapping))
            for index, name, mapping in layers
        ]
        self._selected = {int(index) for index in selected}
        c = theme.palette()

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        summary = QLabel(
            tr("左から最初に出てくる順に 1, 2, 3… と振り直します。"
               "同じ絵を使い回しているセルは同じ番号のままです。")
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        scope_row = QHBoxLayout()
        self.scope_selected = QRadioButton(tr("選択レイヤー"))
        self.scope_all = QRadioButton(tr("すべてのレイヤー"))
        self._scope_group = QButtonGroup(self)
        self._scope_group.addButton(self.scope_selected)
        self._scope_group.addButton(self.scope_all)
        scope_row.addWidget(self.scope_selected)
        scope_row.addWidget(self.scope_all)
        scope_row.addStretch()
        layout.addLayout(scope_row)

        self._changes_box = QWidget()
        self._changes_layout = QVBoxLayout(self._changes_box)
        self._changes_layout.setContentsMargins(0, 0, 0, 0)
        self._changes_layout.setSpacing(4)
        layout.addWidget(self._changes_box)

        # リテイクで中割りを足したカットを正規化すると、AE のタイムリマップと
        # 番号が食い違う。確認画面では必ずこの注意を出す。
        warning = QColor(c["warning"])
        fill = QColor(warning)
        fill.setAlpha(34)
        caution = QFrame()
        caution.setObjectName("normalizeCaution")
        caution.setStyleSheet(
            "QFrame#normalizeCaution{background:rgba(%d,%d,%d,%d);"
            "border:1px solid %s;border-radius:6px;}"
            "QFrame#normalizeCaution QLabel{background:transparent;border:0;color:%s;}"
            % (
                fill.red(), fill.green(), fill.blue(), fill.alpha(),
                warning.name(), c["text"],
            )
        )
        caution_layout = QVBoxLayout(caution)
        caution_layout.setContentsMargins(10, 8, 10, 8)
        caution_layout.setSpacing(4)
        caution_title = QLabel(
            tr("撮影済みのカットをリテイクで直しているときは、正規化しないでください。")
        )
        caution_title.setWordWrap(True)
        title_font = caution_title.font()
        title_font.setBold(True)
        caution_title.setFont(title_font)
        caution_title.setStyleSheet("color:%s;" % warning.name())
        caution_body = QLabel(
            tr("AE ですでにタイムリマップを打っているため、中割りを足したところ以外の"
               "番号が変わると、同じ番号が別の絵を指すようになります。"
               "たとえば A4 は、リテイクの前後で同じ絵であるべきです。"
               "足した絵は末尾の番号のままにしておき、タイムリマップ側で追加してください。")
        )
        caution_body.setWordWrap(True)
        caution_layout.addWidget(caution_title)
        caution_layout.addWidget(caution_body)
        self.caution = caution
        layout.addWidget(caution)

        buttons = QDialogButtonBox()
        self.cancel_button = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = QPushButton()
        self.apply_button.setDefault(False)
        self.apply_button.setAutoDefault(False)
        self.cancel_button.setDefault(True)
        buttons.addButton(self.apply_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        has_selected_changes = any(
            changes for index, _name, changes in self._layers
            if index in self._selected
        )
        (self.scope_selected if has_selected_changes else self.scope_all).setChecked(True)
        self.scope_selected.toggled.connect(self._refresh)
        self._refresh()

    def target_layer_indices(self):
        """実行したときに振り直すレイヤー（番号が変わるものだけ）。"""
        use_all = self.scope_all.isChecked()
        return [
            index for index, _name, changes in self._layers
            if changes and (use_all or index in self._selected)
        ]

    def change_count(self):
        targets = set(self.target_layer_indices())
        return sum(
            len(changes) for index, _name, changes in self._layers
            if index in targets
        )

    def _refresh(self):
        while self._changes_layout.count():
            item = self._changes_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        targets = set(self.target_layer_indices())
        c = theme.palette()
        for index, name, changes in self._layers:
            if index not in targets:
                continue
            pairs = "　".join(f"{old} → {new}" for old, new in changes)
            label = QLabel(
                "<b>%s</b>　<span style='color:%s'>%s</span>"
                % (name.replace("&", "&amp;").replace("<", "&lt;"), c["accent"], pairs)
            )
            label.setWordWrap(True)
            self._changes_layout.addWidget(label)
        count = self.change_count()
        if count:
            self.apply_button.setText(
                tr("{count}個の番号を振り直す").format(count=count)
            )
        else:
            empty = QLabel(tr("このレイヤーの番号は、すでにタイムラインの順番どおりです。"))
            empty.setStyleSheet("color:%s;" % c["text_muted"])
            self._changes_layout.addWidget(empty)
            self.apply_button.setText(tr("振り直す"))
        self.apply_button.setEnabled(bool(count))
