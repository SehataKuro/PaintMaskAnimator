// 読み込みの画面側（計算は app/src/imports.rs → pma-core）。
//
// メニューの「読み込み」の各項目（Qt の import_images_dialog・import_image_folder_dialog・
// import_images_raw_dialog・import_genga_dialog・ImportController.psd_dialog・
// clip_animation_dialog・InitialStateController.import_line_art_dialog など）。
// ファイルを選び、素材の種類（デジタル／アナログ）と2値化を尋ね、進み具合と中止の窓を出す。
// 読み込みは Rust の別のスレッドで行い、終わると `import-job` の事件が来る。
//
// app.js が installImports で組み込む。メニューの項目は `pma-action` の事件で受ける。
import { el, openDialog, message } from "./dialogs.js";
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
  const total = Math.max(1, event.total);
  progress.bar.max = total;
  progress.bar.value = Math.max(0, Math.min(total, event.value));
  progress.text.textContent = event.label;
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
  requestAnimationFrame(draw);
  return { canvas, reset, points: rounded };
}

function imageCanvas(view, offset) {
  const width = view.getUint32(offset, true);
  const height = view.getUint32(offset + 4, true);
  const canvas = el("canvas", { width, height });
  const data = new Uint8ClampedArray(view.buffer, view.byteOffset + offset + 8, width * height * 4);
  canvas.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(data), width, height), 0, 0);
  return { canvas, next: offset + 8 + width * height * 4 };
}

/// 2値化の窓。{form}（2値化）・"plain"（そのまま読み込む）・null（キャンセル）。
async function reduceDialog(inspect, frames) {
  const mode = el("select", {}, el("option", { value: "surface" }, tr("色面抽出")), el("option", { value: "line" }, tr("ライン抽出")));
  mode.title = tr("色面抽出：塗りの色面を保ちながらアンチエイリアスを除去します。\nライン抽出：白背景の線画から線を取り出し、背景を透明にします。");
  const count = el("input", { type: "number", min: "2", max: "100", value: "8", style: { width: "70px" } });
  const slider = el("input", { type: "range", min: "2", max: "100", value: "8", style: { flex: "1" } });
  const flatten = el("input", { type: "checkbox", checked: true });
  const despeckle = el("input", { type: "checkbox", checked: true });
  const despeckleSize = el("input", { type: "number", min: "1", max: "100", value: "3", style: { width: "64px" } });
  const blur = el("input", { type: "number", min: "0", max: "3", step: "0.5", value: "0", style: { width: "64px" } });
  const toneLabel = el("span", { style: { width: "66px", textAlign: "center" } }, tr("標準"));
  const status = el("span", {}, tr("プレビュー反映済み"));
  const swatches = el("div", { class: "sh-row" });
  const originalTitle = el("div", {}, tr("元画像（トーンカーブ）　100%"));
  const reducedTitle = el("div", {}, tr("2値化　100%"));
  const originalBox = el("div", { style: { width: "320px", height: "240px", overflow: "auto", border: "1px solid var(--border)", background: "repeating-conic-gradient(#ccc 0% 25%, #fff 0% 50%) 50% / 16px 16px" } });
  const reducedBox = el("div", { style: { width: "320px", height: "240px", overflow: "auto", border: "1px solid var(--border)", background: "repeating-conic-gradient(#ccc 0% 25%, #fff 0% 50%) 50% / 16px 16px" } });
  // 二つのプレビューは同じ所を見せる。
  const sync = (from, to) => from.addEventListener("scroll", () => {
    to.scrollLeft = from.scrollLeft;
    to.scrollTop = from.scrollTop;
  });
  sync(originalBox, reducedBox);
  sync(reducedBox, originalBox);
  let tonePoints = [[0, 0], [1, 1]];
  let palette = null;
  let dirty = true;
  let busy = false;
  const markDirty = () => {
    dirty = true;
    status.textContent = tr("設定未反映");
    status.style.color = "";
    status.style.fontWeight = "";
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
  for (const input of [mode, flatten, despeckle, despeckleSize, blur]) input.addEventListener("change", markDirty);
  despeckle.addEventListener("change", () => (despeckleSize.disabled = !despeckle.checked));
  const form = () => ({
    mode: mode.value,
    color_count: Math.max(2, Math.min(100, Number(count.value) || 8)),
    flatten_paper: flatten.checked,
    blur: Math.max(0, Math.min(3, Number(blur.value) || 0)),
    despeckle: despeckle.checked ? Math.max(1, Math.min(100, Number(despeckleSize.value) || 3)) : 0,
    tone_curve_points: tonePoints,
  });
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
      const original = imageCanvas(view, 8 + n * 4);
      const reduced = imageCanvas(view, original.next);
      originalBox.replaceChildren(original.canvas);
      reducedBox.replaceChildren(reduced.canvas);
      swatches.replaceChildren(
        ...colors.map((c) => el("span", { class: "sh-swatch", style: { width: "22px", height: "18px", display: "inline-block", background: `rgb(${c[0]},${c[1]},${c[2]})` } })),
      );
      reducedTitle.textContent = tr("{name}　100%（{actual}色／半透明 {transparent}px）", {
        name: mode.selectedOptions[0].textContent,
        actual: n,
        transparent: semi,
      });
      palette = colors;
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
  let lastForm = null;
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
  const body = el(
    "div",
    { style: { display: "flex", flexDirection: "column", gap: "8px" } },
    summary,
    el("div", { class: "sh-row", style: { alignItems: "flex-start" } },
      el("div", {}, originalTitle, originalBox),
      el("div", {}, reducedTitle, reducedBox)),
    el("div", { class: "sh-form" },
      el("span", { class: "sh-label" }, tr("2値化方式")), mode,
      el("span", { class: "sh-label" }, tr("色数")), el("div", { class: "sh-row" }, slider, count, el("span", {}, tr(" 色"))),
      el("span", { class: "sh-label" }, tr("出力する色")), swatches,
      el("span", { class: "sh-label" }, tr("スキャン")), el("label", { class: "sh-check" }, flatten, tr("紙の地色とムラを白にそろえる")),
      el("span", { class: "sh-label" }, tr("ゴミ取り")), el("div", { class: "sh-row" }, el("label", { class: "sh-check" }, despeckle, tr("離れた小さな点を消す")), despeckleSize, el("span", {}, tr(" px以下"))),
      el("span", { class: "sh-label" }, tr("ぼかし")), el("div", { class: "sh-row" }, blur, el("span", {}, tr(" px"))),
      el("span", { class: "sh-label" }, tr("トーンカーブ")), el("div", { class: "sh-row" }, tone.canvas, toneLabel, toneReset)),
    el("div", { class: "sh-row" }, status, previewButton),
  );
  const buttons = [
    {
      label: tr("2値化"),
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
    { label: tr("そのまま読み込む"), value: "plain" },
    { label: tr("キャンセル"), value: null, escape: true },
  ];
  const result = openDialog({ title: tr("2値化"), body, buttons });
  preview();
  const choice = await result;
  if (choice === "reduce") return { form: lastForm };
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
async function importPaths(paths, { layerName = null, genga = false } = {}) {
  const single = paths.length === 1 && !layerName && !genga;
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

const ACTIONS = {
  a_import_images: importImages,
  a_import_genga: importGenga,
  a_import_folder: importFolder,
  a_import_images_raw: importRaw,
};

/// 読み込みの画面を組み込む。
export function installImports() {
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
