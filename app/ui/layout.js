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

// 既定の幅と高さ（Qt 版の既定に近づける）。
function sizeDefaults() {
  const set = (id, size) => api.getPanel(id)?.group.api.setSize(size);
  set("tools", { width: 64 });
  set("tool-properties", { width: 220 });
  // 右の列は、使用色へできるだけ高さを回す（サークルとスライダーは中身の分だけ）。
  set("color-wheel", { width: 260 });
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
