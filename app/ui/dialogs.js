// 【アプリの外枠】窓（Qt の QMessageBox・QInputDialog・新規作成・メモ・バージョン情報）。
// どれも画面の中に重ねて出す（OS の窓は使わない）。Esc で閉じる、Enter で既定のボタン。

import { tr } from "./i18n.js";

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// 開いている窓の数（キーの受け付けを止めるのに使う）。
let openCount = 0;
export function dialogOpen() {
  return openCount > 0;
}

/// 窓を開く。buttons は [{label, value, primary, escape}]。閉じたら押したボタンの value で解決する。
/// onKey は窓の中で押したキー（true を返せばそこで止める）。modal が false なら後ろも触れる。
export function openDialog({ title, body, buttons = [], modal = true, onClose, className = "", initialFocus }) {
  return new Promise((resolve) => {
    if (modal) openCount += 1;
    const overlay = el("div", { class: "sh-overlay" });
    if (!modal) {
      overlay.style.background = "transparent";
      overlay.style.pointerEvents = "none";
    }
    const box = el("div", { class: `sh-dialog ${className}`, role: "dialog", "aria-label": title });
    box.style.pointerEvents = "auto";
    const head = el("div", { class: "sh-dialog-title" }, title);
    const content = el("div", { class: "sh-dialog-body" }, body);
    box.append(head, content);
    let finished = false;
    const close = (value) => {
      if (finished) return;
      finished = true;
      if (modal) openCount -= 1;
      overlay.remove();
      document.removeEventListener("keydown", onKeydown, true);
      onClose?.(value);
      resolve(value);
    };
    if (buttons.length) {
      const row = el("div", { class: "sh-dialog-buttons" });
      for (const b of buttons) {
        const button = el("button", { type: "button", class: b.primary ? "primary" : "" }, b.label);
        button.addEventListener("click", async () => {
          if (b.validate && !(await b.validate())) return;
          close(b.value);
        });
        row.append(button);
        b.element = button;
      }
      box.append(row);
    }
    const escapeValue = buttons.find((b) => b.escape)?.value ?? null;
    const primary = buttons.find((b) => b.primary);
    function onKeydown(e) {
      if (!box.isConnected) return;
      // キーを覚えている欄（ショートカット設定）は Esc・Enter も覚える。
      if (e.target?.classList?.contains("sh-key-edit")) return;
      // 上に別の窓が重なっていれば、そちらが受ける。
      const top = [...document.querySelectorAll(".sh-overlay")].pop();
      if (top !== overlay && top?.style.pointerEvents !== "none") return;
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close(escapeValue);
      } else if (e.key === "Enter" && primary && !(e.target instanceof HTMLTextAreaElement) && !e.isComposing) {
        if (e.target instanceof HTMLButtonElement) return;
        e.preventDefault();
        e.stopPropagation();
        primary.element.click();
      }
    }
    document.addEventListener("keydown", onKeydown, true);
    overlay.append(box);
    document.body.append(overlay);
    // ドラッグで動かせるように（題名の部分）。
    let drag = null;
    head.addEventListener("pointerdown", (e) => {
      const r = box.getBoundingClientRect();
      drag = { x: e.clientX - r.left, y: e.clientY - r.top };
      head.setPointerCapture(e.pointerId);
    });
    head.addEventListener("pointermove", (e) => {
      if (!drag) return;
      box.style.position = "fixed";
      box.style.left = `${Math.max(0, e.clientX - drag.x)}px`;
      box.style.top = `${Math.max(0, e.clientY - drag.y)}px`;
    });
    head.addEventListener("pointerup", () => (drag = null));
    const focus = initialFocus?.() ?? primary?.element ?? box.querySelector("input, select, textarea, button");
    focus?.focus();
    if (focus instanceof HTMLInputElement) focus.select();
    box.close = close;
  });
}

/// 知らせ・確かめ（QMessageBox）。buttons は [[label, value, primary?]]。最後のボタンが Esc。
export function message(title, text, buttons = [[tr("OK"), true, true]], escapeValue) {
  const list = buttons.map(([label, value, primary]) => ({ label, value, primary }));
  const esc = list.find((b) => b.value === escapeValue) ?? list[list.length - 1];
  esc.escape = true;
  return openDialog({ title, body: el("div", { class: "sh-message" }, text), buttons: list });
}

/// はい／いいえ（QMessageBox.question）。
export function ask(title, text) {
  return message(title, text, [[tr("はい"), true, true], [tr("いいえ"), false]], false);
}

/// 文字を1つ尋ねる（QInputDialog.getText）。キャンセルなら null。
export async function prompt(title, label, text = "") {
  const input = el("input", { type: "text", value: text, style: { width: "260px" } });
  const body = el("div", { class: "sh-form" }, el("span", { class: "sh-label" }, label), input);
  const ok = await openDialog({
    title,
    body,
    buttons: [
      { label: tr("OK"), value: true, primary: true },
      { label: tr("キャンセル"), value: false, escape: true },
    ],
    initialFocus: () => input,
  });
  return ok ? input.value : null;
}

/// 新規作成（Qt の NewDocumentDialog）。決めたら {width, height, paper}、やめたら null。
export async function newDocumentDialog({ width, height, papers, lastPaper }) {
  const MAX = 16384;
  const sizes = new Map(papers.filter((p) => p.size).map((p) => [p.id, p.size]));
  const paper = el("select", {
    title: tr("選んだ画像を、一番下の下書きレイヤーとして入れます。用紙は 環境設定 › 新規キャンバス で登録します。"),
  });
  paper.append(el("option", { value: "" }, tr("なし")));
  for (const p of papers) {
    if (!p.size) continue;
    paper.append(el("option", { value: p.id }, `${p.name}（${p.size[0]}×${p.size[1]}）`));
  }
  paper.value = sizes.has(lastPaper) ? lastPaper : "";
  const fitBox = el("input", { type: "checkbox", checked: true });
  const fit = el("label", { class: "sh-check" }, fitBox, tr("キャンバスサイズを用紙に合わせる"));
  const spin = (value) => el("input", { type: "number", min: "64", max: String(MAX), step: "1", value: String(value), style: { width: "110px" } });
  const w = spin(width);
  const h = spin(height);
  let manual = [width, height];
  const sync = () => {
    const size = sizes.get(paper.value);
    fitBox.disabled = !size;
    const fitted = Boolean(size) && fitBox.checked;
    if (fitted) {
      if (!w.disabled) manual = [Number(w.value), Number(h.value)];
      w.value = String(size[0]);
      h.value = String(size[1]);
    } else if (w.disabled) {
      w.value = String(manual[0]);
      h.value = String(manual[1]);
    }
    w.disabled = fitted;
    h.disabled = fitted;
  };
  paper.addEventListener("change", sync);
  fitBox.addEventListener("change", sync);
  sync();
  const body = el("div", { class: "sh-form" },
    el("span", { class: "sh-label" }, tr("レイアウト用紙")), paper,
    el("span"), fit,
    el("span", { class: "sh-label" }, tr("幅")), w,
    el("span", { class: "sh-label" }, tr("高さ")), h,
  );
  const ok = await openDialog({
    title: tr("新規作成"),
    body,
    buttons: [
      { label: tr("OK"), value: true, primary: true },
      { label: tr("キャンセル"), value: false, escape: true },
    ],
    initialFocus: () => (w.disabled ? paper : w),
  });
  if (!ok) return null;
  const clamp = (v) => Math.max(64, Math.min(MAX, Math.round(Number(v) || 64)));
  return { width: clamp(w.value), height: clamp(h.value), paper: paper.value || null };
}

/// メモ（Qt の MemoWindow）。後ろを触れる窓。書くたびに onChange。
let memoBox = null;
export function memoWindow({ text, onChange }) {
  if (memoBox?.isConnected) {
    memoBox.querySelector("textarea").focus();
    return;
  }
  const editor = el("textarea", { placeholder: tr("例：3コマ目のタイミング要確認"), spellcheck: "false" });
  editor.value = text;
  editor.addEventListener("input", () => onChange(editor.value));
  const note = el("div", { class: "sh-hint", style: { marginBottom: "8px", gridColumn: "auto" } },
    tr("カット袋やシートのメモ欄として使えます。プロジェクトに保存され、カットフォルダー書き出しではタイムシートの隣へ {name} として出ます。", { name: "_memo.txt" }));
  const body = el("div", { class: "sh-memo" }, note, editor);
  openDialog({
    title: tr("メモ"),
    body,
    modal: false,
    buttons: [{ label: tr("閉じる"), value: null, escape: true }],
    initialFocus: () => editor,
  });
  memoBox = editor.closest(".sh-dialog");
}

/// メモの窓を、開いた作品のメモに合わせる（開いていれば）。
export function updateMemoWindow(text) {
  const editor = memoBox?.isConnected ? memoBox.querySelector("textarea") : null;
  if (editor && editor.value !== text) editor.value = text;
}

/// バージョン情報（Qt の show_about_dialog）。リンクは shell の open_url で開く。
export function aboutDialog({ appName, displayName, repo, openUrl }) {
  const link = (href, text) => {
    const a = el("a", { href: "#" }, text ?? href);
    a.addEventListener("click", (e) => {
      e.preventDefault();
      openUrl(href);
    });
    return a;
  };
  const p = (...children) => el("p", {}, ...children);
  const body = el("div", { class: "sh-message", style: { whiteSpace: "normal", maxWidth: "520px" } },
    el("h3", {}, displayName),
    p("Copyright © 2026 PaintMaskAnimator contributors"),
    p(tr("本ソフトウェアは Apache License 2.0 のもとで配布されています。商用・非商用を問わず、自由に利用・改変・再配布できます。")),
    p(tr("本ソフトウェアは「現状有姿」で提供され、明示黙示を問わずいかなる保証もありません。詳細はライセンス全文を参照してください。")),
    p(tr("ライセンス全文: "), link("https://www.apache.org/licenses/LICENSE-2.0", "Apache License 2.0"), el("br"),
      tr("ソースコード: "), link(repo)),
    p(el("b", {}, tr("サードパーティコンポーネント")), el("br"),
      "Tauri — Apache-2.0 / MIT", el("br"),
      "dockview-core — MIT", el("br"),
      "tauri-plugin-dialog — Apache-2.0 / MIT（rfd — MIT）"),
  );
  return openDialog({
    title: tr("{app} について", { app: appName }),
    body,
    buttons: [{ label: tr("OK"), value: true, primary: true, escape: true }],
  });
}

/// 短い知らせ（数秒で消える）。
let toastTimer = 0;
export function toast(text) {
  let node = document.querySelector(".sh-toast");
  if (!node) {
    node = el("div", { class: "sh-toast" });
    document.body.append(node);
  }
  node.textContent = text;
  node.style.opacity = "1";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.style.opacity = "0"), 2600);
}
