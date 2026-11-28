// 【Group C】サブビューのパネル（Qt 版の subview.SubViewWidget・session_state）。
//
// 画像ファイルをコピーせず、元の場所へのリンクとして見せる参考画像。読み込みは Rust
// （subview.rs、pma-core の取り込み）。
// - 上の列：◀ 前の画像・▶ 次の画像・開く・全体・名前。下の列：拡大（5〜1600%）・100%表示・回転・0°。
// - 画像は窓に収まる大きさ（回した外形で）× 拡大で、中心からずらして見せる（補間なし）。
// - ホイールでカーソルの下を中心に拡大縮小（1 ノッチ＝×1.15）。右・中ボタンのドラッグでずらす。
//   Space を押してドラッグでずらす、Space＋Ctrl（macOS は ⌘）でドラッグの左右で拡大縮小。
// - 左ドラッグはスポイト。ルーペ（半径 42・10 倍）と前（今の描画色）・後の色を出し、離した所の
//   色を描画色にする（Qt の apply_sampled_color：白は背景色、ほかはメイン・サブの選んでいる方）。
// - ファイル・フォルダーを落として開く。見え方は config.json の last_paint_session.subview に残す。

import { activeColor, drawingOp, paletteOp, showMessage, state as colorState } from "./color_state.js";
import { fitDefaultSizes, togglePanel, onLayoutChange, panelStates } from "./layout.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const IS_MAC = navigator.platform.toUpperCase().includes("MAC");

const EYEDROPPER_CURSOR = (() => {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><g stroke="black" stroke-width="2" fill="none" stroke-linecap="round"><line x1="5" y1="18" x2="16" y2="7"/><line x1="8" y1="21" x2="19" y2="10"/><line x1="15" y1="6" x2="20" y2="11"/><circle cx="5" cy="19" r="2"/></g></svg>`;
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}") 5 19, crosshair`;
})();

const hex = ([r, g, b]) => "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase();
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

function node(tag, attrs = {}, text) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}

let panel = null;

export function createSubviewPanel() {
  if (!panel) panel = new SubView();
  return panel.root;
}

class SubView {
  constructor() {
    this.image = null; // {width, height, canvas, data}
    this.path = null;
    this.files = [];
    this.index = -1;
    this.zoom = 1;
    this.rotation = 0;
    this.pan = { x: 0, y: 0 };
    this.space = false;
    this.control = false;
    this.drag = null; // {mode, press, last, anchor, moved}
    this.pick = null; // {position, before, after}
    this.wheelPercent = null;

    const root = (this.root = node("div", { class: "sv-panel" }));
    const bar = node("div", { class: "sv-bar" });
    this.prev = node("button", { title: "前の画像" }, "◀");
    this.next = node("button", { title: "次の画像" }, "▶");
    const open = node("button", { title: "画像を開く" }, "開く");
    const fit = node("button", { title: "全体を表示" }, "全体");
    this.name = node("span", { class: "sv-name" }, "画像またはフォルダをドロップ");
    bar.append(this.prev, this.next, open, fit, this.name);

    this.canvas = node("canvas", { class: "sv-canvas", tabindex: "-1" });
    const box = node("div", { class: "sv-view" });
    box.append(this.canvas);

    const controls = node("div", { class: "sv-controls" });
    this.zoomSlider = node("input", { type: "range", min: "5", max: "1600", value: "100" });
    this.zoomLabel = node("span", { class: "sv-value" }, "100%");
    const zoom100 = node("button", {}, "100%表示");
    this.rotationSlider = node("input", { type: "range", min: "-180", max: "180", value: "0" });
    this.rotationLabel = node("span", { class: "sv-value" }, "0°");
    const rotation0 = node("button", {}, "0°");
    controls.append(node("span", {}, "拡大"), this.zoomSlider, this.zoomLabel, zoom100,
      node("span", {}, "回転"), this.rotationSlider, this.rotationLabel, rotation0);
    root.append(bar, box, controls);

    this.prev.addEventListener("click", () => this.step(-1));
    this.next.addEventListener("click", () => this.step(1));
    open.addEventListener("click", () => this.choose());
    fit.addEventListener("click", () => this.resetView());
    this.zoomSlider.addEventListener("input", () => this.setZoomPercent(+this.zoomSlider.value));
    zoom100.addEventListener("click", () => this.setSlider(this.zoomSlider, 100));
    this.rotationSlider.addEventListener("input", () => this.setRotation(+this.rotationSlider.value));
    rotation0.addEventListener("click", () => this.setSlider(this.rotationSlider, 0));

    new ResizeObserver(() => this.draw()).observe(box);
    this.installPointer();
    this.installKeys();
    this.installDrop();
    this.updateNavigation();
    this.restore();
  }

  // ---- 開く・並び ----

  async choose() {
    const path = await invoke("subview_pick").catch(() => null);
    if (path) this.loadPath(path);
  }

  /// 開く（Qt の load_path）。開けたら true。
  async loadPath(path) {
    let listing;
    try {
      listing = await invoke("subview_open", { path });
    } catch (e) {
      this.name.textContent = String(e);
      return false;
    }
    const target = listing.files[listing.index];
    const image = await this.read(target);
    if (!image) {
      this.name.textContent = `読み込めません: ${baseName(target)}`;
      return false;
    }
    this.files = listing.files;
    this.index = listing.index;
    this.setImage(target, image);
    return true;
  }

  async read(path) {
    try {
      const buffer = await invoke("subview_image", { path });
      const bytes = new Uint8Array(buffer);
      const view = new DataView(bytes.buffer, bytes.byteOffset, 8);
      const width = view.getUint32(0, true);
      const height = view.getUint32(4, true);
      if (!width || !height) return null;
      const data = new Uint8ClampedArray(bytes.buffer, bytes.byteOffset + 8, width * height * 4);
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      canvas.getContext("2d").putImageData(new ImageData(data, width, height), 0, 0);
      return { width, height, canvas, data };
    } catch {
      return null;
    }
  }

  setImage(path, image) {
    this.path = path;
    this.image = image;
    this.name.textContent = baseName(path);
    this.name.title = path;
    this.resetView();
    this.updateNavigation();
    this.save();
  }

  async step(offset) {
    if (!this.files.length) return;
    const index = this.index + offset;
    if (index < 0 || index >= this.files.length) return;
    const target = this.files[index];
    const image = await this.read(target);
    if (!image) return;
    this.index = index;
    this.setImage(target, image);
  }

  updateNavigation() {
    this.prev.disabled = !(this.index > 0);
    this.next.disabled = !(this.index >= 0 && this.index < this.files.length - 1);
  }

  // ---- 見え方 ----

  setSlider(slider, value) {
    slider.value = String(value);
    slider.dispatchEvent(new Event("input"));
  }

  resetView() {
    this.zoomSlider.value = "100";
    this.zoom = 1;
    this.zoomLabel.textContent = "100%";
    this.pan = { x: 0, y: 0 };
    this.draw();
    this.save();
  }

  setZoomPercent(value) {
    this.zoom = clamp(Math.trunc(value) / 100, 0.05, 16);
    this.zoomLabel.textContent = `${Math.trunc(value)}%`;
    this.draw();
    this.save();
  }

  setRotation(value) {
    this.rotation = value;
    this.rotationLabel.textContent = `${Math.trunc(value)}°`;
    this.draw();
    this.save();
  }

  size() {
    return { width: this.canvas.clientWidth, height: this.canvas.clientHeight };
  }

  fitScale() {
    const { width, height } = this.size();
    if (!this.image || width <= 0 || height <= 0) return 0;
    const angle = (this.rotation * Math.PI) / 180;
    const cos = Math.abs(Math.cos(angle));
    const sin = Math.abs(Math.sin(angle));
    const rw = this.image.width * cos + this.image.height * sin;
    const rh = this.image.width * sin + this.image.height * cos;
    return Math.min(width / rw, height / rh);
  }

  /// 画像 → パネルの座標の行列（Qt の _image_transform）。DOMMatrix。
  transform() {
    const m = new DOMMatrix();
    if (!this.image) return m;
    const { width, height } = this.size();
    // QRect.center() は (幅 − 1) / 2 の整数。
    const cx = Math.trunc((width - 1) / 2) + this.pan.x;
    const cy = Math.trunc((height - 1) / 2) + this.pan.y;
    const scale = Math.max(0.01, this.fitScale() * this.zoom);
    return m.translate(cx, cy).rotate(this.rotation).scale(scale, scale)
      .translate(-this.image.width / 2, -this.image.height / 2);
  }

  toImage(x, y) {
    if (!this.image) return null;
    const inverse = this.transform().inverse();
    if (Number.isNaN(inverse.a)) return null;
    const p = inverse.transformPoint(new DOMPoint(x, y));
    const ix = Math.trunc(p.x);
    const iy = Math.trunc(p.y);
    // Qt と同じく int() で 0 へ切り捨ててから範囲を見る。
    if (!(ix >= 0 && iy >= 0 && ix < this.image.width && iy < this.image.height)) return null;
    return { x: ix + 0.5, y: iy + 0.5 };
  }

  colorAt(x, y) {
    const p = this.toImage(x, y);
    if (!p) return null;
    const i = (Math.trunc(p.y) * this.image.width + Math.trunc(p.x)) * 4;
    const d = this.image.data;
    return [d[i], d[i + 1], d[i + 2]];
  }

  draw() {
    const ratio = window.devicePixelRatio || 1;
    const { width, height } = this.size();
    const w = Math.max(1, Math.round(width * ratio));
    const h = Math.max(1, Math.round(height * ratio));
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    const g = this.canvas.getContext("2d");
    g.setTransform(ratio, 0, 0, ratio, 0, 0);
    g.fillStyle = "#30343a";
    g.fillRect(0, 0, width, height);
    if (!this.image) {
      g.fillStyle = "#c3c8cc";
      g.font = "13px system-ui, sans-serif";
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText("画像またはフォルダを", width / 2, height / 2 - 9);
      g.fillText("ここにドロップ", width / 2, height / 2 + 9);
      return;
    }
    g.imageSmoothingEnabled = false;
    g.setTransform(new DOMMatrix([ratio, 0, 0, ratio, 0, 0]).multiply(this.transform()));
    g.drawImage(this.image.canvas, 0, 0);
    g.setTransform(ratio, 0, 0, ratio, 0, 0);
    if (this.drag?.mode === "pick" && this.pick) this.drawLoupe(g, width, height, ratio);
  }

  drawLoupe(g, width, height, ratio) {
    const { x, y } = this.pick.position;
    const point = this.toImage(x, y);
    if (!point) return;
    const radius = 42;
    const cx = clamp(x + 58, radius + 4, width - radius - 4);
    const cy = clamp(y - 58, radius + 4, height - radius - 30);
    g.save();
    g.beginPath();
    g.arc(cx, cy, radius, 0, Math.PI * 2);
    g.clip();
    g.fillStyle = "#20242a";
    g.fillRect(cx - radius, cy - radius, radius * 2, radius * 2);
    g.setTransform(new DOMMatrix([ratio, 0, 0, ratio, 0, 0]).translate(cx, cy).scale(10, 10).translate(-point.x, -point.y));
    g.imageSmoothingEnabled = false;
    g.drawImage(this.image.canvas, 0, 0);
    g.restore();
    g.strokeStyle = "white";
    g.lineWidth = 2;
    g.beginPath();
    g.arc(cx, cy, radius, 0, Math.PI * 2);
    g.moveTo(cx - 7, cy);
    g.lineTo(cx + 7, cy);
    g.moveTo(cx, cy - 7);
    g.lineTo(cx, cy + 7);
    g.stroke();
    const top = cy + radius + 5;
    const swatches = [[cx - 40, this.pick.before, "前"], [cx + 2, this.pick.after, "後"]];
    g.lineWidth = 1;
    g.font = "12px system-ui, sans-serif";
    g.textAlign = "center";
    g.textBaseline = "middle";
    for (const [left, color, label] of swatches) {
      g.fillStyle = color ? hex(color) : "#000000";
      g.fillRect(left, top, 38, 20);
      g.strokeStyle = "white";
      g.strokeRect(left + 0.5, top + 0.5, 38, 20);
      g.fillStyle = "white";
      g.fillText(label, left + 19, top + 10);
    }
  }

  updateCursor() {
    const mode = this.drag?.mode;
    let cursor = "";
    if (mode === "pan") cursor = "grabbing";
    else if (mode === "zoom" || (this.space && this.control)) cursor = "zoom-in";
    else if (this.space) cursor = "grab";
    else if (mode === "pick") cursor = EYEDROPPER_CURSOR;
    this.canvas.style.cursor = cursor;
  }

  /// 倍率を変える。anchor（パネルの座標）の下の画素は動かさない（Qt の zoom_to_percent）。
  zoomToPercent(percent, anchor) {
    if (!anchor || !this.image) {
      this.setSlider(this.zoomSlider, percent);
      return;
    }
    const inverse = this.transform().inverse();
    this.setSlider(this.zoomSlider, Math.trunc(percent));
    if (Number.isNaN(inverse.a)) return;
    const p = inverse.transformPoint(new DOMPoint(anchor.x, anchor.y));
    const moved = this.transform().transformPoint(p);
    this.pan.x += anchor.x - moved.x;
    this.pan.y += anchor.y - moved.y;
    this.draw();
    this.save();
  }

  // ---- 入力 ----

  installPointer() {
    const c = this.canvas;
    const local = (e) => {
      const r = c.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };
    c.addEventListener("contextmenu", (e) => e.preventDefault());
    c.addEventListener("pointerenter", () => this.updateCursor());
    c.addEventListener("pointerdown", (e) => {
      c.focus({ preventScroll: true });
      if (![0, 1, 2].includes(e.button)) return;
      e.preventDefault();
      c.setPointerCapture(e.pointerId);
      const p = local(e);
      const drag = { press: p, last: p, anchor: p, moved: false, button: e.button };
      if (e.button === 1 || e.button === 2) {
        // 右ドラッグ（中ボタンも）で画像を動かす。左クリックはスポイト。
        drag.mode = "pan";
      } else if (this.space) {
        const ctrl = this.control || (IS_MAC ? e.metaKey : e.ctrlKey);
        drag.mode = ctrl ? "zoom" : "pan";
      } else {
        drag.mode = "pick";
        this.pick = { position: p, before: activeColor(colorState.drawing), after: this.colorAt(p.x, p.y) };
      }
      this.drag = drag;
      this.updateCursor();
      this.draw();
    });
    c.addEventListener("pointermove", (e) => {
      const drag = this.drag;
      if (!drag) return;
      const p = local(e);
      const dx = p.x - drag.last.x;
      const dy = p.y - drag.last.y;
      if (Math.abs(p.x - drag.press.x) + Math.abs(p.y - drag.press.y) > 3) drag.moved = true;
      if (drag.mode === "pan") {
        this.pan.x += dx;
        this.pan.y += dy;
        this.draw();
        this.save();
      } else if (drag.mode === "zoom") {
        const percent = Math.round(this.zoom * 1.01 ** dx * 100);
        this.zoomToPercent(clamp(percent, 5, 1600), drag.anchor);
      } else if (drag.mode === "pick") {
        this.pick.position = p;
        this.pick.after = this.colorAt(p.x, p.y);
        this.draw();
      }
      drag.last = p;
    });
    const release = (e, apply) => {
      const drag = this.drag;
      if (!drag || e.button !== drag.button) return;
      if (apply && drag.mode === "pick") {
        const p = local(e);
        const color = this.colorAt(p.x, p.y);
        if (color) this.applyColor(color);
      }
      this.drag = null;
      this.pick = null;
      this.updateCursor();
      this.draw();
    };
    c.addEventListener("pointerup", (e) => release(e, true));
    c.addEventListener("pointercancel", (e) => release(e, false));
    c.addEventListener("wheel", (e) => {
      // トラックパッドの細かいイベントでも移動量に比例させる（1ノッチ=120）。
      const delta = e.wheelDeltaY ?? -e.deltaY * (e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? 800 : 1);
      if (!delta) return;
      e.preventDefault();
      const current = +this.zoomSlider.value;
      let exact = this.wheelPercent;
      if (exact == null || Math.round(exact) !== current) exact = current;
      exact = clamp(exact * 1.15 ** (delta / 120), 5, 1600);
      this.wheelPercent = exact;
      if (Math.round(exact) !== current) this.zoomToPercent(Math.round(exact), local(e));
    }, { passive: false });
  }

  /// 拾った色を描画色にする（Qt の apply_sampled_color と、サブビューの知らせ）。
  async applyColor(color) {
    if (color[0] === 255 && color[1] === 255 && color[2] === 255) {
      await drawingOp({ op: "mode", mode: "transparent" });
    } else {
      await drawingOp({ op: "set_active", color });
      await paletteOp({ op: "focus_matching", rgb: color });
    }
    showMessage(`サブビューから ${hex(color)} を取得しました`, { ms: 2500 });
  }

  /// Space・Ctrl（macOS は ⌘）はサブビューにフォーカスがあるあいだ、ここで受ける
  /// （Qt の eventFilter：キャンバスの押している間のツールへ渡さない）。
  installKeys() {
    const key = (e) => {
      if (e.code === "Space") return "space";
      if (e.key === (IS_MAC ? "Meta" : "Control")) return "control";
      return null;
    };
    const handle = (e, pressed) => {
      const k = key(e);
      if (!k) return;
      e.preventDefault();
      e.stopPropagation();
      if (e.repeat) return;
      this[k] = pressed;
      this.updateCursor();
    };
    this.root.addEventListener("keydown", (e) => handle(e, true));
    this.root.addEventListener("keyup", (e) => handle(e, false));
    this.root.addEventListener("focusout", (e) => {
      if (this.root.contains(e.relatedTarget)) return;
      this.space = false;
      this.control = false;
      this.updateCursor();
    });
  }

  installDrop() {
    listen("tauri://drag-drop", async ({ payload }) => {
      if (!payload?.paths?.length || !payload.position || !this.root.isConnected) return;
      if (this.root.ownerDocument !== document) return;
      const ratio = window.devicePixelRatio || 1;
      const x = payload.position.x / ratio;
      const y = payload.position.y / ratio;
      const r = this.root.getBoundingClientRect();
      if (x < r.left || x > r.right || y < r.top || y > r.bottom) return;
      for (const path of payload.paths) {
        if (await this.loadPath(path)) return;
      }
    }).catch(() => {});
  }

  // ---- 残す・戻す（Qt の session_state） ----

  save() {
    if (!this.restored) return;
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => {
      const open = panelStates().find(([id]) => id === "subview")?.[2] ?? true;
      invoke("subview_state_set", {
        state: {
          path: this.path,
          zoom: +this.zoomSlider.value,
          rotation: +this.rotationSlider.value,
          pan_x: this.pan.x,
          pan_y: this.pan.y,
          open,
        },
      }).catch(() => {});
    }, 400);
  }

  async restore() {
    const info = await invoke("startup").catch(() => null);
    const selftest = Boolean(info?.pen_selftest || info?.playback_selftest || info?.tools_selftest);
    const record = selftest ? null : await invoke("subview_state_get").catch(() => null);
    if (record?.path && (await this.loadPath(record.path))) {
      this.setSlider(this.zoomSlider, record.zoom);
      this.setSlider(this.rotationSlider, record.rotation);
      this.pan = { x: record.pan_x, y: record.pan_y };
      this.draw();
    }
    if (typeof record?.open === "boolean") {
      // パネルを作っている途中なので、配置が整ってから閉じる・出す。閉じると dockview が
      // 列を等分するので、既定の幅に戻す（保存したワークスペースはこのあと shell.js が当てる）。
      setTimeout(() => {
        const shown = panelStates().find(([id]) => id === "subview")?.[2];
        if (shown === record.open) return;
        togglePanel("subview", record.open);
        fitDefaultSizes();
      }, 0);
    }
    this.restored = true;
    onLayoutChange(() => this.save());
  }
}

function baseName(path) {
  return String(path).split(/[\\/]/).pop();
}
