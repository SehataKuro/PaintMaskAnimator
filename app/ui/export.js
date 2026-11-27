// 書き出し（Qt 版の main_window_export.py・cut_folder_dialog.py・MainWindow.save_png／save_tga・
// TimeRemapController.copy_from_timeline の画面）。Rust の側は export.rs。
//
// メニューの項目は shell.js が `pma-action` の事件で窓へ出す。ここで受ける項目は
// preventDefault して自分で処理する。窓の言葉・題名・既定のファイル名は Qt と同じ。
// 時間のかかる書き出しは Rust が別のスレッドで書き、`export-job` の事件で進み具合を送る
// （Qt と違って中止できる）。書き出しは文書を変えないので、取り消しの履歴には残らない。
//
// 影素材の書き出しは、使用色パネルのフォルダー（名前と色）を `pma-used-color-folders` の
// 事件で尋ねる。使用色パネルが detail.folders = [{name, colors: [[r, g, b], …]}] を入れる。

import { el, openDialog, message, ask, prompt, toast } from "./dialogs.js";
import { tr } from "./i18n.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

// ---------------------------------------------------------------------------
// 共通
// ---------------------------------------------------------------------------

const info = (title, text) => message(title, text);
const warn = (title, text) => message(title, text);
const fail = (title, text) => message(title, text);
const status = (text) => toast(text);

/// 拡張子がなければ足す（Qt の `if not path.lower().endswith(ext): path += ext`）。
function withExtension(path, extension) {
  return path.toLowerCase().endsWith(extension) ? path : path + extension;
}

function pickSave(title, initial, filter, extensions) {
  return invoke("export_pick_save", { title, initial, filter, extensions });
}

function pickFolder(title, start = null) {
  return invoke("export_pick_folder", { title, start });
}

// 進み具合の窓（Qt の create_counter。中止ボタン付き）。
let job = null; // {bar, text, labelFor, resolve, close}

function runJob(command, args, { title, initial, labelFor }) {
  return new Promise((resolve, reject) => {
    const bar = el("progress", { max: "1", value: "0", style: { width: "100%" } });
    const text = el("div", { class: "sh-message", style: { minWidth: "330px" } }, initial);
    const buttons = [{ label: tr("中止"), value: "cancel", escape: true }];
    const closed = openDialog({ title, body: el("div", {}, text, bar), buttons });
    const state = {
      bar,
      text,
      labelFor,
      resolve,
      close: () => buttons[0].element?.closest(".sh-dialog")?.close?.("closed"),
    };
    closed.then((value) => {
      if (value === "cancel") invoke("export_cancel");
    });
    job = state;
    invoke(command, args).catch((error) => {
      state.close();
      job = null;
      reject(error);
    });
  });
}

function onJob(event) {
  if (!job) return;
  if (event.phase === "progress") {
    const total = Math.max(1, event.total);
    const value = Math.max(0, Math.min(total, event.value));
    job.bar.max = total;
    job.bar.value = value;
    const label = job.labelFor(event);
    job.text.textContent = event.stage === "encode" ? label : `${label}\n${value} / ${total}`;
    return;
  }
  const current = job;
  job = null;
  current.close();
  current.resolve(event);
}

const itemLabel = (event) => tr("{number}枚目を書き出しています", { number: Math.min(event.value + 1, event.total) });

// ---------------------------------------------------------------------------
// 今のコマ（PNG・TGA）
// ---------------------------------------------------------------------------

async function saveFrame(format) {
  const png = format === "png";
  const title = png ? tr("PNG保存") : tr("TGA保存");
  const picked = await pickSave(title, png ? "frame.png" : "frame.tga", png ? "PNG" : "TGA", [format]);
  if (!picked) return;
  const path = withExtension(picked, `.${format}`);
  try {
    await invoke("export_frame", { format, path });
  } catch (error) {
    // Qt は PNG の失敗は黙る。TGA は Pillow がないときに知らせる。ここでは両方知らせる。
    await warn(title, png ? String(error) : `${tr("TGAを保存できませんでした。Pillowの導入を確認してください。")}\n\n${error}`);
  }
}

// ---------------------------------------------------------------------------
// XDTS
// ---------------------------------------------------------------------------

async function exportXdts() {
  const title = tr("XDTS書き出し");
  const picked = await pickSave(tr("XDTSタイムシートを書き出す"), "PaintMaskAnimator.xdts", tr("XDTSタイムシート (*.xdts)"), ["xdts"]);
  if (!picked) return;
  const path = withExtension(picked, ".xdts");
  try {
    await invoke("export_xdts", { path });
  } catch (error) {
    await fail(title, String(error));
    return;
  }
  await info(title, tr("タイムシートを書き出しました。\n\n{path}", { path }));
}

// ---------------------------------------------------------------------------
// PSD
// ---------------------------------------------------------------------------

async function exportPsd() {
  const title = tr("PSD書き出し");
  const picked = await pickSave(tr("PSDを書き出す"), "PaintMaskAnimator.psd", "Photoshop Document (*.psd)", ["psd"]);
  if (!picked) return;
  const path = withExtension(picked, ".psd");
  let event;
  try {
    event = await runJob("export_psd_start", { path }, { title, initial: title, labelFor: itemLabel });
  } catch (error) {
    await fail(title, tr("PSDを書き出せませんでした。\n\n{exc}", { exc: String(error) }));
    return;
  }
  if (event.cancelled) return;
  if (event.error !== null && event.error !== undefined) {
    await fail(title, tr("PSDを書き出せませんでした。\n\n{exc}", { exc: event.error }));
    return;
  }
  if (event.result?.no_keys) {
    await fail(title, tr("PSDを書き出せませんでした。\n\n{exc}", { exc: tr("書き出せるキーフレームがありません。") }));
    return;
  }
  await info(title, tr("{keys}個のキーフレームを書き出しました。\n\n{path}", { keys: event.result.keys, path }));
}

// ---------------------------------------------------------------------------
// 連番＋CSV
// ---------------------------------------------------------------------------

async function exportKeySequence(format) {
  const picked = await pickSave(
    tr("連番{format}＋CSVの書き出しフォルダー", { format }),
    `PaintMaskAnimator_${format}_CSV`,
    tr("フォルダー名："),
    [],
  );
  if (!picked) return;
  let target;
  try {
    target = await invoke("export_key_sequence_target", { path: picked });
  } catch (error) {
    if (String(error) === "bad_name") await warn(tr("フォルダー名"), tr("使用できるフォルダー名を指定してください。"));
    else await fail(tr("フォルダー作成エラー"), tr("書き出しフォルダーを作成できません。\n\n{exc}", { exc: String(error) }));
    return;
  }
  if (target.state === "file") {
    await warn(tr("書き出し先"), tr("同じ名前のファイルが存在するため、フォルダーを作成できません。"));
    return;
  }
  if (target.state === "not_empty") {
    const ok = await ask(tr("同名フォルダー"), tr("「{name}」には既存のファイルがあります。\nこのフォルダーへ書き出しますか？", { name: target.folder_name }));
    if (!ok) return;
  }
  const title = tr("連番{format}書き出し", { format });
  let event;
  try {
    event = await runJob(
      "export_key_sequence_start",
      { destination: target.destination, format },
      { title, initial: tr("「{name}」へ書き出しています", { name: target.folder_name }), labelFor: itemLabel },
    );
  } catch (error) {
    await fail(tr("フォルダー作成エラー"), tr("書き出しフォルダーを作成できません。\n\n{exc}", { exc: String(error) }));
    return;
  }
  if (event.cancelled) return;
  if (event.error !== null && event.error !== undefined) {
    await fail(tr("連番書き出しエラー"), tr("書き出し中にエラーが発生しました。\n\n{exc}", { exc: event.error }));
    return;
  }
  if (event.result.no_keys) {
    await warn(tr("連番書き出し"), tr("書き出せるキーフレームがありません。"));
    return;
  }
  status(tr("「{name}」へ{len}枚とTS.csvを書き出しました。", { name: target.folder_name, len: event.result.count }));
  await info(
    tr("連番書き出し完了"),
    tr("次の構成で書き出しました。\n\n{destination}\n├─ 各レイヤー名のフォルダー\n│  └─ レイヤー名0001...\n└─ TS.csv", { destination: target.destination }),
  );
}

// ---------------------------------------------------------------------------
// 影素材
// ---------------------------------------------------------------------------

/// 使用色パネルのフォルダー（色の入ったものだけ）。答えるパネルがなければ空。
function usedColorFolders() {
  const detail = { folders: null };
  window.dispatchEvent(new CustomEvent("pma-used-color-folders", { detail }));
  return (detail.folders ?? []).filter((f) => f && f.name && f.colors?.length);
}

async function chooseItem(title, label, items) {
  const select = el("select", { style: { minWidth: "220px" } });
  for (const item of items) select.append(el("option", { value: item }, item));
  const body = el("div", { class: "sh-form" }, el("span", { class: "sh-label" }, label), select);
  const ok = await openDialog({
    title,
    body,
    buttons: [
      { label: tr("OK"), value: true, primary: true },
      { label: tr("キャンセル"), value: false, escape: true },
    ],
    initialFocus: () => select,
  });
  return ok ? select.value : null;
}

async function exportShadow() {
  const title = tr("影素材の書き出し");
  const folders = usedColorFolders();
  if (!folders.length) {
    await warn(title, tr("色の入った使用色フォルダーがありません。影色をフォルダーにまとめてから実行してください。"));
    return;
  }
  const name = await chooseItem(title, tr("影色をまとめたフォルダー："), folders.map((f) => f.name));
  if (!name) return;
  const destination = await pickFolder(tr("書き出し先のフォルダー"));
  if (!destination) return;
  const colors = folders.find((f) => f.name === name).colors;
  let event;
  try {
    event = await runJob(
      "export_shadow_start",
      { destination, colors },
      { title, initial: tr("「{name}」の影素材を書き出しています", { name }), labelFor: itemLabel },
    );
  } catch (error) {
    await fail(title, tr("書き出し中にエラーが発生しました。\n\n{exc}", { exc: String(error) }));
    return;
  }
  if (event.cancelled) return;
  if (event.error !== null && event.error !== undefined) {
    await fail(title, tr("書き出し中にエラーが発生しました。\n\n{exc}", { exc: event.error }));
    return;
  }
  if (event.result.nothing) {
    status(tr("書き出せるセルがありません。"));
    return;
  }
  status(tr("影素材を{count}枚書き出しました：{destination}", { count: event.result.count, destination }));
}

// ---------------------------------------------------------------------------
// MP4
// ---------------------------------------------------------------------------

const LEADING_FONT_SCALE = 0.07;
const LEADING_MARGIN_SCALE = 0.04;

/// 頭に重ねるカット名を描いて Rust へ渡す（Qt の movie_slate.draw_cut_name と同じ置き方：
/// 太字、高さの 7%、左下、余白は高さの 4%、黒の縁取り8方向に白）。
async function sendSlate(width, height, name) {
  name = (name ?? "").trim();
  if (!name || !width || !height) {
    await invoke("export_slate", new Uint8Array(0));
    return;
  }
  const size = Math.max(12, Math.floor(height * LEADING_FONT_SCALE));
  const margin = Math.max(6, Math.floor(height * LEADING_MARGIN_SCALE));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.font = `bold ${size}px system-ui, -apple-system, "Segoe UI", "Hiragino Sans", "Yu Gothic UI", sans-serif`;
  ctx.textBaseline = "alphabetic";
  const metrics = ctx.measureText(name);
  const ascent = metrics.fontBoundingBoxAscent ?? size * 0.9;
  const descent = metrics.fontBoundingBoxDescent ?? size * 0.25;
  const rect = { x: margin, y: margin, w: Math.max(1, width - margin * 2), h: Math.max(1, height - margin * 2) };
  const baseline = rect.y + rect.h - descent;
  const draw = (dx, dy, color) => {
    ctx.save();
    ctx.beginPath();
    ctx.rect(rect.x + dx, rect.y + dy, rect.w, rect.h);
    ctx.clip();
    ctx.fillStyle = color;
    ctx.fillText(name, rect.x + dx, baseline + dy);
    ctx.restore();
  };
  for (const dx of [-2, 0, 2]) {
    for (const dy of [-2, 0, 2]) if (dx || dy) draw(dx, dy, "black");
  }
  draw(0, 0, "white");
  const x0 = Math.max(0, Math.floor(rect.x - 4));
  const y0 = Math.max(0, Math.floor(baseline - ascent - 4));
  const x1 = Math.min(width, Math.ceil(rect.x + Math.min(rect.w, metrics.width) + 4));
  const y1 = Math.min(height, Math.ceil(baseline + descent + 4));
  const w = Math.max(1, x1 - x0);
  const h = Math.max(1, y1 - y0);
  const data = ctx.getImageData(x0, y0, w, h).data;
  // canvas の画素はストレート。Rust は乗算済みで重ねる（Qt の SourceOver と同じ）。
  const premultiplied = new Uint8Array(data.length);
  for (let i = 0; i < data.length; i += 4) {
    const a = data[i + 3];
    premultiplied[i] = Math.round((data[i] * a) / 255);
    premultiplied[i + 1] = Math.round((data[i + 1] * a) / 255);
    premultiplied[i + 2] = Math.round((data[i + 2] * a) / 255);
    premultiplied[i + 3] = a;
  }
  await invoke("export_slate", premultiplied, { headers: { "x-slate": `${x0},${y0},${w},${h}` } });
}

async function exportMp4() {
  const ffmpeg = await invoke("export_find_ffmpeg");
  if (!ffmpeg) {
    await warn("FFmpeg", tr("ffmpegが必要です。"));
    return;
  }
  const picked = await pickSave(tr("MP4書き出し"), "animation.mp4", "MP4", ["mp4"]);
  if (!picked) return;
  const path = withExtension(picked, ".mp4");
  const doc = await invoke("export_info");
  await sendSlate(doc.width, doc.height, doc.cut_name);
  const labelFor = (event) =>
    event.stage === "encode"
      ? tr("FFmpegで動画へ変換しています")
      : tr("フレーム {index} / {total} を準備しています", { index: Math.min(event.value + 1, event.total), total: event.total });
  let event;
  try {
    event = await runJob("export_mp4_start", { path, ffmpeg }, { title: tr("MP4書き出し"), initial: tr("動画用フレームを準備しています"), labelFor });
  } catch (error) {
    await fail(tr("MP4エラー"), String(error));
    return;
  }
  if (event.cancelled) return;
  if (event.error !== null && event.error !== undefined) await fail(tr("MP4エラー"), event.error);
}

// ---------------------------------------------------------------------------
// タイムリマップをコピー（Issue #23）
// ---------------------------------------------------------------------------

async function copyTimeRemap() {
  let text;
  try {
    text = await invoke("export_time_remap");
  } catch (error) {
    status(tr(String(error)));
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    try {
      await invoke("export_copy_text", { text });
    } catch (error) {
      status(String(error));
      return;
    }
  }
  status(tr("タイムリマップをクリップボードへコピーしました。"));
}

// ---------------------------------------------------------------------------
// カットフォルダー（Qt の CutFolderExportDialog と TemplateEditor）
// ---------------------------------------------------------------------------

const CUT_FOLDER_KEY_VALUES = ["title", "scene", "episode", "cut"];
const FIELD_IDS = ["title", "scene", "episode", "cut", "cell", "number"];
const CEL_FIELDS = ["cell", "number"];
const NUMERIC_FIELDS = ["episode", "cut", "number"];
const DIGIT_CHOICES = [null, 2, 3, 4, 5];
const DEFAULT_DIGITS = { episode: 2, cut: 3, number: 4 };

function fieldLabel(name) {
  return {
    title: tr("作品名"),
    scene: tr("シーン"),
    episode: tr("話数"),
    cut: tr("カット名"),
    cell: tr("セル名"),
    number: tr("セル番号"),
  }[name];
}

/// 保存の形（[{text} | {field, digits, case, prefix, suffix}]）を1文字ずつに分ける（`explode`）。
function explode(template) {
  const out = [];
  for (const token of template ?? []) {
    if (token.field) out.push({ ...token });
    else for (const ch of token.text ?? "") out.push({ text: ch });
  }
  return out;
}

/// 隣り合う文字をまとめた保存の形（`compact`・`Token.to_json`）。
function compact(tokens) {
  const out = [];
  for (const token of tokens) {
    if (!token.field) {
      if (!token.text) continue;
      const last = out[out.length - 1];
      if (last && !last.field) last.text += token.text;
      else out.push({ text: token.text });
      continue;
    }
    const saved = { field: token.field };
    if (token.digits !== null && token.digits !== undefined) saved.digits = token.digits;
    if (token.case && token.case !== "keep") saved.case = token.case;
    if (token.prefix) saved.prefix = token.prefix;
    if (token.suffix) saved.suffix = token.suffix;
    out.push(saved);
  }
  return out;
}

function chunkKind(token) {
  if (token.field) return { kind: "block" };
  const ch = token.text ?? "";
  return /^[\p{L}\p{N}]$/u.test(ch) ? { kind: "word" } : { kind: "symbol", ch };
}

const sameChunk = (a, b) => a.kind === b.kind && a.kind !== "block" && a.ch === b.ch;

let editorStyle = false;
function installStyle() {
  if (editorStyle) return;
  editorStyle = true;
  const css = `
.cf-dialog { width: min(1040px, 94vw); }
.cf-body { display: flex; gap: 16px; min-height: 520px; }
.cf-left { flex: 1; display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.cf-right { flex: 2; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.cf-row { display: flex; align-items: center; gap: 6px; }
.cf-row > .cf-grow { flex: 1; min-width: 0; }
.cf-values { display: grid; grid-template-columns: auto 1fr; gap: 4px 8px; align-items: center; margin-bottom: 6px; }
.cf-rule { border: none; border-top: 1px solid var(--border, #8884); margin: 6px 0; }
.cf-editor { position: relative; min-height: 28px; border: 1px solid var(--border, #8886); border-radius: 6px;
  padding: 3px 6px; display: flex; flex-wrap: wrap; align-items: center; gap: 2px; cursor: text; background: var(--input-bg, transparent); }
.cf-editor.focus { border-color: var(--accent, #3b82f6); box-shadow: 0 0 0 1px var(--accent, #3b82f6); }
.cf-char { padding: 1px 1px; background: #8881; border-radius: 2px; white-space: pre; }
.cf-chip { display: inline-flex; align-items: center; gap: 4px; padding: 1px 7px; border-radius: 5px; background: color-mix(in srgb, var(--accent, #3b82f6) 22%, transparent); cursor: pointer; white-space: nowrap; }
.cf-chip.missing { background: rgba(230, 160, 40, 0.35); }
.cf-affix { border: 1px dashed #8889; border-radius: 3px; padding: 0 3px; }
.cf-detail { opacity: 0.6; border-left: 1px solid #8889; padding-left: 5px; }
.cf-pending { text-decoration: underline dotted var(--accent, #3b82f6); }
.cf-caret { display: inline-block; width: 1px; height: 1.1em; background: currentColor; margin: 0 -0.5px; animation: cf-blink 1s steps(1) infinite; }
@keyframes cf-blink { 50% { opacity: 0; } }
.cf-placeholder { opacity: 0.45; pointer-events: none; }
.cf-input { position: absolute; opacity: 0; width: 1px; height: 1px; left: 0; top: 0; border: none; padding: 0; }
.cf-suggest { position: absolute; z-index: 20; min-width: 220px; background: var(--panel-bg, #2a2a2a); color: inherit;
  border: 1px solid var(--border, #8886); border-radius: 6px; padding: 4px 0; box-shadow: 0 4px 16px #0006; }
.cf-suggest .head { opacity: 0.6; font-size: 11px; padding: 2px 10px; }
.cf-suggest .item { padding: 3px 10px; display: flex; justify-content: space-between; gap: 16px; cursor: pointer; }
.cf-suggest .item.current { background: color-mix(in srgb, var(--accent, #3b82f6) 35%, transparent); }
.cf-suggest .sample { opacity: 0.55; }
.cf-popup { position: fixed; z-index: 30; background: var(--panel-bg, #2a2a2a); border: 1px solid var(--border, #8886);
  border-radius: 6px; padding: 8px 10px; display: flex; flex-direction: column; gap: 6px; box-shadow: 0 4px 16px #0006; }
.cf-popup .choices { display: flex; gap: 3px; }
.cf-popup .choices button.on { background: var(--accent, #3b82f6); color: white; }
.cf-tree { flex: 1; overflow: auto; border: 1px solid var(--border, #8886); border-radius: 4px; padding: 4px 6px; font-size: 12px; min-height: 300px; }
.cf-tree div { white-space: nowrap; }
.cf-problems { color: #e5484d; white-space: pre-wrap; }
.cf-head { display: flex; justify-content: flex-end; margin-bottom: 6px; }
`;
  document.head.append(el("style", {}, css));
}

/// 1つのテンプレートの編集欄（`TemplateEditor`）。文字とブロックを並べ、文字を打つと
/// ブロックの候補を出す。候補に無い文字は区切りの文字として入る。
class TemplateEditor {
  constructor({ allowed, sampleFor, isMissing, onChange, onBlock, placeholder = "" }) {
    this.allowed = allowed;
    this.sampleFor = sampleFor;
    this.isMissing = isMissing;
    this.onChange = onChange;
    this.onBlock = onBlock;
    this.placeholder = placeholder;
    this.tokens = [];
    this.caret = 0;
    this.pending = "";
    this.preedit = "";
    this.focused = false;
    this.suggestIndex = 0;
    this.root = el("div", { class: "cf-editor" });
    this.input = el("input", { class: "cf-input", type: "text", autocomplete: "off", spellcheck: "false" });
    this.suggest = el("div", { class: "cf-suggest", style: { display: "none" } });
    this.root.addEventListener("mousedown", (e) => this.onMouse(e));
    this.input.addEventListener("focus", () => {
      this.focused = true;
      this.render();
      this.updateSuggestions();
    });
    this.input.addEventListener("blur", () => {
      this.focused = false;
      this.preedit = "";
      this.commitPending();
      this.hideSuggestions();
      this.render();
    });
    this.input.addEventListener("keydown", (e) => this.onKey(e));
    this.input.addEventListener("compositionupdate", (e) => {
      this.preedit = e.data ?? "";
      this.render();
    });
    this.input.addEventListener("compositionend", (e) => {
      this.preedit = "";
      this.pending += e.data ?? "";
      this.input.value = "";
      this.render();
      this.updateSuggestions();
    });
    this.input.addEventListener("input", (e) => {
      if (e.isComposing) return;
      const typed = this.input.value;
      this.input.value = "";
      if (typed) {
        this.pending += typed;
        this.render();
        this.updateSuggestions();
      }
    });
    this.render();
  }

  setTemplate(template) {
    this.tokens = explode(template);
    this.caret = this.tokens.length;
    this.pending = "";
    this.render();
  }

  template() {
    return compact(this.tokens);
  }

  changed() {
    this.render();
    this.onChange?.();
  }

  insert(tokens) {
    this.tokens.splice(this.caret, 0, ...tokens);
    this.caret += tokens.length;
    this.changed();
  }

  commitPending() {
    if (!this.pending) return;
    const text = this.pending;
    this.pending = "";
    this.insert([...text].map((ch) => ({ text: ch })));
    this.updateSuggestions();
  }

  insertField(name) {
    if (this.pending && !this.matches(this.pending).length) this.commitPending();
    this.pending = "";
    this.insert([{ field: name, digits: DEFAULT_DIGITS[name] ?? null, case: "keep", prefix: "", suffix: "" }]);
    this.updateSuggestions();
  }

  replaceToken(index, token) {
    if (index < 0 || index >= this.tokens.length) return;
    this.tokens[index] = token;
    this.changed();
  }

  removeToken(index) {
    if (index < 0 || index >= this.tokens.length) return;
    this.tokens.splice(index, 1);
    this.caret = Math.min(index, this.tokens.length);
    this.changed();
  }

  deleteRange(start, end) {
    if (start >= end) return;
    this.tokens.splice(start, end - start);
    this.caret = start;
    this.changed();
    this.updateSuggestions();
  }

  chunkLeft(index) {
    if (index <= 0) return 0;
    const kind = chunkKind(this.tokens[index - 1]);
    if (kind.kind === "block") return index - 1;
    while (index > 0 && sameChunk(chunkKind(this.tokens[index - 1]), kind)) index -= 1;
    return index;
  }

  chunkRight(index) {
    if (index >= this.tokens.length) return this.tokens.length;
    const kind = chunkKind(this.tokens[index]);
    if (kind.kind === "block") return index + 1;
    while (index < this.tokens.length && sameChunk(chunkKind(this.tokens[index]), kind)) index += 1;
    return index;
  }

  move(index) {
    this.caret = Math.max(0, Math.min(index, this.tokens.length));
    this.render();
    this.updateSuggestions();
  }

  chipParts(token) {
    const details = [];
    if (NUMERIC_FIELDS.includes(token.field)) details.push(token.digits ? tr("{n}桁", { n: token.digits }) : tr("可変"));
    if (token.case === "upper") details.push(tr("大文字"));
    else if (token.case === "lower") details.push(tr("小文字"));
    return { label: fieldLabel(token.field), detail: details.join("・") };
  }

  render() {
    this.root.classList.toggle("focus", this.focused);
    this.root.replaceChildren();
    const caretNode = () => el("span", { class: "cf-caret" });
    const pendingNode = () => (this.pending || this.preedit ? el("span", { class: "cf-pending" }, this.pending + this.preedit) : null);
    this.tokens.forEach((token, index) => {
      if (index === this.caret) {
        const p = pendingNode();
        if (p) this.root.append(p);
        if (this.focused) this.root.append(caretNode());
      }
      if (!token.field) {
        const node = el("span", { class: "cf-char" }, token.text === " " ? "␣" : token.text);
        node.dataset.index = String(index);
        this.root.append(node);
        return;
      }
      const { label, detail } = this.chipParts(token);
      const missing = CUT_FOLDER_KEY_VALUES.includes(token.field) && this.isMissing(token.field);
      const chip = el("span", { class: `cf-chip${missing ? " missing" : ""}` },
        token.prefix ? el("span", { class: "cf-affix" }, token.prefix) : null,
        label,
        token.suffix ? el("span", { class: "cf-affix" }, token.suffix) : null,
        detail ? el("span", { class: "cf-detail" }, detail) : null,
      );
      chip.dataset.index = String(index);
      chip.dataset.chip = "1";
      this.root.append(chip);
    });
    if (this.caret >= this.tokens.length) {
      const p = pendingNode();
      if (p) this.root.append(p);
      if (this.focused) this.root.append(caretNode());
    }
    if (!this.tokens.length && !this.pending && !this.preedit && this.placeholder) {
      this.root.append(el("span", { class: "cf-placeholder" }, this.placeholder));
    }
    this.root.append(this.input, this.suggest);
  }

  onMouse(e) {
    if (e.button !== 0) return;
    if (e.target.closest(".cf-suggest")) return;
    e.preventDefault();
    this.commitPending();
    const target = e.target.closest("[data-index]");
    if (target) {
      const index = Number(target.dataset.index);
      if (target.dataset.chip) {
        this.caret = index + 1;
        this.input.focus();
        this.hideSuggestions();
        this.render();
        this.onBlock?.(this, index, target.getBoundingClientRect());
        return;
      }
      const r = target.getBoundingClientRect();
      this.caret = e.clientX < r.left + r.width / 2 ? index : index + 1;
    } else {
      // 行の空いた所：一番近い文字の境目（簡単に、押した場所より右にある最初の要素の前）。
      const items = [...this.root.querySelectorAll("[data-index]")];
      const after = items.find((node) => {
        const r = node.getBoundingClientRect();
        return e.clientY < r.bottom && (e.clientY < r.top || e.clientX < r.left + r.width / 2);
      });
      this.caret = after ? Number(after.dataset.index) : this.tokens.length;
    }
    this.input.focus();
    this.render();
    this.updateSuggestions();
  }

  onKey(e) {
    if (e.isComposing || e.keyCode === 229) return;
    const mac = navigator.platform.toUpperCase().includes("MAC");
    const word = mac ? e.altKey : e.ctrlKey;
    const lineKey = mac && e.metaKey;
    const open = this.suggest.style.display !== "none";
    const stop = () => {
      e.preventDefault();
      e.stopPropagation();
    };
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      stop();
      this.commitPending();
      const left = e.key === "ArrowLeft";
      if (lineKey) this.move(left ? 0 : this.tokens.length);
      else if (word) this.move(left ? this.chunkLeft(this.caret) : this.chunkRight(this.caret));
      else this.move(this.caret + (left ? -1 : 1));
      return;
    }
    if (e.key === "Home" || e.key === "End") {
      stop();
      this.commitPending();
      this.move(e.key === "Home" ? 0 : this.tokens.length);
      return;
    }
    if (e.key === "Backspace") {
      stop();
      if (this.pending) {
        this.pending = [...this.pending].slice(0, -1).join("");
        this.render();
        this.updateSuggestions();
      } else if (word) {
        this.deleteRange(this.chunkLeft(this.caret), this.caret);
      } else if (this.caret > 0) {
        this.deleteRange(this.caret - 1, this.caret);
      }
      return;
    }
    if (e.key === "Delete") {
      stop();
      if (this.pending) return;
      if (word) this.deleteRange(this.caret, this.chunkRight(this.caret));
      else if (this.caret < this.tokens.length) this.deleteRange(this.caret, this.caret + 1);
      return;
    }
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      stop();
      if (!open) this.updateSuggestions(true);
      else this.moveSuggestion(e.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (e.key === "Enter") {
      stop();
      if (this.pending && !this.matches(this.pending).length) this.commitPending();
      else if (open) {
        const name = this.currentSuggestion();
        if (name) this.insertField(name);
      }
      return;
    }
    if (e.key === "Escape") {
      if (open || this.pending) {
        stop();
        this.pending = "";
        this.hideSuggestions();
        this.render();
      }
      return;
    }
    if (e.key === "Tab") {
      this.commitPending();
    }
  }

  matches(query) {
    const q = query.toLowerCase();
    return this.allowed.filter((name) => fieldLabel(name).toLowerCase().includes(q) || name.startsWith(q));
  }

  updateSuggestions(force = false) {
    if (!this.focused && !force) return;
    const query = this.pending + this.preedit;
    let names = query ? this.matches(query) : [...this.allowed];
    let head;
    if (query && !names.length) {
      head = tr("「{text}」の後に追加", { text: query.replaceAll(" ", "␣") });
      names = [...this.allowed];
    } else {
      head = tr("ブロック");
    }
    this.suggestNames = names;
    this.suggestIndex = Math.min(this.suggestIndex, names.length - 1);
    if (this.suggestIndex < 0) this.suggestIndex = 0;
    this.suggest.replaceChildren(el("div", { class: "head" }, head));
    names.forEach((name, index) => {
      const item = el("div", { class: `item${index === this.suggestIndex ? " current" : ""}` },
        el("span", {}, fieldLabel(name)), el("span", { class: "sample" }, this.sampleFor(name)));
      item.addEventListener("mousedown", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.insertField(name);
      });
      this.suggest.append(item);
    });
    const caret = this.root.querySelector(".cf-caret");
    const rootRect = this.root.getBoundingClientRect();
    const caretRect = caret?.getBoundingClientRect() ?? rootRect;
    this.suggest.style.left = `${Math.max(0, caretRect.left - rootRect.left - 4)}px`;
    this.suggest.style.top = `${caretRect.bottom - rootRect.top + 4}px`;
    this.suggest.style.display = "block";
  }

  moveSuggestion(step) {
    const count = this.suggestNames?.length ?? 0;
    if (!count) return;
    this.suggestIndex = (this.suggestIndex + step + count) % count;
    [...this.suggest.querySelectorAll(".item")].forEach((node, index) => node.classList.toggle("current", index === this.suggestIndex));
  }

  currentSuggestion() {
    return this.suggestNames?.[this.suggestIndex] ?? null;
  }

  hideSuggestions() {
    this.suggest.style.display = "none";
  }
}

/// ブロックの設定（`BlockSettingsPopup`）：前後の文字、桁数か表記、削除。
function blockPopup(editor, index, rect) {
  document.querySelector(".cf-popup")?.remove();
  const token = { ...editor.tokens[index] };
  const prefix = el("input", { type: "text", value: token.prefix ?? "", placeholder: tr("前 (例: c)"), style: { width: "90px" } });
  const suffix = el("input", { type: "text", value: token.suffix ?? "", placeholder: tr("後"), style: { width: "90px" } });
  const popup = el("div", { class: "cf-popup" },
    el("b", {}, fieldLabel(token.field)),
    el("div", {}, tr("前後に付ける文字")),
    el("div", { class: "cf-row" }, prefix, suffix),
  );
  let choiceKey = null;
  let choiceValue = null;
  const addChoices = (label, choices, current, key) => {
    choiceKey = key;
    choiceValue = current;
    const row = el("div", { class: "choices" });
    for (const [text, value] of choices) {
      const button = el("button", { type: "button", class: value === current ? "on" : "" }, text);
      button.addEventListener("click", () => {
        choiceValue = value;
        for (const b of row.children) b.classList.toggle("on", b === button);
        apply();
      });
      row.append(button);
    }
    popup.append(el("div", {}, label), row);
  };
  if (NUMERIC_FIELDS.includes(token.field)) {
    addChoices(tr("桁数"), DIGIT_CHOICES.map((d) => [d === null ? tr("可変") : tr("{n}桁", { n: d }), d]), token.digits ?? null, "digits");
  } else if (token.field === "title" || token.field === "cell") {
    addChoices(tr("表記"), [[tr("そのまま"), "keep"], [tr("大文字"), "upper"], [tr("小文字"), "lower"]], token.case ?? "keep", "case");
  }
  const remove = el("button", { type: "button" }, tr("ブロックを削除"));
  popup.append(remove);
  const apply = () => {
    const next = { ...editor.tokens[index], prefix: prefix.value, suffix: suffix.value };
    if (choiceKey) next[choiceKey] = choiceValue;
    editor.replaceToken(index, next);
  };
  const close = () => {
    popup.remove();
    document.removeEventListener("mousedown", outside, true);
  };
  const outside = (e) => {
    if (!popup.contains(e.target)) close();
  };
  for (const input of [prefix, suffix]) {
    input.addEventListener("input", apply);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close();
      }
    });
  }
  remove.addEventListener("click", () => {
    editor.removeToken(index);
    close();
  });
  popup.style.left = `${rect.left}px`;
  popup.style.top = `${rect.bottom + 4}px`;
  document.body.append(popup);
  setTimeout(() => document.addEventListener("mousedown", outside, true));
  prefix.focus();
}

function problemText(problem) {
  switch (problem.kind) {
    case "missing_value":
      return tr("「{name}」を入力してください。", { name: fieldLabel(problem.field) });
    case "no_number":
      return tr("セル画像にセル番号のブロックがありません。");
    case "duplicate":
      return tr("同じ名前のファイルができます：{path}", { path: problem.name });
    case "bad_name": {
      const where = {
        folder: tr("カットフォルダー"),
        cell_file: tr("セル画像"),
        timesheet: tr("タイムシート"),
        timesheet_folder: tr("タイムシートのフォルダー"),
        extra_folder: tr("空のフォルダー"),
      }[problem.place] ?? "";
      return tr("{where}の名前「{name}」は使えません。", { where, name: problem.name });
    }
    default:
      return JSON.stringify(problem);
  }
}

/// カットフォルダーへ書き出しの窓。決めたら {layout, values, destination}、やめたら null。
async function cutFolderDialog(doc) {
  installStyle();
  const settings = await invoke("export_cut_folder_settings");
  const saved = settings.saved ?? {};
  let userPresets = { ...(saved.presets ?? {}) };
  const builtin = settings.builtin;
  const cels = doc.cels;

  const valueEdits = {};
  const valuesBox = el("div", { class: "cf-values" });
  const savedValues = saved.values ?? {};
  for (const name of CUT_FOLDER_KEY_VALUES) {
    const input = el("input", { type: "text", value: String(savedValues[name] ?? "") });
    if (NUMERIC_FIELDS.includes(name)) input.placeholder = tr("番号のみ");
    valueEdits[name] = input;
    valuesBox.append(el("span", { class: "sh-label" }, fieldLabel(name)), input);
  }
  if (doc.cut_hint) valueEdits.cut.value = doc.cut_hint;
  const values = () => Object.fromEntries(Object.entries(valueEdits).map(([k, v]) => [k, v.value.trim()]));
  const sampleFor = (name) => {
    if (name === "cell") return cels[0]?.cell ?? "A";
    if (name === "number") return "1";
    return valueEdits[name].value.trim() || tr("未入力");
  };
  const isMissing = (name) => !valueEdits[name].value.trim();

  let loading = false;
  const editors = [];
  const makeEditor = (allowed, placeholder = "") => {
    const editor = new TemplateEditor({
      allowed,
      sampleFor,
      isMissing,
      placeholder,
      onChange: () => {
        if (!loading) refresh();
      },
      onBlock: blockPopup,
    });
    editors.push(editor);
    return editor;
  };
  const cutFields = FIELD_IDS.filter((f) => !CEL_FIELDS.includes(f));
  const folderEditor = makeEditor(cutFields);
  const cellFolderEditor = makeEditor(FIELD_IDS);
  const cellFileEditor = makeEditor(FIELD_IDS);
  const timesheetEditor = makeEditor(cutFields);
  const timesheetFolderEditor = makeEditor(cutFields, tr("空欄ならカットフォルダー直下"));
  const formatSelect = el("select", {}, el("option", { value: "png" }, ".png"), el("option", { value: "tga" }, ".tga"));
  const sheetSelect = el("select", {}, el("option", { value: "xdts" }, ".xdts"), el("option", { value: "tdts" }, ".tdts"));
  formatSelect.addEventListener("change", () => refresh());
  sheetSelect.addEventListener("change", () => refresh());
  const extraBox = el("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } });
  let extras = []; // [{holder, editor}]
  let writeTimesheet = true;
  const addExtra = (template) => {
    const editor = new TemplateEditor({
      allowed: cutFields, sampleFor, isMissing,
      onChange: () => {
        if (!loading) refresh();
      },
      onBlock: blockPopup,
    });
    editor.setTemplate(template);
    const remove = el("button", { type: "button", title: tr("このフォルダーを削除") }, "×");
    const holder = el("div", { class: "cf-row" }, el("div", { class: "cf-grow" }, editor.root), remove);
    remove.addEventListener("click", () => {
      extras = extras.filter((x) => x.holder !== holder);
      holder.remove();
      refresh();
    });
    extraBox.append(holder);
    extras.push({ holder, editor });
    if (!loading) {
      editor.input.focus();
      refresh();
    }
  };
  const addExtraButton = el("button", { type: "button" }, tr("＋ 追加"));
  addExtraButton.addEventListener("click", () => addExtra([]));

  const destination = el("input", { type: "text", value: String(saved.destination ?? ""), placeholder: tr("カットフォルダーを作る場所") });
  destination.addEventListener("input", () => refresh());
  const browse = el("button", { type: "button" }, tr("参照…"));
  browse.addEventListener("click", async () => {
    const path = await pickFolder(tr("保存先を選ぶ"), destination.value.trim() || null);
    if (path) {
      destination.value = path;
      refresh();
    }
  });
  const tree = el("div", { class: "cf-tree" });
  const problems = el("div", { class: "cf-problems" });
  const presetSelect = el("select", {});

  const currentLayout = () => {
    const layout = {
      folder: folderEditor.template(),
      cell_folder: cellFolderEditor.template(),
      cell_file: cellFileEditor.template(),
      timesheet: timesheetEditor.template(),
      timesheet_folder: timesheetFolderEditor.template(),
      extra_folders: extras.map((x) => x.editor.template()).filter((t) => t.length),
      image_format: formatSelect.value,
      timesheet_format: sheetSelect.value,
    };
    if (!writeTimesheet) layout.write_timesheet = false;
    return layout;
  };
  const setLayout = (layout) => {
    loading = true;
    try {
      folderEditor.setTemplate(layout.folder);
      cellFolderEditor.setTemplate(layout.cell_folder);
      cellFileEditor.setTemplate(layout.cell_file);
      timesheetEditor.setTemplate(layout.timesheet);
      timesheetFolderEditor.setTemplate(layout.timesheet_folder);
      formatSelect.value = layout.image_format === "tga" ? "tga" : "png";
      sheetSelect.value = layout.timesheet_format === "tdts" ? "tdts" : "xdts";
      writeTimesheet = layout.write_timesheet !== false;
      for (const x of extras) x.holder.remove();
      extras = [];
      for (const template of layout.extra_folders ?? []) addExtra(template);
    } finally {
      loading = false;
    }
    refresh();
  };

  // プリセット（Qt の preset_combo）。
  let currentPreset = null;
  const rebuildPresets = (select) => {
    presetSelect.replaceChildren();
    for (const p of builtin) presetSelect.append(el("option", { value: `builtin:${p.name}` }, tr("プリセット: {name}", { name: tr(p.name) })));
    for (const name of Object.keys(userPresets)) presetSelect.append(el("option", { value: `user:${name}` }, tr("プリセット: {name}", { name })));
    presetSelect.append(el("option", { disabled: true, value: "" }, "──────────"));
    presetSelect.append(el("option", { value: "save:" }, tr("現在の構成をプリセットに保存…")));
    if (Object.keys(userPresets).length) presetSelect.append(el("option", { value: "delete:" }, tr("選択中のプリセットを削除")));
    const match = [...presetSelect.options].find((o) => o.value.endsWith(`:${select}`) && /^(builtin|user):/.test(o.value));
    presetSelect.value = match ? match.value : presetSelect.options[0].value;
    currentPreset = presetSelect.value;
  };
  const savePresets = async () => {
    const settingsNow = await invoke("export_cut_folder_settings");
    const data = { ...(settingsNow.saved ?? {}), presets: userPresets };
    await invoke("export_cut_folder_remember", { data });
  };
  presetSelect.addEventListener("change", async () => {
    const [kind, ...rest] = presetSelect.value.split(":");
    const name = rest.join(":");
    if (kind === "builtin") {
      setLayout(builtin.find((p) => p.name === name).layout);
      currentPreset = presetSelect.value;
      return;
    }
    if (kind === "user") {
      try {
        setLayout(await invoke("export_cut_folder_layout", { layout: userPresets[name] }));
      } catch {
        // 読めないプリセットは今の構成のまま。
      }
      currentPreset = presetSelect.value;
      return;
    }
    if (kind === "save") {
      const entered = ((await prompt(tr("プリセットを保存"), tr("プリセット名"))) ?? "").trim();
      if (entered) {
        if (builtin.some((p) => p.name === entered)) {
          await warn(tr("プリセットを保存"), tr("組み込みプリセットと同じ名前は使えません。"));
        } else {
          userPresets[entered] = currentLayout();
          await savePresets();
          rebuildPresets(entered);
          return;
        }
      }
    } else if (kind === "delete") {
      if (currentPreset?.startsWith("user:")) {
        delete userPresets[currentPreset.slice(5)];
        await savePresets();
        rebuildPresets("PMA標準");
        setLayout(builtin[0].layout);
        return;
      }
    }
    presetSelect.value = currentPreset;
  });

  // プレビュー。
  let lastPlan = null;
  let seq = 0;
  const exportButton = { element: null };
  async function refresh() {
    for (const editor of [...editors, ...extras.map((x) => x.editor)]) editor.render();
    const mine = ++seq;
    let plan;
    try {
      plan = await invoke("export_cut_folder_plan", { layout: currentLayout(), values: values(), destination: destination.value });
    } catch (error) {
      problems.textContent = String(error);
      return;
    }
    if (mine !== seq) return;
    lastPlan = plan;
    tree.replaceChildren();
    const rootLabel = `${plan.folder_name || `〈${tr("カットフォルダー")}〉`}/${plan.existing ? `  ${tr("（既存のフォルダー）")}` : ""}`;
    const nodes = new Map();
    const rootNode = { el: el("div", {}, `📁 ${rootLabel}`), children: el("div", { style: { paddingLeft: "16px" } }) };
    tree.append(rootNode.el, rootNode.children);
    const folderNode = (path) => {
      if (!path) return rootNode;
      if (nodes.has(path)) return nodes.get(path);
      const cut = path.lastIndexOf("/");
      const parent = folderNode(cut < 0 ? "" : path.slice(0, cut));
      const node = { el: el("div", {}, `📁 ${path.slice(cut + 1)}/`), children: el("div", { style: { paddingLeft: "16px" } }) };
      parent.children.append(node.el, node.children);
      nodes.set(path, node);
      return node;
    };
    for (const path of plan.folders) folderNode(path);
    const addFile = (relative) => {
      const cut = relative.lastIndexOf("/");
      folderNode(cut < 0 ? "" : relative.slice(0, cut)).children.append(el("div", {}, `📄 ${relative.slice(cut + 1)}`));
    };
    for (const relative of plan.files) addFile(relative);
    if (plan.timesheet_path) addFile(plan.timesheet_path);
    const messages = plan.problems.map(problemText);
    if (!cels.length) messages.push(tr("書き出せるセルがありません。"));
    if (!destination.value.trim()) messages.push(tr("保存先を指定してください。"));
    problems.textContent = messages.join("\n");
    problems.style.display = messages.length ? "" : "none";
    if (exportButton.element) exportButton.element.disabled = messages.length > 0;
  }

  const label = (text) => el("div", {}, text);
  const left = el("div", { class: "cf-left" },
    valuesBox,
    el("hr", { class: "cf-rule" }),
    label(tr("カットフォルダー")), folderEditor.root,
    label(tr("セルフォルダー")), cellFolderEditor.root,
    el("div", { class: "cf-row" }, el("span", { class: "cf-grow" }, tr("セル画像")), formatSelect), cellFileEditor.root,
    label(tr("タイムシートのフォルダー")), timesheetFolderEditor.root,
    el("div", { class: "cf-row" }, el("span", { class: "cf-grow" }, tr("タイムシート")), sheetSelect), timesheetEditor.root,
    el("div", { class: "cf-row" }, el("span", { class: "cf-grow" }, tr("空のフォルダー")), addExtraButton), extraBox,
  );
  const right = el("div", { class: "cf-right" },
    el("div", { class: "cf-row" }, el("span", {}, tr("保存先")), el("div", { class: "cf-grow" }, destination), browse),
    label(tr("プレビュー")), tree, problems,
  );
  const body = el("div", {}, el("div", { class: "cf-head" }, presetSelect), el("div", { class: "cf-body" }, left, right));
  for (const input of Object.values(valueEdits)) input.addEventListener("input", () => refresh());
  destination.style.width = "100%";

  rebuildPresets(saved.preset || "PMA標準");
  setLayout(settings.layout ?? builtin[0].layout);
  const buttons = [
    { label: tr("キャンセル"), value: false, escape: true },
    {
      label: tr("書き出す"),
      value: true,
      validate: async () => {
        await refresh();
        return Boolean(lastPlan && !lastPlan.problems.length && destination.value.trim() && cels.length);
      },
    },
  ];
  const promise = openDialog({ title: tr("カットフォルダーへ書き出し"), body, buttons, className: "cf-dialog" });
  exportButton.element = buttons[1].element;
  refresh();
  const ok = await promise;
  document.querySelector(".cf-popup")?.remove();
  if (!ok) return null;
  // 覚える（Qt の remember。カット名は覚えない）。
  const remembered = values();
  delete remembered.cut;
  const settingsNow = await invoke("export_cut_folder_settings");
  const layout = currentLayout();
  const data = {
    ...(settingsNow.saved ?? {}),
    layout,
    values: remembered,
    destination: destination.value.trim(),
    preset: currentPreset ? currentPreset.split(":").slice(1).join(":") || null : null,
    presets: userPresets,
  };
  await invoke("export_cut_folder_remember", { data });
  return { layout, values: values(), destination: destination.value.trim(), folderName: lastPlan.folder_name };
}

async function exportCutFolder() {
  const title = tr("カットフォルダー書き出し");
  const doc = await invoke("export_info");
  const chosen = await cutFolderDialog(doc);
  if (!chosen) return;
  const target = `${chosen.destination.replace(/[\\/]+$/, "")}/${chosen.folderName}`;
  let state;
  try {
    state = await invoke("export_target_state", { path: target });
  } catch (error) {
    await fail(title, tr("書き出しフォルダーを作成できません。\n\n{exc}", { exc: String(error) }));
    return;
  }
  if (state === "file") {
    await fail(title, tr("書き出しフォルダーを作成できません。\n\n{exc}", { exc: tr("同じ名前のファイルがあるため、フォルダーを作成できません。") }));
    return;
  }
  if (state === "not_empty") {
    const ok = await ask(tr("同名フォルダー"), tr("「{name}」には既存のファイルがあります。\n同じ名前のファイルは上書きします。書き出しますか？", { name: chosen.folderName }));
    if (!ok) return;
  }
  let event;
  try {
    event = await runJob(
      "export_cut_folder_start",
      { parent: chosen.destination, layout: chosen.layout, values: chosen.values },
      { title, initial: tr("「{name}」へ書き出しています", { name: chosen.folderName }), labelFor: itemLabel },
    );
  } catch (error) {
    await fail(title, tr("書き出しフォルダーを作成できません。\n\n{exc}", { exc: String(error) }));
    return;
  }
  if (event.cancelled) return;
  if (event.error !== null && event.error !== undefined) {
    const exc = event.error === "file_in_the_way" ? tr("同じ名前のファイルがあるため、フォルダーを作成できません。") : event.error;
    await fail(title, tr("書き出し中にエラーが発生しました。\n\n{exc}", { exc }));
    return;
  }
  const result = event.result;
  let text = tr("「{name}」へセル{count}枚とタイムシートを書き出しました。", { name: result.folder_name, count: result.count });
  if (result.skipped) text += `\n${tr("セル番号のないキー{count}枚は書き出していません。", { count: result.skipped })}`;
  status(text.split("\n")[0]);
  await info(title, `${text}\n\n${result.target}`);
}

// ---------------------------------------------------------------------------
// つなぐ
// ---------------------------------------------------------------------------

const ACTIONS = {
  a_save: () => saveFrame("png"),
  a_save_tga: () => saveFrame("tga"),
  a_export_png_seq: () => exportKeySequence("PNG"),
  a_export_tga_seq: () => exportKeySequence("TGA"),
  a_export_cut_folder: exportCutFolder,
  a_export_shadow: exportShadow,
  a_export_mp4: exportMp4,
  a_export_xdts: exportXdts,
  a_export_psd: exportPsd,
  a_tl_copy_time_remap: copyTimeRemap,
};

let busy = false;

export function installExport() {
  listen("export-job", ({ payload }) => onJob(payload));
  window.addEventListener("pma-action", (e) => {
    const f = ACTIONS[e.detail?.id];
    if (!f) return;
    e.preventDefault();
    if (busy) return;
    busy = true;
    Promise.resolve()
      .then(f)
      .catch((error) => message(tr("書き出し"), String(error)))
      .finally(() => {
        busy = false;
      });
  });
}
