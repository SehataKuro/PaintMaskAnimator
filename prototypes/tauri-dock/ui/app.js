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

const api = DV.createDockview(document.getElementById("dock"), {
  theme: DV.themeDark,
  createComponent: (options) => {
    const element = PANELS[options.name][1]();
    return { element, init: () => {} };
  },
});
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
invoke("latency_mode").then((on) => on && setTimeout(() => latencyRun().catch((e) => invoke("log", { line: `${e}` })), 600));
invoke("log", { line: "ui 起動" });
invoke("demo_out").then((dir) => {
  invoke("log", { line: `demo_out=${dir}` });
  if (dir) {
    out = dir;
    setTimeout(() => demo().catch((e) => invoke("log", { line: `台本の失敗：${e} ${e.stack}` })), 600);
  }
});
