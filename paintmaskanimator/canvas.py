"""The ``PaintCanvas`` widget — the drawing surface and its irreducible core.

Architecture (see also the domain modules ``document``/``imaging``/``geometry``/
``colors``/``color_ops``/``project_io``): cohesive method clusters were extracted
into ``canvas_<topic>.py`` as ``*Mixin`` classes and composed onto ``PaintCanvas``
below. What remains in this file is the irreducible widget core — ``__init__``,
``paintEvent``, the document-backed properties (``frames``/``current_frame``/
``active_layer_index``), view transforms (zoom/pan/canvas<->widget mapping),
colour resolution for painting, layer compositing, and the delegating
class-method surface that keeps ``imaging``/``color_reduction`` reachable through
``PaintCanvas``. Timeline structure editing, the pixel-writing tools and the
free-transform geometry live in ``canvas_timeline_ops.py``,
``canvas_paint_tools.py`` and ``canvas_transform_geometry.py``.

MRO note: ``InputEventMixin`` provides Qt event overrides (``mousePressEvent`` &c.)
and therefore MUST precede ``QWidget`` in the base list, or the overrides lose to
``QWidget``'s defaults. The reverse hazard applies to *non*-virtual Qt names: a
plain method on a mixin that shares its name with a ``QWidget`` method (e.g.
``connect``, ``scroll``) is shadowed by Qt's own during attribute lookup, so a
mixin must never reuse one. Type-only member declarations for attributes set in
``__init__`` (invisible to a checker inspecting one mixin in isolation) live in
``_canvas_members.py``; a genuinely undefined name still surfaces there.
"""
from .common import *  # noqa: F401,F403
from . import constants
from . import color_ops, colors, geometry, imaging
from .document import Document
from .toolpanel import ToolPanel
from .utils import blank_image, workspace_size
from .logging_setup import get_logger
from .canvas_brush_stabilizer import BrushStabilizerMixin
from .canvas_image_import import ImageImportMixin
from .canvas_input_events import InputEventMixin
from .canvas_key_frame import KeyFrameMixin
from .canvas_onion_interaction import OnionInteractionMixin
from .canvas_onion_render import OnionRenderMixin
from .canvas_paint_tools import PaintToolsMixin
from .canvas_playback import PlaybackMixin
from .canvas_pseudo_transparency import PseudoTransparencyMixin
from .canvas_selection import SelectionMixin
from .canvas_stroke_display import StrokeDisplayMixin
from .canvas_timeline_ops import TimelineStructureMixin
from .canvas_transform_geometry import TransformGeometryMixin
from .canvas_transform_mask import TransformMaskMixin
from .canvas_undo import UndoMixin
from .undo_entries import (
    DocUndo,
    LayerBatchUndo,
    LayerInsertUndo,
    LayerRegionUndo,
    LayerRemoveUndo,
    LayerTilesUndo,
    LayerUndo,
    PaletteStateUndo,
    TweenBatchUndo,
)

log = get_logger(__name__)


class PaintCanvas(
    ImageImportMixin, OnionInteractionMixin, OnionRenderMixin, SelectionMixin,
    TransformMaskMixin, TransformGeometryMixin, PaintToolsMixin,
    TimelineStructureMixin, BrushStabilizerMixin, StrokeDisplayMixin,
    InputEventMixin, UndoMixin, PlaybackMixin, PseudoTransparencyMixin,
    KeyFrameMixin, QWidget
):
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
        # 使用色の親子プレビュー：{子rgb: 親rgb}。表示時のみ子を親色へ塗り替える（非破壊）。
        self.preview_color_remap={}
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
        self.transform_quality=True
        self.transform_quality_active=False
        self.transform_line_threshold=96
        self.transform_tp_line_colors=()
        # TP_mask v0.7 compatible data.  The selected image is reduced to exact
        # colors and stored as one label id per pixel; the deformation gives
        # each output pixel to whichever color covers most of it, so no
        # interpolation colors are ever produced.
        self.transform_tp_palette=[]
        self.transform_tp_masks=[]
        self.transform_tp_line_masks=[]
        self.transform_tp_prepared_preview=None
        self._tp_label_image=None
        self._tp_label_colors=None
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
        # 使用色パネルへの参照。MainWindow が初期化時に差し込む。
        # palette_state のUndo/Redoでパネル状態を復元するために使う。
        self._palette=None
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
    def current_state_for(self, entry):
        """Capture the inverse of ``entry``: the state undoing it would replace.

        Returns ``None`` when the entry targets a cell that no longer exists
        (timeline deletions leave stale history behind), telling the caller to
        drop it instead of applying it.
        """
        def _cell_exists(fi, li):
            return (
                0 <= fi < len(self.frames)
                and 0 <= li < len(self.frames[fi].layers)
            )

        if isinstance(entry, DocUndo):
            return DocUndo(self.document_snapshot())
        if isinstance(entry, PaletteStateUndo):
            palette = getattr(self, "_palette", None)
            if palette is None:
                return None
            # 反対側スタックには「現在のパレット状態」を積む。ラベルは対称。
            return PaletteStateUndo(palette.capture_history_state(), entry.label)
        if isinstance(entry, LayerRegionUndo):
            if not _cell_exists(entry.frame, entry.layer):
                return None
            layer = self.frames[entry.frame].layers[entry.layer]
            return LayerRegionUndo(
                entry.frame,
                entry.layer,
                QRect(entry.rect),
                layer.image.copy(entry.rect),
                bool(layer.has_content),
            )
        if isinstance(entry, LayerTilesUndo):
            if not _cell_exists(entry.frame, entry.layer):
                return None
            layer = self.frames[entry.frame].layers[entry.layer]
            return LayerTilesUndo(
                entry.frame,
                entry.layer,
                tuple(
                    (QRect(rect), layer.image.copy(QRect(rect)))
                    for rect, _image in entry.tiles
                ),
                bool(layer.has_content),
            )
        if isinstance(entry, LayerBatchUndo):
            li = entry.layer
            current = [
                (fi, self.frames[fi].layers[li].image.copy(),
                 self.frames[fi].layers[li].has_content)
                for fi, _img, _hc in entry.cells
                if _cell_exists(fi, li)
            ]
            return LayerBatchUndo(li, current) if current else None
        if isinstance(entry, LayerRemoveUndo):
            index = entry.index
            stored = [
                frame.layers[index].clone()
                for frame in self.frames
                if 0 <= index < len(frame.layers)
            ]
            return LayerInsertUndo(
                int(index),
                stored,
                int(self.active_layer_index),
            )
        if isinstance(entry, LayerInsertUndo):
            return LayerRemoveUndo(
                int(entry.index),
                int(self.active_layer_index),
            )
        if isinstance(entry, TweenBatchUndo):
            li = entry.layer
            current = []
            for fi in range(int(entry.start), min(int(entry.end) + 1, len(self.frames))):
                if 0 <= li < len(self.frames[fi].layers):
                    layer = self.frames[fi].layers[li]
                    current.append((
                        fi,
                        layer.image.copy(),
                        bool(layer.has_content),
                        int(layer.exposure),
                    ))
            return TweenBatchUndo(
                int(li),
                len(self.frames),
                int(entry.start),
                int(entry.end),
                current,
            )
        if isinstance(entry, LayerUndo):
            if not _cell_exists(entry.frame, entry.layer):
                return None
            layer = self.frames[entry.frame].layers[entry.layer]
            return LayerUndo(
                entry.frame,
                entry.layer,
                layer.image.copy(),
                layer.has_content,
            )
        log.warning("unknown undo entry type: %r", type(entry).__name__)
        return None

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


    @staticmethod
    def _normalized_angle(angle):
        return geometry.normalized_angle(angle)

    @staticmethod
    def _rotated_vector(x, y, angle_degrees):
        return geometry.rotated_vector(x, y, angle_degrees)


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


    def effective_tool(self): return self.temp_tool or self.tool
    def work_rect(self):
        w,h=workspace_size(); return QRectF(self.pan.x(),self.pan.y(),w*self.zoom,h*self.zoom)

    def set_zoom_around_canvas_center(self, zoom):
        """Change zoom without moving the canvas center on screen."""
        old_zoom = max(0.0001, float(self.zoom))
        new_zoom = max(0.05, min(8.0, float(zoom)))
        width, height = workspace_size()
        canvas_center = QPointF(width / 2.0, height / 2.0)
        screen_center = self.pan + canvas_center * old_zoom
        self.zoom = new_zoom
        self.pan = screen_center - canvas_center * new_zoom

    def center_canvas(self):
        """Place the workspace center at the center of the canvas widget."""
        width, height = workspace_size()
        self.pan = QPointF(
            (self.width() - width * self.zoom) / 2.0,
            (self.height() - height * self.zoom) / 2.0,
        )
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
            if mask is None:
                return
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
            if display_source is None:
                continue
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


    def silhouette_layer_image(self, layer, apply_palette_filter=True):
        base = self.filtered_layer_image(
            layer,
            apply_palette_filter,
        )
        if not layer.is_paper:
            base = self._pseudo_transparent_display_image(base)
        if base is None:
            return QImage()
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
        ptr = imaging.qimage_buffer(rgba)
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

    def _color_index_for_image(self, image):
        """Return the cached RGBA/pixel index used by palette filtering."""
        try:
            image_key = int(image.cacheKey())
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.debug("cacheKey() unavailable, using id() fallback: %s", exc)
            image_key = id(image)
        width, height = image.width(), image.height()
        index_key = (image_key, width, height)
        indexed = self._color_index_cache.get(index_key)
        if indexed is not None:
            return indexed

        base_rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        if width <= 0 or height <= 0:
            return base_rgba, None, None
        base_ptr = imaging.qimage_buffer(base_rgba)
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
        return indexed

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
        # 使用色の親子プレビュー：表示時のみ子色を親色へ塗り替える（非破壊）。
        remap = (
            dict(getattr(self, "preview_color_remap", None) or {})
            if apply_palette_filter else {}
        )
        if visible is None and legacy_rgb is None and not remap:
            return layer.image
        rgb = legacy_rgb
        try:
            image_key = int(layer.image.cacheKey())
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.debug("cacheKey() unavailable, using id() fallback: %s", exc)
            image_key = id(layer.image)
        width, height = layer.image.width(), layer.image.height()
        visible_key = None if visible is None else tuple(sorted(visible))
        remap_key = tuple(sorted(remap.items())) if remap else None
        key = (image_key, width, height, rgb, visible_key, remap_key)
        cached = self._color_filter_cache.get(key)
        if cached is not None:
            return cached

        base_rgba, packed, opaque = self._color_index_for_image(layer.image)
        if packed is None or opaque is None:
            return base_rgba
        rgba = base_rgba.copy()
        ptr = imaging.qimage_buffer(rgba)
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

        # 親子プレビュー：残っている子色ピクセルを親色へ塗り替える。
        for child, parent in remap.items():
            child_packed = (
                (int(child[0]) << 16) | (int(child[1]) << 8) | int(child[2])
            )
            mask = keep & (packed == child_packed)
            if mask.any():
                pixels[:, :, 0][mask] = int(parent[0])
                pixels[:, :, 1][mask] = int(parent[1])
                pixels[:, :, 2][mask] = int(parent[2])

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
            if draw_image is None:
                continue
            if self.flip_horizontal:
                draw_image = draw_image.mirrored(True, False)
            painter.setOpacity(layer.opacity)
            painter.drawImage(target_rect, draw_image)
        painter.setOpacity(1.0)
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
                if quality_ready and self.transform_original_layer is not None:
                    transform_preview = self._merge_quality_transform(
                        self.transform_original_layer,
                        transform_preview,
                    )
                transform_preview = self._pseudo_transparent_display_image(
                    transform_preview
                )
                if transform_preview is None:
                    transform_preview = QImage()
                transform_preview = (
                    transform_preview.mirrored(True, False)
                    if self.flip_horizontal else transform_preview
                )
                p.setOpacity(1 if quality_ready else .85)
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
