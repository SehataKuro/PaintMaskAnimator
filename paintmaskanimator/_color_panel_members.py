"""Type-only member declarations shared across the used-color panel mixins.

This module exists purely to give pyright/pylance the attributes, methods and
signals really provided at runtime by the composed ``UsedColorPanel`` class (and
its Qt base). Each mixin lives in its own file, so a checker inspecting one mixin
in isolation cannot see members defined on its siblings or set in
``UsedColorPanel.__init__``; without these stubs every such access was reported
as ``reportAttributeAccessIssue``.

At runtime ``UsedColorPanelMembers`` is an empty ``object`` subclass, so mixing
it in is harmless. Under ``TYPE_CHECKING`` it inherits the real Qt base, so Qt
methods resolve too. A genuinely undefined name -- a typo or a removed
attribute -- still surfaces, because it never appears in this list.

Entries are being migrated from ``Any`` to their real types; every one that
moves off ``Any`` starts type-checking its own call sites, so prefer adding a
precise type here over widening one. ``Any`` remains the default for the many
method stubs, whose signatures live with the mixin that defines them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QWidget
    _MembersBase = QWidget
else:
    _MembersBase = object


class UsedColorPanelMembers(_MembersBase):
    if TYPE_CHECKING:
        adjustLineThicknessRequested: Signal
        applyReplacementRequested: Signal
        clearIsolateRequested: Signal
        deleteColorsRequested: Signal
        focusColorRequested: Signal
        freezeGroupsRequested: Signal
        historyStatePush: Signal
        isolateColorClicked: Signal
        mainColorRequested: Signal
        maskColorsChanged: Signal
        mergeColorsRequested: Signal
        previewGroupsChanged: Signal
        selectedColorsChanged: Signal
        sourceScreenColorPicked: Signal
        visibleColorsChanged: Signal
        add_color: Any
        all_masks_enabled: Any
        _append_color_row: Any
        apply_button: Any
        _apply_selection_state: Any
        _apply_swatch_text: Any
        background_rgb: Any
        _begin_mask_sweep: Any
        _begin_selection_sweep: Any
        _begin_visibility_sweep: Any
        capture_history_state: Any
        category_collapsed: Any
        category_colors: Any
        category_order: Any
        category_widgets: Any
        _checkbox_rgb_at_global: Any
        child_to_parent: Any
        _clear_groups: Any
        _clear_mask_colors: Any
        _clear_replacement: Any
        clear_masks_button: Any
        _clear_rows: Any
        clear_categories: Any
        clear_selection_button: Any
        _clear_used_color_selection: Any
        _color_index: Any
        colors: Any
        content: Any
        count_label: Any
        _disable_other_masks: Any
        _disable_other_visible_colors: Any
        _drop_group_links_for: Any
        _drop_group_links_outside_parent_blocks: Any
        _emit_freeze: Any
        _emit_mask_state: Any
        _emit_merge: Any
        _emit_preview: Any
        _emit_replacements: Any
        enabled_colors: Any
        enabled_rgb_set: Any
        _end_mask_sweep: Any
        _end_selection_sweep: Any
        _end_visibility_sweep: Any
        freeze_button: Any
        _group_line_color: Any
        _group_mapping: Any
        _group_root: Any
        _handle_color_drop: Any
        _history_edit: Any
        _history_significant: Any
        _history_suspended: Any
        _category_members: Any
        _refresh_category_headers: Any
        _sync_category_headers: Any
        _hovered_rgb: Any
        _isolate_mask_color: Any
        _isolate_visible_color: Any
        _isolated_rgb: Any
        _make_child_of: Any
        _make_source_wrapper: Any
        _mask_all_mode: Any
        _mask_alt_rgb: Any
        _mask_checkbox_rgb_at_global: Any
        mask_checks: Any
        mask_rgbs: Any
        _mask_sweep_active: Any
        _mask_sweep_changed: Any
        _mask_sweep_state: Any
        _mask_sweep_touched: Any
        merge_button: Any
        _move_mask_sweep: Any
        _move_selection_sweep: Any
        _move_visibility_sweep: Any
        _normalize_groups: Any
        _normalized_colors: Any
        on_groups_frozen: Any
        _on_selection_check_toggled: Any
        _on_swatch_hover: Any
        _ordered_non_background_rgbs: Any
        parent_rgb: Any
        _pre_isolate_enabled: Any
        _pre_mask_alt: Any
        _reapply_row_order: Any
        _record_history: Any
        register_replacements: Any
        replacement_buttons: Any
        replacements: Any
        _refresh_all_group_displays: Any
        _refresh_group_display: Any
        _refresh_used_color_styles: Any
        _remove_color_row: Any
        _reorder_children_under_parents: Any
        _reorder_color: Any
        _reorder_color_block: Any
        restore_history_state: Any
        _retain_parent_selection: Any
        _rgb_key: Any
        row_widgets: Any
        rows: Any
        scroll: Any
        select_matching_color: Any
        _select_single_used_color: Any
        _select_used_color: Any
        selected_rgb_set: Any
        selected_rgbs: Any
        _selection_anchor_rgb: Any
        _selection_check_rgb_at_global: Any
        selection_checks: Any
        _selection_sweep_active: Any
        _selection_sweep_changed: Any
        _selection_sweep_state: Any
        _selection_sweep_touched: Any
        _set_all_masks_on: Any
        _set_checkbox_without_signal: Any
        _set_color_visible: Any
        set_colors: Any
        _set_mask_checkbox_without_signal: Any
        _set_mask_color: Any
        _set_replacement_button_style: Any
        _set_replacement_color: Any
        _set_single_mask_state: Any
        _set_source_button_style: Any
        _set_used_color_parent: Any
        _set_visibility_state: Any
        show_all_button: Any
        _show_all_colors: Any
        _show_mask_context_menu: Any
        _show_used_color_context_menu: Any
        _show_visibility_context_menu: Any
        source_buttons: Any
        source_wrappers: Any
        _sweep_history_before: Any
        _sync_selection_check: Any
        _text_color: Any
        _toggle_used_color_selection: Any
        visibility_checks: Any
        _visibility_sweep_active: Any
        _visibility_sweep_changed: Any
        _visibility_sweep_state: Any
        _visibility_sweep_touched: Any
