// タイムリマップの貼り付け（Qt 版の TimeRemapPasteDialog と TimeRemapController の
// show_paste_dialog・paste_on_timeline）。
//
// 読み取り・列・既定の紐づけ・使う行の絞り込み・当てる前の確かめは Rust（time_remap.rs、
// 計算は pma-core の export::time_remap_paste）。この窓は文字を受け取り、表を出し、
// 使う行と紐づけを集めて Rust に渡すだけ。

import { el, openDialog, message, toast } from "./dialogs.js";
import { tr } from "./i18n.js";
import { openMenu } from "./tools.js";

const { invoke } = window.__TAURI__.core;

/// 翻訳前の文と差し込み（Rust の TextView）を訳す。
function text(view) {
  if (!view) return "";
  if (typeof view === "string") return tr(view);
  return tr(view.template, Object.fromEntries(view.values ?? []));
}

/// 確かめの文（Qt は部分ごとに tr を通してつなぐ。XDTS の紐づけの行は {links} へ）。
function planText(plan) {
  const links = plan.links.map(text).join("\n");
  return plan.message
    .map((part, index) => {
      const values = Object.fromEntries(part.values ?? []);
      if (index === 0 && plan.links.length) values.links = links;
      return tr(part.template, values);
    })
    .join("");
}

const cellName = () => tr("セル");
const GROUP_COLORS = { ACTION: "#e5edf7", CELL: "#f7dddd", CAM: "#dff3df" };

/// 貼り付けの窓。反映したら true。
export async function showPasteDialog(initialText = null) {
  let clipboard = initialText;
  if (clipboard === null) {
    try {
      clipboard = await invoke("time_remap_clipboard");
    } catch {
      clipboard = "";
    }
  }
  let preview = null; // Rust の Preview
  let parsedText = null;
  let included = [];
  let bindings = []; // [[uid, layer]]
  let selected = new Set();
  let anchor = null;

  const area = el("textarea", {
    class: "tr-text",
    spellcheck: "false",
    wrap: "off",
    placeholder: tr("After Effects／ToeiDigitalTimeSheetのコピー情報を貼り付けるか、XDTSファイルを読み込みます。XDTSはこの画面へドロップできます。"),
  });
  area.value = clipboard ?? "";
  const status = el("span", { class: "tr-status" }, tr("データ待機中"));
  const table = el("table", { class: "tr-table" });
  const tableBox = el("div", { class: "tr-table-box" }, table);

  const layerName = (index) => preview?.layers.find(([i]) => i === index)?.[1];
  const bound = (uid) => bindings.find(([key]) => key === uid);

  function setStatus(textValue, kind) {
    status.textContent = textValue;
    status.dataset.kind = kind ?? "";
  }

  function updateStatus() {
    if (!preview || !preview.columns.length) return;
    const duration = Math.max(0, ...preview.columns.map((c) => c.display_values.length), included.length);
    const rows = included.length === duration ? included : new Array(duration).fill(true);
    const used = rows.filter(Boolean).length;
    const linked = preview.columns.filter((c) => bound(c.uid)).length;
    setStatus(tr("{name}／使用 {count}／除外 {max}／レイヤー紐づけ {count2}", {
      name: preview.format, count: used, max: Math.max(0, duration - used), count2: linked,
    }), "ok");
  }

  function headerLabel(column) {
    const lines = [column.group, column.name];
    if (column.bindable) {
      const link = bound(column.uid);
      const linked = link ? layerName(link[1]) : null;
      lines.push(linked ? `→ ${linked}` : tr("クリックで紐づけ"));
    }
    return lines;
  }

  function render() {
    table.replaceChildren();
    if (!preview) return;
    const columns = preview.columns;
    const duration = Math.max(0, ...columns.map((c) => c.display_values.length));
    const head = el("tr", {}, el("th", { class: "tr-use" }, tr("使用")), el("th", { class: "tr-frame" }, "F"));
    for (const column of columns) {
      const th = el("th", {
        class: `tr-col${column.bindable ? " bindable" : ""}`,
        title: column.bindable ? tr("クリックして読み込み先レイヤーを選択します。") : null,
        style: { background: GROUP_COLORS[column.group] ?? "#e7ecef" },
      }, headerLabel(column).map((line) => el("div", {}, line)));
      if (column.bindable) th.addEventListener("click", (e) => bindingMenu(column, e.clientX, e.clientY));
      head.append(th);
    }
    table.append(el("thead", {}, head));
    const body = el("tbody");
    for (let row = 0; row < duration; row++) {
      const check = el("input", { type: "checkbox" });
      check.checked = !!included[row];
      check.addEventListener("change", () => {
        included[row] = check.checked;
        tr_.classList.toggle("excluded", !check.checked);
        updateStatus();
      });
      const tr_ = el("tr", { class: `${included[row] ? "" : "excluded"}${selected.has(row) ? " selected" : ""}` },
        el("td", { class: "tr-use" }, check),
        el("td", { class: "tr-frame" }, String(preview.start_frame + row + 1)));
      for (const column of columns) {
        const value = column.display_values[row] ?? "";
        tr_.append(el("td", { class: ["×", "○", "●"].includes(value) ? "tr-mark" : "" }, value));
      }
      tr_.addEventListener("click", (e) => {
        if (e.target === check) return;
        if (e.shiftKey && anchor !== null) {
          const [a, b] = [Math.min(anchor, row), Math.max(anchor, row)];
          if (!(e.metaKey || e.ctrlKey)) selected.clear();
          for (let i = a; i <= b; i++) selected.add(i);
        } else if (e.metaKey || e.ctrlKey) {
          if (selected.has(row)) selected.delete(row);
          else selected.add(row);
          anchor = row;
        } else {
          selected = new Set([row]);
          anchor = row;
        }
        for (const [i, node] of [...body.children].entries()) node.classList.toggle("selected", selected.has(i));
      });
      body.append(tr_);
    }
    table.append(body);
    updateStatus();
  }

  function bindingMenu(column, x, y) {
    const current = bound(column.uid)?.[1];
    const set = async (layer) => {
      bindings = await invoke("time_remap_set_binding", { bindings, uid: column.uid, layer });
      render();
    };
    const items = [
      { label: tr("「{get}」の紐づけ先", { get: column.name }) },
      "-",
      { label: tr("紐づけを解除（読み込まない）"), checked: current === undefined, action: () => set(null) },
    ];
    if (preview.layers.length) items.push("-");
    for (const [index, name] of preview.layers) {
      items.push({ label: name, checked: current === index, action: () => set(index) });
    }
    if (!preview.layers.length) items.push({ label: tr("紐づけ可能なレイヤーがありません") });
    openMenu(x, y, items);
  }

  function clearAll() {
    preview = null;
    parsedText = null;
    included = [];
    bindings = [];
    selected.clear();
    table.replaceChildren();
    setStatus(tr("データ待機中"), "");
  }

  async function update() {
    const raw = area.value;
    if (!raw.trim()) {
      clearAll();
      return;
    }
    if (preview && raw === parsedText) return;
    try {
      preview = await invoke("time_remap_preview", { text: raw, cellName: cellName() });
    } catch (e) {
      clearAll();
      setStatus(text(e).split("\n")[0], "error");
      return;
    }
    parsedText = raw;
    const duration = Math.max(0, ...preview.columns.map((c) => c.display_values.length));
    included = new Array(duration).fill(true);
    bindings = preview.bindings;
    selected.clear();
    render();
  }

  let timer = null;
  area.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(update, 140);
  });

  async function loadFile() {
    try {
      const loaded = await invoke("time_remap_pick_file", {
        title: tr("タイムシートを読み込む（XDTS／TDTS）"),
        filter: tr("タイムシート (*.xdts *.xtds *.tdts);;すべてのファイル (*)").split(";;")[0].replace(/\s*\(.*\)$/, ""),
      });
      if (loaded === null || loaded === undefined) return;
      area.value = loaded;
      area.focus();
      await update();
    } catch (e) {
      await message(tr("XDTS読み込み"), text(e));
    }
  }

  const setRows = (value) => {
    let rows = [...selected];
    if (!rows.length && anchor !== null) rows = [anchor];
    for (const row of rows) included[row] = value;
    render();
  };

  const button = (label, onclick) => el("button", { type: "button", onclick }, label);
  const body = el("div", { class: "tr-body" },
    el("div", { class: "tr-row" },
      el("b", {}, tr("読み込みデータ")),
      el("span", { class: "tr-stretch" }),
      button(tr("XDTS／TDTSを読み込む…"), loadFile),
      button(tr("クリップボードをタイムシートへ貼付"), async () => {
        area.value = await invoke("time_remap_clipboard").catch(() => "");
        area.focus();
        await update();
      }),
      button(tr("クリア"), () => {
        area.value = "";
        clearAll();
      })),
    area,
    el("div", { class: "tr-row" },
      el("b", {}, tr("タイムシートプレビュー")),
      el("span", { class: "tr-stretch" }),
      status),
    tableBox,
    el("div", { class: "tr-row" },
      button(tr("選択行を除外"), () => setRows(false)),
      button(tr("選択行を使用"), () => setRows(true)),
      button(tr("全て使用"), () => {
        included = included.map(() => true);
        render();
      })),
    el("div", { class: "tr-note" }, tr("ACTION・CELL・CAMをXDTSの列ごとに表示します。CELL名をクリックするとPaintMaskAnimatorのレイヤーへ紐づけできます。「使用」を外した行は取り除き、後続を詰めて反映します。")),
  );

  // XDTS のファイルを窓へ落とす（Qt はダイアログ全体がドロップを受ける）。
  body.addEventListener("dragover", (e) => e.preventDefault());
  body.addEventListener("drop", async (e) => {
    const file = [...(e.dataTransfer?.files ?? [])].find((f) => /\.(xdts|xtds|tdts)$/i.test(f.name));
    if (!file) return;
    e.preventDefault();
    area.value = (await file.text()).replace(/^﻿/, "");
    await update();
  });

  setTimeout(update, 0);
  const accepted = await openDialog({
    title: tr("タイムリマップをタイムシートへ貼り付け"),
    body,
    className: "tr-dialog",
    initialFocus: () => area,
    buttons: [
      {
        label: tr("このタイムシートをタイムラインへ反映"),
        value: true,
        primary: true,
        // Qt の _accept_if_valid。
        validate: async () => {
          await update();
          const ok = preview && included.some(Boolean) && preview.columns.length;
          if (!ok) {
            await message(tr("タイムリマップ"), tr("有効なタイムシートを貼り付け、使用する行を残してください。"));
            return false;
          }
          if (preview.format.startsWith("XDTS") && !bindings.length) {
            await message(tr("XDTS読み込み"), tr("読み込むCELL名をクリックし、紐づけ先レイヤーを選択してください。"));
            return false;
          }
          return true;
        },
      },
      { label: tr("キャンセル"), value: false, escape: true },
    ],
  });
  if (!accepted) return false;
  const args = { text: parsedText, cellName: cellName(), included, bindings };
  try {
    const plan = await invoke("time_remap_plan", args);
    if (!plan) throw { template: "使用するタイムシート行がありません。", values: [] };
    const yes = await message(text(plan.title), planText(plan), [[tr("はい"), true, true], [tr("いいえ"), false]], false);
    if (!yes) return false;
    const done = await invoke("time_remap_apply", args);
    if (done) toast(text(done));
    return !!done;
  } catch (e) {
    await message(tr("タイムリマップ貼り付け"), tr("タイムラインへ反映できませんでした。\n\n{exc}", { exc: text(e) }));
    return false;
  }
}

/// タイムラインでの貼り付け（Qt の paste_on_timeline）。タイムシートとして扱ったら true。
export async function pasteOnTimeline() {
  let outcome;
  try {
    outcome = await invoke("time_remap_paste_on_timeline", { cellName: cellName() });
  } catch {
    return false;
  }
  switch (outcome.kind) {
    case "not_timesheet":
      return false;
    case "dialog":
      await showPasteDialog();
      return true;
    case "applied":
      if (outcome.status) toast(text(outcome.status));
      return true;
    default:
      await message(tr("タイムリマップ貼り付け"), tr("タイムラインへ反映できませんでした。\n\n{exc}", { exc: text(outcome.reason) }));
      return true;
  }
}

/// メニュー・ショートカットの「タイムリマップを貼り付け」を引き受ける。
export function installTimeRemap() {
  let busy = false;
  window.addEventListener("pma-action", (e) => {
    if (e.detail?.id !== "a_tl_paste_time_remap") return;
    e.preventDefault();
    if (busy) return;
    busy = true;
    showPasteDialog().finally(() => {
      busy = false;
    });
  });
}
