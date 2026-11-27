// 0.9.0 の画面の骨組み。キャンバスの表示（viewer.js）と、筆圧つきの線の入力。
//
// 入力の列は OS で分ける。
// - macOS：Rust（pen.rs）が NSEvent から送る `pen` の事件だけを使う。WKWebView の
//   PointerEvent は筆圧が 0 で、位置の列も別なので、線を描くのには使わない。
// - それ以外（Windows の WebView2 など）：PointerEvent の筆圧をそのまま使う。
//
// 線の点は画像の座標にして1フレーム分ずつ Rust へ送り、変わった範囲の画素を受け取って
// テクスチャへ書き戻す。
import { Viewer } from "./viewer.js";
import { createLayout } from "./layout.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const statusLine = document.getElementById("status");

// キャンバスのパネル。WebGL の canvas と、表示の切り替えボタン。
const canvasPanel = document.createElement("div");
canvasPanel.className = "canvas-panel";
const canvas = document.createElement("canvas");
canvasPanel.appendChild(canvas);
const viewButtons = document.createElement("div");
viewButtons.className = "view-buttons";
viewButtons.innerHTML = '<button id="fit" type="button">全体を表示</button><button id="actual" type="button">100%</button>';
canvasPanel.appendChild(viewButtons);
createLayout(document.getElementById("dock"), { canvas: () => canvasPanel });
const viewer = new Viewer(canvas);

// 描いた線の点（自己確認で調べる）。{x, y, pressure, device}（x, y は画面の座標）
let stroke = null;
const strokes = [];
// Rust へまだ送っていない点（画像の座標）と、線の始まりかどうか。
let pending = [];
let pendingBegin = false;
let sending = false;
// Rust とのやり取り1回の時間（ms、自己確認で出す）。
const strokeTimes = [];

// Rust から届いた「範囲＋画素」をテクスチャに書く。
function applyRect(buffer) {
  const head = new DataView(buffer, 0, 16);
  const [x, y, w, h] = [0, 4, 8, 12].map((o) => head.getUint32(o, true));
  if (w && h) viewer.writeRect(x, y, w, h, new Uint8Array(buffer, 16));
}

async function flush() {
  if (sending || pending.length === 0) return;
  sending = true;
  const points = pending;
  const begin = pendingBegin;
  pending = [];
  pendingBegin = false;
  try {
    const t0 = performance.now();
    applyRect(await invoke("stroke", { points, begin }));
    strokeTimes.push(performance.now() - t0);
  } finally {
    sending = false;
  }
  if (pending.length) requestAnimationFrame(flush);
}

let viewText = "";
function showStatus(device, pressure, tiltX, tiltY) {
  const name = { mouse: "マウス", pen: "ペン", eraser: "消しゴム" }[device] ?? device;
  statusLine.textContent =
    `入力：${name}　筆圧 ${pressure.toFixed(3)}　傾き ${tiltX.toFixed(2)}, ${tiltY.toFixed(2)}　${viewText}`;
}
viewer.onchange = () => showSummary();

// 窓の中身の座標（CSS の px）をキャンバス要素の座標にする。
function toCanvas(x, y) {
  const rect = canvas.getBoundingClientRect();
  const cx = x - rect.left;
  const cy = y - rect.top;
  return { x: cx, y: cy, inside: cx >= 0 && cy >= 0 && cx < rect.width && cy < rect.height };
}

// 画面を動かしている途中（スペース＋ドラッグ、中ボタン）。
let panning = null;
let spaceDown = false;

// 1点を受け取る。phase は down / move / up。x, y は窓の中身の座標。
function feed(phase, device, x, y, pressure, tiltX, tiltY) {
  showStatus(device, pressure, tiltX, tiltY);
  const p = toCanvas(x, y);
  if (phase === "down") {
    // 線はキャンバスの上で押したときだけ始める（ボタンなどの上は除く）。
    if (!p.inside || document.elementFromPoint(x, y) !== canvas) return;
    if (spaceDown) {
      panning = { x, y };
      return;
    }
    stroke = [{ x: p.x, y: p.y, pressure, device }];
    strokes.push(stroke);
    pending = [];
    pendingBegin = true;
  } else if (panning) {
    viewer.panBy(x - panning.x, y - panning.y);
    panning = phase === "up" ? null : { x, y };
    return;
  } else if (!stroke) {
    return;
  } else if (phase === "up") {
    stroke = null;
    requestAnimationFrame(flush);
    return;
  } else {
    stroke.push({ x: p.x, y: p.y, pressure, device });
  }
  const q = viewer.screenToImage(p.x, p.y);
  pending.push({ x: q.x, y: q.y, pressure });
  requestAnimationFrame(flush);
}

function usePointerEvents() {
  const device = (e) => (e.pointerType === "pen" ? (e.button === 5 || (e.buttons & 32) ? "eraser" : "pen") : "mouse");
  const pressure = (e) => (e.pointerType === "pen" ? e.pressure : 1);
  const tilt = (e) => [(e.tiltX || 0) / 90, (e.tiltY || 0) / 90];
  canvas.addEventListener("pointerdown", (e) => {
    if (e.button === 1) return;
    canvas.setPointerCapture(e.pointerId);
    feed("down", device(e), e.clientX, e.clientY, pressure(e), ...tilt(e));
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!stroke && !panning) return;
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

function useViewControls() {
  // ホイール：カーソルの位置を中心にズーム（Qt 版と同じ 1 目盛り 1.15 倍）。
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const p = toCanvas(e.clientX, e.clientY);
    const delta = e.deltaMode === 1 ? -e.deltaY * 40 : -e.deltaY;
    viewer.zoomAt(1.15 ** (delta / 120), p.x, p.y);
  }, { passive: false });
  // 中ボタンのドラッグで画面を動かす。
  let middle = null;
  canvas.addEventListener("pointerdown", (e) => {
    if (e.button !== 1) return;
    middle = { x: e.clientX, y: e.clientY };
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!middle) return;
    viewer.panBy(e.clientX - middle.x, e.clientY - middle.y);
    middle = { x: e.clientX, y: e.clientY };
  });
  canvas.addEventListener("pointerup", () => (middle = null));
  window.addEventListener("keydown", (e) => {
    if (e.code === "Space") spaceDown = true;
  });
  window.addEventListener("keyup", (e) => {
    if (e.code === "Space") spaceDown = false;
  });
  document.getElementById("fit").addEventListener("click", () => viewer.fit());
  document.getElementById("actual").addEventListener("click", () => {
    const r = canvas.getBoundingClientRect();
    viewer.zoomAt(1 / viewer.zoom, r.width / 2, r.height / 2);
  });
  let fitted = false;
  new ResizeObserver(() => {
    // 配置が決まって大きさが出たところで、一度だけ全体を表示する。
    if (!fitted && canvas.getBoundingClientRect().width > 0) {
      fitted = true;
      viewer.fit();
    }
    viewer.requestDraw();
  }).observe(canvas);
}

// 筆圧の自己確認：Rust がペンの形の事件を窓へ渡し、線が筆圧どおりに届いたかを調べる。
function median(xs) {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

async function runPenSelftest() {
  const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
  const settle = () => new Promise((r) => setTimeout(r, 300));
  await frame();
  await frame();
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
  // 描いた線が Rust の画像に入り、表示へ戻ってきたか（線の中ほどの画素が黒い）。
  const mid = s[30];
  const q = mid ? viewer.screenToImage(mid.x, mid.y) : { x: -1, y: -1 };
  const buffer = await invoke("read_canvas");
  const [w] = [new DataView(buffer, 8, 4).getUint32(0, true)];
  const i = 16 + (Math.floor(q.y) * w + Math.floor(q.x)) * 4;
  const painted = new Uint8Array(buffer)[i] === 0;
  const ok = strokes.length === 2 && penOk && mouseOk && painted;
  await invoke("report", {
    line: `筆圧の自己確認：${ok ? "成功" : "失敗"}（線 ${strokes.length} 本）\n` +
      `  ペン：${penOk ? "成功" : "失敗"}（${s.length} 点、ペン ${pen ? "はい" : "いいえ"}、筆圧 ${min.toFixed(2)}〜${max.toFixed(2)}）\n` +
      `  マウス（モニター経由）：${mouseOk ? "成功" : "失敗"}（${m.length} 点）\n` +
      `  画像に描けたか：${painted ? "成功" : "失敗"}\n` +
      `  Rust とのやり取り：${strokeTimes.length} 回、中央値 ${median(strokeTimes).toFixed(2)}ms、最大 ${Math.max(0, ...strokeTimes).toFixed(2)}ms`,
  });
  await invoke("quit", { code: ok ? 0 : 1 });
}

// 作品の概要（Rust の summary）。
let summary = null;
function showSummary() {
  if (!summary) return;
  const name = summary.path ? summary.path.split(/[\\/]/).pop() : "（未保存）";
  document.title = `${name} — PaintMaskAnimator`;
  viewText = `${summary.frames ? `コマ ${summary.frame + 1}/${summary.frames}　` : ""}表示 ${(viewer.zoom * 100).toFixed(0)}%　回転 ${viewer.rotation.toFixed(0)}°`;
  showStatus("mouse", 0, 0, 0);
}

// 画像の大きさが変わったときはテクスチャを作り直し、全体を読み直す。
async function reloadCanvas(fit) {
  summary = await invoke("summary");
  if (summary.width !== viewer.width || summary.height !== viewer.height) {
    viewer.setImageSize(summary.width, summary.height);
    fit = true;
  }
  applyRect(await invoke("read_canvas"));
  if (fit) viewer.fit();
  showSummary();
}

async function openProject(path) {
  try {
    await invoke("open_project", { path });
    await reloadCanvas(true);
  } catch (e) {
    statusLine.textContent = `開けませんでした：${e}`;
  }
}

async function stepFrame(delta) {
  if (!summary?.frames) return;
  const n = summary.frames;
  summary = await invoke("set_frame", { frame: (summary.frame + delta + n) % n });
  await reloadCanvas(false);
}

function useDocumentControls() {
  // .pman を窓へ落とすと開く。
  listen("tauri://drag-drop", ({ payload }) => {
    const path = payload.paths?.find((p) => p.toLowerCase().endsWith(".pman"));
    if (path) openProject(path);
  });
  window.addEventListener("keydown", async (e) => {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
    const mod = e.metaKey || e.ctrlKey;
    if (e.key === "ArrowRight" && !mod) {
      e.preventDefault();
      stepFrame(1);
    } else if (e.key === "ArrowLeft" && !mod) {
      e.preventDefault();
      stepFrame(-1);
    } else if (mod && (e.key.toLowerCase() === "y" || (e.shiftKey && e.key.toLowerCase() === "z"))) {
      e.preventDefault();
      if (await invoke("redo")) await reloadCanvas(false);
    } else if (mod && e.key.toLowerCase() === "z") {
      e.preventDefault();
      if (await invoke("undo")) await reloadCanvas(false);
    } else if (mod && e.key.toLowerCase() === "s") {
      e.preventDefault();
      try {
        const path = await invoke("save_project", { path: null });
        statusLine.textContent = `保存しました：${path}`;
      } catch (err) {
        statusLine.textContent = `保存できませんでした：${err}`;
      }
    }
  });
}

async function main() {
  const info = await invoke("startup");
  await reloadCanvas(true);
  useDocumentControls();
  if (info.open) await openProject(info.open);
  useViewControls();
  if (info.native_pen) await useNativePen();
  else usePointerEvents();
  if (info.pen_selftest) await runPenSelftest();
}

main().catch((e) => {
  statusLine.textContent = `起動できなかった：${e}`;
  invoke("report", { line: `起動できなかった：${e}` });
});
