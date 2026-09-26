from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from paintmaskanimator.color_panel import UsedColorPanel
from paintmaskanimator.color_panel_widgets import ScreenEyedropButton


def _app():
    return QApplication.instance() or QApplication([])


def _panel_with_colors():
    panel = UsedColorPanel()
    panel.set_colors([QColor("red"), QColor("blue"), QColor("green")])
    return panel


def test_group_commit_uses_single_merge_button():
    app = _app()
    panel = UsedColorPanel()
    try:
        buttons = [
            button.text()
            for button in panel.findChildren(QPushButton)
        ]
        assert buttons.count("統合") == 1
        assert "フリーズ" not in buttons
        assert not hasattr(panel, "freeze_button")
        assert panel.merge_button.text() == "統合"
    finally:
        panel.close()
        app.processEvents()


def test_merge_button_merges_selected_colors_into_last_selected():
    app = _app()
    panel = _panel_with_colors()
    red, blue, green = (255, 0, 0), (0, 0, 255), (0, 128, 0)
    merged = []
    panel.mergeColorsRequested.connect(
        lambda target, sources: merged.append((target, set(sources)))
    )
    try:
        assert not panel.merge_button.isEnabled()
        panel._select_used_color(red)
        assert not panel.merge_button.isEnabled()
        panel._select_used_color(blue, Qt.KeyboardModifier.ControlModifier)
        assert panel.merge_button.isEnabled()
        panel.merge_button.click()
        assert merged == [(blue, {red, blue})]
        assert green not in merged[0][1]
    finally:
        panel.close()
        app.processEvents()


def test_parent_child_grouping_never_recolors():
    """親子付けはフォルダー扱い。統合ボタンを有効にせず、色も変えない。"""
    app = _app()
    panel = _panel_with_colors()
    red, blue = (255, 0, 0), (0, 0, 255)
    merged = []
    previews = []
    panel.mergeColorsRequested.connect(lambda *args: merged.append(args))
    panel.previewGroupsChanged.connect(previews.append)
    try:
        panel._handle_color_drop(red, blue, "child")
        assert panel.child_to_parent == {red: blue}
        assert not panel.merge_button.isEnabled()
        assert merged == []
        assert all(mapping == {} for mapping in previews)
        assert not hasattr(panel, "freezeGroupsRequested")
    finally:
        panel.close()
        app.processEvents()


def test_category_moves_selected_colors_and_toggles_visibility():
    app = _app()
    panel = _panel_with_colors()
    red = (255, 0, 0)
    blue = (0, 0, 255)
    green = (0, 128, 0)
    try:
        category = panel.create_category("影色")
        panel.selected_rgbs = [red, blue]
        panel._move_colors_to_category(red, category)

        assert panel.category_colors[red] == "影色"
        assert panel.category_colors[blue] == "影色"
        panel._set_category_visibility("影色", False)
        assert not panel.enabled_colors[red]
        assert not panel.enabled_colors[blue]
        assert panel.enabled_colors[green]
    finally:
        panel.close()
        app.processEvents()


def test_category_serialization_restores_folder_assignment_and_state():
    app = _app()
    panel = _panel_with_colors()
    restored = _panel_with_colors()
    red = (255, 0, 0)
    try:
        category = panel.create_category("影色")
        panel._move_colors_to_category(red, category)
        panel._set_category_visibility(category, False)
        panel._set_category_collapsed(category, True)

        restored.restore_categories(panel.serialize_categories())

        assert restored.category_order == ["影色"]
        assert restored.category_colors[red] == "影色"
        assert restored.category_collapsed["影色"]
        assert not restored.enabled_colors[red]
        assert restored.row_widgets[red].isHidden()
    finally:
        panel.close()
        restored.close()
        app.processEvents()


def test_category_structure_is_undo_snapshot_state():
    app = _app()
    panel = _panel_with_colors()
    red = (255, 0, 0)
    try:
        before = panel.capture_history_state()
        category = panel.create_category("影色", record_history=False)
        panel._move_colors_to_category(red, category)
        panel.restore_history_state(before)

        assert panel.category_order == []
        assert red not in panel.category_colors
    finally:
        panel.close()
        app.processEvents()


def test_category_member_is_indented_and_folder_header_is_distinct():
    app = _app()
    panel = _panel_with_colors()
    red = (255, 0, 0)
    try:
        category = panel.create_category("影色")
        panel._move_colors_to_category(red, category)

        row_layout = panel.row_widgets[red].layout()
        assert row_layout.contentsMargins().left() >= 20
        assert panel.row_widgets[red].objectName() == "usedColorCategoryMember"
        header = panel.category_widgets[category]
        assert header.minimumHeight() >= 32
        assert "📁 影色" in header.name_label.text()

        panel._move_colors_to_uncategorized({red})
        assert row_layout.contentsMargins().left() == 0
        assert panel.row_widgets[red].objectName() == "usedColorRow"
    finally:
        panel.close()
        app.processEvents()


def test_replacement_color_can_be_registered_and_emitted():
    app = _app()
    panel = _panel_with_colors()
    red = (255, 0, 0)
    emitted = []
    panel.applyReplacementRequested.connect(emitted.append)
    try:
        panel._set_replacement_color(red, QColor("blue"))

        assert panel.replacement_buttons[red].text() == "#0000FF"
        panel._emit_replacements()
        assert emitted == [{red: (0, 0, 255)}]

        panel._clear_replacement(red)
        assert panel.replacement_buttons[red].text() == "未設定"
    finally:
        panel.close()
        app.processEvents()


def test_replacement_button_opens_editor_on_left_and_eyedropper_on_right():
    app = _app()
    button = ScreenEyedropButton()
    button.resize(80, 26)
    button.show()
    app.processEvents()
    editor_positions = []
    picker_starts = []
    button.colorEditorRequested.connect(editor_positions.append)
    button._begin_global_screen_pick = lambda: picker_starts.append(True)
    try:
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert len(editor_positions) == 1
        assert picker_starts == []

        QTest.mouseClick(button, Qt.MouseButton.RightButton)
        assert picker_starts == [True]
        assert len(editor_positions) == 1
    finally:
        button.close()
        app.processEvents()
