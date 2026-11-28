// アクションのパネル（Qt 版の actionpanel.py）。
//
// Qt 版は actions フォルダーの Python のスクリプトがボタンを足す。0.9.0 は Python を
// 配布しないので、組み込みの2つ（同一画像から色置換・MainLineRepaint）だけを並べ、
// 押したら Rust の窓口（tools.rs の action_run、HOOK）を呼ぶ。
import { el } from "./tools.js";
import { notify } from "./tool_properties.js";
import { message } from "./dialogs.js";
import { tr } from "./i18n.js";

const { invoke } = window.__TAURI__.core;

const BUILTIN = [
  ["same_image_replacement", "同一画像から色置換", "同じタイムライン位置にある上のレイヤーと画素配置を比較し、一致した色対応をそのまま実画像へ適用します。"],
  ["main_line_repaint", "MainLineRepaint", "メイン色・サブ色を線レイヤーへ分離し、抜けた面を周囲の最多色で埋めます。"],
];

export function createActionsPanel() {
  const panel = el("div", { class: "panel pma-actions" });
  for (const [id, label, tip] of BUILTIN) {
    panel.appendChild(el("button", {
      type: "button",
      class: "pma-button",
      text: label,
      tip,
      onclick: () => runBuiltinAction(id),
    }));
  }
  return panel;
}

/// 確かめの窓を出してから Rust の action_run を呼ぶ（Qt の各アクションの最初の QMessageBox）。
/// 知らせはステータスバー、断りは窓で出す（Qt と同じ）。
export async function runBuiltinAction(id) {
  const title = BUILTIN.find(([key]) => key === id)?.[1] ?? id;
  if (id === "main_line_repaint") {
    const ok = await message(
      title,
      tr("選択した使用色と、それ以外の部分を別レイヤーに分離します。"),
      [[tr("OK"), true], [tr("キャンセル"), false, true]],
      false,
    );
    if (!ok) return;
  }
  try {
    const status = await invoke("action_run", { id });
    if (status) notify(tr(status));
  } catch (e) {
    if (id === "main_line_repaint") await message(title, tr(String(e)));
    else notify(String(e));
  }
}
