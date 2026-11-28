// 【Group B】編集メニューの操作（計算は app/src/tools.rs の edit_clipboard・app/src/edit_ops.rs）。
// 切り取り・コピー・貼り付け（OS のクリップボード）、キャンバスサイズの変更、
// 色塗り前の初期状態の保持・消去（InitialStateController.keep・clear）、
// 線整え（LineOps.tidy_lines_dialog：プレビューを見て確定する）。
const { invoke } = window.__TAURI__.core;
import { el, openDialog, ask } from "./dialogs.js";
import { notify } from "./tool_properties.js";
import { tr } from "./i18n.js";

let applyRect = () => {};

// 文字の欄にいるときは、メニューのコピー・貼り付けを欄の中で行う（キャンバスを触らない）。
function editingText() {
  const a = document.activeElement;
  return a instanceof HTMLInputElement || a instanceof HTMLTextAreaElement || a?.isContentEditable;
}

async function clipboard(kind) {
  if (editingText()) {
    document.execCommand(kind);
    return;
  }
  applyRect(await invoke("edit_clipboard", { kind }));
}

/// MainWindow.resize_doc（CanvasSizeDialog）。
async function resizeCanvas() {
  const size = await invoke("edit_canvas_size");
  if (!size) return;
  const MAX = 16384;
  const spin = (value) => el("input", { type: "number", min: "64", max: String(MAX), step: "1", value: String(value), style: { width: "110px" } });
  const w = spin(size[0]);
  const h = spin(size[1]);
  const body = el("div", { class: "sh-form" },
    el("span", { class: "sh-label" }, tr("幅")), w,
    el("span", { class: "sh-label" }, tr("高さ")), h,
  );
  const ok = await openDialog({
    title: tr("キャンバスサイズの変更"),
    body,
    buttons: [
      { label: tr("OK"), value: true, primary: true },
      { label: tr("キャンセル"), value: false, escape: true },
    ],
    initialFocus: () => w,
  });
  if (!ok) return;
  const clamp = (v) => Math.max(64, Math.min(MAX, Math.round(Number(v) || 64)));
  // 表示は document-changed（source が edit）で読み直す。
  await invoke("edit_resize_canvas", { width: clamp(w.value), height: clamp(h.value) });
}

/// 初期状態の保持・消去。尋ねる文が返ったら、はいのときだけもう一度呼ぶ。
async function initialState(kind) {
  let outcome = await invoke("edit_initial_state", { kind, confirmed: false });
  if (outcome.confirm) {
    if (!(await ask(outcome.confirm[0], outcome.confirm[1]))) return;
    outcome = await invoke("edit_initial_state", { kind, confirmed: true });
  }
  if (outcome.message) notify(outcome.message);
  // Qt は消したあと「初期状態と見比べる」を外す。canvas_view.js が document-changed で外す。
}

/// 線整えの窓（tidy_lines_dialog）。
async function tidyLines() {
  const info = await invoke("edit_tidy_dialog");
  if (info.message) {
    notify(info.message);
    return;
  }
  const summary = el("div", {});
  const picture = el("canvas", { style: { maxWidth: "640px", maxHeight: "480px", imageRendering: "pixelated" } });
  const note = el("div", { class: "sh-message", style: { maxWidth: "640px" } }, tr(
    "段と段が角だけで接している所に1画素足して上下左右でつなぎ、線の側面から1画素だけ突き出た所と、段の重なりが厚くなった所の1画素を消します。線の先端・交差・直角の角は変えません。",
  ));
  const diagonal = el("select", {
    title: tr("2×2の塊が角でつながった45°の線の直し方。足すと各行が同じ画素数で1列ずつずれる階段に、消すと1画素幅の45°の線になります。"),
  });
  for (const [label, value] of [["足して太さをそろえる", "add"], ["消して1画素幅にする", "remove"], ["直さない", ""]]) {
    diagonal.append(el("option", { value }, tr(label)));
  }
  // (すべてのコマか, 選択範囲の中だけか)
  const scopes = [[false, false], [true, false], [false, true], [true, true]];
  const scope = el("select", {});
  ["今のコマ", "すべてのコマ", "選択範囲（今のコマ）", "選択範囲（すべてのコマ）"].forEach((label, i) => {
    const option = el("option", { value: String(i) }, tr(label));
    if (!info.has_selection && i >= 2) option.disabled = true;
    scope.append(option);
  });
  if (info.has_selection) scope.value = "2";
  else scope.title = tr("選択範囲を作ると、その中だけを整えられます。");
  const form = () => {
    const [allFrames, inSelection] = scopes[Number(scope.value)];
    return { diagonal: diagonal.value || null, all_frames: allFrames, in_selection: inSelection };
  };
  let request = 0;
  const refresh = async () => {
    const mine = ++request;
    const buffer = await invoke("edit_tidy_preview", { form: form() });
    if (mine !== request) return;
    const head = new DataView(buffer, 0, 16);
    const [added, removed, w, h] = [0, 4, 8, 12].map((o) => head.getUint32(o, true));
    if (w && h) {
      summary.textContent = tr("今のコマ：足す {added}px（赤）／消す {removed}px（青）", { added, removed });
      picture.width = w;
      picture.height = h;
      picture.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(buffer, 16, w * h * 4), w, h), 0, 0);
      picture.style.display = "";
    } else {
      summary.textContent = tr("今のコマには直す所がありません。");
      picture.style.display = "none";
    }
  };
  diagonal.addEventListener("change", refresh);
  scope.addEventListener("change", refresh);
  const body = el("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } },
    summary,
    picture,
    note,
    el("div", { class: "sh-form" },
      el("span", { class: "sh-label" }, tr("2×2の45°の線")), diagonal,
      el("span", { class: "sh-label" }, tr("範囲")), scope,
    ),
  );
  const result = openDialog({
    title: tr("線整え"),
    body,
    buttons: [
      { label: tr("OK"), value: true, primary: true },
      { label: tr("キャンセル"), value: false, escape: true },
    ],
  });
  refresh().catch((e) => notify(String(e)));
  if (!(await result)) return;
  notify(await invoke("edit_tidy_apply", { form: form() }));
}

const ACTIONS = {
  a_cut: () => clipboard("cut"),
  a_copy: () => clipboard("copy"),
  a_paste: () => clipboard("paste"),
  a_resize: resizeCanvas,
  a_keep_initial_state: () => initialState("keep"),
  a_clear_initial_state: () => initialState("clear"),
  a_tidy_lines: tidyLines,
};

/// 編集の操作を組み込む。`applyRect` はキャンバスへ変えた範囲を書く関数（app.js）。
export function installEditOps({ applyRect: apply }) {
  applyRect = apply;
  window.addEventListener("pma-action", (e) => {
    const f = ACTIONS[e.detail?.id];
    if (!f) return;
    e.preventDefault();
    Promise.resolve(f()).catch((err) => notify(String(err)));
  });
}
