// 色のパネル（カラーサークル・カラースライダー・使用色・カラーチャート）が共有する状態と部品。
//
// 状態は Rust（src/colors.rs）の Session にあり、ここは最後に受け取った形を持つだけ。
// - `palette-changed`：使用色パネルの全体（PaletteView）
// - `chart-changed`：カラーチャート（ChartView）
// 描画色（メイン・サブ・背景・描く色の選び）の正本はツールの設定（tools.rs。toolStore）。
// ここの `drawing` はそこから作る形で、変えるときも toolStore.set を通す（ツールのパネルの
// 色の欄・E/C/X のキー・スポイトと同じ所を読み書きするので、どこから変えてもそろう）。
//
// ほかに、パネルから開くメニュー・入力の窓・カラーピッカー・「色相・彩度・明度」の窓と、
// スポイト（WKWebView には EyeDropper が無いので、キャンバスの色を拾う）を置く。
import { clamp, fromHsv, hex, parseHex, toHsv } from "./color_math.js";
import { toolStore } from "./tools.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

/// 最後に受け取った形。
export const state = {
  palette: null,
  drawing: null,
  chart: null,
};

const handlers = { palette: new Set(), drawing: new Set(), chart: new Set() };

/// 変わったら呼ぶ関数を登録する。kind は palette / drawing / chart。
export function subscribe(kind, fn) {
  handlers[kind].add(fn);
  if (state[kind]) fn(state[kind]);
  return () => handlers[kind].delete(fn);
}

function publish(kind, value) {
  state[kind] = value;
  for (const fn of handlers[kind]) {
    try {
      fn(value);
    } catch (e) {
      console.error(e);
    }
  }
}

/// 色のパネルが受け持つメニュー・ショートカットの操作（actions.rs の id）。
const ACTIONS = {
  // 使用色：この色を選択範囲にする（フォーカス・選んだ色・描画色の順）。
  a_focus_used_color: () => paletteOp({ op: "select_pixels" }),
  // 選択色だけ表示／特定色表示を解除（選択レイヤーの色フィルタ）。
  a_isolate_color: () => paletteOp({ op: "isolate_layer_color" }),
  a_clear_color_filter: () => paletteOp({ op: "clear_layer_color_filter" }),
  a_color_chart: () => import("./color_chart.js").then((m) => m.openColorChart()),
};

let started = null;
/// 事件を受け始め、今の形を読む（何度呼んでも1回だけ）。
export function startColors() {
  started ??= (async () => {
    await listen("palette-changed", ({ payload }) => publish("palette", payload));
    await listen("chart-changed", ({ payload }) => publish("chart", payload));
    // 塗りで実際に書いた色（paint_ops.rs。Qt の colorUsed → add_color）：使用色をすぐ数え直して
    // 新しい色の行を出す（palette_view が数え直し、変われば palette-changed も出す）。
    await listen("paint-colors-used", async () => publish("palette", await invoke("palette_view")));
    // 描画色はツールの設定から（tools-changed）。同じ色なら知らせない（ツールの他の欄の変化）。
    let last = "";
    toolStore.subscribe((view) => {
      const drawing = drawingFromSettings(view?.settings);
      const key = JSON.stringify(drawing);
      if (drawing && key !== last) {
        last = key;
        publish("drawing", drawing);
      }
    });
    publish("palette", await invoke("palette_view"));
    publish("chart", await invoke("chart_view"));
    // 使用色フォルダーを尋ねられたら（影の書き出しなど）、detail.folders に
    // [{name, colors: [[r, g, b], …]}] を入れて答える（同期の事件なので、最後に受け取った形から）。
    window.addEventListener("pma-used-color-folders", (e) => {
      if (!e.detail) return;
      e.detail.folders = (state.palette?.folders ?? []).map((f) => ({ name: f.name, colors: f.colors.map((c) => [...c]) }));
    });
    // メニュー・ショートカットの色の操作（shell.js の pma-action を引き受ける）。
    window.addEventListener("pma-action", (e) => {
      const run = ACTIONS[e.detail?.id];
      if (!run) return;
      e.preventDefault();
      run();
    });
  })();
  return started;
}

// ---- 知らせ（下の帯） ----------------------------------------------------

let statusTimer = 0;
/// ステータスバーへ出す（Qt の statusBar().showMessage）。
export function showMessage(text, { error = false, ms = 4000 } = {}) {
  const line = document.getElementById("status");
  if (!line || !text) return;
  line.textContent = text;
  line.classList.toggle("error", error);
  clearTimeout(statusTimer);
  statusTimer = setTimeout(() => line.classList.remove("error"), ms);
}

/// 操作の結果（Outcome）を出す。失敗は窓で知らせる（Qt の QMessageBox）。
async function run(cmd, args, title) {
  try {
    const out = await invoke(cmd, args);
    if (out?.message) showMessage(out.message);
    return out;
  } catch (e) {
    await alertBox(title, String(e));
    return null;
  }
}

export const paletteOp = (op) => run("palette_op", { op }, "使用色");
export const chartOp = (op) => run("chart_op", { op }, "カラーチャート");
export const chartSavePmag = () => run("chart_save_pmag", {}, "PMAG保存");
export const chartLoadPmag = () => run("chart_load_pmag", {}, "PMAG読込");
export const replacementSave = () => run("replacement_set_save", {}, "置換色を保存");
export const replacementLoad = () => run("replacement_set_load", {}, "置換色を読み込む");

/// ツールの設定から描画色の形を作る。{main, sub, background}（#RRGGBB）、mode（main / sub /
/// transparent）、current（いま描く色。背景色のモードは消しゴム）。
function drawingFromSettings(s) {
  if (!s) return null;
  const mode = ["main", "sub", "transparent"].includes(s.color_mode) ? s.color_mode : "main";
  const color = mode === "sub" ? s.color_sub : mode === "main" ? s.color_main : s.background_color;
  return {
    main: hex(s.color_main),
    sub: hex(s.color_sub),
    background: hex(s.background_color),
    mode,
    current: { color, eraser: mode === "transparent", hex: hex(color) },
  };
}

/// 描画色を変える（ツールの設定を変える。tools-changed で各パネルへ届く）。
/// op は set_active {color}・set {which, color}・mode {mode}・swap・reset・background {color}。
export async function drawingOp(op) {
  const s = toolStore.settings;
  if (!s) return state.drawing;
  let patch = null;
  switch (op.op) {
    case "set_active": {
      // 背景色で描いているときはメインへ戻す（Qt の wheel_color_changed）。
      const mode = s.color_mode === "sub" ? "sub" : "main";
      patch = { [mode === "sub" ? "color_sub" : "color_main"]: op.color, color_mode: mode };
      break;
    }
    case "set":
      patch = op.which === "transparent" ? { background_color: op.color }
        : { [op.which === "sub" ? "color_sub" : "color_main"]: op.color };
      break;
    case "mode":
      patch = { color_mode: op.mode };
      break;
    case "swap":
      patch = { color_main: s.color_sub, color_sub: s.color_main };
      break;
    case "reset":
      patch = { color_main: [0, 0, 0], color_sub: [255, 255, 255] };
      break;
    case "background":
      patch = { background_color: op.color };
      break;
  }
  if (patch) await toolStore.set(patch);
  return state.drawing;
}

/// いま編集している色（メイン／サブ。背景色で描いているときはメイン）。
export function activeColor(drawing = state.drawing) {
  if (!drawing) return [0, 0, 0];
  return parseHex(drawing.mode === "sub" ? drawing.sub : drawing.main);
}

// ---- 部品 ----------------------------------------------------------------

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

let openMenu = null;
function closeMenu() {
  openMenu?.remove();
  openMenu = null;
}
window.addEventListener("pointerdown", (e) => {
  if (openMenu && !openMenu.contains(e.target)) closeMenu();
}, true);
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeMenu();
});

/// 右クリックのメニュー。items は {label, action, disabled, items（下の階層）, title} か "-"。
export function showMenu(items, x, y, doc = document) {
  closeMenu();
  const build = (list, left, top) => {
    const menu = el("div", { class: "tl-menu" });
    for (const item of list) {
      if (item === "-") {
        menu.append(el("div", { class: "tl-menu-sep" }));
        continue;
      }
      const row = el("div", {
        class: `tl-menu-item${item.disabled ? " disabled" : ""}${item.items ? " sub" : ""}`,
        title: item.title,
      }, item.label);
      if (item.items) {
        let child = null;
        row.addEventListener("pointerenter", () => {
          menu.querySelectorAll(".tl-submenu").forEach((m) => m.remove());
          const r = row.getBoundingClientRect();
          child = build(item.items, r.right - 2, r.top - 4);
          child.classList.add("tl-submenu");
          menu.append(child);
        });
      } else if (!item.disabled) {
        row.addEventListener("click", () => {
          closeMenu();
          item.action?.();
        });
      }
      menu.append(row);
    }
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    return menu;
  };
  openMenu = build(items, x, y);
  doc.body.append(openMenu);
  // 窓からはみ出さないように寄せる。
  const r = openMenu.getBoundingClientRect();
  const view = doc.defaultView;
  if (r.right > view.innerWidth) openMenu.style.left = `${Math.max(0, view.innerWidth - r.width - 4)}px`;
  if (r.bottom > view.innerHeight) openMenu.style.top = `${Math.max(0, view.innerHeight - r.height - 4)}px`;
}

/// 窓（モーダル）。build(body, close) で中身を作る。close(value) で閉じて value を返す。
export function dialog(title, build, { modal = true, className = "" } = {}) {
  return new Promise((resolve) => {
    const overlay = el("div", { class: `tl-dialog-overlay${modal ? "" : " modeless"}` });
    const box = el("div", { class: `tl-dialog ${className}` }, el("div", { class: "tl-dialog-title" }, title));
    overlay.append(box);
    let done = false;
    const close = (value) => {
      if (done) return;
      done = true;
      overlay.remove();
      window.removeEventListener("keydown", onKey, true);
      resolve(value);
    };
    const onKey = (e) => {
      if (e.key === "Escape" && !eyedrop.waiting()) {
        e.preventDefault();
        e.stopPropagation();
        box.dispatchEvent(new CustomEvent("dialog-cancel"));
      }
    };
    window.addEventListener("keydown", onKey, true);
    build(box, close);
    document.body.append(overlay);
    box.querySelector("input")?.focus();
  });
}

function buttons(...list) {
  return el("div", { class: "tl-dialog-buttons" }, ...list);
}

/// 知らせの窓。
export function alertBox(title, text) {
  return dialog(title, (box, close) => {
    box.append(el("div", { class: "cp-message" }, text));
    const ok = el("button", { class: "primary", onclick: () => close() }, "OK");
    box.append(buttons(ok));
    box.addEventListener("dialog-cancel", () => close());
    setTimeout(() => ok.focus());
  });
}

/// 1行の入力の窓（Qt の QInputDialog.getText）。キャンセルなら null。
export function promptText(title, label, value = "") {
  return dialog(title, (box, close) => {
    const input = el("input", { type: "text", value, class: "cp-text" });
    const ok = () => close(input.value);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") ok();
    });
    box.append(el("label", { class: "cp-field" }, label, input));
    box.append(buttons(
      el("button", { onclick: () => close(null) }, "キャンセル"),
      el("button", { class: "primary", onclick: ok }, "OK"),
    ));
    box.addEventListener("dialog-cancel", () => close(null));
    setTimeout(() => input.select());
  });
}

/// 選択肢の窓（Qt の QInputDialog.getItem）。キャンセルなら null。
export function chooseItem(title, label, choices, current = 0) {
  return dialog(title, (box, close) => {
    const select = el("select", { class: "cp-text" }, choices.map((c, i) => el("option", { value: i, selected: i === current }, c)));
    box.append(el("label", { class: "cp-field" }, label, select));
    box.append(buttons(
      el("button", { onclick: () => close(null) }, "キャンセル"),
      el("button", { class: "primary", onclick: () => close(choices[Number(select.value)]) }, "OK"),
    ));
    box.addEventListener("dialog-cancel", () => close(null));
  });
}

// ---- スポイト ------------------------------------------------------------
//
// Qt 版は画面のどこからでも色を拾えた（ScreenColorPickerOverlay）。WKWebView には EyeDropper が
// 無いので、キャンバスの上でクリックした色を拾う。待っているあいだにキャンバスを押すと、
// app.js が deliver(色) を呼ぶ。Esc でやめる。

let pickWaiter = null;
export const eyedrop = {
  waiting: () => pickWaiter !== null,
  /// 色を待つ。拾えたら [r,g,b]、やめたら null。
  begin(message = "キャンバスの色をクリックしてください（Esc でやめる）") {
    pickWaiter?.(null);
    showMessage(message, { ms: 60000 });
    document.body.classList.add("eyedropping");
    return new Promise((resolve) => {
      pickWaiter = (rgb) => {
        pickWaiter = null;
        document.body.classList.remove("eyedropping");
        resolve(rgb);
      };
    });
  },
  deliver(rgb) {
    pickWaiter?.(rgb);
  },
  cancel() {
    if (pickWaiter) {
      pickWaiter(null);
      showMessage("スポイトをやめました。");
    }
  },
};
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && eyedrop.waiting()) {
    e.preventDefault();
    e.stopImmediatePropagation();
    eyedrop.cancel();
  }
}, true);

// ---- カラーピッカー（Qt の color_picker_dialog.py） ------------------------

const FIELD = 256;

/// 色を選ぶ窓。キャンセルなら null。preview(rgb) を渡すと「プレビュー」を出し、
/// 編集のたびに呼ぶ（キャンセルで元の色を、プレビューを外して OK なら決めた色を1回呼ぶ）。
/// extra は {label, title, action}：左下に足すボタン（押したら action を呼んで null で閉じる）。
export function pickColor(initial, { title = "色を選択", preview = null, extra = null } = {}) {
  const original = [...(initial ?? [255, 255, 255])];
  return dialog(title, (box, close) => {
    box.classList.add("cp-dialog");
    // 色相は浮動小数で持つ（彩度・明度を 0 にしても色相を失わない）。
    const hsv0 = toHsv(original);
    let h = hsv0.h < 0 ? 0 : hsv0.h;
    let s = hsv0.s / 255;
    let v = hsv0.v / 255;
    // RGB で決まった色（数値・コード・スポイト）はそのまま返す。
    let exact = [...original];
    let previewOn = preview !== null;
    let previewed = false;

    const color = () => exact ?? fromHsv(Math.round(h) % 360, Math.round(s * 255), Math.round(v * 255));

    const field = el("canvas", { width: FIELD, height: FIELD, class: "cp-field-canvas" });
    const strip = el("canvas", { width: 20, height: FIELD, class: "cp-strip" });
    const swatchNew = el("div", { class: "cp-swatch", title: "新しい色" });
    const swatchOld = el("div", { class: "cp-swatch old", title: "元の色（クリックで戻す）" });
    swatchOld.style.background = hex(original);
    const spin = (max) => el("input", { type: "number", min: 0, max, step: 1, class: "cp-num" });
    const [hIn, sIn, bIn, rIn, gIn, blIn] = [360, 100, 100, 255, 255, 255].map(spin);
    const hexIn = el("input", { type: "text", maxlength: 7, class: "cp-hex", title: "カラーコードを入力して Enter" });
    const eye = el("button", { title: "キャンバスの色を拾う（Esc でやめる）" }, "スポイト");
    const previewBox = el("input", { type: "checkbox", checked: previewOn });

    const drawField = () => {
      const ctx = field.getContext("2d");
      const img = ctx.createImageData(FIELD, FIELD);
      const hue = Math.round(h) % 360;
      for (let y = 0; y < FIELD; y++) {
        for (let x = 0; x < FIELD; x++) {
          const c = fromHsv(hue, Math.round((x / (FIELD - 1)) * 255), Math.round((1 - y / (FIELD - 1)) * 255));
          const i = (y * FIELD + x) * 4;
          img.data[i] = c[0];
          img.data[i + 1] = c[1];
          img.data[i + 2] = c[2];
          img.data[i + 3] = 255;
        }
      }
      ctx.putImageData(img, 0, 0);
      const px = s * (FIELD - 1);
      const py = (1 - v) * (FIELD - 1);
      ctx.strokeStyle = v > 0.6 && s < 0.4 ? "#000" : "#fff";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(px, py, 6, 0, Math.PI * 2);
      ctx.stroke();
    };
    const drawStrip = () => {
      const ctx = strip.getContext("2d");
      for (let y = 0; y < FIELD; y++) {
        ctx.fillStyle = hex(fromHsv(Math.round((1 - y / FIELD) * 359), 255, 255));
        ctx.fillRect(0, y, 20, 1);
      }
      const y = (1 - h / 360) * FIELD;
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 2;
      ctx.strokeRect(1, y - 2, 18, 4);
    };
    let updating = false;
    const sync = (fromUser = true) => {
      updating = true;
      const c = color();
      swatchNew.style.background = hex(c);
      hIn.value = Math.round(h) % 360;
      sIn.value = Math.round(s * 100);
      bIn.value = Math.round(v * 100);
      [rIn.value, gIn.value, blIn.value] = c;
      if (document.activeElement !== hexIn) hexIn.value = hex(c);
      drawField();
      drawStrip();
      updating = false;
      if (fromUser && preview && previewOn) {
        preview(c);
        previewed = true;
      }
    };
    const setRgb = (c) => {
      exact = c.map((x) => clamp(Math.round(x), 0, 255));
      const t = toHsv(exact);
      if (t.h >= 0) h = t.h;
      s = t.s / 255;
      v = t.v / 255;
      sync();
    };
    const drag = (target, onPoint) => {
      target.addEventListener("pointerdown", (e) => {
        target.setPointerCapture(e.pointerId);
        onPoint(e);
        const move = (ev) => onPoint(ev);
        const up = () => {
          target.removeEventListener("pointermove", move);
          target.removeEventListener("pointerup", up);
        };
        target.addEventListener("pointermove", move);
        target.addEventListener("pointerup", up);
      });
    };
    drag(field, (e) => {
      const r = field.getBoundingClientRect();
      s = clamp((e.clientX - r.left) / r.width, 0, 1);
      v = clamp(1 - (e.clientY - r.top) / r.height, 0, 1);
      exact = null;
      sync();
    });
    drag(strip, (e) => {
      const r = strip.getBoundingClientRect();
      h = clamp((1 - (e.clientY - r.top) / r.height) * 360, 0, 359.99);
      exact = null;
      sync();
    });
    for (const input of [hIn, sIn, bIn]) {
      input.addEventListener("input", () => {
        if (updating) return;
        h = clamp(Number(hIn.value) || 0, 0, 360) % 360;
        s = clamp(Number(sIn.value) || 0, 0, 100) / 100;
        v = clamp(Number(bIn.value) || 0, 0, 100) / 100;
        exact = null;
        sync();
      });
    }
    for (const input of [rIn, gIn, blIn]) {
      input.addEventListener("input", () => {
        if (!updating) setRgb([rIn, gIn, blIn].map((i) => clamp(Number(i.value) || 0, 0, 255)));
      });
    }
    const hexDone = () => {
      const c = parseHex(hexIn.value);
      if (c) setRgb(c);
      else hexIn.value = hex(color());
    };
    hexIn.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        hexDone();
      }
    });
    hexIn.addEventListener("change", hexDone);
    swatchOld.addEventListener("click", () => setRgb(original));
    eye.addEventListener("click", async () => {
      box.closest(".tl-dialog-overlay").classList.add("picking");
      const c = await eyedrop.begin();
      box.closest(".tl-dialog-overlay")?.classList.remove("picking");
      if (c) setRgb(c);
    });
    previewBox.addEventListener("change", () => {
      previewOn = previewBox.checked;
      if (previewOn) {
        preview?.(color());
        previewed = true;
      }
    });

    const grid = el("div", { class: "cp-numbers" },
      "H", hIn, "R", rIn,
      "S", sIn, "G", gIn,
      "B", bIn, "B", blIn,
      "#", hexIn,
    );
    box.append(el("div", { class: "cp-body" },
      field, strip,
      el("div", { class: "cp-side" },
        el("div", { class: "cp-top" }, el("div", { class: "cp-swatches" }, swatchNew, swatchOld), eye),
        grid,
      ),
    ));
    const cancel = () => {
      eyedrop.cancel();
      if (preview && previewed) preview(original);
      close(null);
    };
    const ok = () => {
      eyedrop.cancel();
      const c = color();
      if (preview && !previewOn) preview(c);
      close(c);
    };
    box.append(el("div", { class: "cp-bottom" },
      preview ? el("label", { class: "cp-preview", title: "ON：選んでいる色をその場で反映します（キャンセルで元に戻ります）。" }, previewBox, "プレビュー") : el("span"),
      buttons(
        extra ? el("button", { title: extra.title, onclick: () => { eyedrop.cancel(); extra.action(); close(null); } }, extra.label) : null,
        el("button", { onclick: cancel }, "キャンセル"),
        el("button", { class: "primary", onclick: ok }, "OK"),
      ),
    ));
    box.addEventListener("dialog-cancel", cancel);
    sync(false);
  });
}

// ---- 色相・彩度・明度（Qt の hsv_adjust_dialog.py） ------------------------

/// 選んだ色の色相・彩度・明度をずらす窓。OK で全コマへ当てる。
export function adjustHsv(rgbs) {
  return dialog("色相・彩度・明度", (box, close) => {
    box.classList.add("hsv-dialog");
    box.append(el("div", { class: "tl-dialog-note" },
      `選択中の${rgbs.length}色をまとめて調整します。OKで、選択レイヤーのすべてのコマへ適用します。`));
    const values = [0, 0, 0];
    let timer = 0;
    const sendPreview = () => {
      clearTimeout(timer);
      timer = setTimeout(() => paletteOp({ op: "hsv_preview", rgbs, shift: [...values] }), 16);
    };
    const row = (label, i, [lo, hi], suffix) => {
      const readout = el("span", { class: "hsv-readout" }, `0${suffix}`);
      const slider = el("input", { type: "range", min: lo, max: hi, value: 0, step: 1 });
      slider.addEventListener("input", () => {
        values[i] = Number(slider.value);
        readout.textContent = `${values[i] > 0 ? "+" : values[i] < 0 ? "" : "+"}${values[i]}${suffix}`;
        sendPreview();
      });
      box.append(el("div", { class: "hsv-row" }, el("span", { class: "hsv-caption" }, label), slider, readout));
    };
    row("色相", 0, [-180, 180], "°");
    row("彩度", 1, [-100, 100], "%");
    row("明度", 2, [-100, 100], "%");
    const end = async (apply) => {
      clearTimeout(timer);
      await paletteOp({ op: "hsv_preview", rgbs: [], shift: null });
      if (apply && values.some((x) => x !== 0)) await paletteOp({ op: "hsv_apply", rgbs, shift: [...values] });
      close(apply);
    };
    box.append(buttons(
      el("button", { onclick: () => end(false) }, "キャンセル"),
      el("button", { class: "primary", onclick: () => end(true) }, "OK"),
    ));
    box.addEventListener("dialog-cancel", () => end(false));
  });
}

/// 色の行の見本（置換色が未設定なら赤い斜線）。
export function swatch(color, { unset = false, title } = {}) {
  const s = el("span", { class: `cu-swatch${unset ? " unset" : ""}`, title });
  if (color && !unset) s.style.background = color;
  return s;
}
