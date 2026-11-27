// 筆圧の設定の編集（Qt 版の pressure.py の PressureCurveWidget・PressureEditor）。
//
// カーブの形は Rust（pma-core の pressure_bezier_at）で計算した値を折れ線で引く
// （描くときに使う曲線と同じ）。
import { setTip } from "./tooltip.js";

const { invoke } = window.__TAURI__.core;

const DEFAULT_POINTS = [[0, 0], [0.5, 0.5], [1, 1]];

/// アンカーポイントを足す・動かす・消せる筆圧カーブ。
export class PressureCurve {
  constructor() {
    this.canvas = document.createElement("canvas");
    this.canvas.className = "pma-curve";
    this.points = DEFAULT_POINTS.map((p) => [...p]);
    this.samples = null;
    this.active = null;
    this.onchange = null;
    setTip(this.canvas, "左クリック：アンカーポイント追加／ドラッグ：移動／右クリック：中間アンカーポイント削除／曲線：3次ベジエ補間");
    this.canvas.addEventListener("pointerdown", (e) => this.press(e));
    this.canvas.addEventListener("pointermove", (e) => this.move(e));
    this.canvas.addEventListener("pointerup", () => (this.active = null));
    this.canvas.addEventListener("contextmenu", (e) => e.preventDefault());
    new ResizeObserver(() => this.draw()).observe(this.canvas);
  }

  setPoints(points) {
    const list = (points ?? []).map((p) => [Math.min(1, Math.max(0, +p[0])), Math.min(1, Math.max(0, +p[1]))]);
    list.sort((a, b) => a[0] - b[0]);
    this.points = list.length >= 2 ? list : [[0, 0], [1, 1]];
    this.points[0][0] = 0;
    this.points[this.points.length - 1][0] = 1;
    this.active = null;
    this.refresh();
  }

  graph() {
    const r = this.canvas.getBoundingClientRect();
    return { left: 28, top: 12, width: Math.max(1, r.width - 40), height: Math.max(1, r.height - 36), w: r.width, h: r.height };
  }

  toValue(e) {
    const g = this.graph();
    const r = this.canvas.getBoundingClientRect();
    const x = (e.clientX - r.left - g.left) / g.width;
    const y = 1 - (e.clientY - r.top - g.top) / g.height;
    return [Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y))];
  }

  toScreen([x, y]) {
    const g = this.graph();
    return [g.left + x * g.width, g.top + (1 - y) * g.height];
  }

  nearest(e, radius = 10) {
    const r = this.canvas.getBoundingClientRect();
    let best = null;
    let bestD = radius * radius;
    this.points.forEach((p, i) => {
      const [sx, sy] = this.toScreen(p);
      const d = (sx - (e.clientX - r.left)) ** 2 + (sy - (e.clientY - r.top)) ** 2;
      if (d <= bestD) {
        best = i;
        bestD = d;
      }
    });
    return best;
  }

  changed() {
    this.refresh();
    this.onchange?.(this.points.map((p) => [...p]));
  }

  press(e) {
    if (this.canvas.disabled) return;
    const index = this.nearest(e);
    if (e.button === 2) {
      if (index !== null && index > 0 && index < this.points.length - 1) {
        this.points.splice(index, 1);
        this.changed();
      }
      return;
    }
    if (e.button !== 0) return;
    this.canvas.setPointerCapture(e.pointerId);
    if (index === null) {
      const [x, y] = this.toValue(e);
      this.points.push([x, y]);
      this.points.sort((a, b) => a[0] - b[0]);
      let best = 0;
      this.points.forEach((p, i) => {
        if (Math.abs(p[0] - x) + Math.abs(p[1] - y) < Math.abs(this.points[best][0] - x) + Math.abs(this.points[best][1] - y)) best = i;
      });
      this.active = best;
      this.changed();
    } else {
      this.active = index;
    }
  }

  move(e) {
    if (this.active === null || !(e.buttons & 1)) return;
    let [x, y] = this.toValue(e);
    const i = this.active;
    if (i === 0) x = 0;
    else if (i === this.points.length - 1) x = 1;
    else x = Math.max(this.points[i - 1][0] + 0.005, Math.min(this.points[i + 1][0] - 0.005, x));
    this.points[i] = [x, y];
    this.changed();
  }

  async refresh() {
    const points = this.points.map((p) => [...p]);
    try {
      const samples = await invoke("tools_pressure_curve", { points, count: 100 });
      if (points.every((p, i) => this.points[i] && p[0] === this.points[i][0] && p[1] === this.points[i][1])) {
        this.samples = samples;
      }
    } catch {
      this.samples = null;
    }
    this.draw();
  }

  draw() {
    const c = this.canvas;
    const r = c.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.max(1, Math.round(r.width * dpr));
    c.height = Math.max(1, Math.round(r.height * dpr));
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "rgb(36,36,36)";
    ctx.fillRect(0, 0, r.width, r.height);
    const g = this.graph();
    ctx.strokeStyle = "rgb(90,90,90)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const x = g.left + (g.width * i) / 4;
      const y = g.top + (g.height * i) / 4;
      ctx.beginPath();
      ctx.moveTo(x, g.top);
      ctx.lineTo(x, g.top + g.height);
      ctx.moveTo(g.left, y);
      ctx.lineTo(g.left + g.width, y);
      ctx.stroke();
    }
    ctx.strokeStyle = "rgb(210,210,210)";
    ctx.strokeRect(g.left, g.top, g.width, g.height);
    ctx.strokeStyle = "rgb(255,210,70)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    const samples = this.samples;
    if (samples) {
      samples.forEach((v, i) => {
        const [x, y] = this.toScreen([i / (samples.length - 1), v]);
        if (i) ctx.lineTo(x, y);
        else ctx.moveTo(x, y);
      });
    }
    ctx.stroke();
    ctx.fillStyle = "#fff";
    for (const p of this.points) {
      const [x, y] = this.toScreen(p);
      ctx.beginPath();
      ctx.arc(x, y, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
    ctx.fillStyle = "rgb(220,220,220)";
    ctx.font = "11px sans-serif";
    ctx.fillText("出力", 4, g.top + 10);
    ctx.fillText("入力", g.left + g.width - 24, r.height - 5);
  }
}

/// 筆圧 1 つの設定（反映するか・最小サイズ・最大倍率・カーブ）。
export class PressureEditor {
  constructor(settings) {
    this.onchange = null;
    const root = document.createElement("div");
    root.className = "pma-pressure";
    const label = document.createElement("label");
    this.enabled = document.createElement("input");
    this.enabled.type = "checkbox";
    label.append(this.enabled, "筆圧をブラシサイズに反映");
    setTip(label, "筆圧はブラシサイズだけに反映されます。描画不透明度・色・アルファ値には一切影響しません。");
    root.appendChild(label);
    const slider = (title, min, max) => {
      const row = document.createElement("div");
      row.className = "pma-row";
      const name = document.createElement("span");
      name.className = "pma-pref-title";
      name.textContent = title;
      const input = document.createElement("input");
      input.type = "range";
      input.min = min;
      input.max = max;
      const value = document.createElement("span");
      value.className = "pma-value";
      input.addEventListener("input", () => {
        value.textContent = `${input.value}%`;
        this.emit();
      });
      row.append(name, input, value);
      root.appendChild(row);
      return { input, value };
    };
    this.minimum = slider("最小サイズ", 1, 100);
    this.maximum = slider("最大倍率", 10, 300);
    const curveTitle = document.createElement("div");
    curveTitle.textContent = "筆圧カーブ";
    root.appendChild(curveTitle);
    this.curve = new PressureCurve();
    this.curve.onchange = () => this.emit();
    root.appendChild(this.curve.canvas);
    this.enabled.addEventListener("change", () => this.emit());
    this.element = root;
    this.setSettings(settings ?? {});
  }

  emit() {
    this.onchange?.(this.settings());
  }

  settings() {
    return {
      enabled: this.enabled.checked,
      minimum: Number(this.minimum.input.value) / 100,
      maximum: Number(this.maximum.input.value) / 100,
      points: this.curve.points.map((p) => [...p]),
    };
  }

  /// 設定を見せる（onchange は呼ばない）。
  setSettings(s) {
    this.enabled.checked = s.enabled ?? true;
    this.minimum.input.value = Math.max(1, Math.min(100, Math.round((s.minimum ?? 0.1) * 100)));
    this.maximum.input.value = Math.max(10, Math.min(300, Math.round((s.maximum ?? 1) * 100)));
    this.minimum.value.textContent = `${this.minimum.input.value}%`;
    this.maximum.value.textContent = `${this.maximum.input.value}%`;
    this.curve.setPoints(s.points ?? DEFAULT_POINTS);
  }

  setEnabled(on) {
    this.element.classList.toggle("disabled", !on);
    for (const input of this.element.querySelectorAll("input")) input.disabled = !on;
    this.curve.canvas.disabled = !on;
  }
}
