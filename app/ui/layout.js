// パネルの配置（dockview）。今の Qt 版と同じ並びを既定にする
// （paintmaskanimator/main_window_ui_build.py の addDockWidget の順）。
//
// パネルは OS の別の窓にも出せる（dockview の popout）。別の窓は Rust の on_new_window で、
// 同じ設定の WebView の窓として開く。キャンバスは WebGL の文脈を持つので、別の窓には出さない。

const DV = window["dockview-core"];
const { invoke } = window.__TAURI__.core;

/// 既定のパネル。[id, 題名]。中身は app.js が `panels` で渡す。
export const PANELS = [
  ["canvas", "キャンバス"],
  ["tools", "ツール"],
  ["tool-properties", "ツールプロパティ"],
  ["actions", "アクション"],
  ["color-wheel", "カラーサークル"],
  ["color-sliders", "カラースライダー"],
  ["used-colors", "使用色"],
  ["history", "ヒストリー"],
  ["subview", "サブビュー"],
  ["timeline", "タイムライン"],
];

const title = (id) => PANELS.find(([p]) => p === id)?.[1] ?? id;

function headerAction(group) {
  const holder = document.createElement("div");
  holder.className = "dock-action";
  const button = document.createElement("button");
  holder.appendChild(button);
  const refresh = () => {
    const out = group.api.location.type === "popout";
    const hasCanvas = group.panels.some((p) => p.id === "canvas");
    button.style.display = hasCanvas ? "none" : "";
    button.textContent = out ? "戻す" : "⧉";
    button.title = out ? "メインの窓に戻す" : "別の窓に出す";
  };
  button.addEventListener("click", async () => {
    if (group.api.location.type === "popout") {
      // 窓を閉じると、dockview がもとの場所に戻す。
      const label = popoutLabels.get(group);
      if (label) await invoke("close_window", { label });
    } else {
      await api.addPopoutGroup(group);
    }
  });
  return {
    element: holder,
    init: () => {
      refresh();
      group.api.onDidLocationChange?.(refresh);
      group.api.onDidAddPanel?.(refresh);
      group.api.onDidRemovePanel?.(refresh);
    },
    dispose: () => {},
  };
}

let api = null;
const popoutLabels = new Map();
let popoutCount = 0;

/// 配置を作る。panels は id → 中身の要素を作る関数。
export function createLayout(host, panels) {
  api = DV.createDockview(host, {
    theme: DV.themeDark,
    popoutUrl: "/popout.html",
    createRightHeaderActionComponent: headerAction,
    createComponent: (options) => {
      // 【アプリの外枠】パネルを閉じて出し直すとき・配置を戻すときも同じ中身を使う。
      let element = made.get(options.name);
      if (!element) {
        const make = panels[options.name];
        element = make ? make() : placeholder(options.name);
        made.set(options.name, element);
      }
      return { element, init: () => {} };
    },
  });
  // 別の窓の label は、Rust が開いた順に popout-1, popout-2, … と付ける（dockview が開く順と同じ）。
  api.onDidAddPopoutGroup((e) => {
    popoutCount++;
    const label = `popout-${popoutCount}`;
    popoutLabels.set(e.group, label);
    const names = e.group.panels.map((p) => p.title).join("・");
    invoke("shape_window", { label, width: 360, height: 420, title: `${names} — PaintMaskAnimator` });
  });
  api.onDidRemovePopoutGroup((e) => popoutLabels.delete(e.group));

  addDefaults();
  return api;
}

/// 既定の並び（パネルの id → 置く場所）。
const DEFAULT_POSITIONS = [
  ["canvas", undefined],
  ["tools", { referencePanel: "canvas", direction: "left" }],
  ["tool-properties", { referencePanel: "tools", direction: "right" }],
  ["actions", { referencePanel: "tool-properties", direction: "below" }],
  ["color-wheel", { referencePanel: "canvas", direction: "right" }],
  ["color-sliders", { referencePanel: "color-wheel", direction: "below" }],
  ["used-colors", { referencePanel: "color-sliders", direction: "below" }],
  ["history", { referencePanel: "used-colors", direction: "within" }],
  // 【Group C】サブビューは Qt 版と同じく右端の列（RightDockWidgetArea）。
  ["subview", { direction: "right" }],
  // タイムラインは Qt 版と同じく、窓の幅いっぱいに下へ置く。
  ["timeline", { direction: "below" }],
];

const made = new Map();
let titleOf = (id) => title(id);

function addPanel(id, position) {
  return api.addPanel({ id, component: id, title: titleOf(id), position });
}

function addDefaults() {
  for (const [id, position] of DEFAULT_POSITIONS) addPanel(id, position);
  api.getPanel("used-colors").api.setActive();
  api.getPanel("canvas").api.setActive();
  sizeDefaults();
}

// ---- 【アプリの外枠】パネルの表示・配置（shell.js が使う） ----

/// [id, 題名, 表示中か] の並び（メニューの「パネル」）。
export function panelStates() {
  return PANELS.map(([id, name]) => [id, name, Boolean(api?.getPanel(id))]);
}

/// パネルを閉じる・出す（Qt の toggleViewAction）。出すときは既定の場所の近くに置く。
export function togglePanel(id, visible) {
  const panel = api.getPanel(id);
  const show = visible ?? !panel;
  if (!show && panel) {
    api.removePanel(panel);
  } else if (show && !panel) {
    const position = DEFAULT_POSITIONS.find(([p]) => p === id)?.[1];
    const reference = position?.referencePanel;
    addPanel(id, !reference || api.getPanel(reference) ? position : { referencePanel: "canvas", direction: "right" });
  }
}

/// 起動したときと同じ配置に戻す（ワークスペース › 初期設定）。
export function resetLayout() {
  api.clear();
  addDefaults();
}

/// 今の配置（ワークスペースに保存する）。
export function saveLayout() {
  return api.toJSON();
}

/// 保存した配置に戻す。戻せなければ false（配置はそのまま）。
export function loadLayout(layout) {
  try {
    api.fromJSON(layout);
    return true;
  } catch {
    return false;
  }
}

/// 【Group C】Qt 版で保存した配置（workspace.rs の QtWorkspace）を組む（Qt の apply）。
/// 戻せなければ false（配置は元のまま）。
export function loadQtLayout(ws) {
  if (!ws?.root) return false;
  const before = api.toJSON();
  const first = (node) => (node.type === "area" ? node.panels[0] : first(node.children[0]));
  const placed = new Set();
  const put = (id, position) => {
    if (placed.has(id) || !PANELS.some(([p]) => p === id)) return false;
    placed.add(id);
    addPanel(id, position);
    return true;
  };
  // 子の代表（先頭のパネル）を先に向きどおりに並べてから、子の中を組む。こうすると代表の組が
  // この分割の直下に入る（dockview の分割は入れ子ごとに向きが交互）。
  const fill = (node) => {
    if (node.type === "area") {
      const head = node.panels[0];
      for (const id of node.panels.slice(1)) put(id, { referencePanel: head, direction: "within" });
      if (node.current) api.getPanel(node.current)?.api.setActive();
      return;
    }
    const direction = node.horizontal ? "right" : "below";
    const reps = node.children.map(first);
    for (let i = 1; i < reps.length; i++) put(reps[i], { referencePanel: reps[i - 1], direction });
    node.children.forEach(fill);
  };
  const resize = (node, width, height) => {
    if (node.type !== "split") return;
    node.children.forEach((child, i) => {
      const share = node.sizes[i] ?? 1 / node.children.length;
      const w = node.horizontal ? width * share : width;
      const h = node.horizontal ? height : height * share;
      if (i < node.children.length - 1) {
        api.getPanel(first(child))?.group.api.setSize(node.horizontal ? { width: Math.round(w) } : { height: Math.round(h) });
      }
      resize(child, w, h);
    });
  };
  try {
    api.clear();
    put(first(ws.root), undefined);
    fill(ws.root);
    resize(ws.root, api.width, api.height);
    // 浮いたパネルは窓の中の浮いた組にする（一つの組にまとめる）。
    ws.floating?.forEach((f, index) => {
      const ids = [];
      const collect = (node) => (node.type === "area" ? ids.push(...node.panels) : node.children.forEach(collect));
      collect(f.root);
      const head = ids.find((id) => !placed.has(id) && PANELS.some(([p]) => p === id));
      if (!head) return;
      placed.add(head);
      const [, , w, h] = f.geometry ?? [0, 0, 360, 420];
      api.addPanel({
        id: head, component: head, title: titleOf(head),
        floating: { position: { left: 40 + index * 24, top: 40 + index * 24 }, width: Math.min(w, api.width - 60), height: Math.min(h, api.height - 60) },
      });
      for (const id of ids) if (id !== head) put(id, { referencePanel: head, direction: "within" });
    });
    if (!api.getPanel("canvas")) addPanel("canvas", { direction: "right" });
    return true;
  } catch (e) {
    console.error(e);
    try {
      api.fromJSON(before);
    } catch {
      resetLayout();
    }
    return false;
  }
}

/// 配置が変わったら呼ぶ。
export function onLayoutChange(callback) {
  api.onDidAddPanel(callback);
  api.onDidRemovePanel(callback);
  api.onDidLayoutFromJSON?.(callback);
}

/// テーマ（"light" / "dark"）。
export function setLayoutTheme(name) {
  api.updateOptions({ theme: name === "dark" ? DV.themeDark : DV.themeLight });
}

/// パネルの題名を訳す。
export function translateTitles(tr) {
  titleOf = (id) => tr(title(id));
  for (const [id] of PANELS) api.getPanel(id)?.api.setTitle(titleOf(id));
}

/// 【Group C】既定の幅と高さに戻す（パネルを外すと dockview が列を等分するので、
/// 起動のときにサブビューを閉じたあとに使う）。
export function fitDefaultSizes() {
  sizeDefaults();
}

// 既定の幅と高さ（Qt 版の既定に近づける）。
function sizeDefaults() {
  const set = (id, size) => api.getPanel(id)?.group.api.setSize(size);
  set("tools", { width: 64 });
  set("tool-properties", { width: 220 });
  // 右の列は、使用色へできるだけ高さを回す（サークルとスライダーは中身の分だけ）。
  set("color-wheel", { width: 260 });
  // 【Group C】サブビューは Qt の DEFAULT_PANEL_WIDTHS の 240。縮めた分は左の色の列へ回るので、
  // その分をキャンバスへ戻す（キャンバスを広げると、すぐ右の色の列が縮む）。
  set("subview", { width: 240 });
  const canvasGroup = api.getPanel("canvas")?.group;
  const colorGroup = api.getPanel("color-wheel")?.group;
  if (canvasGroup && colorGroup && colorGroup.api.width > 260) {
    canvasGroup.api.setSize({ width: canvasGroup.api.width + colorGroup.api.width - 260 });
    set("subview", { width: 240 });
  }
  set("used-colors", { height: 2000 });
  set("color-sliders", { height: 112 });
  set("color-wheel", { height: 290 });
  set("timeline", { height: 220 });
}

function placeholder(id) {
  const el = document.createElement("div");
  el.className = "panel placeholder";
  el.textContent = `${title(id)}（これから作る）`;
  return el;
}
