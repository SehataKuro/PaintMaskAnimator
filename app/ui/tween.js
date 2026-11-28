// 変形のトゥイーン（Qt 版の main_window_tween.TweenController と widgets.TweenCommandPopup）。
//
// - 計算と文書の変更は Rust（transform.rs、pma-core の TransformSession::tween_images と
//   Document::install_tween）。ここはタイムラインからの入口と、編集中の操作パネルだけ。
// - 操作パネルは toolStore の transform.tween があるあいだ出す（逆生成・左右 90°・確定・
//   キャンセル）。Enter・Esc は今の変形と同じ（canvas_tools.js の a_transform_commit・Esc）。
import { toolStore, el } from "./tools.js";
import { notify } from "./tool_properties.js";
import { message } from "./dialogs.js";
import { tr } from "./i18n.js";

const { invoke } = window.__TAURI__.core;

const modeName = (mesh) => (mesh ? tr("メッシュ変形") : tr("自由変形"));

async function run(cmd, args) {
  try {
    const status = await invoke(cmd, args);
    if (typeof status === "string") notify(tr(status));
    return true;
  } catch (e) {
    await message(tr("トゥイーン"), tr(String(e)));
    return false;
  }
}

/// タイムラインの「トゥイーンを有効にする」（Qt の enable）。`exposure` は帯の長さ。
export async function enableTween(layer, column, mode, exposure) {
  mode = mode === "mesh" ? "mesh" : "free";
  if (!(exposure >= 2)) {
    await message(tr("トゥイーン"), tr("2コマ以上の表示区間を持つ画像キーフレームで実行してください。"));
    return;
  }
  const ok = await message(
    tr("トゥイーンを有効にする"),
    tr("{exposure}コマの{name}トゥイーンを開始します。\n確定後もトゥイーンとして残り、タイムラインの帯をダブルクリックすると形を直せます。", {
      exposure,
      name: modeName(mode === "mesh"),
    }),
    [[tr("OK"), true, true], [tr("キャンセル"), false]],
    false,
  );
  if (ok) await run("tween_enable", { layer, column, mode });
}

/// 確定したトゥイーンの形を直す（Qt の edit。帯のダブルクリックと右クリックのメニュー）。
export function editTween(layer, column) {
  return run("tween_edit", { layer, column });
}

/// 編集中のトゥイーンをやめる（Qt の cancel_transform_or_tween）。
export function cancelTween() {
  return run("tool_transform", { kind: "cancel" });
}

/// 編集中のトゥイーン（無ければ null）。
export function pendingTween() {
  return toolStore.view?.transform?.tween ?? null;
}

let panel = null;
let panelKey = null;

function closePanel() {
  panel?.remove();
  panel = null;
  panelKey = null;
}

function directionNote(reverse) {
  return reverse
    ? tr("逆生成：現在操作している変形形状をキーフレーム側の◆へ配置し、ラストコマ側を元の初期形状にします。")
    : tr("通常生成：キーフレーム側を元の初期形状、ラストコマ側を現在操作している変形形状にします。");
}

function buildPanel(transform) {
  const mesh = transform.mode === "mesh";
  const name = modeName(mesh);
  const tween = transform.tween;
  const description = mesh
    ? el("div", {}, el("b", {}, tr("メッシュ変形トゥイーン")), el("br"),
      tr("横{int}×縦{int2}の格子点を操作して、最後のコマの形を指定してください。", { int: transform.cols, int2: transform.rows }))
    : el("div", {}, el("b", {}, tr("自由変形トゥイーン")), el("br"),
      tr("白い点や枠を操作して、最後のコマの形を指定してください。"));
  const reverse = el("input", { type: "checkbox" });
  reverse.checked = !!tween.reverse;
  const note = el("div", { class: "tw-note" }, directionNote(reverse.checked));
  reverse.addEventListener("change", async () => {
    note.textContent = directionNote(reverse.checked);
    await run("tween_set_reverse", { reverse: reverse.checked });
  });
  const reverseRow = el("label", { class: "tw-check", title: tr("ON：操作中の変形形状をキーフレーム側へ置き、元の初期形状をラストコマ側へ置きます。") },
    reverse, tr("逆生成（◆ーーーー│）"));
  const button = (label, onclick) => el("button", { type: "button", onclick }, label);
  const rotate = el("div", { class: "tw-row" },
    button(tr("左へ90°"), () => run("tool_transform", { kind: "rotate_left" })),
    button(tr("右へ90°"), () => run("tool_transform", { kind: "rotate_right" })));
  const commit = button(tr("{name}を確定してトゥイーン作成", { name }), () => run("tool_transform", { kind: "commit" }));
  commit.classList.add("primary");
  const cancel = button(tr("キャンセル"), () => cancelTween());
  const box = el("div", { class: "tw-panel", role: "dialog" },
    el("div", { class: "tw-title" }, tr("長方形選択：{name}トゥイーン", { name })),
    description, reverseRow, note, rotate, commit, cancel);
  box.reverseInput = reverse;
  box.noteElement = note;
  return box;
}

function syncPanel(view) {
  const transform = view?.transform;
  if (!transform?.tween) {
    closePanel();
    return;
  }
  const t = transform.tween;
  const key = JSON.stringify([transform.mode, transform.cols, transform.rows, t.key, t.layer]);
  if (key !== panelKey) {
    closePanel();
    panel = buildPanel(transform);
    panelKey = key;
    document.body.append(panel);
  } else if (panel.reverseInput.checked !== !!t.reverse) {
    panel.reverseInput.checked = !!t.reverse;
    panel.noteElement.textContent = directionNote(!!t.reverse);
  }
}

/// 操作パネルを toolStore に合わせて出し入れする（app.js から一度だけ呼ぶ）。
export function installTween() {
  toolStore.subscribe(syncPanel);
}
