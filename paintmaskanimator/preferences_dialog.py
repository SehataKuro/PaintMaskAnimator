"""Preferences window (環境設定).

Every control applies and saves its value as soon as it changes, like a macOS
settings window, so there is no OK / Cancel. The window is not modal and is
kept on the main window, so reopening it (⌘, / Ctrl+,) raises the same one.
Keyboard shortcuts stay in their own dialog: they are a long list of their own
and would crowd out everything else here.
"""
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import config, i18n, layout_paper, preferences, pressure_settings, theme
from .canvas_undo import set_undo_limits
from .i18n import tr
from .pressure import PressureEditor

_CUSTOM_ACCENT = "custom"
# Colours come from the app palette so the theme switch restyles them too.
_STYLE = """
QLabel#preferencesHint { color: palette(placeholder-text); }
"""


def _swatch(color):
    pixmap = QPixmap(14, 14)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)


def _hint(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setObjectName("preferencesHint")
    return label


def _page(*rows):
    """A form page. ``rows`` are ``(label, widget)`` pairs or lone hint widgets."""
    page = QWidget()
    form = QFormLayout(page)
    form.setContentsMargins(20, 16, 20, 16)
    form.setVerticalSpacing(10)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    for row in rows:
        if isinstance(row, tuple):
            form.addRow(*row)
        else:
            form.addRow(row)
    return page


class _ReorderableList(QListWidget):
    """A list the user can reorder by dragging; ``orderChanged`` after a drop."""

    orderChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def dropEvent(self, event):
        super().dropEvent(event)
        self.orderChanged.emit()


class PreferencesDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle(tr("環境設定"))
        self.setModal(False)
        self.resize(620, 520)

        self.setStyleSheet(_STYLE)
        self.sections = QListWidget()
        self.sections.setObjectName("preferencesSections")
        self.sections.setFixedWidth(160)
        self.pages = QStackedWidget()
        self._section_keys = []
        for key, title, page in (
            ("general", tr("一般"), self._build_general_page()),
            ("appearance", tr("外観"), self._build_appearance_page()),
            ("canvas", tr("新規キャンバス"), self._build_canvas_page()),
            ("pressure", tr("筆圧"), self._build_pressure_page()),
            ("workspaces", tr("ワークスペース"), self._build_workspaces_page()),
            ("performance", tr("パフォーマンス"), self._build_performance_page()),
        ):
            item = QListWidgetItem(title)
            item.setSizeHint(QSize(0, 30))
            self.sections.addItem(item)
            self.pages.addWidget(page)
            self._section_keys.append(key)
        self.sections.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.sections.setCurrentRow(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.sections)
        body.addWidget(self.pages, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        # macOS settings windows have no buttons; elsewhere people look for one.
        if not theme.IS_MAC:
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(self.close)
            layout.addWidget(buttons)

    # -- 一般 ---------------------------------------------------------------
    def _build_general_page(self):
        self.language = QComboBox()
        self.language.addItem(tr("システムに合わせる"), i18n.SYSTEM)
        for code, label in i18n.available_languages().items():
            self.language.addItem(label, code)
        self._select_data(self.language, i18n.preferred_language())
        self.language.currentIndexChanged.connect(self._language_changed)

        self.autosave_enabled = QCheckBox(tr("自動保存する"))
        self.autosave_enabled.setChecked(preferences.autosave_enabled())
        self.autosave_enabled.toggled.connect(self._autosave_changed)
        self.autosave_interval = QSpinBox()
        self.autosave_interval.setRange(*preferences.AUTOSAVE_INTERVAL_RANGE)
        self.autosave_interval.setSuffix(tr(" 分ごと"))
        self.autosave_interval.setValue(preferences.autosave_interval_minutes())
        self.autosave_interval.setEnabled(self.autosave_enabled.isChecked())
        self.autosave_interval.valueChanged.connect(self._autosave_changed)

        return _page(
            (tr("言語 / Language"), self.language),
            _hint(tr("言語は次回の起動から切り替わります。")),
            (tr("自動保存"), self.autosave_enabled),
            ("", self.autosave_interval),
            _hint(tr("アプリが異常終了したとき、次の起動で自動保存から作業を復元できます。")),
        )

    def _language_changed(self):
        i18n.set_preferred_language(self.language.currentData())

    def _autosave_changed(self):
        enabled = self.autosave_enabled.isChecked()
        self.autosave_interval.setEnabled(enabled)
        preferences.set_autosave_enabled(enabled)
        preferences.set_autosave_interval_minutes(self.autosave_interval.value())
        self.window.autosave.apply_preferences()

    # -- 外観 ---------------------------------------------------------------
    def _build_appearance_page(self):
        self.theme = QComboBox()
        labels = {
            theme.SYSTEM: tr("システムに合わせる"),
            "light": tr("ライト（明るい）"),
            "dark": tr("ダーク（暗い）"),
        }
        for name in theme.available_themes():
            self.theme.addItem(labels.get(name, name), name)
        self._select_data(self.theme, theme.current_theme())
        self.theme.currentIndexChanged.connect(self._theme_changed)

        self.accent = QComboBox()
        self._fill_accent_choices()
        self.accent.activated.connect(self._accent_chosen)

        return _page(
            (tr("テーマ"), self.theme),
            (tr("アクセントカラー"), self.accent),
        )

    def _theme_changed(self):
        self.window.set_theme(self.theme.currentData())

    def _fill_accent_choices(self):
        self.accent.blockSignals(True)
        self.accent.clear()
        self.accent.addItem(tr("システムに合わせる"), theme.SYSTEM)
        presets = theme.accent_presets()
        for label, hexval in presets:
            self.accent.addItem(_swatch(hexval), label, hexval)
        current = theme.accent_setting()
        if current != theme.SYSTEM and current not in {h for _, h in presets}:
            self.accent.addItem(_swatch(current), tr("カスタム（{color}）").format(color=current), current)
        self.accent.addItem(tr("カスタム…"), _CUSTOM_ACCENT)
        self._select_data(self.accent, current)
        self.accent.blockSignals(False)

    def _accent_chosen(self):
        value = self.accent.currentData()
        if value == _CUSTOM_ACCENT:
            color = QColorDialog.getColor(
                QColor(theme.current_accent()), self, tr("アクセントカラーを選択")
            )
            if not color.isValid():
                self._fill_accent_choices()
                return
            value = color.name()
        self.window.colors.set_accent(value)
        self._fill_accent_choices()

    # -- 新規キャンバス -------------------------------------------------------
    def _build_canvas_page(self):
        width, height = preferences.new_canvas_size()
        self.canvas_width = self._size_spin(width)
        self.canvas_height = self._size_spin(height)

        self.papers = QListWidget()
        self.papers.setIconSize(QSize(40, 40))
        self.papers.setMinimumHeight(150)
        add = QPushButton(tr("追加…"))
        add.clicked.connect(self._add_papers)
        self.rename_paper_button = QPushButton(tr("名前を変更…"))
        self.rename_paper_button.clicked.connect(self._rename_paper)
        self.remove_paper_button = QPushButton(tr("削除"))
        self.remove_paper_button.clicked.connect(self._remove_paper)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        for button in (add, self.rename_paper_button, self.remove_paper_button):
            buttons.addWidget(button)
        buttons.addStretch()
        paper_box = QWidget()
        paper_layout = QVBoxLayout(paper_box)
        paper_layout.setContentsMargins(0, 0, 0, 0)
        paper_layout.addWidget(self.papers, 1)
        paper_layout.addLayout(buttons)
        self.papers.currentRowChanged.connect(self._sync_paper_buttons)
        self.reload_papers()

        page = _page(
            (tr("幅"), self.canvas_width),
            (tr("高さ"), self.canvas_height),
            _hint(tr("起動したときと、ファイル › 新規作成… で使う大きさです。")),
            (tr("レイアウト用紙"), paper_box),
            _hint(tr(
                "ファイル › 新規作成… で選ぶと、一番下の下書きレイヤーとして入り、"
                "キャンバスサイズを用紙に合わせられます。画像は環境設定の中に"
                "コピーして保管します。"
            )),
        )
        form = page.layout()
        assert isinstance(form, QFormLayout)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        return page

    def _size_spin(self, value):
        spin = QSpinBox()
        spin.setRange(*preferences.CANVAS_SIZE_RANGE)
        spin.setSuffix(" px")
        spin.setValue(value)
        spin.valueChanged.connect(self._canvas_size_changed)
        return spin

    def _canvas_size_changed(self):
        preferences.set_new_canvas_size(
            self.canvas_width.value(), self.canvas_height.value()
        )

    def reload_papers(self, select_id=None):
        self.papers.clear()
        for entry in layout_paper.papers():
            size = layout_paper.image_size(entry)
            label = entry["name"] if size is None else f"{entry['name']}\n{size[0]} × {size[1]} px"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry["id"])
            thumbnail = QPixmap(str(layout_paper.image_path(entry)))
            if not thumbnail.isNull():
                item.setIcon(QIcon(thumbnail.scaled(
                    40, 40,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )))
            self.papers.addItem(item)
            if entry["id"] == select_id:
                self.papers.setCurrentItem(item)
        self._sync_paper_buttons()

    def _selected_paper_id(self):
        item = self.papers.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _sync_paper_buttons(self, *_args):
        selected = self._selected_paper_id() is not None
        self.rename_paper_button.setEnabled(selected)
        self.remove_paper_button.setEnabled(selected)

    def _add_papers(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            tr("レイアウト用紙の画像を追加"),
            "",
            tr("画像 (*.png *.jpg *.jpeg *.tga)"),
        )
        added = None
        failed = []
        for path in paths:
            entry = layout_paper.add_paper(path)
            if entry is None:
                failed.append(Path(path).name)
            else:
                added = entry
        self.reload_papers(select_id=added["id"] if added else None)
        if failed:
            QMessageBox.warning(
                self,
                tr("レイアウト用紙"),
                tr("次の画像は読み込めませんでした。\n\n{names}").format(names="\n".join(failed)),
            )

    def _rename_paper(self):
        paper_id = self._selected_paper_id()
        entry = layout_paper.paper(paper_id)
        if entry is None:
            return
        name, ok = QInputDialog.getText(
            self, tr("名前を変更"), tr("用紙の名前"), text=entry["name"]
        )
        if ok and name.strip():
            layout_paper.rename_paper(paper_id, name)
            self.reload_papers(select_id=paper_id)

    def _remove_paper(self):
        paper_id = self._selected_paper_id()
        if paper_id is not None:
            layout_paper.remove_paper(paper_id)
            self.reload_papers()

    # -- 筆圧 ---------------------------------------------------------------
    def _build_pressure_page(self):
        self.pressure_preset = QComboBox()
        self.pressure_preset.currentIndexChanged.connect(self._pressure_preset_chosen)
        add = QPushButton(tr("追加"))
        add.setToolTip(tr("今のプリセットを元に、新しいプリセットを作ります。"))
        add.clicked.connect(self._add_pressure_preset)
        rename = QPushButton(tr("名前を変更…"))
        rename.clicked.connect(self._rename_pressure_preset)
        self.remove_pressure_button = QPushButton(tr("削除"))
        self.remove_pressure_button.clicked.connect(self._remove_pressure_preset)
        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.addWidget(self.pressure_preset, 1)
        for button in (add, rename, self.remove_pressure_button):
            preset_row.addWidget(button)

        self.pressure_editor = PressureEditor()
        self.pressure_editor.changed.connect(self._pressure_edited)
        self.reload_pressure_presets()

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(QLabel(tr("使うプリセット")))
        layout.addLayout(preset_row)
        layout.addWidget(_hint(tr(
            "ブラシが「全体の設定を使う」のとき、ここで選んだプリセットで描きます。"
            "ブラシだけの設定は、ツールプロパティの 筆圧… で切り替えます。"
        )))
        layout.addWidget(self.pressure_editor, 1)
        return page

    def reload_pressure_presets(self):
        self.pressure_preset.blockSignals(True)
        self.pressure_preset.clear()
        self.pressure_preset.addItems(pressure_settings.preset_names())
        self.pressure_preset.setCurrentText(pressure_settings.active_preset_name())
        self.pressure_preset.blockSignals(False)
        self.remove_pressure_button.setEnabled(self.pressure_preset.count() > 1)
        self.pressure_editor.set_settings(pressure_settings.global_settings())

    def _pressure_preset_chosen(self):
        pressure_settings.set_active_preset(self.pressure_preset.currentText())
        self.pressure_editor.set_settings(pressure_settings.global_settings())
        self.window.apply_pressure_settings()

    def _pressure_edited(self):
        pressure_settings.update_preset(
            self.pressure_preset.currentText(), self.pressure_editor.settings()
        )
        self.window.apply_pressure_settings()

    def _add_pressure_preset(self):
        name = pressure_settings.add_preset(
            tr("新しいプリセット"), self.pressure_editor.settings()
        )
        pressure_settings.set_active_preset(name)
        self.reload_pressure_presets()
        self.window.apply_pressure_settings()

    def _rename_pressure_preset(self):
        old = self.pressure_preset.currentText()
        name, ok = QInputDialog.getText(
            self, tr("名前を変更"), tr("プリセットの名前"), text=old
        )
        if not ok or name.strip() == old:
            return
        if not pressure_settings.rename_preset(old, name):
            QMessageBox.warning(
                self,
                tr("名前を変更"),
                tr("その名前は使えません。空欄か、ほかのプリセットと同じ名前です。"),
            )
            return
        self.reload_pressure_presets()

    def _remove_pressure_preset(self):
        if pressure_settings.remove_preset(self.pressure_preset.currentText()):
            self.reload_pressure_presets()
            self.window.apply_pressure_settings()

    # -- ワークスペース -----------------------------------------------------
    def _build_workspaces_page(self):
        self.workspaces = _ReorderableList()
        self.workspaces.orderChanged.connect(self._workspaces_dragged)
        self.workspaces.currentRowChanged.connect(self._sync_workspace_buttons)
        self.workspaces.itemDoubleClicked.connect(lambda _item: self._rename_workspace())

        self.workspace_up = QPushButton(tr("上へ"))
        self.workspace_up.clicked.connect(lambda: self._move_workspace(-1))
        self.workspace_down = QPushButton(tr("下へ"))
        self.workspace_down.clicked.connect(lambda: self._move_workspace(1))
        self.workspace_rename = QPushButton(tr("名前を変更…"))
        self.workspace_rename.clicked.connect(self._rename_workspace)
        self.workspace_overwrite = QPushButton(tr("現在の配置で上書き"))
        self.workspace_overwrite.setToolTip(
            tr("選んだワークスペースを、今のパネルの配置で保存し直します。")
        )
        self.workspace_overwrite.clicked.connect(self._overwrite_workspace)
        self.workspace_delete = QPushButton(tr("削除…"))
        self.workspace_delete.clicked.connect(self._delete_workspace)
        buttons = QVBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        for button in (
            self.workspace_up, self.workspace_down, self.workspace_rename,
            self.workspace_overwrite, self.workspace_delete,
        ):
            buttons.addWidget(button)
        buttons.addStretch()

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        row = QHBoxLayout()
        row.addWidget(self.workspaces, 1)
        row.addLayout(buttons)
        layout.addLayout(row, 1)
        layout.addWidget(_hint(tr(
            "ワークスペース メニューに、この順で並びます。ドラッグでも並べ替えられます。"
            "新しいワークスペースは ワークスペース › 現在の配置を保存… で作ります。"
        )))
        self.reload_workspaces()
        return page

    def reload_workspaces(self):
        workspace = self.window.workspace
        selected = self._selected_workspace()
        active = config.get_value("active_workspace")
        self.workspaces.blockSignals(True)
        self.workspaces.clear()
        for name in workspace.names():
            label = tr("{name}（使用中）").format(name=name) if name == active else name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setSizeHint(QSize(0, 28))
            self.workspaces.addItem(item)
            if name == selected:
                self.workspaces.setCurrentItem(item)
        self.workspaces.blockSignals(False)
        self._sync_workspace_buttons()

    def _selected_workspace(self):
        item = self.workspaces.currentItem() if hasattr(self, "workspaces") else None
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _listed_workspaces(self):
        return [
            self.workspaces.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.workspaces.count())
        ]

    def _sync_workspace_buttons(self, *_args):
        row = self.workspaces.currentRow()
        selected = row >= 0
        self.workspace_up.setEnabled(row > 0)
        self.workspace_down.setEnabled(selected and row < self.workspaces.count() - 1)
        for button in (self.workspace_rename, self.workspace_overwrite, self.workspace_delete):
            button.setEnabled(selected)

    def _workspaces_dragged(self, *_args):
        self.window.workspace.reorder(self._listed_workspaces())

    def _move_workspace(self, step):
        row = self.workspaces.currentRow()
        names = self._listed_workspaces()
        target = row + step
        if row < 0 or not 0 <= target < len(names):
            return
        names[row], names[target] = names[target], names[row]
        self.window.workspace.reorder(names)
        self.workspaces.setCurrentRow(target)

    def _rename_workspace(self):
        old = self._selected_workspace()
        if old is None:
            return
        name, ok = QInputDialog.getText(
            self, tr("名前を変更"), tr("ワークスペース名："), text=old
        )
        if not ok or name.strip() == old:
            return
        if not self.window.workspace.rename(old, name):
            QMessageBox.warning(
                self,
                tr("名前を変更"),
                tr("その名前は使えません。空欄か、ほかのワークスペースと同じ名前です。"),
            )

    def _overwrite_workspace(self):
        name = self._selected_workspace()
        if name is not None:
            self.window.workspace.save(name)

    def _delete_workspace(self):
        name = self._selected_workspace()
        if name is None:
            return
        answer = QMessageBox.question(
            self,
            tr("ワークスペースを削除"),
            tr("ワークスペース「{name}」を削除しますか？").format(name=name),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.window.workspace.delete(name)

    # -- パフォーマンス -------------------------------------------------------
    def _build_performance_page(self):
        self.undo_steps = QSpinBox()
        self.undo_steps.setRange(*preferences.UNDO_STEPS_RANGE)
        self.undo_steps.setSuffix(tr(" 回"))
        self.undo_steps.setValue(preferences.undo_max_steps())
        self.undo_steps.valueChanged.connect(self._undo_limits_changed)
        self.undo_memory = QSpinBox()
        self.undo_memory.setRange(*preferences.UNDO_MEMORY_RANGE)
        self.undo_memory.setSingleStep(128)
        self.undo_memory.setSuffix(" MB")
        self.undo_memory.setValue(preferences.undo_max_memory_mb())
        self.undo_memory.valueChanged.connect(self._undo_limits_changed)
        return _page(
            (tr("取り消せる回数"), self.undo_steps),
            (tr("取り消し履歴のメモリ"), self.undo_memory),
            _hint(tr(
                "どちらかの上限に達すると、古い履歴から消えます。"
                "大きなキャンバスでは、回数より先にメモリの上限に達します。"
                "減らしたときは、次に操作したときに古い履歴が消えます。"
            )),
        )

    def _undo_limits_changed(self):
        preferences.set_undo_max_steps(self.undo_steps.value())
        preferences.set_undo_max_memory_mb(self.undo_memory.value())
        apply_undo_preferences()

    # -----------------------------------------------------------------------
    @staticmethod
    def _select_data(combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def show_section(self, key):
        if key in self._section_keys:
            self.sections.setCurrentRow(self._section_keys.index(key))

    def show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()


def apply_undo_preferences():
    set_undo_limits(
        preferences.undo_max_steps(),
        preferences.undo_max_memory_mb() * 1024 * 1024,
    )
