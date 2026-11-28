// カラースライダーのパネル（Qt の toolpanel.py の rebuild_color_sliders など）。
// RGB／HLS／CMYK を右クリックで切り替える。背景色で描いているあいだは動かさない。
import { fromCmyk, fromHsl, toCmyk, toHsl, hex, clamp } from "./color_math.js";
import { drawingOp, el, showMenu, startColors, subscribe } from "./color_state.js";

const KEY = "pma.colorSliders";

const SPECS = {
  RGB: [["R", 0, 255], ["G", 0, 255], ["B", 0, 255]],
  HLS: [["H", 0, 359], ["L", 0, 255], ["S", 0, 255]],
  CMYK: [["C", 0, 255], ["M", 0, 255], ["Y", 0, 255], ["K", 0, 255]],
};

function channels(mode, c) {
  if (mode === "RGB") return [...c];
  if (mode === "HLS") {
    const t = toHsl(c);
    return [Math.max(0, t.h), t.l, t.s];
  }
  return toCmyk(c);
}

function fromChannels(mode, v) {
  const c = (x) => clamp(Math.trunc(x), 0, 255);
  if (mode === "RGB") return [c(v[0]), c(v[1]), c(v[2])];
  if (mode === "HLS") return fromHsl(clamp(Math.trunc(v[0]), 0, 359), c(v[2]), c(v[1]));
  return fromCmyk(c(v[0]), c(v[1]), c(v[2]), c(v[3]));
}

function gradients(mode, active) {
  const [r, g, b] = active;
  if (mode === "RGB") {
    return [
      `rgb(0,${g},${b}), rgb(255,${g},${b})`,
      `rgb(${r},0,${b}), rgb(${r},255,${b})`,
      `rgb(${r},${g},0), rgb(${r},${g},255)`,
    ];
  }
  if (mode === "HLS") {
    const t = toHsl(active);
    const hue = Math.max(0, t.h);
    const sat = Math.max(1, t.s);
    const mid = hex(fromHsl(hue, sat, 128));
    const gray = hex(fromHsl(hue, 0, 128));
    return [
      "#ff0000 0%, #ffff00 17%, #00ff00 33%, #00ffff 50%, #0000ff 67%, #ff00ff 83%, #ff0000 100%",
      `#000000, ${mid}, #ffffff`,
      `${gray}, ${mid}`,
    ];
  }
  return ["#ffffff, #00ffff", "#ffffff, #ff00ff", "#ffffff, #ffff00", "#ffffff, #000000"];
}

export function createColorSlidersPanel() {
  startColors();
  let mode = "RGB";
  try {
    const saved = localStorage.getItem(KEY);
    if (SPECS[saved]) mode = saved;
  } catch {
    // 覚えていなければ RGB。
  }
  const root = el("div", { class: "panel cs-panel" });
  const form = el("div", { class: "cs-form" });
  root.append(form);
  let rows = [];
  let drawing = null;
  let busy = null;
  let pending = null;

  const active = () => (drawing ? (drawing.mode === "sub" ? drawing.sub : drawing.main) : "#000000");
  const activeRgb = () => {
    const h = active().replace("#", "");
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  };

  const send = (c) => {
    pending = c;
    if (busy) return;
    busy = (async () => {
      while (pending) {
        const next = pending;
        pending = null;
        await drawingOp({ op: "set_active", color: next });
      }
      busy = null;
    })();
  };

  const changed = () => {
    if (!drawing || drawing.mode === "transparent") return;
    const values = rows.map((r) => Number(r.slider.value));
    const c = fromChannels(mode, values);
    refreshGradients(c);
    send(c);
  };

  const refreshGradients = (c) => {
    const list = gradients(mode, drawing?.mode === "transparent" ? [0, 0, 0] : c);
    rows.forEach((r, i) => (r.slider.style.background = `linear-gradient(to right, ${list[i]})`));
  };

  const rebuild = () => {
    form.replaceChildren();
    rows = SPECS[mode].map(([name, lo, hi]) => {
      const slider = el("input", { type: "range", min: lo, max: hi, step: 1, class: "cs-slider" });
      const number = el("input", { type: "number", min: lo, max: hi, step: 1, class: "cs-number" });
      slider.addEventListener("input", () => {
        number.value = slider.value;
        changed();
      });
      number.addEventListener("change", () => {
        let v = Math.round(Number(number.value) || 0);
        // HLS の H は回り込む（Qt の setWrapping）。
        if (mode === "HLS" && name === "H") v = ((v % 360) + 360) % 360;
        v = clamp(v, lo, hi);
        number.value = v;
        slider.value = v;
        changed();
      });
      form.append(el("span", { class: "cs-name" }, name), slider, number);
      return { slider, number };
    });
    sync();
  };

  const sync = () => {
    if (!drawing) return;
    const c = activeRgb();
    const transparent = drawing.mode === "transparent";
    root.classList.toggle("disabled", transparent);
    if (!transparent) {
      channels(mode, c).forEach((v, i) => {
        if (!rows[i]) return;
        rows[i].slider.value = v;
        if (document.activeElement !== rows[i].number) rows[i].number.value = v;
      });
    }
    refreshGradients(c);
  };

  root.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    showMenu(Object.keys(SPECS).map((m) => ({
      label: `${m === mode ? "● " : "　"}${m}`,
      action: () => {
        mode = m;
        try {
          localStorage.setItem(KEY, m);
        } catch {
          // 覚えられなくても動く。
        }
        rebuild();
      },
    })), e.clientX, e.clientY, root.ownerDocument);
  });

  subscribe("drawing", (d) => {
    drawing = d;
    // 自分で動かしている途中に戻ってきた値で、つまみを引き戻さない。
    if (!busy) sync();
  });
  rebuild();
  return root;
}
