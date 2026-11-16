"""使用色パネルの「通常｜置き換え」モードと、新しい行・ボタンのふるまい。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QMenu

from paintmaskanimator.color_panel import UsedColorPanel

RED, BLUE, GREEN = (255, 0, 0), (0, 0, 255), (0, 128, 0)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp):
    panel = UsedColorPanel()
    panel.set_colors([QColor(*RED), QColor(*BLUE), QColor(*GREEN)])
    try:
        yield panel
    finally:
        panel._close_replacement_editor()
        panel.close()
        qapp.processEvents()


def select(panel, *rgbs):
    panel._select_used_color(rgbs[0])
    for rgb in rgbs[1:]:
        panel._select_used_color(rgb, Qt.KeyboardModifier.ControlModifier)


def test_normal_mode_hides_replacements_and_shows_row_toggles(panel):
    assert not panel.replace_mode
    assert panel.normal_toolbar.isVisibleTo(panel)
    assert not panel.replace_toolbar.isVisibleTo(panel)
    assert not panel.apply_button.isVisibleTo(panel)
    assert not panel.replacement_buttons[RED].isVisibleTo(panel)
    visible_area, mask_area = panel.toggle_areas[RED]
    assert visible_area.isVisibleTo(panel) and mask_area.isVisibleTo(panel)


def test_replace_mode_shows_replacement_next_to_each_color(panel):
    panel.mode_switch.replace_button.click()

    assert panel.replace_mode
    assert panel.replace_toolbar.isVisibleTo(panel)
    assert not panel.normal_toolbar.isVisibleTo(panel)
    assert panel.apply_button.isVisibleTo(panel)
    assert panel.replacement_buttons[RED].isVisibleTo(panel)
    # 置き換えモードでは表示・保護は触れない。
    for area in panel.toggle_areas[RED]:
        assert not area.isVisibleTo(panel)

    panel.mode_switch.normal_button.click()
    assert not panel.replace_mode
    assert not panel.replacement_buttons[RED].isVisibleTo(panel)


def test_merge_button_shows_the_last_selected_color_as_its_target(panel):
    assert panel.merge_button.target() is None
    select(panel, RED, BLUE)

    assert panel.merge_button.isEnabled()
    assert panel.merge_button.target() == BLUE
    assert panel.merge_button.text() == "統合"
    # 最後に選んだ行だけ枠で囲む。
    assert panel.source_wrappers[BLUE]._last
    assert not panel.source_wrappers[RED]._last
    assert panel.source_wrappers[RED]._selected


def test_selection_count_is_shown_at_the_bottom(panel):
    assert panel.selection_status.text() == "選択なし"
    select(panel, RED, BLUE)
    assert panel.selection_status.text() == "2色を選択中"


def test_replace_tools_set_and_clear_the_selected_colors(panel, monkeypatch):
    panel.set_replace_mode(True)
    assert not panel.replace_from_main_button.isEnabled()
    select(panel, RED, BLUE)
    assert panel.replace_from_main_button.isEnabled()
    assert not panel.replace_clear_button.isEnabled()

    panel._set_replacements_for_selection(QColor(*GREEN), "test")
    assert panel.current_replacements() == {RED: GREEN, BLUE: GREEN}
    assert panel.replacement_code_labels[RED].text() == "008000"
    assert panel.replace_clear_button.isEnabled()

    panel.replace_clear_button.click()
    assert panel.current_replacements() == {}
    assert panel.replacement_buttons[RED].text() == "未設定"


def test_apply_replaces_every_frame_without_a_range_choice(panel):
    emitted = []
    panel.applyReplacementRequested.connect(emitted.append)
    panel.set_replace_mode(True)
    panel._set_replacement_color(RED, QColor(*GREEN))

    panel.apply_button.click()

    assert emitted == [{RED: GREEN}]


def test_hidden_colors_look_faded_in_normal_mode(panel):
    panel._set_color_visible(RED, False)
    style = panel.source_buttons[RED].styleSheet()
    assert "dashed" in style
    assert "placeholder-text" in panel.code_labels[RED].styleSheet()


def test_panel_menu_holds_the_bulk_actions(panel):
    menu = QMenu()
    panel.build_panel_menu(menu)
    texts = [action.text() for action in menu.actions() if action.text()]
    for expected in (
        "色の選択をすべて解除", "すべての色を表示", "すべての色の保護を外す",
        "置換色を保存…", "置換色を読み込む…", "置換色をすべて外す",
    ):
        assert expected in texts
