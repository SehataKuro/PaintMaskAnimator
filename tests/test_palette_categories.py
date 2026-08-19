from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QPushButton

from paintmaskanimator.color_panel import UsedColorPanel


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
        assert not hasattr(panel, "merge_button")
        assert panel.freeze_button.text() == "統合"
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
