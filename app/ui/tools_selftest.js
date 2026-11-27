// キーの持ち主の自己確認（PMA_TOOLS_SELFTEST=1）。
//
// ツールのキー（P）は shortcuts.js → `pma-action` → canvas_tools.js、選択範囲の解除（⌘D）は
// OS のメニュー（menu-action）→ shell.js → `pma-action` → canvas_tools.js の順に届く。
// それぞれ1回だけ届き、引き受けられ（「まだ移していません」を出さない）、結果が合うかを調べる。
import { toolStore } from "./tools.js";

const { invoke } = window.__TAURI__.core;

export async function runToolsSelftest({ shell }) {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const counts = {};
  const unclaimed = [];
  window.addEventListener("pma-action", (e) => {
    const id = e.detail?.id;
    counts[id] = (counts[id] ?? 0) + 1;
    // 引き受けた側は preventDefault する（同じ事件の後ろの聞き手なので、もう決まっている）。
    if (!e.defaultPrevented) unclaimed.push(id);
  });
  await wait(500);
  // 準備：選択範囲を作り、ブラシ以外のツールにする。
  await invoke("selection_edit", { args: { kind: "rect", points: [[0, 0], [2, 2]] } });
  await toolStore.set({ tool: "lasso" });
  await wait(200);
  const before = { tool: toolStore.settings.tool, selection: !!toolStore.view.selection };

  // P：画面のキー（メニューに無いキーは shortcuts.js が受ける）。
  document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "p", code: "KeyP", bubbles: true, cancelable: true }));
  document.body.dispatchEvent(new KeyboardEvent("keyup", { key: "p", code: "KeyP", bubbles: true, cancelable: true }));
  await wait(300);
  const afterP = toolStore.settings.tool;

  // ⌘D：画面に届いたキー（メニューのキーなので shortcuts.js は受けない）と、OS のメニューの選択。
  const mac = /Mac/.test(navigator.platform);
  document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "d", code: "KeyD", metaKey: mac, ctrlKey: !mac, bubbles: true, cancelable: true }));
  await wait(100);
  const viaKeyOnly = counts.a_selection_clear ?? 0;
  await shell.run("a_selection_clear", "menu", true);
  await wait(300);
  const afterD = !!toolStore.view.selection;
  const toast = document.querySelector(".sh-toast")?.textContent ?? "";

  const ok = before.tool === "lasso" && before.selection
    && afterP === "brush" && counts["tool:brush"] === 1
    && viaKeyOnly === 0 && counts.a_selection_clear === 1 && !afterD
    && unclaimed.length === 0 && !toast.includes("まだ移していません")
    && (counts["tool:dust"] ?? 0) === 0;
  await invoke("report", {
    line: `ツールのキーの自己確認：${ok ? "成功" : "失敗"}\n` +
      `  P：ツール ${before.tool} → ${afterP}、tool:brush ${counts["tool:brush"] ?? 0} 回\n` +
      `  ⌘D：キーだけでは ${viaKeyOnly} 回、メニューで a_selection_clear ${counts.a_selection_clear ?? 0} 回、選択範囲 ${before.selection ? "あり" : "なし"} → ${afterD ? "あり" : "なし"}\n` +
      `  引き受けなかった操作：${unclaimed.join("、") || "なし"}　知らせ：${toast || "なし"}`,
  });
  await invoke("quit", { code: ok ? 0 : 1 });
}
