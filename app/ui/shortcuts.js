// 【アプリの外枠】ショートカット（Qt 版の main_window_shortcuts と widgets.ShortcutDialog）。
//
// キーの書き方は Qt の PortableText（"Ctrl+Shift+S"、macOS では Ctrl が ⌘、Meta が control）。
// - 修飾キーの付いたキーのうちメニューにある操作のものは、OS のメニューが受ける（menu.rs）。
// - それ以外（1・2・A・S・Delete・Shift+E・Ctrl+= など）はここで受けて、shell.js の run へ渡す。
//   文字を入れる欄にいるとき・窓が開いているとき・ほかのパネルが受けたときは受けない。

import { tr } from "./i18n.js";
import { el, openDialog, message, dialogOpen } from "./dialogs.js";

const IS_MAC = /Mac/.test(navigator.platform);

const NAMED = {
  " ": "Space",
  Enter: "Return",
  Escape: "Esc",
  Delete: "Delete",
  Backspace: "Backspace",
  Tab: "Tab",
  ArrowLeft: "Left",
  ArrowRight: "Right",
  ArrowUp: "Up",
  ArrowDown: "Down",
  Home: "Home",
  End: "End",
  PageUp: "PgUp",
  PageDown: "PgDown",
  Insert: "Ins",
};
const MODIFIER_KEYS = { Control: "Ctrl", Shift: "Shift", Alt: "Alt", Meta: "Meta" };

/// 押したキーの名前（修飾キーを除く）。修飾キーだけなら null。
function keyName(e) {
  if (e.key in MODIFIER_KEYS) return null;
  if (e.code === "NumpadEnter") return "Enter";
  if (e.code === "Space") return "Space";
  if (/^Key[A-Z]$/.test(e.code)) return e.code.slice(3);
  if (/^Digit[0-9]$/.test(e.code)) return e.code.slice(5);
  if (/^F[0-9]{1,2}$/.test(e.key)) return e.key;
  if (NAMED[e.key]) return NAMED[e.key];
  if (e.key.length === 1) return e.key.toUpperCase();
  return null;
}

/// 押したキー（修飾キー込み）を Qt の書き方にする。修飾キーだけなら null。
export function comboFromEvent(e) {
  const key = keyName(e);
  if (!key) return null;
  const ctrl = IS_MAC ? e.metaKey : e.ctrlKey;
  const meta = IS_MAC ? e.ctrlKey : e.metaKey;
  // 記号は Shift で別の字になる（Shift+= が +）。その字のまま、Shift は付けない。
  const symbol = e.key.length === 1 && !/[a-zA-Z0-9 ]/.test(e.key) && !/^(Key|Digit)/.test(e.code);
  const parts = [];
  if (ctrl) parts.push("Ctrl");
  if (e.altKey) parts.push("Alt");
  if (e.shiftKey && !symbol) parts.push("Shift");
  if (meta) parts.push("Meta");
  parts.push(symbol ? e.key : key);
  return parts.join("+");
}

/// 押している間の操作の書き方（HoldShortcutEdit：Ctrl・Shift・Alt・Meta の順。修飾キーだけも可）。
function holdComboFromEvent(e) {
  const tokens = [];
  const ctrl = IS_MAC ? e.metaKey : e.ctrlKey;
  const meta = IS_MAC ? e.ctrlKey : e.metaKey;
  if (ctrl) tokens.push("Ctrl");
  if (e.shiftKey) tokens.push("Shift");
  if (e.altKey) tokens.push("Alt");
  if (meta) tokens.push("Meta");
  let key = e.key in MODIFIER_KEYS ? MODIFIER_KEYS[e.key] : keyName(e);
  if (IS_MAC && e.key === "Meta") key = "Ctrl";
  else if (IS_MAC && e.key === "Control") key = "Meta";
  if (!key) return null;
  if (!tokens.includes(key)) tokens.push(key);
  return tokens.join("+");
}

/// 画面に出す書き方（macOS は ⌘⇧ の記号）。
export function displayKey(text) {
  if (!text) return "";
  if (!IS_MAC) return text;
  const symbols = { Ctrl: "⌘", Alt: "⌥", Shift: "⇧", Meta: "⌃" };
  const parts = text.split("+");
  // "Ctrl++" のような最後の + をなくさない。
  if (text.endsWith("++")) parts.splice(parts.length - 2, 2, "+");
  return parts.map((p) => symbols[p] ?? p).join("");
}

function isTextField(node) {
  return node instanceof HTMLInputElement && !["checkbox", "radio", "button", "range"].includes(node.type)
    || node instanceof HTMLTextAreaElement
    || node instanceof HTMLSelectElement
    || node?.isContentEditable;
}

/// 画面のキー受け。table は shortcuts_get の答え。run(id) で操作する。
export function createKeyHandler({ run }) {
  let byCombo = new Map();
  const handler = {
    setTable(table) {
      byCombo = new Map();
      for (const a of table.actions) {
        if (a.hold) continue;
        for (const key of a.keys) {
          // メニューに付けたキーは OS のメニューが受ける。
          if (key === a.menu_key) continue;
          if (!byCombo.has(key)) byCombo.set(key, a.id);
        }
      }
    },
    /// 最後に見たキーと、ほかのパネルが受けたか（メニューと二重にしないため）。
    last: { combo: null, time: 0, handled: false },
  };
  window.addEventListener("keydown", (e) => {
    const combo = comboFromEvent(e);
    handler.last = { combo, time: performance.now(), handled: e.defaultPrevented };
    if (!combo || e.defaultPrevented || e.isComposing || dialogOpen()) return;
    if (isTextField(e.target)) return;
    const id = byCombo.get(combo);
    if (!id) return;
    e.preventDefault();
    if (e.repeat && !["a_prev", "a_next", "a_tl_previous", "a_tl_next", "a_view_zoom_in", "a_view_zoom_out"].includes(id)) return;
    run(id, "key");
  });
  return handler;
}

// ---------------------------------------------------------------------------
// ショートカット設定の窓
// ---------------------------------------------------------------------------

function keyEditor(initial, hold) {
  let value = initial ?? "";
  let recording = false;
  const field = el("input", { type: "text", readonly: true, class: "sh-key-edit", spellcheck: "false" });
  const show = () => {
    field.value = recording ? tr("キーを押してください…") : (value ? displayKey(value) : (hold ? tr("未設定") : ""));
    field.classList.toggle("recording", recording);
  };
  if (hold) field.title = tr("クリックしてキーを入力。BackspaceまたはDeleteで解除。");
  field.addEventListener("focus", () => {
    recording = true;
    show();
  });
  field.addEventListener("blur", () => {
    recording = false;
    show();
  });
  // 修飾キーだけ（押している間の操作）は、離したときに決める。
  let modifierOnly = null;
  field.addEventListener("keydown", (e) => {
    if (!recording) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Escape" && !hold) {
      field.blur();
      return;
    }
    if (hold && (e.key === "Backspace" || e.key === "Delete")) {
      value = "";
      field.blur();
      return;
    }
    if (hold) {
      const text = holdComboFromEvent(e);
      if (e.key in MODIFIER_KEYS) {
        modifierOnly = text;
        return;
      }
      modifierOnly = null;
      if (text) {
        value = text;
        field.blur();
      }
      return;
    }
    const combo = comboFromEvent(e);
    if (combo) {
      value = combo;
      field.blur();
    }
  }, true);
  field.addEventListener("keyup", (e) => {
    if (recording && hold && modifierOnly && e.key in MODIFIER_KEYS) {
      value = modifierOnly;
      modifierOnly = null;
      field.blur();
    }
  });
  show();
  const clear = el("button", { type: "button", title: tr("消去") }, "×");
  clear.addEventListener("click", () => {
    value = "";
    show();
  });
  const box = el("div", { class: "sh-row", style: { flexWrap: "nowrap" } }, field, hold ? null : clear);
  return { element: box, get value() { return value; } };
}

/// ショートカット設定（Qt の ShortcutDialog）。OK で保存したら新しい表を返す。
export async function shortcutDialog({ invoke, table }) {
  const note = el("div", { class: "sh-hint", style: { gridColumn: "auto", marginBottom: "8px" } },
    tr("ショートカット欄を選び、実際のキーまたはキーの組み合わせを押してください。文字列入力ではなくキー入力として認識します。「キャンバス操作」では修飾キー単独も登録できます。"));
  const tabs = el("div", { class: "sh-tabs" });
  const list = el("div", { class: "sh-shortcuts-list" });
  const pages = [];
  const editors = [];
  for (const [category, title] of table.categories) {
    const rows = el("table", { class: "sh-keys-table" });
    for (const a of table.actions.filter((x) => x.category === category)) {
      const editor = keyEditor(a.keys[0] ?? "", a.hold);
      editors.push({ action: a, editor });
      rows.append(el("tr", {},
        el("td", {}, tr(a.name), a.wired ? null : el("span", { class: "sh-pending", title: tr("この機能はまだ移していません：{name}", { name: tr(a.name) }) }, "＊")),
        el("td", {}, editor.element)));
    }
    const tab = el("div", { class: "sh-tab" }, tr(title));
    const index = pages.length;
    tab.addEventListener("click", () => select(index));
    tabs.append(tab);
    pages.push({ tab, rows });
  }
  function select(index) {
    pages.forEach((p, i) => p.tab.classList.toggle("active", i === index));
    list.replaceChildren(pages[index].rows);
  }
  select(0);
  const body = el("div", { class: "sh-shortcuts" }, note, tabs, list);
  let saved = null;
  const ok = await openDialog({
    title: tr("ショートカット設定"),
    body,
    buttons: [
      {
        label: tr("OK"),
        value: true,
        primary: true,
        validate: async () => {
          const keys = {};
          for (const { action, editor } of editors) {
            const first = editor.value;
            // 変えていない欄は触らない（Ctrl+Shift+Z などの2つ目のキーを残す）。
            if (first === (action.keys[0] ?? "")) keys[action.id] = action.keys;
            else keys[action.id] = first ? [first] : [];
          }
          for (const a of table.actions) if (!(a.id in keys)) keys[a.id] = a.keys;
          try {
            saved = await invoke("shortcuts_set", { keys });
            return true;
          } catch (dups) {
            const name = (id) => tr(table.actions.find((a) => a.id === id)?.name ?? id);
            const details = (Array.isArray(dups) ? dups : [[String(dups), "", ""]])
              .slice(0, 12)
              .map(([key, first, second]) => (second ? `${key}: ${name(first)} / ${name(second)}` : key))
              .join("\n");
            await message(tr("ショートカットの重複"), `${tr("同じショートカットが複数の機能に登録されています。")}\n\n${details}`);
            return false;
          }
        },
      },
      { label: tr("キャンセル"), value: false, escape: true },
    ],
  });
  return ok ? saved : null;
}
