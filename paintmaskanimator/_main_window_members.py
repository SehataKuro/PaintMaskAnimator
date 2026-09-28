"""Type-only member declarations shared across the main window mixins.

This module exists purely to give pyright/pylance the attributes, methods and
signals really provided at runtime by the composed ``MainWindow`` class (and its Qt
base). Each mixin lives in its own file, so a checker inspecting one mixin in
isolation cannot see members defined on its siblings or set in
``MainWindow.__init__``; without these stubs every such access was reported as
``reportAttributeAccessIssue``.

At runtime ``MainWindowMembers`` is an empty ``object`` subclass, so mixing it in
is harmless. Under ``TYPE_CHECKING`` it inherits the real Qt base, so Qt
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
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMainWindow
    from .actionpanel import ActionPanel
    from .canvas import PaintCanvas
    from .main_window_export import ExportController
    from .main_window_workspace import WorkspaceController
    from .main_window_autosave import AutosaveController
    from .main_window_color_interaction import ColorInteractionController
    from .main_window_used_color import UsedColorController
    from .main_window_line_ops import LineOpsController
    from .main_window_timeline_ops import TimelineOpsController
    from .main_window_layer_ops import LayerOpsController
    from .main_window_color_chart import ColorChartController
    from .main_window_onion import OnionSkinController
    from .main_window_tween import TweenController
    from .main_window_time_remap import TimeRemapController
    from .main_window_scope_ops import ScopeOpsController
    from .main_window_project_io import ProjectIOController
    from .main_window_import import ImportController
    from .color_panel import UsedColorPanel
    from .color_chart import ColorChartPanel
    from .history_panel import HistoryPanel
    from .theme import StatusBar
    from .toolpanel import ToolPanel, ToolSelectorPanel
    from .timeline import TimelineWidget
    _MembersBase = QMainWindow
else:
    _MembersBase = object

class MainWindowMembers(_MembersBase):
    if TYPE_CHECKING:
        _active_dragged_dock: Any
        _add_dock_hamburger: Any
        _add_dock_hamburger_old: Any
        _apply_time_remap_states_to_layer: Any
        _apply_tool_selector_snap: Any
        _attach_area_hamburger: Any
        _attach_dock_hamburger: Any
        _autosave_timer: Any
        _auxiliary_cursor_targets: Any
        _build_color_slider_menu: Any
        _build_color_wheel_menu: Any
        _build_default_dock_menu: Any
        _close_tween_command_popup: Any
        _closing: Any
        _commit_split_drop: Any
        _configure_floating_window: Any
        _copy_layer_display_properties: Any
        _customize_docking_hover: Any
        _dock_menu_builders: Any
        _download_and_run_installer: Any
        _event_key_token: Any
        _expand_docking_hit_zones: Any
        _finalize_startup_dock_ui: Any
        _finish_auxiliary_hold_drag: Any
        _hamburger_icon: Any
        _hamburger_icon_cache: Any
        _hand_scroll_area_for_widget: Any
        _handle_auxiliary_hold_event: Any
        _held_canvas_shortcut_tokens: Any
        _hide_split_drop_feedback: Any
        _layer_indices_from_rows: Any
        _mouse_global_position: Any
        _navigate_sequence_number: Any
        _normalize_shortcut_token: Any
        _on_canvas_cell_changed: Any
        _onion_settings_browser: Any
        _onion_settings_browser_destroyed: Any
        _onion_settings_window: Any
        _parse_after_effects_time_remap: Any
        _parse_toei_timesheet: Any
        _parse_xdts_timesheet: Any
        _pending_default_dock_layout: bool
        _pending_tool_selector_snap: Any
        _pending_visible_colors: Any
        _playback_emitted_steps: Any
        _playback_started_at: Any
        _previous_draw_color_mode: Any
        _rebuild_area_dock_menu: Any
        _rebuild_dock_menu: Any
        _refresh_theme_dependent_ui: Any
        _refresh_timeline_tween_marker: Any
        _resize_tool_selector_area: Any
        _restore_timeline_selection: Any
        _set_color_chart_visible: Any
        _setup_split_drop_overlay: Any
        _sheet_duration: Any
        _shortcut_tokens: Any
        _show_split_skeleton: Any
        _show_tween_command_popup: Any
        _snap_tool_selector_width: Any
        _split_drop_candidate: Any
        _split_drop_dragged_dock: Any
        _split_drop_overlay: Any
        _split_drop_press_pos: Any
        _split_drop_skeletons: Any
        _split_drop_source_area: Any
        _split_drop_timer: Any
        _start_split_drop_monitor: Any
        _suppress_used_color_refresh_once: Any
        _sync_all_area_hamburgers: Any
        _sync_area_hamburger: Any
        _sync_floating_title: Any
        _sync_modifier_tokens: Any
        _time_remap_source_bank: Any
        _tool_selector_resize_drag_active: Any
        _tool_selector_snap_timer: QTimer
        _tween_command_popup: Any
        _ui_hold_drag_mode: Any
        _ui_hold_grab_widget: Any
        _ui_hold_last_global: Any
        _ui_hold_scroll_area: Any
        _ui_hold_start_global: Any
        _ui_hold_start_scroll: Any
        _update_auxiliary_hold_cursors: Any
        _update_canvas_hold_operation: Any
        _update_split_drop_target: Any
        _used_color_cache: Any
        _used_color_layer_cache: Any
        _used_color_request: Any
        _used_color_timer: QTimer
        _visible_color_timer: QTimer
        _widget_in_timeline: Any
        a_bucket_close_gap: Any
        a_bucket_include_sub: Any
        a_choose_background: Any
        a_clear_color_filter: Any
        a_copy: Any
        a_cut: Any
        a_dust_all_frames: Any
        a_dust_apply: Any
        a_export_cut_folder: Any
        a_export_mp4: Any
        a_export_png_seq: Any
        a_export_psd: Any
        a_export_tga_seq: Any
        a_export_xdts: Any
        a_hold_eyedropper: Any
        a_hold_hand: Any
        a_hold_rotate: Any
        a_hold_zoom: Any
        a_import_folder: Any
        a_import_images: Any
        a_import_images_raw: Any
        a_import_psd: Any
        a_import_clip: Any
        a_open_cut_folder: Any
        a_isolate_color: Any
        a_mainline_repaint: Any
        a_new: Any
        a_next: Any
        a_open_project: Any
        a_paste: Any
        a_pressure: Any
        a_color_chart: Any
        a_prev: Any
        a_redo: Any
        a_remove_dust: Any
        a_resize: Any
        a_save: Any
        a_save_project: Any
        a_save_project_as: Any
        a_save_tga: Any
        a_select_main: Any
        a_select_sub: Any
        a_select_transparent: Any
        a_selection_all_frames: Any
        a_selection_clear: Any
        a_selection_mesh: Any
        a_selection_scale: Any
        a_selection_transform: Any
        a_preferences: Any
        apply_pressure_settings: Any
        insert_layout_paper: Any
        preferences_dialog: Any
        a_shortcuts: Any
        a_silhouette: Any
        a_swap_main_sub: Any
        a_tl_add_exposure: Any
        a_tl_delete_frame: Any
        a_tl_layer_add: Any
        a_tl_layer_delete: Any
        a_tl_layer_rename: Any
        a_tl_next: Any
        a_tl_next_key: Any
        a_tl_onion: Any
        a_tl_paste_time_remap: Any
        a_tl_play: Any
        a_tl_previous: Any
        a_tl_previous_key: Any
        a_toggle_draw_background: Any
        a_transform_cancel: Any
        a_transform_commit: Any
        a_transform_mesh_grid: Any
        a_transform_rotate_left: Any
        a_transform_rotate_right: Any
        a_undo: Any
        action_panel: ActionPanel
        action_panel_dock: Any
        action_panel_scroll: Any
        advance: Any
        area: Any
        build_action_panel: Any
        build_actions: Any
        build_menu: Any
        build_project_metadata: Any
        build_ui: Any
        canvas: PaintCanvas
        canvas_operation_actions: Any
        central_dock: Any
        check_for_updates_interactive: Any
        color_slider_dock: Any
        color_slider_scroll: Any
        color_wheel_dock: Any
        color_wheel_scroll: Any
        color_chart: ColorChartPanel
        color_chart_data: Any
        color_chart_window: Any
        column_group: Any
        component_list: Any
        crop_image: Any
        current_project_path: Any
        dock_manager: Any
        # Collaborator objects the window owns; unlike the mixin methods below,
        # these carry their real type, so their call sites are fully checked.
        export: ExportController
        workspace: WorkspaceController
        autosave: AutosaveController
        colors: ColorInteractionController
        used_color: UsedColorController
        line_ops: LineOpsController
        timeline_ops: TimelineOpsController
        layers: LayerOpsController
        color_chart_ops: ColorChartController
        onion: OnionSkinController
        tween: TweenController
        time_remap: TimeRemapController
        scope: ScopeOpsController
        project: ProjectIOController
        importer: ImportController
        exposure_images: Any
        file_edit_actions: Any
        fit_canvas: Any
        general_actions: Any
        history_panel: HistoryPanel
        import_dropped_image: Any
        import_dropped_images: Any
        import_dropped_images_raw: Any
        import_image_folder_dialog: Any
        import_images_dialog: Any
        import_images_raw_dialog: Any
        make_shortcut_action: Any
        new_doc: Any
        normalized_onion_levels: Any
        on_progress: Any
        palette: UsedColorPanel
        palette_dock: Any
        palette_scroll: Any
        play: Any
        prepare_color_reduction: Any
        prepare_image_import: Any
        pressure: Any
        refresh_selection: Any
        refresh_ui: Any
        relative_shift: Any
        replace_doc: Any
        resize_doc: Any
        rot: Any
        rot_label: Any
        save_png: Any
        save_tga: Any
        save_tga_image: Any
        set_rot: Any
        set_theme: Any
        set_zoom: Any
        shortcuts: Any
        show_canvas_at_100_percent: Any
        show_preferences: Any
        status: Any
        status_bar: StatusBar
        sync_canvas_view_controls: Any
        timeline: TimelineWidget
        timeline_actions: Any
        timeline_dock: Any
        timer: QTimer
        tool_action_list: Any
        tool_actions: Any
        tool_command_actions: Any
        tool_selector: ToolSelectorPanel
        tool_selector_dock: Any
        tools: ToolPanel
        tools_dock: Any
        tools_scroll: Any
        turn_priority: Any
        workspace_menu: Any
        zoom: Any
        zoom_label: Any
