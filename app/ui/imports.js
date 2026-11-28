// 読み込みの画面側（計算は app/src/imports.rs → pma-core）。
//
// メニューの「読み込み」の各項目（Qt の import_images_dialog・import_image_folder_dialog・
// import_images_raw_dialog・import_genga_dialog・ImportController.psd_dialog・
// clip_animation_dialog・InitialStateController.import_line_art_dialog など）。
// ファイルを選び、素材の種類（デジタル／アナログ）と2値化を尋ね、進み具合と中止の窓を出す。
// 読み込みは Rust の別のスレッドで行い、終わると `import-job` の事件が来る。
//
// app.js が installImports で組み込む。メニューの項目は `pma-action` の事件で受ける。
import { el, openDialog, message, prompt } from "./dialogs.js";
import { pickColor, showMenu } from "./color_state.js";
import { showPasteDialog } from "./time_remap.js"; // 【Group B】落とした XDTS
import { notify } from "./tool_properties.js";
import { tr } from "./i18n.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const thousands = (n) => Number(n).toLocaleString("en-US");

/// Qt の tr(...).format(...)：`{key:,}` は3桁ごとのカンマで入れる。
function trf(text, values) {
  let out = tr(text);
  for (const [key, value] of Object.entries(values)) {
    out = out.split(`{${key}:,}`).join(thousands(value)).split(`{${key}}`).join(String(value));
  }
  return out;
}

// ---------------------------------------------------------------------------
// 進み具合（Qt の create_counter）
// ---------------------------------------------------------------------------

let progress = null; // {bar, text, close}
let waiting = null; // import-job の done を待つ resolve

function openProgress(title, label, cancellable = true) {
  const bar = el("progress", { max: "1", value: "0", style: { width: "100%" } });
  const text = el("div", { class: "sh-message", style: { minWidth: "360px" } }, label);
  const buttons = cancellable ? [{ label: tr("キャンセル"), value: "cancel", escape: true }] : [];
  const done = openDialog({ title, body: el("div", {}, text, bar), buttons });
  const state = {
    bar,
    text,
    close: () => text.closest(".sh-dialog")?.close?.("closed"),
  };
  done.then((value) => {
    if (value === "cancel") invoke("import_cancel");
    if (progress === state) progress = null;
  });
  progress = state;
  return state;
}

function onProgress(event) {
  if (!progress) return;
  progress.text.textContent = event.label;
  // 全体が 0 なら終わりの見えない棒（Qt の setRange(0, 0)。CLIP の解析）。
  if (!event.total) {
    progress.bar.removeAttribute("value");
    return;
  }
  const total = Math.max(1, event.total);
  progress.bar.max = total;
  progress.bar.value = Math.max(0, Math.min(total, event.value));
}

/// 読み込みを始め、終わり（import-job の done）を待つ。
async function runJob(title, label, start) {
  const finished = new Promise((resolve) => (waiting = resolve));
  openProgress(title, label);
  try {
    await start();
  } catch (e) {
    waiting = null;
    progress?.close();
    await message(title, String(e));
    return null;
  }
  const event = await finished;
  progress?.close();
  progress = null;
  if (event.error) await message(tr(event.error[0]), tr(event.error[1]));
  if (event.message) notify(event.message);
  if (event.info) await message(tr(event.info[0]), tr(event.info[1]));
  if (event.offer_initial !== null && event.offer_initial !== undefined && !event.cancelled && !event.error) {
    await offerInitial(event.offer_initial.length ? event.offer_initial : null);
  }
  return event;
}

// ---------------------------------------------------------------------------
// 素材の種類（ask_import_material）とデジタル TP の検査（inspect_digital_import）
// ---------------------------------------------------------------------------

async function askMaterial(previous) {
  const text = `${tr("読み込む素材の種類を選んでください。")}\n\n${tr(
    "デジタル（TP）：ペイントソフトで作った2値の絵。全コマを検査し、アンチエイリアスがあったときだけ、いまある色のまま取り除きます。\nアナログ（スキャン）：紙をスキャンした絵。トーンカーブと色数を決めて2値化します。",
  )}`;
  const buttons = [
    { label: tr("デジタル（TP）"), value: "digital", primary: previous !== "analog" },
    { label: tr("アナログ（スキャン）"), value: "analog", primary: previous === "analog" },
    { label: tr("キャンセル"), value: null, escape: true },
  ];
  const kind = await openDialog({ title: tr("画像の読み込み"), body: el("div", { class: "sh-message" }, text), buttons });
  if (kind) await invoke("import_set_material", { kind });
  return kind;
}

/// 全コマのアンチエイリアスを数え、取り除くか尋ねる。
/// {conversion, antialias_frames}・null（キャンセル）・{error}。
async function inspectDigital(paths) {
  const result = new Promise((resolve) => {
    const off = listen("import-antialias", ({ payload }) => {
      off.then((f) => f());
      resolve(payload);
    });
  });
  openProgress(tr("アンチエイリアスの検査"), tr("全コマを検査しています"));
  try {
    await invoke("import_scan_antialias", { paths });
  } catch (e) {
    progress?.close();
    return { error: String(e) };
  }
  const { found, error, cancelled } = await result;
  progress?.close();
  progress = null;
  if (error) return { error };
  if (cancelled) return null;
  if (!found.length) {
    notify(tr("{count}枚ともアンチエイリアスはありませんでした。そのまま読み込みます。", { count: paths.length }));
    return { conversion: "plain" };
  }
  const details = found
    .map((f) => trf("{index}枚目（{name}）：{count:,}px", { index: f.index, name: f.name, count: f.pixels }))
    .join("\n");
  const body = el(
    "div",
    { class: "sh-message" },
    tr("{frames}/{total}枚にアンチエイリアス（混ざった色・半透明の画素）があります。", {
      frames: found.length,
      total: paths.length,
    }),
    "\n\n",
    tr("取り除くと、混ざった画素だけを、となりにある色面の色へ寄せます。色面の色はそのまま残り、色は増えも減りもしません。"),
    el("details", { style: { marginTop: "8px" } }, el("summary", {}, tr("詳細")), el("pre", { style: { maxHeight: "200px", overflow: "auto" } }, details)),
  );
  const choice = await openDialog({
    title: tr("アンチエイリアスの検査"),
    body,
    buttons: [
      { label: tr("取り除いて読み込む"), value: "remove", primary: true },
      { label: tr("そのまま読み込む"), value: "keep" },
      { label: tr("キャンセル"), value: null, escape: true },
    ],
  });
  if (choice === "keep") return { conversion: "plain" };
  if (choice !== "remove") return null;
  return { conversion: "digital", antialias_frames: found.length };
}

// ---------------------------------------------------------------------------
// 2値化の窓（ColorReductionDialog の読み込みの形）
// ---------------------------------------------------------------------------

/// トーンカーブ（ToneCurveWidget）：両端固定・中間点を足す・動かす・右クリックで消す・
/// ダブルクリックで標準へ戻す。
function toneCurveEditor(onChange) {
  const W = 260;
  const H = 112;
  const pad = 8;
  const canvas = el("canvas", { width: W, height: H, style: { cursor: "crosshair", border: "1px solid var(--border)", borderRadius: "3px" } });
  canvas.title = tr("空いている場所をクリック：点を追加／点をドラッグ：濃さを調整／右クリック：中間点を削除／ダブルクリック：標準へ戻す");
  let points = [[0, 0], [1, 1]];
  let active = -1;
  const toWidget = ([x, y]) => [pad + x * (W - 2 * pad), H - pad - y * (H - 2 * pad)];
  const toPoint = (e) => {
    const r = canvas.getBoundingClientRect();
    const x = ((e.clientX - r.left) * (W / r.width) - pad) / (W - 2 * pad);
    const y = (H - pad - (e.clientY - r.top) * (H / r.height)) / (H - 2 * pad);
    return [Math.max(0, Math.min(1, x)), Math.max(0, Math.min(1, y))];
  };
  const nearest = (e) => {
    const r = canvas.getBoundingClientRect();
    const px = (e.clientX - r.left) * (W / r.width);
    const py = (e.clientY - r.top) * (H / r.height);
    let best = -1;
    let dist = 9;
    points.forEach((p, i) => {
      const [wx, wy] = toWidget(p);
      const d = Math.hypot(wx - px, wy - py);
      if (d <= dist) {
        dist = d;
        best = i;
      }
    });
    return best;
  };
  const rounded = () => points.map(([x, y]) => [Math.round(x * 1e5) / 1e5, Math.round(y * 1e5) / 1e5]);
  const draw = () => {
    const g = canvas.getContext("2d");
    const style = getComputedStyle(document.body);
    g.clearRect(0, 0, W, H);
    g.strokeStyle = style.getPropertyValue("--border") || "#888";
    g.strokeRect(pad, pad, W - 2 * pad, H - 2 * pad);
    g.strokeStyle = style.getPropertyValue("--accent") || "#2f6fed";
    g.lineWidth = 2;
    g.beginPath();
    points.forEach((p, i) => {
      const [x, y] = toWidget(p);
      if (i) g.lineTo(x, y);
      else g.moveTo(x, y);
    });
    g.stroke();
    g.fillStyle = g.strokeStyle;
    for (const p of points) {
      const [x, y] = toWidget(p);
      g.fillRect(x - 3, y - 3, 6, 6);
    }
  };
  const move = (e, finished) => {
    if (active < 0) return;
    let [x, y] = toPoint(e);
    if (active === 0) x = 0;
    else if (active === points.length - 1) x = 1;
    else {
      const lo = points[active - 1][0] + 0.01;
      const hi = points[active + 1][0] - 0.01;
      x = Math.max(lo, Math.min(hi, x));
    }
    points[active] = [x, y];
    draw();
    onChange(rounded(), finished);
  };
  canvas.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    const i = nearest(e);
    if (i > 0 && i < points.length - 1) {
      points.splice(i, 1);
      draw();
      onChange(rounded(), true);
    }
  });
  canvas.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    let i = nearest(e);
    if (i < 0) {
      const p = toPoint(e);
      points.push(p);
      points.sort((a, b) => a[0] - b[0]);
      i = points.indexOf(p);
    }
    active = i;
    canvas.setPointerCapture(e.pointerId);
    move(e, false);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (active >= 0 && e.buttons & 1) move(e, false);
  });
  canvas.addEventListener("pointerup", (e) => {
    if (active < 0) return;
    move(e, true);
    active = -1;
  });
  canvas.addEventListener("dblclick", () => reset());
  const reset = () => {
    points = [[0, 0], [1, 1]];
    draw();
    onChange(rounded(), true);
  };
  // プリセットのトーンカーブ（ToneCurveWidget.setPoints・_normalized_points）。
  const setPoints = (list) => {
    const normalized = [];
    for (const point of list) {
      const [x, y] = point;
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      normalized.push([Math.max(0, Math.min(1, x)), Math.max(0, Math.min(1, y))]);
    }
    normalized.push([0, 0], [1, 1]);
    normalized.sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const [x, y] of normalized) {
      if (merged.length && Math.abs(x - merged[merged.length - 1][0]) < 1e-5) merged[merged.length - 1] = [x, y];
      else merged.push([x, y]);
    }
    if (!merged.length || merged[0][0] > 1e-5) merged.unshift([0, 0]);
    merged[0] = [0, 0];
    if (merged[merged.length - 1][0] < 1 - 1e-5) merged.push([1, 1]);
    merged[merged.length - 1] = [1, 1];
    points = merged;
    draw();
    onChange(rounded(), true);
  };
  requestAnimationFrame(draw);
  return { canvas, reset, setPoints, points: rounded };
}

function imageCanvas(view, offset) {
  const width = view.getUint32(offset, true);
  const height = view.getUint32(offset + 4, true);
  const canvas = el("canvas", { width, height });
  const data = new Uint8ClampedArray(view.buffer, view.byteOffset + offset + 8, width * height * 4);
  canvas.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(data), width, height), 0, 0);
  return { canvas, next: offset + 8 + width * height * 4 };
}

/// 2値化の窓（ColorReductionDialog）。{form, allFrames}（2値化）・"plain"（そのまま読み込む）・
/// null（キャンセル）。
/// - allowWithoutReduction：「そのまま読み込む」を出す（読み込むとき）。偽なら読み込んだあとの
///   レイヤーにかけるとき（ボタンは「2値化」）。
/// - scopeChoice：「範囲」（今のコマ／すべてのコマ）を出す。
/// - scan：「紙の地色とムラを白にそろえる」を出して ON にする（ゴミ取りも ON、#51）。
async function reduceDialog(inspect, frames, { allowWithoutReduction = true, scopeChoice = false, scan = true } = {}) {
  const mode = el("select", {}, el("option", { value: "surface" }, tr("色面抽出")), el("option", { value: "line" }, tr("ライン抽出")));
  mode.value = "line";
  mode.title = tr("色面抽出：塗りの色面を保ちながらアンチエイリアスを除去します。\nライン抽出：白背景と元の線色を保ちながら線画を2値化します。");
  const count = el("input", { type: "number", min: "2", max: "100", value: "8", style: { width: "70px" } });
  const slider = el("input", { type: "range", min: "2", max: "100", value: "8", style: { flex: "1" } });
  const flatten = scan ? el("input", { type: "checkbox", checked: true }) : null;
  const despeckle = el("input", { type: "checkbox" });
  despeckle.checked = scan;
  const despeckleSize = el("input", { type: "number", min: "1", max: "100", value: "3", style: { width: "64px" } });
  despeckleSize.disabled = !despeckle.checked;
  const blur = el("input", { type: "number", min: "0", max: "3", step: "0.5", value: "0", style: { width: "64px" } });
  blur.title = tr("トーンカーブの前に弱いぼかしをかけ、スキャンや JPEG のざらつきをならします。線のふちのギザギザや小さな点が減ります。0 でかけません。");
  const range = scopeChoice
    ? el("select", {}, el("option", { value: "current" }, tr("今のコマ")), el("option", { value: "all" }, tr("すべてのコマ")))
    : null;
  if (range) range.value = "all";
  const toneLabel = el("span", { style: { width: "66px", textAlign: "center" } }, tr("標準"));
  const status = el("span", {}, tr("プレビュー反映済み"));
  const swatches = el("div", { class: "sh-row", style: { gap: "3px" } });
  const addColor = el("button", { type: "button", title: tr("出力する色を足します。") }, "＋");
  const autoPalette = el("button", { type: "button", title: tr("指定した色を捨てて、色数から自動で決めた色に戻します。") }, tr("自動に戻す"));
  autoPalette.disabled = true;
  const paletteRow = el("div", { class: "sh-row", title: tr("左クリックで色を変更、右クリックで削除。指定した色だけで描き直します。指定すると色数は使いません。") }, swatches, addColor, autoPalette);
  const originalTitle = el("div", {}, tr("元画像（トーンカーブ）　100%"));
  const reducedTitle = el("div", {}, tr("2値化　100%"));
  const box = () => el("div", { style: { width: "320px", height: "240px", overflow: "auto", border: "1px solid var(--border)", background: "repeating-conic-gradient(#ccc 0% 25%, #fff 0% 50%) 50% / 16px 16px" } });
  const originalBox = box();
  const reducedBox = box();
  // 二つのプレビューは同じ所を見せる。
  const sync = (from, to) => from.addEventListener("scroll", () => {
    to.scrollLeft = from.scrollLeft;
    to.scrollTop = from.scrollTop;
  });
  sync(originalBox, reducedBox);
  sync(reducedBox, originalBox);
  // ほかの代表フレームも同じ設定で2値化して並べる（#10）。1枚目だけ見て決めると、
  // 中間や末尾のコマで色が潰れていても気づけない。
  const referenceCount = Math.min(3, Math.max(0, frames - 1));
  const referenceBoxes = Array.from({ length: referenceCount }, () =>
    el("div", { style: { height: "140px", border: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "center" } }));
  const referenceStrip = el("div", { class: "sh-row", style: { display: referenceCount ? "flex" : "none", gap: "6px", alignItems: "center" } },
    el("span", {}, tr("ほかの代表フレーム（2値化後）")), ...referenceBoxes);
  let tonePoints = [[0, 0], [1, 1]];
  let palette = null; // プレビューで決めた色
  let custom = null; // 人が指定した出力する色（#62）
  let dirty = true;
  let busy = false;
  let lastForm = null;
  const markDirty = () => {
    dirty = true;
    status.textContent = tr("設定未反映");
    status.style.color = "#a15a00";
    status.style.fontWeight = "bold";
  };
  const tone = toneCurveEditor((points, finished) => {
    tonePoints = points;
    const middle = points.length - 2;
    toneLabel.textContent = middle ? tr("中間点 {count}", { count: middle }) : tr("標準");
    if (finished) markDirty();
  });
  const toneReset = el("button", { type: "button" }, tr("標準"));
  toneReset.addEventListener("click", () => tone.reset());
  count.addEventListener("input", () => {
    slider.value = count.value;
    markDirty();
  });
  slider.addEventListener("input", () => {
    count.value = slider.value;
    markDirty();
  });
  for (const input of [mode, flatten, despeckle, despeckleSize, blur]) input?.addEventListener("change", markDirty);
  despeckle.addEventListener("change", () => (despeckleSize.disabled = !despeckle.checked));

  // 出力する色（set_custom_palette・_refresh_palette_swatches）。
  const hexOf = (c) => "#" + c.map((v) => v.toString(16).padStart(2, "0").toUpperCase()).join("");
  const shown = () => (custom ?? palette ?? []).map((c) => [...c]);
  let setCustom = () => {};
  const drawSwatches = (colors) => {
    swatches.replaceChildren(...colors.map((c, index) => {
      const b = el("button", { type: "button", title: hexOf(c), style: { width: "22px", height: "22px", padding: "0", background: `rgb(${c[0]},${c[1]},${c[2]})`, border: "1px solid #777" } });
      b.addEventListener("click", async () => {
        const picked = await pickColor(c, { title: tr("出力する色") });
        if (!picked) return;
        const colors = shown();
        colors[index] = picked;
        setCustom(colors);
      });
      b.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        const enabled = shown().length > 1;
        showMenu([{ label: tr("この色を使わない"), disabled: !enabled, action: () => {
          const colors = shown();
          if (colors.length <= 1) return;
          colors.splice(index, 1);
          setCustom(colors);
        } }], e.clientX, e.clientY);
      });
      return b;
    }));
  };
  setCustom = (colors) => {
    const unique = [];
    for (const c of colors) {
      const rgb = c.slice(0, 3).map(Number);
      if (!unique.some((u) => u.every((v, i) => v === rgb[i]))) unique.push(rgb);
    }
    custom = unique.length ? unique : null;
    count.disabled = slider.disabled = Boolean(custom);
    autoPalette.disabled = !custom;
    drawSwatches(custom ?? palette ?? []);
    markDirty();
  };
  addColor.addEventListener("click", async () => {
    const colors = shown();
    const picked = await pickColor(colors.length ? colors[colors.length - 1] : [0, 0, 0], { title: tr("出力する色を足す") });
    if (picked) setCustom([...colors, picked]);
  });
  autoPalette.addEventListener("click", () => setCustom([]));

  const form = () => ({
    mode: mode.value,
    color_count: Math.max(2, Math.min(100, Number(count.value) || 8)),
    flatten_paper: Boolean(flatten?.checked),
    blur: Math.max(0, Math.min(3, Number(blur.value) || 0)),
    despeckle: despeckle.checked ? Math.max(1, Math.min(100, Number(despeckleSize.value) || 3)) : 0,
    tone_curve_points: tonePoints,
    custom_palette: custom,
  });

  // プリセット（#10）：方式・色数・トーンカーブ・出力する色・紙の地色・ゴミ取り・ぼかし。
  const presetChoice = el("select", { style: { minWidth: "160px" } });
  const presetSave = el("button", { type: "button", title: tr("今の設定に名前を付けて保存します。") }, tr("保存…"));
  const presetDelete = el("button", { type: "button" }, tr("削除"));
  let presets = {};
  const refreshPresets = async (selected = null) => {
    presets = await invoke("binarize_presets").catch(() => ({}));
    const names = Object.keys(presets).sort();
    presetChoice.replaceChildren(el("option", { value: "" }, tr("（選ぶ）")), ...names.map((n) => el("option", { value: n }, n)));
    presetChoice.value = selected !== null && names.includes(selected) ? selected : "";
    presetDelete.disabled = !names.length;
  };
  const currentSettings = () => {
    const f = form();
    const settings = {
      extraction_mode: f.mode === "line" ? "line" : "surface",
      color_count: f.color_count,
      tone_curve_points: tonePoints.map(([x, y]) => [x, y]),
      palette: custom ? custom.map((c) => [...c]) : null,
    };
    if (flatten) settings.flatten_paper = flatten.checked;
    settings.despeckle = f.despeckle;
    settings.blur = f.blur;
    return settings;
  };
  const isInt = (v) => Number.isInteger(v) && typeof v !== "boolean";
  const applySettings = (settings) => {
    if (["surface", "line"].includes(settings.extraction_mode)) mode.value = settings.extraction_mode;
    if (isInt(settings.color_count)) count.value = slider.value = String(Math.max(2, Math.min(100, settings.color_count)));
    if (Array.isArray(settings.tone_curve_points)) {
      try {
        tone.setPoints(settings.tone_curve_points.map(([x, y]) => [Number(x), Number(y)]));
      } catch {
        // 壊れたトーンカーブは飛ばす（Qt も記録して飛ばす）。
      }
    }
    if (flatten && typeof settings.flatten_paper === "boolean") flatten.checked = settings.flatten_paper;
    if (typeof settings.blur === "number") blur.value = String(Math.max(0, Math.min(3, settings.blur)));
    if (isInt(settings.despeckle)) {
      despeckle.checked = settings.despeckle > 0;
      despeckleSize.disabled = !despeckle.checked;
      if (settings.despeckle > 0) despeckleSize.value = String(Math.max(1, Math.min(100, settings.despeckle)));
    }
    let colors = [];
    try {
      colors = Array.isArray(settings.palette) ? settings.palette.map((c) => c.slice(0, 3).map((v) => Math.trunc(Number(v)))) : [];
    } catch {
      colors = [];
    }
    if (colors.every((c) => c.every((v) => Number.isFinite(v) && v >= 0 && v <= 255))) setCustom(colors);
    markDirty();
  };
  presetChoice.addEventListener("change", () => {
    const settings = presets[presetChoice.value];
    if (settings) applySettings(settings);
  });
  presetSave.addEventListener("click", async () => {
    const name = await prompt(tr("プリセットを保存"), tr("名前："), presetChoice.value || "");
    if (name === null || name === undefined) return;
    const saved = await invoke("binarize_preset_save", { name, settings: currentSettings() }).catch((e) => {
      notify(String(e));
      return null;
    });
    if (saved) await refreshPresets(saved);
  });
  presetDelete.addEventListener("click", async () => {
    if (!presetChoice.value) return;
    await invoke("binarize_preset_delete", { name: presetChoice.value });
    await refreshPresets();
  });
  refreshPresets();

  const previewButton = el("button", { type: "button" }, tr("プレビュー"));
  previewButton.title = tr("現在のトーンカーブと色数を使って2値化プレビューを更新します。");
  const preview = async () => {
    if (busy) return;
    busy = true;
    previewButton.disabled = true;
    status.textContent = tr("近い色相をまとめて代表色を作成しています");
    try {
      const settings = form();
      const buffer = await invoke("import_reduce_preview", { form: settings });
      const view = new DataView(buffer);
      const n = view.getUint32(0, true);
      const semi = view.getUint32(4, true);
      const colors = [];
      for (let i = 0; i < n; i++) colors.push([view.getUint8(8 + i * 4), view.getUint8(9 + i * 4), view.getUint8(10 + i * 4)]);
      const refs = view.getUint32(8 + n * 4, true);
      const original = imageCanvas(view, 12 + n * 4);
      const reduced = imageCanvas(view, original.next);
      originalBox.replaceChildren(original.canvas);
      reducedBox.replaceChildren(reduced.canvas);
      let offset = reduced.next;
      for (let i = 0; i < refs; i++) {
        const image = imageCanvas(view, offset);
        offset = image.next;
        image.canvas.style.height = "140px";
        image.canvas.style.width = "auto";
        referenceBoxes[i]?.replaceChildren(image.canvas);
      }
      palette = colors;
      drawSwatches(custom ?? palette);
      reducedTitle.textContent = tr("{name}　100%（{actual}色／半透明 {transparent}px）", {
        name: mode.selectedOptions[0].textContent,
        actual: n,
        transparent: semi,
      });
      settings.palette = colors;
      lastForm = settings;
      dirty = false;
      status.textContent = tr("プレビュー反映済み");
      status.style.color = "#28744a";
      status.style.fontWeight = "bold";
    } catch (e) {
      markDirty();
      await message(tr("2値化"), String(e));
    } finally {
      busy = false;
      previewButton.disabled = false;
    }
  };
  previewButton.addEventListener("click", preview);
  const note = inspect.background
    ? tr("白背景を自動検出")
    : trf("半透明 {count:,}px", { count: inspect.semi_transparent });
  const summary = el(
    "div",
    { class: "sh-message", style: { maxWidth: "660px" } },
    trf("代表{frames}枚：1枚目{count:,}色／{note}　トーンカーブを元画像へ先に適用し、その後に色数を調整して2値化します。", {
      frames,
      count: inspect.color_count,
      note,
    }),
  );
  const rows = [
    el("span", { class: "sh-label" }, tr("2値化方式")), mode,
    el("span", { class: "sh-label" }, tr("プリセット")), el("div", { class: "sh-row" }, presetChoice, presetSave, presetDelete),
    el("span", { class: "sh-label" }, tr("色数")), el("div", { class: "sh-row" }, slider, count, el("span", {}, tr(" 色"))),
    el("span", { class: "sh-label" }, tr("出力する色")), paletteRow,
  ];
  if (flatten) {
    const label = el("label", { class: "sh-check" }, flatten, tr("紙の地色とムラを白にそろえる"));
    label.title = tr("スキャンした紙のクリーム色・照明のムラ・影を、場所ごとに見積もって白へそろえてから2値化します。鉛筆の濃淡は、紙との比のまま残ります。");
    rows.push(el("span", { class: "sh-label" }, tr("スキャン")), label);
  }
  const despeckleLabel = el("label", { class: "sh-check" }, despeckle, tr("離れた小さな点を消す"));
  despeckleLabel.title = tr("2値化のあとで、ほかの絵に触れていない小さな点（スキャンのゴミなど）を消します。線や色面にくっついた点は消しません。");
  rows.push(el("span", { class: "sh-label" }, tr("ゴミ取り")), el("div", { class: "sh-row" }, despeckleLabel, despeckleSize, el("span", {}, tr(" px以下"))));
  if (range) rows.push(el("span", { class: "sh-label" }, tr("範囲")), range);
  rows.push(
    el("span", { class: "sh-label" }, tr("ぼかし")), el("div", { class: "sh-row" }, blur, el("span", {}, tr(" px"))),
    el("span", { class: "sh-label" }, tr("トーンカーブ")), el("div", { class: "sh-row" }, tone.canvas, toneLabel, toneReset),
  );
  const body = el(
    "div",
    { style: { display: "flex", flexDirection: "column", gap: "8px" } },
    summary,
    el("div", { class: "sh-row", style: { alignItems: "flex-start" } },
      el("div", {}, originalTitle, originalBox),
      el("div", {}, reducedTitle, reducedBox)),
    referenceStrip,
    el("div", { class: "sh-form" }, ...rows),
    el("div", { style: { fontSize: "10px" } }, tr("色数の変更中はプレビューを更新しません。トーンカーブ操作中は元画像へ一時適用し、離すと直前の2値化プレビューへ戻ります。")),
    el("div", { class: "sh-row" }, status, previewButton),
  );
  const buttons = [
    {
      label: allowWithoutReduction ? tr("全コマを2値化") : tr("2値化"),
      value: "reduce",
      primary: true,
      validate: async () => {
        if (dirty || !palette) {
          await message(tr("2値化"), tr("トーンカーブまたは色数が未反映です。\n先に下の「プレビュー」を押してください。"));
          return false;
        }
        return true;
      },
    },
  ];
  if (allowWithoutReduction) buttons.push({ label: tr("そのまま読み込む"), value: "plain" });
  buttons.push({ label: tr("キャンセル"), value: null, escape: true });
  const result = openDialog({ title: tr("2値化"), body, buttons });
  preview();
  const choice = await result;
  if (choice === "reduce") return { form: lastForm, allFrames: range ? range.value === "all" : true };
  return choice;
}

/// 2値化の準備（prepare_color_reduction）。{conversion, form?, antialias_frames?}・null（キャンセル）・{error}。
async function prepareReduction(paths) {
  let inspect;
  try {
    inspect = await invoke("import_inspect", { paths });
  } catch (e) {
    return { error: String(e) };
  }
  const material = await askMaterial(inspect.material);
  if (!material) return null;
  if (material === "digital") return inspectDigital(inspect.paths);
  if (!inspect.needs_reduction) return { conversion: "plain" };
  const choice = await reduceDialog(inspect, inspect.frames);
  if (!choice) return null;
  if (choice === "plain") return { conversion: "plain" };
  return { conversion: "reduce", form: choice.form };
}

// ---------------------------------------------------------------------------
// 色塗り前の初期状態（offer_after_import）
// ---------------------------------------------------------------------------

async function offerInitial(layers) {
  const [pending, always] = await invoke("import_initial_pending", { layers });
  if (!pending) return false;
  const keep = async (alwaysOn) => {
    try {
      notify(await invoke("import_keep_initial", { layers, always: alwaysOn }));
      return true;
    } catch (e) {
      notify(String(e));
      return false;
    }
  };
  if (always) return keep(false);
  const check = el("input", { type: "checkbox" });
  const label = el("label", { class: "sh-check", title: tr("環境設定の「一般」から外せます。") }, check, tr("今後、読み込んだ絵は常に初期状態として保存する"));
  const body = el(
    "div",
    {},
    el("div", { class: "sh-message" }, tr("読み込んだ絵を、色塗り前の初期状態として保存しますか？"), "\n\n",
      tr("保存すると自動色塗りの精度が上がります。自動塗りは初期状態の線で部分を分けるので、塗りかけの色に惑わされません。キャンバス下のボタンで初期状態と見比べることもできます。")),
    el("div", { style: { marginTop: "10px" } }, label),
  );
  const answer = await openDialog({
    title: tr("色塗り前の初期状態"),
    body,
    buttons: [
      { label: tr("保存する"), value: true, primary: true },
      { label: tr("保存しない"), value: false, escape: true },
    ],
  });
  return answer ? keep(check.checked) : false;
}

// ---------------------------------------------------------------------------
// メニューの項目
// ---------------------------------------------------------------------------

/// import_dropped_image・import_dropped_images（genga・layer_name つき）。
async function importPaths(paths, { layerName = null, genga = false, sequence = false } = {}) {
  // キャンバスへ落とした画像は、1枚でも import_dropped_images（連番画像読み込み）。
  const single = !sequence && paths.length === 1 && !layerName && !genga;
  const title = single ? tr("画像読み込み") : tr("連番画像読み込み");
  const reduction = await prepareReduction(paths);
  if (!reduction) return;
  if (reduction.error) {
    const text = single
      ? tr("{Path} を読み込めませんでした。\n\n{error}", { Path: paths[0].split(/[\\/]/).pop(), error: reduction.error })
      : tr("画像を読み込めませんでした。\n\n{error}", { error: reduction.error });
    await message(title, text);
    return;
  }
  await runJob(title, tr("画像を読み込んでいます"), () =>
    invoke("import_images", {
      request: {
        paths,
        conversion: reduction.conversion,
        form: reduction.form ?? null,
        layer_name: layerName,
        genga,
        title,
        single,
        antialias_frames: reduction.antialias_frames ?? 0,
      },
    }),
  );
}

async function importImages() {
  const picked = await invoke("import_pick", { kind: "images", title: tr("画像を読み込む") });
  if (picked?.paths?.length) await importPaths(picked.paths);
}

async function importGenga() {
  const picked = await invoke("import_pick", { kind: "images", title: tr("原画を読み込む") });
  if (picked?.paths?.length) await importPaths(picked.paths, { layerName: tr("原画"), genga: true });
}

async function importFolder() {
  const title = tr("画像フォルダーを読み込む");
  const picked = await invoke("import_pick", { kind: "folder", title });
  if (!picked) return;
  if (!picked.paths.length) {
    await message(title, tr("選択したフォルダーに対応画像がありません。\n\n対応形式：PNG、JPEG、TGA"));
    return;
  }
  await importPaths(picked.paths, { layerName: picked.name });
}

async function importRaw() {
  const title = tr("変換せず読み込む");
  const picked = await invoke("import_pick", { kind: "images", title: tr("変換せず読み込む（下書きレイヤー）") });
  if (!picked?.paths?.length) return;
  await runJob(title, tr("画像を読み込んでいます"), () =>
    invoke("import_images", {
      request: { paths: picked.paths, conversion: "draft", title, single: false },
    }),
  );
}

/// ImportController.psd_dialog：最上位のレイヤーごとに列を足す。
async function importPsd() {
  const picked = await invoke("import_pick", { kind: "psd", title: tr("PSDを読み込む") });
  const path = picked?.paths?.[0];
  if (!path) return;
  await runJob(tr("PSD読み込み"), tr("PSDを読み込んでいます"), () => invoke("import_psd", { path }));
}

/// InitialStateController.import_line_art_dialog。
async function importInitialState() {
  const title = tr("線画カットフォルダーを初期状態として読み込む");
  const picked = await invoke("import_pick", { kind: "line_art_folder", title });
  const path = picked?.paths?.[0];
  if (!path) return;
  await runJob(title, tr("線画の初期状態を読み込んでいます…"), () => invoke("import_initial_state", { path }));
}

// ---------------------------------------------------------------------------
// CLIP STUDIO アニメーション（ImportController.clip_animation・app/src/imports_clip.rs）
// ---------------------------------------------------------------------------

let hooks = {}; // {save}：先に保存する（shell.js の「保存」）。

/// 置き換える前に尋ねる（_confirm_replace_document）。続けてよければ true。
async function confirmReplace(
  title = tr("CLIP STUDIOアニメーションを読み込む"),
  text = tr("現在のキャンバスをCLIP STUDIOアニメーションで置き換えます。\n先に現在のプロジェクトを保存しますか？"),
) {
  if (!(await invoke("import_clip_needs_confirm"))) return true;
  const answer = await message(
    title,
    text,
    [
      [tr("保存する"), "save", true],
      [tr("保存せず読み込む"), "discard"],
      [tr("キャンセル"), "cancel"],
    ],
    "cancel",
  );
  if (answer === "save") return Boolean(await hooks.save?.());
  return answer === "discard";
}

/// clip_animation_dialog：読んでから置き換えを確かめ、今の文書を置き換える。
async function importClip() {
  const picked = await invoke("import_pick", { kind: "clip", title: tr("CLIP STUDIOアニメーションを読み込む") });
  const path = picked?.paths?.[0];
  if (path) await importClipPath(path);
}

/// ImportController.clip_animation（メニューからも、落としたファイルからも）。
async function importClipPath(path) {
  // Qt は .clip でなければ何もしない。
  if (!/\.clip$/i.test(path)) return;
  const title = tr("CLIP STUDIOアニメーション読み込み");
  const event = await runJob(title, tr("CLIP STUDIOアニメーションを解析しています…"), () => invoke("import_clip", { path }));
  if (!event || event.error || event.cancelled) return;
  if (!(await confirmReplace())) {
    await invoke("import_clip_discard");
    return;
  }
  try {
    notify(await invoke("import_clip_apply"));
  } catch (e) {
    await message(title, String(e));
  }
}

/// InitialStateController.import_clip_line_art_dialog。
async function importClipInitialState() {
  const title = tr("CLIPの線画を初期状態として読み込む");
  const picked = await invoke("import_pick", { kind: "clip", title });
  const path = picked?.paths?.[0];
  if (!path) return;
  await runJob(title, tr("線画の初期状態を読み込んでいます…"), () => invoke("import_clip_initial_state", { path }));
}

// ---------------------------------------------------------------------------
// 【Group B】カットフォルダーを開く（ImportController.cut_folder・app/src/imports_layer.rs）
// ---------------------------------------------------------------------------

async function openCutFolder() {
  const title = tr("カットフォルダーを開く");
  const picked = await invoke("import_pick", { kind: "line_art_folder", title });
  const path = picked?.paths?.[0];
  if (!path) return;
  const event = await runJob(title, tr("カットフォルダーを読み込んでいます：{name}", { name: path.split(/[\\/]/).pop() }), () =>
    invoke("import_cut_folder", { path }),
  );
  if (!event || event.error || event.cancelled) return;
  if (!(await confirmReplace(title, tr("現在のキャンバスをカットフォルダーの内容で置き換えます。\n先に現在のプロジェクトを保存しますか？")))) {
    await invoke("import_cut_folder_discard");
    return;
  }
  let applied;
  try {
    applied = await invoke("import_cut_folder_apply");
  } catch (e) {
    await message(title, String(e));
    return;
  }
  notify(applied.message);
  if (applied.notes.length) await message(title, `${applied.message}\n\n${applied.notes.join("\n")}`);
  await offerInitial(null);
}

// ---------------------------------------------------------------------------
// 【Group B】読み込んだあとの2値化・再変換（binarize_layer_dialog・reconvert_layer_dialog）
// ---------------------------------------------------------------------------

async function binarizeLayer() {
  let inspect;
  try {
    inspect = await invoke("layer_binarize_inspect");
  } catch (e) {
    await message(tr("2値化"), String(e));
    return;
  }
  if (!inspect) {
    notify(tr("2値化：このコマには絵がありません。"));
    return;
  }
  const choice = await reduceDialog(inspect, inspect.frames, { allowWithoutReduction: false, scopeChoice: true, scan: false });
  if (!choice || choice === "plain") return;
  await runJob(tr("2値化"), tr("2値化しています"), () =>
    invoke("layer_binarize_run", { form: choice.form, allFrames: choice.allFrames }),
  );
}

async function reconvertLayer() {
  const title = tr("再変換");
  const check = await invoke("layer_reconvert_check");
  if (check.status) {
    notify(check.status);
    return;
  }
  if (check.warning) {
    await message(title, check.warning);
    return;
  }
  const yes = await message(
    title,
    tr("元のファイル{count}枚から変換し直します。読み込んだあとに描き足した分は元に戻ります（Undo で戻せます）。続けますか？", { count: check.paths.length }),
    [
      [tr("はい"), true],
      [tr("いいえ"), false, true],
    ],
    false,
  );
  if (!yes) return;
  const reduction = await prepareReduction(check.paths);
  if (!reduction) return;
  if (reduction.error) {
    await message(title, reduction.error);
    return;
  }
  await runJob(title, tr("画像を読み込んでいます"), () =>
    invoke("layer_reconvert_run", {
      request: {
        paths: check.paths,
        conversion: reduction.conversion,
        form: reduction.form ?? null,
        title,
        single: false,
        antialias_frames: reduction.antialias_frames ?? 0,
      },
    }),
  );
}

// ---------------------------------------------------------------------------
// 【Group B】ファイルを落とす（MainWindow.dropEvent・CanvasWidget.dropEvent・タイムライン）
// ---------------------------------------------------------------------------

const suffix = (p) => {
  const name = p.split(/[\\/]/).pop();
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot).toLowerCase();
};

/// TimeRemapController.open_dropped：.xdts／.xtds だけを貼り付けの窓へ。
async function openDroppedRemap(path) {
  if (![".xdts", ".xtds"].includes(suffix(path))) return;
  let text;
  try {
    text = await invoke("import_read_timesheet", { path });
  } catch (e) {
    await message(tr("XDTS読み込み"), String(e));
    return;
  }
  await showPasteDialog(text);
}

/// 落としたファイルを振り分ける。キャンバスの上なら .pman → .clip → タイムシート → 画像、
/// それ以外（窓・タイムライン）なら .pman → .xdts／.xtds → .clip（画像は受けない）。
export async function handleDrop(paths, onCanvas) {
  const first = (list) => paths.find((p) => list.includes(suffix(p)));
  const project = first([".pman"]);
  if (project) return hooks.openProject?.(project);
  if (onCanvas) {
    const clip = first([".clip"]);
    if (clip) return importClipPath(clip);
    const remap = first([".xdts", ".xtds", ".tdts"]);
    if (remap) return openDroppedRemap(remap);
    const images = paths.filter((p) => [".jpg", ".jpeg", ".png", ".tga"].includes(suffix(p)));
    if (images.length) return importPaths(images, { sequence: true });
    return;
  }
  const remap = first([".xdts", ".xtds"]);
  if (remap) return openDroppedRemap(remap);
  const clip = first([".clip"]);
  if (clip) return importClipPath(clip);
}

const ACTIONS = {
  a_open_cut_folder: openCutFolder, // 【Group B】
  a_binarize_layer: binarizeLayer, // 【Group B】
  a_reconvert_layer: reconvertLayer, // 【Group B】
  a_import_clip: importClip,
  a_import_clip_initial_state: importClipInitialState,
  a_import_images: importImages,
  a_import_initial_state: importInitialState,
  a_import_psd: importPsd,
  a_import_genga: importGenga,
  a_import_folder: importFolder,
  a_import_images_raw: importRaw,
};

/// 読み込みの画面を組み込む。`save` は先に保存する関数（CLIP の置き換えの確かめ）。
export function installImports({ save, openProject } = {}) {
  hooks = { save, openProject };
  listen("import-job", ({ payload }) => {
    if (payload.phase === "progress") {
      onProgress(payload);
      return;
    }
    const resolve = waiting;
    waiting = null;
    resolve?.(payload);
  });
  window.addEventListener("pma-action", (e) => {
    const f = ACTIONS[e.detail?.id];
    if (!f) return;
    e.preventDefault();
    Promise.resolve(f()).catch((err) => notify(String(err)));
  });
}
