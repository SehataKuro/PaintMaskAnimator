// ツールのパネル（Qt 版の toolpanel.py の ToolSelectorPanel・SwatchStack）と、
// ツールの状態の控え（toolStore）。
//
// ツールの設定の正本は Rust（tools.rs）にあり、`tools-changed` の事件で届く。画面の
// パネル（ツール・ツールプロパティ・キャンバス）は toolStore を通して読み書きする。
import { iconSvg } from "./icons.js";
import { setTip, hideTip } from "./tooltip.js";
import { PressureEditor } from "./pressure.js";
import { displayKey } from "./shortcuts.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

/// ツールの名前（Qt の tool_label）。ライン（U）はアイコンを持たない。
export const TOOL_LABELS = {
  brush: "ブラシ",
  line: "ライン",
  shape: "図形",
  bucket: "バケツ",
  lasso_fill: "投げ縄塗り",
  lasso: "投げ縄選択",
  replace_color: "色置換",
  skewer_fill: "串刺し塗り",
  rect_select: "長方形選択",
  auto_select: "自動選択",
  eyedropper: "スポイト",
  dust: "ゴミ取り",
};

export const toolLabel = (id) => TOOL_LABELS[id] ?? id;

// ショートカットの表（正本は外枠の actions.rs・shortcuts_get。ショートカット設定で変えた
// キーもここに入る）。ツールの説明と、押している間の操作のキーに使う。
let shortcutTable = null;
export async function refreshShortcuts() {
  try {
    shortcutTable = await invoke("shortcuts_get");
  } catch {
    // 表が無いとき（試験など）は既定のキーのまま。
  }
  return shortcutTable;
}

function keysOf(id) {
  return shortcutTable?.actions.find((a) => a.id === id)?.keys ?? null;
}

/// 押している間の操作のキー（a_hold_* → ["Shift", "Space"] など）。
export function holdKeys() {
  const fallback = {
    a_hold_hand: ["Space"],
    a_hold_rotate: ["Shift", "Space"],
    a_hold_zoom: /Mac/.test(navigator.platform) ? ["Alt", "Space"] : ["Ctrl", "Space"],
    a_hold_eyedropper: ["Alt"],
  };
  const out = {};
  for (const [id, keys] of Object.entries(fallback)) {
    const stored = keysOf(id);
    out[id] = stored ? (stored[0] ? stored[0].split("+") : []) : keys;
  }
  return out;
}

/// ショートカットを添えた名前（issue #102）。キーが無ければ名前だけ。
export function toolLabelWithKey(id) {
  const key = keysOf(`tool:${id}`)?.[0];
  return key ? `${toolLabel(id)}（${displayKey(key)}）` : toolLabel(id);
}

/// ツールの状態の控え。
export const toolStore = {
  view: null,
  listeners: new Set(),
  async init() {
    await refreshShortcuts();
    this.view = await invoke("tools_state");
    await listen("tools-changed", ({ payload }) => this.receive(payload));
    this.emit();
  },
  receive(view) {
    this.view = view;
    this.emit();
  },
  emit() {
    for (const f of this.listeners) f(this.view);
  },
  subscribe(f) {
    this.listeners.add(f);
    if (this.view) f(this.view);
    return () => this.listeners.delete(f);
  },
  get settings() {
    return this.view?.settings;
  },
  /// 設定の一部を変える（Rust が範囲を直して保存し、tools-changed で返す）。
  async set(patch) {
    const view = await invoke("tools_set", { patch });
    this.receive(view);
    return view;
  },
};

/// グループ（まとめるときだけ使う）。
export function groupOf(id, view) {
  if (!view?.settings?.tool_groups_enabled) return null;
  for (const [group, members] of view.groups) if (members.includes(id)) return group;
  return null;
}

export function groupMembers(group, view) {
  return view.groups.find(([g]) => g === group)?.[1] ?? [];
}

/// ツールを選ぶ。グループの中なら、表に出すツールも覚える。
export function selectTool(id) {
  const view = toolStore.view;
  if (!view) return;
  const patch = { tool: id };
  const group = groupOf(id, view);
  if (group) patch.group_current = { [group]: id };
  if (view.settings.tool !== id || group) return toolStore.set(patch);
}

/// 同じグループの次のツール（Qt の next_in_group）。グループに入っていなければ自分。
export function nextInGroup(id, view) {
  const group = groupOf(id, view);
  if (!group) return id;
  const members = groupMembers(group, view);
  return members[(members.indexOf(id) + 1) % members.length];
}

// ---------------------------------------------------------------------------
// 小さな部品（メニュー・ダイアログ。見た目はタイムラインと同じ）
// ---------------------------------------------------------------------------

/// 選べる項目のメニューを開く。items は {label, icon?, checked?, action}。
export function openMenu(x, y, items) {
  closeMenu();
  const menu = document.createElement("div");
  menu.className = "tl-menu pma-menu";
  for (const item of items) {
    if (item === "-") {
      menu.appendChild(Object.assign(document.createElement("div"), { className: "tl-menu-sep" }));
      continue;
    }
    const row = document.createElement("div");
    row.className = "tl-menu-item pma-menu-item";
    if (item.checked) row.classList.add("checked");
    if (item.icon) {
      const icon = document.createElement("span");
      icon.className = "pma-menu-icon";
      icon.innerHTML = iconSvg(item.icon);
      row.appendChild(icon);
    }
    row.appendChild(document.createTextNode(item.label));
    row.addEventListener("click", () => {
      closeMenu();
      item.action?.();
    });
    menu.appendChild(row);
  }
  document.body.appendChild(menu);
  const w = menu.offsetWidth;
  const h = menu.offsetHeight;
  menu.style.left = `${Math.max(4, Math.min(x, window.innerWidth - w - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(y, window.innerHeight - h - 4))}px`;
  setTimeout(() => window.addEventListener("pointerdown", outside, true));
  openMenu.current = menu;
  return menu;
}

function outside(e) {
  if (openMenu.current && !openMenu.current.contains(e.target)) closeMenu();
}

export function closeMenu() {
  window.removeEventListener("pointerdown", outside, true);
  openMenu.current?.remove();
  openMenu.current = null;
}

/// 中身を受け取ってダイアログを開く。buttons は [{label, primary?, action}]。
/// action が false を返したら閉じない。
export function openDialog(title, content, buttons) {
  const overlay = document.createElement("div");
  overlay.className = "tl-dialog-overlay";
  const dialog = document.createElement("div");
  dialog.className = "tl-dialog pma-dialog";
  const head = document.createElement("div");
  head.className = "tl-dialog-title";
  head.textContent = title;
  dialog.append(head, content);
  const row = document.createElement("div");
  row.className = "tl-dialog-buttons";
  const close = () => overlay.remove();
  for (const b of buttons) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = b.label;
    if (b.primary) button.className = "primary";
    button.addEventListener("click", async () => {
      if ((await b.action?.()) !== false) close();
    });
    row.appendChild(button);
  }
  dialog.appendChild(row);
  overlay.appendChild(dialog);
  overlay.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Escape") close();
  });
  document.body.appendChild(overlay);
  return { overlay, dialog, close };
}

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "tip") setTip(node, v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node[k] = v;
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

const hex = (rgb) => `#${rgb.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
const fromHex = (text) => {
  const m = /^#?([0-9a-f]{6})$/i.exec(text.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
export { hex, fromHex };

/// 色を選ぶ（OS の色の窓）。選んだら [r, g, b] を返す。
export function pickColor(initial) {
  return new Promise((resolve) => {
    const input = el("input", { type: "color", value: hex(initial) });
    input.style.position = "fixed";
    input.style.opacity = "0";
    input.style.pointerEvents = "none";
    document.body.appendChild(input);
    input.addEventListener("change", () => {
      resolve(fromHex(input.value));
      input.remove();
    });
    input.addEventListener("blur", () => setTimeout(() => input.remove(), 1000));
    input.click();
  });
}

// ---------------------------------------------------------------------------
// 環境設定のツールの項目（Qt の preferences_dialog.py の「一般」と「筆圧」から、
// ツールに関わるもの）
// ---------------------------------------------------------------------------

export function openToolPreferences() {
  const s = toolStore.settings;
  if (!s) return;
  const groups = el("input", { type: "checkbox", checked: s.tool_groups_enabled });
  const hold = el("input", { type: "checkbox", checked: s.tool_hold_enabled });
  const holdMs = el("input", { type: "number", min: 0, max: 3000, step: 50, value: s.tool_hold_ms, disabled: !s.tool_hold_enabled });
  const bgTool = el("select", {},
    el("option", { value: "brush", text: "ブラシ" }),
    el("option", { value: "bucket", text: "バケツ" }));
  bgTool.value = s.background_color_tool;
  const apply = (patch) => toolStore.set(patch);
  // すぐに反映して保存する（#109）。
  groups.addEventListener("change", () => apply({ tool_groups_enabled: groups.checked }));
  hold.addEventListener("change", () => {
    holdMs.disabled = !hold.checked;
    apply({ tool_hold_enabled: hold.checked });
  });
  holdMs.addEventListener("change", () => apply({ tool_hold_ms: Number(holdMs.value) || 0 }));
  bgTool.addEventListener("change", () => apply({ background_color_tool: bgTool.value }));
  const hint = (text) => el("div", { class: "pma-hint", text });
  const field = (title, ...widgets) => el("div", { class: "pma-pref-row" }, el("div", { class: "pma-pref-title", text: title }), el("div", { class: "pma-pref-body" }, ...widgets));
  const content = el("div", { class: "pma-prefs" },
    field("背景色選択時の描画ツール", bgTool),
    hint("描画ツール以外で背景色のショートカット（E）を押したときに使います。"),
    field("ツールのアイコン", el("label", {}, groups, "ツールのアイコンをまとめる")),
    hint("バケツ・投げ縄塗り・串刺し塗りと、投げ縄選択・長方形選択・自動選択を、それぞれ1つのアイコンに重ねます。もう一度押すか長押しで切り替えます。"),
    field("ツールの一時切り替え", el("label", {}, hold, "ツールのキーを長押ししたら、離したときに元のツールへ戻す")),
    field("", el("label", {}, holdMs, " ms 以上押したら")),
    hint("ツールのキー（P・G など）を押すとすぐ切り替わり、この長さ以上押してから離すと元のツールへ戻ります。0 ms にすると押している間だけ切り替わり、離せば必ず元のツールに戻ります（完全な一時切り替え）。短く押して切り替えたままにしたいときは 300〜500 ms ほどにします。"),
    el("div", { class: "pma-pref-section", text: "筆圧" }),
    pressurePresetsEditor(),
  );
  openDialog("環境設定", content, [{ label: "閉じる", primary: true }]);
}

/// 筆圧のプリセットの編集（Qt の _build_pressure_page）。
function pressurePresetsEditor() {
  const combo = el("select");
  const editor = new PressureEditor();
  const remove = el("button", { type: "button", text: "削除" });
  const presets = () => toolStore.settings.pressure_presets;
  const active = () => toolStore.settings.pressure_active_preset;
  const reload = () => {
    combo.replaceChildren(...presets().map((p) => el("option", { value: p.name, text: p.name })));
    combo.value = active();
    remove.disabled = presets().length <= 1;
    const p = presets().find((x) => x.name === active()) ?? presets()[0];
    editor.setSettings(p);
  };
  const save = (list, name = active()) => toolStore.set({ pressure_presets: list, pressure_active_preset: name }).then(reload);
  combo.addEventListener("change", () => save(presets(), combo.value));
  editor.onchange = () => {
    const list = presets().map((p) => (p.name === active() ? { name: p.name, ...editor.settings() } : p));
    toolStore.set({ pressure_presets: list });
  };
  const unique = (base) => {
    const names = new Set(presets().map((p) => p.name));
    if (!names.has(base)) return base;
    for (let i = 2; ; i++) if (!names.has(`${base} ${i}`)) return `${base} ${i}`;
  };
  const add = el("button", {
    type: "button", text: "追加", tip: "今のプリセットを元に、新しいプリセットを作ります。",
    onclick: () => {
      const name = unique("新しいプリセット");
      save([...presets(), { name, ...editor.settings() }], name);
    },
  });
  const rename = el("button", {
    type: "button", text: "名前を変更…",
    onclick: () => {
      const old = active();
      const input = el("input", { type: "text", value: old });
      openDialog("名前を変更", el("div", {}, el("div", { class: "tl-dialog-note", text: "プリセットの名前" }), input), [
        { label: "キャンセル" },
        {
          label: "OK", primary: true,
          action: () => {
            const name = input.value.trim();
            if (name === old) return true;
            if (!name || presets().some((p) => p.name === name)) {
              input.setCustomValidity("その名前は使えません。空欄か、ほかのプリセットと同じ名前です。");
              input.reportValidity();
              return false;
            }
            save(presets().map((p) => (p.name === old ? { ...p, name } : p)), name);
            return true;
          },
        },
      ]);
      input.focus();
    },
  });
  remove.addEventListener("click", () => {
    const list = presets().filter((p) => p.name !== active());
    if (list.length) save(list, list[0].name);
  });
  reload();
  return el("div", { class: "pma-pressure-page" },
    el("div", { text: "使うプリセット" }),
    el("div", { class: "pma-row" }, combo, add, rename, remove),
    el("div", { class: "pma-hint", text: "ブラシが「全体の設定を使う」のとき、ここで選んだプリセットで描きます。ブラシだけの設定は、ツールプロパティの 筆圧… で切り替えます。" }),
    editor.element);
}

// ---------------------------------------------------------------------------
// ツールのパネル
// ---------------------------------------------------------------------------

const LONG_PRESS_MS = 450;

/// ツールのパネルを作る。
export function createToolsPanel() {
  const panel = el("div", { class: "panel pma-tools" });
  const grid = el("div", { class: "pma-tool-grid" });
  const swatch = el("div", { class: "pma-swatch" });
  panel.append(grid, swatch);
  // ショートカット設定で変えたキーを説明に出す。
  panel.addEventListener("pointerenter", () => refreshShortcuts());

  // 描画色（メインが左上・サブが右下、下に背景色の帯）。
  const main = el("button", { type: "button", class: "pma-swatch-main", tip: "クリック：メイン色に切替" });
  const sub = el("button", { type: "button", class: "pma-swatch-sub", tip: "クリック：サブ色に切替" });
  const bg = el("button", { type: "button", class: "pma-swatch-bg", tip: "クリック：背景色で描画／右クリック：背景色を変更" });
  swatch.append(sub, main, bg);
  main.addEventListener("click", () => toolStore.set({ color_mode: "main" }));
  sub.addEventListener("click", () => toolStore.set({ color_mode: "sub" }));
  bg.addEventListener("click", () => toolStore.set({ color_mode: "transparent" }));
  const changeBackground = async (e) => {
    e.preventDefault();
    const rgb = await pickColor(toolStore.settings.background_color);
    if (rgb) toolStore.set({ background_color: rgb, color_mode: "transparent" });
  };
  bg.addEventListener("contextmenu", changeBackground);
  bg.addEventListener("dblclick", changeBackground);
  for (const [button, key] of [[main, "color_main"], [sub, "color_sub"]]) {
    button.addEventListener("dblclick", async () => {
      const rgb = await pickColor(toolStore.settings[key]);
      if (rgb) toolStore.set({ [key]: rgb, color_mode: key === "color_main" ? "main" : "sub" });
    });
  }

  panel.addEventListener("contextmenu", (e) => {
    if (e.target === bg) return;
    e.preventDefault();
    openMenu(e.clientX, e.clientY, [{ label: "ツールの設定…", action: openToolPreferences }]);
  });

  let dragTools = null;
  let press = null;

  function items(view) {
    // 並び（tool_order）に沿って、まとめるときはグループを最初のツールの場所に置く。
    const out = [];
    const seen = new Set();
    for (const id of view.settings.tool_order) {
      const group = groupOf(id, view);
      if (group) {
        if (seen.has(group)) continue;
        seen.add(group);
        out.push({ key: group, group, members: groupMembers(group, view), shown: view.settings.group_current[group] });
      } else {
        out.push({ key: id, group: null, members: [id], shown: id });
      }
    }
    return out;
  }

  function tipFor(item) {
    if (!item.group) return toolLabelWithKey(item.shown);
    const names = item.members.map((m) => toolLabelWithKey(m)).join("、");
    return `${toolLabelWithKey(item.shown)}\n（もう一度押すか長押しで切り替え：${names}）`;
  }

  function showGroupMenu(item, button) {
    const r = button.getBoundingClientRect();
    const view = toolStore.view;
    openMenu(r.right, r.top, item.members.map((id) => ({
      label: toolLabel(id),
      icon: id,
      checked: id === view.settings.tool,
      action: () => selectTool(id),
    })));
  }

  function render(view) {
    if (!view) return;
    const s = view.settings;
    grid.replaceChildren();
    for (const item of items(view)) {
      const button = el("button", { type: "button", class: "pma-tool" });
      button.innerHTML = iconSvg(item.shown);
      if (item.group) button.classList.add("grouped");
      const active = item.members.includes(s.tool);
      if (active) button.classList.add("active");
      button.setAttribute("aria-label", toolLabel(item.shown));
      setTip(button, () => tipFor(item));
      button.draggable = true;
      button.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        press = { item, wasActive: item.members.includes(toolStore.settings.tool), fired: false };
        if (item.group) {
          press.timer = setTimeout(() => {
            press.fired = true;
            showGroupMenu(item, button);
          }, LONG_PRESS_MS);
        }
      });
      button.addEventListener("pointerup", (e) => {
        if (e.button !== 0 || !press || press.item !== item) return;
        clearTimeout(press.timer);
        const p = press;
        press = null;
        if (p.fired) return;
        if (item.group && p.wasActive) {
          // 表に出ているツールをもう一度押した：グループのメニュー。
          showGroupMenu(item, button);
          return;
        }
        selectTool(item.shown);
      });
      button.addEventListener("dragstart", (e) => {
        clearTimeout(press?.timer);
        press = null;
        hideTip();
        dragTools = item.members;
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", item.key);
      });
      button.addEventListener("dragover", (e) => {
        if (!dragTools) return;
        e.preventDefault();
        button.classList.add("drop");
      });
      button.addEventListener("dragleave", () => button.classList.remove("drop"));
      button.addEventListener("drop", (e) => {
        e.preventDefault();
        button.classList.remove("drop");
        if (!dragTools || item.members.some((m) => dragTools.includes(m))) return;
        // 落とした所のツールの前へ並べ替える（グループはまとめて動かす）。
        const order = toolStore.settings.tool_order.filter((t) => !dragTools.includes(t));
        const at = order.indexOf(item.members[0]);
        order.splice(at < 0 ? order.length : at, 0, ...dragTools);
        dragTools = null;
        toolStore.set({ tool_order: order });
      });
      button.addEventListener("dragend", () => (dragTools = null));
      grid.appendChild(button);
    }
    const style = (button, rgb, selected) => {
      button.style.background = hex(rgb);
      button.classList.toggle("selected", selected);
    };
    style(main, s.color_main, s.color_mode === "main");
    style(sub, s.color_sub, s.color_mode === "sub");
    style(bg, s.background_color, s.color_mode === "transparent");
  }

  toolStore.subscribe(render);
  return panel;
}
