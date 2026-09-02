"""The used-color panel — the list of colours in use in the document.

Architecture: the row widgets it composes (swatch button, the three checkbox
variants, the swatch row) live in ``color_panel_widgets.py``; cohesive method
clusters were extracted into ``color_panel_<topic>.py`` as ``*Mixin`` classes and
composed onto ``UsedColorPanel`` below (grouping/reorder, selection, visibility,
mask colours, history snapshots). What remains here is the panel itself: the
widget tree, building and refreshing colour rows from a colour list, swatch
styling, and the row context menu. Type-only member declarations shared by the
mixins live in ``_color_panel_members.py``.
"""
from typing import Any
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)
from .i18n import tr
from .logging_setup import get_logger


from .color_panel_grouping import ColorGroupingMixin
from .color_panel_history import ColorPanelHistoryMixin
from .color_panel_mask import MaskColorsMixin
from .color_panel_selection import ColorSelectionMixin
from .color_panel_visibility import ColorVisibilityMixin
from .color_panel_widgets import (
    CheckClickArea,
    ColorCategoryHeader,
    ColorSelectionArea,
    ColorSelectionCheckBox,
    ColorVisibilityCheckBox,
    MaskColorCheckBox,
    ReplacementColorPopup,
    ScreenEyedropButton,
    SourceColorButton,
)

log = get_logger(__name__)


class UsedColorPanel(
    ColorGroupingMixin, ColorSelectionMixin, ColorVisibilityMixin,
    MaskColorsMixin, ColorPanelHistoryMixin, QWidget
):
    mainColorRequested = Signal(QColor)
    isolateColorClicked = Signal(QColor)
    clearIsolateRequested = Signal()
    sourceScreenColorPicked = Signal(QColor)
    applyReplacementRequested = Signal(object)
    mergeColorsRequested = Signal(object, object)
    # 親子関係の更新時に旧色プレビューを解除する通知。
    previewGroupsChanged = Signal(object)
    # 登録した親子を実ピクセルへ統合する要求（{子rgb: 親rgb}）。
    freezeGroupsRequested = Signal(object)
    deleteColorsRequested = Signal(object)
    adjustLineThicknessRequested = Signal(object)
    focusColorRequested = Signal(object)
    maskColorsChanged = Signal(object)
    selectedColorsChanged = Signal(object)
    visibleColorsChanged = Signal(object)
    # 並べ替え・親子・表示/マスクの変更をUndo履歴へ積む要求。
    # (変更前スナップショット, 履歴ラベル) を渡す。
    historyStatePush = Signal(object, str)

    # RGB-keyed state. Declared here so every assignment widens to plain
    # ``int`` tuples instead of pyright inferring ``Literal[255]`` keys from the
    # ``(255, 255, 255)`` background seed (which then rejected real colours).
    enabled_colors: dict[tuple[int, int, int], bool]
    mask_rgbs: set[tuple[int, int, int]]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setMaximumWidth(16777215)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.setMinimumHeight(0)
        self.background_rgb: tuple[int, int, int] = (255, 255, 255)
        self.colors = []
        self.enabled_colors = {self.background_rgb: True}
        self.mask_rgbs = {self.background_rgb}
        self._mask_all_mode = True

        # 親子グループ。{子rgb: 親rgb}。親自身は含めない。
        # ドラッグで子付けしても、キャンバス上の色表示は変更しない。
        self.child_to_parent = {}

        # カラーチャート適用時の役割情報。使用色一覧は色を簡潔に見せるため
        # タグ文字を常時描画しないが、再登録・PMAG保存用に保持する。
        self.child_tags = {}
        self.parent_tags = {}
        self.tag_library = ["Base"]
        self.tag_colors = {"Base": "#FFFFFF"}
        self.tag_order = []

        # 使用色カテゴリー（1色につき1フォルダー、1階層）。
        self.category_order = []
        self.category_colors = {}
        self.category_collapsed = {}
        self.category_widgets = {}
        self.replacements = {}
        self.replacement_buttons = {}
        self._replacement_popup = None
        self._replacement_popup_rgb = None

        # Used-color selection is separate from the drawing mask.
        # The most recently selected color is the parent; the others are children.
        self.selected_rgbs = []
        self.parent_rgb = None
        self._selection_anchor_rgb = None

        self.visibility_checks = {}
        self.mask_checks = {}
        self.selection_checks = {}
        self.source_buttons = {}
        self.source_wrappers = {}
        self.row_widgets = {}

        # ホバー中の色（HEXはホバー時のみ表示して色面積を最大化する）。
        self._hovered_rgb = None

        self._selection_sweep_active = False
        self._selection_sweep_state = True
        self._selection_sweep_touched = set()
        self._selection_sweep_changed = False

        self._visibility_sweep_active = False
        self._visibility_sweep_state = True
        self._visibility_sweep_touched = set()
        self._visibility_sweep_changed = False
        self._mask_sweep_active = False
        self._mask_sweep_state = True
        self._mask_sweep_touched = set()
        self._mask_sweep_changed = False
        self._isolated_rgb = None
        self._pre_isolate_enabled = None
        self._mask_alt_rgb = None
        self._pre_mask_alt = None

        # Undo履歴用。復元中は再記録を止め、スウィープ中は開始時状態を保持する。
        self._history_suspended = False
        self._sweep_history_before = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        layout.addWidget(QLabel(tr("<b>使用色</b>")))
        note = QLabel(
            tr("使用色：クリックで選択（Shift＝範囲／Ctrl＝追加）。"
            "［＋フォルダー］でカテゴリーを作り、色をヘッダーへドラッグして格納。"
            "フォルダーのチェックで所属色を一括表示／非表示。"
            "ドラッグで並べ替え、色の中央へドロップ＝その色の「子」として整理。"
            "親子付けしても色表示は変わりません。解除は右クリックから行えます。"
            "問題なければ［統合］で実画像へ焼き込みます。")
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.count_label = QLabel(tr("0色"))
        layout.addWidget(self.count_label)
        self.category_button = QPushButton(tr("＋ フォルダー"))
        self.category_button.setToolTip(tr("使用色をまとめるフォルダーを作成します。"))
        self.category_button.clicked.connect(self._prompt_create_category)
        layout.addWidget(self.category_button)

        header_widget = QWidget()
        header = QGridLayout(header_widget)
        scrollbar_width = self.style().pixelMetric(
            QStyle.PixelMetric.PM_ScrollBarExtent
        )
        header.setContentsMargins(1, 0, 1 + scrollbar_width, 0)
        header.setHorizontalSpacing(0)
        header.setColumnMinimumWidth(0, 32)
        header.setColumnMinimumWidth(1, 32)
        header.setColumnMinimumWidth(2, 32)
        selection_header = QLabel(tr("選択"))
        selection_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        selection_header.setStyleSheet("font-size:10px;")
        selection_header.setToolTip(
            tr("クリック：この色だけ選択／Shift＋クリック：範囲選択／"
            "Ctrl＋クリック：追加・解除。チェックと選択は連動します。")
        )
        header.addWidget(selection_header, 0, 0, Qt.AlignmentFlag.AlignCenter)
        visibility_header = QLabel(tr("表示"))
        visibility_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        visibility_header.setStyleSheet("font-size:10px;")
        visibility_header.setToolTip(tr("各行の薄い背景セル全体を右クリックして表示メニューを開けます。"))
        header.addWidget(visibility_header, 0, 1, Qt.AlignmentFlag.AlignCenter)
        mask_header = QLabel(tr("描画対象"))
        mask_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mask_header.setStyleSheet("font-size:10px;")
        mask_header.setToolTip(
            tr("ONにした色の上へ描けます。各行の薄い背景セル全体を"
            "右クリックして描画対象メニューを開けます。")
        )
        header.addWidget(mask_header, 0, 2, Qt.AlignmentFlag.AlignCenter)
        color_header = QLabel(tr("色（ドラッグで並べ替え／親子付け）"))
        color_header.setStyleSheet("font-size:10px;")
        header.addWidget(color_header, 0, 3)
        replacement_header = QLabel(tr("置換色"))
        replacement_header.setStyleSheet("font-size:10px;")
        replacement_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(replacement_header, 0, 4)
        header.setColumnMinimumWidth(3, 144)
        header.setColumnMinimumWidth(4, 72)
        header.setColumnStretch(3, 1)
        header.setColumnStretch(4, 1)
        layout.addWidget(header_widget)

        self.scroll: QScrollArea = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(0)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.content = QWidget()
        self.rows = QVBoxLayout(self.content)
        self.rows.setContentsMargins(2, 2, 2, 2)
        self.rows.setSpacing(1)
        self.rows.addStretch(1)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

        # 見出しと同じ5列に配置し、各ボタンの意味と位置を揃える。
        button_row = QGridLayout()
        button_row.setHorizontalSpacing(0)
        button_row.setContentsMargins(1, 0, 1 + scrollbar_width, 0)
        button_row.setColumnMinimumWidth(0, 32)
        button_row.setColumnMinimumWidth(1, 32)
        button_row.setColumnMinimumWidth(2, 32)
        button_row.setColumnMinimumWidth(3, 144)
        button_row.setColumnMinimumWidth(4, 72)
        button_row.setColumnStretch(3, 1)
        button_row.setColumnStretch(4, 1)

        self.clear_selection_button = QPushButton(tr("選択"))
        self.clear_selection_button.setToolTip(tr("選択をすべて解除します。"))
        self.clear_selection_button.clicked.connect(self._clear_used_color_selection)
        self.clear_selection_button.setFixedWidth(32)
        self.clear_selection_button.setStyleSheet("font-size:9px;padding:0px;")

        self.show_all_button = QPushButton(tr("全表示"))
        self.show_all_button.setToolTip(
            tr("非表示にした使用色と背景色をすべて表示します。")
        )
        self.show_all_button.clicked.connect(self._show_all_colors)
        self.show_all_button.setFixedWidth(32)
        self.show_all_button.setStyleSheet("font-size:9px;padding:0px;")

        self.clear_masks_button = QPushButton(tr("全体"))
        self.clear_masks_button.setToolTip(
            tr("すべての使用色と背景をマスクON（描画可能）にします。")
        )
        self.clear_masks_button.clicked.connect(self._set_all_masks_on)
        self.clear_masks_button.setFixedWidth(32)
        self.clear_masks_button.setStyleSheet("font-size:9px;padding:0px;")

        self.freeze_button = QPushButton(tr("統合"))
        self.freeze_button.setToolTip(
            tr("登録した親子（子→親の塗り替え）を、実際の画像へ焼き込みます。"
            "焼き込むと親子は解除され、Undoで元に戻せます。")
        )
        self.freeze_button.clicked.connect(self._emit_freeze)
        self.freeze_button.setEnabled(False)
        self.freeze_button.setMinimumWidth(72)
        self.freeze_button.setMaximumWidth(16777215)
        self.freeze_button.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.freeze_button.setStyleSheet("font-size:10px;padding:1px;")

        self.apply_button = QPushButton(tr("色置換"))
        self.apply_button.setToolTip(
            tr("登録した置換色を、選択レイヤーのすべてのコマへ適用します。")
        )
        self.apply_button.clicked.connect(self._emit_replacements)
        self.apply_button.setMinimumWidth(72)
        self.apply_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.apply_button.setStyleSheet("font-size:10px;padding:1px;")

        for column, button in enumerate((
            self.clear_selection_button,
            self.show_all_button,
            self.clear_masks_button,
        )):
            button.setMinimumWidth(0)
            button_row.addWidget(button, 0, column)
        # 親子の統合確定は色スウォッチ列（col3）に置く。
        action_buttons = QHBoxLayout()
        action_buttons.setContentsMargins(0, 0, 0, 0)
        action_buttons.setSpacing(2)
        self.freeze_button.setMinimumWidth(0)
        action_buttons.addWidget(self.freeze_button)
        button_row.addLayout(action_buttons, 0, 3)
        button_row.addWidget(self.apply_button, 0, 4)
        layout.addLayout(button_row)
        self.visibleColorsChanged.connect(self._refresh_category_headers)

    @staticmethod
    def _rgb_key(color):
        qc = QColor(color)
        return (qc.red(), qc.green(), qc.blue())

    @staticmethod
    def _text_color(rgb):
        luminance = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        return "#111111" if luminance >= 150 else "#ffffff"

    def _set_replacement_button_style(self, source_rgb):
        button = self.replacement_buttons.get(source_rgb)
        if button is None:
            return
        replacement_color = self.replacements.get(source_rgb)
        if replacement_color is None:
            button.setText(tr("未設定"))
            button.setStyleSheet(
                "QToolButton{background:#f2f2f2;border:1px dashed #888;}"
                "QToolButton:hover{border:1px dashed #888;}"
            )
            return
        button.setText(
            replacement_color.name(QColor.NameFormat.HexRgb).upper()
        )
        replacement_rgb = (
            replacement_color.red(),
            replacement_color.green(),
            replacement_color.blue(),
        )
        button.setStyleSheet(
            "QToolButton{"
            f"background:{replacement_color.name()};"
            f"color:{self._text_color(replacement_rgb)};"
            "border:1px solid #777;}"
            "QToolButton:hover{border:1px solid #777;}"
        )

    def _toggle_replacement(self, source_rgb):
        """未設定なら現在色を登録し、登録済みなら解除する。"""
        if source_rgb == self.background_rgb:
            return
        if source_rgb in self.replacements:
            self._clear_replacement(source_rgb)
        else:
            self._request_replacement(source_rgb)

    def _close_replacement_editor(self):
        popup = self._replacement_popup
        self._replacement_popup = None
        self._replacement_popup_rgb = None
        if popup is not None:
            popup.close()
            popup.deleteLater()

    def _replacement_popup_destroyed(self, popup):
        if self._replacement_popup is popup:
            self._replacement_popup = None
            self._replacement_popup_rgb = None

    def _toggle_replacement_editor(self, source_rgb, global_position):
        """左クリックでRGB／HSV編集を開き、再度の左クリックで閉じる。"""
        if source_rgb == self.background_rgb:
            return
        if (
            self._replacement_popup is not None
            and self._replacement_popup.isVisible()
            and self._replacement_popup_rgb == source_rgb
        ):
            self._close_replacement_editor()
            return

        self._close_replacement_editor()
        color = self.replacements.get(source_rgb)
        if color is None:
            window = self.window()
            canvas = getattr(window, "canvas", None)
            if canvas is not None:
                color = (
                    canvas.sub_color
                    if canvas.color_mode == "sub"
                    else canvas.main_color
                )
            else:
                color = QColor(*source_rgb)
            self._set_replacement_color(source_rgb, color)

        popup = ReplacementColorPopup(QColor(color), source_rgb, self)
        self._replacement_popup = popup
        self._replacement_popup_rgb = source_rgb
        popup.colorChanged.connect(
            lambda changed, rgb=source_rgb:
            self._set_replacement_color(rgb, changed)
        )
        popup.destroyed.connect(
            lambda _obj=None, current=popup:
            self._replacement_popup_destroyed(current)
        )

        popup.adjustSize()
        screen = (
            QApplication.screenAt(global_position)
            or QApplication.primaryScreen()
        )
        position = QPoint(global_position)
        if screen is not None:
            available = screen.availableGeometry()
            width = popup.sizeHint().width()
            height = popup.sizeHint().height()
            position.setX(max(
                available.left(),
                min(position.x(), available.right() - width + 1),
            ))
            position.setY(max(
                available.top(),
                min(position.y(), available.bottom() - height + 1),
            ))
        popup.move(position)
        popup.show()
        popup.raise_()
        popup.activateWindow()

    def _request_replacement(self, source_rgb):
        if source_rgb == self.background_rgb:
            return
        canvas = getattr(self.window(), "canvas", None)
        if canvas is None:
            return
        color = (
            canvas.sub_color
            if canvas.color_mode == "sub"
            else canvas.main_color
        )
        self._set_replacement_color(source_rgb, color)

    def register_replacements(self, mapping):
        registered = 0
        for source_rgb, target_rgb in mapping.items():
            source_rgb = tuple(int(value) for value in source_rgb[:3])
            target_rgb = tuple(int(value) for value in target_rgb[:3])
            if (
                source_rgb == self.background_rgb
                or source_rgb not in self.replacement_buttons
            ):
                continue
            self._set_replacement_color(source_rgb, QColor(*target_rgb))
            registered += 1
        return registered

    def _set_replacement_color(self, source_rgb, color):
        if source_rgb == self.background_rgb:
            return
        replacement = QColor(color)
        if not replacement.isValid():
            return
        self.replacements[source_rgb] = replacement
        self._set_replacement_button_style(source_rgb)

    def _clear_replacement(self, source_rgb):
        if source_rgb == self.background_rgb:
            return
        if self._replacement_popup_rgb == source_rgb:
            self._close_replacement_editor()
        self.replacements.pop(source_rgb, None)
        self._set_replacement_button_style(source_rgb)

    def _emit_replacements(self):
        mapping = {
            tuple(source): (color.red(), color.green(), color.blue())
            for source, color in self.replacements.items()
            if tuple(source) != self.background_rgb
            and tuple(source) in self.replacement_buttons
        }
        self.applyReplacementRequested.emit(mapping)

    def _unique_category_name(self, requested):
        base = str(requested or "").strip() or tr("新規フォルダー")
        name = base
        suffix = 2
        existing = {value.casefold() for value in self.category_order}
        while name.casefold() in existing:
            name = f"{base} {suffix}"
            suffix += 1
        return name

    def _prompt_create_category(self):
        name, accepted = QInputDialog.getText(
            self, tr("使用色フォルダー"), tr("フォルダー名："),
            text=self._unique_category_name(tr("新規フォルダー")),
        )
        if accepted and str(name).strip():
            self.create_category(str(name).strip())

    def create_category(self, name, record_history=True):
        name = self._unique_category_name(name)
        before = self.capture_history_state() if record_history else None
        self.category_order.append(name)
        self.category_collapsed[name] = False
        self._ensure_category_header(name)
        self._reapply_row_order()
        self._refresh_category_headers()
        if record_history:
            self._record_history(tr("使用色フォルダーを作成"), before)
        return name

    def _ensure_category_header(self, name):
        header = self.category_widgets.get(name)
        if header is not None:
            header.setName(name)
            return header
        header = ColorCategoryHeader(name, self.content)
        header.visibilityChanged.connect(
            lambda visible, category=name:
            self._set_category_visibility(category, visible)
        )
        header.collapsedChanged.connect(
            lambda collapsed, category=name:
            self._set_category_collapsed(category, collapsed)
        )
        header.colorDropped.connect(
            lambda rgb, category=name:
            self._move_colors_to_category(rgb, category)
        )
        header.contextMenuRequested.connect(
            lambda global_pos, category=name:
            self._show_category_context_menu(category, global_pos)
        )
        self.category_widgets[name] = header
        return header

    def _sync_category_headers(self):
        for name in list(self.category_widgets):
            if name in self.category_order:
                continue
            header = self.category_widgets.pop(name)
            self.rows.removeWidget(header)
            header.deleteLater()
        for name in self.category_order:
            self._ensure_category_header(name)

    def _category_members(self, name, existing_only=True):
        existing = set(self.source_buttons)
        return [
            self._rgb_key(color) for color in self.colors
            if self.category_colors.get(self._rgb_key(color)) == name
            and self._rgb_key(color) != self.background_rgb
            and (not existing_only or self._rgb_key(color) in existing)
        ]

    def _refresh_category_headers(self, *_args):
        for name in self.category_order:
            header = self.category_widgets.get(name)
            if header is None:
                continue
            members = self._category_members(name)
            header.setVisibilityState(
                [self.enabled_colors.get(rgb, True) for rgb in members]
            )
            header.name_label.setText(tr("📁 {name}（{len}色）").format(name=name, len=len(members)))
            header.setToolTip(
                tr("フォルダーへ色をドラッグして格納できます。"
                "右クリックで名前変更・削除ができます。")
            )

    def _set_category_collapsed(self, name, collapsed):
        if name not in self.category_order:
            return
        self.category_collapsed[name] = bool(collapsed)
        self._reapply_row_order()

    def _set_category_visibility(self, name, visible):
        members = self._category_members(name)
        if not members:
            self._refresh_category_headers()
            return
        with self._history_edit(tr("使用色フォルダーの表示切り替え")):
            self._isolated_rgb = None
            self._pre_isolate_enabled = None
            for rgb in members:
                self._set_checkbox_without_signal(rgb, bool(visible))
            self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _move_colors_to_category(self, dragged_rgb, category):
        if category not in self.category_order:
            return
        dragged_rgb = tuple(dragged_rgb)
        selected = {tuple(rgb) for rgb in self.selected_rgbs}
        sources = selected if dragged_rgb in selected else {dragged_rgb}
        sources.discard(self.background_rgb)
        if not sources:
            return
        with self._history_edit(tr("使用色をフォルダーへ移動")):
            for rgb in sources:
                self.category_colors[rgb] = category
            self.category_collapsed[category] = False
            header = self.category_widgets.get(category)
            if header is not None:
                header.setCollapsed(False)
            self._reapply_row_order()
            self._refresh_category_headers()

    def _move_colors_to_uncategorized(self, sources):
        sources = {tuple(rgb) for rgb in sources}
        with self._history_edit(tr("使用色をフォルダーから移動")):
            for rgb in sources:
                self.category_colors.pop(rgb, None)
            self._reapply_row_order()
            self._refresh_category_headers()

    def _show_category_context_menu(self, name, global_position):
        if name not in self.category_order:
            return
        menu = QMenu(self)
        rename_action = menu.addAction(tr("名前を変更…"))
        delete_action = menu.addAction(tr("フォルダーを削除"))
        chosen = menu.exec(global_position)
        if chosen is rename_action:
            new_name, accepted = QInputDialog.getText(
                self, tr("使用色フォルダー"), tr("フォルダー名："), text=name
            )
            new_name = str(new_name).strip()
            if accepted and new_name and new_name != name:
                self.rename_category(name, new_name)
        elif chosen is delete_action:
            self.delete_category(name)

    def rename_category(self, old_name, new_name):
        if old_name not in self.category_order:
            return
        if new_name.casefold() != old_name.casefold():
            new_name = self._unique_category_name(new_name)
        before = self.capture_history_state()
        index = self.category_order.index(old_name)
        self.category_order[index] = new_name
        for rgb, category in list(self.category_colors.items()):
            if category == old_name:
                self.category_colors[rgb] = new_name
        collapsed = self.category_collapsed.pop(old_name, False)
        self.category_collapsed[new_name] = collapsed
        old_header = self.category_widgets.pop(old_name, None)
        if old_header is not None:
            self.rows.removeWidget(old_header)
            old_header.deleteLater()
        self._ensure_category_header(new_name).setCollapsed(collapsed)
        self._reapply_row_order()
        self._refresh_category_headers()
        self._record_history(tr("使用色フォルダー名を変更"), before)

    def delete_category(self, name, record_history=True):
        if name not in self.category_order:
            return
        before = self.capture_history_state() if record_history else None
        self.category_order.remove(name)
        self.category_collapsed.pop(name, None)
        for rgb, category in list(self.category_colors.items()):
            if category == name:
                self.category_colors.pop(rgb, None)
        header = self.category_widgets.pop(name, None)
        if header is not None:
            self.rows.removeWidget(header)
            header.deleteLater()
        self._reapply_row_order()
        if record_history:
            self._record_history(tr("使用色フォルダーを削除"), before)

    def clear_categories(self):
        for header in self.category_widgets.values():
            self.rows.removeWidget(header)
            header.deleteLater()
        self.category_order = []
        self.category_colors = {}
        self.category_collapsed = {}
        self.category_widgets = {}
        self._reapply_row_order()

    def serialize_categories(self):
        return {
            "folders": [
                {
                    "name": name,
                    "collapsed": bool(self.category_collapsed.get(name, False)),
                }
                for name in self.category_order
            ],
            "assignments": {
                "#{:02X}{:02X}{:02X}".format(*rgb): category
                for rgb, category in self.category_colors.items()
                if category in self.category_order
            },
            "enabled": {
                "#{:02X}{:02X}{:02X}".format(*rgb): bool(enabled)
                for rgb, enabled in self.enabled_colors.items()
            },
        }

    def remap_saved_color_metadata(self, mapping):
        """色置換後も親子・タグ・フォルダー情報を新RGBへ引き継ぐ。"""
        normalized = {
            tuple(int(channel) for channel in source[:3]):
            tuple(int(channel) for channel in destination[:3])
            for source, destination in dict(mapping or {}).items()
        }

        def remap(rgb):
            return normalized.get(tuple(rgb), tuple(rgb))

        groups = {}
        for child, parent in self.child_to_parent.items():
            new_child, new_parent = remap(child), remap(parent)
            if (
                new_child != self.background_rgb
                and new_parent != self.background_rgb
                and new_child != new_parent
            ):
                groups[new_child] = new_parent
        self.child_to_parent = groups

        self.child_tags = {
            remap(rgb): tag for rgb, tag in self.child_tags.items()
            if remap(rgb) != self.background_rgb
        }
        self.parent_tags = {
            remap(rgb): tag for rgb, tag in self.parent_tags.items()
            if remap(rgb) != self.background_rgb
        }
        self.category_colors = {
            remap(rgb): category for rgb, category in self.category_colors.items()
            if remap(rgb) != self.background_rgb
        }
        self.enabled_colors = {
            remap(rgb): bool(enabled)
            for rgb, enabled in self.enabled_colors.items()
        }
        self.enabled_colors[self.background_rgb] = True
        self.mask_rgbs = {remap(rgb) for rgb in self.mask_rgbs}
        self.selected_rgbs = list(dict.fromkeys(
            remap(rgb) for rgb in self.selected_rgbs
            if remap(rgb) != self.background_rgb
        ))
        self.parent_rgb = (
            remap(self.parent_rgb) if self.parent_rgb is not None else None
        )

    def restore_categories(self, payload):
        if not isinstance(payload, dict):
            self.clear_categories()
            return
        self.clear_categories()
        folders = payload.get("folders", [])
        if isinstance(folders, list):
            for entry in folders:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).strip()
                if not name:
                    continue
                name = self.create_category(name, record_history=False)
                collapsed = bool(entry.get("collapsed", False))
                self.category_collapsed[name] = collapsed
                self.category_widgets[name].setCollapsed(collapsed)
        assignments = payload.get("assignments", {})
        if isinstance(assignments, dict):
            for color_hex, category in assignments.items():
                color = QColor(str(color_hex))
                if color.isValid() and category in self.category_order:
                    self.category_colors[self._rgb_key(color)] = category
        enabled = payload.get("enabled", {})
        if isinstance(enabled, dict):
            for color_hex, visible in enabled.items():
                color = QColor(str(color_hex))
                rgb = self._rgb_key(color)
                if color.isValid() and rgb in self.visibility_checks:
                    self._set_checkbox_without_signal(rgb, bool(visible))
        self._reapply_row_order()
        self._refresh_category_headers()
        self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _group_line_color(self, rgb):
        """rgb が親子グループに属していれば、束ねる縦ライン色（ルート親色）を返す。"""
        if rgb in self.child_to_parent:
            root = self._group_root(rgb)
            return QColor(*root)
        if any(parent == rgb for parent in self.child_to_parent.values()):
            return QColor(*rgb)
        return None

    def _apply_swatch_text(self, rgb):
        """色スウォッチにカラーコードを常時表示する。"""
        button = self.source_buttons.get(rgb)
        if button is None:
            return
        is_background = rgb == self.background_rgb
        is_child = rgb in self.child_to_parent
        hex_text = (
            tr("背景色 #FFFFFF") if is_background
            else "#{:02X}{:02X}{:02X}".format(*rgb)
        )
        if is_child:
            button.setText(f"　└ {hex_text}")
            return
        child_count = sum(
            1 for parent in self.child_to_parent.values() if parent == rgb
        )
        button.setText(
            tr("{text}（親・{count}）").format(text=hex_text, count=child_count) if child_count else hex_text
        )

    def _set_source_button_style(self, rgb):
        button = self.source_buttons.get(rgb)
        wrapper = self.source_wrappers.get(rgb)
        if button is None:
            return

        selected = rgb in self.selected_rgbs
        if wrapper is not None:
            wrapper.setSelected(selected)
            wrapper.setGroupLine(self._group_line_color(rgb))
        self._sync_selection_check(rgb)

        # 子色のボタン見た目（元色＋親色ボーダー）は _refresh_group_display が持つ。
        if rgb not in self.child_to_parent:
            color = QColor(*rgb)
            text_color = self._text_color(rgb)
            button.setStyleSheet(
                "QToolButton{"
                f"background:{color.name()};color:{text_color};"
                "border:1px solid rgba(255,255,255,0.55);padding:2px;"
                "}"
                "QToolButton:hover{"
                f"background:{color.name()};color:{text_color};"
                "border:1px solid rgba(255,255,255,0.55);"
                "}"
            )
            self._apply_swatch_text(rgb)

        if rgb == self.background_rgb:
            button.setToolTip(
                tr("背景色 #FFFFFF。並べ替えや親子付けの対象にはできません。")
            )
        else:
            group_note = ""
            parent_of_this = self.child_to_parent.get(rgb)
            if parent_of_this is not None:
                group_note = (
                    tr("　現在 #{value:02X}{value2:02X}{value3:02X} の子です。").format(value=parent_of_this[0], value2=parent_of_this[1], value3=parent_of_this[2])
                )
            button.setToolTip(
                tr("クリック：この色だけ選択／Shift＋クリック：範囲選択／"
                   "Ctrl＋クリック：選択に追加・解除。"
                   "ドラッグで並べ替え、色の中央へドロップ＝その色の子として整理。"
                   "親子付けしても色表示は変わりません。"
                   "解除はドラッグと右クリックで行えます。")
                + group_note
            )

    def _on_swatch_hover(self, rgb, hovered):
        if hovered:
            previous = self._hovered_rgb
            self._hovered_rgb = rgb
            if previous is not None and previous != rgb:
                self._apply_swatch_text(previous)
        elif self._hovered_rgb == rgb:
            self._hovered_rgb = None
        self._apply_swatch_text(rgb)

    def _clear_rows(self):
        self._close_replacement_editor()
        self.visibility_checks.clear()
        self.mask_checks.clear()
        self.selection_checks.clear()
        self.source_buttons.clear()
        self.source_wrappers.clear()
        self.replacement_buttons.clear()
        self.row_widgets.clear()
        while self.rows.count() > 1:
            item = self.rows.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def _remove_color_row(self, rgb):
        """Remove only one row so palette updates do not rebuild every widget."""
        if self._replacement_popup_rgb == rgb:
            self._close_replacement_editor()
        widget = self.row_widgets.pop(rgb, None)
        if widget is not None:
            self.rows.removeWidget(widget)
            widget.deleteLater()
        self.visibility_checks.pop(rgb, None)
        self.mask_checks.pop(rgb, None)
        self.selection_checks.pop(rgb, None)
        self.source_buttons.pop(rgb, None)
        self.source_wrappers.pop(rgb, None)
        self.replacement_buttons.pop(rgb, None)
        self.replacements.pop(rgb, None)

    def _make_source_wrapper(self, source, source_rgb):
        is_background = source_rgb == self.background_rgb
        wrapper = ColorSelectionArea()
        wrapper.setColorKey(source_rgb, is_background)
        if not is_background:
            wrapper.setCursor(Qt.CursorShape.OpenHandCursor)
        wrapper.clicked.connect(
            lambda modifiers, rgb=source_rgb:
            self._select_used_color(rgb, modifiers)
        )
        wrapper.colorDropped.connect(
            lambda dragged_rgb, mode, target_rgb=source_rgb:
            self._handle_color_drop(dragged_rgb, target_rgb, mode)
        )
        wrapper.contextMenuRequested.connect(
            lambda global_pos, rgb=source_rgb:
            self._show_used_color_context_menu(rgb, global_pos)
        )
        wrapper.hoverChanged.connect(
            lambda hovered, rgb=source_rgb: self._on_swatch_hover(rgb, hovered)
        )
        wrapper_layout = QHBoxLayout(wrapper)
        # 右余白は0にしてスウォッチを選択列いっぱいへ広げる。
        wrapper_layout.setContentsMargins(2, 3, 0, 3)
        wrapper_layout.setSpacing(0)
        wrapper_layout.addWidget(source)
        self.source_wrappers[source_rgb] = wrapper
        return wrapper

    def _append_color_row(self, color):
        qc = QColor(color)
        source_rgb = self._rgb_key(qc)
        is_background = source_rgb == self.background_rgb

        # 行全体では選択操作を受けない。
        # 「選択」列の source_wrapper だけが親子選択メニューを担当する。
        row_widget = QWidget()
        row = QGridLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setHorizontalSpacing(0)
        row.setColumnMinimumWidth(0, 32)
        row.setColumnMinimumWidth(1, 32)
        row.setColumnMinimumWidth(2, 32)
        row.setColumnMinimumWidth(3, 72)
        row.setColumnMinimumWidth(4, 72)
        row.setColumnStretch(3, 1)
        row.setColumnStretch(4, 1)

        selection_check = ColorSelectionCheckBox()
        selection_check.setChecked(source_rgb in self.selected_rgbs)
        selection_check.setEnabled(not is_background)
        selection_check.setToolTip(
            tr("背景色は選択できません。")
            if is_background else
            (
                tr("クリック：この色だけ選択／Shift＋クリック：範囲選択／"
                "Ctrl＋クリック：追加・解除／上下になぞる：一括選択／"
                "Alt＋クリック：この色だけ選択")
            )
        )
        if not is_background:
            selection_check.toggled.connect(
                lambda checked, rgb=source_rgb: self._on_selection_check_toggled(rgb, checked)
            )
            selection_check.altClicked.connect(
                lambda rgb=source_rgb: self._select_single_used_color(rgb)
            )
            selection_check.sweepStarted.connect(
                lambda checked, rgb=source_rgb: self._begin_selection_sweep(rgb, checked)
            )
            selection_check.sweepMoved.connect(self._move_selection_sweep)
            selection_check.sweepFinished.connect(self._end_selection_sweep)
        self.selection_checks[source_rgb] = selection_check

        visible_check = ColorVisibilityCheckBox()
        visible_check.setChecked(
            self.enabled_colors.get(source_rgb, True)
        )
        visible_check.setToolTip(
            (
                tr("クリック：背景表示ON/OFF／上下になぞる：一括ON/OFF／"
                "Alt＋クリック：背景だけ表示／右クリック：表示メニュー")
            )
            if is_background else
            (
                tr("クリック：表示ON/OFF／上下になぞる：一括ON/OFF／"
                "Alt＋クリック：この色だけ表示／右クリック：表示メニュー")
            )
        )
        visible_check.toggled.connect(
            lambda checked, rgb=source_rgb: self._set_color_visible(rgb, checked)
        )
        visible_check.altClicked.connect(
            lambda rgb=source_rgb: self._isolate_visible_color(rgb)
        )
        visible_check.sweepStarted.connect(
            lambda checked, rgb=source_rgb: self._begin_visibility_sweep(rgb, checked)
        )
        visible_check.sweepMoved.connect(self._move_visibility_sweep)
        visible_check.sweepFinished.connect(self._end_visibility_sweep)
        self.visibility_checks[source_rgb] = visible_check

        if self._mask_all_mode:
            self.mask_rgbs.add(source_rgb)
        mask_check = MaskColorCheckBox()
        mask_check.setChecked(source_rgb in self.mask_rgbs)
        mask_check.setToolTip(
            (
                tr("描画対象：ONにするとこの色の上へ描けます。"
                "クリック：ON/OFF／上下になぞる：一括ON/OFF／"
                "Alt＋クリック：この色だけON／右クリック：描画対象メニュー")
            )
            if not is_background else
            (
                tr("描画対象：ONにすると背景の上へ描けます。"
                "クリック：ON/OFF／上下になぞる：一括ON/OFF／"
                "Alt＋クリック：背景だけON／右クリック：描画対象メニュー")
            )
        )
        mask_check.toggled.connect(
            lambda checked, rgb=source_rgb: self._set_mask_color(rgb, checked)
        )
        mask_check.altClicked.connect(
            lambda rgb=source_rgb: self._isolate_mask_color(rgb)
        )
        mask_check.sweepStarted.connect(
            lambda checked, rgb=source_rgb: self._begin_mask_sweep(rgb, checked)
        )
        mask_check.sweepMoved.connect(self._move_mask_sweep)
        mask_check.sweepFinished.connect(self._end_mask_sweep)
        self.mask_checks[source_rgb] = mask_check

        source = SourceColorButton()
        source.setFixedHeight(26)
        source.setMinimumWidth(72)
        source.setText(
            tr("背景色 #FFFFFF")
            if is_background
            else qc.name(QColor.NameFormat.HexRgb).upper()
        )
        # 「選択」列のマウス操作はすべて外側の ColorSelectionArea が担当する。
        # これにより、ドラッグはスポイトではなく選択ON/OFFの連続切替になる。
        source.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.source_buttons[source_rgb] = source
        source_wrapper = self._make_source_wrapper(source, source_rgb)
        source_wrapper.setMinimumWidth(72)
        source_wrapper.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        source.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._set_source_button_style(source_rgb)

        # チェックボックスだけでなく、薄いグレーのセル全体をクリック領域にする。
        selection_area = CheckClickArea(selection_check)
        visible_area = CheckClickArea(visible_check)
        mask_area = CheckClickArea(mask_check)
        for widget in (selection_check, selection_area):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, w=widget, rgb=source_rgb:
                self._show_used_color_context_menu(rgb, w.mapToGlobal(pos))
            )
        for widget in (visible_check, visible_area):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, w=widget, rgb=source_rgb:
                self._show_visibility_context_menu(rgb, w.mapToGlobal(pos))
            )
        for widget in (mask_check, mask_area):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, w=widget, rgb=source_rgb:
                self._show_mask_context_menu(rgb, w.mapToGlobal(pos))
            )
        row.addWidget(selection_area, 0, 0)
        row.addWidget(visible_area, 0, 1)
        row.addWidget(mask_area, 0, 2)
        if is_background:
            row.addWidget(source_wrapper, 0, 3, 1, 2)
        else:
            replacement = ScreenEyedropButton()
            replacement.setFixedHeight(26)
            replacement.setMinimumWidth(72)
            replacement.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            replacement.setToolTip(
                tr("左クリック：RGB／HSV編集。右クリック：画面全体のスポイトへ切替。"
                "スポイト中は左クリックで確定、Escで解除。")
            )
            replacement.colorPicked.connect(
                lambda color, rgb=source_rgb:
                self._set_replacement_color(rgb, color)
            )
            replacement.colorEditorRequested.connect(
                lambda global_pos, rgb=source_rgb:
                self._toggle_replacement_editor(rgb, global_pos)
            )
            self.replacement_buttons[source_rgb] = replacement
            self._set_replacement_button_style(source_rgb)
            row.addWidget(source_wrapper, 0, 3)
            row.addWidget(replacement, 0, 4)

        self.row_widgets[source_rgb] = row_widget
        # 末尾（ストレッチの手前）へ追加する。並び順は _reapply_row_order で整える。
        self.rows.insertWidget(max(0, self.rows.count() - 1), row_widget)
        self._refresh_group_display(source_rgb)

    def _normalized_colors(self, colors):
        seen = set()
        result = []
        for color in colors:
            qc = QColor(color)
            key = self._rgb_key(qc)
            if key not in seen:
                seen.add(key)
                result.append(qc)
        return result

    def set_colors(self, colors):
        normalized = [
            color for color in self._normalized_colors(colors)
            if self._rgb_key(color) != self.background_rgb
        ]
        incoming_by_key = {
            self._rgb_key(color): QColor(color) for color in normalized
        }
        incoming_keys = {self.background_rgb, *incoming_by_key.keys()}
        self.replacements = {
            rgb: color for rgb, color in self.replacements.items()
            if rgb in incoming_keys and rgb != self.background_rgb
        }
        current_keys = {self._rgb_key(color) for color in self.colors}
        added = incoming_keys - current_keys
        removed = current_keys - incoming_keys
        palette_changed = bool(added or removed)

        old_mask_rgbs = set(self.mask_rgbs)
        old_selected = list(self.selected_rgbs)
        old_group_mapping = self._group_mapping()

        # 消えた色だけを削除する。全行再構築と色順の並べ替えを避ける。
        for rgb in tuple(removed):
            if rgb == self.background_rgb:
                continue
            self._remove_color_row(rgb)
            self.enabled_colors.pop(rgb, None)
            self.mask_rgbs.discard(rgb)
            # 消えた色が絡む親子関係は破棄する。
            self._drop_group_links_for(rgb)

        self.selected_rgbs = [
            rgb for rgb in self.selected_rgbs
            if rgb in incoming_keys and rgb != self.background_rgb
        ]
        self.parent_rgb = self.selected_rgbs[-1] if self.selected_rgbs else None

        # 既存の並びを維持し、新色だけ末尾へ追加する。
        retained = [
            QColor(color) for color in self.colors
            if self._rgb_key(color) in incoming_keys
            and self._rgb_key(color) != self.background_rgb
        ]
        self.colors = [QColor(*self.background_rgb), *retained]

        if self.background_rgb not in self.source_buttons:
            self.enabled_colors[self.background_rgb] = True
            self._append_color_row(QColor(*self.background_rgb))

        existing = {self._rgb_key(color) for color in self.colors}
        for color in normalized:
            rgb = self._rgb_key(color)
            if rgb in existing:
                continue
            self.colors.append(QColor(color))
            self.enabled_colors[rgb] = True
            self.mask_rgbs.add(rgb)
            self._append_color_row(color)
            existing.add(rgb)

        self.enabled_colors = {
            rgb: self.enabled_colors.get(rgb, True)
            for rgb in incoming_keys
        }
        for rgb in set(old_selected) | set(self.selected_rgbs):
            if rgb in self.source_buttons:
                self._set_source_button_style(rgb)

        # 消えた色で親子が壊れた場合に備え、正規化と表示更新を行う。
        self._normalize_groups()
        self._reorder_children_under_parents()
        self._reapply_row_order()
        self._refresh_all_group_displays()
        new_group_mapping = self._group_mapping()
        if new_group_mapping != old_group_mapping:
            self._emit_preview()
        self.count_label.setText(tr("{len}色").format(len=len(self.colors)))
        if self._isolated_rgb not in incoming_keys:
            self._isolated_rgb = None
            self._pre_isolate_enabled = None

        self._mask_all_mode = self.all_masks_enabled()
        if old_mask_rgbs != self.mask_rgbs:
            self.maskColorsChanged.emit(set(self.mask_rgbs))
        if old_selected != self.selected_rgbs:
            self.selectedColorsChanged.emit(set(self.selected_rgbs))
        if palette_changed:
            self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def add_color(self, color):
        """Add a newly drawn color immediately, before a whole-image scan."""
        qc = QColor(color)
        if not qc.isValid() or qc.alpha() == 0:
            return
        key = self._rgb_key(qc)
        if key in self.source_buttons:
            return
        self.colors.append(qc)
        self.enabled_colors[key] = True
        self.mask_rgbs.add(key)
        self._mask_all_mode = True
        self._append_color_row(qc)
        self.count_label.setText(tr("{len}色").format(len=len(self.colors)))
        self._emit_mask_state()
        self.visibleColorsChanged.emit(self.enabled_rgb_set())

    def _refresh_used_color_styles(self, affected=None):
        keys = set(self.source_buttons) if affected is None else set(affected)
        for key in keys:
            if key in self.source_buttons:
                self._set_source_button_style(key)

    def _set_used_color_parent(self, rgb):
        if rgb == self.background_rgb:
            return
        old = set(self.selected_rgbs)
        self.selected_rgbs = [value for value in self.selected_rgbs if value != rgb]
        self.selected_rgbs.append(rgb)
        self.parent_rgb = rgb
        self._refresh_used_color_styles(old | set(self.selected_rgbs))
        self.selectedColorsChanged.emit(set(self.selected_rgbs))

    def _show_used_color_context_menu(self, rgb, global_position):
        menu = QMenu(self)
        action_main_color = menu.addAction(tr("メインカラーにする"))
        menu.addSeparator()
        if rgb == self.background_rgb:
            chosen = menu.exec(global_position)
            if chosen is action_main_color:
                self.mainColorRequested.emit(QColor(*rgb))
            return
        parent = (
            tuple(self.parent_rgb)
            if self.parent_rgb is not None
            else None
        )

        selected_line_colors = {
            tuple(value) for value in self.selected_rgbs
            if tuple(value) != self.background_rgb
        }

        # 右クリックした色が選択中なら、親・子を含む選択色全体を削除する。
        # 未選択の色を右クリックした場合は、その色だけを削除対象にする。
        clicked_rgb = tuple(rgb)
        delete_sources = (
            set(selected_line_colors)
            if clicked_rgb in selected_line_colors
            else {clicked_rgb}
        )
        delete_sources.discard(self.background_rgb)

        action_delete = menu.addAction(tr("削除"))
        action_delete.setToolTip(
            tr("選択した使用色を #FFFFFF へ統合します。")
        )

        main_window: Any = self.window()
        canvas = getattr(main_window, "canvas", None)
        tween_running = bool(
            getattr(canvas, "tween_pending", None)
        )
        can_adjust_thickness = (
            not tween_running
            and parent is not None
            and tuple(rgb) == parent
            and parent in selected_line_colors
        )
        action_thickness = None
        if can_adjust_thickness:
            action_thickness = menu.addAction(tr("太さを調整"))
            action_thickness.setToolTip(
                tr("選択中の親色・子色をまとめて調整します。")
            )

        action_focus = menu.addAction(tr("対象に注視"))
        folder_menu = menu.addMenu(tr("フォルダーへ移動"))
        folder_actions = {}
        uncategorized_action = folder_menu.addAction(tr("未分類"))
        folder_menu.addSeparator()
        for category_name in self.category_order:
            action = folder_menu.addAction(category_name)
            folder_actions[action] = category_name
        folder_menu.addSeparator()
        new_folder_action = folder_menu.addAction(tr("新規フォルダー…"))
        menu.addSeparator()

        # 親子グループ関連。
        clicked_rgb = tuple(rgb)
        in_group = (
            clicked_rgb in self.child_to_parent
            or any(p == clicked_rgb for p in self.child_to_parent.values())
        )
        action_ungroup = None
        if in_group:
            action_ungroup = menu.addAction(tr("親子を解除"))
            action_ungroup.setToolTip(tr("この色に関わる親子関係を解除します。"))
        action_ungroup_all = None
        action_freeze = None
        if self.child_to_parent:
            action_ungroup_all = menu.addAction(tr("親子をすべて解除"))
            action_freeze = menu.addAction(tr("親子を統合（焼き込み）"))
            action_freeze.setToolTip(tr("登録した子→親の塗り替えを実画像へ確定します。"))
        action_clear = None
        if self.selected_rgbs:
            menu.addSeparator()
            action_clear = menu.addAction(tr("全選択解除"))

        chosen = menu.exec(global_position)
        if chosen is action_main_color:
            self.mainColorRequested.emit(QColor(*rgb))
        elif chosen is action_delete:
            self.deleteColorsRequested.emit(
                set(delete_sources)
            )
        elif action_thickness is not None and chosen is action_thickness:
            self.adjustLineThicknessRequested.emit(
                set(selected_line_colors)
            )
        elif chosen is action_focus:
            self._set_used_color_parent(rgb)
            self.focusColorRequested.emit(tuple(rgb))
        elif chosen is uncategorized_action:
            move_sources = (
                selected_line_colors
                if clicked_rgb in selected_line_colors
                else {clicked_rgb}
            )
            self._move_colors_to_uncategorized(move_sources)
        elif chosen in folder_actions:
            self._move_colors_to_category(
                clicked_rgb, folder_actions[chosen]
            )
        elif chosen is new_folder_action:
            name, accepted = QInputDialog.getText(
                self, tr("使用色フォルダー"), tr("フォルダー名："),
                text=self._unique_category_name(tr("新規フォルダー")),
            )
            if accepted and str(name).strip():
                category = self.create_category(str(name).strip())
                self._move_colors_to_category(clicked_rgb, category)
        elif action_ungroup is not None and chosen is action_ungroup:
            with self._history_edit(tr("親子を解除")):
                if self._drop_group_links_for(clicked_rgb):
                    self._normalize_groups()
                    self._refresh_all_group_displays()
                    self._emit_preview()
        elif action_ungroup_all is not None and chosen is action_ungroup_all:
            self._clear_groups()
        elif action_freeze is not None and chosen is action_freeze:
            self._emit_freeze()
        elif action_clear is not None and chosen is action_clear:
            self._clear_used_color_selection()

    # ------------------------------------------------------------------
    # Undo履歴（並べ替え・親子・表示/マスク）
    # ------------------------------------------------------------------
