// カラーサークルのパネル（Qt の widgets.py の HSVColorWheel と、toolpanel.py の描画色の欄）。
//
// - 色相：外側のリング（RING）か上のバー（BAR）
// - 内側：彩度×明度の四角（HSV）か、輝度と彩度の三角（HLS。上＝純色、左下＝黒、右下＝白）
// - 下：メイン・サブの重ねた見本（⇄ で入れ替え、◩ で黒／白に戻す）、カラーコード、背景色
//
// 形の切り替えは右クリックのメニュー（Qt の _build_color_wheel_menu）。選んだ形はこの端末に
// 覚える（Qt の config と同じく、作品には入れない）。
import { clamp, fromHsv, hex, parseHex, sameColor, toHsv } from "./color_math.js";
import { activeColor, drawingOp, el, pickColor, showMenu, showMessage, startColors, subscribe } from "./color_state.js";

const MODES_KEY = "pma.colorWheel";

function loadModes() {
  try {
    const v = JSON.parse(localStorage.getItem(MODES_KEY) || "{}");
    return { mode: v.mode === "HLS" ? "HLS" : "HSV", hue: v.hue === "BAR" ? "BAR" : "RING" };
  } catch {
    return { mode: "HSV", hue: "RING" };
  }
}

function saveModes(m) {
  try {
    localStorage.setItem(MODES_KEY, JSON.stringify(m));
  } catch {
    // 覚えられなくても動く。
  }
}

export function createColorWheelPanel() {
  startColors();
  const modes = loadModes();
  const root = el("div", { class: "panel cw-panel" });
  const surface = el("div", { class: "cw-surface" });
  const canvas = el("canvas", { class: "cw-wheel" });
  surface.append(canvas);
  root.append(surface);

  // ---- 輪 ----
  let color = [0, 0, 0];
  // 色相は自分でも持つ（無彩色にしても色相を失わない）。
  let hue = 0;
  let size = { w: 0, h: 0, dpr: 1 };
  let cache = null;

  const geometry = () => {
    const { w, h } = size;
    if (modes.hue === "RING") {
      const avail = Math.max(1, h - 4);
      const d = Math.min(Math.max(1, w - 4), Math.max(1, avail - 2));
      const cx = w / 2;
      const cy = 2 + avail / 2;
      const outer = d / 2;
      const ring = Math.max(12, Math.min(22, outer * 0.2));
      const inner = Math.max(1, outer - ring - 3);
      const side = inner * Math.SQRT2;
      return { cx, cy, outer, ringInner: outer - ring, square: { x: cx - side / 2, y: cy - side / 2, s: side } };
    }
    const bar = { x: 4, y: 3, w: Math.max(24, w - 8), h: 18 };
    const side = Math.min(Math.max(1, w - 8), Math.max(1, h - 29));
    return { bar, square: { x: (w - side) / 2, y: 25, s: side } };
  };

  const ringHue = (g, x, y) => {
    const a = Math.atan2(x - g.cx, g.cy - y);
    return Math.round((((a * 180) / Math.PI) % 360 + 360) % 360 * 359 / 360);
  };

  const triangle = (g) => {
    const q = g.square;
    return { top: [q.x + q.s / 2, q.y], left: [q.x, q.y + q.s], right: [q.x + q.s, q.y + q.s] };
  };

  const barycentric = ([px, py], { top, left, right }) => {
    const denom = (left[1] - right[1]) * (top[0] - right[0]) + (right[0] - left[0]) * (top[1] - right[1]);
    if (Math.abs(denom) < 1e-9) return [0, 0, 1];
    const wTop = ((left[1] - right[1]) * (px - right[0]) + (right[0] - left[0]) * (py - right[1])) / denom;
    const wLeft = ((right[1] - top[1]) * (px - right[0]) + (top[0] - right[0]) * (py - right[1])) / denom;
    return [wTop, wLeft, 1 - wTop - wLeft];
  };

  const triangleColor = (wTop, wRight) => {
    const pure = fromHsv(hue, 255, 255);
    return pure.map((c) => clamp(Math.round(wTop * c + wRight * 255), 0, 255));
  };

  const trianglePoint = (g) => {
    const t = triangle(g);
    const [hr, hg, hb] = fromHsv(hue, 255, 255);
    const [cr, cg, cb] = color;
    const saa = hr * hr + hg * hg + hb * hb;
    const sac = (hr + hg + hb) * 255;
    const scc = 3 * 255 * 255;
    const b1 = hr * cr + hg * cg + hb * cb;
    const b2 = 255 * (cr + cg + cb);
    const det = saa * scc - sac * sac;
    let wTop = 0;
    let wRight = 1;
    if (Math.abs(det) >= 1e-6) {
      wTop = (b1 * scc - b2 * sac) / det;
      wRight = (saa * b2 - sac * b1) / det;
    }
    wTop = Math.max(0, wTop);
    wRight = Math.max(0, wRight);
    const total = wTop + wRight;
    if (total > 1) {
      wTop /= total;
      wRight /= total;
    }
    const wLeft = 1 - wTop - wRight;
    return [0, 1].map((i) => wTop * t.top[i] + wLeft * t.left[i] + wRight * t.right[i]);
  };

  const image = () => {
    const { w, h, dpr } = size;
    const key = `${w}x${h}@${dpr}:${hue}:${modes.mode}:${modes.hue}`;
    if (cache?.key === key) return cache.bitmap;
    const W = Math.round(w * dpr);
    const H = Math.round(h * dpr);
    const off = new OffscreenCanvas(Math.max(1, W), Math.max(1, H));
    const ctx = off.getContext("2d");
    const img = ctx.createImageData(off.width, off.height);
    const g = geometry();
    const tri = modes.mode === "HLS" ? triangle(g) : null;
    const q = g.square;
    for (let Y = 0; Y < off.height; Y++) {
      for (let X = 0; X < off.width; X++) {
        const x = (X + 0.5) / dpr;
        const y = (Y + 0.5) / dpr;
        let c = null;
        if (modes.hue === "RING") {
          const d = Math.hypot(x - g.cx, y - g.cy);
          if (d >= g.ringInner && d <= g.outer) c = fromHsv(ringHue(g, x, y), 255, 255);
        } else if (x >= g.bar.x && x < g.bar.x + g.bar.w && y >= g.bar.y && y < g.bar.y + g.bar.h) {
          c = fromHsv(Math.round((359 * (x - g.bar.x)) / g.bar.w), 255, 255);
        }
        if (!c && x >= q.x && x < q.x + q.s && y >= q.y && y < q.y + q.s) {
          if (tri) {
            const [wt, wl, wr] = barycentric([x, y], tri);
            if (wt >= 0 && wl >= 0 && wr >= 0) c = triangleColor(wt, wr);
          } else {
            const sat = clamp(Math.round((255 * (x - q.x)) / q.s), 0, 255);
            const val = clamp(Math.round((255 * (q.y + q.s - y)) / q.s), 0, 255);
            c = fromHsv(hue, sat, val);
          }
        }
        if (!c) continue;
        const i = (Y * off.width + X) * 4;
        img.data[i] = c[0];
        img.data[i + 1] = c[1];
        img.data[i + 2] = c[2];
        img.data[i + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    cache = { key, bitmap: off };
    return off;
  };

  const draw = () => {
    const rect = surface.getBoundingClientRect();
    const w = Math.max(160, Math.floor(rect.width - 6));
    const h = Math.min(200, Math.max(160, w));
    const dpr = window.devicePixelRatio || 1;
    if (w !== size.w || h !== size.h || dpr !== size.dpr) {
      size = { w, h, dpr };
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(image(), 0, 0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const g = geometry();
    // 色相の印。
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#ffffff";
    if (modes.hue === "RING") {
      const a = ((hue * 360) / 359) * (Math.PI / 180);
      const r = (g.outer + g.ringInner) / 2;
      ctx.beginPath();
      ctx.arc(g.cx + Math.sin(a) * r, g.cy - Math.cos(a) * r, (g.outer - g.ringInner) / 2, 0, Math.PI * 2);
      ctx.stroke();
    } else {
      const x = g.bar.x + (hue / 359) * g.bar.w;
      ctx.strokeRect(x - 2, g.bar.y - 1, 4, g.bar.h + 2);
    }
    // 色の印。
    let p;
    if (modes.mode === "HLS") p = trianglePoint(g);
    else {
      const t = toHsv(color);
      p = [g.square.x + (t.s / 255) * g.square.s, g.square.y + (1 - t.v / 255) * g.square.s];
    }
    const light = (color[0] * 299 + color[1] * 587 + color[2] * 114) / 1000 > 140;
    ctx.strokeStyle = light ? "#000000" : "#ffffff";
    ctx.beginPath();
    ctx.arc(p[0], p[1], 5, 0, Math.PI * 2);
    ctx.stroke();
  };

  // 押した場所（色相か内側か）を覚えて、ドラッグのあいだはそこだけを動かす。
  let part = null;
  let sending = null;
  let pending = null;
  const send = (c) => {
    pending = c;
    if (sending) return;
    sending = (async () => {
      while (pending) {
        const next = pending;
        pending = null;
        await drawingOp({ op: "set_active", color: next });
      }
      sending = null;
    })();
  };

  const pointColor = (x, y, start) => {
    const g = geometry();
    const q = g.square;
    if (start) {
      if (modes.hue === "RING") {
        const d = Math.hypot(x - g.cx, y - g.cy);
        if (d >= g.ringInner - 2 && d <= g.outer + 2) part = "hue";
      } else if (y >= g.bar.y && y <= g.bar.y + g.bar.h) part = "hue";
      if (!part && x >= q.x - 2 && x <= q.x + q.s + 2 && y >= q.y - 2 && y <= q.y + q.s + 2) part = "inner";
      if (!part) return null;
    }
    if (part === "hue") {
      // 三角では、中の位置（重み）を保って色相だけ替える。
      const weights = modes.mode === "HLS" ? barycentric(trianglePoint(g), triangle(g)) : null;
      hue = modes.hue === "RING" ? ringHue(g, x, y) : clamp(Math.round((359 * (x - g.bar.x)) / g.bar.w), 0, 359);
      if (weights) return triangleColor(Math.max(0, weights[0]), Math.max(0, weights[2]));
      const t = toHsv(color);
      return fromHsv(hue, t.s, t.v);
    }
    if (modes.mode === "HLS") {
      const tri = triangle(g);
      let [wt, wl, wr] = barycentric([x, y], tri);
      wt = Math.max(0, wt);
      wl = Math.max(0, wl);
      wr = Math.max(0, wr);
      const sum = wt + wl + wr || 1;
      return triangleColor(wt / sum, wr / sum);
    }
    const sat = clamp(Math.round((255 * (x - q.x)) / q.s), 0, 255);
    const val = clamp(Math.round((255 * (q.y + q.s - y)) / q.s), 0, 255);
    return fromHsv(hue, sat, val);
  };

  const local = (e) => {
    const r = canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };
  canvas.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    part = null;
    const c = pointColor(...local(e), true);
    if (!c) return;
    canvas.setPointerCapture(e.pointerId);
    color = c;
    draw();
    send(c);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!part) return;
    const c = pointColor(...local(e), false);
    if (!c || sameColor(c, color)) return;
    color = c;
    draw();
    send(c);
  });
  canvas.addEventListener("pointerup", () => (part = null));
  // ダブルクリック：いま編集している色（メイン／サブ）をカラーピッカーで決める。
  canvas.addEventListener("dblclick", () => editColor(current?.mode === "sub" ? "sub" : "main"));
  canvas.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    const set = (k, v) => () => {
      modes[k] = v;
      saveModes(modes);
      cache = null;
      draw();
    };
    showMenu([
      { label: `${modes.hue === "RING" ? "● " : "　"}色相リング`, action: set("hue", "RING") },
      { label: `${modes.hue === "BAR" ? "● " : "　"}色相バー`, action: set("hue", "BAR") },
      "-",
      { label: `${modes.mode === "HSV" ? "● " : "　"}HSV（四角）`, action: set("mode", "HSV") },
      { label: `${modes.mode === "HLS" ? "● " : "　"}HLS（三角）`, action: set("mode", "HLS") },
    ], e.clientX, e.clientY, root.ownerDocument);
  });
  canvas.title = "外側の色相リングと、内側の四角で彩度と明度を選択します。右クリックで形を切り替えます。";

  // ---- 描画色の欄 ----
  const mainBtn = el("button", { class: "cw-well main", title: "クリック：メイン色を選択／ダブルクリック：色を設定" });
  const subBtn = el("button", { class: "cw-well sub", title: "クリック：サブ色を選択／ダブルクリック：色を設定" });
  const swapBtn = el("button", { class: "cw-corner swap", title: "メイン色とサブ色を切り替え" }, "⇄");
  const resetBtn = el("button", { class: "cw-corner reset", title: "初期色（黒／白）に戻す" }, el("span", { class: "cw-reset-icon" }));
  const stack = el("div", { class: "cw-stack" }, subBtn, mainBtn, swapBtn, resetBtn);
  const codeRow = (which, caption) => {
    const input = el("input", { class: "cw-code", maxlength: 7, title: "カラーコードを入力して Enter で色を変更" });
    const copy = el("button", { class: "cw-copy", title: "現在のカラーコードをコピー" }, "コピー");
    const label = el("span", { class: "cw-caption" }, caption);
    const apply = async () => {
      const c = parseHex(input.value);
      if (!c) {
        input.value = current?.[which] ?? "";
        return;
      }
      await drawingOp({ op: "set", which, color: c });
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        apply();
        input.blur();
      }
    });
    input.addEventListener("change", apply);
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(current?.[which] ?? "");
        showMessage(`カラーコードをコピーしました：${current?.[which]}`);
      } catch {
        input.select();
        document.execCommand("copy");
      }
    });
    return { label, input, copy };
  };
  const mainRow = codeRow("main", "メイン");
  const subRow = codeRow("sub", "サブ");
  const bgBtn = el("button", { class: "cw-bg", title: "クリック：背景色で描画／右クリック：背景色の表示色を変更" });
  const info = el("div", { class: "cw-info" },
    mainRow.label, mainRow.input, mainRow.copy,
    subRow.label, subRow.input, subRow.copy,
    el("span", { class: "cw-caption" }, "背景色"), bgBtn,
  );
  root.append(el("div", { class: "cw-drawing" }, stack, info));

  async function editColor(which) {
    if (!current) return;
    const initial = parseHex(current[which]);
    const titles = { main: "メイン色", sub: "サブ色", transparent: "背景色の表示色" };
    const c = await pickColor(initial, { title: titles[which] });
    if (!c) return;
    if (which === "transparent") await drawingOp({ op: "background", color: c });
    else await drawingOp({ op: "set", which, color: c });
  }

  mainBtn.addEventListener("click", () => drawingOp({ op: "mode", mode: "main" }));
  subBtn.addEventListener("click", () => drawingOp({ op: "mode", mode: "sub" }));
  mainBtn.addEventListener("dblclick", () => editColor("main"));
  subBtn.addEventListener("dblclick", () => editColor("sub"));
  swapBtn.addEventListener("click", () => drawingOp({ op: "swap" }));
  resetBtn.addEventListener("click", () => drawingOp({ op: "reset" }));
  bgBtn.addEventListener("click", () => drawingOp({ op: "mode", mode: "transparent" }));
  bgBtn.addEventListener("dblclick", () => editColor("transparent"));
  bgBtn.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    editColor("transparent");
  });

  let current = null;
  subscribe("drawing", (d) => {
    current = d;
    mainBtn.style.background = d.main;
    subBtn.style.background = d.sub;
    bgBtn.style.background = d.background;
    mainBtn.classList.toggle("active", d.mode === "main");
    subBtn.classList.toggle("active", d.mode === "sub");
    bgBtn.classList.toggle("active", d.mode === "transparent");
    mainRow.label.classList.toggle("active", d.mode === "main");
    subRow.label.classList.toggle("active", d.mode === "sub");
    if (document.activeElement !== mainRow.input) mainRow.input.value = d.main;
    if (document.activeElement !== subRow.input) subRow.input.value = d.sub;
    // 背景色で描いているあいだも、輪はメインの色を出す（Qt の sync_sliders）。
    const c = activeColor(d);
    if (!part && !sameColor(c, color)) {
      color = c;
      const t = toHsv(c);
      if (t.h >= 0) hue = t.h;
      draw();
    }
  });

  new ResizeObserver(() => draw()).observe(surface);
  return root;
}
