from .common import *  # noqa: F401,F403
from . import constants
from . import color_ops, colors, geometry, imaging
from .document import Document
from .models import Layer, make_frame
from .pressure import _pressure_bezier_at
from .timeline import TimelineWidget
from .toolpanel import ToolPanel
from .utils import blank_image, natural_path_key, workspace_size
from .logging_setup import get_logger

log = get_logger(__name__)


class PaintCanvas(QWidget):
    status_message=Signal(str)
    colorUsed=Signal(QColor)
    viewChanged=Signal(float, float)
    onionInteractionChanged=Signal()
    onionInteractionFinished=Signal()
    changed=Signal(); selectionChanged=Signal(); selectionCleared=Signal(); cellChanged=Signal(int,int); imagesDropped=Signal(object); projectDropped=Signal(str); timeRemapDropped=Signal(str); colorSampled=Signal(QColor)
    def __init__(self):
        super().__init__(); self.setFocusPolicy(Qt.FocusPolicy.StrongFocus); self.setMouseTracking(True); self.setTabletTracking(True); self.setMinimumSize(320,120); self.setAcceptDrops(True)
        self._document=Document()
        self._playback_active=False
        self._playback_frame_cache={}
        self._playback_cache_limit=14
        self._playback_resolved_keys=[]
        # 連番読込直後の画像を、タイムリマップ再配置用に保持する。
        self._sequence_source_bank=[]
        self._sequence_source_bank_layer_index=-1
        self._sequence_source_bank_layer_name=""
        # シートから外した絵番号を、右クリックで再配置するため保持する。
        self._sequence_archive={}
        self.timeline_mode="sheet"
        self.tool="brush"; self.temp_tool=None; self.main_color=QColor("black"); self.sub_color=QColor(255,0,0); self.color_mode="main"; self.transparent_display_color=QColor("white"); self.background_mask_rgb=(255,255,255); self.mask_color_rgbs=set(); self.mask_color_rgb=None; self.mask_all_enabled=True; self.selected_used_color_rgbs=set(); self.visible_color_rgbs=None
        self.selection_polygon=[]
        self.selection_mask_override=None
        self.selection_outline_polygons=[]
        self.selection_mask_rect=None
        self._selection_fade_opacity=1.0
        self._selection_fade_started=time.monotonic()
        self._selection_blink_timer=QTimer(self)
        self._selection_blink_timer.setInterval(40)
        self._selection_blink_timer.timeout.connect(
            self._update_selection_fade
        )
        self._selection_blink_timer.start()
        self.rect_start=None
        self.rect_end=None
        self.transform_active=False
        self.transform_mode=None
        self.transform_original_layer=None
        self.transform_source=None
        self.transform_source_rect=None
        self.transform_points=[]
        self.transform_handle=-1
        self.transform_drag_kind=None
        self.transform_drag_start=QPointF()
        self.transform_drag_points=[]
        self.transform_original_has_content=False
        self.transform_frame_index=0
        self.transform_layer_index=0
        self.transform_apply_all_frames=False
        self.tween_pending = None
        self.transform_quality=False
        self.transform_quality_active=False
        self.transform_line_threshold=96
        self.transform_tp_line_colors=()
        # TP_mask v0.7 compatible data.  The selected image is converted to
        # one monochrome mask per exact color, then every mask is transformed
        # independently and recombined without interpolation colors.
        self.transform_tp_palette=[]
        self.transform_tp_masks=[]
        self.transform_tp_line_masks=[]
        self.transform_tp_prepared_preview=None
        self.transform_tp_fill_smoothing=0.55
        self.transform_tp_line_smoothing=0.85
        self._tp_geometry_cache_key=None
        self._tp_geometry_cache_bbox=None
        self._tp_geometry_cache_fill_overlay=None
        self._tp_geometry_cache_line_soft=[]
        self._tp_preview_cache_key=None
        self._tp_preview_cache_image=None
        self._tp_mask_source_key=None
        self._tp_proxy_source_key=None
        self._tp_proxy_source_image=None
        self._tp_proxy_rendering=False
        # V62: quality preview is generated outside paintEvent.  While the
        # per-color TP masks are transformed, a counter popup reports progress.
        self._tp_preview_progress_busy=False
        self._tp_preview_progress_scheduled=False
        self._transform_line_adjusting=False
        self._transform_line_preview_timer=QTimer(self)
        self._transform_line_preview_timer.setSingleShot(True)
        self._transform_line_preview_timer.setInterval(120)
        self._transform_line_preview_timer.timeout.connect(
            self._render_deferred_transform_line_preview
        )
        self.transform_mesh_cols=4
        self.transform_mesh_rows=4
        self.transform_mesh_grid=4  # legacy compatibility
        self.transform_mesh_reference_points=[]
        self.pen_size=8; self.pen_opacity=1.0
        self.brush_stabilizer_strength = 0
        self._stabilized_canvas = None
        self._last_raw_canvas = None
        self._brush_stabilizer_history = []
        self._last_brush_pressure = 1.0
        self._pressure_input_history = []
        self._brush_follow_settle = 0.0
        self._brush_follow_timer = QTimer(self)
        self._brush_follow_timer.setInterval(16)
        self._brush_follow_timer.timeout.connect(
            self._advance_stabilized_brush
        )
        self.pressure_enabled=True; self.pressure_min=.1; self.pressure_max=1.0; self.pressure_curve=1.0; self.pressure_curve_points=[[0.0,0.0],[0.5,0.5],[1.0,1.0]]
        self.zoom=1.0; self.pan=QPointF(); self.rotation=0.0; self.flip_horizontal=False; self.onion_skin=False
        self.onion_previous_count=1
        self.onion_next_count=1
        self.onion_previous_opacity=0.22
        self.onion_next_opacity=0.22
        self.onion_previous_color=QColor(255, 92, 92)
        self.onion_next_color=QColor(92, 160, 255)
        self.onion_previous_color_enabled=False
        self.onion_next_color_enabled=False
        self.onion_selected_colors_only=False
        self.onion_previous_shift_x=0.0
        self.onion_previous_shift_y=0.0
        self.onion_previous_rotation=0.0
        self.onion_previous_scale=100.0
        self.onion_next_shift_x=0.0
        self.onion_next_shift_y=0.0
        self.onion_next_rotation=0.0
        self.onion_next_scale=100.0
        # TU/TB用のカメラ倍率。表示用デジタルズームself.zoomとは分離。
        self.onion_tu_tb_scale=1.0
        self.onion_previous_levels=[100]
        self.onion_next_levels=[100]
        self.onion_center_percent=50.0
        self._onion_interaction_mode=None
        self._onion_interaction_direction=-1
        self._onion_interaction_axis=None
        self._onion_interaction_operation=None
        self._onion_interaction_dragging=False
        self._onion_interaction_start_widget=QPointF()
        self._onion_interaction_start_pan=QPointF()
        self._onion_interaction_start_view_rotation=0.0
        self._onion_interaction_start_previous=(0.0, 0.0)
        self._onion_interaction_start_next=(0.0, 0.0)
        self._onion_interaction_start_previous_rotation=0.0
        self._onion_interaction_start_next_rotation=0.0
        self._onion_interaction_start_previous_scale=100.0
        self._onion_interaction_start_next_scale=100.0
        self._onion_interaction_start_tu_tb_scale=1.0
        self.onion_all_layers=True
        self._color_filter_cache = {}
        self._pseudo_transparency_cache = {}
        # 画像ごとのRGBインデックスを保持し、表示チェックのたびの再計算を避ける。
        self._color_index_cache = {}
        self.silhouette_non_background = False
        self._silhouette_cache = {}
        self._onion_cache = {}
        self.drawing=False; self.last_canvas=QPointF(); self.last_widget=QPointF(); self.lasso=[]; self.middle_hand=False
        self._brush_cursor_widget_pos = QPointF(-1000, -1000)
        self._brush_cursor_inside = False
        # ブラシ確定時にタイムライン全体を作り直さないための状態。
        self._brush_started_with_content = True
        self._editable_key_was_blank = False
        self._brush_blend_base_image = None
        self._brush_blended_colors = set()
        # Undo用の変更前画素はタイル単位で遅延保存する。全画面サイズの
        # QImageをストローク開始時に確保しない。
        self._stroke_before_tiles = None
        self._stroke_dirty_rect = None
        self._stroke_undo_frame = 0
        self._stroke_undo_layer = 0
        self._stroke_prev_has_content = False
        self._brush_stroke_opacity = 1.0
        # ブラシ描画中の表示用バッファ（白→透明変換をストローク領域だけ差分更新する）。
        # 大画像でストロークごとに全画素を再変換する重い処理を避けるための最適化。
        self._stroke_display_image = None
        self._stroke_display_layer_index = -1
        self._brush_runtime_warmed = False
        self._cell_structure_dirty = True
        # ライン／図形ツールのプレビュー状態
        self.line_start=None
        self.line_end=None
        self.line_control=None
        self.line_curve_stage=0  # 0:待機、1:基準線ドラッグ、2:曲率指定
        self.shape_start=None
        self.shape_end=None
        self.undo_stack=[]; self.redo_stack=[]; self.stroke_before=None
        self.mesh_points=[]; self.mesh_original=None; self.mesh_active=-1; self.mesh_grid=4
        self.checker_light=QColor(255,255,255); self.checker_dark=QColor(255,255,255)

        self.selection_clear_overlay = QPushButton("選択解除", self)
        self.selection_clear_overlay.setToolTip("現在の選択範囲を解除します。")
        self.selection_clear_overlay.setFixedHeight(26)
        self.selection_clear_overlay.setStyleSheet(
            "QPushButton{background:rgba(35,35,35,215);color:white;"
            "border:1px solid white;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:rgba(70,70,70,235);}"
        )
        self.selection_clear_overlay.setFocusPolicy(
            Qt.FocusPolicy.NoFocus
        )
        self.selection_clear_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            False,
        )
        self.selection_clear_overlay.clicked.connect(
            self.clear_selection
        )
        self.selection_clear_overlay.hide()
        self.selectionChanged.connect(
            self._update_selection_clear_overlay
        )
        QTimer.singleShot(0, self.warm_up_brush_runtime)
    def _position_selection_clear_overlay(self):
        button = getattr(self, "selection_clear_overlay", None)
        if button is None:
            return
        button.adjustSize()
        margin = 10
        button.move(
            max(margin, self.width() - button.width() - margin),
            margin,
        )
        button.raise_()

    def _update_selection_clear_overlay(self):
        """選択中は、使用ツールに関係なく右上の解除を有効化する。"""
        button = getattr(
            self,
            "selection_clear_overlay",
            None,
        )
        if button is None:
            return
        visible = bool(self.selection_polygon)
        button.setEnabled(True)
        button.setVisible(visible)
        if visible:
            self._position_selection_clear_overlay()
            button.raise_()

    def resizeEvent(self, event):
        self._position_selection_clear_overlay()
        super().resizeEvent(event)

    # Core document state lives in self._document; these properties delegate so
    # the many existing `self.frames` / `self.current_frame` call sites keep
    # working unchanged.
    @property
    def frames(self): return self._document.frames
    @frames.setter
    def frames(self, value): self._document.frames = value
    @property
    def current_frame(self): return self._document.current_frame
    @current_frame.setter
    def current_frame(self, value): self._document.current_frame = value
    @property
    def active_layer_index(self): return self._document.active_layer_index
    @active_layer_index.setter
    def active_layer_index(self, value): self._document.active_layer_index = value
    @property
    def layers(self): return self._document.layers
    @property
    def active_layer(self): return self._document.active_layer
    def document_snapshot(self): return self._document.snapshot()
    def push_doc_undo(self): self.undo_stack.append(("doc",self.document_snapshot())); self.undo_stack=self.undo_stack[-MAX_UNDO:]; self.redo_stack.clear()
    def push_layer_undo(self):
        l=self.active_layer; self.undo_stack.append(("layer",self.current_frame,self.active_layer_index,l.image.copy(),l.has_content)); self.undo_stack=self.undo_stack[-MAX_UNDO:]; self.redo_stack.clear()
    def push_layer_region_undo(self, rect):
        """Store only the part of the active layer an operation can change."""
        layer = self.active_layer
        rect = QRect(rect).intersected(layer.image.rect())
        if rect.isEmpty():
            return False
        self.undo_stack.append((
            "layer_region",
            int(self.current_frame),
            int(self.active_layer_index),
            rect,
            layer.image.copy(rect),
            bool(layer.has_content),
        ))
        self.undo_stack = self.undo_stack[-MAX_UNDO:]
        self.redo_stack.clear()
        return True
    def current_state_for(self,entry):
        if entry[0]=="doc":
            return ("doc",self.document_snapshot())
        if entry[0] == "layer_region":
            _, fi, li, bbox, _before, _hc = entry
            if not (
                0 <= int(fi) < len(self.frames)
                and 0 <= int(li) < len(self.frames[int(fi)].layers)
            ):
                return None
            layer = self.frames[int(fi)].layers[int(li)]
            return (
                "layer_region",
                int(fi),
                int(li),
                QRect(bbox),
                layer.image.copy(bbox),
                bool(layer.has_content),
            )
        if entry[0] == "layer_tiles":
            _, fi, li, tiles, _hc = entry
            if not (
                0 <= int(fi) < len(self.frames)
                and 0 <= int(li) < len(self.frames[int(fi)].layers)
            ):
                return None
            layer = self.frames[int(fi)].layers[int(li)]
            current_tiles = tuple(
                (QRect(rect), layer.image.copy(QRect(rect)))
                for rect, _image in tiles
            )
            return (
                "layer_tiles",
                int(fi),
                int(li),
                current_tiles,
                bool(layer.has_content),
            )
        if entry[0]=="layer_batch":
            _, li, cells = entry
            current = []
            for fi, _, _ in cells:
                if 0 <= fi < len(self.frames) and 0 <= li < len(self.frames[fi].layers):
                    layer = self.frames[fi].layers[li]
                    current.append((fi, layer.image.copy(), layer.has_content))
            return ("layer_batch", li, current) if current else None
        if entry[0] == "layer_remove":
            _, index, _target_active = entry
            stored = []
            for frame in self.frames:
                if 0 <= index < len(frame.layers):
                    stored.append(frame.layers[index].clone())
            return (
                "layer_insert",
                int(index),
                stored,
                int(self.active_layer_index),
            )
        if entry[0] == "layer_insert":
            _, index, _layers, _target_active = entry
            return (
                "layer_remove",
                int(index),
                int(self.active_layer_index),
            )
        if entry[0] == "tween_batch":
            _, li, _restore_count, start, end, _cells = entry
            current = []
            for fi in range(int(start), min(int(end) + 1, len(self.frames))):
                if 0 <= li < len(self.frames[fi].layers):
                    layer = self.frames[fi].layers[li]
                    current.append((
                        fi,
                        layer.image.copy(),
                        bool(layer.has_content),
                        int(layer.exposure),
                    ))
            return (
                "tween_batch",
                int(li),
                len(self.frames),
                int(start),
                int(end),
                current,
            )
        _,fi,li,_,_=entry
        if not (
            0 <= int(fi) < len(self.frames)
            and 0 <= int(li) < len(self.frames[int(fi)].layers)
        ):
            return None
        l=self.frames[fi].layers[li]
        return ("layer",fi,li,l.image.copy(),l.has_content)
    def apply_undo_entry(self,e):
        if e[0] == "layer_tiles":
            _, fi, li, tiles, hc = e
            if (
                0 <= int(fi) < len(self.frames)
                and 0 <= int(li) < len(self.frames[int(fi)].layers)
            ):
                fi, li = int(fi), int(li)
                self.current_frame = fi
                self.active_layer_index = li
                layer = self.frames[fi].layers[li]
                old_display_key = self._pseudo_transparency_key(layer.image)
                display = self._pseudo_transparency_cache.pop(
                    old_display_key,
                    None,
                )
                painter = QPainter(layer.image)
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Source
                )
                for rect, image in tiles:
                    painter.drawImage(QRect(rect).topLeft(), image)
                painter.end()
                layer.has_content = bool(hc)
                if display is not None:
                    for rect, _image in tiles:
                        self._patch_pseudo_transparent_display(
                            display,
                            layer.image,
                            QRect(rect),
                        )
                    self._cache_pseudo_transparent_display(
                        layer.image,
                        display,
                    )
                self._stroke_display_image = None
                self._stroke_display_layer_index = -1
                self.cellChanged.emit(fi, li)
                self.selectionChanged.emit()
        elif e[0] == "layer_region":
            _, fi, li, bbox, crop, hc = e
            if (
                0 <= int(fi) < len(self.frames)
                and 0 <= int(li) < len(self.frames[int(fi)].layers)
            ):
                fi, li = int(fi), int(li)
                self.current_frame = fi
                self.active_layer_index = li
                layer = self.frames[fi].layers[li]
                old_display_key = self._pseudo_transparency_key(layer.image)
                display = self._pseudo_transparency_cache.pop(
                    old_display_key,
                    None,
                )
                painter = QPainter(layer.image)
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Source
                )
                painter.drawImage(QRect(bbox).topLeft(), crop)
                painter.end()
                layer.has_content = bool(hc)
                if display is not None:
                    self._patch_pseudo_transparent_display(
                        display,
                        layer.image,
                        QRect(bbox),
                    )
                    self._cache_pseudo_transparent_display(
                        layer.image,
                        display,
                    )
                self._stroke_display_image = None
                self._stroke_display_layer_index = -1
                self.cellChanged.emit(fi, li)
                self.selectionChanged.emit()
        elif e[0]=="doc":
            _,snap=e
            fs,cf,al,w,h=snap
            constants.CANVAS_WIDTH=w
            constants.CANVAS_HEIGHT=h
            # The entry was popped before application and its inverse has
            # already been captured. Transfer the stored snapshot directly;
            # cloning every frame/layer/image again only adds latency.
            self.frames=fs
            self.current_frame=cf
            self.active_layer_index=al
            self.coalesce_numbered_images()
            self.changed.emit()
        elif e[0]=="layer_batch":
            _, li, cells = e
            self.active_layer_index = li
            for fi, img, hc in cells:
                if 0 <= fi < len(self.frames) and 0 <= li < len(self.frames[fi].layers):
                    self.frames[fi].layers[li].image = img
                    self.frames[fi].layers[li].has_content = hc
                    self.cellChanged.emit(fi, li)
            self.selectionChanged.emit()
        elif e[0] == "layer_remove":
            _, index, target_active = e
            for frame in self.frames:
                if 0 <= index < len(frame.layers) and len(frame.layers) > 1:
                    frame.layers.pop(index)
            self.active_layer_index = max(
                0,
                min(int(target_active), len(self.layers) - 1),
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        elif e[0] == "layer_insert":
            _, index, stored_layers, target_active = e
            for frame_index, frame in enumerate(self.frames):
                if frame_index < len(stored_layers):
                    layer = stored_layers[frame_index]
                else:
                    layer = Layer(
                        f"Layer {index + 1}",
                        blank_image(),
                        visible=True,
                        opacity=1.0,
                    )
                frame.layers.insert(
                    max(0, min(int(index), len(frame.layers))),
                    layer,
                )
            self.active_layer_index = max(
                0,
                min(int(target_active), len(self.layers) - 1),
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        elif e[0] == "tween_batch":
            _, li, restore_count, start, end, cells = e
            restore_count = max(1, int(restore_count))
            if len(self.frames) < restore_count:
                self._ensure_frame_count(restore_count)
            for fi, image, has_content, exposure in cells:
                if (
                    0 <= fi < len(self.frames)
                    and 0 <= li < len(self.frames[fi].layers)
                ):
                    layer = self.frames[fi].layers[li]
                    layer.image = image
                    layer.has_content = bool(has_content)
                    layer.exposure = max(1, int(exposure))
            if len(self.frames) > restore_count:
                del self.frames[restore_count:]
            self.current_frame = max(
                0, min(int(start), len(self.frames) - 1)
            )
            self.active_layer_index = max(
                0, min(int(li), len(self.layers) - 1)
            )
            self._onion_cache.clear()
            self.changed.emit()
            self.selectionChanged.emit()
        else:
            _,fi,li,img,hc=e
            self.current_frame=fi
            self.active_layer_index=li
            self.frames[fi].layers[li].image=img
            self.frames[fi].layers[li].has_content=hc
            self.cellChanged.emit(fi,li)
            self.selectionChanged.emit()
        self.update()
    def undo(self):
        while self.undo_stack:
            entry = self.undo_stack.pop()
            current = self.current_state_for(entry)
            if current is None:
                # タイムラインの削除・正規化後に残った、既に存在しない
                # セルの古い履歴は安全に破棄する。
                continue
            self.redo_stack.append(current)
            self.apply_undo_entry(entry)
            return
    def redo(self):
        while self.redo_stack:
            entry = self.redo_stack.pop()
            current = self.current_state_for(entry)
            if current is None:
                continue
            self.undo_stack.append(current)
            self.apply_undo_entry(entry)
            return
    def set_layer_visibility(self, li, on):
        if li < 0:
            return
        changed = False
        for f in self.frames:
            if li < len(f.layers):
                f.layers[li].visible = bool(on)
                changed = True
        if changed:
            self._onion_cache.clear()
            self.update()

    def set_layer_opacity(self, li, opacity):
        """表示用のレイヤー不透明度。画像内のRGBA値は変更しない。"""
        if li < 0:
            return
        opacity = max(0.0, min(1.0, float(opacity)))
        changed = False
        for frame in self.frames:
            if li < len(frame.layers):
                frame.layers[li].opacity = opacity
                changed = True
        if changed:
            self._onion_cache.clear()
            self.update()

    def add_layer(self):
        # 全フレーム・全画像の文書スナップショットを作らず、
        # 追加レイヤーだけをUndo対象にして大量コマ時の待ち時間を抑える。
        old_active = int(self.active_layer_index)
        insert_index = len(self.layers)
        name = f"Layer {insert_index + 1}"
        self.undo_stack.append((
            "layer_remove",
            insert_index,
            old_active,
        ))
        self.undo_stack = self.undo_stack[-MAX_UNDO:]
        self.redo_stack.clear()

        # QImageの暗黙共有を使い、空画像バッファをコマ数分確保しない。
        shared_blank = blank_image()
        for frame in self.frames:
            frame.layers.append(
                Layer(
                    name,
                    shared_blank.copy(),
                    visible=True,
                    opacity=1.0,
                )
            )
        self.active_layer_index = insert_index
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
    def delete_layer(self):
        if len(self.layers) <= 1:
            return
        index = int(self.active_layer_index)
        target_active = max(0, index - 1)
        stored_layers = [
            frame.layers[index].clone()
            for frame in self.frames
        ]
        self.undo_stack.append((
            "layer_insert",
            index,
            stored_layers,
            target_active,
        ))
        self.undo_stack = self.undo_stack[-MAX_UNDO:]
        self.redo_stack.clear()
        for frame in self.frames:
            frame.layers.pop(index)
        self.active_layer_index = target_active
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
    @staticmethod
    def _clear_timeline_layer_cell(layer):
        layer.image = blank_image()
        layer.has_content = False
        layer.is_blank_key = False
        layer.exposure = 1
        layer.sequence_number = None
        layer.sequence_only = False

    def timeline_block_at(self, column, layer_index):
        if not self.frames:
            return None
        column = int(column)
        layer_index = int(layer_index)
        if not (0 <= column < len(self.frames)):
            return None
        kind, start, exposure = TimelineWidget.timeline_span_at(
            self.frames,
            layer_index,
            column,
        )
        if kind not in ("content", "blank") or start is None:
            return None
        return kind, int(start), max(1, int(exposure))

    def _shift_timeline_layer_right(
        self,
        layer_index,
        start_column,
        amount,
    ):
        """指定レイヤーの明示コマを右へ移動し、時間の隙間を作る。"""
        if not self.frames:
            return

        layer_index = int(layer_index)
        start_column = max(0, int(start_column))
        amount = max(1, int(amount))
        if not (
            0 <= layer_index
            < len(self.frames[0].layers)
        ):
            return

        explicit_cells = []
        required_count = start_column + amount
        for column in range(
            start_column,
            len(self.frames),
        ):
            if (
                layer_index
                >= len(self.frames[column].layers)
            ):
                continue
            layer = self.frames[column].layers[
                layer_index
            ]
            if (
                layer.has_content
                or getattr(
                    layer,
                    "is_blank_key",
                    False,
                )
            ):
                copied = layer.clone()
                explicit_cells.append(
                    (column, copied)
                )
                required_count = max(
                    required_count,
                    column
                    + amount
                    + max(1, int(copied.exposure)),
                )

        self._ensure_frame_count(required_count)

        # 先に元の明示コマをすべて未使用へ戻してから配置する。
        # これにより、移動元と移動先が重なっても内容を失わない。
        for column, _copied in explicit_cells:
            self._clear_timeline_layer_cell(
                self.frames[column].layers[
                    layer_index
                ]
            )

        for column, copied in reversed(
            explicit_cells
        ):
            self.frames[
                column + amount
            ].layers[layer_index] = copied

    def _shift_timeline_layer_left(
        self,
        layer_index,
        start_column,
        amount=1,
    ):
        """指定位置以降の明示コマを左へ移動し、削除した時間を詰める。"""
        if not self.frames:
            return

        layer_index = int(layer_index)
        start_column = max(0, int(start_column))
        amount = max(1, int(amount))
        if not (
            0 <= layer_index
            < len(self.frames[0].layers)
        ):
            return

        explicit_cells = []
        for column in range(start_column, len(self.frames)):
            layer = self.frames[column].layers[layer_index]
            if (
                layer.has_content
                or getattr(layer, "is_blank_key", False)
            ):
                explicit_cells.append((column, layer.clone()))

        for column, _copied in explicit_cells:
            self._clear_timeline_layer_cell(
                self.frames[column].layers[layer_index]
            )

        for column, copied in explicit_cells:
            target_column = column - amount
            if target_column >= 0:
                self.frames[target_column].layers[layer_index] = copied

    def _trim_unused_trailing_frames(self):
        """全レイヤーで未使用になった末尾の時間列を取り除く。"""
        while len(self.frames) > 1:
            last_column = len(self.frames) - 1
            if any(
                self.timeline_block_at(last_column, layer_index)
                is not None
                for layer_index in range(len(self.frames[0].layers))
            ):
                break
            self.frames.pop()

    def create_blank_key(
        self,
        column=None,
        layer_index=None,
    ):
        """○を作成する。

        開始セル（●／○）を選択した場合：
            元のコマを残し、露出末尾の次へ同じ長さの○を挿入する。

        露出途中（ー／│）を選択した場合：
            全体の長さを変えず、選択位置から後半を○へ分割する。
        """
        if column is None:
            column = self.current_frame
        if layer_index is None:
            layer_index = self.active_layer_index

        column = max(0, int(column))
        layer_index = int(layer_index)
        if not self.frames:
            return False
        if not (
            0 <= layer_index
            < len(self.frames[0].layers)
        ):
            return False

        self.push_doc_undo()
        self._ensure_frame_count(column + 1)

        block = self.timeline_block_at(
            column,
            layer_index,
        )
        blank_exposure = 1

        if block is not None:
            _kind, start, exposure = block
            start = int(start)
            exposure = max(1, int(exposure))
            source = self.frames[
                start
            ].layers[layer_index]
            template = source.clone()

            if column == start:
                # ●／○の開始セルを選択：
                # 元のコマを保持し、❘の次へ同じ長さの○を挿入する。
                insertion_column = start + exposure
                blank_exposure = exposure
                self._shift_timeline_layer_right(
                    layer_index,
                    insertion_column,
                    blank_exposure,
                )
                self._ensure_frame_count(
                    insertion_column + blank_exposure
                )
                column = insertion_column
            else:
                # ー／│を選択：
                # 露出全体の長さは変えず、選択位置で前後に分割する。
                #
                # 例：
                # ●ーーーーー│
                #       ↓
                # ●ーー○ーー│
                split_offset = max(
                    1,
                    column - start,
                )
                split_offset = min(
                    split_offset,
                    exposure - 1,
                )
                blank_exposure = max(
                    1,
                    exposure - split_offset,
                )
                source.exposure = max(
                    1,
                    split_offset,
                )
                column = start + split_offset
                self._ensure_frame_count(
                    column + blank_exposure
                )
        else:
            # 未使用セル上では、その位置へ○を作る。
            # 直前の明示コマがある場合は、○の直前まで露出を伸ばす。
            self._ensure_frame_count(column + 1)
            target = self.frames[
                column
            ].layers[layer_index]
            template = target.clone()

            previous_start = None
            for candidate in range(
                column - 1,
                -1,
                -1,
            ):
                layer = self.frames[
                    candidate
                ].layers[layer_index]
                if (
                    layer.has_content
                    or getattr(
                        layer,
                        "is_blank_key",
                        False,
                    )
                ):
                    previous_start = candidate
                    template = layer.clone()
                    break

            if previous_start is not None:
                previous = self.frames[
                    previous_start
                ].layers[layer_index]
                previous.exposure = max(
                    1,
                    column - previous_start,
                )

        target = self.frames[
            column
        ].layers[layer_index]
        target.image = blank_image()
        target.visible = bool(template.visible)
        target.opacity = float(template.opacity)
        target.alpha_locked = bool(
            template.alpha_locked
        )
        target.color_filter_enabled = bool(
            template.color_filter_enabled
        )
        target.color_filter_rgb = (
            tuple(template.color_filter_rgb)
            if template.color_filter_rgb
            is not None
            else None
        )
        target.has_content = False
        target.is_blank_key = True
        target.exposure = blank_exposure

        self.current_frame = column
        self.active_layer_index = layer_index
        self._cell_structure_dirty = True
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def add_frame(self, dup):
        # 互換用。空追加は選択中コマの直後へ○を挿入する。
        if not dup:
            return self.create_blank_key(
                self.current_frame,
                self.active_layer_index,
            )

        self.push_doc_undo()
        source_frame = self.frames[self.current_frame]
        new_frame = source_frame.clone()
        at = self.current_frame + 1
        self.frames.insert(at, new_frame)
        self.current_frame = at
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def delete_frame(self):
        """選択レイヤーの表示コマを1つ削除する。"""
        if not self.frames:
            return
        layer_index = int(self.active_layer_index)
        current = int(self.current_frame)
        block = self.timeline_block_at(current, layer_index)

        self.push_doc_undo()
        if block is not None:
            _kind, start, exposure = block
            layer = self.frames[start].layers[layer_index]
            if layer.has_content and layer.sequence_number is not None:
                self._sequence_archive[
                    (layer_index, int(layer.sequence_number))
                ] = layer.clone()
            if exposure > 1:
                layer.exposure = exposure - 1
                self._shift_timeline_layer_left(
                    layer_index,
                    start + exposure,
                )
            else:
                self._clear_timeline_layer_cell(layer)
                self._shift_timeline_layer_left(
                    layer_index,
                    start + 1,
                )
            self.current_frame = min(current, len(self.frames) - 1)
        elif len(self.frames) > 1:
            # ほかのレイヤーの時間列は動かさず、選択レイヤーだけを詰める。
            self._shift_timeline_layer_left(
                layer_index,
                current + 1,
            )
            self.current_frame = current
        else:
            if self.undo_stack:
                self.undo_stack.pop()
            return

        self._trim_unused_trailing_frames()
        self.current_frame = min(self.current_frame, len(self.frames) - 1)

        self._cell_structure_dirty = True
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()

    def previous_frame(self):
        if not self.frames:
            return
        self.current_frame = max(0, self.current_frame - 1)
        self.selectionChanged.emit()
        self.update()

    def next_frame(self):
        if not self.frames:
            return
        next_column = self.current_frame + 1
        old_count = len(self.frames)
        self._ensure_frame_count(next_column + 1)
        self.current_frame = next_column
        # 追加されたセルは未使用のまま。○の空キーフレームにはしない。
        if len(self.frames) != old_count:
            self._cell_structure_dirty = True
            self.changed.emit()
        self.selectionChanged.emit()
        self.update()

    def timeline_step_columns(self, layer_index=None):
        """このレイヤーで有効なコマ開始位置一覧を返す。

        内容キーフレーム（●）だけでなく、空フレーム（○）も
        1つのコマ開始位置として扱う。
        """
        if not self.frames:
            return []
        if layer_index is None:
            layer_index = self.active_layer_index
        layer_index = int(layer_index)

        columns = []
        for column in range(len(self.frames)):
            try:
                kind, start, _exposure = TimelineWidget.timeline_span_at(
                    self.frames,
                    layer_index,
                    column,
                )
            except (IndexError, KeyError, TypeError, AttributeError, ValueError) as exc:
                log.debug("timeline_span_at(col=%s) failed: %s", column, exc)
                continue
            if kind in ("content", "blank") and start == column:
                columns.append(column)
        return columns

    def previous_key_frame(self):
        layer_index = self.active_layer_index
        keys = self.timeline_step_columns(layer_index)
        previous = [i for i in keys if i < self.current_frame]
        if previous:
            self.current_frame = previous[-1]
        elif keys:
            self.current_frame = keys[-1]
        self.selectionChanged.emit()
        self.update()

    def next_key_frame(self):
        layer_index = self.active_layer_index
        keys = self.timeline_step_columns(layer_index)
        following = [i for i in keys if i > self.current_frame]
        if following:
            self.current_frame = following[0]
        elif keys:
            self.current_frame = keys[0]
        self.selectionChanged.emit()
        self.update()

    def set_duration(self, d):
        layer = self.active_layer
        if not layer.has_content:
            source = self.resolve_key_frame(self.current_frame, self.active_layer_index)
            if source is not None:
                self.frames[source].layers[self.active_layer_index].exposure = max(1, int(d))
            else:
                layer.exposure = max(1, int(d))
        else:
            layer.exposure = max(1, int(d))
        self.changed.emit()
    def select_exposure(self, column, visual_row):
        self.current_frame = max(0, min(int(column), len(self.frames) - 1))
        layer_count = len(self.layers)
        if layer_count:
            visual_row = max(0, min(int(visual_row), layer_count - 1))
            self.active_layer_index = layer_count - 1 - visual_row
        self.selectionChanged.emit()
        self.update()

    def current_exposure(self):
        return self.current_frame

    def resolve_key_frame(self, column, layer_index):
        if not self.frames:
            return None
        column = max(0, min(int(column), len(self.frames) - 1))
        for key_col in range(column, -1, -1):
            if layer_index >= len(self.frames[key_col].layers):
                continue
            layer = self.frames[key_col].layers[layer_index]
            if layer.has_content:
                return (
                    key_col
                    if column < key_col + max(1, layer.exposure)
                    else None
                )
            if getattr(layer, "is_blank_key", False):
                return None
        return None

    def resolve_exposure_block(self, column, layer_index):
        """●ーーーー｜を1つのキーフレーム露出ブロックとして返す。"""
        if not self.frames:
            return None
        column = max(0, min(int(column), len(self.frames) - 1))
        layer_index = int(layer_index)
        key_column = self.resolve_key_frame(column, layer_index)
        if key_column is None:
            return None
        if not (
            0 <= key_column < len(self.frames)
            and 0 <= layer_index
            < len(self.frames[key_column].layers)
        ):
            return None
        key_layer = self.frames[key_column].layers[layer_index]
        exposure = max(1, int(key_layer.exposure))
        end_column = min(
            len(self.frames) - 1,
            key_column + exposure - 1,
        )
        return (
            int(key_column),
            int(end_column),
            int(exposure),
        )

    def normalize_sequence_numbers(self, layer_index=None):
        """シートの登場順で絵番号を正規化し、連番にも反映する。"""
        if not self.frames:
            return
        if layer_index is None:
            layer_indices = range(len(self.frames[0].layers))
        else:
            layer_indices = (int(layer_index),)
        for target_layer_index in layer_indices:
            sheet_numbers = []
            for frame in self.frames:
                layer = frame.layers[target_layer_index]
                if (
                    layer.has_content
                    and not layer.sequence_only
                    and layer.sequence_number is not None
                    and int(layer.sequence_number) not in sheet_numbers
                ):
                    sheet_numbers.append(int(layer.sequence_number))
            all_numbers = {
                int(frame.layers[target_layer_index].sequence_number)
                for frame in self.frames
                if frame.layers[target_layer_index].sequence_number is not None
            }
            all_numbers.update(
                int(number)
                for archived_layer, number in self._sequence_archive
                if int(archived_layer) == target_layer_index
            )
            remaining = sorted(all_numbers - set(sheet_numbers))
            ordered = sheet_numbers + remaining
            mapping = {
                old_number: new_number
                for new_number, old_number in enumerate(ordered, 1)
            }
            next_number = len(mapping) + 1
            for frame in self.frames:
                layer = frame.layers[target_layer_index]
                if layer.sequence_number is not None:
                    layer.sequence_number = mapping[int(layer.sequence_number)]
                elif layer.has_content and not layer.sequence_only:
                    layer.sequence_number = next_number
                    next_number += 1
            normalized_archive = {}
            for (archived_layer, old_number), archived in self._sequence_archive.items():
                if int(archived_layer) == target_layer_index:
                    new_number = mapping.get(int(old_number), int(old_number))
                    archived.sequence_number = new_number
                else:
                    new_number = int(old_number)
                normalized_archive[(int(archived_layer), new_number)] = archived
            self._sequence_archive = normalized_archive

    def sequence_entry_columns(self, layer_index):
        """絵番号ごとの代表セル位置を番号順で返す。"""
        entries = {}
        for column, frame in enumerate(self.frames):
            if not (0 <= layer_index < len(frame.layers)):
                continue
            layer = frame.layers[layer_index]
            number = layer.sequence_number
            if (
                number is not None
                and int(number) >= 1
                and (layer.has_content or layer.is_blank_key)
            ):
                number = int(number)
                if number not in entries or layer.sequence_only:
                    entries[number] = column
        return [entries[number] for number in sorted(entries)]

    def insert_sequence_blank(self, layer_index, after_number):
        """選択番号の直後へ空画像番号を挿入し、後続番号を送る。"""
        layer_index = int(layer_index)
        entries = self.sequence_entry_columns(layer_index)
        insert_number = (
            1 if not entries else max(1, int(after_number) + 1)
        )
        self.push_doc_undo()
        # 同じ番号は同じ画像を指すため、シート上の重複参照も含めて
        # 挿入位置以降を一括で繰り下げる。
        for frame in self.frames:
            layer = frame.layers[layer_index]
            if (
                layer.sequence_number is not None
                and int(layer.sequence_number) >= insert_number
            ):
                layer.sequence_number = int(layer.sequence_number) + 1
        shifted_archive = {}
        for (archived_layer, number), archived in self._sequence_archive.items():
            if archived_layer == layer_index and int(number) >= insert_number:
                archived.sequence_number = int(number) + 1
                number = int(number) + 1
            shifted_archive[(archived_layer, int(number))] = archived
        self._sequence_archive = shifted_archive
        new_column = len(self.frames)
        self._ensure_frame_count(new_column + 1)
        target = self.frames[new_column].layers[layer_index]
        target.image = blank_image()
        target.has_content = False
        target.is_blank_key = True
        target.sequence_number = insert_number
        target.sequence_only = True
        target.exposure = 1
        self.current_frame = new_column
        self.active_layer_index = layer_index
        self._cell_structure_dirty = True
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def move_sequence_image(self, layer_index, first_number, second_number):
        """連番画像を差し込み移動し、間の画像を1コマずつ送る。"""
        first_number = int(first_number)
        second_number = int(second_number)
        if first_number == second_number:
            return False
        columns = self.sequence_entry_columns(int(layer_index))
        by_number = {
            int(self.frames[column].layers[layer_index].sequence_number): column
            for column in columns
        }
        if first_number not in by_number or second_number not in by_number:
            return False
        self.push_doc_undo()
        low, high = sorted((first_number, second_number))
        ordered_numbers = list(range(low, high + 1))
        snapshots = {}
        for number in ordered_numbers:
            if number not in by_number:
                return False
            layer = self.frames[by_number[number]].layers[layer_index]
            snapshots[number] = (
                layer.image.copy(), bool(layer.has_content), bool(layer.is_blank_key)
            )
        if first_number < second_number:
            source_for_number = {
                number: number + 1 for number in range(first_number, second_number)
            }
        else:
            source_for_number = {
                number: number - 1 for number in range(second_number + 1, first_number + 1)
            }
        source_for_number[second_number] = first_number
        for frame in self.frames:
            layer = frame.layers[layer_index]
            number = layer.sequence_number
            if number in source_for_number:
                image, has_content, is_blank = snapshots[source_for_number[number]]
                layer.image = image.copy()
                layer.has_content = has_content
                layer.is_blank_key = is_blank
        self.changed.emit()
        self.update()
        return True

    def sync_numbered_image_from_cell(self, frame_index, layer_index):
        """同じレイヤー・同じ絵番号を、1つの画像オブジェクトへ結び直す。"""
        source = self.frames[int(frame_index)].layers[int(layer_index)]
        if source.sequence_number is None or not source.has_content:
            return
        for index, frame in enumerate(self.frames):
            if index == int(frame_index):
                continue
            target = frame.layers[int(layer_index)]
            if target.sequence_number == source.sequence_number:
                # QImageのコピーを配ると、次の描画開始時点で各セルが
                # 別画像へ分離する。同じPythonオブジェクトを共有し、
                # 同じ番号を実体1枚として扱う。
                target.image = source.image
                target.has_content = True
                target.is_blank_key = False
        archived = self._sequence_archive.get((
            int(layer_index), int(source.sequence_number)
        ))
        if archived is not None:
            archived.image = source.image
            archived.has_content = True
            archived.is_blank_key = False

    def coalesce_numbered_images(self):
        """文書内の同一レイヤー・同一番号の画像参照を統合する。"""
        shared = {}
        preferred = int(self.current_frame)
        if 0 <= preferred < len(self.frames):
            for layer_index, layer in enumerate(self.frames[preferred].layers):
                if layer.has_content and layer.sequence_number is not None:
                    shared[(layer_index, int(layer.sequence_number))] = layer.image

        for frame in self.frames:
            for layer_index, layer in enumerate(frame.layers):
                if not layer.has_content or layer.sequence_number is None:
                    continue
                key = (layer_index, int(layer.sequence_number))
                image = shared.setdefault(key, layer.image)
                layer.image = image

        for key, archived in self._sequence_archive.items():
            shared_image = shared.get((int(key[0]), int(key[1])))
            if shared_image is not None:
                archived.image = shared_image

    def delete_sequence_entry(self, layer_index, number):
        """連番画像を削除し、シート側の参照セルを未使用へ戻す。"""
        number = int(number)
        self.push_doc_undo()
        found = False
        for frame in self.frames:
            layer = frame.layers[int(layer_index)]
            if layer.sequence_number == number:
                self._clear_timeline_layer_cell(layer)
                found = True
        if not found:
            self.undo_stack.pop()
            return False
        self._trim_unused_trailing_frames()
        self.current_frame = min(self.current_frame, len(self.frames) - 1)
        self._cell_structure_dirty = True
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def apply_sequence_only_entries(self):
        """連番で追加した番号をシートの保持区間、または末尾へ反映する。"""
        pending = []
        for column, frame in enumerate(self.frames):
            for layer_index, layer in enumerate(frame.layers):
                if layer.sequence_only and layer.sequence_number is not None:
                    pending.append((int(layer.sequence_number), layer_index, column, layer.clone()))
        for number, layer_index, source_column, copied in sorted(pending):
            target_exposure = 1
            previous = None
            for column, frame in enumerate(self.frames):
                layer = frame.layers[layer_index]
                if (
                    not layer.sequence_only
                    and layer.sequence_number == number - 1
                    and (layer.has_content or layer.is_blank_key)
                ):
                    previous = (column, layer)
                    break
            target_column = None
            if previous is not None:
                start, previous_layer = previous
                exposure = max(1, int(previous_layer.exposure))
                if exposure > 1:
                    # 保持区間の長さは変えず、前半を既存番号、後半を
                    # 追加番号へ分配する。
                    target_column = start + max(1, exposure // 2)
                    previous_layer.exposure = target_column - start
                    target_exposure = start + exposure - target_column
            if target_column is None:
                visible_columns = [
                    column
                    for column, frame in enumerate(self.frames)
                    if any(
                        (layer.has_content or layer.is_blank_key)
                        and not layer.sequence_only
                        for layer in frame.layers
                    )
                ]
                target_column = (max(visible_columns) + 1) if visible_columns else 0
            self._ensure_frame_count(target_column + 1)
            target = self.frames[target_column].layers[layer_index]
            if target.has_content or target.is_blank_key:
                target_column = len(self.frames)
                self._ensure_frame_count(target_column + 1)
                target = self.frames[target_column].layers[layer_index]
            target.image = copied.image.copy()
            target.has_content = bool(copied.has_content)
            target.is_blank_key = bool(
                copied.is_blank_key and not copied.has_content
            )
            target.sequence_number = number
            target.sequence_only = False
            target.exposure = max(1, int(target_exposure))
            target.visible = copied.visible
            target.opacity = copied.opacity
            self._clear_timeline_layer_cell(
                self.frames[source_column].layers[layer_index]
            )
        if pending:
            self._trim_unused_trailing_frames()
            self.current_frame = min(self.current_frame, len(self.frames) - 1)
            self._cell_structure_dirty = True
            self.changed.emit()
            self.selectionChanged.emit()
            self.update()

    def ensure_editable_key(self):
        """未使用／○／保持セルを独立した●キーフレームへ変換する。"""
        self._editable_key_was_blank = False
        layer = self.active_layer
        if layer.has_content:
            if layer.sequence_number is not None:
                # 描画開始前から全参照を同じ画像へ結び、ストローク中も
                # 同番号の画像が分離しないようにする。
                self.sync_numbered_image_from_cell(
                    self.current_frame,
                    self.active_layer_index,
                )
            if layer.is_blank_key:
                # 連番の空セルへ描画した旧データでは両方のフラグが
                # Trueになり得る。内容キーとして即時修復する。
                layer.is_blank_key = False
                layer.sequence_only = False
                self._cell_structure_dirty = True
                return True
            return False

        current = int(self.current_frame)
        layer_index = int(self.active_layer_index)
        block = self.timeline_block_at(current, layer_index)
        retained_number = layer.sequence_number

        if block is not None:
            kind, start, exposure = block
            source_layer = self.frames[start].layers[layer_index]
            copied = source_layer.clone()
            old_end = start + exposure - 1

            if current > start:
                source_layer.exposure = max(1, current - start)

            layer.visible = bool(copied.visible)
            layer.opacity = float(copied.opacity)
            layer.alpha_locked = bool(copied.alpha_locked)
            layer.color_filter_enabled = bool(
                copied.color_filter_enabled
            )
            layer.color_filter_rgb = (
                tuple(copied.color_filter_rgb)
                if copied.color_filter_rgb is not None
                else None
            )
            layer.image = (
                copied.image.copy()
                if kind == "content"
                else blank_image()
            )
            self._editable_key_was_blank = kind != "content"
            layer.exposure = max(1, old_end - current + 1)
        else:
            # An uncreated cell already owns a transparent image.  Keep it
            # instead of allocating and clearing another full-canvas image at
            # the instant the first stroke begins.
            self._editable_key_was_blank = True
            layer.exposure = 1

        layer.has_content = True
        layer.is_blank_key = False
        if self.timeline_mode == "sheet":
            if retained_number is not None:
                layer.sequence_number = int(retained_number)
            else:
                existing_numbers = [
                    int(frame.layers[layer_index].sequence_number)
                    for frame in self.frames
                    if (
                        frame.layers[layer_index].has_content
                        and frame.layers[layer_index].sequence_number is not None
                    )
                ]
                layer.sequence_number = max(existing_numbers, default=0) + 1
        self._cell_structure_dirty = True
        return True

    def set_tool(self, t):
        if t != self.tool:
            self._stop_brush_follow_timer()
            self.line_start = None
            self.line_end = None
            self.line_control = None
            self.line_curve_stage = 0
            self.shape_start = None
            self.shape_end = None
            self.drawing = False
        self.tool = t
        self.update_tool_cursor()
        self.update()

    def _eyedropper_cursor(self):
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor("black"), 2)
        painter.setPen(pen)
        painter.drawLine(5, 18, 16, 7)
        painter.drawLine(8, 21, 19, 10)
        painter.drawLine(15, 6, 20, 11)
        painter.drawEllipse(QPointF(5, 19), 2, 2)
        painter.end()
        return QCursor(pixmap, 5, 19)

    def begin_onion_transform_interaction(self, direction):
        """旧UI互換：前／後オニオンをXY移動モードにする。"""
        self.begin_onion_shift_interaction(direction, "xy")

    def begin_onion_shift_interaction(self, direction, axis="x"):
        """前／後オニオンのXYシフトをキャンバス上で調整する。"""
        axis = str(axis).lower()
        if axis not in ("x", "y", "xy"):
            axis = "xy"
        self._onion_interaction_mode = "onion_shift"
        self._onion_interaction_direction = (
            -1 if int(direction) < 0 else 1
        )
        self._onion_interaction_axis = axis
        self._onion_interaction_operation = "move"
        self._onion_interaction_dragging = False
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update_tool_cursor()
        label = (
            "前" if self._onion_interaction_direction < 0 else "後"
        )
        axis_label = (
            "X方向" if axis == "x"
            else "Y方向" if axis == "y"
            else "XY方向"
        )
        self.status_message.emit(
            f"{label}のオニオンスキン：ドラッグで"
            f"{axis_label}へ移動します。Escで解除します。"
        )

    def begin_onion_canvas_position_interaction(self):
        """キャンバスの移動と回転を行い、前後の相対値を補正する。"""
        self._onion_interaction_mode = "canvas"
        self._onion_interaction_direction = 0
        self._onion_interaction_axis = None
        self._onion_interaction_operation = None
        self._onion_interaction_dragging = False
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update_tool_cursor()
        self.status_message.emit(
            "表示位置：左ドラッグでキャンバス移動、"
            "Shift＋左ドラッグまたは右ドラッグで回転します。"
            "TU／TB拡大率は維持され、前後オニオンの相対値へ"
            "反映されます。Escで解除します。"
        )

    @staticmethod
    def _normalized_angle(angle):
        return geometry.normalized_angle(angle)

    @staticmethod
    def _rotated_vector(x, y, angle_degrees):
        return geometry.rotated_vector(x, y, angle_degrees)

    def set_onion_canvas_view_rotation(self, value):
        """表示位置の回転UIからキャンバス角度を変更する。"""
        target = self._normalized_angle(value)
        current = self._normalized_angle(self.rotation)
        delta = self._normalized_angle(target - current)
        if abs(delta) < 1e-9:
            return

        # キャンバス回転分を前後の相対値から差し引き、
        # オニオンの画面上の位置・角度を保つ。
        previous_shift = self._rotated_vector(
            self.onion_previous_shift_x,
            self.onion_previous_shift_y,
            -delta,
        )
        next_shift = self._rotated_vector(
            self.onion_next_shift_x,
            self.onion_next_shift_y,
            -delta,
        )
        self.onion_previous_shift_x = previous_shift.x()
        self.onion_previous_shift_y = previous_shift.y()
        self.onion_next_shift_x = next_shift.x()
        self.onion_next_shift_y = next_shift.y()
        self.onion_previous_rotation = self._normalized_angle(
            self.onion_previous_rotation - delta
        )
        self.onion_next_rotation = self._normalized_angle(
            self.onion_next_rotation - delta
        )
        self.rotation = target
        self.viewChanged.emit(
            float(self.zoom),
            float(self.rotation),
        )
        self.onionInteractionChanged.emit()
        self.update()

    def cancel_onion_interaction(self, restore=False):
        if self._onion_interaction_mode is None:
            return
        if restore and self._onion_interaction_dragging:
            self.pan = QPointF(self._onion_interaction_start_pan)
            self.rotation = float(
                self._onion_interaction_start_view_rotation
            )
            (
                self.onion_previous_shift_x,
                self.onion_previous_shift_y,
            ) = self._onion_interaction_start_previous
            (
                self.onion_next_shift_x,
                self.onion_next_shift_y,
            ) = self._onion_interaction_start_next
            self.onion_previous_rotation = float(
                self._onion_interaction_start_previous_rotation
            )
            self.onion_next_rotation = float(
                self._onion_interaction_start_next_rotation
            )
            self.onion_previous_scale = float(
                self._onion_interaction_start_previous_scale
            )
            self.onion_next_scale = float(
                self._onion_interaction_start_next_scale
            )
            self.onion_tu_tb_scale = float(
                self._onion_interaction_start_tu_tb_scale
            )
            self.onionInteractionChanged.emit()
            self.viewChanged.emit(
                float(self.zoom),
                float(self.rotation),
            )
        self._onion_interaction_mode = None
        self._onion_interaction_axis = None
        self._onion_interaction_operation = None
        self._onion_interaction_dragging = False
        self.drawing = False
        self.update_tool_cursor()
        self.update()
        self.onionInteractionFinished.emit()

    def _onion_widget_delta_to_canvas(self, delta):
        zoom = max(
            0.0001,
            float(self.zoom) * float(self.onion_tu_tb_scale),
        )
        dx = float(delta.x())
        dy = float(delta.y())
        angle = math.radians(float(self.rotation))
        canvas_dx = (
            dx * math.cos(angle) + dy * math.sin(angle)
        ) / zoom
        canvas_dy = (
            -dx * math.sin(angle) + dy * math.cos(angle)
        ) / zoom
        return QPointF(canvas_dx, canvas_dy)

    def _begin_onion_interaction_drag(
        self,
        widget_position,
        button=Qt.MouseButton.LeftButton,
        modifiers=Qt.KeyboardModifier.NoModifier,
    ):
        self._onion_interaction_dragging = True
        self._onion_interaction_start_widget = QPointF(
            widget_position
        )
        self._onion_interaction_start_pan = QPointF(self.pan)
        self._onion_interaction_start_view_rotation = float(
            self.rotation
        )
        self._onion_interaction_start_previous = (
            float(self.onion_previous_shift_x),
            float(self.onion_previous_shift_y),
        )
        self._onion_interaction_start_next = (
            float(self.onion_next_shift_x),
            float(self.onion_next_shift_y),
        )
        self._onion_interaction_start_previous_rotation = float(
            self.onion_previous_rotation
        )
        self._onion_interaction_start_next_rotation = float(
            self.onion_next_rotation
        )
        self._onion_interaction_start_previous_scale = float(
            self.onion_previous_scale
        )
        self._onion_interaction_start_next_scale = float(
            self.onion_next_scale
        )
        self._onion_interaction_start_tu_tb_scale = float(
            self.onion_tu_tb_scale
        )
        if self._onion_interaction_mode == "onion_shift":
            self._onion_interaction_operation = "move"
        else:
            self._onion_interaction_operation = (
                "rotate"
                if (
                    button == Qt.MouseButton.RightButton
                    or modifiers
                    & Qt.KeyboardModifier.ShiftModifier
                )
                else "move"
            )
        self.drawing = True
        self.update_tool_cursor()

    def _update_onion_interaction_drag(self, widget_position):
        if not self._onion_interaction_dragging:
            return
        widget_delta = (
            QPointF(widget_position)
            - self._onion_interaction_start_widget
        )
        canvas_delta = self._onion_widget_delta_to_canvas(
            widget_delta
        )

        previous_x, previous_y = (
            self._onion_interaction_start_previous
        )
        next_x, next_y = self._onion_interaction_start_next
        operation = self._onion_interaction_operation or "move"

        if self._onion_interaction_mode == "onion_shift":
            direction = self._onion_interaction_direction
            axis = self._onion_interaction_axis or "x"
            delta_x = (
                canvas_delta.x() if axis in ("x", "xy") else 0.0
            )
            delta_y = (
                canvas_delta.y() if axis in ("y", "xy") else 0.0
            )
            if direction < 0:
                self.onion_previous_shift_x = (
                    previous_x + delta_x
                )
                self.onion_previous_shift_y = (
                    previous_y + delta_y
                )
            else:
                self.onion_next_shift_x = next_x + delta_x
                self.onion_next_shift_y = next_y + delta_y

        elif self._onion_interaction_mode == "canvas":
            if operation == "rotate":
                angle_delta = float(widget_delta.x()) * 0.35
                self.rotation = self._normalized_angle(
                    self._onion_interaction_start_view_rotation
                    + angle_delta
                )

                # キャンバスの回転分をシフト座標とオニオン回転から
                # 差し引き、オニオンスキンの画面上の位置・角度を保つ。
                previous_shift = self._rotated_vector(
                    previous_x,
                    previous_y,
                    -angle_delta,
                )
                next_shift = self._rotated_vector(
                    next_x,
                    next_y,
                    -angle_delta,
                )
                self.onion_previous_shift_x = previous_shift.x()
                self.onion_previous_shift_y = previous_shift.y()
                self.onion_next_shift_x = next_shift.x()
                self.onion_next_shift_y = next_shift.y()
                self.onion_previous_rotation = (
                    self._normalized_angle(
                        self._onion_interaction_start_previous_rotation
                        - angle_delta
                    )
                )
                self.onion_next_rotation = (
                    self._normalized_angle(
                        self._onion_interaction_start_next_rotation
                        - angle_delta
                    )
                )
                self.viewChanged.emit(
                    float(self.zoom),
                    float(self.rotation),
                )
            else:
                # キャンバスは画面上でドラッグし、前後のオニオンは
                # 画面上の位置を保つよう逆向きにシフト値を補正する。
                self.pan = (
                    self._onion_interaction_start_pan
                    + widget_delta
                )
                self.onion_previous_shift_x = (
                    previous_x - canvas_delta.x()
                )
                self.onion_previous_shift_y = (
                    previous_y - canvas_delta.y()
                )
                self.onion_next_shift_x = (
                    next_x - canvas_delta.x()
                )
                self.onion_next_shift_y = (
                    next_y - canvas_delta.y()
                )
                self.viewChanged.emit(
                    float(self.zoom),
                    float(self.rotation),
                )

        self.onionInteractionChanged.emit()
        self.update()

    def update_tool_cursor(self):
        if self._onion_interaction_mode in (
            "canvas",
            "onion_shift",
            "onion_transform",
        ):
            if (
                self._onion_interaction_dragging
                and self._onion_interaction_operation == "rotate"
            ):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif self._onion_interaction_mode == "canvas":
                self.setCursor(
                    Qt.CursorShape.ClosedHandCursor
                    if self._onion_interaction_dragging
                    else Qt.CursorShape.OpenHandCursor
                )
            elif self._onion_interaction_axis == "x":
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif self._onion_interaction_axis == "y":
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            else:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            return

        tool = self.effective_tool()
        if tool == "hand":
            self.setCursor(
                Qt.CursorShape.ClosedHandCursor
                if self.drawing or self.middle_hand
                else Qt.CursorShape.OpenHandCursor
            )
        elif tool in ("zoom", "rotate"):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif tool == "eyedropper":
            self.setCursor(self._eyedropper_cursor())
        elif tool == "brush" and self._brush_cursor_inside:
            # 同じBlankCursorをマウス移動のたびに再設定すると、Windowsで
            # OSカーソルと描画リングが一瞬切り替わって見えることがある。
            if self.cursor().shape() != Qt.CursorShape.BlankCursor:
                self.setCursor(Qt.CursorShape.BlankCursor)
        else:
            self.unsetCursor()

    def set_pen_size(self, value):
        self.pen_size = max(0.5, float(value))
        self.update()

    def set_brush_stabilizer(self, value):
        self.brush_stabilizer_strength = max(
            0, min(300, int(value))
        )

    def _brush_stabilizer_window(self):
        strength = max(
            0, min(300, int(self.brush_stabilizer_strength))
        )
        if strength <= 0:
            return 1
        return max(2, min(64, 2 + int(round(strength * 0.20))))

    def _reset_brush_stabilizer(self, point=None):
        initial = QPointF(point) if point is not None else None
        self._stabilized_canvas = (
            QPointF(initial) if initial is not None else None
        )
        self._last_raw_canvas = (
            QPointF(initial) if initial is not None else None
        )
        self._brush_stabilizer_history = (
            [QPointF(initial)] if initial is not None else []
        )
        self._last_brush_pressure = 1.0
        self._pressure_input_history = []

    def _smooth_brush_pressure(self, pressure):
        """直近の筆圧を重み付き平均し、サイズ変化の段差を抑える。"""
        value = max(0.001, min(1.0, float(pressure)))
        history = self._pressure_input_history
        history.append(value)
        if len(history) > 7:
            del history[:-7]
        weights = list(range(1, len(history) + 1))
        return sum(
            sample * weight
            for sample, weight in zip(history, weights)
        ) / float(sum(weights))


    def _stabilized_brush_point(self, raw_point, settle=0.0):
        """移動平均＋遅延半径で、実際に描画する座標を返す。"""
        raw = QPointF(raw_point)
        strength = max(
            0, min(300, int(self.brush_stabilizer_strength))
        )
        settle = max(0.0, min(1.0, float(settle)))
        self._last_raw_canvas = QPointF(raw)

        if strength <= 0:
            self._brush_stabilizer_history = [QPointF(raw)]
            self._stabilized_canvas = QPointF(raw)
            return QPointF(raw)

        history = self._brush_stabilizer_history
        history.append(QPointF(raw))
        window = self._brush_stabilizer_window()
        if len(history) > window:
            del history[:-window]

        # 新しい点を少し強くしつつ、直近の細かな揺れを平均化する。
        weights = list(range(1, len(history) + 1))
        weight_sum = float(sum(weights))
        target = QPointF(
            sum(point.x() * weight for point, weight in zip(history, weights))
            / weight_sum,
            sum(point.y() * weight for point, weight in zip(history, weights))
            / weight_sum,
        )

        if self._stabilized_canvas is None:
            self._stabilized_canvas = QPointF(target)
            return QPointF(target)

        current = QPointF(self._stabilized_canvas)
        dx = target.x() - current.x()
        dy = target.y() - current.y()
        distance = math.hypot(dx, dy)

        # 画面上の約0～20pxを補正半径として使う。
        # 強度が大きいほど、小さな手振れでは描画点が動かない。
        radius_screen = (
            60.0 * math.pow(strength / 300.0, 1.35)
        )
        radius_canvas = (
            radius_screen / max(0.05, float(self.zoom))
        )
        radius_canvas *= (1.0 - settle)

        if distance <= radius_canvas or distance <= 1e-9:
            filtered = current
        else:
            move_distance = distance - radius_canvas
            ratio = move_distance / distance
            filtered = QPointF(
                current.x() + dx * ratio,
                current.y() + dy * ratio,
            )

        self._stabilized_canvas = QPointF(filtered)
        return filtered

    def _draw_stabilized_brush_to(self, raw_point, pressure):
        """入力イベント間も補間し、補正後の軌跡を連続描画する。"""
        raw = QPointF(raw_point)
        self._brush_follow_settle = 0.0
        self._start_brush_follow_timer()
        if self.last_canvas is None:
            self.last_canvas = QPointF(raw)

        strength = max(
            0, min(300, int(self.brush_stabilizer_strength))
        )
        if strength <= 0:
            self.draw_line(self.last_canvas, raw, pressure)
            self.last_canvas = QPointF(raw)
            self._last_raw_canvas = QPointF(raw)
            return

        start_raw = (
            QPointF(self._last_raw_canvas)
            if self._last_raw_canvas is not None
            else QPointF(raw)
        )
        distance = math.hypot(
            raw.x() - start_raw.x(),
            raw.y() - start_raw.y(),
        )
        step_canvas = max(
            0.5,
            min(
                2.5,
                1.5 / max(0.05, float(self.zoom)),
            ),
        )
        steps = max(
            1,
            min(96, int(math.ceil(distance / step_canvas))),
        )

        for index in range(1, steps + 1):
            ratio = index / float(steps)
            intermediate = QPointF(
                start_raw.x() + (raw.x() - start_raw.x()) * ratio,
                start_raw.y() + (raw.y() - start_raw.y()) * ratio,
            )
            filtered = self._stabilized_brush_point(intermediate)
            if (
                abs(filtered.x() - self.last_canvas.x()) > 0.005
                or abs(filtered.y() - self.last_canvas.y()) > 0.005
            ):
                self.draw_line(
                    self.last_canvas,
                    filtered,
                    pressure,
                )
                self.last_canvas = QPointF(filtered)

    def _start_brush_follow_timer(self):
        self._brush_follow_settle = 0.0
        if (
            int(self.brush_stabilizer_strength) > 0
            and not self._brush_follow_timer.isActive()
        ):
            self._brush_follow_timer.start()

    def _stop_brush_follow_timer(self):
        self._brush_follow_timer.stop()
        self._brush_follow_settle = 0.0

    def _advance_stabilized_brush(self):
        """入力が止まっていても、描画点をカーソル終点へ追従させる。"""
        if (
            not self.drawing
            or self.effective_tool() != "brush"
            or self._last_raw_canvas is None
            or self.last_canvas is None
        ):
            self._stop_brush_follow_timer()
            return

        raw = QPointF(self._last_raw_canvas)
        distance = math.hypot(
            raw.x() - self.last_canvas.x(),
            raw.y() - self.last_canvas.y(),
        )
        if distance <= 0.01:
            return

        strength = max(
            0,
            min(300, int(self.brush_stabilizer_strength)),
        )
        if strength <= 0:
            self.draw_line(
                self.last_canvas,
                raw,
                self._last_brush_pressure,
            )
            self.last_canvas = QPointF(raw)
            return

        self._brush_follow_settle = min(
            1.0,
            self._brush_follow_settle
            + 0.035
            + 0.055 * (strength / 300.0),
        )
        filtered = self._stabilized_brush_point(
            raw,
            settle=self._brush_follow_settle,
        )
        if (
            abs(filtered.x() - self.last_canvas.x()) > 0.005
            or abs(filtered.y() - self.last_canvas.y()) > 0.005
        ):
            self.draw_line(
                self.last_canvas,
                filtered,
                self._last_brush_pressure,
            )
            self.last_canvas = QPointF(filtered)

    def _finish_stabilized_brush(self, raw_point, pressure):
        """ペンを離したとき、補正点を終点へ滑らかに収束させる。"""
        raw = QPointF(raw_point)
        self._draw_stabilized_brush_to(raw, pressure)

        strength = max(
            0, min(300, int(self.brush_stabilizer_strength))
        )
        if strength <= 0:
            return

        settle_steps = self._brush_stabilizer_window() + 8
        for index in range(settle_steps):
            settle = (index + 1) / float(settle_steps)
            filtered = self._stabilized_brush_point(
                raw,
                settle=settle,
            )
            if (
                abs(filtered.x() - self.last_canvas.x()) > 0.005
                or abs(filtered.y() - self.last_canvas.y()) > 0.005
            ):
                self.draw_line(
                    self.last_canvas,
                    filtered,
                    pressure,
                )
                self.last_canvas = QPointF(filtered)

        # 数値誤差だけが残った場合も、終点を確実に一致させる。
        if (
            abs(raw.x() - self.last_canvas.x()) > 0.01
            or abs(raw.y() - self.last_canvas.y()) > 0.01
        ):
            self.draw_line(self.last_canvas, raw, pressure)
            self.last_canvas = QPointF(raw)

    def enterEvent(self, event):
        local = self.mapFromGlobal(QCursor.pos())
        self._brush_cursor_widget_pos = QPointF(local)
        self._brush_cursor_inside = self.inside(
            self.widget_to_canvas(self._brush_cursor_widget_pos)
        )
        self.update_tool_cursor()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._brush_cursor_inside = False
        self.update_tool_cursor()
        self.update()
        super().leaveEvent(event)

    def effective_tool(self): return self.temp_tool or self.tool
    def work_rect(self):
        w,h=workspace_size(); return QRectF(self.pan.x(),self.pan.y(),w*self.zoom,h*self.zoom)
    def canvas_rect(self):
        return QRectF(self.pan.x()+OUTSIDE_MARGIN*self.zoom,self.pan.y()+OUTSIDE_MARGIN*self.zoom,constants.CANVAS_WIDTH*self.zoom,constants.CANVAS_HEIGHT*self.zoom)
    def widget_to_canvas(self,p):
        c=self.work_rect().center(); q=QPointF(p)
        if self.rotation:
            a=math.radians(-self.rotation); dx=q.x()-c.x();dy=q.y()-c.y();q=QPointF(c.x()+dx*math.cos(a)-dy*math.sin(a),c.y()+dx*math.sin(a)+dy*math.cos(a))
        camera_scale=max(0.0001,float(self.onion_tu_tb_scale))
        if abs(camera_scale-1.0)>1e-9:
            q=QPointF(
                c.x()+(q.x()-c.x())/camera_scale,
                c.y()+(q.y()-c.y())/camera_scale,
            )
        x=(q.x()-self.pan.x())/self.zoom; y=(q.y()-self.pan.y())/self.zoom
        if self.flip_horizontal:x=workspace_size()[0]-x
        return QPointF(x,y)
    def canvas_to_widget(self,p):
        w,_=workspace_size(); x=w-p.x() if self.flip_horizontal else p.x(); q=QPointF(self.pan.x()+x*self.zoom,self.pan.y()+p.y()*self.zoom)
        c=self.work_rect().center()
        camera_scale=max(0.0001,float(self.onion_tu_tb_scale))
        if abs(camera_scale-1.0)>1e-9:
            q=QPointF(
                c.x()+(q.x()-c.x())*camera_scale,
                c.y()+(q.y()-c.y())*camera_scale,
            )
        if self.rotation:
            a=math.radians(self.rotation);dx=q.x()-c.x();dy=q.y()-c.y();q=QPointF(c.x()+dx*math.cos(a)-dy*math.sin(a),c.y()+dx*math.sin(a)+dy*math.cos(a))
        return q
    def inside(self,p): w,h=workspace_size(); return 0<=p.x()<w and 0<=p.y()<h
    def color(self):
        if self.color_mode=="transparent":
            return QColor(0,0,0,0)
        return QColor(self.main_color if self.color_mode=="main" else self.sub_color)

    @staticmethod
    def is_pseudo_transparent_color(*args, **kwargs):
        return colors.is_pseudo_transparent_color(*args, **kwargs)

    def paint_source_color(self, base_color=None):
        """描画元のRGB色を、常にα255で返す。"""
        source = QColor(
            self.color() if base_color is None else base_color
        )
        if source.alpha() == 0:
            # 透明色モードは、キャンバス上では疑似透明色の白。
            source = QColor(255, 255, 255)
        source.setAlpha(255)
        return source

    def paint_opacity_value(self, opacity=None):
        """UI不透明度だけを返す。筆圧値は一切参照しない。"""
        window = self.window()
        tools = getattr(window, "tools", None)
        checkbox = getattr(
            tools,
            "opacity_enabled",
            None,
        )
        enabled = bool(
            checkbox is not None
            and checkbox.isChecked()
        )
        if not enabled:
            return 1.0

        amount = (
            float(self.pen_opacity)
            if opacity is None
            else float(opacity)
        )
        return max(0.0, min(1.0, amount))

    def blended_paint_color(
        self,
        destination_color,
        base_color=None,
        opacity=None,
    ):
        """互換用。下地と混色せず、指定RGBをそのまま返す。"""
        del destination_color, opacity
        return self.paint_source_color(base_color)

    def opaque_paint_color(
        self,
        base_color=None,
        opacity=None,
    ):
        """選択RGBをα255のまま返す。不透明度ではRGBを変えない。"""
        del opacity
        return self.paint_source_color(base_color)

    def _emit_actual_paint_colors(self, colors, maximum=64):
        """実際に生成されたRGBを安全に使用色へ通知する。"""
        if colors is None:
            return
        try:
            iterator = iter(colors)
        except TypeError:
            return

        emitted = 0
        seen = set()
        for value in iterator:
            try:
                rgb = tuple(
                    max(0, min(255, int(channel)))
                    for channel in value[:3]
                )
            except (
                TypeError,
                ValueError,
                IndexError,
            ):
                continue
            if rgb == (255, 255, 255) or rgb in seen:
                continue
            seen.add(rgb)
            red, green, blue = (
                int(rgb[0]),
                int(rgb[1]),
                int(rgb[2]),
            )
            self.colorUsed.emit(
                QColor(red, green, blue)
            )
            emitted += 1
            if emitted >= max(1, int(maximum)):
                break

    def _binary_paint_overlay_rgba(self, overlay):
        """描画マスクの境界を完全な0／255へ2値化する。

        筆圧による線幅変化、曲線、ラインの入り抜きで発生した
        半端なアルファ値を、RGB合成の前に除去する。
        """
        rgba = self._qimage_rgba_array(overlay)
        if rgba.size == 0:
            return rgba

        alpha = rgba[:, :, 3]
        active = alpha >= 128

        # 非描画部分は完全な透明、描画部分は完全な不透明に固定する。
        rgba[~active, :3] = 0
        rgba[:, :, 3][~active] = 0
        rgba[:, :, 3][active] = 255
        return rgba

    @staticmethod
    def _paint_rgb_palette(*args, **kwargs):
        return colors.paint_rgb_palette(*args, **kwargs)

    @classmethod
    def _snap_overlay_to_exact_rgbs(cls, *args, **kwargs):
        return color_ops._snap_overlay_to_exact_rgbs(*args, **kwargs)

    def _blend_overlay_into_active_layer(
        self,
        overlay,
        top_left=None,
        base_image=None,
        opacity=None,
        exact_colors=None,
    ):
        """100%は正規RGB直書き、100%未満だけ通常の不透明度合成。"""
        if overlay is None or overlay.isNull():
            return ()

        destination_image = self.active_layer.image
        if (
            destination_image is None
            or destination_image.isNull()
        ):
            return ()

        point = (
            top_left
            if top_left is not None
            else QPoint(0, 0)
        )
        origin_x = int(point.x())
        origin_y = int(point.y())

        source_x = max(0, -origin_x)
        source_y = max(0, -origin_y)
        destination_x = max(0, origin_x)
        destination_y = max(0, origin_y)
        width = min(
            overlay.width() - source_x,
            destination_image.width() - destination_x,
        )
        height = min(
            overlay.height() - source_y,
            destination_image.height() - destination_y,
        )
        if width <= 0 or height <= 0:
            return ()

        overlay_rgba = self._binary_paint_overlay_rgba(
            overlay
        )
        overlay_rgba = overlay_rgba[
            source_y:source_y + height,
            source_x:source_x + width,
        ]
        active_mask = (
            overlay_rgba[:, :, 3] == 255
        )
        if not np.any(active_mask):
            return ()

        # プレマルチプライ変換の±1誤差を、正規RGBへ必ず吸着する。
        overlay_rgba = self._snap_overlay_to_exact_rgbs(
            overlay_rgba,
            active_mask,
            exact_colors,
        )

        amount = self.paint_opacity_value(opacity)
        if amount <= 0.0:
            return ()

        result_rgba = np.zeros_like(overlay_rgba)

        if amount >= 0.999999:
            # 最重要経路：100%では下地を参照せず正規RGBを直書きする。
            # ここでは新しい近似RGBを生成しない。
            result_rgba[active_mask, :3] = (
                overlay_rgba[active_mask, :3]
            )
            result_rgba[active_mask, 3] = 255
            written_rgb = overlay_rgba[
                active_mask,
                :3,
            ]
        else:
            # 不透明度を明示的にONにした場合だけ、通常のRGB合成を行う。
            if (
                base_image is not None
                and not base_image.isNull()
                and base_image.width() == width
                and base_image.height() == height
            ):
                base_crop = base_image
            else:
                reference = (
                    base_image
                    if (
                        base_image is not None
                        and not base_image.isNull()
                        and base_image.width() == destination_image.width()
                        and base_image.height() == destination_image.height()
                    )
                    else destination_image
                )
                base_crop = reference.copy(
                    destination_x,
                    destination_y,
                    width,
                    height,
                )
            base_rgba = self._qimage_rgba_array(
                base_crop
            )
            base_rgb = base_rgba[
                :, :, :3
            ].astype(np.float32)
            base_rgb[
                base_rgba[:, :, 3] == 0
            ] = 255.0

            source_rgb = overlay_rgba[
                :, :, :3
            ].astype(np.float32)
            blended_rgb = np.clip(
                np.rint(
                    base_rgb * (1.0 - amount)
                    + source_rgb * amount
                ),
                0,
                255,
            ).astype(np.uint8)

            result_rgba[
                active_mask,
                :3,
            ] = blended_rgb[active_mask]
            result_rgba[
                active_mask,
                3,
            ] = 255
            written_rgb = blended_rgb[
                active_mask
            ]

        result_image = self._rgba_array_to_qimage(
            result_rgba
        )
        painter = QPainter(destination_image)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver
        )
        painter.drawImage(
            QPoint(destination_x, destination_y),
            result_image,
        )
        painter.end()

        if written_rgb.size == 0:
            return ()

        # 100%は正規RGBだけを通知する。
        if amount >= 0.999999:
            palette = self._paint_rgb_palette(
                exact_colors
            )
            if palette.size:
                return tuple(
                    tuple(int(channel) for channel in rgb)
                    for rgb in palette
                )

        unique = np.unique(
            written_rgb,
            axis=0,
        )
        return tuple(
            tuple(int(channel) for channel in rgb)
            for rgb in unique
        )

    def _begin_opaque_brush_stroke(self):
        """Start a stroke without allocating or copying a full-layer image."""
        self._stroke_before_tiles = {}
        self._stroke_dirty_rect = QRect()
        self._stroke_undo_frame = int(self.current_frame)
        self._stroke_undo_layer = int(self.active_layer_index)
        self._stroke_prev_has_content = bool(self.active_layer.has_content)
        self._brush_blend_base_image = None
        self._brush_blended_colors = set()
        # この値はUIの不透明度だけから取得する。
        # タブレット筆圧値は絶対に掛けない。
        self._brush_stroke_opacity = (
            self.paint_opacity_value()
        )
        self._init_stroke_display()

    def _ensure_before_region(self, rect):
        """Lazily preserve pre-stroke pixels in fixed-size touched tiles."""
        tiles = self._stroke_before_tiles
        if tiles is None:
            return
        live = self.active_layer.image
        bounds = QRect(
            0, 0, live.width(), live.height()
        )
        rect = QRect(rect).intersected(bounds)
        if rect.isEmpty():
            return
        tile_size = 256
        first_x = rect.left() // tile_size
        last_x = rect.right() // tile_size
        first_y = rect.top() // tile_size
        last_y = rect.bottom() // tile_size
        for tile_y in range(first_y, last_y + 1):
            for tile_x in range(first_x, last_x + 1):
                key = (tile_x, tile_y)
                if key in tiles:
                    continue
                tile_rect = QRect(
                    tile_x * tile_size,
                    tile_y * tile_size,
                    tile_size,
                    tile_size,
                ).intersected(bounds)
                tiles[key] = (tile_rect, live.copy(tile_rect))
        self._stroke_dirty_rect = self._stroke_dirty_rect.united(rect)

    def _stroke_before_region(self, rect):
        """Assemble a small immutable pre-stroke crop from saved tiles."""
        rect = QRect(rect)
        tiles = self._stroke_before_tiles
        if tiles is None or rect.isEmpty():
            return QImage()
        result = QImage(
            rect.width(),
            rect.height(),
            self.active_layer.image.format(),
        )
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Source
        )
        tile_size = 256
        for tile_y in range(
            rect.top() // tile_size,
            rect.bottom() // tile_size + 1,
        ):
            for tile_x in range(
                rect.left() // tile_size,
                rect.right() // tile_size + 1,
            ):
                saved = tiles.get((tile_x, tile_y))
                if saved is None:
                    continue
                tile_rect, tile_image = saved
                overlap = QRect(tile_rect).intersected(rect)
                if overlap.isEmpty():
                    continue
                source = QRect(
                    overlap.x() - tile_rect.x(),
                    overlap.y() - tile_rect.y(),
                    overlap.width(),
                    overlap.height(),
                )
                painter.drawImage(
                    QPoint(overlap.x() - rect.x(), overlap.y() - rect.y()),
                    tile_image,
                    source,
                )
        painter.end()
        return result

    def _finish_opaque_brush_stroke(self):
        colors = tuple(
            getattr(self, "_brush_blended_colors", set())
        )
        self._emit_actual_paint_colors(colors)
        if (
            self._stroke_before_tiles is not None
            and self._stroke_dirty_rect is not None
            and not self._stroke_dirty_rect.isEmpty()
        ):
            tiles = tuple(
                (QRect(rect), image)
                for rect, image in self._stroke_before_tiles.values()
            )
            self.undo_stack.append((
                "layer_tiles",
                self._stroke_undo_frame,
                self._stroke_undo_layer,
                tiles,
                self._stroke_prev_has_content,
            ))
            self.undo_stack = self.undo_stack[-MAX_UNDO:]
            self.redo_stack.clear()
        self._stroke_before_tiles = None
        self._stroke_dirty_rect = None
        self._brush_blend_base_image = None
        self._brush_blended_colors = set()
        self._brush_stroke_opacity = (
            self.paint_opacity_value()
        )
        self._finish_stroke_display()

    def _stroke_display_eligible(self):
        """True when the incremental display buffer can represent the layer.

        Only when the active layer is a normal (non-paper) layer with no
        palette/colour filter and silhouette mode off — otherwise the display
        image is not a plain white-to-transparent transform of ``layer.image``
        and the slower full-image path stays correct.
        """
        layer = self.active_layer
        if layer is None or getattr(layer, "is_paper", False):
            return False
        if self.silhouette_non_background:
            return False
        if getattr(self, "visible_color_rgbs", None):
            return False
        if (
            getattr(layer, "color_filter_enabled", False)
            and layer.color_filter_rgb is not None
        ):
            return False
        return True

    def prewarm_blank_stroke_display(self):
        """Prepare a new blank layer's display buffer before the first press."""
        if not self._stroke_display_eligible():
            return
        layer = self.active_layer
        if layer.has_content or layer.image is None or layer.image.isNull():
            return
        key = self._pseudo_transparency_key(layer.image)
        if key in self._pseudo_transparency_cache:
            return
        display = QImage(
            layer.image.size(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        display.fill(Qt.GlobalColor.transparent)
        if len(self._pseudo_transparency_cache) >= 96:
            self._pseudo_transparency_cache.pop(
                next(iter(self._pseudo_transparency_cache))
            )
        self._pseudo_transparency_cache[key] = display

    def warm_up_brush_runtime(self):
        """Prime Qt/numpy brush primitives without touching the document."""
        if self._brush_runtime_warmed:
            return

        scratch = QImage(
            64,
            64,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        scratch.fill(Qt.GlobalColor.transparent)
        painter = QPainter(scratch)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            False,
        )
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver
        )
        painter.setPen(QPen(
            QColor(32, 64, 96, 255),
            8.0,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        ))
        painter.drawLine(QPointF(12, 12), QPointF(52, 44))
        painter.end()

        # Exercise the same format conversion and ndarray view used by the
        # first real stamp.  Keep the scratch image local so no display cache,
        # layer pixels, or undo state is affected.
        rgba = scratch.convertToFormat(QImage.Format.Format_RGBA8888)
        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (rgba.height(), rgba.bytesPerLine())
        )
        _ = rows[:, : rgba.width() * 4].reshape(
            (rgba.height(), rgba.width(), 4)
        )[:, :, 3].max()
        rgba.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        self.pressure_size_scale(1.0)
        self._brush_runtime_warmed = True

    def _init_stroke_display(self):
        """Move the cached display image into stroke ownership without copying.

        The per-stamp cost then becomes proportional to the brush footprint
        and a warm-cache stroke start is independent of canvas size.
        """
        self._stroke_display_image = None
        self._stroke_display_layer_index = -1
        if not self._stroke_display_eligible():
            return
        layer = self.active_layer
        cache_key = self._pseudo_transparency_key(layer.image)
        base = self._pseudo_transparency_cache.pop(cache_key, None)
        if base is None:
            if (
                not layer.has_content
                or self._editable_key_was_blank
            ):
                # A brand-new canvas can receive a press before its first idle
                # repaint.  Its layer is known to be entirely transparent, so
                # avoid scanning and converting the whole workspace on the
                # first stroke.  The buffer is patched incrementally below as
                # stamps are written to the real layer image.
                base = QImage(
                    layer.image.size(),
                    QImage.Format.Format_ARGB32_Premultiplied,
                )
                base.fill(Qt.GlobalColor.transparent)
            else:
                # Usually the idle repaint has already populated this cache.
                # Keep a correctness fallback for presses arriving before the
                # first paint on a layer that already contains pixels.
                base = self._pseudo_transparent_display_image(layer.image)
                self._pseudo_transparency_cache.pop(cache_key, None)
        if base is None or base.isNull():
            return
        self._stroke_display_image = base
        self._stroke_display_layer_index = self.active_layer_index

    def _patch_stroke_display(self, canvas_rect):
        """Re-apply the white→transparent transform for one stamped region."""
        buffer = self._stroke_display_image
        if buffer is None:
            return
        self._patch_pseudo_transparent_display(
            buffer,
            self.active_layer.image,
            canvas_rect,
        )

    @staticmethod
    def _patch_pseudo_transparent_display(buffer, image, canvas_rect):
        """Update one region of an existing pseudo-transparent display."""
        x1 = max(0, int(canvas_rect.left()))
        y1 = max(0, int(canvas_rect.top()))
        x2 = min(image.width(), int(canvas_rect.left() + canvas_rect.width()))
        y2 = min(image.height(), int(canvas_rect.top() + canvas_rect.height()))
        w, h = x2 - x1, y2 - y1
        if w <= 0 or h <= 0:
            return
        sub = image.copy(x1, y1, w, h).convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        ptr = sub.bits()
        try:
            ptr.setsize(sub.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (h, sub.bytesPerLine())
        )
        pixels = rows[:, : w * 4].reshape((h, w, 4))
        alpha = pixels[:, :, 3]
        present = alpha > 0
        white = (
            present
            & (pixels[:, :, 0] == 255)
            & (pixels[:, :, 1] == 255)
            & (pixels[:, :, 2] == 255)
        )
        alpha[white] = 0
        alpha[present & ~white] = 255
        patch = sub.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        painter = QPainter(buffer)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Source
        )
        painter.drawImage(x1, y1, patch)
        painter.end()

    def _cache_pseudo_transparent_display(self, image, display):
        if display is None or display.isNull():
            return
        key = self._pseudo_transparency_key(image)
        if len(self._pseudo_transparency_cache) >= 96:
            self._pseudo_transparency_cache.pop(
                next(iter(self._pseudo_transparency_cache))
            )
        self._pseudo_transparency_cache[key] = display

    def _finish_stroke_display(self):
        """Hand the fully-patched buffer to the display cache, then release it.

        Seeding the cache under the layer's current key means the first repaint
        after the stroke is a cache hit instead of another full re-transform.
        """
        buffer = self._stroke_display_image
        self._stroke_display_image = None
        self._stroke_display_layer_index = -1
        if buffer is None:
            return
        layer = self.active_layer
        if layer is None or layer.image is None or layer.image.isNull():
            return
        self._cache_pseudo_transparent_display(layer.image, buffer)

    def selected_mask_colors(self):
        values = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in getattr(self, "mask_color_rgbs", set())
            if rgb is not None and len(rgb) >= 3
        }
        legacy = getattr(self, "mask_color_rgb", None)
        if legacy is not None and not values:
            values.add(tuple(int(channel) for channel in legacy[:3]))
        return values
    def selected_used_colors(self):
        return {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in getattr(self, "selected_used_color_rgbs", set())
            if rgb is not None and len(rgb) >= 3
        }

    def _invalidate_tp_preview_cache(self, geometry=True):
        """Invalidate TP output; geometry=False keeps transformed mask cache."""
        self._tp_preview_cache_key = None
        self._tp_preview_cache_image = None
        if geometry:
            self._tp_geometry_cache_key = None
            self._tp_geometry_cache_bbox = None
            self._tp_geometry_cache_fill_overlay = None
            self._tp_geometry_cache_line_soft = []

    def _clear_tp_transform_masks(self, clear_proxy=True):
        self.transform_tp_palette = []
        self.transform_tp_masks = []
        self.transform_tp_line_masks = []
        self.transform_tp_prepared_preview = None
        self._tp_mask_source_key = None
        if clear_proxy:
            self._tp_proxy_source_key = None
            self._tp_proxy_source_image = None
        self._invalidate_tp_preview_cache()

    def _tp_mask_key(self, source):
        if source is None or source.isNull():
            return None
        return (
            int(source.cacheKey()),
            int(source.width()),
            int(source.height()),
            tuple(self.transform_tp_line_colors),
            bool(self._tp_proxy_rendering),
        )

    @staticmethod
    def _tp_uses_proxy(*args, **kwargs):
        return imaging.tp_uses_proxy(*args, **kwargs)

    def _tp_geometry_cache_is_current(self, source=None, target_width=None, target_height=None):
        source = source if source is not None else self.transform_source
        if source is None or source.isNull() or not self.transform_points:
            return False
        target_width = int(
            target_width if target_width is not None else self.active_layer.image.width()
        )
        target_height = int(
            target_height if target_height is not None else self.active_layer.image.height()
        )
        bbox, _raw_selection = self._tp_transform_bbox(target_width, target_height)
        if bbox.isEmpty():
            return self._tp_geometry_cache_key is not None
        key = self._tp_geometry_key(source, target_width, target_height, bbox)
        return (
            self._tp_geometry_cache_key == key
            and self._tp_geometry_cache_fill_overlay is not None
        )

    def request_quality_preview_counter(self, label="クオリティプレビューを生成しています"):
        """Schedule quality rendering after the current paint/input event."""
        if (
            self._tp_preview_progress_busy
            or self._tp_preview_progress_scheduled
            or not self.transform_active
            or not self.transform_quality_active
        ):
            return
        self._tp_preview_progress_scheduled = True
        QTimer.singleShot(
            0,
            lambda text=str(label): self.refresh_quality_preview_with_counter(text),
        )

    def refresh_quality_preview_with_counter(
        self,
        label="クオリティプレビューを生成しています",
        full_resolution=False,
    ):
        """Build the expensive TP_mask preview with a visible mask counter."""
        self._tp_preview_progress_scheduled = False
        if (
            self._tp_preview_progress_busy
            or not self.transform_active
            or not self.transform_quality_active
            or self._transform_line_adjusting
            or self.transform_source is None
            or self.transform_source.isNull()
        ):
            return

        target_width = self.active_layer.image.width()
        target_height = self.active_layer.image.height()
        use_proxy = (
            not full_resolution
            and self._tp_uses_proxy(target_width, target_height)
        )
        if use_proxy:
            if self._tp_preview_cache_image is not None:
                return
        else:
            geometry_current = self._tp_geometry_cache_is_current(
                self.transform_source, target_width, target_height
            )
            if geometry_current and self._tp_preview_cache_image is not None:
                return

        total = max(
            1,
            len(self.transform_tp_masks)
            + len(self.transform_tp_line_masks)
            + 2,
        )
        window = self.window()
        progress = None
        can_show_counter = all(
            hasattr(window, name)
            for name in (
                "create_progress_counter",
                "update_progress_counter",
                "close_progress_counter",
            )
        )

        # create_progress_counter() calls processEvents().  Mark the renderer as
        # busy first so a repaint during popup creation uses the lightweight
        # preview instead of recursively starting another TP_mask calculation.
        self._tp_preview_progress_busy = True
        try:
            if can_show_counter:
                progress = window.create_progress_counter(
                    (
                        "Tp_mask 軽量プレビュー"
                        if use_proxy
                        else "Tp_mask クオリティプレビュー"
                    ),
                    total,
                    label,
                )

            def report(value, report_total, message):
                if progress is not None:
                    window.update_progress_counter(
                        progress, value, report_total, message
                    )

            self.transform_preview_image(
                self.transform_source,
                target_width,
                target_height,
                quality=True,
                preview_only=use_proxy,
                progress_callback=report,
            )
            if progress is not None:
                window.update_progress_counter(
                    progress,
                    total,
                    total,
                    "プレビューを更新しました",
                )
        except (ValueError, IndexError, TypeError, RuntimeError, AttributeError, MemoryError) as exc:
            log.warning("quality preview generation failed: %s", exc, exc_info=True)
            self.status_message.emit(
                f"クオリティプレビューを生成できませんでした: {exc}"
            )
        finally:
            if progress is not None:
                window.close_progress_counter(progress)
            self._tp_preview_progress_busy = False
        self.update()

    def set_transform_quality(self, enabled):
        self.transform_quality = bool(enabled)
        if self.transform_quality:
            self.transform_apply_all_frames = False
        if self.transform_active:
            self.transform_quality_active = self.transform_quality
            self.transform_tp_line_colors = tuple(sorted(self.selected_used_colors()))
            if self.transform_quality_active:
                target_width = self.active_layer.image.width()
                target_height = self.active_layer.image.height()
                if self._tp_uses_proxy(target_width, target_height):
                    self._clear_tp_transform_masks(clear_proxy=False)
                else:
                    self._prepare_tp_transform_masks()
            else:
                self._clear_tp_transform_masks()
        self._invalidate_tp_preview_cache()
        if self.transform_active and self.transform_quality_active:
            self.request_quality_preview_counter(
                "クオリティ方式へ切り替えています"
            )
        self.update()

    def set_transform_line_threshold(self, value):
        self.transform_line_threshold = max(1, min(254, int(value)))
        # v0.7 caches transformed soft masks.  A line-width change only
        # reapplies the threshold and does not transform every color again.
        self._invalidate_tp_preview_cache(geometry=False)
        if (
            self.transform_active
            and self.transform_quality_active
            and not self._tp_preview_progress_busy
            and self._tp_geometry_cache_fill_overlay is not None
        ):
            target_width = self.active_layer.image.width()
            target_height = self.active_layer.image.height()
            self.transform_preview_image(
                self.transform_source,
                target_width,
                target_height,
                quality=True,
                preview_only=self._tp_uses_proxy(
                    target_width, target_height
                ),
            )
        self.update()

    def begin_transform_line_adjustment(self):
        """スライダー操作中は重いクオリティ再生成を保留する。"""
        self._transform_line_adjusting = True
        self._transform_line_preview_timer.stop()
        self._tp_preview_progress_scheduled = False

    def finish_transform_line_adjustment(self):
        """スライダーを離した時にクオリティプレビューを一度だけ更新する。"""
        was_adjusting = self._transform_line_adjusting
        self._transform_line_adjusting = False
        if (
            was_adjusting
            and self.transform_active
            and self.transform_quality_active
        ):
            # マウスリリース処理を先に完了させ、クリックが確定操作の
            # ように見えないよう少し遅らせて一度だけ生成する。
            self._transform_line_preview_timer.start()
        self.update()

    def _render_deferred_transform_line_preview(self):
        if self.transform_active and self.transform_quality_active:
            self.request_quality_preview_counter(
                "実線の太さをプレビューへ反映しています"
            )

    def set_transform_line_colors(self, colors):
        normalized = {
            tuple(int(channel) for channel in rgb[:3])
            for rgb in (colors or set())
            if rgb is not None and len(rgb) >= 3
        }
        self.selected_used_color_rgbs = normalized
        if self.transform_active:
            self.transform_tp_line_colors = tuple(sorted(normalized))
            if self.transform_quality_active:
                target_width = self.active_layer.image.width()
                target_height = self.active_layer.image.height()
                self._clear_tp_transform_masks(clear_proxy=False)
                if not self._tp_uses_proxy(target_width, target_height):
                    self._prepare_tp_transform_masks()
                self.request_quality_preview_counter(
                    "実線色を反映しています"
                )
            else:
                self._invalidate_tp_preview_cache()
            self.update()

    def apply_selection_clip(self, painter, offset_x=0.0, offset_y=0.0):
        """Apply the active selection without relying on PySide overload-specific arguments."""
        if not self.selection_polygon:
            return
        if self.selection_mask_override is not None:
            device = painter.device()
            mask = self.selection_mask_bool(
                device.width(),
                device.height(),
                int(round(offset_x)),
                int(round(offset_y)),
            )
            region = QRegion()
            for y in range(mask.shape[0]):
                row = mask[y]  # pyright: ignore[reportOptionalSubscript]
                changes = np.diff(
                    np.pad(row.astype(np.int8), (1, 1))
                )
                starts = np.flatnonzero(changes == 1)
                ends = np.flatnonzero(changes == -1)
                for start, end in zip(starts, ends):
                    region += QRegion(int(start), y, int(end - start), 1)
            painter.setClipRegion(region)
            return
        polygon = QPolygonF([
            QPointF(float(point.x()) - float(offset_x), float(point.y()) - float(offset_y))
            for point in self.selection_polygon
        ])
        if len(polygon) >= 3:
            path = QPainterPath()
            path.addPolygon(polygon)
            path.closeSubpath()
            painter.setClipPath(path)

    def pressure_size_scale(self, pressure):
        """筆圧から線幅倍率だけを返す。不透明度には使用しない。"""
        if not self.pressure_enabled:
            return 1.0
        pressure = max(
            0.0,
            min(1.0, float(pressure)),
        )
        points = getattr(
            self,
            "pressure_curve_points",
            None,
        )
        if points and len(points) >= 2:
            value = _pressure_bezier_at(
                points,
                pressure,
            )
        else:
            value = pressure ** self.pressure_curve
        return (
            max(self.pressure_min, value)
            * self.pressure_max
        )

    def pressure_value(self, pressure):
        """旧呼び出し互換。返す値は線幅倍率のみ。"""
        return self.pressure_size_scale(pressure)

    def _update_stroke_region(self, a, b, width):
        """Repaint only the widget area touched by a brush segment."""
        wa = self.canvas_to_widget(a)
        wb = self.canvas_to_widget(b)
        margin = max(4, int(math.ceil(float(width) * max(self.zoom, 0.01) / 2.0)) + 3)
        dirty = QRectF(wa, wb).normalized().adjusted(
            -margin, -margin, margin, margin
        ).toAlignedRect()
        self.update(dirty)

    def draw_line(self,a,b,pressure):
        """完全な非AAマスクで、下地色と均一にRGB合成する。"""
        painter_tool = self.effective_tool()
        source_color = self.paint_source_color()
        # 筆圧はここで線幅にだけ使用する。
        width = max(
            0.5,
            self.pen_size
            * self.pressure_size_scale(pressure)
        )
        draw_width = max(0.5, float(width))
        raster_a = QPointF(float(a.x()), float(a.y()))
        raster_b = QPointF(float(b.x()), float(b.y()))

        margin = int(math.ceil(draw_width / 2.0)) + 3
        left = max(
            0,
            int(math.floor(min(a.x(), b.x()))) - margin,
        )
        top = max(
            0,
            int(math.floor(min(a.y(), b.y()))) - margin,
        )
        right = min(
            self.active_layer.image.width(),
            int(math.ceil(max(a.x(), b.x()))) + margin + 1,
        )
        bottom = min(
            self.active_layer.image.height(),
            int(math.ceil(max(a.y(), b.y()))) + margin + 1,
        )
        if right <= left or bottom <= top:
            return

        rect = QRectF(
            left,
            top,
            right - left,
            bottom - top,
        ).toAlignedRect()
        self._ensure_before_region(rect)
        before_region = self._stroke_before_region(rect)
        overlay = QImage(
            rect.width(),
            rect.height(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        overlay.fill(Qt.GlobalColor.transparent)

        painter = QPainter(overlay)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing,
            False,
        )
        self.apply_selection_clip(
            painter,
            rect.x(),
            rect.y(),
        )
        local_a = QPointF(
            raster_a.x() - rect.x(),
            raster_a.y() - rect.y(),
        )
        local_b = QPointF(
            raster_b.x() - rect.x(),
            raster_b.y() - rect.y(),
        )
        painter.setPen(
            QPen(
                source_color,
                draw_width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawLine(local_a, local_b)
        painter.end()

        sub_only = bool(
            painter_tool == "brush"
            and not getattr(self, "mask_all_enabled", True)
        )
        if sub_only:
            overlay_rgba = self._qimage_rgba_array(overlay)
            base_region = before_region
            base_rgba = self._qimage_rgba_array(base_region)

            selected_rgbs = self.selected_mask_colors()
            eligible = np.zeros(
                base_rgba.shape[:2],
                dtype=bool,
            )
            if selected_rgbs:
                packed = (
                    (
                        base_rgba[:, :, 0].astype(np.uint32)
                        << 16
                    )
                    | (
                        base_rgba[:, :, 1].astype(np.uint32)
                        << 8
                    )
                    | base_rgba[:, :, 2].astype(np.uint32)
                )
                selected_values = np.fromiter(
                    (
                        (r << 16) | (g << 8) | b
                        for r, g, b in selected_rgbs
                    ),
                    dtype=np.uint32,
                    count=len(selected_rgbs),
                )
                eligible |= (
                    (base_rgba[:, :, 3] > 0)
                    & np.isin(packed, selected_values)
                )
                if self.background_mask_rgb in selected_rgbs:
                    eligible |= (
                        (base_rgba[:, :, 3] == 0)
                        | np.all(
                            base_rgba[:, :, :3] == 255,
                            axis=2,
                        )
                    )

            overlay_rgba[:, :, 3][~eligible] = 0
            overlay = self._rgba_array_to_qimage(
                overlay_rgba
            )

        # 筆圧とは無関係な、ストローク開始時の固定不透明度。
        fixed_opacity = float(
            getattr(
                self,
                "_brush_stroke_opacity",
                self.paint_opacity_value(),
            )
        )
        colors = self._blend_overlay_into_active_layer(
            overlay,
            rect.topLeft(),
            base_image=before_region,
            opacity=fixed_opacity,
            exact_colors=(source_color,),
        )
        color_set = getattr(
            self,
            "_brush_blended_colors",
            None,
        )
        if color_set is not None:
            color_set.update(colors)

        self.active_layer.has_content = True
        if (
            self._stroke_display_image is not None
            and self.active_layer_index == self._stroke_display_layer_index
        ):
            self._patch_stroke_display(rect)
        self._update_stroke_region(a, b, draw_width)

    @staticmethod
    def _scanline_connected_region(*args, **kwargs):
        return imaging.scanline_connected_region(*args, **kwargs)

    def flood_fill(self,p):
        self.push_layer_undo()
        image = self.active_layer.image.convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        width, height = image.width(), image.height()
        start_x, start_y = int(p.x()), int(p.y())
        if not (0 <= start_x < width and 0 <= start_y < height):
            if self.undo_stack:
                self.undo_stack.pop()
            return

        selection_mask = self.selection_mask_bool(width, height)
        if selection_mask is not None and not selection_mask[start_y, start_x]:
            if self.undo_stack:
                self.undo_stack.pop()
            self.status_message.emit("選択範囲の外側なので塗りを開始しませんでした。")
            return

        ptr = image.bits()
        try:
            ptr.setsize(image.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, image.bytesPerLine())
        )
        pixels = rows[:, :width * 4].reshape((height, width, 4))

        target = pixels[start_y, start_x].copy()
        replacement = self.paint_source_color()
        paint_opacity = self.paint_opacity_value()

        window = self.window()
        include_masks = bool(
            hasattr(window, "tools")
            and window.tools.bucket_include_sub.isChecked()
        )
        close_gap = bool(
            hasattr(window, "tools")
            and window.tools.bucket_close_gap.isChecked()
        )
        adjacent_fill = bool(
            not hasattr(window, "tools")
            or window.tools.bucket_adjacent.isChecked()
        )

        pseudo_background = (
            (pixels[:, :, 3] == 0)
            | np.all(pixels[:, :, :3] == 255, axis=2)
        )
        target_is_background = bool(
            int(target[3]) == 0
            or (
                int(target[0]) == 255
                and int(target[1]) == 255
                and int(target[2]) == 255
            )
        )
        if target_is_background:
            target_mask = pseudo_background.copy()
            same_color_mask = pseudo_background.copy()
        else:
            target_mask = (
                (pixels[:, :, 3] > 0)
                & np.all(pixels[:, :, :3] == target[:3], axis=2)
            )
            same_color_mask = target_mask.copy()
        if selection_mask is not None:
            target_mask &= selection_mask
            same_color_mask &= selection_mask

        def dilate(mask, iterations):
            result = mask.copy()
            for _ in range(iterations):
                source = result.copy()
                result[1:, :] |= source[:-1, :]
                result[:-1, :] |= source[1:, :]
                result[:, 1:] |= source[:, :-1]
                result[:, :-1] |= source[:, 1:]
                result[1:, 1:] |= source[:-1, :-1]
                result[:-1, :-1] |= source[1:, 1:]
                result[1:, :-1] |= source[:-1, 1:]
                result[:-1, 1:] |= source[1:, :-1]
            return result

        def erode(mask, iterations):
            result = mask.copy()
            for _ in range(iterations):
                padded = np.pad(
                    result,
                    ((1, 1), (1, 1)),
                    mode="constant",
                    constant_values=False,
                )
                result = (
                    padded[0:-2, 0:-2]
                    & padded[0:-2, 1:-1]
                    & padded[0:-2, 2:]
                    & padded[1:-1, 0:-2]
                    & padded[1:-1, 1:-1]
                    & padded[1:-1, 2:]
                    & padded[2:, 0:-2]
                    & padded[2:, 1:-1]
                    & padded[2:, 2:]
                )
            return result

        if adjacent_fill:
            boundary = ~target_mask
            gap_recovery_mask = None
            if close_gap:
                gap_radius = max(
                    1,
                    int(window.tools.bucket_gap_width.value())
                    if hasattr(window, "tools")
                    else 4,
                )
                virtual_boundary = erode(
                    dilate(boundary, gap_radius),
                    gap_radius,
                )
                # 仮想境界で漏れだけを止め、元画像では塗れる細い領域を
                # 最終描画時に回収する。線そのものは target_mask 外なので
                # 回収対象にはならない。
                gap_recovery_mask = virtual_boundary & target_mask
            else:
                virtual_boundary = boundary

            passable = target_mask & ~virtual_boundary
            if selection_mask is not None:
                passable &= selection_mask
            passable[start_y, start_x] = bool(target_mask[start_y, start_x])
            region = self._scanline_connected_region(
                passable, (start_x, start_y)
            )

            if not np.any(region):
                if self.undo_stack:
                    self.undo_stack.pop()
                return

            require_closed = bool(
                hasattr(window, "tools")
                and window.tools.bucket_require_closed.isChecked()
            )
            # A selection itself acts as a closed boundary.
            if selection_mask is None and require_closed and (
                np.any(region[0, :]) or np.any(region[-1, :])
                or np.any(region[:, 0]) or np.any(region[:, -1])
            ):
                if self.undo_stack:
                    self.undo_stack.pop()
                self.status_message.emit(
                    "領域がキャンバス端まで開いているため、塗りを開始しませんでした。"
                )
                return

            if close_gap and gap_recovery_mask is not None:
                touching = dilate(region, 1) & gap_recovery_mask
                start_y_values, start_x_values = np.nonzero(touching)
                if start_x_values.size:
                    recovered = self._scanline_connected_region(
                        gap_recovery_mask,
                        zip(start_x_values.tolist(), start_y_values.tolist()),
                    )
                    region |= recovered
        else:
            # 「隣接」OFFでは、クリック位置と同じRGBAを持つ全ピクセルを対象にする。
            region = same_color_mask.copy()

        final_region = region.copy()
        if include_masks:
            selected_colors = self.selected_used_colors()
            rgb = pixels[:, :, :3].astype(np.int32)
            opaque_pixels = pixels[:, :, 3] > 0
            mask_family = np.zeros((height, width), dtype=bool)
            for selected_color in selected_colors:
                target_rgb = np.array(selected_color, dtype=np.int32)
                delta = rgb - target_rgb
                mask_family |= (
                    opaque_pixels
                    & (np.sum(delta * delta, axis=2) <= 56 * 56)
                )
            if self.background_mask_rgb in selected_colors:
                mask_family |= pseudo_background
            if selection_mask is not None:
                mask_family &= selection_mask

            if adjacent_fill:
                adjacent = np.zeros_like(region)
                adjacent[1:, :] |= region[:-1, :]
                adjacent[:-1, :] |= region[1:, :]
                adjacent[:, 1:] |= region[:, :-1]
                adjacent[:, :-1] |= region[:, 1:]
                seed_points = np.argwhere(mask_family & adjacent)
                if seed_points.size:
                    starts = [
                        (int(point[1]), int(point[0]))
                        for point in seed_points
                    ]
                    final_region |= self._scanline_connected_region(
                        mask_family, starts
                    )
            else:
                # 非隣接モードでは、選択された使用色もレイヤー全体から一括対象にする。
                final_region |= mask_family

        if selection_mask is not None:
            final_region &= selection_mask
        if not np.any(final_region):
            if self.undo_stack:
                self.undo_stack.pop()
            return

        ys, xs = np.nonzero(final_region)
        if len(xs) == 0:
            if self.undo_stack:
                self.undo_stack.pop()
            return

        # Replace the provisional full-layer snapshot with the actual changed
        # bounding box now that the fill region is known.
        if self.undo_stack and self.undo_stack[-1][0] == "layer":
            previous = self.undo_stack[-1]
            fill_rect = QRect(
                int(xs.min()),
                int(ys.min()),
                int(xs.max() - xs.min() + 1),
                int(ys.max() - ys.min() + 1),
            )
            self.undo_stack[-1] = (
                "layer_region",
                int(previous[1]),
                int(previous[2]),
                fill_rect,
                previous[3].copy(fill_rect),
                bool(previous[4]),
            )

        source_rgb = np.asarray(
            [
                replacement.red(),
                replacement.green(),
                replacement.blue(),
            ],
            dtype=np.uint8,
        )

        if paint_opacity >= 0.999999:
            # 100%は正規RGBをそのまま書き込み、近似色を生成しない。
            pixels[ys, xs, :3] = source_rgb
            written_rgb = source_rgb.reshape(
                (1, 3)
            )
        else:
            # 不透明度チェックONかつ100%未満の場合だけ通常混色。
            destination_rgb = pixels[
                ys,
                xs,
                :3,
            ].astype(np.float32)
            transparent_pixels = (
                pixels[ys, xs, 3] == 0
            )
            destination_rgb[
                transparent_pixels
            ] = 255.0
            source_float = source_rgb.astype(
                np.float32
            )
            written_rgb = np.clip(
                np.rint(
                    destination_rgb
                    * (1.0 - paint_opacity)
                    + source_float[None, :]
                    * paint_opacity
                ),
                0,
                255,
            ).astype(np.uint8)
            pixels[ys, xs, :3] = written_rgb

        pixels[ys, xs, 3] = 255

        self.active_layer.image = image.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        self.active_layer.has_content = True
        self._emit_actual_paint_colors(
            (
                (
                    int(source_rgb[0]),
                    int(source_rgb[1]),
                    int(source_rgb[2]),
                ),
            )
            if paint_opacity >= 0.999999
            else np.unique(written_rgb, axis=0)
        )
        self.cellChanged.emit(self.current_frame, self.active_layer_index)
        self.update()

    def auto_select_region(self, point, modifiers=Qt.KeyboardModifier.NoModifier):
        """バケツと同じ連続領域判定で選択マスクを作成・加減算する。"""
        image = self.active_layer.image.convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        width, height = image.width(), image.height()
        start_x, start_y = int(point.x()), int(point.y())
        if not (0 <= start_x < width and 0 <= start_y < height):
            return False

        ptr = image.bits()
        try:
            ptr.setsize(image.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, image.bytesPerLine())
        )
        pixels = rows[:, :width * 4].reshape((height, width, 4))
        target = pixels[start_y, start_x]
        pseudo_background = (
            (pixels[:, :, 3] == 0)
            | np.all(pixels[:, :, :3] == 255, axis=2)
        )
        target_is_background = bool(
            int(target[3]) == 0
            or np.all(target[:3] == 255)
        )
        target_mask = (
            pseudo_background.copy()
            if target_is_background
            else (
                (pixels[:, :, 3] > 0)
                & np.all(pixels[:, :, :3] == target[:3], axis=2)
            )
        )

        window = self.window()
        tools = getattr(window, "tools", None)
        adjacent = bool(
            tools is None or tools.bucket_adjacent.isChecked()
        )
        close_gap = bool(
            tools is not None
            and tools.bucket_close_gap.isChecked()
            and adjacent
        )

        if adjacent:
            passable = target_mask
            if close_gap:
                radius = max(1, int(tools.bucket_gap_width.value()))
                boundary = ~target_mask
                expanded = boundary.copy()
                for _ in range(radius):
                    source = expanded.copy()
                    expanded[1:, :] |= source[:-1, :]
                    expanded[:-1, :] |= source[1:, :]
                    expanded[:, 1:] |= source[:, :-1]
                    expanded[:, :-1] |= source[:, 1:]
                    expanded[1:, 1:] |= source[:-1, :-1]
                    expanded[:-1, :-1] |= source[1:, 1:]
                    expanded[1:, :-1] |= source[:-1, 1:]
                    expanded[:-1, 1:] |= source[1:, :-1]
                virtual_boundary = expanded
                for _ in range(radius):
                    padded = np.pad(
                        virtual_boundary,
                        ((1, 1), (1, 1)),
                        mode="constant",
                        constant_values=False,
                    )
                    virtual_boundary = (
                        padded[0:-2, 0:-2]
                        & padded[0:-2, 1:-1]
                        & padded[0:-2, 2:]
                        & padded[1:-1, 0:-2]
                        & padded[1:-1, 1:-1]
                        & padded[1:-1, 2:]
                        & padded[2:, 0:-2]
                        & padded[2:, 1:-1]
                        & padded[2:, 2:]
                    )
                passable = target_mask & ~virtual_boundary
                passable[start_y, start_x] = bool(
                    target_mask[start_y, start_x]
                )
            region = self._scanline_connected_region(
                passable, (start_x, start_y)
            )
            require_closed = bool(
                tools is not None
                and tools.bucket_require_closed.isChecked()
            )
            if require_closed and (
                np.any(region[0, :]) or np.any(region[-1, :])
                or np.any(region[:, 0]) or np.any(region[:, -1])
            ):
                self.status_message.emit(
                    "領域がキャンバス端まで開いているため選択しませんでした。"
                )
                return False
        else:
            region = target_mask.copy()

        current = self.selection_mask_bool(width, height)
        if current is None:
            current = np.zeros((height, width), dtype=bool)
        if modifiers & Qt.KeyboardModifier.AltModifier:
            combined = current & ~region
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            combined = current | region
        else:
            combined = region

        if not np.any(combined):
            self.clear_selection()
            return True

        contour_owner = self.window()
        contours = contour_owner._mask_contours(combined)
        contour = contour_owner._largest_contour(contours)
        if not contour:
            return False
        ys, xs = np.nonzero(combined)
        self.selection_polygon = contour
        self.selection_mask_override = combined
        self.selection_outline_polygons = contours
        self.selection_mask_rect = QRectF(
            int(xs.min()), int(ys.min()),
            int(xs.max() - xs.min() + 1),
            int(ys.max() - ys.min() + 1),
        )
        self._selection_fade_started = time.monotonic()
        self.lasso = []
        self.rect_start = None
        self.rect_end = None
        self.selectionChanged.emit()
        self.update()
        return True
    def init_mesh(self):
        w,h=workspace_size(); self.mesh_original=self.active_layer.image.copy(); self.mesh_points=[]
        for gy in range(self.mesh_grid):
            for gx in range(self.mesh_grid): self.mesh_points.append(QPointF(gx*w/(self.mesh_grid-1),gy*h/(self.mesh_grid-1)))
    def nearest_mesh(self,p):
        if not self.mesh_points:return -1
        d=[(q.x()-p.x())**2+(q.y()-p.y())**2 for q in self.mesh_points]; i=int(np.argmin(d)); return i if d[i]<(40/max(self.zoom,.01))**2 else -1
    def commit_mesh(self):
        if self.mesh_original is None or not self.mesh_points:return
        self.apply_mesh_release()

    def apply_mesh_release(self):
        if self.mesh_original is None:return
        img=self.mesh_original.convertToFormat(QImage.Format.Format_RGBA8888); w,h=img.width(),img.height()
        ptr=img.bits(); arr=np.frombuffer(ptr,dtype=np.uint8,count=w*h*4).reshape((h,w,4)).copy()
        yy,xx=np.mgrid[0:h,0:w].astype(np.float32); dx=np.zeros_like(xx);dy=np.zeros_like(yy);ws=np.zeros_like(xx)
        originals=[]
        for gy in range(self.mesh_grid):
            for gx in range(self.mesh_grid): originals.append(QPointF(gx*w/(self.mesh_grid-1),gy*h/(self.mesh_grid-1)))
        radius=max(w,h)/2.0
        for o,n in zip(originals,self.mesh_points):
            dist2=(xx-o.x())**2+(yy-o.y())**2; weight=np.exp(-dist2/(2*(radius/2.5)**2)); dx+=(n.x()-o.x())*weight;dy+=(n.y()-o.y())*weight;ws+=weight
        sx=np.clip(np.rint(xx-dx/np.maximum(ws,.001)),0,w-1).astype(np.int32); sy=np.clip(np.rint(yy-dy/np.maximum(ws,.001)),0,h-1).astype(np.int32)
        out=arr[sy,sx]; q=QImage(out.data,w,h,out.strides[0],QImage.Format.Format_RGBA8888).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self.active_layer.image=q; self.active_layer.has_content=True; self.mesh_original=None; self.mesh_points=[]; self.cellChanged.emit(self.current_frame,self.active_layer_index); self.update()
    def cancel_mesh(self):
        self.mesh_original=None; self.mesh_points=[]; self.mesh_active=-1; self.update()

    def flip_active_layer(self, horizontal=True):
        if self.active_layer.is_paper:
            return
        self.ensure_editable_key()
        self.push_layer_undo()
        self.active_layer.image = self.active_layer.image.mirrored(horizontal, not horizontal)
        self.active_layer.has_content = True
        self.cellChanged.emit(self.current_frame, self.active_layer_index)
        self.update()

    def move_timeline_cell(self, source_frame, source_layer, destination_frame, destination_layer):
        source_frame = int(source_frame)
        destination_frame = int(destination_frame)
        source_layer = int(source_layer)
        destination_layer = int(destination_layer)
        if not (0 <= source_frame < len(self.frames)) or destination_frame < 0:
            return False
        if not (0 <= source_layer < len(self.frames[source_frame].layers)):
            return False
        source = self.frames[source_frame].layers[source_layer]
        source_is_blank = bool(
            getattr(source, "is_blank_key", False)
        )
        if not (source.has_content or source_is_blank):
            return False

        self.push_doc_undo()
        moving = source.clone()
        moving.exposure = max(1, int(source.exposure))

        # 同一レイヤー内で後方へ移動するときは、元セルを抜いた分だけ座標を補正。
        source_span = max(1, int(source.exposure))
        self._clear_timeline_layer_cell(source)

        # 隣接する「●ーー」の直後のキーを移動した場合、抜けた位置は
        # 空セルにせず直前キーの保持区間（ー）として埋める。
        previous_key = None
        for index in range(source_frame - 1, -1, -1):
            candidate = self.frames[index].layers[source_layer]
            if (
                candidate.has_content
                or getattr(candidate, "is_blank_key", False)
            ):
                previous_key = index
                break
        if previous_key is not None:
            previous = self.frames[previous_key].layers[source_layer]
            previous_end = previous_key + max(1, int(previous.exposure))
            if previous_end >= source_frame:
                fill_until = source_frame + source_span
                if (
                    source_layer == destination_layer
                    and destination_frame > source_frame
                ):
                    # キーを右へずらした距離全体を直前キーの保持で埋める。
                    # 移動元の1コマ分だけ延長すると途中が未使用になる。
                    fill_until = max(fill_until, destination_frame)
                previous.exposure = max(
                    int(previous.exposure),
                    fill_until - previous_key,
                )

        self._ensure_frame_count(destination_frame + 1)
        if not (0 <= destination_layer < len(self.frames[destination_frame].layers)):
            return False

        occupying_block = self.timeline_block_at(
            destination_frame,
            destination_layer,
        )
        if occupying_block is not None:
            _occupying_kind, key, _occupying_exposure = (
                occupying_block
            )
            if int(key) != source_frame:
                occupied = self.frames[key].layers[
                    destination_layer
                ]
                if key < destination_frame:
                    # 保持区間上へ落とした場合は落下位置で切る。
                    occupied.exposure = max(
                        1,
                        destination_frame - key,
                    )
                elif key == destination_frame:
                    # 既存の●／○以降を右へ1セル送る。
                    keys = [
                        index
                        for index in range(len(self.frames))
                        if (
                            (
                                self.frames[index]
                                .layers[destination_layer]
                                .has_content
                            )
                            or getattr(
                                self.frames[index]
                                .layers[destination_layer],
                                "is_blank_key",
                                False,
                            )
                        )
                        and index >= destination_frame
                    ]
                    self._ensure_frame_count(
                        len(self.frames) + 1
                    )
                    for index in reversed(keys):
                        target = index + 1
                        self._ensure_frame_count(target + 1)
                        src = self.frames[index].layers[
                            destination_layer
                        ]
                        dst = self.frames[target].layers[
                            destination_layer
                        ]
                        copied = src.clone()
                        dst.image = copied.image.copy()
                        dst.has_content = bool(
                            copied.has_content
                        )
                        dst.is_blank_key = bool(
                            getattr(
                                copied,
                                "is_blank_key",
                                False,
                            )
                        )
                        dst.exposure = int(copied.exposure)
                        dst.visible = bool(copied.visible)
                        dst.opacity = float(copied.opacity)
                        dst.alpha_locked = bool(
                            copied.alpha_locked
                        )
                        dst.color_filter_enabled = bool(
                            copied.color_filter_enabled
                        )
                        dst.color_filter_rgb = (
                            tuple(copied.color_filter_rgb)
                            if copied.color_filter_rgb is not None
                            else None
                        )
                        dst.sequence_number = copied.sequence_number
                        dst.sequence_only = bool(copied.sequence_only)
                        self._clear_timeline_layer_cell(src)

        destination = self.frames[destination_frame].layers[destination_layer]
        destination.image = (
            moving.image.copy()
            if not source_is_blank
            else blank_image()
        )
        destination.has_content = not source_is_blank
        destination.is_blank_key = source_is_blank
        destination.sequence_number = (
            None if source_is_blank else moving.sequence_number
        )
        destination.sequence_only = bool(moving.sequence_only)
        destination.exposure = moving.exposure
        destination.visible = moving.visible
        destination.opacity = moving.opacity
        destination.color_filter_enabled = moving.color_filter_enabled
        destination.color_filter_rgb = (
            tuple(moving.color_filter_rgb)
            if moving.color_filter_rgb is not None else None
        )
        self.current_frame = destination_frame
        self.active_layer_index = destination_layer
        self._onion_cache.clear()
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        return True

    def _read_image_file(self, path):
        path = str(path)
        image = QImage()
        errors = []
        reader = QImageReader(path)
        reader.setAutoTransform(False)
        try:
            reader.setDecideFormatFromContent(True)
        except AttributeError:
            pass
        declared_size = reader.size()
        if declared_size.isValid():
            declared_width = int(declared_size.width())
            declared_height = int(declared_size.height())
            if (
                declared_width < 1
                or declared_height < 1
                or declared_width > MAX_IMAGE_DIMENSION
                or declared_height > MAX_IMAGE_DIMENSION
                or declared_width * declared_height > MAX_SINGLE_IMAGE_PIXELS
            ):
                return None, (
                    "画像サイズが上限を超えています。\n"
                    f"{declared_width} × {declared_height}px"
                )
        image = reader.read()
        if image.isNull():
            errors.append("Qt: " + (reader.errorString() or "画像データを解釈できませんでした"))
        if image.isNull() and PILImage is not None:
            try:
                with PILImage.open(path) as pil:
                    pil_width, pil_height = map(int, pil.size)
                    if (
                        pil_width < 1
                        or pil_height < 1
                        or pil_width > MAX_IMAGE_DIMENSION
                        or pil_height > MAX_IMAGE_DIMENSION
                        or pil_width * pil_height > MAX_SINGLE_IMAGE_PIXELS
                    ):
                        raise ValueError(
                            "画像サイズが上限を超えています。"
                            f" ({pil_width} × {pil_height}px)"
                        )
                    pil.load()
                    pil = pil.convert("RGBA")
                    raw = pil.tobytes("raw", "RGBA")
                    converted = QImage(raw, pil.width, pil.height, pil.width * 4, QImage.Format.Format_RGBA8888)
                    image = converted.copy()
            except (OSError, ValueError, TypeError, MemoryError, RuntimeError) as exc:
                # Pillow raises a wide, loosely-documented set on undecodable
                # or oversized images; record it and fall back to the Qt reader.
                log.info("Pillow decode of %s failed: %s", path, exc)
                errors.append(f"Pillow: {exc}")
        if image.isNull():
            suffix = Path(path).suffix.lower() or "拡張子なし"
            return None, f"形式: {suffix}\n" + "\n".join(errors)
        return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied), ""

    @staticmethod
    def _natural_path_key(*args, **kwargs):
        return natural_path_key(*args, **kwargs)

    def _ensure_frame_count(self, count):
        if count <= len(self.frames):
            return
        names = [layer.name for layer in self.frames[0].layers]
        template = self.frames[0].layers
        while len(self.frames) < count:
            frame = make_frame(names)
            for index, layer in enumerate(frame.layers):
                layer.visible = template[index].visible
                layer.opacity = template[index].opacity
                layer.alpha_locked = template[index].alpha_locked
            self.frames.append(frame)

    def _place_imported_image(self, image, target):
        x = OUTSIDE_MARGIN + (constants.CANVAS_WIDTH - image.width()) // 2
        y = OUTSIDE_MARGIN + (constants.CANVAS_HEIGHT - image.height()) // 2
        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(QPoint(x, y), image)
        painter.end()

    def _make_white_transparent(self, image):
        """Convert exact #FFFFFF pixels to fully transparent before placement."""
        if image is None or image.isNull():
            return image
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = rgba.width(), rgba.height()
        if width <= 0 or height <= 0:
            return rgba

        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, rgba.bytesPerLine())
        )
        pixels = rows[:, :width * 4].reshape((height, width, 4))
        white = (
            (pixels[:, :, 0] == 255)
            & (pixels[:, :, 1] == 255)
            & (pixels[:, :, 2] == 255)
            & (pixels[:, :, 3] != 0)
        )
        pixels[:, :, 3][white] = 0
        return rgba.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    @classmethod
    def image_alpha_statistics(cls, *args, **kwargs):
        return color_ops.image_alpha_statistics(*args, **kwargs)

    @classmethod
    def binarize_alpha_for_pixel_art(cls, *args, **kwargs):
        return color_ops.binarize_alpha_for_pixel_art(*args, **kwargs)

    @classmethod
    def opaque_rgb_color_count(cls, *args, **kwargs):
        return color_ops.opaque_rgb_color_count(*args, **kwargs)

    @classmethod
    def detect_opaque_border_background(cls, *args, **kwargs):
        return color_ops.detect_opaque_border_background(*args, **kwargs)

    @staticmethod
    def _local_color_variation(*args, **kwargs):
        return colors.local_color_variation(*args, **kwargs)

    @classmethod
    def estimate_mixed_boundary_pixels(cls, *args, **kwargs):
        return color_ops.estimate_mixed_boundary_pixels(*args, **kwargs)

    @staticmethod
    def _median_cut_palette_from_samples(*args, **kwargs):
        return colors.median_cut_palette_from_samples(*args, **kwargs)

    @classmethod
    def _surface_guided_palette_labels(cls, *args, **kwargs):
        return color_ops._surface_guided_palette_labels(*args, **kwargs)

    @staticmethod
    def _line_palette_labels(*args, **kwargs):
        return color_ops._line_palette_labels(*args, **kwargs)


    @staticmethod
    def normalize_tone_curve_points(*args, **kwargs):
        return colors.normalize_tone_curve_points(*args, **kwargs)

    @classmethod
    def tone_curve_samples(cls, *args, **kwargs):
        return color_ops.tone_curve_samples(*args, **kwargs)

    @classmethod
    def tone_curve_lut(cls, *args, **kwargs):
        return color_ops.tone_curve_lut(*args, **kwargs)

    @classmethod
    def apply_tone_curve(cls, *args, **kwargs):
        return color_ops.apply_tone_curve(*args, **kwargs)

    @staticmethod
    def _rgb_hsv_features(*args, **kwargs):
        return colors.rgb_hsv_features(*args, **kwargs)

    @classmethod
    def _hue_cluster_palette_from_samples(cls, *args, **kwargs):
        return color_ops._hue_cluster_palette_from_samples(*args, **kwargs)

    @staticmethod
    def _priority_palette_colors_from_samples(*args, **kwargs):
        return colors.priority_palette_colors_from_samples(*args, **kwargs)


    @classmethod
    def build_color_reduction_palette(cls, *args, **kwargs):
        return color_ops.build_color_reduction_palette(*args, **kwargs)



    @classmethod
    def apply_color_reduction_palette(cls, *args, **kwargs):
        return color_ops.apply_color_reduction_palette(*args, **kwargs)



    def import_image(self, path, color_reduction=None):
        return self.import_image_sequence(
            [path],
            color_reduction=color_reduction,
        )

    def import_image_sequence(
        self,
        paths,
        progress_callback=None,
        color_reduction=None,
        layer_name=None,
    ):
        paths = sorted([str(path) for path in paths], key=self._natural_path_key)
        if not paths:
            return False, "画像ファイルがありません。"
        decoded = []
        for index, path in enumerate(paths, 1):
            if progress_callback:
                progress_callback(index - 1, len(paths), f"{Path(path).name} を読み込んでいます")
            image, error = self._read_image_file(path)
            if image is None:
                return False, f"{Path(path).name}\n{error}"
            if color_reduction:
                palette = color_reduction.get("palette")
                extraction_mode = color_reduction.get(
                    "extraction_mode",
                    "surface",
                )
                line_extraction = extraction_mode == "line"
                reduction_background = (
                    (255, 255, 255)
                    if line_extraction
                    else color_reduction.get("background_rgb")
                )
                alpha_threshold = int(
                    color_reduction.get(
                        "alpha_threshold",
                        128,
                    )
                )
                tone_curve_points = color_reduction.get(
                    "tone_curve_points",
                    [(0.0, 0.0), (1.0, 1.0)],
                )
                if tone_curve_points:
                    image = self.apply_tone_curve(
                        image,
                        tone_curve_points=tone_curve_points,
                        background_rgb=reduction_background,
                    )
                if palette is not None:
                    if progress_callback:
                        progress_callback(
                            index - 1,
                            len(paths),
                            f"{Path(path).name} の元画像へトーンカーブを適用し、"
                            "共通パレットで2値化しています",
                        )
                    image = self.apply_color_reduction_palette(
                        image,
                        palette,
                        alpha_threshold=alpha_threshold,
                        opaque_background=(
                            True
                            if line_extraction
                            else bool(
                                color_reduction.get(
                                    "opaque_background",
                                    False,
                                )
                            )
                        ),
                        background_rgb=reduction_background,
                        tone_curve_points=[
                            (0.0, 0.0),
                            (1.0, 1.0),
                        ],
                        extraction_mode=extraction_mode,
                    )
            else:
                # JPEGにはアルファチャンネルがないため、白を透明化すると
                # 圧縮後に白となった画素まで欠けて見える。デコード済みの
                # RGBをそのまま保持し、PNG／TGAの既存透明化だけを維持する。
                if Path(path).suffix.lower() not in (".jpg", ".jpeg"):
                    image = self._make_white_transparent(image)
            decoded.append((path, image))
        if progress_callback:
            progress_callback(len(paths), len(paths), "画像の配置を準備しています")
        self.push_doc_undo()
        start_frame = self.current_frame
        self._ensure_frame_count(start_frame + len(decoded))
        name = str(layer_name).strip() if layer_name is not None else ""
        if not name:
            name = Path(decoded[0][0]).stem or f"Image {len(self.layers)}"
        insert_index = len(self.frames[0].layers)
        for frame in self.frames:
            frame.layers.append(Layer(name, blank_image()))
        for offset, (_, image) in enumerate(decoded):
            if progress_callback:
                progress_callback(offset, len(decoded), f"{offset + 1} / {len(decoded)} 枚を配置しています")
            layer = self.frames[start_frame + offset].layers[insert_index]
            self._place_imported_image(image, layer.image)
            layer.has_content = True
            layer.exposure = 1
            layer.sequence_number = offset + 1
        self._sequence_source_bank = [
            self.frames[start_frame + offset]
            .layers[insert_index].image.copy()
            for offset in range(len(decoded))
        ]
        self._sequence_source_bank_layer_index = int(insert_index)
        self._sequence_source_bank_layer_name = str(name)
        self.active_layer_index = insert_index
        self.current_frame = start_frame
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()
        if progress_callback:
            progress_callback(len(decoded), len(decoded), "読み込み完了")
        return True, ""

    def dragEnterEvent(self,e):
        urls=e.mimeData().urls() if e.mimeData().hasUrls() else []
        valid=any(Path(u.toLocalFile()).suffix.lower() in (".pman",".xdts",".xtds",".jpg",".jpeg",".png",".tga") for u in urls)
        if valid:e.acceptProposedAction()
        else:e.ignore()

    def dragMoveEvent(self,e):
        e.acceptProposedAction()

    def dropEvent(self,e):
        project_paths=[]
        remap_paths=[]
        paths=[]
        for u in e.mimeData().urls():
            path=u.toLocalFile()
            suffix=Path(path).suffix.lower()
            if suffix==".pman":
                project_paths.append(path)
            elif suffix in (".xdts", ".xtds"):
                remap_paths.append(path)
            elif suffix in (".jpg",".jpeg",".png",".tga"):
                paths.append(path)
        if project_paths:
            self.projectDropped.emit(project_paths[0])
        elif remap_paths:
            self.timeRemapDropped.emit(remap_paths[0])
        elif paths:
            self.imagesDropped.emit(paths)
        e.acceptProposedAction()

    def selection_mask_bool(self, width=None, height=None, offset_x=0, offset_y=0):
        """Return a boolean mask for the active selection in the requested local area."""
        if not self.selection_polygon:
            return None
        width = int(width if width is not None else self.active_layer.image.width())
        height = int(height if height is not None else self.active_layer.image.height())
        if width <= 0 or height <= 0:
            return np.zeros((max(0, height), max(0, width)), dtype=bool)
        if self.selection_mask_override is not None:
            source = np.asarray(self.selection_mask_override, dtype=bool)
            result = np.zeros((height, width), dtype=bool)
            source_x1 = max(0, int(offset_x))
            source_y1 = max(0, int(offset_y))
            source_x2 = min(source.shape[1], int(offset_x) + width)
            source_y2 = min(source.shape[0], int(offset_y) + height)
            if source_x2 > source_x1 and source_y2 > source_y1:
                target_x1 = source_x1 - int(offset_x)
                target_y1 = source_y1 - int(offset_y)
                result[
                    target_y1:target_y1 + source_y2 - source_y1,
                    target_x1:target_x1 + source_x2 - source_x1,
                ] = source[source_y1:source_y2, source_x1:source_x2]
            return result
        mask = QImage(width, height, QImage.Format.Format_RGBA8888)
        mask.fill(Qt.GlobalColor.transparent)
        local_polygon = QPolygonF([
            QPointF(point.x() - offset_x, point.y() - offset_y)
            for point in self.selection_polygon
        ])
        painter = QPainter(mask)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 255))
        painter.drawPolygon(local_polygon)
        painter.end()
        ptr = mask.bits()
        try:
            ptr.setsize(mask.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, mask.bytesPerLine())
        )
        return rows[:, :width * 4].reshape((height, width, 4))[:, :, 3].copy() > 0

    @staticmethod
    def _bool_mask_image(*args, **kwargs):
        return imaging.bool_mask_image(*args, **kwargs)

    def _update_selection_fade(self):
        phase = (
            (time.monotonic() - self._selection_fade_started) % 2.0
        ) / 2.0
        self._selection_fade_opacity = (
            0.08 + 0.92 * (0.5 + 0.5 * math.cos(phase * math.tau))
        )
        if self.selection_polygon:
            if self.transform_active and not self.transform_outer_polygon().isEmpty():
                selection_rect = self.transform_outer_polygon().boundingRect()
            else:
                selection_rect = (
                    self.selection_mask_rect
                    if self.selection_mask_rect is not None
                    else QRectF(self.selection_bounds())
                )
            corners = [
                self.canvas_to_widget(selection_rect.topLeft()),
                self.canvas_to_widget(selection_rect.topRight()),
                self.canvas_to_widget(selection_rect.bottomRight()),
                self.canvas_to_widget(selection_rect.bottomLeft()),
            ]
            xs = [point.x() for point in corners]
            ys = [point.y() for point in corners]
            dirty = QRectF(
                min(xs), min(ys),
                max(xs) - min(xs), max(ys) - min(ys),
            ).adjusted(-4, -4, 4, 4).toAlignedRect()
            region = QRegion(dirty.intersected(self.rect()))
            if self._brush_cursor_inside:
                cursor_radius = max(
                    10,
                    int(math.ceil(
                        float(self.pen_size) * max(self.zoom, 0.01) / 2.0
                    )) + 6,
                )
                cursor_rect = QRectF(
                    self._brush_cursor_widget_pos,
                    self._brush_cursor_widget_pos,
                ).adjusted(
                    -cursor_radius, -cursor_radius,
                    cursor_radius, cursor_radius,
                ).toAlignedRect()
                region -= QRegion(cursor_rect)
            if not region.isEmpty():
                self.update(region)

    def _clear_selection_state(self):
        if self.transform_active:
            self.cancel_selection_transform()
        self.selection_polygon = []
        self.selection_mask_override = None
        self.selection_outline_polygons = []
        self.selection_mask_rect = None
        self.lasso = []
        self.rect_start = None
        self.rect_end = None
        self.selectionChanged.emit()
        self.update()

    def clear_selection(self):
        self._clear_selection_state()
        self.selectionCleared.emit()

    def clear_selection_preserving_used_colors(self):
        """内部処理用。使用色の選択を維持したまま画像選択だけ解除する。"""
        self._clear_selection_state()

    def _masked_selection_source(self, rect):
        source = self.active_layer.image.copy(rect)
        if not self.selection_polygon:
            return source
        if self.selection_mask_override is not None:
            mask = self._bool_mask_image(self.selection_mask_bool(
                rect.width(), rect.height(), rect.x(), rect.y()
            ))
            painter = QPainter(source)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_DestinationIn
            )
            painter.drawImage(0, 0, mask)
            painter.end()
            return source
        mask = QImage(rect.width(), rect.height(), QImage.Format.Format_ARGB32_Premultiplied)
        mask.fill(Qt.GlobalColor.transparent)
        painter = QPainter(mask)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 255))
        painter.drawPolygon(QPolygonF([
            QPointF(point.x() - rect.x(), point.y() - rect.y())
            for point in self.selection_polygon
        ]))
        painter.end()
        painter = QPainter(source)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, mask)
        painter.end()
        return source

    @staticmethod
    def _regular_grid_points(rect, cols, rows=None):
        return geometry.regular_grid_points(rect, cols, rows)

    def _regular_mesh_reference_points(self, rect, cols, rows):
        cols = max(2, int(cols))
        rows = max(2, int(rows))
        source = getattr(self, "transform_source", None)

        if source is not None and not source.isNull():
            pixel_width = max(0.0, float(source.width() - 1))
            pixel_height = max(0.0, float(source.height() - 1))
        else:
            pixel_width = max(0.0, float(rect.width()) - 1.0)
            pixel_height = max(0.0, float(rect.height()) - 1.0)

        return [
            QPointF(
                float(rect.left())
                + pixel_width * gx / float(cols - 1),
                float(rect.top())
                + pixel_height * gy / float(rows - 1),
            )
            for gy in range(rows)
            for gx in range(cols)
        ]

    def auto_select_used_area(self):
        """Select the bounding area of every opaque pixel, including gray colors."""
        if not self.frames or not self.layers:
            return False
        self.ensure_editable_key()
        image = self.active_layer.image.convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        width, height = image.width(), image.height()
        if width <= 0 or height <= 0:
            return False
        ptr = image.bits()
        try:
            ptr.setsize(image.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, image.bytesPerLine())
        )
        pixels = rows[:, :width * 4].reshape((height, width, 4))
        used = pixels[:, :, 3] > 0
        ys, xs = np.nonzero(used)
        if len(xs) == 0:
            return False
        left = int(xs.min())
        right = int(xs.max()) + 1
        top = int(ys.min())
        bottom = int(ys.max()) + 1
        self.selection_polygon = [
            QPointF(left, top),
            QPointF(right, top),
            QPointF(right, bottom),
            QPointF(left, bottom),
        ]
        self.selectionChanged.emit()
        self.update()
        return True

    def begin_selection_transform(self, mode):
        if not self.selection_polygon:
            return False
        selection_rect = self.selection_bounds().intersected(
            self.active_layer.image.rect()
        )
        # 選択境界ぴったりで切り出すと、縮小時のサンプリングで
        # 外周画素が透明側へ丸められて欠ける。変形元だけに透明な
        # 安全余白を持たせ、選択マスク自体の形状は変更しない。
        rect = selection_rect.adjusted(-2, -2, 2, 2).intersected(
            self.active_layer.image.rect()
        )
        if rect.isEmpty():
            return False
        if self.transform_active:
            self.cancel_selection_transform()

        mode = mode if mode in ("scale", "free", "mesh") else "free"
        self.transform_active = True
        self.transform_mode = mode
        self.transform_quality_active = bool(getattr(self, "transform_quality", False))
        self.transform_tp_line_colors = tuple(sorted(self.selected_used_colors()))
        if self.transform_quality_active:
            self.transform_apply_all_frames = False
        self._invalidate_tp_preview_cache()
        self.transform_frame_index = self.current_frame
        self.transform_layer_index = self.active_layer_index
        self.transform_original_layer = self.active_layer.image.copy()
        self.transform_original_has_content = self.active_layer.has_content
        self.transform_source_rect = QRectF(rect)
        self.transform_source = self._masked_selection_source(rect)
        if self.transform_quality_active:
            target_width = self.active_layer.image.width()
            target_height = self.active_layer.image.height()
            use_proxy = self._tp_uses_proxy(target_width, target_height)
            if PILImage is None or PILImageFilter is None:
                # Keep the transform usable even when Pillow is unavailable,
                # but do not pretend that the TP_mask method is active.
                self.transform_quality_active = False
                self.status_message.emit(
                    "クオリティ変形には Pillow が必要です。通常変形へ切り替えました。"
                )
            elif use_proxy:
                self._clear_tp_transform_masks(clear_proxy=False)
                self.status_message.emit(
                    "5000px以上の画像は軽量TpMaskプレビューで表示し、"
                    "確定時に原寸で処理します。"
                )
            elif not self._prepare_tp_transform_masks():
                self.transform_quality_active = False
                self.status_message.emit(
                    "TpMaskを準備できなかったため、通常変形へ切り替えました。"
                )
        self.transform_drag_kind = None
        self.transform_drag_start = QPointF()
        self.transform_drag_points = []

        painter = QPainter(self.active_layer.image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 255))
        if self.selection_mask_override is not None:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_DestinationOut
            )
            local_mask = self.selection_mask_bool(
                rect.width(), rect.height(), rect.x(), rect.y()
            )
            painter.drawImage(
                rect.topLeft(), self._bool_mask_image(local_mask)
            )
        else:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
            )
            painter.drawPolygon(QPolygonF(self.selection_polygon))
        painter.end()

        if mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
            self.transform_mesh_cols = cols
            self.transform_mesh_rows = rows
            self.transform_mesh_grid = cols
            self.transform_points = (
                self._regular_mesh_reference_points(
                    QRectF(rect),
                    cols,
                    rows,
                )
            )
            self.transform_mesh_reference_points = [
                QPointF(point) for point in self.transform_points
            ]
            self._active_mesh_cols = cols
            self._active_mesh_rows = rows
        else:
            self.transform_mesh_reference_points = []
            left, top = float(rect.left()), float(rect.top())
            right, bottom = float(rect.right() + 1), float(rect.bottom() + 1)
            self.transform_points = [
                QPointF(left, top), QPointF(right, top),
                QPointF(right, bottom), QPointF(left, bottom),
            ]
        self.transform_handle = -1
        if self.transform_quality_active:
            self.request_quality_preview_counter(
                "最初のクオリティプレビューを生成しています"
            )
        self.update()
        return True

    def set_transform_mesh_grid(self, cols, rows=None):
        cols = max(2, min(12, int(cols)))
        rows = max(2, min(12, int(rows if rows is not None else cols)))
        self.transform_mesh_cols = cols
        self.transform_mesh_rows = rows
        self.transform_mesh_grid = cols
        if not self.transform_active or self.transform_mode != "mesh":
            return

        old_points = list(self.transform_points)
        old_cols = max(2, int(getattr(self, "_active_mesh_cols", 0) or 0))
        old_rows = max(2, int(getattr(self, "_active_mesh_rows", 0) or 0))
        if old_cols * old_rows != len(old_points):
            old_cols = max(2, int(round(math.sqrt(len(old_points))))) if old_points else 0
            old_rows = old_cols if old_cols and old_cols * old_cols == len(old_points) else 0

        if old_cols < 2 or old_rows < 2 or old_cols * old_rows != len(old_points):
            self.transform_points = self._regular_grid_points(
                self.transform_source_rect, cols, rows
            )
        else:
            def sample(u, v):
                x = max(0.0, min(old_cols - 1.0, u * (old_cols - 1)))
                y = max(0.0, min(old_rows - 1.0, v * (old_rows - 1)))
                x0, y0 = int(math.floor(x)), int(math.floor(y))
                x1, y1 = min(old_cols - 1, x0 + 1), min(old_rows - 1, y0 + 1)
                tx, ty = x - x0, y - y0
                p00 = old_points[y0 * old_cols + x0]
                p10 = old_points[y0 * old_cols + x1]
                p01 = old_points[y1 * old_cols + x0]
                p11 = old_points[y1 * old_cols + x1]
                return QPointF(
                    (1 - ty) * ((1 - tx) * p00.x() + tx * p10.x())
                    + ty * ((1 - tx) * p01.x() + tx * p11.x()),
                    (1 - ty) * ((1 - tx) * p00.y() + tx * p10.y())
                    + ty * ((1 - tx) * p01.y() + tx * p11.y()),
                )

            self.transform_points = [
                sample(gx / (cols - 1), gy / (rows - 1))
                for gy in range(rows)
                for gx in range(cols)
            ]

        self.transform_mesh_reference_points = (
            self._regular_mesh_reference_points(
                self.transform_source_rect,
                cols,
                rows,
            )
        )
        self._active_mesh_cols = cols
        self._active_mesh_rows = rows
        self.transform_handle = -1
        self._invalidate_tp_preview_cache()
        if self.transform_quality_active:
            self.request_quality_preview_counter(
                "メッシュ格子のプレビューを生成しています"
            )
        self.update()

    def nearest_transform_handle(self, point):
        if not self.transform_active or not self.transform_points:
            return -1
        radius = 14 / max(self.zoom, 0.01)
        distances = [
            (handle.x() - point.x()) ** 2 + (handle.y() - point.y()) ** 2
            for handle in self.transform_points
        ]
        index = int(np.argmin(distances))
        return index if distances[index] <= radius * radius else -1

    def transform_center(self):
        if not self.transform_points:
            return QPointF()
        return QPointF(
            sum(point.x() for point in self.transform_points) / len(self.transform_points),
            sum(point.y() for point in self.transform_points) / len(self.transform_points),
        )

    def transform_rotation_handle(self):
        if not self.transform_points:
            return None
        if self.transform_mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            if len(self.transform_points) < cols:
                return None
            p0, p1 = self.transform_points[0], self.transform_points[cols - 1]
        elif len(self.transform_points) == 4:
            p0, p1 = self.transform_points[0], self.transform_points[1]
        else:
            return None
        midpoint = QPointF((p0.x() + p1.x()) / 2.0, (p0.y() + p1.y()) / 2.0)
        center = self.transform_center()
        dx, dy = midpoint.x() - center.x(), midpoint.y() - center.y()
        length = math.hypot(dx, dy) or 1.0
        distance = 36.0 / max(self.zoom, 0.01)
        return QPointF(
            midpoint.x() + dx / length * distance,
            midpoint.y() + dy / length * distance,
        )

    def transform_outer_polygon(self):
        if not self.transform_points:
            return QPolygonF()
        if self.transform_mode != "mesh":
            return QPolygonF(self.transform_points[:4])
        cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
        rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
        points = self.transform_points
        if len(points) != cols * rows:
            return QPolygonF()
        subdivisions = 8
        outline = []

        for step in range((cols - 1) * subdivisions + 1):
            outline.append(
                self._mesh_curve_point(
                    points, cols, rows,
                    step / float(subdivisions), 0.0,
                )
            )
        for step in range(1, (rows - 1) * subdivisions + 1):
            outline.append(
                self._mesh_curve_point(
                    points, cols, rows,
                    float(cols - 1),
                    step / float(subdivisions),
                )
            )
        for step in range(
            (cols - 1) * subdivisions - 1, -1, -1
        ):
            outline.append(
                self._mesh_curve_point(
                    points, cols, rows,
                    step / float(subdivisions),
                    float(rows - 1),
                )
            )
        for step in range(
            (rows - 1) * subdivisions - 1, 0, -1
        ):
            outline.append(
                self._mesh_curve_point(
                    points, cols, rows,
                    0.0,
                    step / float(subdivisions),
                )
            )
        return QPolygonF(outline)

    def transformed_selection_outline(self, outline):
        """選択輪郭を現在の変形プレビューと同じ座標へ写像する。"""
        if (
            not self.transform_active
            or self.transform_source_rect is None
            or not self.transform_points
        ):
            return [QPointF(point) for point in outline]
        rect = QRectF(self.transform_source_rect)
        width = max(1.0, float(rect.width()))
        height = max(1.0, float(rect.height()))
        if self.transform_mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
            if len(self.transform_points) != cols * rows:
                return [QPointF(point) for point in outline]
            return [
                self._mesh_curve_point(
                    self.transform_points,
                    cols,
                    rows,
                    (float(point.x()) - rect.left()) / width * (cols - 1),
                    (float(point.y()) - rect.top()) / height * (rows - 1),
                )
                for point in outline
            ]
        if len(self.transform_points) != 4:
            return [QPointF(point) for point in outline]
        transform = self._quad_homography(
            [
                QPointF(0, 0), QPointF(width, 0),
                QPointF(width, height), QPointF(0, height),
            ],
            self.transform_points,
        )
        return [
            transform.map(QPointF(
                float(point.x()) - rect.left(),
                float(point.y()) - rect.top(),
            ))
            for point in outline
        ]

    def begin_transform_drag(self, point):
        if not self.transform_active:
            return False
        rotation_handle = self.transform_rotation_handle()
        radius = 16 / max(self.zoom, 0.01)
        if rotation_handle is not None:
            if (
                (rotation_handle.x() - point.x()) ** 2
                + (rotation_handle.y() - point.y()) ** 2
                <= radius * radius
            ):
                self.transform_drag_kind = ("rotate", -1)
                self.transform_drag_start = QPointF(point)
                self.transform_drag_points = [QPointF(p) for p in self.transform_points]
                return True
        handle = self.nearest_transform_handle(point)
        if handle >= 0:
            self.transform_drag_kind = ("handle", handle)
            self.transform_drag_start = QPointF(point)
            self.transform_drag_points = [QPointF(p) for p in self.transform_points]
            self.transform_handle = handle
            return True
        if self.transform_outer_polygon().containsPoint(
            point, Qt.FillRule.OddEvenFill
        ):
            self.transform_drag_kind = ("move", -1)
            self.transform_drag_start = QPointF(point)
            self.transform_drag_points = [QPointF(p) for p in self.transform_points]
            return True
        self.transform_drag_kind = None
        return False

    def update_transform_drag(self, point):
        if not self.transform_drag_kind:
            return
        kind, index = self.transform_drag_kind
        start_points = self.transform_drag_points
        if kind == "move":
            delta = point - self.transform_drag_start
            self.transform_points = [p + delta for p in start_points]
        elif kind == "rotate":
            center = QPointF(
                sum(p.x() for p in start_points) / len(start_points),
                sum(p.y() for p in start_points) / len(start_points),
            )
            a0 = math.atan2(
                self.transform_drag_start.y() - center.y(),
                self.transform_drag_start.x() - center.x(),
            )
            a1 = math.atan2(point.y() - center.y(), point.x() - center.x())
            angle = a1 - a0
            cs, sn = math.cos(angle), math.sin(angle)
            self.transform_points = [
                QPointF(
                    center.x() + (p.x() - center.x()) * cs - (p.y() - center.y()) * sn,
                    center.y() + (p.x() - center.x()) * sn + (p.y() - center.y()) * cs,
                )
                for p in start_points
            ]
        elif kind == "handle":
            if self.transform_mode in ("free", "mesh"):
                self.transform_points[index] = QPointF(point)
            else:
                opposite_index = (index + 2) % 4
                opposite = start_points[opposite_index]
                original = start_points[index] - opposite
                current = point - opposite
                denominator = original.x() ** 2 + original.y() ** 2
                scale = (
                    (current.x() * original.x() + current.y() * original.y())
                    / denominator
                    if denominator > 1e-8 else 1.0
                )
                if abs(scale) < 0.02:
                    scale = 0.02 if scale >= 0 else -0.02
                self.transform_points = [
                    opposite + (p - opposite) * scale
                    for p in start_points
                ]
        self._invalidate_tp_preview_cache()
        self.update()

    def end_transform_drag(self):
        self.transform_drag_kind = None
        self.transform_handle = -1
        self.transform_drag_points = []
        if self.transform_quality_active:
            self.request_quality_preview_counter(
                "変形後のクオリティプレビューを生成しています"
            )
        self.update()

    def rotate_selection_transform(self, degrees):
        if not self.transform_active or not self.transform_points:
            return
        center = self.transform_center()
        angle = math.radians(float(degrees))
        cs, sn = math.cos(angle), math.sin(angle)
        self.transform_points = [
            QPointF(
                center.x() + (p.x() - center.x()) * cs - (p.y() - center.y()) * sn,
                center.y() + (p.x() - center.x()) * sn + (p.y() - center.y()) * cs,
            )
            for p in self.transform_points
        ]
        self._invalidate_tp_preview_cache()
        if self.transform_quality_active:
            self.request_quality_preview_counter(
                "回転後のクオリティプレビューを生成しています"
            )
        self.update()

    @staticmethod
    def _quad_homography(source_points, target_points):
        return geometry.quad_homography(source_points, target_points)

    @staticmethod
    def _mesh_catmull_scalar(p0, p1, p2, p3, t):
        return geometry.mesh_catmull_scalar(p0, p1, p2, p3, t)

    def _mesh_reference_grid(self, cols, rows):
        reference = list(
            getattr(
                self,
                "transform_mesh_reference_points",
                [],
            )
        )
        if len(reference) == cols * rows:
            return [QPointF(point) for point in reference]

        rect = self.transform_source_rect
        if rect is None:
            return []
        return self._regular_mesh_reference_points(
            QRectF(rect),
            cols,
            rows,
        )

    def _mesh_curve_point(self, points, cols, rows, grid_x, grid_y):
        """基準格子からの変位だけを滑らかに補間する。"""
        if not points or cols < 2 or rows < 2:
            return QPointF()

        reference = self._mesh_reference_grid(cols, rows)
        if len(reference) != cols * rows:
            return QPointF()

        grid_x = max(
            0.0,
            min(float(cols - 1), float(grid_x)),
        )
        grid_y = max(
            0.0,
            min(float(rows - 1), float(grid_y)),
        )
        cell_x = min(cols - 2, int(math.floor(grid_x)))
        cell_y = min(rows - 2, int(math.floor(grid_y)))
        local_x = grid_x - cell_x
        local_y = grid_y - cell_y

        def clamped_index(row, col):
            row = max(0, min(rows - 1, int(row)))
            col = max(0, min(cols - 1, int(col)))
            return row * cols + col

        def displacement_at(row, col):
            index = clamped_index(row, col)
            return QPointF(
                points[index].x() - reference[index].x(),
                points[index].y() - reference[index].y(),
            )

        horizontal_displacements = []
        for row in range(cell_y - 1, cell_y + 3):
            p0 = displacement_at(row, cell_x - 1)
            p1 = displacement_at(row, cell_x)
            p2 = displacement_at(row, cell_x + 1)
            p3 = displacement_at(row, cell_x + 2)
            horizontal_displacements.append(QPointF(
                self._mesh_catmull_scalar(
                    p0.x(),
                    p1.x(),
                    p2.x(),
                    p3.x(),
                    local_x,
                ),
                self._mesh_catmull_scalar(
                    p0.y(),
                    p1.y(),
                    p2.y(),
                    p3.y(),
                    local_x,
                ),
            ))

        displacement = QPointF(
            self._mesh_catmull_scalar(
                horizontal_displacements[0].x(),
                horizontal_displacements[1].x(),
                horizontal_displacements[2].x(),
                horizontal_displacements[3].x(),
                local_y,
            ),
            self._mesh_catmull_scalar(
                horizontal_displacements[0].y(),
                horizontal_displacements[1].y(),
                horizontal_displacements[2].y(),
                horizontal_displacements[3].y(),
                local_y,
            ),
        )

        # 基準格子上の位置は双線形補間する。
        i00 = cell_y * cols + cell_x
        i10 = cell_y * cols + cell_x + 1
        i01 = (cell_y + 1) * cols + cell_x
        i11 = (cell_y + 1) * cols + cell_x + 1
        r00 = reference[i00]
        r10 = reference[i10]
        r01 = reference[i01]
        r11 = reference[i11]

        base = QPointF(
            (1.0 - local_y)
            * (
                (1.0 - local_x) * r00.x()
                + local_x * r10.x()
            )
            + local_y
            * (
                (1.0 - local_x) * r01.x()
                + local_x * r11.x()
            ),
            (1.0 - local_y)
            * (
                (1.0 - local_x) * r00.y()
                + local_x * r10.y()
            )
            + local_y
            * (
                (1.0 - local_x) * r01.y()
                + local_x * r11.y()
            ),
        )

        return QPointF(
            base.x() + displacement.x(),
            base.y() + displacement.y(),
        )

    def _curved_mesh_points(
        self, points, cols, rows, subdivisions=8
    ):
        """基準格子に滑らかな変位を加えた高密度メッシュを生成する。"""
        subdivisions = max(2, int(subdivisions))
        dense_cols = (cols - 1) * subdivisions + 1
        dense_rows = (rows - 1) * subdivisions + 1
        dense = []
        for dense_y in range(dense_rows):
            grid_y = dense_y / float(subdivisions)
            for dense_x in range(dense_cols):
                grid_x = dense_x / float(subdivisions)
                dense.append(
                    self._mesh_curve_point(
                        points,
                        cols,
                        rows,
                        grid_x,
                        grid_y,
                    )
                )
        return dense, dense_cols, dense_rows

    def _mesh_preview_image(
        self, source_image=None, target_width=None, target_height=None, smooth=False
    ):
        # 変形では補間色を生成しない。呼び出し側の指定に関係なく最近傍に固定する。
        smooth = False
        source_image = source_image if source_image is not None else self.transform_source
        if source_image is None:
            return None
        source = source_image.convertToFormat(QImage.Format.Format_RGBA8888)
        sw, sh = source.width(), source.height()
        cw = int(target_width if target_width is not None else self.active_layer.image.width())
        ch = int(target_height if target_height is not None else self.active_layer.image.height())
        if sw <= 0 or sh <= 0 or cw <= 0 or ch <= 0:
            return None
        sp = source.bits()
        try:
            sp.setsize(source.sizeInBytes())
        except AttributeError:
            pass
        source_rows = np.frombuffer(sp, dtype=np.uint8).reshape(
            (sh, source.bytesPerLine())
        )
        source_pixels = source_rows[:, :sw * 4].reshape((sh, sw, 4)).copy()
        output = np.zeros((ch, cw, 4), dtype=np.uint8)
        cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
        rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
        if len(self.transform_points) != cols * rows:
            return None

        def raster_triangle(target_triangle, source_triangle):
            tx = np.array([p.x() for p in target_triangle], dtype=np.float64)
            ty = np.array([p.y() for p in target_triangle], dtype=np.float64)
            min_x = max(0, int(math.floor(float(tx.min()))))
            max_x = min(cw - 1, int(math.ceil(float(tx.max()))))
            min_y = max(0, int(math.floor(float(ty.min()))))
            max_y = min(ch - 1, int(math.ceil(float(ty.max()))))
            if max_x < min_x or max_y < min_y:
                return
            denominator = (
                (ty[1] - ty[2]) * (tx[0] - tx[2])
                + (tx[2] - tx[1]) * (ty[0] - ty[2])
            )
            if abs(denominator) < 1e-8:
                return
            yy, xx = np.mgrid[min_y:max_y + 1, min_x:max_x + 1]
            w0 = (
                (ty[1] - ty[2]) * (xx - tx[2])
                + (tx[2] - tx[1]) * (yy - ty[2])
            ) / denominator
            w1 = (
                (ty[2] - ty[0]) * (xx - tx[2])
                + (tx[0] - tx[2]) * (yy - ty[2])
            ) / denominator
            w2 = 1.0 - w0 - w1
            inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not np.any(inside):
                return
            sx_float = (
                w0 * source_triangle[0][0]
                + w1 * source_triangle[1][0]
                + w2 * source_triangle[2][0]
            )
            sy_float = (
                w0 * source_triangle[0][1]
                + w1 * source_triangle[1][1]
                + w2 * source_triangle[2][1]
            )
            region = output[min_y:max_y + 1, min_x:max_x + 1]
            if smooth:
                sx_float = np.clip(sx_float, 0.0, sw - 1.0)
                sy_float = np.clip(sy_float, 0.0, sh - 1.0)
                x0 = np.floor(sx_float).astype(np.int32)
                y0 = np.floor(sy_float).astype(np.int32)
                x1 = np.minimum(sw - 1, x0 + 1)
                y1 = np.minimum(sh - 1, y0 + 1)
                tx = (sx_float - x0)[..., None]
                ty = (sy_float - y0)[..., None]
                sampled = (
                    source_pixels[y0, x0].astype(np.float32) * (1.0 - tx) * (1.0 - ty)
                    + source_pixels[y0, x1].astype(np.float32) * tx * (1.0 - ty)
                    + source_pixels[y1, x0].astype(np.float32) * (1.0 - tx) * ty
                    + source_pixels[y1, x1].astype(np.float32) * tx * ty
                )
                region[inside] = np.clip(
                    sampled[inside] + 0.5, 0, 255
                ).astype(np.uint8)
            else:
                sx = np.clip(np.rint(sx_float).astype(np.int32), 0, sw - 1)
                sy = np.clip(np.rint(sy_float).astype(np.int32), 0, sh - 1)
                region[inside] = source_pixels[sy[inside], sx[inside]]

        curved, dense_cols, dense_rows = self._curved_mesh_points(
            self.transform_points, cols, rows, subdivisions=8
        )
        for dense_y in range(dense_rows - 1):
            source_y0 = (
                (sh - 1) * dense_y / max(1, dense_rows - 1)
            )
            source_y1 = (
                (sh - 1) * (dense_y + 1) / max(1, dense_rows - 1)
            )
            for dense_x in range(dense_cols - 1):
                source_x0 = (
                    (sw - 1) * dense_x / max(1, dense_cols - 1)
                )
                source_x1 = (
                    (sw - 1) * (dense_x + 1)
                    / max(1, dense_cols - 1)
                )
                index = dense_y * dense_cols + dense_x
                p00 = curved[index]
                p10 = curved[index + 1]
                p01 = curved[index + dense_cols]
                p11 = curved[index + dense_cols + 1]
                raster_triangle(
                    (p00, p10, p11),
                    (
                        (source_x0, source_y0),
                        (source_x1, source_y0),
                        (source_x1, source_y1),
                    ),
                )
                raster_triangle(
                    (p00, p11, p01),
                    (
                        (source_x0, source_y0),
                        (source_x1, source_y1),
                        (source_x0, source_y1),
                    ),
                )
        return QImage(
            output.data, cw, ch, output.strides[0], QImage.Format.Format_RGBA8888
        ).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    # Pure QImage/NumPy/PIL conversions now live in imaging.py; these wrappers
    # keep the existing call sites (self._.../cls._...) working unchanged.
    @staticmethod
    def _qimage_rgba_array(image):
        return imaging.qimage_rgba_array(image)

    @staticmethod
    def _rgba_array_to_qimage(rgba):
        return imaging.rgba_array_to_qimage(rgba)

    @staticmethod
    def _pil_l_to_qimage(mask):
        return imaging.pil_l_to_qimage(mask)

    @staticmethod
    def _qimage_gray_array(image):
        return imaging.qimage_gray_array(image)

    @classmethod
    def _qimage_to_pil_rgba(cls, image):
        return imaging.qimage_to_pil_rgba(image)

    @classmethod
    def _pil_rgba_to_qimage(cls, image):
        return imaging.pil_rgba_to_qimage(image)

    @staticmethod
    def _tp_transparent_to_white(*args, **kwargs):
        return imaging.tp_transparent_to_white(*args, **kwargs)

    @staticmethod
    def _tp_white_to_transparent(*args, **kwargs):
        return imaging.tp_white_to_transparent(*args, **kwargs)

    @staticmethod
    def _tp_prepare_palette_image(*args, **kwargs):
        return imaging.tp_prepare_palette_image(*args, **kwargs)

    @staticmethod
    def _tp_make_color_masks(*args, **kwargs):
        return imaging.tp_make_color_masks(*args, **kwargs)

    def _prepare_tp_transform_masks(self, source_image=None):
        """Build the same exact-color masks used by TP Mask Transform v0.7."""
        self._clear_tp_transform_masks(clear_proxy=False)
        source = source_image if source_image is not None else self.transform_source
        if (
            PILImage is None or PILImageFilter is None
            or source is None or source.isNull()
        ):
            return False

        source_rgba = self._qimage_to_pil_rgba(source).convert("RGBA")
        prepared = self._tp_transparent_to_white(source_rgba)
        exact_color_counts = prepared.getcolors(maxcolors=257)
        if exact_color_counts is None:
            # This intentionally mirrors v0.7: color-rich images are reduced
            # without dithering.  The large-image proxy uses fewer colors to
            # keep its temporary mask memory bounded.
            prepared, palette_rgba = self._tp_prepare_palette_image(
                source_rgba,
                (
                    TP_MASK_PROXY_MAX_COLORS
                    if self._tp_proxy_rendering
                    else 64
                ),
            )
        else:
            palette_rgba = sorted(
                tuple(int(channel) for channel in color)
                for _count, color in exact_color_counts
                if color[3] > 0
            )

        prepared_array = np.asarray(prepared.convert("RGBA"), dtype=np.uint8)
        line_masks = []
        for line_color in self.transform_tp_line_colors:
            red, green, blue = line_color
            selected = (
                (prepared_array[:, :, 3] > 0)
                & np.all(
                    prepared_array[:, :, :3]
                    == np.asarray((red, green, blue), dtype=np.uint8),
                    axis=2,
                )
            )
            candidate = PILImage.fromarray(
                (selected * 255).astype(np.uint8), "L"
            )
            if candidate.getbbox() is not None:
                line_masks.append((tuple(line_color), candidate))
                palette_rgba = [
                    color for color in palette_rgba
                    if tuple(color[:3]) != tuple(line_color)
                ]

        self.transform_tp_palette = list(palette_rgba)
        self.transform_tp_masks = self._tp_make_color_masks(
            prepared, self.transform_tp_palette
        )
        self.transform_tp_line_masks = line_masks
        self.transform_tp_prepared_preview = self._pil_rgba_to_qimage(
            self._tp_white_to_transparent(prepared)
        )
        self._tp_mask_source_key = self._tp_mask_key(source)
        self._invalidate_tp_preview_cache()
        return bool(self.transform_tp_masks or self.transform_tp_line_masks)

    def _project_transform_source(
        self, source, target_width, target_height, smooth=False
    ):
        canvas_rect = QRectF(0, 0, target_width, target_height)
        if self.transform_mode == "mesh":
            return (
                self._mesh_preview_image(
                    source, target_width, target_height, smooth=smooth
                ),
                canvas_rect,
            )
        if len(self.transform_points) != 4:
            return None, canvas_rect
        width, height = source.width(), source.height()
        if width <= 0 or height <= 0:
            return None, canvas_rect
        source_quad = [
            QPointF(0, 0), QPointF(width, 0),
            QPointF(width, height), QPointF(0, height),
        ]
        transform = self._quad_homography(source_quad, self.transform_points)
        preview = QImage(
            target_width,
            target_height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        preview.fill(Qt.GlobalColor.transparent)
        painter = QPainter(preview)
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform, False
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setTransform(transform)
        painter.drawImage(0, 0, source)
        painter.end()
        return preview, canvas_rect

    def _transform_is_reducing(self, source=None):
        """変形のどこかに縮小があり、補間が必要かを返す。"""
        source = source if source is not None else self.transform_source
        if source is None or source.isNull() or not self.transform_points:
            return False

        if self.transform_mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
            reference = self._mesh_reference_grid(cols, rows)
            if (
                len(self.transform_points) != cols * rows
                or len(reference) != cols * rows
            ):
                return False
            pairs = []
            for row in range(rows):
                for col in range(cols - 1):
                    index = row * cols + col
                    pairs.append((index, index + 1))
            for row in range(rows - 1):
                for col in range(cols):
                    index = row * cols + col
                    pairs.append((index, index + cols))
            for first, second in pairs:
                current = math.hypot(
                    self.transform_points[second].x()
                    - self.transform_points[first].x(),
                    self.transform_points[second].y()
                    - self.transform_points[first].y(),
                )
                original = math.hypot(
                    reference[second].x() - reference[first].x(),
                    reference[second].y() - reference[first].y(),
                )
                if original > 1e-8 and current < original * 0.9999:
                    return True
            return False

        if len(self.transform_points) != 4:
            return False
        source_width = max(1.0, float(source.width()))
        source_height = max(1.0, float(source.height()))
        p0, p1, p2, p3 = self.transform_points[:4]
        horizontal_edges = (
            math.hypot(p1.x() - p0.x(), p1.y() - p0.y()),
            math.hypot(p2.x() - p3.x(), p2.y() - p3.y()),
        )
        vertical_edges = (
            math.hypot(p3.x() - p0.x(), p3.y() - p0.y()),
            math.hypot(p2.x() - p1.x(), p2.y() - p1.y()),
        )
        return (
            min(horizontal_edges) < source_width * 0.9999
            or min(vertical_edges) < source_height * 0.9999
        )

    def _tp_is_enlarging(self):
        source = self.transform_source
        if source is None or source.isNull() or not self.transform_points:
            return False
        source_width = max(1.0, float(source.width()))
        source_height = max(1.0, float(source.height()))

        if self.transform_mode == "mesh":
            cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
            rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
            if len(self.transform_points) != cols * rows:
                return False
            horizontal = []
            vertical = []
            for row in range(rows):
                for col in range(cols - 1):
                    p0 = self.transform_points[row * cols + col]
                    p1 = self.transform_points[row * cols + col + 1]
                    horizontal.append(math.hypot(p1.x()-p0.x(), p1.y()-p0.y()))
            for row in range(rows - 1):
                for col in range(cols):
                    p0 = self.transform_points[row * cols + col]
                    p1 = self.transform_points[(row + 1) * cols + col]
                    vertical.append(math.hypot(p1.x()-p0.x(), p1.y()-p0.y()))
            expected_x = source_width / max(1, cols - 1)
            expected_y = source_height / max(1, rows - 1)
            scale_x = (sum(horizontal) / len(horizontal)) / expected_x if horizontal else 1.0
            scale_y = (sum(vertical) / len(vertical)) / expected_y if vertical else 1.0
            return scale_x > 1.0001 or scale_y > 1.0001

        if len(self.transform_points) != 4:
            return False
        p0, p1, p2, p3 = self.transform_points[:4]
        scale_x = (
            math.hypot(p1.x()-p0.x(), p1.y()-p0.y())
            + math.hypot(p2.x()-p3.x(), p2.y()-p3.y())
        ) / (2.0 * source_width)
        scale_y = (
            math.hypot(p3.x()-p0.x(), p3.y()-p0.y())
            + math.hypot(p2.x()-p1.x(), p2.y()-p1.y())
        ) / (2.0 * source_height)
        return scale_x > 1.0001 or scale_y > 1.0001

    def _tp_transform_bbox(self, target_width, target_height):
        outline = self.transform_outer_polygon()
        if outline.isEmpty():
            return QRectF(), QRectF()
        raw_bounds = outline.boundingRect()
        canvas = QRectF(0, 0, target_width, target_height)
        expanded = raw_bounds.adjusted(-3, -3, 3, 3).intersected(canvas)
        if expanded.isEmpty():
            return QRectF(), raw_bounds.intersected(canvas)
        left = max(0, int(math.floor(expanded.left())))
        top = max(0, int(math.floor(expanded.top())))
        right = min(target_width, int(math.ceil(expanded.right())))
        bottom = min(target_height, int(math.ceil(expanded.bottom())))
        if right <= left or bottom <= top:
            return QRectF(), raw_bounds.intersected(canvas)
        return (
            QRectF(left, top, right-left, bottom-top),
            raw_bounds.intersected(canvas),
        )

    def _tp_mesh_render_mask(self, source_array, bbox, render_scale, smooth):
        # クオリティ変形も画素補間は行わず、色境界を最近傍で保持する。
        smooth = False
        source_array = np.asarray(source_array, dtype=np.uint8)
        source_height, source_width = source_array.shape
        out_width = max(1, int(math.ceil(bbox.width() * render_scale)))
        out_height = max(1, int(math.ceil(bbox.height() * render_scale)))
        output = np.zeros((out_height, out_width), dtype=np.uint8)
        cols = max(2, int(getattr(self, "transform_mesh_cols", 4)))
        rows = max(2, int(getattr(self, "transform_mesh_rows", 4)))
        if len(self.transform_points) != cols * rows:
            return output

        points = [
            QPointF(
                (point.x() - bbox.left()) * render_scale,
                (point.y() - bbox.top()) * render_scale,
            )
            for point in self.transform_points
        ]

        def raster_triangle(target_triangle, source_triangle):
            target_x = np.array(
                [point.x() for point in target_triangle], dtype=np.float64
            )
            target_y = np.array(
                [point.y() for point in target_triangle], dtype=np.float64
            )
            min_x = max(0, int(math.floor(float(target_x.min()))))
            max_x = min(out_width - 1, int(math.ceil(float(target_x.max()))))
            min_y = max(0, int(math.floor(float(target_y.min()))))
            max_y = min(out_height - 1, int(math.ceil(float(target_y.max()))))
            if max_x < min_x or max_y < min_y:
                return
            denominator = (
                (target_y[1] - target_y[2]) * (target_x[0] - target_x[2])
                + (target_x[2] - target_x[1]) * (target_y[0] - target_y[2])
            )
            if abs(denominator) < 1e-8:
                return
            yy, xx = np.mgrid[min_y:max_y + 1, min_x:max_x + 1]
            weight0 = (
                (target_y[1] - target_y[2]) * (xx - target_x[2])
                + (target_x[2] - target_x[1]) * (yy - target_y[2])
            ) / denominator
            weight1 = (
                (target_y[2] - target_y[0]) * (xx - target_x[2])
                + (target_x[0] - target_x[2]) * (yy - target_y[2])
            ) / denominator
            weight2 = 1.0 - weight0 - weight1
            inside = (
                (weight0 >= -1e-6)
                & (weight1 >= -1e-6)
                & (weight2 >= -1e-6)
            )
            if not np.any(inside):
                return
            source_x = (
                weight0 * source_triangle[0][0]
                + weight1 * source_triangle[1][0]
                + weight2 * source_triangle[2][0]
            )
            source_y = (
                weight0 * source_triangle[0][1]
                + weight1 * source_triangle[1][1]
                + weight2 * source_triangle[2][1]
            )
            region = output[min_y:max_y + 1, min_x:max_x + 1]
            if smooth:
                source_x = np.clip(source_x, 0.0, source_width - 1.0)
                source_y = np.clip(source_y, 0.0, source_height - 1.0)
                x0 = np.floor(source_x).astype(np.int32)
                y0 = np.floor(source_y).astype(np.int32)
                x1 = np.minimum(source_width - 1, x0 + 1)
                y1 = np.minimum(source_height - 1, y0 + 1)
                fraction_x = source_x - x0
                fraction_y = source_y - y0
                sampled = (
                    source_array[y0, x0].astype(np.float32)
                    * (1.0-fraction_x) * (1.0-fraction_y)
                    + source_array[y0, x1].astype(np.float32)
                    * fraction_x * (1.0-fraction_y)
                    + source_array[y1, x0].astype(np.float32)
                    * (1.0-fraction_x) * fraction_y
                    + source_array[y1, x1].astype(np.float32)
                    * fraction_x * fraction_y
                )
                region[inside] = np.clip(
                    sampled[inside] + 0.5, 0, 255
                ).astype(np.uint8)
            else:
                sample_x = np.clip(
                    np.rint(source_x).astype(np.int32), 0, source_width - 1
                )
                sample_y = np.clip(
                    np.rint(source_y).astype(np.int32), 0, source_height - 1
                )
                region[inside] = source_array[
                    sample_y[inside], sample_x[inside]
                ]

        curved, dense_cols, dense_rows = self._curved_mesh_points(
            points, cols, rows, subdivisions=8
        )
        for dense_y in range(dense_rows - 1):
            source_y0 = (
                (source_height - 1)
                * dense_y / max(1, dense_rows - 1)
            )
            source_y1 = (
                (source_height - 1)
                * (dense_y + 1) / max(1, dense_rows - 1)
            )
            for dense_x in range(dense_cols - 1):
                source_x0 = (
                    (source_width - 1)
                    * dense_x / max(1, dense_cols - 1)
                )
                source_x1 = (
                    (source_width - 1)
                    * (dense_x + 1) / max(1, dense_cols - 1)
                )
                index = dense_y * dense_cols + dense_x
                p00 = curved[index]
                p10 = curved[index + 1]
                p01 = curved[index + dense_cols]
                p11 = curved[index + dense_cols + 1]
                raster_triangle(
                    (p00, p10, p11),
                    (
                        (source_x0, source_y0),
                        (source_x1, source_y0),
                        (source_x1, source_y1),
                    ),
                )
                raster_triangle(
                    (p00, p11, p01),
                    (
                        (source_x0, source_y0),
                        (source_x1, source_y1),
                        (source_x0, source_y1),
                    ),
                )
        return output

    def _tp_project_mask_to_bbox(self, mask, bbox, render_scale=1):
        out_width = max(1, int(math.ceil(bbox.width() * render_scale)))
        out_height = max(1, int(math.ceil(bbox.height() * render_scale)))
        if self.transform_mode == "mesh":
            source_array = np.asarray(mask.convert("L"), dtype=np.uint8)
            rendered = self._tp_mesh_render_mask(
                source_array, bbox, render_scale, smooth=False
            )
            return PILImage.fromarray(rendered, "L")

        output = QImage(
            out_width, out_height, QImage.Format.Format_Grayscale8
        )
        output.fill(0)
        source_width, source_height = mask.size
        source_quad = [
            QPointF(0, 0), QPointF(source_width, 0),
            QPointF(source_width, source_height), QPointF(0, source_height),
        ]
        target_quad = [
            QPointF(
                (point.x() - bbox.left()) * render_scale,
                (point.y() - bbox.top()) * render_scale,
            )
            for point in self.transform_points[:4]
        ]
        transform = self._quad_homography(source_quad, target_quad)
        painter = QPainter(output)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setTransform(transform)
        painter.drawImage(QPointF(0, 0), self._pil_l_to_qimage(mask))
        painter.end()
        return PILImage.fromarray(self._qimage_gray_array(output), "L")

    def _tp_render_mask_to_bbox(
        self, mask, bbox, smoothing=0.0, supersample=4
    ):
        """v0.7 mask renderer, extended to free and mesh transformations."""
        if self._tp_proxy_rendering:
            supersample = 1
        out_width = max(1, int(math.ceil(bbox.width())))
        out_height = max(1, int(math.ceil(bbox.height())))
        if self._tp_is_enlarging():
            source = mask.convert("L")
            if smoothing > 0.001:
                source = source.filter(
                    PILImageFilter.GaussianBlur(radius=float(smoothing))
                )
            return self._tp_project_mask_to_bbox(source, bbox, 1)

        high = self._tp_project_mask_to_bbox(mask.convert("L"), bbox, supersample)
        high = high.filter(
            PILImageFilter.GaussianBlur(radius=0.65 * supersample)
        )
        return high.resize(
            (out_width, out_height), PILImage.Resampling.LANCZOS
        )

    def _tp_geometry_key(self, source, target_width, target_height, bbox):
        points = tuple(
            (round(point.x(), 5), round(point.y(), 5))
            for point in self.transform_points
        )
        return (
            int(source.cacheKey()),
            int(target_width), int(target_height),
            self.transform_mode,
            points,
            int(getattr(self, "transform_mesh_cols", 4)),
            int(getattr(self, "transform_mesh_rows", 4)),
            int(bbox.left()), int(bbox.top()),
            int(bbox.width()), int(bbox.height()),
            tuple(tuple(color) for color in self.transform_tp_palette),
            tuple(tuple(color) for color, _mask in self.transform_tp_line_masks),
        )

    def _build_tp_geometry_cache(
        self, source, target_width, target_height, progress_callback=None
    ):
        bbox, _raw_selection = self._tp_transform_bbox(
            target_width, target_height
        )
        if bbox.isEmpty():
            self._tp_geometry_cache_bbox = QRectF()
            self._tp_geometry_cache_fill_overlay = PILImage.new(
                "RGBA", (1, 1), (0, 0, 0, 0)
            )
            self._tp_geometry_cache_line_soft = []
            return bbox

        cache_key = self._tp_geometry_key(
            source, target_width, target_height, bbox
        )
        if (
            self._tp_geometry_cache_key == cache_key
            and self._tp_geometry_cache_fill_overlay is not None
        ):
            return QRectF(self._tp_geometry_cache_bbox)

        total = max(
            1,
            len(self.transform_tp_masks)
            + len(self.transform_tp_line_masks)
            + 2,
        )
        completed = 0
        if progress_callback is not None:
            progress_callback(
                completed, total, "色マスクの変形を準備しています"
            )

        width = max(1, int(math.ceil(bbox.width())))
        height = max(1, int(math.ceil(bbox.height())))
        best_value = np.zeros((height, width), dtype=np.uint8)
        best_rgba = np.zeros((height, width, 4), dtype=np.uint8)

        # Every exact color, including the temporary white background, competes
        # for each output pixel.  This is the defining v0.7 TP_mask behavior.
        fill_count = len(self.transform_tp_masks)
        for mask_index, (color, mask) in enumerate(zip(
            self.transform_tp_palette, self.transform_tp_masks
        ), 1):
            rendered = self._tp_render_mask_to_bbox(
                mask, bbox, self.transform_tp_fill_smoothing
            )
            values = np.asarray(rendered, dtype=np.uint8)
            replace = values > best_value
            if np.any(replace):
                best_value[replace] = values[replace]
                best_rgba[replace] = np.asarray(color, dtype=np.uint8)
            completed += 1
            if progress_callback is not None:
                progress_callback(
                    completed,
                    total,
                    f"色マスクを変形しています（{mask_index} / {fill_count}）",
                )

        output = np.zeros((height, width, 4), dtype=np.uint8)
        visible = best_value > 0
        if np.any(visible):
            output[visible, :3] = best_rgba[visible, :3]
            output[visible, 3] = 255
        white = visible & np.all(output[:, :, :3] == 255, axis=2)
        output[white, 3] = 0
        completed += 1
        if progress_callback is not None:
            progress_callback(
                completed, total, "色マスクを再合成しています"
            )

        line_soft = []
        line_count = len(self.transform_tp_line_masks)
        for line_index, (line_color, line_mask) in enumerate(
            self.transform_tp_line_masks, 1
        ):
            line_soft.append((
                tuple(line_color),
                self._tp_render_mask_to_bbox(
                    line_mask, bbox, self.transform_tp_line_smoothing
                ),
            ))
            completed += 1
            if progress_callback is not None:
                progress_callback(
                    completed,
                    total,
                    f"実線マスクを変形しています（{line_index} / {line_count}）",
                )

        self._tp_geometry_cache_key = cache_key
        self._tp_geometry_cache_bbox = QRectF(bbox)
        self._tp_geometry_cache_fill_overlay = PILImage.fromarray(
            output, "RGBA"
        )
        self._tp_geometry_cache_line_soft = line_soft
        completed += 1
        if progress_callback is not None:
            progress_callback(
                min(completed, total), total, "プレビューを仕上げています"
            )
        return bbox

    def _tp_mask_preview_image(
        self, source_image=None, target_width=None, target_height=None,
        progress_callback=None
    ):
        source = (
            source_image if source_image is not None else self.transform_source
        )
        if source is None or source.isNull():
            return None, QRectF()
        target_width = int(
            target_width
            if target_width is not None
            else self.active_layer.image.width()
        )
        target_height = int(
            target_height
            if target_height is not None
            else self.active_layer.image.height()
        )
        canvas_rect = QRectF(0, 0, target_width, target_height)

        expected_mask_key = self._tp_mask_key(source)
        if (
            self._tp_mask_source_key != expected_mask_key
            or (
                not self.transform_tp_masks
                and not self.transform_tp_line_masks
            )
        ):
            if not self._prepare_tp_transform_masks(source):
                return self._project_transform_source(
                    source, target_width, target_height, smooth=False
                )

        threshold = max(
            1, min(254, int(getattr(self, "transform_line_threshold", 96)))
        )
        bbox, raw_selection = self._tp_transform_bbox(
            target_width, target_height
        )
        point_key = tuple(
            (round(point.x(), 5), round(point.y(), 5))
            for point in self.transform_points
        )
        preview_key = (
            int(source.cacheKey()), target_width, target_height,
            self.transform_mode, point_key,
            int(getattr(self, "transform_mesh_cols", 4)),
            int(getattr(self, "transform_mesh_rows", 4)),
            threshold,
            tuple(self.transform_tp_line_colors),
        )
        if (
            source is self.transform_source
            and self._tp_preview_cache_key == preview_key
            and self._tp_preview_cache_image is not None
        ):
            return self._tp_preview_cache_image, canvas_rect

        bbox = self._build_tp_geometry_cache(
            source, target_width, target_height, progress_callback
        )
        full = QImage(
            target_width, target_height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        full.fill(Qt.GlobalColor.transparent)
        if not bbox.isEmpty():
            width = max(1, int(math.ceil(bbox.width())))
            height = max(1, int(math.ceil(bbox.height())))
            overlay = self._tp_geometry_cache_fill_overlay.copy()
            for line_color, line_soft in self._tp_geometry_cache_line_soft:
                binary = line_soft.point(
                    lambda value, limit=threshold:
                    255 if value >= limit else 0
                )
                red, green, blue = line_color
                line_fill = PILImage.new(
                    "RGBA", (width, height), (red, green, blue, 255)
                )
                overlay.paste(line_fill, (0, 0), binary)
            painter = QPainter(full)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )
            painter.drawImage(
                QPointF(math.floor(bbox.left()), math.floor(bbox.top())),
                self._pil_rgba_to_qimage(overlay),
            )
            painter.end()

        if source is self.transform_source:
            self._tp_preview_cache_key = preview_key
            self._tp_preview_cache_image = full
        return full, canvas_rect

    def _proxy_transform_preview_image(
        self,
        source,
        target_width,
        target_height,
        quality,
        progress_callback=None,
    ):
        """大画像の表示用変形を縮小座標で生成し、UIの負荷を抑える。"""
        scale = min(
            1.0,
            TP_MASK_PROXY_MAX_DIMENSION
            / float(max(1, target_width, target_height)),
        )
        proxy_width = max(1, int(round(target_width * scale)))
        proxy_height = max(1, int(round(target_height * scale)))
        scale_x = proxy_width / float(max(1, target_width))
        scale_y = proxy_height / float(max(1, target_height))
        source_width = max(1, int(round(source.width() * scale_x)))
        source_height = max(1, int(round(source.height() * scale_y)))
        proxy_source_key = (
            int(source.cacheKey()),
            source_width,
            source_height,
        )
        if (
            self._tp_proxy_source_key != proxy_source_key
            or self._tp_proxy_source_image is None
            or self._tp_proxy_source_image.isNull()
        ):
            self._tp_proxy_source_image = source.scaled(
                source_width,
                source_height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._tp_proxy_source_key = proxy_source_key

        proxy_source = self._tp_proxy_source_image
        original_source = self.transform_source
        original_points = self.transform_points
        original_source_rect = self.transform_source_rect
        original_proxy_rendering = self._tp_proxy_rendering
        self.transform_source = proxy_source
        self.transform_points = [
            QPointF(point.x() * scale_x, point.y() * scale_y)
            for point in original_points
        ]
        if original_source_rect is not None:
            self.transform_source_rect = QRectF(
                original_source_rect.x() * scale_x,
                original_source_rect.y() * scale_y,
                original_source_rect.width() * scale_x,
                original_source_rect.height() * scale_y,
            )
        self._tp_proxy_rendering = True
        try:
            if quality:
                return self._tp_mask_preview_image(
                    proxy_source,
                    proxy_width,
                    proxy_height,
                    progress_callback=progress_callback,
                )
            return self._project_transform_source(
                proxy_source,
                proxy_width,
                proxy_height,
                smooth=False,
            )
        finally:
            self.transform_source = original_source
            self.transform_points = original_points
            self.transform_source_rect = original_source_rect
            self._tp_proxy_rendering = original_proxy_rendering

    def transform_preview_image(
        self,
        source_image=None,
        target_width=None,
        target_height=None,
        quality=None,
        preview_only=False,
        progress_callback=None,
    ):
        source = source_image if source_image is not None else self.transform_source
        if not self.transform_active or source is None:
            return None, QRectF()
        target_width = int(
            target_width if target_width is not None else self.active_layer.image.width()
        )
        target_height = int(
            target_height if target_height is not None else self.active_layer.image.height()
        )
        if quality is None:
            quality = bool(getattr(self, "transform_quality_active", False))
        if (
            preview_only
            and self._tp_uses_proxy(target_width, target_height)
        ):
            return self._proxy_transform_preview_image(
                source,
                target_width,
                target_height,
                bool(quality),
                progress_callback=progress_callback,
            )
        if quality:
            return self._tp_mask_preview_image(
                source,
                target_width,
                target_height,
                progress_callback=progress_callback,
            )
        return self._project_transform_source(
            source,
            target_width,
            target_height,
            smooth=False,
        )

    def _selection_source_from_image(self, image):
        rect = self.transform_source_rect.toAlignedRect().intersected(image.rect())
        if rect.isEmpty():
            return None
        source = image.copy(rect)
        if self.selection_polygon:
            if self.selection_mask_override is not None:
                mask = self._bool_mask_image(self.selection_mask_bool(
                    rect.width(), rect.height(), rect.x(), rect.y()
                ))
                painter = QPainter(source)
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_DestinationIn
                )
                painter.drawImage(0, 0, mask)
                painter.end()
                return source
            mask = QImage(rect.width(), rect.height(), QImage.Format.Format_ARGB32_Premultiplied)
            mask.fill(Qt.GlobalColor.transparent)
            painter = QPainter(mask)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 255))
            painter.drawPolygon(QPolygonF([
                QPointF(point.x() - rect.x(), point.y() - rect.y())
                for point in self.selection_polygon
            ]))
            painter.end()
            painter = QPainter(source)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.drawImage(0, 0, mask)
            painter.end()
        return source

    def _cleared_selection_base(self, image):
        base = image.copy()
        painter = QPainter(base)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 255))
        if self.selection_mask_override is not None:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_DestinationOut
            )
            painter.drawImage(
                0, 0, self._bool_mask_image(self.selection_mask_override)
            )
        elif self.selection_polygon:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
            )
            painter.drawPolygon(QPolygonF(self.selection_polygon))
        elif self.transform_source_rect is not None:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Clear
            )
            painter.fillRect(self.transform_source_rect, QColor(0, 0, 0, 255))
        painter.end()
        return base

    def _reset_selection_transform(self):
        self.transform_active = False
        self.transform_mode = None
        self.transform_original_layer = None
        self.transform_original_has_content = False
        self.transform_source = None
        self.transform_source_rect = None
        self.transform_points = []
        self.transform_mesh_reference_points = []
        self.transform_handle = -1
        self.transform_drag_kind = None
        self.transform_drag_points = []
        self.transform_quality_active = False
        self.transform_tp_line_colors = ()
        self._clear_tp_transform_masks()

    def commit_selection_transform(self, all_frames=None):
        if not self.transform_active:
            return
        window = self.window()
        quality_active = bool(getattr(self, "transform_quality_active", False))
        if all_frames is None:
            all_frames = bool(
                window.tools.selection_all_frames.isChecked()
                if hasattr(window, "tools")
                else getattr(self, "transform_apply_all_frames", False)
            )
        if quality_active:
            all_frames = False
            target_width = self.active_layer.image.width()
            target_height = self.active_layer.image.height()
            if self._tp_uses_proxy(target_width, target_height):
                # 大画像の表示中キャッシュは縮小代理画像なので、確定時だけ
                # 原寸マスクを生成する。代理画像を確定結果へ流用しない。
                self._clear_tp_transform_masks(clear_proxy=False)
                self.refresh_quality_preview_with_counter(
                    "変形確定用の原寸クオリティ画像を生成しています",
                    full_resolution=True,
                )
            elif self._tp_preview_cache_image is None:
                self.refresh_quality_preview_with_counter(
                    "変形確定用のクオリティ画像を生成しています"
                )
            if self._tp_preview_cache_image is None:
                self.status_message.emit(
                    "クオリティ画像を生成できなかったため、変形確定を中止しました。"
                )
                return

        layer_index = int(getattr(self, "transform_layer_index", self.active_layer_index))
        current_index = int(getattr(self, "transform_frame_index", self.current_frame))
        original_current = self.transform_original_layer
        original_current_has_content = getattr(
            self, "transform_original_has_content", self.active_layer.has_content
        )
        frame_indices = list(
            range(len(self.frames)) if all_frames else [current_index]
        )
        undo_cells = []
        changed_frames = []
        progress = None
        if all_frames and hasattr(window, "create_progress_counter"):
            progress = window.create_progress_counter(
                "すべてのコマに変形",
                len(frame_indices),
                "変形を準備しています",
            )

        for progress_index, frame_index in enumerate(frame_indices, 1):
            if progress is not None:
                window.update_progress_counter(
                    progress,
                    progress_index - 1,
                    len(frame_indices),
                    f"コマ {frame_index + 1} を変形しています",
                )
            if not (0 <= frame_index < len(self.frames)):
                continue
            frame = self.frames[frame_index]
            if not (0 <= layer_index < len(frame.layers)):
                continue
            layer = frame.layers[layer_index]
            if frame_index == current_index and original_current is not None:
                original = original_current.copy()
                original_has_content = original_current_has_content
            else:
                if not layer.has_content:
                    continue
                original = layer.image.copy()
                original_has_content = layer.has_content

            source = (
                self.transform_source
                if quality_active and frame_index == current_index
                and self.transform_source is not None
                else self._selection_source_from_image(original)
            )
            if source is None or source.isNull():
                continue
            preview, _target = self.transform_preview_image(
                source, original.width(), original.height(),
                quality=quality_active,
            )
            if preview is None:
                continue

            base = self._cleared_selection_base(original)
            painter = QPainter(base)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawImage(0, 0, preview)
            painter.end()

            undo_cells.append((frame_index, original.copy(), original_has_content))
            layer.image = base
            layer.has_content = True
            changed_frames.append(frame_index)

        if progress is not None:
            window.close_progress_counter(progress)

        for frame_index in changed_frames:
            self.sync_numbered_image_from_cell(
                frame_index,
                layer_index,
            )

        if undo_cells:
            if len(undo_cells) == 1:
                frame_index, image, has_content = undo_cells[0]
                self.undo_stack.append((
                    "layer", frame_index, layer_index, image, has_content
                ))
            else:
                self.undo_stack.append(("layer_batch", layer_index, undo_cells))
            self.undo_stack = self.undo_stack[-MAX_UNDO:]
            self.redo_stack.clear()
            # 変形ではコマ構造と使用色の種類は変わらないため、
            # タイムライン全再構築・使用色全走査は行わない。
        elif (
            original_current is not None
            and 0 <= current_index < len(self.frames)
            and 0 <= layer_index < len(self.frames[current_index].layers)
        ):
            layer = self.frames[current_index].layers[layer_index]
            layer.image = original_current
            layer.has_content = original_current_has_content

        self._reset_selection_transform()
        self.transform_apply_all_frames = False
        # V62: a confirmed transform finishes the operation completely.
        self.selection_polygon = []
        self.selection_mask_override = None
        self.selection_outline_polygons = []
        self.selection_mask_rect = None
        self.lasso = []
        self.rect_start = None
        self.rect_end = None
        self.selectionChanged.emit()
        self._update_selection_clear_overlay()
        self.update()

    def cancel_selection_transform(self):
        if not self.transform_active:
            return
        if self.transform_original_layer is not None:
            frame_index = int(getattr(self, "transform_frame_index", self.current_frame))
            layer_index = int(getattr(self, "transform_layer_index", self.active_layer_index))
            if (
                0 <= frame_index < len(self.frames)
                and 0 <= layer_index < len(self.frames[frame_index].layers)
            ):
                layer = self.frames[frame_index].layers[layer_index]
                layer.image = self.transform_original_layer
                layer.has_content = getattr(
                    self, "transform_original_has_content", layer.has_content
                )
        self._reset_selection_transform()
        self.transform_apply_all_frames = False
        self.update()

    def selection_bounds(self):
        if not self.selection_polygon:
            return QRectF(0,0,self.active_layer.image.width(),self.active_layer.image.height()).toAlignedRect()
        if self.selection_mask_rect is not None:
            return QRectF(self.selection_mask_rect).toAlignedRect()
        xs=[p.x() for p in self.selection_polygon]; ys=[p.y() for p in self.selection_polygon]
        return QRectF(min(xs),min(ys),max(xs)-min(xs),max(ys)-min(ys)).toAlignedRect()

    def copy_selection(self):
        rect=self.selection_bounds().intersected(self.active_layer.image.rect())
        if rect.isEmpty(): return
        QApplication.clipboard().setImage(self.active_layer.image.copy(rect))

    def cut_selection(self):
        rect=self.selection_bounds().intersected(self.active_layer.image.rect())
        if rect.isEmpty(): return
        self.push_layer_region_undo(rect)
        QApplication.clipboard().setImage(self.active_layer.image.copy(rect))
        p=QPainter(self.active_layer.image)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        if self.selection_polygon:
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(0,0,0,255));p.drawPolygon(QPolygonF(self.selection_polygon))
        else:p.fillRect(rect,QColor(0,0,0,255))
        p.end();self.cellChanged.emit(self.current_frame,self.active_layer_index);self.update()

    def paste_clipboard(self):
        image=QApplication.clipboard().image()
        if image.isNull(): return
        self.ensure_editable_key()
        rect=self.selection_bounds()
        pos=rect.topLeft() if self.selection_polygon else QPoint(OUTSIDE_MARGIN,OUTSIDE_MARGIN)
        changed_rect = QRect(pos, image.size()).intersected(
            self.active_layer.image.rect()
        )
        if not self.push_layer_region_undo(changed_rect): return
        p=QPainter(self.active_layer.image);p.drawImage(pos,image);p.end()
        self.active_layer.has_content=True
        self.cellChanged.emit(self.current_frame,self.active_layer_index);self.update()

    def sample_color(self, p, include_canvas_background=False):
        x, y = int(p.x()), int(p.y())
        width, height = workspace_size()
        if not (0 <= x < width and 0 <= y < height):
            return

        # 上から順に実画像を調べる。レイヤー表示不透明度は色データへ
        # 混ぜず、スポイトでは元のRGBA色を取得する。
        color = None
        layer_count = len(self.frames[self.current_frame].layers)
        for layer_index in range(layer_count - 1, -1, -1):
            key_frame = self.resolve_key_frame(
                self.current_frame,
                layer_index,
            )
            if key_frame is None:
                continue
            layer = self.frames[key_frame].layers[layer_index]
            if not layer.visible or float(layer.opacity) <= 0.0:
                continue
            source = layer.image
            if not (0 <= x < source.width() and 0 <= y < source.height()):
                continue
            # 使用色の表示OFFなどで見えていない画素は飛ばすが、
            # 表示不透明度やシルエット色はスポイト色へ混ぜない。
            display_source = self._display_layer_image(layer, layer_index)
            if display_source.pixelColor(x, y).alpha() == 0:
                continue
            candidate = source.pixelColor(x, y)
            if candidate.alpha() > 0:
                color = QColor(candidate)
                break

        if color is None or color.alpha() == 0:
            if not include_canvas_background:
                return
            color = QColor(self.transparent_display_color)
            if not color.isValid():
                return
        color.setAlpha(255)
        if self.color_mode == "sub":
            self.sub_color = QColor(color)
        else:
            self.main_color = QColor(color)
            if self.color_mode == "transparent":
                self.color_mode = "main"
        self.colorSampled.emit(QColor(color))
        self.update()

    def _pseudo_transparency_key(self, image):
        """Return the cache key shared by idle and in-stroke display paths."""
        try:
            return (
                int(image.cacheKey()),
                image.width(),
                image.height(),
            )
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.debug("cacheKey() unavailable, using id() fallback: %s", exc)
            return (id(image), image.width(), image.height())

    def _pseudo_transparent_display_image(self, image):
        """#FFFFFFを表示上だけ透明化し、他の可視画素はα255で表示する。"""
        if image is None or image.isNull():
            return image
        key = self._pseudo_transparency_key(image)

        cached = self._pseudo_transparency_cache.get(key)
        if cached is not None:
            return cached

        rgba = image.convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        width, height = rgba.width(), rgba.height()
        if width <= 0 or height <= 0:
            return rgba

        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(
            ptr,
            dtype=np.uint8,
        ).reshape((height, rgba.bytesPerLine()))
        pixels = rows[:, :width * 4].reshape(
            (height, width, 4)
        )
        alpha = pixels[:, :, 3]
        present = alpha > 0
        white = (
            present
            & (pixels[:, :, 0] == 255)
            & (pixels[:, :, 1] == 255)
            & (pixels[:, :, 2] == 255)
        )
        alpha[white] = 0
        alpha[present & ~white] = 255

        result = rgba.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        if len(self._pseudo_transparency_cache) >= 96:
            self._pseudo_transparency_cache.pop(
                next(iter(self._pseudo_transparency_cache))
            )
        self._pseudo_transparency_cache[key] = result
        return result

    def silhouette_layer_image(self, layer, apply_palette_filter=True):
        base = self.filtered_layer_image(
            layer,
            apply_palette_filter,
        )
        if not layer.is_paper:
            base = self._pseudo_transparent_display_image(base)
        if not self.silhouette_non_background:
            return base
        try:
            key = (int(base.cacheKey()), base.width(), base.height())
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.debug("cacheKey() unavailable, using id() fallback: %s", exc)
            key = (id(base), base.width(), base.height())
        cached = self._silhouette_cache.get(key)
        if cached is not None:
            return cached
        rgba = base.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = rgba.width(), rgba.height()
        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape((height, rgba.bytesPerLine()))
        pixels = rows[:, :width * 4].reshape((height, width, 4))
        visible = pixels[:, :, 3] > 0
        pixels[:, :, 0][visible] = 0
        pixels[:, :, 1][visible] = 0
        pixels[:, :, 2][visible] = 0
        result = rgba.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        if len(self._silhouette_cache) >= 64:
            self._silhouette_cache.clear()
        self._silhouette_cache[key] = result
        return result

    def filtered_layer_image(self, layer, apply_palette_filter=True):
        """Return a cached display-only image honoring this layer's palette visibility."""
        visible = (
            getattr(self, "visible_color_rgbs", None)
            if apply_palette_filter else None
        )
        legacy_rgb = (
            tuple(int(value) for value in layer.color_filter_rgb[:3])
            if getattr(layer, "color_filter_enabled", False) and layer.color_filter_rgb is not None
            else None
        )
        if visible is None and legacy_rgb is None:
            return layer.image
        rgb = legacy_rgb
        try:
            image_key = int(layer.image.cacheKey())
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.debug("cacheKey() unavailable, using id() fallback: %s", exc)
            image_key = id(layer.image)
        width, height = layer.image.width(), layer.image.height()
        visible_key = None if visible is None else tuple(sorted(visible))
        key = (image_key, width, height, rgb, visible_key)
        cached = self._color_filter_cache.get(key)
        if cached is not None:
            return cached

        index_key = (image_key, width, height)
        indexed = self._color_index_cache.get(index_key)
        if indexed is None:
            base_rgba = layer.image.convertToFormat(QImage.Format.Format_RGBA8888)
            if width <= 0 or height <= 0:
                return base_rgba
            base_ptr = base_rgba.bits()
            try:
                base_ptr.setsize(base_rgba.sizeInBytes())
            except AttributeError:
                pass
            base_rows = np.frombuffer(base_ptr, dtype=np.uint8).reshape(
                (height, base_rgba.bytesPerLine())
            )
            base_pixels = base_rows[:, :width * 4].reshape((height, width, 4))
            opaque = (base_pixels[:, :, 3] > 0).copy()
            packed = (
                (base_pixels[:, :, 0].astype(np.uint32) << 16)
                | (base_pixels[:, :, 1].astype(np.uint32) << 8)
                | base_pixels[:, :, 2].astype(np.uint32)
            )
            indexed = (base_rgba.copy(), packed, opaque)
            if len(self._color_index_cache) >= 24:
                self._color_index_cache.pop(next(iter(self._color_index_cache)))
            self._color_index_cache[index_key] = indexed

        base_rgba, packed, opaque = indexed
        rgba = base_rgba.copy()
        ptr = rgba.bits()
        try:
            ptr.setsize(rgba.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape((height, rgba.bytesPerLine()))
        pixels = rows[:, :width * 4].reshape((height, width, 4))

        if visible is not None:
            selected_values = np.fromiter(
                ((r << 16) | (g << 8) | b for r, g, b in visible),
                dtype=np.uint32,
                count=len(visible),
            )
            keep = opaque & np.isin(packed, selected_values)
        else:
            keep = opaque.copy()
        if rgb is not None:
            packed_target = (
                (int(rgb[0]) << 16) | (int(rgb[1]) << 8) | int(rgb[2])
            )
            keep &= opaque & (packed == packed_target)
        pixels[:, :, 3][~keep] = 0

        filtered = rgba.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        if len(self._color_filter_cache) >= 96:
            self._color_filter_cache.pop(next(iter(self._color_filter_cache)))
        self._color_filter_cache[key] = filtered
        return filtered

    def _display_layer_image(self, layer, layer_index):
        """白を疑似透明化した、表示専用のレイヤー画像を返す。"""
        # ストローク中はアクティブレイヤーの差分更新済みバッファをそのまま使う。
        if (
            self._stroke_display_image is not None
            and layer_index == self._stroke_display_layer_index
            and layer is self.active_layer
        ):
            return self._stroke_display_image
        apply_palette_filter = (
            layer_index == self.active_layer_index
        )
        if (
            self.silhouette_non_background
            and not layer.is_paper
        ):
            return self.silhouette_layer_image(
                layer,
                apply_palette_filter,
            )

        draw_image = self.filtered_layer_image(
            layer,
            apply_palette_filter,
        )
        if not layer.is_paper:
            draw_image = self._pseudo_transparent_display_image(
                draw_image
            )
        return draw_image

    def composite(self, fi, white=True):
        result = blank_image(QColor("white") if white else Qt.GlobalColor.transparent)
        painter = QPainter(result)
        layer_count = len(self.frames[fi].layers)
        for layer_index in range(layer_count):
            key_frame = self.resolve_key_frame(fi, layer_index)
            if key_frame is None:
                continue
            layer = self.frames[key_frame].layers[layer_index]
            if layer.visible:
                painter.setOpacity(layer.opacity)
                painter.drawImage(
                    0, 0, self._display_layer_image(layer, layer_index)
                )
        painter.end()
        return result

    def _build_playback_key_map(self):
        """各フレームで表示するキーフレームを事前解決する。"""
        frame_count = len(self.frames)
        if frame_count <= 0:
            self._playback_resolved_keys = []
            return

        layer_count = max(
            (len(frame.layers) for frame in self.frames),
            default=0,
        )
        resolved = [
            [None] * layer_count
            for _ in range(frame_count)
        ]

        for layer_index in range(layer_count):
            current_key = None
            exposure_end = -1
            for frame_index in range(frame_count):
                if layer_index >= len(
                    self.frames[frame_index].layers
                ):
                    current_key = None
                    exposure_end = -1
                    continue

                layer = self.frames[
                    frame_index
                ].layers[layer_index]
                if layer.has_content:
                    current_key = frame_index
                    exposure_end = (
                        frame_index
                        + max(1, int(layer.exposure))
                    )
                elif frame_index >= exposure_end:
                    current_key = None

                resolved[frame_index][layer_index] = current_key

        self._playback_resolved_keys = resolved

    def set_playback_active(self, active):
        self._playback_active = bool(active)
        self._playback_frame_cache.clear()
        if self._playback_active:
            self._build_playback_key_map()
        else:
            self._playback_resolved_keys = []
        self.update()

    def playback_advance(self, steps=1):
        """UI全体を再構築せず、再生フレームだけを進める。"""
        if not self.frames:
            return
        self.current_frame = (
            self.current_frame + max(1, int(steps))
        ) % len(self.frames)
        self.update()

    def _playback_cache_dimensions(self, target_rect):
        source_width = max(1, int(round(target_rect.width())))
        source_height = max(1, int(round(target_rect.height())))

        # 大画像でも再生用キャッシュは表示領域程度までに制限する。
        max_width = max(480, min(1920, int(self.width() * 1.25)))
        max_height = max(320, min(1080, int(self.height() * 1.25)))
        scale = min(
            1.0,
            max_width / float(source_width),
            max_height / float(source_height),
        )
        return (
            max(1, int(round(source_width * scale))),
            max(1, int(round(source_height * scale))),
        )

    def playback_frame_image(self, frame_index, target_rect):
        if not (0 <= frame_index < len(self.frames)):
            return None

        cache_width, cache_height = (
            self._playback_cache_dimensions(target_rect)
        )
        cache_key = (
            int(frame_index),
            int(cache_width),
            int(cache_height),
            int(self.active_layer_index),
            bool(self.flip_horizontal),
            bool(self.silhouette_non_background),
        )
        cached = self._playback_frame_cache.get(cache_key)
        if cached is not None:
            # 最近使ったフレームを末尾へ移す簡易LRU。
            self._playback_frame_cache.pop(cache_key, None)
            self._playback_frame_cache[cache_key] = cached
            return cached

        image = QImage(
            cache_width,
            cache_height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            False,
        )
        destination = QRectF(
            0.0,
            0.0,
            float(cache_width),
            float(cache_height),
        )

        layer_count = len(self.frames[frame_index].layers)
        resolved_row = (
            self._playback_resolved_keys[frame_index]
            if frame_index < len(self._playback_resolved_keys)
            else []
        )
        for layer_index in range(layer_count):
            key_frame = (
                resolved_row[layer_index]
                if layer_index < len(resolved_row)
                else self.resolve_key_frame(
                    frame_index, layer_index
                )
            )
            if key_frame is None:
                continue

            layer = self.frames[
                key_frame
            ].layers[layer_index]
            if not layer.visible:
                continue

            draw_image = self._display_layer_image(
                layer, layer_index
            )
            if self.flip_horizontal:
                draw_image = draw_image.mirrored(True, False)
            painter.setOpacity(float(layer.opacity))
            painter.drawImage(destination, draw_image)

        painter.end()

        self._playback_frame_cache[cache_key] = image
        while (
            len(self._playback_frame_cache)
            > self._playback_cache_limit
        ):
            oldest_key = next(iter(self._playback_frame_cache))
            self._playback_frame_cache.pop(oldest_key, None)
        return image

    def draw_frame_direct(self, painter, target_rect, frame_index):
        """Draw the current frame directly to the widget, avoiding a full-size
        temporary composite on every brush mouse move."""
        layer_count = len(self.frames[frame_index].layers)
        for layer_index in range(layer_count):
            key_frame = self.resolve_key_frame(frame_index, layer_index)
            if key_frame is None:
                continue
            layer = self.frames[key_frame].layers[layer_index]
            if not layer.visible:
                continue
            draw_image = self._display_layer_image(layer, layer_index)
            if self.flip_horizontal:
                draw_image = draw_image.mirrored(True, False)
            painter.setOpacity(layer.opacity)
            painter.drawImage(target_rect, draw_image)
        painter.setOpacity(1.0)
    def wheelEvent(self,e):
        old=self.zoom; factor=1.15 if e.angleDelta().y()>0 else 1/1.15; self.zoom=max(.05,min(8.0,self.zoom*factor)); cur=e.position(); self.pan=cur-(cur-self.pan)*(self.zoom/old); self.update(); e.accept()
    def keyPressEvent(self,e):
        if (
            self._onion_interaction_mode is not None
            and e.key() == Qt.Key.Key_Escape
        ):
            self.cancel_onion_interaction(restore=True)
            e.accept()
            return

        if self.transform_active and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            window = self.window()
            if hasattr(window, "commit_transform_or_tween"):
                window.commit_transform_or_tween()
            else:
                self.commit_selection_transform()
            e.accept()
            return
        if self.transform_active and e.key() == Qt.Key.Key_Escape:
            window = self.window()
            if hasattr(window, "cancel_transform_or_tween"):
                window.cancel_transform_or_tween()
            else:
                self.cancel_selection_transform()
            e.accept()
            return
        if self.effective_tool() == "line" and self.line_curve_stage == 2:
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.commit_line_tool()
                e.accept()
                return
            if e.key() == Qt.Key.Key_Escape:
                self.line_start = None
                self.line_end = None
                self.line_control = None
                self.line_curve_stage = 0
                self.drawing = False
                self.update()
                e.accept()
                return
        super().keyPressEvent(e)
    def keyReleaseEvent(self,e):
        super().keyReleaseEvent(e)
    def _active_tool_panel(self):
        window = self.window()
        return window.tools if hasattr(window, "tools") else None

    def _prepare_draw_painter(self, painter, transparent=False):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.apply_selection_clip(painter)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Source
        )

    def _current_stroke_color(self):
        color = self.opaque_paint_color()
        return color, self.is_pseudo_transparent_color(color)

    def _line_preview_path(self):
        if self.line_start is None or self.line_end is None:
            return None
        path = QPainterPath(QPointF(self.line_start))
        if self.line_curve_stage == 2 and self.line_control is not None:
            path.quadTo(QPointF(self.line_control), QPointF(self.line_end))
        else:
            path.lineTo(QPointF(self.line_end))
        return path

    def _draw_tapered_path(
        self,
        painter,
        path,
        color,
        base_width,
        start_width=None,
        end_width=None,
        start_curve=1.0,
        end_curve=1.0,
    ):
        """入り抜き幅を変化させながら、非AA線分として描画する。"""
        try:
            length = max(1.0, float(path.length()))
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            log.debug("path.length() failed, using endpoint distance: %s", exc)
            length = max(
                1.0,
                math.hypot(
                    self.line_end.x() - self.line_start.x(),
                    self.line_end.y() - self.line_start.y(),
                ),
            )
        steps = max(2, min(4096, int(math.ceil(length * 1.5))))
        previous = path.pointAtPercent(0.0)
        for index in range(1, steps + 1):
            ratio = index / steps
            point = path.pointAtPercent(ratio)
            width = float(base_width)
            if start_width is not None and ratio <= 0.5:
                local = max(0.0, min(1.0, ratio * 2.0))
                eased = local ** max(0.05, float(start_curve))
                width = float(start_width) + (
                    float(base_width) - float(start_width)
                ) * eased
            elif end_width is not None and ratio >= 0.5:
                local = max(0.0, min(1.0, (ratio - 0.5) * 2.0))
                eased = local ** max(0.05, float(end_curve))
                width = float(base_width) + (
                    float(end_width) - float(base_width)
                ) * eased
            width = max(0.5, width)
            pen = QPen(
                color,
                width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            painter.drawLine(previous, point)
            previous = point

    def commit_line_tool(self):
        path = self._line_preview_path()
        if path is None:
            return
        self.ensure_editable_key()
        panel = self._active_tool_panel()
        base_width = max(0.5, float(self.pen_size))
        start_width = None
        end_width = None
        start_curve = 1.0
        end_curve = 1.0
        if panel is not None:
            if panel.line_taper_in.isChecked():
                start_width = (
                    panel.line_taper_in_size.value() / 2.0
                )
                start_curve = (
                    panel.line_taper_in_curve.value() / 100.0
                )
            if panel.line_taper_out.isChecked():
                end_width = (
                    panel.line_taper_out_size.value() / 2.0
                )
                end_curve = (
                    1.0
                    / max(
                        0.05,
                        panel.line_taper_out_curve.value() / 100.0,
                    )
                )

        source_color = self.paint_source_color()
        maximum_width = max(
            base_width,
            float(start_width or 0.0),
            float(end_width or 0.0),
        )
        undo_margin = int(math.ceil(maximum_width / 2.0)) + 3
        undo_rect = path.boundingRect().adjusted(
            -undo_margin, -undo_margin, undo_margin, undo_margin
        ).toAlignedRect()
        if not self.push_layer_region_undo(undo_rect):
            return
        base_image = self.active_layer.image.copy()
        overlay = QImage(
            self.active_layer.image.width(),
            self.active_layer.image.height(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        overlay.fill(Qt.GlobalColor.transparent)

        painter = QPainter(overlay)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            False,
        )
        self.apply_selection_clip(painter)
        if start_width is None and end_width is None:
            painter.setPen(
                QPen(
                    source_color,
                    base_width,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.drawPath(path)
        else:
            self._draw_tapered_path(
                painter,
                path,
                source_color,
                base_width,
                start_width,
                end_width,
                start_curve,
                end_curve,
            )
        painter.end()

        colors = self._blend_overlay_into_active_layer(
            overlay,
            QPoint(0, 0),
            base_image=base_image,
            opacity=self.pen_opacity,
            exact_colors=(source_color,),
        )
        self._emit_actual_paint_colors(colors)

        self.active_layer.has_content = True
        self.cellChanged.emit(
            self.current_frame,
            self.active_layer_index,
        )
        self.line_start = None
        self.line_end = None
        self.line_control = None
        self.line_curve_stage = 0
        self.drawing = False
        self.update()

    def _shape_rect(self):
        if self.shape_start is None or self.shape_end is None:
            return QRectF()
        start = QPointF(self.shape_start)
        end = QPointF(self.shape_end)
        panel = self._active_tool_panel()
        if panel is not None and panel.shape_lock_ratio.isChecked():
            dx = end.x() - start.x()
            dy = end.y() - start.y()
            size = max(abs(dx), abs(dy))
            end = QPointF(
                start.x() + (size if dx >= 0 else -size),
                start.y() + (size if dy >= 0 else -size),
            )
        return QRectF(start, end).normalized()

    def _shape_path(self):
        rect = self._shape_rect()
        if rect.isEmpty():
            return None
        panel = self._active_tool_panel()
        shape_type = panel.shape_type.currentText() if panel is not None else "多角形"
        path = QPainterPath()
        if shape_type == "楕円":
            path.addEllipse(rect)
            return path

        corners = max(3, int(panel.shape_corners.value()) if panel is not None else 4)
        center = rect.center()
        radius_x = rect.width() / 2.0
        radius_y = rect.height() / 2.0
        if corners == 4:
            # 4角はひし形ではなく、ドラッグ範囲に沿う□（長方形）にする。
            points = [
                QPointF(rect.left(), rect.top()),
                QPointF(rect.right(), rect.top()),
                QPointF(rect.right(), rect.bottom()),
                QPointF(rect.left(), rect.bottom()),
            ]
        else:
            points = []
            for index in range(corners):
                angle = -math.pi / 2.0 + (2.0 * math.pi * index / corners)
                points.append(
                    QPointF(
                        center.x() + math.cos(angle) * radius_x,
                        center.y() + math.sin(angle) * radius_y,
                    )
                )
        if points:
            path.moveTo(points[0])
            for point in points[1:]:
                path.lineTo(point)
            path.closeSubpath()
        return path

    def commit_shape_tool(self):
        path = self._shape_path()
        if path is None:
            self.shape_start = None
            self.shape_end = None
            self.drawing = False
            self.update()
            return

        self.ensure_editable_key()
        panel = self._active_tool_panel()
        use_split_colors = bool(
            panel is not None
            and panel.shape_sub_outline_main_fill.isChecked()
        )
        fill_inside = bool(
            panel is not None
            and panel.shape_fill_inside.isChecked()
        )
        outline_width = (
            panel.shape_outline_width.value() / 2.0
            if panel is not None
            else 1.0
        )
        undo_margin = int(math.ceil(float(outline_width) / 2.0)) + 3
        undo_rect = path.boundingRect().adjusted(
            -undo_margin, -undo_margin, undo_margin, undo_margin
        ).toAlignedRect()
        if not self.push_layer_region_undo(undo_rect):
            return

        if use_split_colors:
            outline_color = self.paint_source_color(
                self.sub_color
            )
            fill_color = self.paint_source_color(
                self.main_color
            )
        else:
            outline_color = self.paint_source_color()
            fill_color = QColor(outline_color)

        base_image = self.active_layer.image.copy()
        overlay = QImage(
            self.active_layer.image.width(),
            self.active_layer.image.height(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        overlay.fill(Qt.GlobalColor.transparent)

        painter = QPainter(overlay)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing,
            False,
        )
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            False,
        )
        self.apply_selection_clip(painter)
        if fill_inside:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill_color)
            painter.drawPath(path)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                outline_color,
                max(0.5, float(outline_width)),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawPath(path)
        painter.end()

        exact_shape_colors = (
            (outline_color, fill_color)
            if fill_inside
            else (outline_color,)
        )
        colors = self._blend_overlay_into_active_layer(
            overlay,
            QPoint(0, 0),
            base_image=base_image,
            opacity=self.pen_opacity,
            exact_colors=exact_shape_colors,
        )
        self._emit_actual_paint_colors(colors)

        self.active_layer.has_content = True
        self.cellChanged.emit(
            self.current_frame,
            self.active_layer_index,
        )
        self.shape_start = None
        self.shape_end = None
        self.drawing = False
        self.update()

    def fill_lasso_polygon(self, points):
        if len(points) < 3:
            return

        self.ensure_editable_key()
        window = self.window()
        outline_and_fill = bool(
            hasattr(window, "tools")
            and window.tools.lasso_main_outline_sub_fill.isChecked()
        )
        mask_only = not getattr(self, "mask_all_enabled", True)
        inside_boundary = bool(
            hasattr(window, "tools")
            and window.tools.lasso_inside_boundary.isChecked()
        )
        outline_width = (
            window.tools.lasso_outline_width.value() / 2.0
            if outline_and_fill and hasattr(window, "tools")
            else 0.0
        )

        polygon = QPolygonF(points)
        margin = int(math.ceil(outline_width / 2.0)) + 2
        rect = polygon.boundingRect().adjusted(
            -margin, -margin, margin, margin
        ).toAlignedRect().intersected(self.active_layer.image.rect())
        if rect.isEmpty():
            return
        self.push_layer_region_undo(rect)

        local_polygon = QPolygonF([
            QPointF(point.x() - rect.x(), point.y() - rect.y())
            for point in points
        ])
        overlay = QImage(
            rect.width(),
            rect.height(),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        overlay.fill(Qt.GlobalColor.transparent)

        fill_color = (
            self.paint_source_color(self.main_color)
            if outline_and_fill
            else self.paint_source_color()
        )

        painter = QPainter(overlay)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.apply_selection_clip(painter, rect.x(), rect.y())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill_color)
        painter.drawPolygon(local_polygon)

        line_color = None
        if outline_and_fill:
            line_color = self.paint_source_color(self.sub_color)
            line_pen = QPen(line_color)
            line_pen.setWidthF(max(0.5, float(outline_width)))
            line_pen.setStyle(Qt.PenStyle.SolidLine)
            line_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(line_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(local_polygon)
        painter.end()

        width, height = rect.width(), rect.height()
        source = self.active_layer.image.copy(rect).convertToFormat(
            QImage.Format.Format_RGBA8888
        )
        mask_image = overlay.convertToFormat(
            QImage.Format.Format_RGBA8888
        )

        source_ptr = source.bits()
        mask_ptr = mask_image.bits()
        try:
            source_ptr.setsize(source.sizeInBytes())
            mask_ptr.setsize(mask_image.sizeInBytes())
        except AttributeError:
            pass
        source_rows = np.frombuffer(
            source_ptr, dtype=np.uint8
        ).reshape((height, source.bytesPerLine()))
        mask_rows = np.frombuffer(
            mask_ptr, dtype=np.uint8
        ).reshape((height, mask_image.bytesPerLine()))
        source_pixels = source_rows[:, :width * 4].reshape(
            (height, width, 4)
        )
        mask_pixels = mask_rows[:, :width * 4].reshape(
            (height, width, 4)
        )

        if mask_only:
            selected_rgbs = self.selected_mask_colors()
            if not selected_rgbs:
                target_mask = np.zeros((height, width), dtype=bool)
            else:
                packed_source = (
                    (source_pixels[:, :, 0].astype(np.uint32) << 16)
                    | (source_pixels[:, :, 1].astype(np.uint32) << 8)
                    | source_pixels[:, :, 2].astype(np.uint32)
                )
                selected_values = np.fromiter(
                    ((r << 16) | (g << 8) | b for r, g, b in selected_rgbs),
                    dtype=np.uint32,
                    count=len(selected_rgbs),
                )
                target_mask = (
                    (source_pixels[:, :, 3] > 0)
                    & np.isin(packed_source, selected_values)
                )
                if self.background_mask_rgb in selected_rgbs:
                    target_mask |= (
                        (source_pixels[:, :, 3] == 0)
                        | np.all(
                            source_pixels[:, :, :3] == 255,
                            axis=2,
                        )
                    )
            mask_pixels[:, :, 3][~target_mask] = 0

        if inside_boundary:
            polygon_area = mask_pixels[:, :, 3] > 0
            passable = polygon_area & (
                (source_pixels[:, :, 3] == 0)
                | np.all(
                    source_pixels[:, :, :3] == 255,
                    axis=2,
                )
            )
            candidates = np.argwhere(passable)
            if candidates.size:
                center = np.mean(
                    np.array([[point.x() - rect.x(), point.y() - rect.y()]
                              for point in points]),
                    axis=0,
                )
                distances = (
                    (candidates[:, 1] - center[0]) ** 2
                    + (candidates[:, 0] - center[1]) ** 2
                )
                seed_y, seed_x = candidates[int(np.argmin(distances))]
                region = self._scanline_connected_region(
                    passable, (int(seed_x), int(seed_y))
                )
                mask_pixels[:, :, 3][~region] = 0
            else:
                mask_pixels[:, :, 3] = 0

        overlay = mask_image.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        exact_lasso_colors = (
            (fill_color, line_color)
            if line_color is not None
            else (fill_color,)
        )
        colors = self._blend_overlay_into_active_layer(
            overlay,
            rect.topLeft(),
            base_image=self.active_layer.image.copy(),
            opacity=self.pen_opacity,
            exact_colors=exact_lasso_colors,
        )

        self.active_layer.has_content = True
        self._emit_actual_paint_colors(colors)
        self.cellChanged.emit(
            self.current_frame,
            self.active_layer_index,
        )
        self.update()

    def mousePressEvent(self,e):
        self.setFocus()
        self.last_widget = e.position()
        self._brush_cursor_widget_pos = QPointF(e.position())
        self._brush_cursor_inside = self.inside(
            self.widget_to_canvas(e.position())
        )
        self.update_tool_cursor()
        if e.button() == Qt.MouseButton.MiddleButton:
            self.middle_hand = True
            self.drawing = True
            self.update_tool_cursor()
            return

        if (
            self._onion_interaction_mode is not None
            and e.button() in (
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.RightButton,
            )
        ):
            self._begin_onion_interaction_drag(
                e.position(),
                e.button(),
                e.modifiers(),
            )
            e.accept()
            return

        if e.button() == Qt.MouseButton.RightButton:
            p = self.widget_to_canvas(e.position())
            if self.inside(p):
                self.sample_color(p)
                e.accept()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return

        t = self.effective_tool()
        p = self.widget_to_canvas(e.position())
        if t == "hand":
            self.drawing = True
            self.update_tool_cursor()
            return
        if t in ("zoom", "rotate"):
            self.drawing = True
            self.last_widget = e.position()
            self.update_tool_cursor()
            return
        if self.transform_active:
            self.drawing = self.begin_transform_drag(p)
            if self.drawing:
                self.update()
            return
        if (
            not self.inside(p)
            and t not in ("lasso", "lasso_fill", "rect_select")
        ):
            return
        if t == "eyedropper":
            self.sample_color(
                p,
                include_canvas_background=(self.temp_tool == "eyedropper"),
            )
            return
        if t == "line":
            if self.line_curve_stage == 2:
                self.line_control = QPointF(p)
                self.commit_line_tool()
                return
            self.line_start = QPointF(p)
            self.line_end = QPointF(p)
            self.line_control = None
            self.line_curve_stage = 1
            self.drawing = True
            self.update()
            return
        if t == "shape":
            self.shape_start = QPointF(p)
            self.shape_end = QPointF(p)
            self.drawing = True
            self.update()
            return
        if t == "brush":
            self._brush_started_with_content = bool(self.active_layer.has_content)
            self.ensure_editable_key()
            self._begin_opaque_brush_stroke()
            self.drawing = True
            self._reset_brush_stabilizer(p)
            self._start_brush_follow_timer()
            self.last_canvas = QPointF(p)
            self.draw_line(p, p, 1.0)
            used_color = self.opaque_paint_color(
                opacity=self._brush_stroke_opacity
            )
            if not self.is_pseudo_transparent_color(used_color):
                self.colorUsed.emit(used_color)
        elif t == "bucket":
            self.ensure_editable_key()
            self.flood_fill(QPoint(int(p.x()), int(p.y())))
        elif t == "auto_select":
            self.auto_select_region(
                QPoint(int(p.x()), int(p.y())),
                e.modifiers(),
            )
        elif t in ("lasso", "lasso_fill"):
            self.ensure_editable_key()
            self.drawing = True
            self.lasso = [p]
        elif t == "rect_select":
            self.ensure_editable_key()
            self.drawing = True
            self.rect_start = p
            self.rect_end = p
        elif t == "mesh":
            self.ensure_editable_key()
            if not self.mesh_points:
                self.push_layer_undo()
                self.init_mesh()
            self.mesh_active = self.nearest_mesh(p)
            self.drawing = self.mesh_active >= 0
            self.update()
    def mouseMoveEvent(self,e):
        old_cursor = QPointF(self._brush_cursor_widget_pos)
        self._brush_cursor_widget_pos = QPointF(e.position())
        self._brush_cursor_inside = self.inside(
            self.widget_to_canvas(e.position())
        )
        self.update_tool_cursor()
        cursor_radius = max(
            8,
            int(math.ceil(float(self.pen_size) * max(self.zoom, 0.01) / 2.0)) + 4,
        )
        self.update(
            QRectF(old_cursor, old_cursor).adjusted(
                -cursor_radius, -cursor_radius,
                cursor_radius, cursor_radius,
            ).toAlignedRect()
        )
        self.update(
            QRectF(self._brush_cursor_widget_pos, self._brush_cursor_widget_pos).adjusted(
                -cursor_radius, -cursor_radius,
                cursor_radius, cursor_radius,
            ).toAlignedRect()
        )
        t = self.effective_tool()
        p = self.widget_to_canvas(e.position())

        if self._onion_interaction_mode is not None:
            if self._onion_interaction_dragging:
                self._update_onion_interaction_drag(e.position())
            return

        # 曲線の2段階目はボタンを押していなくても曲率をプレビューする。
        if t == "line" and self.line_curve_stage == 2 and not self.drawing:
            if self.inside(p):
                self.line_control = QPointF(p)
                self.update()
            return
        if not self.drawing:
            return
        if self.middle_hand or t == "hand":
            delta = e.position() - self.last_widget
            self.pan += delta
            self.last_widget = e.position()
            self.update()
            return
        if t == "zoom":
            delta = e.position().y() - self.last_widget.y()
            factor = math.pow(1.01, -delta)
            old_zoom = self.zoom
            self.zoom = max(0.05, min(8.0, self.zoom * factor))
            anchor = e.position()
            if old_zoom > 0:
                self.pan = anchor - (anchor - self.pan) * (self.zoom / old_zoom)
            self.last_widget = e.position()
            self.viewChanged.emit(float(self.zoom), float(self.rotation))
            self.update()
            return
        if t == "rotate":
            delta = e.position().x() - self.last_widget.x()
            self.rotation = ((self.rotation + delta * 0.35 + 180.0) % 360.0) - 180.0
            self.last_widget = e.position()
            self.viewChanged.emit(float(self.zoom), float(self.rotation))
            self.update()
            return
        if self.transform_active and self.drawing:
            self.update_transform_drag(p)
            return
        if t == "eyedropper" and self.inside(p):
            self.sample_color(
                p,
                include_canvas_background=(self.temp_tool == "eyedropper"),
            )
            return
        if t == "brush" and self.inside(p):
            self._draw_stabilized_brush_to(p, 1.0)
        elif t == "line" and self.line_curve_stage == 1 and self.inside(p):
            self.line_end = QPointF(p)
            self.update()
        elif t == "shape" and self.inside(p):
            self.shape_end = QPointF(p)
            self.update()
        elif t in ("lasso", "lasso_fill"):
            # キャンバス外から開始／通過しても軌跡を保持する。
            self.lasso.append(QPointF(p))
            self.update()
        elif t == "rect_select":
            # キャンバス外の座標も保持し、確定時に画像領域へクリップする。
            self.rect_end = QPointF(p)
            self.update()
        elif t == "mesh" and self.mesh_active >= 0:
            self.mesh_points[self.mesh_active] = p
            self.update()
    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.effective_tool() == "rotate":
            self.rotation = 0.0
            self.viewChanged.emit(float(self.zoom), 0.0)
            self.update()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def mouseReleaseEvent(self,e):
        if e.button() == Qt.MouseButton.MiddleButton:
            self.middle_hand = False
            self.drawing = False
            self.update_tool_cursor()
            return

        if (
            self._onion_interaction_mode is not None
            and e.button() in (
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.RightButton,
            )
        ):
            if self._onion_interaction_dragging:
                self._update_onion_interaction_drag(e.position())
            self.cancel_onion_interaction(restore=False)
            e.accept()
            return

        if e.button() != Qt.MouseButton.LeftButton:
            return

        t = self.effective_tool()
        if self.transform_active and self.drawing:
            self.end_transform_drag()
            self.drawing = False
            self.update()
            return

        if t == "brush":
            raw_release = self.widget_to_canvas(e.position())
            if (
                self.drawing
                and self.inside(raw_release)
                and self.last_canvas is not None
            ):
                self._finish_stabilized_brush(
                    raw_release,
                    1.0,
                )
            self._stop_brush_follow_timer()
            self._finish_opaque_brush_stroke()
            self._reset_brush_stabilizer()
            self._cell_structure_dirty = (
                not self._brush_started_with_content
                and bool(self.active_layer.has_content)
            )
            self.cellChanged.emit(self.current_frame, self.active_layer_index)
        elif t == "line" and self.line_curve_stage == 1:
            self.drawing = False
            panel = self._active_tool_panel()
            if panel is not None and panel.line_type.currentText() == "曲線":
                self.line_curve_stage = 2
                self.line_control = QPointF(
                    (self.line_start.x() + self.line_end.x()) / 2.0,
                    (self.line_start.y() + self.line_end.y()) / 2.0,
                )
                self.status_message.emit(
                    "マウスを動かしてカーブを調整し、クリックで確定します。Escで取消。"
                )
                self.update()
                return
            self.commit_line_tool()
            return
        elif t == "shape":
            self.commit_shape_tool()
            return
        elif t == "lasso" and len(self.lasso) >= 3:
            self.selection_polygon = list(self.lasso)
            self.selection_mask_override = None
            self.selection_outline_polygons = []
            self.selection_mask_rect = None
            self.lasso = []
            self.selectionChanged.emit()
        elif t == "lasso_fill" and len(self.lasso) >= 3:
            polygon = list(self.lasso)
            self.lasso = []
            self.fill_lasso_polygon(polygon)
        elif t == "rect_select" and self.rect_start is not None and self.rect_end is not None:
            x1, x2 = sorted((self.rect_start.x(), self.rect_end.x()))
            y1, y2 = sorted((self.rect_start.y(), self.rect_end.y()))
            self.selection_polygon = [
                QPointF(x1, y1), QPointF(x2, y1),
                QPointF(x2, y2), QPointF(x1, y2),
            ]
            self.selection_mask_override = None
            self.selection_outline_polygons = []
            self.selection_mask_rect = None
            self.rect_start = None
            self.rect_end = None
            self.selectionChanged.emit()
        elif t == "mesh" and self.mesh_active >= 0:
            self.mesh_active = -1
        self.drawing = False
        self.update_tool_cursor()
        self.update()
    def tabletEvent(self,e):
        self._brush_cursor_widget_pos = QPointF(e.position())
        self._brush_cursor_inside = self.inside(
            self.widget_to_canvas(e.position())
        )
        self.update_tool_cursor()
        self.update()

        # オニオン操作中はブラシ等の通常ツールを一切動作させない。
        # ペンを離した時点で操作を確定し、元のツールへ戻る。
        if self._onion_interaction_mode is not None:
            event_type = e.type()
            if event_type == e.Type.TabletPress:
                tablet_button = (
                    Qt.MouseButton.RightButton
                    if e.buttons() & Qt.MouseButton.RightButton
                    else Qt.MouseButton.LeftButton
                )
                self._begin_onion_interaction_drag(
                    e.position(),
                    tablet_button,
                    e.modifiers(),
                )
            elif (
                event_type == e.Type.TabletMove
                and self._onion_interaction_dragging
            ):
                self._update_onion_interaction_drag(e.position())
            elif event_type == e.Type.TabletRelease:
                if self._onion_interaction_dragging:
                    self._update_onion_interaction_drag(e.position())
                self.cancel_onion_interaction(restore=False)
            e.accept()
            return

        if self.effective_tool() != "brush":e.ignore();return
        p=self.widget_to_canvas(e.position()); pressure=max(.001,float(e.pressure()))
        if e.type()==e.Type.TabletPress and self.inside(p):
            self._brush_started_with_content = bool(self.active_layer.has_content)
            self.ensure_editable_key()
            self._begin_opaque_brush_stroke()
            self.drawing = True
            self._reset_brush_stabilizer(p)
            self._start_brush_follow_timer()
            self.last_canvas = QPointF(p)
            pressure = self._smooth_brush_pressure(pressure)
            self._last_brush_pressure = pressure
            self.draw_line(p, p, pressure)
            used_color = self.opaque_paint_color(
                opacity=self._brush_stroke_opacity
            )
            if not self.is_pseudo_transparent_color(used_color):
                self.colorUsed.emit(used_color)
            e.accept()
        elif e.type()==e.Type.TabletMove and self.drawing:
            if self.inside(p):
                pressure = self._smooth_brush_pressure(
                    pressure
                )
                self._last_brush_pressure = pressure
                self._draw_stabilized_brush_to(
                    p,
                    pressure,
                )
            e.accept()
        elif e.type()==e.Type.TabletRelease:
            if (
                self.drawing
                and self.inside(p)
                and self.last_canvas is not None
            ):
                self._finish_stabilized_brush(
                    p,
                    max(
                        0.001,
                        float(self._last_brush_pressure),
                    ),
                )
            self._stop_brush_follow_timer()
            self._finish_opaque_brush_stroke()
            self._reset_brush_stabilizer()
            self.drawing=False
            self._cell_structure_dirty = (
                not self._brush_started_with_content
                and bool(self.active_layer.has_content)
            )
            self.cellChanged.emit(self.current_frame,self.active_layer_index)
            e.accept()
        else:e.ignore()
    def _tinted_onion_image(
        self, frame_index, color, color_enabled=True, selected_colors_only=False
    ):
        signature = []
        for layer_index in range(len(self.frames[frame_index].layers)):
            key_frame = self.resolve_key_frame(frame_index, layer_index)
            if key_frame is None:
                signature.append(None)
                continue
            layer = self.frames[key_frame].layers[layer_index]
            signature.append((
                key_frame, int(layer.image.cacheKey()), bool(layer.visible),
                round(float(layer.opacity), 4),
                bool(layer.color_filter_enabled),
                tuple(layer.color_filter_rgb) if layer.color_filter_rgb is not None else None,
            ))
        visible_signature = (
            None if self.visible_color_rgbs is None
            else tuple(sorted(self.visible_color_rgbs))
        )
        selected_signature = tuple(sorted(self.selected_used_color_rgbs))
        cache_key = (
            int(frame_index), int(QColor(color).rgba()), bool(color_enabled),
            bool(selected_colors_only), bool(self.onion_all_layers),
            int(self.active_layer_index), selected_signature,
            tuple(signature), bool(self.silhouette_non_background),
            visible_signature,
        )
        cached = self._onion_cache.get(cache_key)
        if cached is not None:
            return cached

        if self.onion_all_layers:
            image = self.composite(frame_index, False)
        else:
            image = blank_image()
            key_frame = self.resolve_key_frame(frame_index, self.active_layer_index)
            if key_frame is not None:
                layer = self.frames[key_frame].layers[self.active_layer_index]
                if layer.visible:
                    painter = QPainter(image)
                    painter.setOpacity(max(0.0, min(1.0, float(layer.opacity))))
                    painter.drawImage(
                        0,
                        0,
                        self._display_layer_image(
                            layer,
                            self.active_layer_index,
                        ),
                    )
                    painter.end()
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = image.width(), image.height()
        ptr = image.bits()
        try:
            ptr.setsize(image.sizeInBytes())
        except AttributeError:
            pass
        rows = np.frombuffer(ptr, dtype=np.uint8).reshape(
            (height, image.bytesPerLine())
        )
        rgba = rows[:, :width * 4].reshape((height, width, 4)).copy()

        # 使用色パネルで選択されている親・子の色だけを残す。
        # 選択が空の場合は、チェックの意味を明確にするため全て透明にする。
        if selected_colors_only:
            selected = tuple(self.selected_used_color_rgbs)
            keep = np.zeros((height, width), dtype=bool)
            for rgb_value in selected:
                rgb_value = tuple(int(v) for v in rgb_value[:3])
                keep |= np.all(
                    rgba[:, :, :3] == np.asarray(rgb_value, dtype=np.uint8),
                    axis=2,
                )
            rgba[~keep, 3] = 0

        if color_enabled:
            # CLIP STUDIOのレイヤーカラーに近い処理:
            # 黒を指定色、白を白へ割り当て、中間調はその間を補間する。
            luminance = (
                rgba[:, :, 0].astype(np.float32) * 0.299
                + rgba[:, :, 1].astype(np.float32) * 0.587
                + rgba[:, :, 2].astype(np.float32) * 0.114
            ) / 255.0
            base = np.array(
                [QColor(color).red(), QColor(color).green(), QColor(color).blue()],
                dtype=np.float32,
            )
            mapped = base[None, None, :] * (1.0 - luminance[:, :, None])
            mapped += 255.0 * luminance[:, :, None]
            rgba[:, :, :3] = np.clip(mapped, 0, 255).astype(np.uint8)

        result = QImage(
            rgba.data, width, height, rgba.strides[0],
            QImage.Format.Format_RGBA8888,
        ).copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        if len(self._onion_cache) >= 48:
            self._onion_cache.pop(next(iter(self._onion_cache)))
        self._onion_cache[cache_key] = result
        return result

    def onion_shift_values(self, direction):
        if int(direction) < 0:
            return (
                float(self.onion_previous_shift_x),
                float(self.onion_previous_shift_y),
                float(self.onion_previous_rotation),
            )
        return (
            float(self.onion_next_shift_x),
            float(self.onion_next_shift_y),
            float(self.onion_next_rotation),
        )

    def onion_transform_values(self, direction):
        shift_x, shift_y, rotation = self.onion_shift_values(
            direction
        )
        scale = (
            float(self.onion_previous_scale)
            if int(direction) < 0
            else float(self.onion_next_scale)
        )
        return (
            shift_x,
            shift_y,
            rotation,
            max(0.01, scale / 100.0),
        )

    def _onion_frame_identity(self, frame_index):
        """指定位置で表示される絵を、保持フレームと区別せず識別する。"""
        frame_index = int(frame_index)
        if not (0 <= frame_index < len(self.frames)):
            return None

        if not self.onion_all_layers:
            layer_index = int(self.active_layer_index)
            key_frame = self.resolve_key_frame(frame_index, layer_index)
            if key_frame is None:
                return None
            layer = self.frames[key_frame].layers[layer_index]
            return (layer_index, int(key_frame)) if layer.visible else None

        identity = []
        layer_count = len(self.frames[frame_index].layers)
        for layer_index in range(layer_count):
            key_frame = self.resolve_key_frame(frame_index, layer_index)
            if key_frame is None:
                continue
            layer = self.frames[key_frame].layers[layer_index]
            if layer.visible:
                identity.append((layer_index, int(key_frame)))
        return tuple(identity) if identity else None

    def _onion_frame_indices(self, direction, count):
        """空セルと同じ絵の保持区間を飛ばし、前後の実画像位置を返す。"""
        direction = -1 if int(direction) < 0 else 1
        count = max(0, int(count))
        if count <= 0:
            return []

        indices = []
        previous_identity = self._onion_frame_identity(
            self.current_frame
        )
        frame_index = int(self.current_frame) + direction
        while 0 <= frame_index < len(self.frames) and len(indices) < count:
            identity = self._onion_frame_identity(frame_index)
            if identity is not None and identity != previous_identity:
                indices.append(frame_index)
                previous_identity = identity
            frame_index += direction
        return indices

    def _draw_onion_range(
        self, painter, work_rect, direction, count, opacity, color,
        color_enabled=True, selected_colors_only=False
    ):
        count = max(0, int(count))
        shift_x, shift_y, local_rotation, local_scale = (
            self.onion_transform_values(direction)
        )
        levels = (
            list(self.onion_previous_levels)
            if int(direction) < 0
            else list(self.onion_next_levels)
        )
        transform_center = work_rect.center()
        outline_rect = QRectF(self.canvas_rect()).adjusted(
            -0.5, -0.5, 0.5, 0.5
        )
        frame_indices = self._onion_frame_indices(direction, count)

        # 遠い絵から描き、近い絵を手前へ重ねる。
        for distance, frame_index in reversed(list(enumerate(
            frame_indices,
            start=1,
        ))):

            onion = self._tinted_onion_image(
                frame_index,
                color,
                color_enabled,
                selected_colors_only,
            )
            if self.flip_horizontal:
                onion = onion.mirrored(True, False)

            if distance - 1 < len(levels):
                level = max(
                    0.0,
                    min(1.0, float(levels[distance - 1]) / 100.0),
                )
            else:
                level = max(
                    0.0,
                    min(
                        1.0,
                        1.0
                        - ((distance - 1) / max(1, count)) * 0.55,
                    ),
                )
            draw_opacity = max(
                0.0,
                min(1.0, float(opacity) * level),
            )

            if draw_opacity > 0.0:
                painter.save()
                painter.translate(
                    transform_center.x()
                    + shift_x * float(self.zoom),
                    transform_center.y()
                    + shift_y * float(self.zoom),
                )
                painter.rotate(local_rotation)
                painter.scale(local_scale, local_scale)
                painter.translate(
                    -transform_center.x(),
                    -transform_center.y(),
                )
                painter.setOpacity(draw_opacity)
                painter.drawImage(work_rect, onion)
                painter.restore()

        if frame_indices:
            # 外周は全体濃度・コマ別濃度・色適用設定とは分離し、
            # 常に100%不透明度の1px線で表示する。
            painter.save()
            painter.translate(
                transform_center.x()
                + shift_x * float(self.zoom),
                transform_center.y()
                + shift_y * float(self.zoom),
            )
            painter.rotate(local_rotation)
            painter.scale(local_scale, local_scale)
            painter.translate(
                -transform_center.x(),
                -transform_center.y(),
            )
            painter.setOpacity(1.0)
            outline_color = QColor(color)
            outline_color.setAlpha(255)
            outline_pen = QPen(outline_color, 1.0)
            outline_pen.setCosmetic(True)
            outline_pen.setStyle(Qt.PenStyle.SolidLine)
            painter.setPen(outline_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(outline_rect)
            painter.restore()

    def paintEvent(self,e):
        p=QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.fillRect(self.rect(),QColor(42,42,42)); wr=self.work_rect(); cr=self.canvas_rect()
        p.save(); c=wr.center(); p.translate(c); p.rotate(self.rotation); p.scale(max(0.0001,float(self.onion_tu_tb_scale)),max(0.0001,float(self.onion_tu_tb_scale))); p.translate(-c)
        p.fillRect(wr,QColor(72,72,72))
        p.fillRect(cr, self.transparent_display_color)
        p.setOpacity(1)
        # 再生中は表示領域サイズの合成キャッシュを使用し、
        # 大画像をレイヤーごとに毎フレーム再縮小しない。
        if self._playback_active:
            playback_image = self.playback_frame_image(
                self.current_frame,
                wr,
            )
            if playback_image is not None:
                p.drawImage(wr, playback_image)
        else:
            # 編集時は元解像度のレイヤーを直接描画する。
            self.draw_frame_direct(p, wr, self.current_frame)
        # オニオンスキンは現在コマより手前へ重ねる。
        if self.onion_skin:
            self._draw_onion_range(
                p, wr, -1, self.onion_previous_count,
                self.onion_previous_opacity, self.onion_previous_color,
                self.onion_previous_color_enabled,
                self.onion_selected_colors_only,
            )
            self._draw_onion_range(
                p, wr, 1, self.onion_next_count,
                self.onion_next_opacity, self.onion_next_color,
                self.onion_next_color_enabled,
                self.onion_selected_colors_only,
            )
        p.setOpacity(1)
        if self.transform_active and self.transform_points:
            quality_requested = (
                bool(getattr(self, "transform_quality_active", False))
                and self.transform_drag_kind is None
                and not self._transform_line_adjusting
            )
            quality_ready = (
                quality_requested
                and self._tp_preview_cache_image is not None
                and not self._tp_preview_progress_busy
            )
            if quality_requested and not quality_ready:
                if not self._tp_preview_progress_busy:
                    self.request_quality_preview_counter()
                # Dragging/repainting stays responsive while the scheduled
                # TP_mask job displays its counter popup.
                transform_preview, _ = self.transform_preview_image(
                    quality=False,
                    preview_only=True,
                )
            else:
                transform_preview, _ = self.transform_preview_image(
                    quality=quality_ready,
                    preview_only=True,
                )
            if transform_preview is not None:
                transform_preview = self._pseudo_transparent_display_image(
                    transform_preview
                )
                transform_preview = (
                    transform_preview.mirrored(True, False)
                    if self.flip_horizontal else transform_preview
                )
                p.setOpacity(.85)
                p.drawImage(wr, transform_preview)
                p.setOpacity(1)
        p.setPen(QPen(QColor(15,15,15),1));p.drawRect(cr);p.restore()
        # ライン／図形の確定前プレビュー
        if self.line_start is not None and self.line_end is not None:
            preview_color = self.opaque_paint_color()
            preview_color.setAlpha(220)
            p.setPen(QPen(
                preview_color,
                max(0.5, float(self.pen_size) * self.zoom),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            ))
            line_path = QPainterPath(self.canvas_to_widget(self.line_start))
            if self.line_curve_stage == 2 and self.line_control is not None:
                line_path.quadTo(
                    self.canvas_to_widget(self.line_control),
                    self.canvas_to_widget(self.line_end),
                )
            else:
                line_path.lineTo(self.canvas_to_widget(self.line_end))
            p.drawPath(line_path)

        if self.shape_start is not None and self.shape_end is not None:
            rect_preview = self._shape_rect()
            panel = self._active_tool_panel()
            shape_type = panel.shape_type.currentText() if panel is not None else "多角形"
            preview_points = []
            if not rect_preview.isEmpty():
                center = rect_preview.center()
                if shape_type == "楕円":
                    point_count = 64
                    for index in range(point_count):
                        angle = -math.pi / 2.0 + 2.0 * math.pi * index / point_count
                        canvas_point = QPointF(
                            center.x() + math.cos(angle) * rect_preview.width() / 2.0,
                            center.y() + math.sin(angle) * rect_preview.height() / 2.0,
                        )
                        preview_points.append(self.canvas_to_widget(canvas_point))
                else:
                    point_count = max(
                        3,
                        int(panel.shape_corners.value()) if panel is not None else 4,
                    )
                    if point_count == 4:
                        canvas_points = [
                            QPointF(rect_preview.left(), rect_preview.top()),
                            QPointF(rect_preview.right(), rect_preview.top()),
                            QPointF(rect_preview.right(), rect_preview.bottom()),
                            QPointF(rect_preview.left(), rect_preview.bottom()),
                        ]
                        preview_points.extend(
                            self.canvas_to_widget(point) for point in canvas_points
                        )
                    else:
                        for index in range(point_count):
                            angle = -math.pi / 2.0 + 2.0 * math.pi * index / point_count
                            canvas_point = QPointF(
                                center.x() + math.cos(angle) * rect_preview.width() / 2.0,
                                center.y() + math.sin(angle) * rect_preview.height() / 2.0,
                            )
                            preview_points.append(self.canvas_to_widget(canvas_point))
            if preview_points:
                preview_color = self.opaque_paint_color()
                preview_color.setAlpha(220)
                width = (
                    panel.shape_outline_width.value() / 2.0
                    if panel is not None else 1.0
                )
                p.setPen(QPen(
                    preview_color,
                    max(0.5, float(width) * self.zoom),
                    Qt.PenStyle.SolidLine,
                ))
                p.setBrush(Qt.BrushStyle.NoBrush)
                preview_path = QPainterPath(preview_points[0])
                for point in preview_points[1:]:
                    preview_path.lineTo(point)
                preview_path.closeSubpath()
                p.drawPath(preview_path)

        # overlays are drawn in widget coordinates so rotation is applied only once
        preview_selection = self.lasso
        if preview_selection:
            p.setPen(QPen(QColor(255,70,70),2,Qt.PenStyle.DashLine));
            pts=[self.canvas_to_widget(q) for q in preview_selection]
            for a,b in zip(pts,pts[1:]):p.drawLine(a,b)
        elif self.selection_polygon:
            outlines = (
                self.selection_outline_polygons
                if self.selection_outline_polygons
                else [self.selection_polygon]
            )
            p.save()
            p.setOpacity(self._selection_fade_opacity)
            p.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Difference
            )
            p.setPen(QPen(QColor(255,255,255),2,Qt.PenStyle.DashLine))
            for outline in outlines:
                display_outline = (
                    self.transformed_selection_outline(outline)
                    if self.transform_active else outline
                )
                pts = [
                    self.canvas_to_widget(point)
                    for point in display_outline
                ]
                for first, second in zip(pts, pts[1:]):
                    p.drawLine(first, second)
                if len(pts) > 2:
                    p.drawLine(pts[-1], pts[0])
            p.restore()
        if self.rect_start is not None and self.rect_end is not None:
            a=self.canvas_to_widget(self.rect_start); b=self.canvas_to_widget(self.rect_end)
            p.setPen(QPen(QColor(255,70,70),2,Qt.PenStyle.DashLine))
            p.drawRect(QRectF(a,b).normalized())
        if self.transform_active and self.transform_points:
            p.setPen(QPen(QColor(0,220,255),2))
            points=[self.canvas_to_widget(q) for q in self.transform_points]
            if self.transform_mode=="mesh":
                cols=max(2,int(getattr(self, "transform_mesh_cols", 4)))
                rows=max(2,int(getattr(self, "transform_mesh_rows", 4)))
                if len(points)==cols*rows:
                    for gy in range(rows):
                        for gx in range(cols-1):
                            p.drawLine(points[gy*cols+gx],points[gy*cols+gx+1])
                    for gx in range(cols):
                        for gy in range(rows-1):
                            p.drawLine(points[gy*cols+gx],points[(gy+1)*cols+gx])
            elif len(points)==4:
                for index in range(4):
                    p.drawLine(points[index],points[(index+1)%4])
            rotation_handle=self.transform_rotation_handle()
            if rotation_handle is not None:
                rotation_widget=self.canvas_to_widget(rotation_handle)
                if self.transform_mode == "mesh":
                    cols=max(2,int(getattr(self, "transform_mesh_cols", 4)))
                    left=self.transform_points[0]
                    right=self.transform_points[cols-1]
                else:
                    left=self.transform_points[0]
                    right=self.transform_points[1]
                top_mid=self.canvas_to_widget(QPointF(
                    (left.x()+right.x())/2,
                    (left.y()+right.y())/2,
                ))
                p.drawLine(top_mid,rotation_widget)
                p.setBrush(QColor(255,220,70))
                p.drawEllipse(rotation_widget,6,6)
            p.setBrush(QColor("white"))
            for point in points:p.drawEllipse(point,5,5)
        if self.mesh_points:
            p.setPen(QPen(QColor(0,210,255),1)); g=self.mesh_grid
            for gy in range(g):
                for gx in range(g-1):p.drawLine(self.canvas_to_widget(self.mesh_points[gy*g+gx]),self.canvas_to_widget(self.mesh_points[gy*g+gx+1]))
            for gx in range(g):
                for gy in range(g-1):p.drawLine(self.canvas_to_widget(self.mesh_points[gy*g+gx]),self.canvas_to_widget(self.mesh_points[(gy+1)*g+gx]))
            p.setBrush(QColor("white"));
            for q in self.mesh_points:
                wq=self.canvas_to_widget(q);p.drawEllipse(wq,5,5)
        if (
            self.effective_tool() in ("brush", "line")
            and self._brush_cursor_inside
            and self.rect().contains(self._brush_cursor_widget_pos.toPoint())
        ):
            diameter = max(1.0, float(self.pen_size) * max(self.zoom, 0.01))
            center = QPointF(self._brush_cursor_widget_pos)
            ring = QRectF(
                center.x() - diameter / 2.0,
                center.y() - diameter / 2.0,
                diameter,
                diameter,
            )
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            # 白を Difference 合成すると、円の下にある色がネガ反転する。
            p.save()
            p.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Difference
            )
            cursor_pen = QPen(QColor(255, 255, 255, 255), 1.0)
            cursor_pen.setCosmetic(True)
            p.setPen(cursor_pen)
            p.drawEllipse(ring)
            p.restore()

        tool_labels = dict(ToolPanel.TOOLS)
        tool_labels.update({
            "hand": "ハンド",
            "zoom": "拡大縮小",
            "rotate": "回転",
            "eyedropper": "スポイト",
        })
        tool_name = tool_labels.get(self.effective_tool(), str(self.effective_tool()))
        p.setPen(QColor("white"))
        p.drawText(
            10, self.height()-10,
            f"{tool_name} | {self.zoom*100:.0f}% | Frame {self.current_frame+1}"
        )
        p.end()
