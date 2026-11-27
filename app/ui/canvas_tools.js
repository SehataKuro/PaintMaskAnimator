// キャンバスの上の操作をツールごとに振り分ける（Qt 版の canvas_input_events.py・
// main_window_input.py・main_window_tool_hold.py）。
//
// - 押している間のツール：Space＝ハンド、Shift＋Space＝回転、Alt＝スポイト、
//   拡大縮小は macOS が Alt＋Space、それ以外は Ctrl＋Space。Space を短く押して
//   離したら（0.3 秒未満）再生／停止。
// - ブラシの線は app.js（筆圧の経路）が描く。ここはブラシ以外のツールと、ブラシの
//   Shift＋クリックの直線、Ctrl＋Alt のドラッグでのサイズ変更を受け持つ。
// - ツールのキー（P・G など）と、選択範囲・描画色のショートカット。
import { toolStore, selectTool, nextInGroup, groupOf } from "./tools.js";
import { toolFlags, notify } from "./tool_properties.js";

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
  const mod = (e) => (IS_MAC ? e.metaKey : e.ctrlKey);

  /// 今のツール（押している間のツールが先。図形の直線・曲線はライン）。
  function effectiveTool() {
    if (holdTool) return holdTool;
    const s = settings();
    if (!s) return "brush";
    return toolFlags(s.tool, s).isLine ? "line" : s.tool;
  }

  function updateHold() {
    const zoomKeys = IS_MAC ? ["Alt", "Space"] : ["Ctrl", "Space"];
    const bindings = [["zoom", zoomKeys], ["rotate", ["Shift", "Space"]], ["hand", ["Space"]], ["eyedropper", ["Alt"]]];
    let best = null;
    bindings.forEach(([tool, keys], priority) => {
      if (!keys.every((k) => held.has(k))) return;
      const score = [keys.length, -priority];
      if (!best || score[0] > best[0][0] || (score[0] === best[0][0] && score[1] > best[0][1])) best = [score, tool];
    });
    let next = best?.[1] ?? null;
    // Ctrl＋Alt はブラシ・ラインのサイズ変更（押している間のツールにしない）。
    if (held.has("Ctrl") && held.has("Alt") && ["brush", "line"].includes(settings()?.tool)) next = null;
    if (next !== holdTool) {
      holdTool = next;
      active = null;
      overlay.setPreview(curve ? overlay.preview : null);
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

  // 図形の形（Rust の shape_path と同じ。画面に出すだけ）。
  function shapePreview(start, end) {
    const s = settings();
    let e = end;
    if (s.shape_lock_ratio) {
      const dx = end[0] - start[0];
      const dy = end[1] - start[1];
      const size = Math.max(Math.abs(dx), Math.abs(dy));
      e = [start[0] + (dx >= 0 ? size : -size), start[1] + (dy >= 0 ? size : -size)];
    }
    const x = Math.min(start[0], e[0]);
    const y = Math.min(start[1], e[1]);
    const w = Math.abs(e[0] - start[0]);
    const h = Math.abs(e[1] - start[1]);
    if (s.shape_type === "ellipse") return { kind: "shape", points: [], ellipse: [x, y, w, h] };
    const n = Math.max(3, s.shape_corners);
    if (n === 4) return { kind: "shape", points: [[x, y], [x + w, y], [x + w, y + h], [x, y + h]] };
    const points = [];
    for (let i = 0; i < n; i++) {
      const a = -Math.PI / 2 + (2 * Math.PI * i) / n;
      points.push([x + w / 2 + (Math.cos(a) * w) / 2, y + h / 2 + (Math.sin(a) * h) / 2]);
    }
    return { kind: "shape", points };
  }

  function linePreview(state) {
    overlay.setPreview({ kind: "line", start: state.start, end: state.end, control: state.control, width: settings().brush_size });
  }

  async function commitLine(state) {
    overlay.setPreview(null);
    curve = null;
    await run("tool_line", { start: state.start, end: state.end, control: state.control ?? null, brushLine: !!state.brushLine });
    brushAnchor = state.end;
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
      } else if (["bucket", "skewer_fill", "replace_color"].includes(t)) {
        run("tool_fill", { tool: t, x: p[0], y: p[1] });
      } else if (t === "dust") {
        if (s.dust_mode === "line_tidy") active = { kind: "tidy" };
      }
      return true;
    }
    if (!active) return phase === "up" ? false : !!curve || t !== "brush";
    const a = active;
    if (a.kind === "resize") {
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
      overlay.setPreview(shapePreview(a.start, a.end));
    } else if (a.kind === "lasso" || a.kind === "lasso_fill") {
      const last = a.points[a.points.length - 1];
      if (Math.hypot(p[0] - last[0], p[1] - last[1]) * viewer.zoom >= 1) a.points.push(p);
      overlay.setPreview({ kind: "lasso", points: a.points });
    } else if (a.kind === "rect") {
      a.end = p;
      overlay.setPreview({ kind: "rect", start: a.start, end: a.end });
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
      overlay.setPreview(null);
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
    } else if (a.kind === "tidy") {
      run("tool_dust_apply");
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
  canvas.addEventListener("pointerleave", () => {
    hover = null;
    showBrushCursor();
  });
  canvas.addEventListener("dblclick", () => {
    if (effectiveTool() === "rotate") viewer.setRotation(0);
  });

  // ---- キー ----
  const token = (e) => {
    if (e.code === "Space") return "Space";
    if (e.key === "Shift") return "Shift";
    if (e.key === "Alt") return "Alt";
    if (e.key === (IS_MAC ? "Meta" : "Control")) return "Ctrl";
    return null;
  };
  const typing = (e) =>
    e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement ||
    e.target instanceof HTMLSelectElement || e.target.closest?.(".tl-dialog-overlay");

  function toolForKey(key) {
    const s = settings();
    for (const [tool, k] of Object.entries(s.shortcuts)) if (k && k.toUpperCase() === key) return tool;
    return null;
  }

  async function togglePlayback() {
    try {
      const view = await invoke("timeline_view");
      await invoke("timeline_play", { on: !view.playing });
    } catch (e) {
      notify(String(e));
    }
  }

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
    if (e.defaultPrevented) return;
    const s = settings();
    if (!s) return;
    const m = mod(e);
    const key = e.key.length === 1 ? e.key.toUpperCase() : e.key;
    // 曲線の2段目：Enter で確定、Esc でやめる。
    if (curve && (key === "Enter" || key === "Escape")) {
      e.preventDefault();
      if (key === "Enter") commitLine(curve);
      else {
        curve = null;
        overlay.setPreview(null);
      }
      return;
    }
    if (m && !e.altKey) {
      if ((key === "D" && !e.shiftKey) || (key === "A" && e.shiftKey)) {
        e.preventDefault();
        select({ kind: "clear" });
      } else if (key === "A") {
        e.preventDefault();
        select({ kind: "all" });
      } else if (key === "I" && e.shiftKey) {
        e.preventDefault();
        select({ kind: "invert" });
      }
      return;
    }
    if (m || e.altKey) return;
    if (key === "Delete" || key === "Backspace") {
      e.preventDefault();
      run("selection_delete");
      return;
    }
    if (key === "[" || key === "]") {
      // 1段（約15%、最低 0.5px）。
      const current = s.brush_size;
      let target = key === "]" ? Math.max(current + 0.5, current * 1.15) : Math.min(current - 0.5, current / 1.15);
      target = Math.round(target * 2) / 2;
      toolStore.set({ brush_size: Math.max(0.5, Math.min(400, target)) });
      return;
    }
    if (e.shiftKey) return;
    if (key === "E") {
      const patch = { color_mode: "transparent" };
      if (!DRAW_TOOLS.includes(s.tool)) patch.tool = s.background_color_tool;
      toolStore.set(patch);
      return;
    }
    if (key === "C") {
      if (s.color_mode === "transparent") toolStore.set({ color_mode: ["main", "sub"].includes(previousColorMode) ? previousColorMode : "main" });
      else {
        previousColorMode = s.color_mode;
        toolStore.set({ color_mode: "transparent" });
      }
      return;
    }
    if (key === "X") {
      toolStore.set({ color_mode: s.color_mode === "main" ? "sub" : "main" });
      notify("メインカラーとサブカラーを切り替えました。");
      return;
    }
    const pressed = toolForKey(key);
    if (!pressed) return;
    e.preventDefault();
    if (e.repeat || toolHold?.tool === pressed) return;
    const view = toolStore.view;
    // まとめているときは、グループの先頭のキーをもう一度押すと次のツール（Qt の next_in_group）。
    let tool = pressed;
    const group = groupOf(pressed, view);
    if (!toolHold && group && view.groups.find(([g]) => g === group)[1][0] === pressed && groupOf(s.tool, view) === group) {
      tool = nextInGroup(s.tool, view);
    }
    if (s.tool_hold_enabled) {
      toolHold = { tool, key, previous: toolHold?.previous ?? s.tool, at: performance.now() };
    }
    selectTool(tool);
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
    const key = e.key.length === 1 ? e.key.toUpperCase() : e.key;
    if (toolHold && key === toolHold.key) {
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

  toolStore.subscribe(() => {
    if (curve && effectiveTool() !== "line") {
      curve = null;
      overlay.setPreview(null);
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
