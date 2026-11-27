// 【アプリの外枠】ヒストリーのパネル（Qt 版の history_panel.HistoryPanel）。
//
// 本線（左の列）は「開始状態」から今までの操作と、取り消して先にある操作（点線）。
// 戻ってから描き直したときの元の流れは、右の列へ分岐として残る（点線、Rust の history.rs が
// 並べる）。本線のブロックをクリックするとその状態まで一気に戻る／進む。分岐のブロックは
// その流れへ切り替える。

import { tr } from "./i18n.js";
import { el, message } from "./dialogs.js";

// 寸法は Qt と同じ。
const BLOCK_W = 128;
const BLOCK_H = 26;
const COL_GAP = 28;
const ROW_GAP = 16;
const MARGIN = 14;
const RADIUS = 6;
const SVG = "http://www.w3.org/2000/svg";

const rect = (col, row) => ({
  x: MARGIN + col * (BLOCK_W + COL_GAP),
  y: MARGIN + row * (BLOCK_H + ROW_GAP),
});

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

export function helpText() {
  return tr("操作の履歴です。ブロックをクリックすると、その状態まで一気に戻る／進むします。戻ってから編集し直したときの元の履歴は右隣の列に分岐として残り、同じ地点から複数分岐したときは横に並びます。点線のブロックをクリックでそちらへ戻せます。");
}

function label(node) {
  const text = tr(node.label);
  if (node.note === "current") return tr("{label}（現在）", { label: text });
  if (node.note === "branch_many") return tr("{label}（分岐 {total}件）", { label: text, total: node.total });
  if (node.note === "branch") return tr("{label}（分岐）", { label: text });
  return text;
}

// 幅に収まらない名前は「…」で切る。
let measure = null;
function elide(text, width) {
  measure ??= document.createElement("canvas").getContext("2d");
  measure.font = "12px -apple-system, 'Hiragino Sans', 'Yu Gothic UI', sans-serif";
  if (measure.measureText(text).width <= width) return text;
  let lo = 0;
  let hi = text.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (measure.measureText(`${text.slice(0, mid)}…`).width <= width) lo = mid;
    else hi = mid - 1;
  }
  return `${text.slice(0, lo)}…`;
}

export function createHistoryPanel({ invoke, listen }) {
  const title = el("span", { class: "hp-title" }, tr("ヒストリー"));
  const help = el("button", { type: "button", class: "hp-help", title: tr("ヒストリーパネルの使い方") }, "?");
  help.addEventListener("click", () => message(tr("ヒストリーパネルの使い方"), helpText()));
  const scroll = el("div", { class: "hp-scroll" });
  const panel = el("div", { class: "hp-panel" }, el("div", { class: "hp-header" }, title, help), scroll);

  let busy = false;
  async function act(command, args) {
    if (busy) return;
    busy = true;
    try {
      await invoke(command, args);
    } catch (e) {
      console.warn("ヒストリーの移動に失敗", e);
    } finally {
      busy = false;
    }
  }

  function draw(graph) {
    const nodes = graph.nodes;
    const maxCol = Math.max(0, ...nodes.map((n) => n.col));
    const maxRow = Math.max(0, ...nodes.map((n) => n.row));
    const width = MARGIN * 2 + (maxCol + 1) * BLOCK_W + maxCol * COL_GAP;
    const height = MARGIN * 2 + (maxRow + 1) * BLOCK_H + maxRow * ROW_GAP;
    const root = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}` });
    // 先に線（ブロックの後ろ）。
    for (const node of nodes) {
      if (!node.from) continue;
      const p = rect(node.from[0], node.from[1]);
      const c = rect(node.col, node.row);
      const x0 = p.x + BLOCK_W / 2;
      const y0 = p.y + BLOCK_H;
      const x1 = c.x + BLOCK_W / 2;
      const y1 = c.y;
      const mid = (y0 + y1) / 2;
      const d = node.curve ? `M${x0},${y0} C${x0},${mid} ${x1},${mid} ${x1},${y1}` : `M${x0},${y0} L${x1},${y1}`;
      root.append(svg("path", { d, class: `hp-link${node.is_future || node.kind === "branch" ? " future" : ""}` }));
    }
    let current = null;
    for (const node of nodes) {
      const r = rect(node.col, node.row);
      const isCurrent = node.kind === "main" && node.value === 0;
      const classes = ["hp-block"];
      if (isCurrent) classes.push("current");
      else if (node.kind === "branch") classes.push("branch", "future");
      else if (node.is_future) classes.push("future");
      const clickable = node.kind === "branch" || node.value !== 0;
      const g = svg("g", { class: classes.join(" ") });
      if (!clickable) g.style.cursor = "default";
      g.append(svg("rect", { x: r.x + 0.5, y: r.y + 0.5, width: BLOCK_W - 1, height: BLOCK_H - 1, rx: RADIUS, ry: RADIUS }));
      const full = label(node);
      const text = svg("text", { x: r.x + 8, y: r.y + BLOCK_H / 2 + 1 });
      if (isCurrent) text.setAttribute("font-weight", "700");
      text.textContent = elide(full, BLOCK_W - 16);
      g.append(text);
      const tip = svg("title");
      tip.textContent = full;
      g.append(tip);
      if (clickable) {
        g.addEventListener("click", () => {
          if (node.kind === "main") act("history_jump", { delta: node.value });
          else act("history_switch", { index: node.value, steps: node.steps });
        });
      }
      root.append(g);
      if (isCurrent) current = r;
    }
    scroll.replaceChildren(root);
    // 今のブロックが見えるように。
    if (current) {
      const top = current.y - MARGIN;
      const bottom = current.y + BLOCK_H + MARGIN;
      if (top < scroll.scrollTop) scroll.scrollTop = top;
      else if (bottom > scroll.scrollTop + scroll.clientHeight) scroll.scrollTop = bottom - scroll.clientHeight;
    }
  }

  let refreshing = false;
  let again = false;
  async function refresh() {
    if (refreshing) {
      again = true;
      return;
    }
    refreshing = true;
    try {
      do {
        again = false;
        draw(await invoke("history_graph"));
      } while (again);
    } catch (e) {
      scroll.replaceChildren(el("div", { class: "hp-help-text" }, String(e)));
    } finally {
      refreshing = false;
    }
  }
  listen?.("document-changed", ({ payload }) => {
    // 再生の刻みでは履歴は変わらない。
    if (payload?.source === "playback") return;
    refresh();
  });
  refresh();
  panel.refresh = refresh;
  panel.retranslate = () => {
    title.textContent = tr("ヒストリー");
    help.title = tr("ヒストリーパネルの使い方");
    refresh();
  };
  return panel;
}
