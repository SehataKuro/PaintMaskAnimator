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
      if (toolStore.view?.selection) {
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
    if (selection) {
      this.ants(ctx, () => {
        for (const outline of selection.outlines) this.path(ctx, outline, outline.length >= 3);
      });
    }
    const p = this.preview;
    if (p?.kind === "lasso" && p.points.length > 1) {
      this.ants(ctx, () => this.path(ctx, p.points, false));
    } else if (p?.kind === "rect") {
      const [a, b] = [p.start, p.end];
      this.ants(ctx, () => this.path(ctx, [a, [b[0], a[1]], b, [a[0], b[1]]], true));
    } else if (p?.kind === "shape" && p.points) {
      this.ants(ctx, () => {
        if (p.ellipse) this.ellipse(ctx, p.ellipse);
        else this.path(ctx, p.points, true);
      });
    } else if (p?.kind === "line") {
      ctx.save();
      ctx.strokeStyle = "rgba(77,139,255,0.9)";
      ctx.lineWidth = Math.max(1, p.width * this.viewer.zoom);
      ctx.lineCap = "round";
      ctx.beginPath();
      const a = this.toScreen(p.start);
      const b = this.toScreen(p.end);
      ctx.moveTo(a.x, a.y);
      if (p.control) {
        const k = this.toScreen(p.control);
        ctx.quadraticCurveTo(k.x, k.y, b.x, b.y);
      } else {
        ctx.lineTo(b.x, b.y);
      }
      ctx.stroke();
      ctx.restore();
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
