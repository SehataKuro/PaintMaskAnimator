// 塗りの操作の画面側（計算は app/src/paint_ops.rs → pma-core）。
//
// - バケツのなぞり塗り（押して・動かして・離す。離すまでで取り消し1回）
// - 串刺し塗り：クリック、指定待ちの案内（帯と色の重ね）、Enter（推測を採用）・
//   Esc（このコマを飛ばす）・Shift＋Esc（この向きを飛ばす）
// - バケツの色置換：線の色なら尋ねる（Qt の replace_color_at）
// - 自動で塗る（見本を選ぶ画面・進み具合と中止・確かめたい所の一覧）
// - 見本との違いチェック（進み具合と中止・候補の一覧）
// - メニューの「ゴミ取り／塗り抜け…」（Qt と同じく画面を出さずにツールの設定で当てる）
//
// canvas_tools.js がキャンバスの点を渡し、app.js が installPaintOps で組み込む。
import { toolStore } from "./tools.js";
import { notify } from "./tool_properties.js";
import { el, openDialog, ask, dialogOpen } from "./dialogs.js";
import { tr } from "./i18n.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

let ctx = null;

async function call(cmd, args) {
  try {
    const out = await invoke(cmd, args);
    if (out instanceof ArrayBuffer && out.byteLength >= 16) ctx?.applyRect(out);
    return out;
  } catch (e) {
    notify(String(e));
    return null;
  }
}

// ---------------------------------------------------------------------------
// バケツのなぞり塗り
// ---------------------------------------------------------------------------

let drag = null;

/// 押した。
export function bucketDown(p, shift) {
  const state = { chain: null, pending: null, shift };
  drag = state;
  state.chain = call("paint_bucket_begin", { x: p[0], y: p[1], shift });
}

/// 動かした（前の計算が終わるまで最後の点だけ覚えておく）。
export function bucketMove(p) {
  const state = drag;
  if (!state) return;
  const first = state.pending === null;
  state.pending = p;
  if (!first) return;
  state.chain = state.chain.then(() => {
    const q = state.pending;
    state.pending = null;
    return call("paint_bucket_extend", { x: q[0], y: q[1], shift: state.shift });
  });
}

/// 離した。
export function bucketUp() {
  const state = drag;
  drag = null;
  if (!state) return;
  state.chain.then(() => call("paint_bucket_end"));
}

// ---------------------------------------------------------------------------
// 串刺し塗り
// ---------------------------------------------------------------------------

let skewer = { pending: false, has_guess: false, banner: null };
let guide = null; // {x, y, w, h, canvas}
let banner = null;
let guideCanvas = null;

function showSkewer(view) {
  if (!view) return;
  skewer = view;
  if (banner) {
    banner.textContent = view.banner ?? "";
    banner.style.display = view.pending && view.banner ? "" : "none";
  }
  refreshGuide();
}

async function refreshGuide() {
  guide = null;
  if (skewer.pending) {
    try {
      const buf = await invoke("paint_skewer_guide");
      guide = decodeGuide(buf);
    } catch (e) {
      notify(String(e));
    }
  }
  drawGuide();
}

function decodeGuide(buf) {
  if (!(buf instanceof ArrayBuffer) || buf.byteLength < 20) return null;
  const view = new DataView(buf);
  const [x, y, w, h] = [0, 4, 8, 12].map((o) => view.getUint32(o, true));
  if (!w || !h) return null;
  const color = new Uint8Array(buf, 16, 4);
  const mask = new Uint8Array(buf, 20);
  const image = new ImageData(w, h);
  for (let i = 0; i < w * h; i++) {
    if (!mask[i]) continue;
    image.data.set(color, i * 4);
  }
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  canvas.getContext("2d").putImageData(image, 0, 0);
  return { x, y, w, h, canvas };
}

function drawGuide() {
  const c = guideCanvas;
  if (!c) return;
  const rect = c.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(rect.width * dpr));
  const h = Math.max(1, Math.round(rect.height * dpr));
  if (c.width !== w || c.height !== h) {
    c.width = w;
    c.height = h;
  }
  const g = c.getContext("2d");
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.clearRect(0, 0, w, h);
  if (!guide || !ctx) return;
  const [a, b, cc, d, e, f] = ctx.viewer._imageToScreenMatrix();
  g.setTransform(a * dpr, b * dpr, cc * dpr, d * dpr, e * dpr, f * dpr);
  g.imageSmoothingEnabled = false;
  g.globalAlpha = 0.55;
  g.drawImage(guide.canvas, guide.x, guide.y);
}

/// クリックした（指定待ちなら、その場所で範囲を決める）。
export async function skewerClick(p, shift) {
  showSkewer(await call("paint_skewer_click", { x: p[0], y: p[1], shift }));
}

async function skewerKey(kind) {
  showSkewer(await call("paint_skewer_key", { kind }));
}

/// 指定待ちのあいだの Enter・Esc（canvas_tools.js より先に受ける）。
function onSkewerKey(e) {
  if (!skewer.pending || dialogOpen() || e.isComposing) return;
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
  if (e.key === "Enter") {
    e.preventDefault();
    e.stopPropagation();
    if (skewer.has_guess) skewerKey("accept");
  } else if (e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation();
    skewerKey(e.shiftKey ? "skip_direction" : "skip");
  }
}

// ---------------------------------------------------------------------------
// バケツの色置換
// ---------------------------------------------------------------------------

export async function replaceColor(p) {
  const view = await call("paint_replace_color", { x: p[0], y: p[1], confirmed: false });
  if (!view?.confirm) return;
  const yes = await ask(
    tr("色置換"),
    tr("クリックした色 #{hex} は線の色です。選択レイヤーのすべてのコマで、この色の線を置き換えますか？", { hex: view.confirm }),
  );
  if (!yes) {
    notify(tr("色置換を取りやめました。"));
    return;
  }
  await call("paint_replace_color", { x: p[0], y: p[1], confirmed: true });
}

/// ゴミ取りを当てる。`points` は線整えでなぞった点（無ければ全体）。
export function dustApply(points = null) {
  return call("paint_dust_apply", { points });
}

// ---------------------------------------------------------------------------
// 進み具合（自動で塗る・見本との違いチェック）
// ---------------------------------------------------------------------------

let progress = null; // {bar, label, close}

function openProgress(title, label) {
  const bar = el("progress", { max: "1", value: "0", style: { width: "100%" } });
  const text = el("div", { class: "sh-message", style: { minWidth: "330px" } }, label);
  const buttons = [{ label: tr("中止"), value: "cancel", escape: true }];
  const done = openDialog({ title, body: el("div", {}, text, bar), buttons });
  const state = {
    bar,
    text,
    close: () => buttons[0].element?.closest(".sh-dialog")?.close?.("closed"),
  };
  done.then((value) => {
    if (value === "cancel") invoke("paint_job_cancel");
    if (progress === state) progress = null;
  });
  progress = state;
}

function onJob(event) {
  if (event.phase === "progress") {
    if (!progress) return;
    const total = Math.max(1, event.total);
    const value = Math.max(0, Math.min(total, event.value));
    progress.bar.max = total;
    progress.bar.value = value;
    progress.text.textContent = `${event.label}\n${value} / ${total}`;
    return;
  }
  progress?.close();
  progress = null;
  if (event.message) notify(event.message);
  if (event.list) showFindings(event.list);
}

// ---------------------------------------------------------------------------
// 候補の一覧（Qt の ReferenceCheckDialog。後ろを触れる窓）
// ---------------------------------------------------------------------------

let findingsBox = null;

function showFindings(list) {
  findingsBox?.close?.(null);
  const rows = list.rows.map((row, i) => {
    const swatch = el("span", {
      class: "paint-swatch",
      style: {
        display: "inline-block", width: "12px", height: "12px", marginRight: "6px", verticalAlign: "middle",
        border: "1px solid var(--border)",
        background: row.rgb ? `rgb(${row.rgb[0]},${row.rgb[1]},${row.rgb[2]})` : "transparent",
      },
    });
    const tr_ = el("tr", { tabindex: "0", style: { cursor: "pointer" } },
      el("td", {}, row.position),
      el("td", {}, row.text),
      el("td", {}, swatch, row.color),
      el("td", { style: { textAlign: "right" } }, row.pixels),
    );
    const pick = () => {
      table.querySelectorAll("tr.selected").forEach((r) => {
        r.classList.remove("selected");
        r.style.background = "";
      });
      tr_.classList.add("selected");
      tr_.style.background = "var(--selection)";
      call("paint_show_finding", { row: i });
    };
    tr_.addEventListener("click", pick);
    tr_.addEventListener("keydown", (e) => {
      if (e.key === "Enter") pick();
    });
    return tr_;
  });
  const head = el("tr", {}, ...[tr("コマ"), tr("内容"), tr("色"), tr("画素数")].map((t) => el("th", { style: { textAlign: "left", padding: "2px 8px 2px 0" } }, t)));
  const table = el("table", { class: "paint-findings", style: { borderCollapse: "collapse", width: "100%" } },
    el("thead", {}, head), el("tbody", {}, rows));
  const body = el("div", { style: { width: "520px", maxWidth: "100%" } },
    el("div", { class: "sh-message", style: { marginBottom: "6px" } }, list.intro),
    list.reference_only ? el("div", { class: "sh-message", style: { marginBottom: "6px" } }, list.reference_only) : null,
    el("div", { style: { maxHeight: "320px", overflow: "auto", border: "1px solid var(--border)" } }, table),
  );
  const buttons = [{ label: tr("閉じる"), value: null, primary: true, escape: true }];
  openDialog({ title: list.title, body, buttons, modal: false });
  findingsBox = buttons[0].element?.closest(".sh-dialog") ?? null;
}

// ---------------------------------------------------------------------------
// 自動で塗る：見本を選ぶ画面（Qt の AutoPaintDialog）
// ---------------------------------------------------------------------------

function thumbnailCanvas(buf, className) {
  const canvas = el("canvas", { class: className });
  if (!(buf instanceof ArrayBuffer) || buf.byteLength < 8) return canvas;
  const view = new DataView(buf);
  const w = view.getUint32(0, true);
  const h = view.getUint32(4, true);
  if (!w || !h || buf.byteLength < 8 + w * h * 4) return canvas;
  canvas.width = w;
  canvas.height = h;
  canvas.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(buf, 8, w * h * 4), w, h), 0, 0);
  return canvas;
}

async function chooseReferences(dialog) {
  const cells = dialog.cells;
  const chosen = new Set([dialog.current]);
  let current = dialog.current;
  let cardSize = 158;
  const thumbs = new Map();
  const cards = [];
  const checker = "repeating-conic-gradient(#ddd 0% 25%, #fff 0% 50%) 50% / 16px 16px";

  const grid = el("div", { class: "paint-auto-grid", role: "listbox", "aria-label": tr("見本にするコマ"), "aria-multiselectable": "true" });
  Object.assign(grid.style, { display: "flex", flexWrap: "wrap", gap: "8px", alignContent: "flex-start", overflow: "auto", flex: "1", minHeight: "0", padding: "4px" });
  const search = el("input", { type: "text", placeholder: tr("コマを検索"), style: { flex: "1" } });
  const first = el("button", { type: "button" }, tr("先頭へ"));
  const last = el("button", { type: "button" }, tr("末尾へ"));
  const slider = el("input", { type: "range", min: "130", max: "230", value: String(cardSize), "aria-label": tr("サムネイルの大きさ"), style: { maxWidth: "180px" } });
  const visibleStatus = el("span", { style: { marginLeft: "auto" } });
  const previewTitle = el("div", {});
  const previewBox = el("div", { style: { flex: "1", minHeight: "160px", display: "flex", alignItems: "center", justifyContent: "center", background: checker, border: "1px solid var(--border)" } });
  const toggle = el("button", { type: "button" });
  const selectionStatus = el("span", { style: { flex: "1" } });
  const clearButton = el("button", { type: "button" }, tr("すべて解除"));
  const selectedList = el("div", { "aria-label": tr("選んだ見本"), style: { minHeight: "100px", maxHeight: "160px", overflow: "auto", border: "1px solid var(--border)" } });
  const removeButton = el("button", { type: "button" }, tr("選んだ見本から外す"));
  const summary = el("span", { style: { flex: "1" } });
  let selectedEntry = null;

  const thumb = (index) => {
    if (!thumbs.has(index)) {
      const cell = cells[index];
      thumbs.set(index, invoke("paint_cell_thumbnail", { frame: cell.frame, layer: cell.layer, max: 280 }).catch(() => null));
    }
    return thumbs.get(index);
  };
  const fitImage = (canvas, w, h) => {
    Object.assign(canvas.style, { maxWidth: `${w}px`, maxHeight: `${h}px`, width: "auto", height: "auto", imageRendering: "auto" });
    return canvas;
  };

  cells.forEach((cell, index) => {
    const image = el("div", { style: { display: "flex", alignItems: "center", justifyContent: "center", background: checker, borderRadius: "4px", overflow: "hidden" } });
    const title = el("div", { style: { fontWeight: "600", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, cell.title);
    const position = el("div", { style: { opacity: "0.75", fontSize: "0.9em" } }, cell.position);
    const card = el("div", { role: "option", tabindex: "0", title: cell.label }, image, title, position);
    Object.assign(card.style, { border: "2px solid var(--border)", borderRadius: "6px", padding: "6px", cursor: "pointer", boxSizing: "border-box", display: "flex", flexDirection: "column", gap: "3px" });
    card.addEventListener("click", () => {
      if (chosen.has(index)) chosen.delete(index);
      else chosen.add(index);
      inspect(index, false);
      changed();
    });
    card.addEventListener("keydown", (e) => {
      if (e.key === " ") {
        e.preventDefault();
        card.click();
      }
    });
    card.addEventListener("focus", () => inspect(index, false));
    cards.push({ card, image, index });
    thumb(index).then((buf) => {
      const canvas = fitImage(thumbnailCanvas(buf, "paint-auto-thumb"), cardSize - 16, Math.round(cardSize * 0.83));
      image.append(canvas);
    });
    grid.append(card);
  });

  const sizeCards = () => {
    for (const { card, image } of cards) {
      card.style.width = `${cardSize + 10}px`;
      image.style.height = `${Math.round(cardSize * 0.83)}px`;
      const canvas = image.querySelector("canvas");
      if (canvas) fitImage(canvas, cardSize - 16, Math.round(cardSize * 0.83));
    }
  };

  function inspect(index, reveal) {
    if (index < 0 || index >= cells.length) return;
    current = index;
    previewTitle.textContent = cells[index].label;
    previewBox.replaceChildren();
    thumb(index).then((buf) => {
      if (current !== index) return;
      const canvas = thumbnailCanvas(buf, "paint-auto-preview");
      Object.assign(canvas.style, { maxWidth: "100%", maxHeight: "100%" });
      previewBox.replaceChildren(canvas);
    });
    if (reveal) {
      search.value = "";
      filter();
      cards[index].card.scrollIntoView({ block: "nearest" });
    }
    updateToggle();
    for (const c of cards) c.card.style.outline = c.index === current ? "1px dashed var(--accent)" : "";
  }

  function updateToggle() {
    toggle.textContent = chosen.has(current) ? tr("見本から外す") : tr("見本に追加");
  }

  function changed() {
    const indices = [...chosen].sort((a, b) => a - b);
    selectedList.replaceChildren();
    if (!indices.includes(selectedEntry)) selectedEntry = null;
    for (const i of indices) {
      const row = el("div", { tabindex: "0", style: { padding: "2px 6px", cursor: "pointer", display: "flex", alignItems: "center", gap: "6px" } });
      const icon = el("span", { style: { width: "42px", height: "36px", display: "inline-flex", alignItems: "center", justifyContent: "center", background: checker } });
      thumb(i).then((buf) => icon.append(fitImage(thumbnailCanvas(buf, ""), 42, 36)));
      row.append(icon, cells[i].label);
      if (i === selectedEntry) row.style.background = "var(--selection)";
      row.addEventListener("click", () => {
        selectedEntry = i;
        inspect(i, true);
        changed();
      });
      selectedList.append(row);
    }
    const count = indices.length;
    selectionStatus.textContent = tr("選んだ見本 {count}枚", { count });
    clearButton.disabled = count === 0;
    removeButton.disabled = count === 0;
    if (count === 0) summary.textContent = tr("見本を選択してください");
    else if (count === cells.length) summary.textContent = tr("塗るコマを残してください");
    else summary.textContent = tr("見本 {references}枚 · 塗るコマ {targets}枚", { references: count, targets: cells.length - count });
    if (runButton.element) runButton.element.disabled = !(count > 0 && count < cells.length);
    for (const c of cards) {
      const on = chosen.has(c.index);
      c.card.style.borderColor = on ? "var(--accent)" : "var(--border)";
      c.card.setAttribute("aria-selected", on ? "true" : "false");
    }
    updateToggle();
  }

  function filter() {
    const text = search.value.trim().toLowerCase();
    let visible = 0;
    for (const c of cards) {
      const hidden = !cells[c.index].label.toLowerCase().includes(text);
      c.card.style.display = hidden ? "none" : "";
      visible += hidden ? 0 : 1;
    }
    visibleStatus.textContent = tr("表示 {visible} / {total}コマ", { visible, total: cells.length });
  }

  search.addEventListener("input", filter);
  first.addEventListener("click", () => inspect(0, true));
  last.addEventListener("click", () => inspect(cells.length - 1, true));
  slider.addEventListener("input", () => {
    cardSize = Number(slider.value);
    sizeCards();
  });
  toggle.addEventListener("click", () => {
    if (chosen.has(current)) chosen.delete(current);
    else chosen.add(current);
    changed();
  });
  clearButton.addEventListener("click", () => {
    chosen.clear();
    changed();
  });
  removeButton.addEventListener("click", () => {
    const target = selectedEntry ?? [...chosen].sort((a, b) => a - b)[0];
    if (target === undefined) return;
    chosen.delete(target);
    selectedEntry = null;
    changed();
  });

  const gallery = el("div", { style: { display: "flex", flexDirection: "column", gap: "6px", flex: "3", minWidth: "0", minHeight: "0" } },
    el("div", { style: { display: "flex", gap: "6px" } }, search, first, last),
    grid,
    el("div", { style: { display: "flex", gap: "8px", alignItems: "center" } }, el("span", {}, tr("表示サイズ")), slider, visibleStatus),
  );
  const side = el("div", { style: { display: "flex", flexDirection: "column", gap: "6px", flex: "1", minWidth: "240px", paddingLeft: "12px" } },
    el("div", { style: { fontWeight: "600" } }, tr("プレビュー")),
    previewTitle, previewBox, toggle,
    el("div", { style: { display: "flex", alignItems: "center", gap: "6px" } }, selectionStatus, clearButton),
    selectedList, removeButton,
  );
  const body = el("div", { style: { display: "flex", flexDirection: "column", gap: "10px", width: "min(1060px, calc(100vw - 80px))", height: "min(640px, calc(100vh - 160px))" } },
    el("div", { style: { display: "flex", flex: "1", minHeight: "0" } }, gallery, side),
    el("div", {}, summary),
  );
  const runButton = { label: tr("この見本で塗る"), value: true, primary: true };
  const buttons = [{ label: tr("キャンセル"), value: false, escape: true }, runButton];
  const opened = openDialog({ title: tr("自動で塗る"), body, buttons, className: "paint-auto-dialog", initialFocus: () => search });
  sizeCards();
  changed();
  inspect(current, true);
  filter();
  const ok = await opened;
  return ok ? [...chosen].sort((a, b) => a - b) : null;
}

export async function autoPaint() {
  let dialog;
  try {
    dialog = await invoke("paint_auto_dialog");
  } catch (e) {
    notify(String(e));
    return;
  }
  const references = await chooseReferences(dialog);
  if (!references) return;
  openProgress(tr("自動で塗る"), tr("全体 0％・残り時間を計算中"));
  try {
    await invoke("paint_auto_run", { references });
  } catch (e) {
    progress?.close();
    notify(String(e));
  }
}

export async function referenceCheck() {
  openProgress(tr("見本との違いチェック"), tr("色を数えています"));
  try {
    await invoke("paint_reference_check");
  } catch (e) {
    progress?.close();
    notify(String(e));
  }
}

// ---------------------------------------------------------------------------
// 組み込み
// ---------------------------------------------------------------------------

const ACTIONS = {
  a_remove_dust: () => dustApply(null),
  a_auto_paint: () => autoPaint(),
  a_reference_check: () => referenceCheck(),
};

/// 引き受ける id の一覧（確かめ用）。
export const claimedPaintActions = () => Object.keys(ACTIONS);

export function installPaintOps({ host, viewer, applyRect }) {
  ctx = { viewer, applyRect };
  guideCanvas = el("canvas", { class: "pma-overlay paint-skewer-guide" });
  host.append(guideCanvas);
  banner = el("div", { class: "paint-skewer-banner" });
  Object.assign(banner.style, {
    position: "absolute", left: "50%", top: "8px", transform: "translateX(-50%)", display: "none",
    background: "var(--surface)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: "6px",
    padding: "4px 12px", pointerEvents: "none", maxWidth: "80%", whiteSpace: "pre-wrap", zIndex: "5",
  });
  host.append(banner);
  new ResizeObserver(() => drawGuide()).observe(host);
  viewer.onchange = ((previous) => () => {
    previous?.();
    drawGuide();
  })(viewer.onchange);

  listen("paint-status", ({ payload }) => {
    if (payload) notify(payload);
  });
  listen("paint-job", ({ payload }) => onJob(payload));
  // 作品が変わったら、指定待ちの案内を片付ける（Rust 側は Session::install で消える）。
  listen("document-changed", () => {
    if (skewer.pending) refreshGuide();
  });
  window.addEventListener("keydown", onSkewerKey, true);
  // 串刺し塗りから別のツールへ移ったら指定待ちをやめる（Qt の _abort_skewer_fill）。
  toolStore.subscribe((view) => {
    const tool = view?.settings?.tool ?? toolStore.settings?.tool;
    if (skewer.pending && tool && tool !== "skewer_fill") skewerKey("abort");
  });
  window.addEventListener("pma-action", (e) => {
    const f = ACTIONS[e.detail?.id];
    if (!f) return;
    e.preventDefault();
    Promise.resolve(f()).catch((err) => notify(String(err)));
  });
}
