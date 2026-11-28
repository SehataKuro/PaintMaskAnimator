// キャンバスの上に重ねる 2D の層（WebGL の画像の上）。
//
// - 選択範囲の点線（マーチングアンツ。Qt と同じく白黒の点線を流す）
// - 描いている途中の形（投げ縄・長方形・図形・直線／曲線）
// - ブラシの太さの丸（カーソル）
// - 右上の「選択解除」ボタン（どのツールからでも解除できる。issue #110）
//
// 形の座標はすべて画像の座標で持ち、描くときに viewer で画面の座標へ直す。
import { toolStore } from "./tools.js";
import { setTip } from "./tooltip.js";

const { invoke } = window.__TAURI__.core;

export class Overlay {
  constructor(host, viewer) {
    this.viewer = viewer;
    this.canvas = document.createElement("canvas");
    this.canvas.className = "pma-overlay";
    host.appendChild(this.canvas);
    // 描いている途中の形。{kind: "lasso"|"rect"|"shape"|"line", ...}
    this.preview = null;
    // ブラシの丸（画面の座標と半径の画素）。
    this.cursor = null;
    this.dash = 0;
    this.frame = 0;
    this.clearButton = document.createElement("button");
    this.clearButton.type = "button";
    this.clearButton.className = "pma-clear-selection";
    this.clearButton.textContent = "選択解除";
    setTip(this.clearButton, "現在の選択範囲を解除します。");
    this.clearButton.addEventListener("click", () => invoke("selection_edit", { args: { kind: "clear" } }));
    host.appendChild(this.clearButton);
    toolStore.subscribe((view) => {
      this.clearButton.style.display = view?.can_clear_selection ? "" : "none";
      this.requestDraw();
    });
    // 点線を流す（選択があるときだけ回す）。
    setInterval(() => {
      if (toolStore.view?.selection || toolStore.view?.transform) {
        this.dash = (this.dash + 1) % 8;
        this.requestDraw();
      }
    }, 120);
    new ResizeObserver(() => this.requestDraw()).observe(host);
  }

  setPreview(preview) {
    this.preview = preview;
    this.requestDraw();
  }

  /// 変形の今の形で描いた画素（{x, y, image}。null で消す）。
  setTransformPreview(preview) {
    this.transformPreview = preview;
    this.requestDraw();
  }

  setCursor(cursor) {
    this.cursor = cursor;
    this.requestDraw();
  }

  requestDraw() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.draw();
    });
  }

  toScreen(p) {
    return this.viewer.imageToScreen(p[0], p[1]);
  }

  path(ctx, points, close) {
    points.forEach((p, i) => {
      const s = this.toScreen(p);
      if (i) ctx.lineTo(s.x, s.y);
      else ctx.moveTo(s.x, s.y);
    });
    if (close) ctx.closePath();
  }

  ants(ctx, build) {
    ctx.save();
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "#fff";
    ctx.lineDashOffset = -this.dash;
    ctx.beginPath();
    build();
    ctx.stroke();
    ctx.strokeStyle = "#000";
    ctx.lineDashOffset = 4 - this.dash;
    ctx.stroke();
    ctx.restore();
  }

  draw() {
    const c = this.canvas;
    const rect = c.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(rect.width * dpr));
    const h = Math.max(1, Math.round(rect.height * dpr));
    if (c.width !== w || c.height !== h) {
      c.width = w;
      c.height = h;
    }
    const ctx = c.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const selection = toolStore.view?.selection;
    const transform = toolStore.view?.transform;
    // 変形の見た目（Qt：通常は不透明度 85%、クオリティは 100%）。点線・ハンドルより下に描く。
    if (transform && this.transformPreview) {
      const t = this.transformPreview;
      this.drawImagePixels(ctx, dpr, t.image, t.x, t.y, transform.quality ? 1 : 0.85);
    }
    // 変形中は、選択の輪郭を今の形へ写して引く（transformed_selection_outline）。
    const outlines = transform ? transform.outlines : selection?.outlines;
    if (outlines?.length) {
      this.ants(ctx, () => {
        for (const outline of outlines) this.path(ctx, outline, outline.length >= 3);
      });
    }
    if (transform) this.drawTransform(ctx, transform);
    const p = this.preview;
    if (p?.kind === "lasso" && p.points.length > 1) {
      this.ants(ctx, () => this.path(ctx, p.points, false));
    } else if (p?.kind === "rect") {
      const [a, b] = [p.start, p.end];
      this.ants(ctx, () => this.path(ctx, [a, [b[0], a[1]], b, [a[0], b[1]]], true));
    } else if (p?.kind === "pixels") {
      // 確定と同じ画素（直線・図形の #117）。画像の画素をそのまま拡大して重ねる。
      this.drawImagePixels(ctx, dpr, p.image, p.x, p.y, 1);
    }
    if (this.cursor) {
      const { x, y, radius } = this.cursor;
      ctx.save();
      ctx.lineWidth = 1;
      ctx.strokeStyle = "rgba(0,0,0,0.8)";
      ctx.beginPath();
      ctx.arc(x, y, Math.max(1, radius), 0, Math.PI * 2);
      ctx.stroke();
      ctx.strokeStyle = "rgba(255,255,255,0.8)";
      ctx.beginPath();
      ctx.arc(x, y, Math.max(1, radius) + 1, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
  }

  /// 変形の枠・メッシュの格子・回転のつまみ・ハンドル（Qt の paintEvent と同じ色と大きさ）。
  drawTransform(ctx, t) {
    const pts = t.points.map((p) => this.toScreen(p));
    if (!pts.length) return;
    ctx.save();
    ctx.strokeStyle = "rgb(0,220,255)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    const seg = (a, b) => {
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
    };
    let left;
    let right;
    if (t.mode === "mesh") {
      const { cols, rows } = t;
      if (pts.length === cols * rows) {
        for (let gy = 0; gy < rows; gy++) for (let gx = 0; gx < cols - 1; gx++) seg(pts[gy * cols + gx], pts[gy * cols + gx + 1]);
        for (let gx = 0; gx < cols; gx++) for (let gy = 0; gy < rows - 1; gy++) seg(pts[gy * cols + gx], pts[(gy + 1) * cols + gx]);
      }
      [left, right] = [t.points[0], t.points[cols - 1]];
    } else if (pts.length === 4) {
      for (let i = 0; i < 4; i++) seg(pts[i], pts[(i + 1) % 4]);
      [left, right] = [t.points[0], t.points[1]];
    }
    ctx.stroke();
    // 回転のつまみ（上辺の中点から外へ 36/表示倍率。transform_rotation_handle）。
    if (left && right) {
      const n = t.points.length;
      const c = t.points.reduce((a, p) => [a[0] + p[0] / n, a[1] + p[1] / n], [0, 0]);
      const mid = [(left[0] + right[0]) / 2, (left[1] + right[1]) / 2];
      const dx = mid[0] - c[0];
      const dy = mid[1] - c[1];
      const length = Math.hypot(dx, dy) || 1;
      const distance = 36 / Math.max(this.viewer.zoom, 0.01);
      const handle = this.toScreen([mid[0] + (dx / length) * distance, mid[1] + (dy / length) * distance]);
      const m = this.toScreen(mid);
      ctx.beginPath();
      ctx.moveTo(m.x, m.y);
      ctx.lineTo(handle.x, handle.y);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(handle.x, handle.y, 6, 0, Math.PI * 2);
      ctx.fillStyle = "rgb(255,220,70)";
      ctx.fill();
      ctx.stroke();
    }
    ctx.fillStyle = "#fff";
    for (const p of pts) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
    ctx.restore();
  }

  /// 画像の座標から画面の座標への行列（ctx.setTransform の 6 つの値）。
  imageMatrix(dpr) {
    const o = this.toScreen([0, 0]);
    const ax = this.toScreen([1, 0]);
    const ay = this.toScreen([0, 1]);
    return [ax.x - o.x, ax.y - o.y, ay.x - o.x, ay.y - o.y, o.x, o.y].map((v) => v * dpr);
  }

  /// 画像の (x, y) に、画素の絵（canvas）をニアレストで重ねる。
  drawImagePixels(ctx, dpr, image, x, y, alpha) {
    ctx.save();
    ctx.setTransform(...this.imageMatrix(dpr));
    ctx.imageSmoothingEnabled = false;
    ctx.globalAlpha = alpha;
    ctx.drawImage(image, x, y);
    ctx.restore();
  }

  ellipse(ctx, [x, y, w, h]) {
    const n = 64;
    const points = [];
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      points.push([x + w / 2 + (Math.cos(a) * w) / 2, y + h / 2 + (Math.sin(a) * h) / 2]);
    }
    this.path(ctx, points, true);
  }
}
