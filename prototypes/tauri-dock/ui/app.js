// 画面を Rust にする判断のための試作（Tauri + dockview）。アプリには組み込まない。
"use strict";

window.addEventListener("error", (e) => window.__TAURI__?.core.invoke("log", { line: `JS エラー：${e.message} @${e.lineno}` }));
window.addEventListener("unhandledrejection", (e) => window.__TAURI__?.core.invoke("log", { line: `JS 失敗：${e.reason}` }));

const DV = window["dockview-core"];
const invoke = (cmd, args) => window.__TAURI__.core.invoke(cmd, args);
const CANVAS_W = 1920, CANVAS_H = 1080;

// ---- ブラシの遅れの記録 ----
const latency = { segmentUs: [], frameMs: [], lastFrame: null, drawing: false };
function pct(values, p) {
  if (!values.length) return 0;
  const v = [...values].sort((a, b) => a - b);
  return v[Math.round((v.length - 1) * p)];
}
function latencySummary() {
  return `区間 ${latency.segmentUs.length} 件：中央値 ${pct(latency.segmentUs, .5).toFixed(0)}µs／95% ${pct(latency.segmentUs, .95).toFixed(0)}µs　`
    + `フレーム間隔：中央値 ${pct(latency.frameMs, .5).toFixed(1)}ms／95% ${pct(latency.frameMs, .95).toFixed(1)}ms`;
}
function trackFrames(now) {
  if (latency.drawing && latency.lastFrame !== null) latency.frameMs.push(now - latency.lastFrame);
  latency.lastFrame = now;
  requestAnimationFrame(trackFrames);
}
requestAnimationFrame(trackFrames);

// ---- パネル ----
const state = { color: "#282828", radius: 6, pressure: 1, pointerType: "-" };
const ui = {};

function canvasPanel() {
  const host = document.createElement("div");
  host.className = "canvas-host";
  const canvas = document.createElement("canvas");
  canvas.width = CANVAS_W; canvas.height = CANVAS_H;
  host.appendChild(canvas);
  const ctx = canvas.getContext("2d", { desynchronized: true });
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);
  ctx.lineCap = "round"; ctx.lineJoin = "round";
  let last = null;
  const toCanvas = (e) => {
    const r = canvas.getBoundingClientRect();
    const scale = Math.min(r.width / CANVAS_W, r.height / CANVAS_H);
    const ox = r.left + (r.width - CANVAS_W * scale) / 2, oy = r.top + (r.height - CANVAS_H * scale) / 2;
    return { x: (e.clientX - ox) / scale, y: (e.clientY - oy) / scale,
             p: e.pressure > 0 ? e.pressure : 1 };
  };
  const segment = (pt) => {
    const t0 = performance.now();
    const r = state.radius * Math.max(0.05, Math.min(1, pt.p));
    ctx.strokeStyle = state.color; ctx.fillStyle = state.color;
    if (!last) {
      ctx.beginPath(); ctx.arc(pt.x, pt.y, r, 0, Math.PI * 2); ctx.fill();
    } else {
      ctx.lineWidth = r * 2;
      ctx.beginPath(); ctx.moveTo(last.x, last.y); ctx.lineTo(pt.x, pt.y); ctx.stroke();
    }
    last = pt;
    latency.segmentUs.push((performance.now() - t0) * 1000);
  };
  const pressures = [];
  canvas.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    pressures.length = 0; pressures.push(e.pressure);
    canvas.setPointerCapture?.(e.pointerId);
    latency.drawing = true; last = null;
    state.pointerType = e.pointerType; state.pressure = e.pressure;
    segment(toCanvas(e));
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!latency.drawing) return;
    const events = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
    for (const ev of (events.length ? events : [e])) { segment(toCanvas(ev)); pressures.push(ev.pressure); }
    state.pointerType = e.pointerType; state.pressure = e.pressure;
    ui.pressure && (ui.pressure.textContent = `筆圧：${e.pressure.toFixed(2)}（${e.pointerType}）`);
  });
  const end = (e) => {
    if (latency.drawing && e && e.isTrusted) invoke("log", { line: `線：${pressures.length} 件、筆圧 ${Math.min(...pressures).toFixed(2)}〜${Math.max(...pressures).toFixed(2)}（${e.pointerType}）` });
    latency.drawing = false; last = null; ui.latency && (ui.latency.textContent = latencySummary()); };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  return host;
}

function toolsPanel() {
  const el = document.createElement("div"); el.className = "panel";
  el.innerHTML = `<div>ブラシの太さ</div><input type="range" min="1" max="60" value="6">
    <div class="pressure">筆圧：-</div><div>筆圧は PointerEvent.pressure（WebKit／WebView2）</div>`;
  el.querySelector("input").addEventListener("input", (e) => state.radius = +e.target.value);
  ui.pressure = el.querySelector(".pressure");
  return el;
}

function colorPanel() {
  const el = document.createElement("div"); el.className = "panel";
  el.innerHTML = `<input type="color" value="#282828"><div class="swatches"></div>`;
  el.querySelector("input").addEventListener("input", (e) => state.color = e.target.value);
  const sw = el.querySelector(".swatches");
  for (let i = 0; i < 18; i++) {
    const d = document.createElement("div");
    d.style.background = `hsl(${i * 20}, 70%, 70%)`;
    d.addEventListener("click", () => state.color = d.style.background);
    sw.appendChild(d);
  }
  return el;
}

function layersPanel() {
  const el = document.createElement("div"); el.className = "panel";
  el.innerHTML = `<div>レイヤー名（日本語入力の確認）</div>
    <input value="線画"><input value="影"><input value="Layer 3">`;
  return el;
}

function timelinePanel() {
  const el = document.createElement("div"); el.className = "panel";
  el.innerHTML = `<div class="latency">${latencySummary()}</div><div class="frames"></div>`;
  const frames = el.querySelector(".frames");
  for (let i = 1; i <= 48; i++) {
    const s = document.createElement("span"); s.textContent = i; if (i === 1) s.className = "on";
    frames.appendChild(s);
  }
  ui.latency = el.querySelector(".latency");
  return el;
}

function scriptPanel() {
  const el = document.createElement("div"); el.className = "panel";
  el.innerHTML = `<div>スクリプト（日本語入力の確認）</div><textarea rows="6"># ここに日本語を入力して確かめる</textarea>`;
  return el;
}

const PANELS = {
  canvas: ["キャンバス", canvasPanel], tools: ["ツール", toolsPanel], color: ["カラー", colorPanel],
  layers: ["レイヤー", layersPanel], timeline: ["タイムライン", timelinePanel], script: ["スクリプト", scriptPanel],
};

// ---- 別の OS の窓（dockview の popout。Tauri の on_new_window で WKWebView の窓を開く） ----
// 別の窓の label は Rust が開いた順に popout-1, popout-2, … と付ける。dockview が開いた順と同じ。
const popouts = []; // { label, group, lastOuter, moved, still }
let popoutCount = 0;
const isPopout = (group) => group.api.location.type === "popout";
async function popOut(group) {
  await api.addPopoutGroup(group);
}
function popBack(group) {
  const entry = popouts.find((p) => p.group === group);
  if (entry) invoke("close_window", { label: entry.label }); // 窓を閉じると dockview がもとの場所に戻す
}
function headerAction(group) {
  const el = document.createElement("div");
  el.style.cssText = "display:flex;align-items:center;height:100%;padding:0 6px;";
  const button = document.createElement("button");
  button.style.cssText = "background:#333;color:#ddd;border:1px solid #555;border-radius:3px;font:12px inherit;padding:1px 6px;cursor:pointer;";
  el.appendChild(button);
  const refresh = () => {
    const out = isPopout(group);
    button.textContent = out ? "メインの窓に戻す" : "⧉";
    button.title = out ? "メインの窓に戻す" : "別の窓に出す";
    button.className = out ? "pma-back" : "pma-popout";
  };
  button.addEventListener("click", () => (isPopout(group) ? popBack(group) : popOut(group)));
  return {
    element: el,
    init: () => { refresh(); group.api.onDidLocationChange?.(refresh); },
    dispose: () => {},
  };
}

const api = DV.createDockview(document.getElementById("dock"), {
  theme: DV.themeDark,
  popoutUrl: "/popout.html",
  createRightHeaderActionComponent: headerAction,
  createComponent: (options) => {
    const element = PANELS[options.name][1]();
    return { element, init: () => {} };
  },
});
api.onDidAddPopoutGroup((e) => {
  popoutCount++;
  const label = `popout-${popoutCount}`;
  popouts.push({ label, group: e.group, lastOuter: null, wasOutside: false, moved: false, still: 0 });
  const names = e.group.panels.map((p) => p.title).join("・");
  invoke("shape_window", { label, width: e.group.panels.some((p) => p.id === "timeline") ? 520 : 320,
    height: e.group.panels.some((p) => p.id === "timeline") ? 200 : 380, title: `${names} — PMA 試作（別の窓）` });
  invoke("log", { line: `別の窓に出した：${e.group.panels.map((p) => p.title).join("・")} → popout-${popoutCount}` });
});
api.onDidRemovePopoutGroup((e) => {
  const i = popouts.findIndex((p) => p.group === e.group);
  if (i >= 0) {
    invoke("log", { line: `メインの窓に戻った：${popouts[i].label}` });
    popouts.splice(i, 1);
  }
});
// 一度外へ出した別の窓をメインの窓の上まで動かして止めたら戻す（動かしている間は戻る先を出す）。
const hint = document.createElement("div");
hint.style.cssText = "position:fixed;inset:24px;border:3px solid #3c8cff;border-radius:8px;background:rgba(40,110,230,.27);"
  + "display:none;align-items:center;justify-content:center;color:#fff;font-size:28px;z-index:99999;pointer-events:none;";
hint.textContent = "ここで止めると、もとの場所に戻る";
document.body.appendChild(hint);
let windowsNow = [];
async function watchPopouts() {
  if (!popouts.length) { hint.style.display = "none"; return; }
  windowsNow = await invoke("window_rects");
  const main = windowsNow.find((w) => w.label === "main");
  let hovering = false;
  for (const p of popouts) {
    const w = windowsNow.find((x) => x.label === p.label);
    if (!w || !main) continue;
    const [x, y, width, height] = w.outer;
    if (p.lastOuter && (Math.abs(p.lastOuter[0] - x) > 0.5 || Math.abs(p.lastOuter[1] - y) > 0.5)) { p.moved = true; p.still = 0; }
    else p.still++;
    p.lastOuter = w.outer;
    const [mx, my, mw, mh] = main.inner;
    // 一度メインの窓の外へ完全に出てからでないと、メインの窓の上で止めても戻さない。
    const [ox, oy, ow, oh] = main.outer;
    if (x >= ox + ow || x + width <= ox || y >= oy + oh || y + height <= oy) p.wasOutside = true;
    const cx = x + width / 2, cy = y + height / 2;
    const over = p.wasOutside && cx > mx + 40 && cx < mx + mw - 40 && cy > my + 40 && cy < my + mh - 40;
    if (p.moved && over) {
      if (p.still >= 15) { p.moved = false; invoke("log", { line: `メインの窓の上で止めたので戻す：${p.label}` }); popBack(p.group); }
      else hovering = true;
    }
  }
  hint.style.display = hovering ? "flex" : "none";
}
let watching = false;
setInterval(() => { if (!watching && !popoutMode) { watching = true; watchPopouts().finally(() => watching = false); } }, 33);
let popoutMode = false;

const add = (id, position) => api.addPanel({ id, component: id, title: PANELS[id][0], position });
add("canvas");
add("tools", { referencePanel: "canvas", direction: "left" });
add("color", { referencePanel: "tools", direction: "within" });
add("layers", { referencePanel: "canvas", direction: "right" });
add("script", { referencePanel: "layers", direction: "within" });
add("timeline", { referencePanel: "canvas", direction: "below" });
api.getPanel("tools").api.setActive();
api.getPanel("layers").api.setActive();
api.getPanel("canvas").api.setActive();
// 左右の列を細くする（今の Qt の配置に近づける）。
requestAnimationFrame(() => {
  api.getPanel("tools").group.api.setSize({ width: 230 });
  api.getPanel("layers").group.api.setSize({ width: 280 });
  api.getPanel("timeline").group.api.setSize({ height: 180 });
});

// ---- 台本（PMA_DEMO_OUT があるときだけ） ----
const cursor = document.getElementById("cursor");
const cursorPath = document.getElementById("cursor-path");
const caption = document.getElementById("caption");
const pointer = { x: 640, y: 400 };
const ease = (t) => t * t * (3 - 2 * t);
const nextFrame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

let out = null, frameNo = 0;
async function frame() {
  cursor.style.transform = `translate(${pointer.x}px, ${pointer.y}px)`;
  await nextFrame();
  if (out) {
    await invoke("snapshot", { path: `${out}/frame_${String(frameNo).padStart(5, "0")}.png` });
    frameNo++;
  }
}

function tabEl(id) {
  return [...document.querySelectorAll(".dv-tab")].find((t) => t.textContent.trim().startsWith(PANELS[id][0]));
}
function bodyRect(id) {
  const panel = api.getPanel(id);
  return panel.group.element.querySelector(".dv-content-container").getBoundingClientRect();
}
function at(id, fx, fy) {
  const r = bodyRect(id);
  return { x: r.left + r.width * fx, y: r.top + r.height * fy };
}
function center(el) {
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}

function fire(el, type, Ctor, p, extra = {}) {
  const init = { bubbles: true, cancelable: true, composed: true, clientX: p.x, clientY: p.y, screenX: p.x, screenY: p.y,
    button: 0, buttons: type.endsWith("up") || type === "drop" || type === "dragend" ? 0 : 1, view: window, ...extra };
  if (Ctor === PointerEvent) Object.assign(init, { pointerId: 1, pointerType: "mouse", isPrimary: true, pressure: init.buttons ? 0.5 : 0 });
  const ev = new Ctor(type, init);
  el.dispatchEvent(ev);
  return ev;
}

async function approach(to, frames = 6) {
  const from = { ...pointer };
  for (let i = 1; i <= frames; i++) {
    const t = ease(i / frames);
    pointer.x = from.x + (to.x - from.x) * t; pointer.y = from.y + (to.y - from.y) * t;
    await frame();
  }
}

// HTML5 のドラッグ＆ドロップ（dockview のタブの移動）を、本物と同じ事件の並びで入れる。
async function dragTab(id, toFn, frames = 45, shift = false) {
  const src = tabEl(id);
  const from = center(src);
  await approach(from);
  cursorPath.setAttribute("fill", "#ffd23c");
  fire(src, "pointerdown", PointerEvent, from, { shiftKey: shift });
  if (shift) return; // shift ＋ pointerdown は dockview が浮いた窓を作り、そのまま移動に入る
  const dt = new DataTransfer();
  fire(src, "dragstart", DragEvent, from, { dataTransfer: dt });
  await frame();
  const to = toFn();
  let last = null;
  for (let i = 1; i <= frames; i++) {
    const t = ease(i / frames);
    pointer.x = from.x + (to.x - from.x) * t; pointer.y = from.y + (to.y - from.y) * t;
    fire(src, "drag", DragEvent, pointer, { dataTransfer: dt });
    const el = document.elementFromPoint(pointer.x, pointer.y);
    if (el !== last) {
      if (last) fire(last, "dragleave", DragEvent, pointer, { dataTransfer: dt, relatedTarget: el });
      if (el) fire(el, "dragenter", DragEvent, pointer, { dataTransfer: dt, relatedTarget: last });
      last = el;
    }
    if (el) fire(el, "dragover", DragEvent, pointer, { dataTransfer: dt });
    await frame();
  }
  if (last) fire(last, "drop", DragEvent, pointer, { dataTransfer: dt });
  fire(src, "dragend", DragEvent, pointer, { dataTransfer: dt });
  fire(src, "pointerup", PointerEvent, pointer);
  cursorPath.setAttribute("fill", "#fff");
  await frame();
}

// ポインターの押し・移動・離し（区切りや浮いた窓の移動、キャンバスへの描画）。
async function pointerDrag(fromFn, by, frames = 35, shift = false, target = null) {
  const from = fromFn();
  await approach(from);
  const el = target || document.elementFromPoint(from.x, from.y);
  cursorPath.setAttribute("fill", "#ffd23c");
  fire(el, "pointerdown", PointerEvent, from, { shiftKey: shift });
  fire(el, "mousedown", MouseEvent, from, { shiftKey: shift });
  for (let i = 1; i <= frames; i++) {
    const t = ease(i / frames);
    pointer.x = from.x + by.x * t; pointer.y = from.y + by.y * t;
    fire(el, "pointermove", PointerEvent, pointer);
    fire(el, "mousemove", MouseEvent, pointer);
    await frame();
  }
  fire(el, "pointerup", PointerEvent, pointer);
  fire(el, "mouseup", MouseEvent, pointer);
  cursorPath.setAttribute("fill", "#fff");
  await frame();
}

async function wait(n) { for (let i = 0; i < n; i++) await frame(); }

async function demo() {
  cursor.style.display = "block"; caption.style.display = "block";
  const say = (t) => caption.textContent = t;
  const canvasEl = () => document.querySelector(".canvas-host canvas");

  say("Tauri + dockview：初期配置。キャンバス（1920×1080）に描く");
  await wait(15);
  await pointerDrag(() => at("canvas", .25, .35), { x: 120, y: 90 }, 14, false, canvasEl());
  await pointerDrag(() => at("canvas", .45, .35), { x: -60, y: 110 }, 14, false, canvasEl());
  await pointerDrag(() => at("canvas", .55, .38), { x: 140, y: 20 }, 14, false, canvasEl());
  await wait(15);

  say("「カラー」タブをキャンバスの右端へドラッグ → 右に分割");
  await dragTab("color", () => at("canvas", .9, .5));
  await wait(25);

  say("「タイムライン」タブを「レイヤー」の中央へ → タブとしてまとめる");
  await dragTab("timeline", () => at("layers", .5, .5));
  await wait(25);

  say("「スクリプト」タブを Shift を押しながら掴む → フロート（浮いた窓）");
  await dragTab("script", null, 0, true);
  for (let i = 1; i <= 35; i++) {
    const t = ease(i / 35);
    const p = { x: pointer.x + (-520 - 0) / 35, y: pointer.y + 160 / 35 };
    pointer.x = p.x; pointer.y = p.y;
    fire(window, "pointermove", PointerEvent, pointer);
    fire(document, "mousemove", MouseEvent, pointer);
    await frame();
  }
  fire(window, "pointerup", PointerEvent, pointer);
  fire(document, "mouseup", MouseEvent, pointer);
  cursorPath.setAttribute("fill", "#fff");
  await wait(20);

  say("浮いた窓を題名の帯で掴んで動かす");
  const titleBar = () => api.getPanel("script").group.element
    .closest(".dv-resize-container").querySelector(".dv-floating-titlebar");
  await pointerDrag(() => {
    const bar = titleBar().getBoundingClientRect();
    return { x: bar.left + bar.width / 2, y: bar.top + bar.height / 2 };
  }, { x: 300, y: 80 }, 35, false, titleBar());
  await wait(20);

  say("浮いた窓のタブをキャンバスの下端へ → 下に分割してドッキング");
  await dragTab("script", () => at("canvas", .5, .9));
  await wait(25);

  say("「ツール」タブをキャンバスの左端へ → 左に分割");
  await dragTab("tools", () => at("canvas", .1, .5));
  await wait(25);

  say("区切りを動かして大きさを変える");
  await pointerDrag(() => {
    const r = bodyRect("color");
    return { x: r.left - 2, y: r.top + r.height / 2 };
  }, { x: 220, y: 0 });
  await wait(40);

  await invoke("log", { line: `${frameNo} フレームを ${out} に保存　ブラシ：${latencySummary()}` });
  await invoke("quit");
}

// 1秒に240回のペンの移動（1フレームに4回）を入れ続け、フレームの間隔と描画の JS の時間を測る。
async function latencyRun() {
  const canvas = document.querySelector(".canvas-host canvas");
  const strokeLen = 60, perFrame = 4, intervals = [], jsMs = [];
  let last = performance.now();
  for (let f = 1; f <= 1200; f++) {
    await new Promise((r) => requestAnimationFrame(r));
    const now = performance.now();
    if (f > 1) intervals.push(now - last);
    last = now;
    const k = f % strokeLen, stroke = Math.floor(f / strokeLen);
    const at = (i) => {
      const t = i / (strokeLen * perFrame);
      const r = canvas.getBoundingClientRect();
      return { x: r.left + r.width * (0.1 + 0.8 * t),
               y: r.top + r.height * (0.15 + 0.03 * (stroke % 20)) + 40 * Math.sin(t * 12) };
    };
    const t0 = performance.now();
    if (k === 0) fire(canvas, "pointerdown", PointerEvent, at(0));
    else if (k === strokeLen - 1) fire(canvas, "pointerup", PointerEvent, at(k * perFrame));
    else for (let j = 1; j <= perFrame; j++) fire(canvas, "pointermove", PointerEvent, at((k - 1) * perFrame + j));
    jsMs.push(performance.now() - t0);
  }
  await invoke("log", { line: `Tauri：フレーム間隔（${intervals.length}）中央値 ${pct(intervals, .5).toFixed(1)}ms／95% ${pct(intervals, .95).toFixed(1)}ms`
    + `　1フレームの描画の JS 時間 中央値 ${pct(jsMs, .5).toFixed(2)}ms／95% ${pct(jsMs, .95).toFixed(2)}ms`
    + `　区間の平均 ${(jsMs.reduce((a, b) => a + b, 0) / latency.segmentUs.length * 1000).toFixed(1)}µs（${latency.segmentUs.length} 件）` });
  await invoke("quit");
}

// 筆圧の自己確認：Rust がペンの事件（tabletPoint、筆圧つき）を窓に渡し、線の終わりに筆圧の範囲を記録する。
invoke("pen_selftest_mode").then(async (on) => {
  if (!on) return;
  await new Promise((r) => setTimeout(r, 800));
  for (let n = 1; ; n++) {
    await new Promise((r) => requestAnimationFrame(r));
    if (!(await invoke("pen_selftest_step", { n }))) break;
  }
  await new Promise((r) => setTimeout(r, 300));
  await invoke("quit");
});
// 日本語入力の自己確認：レイヤー名の欄に入り、Rust から IME の窓口（変換中→確定）を呼ぶ。
invoke("ime_selftest_mode").then(async (on) => {
  if (!on) return;
  await new Promise((r) => setTimeout(r, 800));
  const input = document.querySelector(".panel input[value='線画']");
  const seen = [];
  for (const type of ["compositionstart", "compositionupdate", "compositionend"]) {
    input.addEventListener(type, (e) => seen.push(`${type}(${e.data})`));
  }
  await invoke("ime_selftest_step", { step: 0 });
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  const pause = () => new Promise((r) => setTimeout(r, 150));
  await pause();
  for (const step of [1, 2]) {
    await invoke("ime_selftest_step", { step });
    await pause();
    await invoke("log", { line: `IME 変換中（${step}）：${JSON.stringify(input.value)}` });
  }
  await invoke("ime_selftest_step", { step: 3 });
  await pause();
  await invoke("log", { line: `IME 確定後：${JSON.stringify(input.value)}　事件：${seen.join(" ")}` });
  await invoke("quit");
});
// ---- 別の窓の台本（PMA_POPOUT_OUT があるときだけ）。窓ごとの写しと位置を保存し、動画は compose_popout.py で作る ----
async function popoutDemo(dir) {
  popoutMode = true;
  const log = [];
  let n = 0, caption = "", dragging = false;
  const g = { x: 500, y: 400 }; // 指先（画面の座標）
  const rects = async () => (windowsNow = await invoke("window_rects"));
  const win = (label) => windowsNow.find((w) => w.label === label);
  const tick = async () => {
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    await rects();
    await watchPopouts();
    windowsNow = await invoke("window_rects");
    const shots = windowsNow.map((w) => w.label === "main"
      ? invoke("snapshot", { path: `${dir}/main_${String(n).padStart(5, "0")}.png` })
      : invoke("snapshot_window", { label: w.label, path: `${dir}/${w.label}_${String(n).padStart(5, "0")}.png` })
        .catch(() => {})); // 戻して閉じた直後の窓は写せない
    await Promise.all(shots);
    log.push(JSON.stringify({ n, caption, pointer: [g.x, g.y], dragging, hint: hint.style.display === "flex",
      windows: windowsNow.map((w) => ({ label: w.label, title: w.label === "main" ? "PMA 試作：Tauri + dockview" : w.title, outer: w.outer, inner: w.inner })) }));
    n++;
  };
  const wait = async (k) => { for (let i = 0; i < k; i++) await tick(); };
  // 要素の画面の座標：メインの窓なら inner の位置を、別の窓ならその窓の inner の位置を足す。
  const screenOf = (el, label) => {
    const r = el.getBoundingClientRect();
    const w = win(label);
    // tao の中身の位置は窓の上端（題名の帯を含む）。ページは帯の下から始まるので、その分を足す。
    const bar = w.outer[3] - el.ownerDocument.defaultView.innerHeight;
    return { x: w.inner[0] + r.left + r.width / 2, y: w.inner[1] + bar + r.top + r.height / 2 };
  };
  const moveTo = async (to, frames = 14) => {
    const from = { ...g };
    for (let i = 1; i <= frames; i++) { const t = ease(i / frames); g.x = from.x + (to.x - from.x) * t; g.y = from.y + (to.y - from.y) * t; await tick(); }
  };
  const click = async (el, label) => {
    await moveTo(screenOf(el, label));
    dragging = true; await tick();
    el.click();
    dragging = false; await tick();
  };
  const labelOf = (id) => popouts.find((p) => p.group === api.getPanel(id).group)?.label;
  const moveWindow = async (label, target, frames = 45) => {
    const w = win(label);
    const start = { x: w.outer[0], y: w.outer[1] };
    const grip = { x: w.outer[2] / 2, y: 14 };
    await moveTo({ x: start.x + grip.x, y: start.y + grip.y }, 12);
    dragging = true;
    for (let i = 1; i <= frames; i++) {
      const t = ease(i / frames);
      const x = start.x + (target.x - start.x) * t, y = start.y + (target.y - start.y) * t;
      await invoke("move_window", { label, x, y });
      g.x = x + grip.x; g.y = y + grip.y;
      await tick();
    }
    dragging = false;
  };
  const findButton = (group) => group.element.querySelector("button.pma-popout, button.pma-back");

  await rects();
  caption = "Tauri + dockview：パネルを別の OS の窓に出す。「スクリプト」のグループの ⧉ を押す";
  await wait(15);
  api.getPanel("script").api.setActive();
  await wait(3);
  const scriptGroup = api.getPanel("script").group;
  await click(findButton(scriptGroup), "main");
  caption = "「スクリプト」と「レイヤー」のグループが自分の OS の窓になる（dockview の popout）";
  await wait(30);
  const sLabel = labelOf("script");
  if (!sLabel) throw new Error("別の窓が開かない");
  const main = win("main");
  // 別の画面があればその画面へ、無ければメインの窓の右へ。
  const mcx = main.outer[0] + main.outer[2] / 2;
  const other = (await invoke("monitors")).find((m) => mcx < m[0] || mcx > m[0] + m[2]);
  const outX = other ? other[0] + 80 : main.outer[0] + main.outer[2] + 30;
  caption = other ? "別の窓をもう1つの画面へ動かす（台本は OS の窓を動かす命令で動かす）"
    : "別の窓をメインの窓の外へ動かす（台本は OS の窓を動かす命令で動かす）";
  await moveWindow(sLabel, { x: outX, y: main.outer[1] + 20 });
  await wait(10);

  caption = "別の窓の中で入力する（窓の外でもふつうに使える）";
  api.getPanel("script").api.setActive();
  await wait(3);
  const textarea = api.getPanel("script").group.element.ownerDocument.querySelector("textarea");
  await click(textarea, sLabel);
  textarea.focus();
  for (const c of "\nlayer.hide(\"影\")  # 別の窓から") {
    textarea.value += c;
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    await tick(); await tick();
  }
  await wait(15);

  caption = "「タイムライン」も別の窓に出して、スクリプトの下に置く";
  await click(findButton(api.getPanel("timeline").group), "main");
  await wait(25);
  const tLabel = labelOf("timeline");
  await moveWindow(tLabel, { x: outX, y: main.outer[1] + 440 }, 40);
  await wait(10);
  const report = await invoke("os_windows");
  await rects();
  const m = win("main").outer;
  for (const p of popouts) {
    const o = win(p.label).outer;
    const outside = o[0] >= m[0] + m[2] || o[0] + o[2] <= m[0] || o[1] >= m[1] + m[3] || o[1] + o[3] <= m[1];
    report.push(`別の窓 ${p.label}（${p.group.panels.map((x) => x.title).join("・")}）：外枠 ${o.map((v) => v.toFixed(0)).join(",")}、メインの窓 ${m.map((v) => v.toFixed(0)).join(",")} の外に${outside ? "ある" : "ない"}`);
  }
  await invoke("log", { line: "OS の窓：\n  " + report.join("\n  ") });
  await wait(10);

  caption = "「メインの窓に戻す」→ タイムラインがもとの場所に戻る";
  await click(findButton(api.getPanel("timeline").group), tLabel);
  await wait(30);

  caption = "スクリプトの窓をメインの窓の上まで動かして止める → もとの場所に戻る";
  const mw = win("main"), sw = win(sLabel);
  await moveWindow(sLabel, { x: mw.inner[0] + mw.inner[2] / 2 - sw.outer[2] / 2, y: mw.inner[1] + mw.inner[3] / 2 - sw.outer[3] / 2 });
  await wait(40);
  await invoke("write_text", { path: `${dir}/frames.jsonl`, text: log.join("\n") });
  await invoke("write_text", { path: `${dir}/report.txt`, text: report.join("\n") });
  await invoke("log", { line: `${n} フレームを ${dir} に保存　残りの別の窓：${popouts.length}` });
  await invoke("quit");
}
invoke("popout_out").then((dir) => dir && setTimeout(() => popoutDemo(dir).catch((e) => invoke("log", { line: `台本の失敗：${e} ${e.stack}` }).then(() => invoke("quit"))), 800));
invoke("latency_mode").then((on) => on && setTimeout(() => latencyRun().catch((e) => invoke("log", { line: `${e}` })), 600));
invoke("log", { line: "ui 起動" });
invoke("demo_out").then((dir) => {
  invoke("log", { line: `demo_out=${dir}` });
  if (dir) {
    out = dir;
    setTimeout(() => demo().catch((e) => invoke("log", { line: `台本の失敗：${e} ${e.stack}` })), 600);
  }
});
