// キャンバスの下のバー（Qt 版の main_window_ui_build.py の canvas bar と
// main_window_view_preview.py・preview_controls.py）。
//
// 拡大・回転のスライダー、100%・全体・回転を0°・左右／上下反転、今の絵の書き出し
// （クリップボード・フォルダー）、初期状態との比較・ブラックチェック・隣接色分け・
// チャンネル・露光量。見え方の計算は Rust（view.rs → pma-core の compose）が持ち、
// ここはボタンと状態をそろえるだけ。ズーム・回転・反転は viewer.js の変換だけで行う。
//
// メニューの項目（a_silhouette・a_preview_map・a_exposure_toggle・a_channel_*）は shell.js が
// `pma-action` の事件で出すので、ここで preventDefault して受ける。
import { iconSvg } from "./icons.js";
import { setTip } from "./tooltip.js";
import { message, toast } from "./dialogs.js";
import { showMenu } from "./color_state.js";
import { tr } from "./i18n.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const EXPOSURE_MIN = -10;
const EXPOSURE_MAX = 10;

/// チャンネル表示の選択肢（channel_labels）。
const CHANNELS = [
  ["rgb", "RGB"],
  ["red", "赤"],
  ["green", "緑"],
  ["blue", "青"],
  ["alpha", "アルファ"],
];

/// チャンネルのアイコン（preview_controls.channel_icon：加法混色の3つの丸）。
function channelIcon(channel, size = 20) {
  const scale = 2;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size * scale;
  canvas.style.width = canvas.style.height = `${size}px`;
  const g = canvas.getContext("2d");
  g.scale((scale * size) / 22, (scale * size) / 22);
  const circles = [
    ["#ff2020", 14.4, 14.0, "red"],
    ["#00e82c", 11.0, 8.0, "green"],
    ["#2450ff", 7.6, 14.0, "blue"],
  ];
  const path = (cx, cy) => {
    const p = new Path2D();
    p.arc(cx, cy, 6, 0, Math.PI * 2);
    return p;
  };
  if (channel === "rgb") {
    // 重なりは加法混色（黄・シアン・マゼンタ・白）。
    g.globalCompositeOperation = "lighter";
    for (const [color, cx, cy] of circles) {
      g.fillStyle = color;
      g.fill(path(cx, cy));
    }
  } else if (channel === "alpha") {
    g.strokeStyle = "#909090";
    g.lineWidth = 1;
    g.fillStyle = "#202020";
    g.beginPath();
    g.roundRect(2, 2, 18, 18, 2);
    g.fill();
    g.stroke();
    g.fillStyle = "#ffffff";
    g.fill(path(11, 11));
  } else {
    g.strokeStyle = "#909090";
    g.lineWidth = 1.2;
    for (const [, cx, cy, value] of circles) if (value !== channel) g.stroke(path(cx, cy));
    for (const [color, cx, cy, value] of circles) {
      if (value !== channel) continue;
      g.fillStyle = color;
      g.fill(path(cx, cy));
    }
  }
  return canvas;
}

function iconButton(icon, tip, { checkable = false } = {}) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "cv-icon";
  b.innerHTML = iconSvg(icon);
  b.tabIndex = -1; // Qt の NoFocus（キーはキャンバスへ）
  if (checkable) b.setAttribute("aria-pressed", "false");
  setTip(b, tip);
  return b;
}

function setChecked(button, on) {
  button.classList.toggle("checked", Boolean(on));
  button.setAttribute("aria-pressed", on ? "true" : "false");
}

/// 露光量の値（ExposureControl）：下線の付いた値を左右にドラッグで変え、クリックで入力する。
function exposureControl(onChange) {
  const box = document.createElement("span");
  box.className = "cv-exposure";
  const link = document.createElement("span");
  link.className = "cv-exposure-link";
  const input = document.createElement("input");
  input.type = "number";
  input.min = String(EXPOSURE_MIN);
  input.max = String(EXPOSURE_MAX);
  input.step = "0.1";
  input.className = "cv-exposure-input";
  input.style.display = "none";
  box.append(link, input);
  let value = 0;
  const clamp = (v) => Math.min(EXPOSURE_MAX, Math.max(EXPOSURE_MIN, Math.round(v * 10) / 10));
  const sign = (v) => `${v >= 0 ? "+" : "-"}${Math.abs(v).toFixed(1)}`;
  function set(v) {
    value = clamp(v);
    link.textContent = sign(value);
    if (document.activeElement !== input) input.value = value.toFixed(1);
  }
  function change(v) {
    const next = clamp(v);
    if (next === value) return;
    set(next);
    onChange(next);
  }
  let press = null;
  link.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    link.setPointerCapture(e.pointerId);
    press = { x: e.clientX, dragged: false };
    e.preventDefault();
  });
  link.addEventListener("pointermove", (e) => {
    if (!press) return;
    const dx = e.clientX - press.x;
    if (Math.abs(dx) >= 3) {
      press.dragged = true;
      press.x = e.clientX;
      change(value + dx / 20);
    }
  });
  link.addEventListener("pointerup", () => {
    if (!press) return;
    const dragged = press.dragged;
    press = null;
    if (!dragged) {
      // クリックして入力（begin_edit）。
      link.style.display = "none";
      input.style.display = "";
      input.value = value.toFixed(1);
      input.focus();
      input.select();
    }
  });
  const end = () => {
    input.style.display = "none";
    link.style.display = "";
    set(value);
  };
  input.addEventListener("input", () => {
    const v = Number.parseFloat(input.value);
    if (Number.isFinite(v)) change(v);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === "Escape") {
      e.preventDefault();
      input.blur();
    }
    e.stopPropagation();
  });
  input.addEventListener("blur", end);
  set(0);
  return { element: box, set };
}

/// 下のバーを作る。shell は後から渡す（メニューのチェックを合わせる）。
/// busy() が真のあいだ（線を描いている途中）は、縮小表示「線を残す」の画像を作らない。
export function createCanvasView({ viewer, busy }) {
  const bar = document.createElement("div");
  bar.className = "cv-bar";
  let shell = null;
  let info = null;

  // ---- 拡大・回転 ----
  const zoomTitle = el("span", "cv-title", tr("拡大"));
  const zoom = slider(5, 1600, 100);
  const zoomLabel = el("span", "cv-value", "100%");
  const b100 = iconButton("zoom_actual", tr("100%表示"));
  const bfit = iconButton("fit", tr("全体を表示"));
  const rotTitle = el("span", "cv-title", tr("回転"));
  const rot = slider(-180, 180, 0);
  const rotLabel = el("span", "cv-value", "0°");
  const b0 = iconButton("rotate_reset", tr("回転を0°に戻す"));
  const flipH = iconButton("flip_horizontal", tr("左右反転プレビュー"), { checkable: true });
  const flipV = iconButton("flip_vertical", tr("上下反転プレビュー"), { checkable: true });
  zoom.addEventListener("input", () => viewer.setZoom(Number(zoom.value) / 100));
  rot.addEventListener("input", () => viewer.setRotation(Number(rot.value)));
  b100.addEventListener("click", () => viewer.showActual());
  bfit.addEventListener("click", () => viewer.fit());
  b0.addEventListener("click", () => viewer.setRotation(0));
  flipH.addEventListener("click", () => {
    viewer.setFlipped(!viewer.flipped);
    setChecked(flipH, viewer.flipped);
  });
  flipV.addEventListener("click", () => {
    viewer.setFlippedVertical(!viewer.flippedV);
    setChecked(flipV, viewer.flippedV);
  });

  // ---- 今の絵の書き出し ----
  const copy = iconButton("clipboard", tr("今の絵をクリップボードへ書き出す"));
  const save = iconButton("camera", tr("今の絵をフォルダーへ書き出す（年月日時分秒のファイル名）。Shift＋クリックで保存先を選び直す"));
  const separator = el("span", "cv-sep", "");
  copy.addEventListener("click", async () => {
    try {
      await invoke("view_copy_canvas");
      toast(tr("今の絵をクリップボードへ書き出しました。"));
    } catch (e) {
      toast(String(e));
    }
  });
  save.addEventListener("click", async (e) => {
    const now = new Date();
    const two = (n) => String(n).padStart(2, "0");
    const stamp = `${now.getFullYear()}${two(now.getMonth() + 1)}${two(now.getDate())}${two(now.getHours())}${two(now.getMinutes())}${two(now.getSeconds())}`;
    try {
      const result = await invoke("view_save_snapshot", { choose: e.shiftKey, stamp, title: tr("画像の保存先フォルダー") });
      if (result.failed) await message(tr("画像を保存"), tr("画像を保存できませんでした：{path}", { path: result.failed }));
      else if (result.name) toast(tr("「{name}」として保存しました。", { name: result.name }));
    } catch (error) {
      toast(String(error));
    }
  });

  // ---- 見え方のチェック ----
  const compare = iconButton("compare", tr("色塗り前の初期状態と比べる（押すと初期状態を表示、もう一度押すと今の絵に戻る）"), { checkable: true });
  const black = iconButton("black_check", tr("ブラックチェック（背景以外を黒シルエット表示）"), { checkable: true });
  const map = iconButton("map_colors", tr("隣接色分け：隣り合う部分を見分けやすい仮の色で塗り分けて表示する（近い色の塗り漏れが見つかる。元の色が同じ部分は同じ色）。アクティブレイヤーだけに掛かる"), { checkable: true });
  const channel = document.createElement("button");
  channel.type = "button";
  channel.className = "cv-icon cv-channel";
  channel.tabIndex = -1;
  setTip(channel, tr("表示するチャンネル"));
  const exposureButton = iconButton("exposure", tr("露光量の調整：押すと 0 に戻す。もう一度押すと直前の値に戻す"), { checkable: true });
  const exposure = exposureControl((v) => set({ exposure: v }));
  setTip(exposure.element, tr("露光量（段）。左右ドラッグ、またはクリックして入力"));
  compare.addEventListener("click", () => set({ show_initial_state: !info?.show_initial_state }));
  black.addEventListener("click", () => set({ silhouette: !info?.silhouette }));
  map.addEventListener("click", () => set({ map_colors: !info?.map_colors }));
  exposureButton.addEventListener("click", () => set({ toggle_exposure: true }));
  channel.addEventListener("click", () => {
    const r = channel.getBoundingClientRect();
    showMenu(CHANNELS.map(([value, label]) => {
      const row = el("span", "cv-channel-item", "");
      row.append(channelIcon(value, 16), document.createTextNode(` ${tr(label)}${value === info?.channel ? "  ✓" : ""}`));
      // 同じものを選び直しても変えない（ChannelPreviewButton.setChannel）。
      return { label: row, action: () => value !== info?.channel && set({ channel: value }) };
    }), r.left, r.bottom + 2);
  });

  const spacer = (w) => {
    const s = document.createElement("span");
    s.style.width = `${w}px`;
    s.style.flex = "none";
    return s;
  };
  const stretch = el("span", "cv-stretch", "");
  bar.append(zoomTitle, zoom, zoomLabel, b100, bfit, spacer(16), rotTitle, rot, rotLabel, b0, flipH, flipV,
    spacer(16), stretch, copy, save, separator, compare, black, map, channel, exposureButton, exposure.element);

  // ---- 状態をそろえる ----
  function syncView() {
    const zp = Math.max(5, Math.min(1600, Math.round(viewer.zoom * 100)));
    const rd = Math.max(-180, Math.min(180, Math.round(viewer.rotation)));
    if (document.activeElement !== zoom) zoom.value = String(zp);
    if (document.activeElement !== rot) rot.value = String(rd);
    zoomLabel.textContent = `${zp}%`;
    rotLabel.textContent = `${rd}°`;
    setChecked(flipH, viewer.flipped);
    setChecked(flipV, viewer.flippedV);
    requestShrunk();
  }
  function apply(next) {
    info = next;
    setChecked(compare, info.show_initial_state);
    setChecked(black, info.silhouette);
    setChecked(map, info.map_colors);
    setChecked(exposureButton, Math.abs(info.exposure) > 1e-9);
    exposure.set(info.exposure);
    channel.replaceChildren(channelIcon(info.channel));
    const label = CHANNELS.find(([v]) => v === info.channel)?.[1] ?? "RGB";
    channel.setAttribute("aria-label", `${tr("チャンネル")}: ${tr(label)}`);
    viewer.setChecker(info.checker);
    viewer.useShrunk = info.shrink_mode === "keep_lines";
    shell?.setChecked?.("a_silhouette", info.silhouette);
    shell?.setChecked?.("a_preview_map", info.map_colors);
    requestShrunk();
  }
  async function set(change) {
    try {
      const out = await invoke("view_set", { change });
      apply(out.info);
      if (out.message) toast(tr(out.message));
    } catch (e) {
      toast(String(e));
    }
  }
  async function refresh() {
    try {
      apply(await invoke("view_state"));
    } catch {
      // 起動の途中。
    }
  }

  // ---- 縮小表示「線を残す」 ----
  let shrinkTimer = 0;
  let shrinking = false;
  function requestShrunk() {
    clearTimeout(shrinkTimer);
    if (!viewer.useShrunk) return;
    shrinkTimer = setTimeout(async () => {
      const want = viewer.shrinkSize();
      if (!want || busy?.()) return;
      if (viewer.shrunk && viewer.shrunk.width === want.width && viewer.shrunk.height === want.height) return;
      if (shrinking) return requestShrunk();
      shrinking = true;
      try {
        const buffer = await invoke("view_shrunk", want);
        const head = new DataView(buffer, 0, 16);
        const w = head.getUint32(8, true);
        const h = head.getUint32(12, true);
        // 待っているあいだに描いた・大きさが変わったなら捨てて作り直す。
        const now = viewer.shrinkSize();
        if (now && now.width === w && now.height === h && !busy?.()) viewer.setShrunk(w, h, new Uint8Array(buffer, 16));
        else requestShrunk();
      } catch {
        // 作品を閉じたなど。元の画像で描く。
      } finally {
        shrinking = false;
      }
    }, 120);
  }
  // 画素が変わったら（線・読み直し）縮めた画像を作り直す。
  const writeRect = viewer.writeRect.bind(viewer);
  viewer.writeRect = (...args) => {
    writeRect(...args);
    requestShrunk();
  };
  viewer.onchange = ((previous) => () => {
    previous?.();
    syncView();
  })(viewer.onchange);

  // ---- 事件 ----
  listen("view-changed", ({ payload }) => apply(payload));
  // 背景の表示色が変わると市松模様にするかが変わる。
  listen("display-changed", () => refresh());
  // 初期状態を消したら（保持した初期状態を消す）、比較をやめる（InitialStateController.clear）。
  let docTimer = 0;
  listen("document-changed", ({ payload }) => {
    if (payload?.source === "playback" || payload?.source === "canvas") return;
    clearTimeout(docTimer);
    docTimer = setTimeout(async () => {
      const now = await invoke("view_state").catch(() => null);
      if (!now) return;
      if (now.show_initial_state && !now.has_initial_state) await set({ show_initial_state: false });
      else apply(now);
    }, 150);
  });
  const ACTIONS = {
    a_silhouette: () => set({ silhouette: !info?.silhouette }),
    a_preview_map: () => set({ map_colors: !info?.map_colors }),
    a_exposure_toggle: () => set({ toggle_exposure: true }),
    a_channel_red: () => set({ toggle_channel: "red" }),
    a_channel_green: () => set({ toggle_channel: "green" }),
    a_channel_blue: () => set({ toggle_channel: "blue" }),
    a_channel_alpha: () => set({ toggle_channel: "alpha" }),
  };
  window.addEventListener("pma-action", (e) => {
    const f = ACTIONS[e.detail?.id];
    if (!f) return;
    e.preventDefault();
    f();
  });

  refresh();
  syncView();
  return {
    bar,
    /// 外枠（shell.js）ができたら渡す。メニューのチェックを今の状態に合わせる。
    setShell(s) {
      shell = s;
      if (info) apply(info);
    },
    requestShrunk,
  };
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

function slider(min, max, value) {
  const s = document.createElement("input");
  s.type = "range";
  s.min = String(min);
  s.max = String(max);
  s.value = String(value);
  s.className = "cv-slider";
  s.tabIndex = -1;
  return s;
}
