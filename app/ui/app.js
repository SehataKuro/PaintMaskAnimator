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
// タイムラインのパネル（timeline.js）。
import { createTimelinePanel } from "./timeline.js";
// 色のパネル（カラーサークル・カラースライダー・使用色。color_state.js が状態を持つ）。
import { createColorWheelPanel } from "./color_wheel.js";
import { createColorSlidersPanel } from "./color_sliders.js";
import { createUsedColorsPanel } from "./used_colors.js";
import { eyedrop, startColors } from "./color_state.js";
// ツール・ツールプロパティ・アクションのパネルと、キャンバスの上のツール（tools.js ほか）。
import { toolStore, createToolsPanel } from "./tools.js";
import { createToolPropertiesPanel } from "./tool_properties.js";
import { createActionsPanel } from "./actions.js";
import { Overlay } from "./overlay.js";
import { createCanvasTools } from "./canvas_tools.js";
// 塗りの操作（paint_ops.js）：串刺し塗りの案内・自動で塗る・見本との違いチェック。
import { installPaintOps } from "./paint_ops.js";
import { installExport } from "./export.js"; // 書き出し（export.js）。
// 読み込み（imports.js）。
import { installImports, handleDrop } from "./imports.js"; // 【Group B】handleDrop
import { installEditOps } from "./edit_ops.js"; // 【Group B】編集・初期状態・線整え
import { installTimeRemap } from "./time_remap.js"; // 【タイムリマップの貼り付け】
import { installTween } from "./tween.js"; // 【トゥイーン】
import { runToolsSelftest } from "./tools_selftest.js";
// 【アプリの外枠】メニュー・ファイル・環境設定など（shell.js）と、ヒストリーのパネル。
import { initShell } from "./shell.js";
import { createHistoryPanel } from "./history_panel.js";
import { createCanvasView } from "./canvas_view.js"; // 【キャンバスの見え方】下のバー（canvas_view.js）

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const statusLine = document.getElementById("status");

// キャンバスのパネル。WebGL の canvas と、【キャンバスの見え方】下のバー（canvas_view.js）。
const canvasDock = document.createElement("div");
canvasDock.className = "canvas-dock";
const canvasPanel = document.createElement("div");
canvasPanel.className = "canvas-panel";
const canvas = document.createElement("canvas");
canvasPanel.appendChild(canvas);
canvasDock.appendChild(canvasPanel);
let timelinePanel = null;
createLayout(document.getElementById("dock"), {
  canvas: () => canvasDock,
  // タイムラインのパネル（timeline.js）。文書の変化は document-changed の事件で知る。
  // ツールのパネル（tools.js・tool_properties.js・actions.js）。
  tools: () => createToolsPanel(),
  "tool-properties": () => createToolPropertiesPanel(),
  actions: () => createActionsPanel(),
  timeline: () => (timelinePanel = createTimelinePanel({ invoke })),
  // 【アプリの外枠】ヒストリー（history_panel.js）。
  history: () => createHistoryPanel({ invoke, listen }),
  // 色のパネル（color_wheel.js・color_sliders.js・used_colors.js）。描画色はツールの設定
  // （tools-changed）、使用色とチャートは palette-changed・chart-changed で表示を合わせる。
  "color-wheel": () => createColorWheelPanel(),
  "color-sliders": () => createColorSlidersPanel(),
  "used-colors": () => createUsedColorsPanel(),
});
// 【アプリの外枠】shell.js（main で始める）。
let shell = null;
const viewer = new Viewer(canvas);

// 描いた線の点（自己確認で調べる）。{x, y, pressure, device}（x, y は画面の座標）
let stroke = null;
const strokes = [];
// Rust へ送る線の仕事の列。始め・続き（まとめた点）・終わりを、この順で1つずつ送る。
const queue = [];
let pendingPoints = [];
let sending = false;
// 線の太さ・色・手ぶれ補正はツールの設定（tools.rs）を使う。筆圧の自己確認だけは決まった値で描く。
const selftestTool = { size: 5, color: [0, 0, 0], stabilizer: 0 };
let penSelftest = false;
// Rust とのやり取り1回の時間（ms、自己確認で出す）。
const strokeTimes = [];

// Rust から届いた「範囲＋画素」をテクスチャに書く。
function applyRect(buffer) {
  const head = new DataView(buffer, 0, 16);
  const [x, y, w, h] = [0, 4, 8, 12].map((o) => head.getUint32(o, true));
  if (w && h) viewer.writeRect(x, y, w, h, new Uint8Array(buffer, 16));
}

async function call(job) {
  const t0 = performance.now();
  applyRect(await invoke(job.cmd, job.args));
  strokeTimes.push(performance.now() - t0);
}

// 列を順に送る。続きの点は1フレーム分ずつまとめて送る。
async function pump() {
  if (sending) return;
  sending = true;
  try {
    while (queue.length || pendingPoints.length) {
      if (queue.length && queue[0].cmd !== "stroke_end") {
        await call(queue.shift());
        continue;
      }
      if (pendingPoints.length) {
        const points = pendingPoints;
        pendingPoints = [];
        await call({ cmd: "stroke_move", args: { points } });
        continue;
      }
      await call(queue.shift());
    }
  } catch (e) {
    statusLine.textContent = `描けませんでした：${e}`;
  } finally {
    sending = false;
  }
}

let viewText = "";
function showStatus(device, pressure, tiltX, tiltY) {
  const name = { mouse: "マウス", pen: "ペン", eraser: "消しゴム" }[device] ?? device;
  statusLine.textContent =
    `入力：${name}　筆圧 ${pressure.toFixed(3)}　傾き ${tiltX.toFixed(2)}, ${tiltY.toFixed(2)}　${viewText}`;
}
viewer.onchange = () => showSummary();
// 【キャンバスの見え方】下のバー（拡大・回転・反転・書き出し・見え方のチェック）。
const canvasView = createCanvasView({ viewer, busy: () => stroke !== null });
canvasDock.appendChild(canvasView.bar);
// キャンバスの上の層（選択範囲の点線など）と、ブラシ以外のツールの操作（overlay.js・canvas_tools.js）。
const overlay = new Overlay(canvasPanel, viewer);
const canvasTools = createCanvasTools({ canvas, viewer, overlay, applyRect });
installPaintOps({ host: canvasPanel, viewer, applyRect }); // paint_ops.js
installExport(); // 書き出し（export.js）。
installImports({ save: () => shell?.run("a_save_project"), openProject }); // imports.js（CLIP の置き換えの前に保存する・【Group B】落とした .pman）
installEditOps({ applyRect }); // 【Group B】edit_ops.js
installTimeRemap(); // 【タイムリマップの貼り付け】time_remap.js
installTween(); // 【トゥイーン】tween.js

// 窓の中身の座標（CSS の px）をキャンバス要素の座標にする。
function toCanvas(x, y) {
  const rect = canvas.getBoundingClientRect();
  const cx = x - rect.left;
  const cy = y - rect.top;
  return { x: cx, y: cy, inside: cx >= 0 && cy >= 0 && cx < rect.width && cy < rect.height };
}

// 画面を動かしている途中（ハンドは canvas_tools.js が受け持つ）。
let panning = null;

// 1点を受け取る。phase は down / move / up。x, y は窓の中身の座標。
function feed(phase, device, x, y, pressure, tiltX, tiltY) {
  showStatus(device, pressure, tiltX, tiltY);
  const p = toCanvas(x, y);
  // 色のパネルのスポイト（カラーピッカー・置換色）が待っていれば、線もツールも使わずに
  // ツールのスポイトと同じ拾い方（tool_sample_color）で色を拾う。
  if (phase === "down" && !stroke && eyedrop.waiting()) {
    if (p.inside && document.elementFromPoint(x, y) === canvas) pickCanvasColor(p.x, p.y);
    return;
  }
  // ブラシの線の途中でなければ、ツールごとの操作に任せる（canvas_tools.js）。
  if (!stroke && !panning && (phase !== "down" || (p.inside && document.elementFromPoint(x, y) === canvas)) &&
      canvasTools.feed(phase, device, p.x, p.y, pressure)) return;
  if (phase === "down") {
    // 線はキャンバスの上で押したときだけ始める（ボタンなどの上は除く）。
    if (!p.inside || document.elementFromPoint(x, y) !== canvas) return;
    stroke = [{ x: p.x, y: p.y, pressure, device }];
    strokes.push(stroke);
    pendingPoints = [];
    const q = viewer.screenToImage(p.x, p.y);
    queue.push({
      cmd: "stroke_begin",
      args: {
        tool: { ...(penSelftest ? selftestTool : {}), eraser: device === "eraser", zoom: viewer.zoom },
        point: { x: q.x, y: q.y, pressure, pen: device !== "mouse", time: performance.now() },
      },
    });
    pump();
    return;
  } else if (panning) {
    viewer.panBy(x - panning.x, y - panning.y);
    panning = phase === "up" ? null : { x, y };
    return;
  } else if (!stroke) {
    return;
  } else if (phase === "up") {
    const last = stroke[stroke.length - 1];
    stroke = null;
    canvasTools.strokeEnded(last.x, last.y);
    const q = viewer.screenToImage(last.x, last.y);
    queue.push({
      cmd: "stroke_end",
      args: { point: { x: q.x, y: q.y, pressure: last.pressure, pen: last.device !== "mouse", time: performance.now() } },
    });
    pump();
    return;
  } else {
    stroke.push({ x: p.x, y: p.y, pressure, device });
  }
  const q = viewer.screenToImage(p.x, p.y);
  pendingPoints.push({ x: q.x, y: q.y, pressure, pen: device !== "mouse", time: performance.now() });
  requestAnimationFrame(pump);
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
    if (!stroke && !panning && !canvasTools.active) return;
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
  // 【アプリの外枠】窓の題名は shell.js が付ける（Qt と同じ形）。
  void name;
  viewText = `${summary.frames ? `コマ ${summary.frame + 1}/${summary.frames}　` : ""}表示 ${(viewer.zoom * 100).toFixed(0)}%　回転 ${viewer.rotation.toFixed(0)}°`;
  showStatus("mouse", 0, 0, 0);
}

// 画像の大きさが変わったときはテクスチャを作り直し、全体を読み直す。
async function reloadCanvas(fit) {
  const t0 = performance.now();
  summary = await invoke("summary");
  if (summary.width !== viewer.width || summary.height !== viewer.height) {
    viewer.setImageSize(summary.width, summary.height);
    fit = true;
  }
  applyRect(await invoke("read_canvas"));
  if (fit) viewer.fit();
  showSummary();
  reloadTimes.push(performance.now() - t0);
  if (reloadTimes.length > 1000) reloadTimes.splice(0, 500);
  onCanvasShown?.(summary.frame);
}
// 読み直し1回の時間（ms、再生の自己確認で出す）と、読み直したときに呼ぶ関数（自己確認用）。
const reloadTimes = [];
let onCanvasShown = null;

// 文書が変わったら（Rust の document-changed）キャンバスを読み直す。読み直しの途中に
// 次の知らせが来たら、終わってから最後の状態を一度だけ読む（再生で知らせが溜まらない）。
let reloading = false;
let reloadAgain = false;
// 自己確認で自分で読み直しているあいだは、知らせで読み直さない。
let measuring = false;
let reloadFit = false;
async function scheduleReload(fit = false) {
  reloadFit ||= fit;
  if (reloading) {
    reloadAgain = true;
    return;
  }
  reloading = true;
  try {
    do {
      reloadAgain = false;
      const f = reloadFit;
      reloadFit = false;
      await reloadCanvas(f);
    } while (reloadAgain);
  } catch (e) {
    statusLine.textContent = `表示を読めませんでした：${e}`;
  } finally {
    reloading = false;
  }
}

// スポイト：表示しているキャンバスの画素（背景の表示色・置換のプレビューを含む）を拾う。
// 色のパネルのスポイト：ツールのスポイトと同じく、表示しているレイヤーの元の色を拾う
// （描画色は変えない）。透明・白は白（背景）。画像の外なら null。
async function pickCanvasColor(sx, sy) {
  const q = viewer.screenToImage(sx, sy);
  try {
    const picked = await invoke("tool_sample_color", { x: q.x, y: q.y, source: "display" });
    eyedrop.deliver(picked ? picked.rgb : null);
  } catch (e) {
    statusLine.textContent = String(e);
    eyedrop.deliver(null);
  }
}

async function useColorEvents() {
  // 描画色はツールの設定にあり、ブラシは Rust（tools.rs）がそこから描く色を取る。
  await startColors();
  // 表示の絞り込み・置換のプレビュー・背景の表示色が変わったら、キャンバスを読み直す。
  await listen("display-changed", () => {
    if (!measuring) scheduleReload();
  });
}

async function useDocumentEvents() {
  await listen("document-changed", ({ payload }) => {
    // 線（source が canvas）は、変わった範囲の画素を受け取って書き込み済み。
    if (payload.source === "canvas" || measuring) return;
    scheduleReload(payload.source === "open");
  });
}

async function openProject(path) {
  // 【アプリの外枠】未保存の確かめと、最近使ったファイルは shell.js。
  if (shell) {
    await shell.openDropped(path);
    return;
  }
  try {
    // 開けたら document-changed（source が open）で全体を表示し直す。
    await invoke("open_project", { path });
  } catch (e) {
    statusLine.textContent = `開けませんでした：${e}`;
  }
}

async function stepFrame(delta) {
  if (!summary?.frames) return;
  const n = summary.frames;
  // 表示は document-changed で読み直す（タイムラインも同じ知らせで今のコマを合わせる）。
  summary = await invoke("set_frame", { frame: (summary.frame + delta + n) % n });
}

function useDocumentControls() {
  // 【Group B】落としたファイル：キャンバスの上か、それ以外（窓・タイムライン）かで振り分ける
  // （.pman・.clip・タイムシート・画像。imports.js の handleDrop）。
  listen("tauri://drag-drop", ({ payload }) => {
    const paths = payload.paths ?? [];
    if (!paths.length) return;
    const ratio = window.devicePixelRatio || 1;
    const x = (payload.position?.x ?? -1) / ratio;
    const y = (payload.position?.y ?? -1) / ratio;
    const target = document.elementFromPoint(x, y);
    handleDrop(paths, Boolean(target && canvasPanel.contains(target))).catch((e) => (statusLine.textContent = String(e)));
  });
  // 取り消し・やり直しのキーはここだけ（キャンバスもタイムラインも同じ履歴）。表示は
  // document-changed で、キャンバスとタイムラインの両方が読み直す。
  window.addEventListener("keydown", async (e) => {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
    // タイムラインのパネルが受けたキー（左右のコマ送りなど）は二重に行わない。
    if (e.defaultPrevented) return;
    const mod = e.metaKey || e.ctrlKey;
    if (e.key === "ArrowRight" && !mod) {
      e.preventDefault();
      stepFrame(1);
    } else if (e.key === "ArrowLeft" && !mod) {
      e.preventDefault();
      stepFrame(-1);
    }
    // 【アプリの外枠】取り消し・やり直し・保存は、メニュー（menu.rs → shell.js）が受ける。
  });
}

// 【キャンバスの見え方】表示：拡大／縮小は、マウスカーソルの位置を中心にする（Qt の
// step_canvas_zoom：カーソルがキャンバスの外にあっても、その位置を中心にする）。
let lastPointer = null;
window.addEventListener("pointermove", (e) => (lastPointer = { x: e.clientX, y: e.clientY }), { capture: true, passive: true });
function zoomCenter(factor) {
  const r = canvas.getBoundingClientRect();
  const p = lastPointer ? toCanvas(lastPointer.x, lastPointer.y) : { x: r.width / 2, y: r.height / 2 };
  viewer.zoomAt(factor, p.x, p.y);
}

// 再生の自己確認：再生して、キャンバスが何 fps で今のコマを表示できたかを測る。
// 1) 再生（Rust の時計の fps どおりに進む）で、キャンバスに出せたコマの速さ。
// 2) 最速：コマを変えて全体を読み込むのを続けたときの速さ（再生の上限の目安）。
async function runPlaybackSelftest(count) {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  await wait(500);
  const view = await invoke("timeline_view");
  if (!view.loaded) {
    await invoke("report", { line: "再生の自己確認：失敗（作品を開いていません）" });
    await invoke("quit", { code: 1 });
    return;
  }
  const shown = [];
  onCanvasShown = (frame) => shown.push({ t: performance.now(), frame });
  reloadTimes.length = 0;
  await invoke("timeline_play", { on: true });
  const deadline = performance.now() + 60_000;
  while (shown.length < count && performance.now() < deadline) await wait(10);
  await invoke("timeline_play", { on: false });
  onCanvasShown = null;
  await wait(200);
  const rate = (items) =>
    items.length > 1 ? ((items.length - 1) * 1000) / (items[items.length - 1].t - items[0].t) : 0;
  const steady = shown.slice(Math.floor(shown.length / 2));
  const playTimes = [...reloadTimes];
  // 飛ばしたコマ（前に出したコマから 2 以上進んだ数。範囲の先頭へ戻るのは数えない）。
  let skipped = 0;
  for (let i = 1; i < shown.length; i++) {
    const d = shown[i].frame - shown[i - 1].frame;
    if (d > 1) skipped += d - 1;
  }
  // 最速：知らせを待たずに、コマを変えては全体を読む。
  const n = summary.frames;
  measuring = true;
  const t0 = performance.now();
  for (let i = 0; i < count; i++) {
    await invoke("set_frame", { frame: i % n });
    await reloadCanvas(false);
  }
  const max = (count * 1000) / (performance.now() - t0);
  measuring = false;
  const fps = view.fps;
  const ok = shown.length >= count && rate(steady) >= fps * 0.9;
  await invoke("report", {
    line: `再生の自己確認：${ok ? "成功" : "失敗"}（${summary.width}×${summary.height}、${n} コマ、${fps}fps）\n` +
      `  再生：${shown.length} コマを表示、全体 ${rate(shown).toFixed(1)}fps、後半 ${rate(steady).toFixed(1)}fps、飛ばしたコマ ${skipped}\n` +
      `  読み直し1回：中央値 ${median(playTimes).toFixed(2)}ms、最大 ${Math.max(0, ...playTimes).toFixed(2)}ms\n` +
      `  最速（コマを変えて全体を読む）：${max.toFixed(1)}fps`,
  });
  await invoke("quit", { code: ok ? 0 : 1 });
}

async function main() {
  const info = await invoke("startup");
  // ツールの設定（tools.rs）。筆圧の自己確認では、キャンバスのツールを使わずブラシで描く。
  penSelftest = !!info.pen_selftest;
  canvasTools.enabled = !penSelftest;
  await toolStore.init();
  await useDocumentEvents();
  await useColorEvents();
  await reloadCanvas(true);
  useDocumentControls();
  // 【アプリの外枠】メニュー・キー・題名など（shell.js）。
  shell = await initShell({
    invoke,
    listen,
    startup: info,
    hooks: {
      zoomIn: () => zoomCenter(1.25), // Qt と同じ 1.25 倍
      zoomOut: () => zoomCenter(1 / 1.25),
      fit: () => viewer.fit(),
      stepFrame,
      timelinePanel: () => timelinePanel,
      status: (text) => (statusLine.textContent = text),
    },
  });
  canvasView.setShell(shell); // 【キャンバスの見え方】メニューのチェック
  if (info.open) await shell.openPath(info.open);
  useViewControls();
  if (info.native_pen) await useNativePen();
  else usePointerEvents();
  if (info.pen_selftest) await runPenSelftest();
  if (info.playback_selftest) await runPlaybackSelftest(info.playback_selftest);
  if (info.colors_selftest != null) {
    // 色のパネルの自己確認（color_selftest.js）。普段は読み込まない。
    const { runColorsSelftest } = await import("./color_selftest.js");
    await runColorsSelftest({ invoke, pause: info.colors_selftest });
  }
  // キーの持ち主の自己確認（tools_selftest.js）。
  if (info.tools_selftest) await runToolsSelftest({ shell, canvas, viewer }); // 【オニオン】右ドラッグの確認に canvas・viewer
}

main().catch((e) => {
  statusLine.textContent = `起動できなかった：${e}`;
  invoke("report", { line: `起動できなかった：${e}` });
});
