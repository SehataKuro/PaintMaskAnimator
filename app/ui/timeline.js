// タイムラインのパネル（Qt の paintmaskanimator/timeline.py の移植）。
//
// 表示する値（行・区間・印・選択・現在コマ）は Rust（src/timeline.rs）が文書から組み立て、
// ここは描くことと、マウス・キーの操作を Rust の命令へ渡すことだけをする。
//
// 描き方：表は一枚の canvas に、見えている範囲だけを描く（長いカットでも軽い）。スクロールは
// 大きさだけを持つ div（tl-scroll）に任せ、レイヤー名の列と見出しの行も同じ canvas に
// スクロール位置から描く。レイヤー名とセルの行は同じ計算で並ぶので、どこまでスクロール
// しても横に揃う（#101）。
//
// 文書はキャンバスと同じ Session の作品。文書が変わると Rust が `document-changed` の事件を
// 送る。再生は Rust のスレッドがコマを進め、同じ事件（playhead 付き）で知らせる。受け取ったら
// 再生ヘッドだけを描き直す（#107。キャンバスも同じ事件で今のコマを表示する）。

const LAYER_WIDTH = 190;
const HEADER_HEIGHT = 25;
const BASE_CELL_WIDTH = 36;
const BASE_ROW_HEIGHT = 28;
const BAR_INSET = 3;
const GRIP_WIDTH = 3;
const HANDLE_HIT = 6;
const CHEVRON_WIDTH = 18;
const MARKER_WIDTH = 50;

// theme.py の dark の色。
const C = {
  window: "#22252b",
  surface: "#2b2f37",
  surfaceAlt: "#31363f",
  border: "#3c424c",
  text: "#e6e9ee",
  muted: "#9aa2ad",
  accent: "#4d8bff",
  selection: "#33507f",
  warning: "#e0a63a",
  error: "#ff6b6b",
  key: "#16445a",
  keyText: "#8ed8f5",
  hold: "#213c49",
  sheetKey: "#594514",
  sheetKeyText: "#ffd86a",
  sheetHold: "#463b21",
  uncreated: "#292d34",
  tween: "#4a2747",
  tweenText: "#f0a8e8",
  genga: "rgb(230,120,20)",
};

const FONT = '-apple-system, "Hiragino Sans", "Yu Gothic UI", "Meiryo", sans-serif';
const IS_MAC = /Mac/.test(navigator.platform);
const CTRL_LABEL = IS_MAC ? "⌘" : "Ctrl";

function alpha(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a / 255})`;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children) node.append(child);
  return node;
}

function framePosition(index) {
  return `${index + 1}コマ目`;
}

/// タイムラインのパネルを作る。invoke は Tauri の invoke。
///
/// 文書は Rust の Session の作品（キャンバスと同じ文書・同じ履歴）。文書が変わると Rust が
/// `document-changed` の事件を送るので、ほかの所（キャンバス・取り消し）からの変化は
/// それを受けて表示を読み直す。キャンバスもこの事件で表示を合わせる。
export function createTimelinePanel({ invoke }) {
  const listen = window.__TAURI__?.event?.listen;
  let view = null;
  let zoom = 1;
  const expanded = new Set(); // サムネイルを開いたレイヤー番号
  let layerSelection = new Set(); // レイヤー一覧で選んだレイヤー番号
  let drag = null;
  let hover = null;
  let spaceDown = false;
  let docVersion = 0;
  const thumbs = new Map();
  let drawQueued = false;

  // ---- 部品 ----
  const panel = el("div", { class: "tl-panel", tabindex: "0" });
  const toolbar = el("div", { class: "tl-toolbar" });
  const body = el("div", { class: "tl-body" });
  const scroll = el("div", { class: "tl-scroll" });
  const spacer = el("div", { class: "tl-spacer" });
  const canvas = el("canvas", { class: "tl-canvas" });
  const corner = el("div", { class: "tl-corner" });
  const rename = el("input", { class: "tl-rename", type: "text", spellcheck: "false" });
  const status = el("div", { class: "tl-status" });
  const empty = el("div", { class: "tl-empty" });
  scroll.append(spacer);
  body.append(scroll, canvas, corner, rename, empty);
  panel.append(toolbar, body, status);
  const ctx = canvas.getContext("2d");

  // ---- ツールバー（Qt の TimelineWidget の上の並び） ----
  const button = (label, title, onclick, cls = "") =>
    el("button", { type: "button", class: `tl-btn ${cls}`, title, onclick }, label);
  const sep = () => el("span", { class: "tl-sep" });
  const modeSheet = button("シート", "シート：タイムシートの絵番号を保持", () => setMode("sheet"), "tl-tab");
  const modeSequence = button("連番", "連番：左から順番に自動採番", () => setMode("sequence"), "tl-tab");
  const opacityLabel = el("span", { class: "tl-label" }, "不透明");
  const opacity = el("input", {
    type: "range", min: "0", max: "100", value: "100", class: "tl-opacity",
    title: "選択レイヤーの表示不透明度です。画像の色データ自体は変更しません。",
  });
  const opacityValue = el("span", { class: "tl-label tl-opacity-value" }, "100%");
  const addBlank = button("○", "空フレームを追加\n現在の位置に空フレーム（○）を作ります。", () => createBlankAtCurrent());
  const addExposure = button("⟶", "コマを伸ばす\n現在のキーフレーム／空フレームを1コマ伸ばします。", () => extendCurrent());
  const del = button("✕", "コマを削除（Shift+E）", () => edit({ op: "delete_frame" }));
  const remap = button("リマップ", "リマップを貼り付け（まだ移していません）", null);
  remap.disabled = true;
  const normalize = button("順番で正規化", "タイムラインの順番で正規化\n左から最初に出てくる順に番号を振り直します。実行前に、振り直す番号の一覧を確認できます。", () => normalizeDialog());
  const badge = el("span", { class: "tl-badge" });
  const first = button("⇤", "先頭へ（Home）", () => navigate("first"));
  const prev = button("‹", "前のフレーム（1）", () => navigate("previous"));
  const prevKey = button("«", "前のコマ（A）", () => navigate("previous_key"));
  const play = button("▶", "再生／停止（Enter）", () => togglePlay(), "tl-play");
  const nextKey = button("»", "次のコマ（S）", () => navigate("next_key"));
  const next = button("›", "次のフレーム（2）", () => navigate("next"));
  const last = button("⇥", "末尾へ（End）", () => navigate("last"));
  const counter = el("span", { class: "tl-counter", title: "現在位置 / 最後のコマ" });
  const onion = button("オニオンスキン", "オニオンスキン表示のON／OFF", () => setOptions({ onion: !view?.onion }), "tl-chip");
  // 【オニオン：onion.js】設定の窓（Qt の onion_settings。再クリックで閉じる）。
  const onionSettings = button("設定", "クリックでオニオンスキン設定を開き、再クリックで閉じます。", () => window.dispatchEvent(new CustomEvent("pma-onion-settings")), "tl-chip");
  const follow = button("追従", "再生中にタイムラインを追いかける（OFF にすると止めたときだけ合わせる）", () => setOptions({ followPlayback: !view?.follow_playback }), "tl-chip");
  const hint = el("span", {
    class: "tl-hint",
    title: `ドラッグ・Shift＋クリック：複数選択／選択範囲をそのままドラッグ：まとめて移動／番号・✗の箱：移動／Alt＋ドラッグ：複製／左右端：伸縮／Space：ハンド／${CTRL_LABEL}＋ホイール：拡大縮小`,
  }, `Shift/${CTRL_LABEL}：複数選択`);
  const fps = el("input", { type: "number", min: "1", max: "60", value: "24", class: "tl-fps", title: "fps" });
  const fpsLabel = el("span", { class: "tl-label" }, "fps");
  toolbar.append(
    modeSheet, modeSequence, sep(), opacityLabel, opacity, opacityValue, sep(),
    addBlank, addExposure, del, remap, sep(), normalize, badge,
    el("span", { class: "tl-stretch" }),
    first, prev, prevKey, play, nextKey, next, last, counter,
    el("span", { class: "tl-stretch" }),
    onion, onionSettings, follow, hint, fps, fpsLabel,
  );

  // レイヤー名の列の見出し（サムネイルの開閉・追加・削除）。
  const expandAll = button("▸", "すべてのレイヤーのサムネイルを開く／閉じる", () => toggleAllExpanded(), "tl-small");
  const layerAdd = button("+", "レイヤーを追加（Ctrl+Shift+N）", () => edit({ op: "add_layer" }), "tl-small");
  const layerDel = button("−", "レイヤーを削除", () => deleteActiveLayer(), "tl-small");
  corner.append(expandAll, el("span", { class: "tl-corner-label" }, "レイヤー"), el("span", { class: "tl-stretch" }), layerAdd, layerDel);

  empty.append(el("div", {}, "作品を開いていません（.pman を窓へ落とすと開きます）"));

  // ---- 寸法 ----
  const cellWidth = () => Math.max(18, Math.round(BASE_CELL_WIDTH * zoom));
  const rowHeight = () => Math.max(20, Math.round(BASE_ROW_HEIGHT * zoom));
  const expandedHeight = () => rowHeight() + Math.max(34, Math.round(rowHeight() * 1.4));
  let tops = [0];
  function layoutRows() {
    tops = [0];
    for (const row of view?.rows ?? []) {
      tops.push(tops[tops.length - 1] + (expanded.has(row.layer) ? expandedHeight() : rowHeight()));
    }
  }
  const totalHeight = () => tops[tops.length - 1];
  const viewportWidth = () => scroll.clientWidth;
  const viewportHeight = () => scroll.clientHeight;
  function columnCount() {
    if (!view) return 1;
    const visible = Math.ceil((viewportWidth() - LAYER_WIDTH) / cellWidth()) + 4;
    return Math.max(view.frame_count + 24, view.range[1] + 24, visible);
  }
  const sheet = () => view?.mode === "sheet";
  const rowOfLayer = (layer) => view.rows.findIndex((r) => r.layer === layer);
  const colX = (column) => LAYER_WIDTH + column * cellWidth() - scroll.scrollLeft;
  const rowY = (row) => HEADER_HEIGHT + tops[row] - scroll.scrollTop;

  function hit(clientX, clientY) {
    const rect = scroll.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const contentX = x - LAYER_WIDTH + scroll.scrollLeft;
    const contentY = y - HEADER_HEIGHT + scroll.scrollTop;
    const column = Math.floor(contentX / cellWidth());
    let row = -1;
    if (contentY >= 0) {
      for (let i = 0; i < tops.length - 1; i++) {
        if (contentY >= tops[i] && contentY < tops[i + 1]) row = i;
      }
    }
    return {
      x, y, column: Math.max(0, column), rawColumn: column, row,
      inCell: x - (LAYER_WIDTH + column * cellWidth() - scroll.scrollLeft),
      area: y < HEADER_HEIGHT ? (x < LAYER_WIDTH ? "corner" : "header") : x < LAYER_WIDTH ? "layers" : "cells",
      boundary: Math.max(0, Math.round(contentX / cellWidth())),
    };
  }

  /// 行 row の、列 column を含む区間。
  function spanAt(row, column) {
    const spans = view?.rows[row]?.spans;
    if (!spans) return null;
    let lo = 0;
    let hi = spans.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const s = spans[mid];
      if (column < s.start) hi = mid - 1;
      else if (column >= s.start + s.length) lo = mid + 1;
      else return s;
    }
    return null;
  }
  function firstVisible(items, column) {
    let lo = 0;
    let hi = items.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (items[mid].start + items[mid].length <= column) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  // ---- Rust とのやり取り ----
  function setView(next, { changed = false, reveal = false } = {}) {
    view = next;
    if (changed) {
      docVersion++;
      thumbs.clear();
    }
    if (!layerSelection.size || !view.rows.some((r) => layerSelection.has(r.layer))) {
      layerSelection = new Set([view.active_layer]);
    }
    for (const layer of [...expanded]) if (layer >= view.rows.length) expanded.delete(layer);
    syncToolbar();
    layoutRows();
    sizeSpacer();
    if (reveal) revealCurrent();
    requestDraw();
  }
  async function call(name, args = {}, options = {}) {
    try {
      setView(await invoke(name, args), options);
    } catch (e) {
      showStatus(String(e), true);
    }
  }
  async function edit(operation) {
    try {
      const result = await invoke("timeline_edit", { edit: operation });
      setView(result.view, { changed: !result.error });
      if (result.message) showStatus(result.message, result.error);
    } catch (e) {
      showStatus(String(e), true);
    }
  }
  const navigate = (to) => call("timeline_navigate", { to }, { reveal: true, changed: to === "next" });
  const setMode = (mode) => call("timeline_set_mode", { mode }, { changed: true, reveal: true });
  const setOptions = (options) => call("timeline_set_options", options);
  async function togglePlay() {
    if (!view?.loaded) return;
    await call("timeline_play", { on: !view.playing }, { reveal: true });
  }

  let statusTimer = 0;
  function showStatus(text, error = false) {
    status.textContent = text;
    status.classList.toggle("error", error);
    status.classList.add("shown");
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => status.classList.remove("shown"), 2600);
  }

  function syncToolbar() {
    const loaded = !!view?.loaded;
    empty.style.display = loaded ? "none" : "";
    modeSheet.classList.toggle("active", view?.mode === "sheet");
    modeSequence.classList.toggle("active", view?.mode === "sequence");
    addExposure.style.display = sheet() ? "" : "none";
    normalize.disabled = !sheet();
    badge.textContent = view?.normalize_pending ? String(view.normalize_pending) : "";
    badge.style.display = sheet() && view?.normalize_pending ? "" : "none";
    badge.title = `番号の順番がタイムラインとずれている絵：${view?.normalize_pending ?? 0}`;
    play.textContent = view?.playing ? "■" : "▶";
    play.classList.toggle("active", !!view?.playing);
    onion.classList.toggle("active", !!view?.onion);
    follow.classList.toggle("active", !!view?.follow_playback);
    if (document.activeElement !== fps) fps.value = String(view?.fps ?? 24);
    const active = view?.rows.find((r) => r.layer === view.active_layer);
    if (active && document.activeElement !== opacity) {
      opacity.value = String(Math.round(active.opacity * 100));
      opacityValue.textContent = `${Math.round(active.opacity * 100)}%`;
    }
    updateCounter();
    expandAll.textContent = allExpanded() ? "▾" : "▸";
  }
  function updateCounter() {
    if (!view?.loaded) {
      counter.innerHTML = "";
      return;
    }
    const current = sheet() ? view.current : view.current_column ?? 0;
    const total = sheet() ? view.sheet_frames : view.frame_count;
    counter.innerHTML = `<span class="tl-counter-current">${framePosition(current)}</span><span class="tl-counter-total"> / ${framePosition(total - 1)}</span>`;
  }

  function sizeSpacer() {
    spacer.style.width = `${LAYER_WIDTH + columnCount() * cellWidth()}px`;
    spacer.style.height = `${HEADER_HEIGHT + totalHeight()}px`;
  }

  /// 現在コマが見える位置までスクロールする（Qt の setCurrentCell と同じ）。
  function revealCurrent() {
    if (!view?.loaded || view.current_column == null) return;
    const cw = cellWidth();
    const left = view.current_column * cw;
    const width = viewportWidth() - LAYER_WIDTH;
    if (left < scroll.scrollLeft) scroll.scrollLeft = left;
    else if (left + cw > scroll.scrollLeft + width) scroll.scrollLeft = left + cw - width;
    const row = rowOfLayer(view.active_layer);
    if (row >= 0) {
      const top = tops[row];
      const height = viewportHeight() - HEADER_HEIGHT;
      if (top < scroll.scrollTop) scroll.scrollTop = top;
      else if (tops[row + 1] > scroll.scrollTop + height) scroll.scrollTop = tops[row + 1] - height;
    }
  }

  // ---- 描画 ----
  function requestDraw() {
    if (drawQueued) return;
    drawQueued = true;
    requestAnimationFrame(() => {
      drawQueued = false;
      draw();
    });
  }

  function draw() {
    const dpr = window.devicePixelRatio || 1;
    const w = viewportWidth();
    const h = viewportHeight();
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = C.uncreated;
    ctx.fillRect(0, 0, w, h);
    if (!view?.loaded) return;
    const cw = cellWidth();
    const c0 = Math.max(0, Math.floor(scroll.scrollLeft / cw));
    const c1 = Math.min(columnCount(), Math.ceil((scroll.scrollLeft + w - LAYER_WIDTH) / cw) + 1);

    ctx.save();
    ctx.beginPath();
    ctx.rect(LAYER_WIDTH, HEADER_HEIGHT, w - LAYER_WIDTH, h - HEADER_HEIGHT);
    ctx.clip();
    drawCells(c0, c1, w, h);
    ctx.restore();

    ctx.save();
    ctx.beginPath();
    ctx.rect(LAYER_WIDTH, 0, w - LAYER_WIDTH, HEADER_HEIGHT);
    ctx.clip();
    drawHeader(c0, c1, w);
    ctx.restore();

    ctx.save();
    ctx.beginPath();
    ctx.rect(0, HEADER_HEIGHT, LAYER_WIDTH, h - HEADER_HEIGHT);
    ctx.clip();
    drawLayers(w, h);
    ctx.restore();

    ctx.fillStyle = C.surfaceAlt;
    ctx.fillRect(0, 0, LAYER_WIDTH, HEADER_HEIGHT);
    ctx.fillStyle = C.border;
    ctx.fillRect(0, HEADER_HEIGHT - 1, LAYER_WIDTH, 1);
    ctx.fillRect(LAYER_WIDTH - 1, 0, 1, h);
  }

  function visibleRows(h) {
    const out = [];
    for (let row = 0; row < view.rows.length; row++) {
      const y = rowY(row);
      const height = tops[row + 1] - tops[row];
      if (y + height < HEADER_HEIGHT || y > h) continue;
      out.push([row, y, height]);
    }
    return out;
  }

  function drawCells(c0, c1, w, h) {
    const cw = cellWidth();
    const fpsValue = Math.max(1, view.fps);
    const rows = visibleRows(h);
    const gridBottom = Math.min(h, rowY(view.rows.length));
    // 格子。
    for (const [row, y, height] of rows) {
      ctx.fillStyle = C.border;
      ctx.fillRect(LAYER_WIDTH, y + height - 1, w - LAYER_WIDTH, 1);
    }
    for (let column = c0; column < c1; column++) {
      const x = colX(column);
      const second = (column + 1) % fpsValue === 0;
      ctx.fillStyle = second ? alpha(C.muted, 150) : C.border;
      ctx.fillRect(x + cw - 1, HEADER_HEIGHT, 1, gridBottom - HEADER_HEIGHT);
    }
    // 中身。
    for (const [row, y, height] of rows) drawRow(view.rows[row], row, y, height, c0, c1);
    if (sheet()) {
      // 6コマごとの帯を交互に薄く暗くする。
      ctx.fillStyle = "rgba(0,0,0,0.04)";
      for (let start = 6 + 12 * Math.max(0, Math.floor((c0 - 6) / 12)); start < c1; start += 12) {
        ctx.fillRect(colX(start), HEADER_HEIGHT, 6 * cw, h);
      }
      // 尺の外側は、内容を残したままグレーアウトする。
      const [start, end] = displayRange();
      ctx.fillStyle = "rgba(96,96,96,0.27)";
      const left = colX(start);
      if (left > LAYER_WIDTH) ctx.fillRect(LAYER_WIDTH, HEADER_HEIGHT, left - LAYER_WIDTH, h);
      const right = colX(end);
      if (right < w) ctx.fillRect(right, HEADER_HEIGHT, w - right, h);
    }
    drawSelection(rows);
    drawDragPreview();
    drawThumbnails(rows);
    drawPlayhead(h);
  }

  function spanColors(tween) {
    if (tween) return [C.tween, C.tween, C.tweenText];
    return sheet() ? [C.sheetHold, C.sheetKey, C.sheetKeyText] : [C.hold, C.key, C.keyText];
  }

  function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    ctx.roundRect(x, y, Math.max(0, w), Math.max(0, h), r);
  }

  function wave(x0, x1, y) {
    // 隣のセルとつながるよう、位相は表の中の x から決める。
    const offset = scroll.scrollLeft;
    ctx.beginPath();
    for (let x = x0; x <= x1; x += 1) {
      const yy = y + 2.2 * Math.sin((2 * Math.PI * (x + offset)) / 8);
      if (x === x0) ctx.moveTo(x, yy);
      else ctx.lineTo(x, yy);
    }
    ctx.stroke();
  }

  function cross(cx, cy, size, color) {
    const half = size / 2;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.6;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(cx - half, cy - half);
    ctx.lineTo(cx + half, cy + half);
    ctx.moveTo(cx - half, cy + half);
    ctx.lineTo(cx + half, cy - half);
    ctx.stroke();
    ctx.lineCap = "butt";
  }

  function arrowHead(tipX, y, direction, color) {
    const size = 5;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(tipX, y);
    ctx.lineTo(tipX - direction * size * 1.2, y - size * 0.8);
    ctx.lineTo(tipX - direction * size * 1.2, y + size * 0.8);
    ctx.closePath();
    ctx.fill();
  }

  function drawRow(row, index, y, height, c0, c1) {
    const cw = cellWidth();
    const band = Math.min(height, rowHeight());
    const top = y + BAR_INSET;
    const bottom = y + band - BAR_INSET;
    const barBottom = y + height - BAR_INSET;
    const midY = (top + bottom) / 2;
    // 空き（データ上は未使用）を、空セルと同じ ✗ と波線で描く。
    for (let i = firstVisible(row.gaps, c0); i < row.gaps.length && row.gaps[i].start < c1; i++) {
      const gap = row.gaps[i];
      const x0 = colX(gap.start);
      const x1 = colX(gap.start + gap.length);
      let lineLeft = x0;
      if (gap.cross) {
        cross(x0 + cw / 2, (top + bottom) / 2, Math.min(cw - 2, bottom - top) * 0.36, C.muted);
        lineLeft = x0 + cw;
      }
      if (x1 - lineLeft >= 3) {
        ctx.strokeStyle = C.muted;
        ctx.lineWidth = 1.4;
        wave(lineLeft, x1, midY);
      }
    }
    ctx.font = `bold ${Math.max(10, Math.round(12 * Math.min(zoom, 1.4)))}px ${FONT}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (let i = firstVisible(row.spans, c0); i < row.spans.length && row.spans[i].start < c1; i++) {
      const span = row.spans[i];
      const blank = span.kind !== "cell";
      const [fill, box, ink] = spanColors(span.tween);
      const x0 = colX(span.start);
      const x1 = colX(span.start + span.length);
      if (!blank) {
        roundRect(x0 + 1.5, top + 0.5, x1 - x0 - 2, barBottom - top - 1, 4);
        ctx.fillStyle = fill;
        ctx.fill();
        ctx.strokeStyle = alpha(ink, 70);
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      // 先頭の番号の箱。
      const boxLeft = x0 + 1;
      const boxWidth = cw - 2;
      if (span.kind === "blank") {
        cross(boxLeft + boxWidth / 2, (top + bottom) / 2, Math.min(boxWidth, bottom - top) * 0.36, C.muted);
      } else if (span.kind === "sequence_blank") {
        ctx.setLineDash([3, 2]);
        roundRect(boxLeft + 0.5, top + 0.5, boxWidth - 1, bottom - top - 1, 4);
        ctx.strokeStyle = C.muted;
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = C.muted;
        ctx.font = `${Math.max(10, Math.round(12 * Math.min(zoom, 1.4)))}px ${FONT}`;
        ctx.fillText(span.label, boxLeft + boxWidth / 2, (top + bottom) / 2);
        ctx.font = `bold ${Math.max(10, Math.round(12 * Math.min(zoom, 1.4)))}px ${FONT}`;
      } else {
        roundRect(boxLeft + 0.5, top + 0.5, boxWidth - 1, bottom - top - 1, 4);
        ctx.fillStyle = box;
        ctx.fill();
        ctx.strokeStyle = alpha(ink, 150);
        ctx.stroke();
        ctx.fillStyle = ink;
        ctx.fillText(elide(span.label, boxWidth - 4), boxLeft + boxWidth / 2, (top + bottom) / 2 + 0.5);
        if (span.genga) {
          // 原画：箱の右上の角を橙の三角で塗る。
          const size = Math.min(boxWidth, bottom - top) * 0.42;
          ctx.fillStyle = C.genga;
          ctx.beginPath();
          ctx.moveTo(boxLeft + boxWidth - size, top);
          ctx.lineTo(boxLeft + boxWidth, top);
          ctx.lineTo(boxLeft + boxWidth, top + size);
          ctx.closePath();
          ctx.fill();
        }
      }
      if (span.length > 1) {
        const lineLeft = boxLeft + boxWidth + 1;
        let lineRight = x1 - 1 - 2 - GRIP_WIDTH - 3;
        if (span.tween === "forward") lineRight -= 8;
        if (lineRight - lineLeft >= 3) {
          if (blank) {
            ctx.strokeStyle = C.muted;
            ctx.lineWidth = 1.4;
            wave(lineLeft, lineRight, midY);
          } else {
            const start = lineLeft + (span.tween === "reverse" ? 7 : 0);
            ctx.strokeStyle = span.tween ? ink : alpha(ink, 200);
            ctx.lineWidth = span.tween ? 1.6 : 2;
            ctx.beginPath();
            ctx.moveTo(start, midY);
            ctx.lineTo(lineRight, midY);
            ctx.stroke();
          }
        }
        if (span.tween === "forward") arrowHead(lineRight + 7, midY, 1, ink);
        if (span.tween === "reverse") arrowHead(x0 + cw + 1, midY, -1, ink);
        if (span.tween) {
          // 中割りのコマ。再生位置のコマは赤で塗る。
          for (let column = Math.max(span.start + 1, c0); column < Math.min(span.start + span.length - 1, c1); column++) {
            const current = column === view.current_column;
            ctx.beginPath();
            ctx.arc(colX(column) + cw / 2, midY, current ? 3.2 : 2.6, 0, Math.PI * 2);
            ctx.fillStyle = current ? C.error : C.surface;
            ctx.fill();
            ctx.strokeStyle = current ? C.error : ink;
            ctx.lineWidth = 1.3;
            ctx.stroke();
          }
        }
        // 確定済みのトゥイーンの帯は伸縮できないので、取っ手を描かない。
        if (!span.group) {
          const gripRight = x1 - 1 - 2;
          roundRect(gripRight - GRIP_WIDTH, midY - 6, GRIP_WIDTH, 12, 1.5);
          ctx.fillStyle = blank ? C.muted : alpha(ink, 210);
          ctx.fill();
        }
      }
    }
    if (row.draft) {
      // 下書きレイヤーの行は、セルにも斜線を重ねる。
      ctx.save();
      ctx.beginPath();
      ctx.rect(LAYER_WIDTH, y, viewportWidth() - LAYER_WIDTH, height);
      ctx.clip();
      ctx.strokeStyle = alpha(C.warning, 70);
      ctx.lineWidth = 1;
      ctx.beginPath();
      const offset = scroll.scrollLeft % 8;
      for (let x = LAYER_WIDTH - offset - height; x < viewportWidth(); x += 8) {
        ctx.moveTo(x, y + height);
        ctx.lineTo(x + height, y);
      }
      ctx.stroke();
      ctx.restore();
    }
    void index;
  }

  function elide(text, width) {
    if (ctx.measureText(text).width <= width) return text;
    let t = text;
    while (t.length > 1 && ctx.measureText(`${t}…`).width > width) t = t.slice(0, -1);
    return `${t}…`;
  }

  function displayRange() {
    if (drag?.kind === "range") return drag.preview;
    return view.range;
  }

  function drawSelection(rows) {
    const cw = cellWidth();
    const visible = new Map(rows.map(([row, y, height]) => [view.rows[row].layer, [y, height]]));
    ctx.strokeStyle = C.error;
    ctx.lineWidth = 2;
    for (const [layer, column] of view.selection) {
      const place = visible.get(layer);
      if (!place) continue;
      const x = colX(column);
      if (x + cw < LAYER_WIDTH || x > viewportWidth()) continue;
      roundRect(x + 1, place[0] + 1, cw - 2, place[1] - 2, 4);
      ctx.stroke();
    }
  }

  function cellRect(layer, column) {
    const row = rowOfLayer(layer);
    if (row < 0) return null;
    return [colX(column), rowY(row), cellWidth(), tops[row + 1] - tops[row]];
  }

  function drawDragPreview() {
    if (!drag) return;
    if (drag.kind === "resize" && drag.preview != null) {
      const fixedEnd = drag.span.start + drag.span.length - 1;
      const [from, to] = drag.left
        ? [Math.max(0, Math.min(drag.preview, fixedEnd)), fixedEnd]
        : [drag.span.start, Math.max(drag.span.start, drag.preview)];
      ctx.fillStyle = "rgba(255,215,0,0.43)";
      for (let column = from; column <= to; column++) {
        const r = cellRect(drag.layer, column);
        if (r) ctx.fillRect(r[0] + 1, r[1] + 1, r[2] - 2, r[3] - 2);
      }
    } else if (drag.kind === "group" && drag.dest) {
      const dl = drag.dest.layer - drag.anchor.layer;
      const dc = drag.dest.column - drag.anchor.column;
      ctx.strokeStyle = "rgb(229,57,53)";
      ctx.fillStyle = "rgba(255,215,0,0.25)";
      ctx.lineWidth = 2;
      for (const [layer, column] of view.selection) {
        const r = cellRect(layer + dl, column + dc);
        if (!r || column + dc < 0) continue;
        ctx.fillRect(r[0] + 1, r[1] + 1, r[2] - 2, r[3] - 2);
        ctx.strokeRect(r[0] + 1, r[1] + 1, r[2] - 2, r[3] - 2);
      }
    } else if (drag.kind === "move" && drag.dest && (drag.dest.layer !== drag.layer || drag.dest.column !== drag.column)) {
      const r = cellRect(drag.dest.layer, drag.dest.column);
      if (r) {
        ctx.strokeStyle = "rgb(229,57,53)";
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(r[0], r[1] + 1);
        ctx.lineTo(r[0], r[1] + r[3] - 1);
        ctx.stroke();
      }
    }
  }

  function drawPlayhead(h) {
    const column = view.current_column;
    if (column == null) return;
    const x = colX(column);
    const cw = cellWidth();
    if (x + cw < LAYER_WIDTH || x > viewportWidth()) return;
    // 現在のコマの列を薄く色付けし、左右の境目に細い線を引く。
    ctx.fillStyle = alpha(C.error, 26);
    ctx.fillRect(x, HEADER_HEIGHT, cw, h);
    ctx.fillStyle = alpha(C.error, 170);
    ctx.fillRect(x, HEADER_HEIGHT, 1, h);
    ctx.fillRect(x + cw - 1, HEADER_HEIGHT, 1, h);
  }

  function drawHeader(c0, c1, w) {
    const cw = cellWidth();
    const fpsValue = Math.max(1, view.fps);
    ctx.fillStyle = C.surfaceAlt;
    ctx.fillRect(LAYER_WIDTH, 0, w - LAYER_WIDTH, HEADER_HEIGHT);
    ctx.fillStyle = C.border;
    ctx.fillRect(LAYER_WIDTH, HEADER_HEIGHT - 1, w - LAYER_WIDTH, 1);
    const fontSize = 11;
    ctx.textBaseline = "middle";
    // 見出しの文字（6コマおき）は左寄せで次の列へはみ出してよいので、先に目盛りだけ描く。
    const labelStart = Math.max(0, c0 - 6);
    for (let column = labelStart; column < c1; column++) {
      const x = colX(column);
      const second = column % fpsValue === 0;
      const tick = HEADER_HEIGHT * (second ? 0.5 : 0.22);
      ctx.fillStyle = second ? C.muted : C.border;
      ctx.fillRect(x, HEADER_HEIGHT - tick, 1, tick);
      if (column % 6 === 0 && column !== view.current_column) {
        ctx.font = `${second ? "bold " : ""}${fontSize}px ${FONT}`;
        ctx.fillStyle = second ? C.text : C.muted;
        ctx.textAlign = "left";
        ctx.fillText(framePosition(column), x + 3, (HEADER_HEIGHT - tick * 0.6) / 2 + 1);
      }
    }
    const current = view.current_column;
    if (current != null) {
      const x = colX(current);
      // 現在コマの札。
      ctx.fillStyle = C.error;
      roundRect(x + 1, 3, cw - 2, HEADER_HEIGHT - 3, 4);
      ctx.fill();
      ctx.fillRect(x + 1, HEADER_HEIGHT - 4, cw - 2, 4);
      ctx.fillStyle = "#ffffff";
      ctx.font = `bold ${fontSize}px ${FONT}`;
      ctx.textAlign = "center";
      ctx.fillText(String(current + 1), x + cw / 2, HEADER_HEIGHT / 2 + 2);
    }
    if (sheet()) {
      const [start, end] = displayRange();
      const left = colX(start) + 2;
      const right = colX(end) - 2;
      ctx.fillStyle = "rgba(96,96,96,0.27)";
      if (left - 2 > LAYER_WIDTH) ctx.fillRect(LAYER_WIDTH, 0, left - 2 - LAYER_WIDTH, HEADER_HEIGHT);
      if (right + 2 < w) ctx.fillRect(right + 2, 0, w - right - 2, HEADER_HEIGHT);
      ctx.strokeStyle = C.muted;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(left, 1);
      ctx.lineTo(right, 1);
      ctx.moveTo(left, 0);
      ctx.lineTo(left, HEADER_HEIGHT - 1);
      ctx.moveTo(right, 0);
      ctx.lineTo(right, HEADER_HEIGHT - 1);
      ctx.stroke();
    }
  }

  function drawLayers(w, h) {
    for (const [row, y, height] of visibleRows(h)) {
      const layer = view.rows[row];
      const active = layer.layer === view.active_layer;
      const selected = active || layerSelection.has(layer.layer);
      ctx.fillStyle = selected ? C.selection : C.surfaceAlt;
      ctx.fillRect(0, y, LAYER_WIDTH, height);
      if (active) {
        ctx.fillStyle = C.accent;
        ctx.fillRect(0, y, 3, height);
      } else if (layer.draft) {
        ctx.strokeStyle = C.warning;
        ctx.lineWidth = 3;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.moveTo(1.5, y + 2);
        ctx.lineTo(1.5, y + height - 2);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      const band = Math.min(height, rowHeight());
      const mid = y + band / 2;
      const isExpanded = expanded.has(layer.layer);
      // ＞（サムネイルの開閉）
      ctx.fillStyle = isExpanded ? C.text : C.muted;
      ctx.font = `11px ${FONT}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(isExpanded ? "▾" : "▸", CHEVRON_WIDTH / 2 + 2, mid);
      // 目（表示切替）
      drawEye(CHEVRON_WIDTH + (MARKER_WIDTH - CHEVRON_WIDTH) / 2, mid, layer.visible);
      // 名前と下書きのバッジ。
      let right = LAYER_WIDTH - 6;
      if (layer.draft) {
        ctx.font = `600 10px ${FONT}`;
        const label = "✎ 下書き";
        const bw = ctx.measureText(label).width + 12;
        const bh = Math.min(band - 6, 16);
        roundRect(right - bw, mid - bh / 2, bw, bh, bh / 2);
        ctx.fillStyle = alpha(C.warning, 46);
        ctx.fill();
        ctx.strokeStyle = C.warning;
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.fillStyle = C.warning;
        ctx.textAlign = "center";
        ctx.fillText(label, right - bw / 2, mid + 0.5);
        right -= bw + 4;
      }
      ctx.font = `${layer.draft ? "italic " : ""}13px ${FONT}`;
      ctx.fillStyle = layer.visible ? C.text : C.muted;
      ctx.textAlign = "left";
      ctx.fillText(elide(layer.name, right - MARKER_WIDTH), MARKER_WIDTH, mid + 0.5);
      ctx.fillStyle = C.border;
      ctx.fillRect(0, y + height - 1, LAYER_WIDTH, 1);
    }
    if (drag?.kind === "layers" && drag.target != null) {
      const y = HEADER_HEIGHT + tops[drag.target] - scroll.scrollTop;
      ctx.fillStyle = C.accent;
      ctx.fillRect(0, y - 1, LAYER_WIDTH, 2);
    }
  }

  function drawEye(cx, cy, open) {
    ctx.strokeStyle = open ? C.text : C.muted;
    ctx.lineWidth = 1.3;
    ctx.beginPath();
    ctx.moveTo(cx - 7, cy);
    ctx.quadraticCurveTo(cx, cy - 7, cx + 7, cy);
    ctx.quadraticCurveTo(cx, cy + 7, cx - 7, cy);
    ctx.stroke();
    if (open) {
      ctx.beginPath();
      ctx.arc(cx, cy, 2.2, 0, Math.PI * 2);
      ctx.fillStyle = C.text;
      ctx.fill();
    } else {
      ctx.beginPath();
      ctx.moveTo(cx - 6, cy + 5);
      ctx.lineTo(cx + 6, cy - 5);
      ctx.stroke();
    }
  }

  // ---- サムネイル（開いた行のキーのセル） ----
  function drawThumbnails(rows) {
    const cw = cellWidth();
    const collapsed = rowHeight();
    for (const [row, y, height] of rows) {
      const layer = view.rows[row];
      if (!expanded.has(layer.layer)) continue;
      const areaTop = y + collapsed - 2;
      const areaHeight = height - collapsed - 3;
      const c0 = Math.floor(scroll.scrollLeft / cw);
      for (let i = firstVisible(layer.spans, c0); i < layer.spans.length; i++) {
        const span = layer.spans[i];
        const left = colX(span.start);
        if (left > viewportWidth()) break;
        if (span.kind !== "cell") continue;
        const areaWidth = cw * span.length - (span.length > 1 ? 14 : 7);
        if (areaHeight < 10 || areaWidth < 10) continue;
        const image = thumbnail(layer.layer, span, Math.round(areaWidth), Math.round(areaHeight));
        if (!image) continue;
        ctx.fillStyle = "#ffffff";
        ctx.strokeStyle = C.border;
        ctx.lineWidth = 1;
        roundRect(left + 3.5, areaTop - 0.5, image.width / (window.devicePixelRatio || 1) + 1, image.height / (window.devicePixelRatio || 1) + 1, 3);
        ctx.fill();
        ctx.stroke();
        ctx.drawImage(image, left + 4, areaTop, image.width / (window.devicePixelRatio || 1), image.height / (window.devicePixelRatio || 1));
      }
    }
  }

  function thumbnail(layer, span, width, height) {
    const dpr = window.devicePixelRatio || 1;
    const key = `${docVersion}:${view.mode}:${layer}:${span.column}:${span.number}:${width}x${height}`;
    const cached = thumbs.get(key);
    if (cached !== undefined) return cached;
    thumbs.set(key, null);
    invoke("timeline_thumbnail", {
      layer, column: span.column, number: span.number,
      width: Math.round(width * dpr), height: Math.round(height * dpr),
    }).then((buffer) => {
      const head = new DataView(buffer, 0, 8);
      const w = head.getUint32(0, true);
      const h = head.getUint32(4, true);
      if (!w || !h) return;
      const off = document.createElement("canvas");
      off.width = w;
      off.height = h;
      off.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(buffer, 8, w * h * 4), w, h), 0, 0);
      thumbs.set(key, off);
      requestDraw();
    }).catch(() => {});
    return null;
  }

  const allExpanded = () => !!view?.rows.length && view.rows.every((r) => expanded.has(r.layer));
  function toggleAllExpanded() {
    if (allExpanded()) expanded.clear();
    else for (const row of view?.rows ?? []) expanded.add(row.layer);
    relayout();
  }
  function toggleExpanded(layer) {
    if (expanded.has(layer)) expanded.delete(layer);
    else expanded.add(layer);
    relayout();
  }
  function relayout() {
    layoutRows();
    sizeSpacer();
    syncToolbar();
    requestDraw();
  }

  // ---- マウス ----
  function cellTarget(h) {
    const row = view.rows[h.row];
    return row ? { layer: row.layer, column: h.column } : null;
  }

  /// 帯の左端・右端の取っ手に当たっているか（Qt の _edge_source_at）。
  function edgeAt(h) {
    if (!sheet() || h.row < 0) return null;
    const span = spanAt(h.row, h.column);
    if (!span || span.group) return null;
    const end = span.start + span.length - 1;
    if (h.column === span.start && h.inCell <= HANDLE_HIT) return { span, left: true };
    if (h.column === end && h.inCell >= cellWidth() - 1 - HANDLE_HIT) return { span, left: false };
    return null;
  }

  /// 動かせる箱（キーの番号・空キーの ✗）か。
  function movable(h) {
    const span = spanAt(h.row, h.column);
    return span && !span.group && h.column === span.start ? span : null;
  }

  function rangeEdgeAt(h) {
    if (!sheet()) return null;
    const [start, end] = view.range;
    if (Math.abs(h.x - colX(end)) <= 6) return "end";
    if (Math.abs(h.x - colX(start)) <= 6) return "start";
    return null;
  }

  scroll.addEventListener("pointerdown", (e) => {
    if (!view?.loaded) return;
    panel.focus({ preventScroll: true });
    const h = hit(e.clientX, e.clientY);
    if (e.button === 1 || (e.button === 0 && spaceDown)) {
      drag = { kind: "pan", x: e.clientX, y: e.clientY, left: scroll.scrollLeft, top: scroll.scrollTop };
      scroll.setPointerCapture(e.pointerId);
      scroll.style.cursor = "grabbing";
      e.preventDefault();
      return;
    }
    if (e.button !== 0) return;
    scroll.setPointerCapture(e.pointerId);
    if (h.area === "header") {
      const edge = rangeEdgeAt(h);
      if (edge) {
        drag = { kind: "range", edge, preview: [...view.range] };
      } else {
        drag = { kind: "scrub", last: h.column };
        scrub(h.column);
      }
      e.preventDefault();
      return;
    }
    if (h.area === "layers") {
      layerPress(e, h);
      return;
    }
    if (h.area !== "cells" || h.row < 0) return;
    const target = cellTarget(h);
    const multi = e.shiftKey || e.ctrlKey || e.metaKey;
    const key = `${target.layer},${target.column}`;
    const selected = new Set(view.selection.map(([l, c]) => `${l},${c}`));
    if (!multi && selected.has(key) && selected.size > 1) {
      // 複数選択済みの範囲をそのままドラッグする。
      drag = { kind: "group", anchor: target, dest: target };
      scroll.style.cursor = "grabbing";
      return;
    }
    const edge = multi ? null : edgeAt(h);
    if (edge) {
      drag = { kind: "resize", layer: target.layer, span: edge.span, left: edge.left, preview: edge.span.start };
      select(target.layer, edge.span.start, "replace");
      scroll.style.cursor = "ew-resize";
      return;
    }
    const box = multi ? null : movable(h);
    if (box) {
      drag = { kind: "move", layer: target.layer, column: target.column, dest: target, copy: e.altKey };
      select(target.layer, target.column, "replace");
      scroll.style.cursor = e.altKey ? "copy" : "grabbing";
      return;
    }
    const how = e.shiftKey ? "range" : e.ctrlKey || e.metaKey ? "toggle" : "replace";
    drag = { kind: "select", from: target, to: target };
    select(target.layer, target.column, how);
  });

  scroll.addEventListener("pointermove", (e) => {
    if (!view?.loaded) return;
    const h = hit(e.clientX, e.clientY);
    if (!drag) {
      updateCursor(h, e);
      return;
    }
    if (drag.kind === "pan") {
      scroll.scrollLeft = drag.left - (e.clientX - drag.x);
      scroll.scrollTop = drag.top - (e.clientY - drag.y);
      return;
    }
    if (drag.kind === "scrub") {
      if (h.column !== drag.last) {
        drag.last = h.column;
        scrub(h.column);
      }
      return;
    }
    if (drag.kind === "range") {
      const [start, end] = view.range;
      drag.preview = drag.edge === "start" ? [Math.min(h.boundary, end - 1), end] : [start, Math.max(start + 1, h.boundary)];
      requestDraw();
      return;
    }
    if (drag.kind === "layers") {
      layerDragMove(h);
      return;
    }
    const target = h.row >= 0 ? cellTarget(h) : null;
    if (drag.kind === "resize") {
      drag.preview = Math.max(0, h.rawColumn);
    } else if (drag.kind === "group" || drag.kind === "move") {
      if (target) drag.dest = target;
      if (drag.kind === "move") {
        drag.copy = e.altKey;
        scroll.style.cursor = e.altKey ? "copy" : "grabbing";
      }
    } else if (drag.kind === "select" && target) {
      if (target.layer !== drag.to.layer || target.column !== drag.to.column) {
        drag.to = target;
        invoke("timeline_select_rect", { from: [drag.from.layer, drag.from.column], to: [target.layer, target.column] })
          .then((v) => setView(v))
          .catch(() => {});
      }
    }
    autoScroll(h);
    requestDraw();
  });

  function autoScroll(h) {
    const margin = 24;
    if (h.x > viewportWidth() - margin) scroll.scrollLeft += 12;
    else if (h.x < LAYER_WIDTH + margin && h.x > LAYER_WIDTH) scroll.scrollLeft -= 12;
  }

  scroll.addEventListener("pointerup", (e) => {
    const d = drag;
    drag = null;
    scroll.style.cursor = "";
    if (!d || !view?.loaded) return;
    const h = hit(e.clientX, e.clientY);
    switch (d.kind) {
      case "range":
        call("timeline_set_range", { edge: d.edge, column: d.edge === "start" ? d.preview[0] : d.preview[1] });
        break;
      case "group":
        if (d.dest.layer !== d.anchor.layer || d.dest.column !== d.anchor.column) {
          edit({ op: "move_selection", anchor_layer: d.anchor.layer, anchor_column: d.anchor.column, to_layer: d.dest.layer, to_column: d.dest.column });
        }
        break;
      case "resize": {
        const row = h.row >= 0 ? view.rows[h.row]?.layer : d.layer;
        if (row === d.layer || h.row < 0) {
          edit({ op: "resize_exposure", layer: d.layer, key_column: d.span.start, boundary: d.preview, left: d.left });
        }
        break;
      }
      case "move":
        if (d.dest.layer !== d.layer || d.dest.column !== d.column) {
          edit({ op: d.copy ? "copy_cell" : "move_cell", layer: d.layer, column: d.column, to_layer: d.dest.layer, to_column: d.dest.column });
        }
        break;
      case "layers":
        layerDragEnd(d);
        break;
      default:
        break;
    }
    requestDraw();
  });

  scroll.addEventListener("dblclick", (e) => {
    const h = hit(e.clientX, e.clientY);
    if (h.area === "layers" && h.x > MARKER_WIDTH && h.row >= 0) {
      startRename(h.row);
    } else if (h.area === "cells" && h.row >= 0) {
      const span = spanAt(h.row, h.column);
      if (span?.group) showStatus("トゥイーンの形を直すのは、キャンバスが文書を描くようになってから移します。", true);
    }
  });

  function updateCursor(h, e) {
    let cursor = "";
    if (spaceDown) cursor = "grab";
    else if (h.area === "header" && rangeEdgeAt(h)) cursor = "ew-resize";
    else if (h.area === "cells" && h.row >= 0) {
      if (edgeAt(h)) cursor = "ew-resize";
      else if (movable(h)) cursor = e.altKey ? "copy" : "grab";
    }
    scroll.style.cursor = cursor;
    const tip = h.area === "cells" ? tooltip(h) : h.area === "layers" ? "左端の＞でサムネイルを開閉、目のアイコンで表示／非表示を切替、ダブルクリックでレイヤー名を変更。" : "";
    if (scroll.title !== tip) scroll.title = tip;
    hover = h;
  }

  function tooltip(h) {
    if (h.row < 0) return "";
    const span = spanAt(h.row, h.column);
    if (!span) return "";
    if (span.group) return `確定したトゥイーン（${span.length}コマ）。右クリックで解除`;
    const end = span.start + span.length - 1;
    let text = "";
    if (span.kind === "blank" && h.column === span.start) text = "空フレームの先頭です。描画すると自動的にキーフレーム化します。";
    else if (h.column === end) text = span.kind === "cell" ? "右端をドラッグして後方向の表示コマ数を変更" : "空フレームの右端をドラッグして表示コマ数を変更";
    else if (span.kind === "cell" && h.column === span.start) {
      text = `${span.cell_name ? `CLIP STUDIOセル名：${span.cell_name}\n` : ""}中央をドラッグして移動／左端をドラッグして前方向へ伸縮\nレイヤーセル ${h.column + 1} / ${span.length}コマ`;
    }
    if (span.genga && text) text = `原画\n${text}`;
    return text;
  }

  function select(layer, column, how) {
    invoke("timeline_select", { layer, column, how })
      .then((v) => setView(v))
      .catch((err) => showStatus(String(err), true));
  }
  function scrub(column) {
    // シートでは範囲外のコマへも移動できる（連番は番号の範囲内だけ）。
    const limit = sheet() ? columnCount() : view.frame_count;
    if (column < 0 || column >= limit) return;
    if (sheet() && column === view.current_column) return;
    call("timeline_set_frame", { column }, { changed: sheet() && column >= view.sheet_frames });
  }

  scroll.addEventListener("scroll", () => requestDraw());
  scroll.addEventListener("wheel", (e) => {
    if (!(e.ctrlKey || e.metaKey) || !view?.loaded) return;
    e.preventDefault();
    const rect = scroll.getBoundingClientRect();
    const ax = Math.max(0, e.clientX - rect.left - LAYER_WIDTH);
    const ay = Math.max(0, e.clientY - rect.top - HEADER_HEIGHT);
    setZoom(zoom * 1.15 ** (-e.deltaY / 120), ax, ay);
  }, { passive: false });

  function setZoom(next, ax, ay) {
    const old = [cellWidth(), totalHeight()];
    zoom = Math.max(0.5, Math.min(3, next));
    layoutRows();
    sizeSpacer();
    const [cw, th] = [cellWidth(), totalHeight()];
    scroll.scrollLeft = ((scroll.scrollLeft + ax) * cw) / old[0] - ax;
    if (old[1]) scroll.scrollTop = ((scroll.scrollTop + ay) * th) / old[1] - ay;
    requestDraw();
  }

  // ---- レイヤー名の列 ----
  function layerPress(e, h) {
    if (h.row < 0) return;
    const layer = view.rows[h.row].layer;
    if (h.x <= CHEVRON_WIDTH) {
      toggleExpanded(layer);
      return;
    }
    if (h.x <= MARKER_WIDTH) {
      edit({ op: "set_visible", layer, visible: !view.rows[h.row].visible });
      return;
    }
    if (e.shiftKey) {
      // 連続した範囲だけを選ぶ（Qt の ContiguousSelection）。
      const a = rowOfLayer(view.active_layer);
      const [lo, hi] = [Math.min(a, h.row), Math.max(a, h.row)];
      layerSelection = new Set(view.rows.slice(lo, hi + 1).map((r) => r.layer));
      requestDraw();
      return;
    }
    if (!layerSelection.has(layer) || layerSelection.size === 1) layerSelection = new Set([layer]);
    drag = { kind: "layers", from: h.row, target: null, moved: false };
    if (layer !== view.active_layer) edit({ op: "select_layer", layer });
  }
  function selectedLayerRows() {
    return view.rows.map((r, i) => (layerSelection.has(r.layer) ? i : -1)).filter((i) => i >= 0);
  }
  function layerDragMove(h) {
    const contentY = h.y - HEADER_HEIGHT + scroll.scrollTop;
    let target = 0;
    for (let i = 0; i < view.rows.length; i++) if (contentY > (tops[i] + tops[i + 1]) / 2) target = i + 1;
    if (target !== drag.from && target !== drag.from + 1) drag.moved = true;
    drag.target = drag.moved ? target : null;
    requestDraw();
  }
  function layerDragEnd(d) {
    // 行の境目（挿入位置）を、Qt の _layer_rows_moved と同じく動かしたあとの先頭の行に直す。
    if (d.target == null) return;
    const selected = selectedLayerRows();
    const rows = selected.length > 1 ? selected : [d.from];
    const start = rows[0];
    const block = rows.length;
    const finalRow = Math.max(0, Math.min(d.target > start ? d.target - block : d.target, view.rows.length - block));
    if (finalRow !== start) edit({ op: "move_layer_rows", rows, to_row: finalRow });
  }

  function startRename(row) {
    const layer = view.rows[row];
    rename.style.display = "block";
    rename.style.left = `${MARKER_WIDTH}px`;
    rename.style.top = `${rowY(row) + 2}px`;
    rename.style.width = `${LAYER_WIDTH - MARKER_WIDTH - 6}px`;
    rename.style.height = `${rowHeight() - 4}px`;
    rename.value = layer.name;
    rename.dataset.layer = String(layer.layer);
    rename.focus();
    rename.select();
  }
  function finishRename(commit) {
    if (rename.style.display !== "block") return;
    rename.style.display = "none";
    const layer = Number(rename.dataset.layer);
    const row = view.rows.find((r) => r.layer === layer);
    if (commit && row && rename.value.trim() !== row.name) edit({ op: "rename_layer", layer, name: rename.value });
    panel.focus({ preventScroll: true });
  }
  rename.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Enter") finishRename(true);
    else if (e.key === "Escape") finishRename(false);
  });
  rename.addEventListener("blur", () => finishRename(true));

  function deleteActiveLayer() {
    if (!view?.loaded) return;
    if (view.rows.length <= 1) return;
    edit({ op: "delete_layers", layers: [view.active_layer] });
  }

  // ---- 右クリックのメニュー ----
  scroll.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (!view?.loaded) return;
    const h = hit(e.clientX, e.clientY);
    if (h.area === "layers") {
      if (h.row < 0) return;
      const layer = view.rows[h.row].layer;
      if (!layerSelection.has(layer)) {
        layerSelection = new Set([layer]);
        if (layer !== view.active_layer) edit({ op: "select_layer", layer });
      }
      layerMenu(e.clientX, e.clientY);
    } else if (h.area === "cells") {
      cellMenu(e.clientX, e.clientY, h);
    }
  });

  function layerMenu(x, y) {
    const layers = [...layerSelection];
    const allDraft = view.rows.filter((r) => layerSelection.has(r.layer)).every((r) => r.draft);
    const items = [
      { label: "複製", action: () => edit({ op: "duplicate_layers", layers }) },
      { label: "結合（まだ移していません）", enabled: false },
      { label: "削除", action: () => edit({ op: "delete_layers", layers }) },
      "-",
      { label: `${allDraft ? "✓ " : ""}下書きレイヤー`, action: () => edit({ op: "toggle_draft", layers }) },
    ];
    if (sheet()) items.push("-", { label: "番号の正規化", action: () => normalizeDialog(layers) });
    showMenu(x, y, items);
  }

  function cellMenu(x, y, h) {
    const onCell = h.row >= 0;
    const target = onCell ? cellTarget(h) : null;
    const span = onCell ? spanAt(h.row, h.column) : null;
    const has = !!span;
    const args = target ? { layer: target.layer, column: target.column } : {};
    const run = (op, extra = {}) => () => edit({ op, ...args, ...extra });
    const numbers = onCell ? view.rows[h.row].numbers : [];
    const items = [
      { label: "中割りトラックラベル○を作成", enabled: onCell, action: run("create_blank") },
      { label: "トラックラベルを削除", enabled: has, action: run("clear_cell") },
      "-",
      { label: "削除", enabled: has, action: run("clear_cell") },
      { label: "切り取り", enabled: has, action: run("cut_clip") },
      { label: "コピー", enabled: has, action: run("copy_clip") },
      { label: "貼り付け", enabled: onCell && view.clipboard && sheet(), action: run("paste_clip") },
      "-",
      { label: "新規アニメーションセル", enabled: onCell && sheet(), action: run("new_cell") },
      "-",
      { label: "クリップを結合", enabled: has && sheet(), action: run("merge_clip") },
      { label: "クリップを分割", enabled: has && sheet(), action: run("split_clip") },
      { label: "この位置を新しい番号にする", enabled: has && sheet(), action: run("new_number_here") },
      { label: "原画の印を付ける／外す", enabled: has, action: run("toggle_genga") },
      { label: "次の原画までに中割りを入れる…", enabled: has && span.genga && sheet(), action: () => inbetweenDialog(args) },
      { label: "表示開始フレームにする", enabled: onCell && sheet(), action: () => call("timeline_set_range", { edge: "start", column: h.column }) },
      // 終端は「このコマまで」。範囲は開始以上・終了未満で持つ。
      { label: "表示終了フレームにする", enabled: onCell && sheet(), action: () => call("timeline_set_range", { edge: "end", column: h.column + 1 }) },
      "-",
      { label: "タイムリマップをコピー（まだ移していません）", enabled: false },
      "-",
      { label: "フレームを挿入", enabled: onCell && sheet(), action: run("insert_frame_at") },
      { label: "フレームを削除", enabled: onCell && sheet(), action: run("delete_frame_at") },
      "-",
      {
        label: "コマを増やす",
        submenu: [
          { label: "空フレームを追加", enabled: onCell, action: run("create_blank") },
          { label: "表示コマを1コマ伸ばす", enabled: has && sheet(), action: run("extend_exposure") },
        ],
      },
    ];
    if (numbers.length && sheet()) {
      items.push({
        label: "連番の番号を呼び出す",
        submenu: numbers.map((n) => ({ label: String(n), action: run("recall_number", { number: n }) })),
      });
    }
    if (span?.group) {
      items.push("-",
        { label: "トゥイーンの形を直す（まだ移していません）", enabled: false },
        { label: "トゥイーンを解除（中割りを通常のセルにする）", action: run("release_tween") });
    } else if (span && span.kind === "cell" && h.column === span.start + span.length - 1 && span.length >= 2 && sheet()) {
      items.push("-", {
        label: "トゥイーンを有効にする",
        submenu: [
          { label: "自由変形（まだ移していません）", enabled: false },
          { label: "メッシュ変形（まだ移していません）", enabled: false },
        ],
      });
    }
    showMenu(x, y, items);
  }

  function showMenu(x, y, items) {
    const doc = panel.ownerDocument;
    doc.querySelectorAll(".tl-menu").forEach((m) => m.remove());
    const build = (list, left, top, level) => {
      const menu = el("div", { class: "tl-menu" });
      menu.dataset.level = String(level);
      for (const item of list) {
        if (item === "-") {
          menu.append(el("div", { class: "tl-menu-sep" }));
          continue;
        }
        const entry = el("div", { class: `tl-menu-item${item.enabled === false ? " disabled" : ""}${item.submenu ? " sub" : ""}` }, item.label);
        if (item.submenu) {
          entry.addEventListener("pointerenter", () => {
            doc.querySelectorAll(".tl-menu").forEach((m) => Number(m.dataset.level) > level && m.remove());
            const r = entry.getBoundingClientRect();
            build(item.submenu, r.right - 2, r.top - 4, level + 1);
          });
        } else {
          entry.addEventListener("pointerenter", () => doc.querySelectorAll(".tl-menu").forEach((m) => Number(m.dataset.level) > level && m.remove()));
          if (item.enabled !== false) {
            entry.addEventListener("click", () => {
              close();
              item.action?.();
            });
          }
        }
        menu.append(entry);
      }
      doc.body.append(menu);
      const r = menu.getBoundingClientRect();
      const vw = doc.defaultView.innerWidth;
      const vh = doc.defaultView.innerHeight;
      menu.style.left = `${Math.max(0, Math.min(left, vw - r.width - 2))}px`;
      menu.style.top = `${Math.max(0, Math.min(top, vh - r.height - 2))}px`;
      return menu;
    };
    const close = () => {
      doc.querySelectorAll(".tl-menu").forEach((m) => m.remove());
      doc.removeEventListener("pointerdown", outside, true);
      doc.removeEventListener("keydown", escape, true);
    };
    const outside = (e) => {
      if (!e.target.closest?.(".tl-menu")) close();
    };
    const escape = (e) => {
      if (e.key === "Escape") close();
    };
    build(items, x, y, 0);
    setTimeout(() => {
      doc.addEventListener("pointerdown", outside, true);
      doc.addEventListener("keydown", escape, true);
    });
  }

  // ---- 小さな確認画面 ----
  function dialog(title, content, buttons) {
    const doc = panel.ownerDocument;
    const overlay = el("div", { class: "tl-dialog-overlay" });
    const box = el("div", { class: "tl-dialog" }, el("div", { class: "tl-dialog-title" }, title), content);
    const row = el("div", { class: "tl-dialog-buttons" });
    const close = () => overlay.remove();
    for (const [label, action, primary] of buttons) {
      row.append(el("button", { type: "button", class: primary ? "primary" : "", onclick: () => {
        if (action?.() !== false) close();
      } }, label));
    }
    box.append(row);
    overlay.append(box);
    overlay.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Escape") close();
      if (e.key === "Enter") row.querySelector(".primary")?.click();
    });
    doc.body.append(overlay);
    (box.querySelector("input") ?? row.querySelector(".primary"))?.focus();
    return close;
  }

  function inbetweenDialog(args) {
    const input = el("input", { type: "number", min: "1", max: "99", value: "1" });
    dialog("中割りを入れる", el("label", { class: "tl-dialog-field" }, "中割りの枚数：", input), [
      ["キャンセル"],
      ["OK", () => edit({ op: "inbetween", ...args, count: Math.max(1, Math.min(99, Number(input.value) || 1)) }), true],
    ]);
  }

  async function normalizeDialog(layers = null) {
    if (!sheet()) return;
    const preview = await invoke("timeline_normalize_preview");
    if (!preview.some((p) => p.changes.length)) {
      showStatus("番号はすでにタイムラインの順番どおりです。");
      return;
    }
    const chosen = new Set(layers ?? [view.active_layer]);
    const list = el("div", { class: "tl-normalize-list" });
    const boxes = [];
    for (const p of preview) {
      const check = el("input", { type: "checkbox" });
      check.checked = chosen.has(p.layer) && p.changes.length > 0;
      check.disabled = !p.changes.length;
      boxes.push([p.layer, check]);
      const detail = p.changes.length ? p.changes.map(([a, b]) => `${a}→${b}`).join("　") : "変更なし";
      list.append(el("label", { class: "tl-normalize-row" }, check, el("b", {}, p.name), el("span", {}, detail)));
    }
    dialog("タイムラインの順番で正規化", el("div", {},
      el("div", { class: "tl-dialog-note" }, "左から最初に出てくる順に番号を振り直します。"),
      list), [
      ["キャンセル"],
      ["正規化", () => {
        const target = boxes.filter(([, c]) => c.checked).map(([l]) => l);
        if (target.length) edit({ op: "normalize", layers: target });
      }, true],
    ]);
  }

  // ---- ツールバーの操作 ----
  function currentTarget() {
    if (!view?.loaded) return null;
    return { layer: view.active_layer, column: view.current_column ?? 0 };
  }
  function createBlankAtCurrent() {
    const t = currentTarget();
    if (t) edit({ op: "create_blank", ...t });
  }
  function extendCurrent() {
    const t = currentTarget();
    if (t && sheet()) edit({ op: "extend_exposure", ...t });
  }
  opacity.addEventListener("input", () => {
    opacityValue.textContent = `${opacity.value}%`;
    if (view?.loaded) edit({ op: "set_opacity", layer: view.active_layer, opacity: Number(opacity.value) / 100 });
  });
  fps.addEventListener("change", () => {
    const value = Math.max(1, Math.min(60, Number(fps.value) || 24));
    call("timeline_set_fps", { fps: value });
  });

  // ---- キー（パネルにフォーカスがあるとき） ----
  panel.addEventListener("keydown", (e) => {
    if (!view?.loaded || e.target instanceof HTMLInputElement) return;
    const mod = e.metaKey || e.ctrlKey;
    const t = currentTarget();
    const keyMap = {
      Home: () => navigate("first"),
      End: () => navigate("last"),
      ArrowLeft: () => navigate("previous"),
      ArrowRight: () => navigate("next"),
      Enter: () => togglePlay(),
      F2: () => {
        const row = rowOfLayer(view.active_layer);
        if (row >= 0) startRename(row);
      },
      Delete: () => t && edit({ op: "clear_cell", ...t }),
      Backspace: () => t && edit({ op: "clear_cell", ...t }),
    };
    let action = null;
    if (e.code === "Space") {
      spaceDown = true;
      scroll.style.cursor = "grab";
      e.preventDefault();
      return;
    }
    // 取り消し・やり直し（Cmd/Ctrl+Z・Shift+Z・Y）は app.js の一つだけ（アプリ全体の履歴）。
    if (mod && e.key.toLowerCase() === "c") action = () => t && edit({ op: "copy_clip", ...t });
    else if (mod && e.key.toLowerCase() === "x") action = () => t && edit({ op: "cut_clip", ...t });
    else if (mod && e.key.toLowerCase() === "v") action = () => t && edit({ op: "paste_clip", ...t });
    else if (mod && e.shiftKey && e.key.toLowerCase() === "n") action = () => edit({ op: "add_layer" });
    else if (mod && (e.key === "=" || e.key === "+")) action = () => setZoom(zoom * 1.15, 0, 0);
    else if (mod && e.key === "-") action = () => setZoom(zoom / 1.15, 0, 0);
    else if (mod) return;
    else if (e.shiftKey && e.key.toLowerCase() === "e") action = () => edit({ op: "delete_frame" });
    else if (e.key === "1") action = () => navigate("previous");
    else if (e.key === "2") action = () => navigate("next");
    else if (e.key.toLowerCase() === "a" && !e.shiftKey) action = () => navigate("previous_key");
    else if (e.key.toLowerCase() === "s" && !e.shiftKey) action = () => navigate("next_key");
    else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      const row = rowOfLayer(view.active_layer) + (e.key === "ArrowUp" ? -1 : 1);
      if (row >= 0 && row < view.rows.length) action = () => edit({ op: "select_layer", layer: view.rows[row].layer });
    } else action = keyMap[e.key];
    if (action) {
      e.preventDefault();
      action();
    }
  });
  panel.addEventListener("keyup", (e) => {
    if (e.code === "Space") {
      spaceDown = false;
      scroll.style.cursor = "";
    }
  });

  // ---- 文書の変化（Rust の document-changed） ----
  // 自分の操作（source が timeline）は命令の返事で表示を合わせ済み。再生の刻みは
  // 再生ヘッドだけを動かす。ほかは表示を読み直す（重なったら最後に一度だけ）。
  let refreshing = false;
  let refreshAgain = false;
  let refreshChanged = false;
  async function refresh(changed) {
    refreshChanged ||= changed;
    if (refreshing) {
      refreshAgain = true;
      return;
    }
    refreshing = true;
    try {
      do {
        refreshAgain = false;
        const options = { changed: refreshChanged, reveal: true };
        refreshChanged = false;
        setView(await invoke("timeline_view"), options);
      } while (refreshAgain);
    } catch (e) {
      showStatus(`タイムラインを読めなかった：${e}`, true);
    } finally {
      refreshing = false;
    }
  }
  function followPlayhead(playhead) {
    if (!view?.loaded) return;
    if (!playhead.playing) {
      // 止まったら、省いていた選択などを合わせ直す。
      refresh(false);
      return;
    }
    if (!view.follow_playback) return;
    if (view.current === playhead.current && view.current_column === playhead.column) return;
    view = { ...view, current: playhead.current, current_column: playhead.column };
    updateCounter();
    revealCurrent();
    requestDraw();
  }
  listen?.("document-changed", ({ payload }) => {
    if (payload.source === "timeline") return;
    if (payload.source === "playback" && payload.playhead) {
      followPlayhead(payload.playhead);
      return;
    }
    refresh(payload.structure || payload.pixels);
  });

  new ResizeObserver(() => {
    if (!view) return;
    sizeSpacer();
    requestDraw();
  }).observe(scroll);

  // 最初の表示と、確かめ用の起動の設定（環境変数）。
  (async () => {
    try {
      setView(await invoke("timeline_view"), { changed: true });
      const debug = await invoke("timeline_debug_options").catch(() => ({}));
      if (view.loaded) {
        if (debug.sequence) await setMode("sequence");
        if (debug.expand) toggleAllExpanded();
        if (debug.scroll) {
          sizeSpacer();
          scroll.scrollLeft = debug.scroll[0];
          scroll.scrollTop = debug.scroll[1];
        }
        if (debug.autoplay) await call("timeline_play", { on: true });
      }
    } catch (e) {
      showStatus(`タイムラインを読めなかった：${e}`, true);
    }
  })();

  void hover;
  return panel;
}
