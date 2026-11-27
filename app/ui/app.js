// 0.9.0 の画面の骨組み。今は筆圧つきの線を描くだけ。
//
// 入力の列は OS で分ける。
// - macOS：Rust（pen.rs）が NSEvent から送る `pen` の事件だけを使う。WKWebView の
//   PointerEvent は筆圧が 0 で、位置の列も別なので、線を描くのには使わない。
// - それ以外（Windows の WebView2 など）：PointerEvent の筆圧をそのまま使う。
"use strict";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const MAX_WIDTH = 24; // 筆圧 1 のときの線の太さ（CSS の px）

const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");
const statusLine = document.getElementById("status");

// 描いた線の点（自己確認で調べる）。{x, y, pressure, device}
let stroke = null;
const strokes = [];

function fitCanvas() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(rect.width * dpr);
  const h = Math.round(rect.height * dpr);
  if (canvas.width === w && canvas.height === h) return;
  canvas.width = w;
  canvas.height = h;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  redraw();
}

function redraw() {
  const rect = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, rect.width, rect.height);
  for (const s of strokes) drawStroke(s);
}

function drawSegment(a, b) {
  const width = Math.max(0.5, MAX_WIDTH * (a.pressure + b.pressure) / 2);
  ctx.strokeStyle = b.device === "eraser" ? "#fff" : "#000";
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.lineTo(b.x, b.y);
  ctx.stroke();
}

function drawStroke(points) {
  for (let i = 1; i < points.length; i++) drawSegment(points[i - 1], points[i]);
}

// 窓の中身の座標（CSS の px）をキャンバスの座標にする。キャンバスの外なら null。
function toCanvas(x, y) {
  const rect = canvas.getBoundingClientRect();
  const cx = x - rect.left;
  const cy = y - rect.top;
  return { x: cx, y: cy, inside: cx >= 0 && cy >= 0 && cx < rect.width && cy < rect.height };
}

function showStatus(device, pressure, tiltX, tiltY) {
  const name = { mouse: "マウス", pen: "ペン", eraser: "消しゴム" }[device] ?? device;
  statusLine.textContent =
    `入力：${name}　筆圧 ${pressure.toFixed(3)}　傾き ${tiltX.toFixed(2)}, ${tiltY.toFixed(2)}`;
}

// 1点を受け取る。phase は down / move / up。
function feed(phase, device, x, y, pressure, tiltX, tiltY) {
  showStatus(device, pressure, tiltX, tiltY);
  const p = toCanvas(x, y);
  if (phase === "down") {
    // 線はキャンバスの上で押したときだけ始める（ボタンなどの上は除く）。
    if (!p.inside || document.elementFromPoint(x, y) !== canvas) return;
    stroke = [{ x: p.x, y: p.y, pressure, device }];
    strokes.push(stroke);
    drawSegment(stroke[0], stroke[0]);
    return;
  }
  if (!stroke) return;
  if (phase === "up") {
    stroke = null;
    return;
  }
  const point = { x: p.x, y: p.y, pressure, device };
  drawSegment(stroke[stroke.length - 1], point);
  stroke.push(point);
}

function usePointerEvents() {
  const device = (e) => (e.pointerType === "pen" ? (e.button === 5 || (e.buttons & 32) ? "eraser" : "pen") : "mouse");
  const pressure = (e) => (e.pointerType === "pen" ? e.pressure : 1);
  const tilt = (e) => [(e.tiltX || 0) / 90, (e.tiltY || 0) / 90];
  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId);
    feed("down", device(e), e.clientX, e.clientY, pressure(e), ...tilt(e));
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!stroke) return;
    // 1フレームのあいだの点もまとめて受け取る。
    const events = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
    for (const c of events) feed("move", device(c), c.clientX, c.clientY, pressure(c), ...tilt(c));
  });
  const end = (e) => feed("up", device(e), e.clientX, e.clientY, 0, 0, 0);
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
}

async function useNativePen() {
  await listen("pen", ({ payload: s }) => feed(s.phase, s.device, s.x, s.y, s.pressure, s.tilt_x, s.tilt_y));
}

// 筆圧の自己確認：Rust がペンの形の事件を窓へ渡し、線が筆圧どおりに描けたかを調べる。
async function runPenSelftest() {
  const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
  await frame();
  await frame();
  const settle = () => new Promise((r) => setTimeout(r, 300));
  // 1本目：ペンの形の事件（筆圧 0.1 → 1.0 → 0.1）。
  for (let n = 1; ; n++) {
    if (!(await invoke("pen_selftest_step", { n, mouse: false }))) break;
    await frame();
  }
  await settle();
  // 2本目：マウスの事件を事件の列に入れる（ローカルモニターを通る）。
  for (let n = 1; ; n++) {
    if (!(await invoke("pen_selftest_step", { n, mouse: true }))) break;
    await frame();
  }
  await settle();
  const s = strokes[0] ?? [];
  const ps = s.map((p) => p.pressure);
  const min = ps.length ? Math.min(...ps) : 0;
  const max = ps.length ? Math.max(...ps) : 0;
  const pen = s.length > 0 && s.every((p) => p.device === "pen");
  const penOk = s.length === 61 && pen && Math.abs(min - 0.1) < 0.01 && Math.abs(max - 1) < 0.01;
  const m = strokes[1] ?? [];
  const mouseOk = m.length === 4 && m.every((p) => p.device === "mouse" && p.pressure === 1);
  const ok = strokes.length === 2 && penOk && mouseOk;
  await invoke("report", {
    line: `筆圧の自己確認：${ok ? "成功" : "失敗"}（線 ${strokes.length} 本）\n` +
      `  ペン：${penOk ? "成功" : "失敗"}（${s.length} 点、ペン ${pen ? "はい" : "いいえ"}、筆圧 ${min.toFixed(2)}〜${max.toFixed(2)}）\n` +
      `  マウス（モニター経由）：${mouseOk ? "成功" : "失敗"}（${m.length} 点）`,
  });
  await invoke("quit", { code: ok ? 0 : 1 });
}

async function main() {
  const info = await invoke("startup");
  fitCanvas();
  new ResizeObserver(fitCanvas).observe(canvas);
  document.getElementById("clear").addEventListener("click", () => {
    strokes.length = 0;
    stroke = null;
    redraw();
  });
  if (info.native_pen) await useNativePen();
  else usePointerEvents();
  if (info.pen_selftest) await runPenSelftest();
}

main();
