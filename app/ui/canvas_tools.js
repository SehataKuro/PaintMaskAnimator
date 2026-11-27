// キャンバスの上の操作をツールごとに振り分ける（Qt 版の canvas_input_events.py・
// main_window_input.py・main_window_tool_hold.py）。
//
// - 押している間のツール：Space＝ハンド、Shift＋Space＝回転、Alt＝スポイト、
//   拡大縮小は macOS が Alt＋Space、それ以外は Ctrl＋Space。Space を短く押して
//   離したら（0.3 秒未満）再生／停止。
// - ブラシの線は app.js（筆圧の経路）が描く。ここはブラシ以外のツールと、ブラシの
//   Shift＋クリックの直線、Ctrl＋Alt のドラッグでのサイズ変更を受け持つ。
// - ツールのキー（P・G など）と、選択範囲・描画色のショートカット。
import { toolStore, el, openDialog, selectTool, nextInGroup, groupOf, pickColor, holdKeys, refreshShortcuts } from "./tools.js";
import { toolFlags, notify, openPressureDialog } from "./tool_properties.js";
// 塗りの操作（paint_ops.js）：バケツのなぞり塗り・串刺し塗り・色置換・線整え。
import { bucketDown, bucketMove, bucketUp, skewerClick, replaceColor, dustApply } from "./paint_ops.js";

const { invoke } = window.__TAURI__.core;

const IS_MAC = /Mac/.test(navigator.platform);
const SPACE_TAP_MS = 300;
const DRAW_TOOLS = ["brush", "bucket", "line", "shape", "lasso_fill", "replace_color", "skewer_fill"];

export function createCanvasTools({ canvas, viewer, overlay, applyRect }) {
  // 押しているキー（Qt のショートカットの字：Space・Shift・Alt・Ctrl）。
  const held = new Set();
  let holdTool = null;
  let spaceTap = null;
  // 押している間のツールのキー（一時切り替え）。
  let toolHold = null;
  // 今の操作（押してから離すまで）。
  let active = null;
  // 曲線の2段目（始点と終点を決めたあと、次のクリックで弓なりを決める）。
  let curve = null;
  // ブラシの直線の始点（前の線の終わり）。
  let brushAnchor = null;
  let hover = null;
  let previousColorMode = "main";
  const api = { enabled: true, get active() { return !!active; } };

  const settings = () => toolStore.settings;

  /// 今のツール（押している間のツールが先。図形の直線・曲線はライン）。
  function effectiveTool() {
    if (holdTool) return holdTool;
    const s = settings();
    if (!s) return "brush";
    return toolFlags(s.tool, s).isLine ? "line" : s.tool;
  }

  function updateHold() {
    // キーはショートカット設定の「キャンバス操作」（actions.rs の a_hold_*）。
    const keys = holdKeys();
    const bindings = [["zoom", keys.a_hold_zoom], ["rotate", keys.a_hold_rotate], ["hand", keys.a_hold_hand], ["eyedropper", keys.a_hold_eyedropper]];
    let best = null;
    bindings.forEach(([tool, keys], priority) => {
      if (!keys?.length || !keys.every((k) => held.has(k))) return;
      const score = [keys.length, -priority];
      if (!best || score[0] > best[0][0] || (score[0] === best[0][0] && score[1] > best[0][1])) best = [score, tool];
    });
    let next = best?.[1] ?? null;
    // Ctrl＋Alt はブラシ・ラインのサイズ変更（押している間のツールにしない）。
    if (held.has("Ctrl") && held.has("Alt") && ["brush", "line"].includes(settings()?.tool)) next = null;
    if (next !== holdTool) {
      holdTool = next;
      active = null;
      if (!curve) clearPreview();
      updateCursor();
    }
  }

  function updateCursor() {
    const t = effectiveTool();
    const cursor = {
      hand: active ? "grabbing" : "grab",
      zoom: "zoom-in",
      rotate: "ew-resize",
      eyedropper: "copy",
    }[t] ?? "crosshair";
    canvas.style.cursor = cursor;
    showBrushCursor();
  }

  function showBrushCursor() {
    const s = settings();
    const t = effectiveTool();
    if (!hover || !s || !(t === "brush" || t === "line")) {
      overlay.setCursor(null);
      return;
    }
    overlay.setCursor({ x: hover.x, y: hover.y, radius: (s.brush_size * viewer.zoom) / 2 });
  }

  const toImage = (x, y) => {
    const q = viewer.screenToImage(x, y);
    return [q.x, q.y];
  };
  const insideImage = ([x, y]) => x >= 0 && y >= 0 && x < viewer.width && y < viewer.height;

  async function run(cmd, args) {
    try {
      const out = await invoke(cmd, args);
      if (out instanceof ArrayBuffer) applyRect(out);
      return out;
    } catch (e) {
      notify(String(e));
      return null;
    }
  }

  const select = (args) => run("selection_edit", { args });

  let lastSample = null;
  function sample(p, force) {
    const key = `${Math.floor(p[0])},${Math.floor(p[1])}`;
    if (!force && key === lastSample) return;
    lastSample = key;
    if (insideImage(p)) run("tool_eyedropper", { x: p[0], y: p[1], source: "display" });
  }

  // 直線・図形の確定前の見た目（#117）：確定と同じ画素を Rust（tools.rs の
  // tool_line_preview、計算は pma-core の line_tool）から受け取って重ねる。
  // 求めは1つずつ送り、返ってくる前に次の求めが来たら最後のものだけを送る。
  let previewWant = null;
  let previewBusy = false;
  let previewSeq = 0;
  function pixelsPreview(buffer) {
    const head = new DataView(buffer, 0, 16);
    const [x, y, w, h] = [0, 4, 8, 12].map((o) => head.getUint32(o, true));
    if (!w || !h) return null;
    const image = document.createElement("canvas");
    image.width = w;
    image.height = h;
    image.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(buffer, 16, w * h * 4), w, h), 0, 0);
    return { kind: "pixels", x, y, image };
  }
  async function pumpPreview() {
    previewBusy = true;
    try {
      while (previewWant) {
        const args = previewWant;
        previewWant = null;
        const seq = previewSeq;
        let buffer = null;
        try {
          buffer = await invoke("tool_line_preview", { args });
        } catch {
          buffer = null;
        }
        if (seq === previewSeq) overlay.setPreview(buffer ? pixelsPreview(buffer) : null);
      }
    } finally {
      previewBusy = false;
    }
  }
  function requestPreview(args) {
    previewWant = args;
    previewSeq++;
    if (!previewBusy) pumpPreview();
  }
  /// 確定前の見た目を消す（返ってくる途中の見た目も出さない）。
  function clearPreview() {
    previewWant = null;
    previewSeq++;
    overlay.setPreview(null);
  }
  api.clearPreview = clearPreview;

  function linePreview(state) {
    requestPreview({ kind: "line", start: state.start, end: state.end, control: state.control ?? null, brushLine: !!state.brushLine });
  }

  async function commitLine(state) {
    clearPreview();
    curve = null;
    await run("tool_line", { start: state.start, end: state.end, control: state.control ?? null, brushLine: !!state.brushLine });
    brushAnchor = state.end;
  }

  // ---- 選択範囲の変形（Rust の transform.rs、計算は pma-core） ----
  // ドラッグは1つずつ順に送る。動かしている途中の点は最後のものだけを送る。
  let dragChain = Promise.resolve();
  let dragMove = null;
  function transformDrag(phase, p) {
    const send = (ph, q) => invoke("transform_drag", { phase: ph, x: q[0], y: q[1], zoom: viewer.zoom });
    if (phase === "move") {
      const scheduled = dragMove !== null;
      dragMove = p;
      if (scheduled) return;
    } else {
      dragMove = null;
    }
    dragChain = dragChain.then(() => {
      if (phase === "move") {
        const q = dragMove;
        dragMove = null;
        return q ? send("move", q) : null;
      }
      return send(phase, p);
    }).catch((e) => notify(String(e)));
  }
  // 今の形で描いた画素（確定と同じ画素）。形が変わるたびに1つずつ求める。
  let transformKey = null;
  let transformBusy = false;
  let transformAgain = false;
  async function refreshTransformPreview() {
    if (transformBusy) {
      transformAgain = true;
      return;
    }
    transformBusy = true;
    try {
      do {
        transformAgain = false;
        const buffer = await invoke("transform_preview");
        overlay.setTransformPreview(toolStore.view?.transform ? pixelsPreview(buffer) : null);
      } while (transformAgain);
    } catch (e) {
      notify(String(e));
    } finally {
      transformBusy = false;
    }
  }
  function transformChanged(view) {
    const t = view?.transform;
    const key = t ? JSON.stringify([t.mode, t.points, t.quality]) : null;
    if (key === transformKey) return;
    transformKey = key;
    if (key) refreshTransformPreview();
    else overlay.setTransformPreview(null);
  }
  async function transformCommand(kind) {
    const message = await run("tool_transform", { kind });
    if (typeof message === "string") notify(message);
  }
  // 取り消しのキー（変形の途中は形を戻す）で形が変わったら、ツールの状態を読み直す。
  window.__TAURI__.event.listen("document-changed", async ({ payload }) => {
    if (payload.source !== "history" || !toolStore.view?.transform) return;
    try {
      toolStore.receive(await invoke("tools_state"));
    } catch {
      // 作品を閉じたときなど。
    }
  });

  /// メッシュの格子数（Qt の choose_transform_mesh_grid）。
  function chooseMeshGrid() {
    const s = settings();
    const spin = (value) => el("input", { type: "number", min: 2, max: 12, step: 1, value, class: "pma-spin" });
    const cols = spin(s.transform_mesh_x);
    const rows = spin(s.transform_mesh_y);
    const content = el("div", { class: "pma-form" },
      el("label", { class: "pma-row" }, el("span", { text: "横の格子数" }), cols),
      el("label", { class: "pma-row" }, el("span", { text: "縦の格子数" }), rows));
    const clamp = (v) => Math.max(2, Math.min(12, Math.round(Number(v) || 2)));
    openDialog("メッシュ格子数", content, [
      { label: "キャンセル" },
      {
        label: "OK",
        primary: true,
        action: async () => {
          const [c, r] = [clamp(cols.value), clamp(rows.value)];
          await toolStore.set({ transform_mesh_x: c, transform_mesh_y: r });
          if (!toolStore.view?.transform) notify(`次回のメッシュ変形を 横${c}×縦${r} 格子に設定しました。`);
        },
      },
    ]);
  }

  /// 1点を受け取る。ブラシの線として app.js に任せるときは false を返す。
  api.feed = (phase, device, x, y) => {
    if (!api.enabled || !settings()) return false;
    const t = effectiveTool();
    const p = toImage(x, y);
    if (phase === "down") {
      if (spaceTap) spaceTap.interrupted = true;
      const s = settings();
      // Ctrl＋Alt＋ドラッグ：ブラシ・ラインのサイズ（1px で 0.1）。
      if (held.has("Ctrl") && held.has("Alt") && ["brush", "line"].includes(s.tool) && !holdTool) {
        active = { kind: "resize", x, size: s.brush_size };
        return true;
      }
      if (t === "hand" || t === "zoom" || t === "rotate") {
        active = { kind: t, x, y, anchor: { x, y } };
        updateCursor();
        return true;
      }
      // 選択範囲の変形中は、ハンドル・回転のつまみ・中のドラッグだけ（Qt の begin_transform_drag）。
      if (toolStore.view?.transform) {
        active = { kind: "transform" };
        transformDrag("begin", p);
        return true;
      }
      if (t === "brush") {
        if (device === "eraser" || !held.has("Shift")) return false;
        // Shift＋クリック：前の線の終わりから直線（入り抜きは使わない）。
        active = { kind: "line", start: brushAnchor ?? p, end: p, brushLine: true };
        linePreview(active);
        return true;
      }
      if (!insideImage(p) && !["lasso", "lasso_fill", "rect_select"].includes(t)) return true;
      if (t === "eyedropper") {
        active = { kind: "eyedropper" };
        sample(p, true);
      } else if (t === "line") {
        if (curve) {
          curve.control = p;
          commitLine(curve);
          return true;
        }
        active = { kind: "line", start: p, end: p };
        linePreview(active);
      } else if (t === "shape") {
        active = { kind: "shape", start: p, end: p };
      } else if (t === "lasso" || t === "lasso_fill") {
        active = { kind: t, points: [p] };
      } else if (t === "rect_select") {
        active = { kind: "rect", start: p, end: p };
      } else if (t === "auto_select") {
        const mode = held.has("Alt") ? "subtract" : held.has("Shift") ? "add" : "replace";
        select({ kind: "auto", points: [p], mode });
      } else if (t === "bucket") {
        // paint_ops.js：押して動かすと、なぞった所をまとめて塗る。
        active = { kind: "bucket" };
        bucketDown(p, held.has("Shift"));
      } else if (t === "skewer_fill") {
        skewerClick(p, held.has("Shift"));
      } else if (t === "replace_color") {
        replaceColor(p);
      } else if (t === "dust") {
        if (s.dust_mode === "line_tidy") {
          active = { kind: "tidy", points: [p] };
          if (s.line_tidy_shape === "lasso") overlay.setPreview({ kind: "lasso", points: active.points });
        }
      }
      return true;
    }
    if (!active) return phase === "up" ? false : !!curve || t !== "brush";
    const a = active;
    if (a.kind === "transform") {
      transformDrag(phase === "up" ? "end" : "move", p);
    } else if (a.kind === "resize") {
      toolStore.set({ brush_size: Math.max(0.5, Math.min(400, Math.round((a.size + (x - a.x) * 0.1) * 2) / 2)) });
    } else if (a.kind === "hand") {
      viewer.panBy(x - a.x, y - a.y);
      a.x = x;
      a.y = y;
    } else if (a.kind === "zoom") {
      viewer.zoomAt(1.01 ** (x - a.x), a.anchor.x, a.anchor.y);
      a.x = x;
    } else if (a.kind === "rotate") {
      viewer.setRotation(viewer.rotation + (x - a.x) * 0.35);
      a.x = x;
    } else if (a.kind === "eyedropper") {
      sample(p, phase === "up");
    } else if (a.kind === "line") {
      a.end = p;
      linePreview(a);
    } else if (a.kind === "shape") {
      a.end = p;
      requestPreview({ kind: "shape", start: a.start, end: a.end });
    } else if (a.kind === "lasso" || a.kind === "lasso_fill") {
      const last = a.points[a.points.length - 1];
      if (Math.hypot(p[0] - last[0], p[1] - last[1]) * viewer.zoom >= 1) a.points.push(p);
      overlay.setPreview({ kind: "lasso", points: a.points });
    } else if (a.kind === "rect") {
      a.end = p;
      overlay.setPreview({ kind: "rect", start: a.start, end: a.end });
    } else if (a.kind === "bucket") {
      if (phase !== "up") bucketMove(p);
    } else if (a.kind === "tidy") {
      const last = a.points[a.points.length - 1];
      if (Math.hypot(p[0] - last[0], p[1] - last[1]) * viewer.zoom >= 1) a.points.push(p);
      if (settings().line_tidy_shape === "lasso") overlay.setPreview({ kind: "lasso", points: a.points });
    }
    if (phase !== "up") return true;
    active = null;
    updateCursor();
    if (a.kind === "line") {
      const curveType = !a.brushLine && (settings().tool === "line" ? settings().line_type : settings().shape_type) === "curve";
      if (curveType && Math.hypot(a.end[0] - a.start[0], a.end[1] - a.start[1]) > 0) {
        curve = { start: a.start, end: a.end, control: null };
        linePreview(curve);
      } else {
        commitLine(a);
      }
    } else if (a.kind === "shape") {
      clearPreview();
      run("tool_shape", { start: a.start, end: a.end });
    } else if (a.kind === "lasso_fill") {
      overlay.setPreview(null);
      run("tool_lasso_fill", { points: a.points });
    } else if (a.kind === "lasso") {
      overlay.setPreview(null);
      select({ kind: "lasso", points: a.points });
    } else if (a.kind === "rect") {
      overlay.setPreview(null);
      select({ kind: "rect", points: [a.start, a.end] });
    } else if (a.kind === "bucket") {
      bucketUp();
    } else if (a.kind === "tidy") {
      overlay.setPreview(null);
      dustApply(a.points);
    }
    return true;
  };

  /// ブラシの線が終わった（Shift＋クリックの直線の始点にする）。
  api.strokeEnded = (x, y) => {
    brushAnchor = toImage(x, y);
  };

  // ---- 画面のマウス（右クリックのスポイト、ホバー、回転の戻し） ----
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  let rightDrag = false;
  canvas.addEventListener("pointerdown", (e) => {
    if (e.button !== 2 || !api.enabled) return;
    const r = canvas.getBoundingClientRect();
    rightDrag = true;
    canvas.setPointerCapture(e.pointerId);
    sample(toImage(e.clientX - r.left, e.clientY - r.top), true);
  });
  canvas.addEventListener("pointermove", (e) => {
    const r = canvas.getBoundingClientRect();
    hover = { x: e.clientX - r.left, y: e.clientY - r.top };
    if (rightDrag) sample(toImage(hover.x, hover.y), false);
    if (curve) {
      curve.control = toImage(hover.x, hover.y);
      linePreview(curve);
    }
    showBrushCursor();
  });
  canvas.addEventListener("pointerup", (e) => {
    if (e.button === 2) rightDrag = false;
  });
  // ショートカット設定で変えた押している間のキーを読み直す。
  canvas.addEventListener("pointerenter", () => refreshShortcuts());
  canvas.addEventListener("pointerleave", () => {
    hover = null;
    showBrushCursor();
  });
  canvas.addEventListener("dblclick", () => {
    if (effectiveTool() === "rotate") viewer.setRotation(0);
  });

  // ---- キー ----
  // キーの持ち主は1つにする。修飾キーの付いた操作は OS のメニュー（menu.rs）、それ以外の
  // ショートカット（ツールのキー・Delete・E・C・X・[ ]）は shortcuts.js が受け、まだ移して
  // いない操作として `pma-action` {id, source} を送る。ここはそれを引き受ける（preventDefault）。
  // ここで直接キーを受けるのは、押している間の操作（Space・Alt など、ショートカット設定の
  // 「キャンバス操作」のキー）と、曲線の2段目の Enter／Esc だけ。
  const token = (e) => {
    if (e.code === "Space") return "Space";
    if (e.key === "Shift") return "Shift";
    if (e.key === "Alt") return "Alt";
    if (e.key === (IS_MAC ? "Meta" : "Control")) return "Ctrl";
    if (e.key === (IS_MAC ? "Control" : "Meta")) return "Meta";
    return null;
  };
  const typing = (e) =>
    e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement ||
    e.target instanceof HTMLSelectElement || e.target.closest?.(".tl-dialog-overlay, .sh-dialog-overlay, dialog");

  async function togglePlayback() {
    try {
      const view = await invoke("timeline_view");
      await invoke("timeline_play", { on: !view.playing });
    } catch (e) {
      notify(String(e));
    }
  }

  // 最後に押したキー（ツールのキーの一時切り替えで、離したのを知るため）。
  let lastKey = null;

  window.addEventListener("keydown", (e) => {
    if (typing(e) || !api.enabled) return;
    const tok = token(e);
    if (tok) {
      if (tok === "Space") e.preventDefault();
      if (!held.has(tok)) {
        if (tok === "Space") spaceTap = held.size === 0 ? { at: performance.now(), interrupted: false } : null;
        else if (spaceTap) spaceTap.interrupted = true;
        held.add(tok);
        updateHold();
      }
      return;
    }
    if (spaceTap) spaceTap.interrupted = true;
    lastKey = { code: e.code, at: performance.now() };
    // 変形中の Esc はキャンセル（Qt の keyPressEvent。Enter はメニューの「変形を確定」）。
    if (e.key === "Escape" && toolStore.view?.transform && !curve) {
      e.preventDefault();
      transformCommand("cancel");
      return;
    }
    // 曲線の2段目：Enter で確定、Esc でやめる。
    if (curve && (e.key === "Enter" || e.key === "Escape")) {
      e.preventDefault();
      if (e.key === "Enter") commitLine(curve);
      else {
        curve = null;
        clearPreview();
      }
    }
  }, true);

  /// ツールのショートカット（shortcuts.js → pma-action の tool:<id>）。
  function pressTool(pressed, source) {
    const s = settings();
    const view = toolStore.view;
    if (toolHold?.tool === pressed) return; // キーの自動リピート
    // まとめているときは、グループの先頭のキーをもう一度押すと次のツール（Qt の next_in_group）。
    let tool = pressed;
    const group = groupOf(pressed, view);
    if (!toolHold && group && view.groups.find(([g]) => g === group)[1][0] === pressed && groupOf(s.tool, view) === group) {
      tool = nextInGroup(s.tool, view);
    }
    const fromKey = source === "key" && lastKey && performance.now() - lastKey.at < 500;
    if (s.tool_hold_enabled && fromKey) {
      toolHold = { tool, code: lastKey.code, previous: toolHold?.previous ?? s.tool, at: performance.now() };
    } else {
      toolHold = null;
    }
    selectTool(tool);
  }

  function brushStep(direction) {
    // 1段（約15%、最低 0.5px）。
    const current = settings().brush_size;
    let target = direction > 0 ? Math.max(current + 0.5, current * 1.15) : Math.min(current - 0.5, current / 1.15);
    target = Math.round(target * 2) / 2;
    toolStore.set({ brush_size: Math.max(0.5, Math.min(400, target)) });
  }

  /// ここで引き受ける操作（actions.rs の id）。
  const ACTIONS = {
    a_selection_clear: () => select({ kind: "clear" }),
    a_selection_all: () => select({ kind: "all" }),
    a_selection_invert: () => select({ kind: "invert" }),
    a_selection_delete: () => run("selection_delete"),
    a_select_main: () => toolStore.set({ color_mode: "main" }),
    a_select_sub: () => toolStore.set({ color_mode: "sub" }),
    a_select_transparent: () => {
      const s = settings();
      const patch = { color_mode: "transparent" };
      if (!DRAW_TOOLS.includes(s.tool)) patch.tool = s.background_color_tool;
      toolStore.set(patch);
    },
    a_toggle_draw_background: () => {
      const s = settings();
      if (s.color_mode === "transparent") toolStore.set({ color_mode: ["main", "sub"].includes(previousColorMode) ? previousColorMode : "main" });
      else {
        previousColorMode = s.color_mode;
        toolStore.set({ color_mode: "transparent" });
      }
    },
    a_swap_main_sub: () => {
      toolStore.set({ color_mode: settings().color_mode === "main" ? "sub" : "main" });
      notify("メインカラーとサブカラーを切り替えました。");
    },
    a_choose_background: async () => {
      const rgb = await pickColor(settings().background_color);
      if (rgb) toolStore.set({ background_color: rgb, color_mode: "transparent" });
    },
    a_brush_smaller: () => brushStep(-1),
    a_brush_larger: () => brushStep(1),
    a_pressure: () => openPressureDialog(),
    a_mainline_repaint: () => run("action_run", { id: "main_line_repaint" }),
    a_selection_transform: () => transformCommand("free"),
    a_selection_scale: () => transformCommand("scale"),
    a_selection_mesh: () => transformCommand("mesh"),
    a_transform_rotate_left: () => transformCommand("rotate_left"),
    a_transform_rotate_right: () => transformCommand("rotate_right"),
    a_transform_cancel: () => transformCommand("cancel"),
    a_transform_mesh_grid: () => chooseMeshGrid(),
    a_transform_commit: () => {
      // Enter：曲線の2段目なら曲線を確定、変形中なら変形を確定。
      if (curve) return commitLine(curve);
      return transformCommand("commit");
    },
    a_dust_apply: () => dustApply(null),
    a_bucket_include_sub: () => toolStore.set({ bucket_include_sub: !settings().bucket_include_sub }),
    a_bucket_close_gap: () => toolStore.set({ bucket_close_gap: !settings().bucket_close_gap }),
    a_bucket_adjacent: () => toolStore.set({ bucket_adjacent: !settings().bucket_adjacent }),
    a_bucket_extend_lines: () => toolStore.set({ bucket_extend_lines: !settings().bucket_extend_lines }),
    a_bucket_island_fill: () => toolStore.set({ bucket_island_fill: !settings().bucket_island_fill }),
    a_bucket_all_frames: () => selectTool(settings().tool === "skewer_fill" ? "bucket" : "skewer_fill"),
    a_bucket_replace_color: () => selectTool(settings().tool === "replace_color" ? "bucket" : "replace_color"),
    a_dust_all_frames: () => toolStore.set({ dust_all_frames: !settings().dust_all_frames }),
    a_selection_all_frames: () => toolStore.set({ selection_all_frames: !settings().selection_all_frames }),
  };
  /// 引き受ける id の一覧（確かめ用）。
  api.claimed = () => [...Object.keys(ACTIONS), ...toolStore.view.tools.map(([id]) => `tool:${id}`)];

  /// 操作を1つ行う。引き受けたら true。
  api.runAction = (id, source = "menu") => {
    if (!settings()) return false;
    if (id.startsWith("tool:")) {
      const tool = id.slice(5);
      if (!toolStore.view.tools.some(([t]) => t === tool)) return false;
      pressTool(tool, source);
      return true;
    }
    const f = ACTIONS[id];
    if (!f) return false;
    Promise.resolve(f()).catch((e) => notify(String(e)));
    return true;
  };

  window.addEventListener("pma-action", (e) => {
    const { id, source } = e.detail ?? {};
    // 筆圧の自己確認のあいだは触らない。
    if (!api.enabled || !id) return;
    if (api.runAction(id, source)) e.preventDefault();
  });

  window.addEventListener("keyup", (e) => {
    const tok = token(e);
    if (tok) {
      held.delete(tok);
      if (tok === "Space" && spaceTap) {
        const tap = spaceTap;
        spaceTap = null;
        if (!tap.interrupted && performance.now() - tap.at < SPACE_TAP_MS) togglePlayback();
      }
      updateHold();
      return;
    }
    if (toolHold && e.code === toolHold.code) {
      const h = toolHold;
      toolHold = null;
      const s = settings();
      if (performance.now() - h.at >= s.tool_hold_ms && s.tool === h.tool && h.previous !== h.tool) {
        // 描いている途中なら描き終わってから戻す。
        const revert = () => (active ? setTimeout(revert, 50) : selectTool(h.previous));
        revert();
      }
    }
  });

  window.addEventListener("blur", () => {
    held.clear();
    spaceTap = null;
    updateHold();
  });

  toolStore.subscribe((view) => {
    transformChanged(view);
    if (curve && effectiveTool() !== "line") {
      curve = null;
      clearPreview();
    }
    updateCursor();
  });
  viewer.onchange = ((previous) => () => {
    previous?.();
    showBrushCursor();
    overlay.requestDraw();
  })(viewer.onchange);
  return api;
}
