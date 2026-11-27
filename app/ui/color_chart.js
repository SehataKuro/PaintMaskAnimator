// カラーチャートの窓（Qt の color_chart.py の ColorChartPanel と main_window_color_chart.py）。
//
// 親子色とタグを画像から独立して持つ。中身は Rust（src/colors.rs の chart_*、pma-core の
// used_colors::chart）にあり、`chart-changed` で受け取って描く。
// - タイルの色はドラッグで動かす（別のタイルの親・子の位置へ。何も無い所へ落とすと新しいタイル）
// - タグは右の一覧からタイルの色へドラッグして付ける。一覧の中のドラッグで順を変える
// - 色のダブルクリックでタグを選ぶ、グループ名のダブルクリックで名前を変える
// - 色のクリックで、その色を描画色にする（Qt の colorPicked → apply_sampled_color）
//
// 窓は使用色パネルの「▦」かメニューから開く。開いたまま他の操作もできる（モーダルにしない）。
import { parseHex, textColor } from "./color_math.js";
import {
  alertBox,
  chartLoadPmag,
  chartOp,
  chartSavePmag,
  chooseItem,
  drawingOp,
  el,
  paletteOp,
  pickColor,
  promptText,
  startColors,
  subscribe,
} from "./color_state.js";

let windowEl = null;

export function openColorChart() {
  startColors();
  if (windowEl) {
    windowEl.style.display = "";
    windowEl.querySelector(".cc-tiles")?.focus();
    return;
  }
  windowEl = createWindow();
  document.body.append(windowEl);
}

function createWindow() {
  const close = el("button", { class: "cc-close", title: "閉じる" }, "×");
  const titleBar = el("div", { class: "cc-title" }, el("span", {}, "カラーチャート"), close);
  const note = el("div", { class: "cc-note" },
    "親子色とタグを画像から独立して保持します。タイルはドラッグで移動、ダブルクリックでタグを変更できます。");
  const summary = el("div", { class: "cc-summary" }, "未登録");
  const tiles = el("div", { class: "cc-tiles", tabindex: 0 });
  const tagList = el("div", { class: "cc-tag-list", title: "ドラッグ：色タイルへ割り当て／一覧内で順序変更\nダブルクリック：名前とタグ色を編集" });
  const addTag = el("button", { title: "タグを追加" }, "＋");
  const removeTag = el("button", { title: "選んだタグを消す" }, "－");
  const tagBox = el("div", { class: "cc-tag-box" }, el("div", { class: "cc-tag-head" }, "タグ"), tagList, el("div", { class: "cc-tag-buttons" }, addTag, removeTag));
  const capture = el("button", {}, "現在の親子付けを登録");
  const apply = el("button", {}, "チャートを使用色へ適用");
  const save = el("button", {}, "PMAG保存");
  const load = el("button", {}, "PMAG読込");
  const win = el("div", { class: "cc-window" },
    titleBar,
    el("div", { class: "cc-content" },
      note, summary,
      el("div", { class: "cc-body" }, tiles, tagBox),
      capture, apply,
      el("div", { class: "cc-file-row" }, save, load),
    ),
  );

  close.addEventListener("click", () => (win.style.display = "none"));
  capture.addEventListener("click", () => chartOp({ op: "capture" }));
  apply.addEventListener("click", () => chartOp({ op: "apply" }));
  save.addEventListener("click", () => chartSavePmag());
  load.addEventListener("click", () => chartLoadPmag());

  // 窓を題名の帯で動かす。
  titleBar.addEventListener("pointerdown", (e) => {
    if (e.target === close) return;
    titleBar.setPointerCapture(e.pointerId);
    const r = win.getBoundingClientRect();
    const dx = e.clientX - r.left;
    const dy = e.clientY - r.top;
    const move = (ev) => {
      win.style.left = `${Math.max(0, ev.clientX - dx)}px`;
      win.style.top = `${Math.max(0, ev.clientY - dy)}px`;
    };
    const up = () => {
      titleBar.removeEventListener("pointermove", move);
      titleBar.removeEventListener("pointerup", up);
    };
    titleBar.addEventListener("pointermove", move);
    titleBar.addEventListener("pointerup", up);
  });

  let chart = null;
  let selectedTag = null;

  // ---- タグ ----
  const tagDialog = (title, name = "", color = "#7F8C8D") => new Promise((resolve) => {
    // 名前と色の窓（Qt の TagEditDialog）。色はカラーピッカーで選ぶ。
    const overlay = el("div", { class: "tl-dialog-overlay" });
    const box = el("div", { class: "tl-dialog" }, el("div", { class: "tl-dialog-title" }, title));
    const nameIn = el("input", { type: "text", value: name, class: "cp-text" });
    let current = color;
    const colorBtn = el("button", { class: "cc-tag-color" });
    const paint = () => {
      colorBtn.style.background = current;
      colorBtn.textContent = current;
      colorBtn.style.color = textColor(parseHex(current) ?? [0, 0, 0]);
    };
    paint();
    colorBtn.addEventListener("click", async () => {
      overlay.style.display = "none";
      const c = await pickColor(parseHex(current), { title: "タグ色" });
      overlay.style.display = "";
      if (c) {
        current = `#${c.map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase()}`;
        paint();
      }
    });
    const done = (v) => {
      overlay.remove();
      resolve(v);
    };
    box.append(
      el("label", { class: "cp-field" }, "タグ名", nameIn),
      el("label", { class: "cp-field" }, "タグ色", colorBtn),
      el("div", { class: "tl-dialog-buttons" },
        el("button", { onclick: () => done(null) }, "キャンセル"),
        el("button", { class: "primary", onclick: () => done({ name: nameIn.value.trim(), color: current }) }, "OK")),
    );
    overlay.append(box);
    document.body.append(overlay);
    nameIn.focus();
  });

  addTag.addEventListener("click", async () => {
    const names = new Set((chart?.tags ?? []).map(([n]) => n));
    let base = "Tag";
    let n = 1;
    while (names.has(`${base}${n}`)) n++;
    const r = await tagDialog("カラーチャートのタグ", `${base}${n}`);
    if (r?.name) await chartOp({ op: "add_tag", name: r.name, color: r.color });
  });
  removeTag.addEventListener("click", async () => {
    if (!selectedTag || selectedTag === "Base") return;
    await chartOp({ op: "remove_tag", name: selectedTag });
    selectedTag = null;
  });
  const editTag = async (name, color) => {
    const r = await tagDialog("カラーチャートのタグ", name, color);
    if (!r || !r.name) return;
    if (r.name !== name && (chart?.tags ?? []).some(([n]) => n === r.name)) {
      await alertBox("タグ編集", "同じ名前のタグが既にあります。");
      return;
    }
    await chartOp({ op: "edit_tag", old: name, new: r.name, color: r.color });
  };

  // ---- ドラッグ（色・タグ） ----
  let drag = null;
  const ghost = el("div", { class: "cu-ghost cc-ghost" });
  const hitAt = (x, y) => {
    const node = document.elementFromPoint(x, y);
    const color = node?.closest?.("[data-role]");
    if (color && tiles.contains(color)) {
      return { group_id: color.dataset.group, role: color.dataset.role, index: Number(color.dataset.index || 0) };
    }
    const tile = node?.closest?.(".cc-tile");
    if (tile && tiles.contains(tile)) return { group_id: tile.dataset.group, role: "child", index: 1e6 };
    if (node && (node === tiles || tiles.contains(node))) return null;
    const tag = node?.closest?.(".cc-tag");
    if (tag) return { tag: tag.dataset.tag };
    return undefined;
  };
  const startDrag = (e, payload, label, background) => {
    if (e.button !== 0) return;
    drag = { payload, x: e.clientX, y: e.clientY, moved: false, label, background, node: e.currentTarget };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onMove = (e) => {
    if (!drag) return;
    if (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 5) return;
    drag.moved = true;
    ghost.textContent = drag.label;
    ghost.style.background = drag.background;
    ghost.style.color = textColor(parseHex(drag.background) ?? [0, 0, 0]);
    ghost.style.left = `${e.clientX + 10}px`;
    ghost.style.top = `${e.clientY + 6}px`;
    if (!ghost.isConnected) document.body.append(ghost);
  };
  const onUp = async (e) => {
    if (!drag) return;
    const d = drag;
    drag = null;
    ghost.remove();
    if (!d.moved) {
      d.click?.();
      return;
    }
    const hit = hitAt(e.clientX, e.clientY);
    if (hit === undefined) return;
    if (d.payload.tag) {
      if (hit?.tag) {
        // 一覧の中で順を変える（Base は先頭のまま）。
        const order = (chart?.tags ?? []).map(([n]) => n).filter((n) => n !== "Base" && n !== d.payload.tag);
        const at = order.indexOf(hit.tag);
        order.splice(at < 0 ? order.length : at, 0, d.payload.tag);
        await chartOp({ op: "tag_order", order });
      } else if (hit && !hit.tag && hit.index !== 1e6) {
        await chartOp({ op: "assign_tag", hit, tag: d.payload.tag });
      }
      return;
    }
    if (hit?.tag) return;
    const same = hit && hit.group_id === d.payload.group_id && hit.role === d.payload.role && hit.index === d.payload.index;
    if (!same) await chartOp({ op: "move", source: d.payload, target: hit });
  };
  win.addEventListener("pointermove", onMove);
  win.addEventListener("pointerup", onUp);
  win.addEventListener("pointercancel", () => {
    drag = null;
    ghost.remove();
  });

  const pickChartColor = async (hexColor) => {
    const c = parseHex(hexColor);
    if (!c) return;
    await drawingOp({ op: "set_active", color: c });
    await paletteOp({ op: "focus_matching", rgb: c });
  };

  const chooseTag = async (hit, currentTag) => {
    const choices = ["（タグなし）", ...(chart?.tags ?? []).map(([n]) => n)];
    const at = Math.max(0, choices.indexOf(currentTag || "（タグなし）"));
    const picked = await chooseItem("色タイルのタグ", "タグ", choices, at);
    if (picked == null) return;
    await chartOp({ op: "assign_tag", hit, tag: picked === "（タグなし）" ? "" : picked });
  };

  const tagColor = (name) => (chart?.tags ?? []).find(([n]) => n === name)?.[1];

  const chip = (color, tag, data) => {
    const node = el("div", {
      class: `cc-color ${data.role}`,
      "data-group": data.group_id,
      "data-role": data.role,
      "data-index": data.index ?? 0,
      title: `${color}${tag ? `　タグ：${tag}` : ""}\nクリック：描画色にする／ドラッグ：移動／ダブルクリック：タグを変更`,
    });
    node.style.background = color;
    node.style.color = textColor(parseHex(color) ?? [0, 0, 0]);
    node.append(el("span", { class: "cc-code" }, color.replace("#", "")));
    if (tag) {
      const t = el("span", { class: "cc-chip-tag" }, tag);
      const c = tagColor(tag);
      if (c) t.style.borderColor = c;
      node.append(t);
    }
    node.addEventListener("pointerdown", (e) => {
      startDrag(e, { group_id: data.group_id, role: data.role, index: data.index ?? 0 }, color, color);
      if (drag) drag.click = () => pickChartColor(color);
    });
    node.addEventListener("dblclick", () => chooseTag({ group_id: data.group_id, role: data.role, index: data.index ?? 0 }, tag));
    return node;
  };

  const render = (view) => {
    chart = view;
    summary.textContent = view.summary;
    tiles.replaceChildren(...view.tiles.map((tile) => {
      const name = el("span", { class: "cc-tile-name", title: "ダブルクリックで名前を変更" }, tile.name || "（グループ名）");
      name.addEventListener("dblclick", async () => {
        const next = await promptText("グループ名", "グループ名：", tile.name);
        if (next != null) await chartOp({ op: "rename", group_id: tile.id, name: next.trim() });
      });
      const parent = tile.placeholder
        ? el("div", { class: "cc-color parent placeholder", "data-group": tile.id, "data-role": "parent", "data-index": 0 }, "未設定")
        : chip(tile.parent, tile.parent_tag, { group_id: tile.id, role: "parent" });
      const kids = el("div", { class: "cc-children" }, tile.children.map((c) => chip(c.color, c.tag, { group_id: tile.id, role: "child", index: c.index })));
      return el("div", { class: "cc-tile", "data-group": tile.id }, el("div", { class: "cc-tile-head" }, name), el("div", { class: "cc-tile-body" }, parent, kids));
    }));
    if (!view.tiles.length) tiles.append(el("div", { class: "cc-empty" }, "使用色を親子付けして「現在の親子付けを登録」を押すと、ここに並びます。"));
    tagList.replaceChildren(...view.tags.map(([name, color]) => {
      const node = el("div", { class: `cc-tag${name === selectedTag ? " selected" : ""}`, "data-tag": name },
        el("span", { class: "cc-tag-dot", style: `background:${color}` }), name);
      node.addEventListener("pointerdown", (e) => {
        startDrag(e, { tag: name }, name, color);
        if (drag) {
          drag.click = () => {
            selectedTag = name;
            tagList.querySelectorAll(".cc-tag").forEach((n) => n.classList.toggle("selected", n.dataset.tag === name));
          };
        }
      });
      node.addEventListener("dblclick", () => editTag(name, color));
      return node;
    }));
  };
  subscribe("chart", render);
  return win;
}
