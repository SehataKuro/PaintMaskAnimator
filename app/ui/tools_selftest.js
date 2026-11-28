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

  // 直線（pma-core の line_tool）：確定前の見た目と確定が同じ画素で、履歴は「描画…」が1つ。
  const summary = await invoke("summary");
  const [w, h] = [summary.width, summary.height];
  const line = { kind: "line", start: [w * 0.3, h * 0.5], end: [w * 0.7, h * 0.5], control: null, brushLine: false };
  const undoCount = async () => (await invoke("history")).undo.length;
  const lastLabel = async () => (await invoke("history")).undo.at(-1) ?? "";
  // 小さな見本（4×3）でも線が残るよう、保存してある入り抜き・太さに頼らない。
  const lineKeys = ["line_taper_in", "line_taper_out", "brush_size", "color_mode"];
  const savedLine = Object.fromEntries(lineKeys.map((k) => [k, toolStore.settings[k]]));
  await toolStore.set({ line_taper_in: false, line_taper_out: false, brush_size: 2, color_mode: "main" });
  const undo0 = await undoCount();
  const preview = new DataView(await invoke("tool_line_preview", { args: line }));
  const previewRect = [0, 4, 8, 12].map((o) => preview.getUint32(o, true));
  const written = new DataView(await invoke("tool_line", { start: line.start, end: line.end, control: null, brushLine: false }));
  const writtenRect = [0, 4, 8, 12].map((o) => written.getUint32(o, true));
  const lineOk = previewRect[2] > 0 && previewRect.join() === writtenRect.join()
    && (await undoCount()) === undo0 + 1 && (await lastLabel()).startsWith("描画");
  await toolStore.set(savedLine);

  // 変形：メニューの「選択範囲：自由変形」→ 中をドラッグ → Enter（変形を確定）。履歴は1つ。
  await invoke("selection_edit", { args: { kind: "rect", points: [[w * 0.2, h * 0.3], [w * 0.8, h * 0.7]] } });
  const undo1 = await undoCount();
  await shell.run("a_selection_transform", "menu", true);
  await wait(300);
  const began = !!toolStore.view.transform;
  const move = async (phase, x) => invoke("transform_drag", { phase, x, y: h * 0.5, zoom: 1 });
  const grabbed = await move("begin", w * 0.5);
  await move("move", w * 0.5 + 3);
  await move("end", w * 0.5 + 3);
  await wait(200);
  const moved = toolStore.view.transform?.history === 1;
  await shell.run("a_transform_commit", "menu", true);
  await wait(300);
  const transformOk = began && grabbed && moved && !toolStore.view.transform && !toolStore.view.selection
    && (await undoCount()) === undo1 + 1 && (await lastLabel()).startsWith("描画");

  // オニオン（onion.rs）：タイムラインの ON と設定の窓の値が Rust に届き、ずらしの
  // ドラッグ（設定の窓の「X・Yシフト」のあとのキャンバスのドラッグ）で値が動く。
  const onionBefore = await invoke("onion_state");
  await invoke("timeline_set_options", { onion: true });
  const shown = (await invoke("onion_state")).enabled;
  await invoke("onion_interaction", { mode: "shift", direction: -1, axis: "x" });
  const vs = { pan: [0, 0], rotation: 0, zoom: 1 };
  await invoke("onion_drag", { phase: "begin", x: 10, y: 10, rotate: false, view: vs });
  await invoke("onion_drag", { phase: "end", x: 15, y: 30, rotate: false, view: vs });
  const shifted = await invoke("onion_state");
  const onionOk = shown && shifted.previous.shift_x === onionBefore.previous.shift_x + 5
    && shifted.previous.shift_y === onionBefore.previous.shift_y && !shifted.interaction;
  await invoke("onion_set", { patch: { enabled: onionBefore.enabled, previous: { shift_x: onionBefore.previous.shift_x } } });

  const ok = before.tool === "lasso" && before.selection
    && afterP === "brush" && counts["tool:brush"] === 1
    && viaKeyOnly === 0 && counts.a_selection_clear === 1 && !afterD
    && unclaimed.length === 0 && !toast.includes("まだ移していません")
    && (counts["tool:dust"] ?? 0) === 0
    && lineOk && transformOk && counts.a_transform_commit === 1 && onionOk;
  await invoke("report", {
    line: `ツールのキーの自己確認：${ok ? "成功" : "失敗"}\n` +
      `  P：ツール ${before.tool} → ${afterP}、tool:brush ${counts["tool:brush"] ?? 0} 回\n` +
      `  ⌘D：キーだけでは ${viaKeyOnly} 回、メニューで a_selection_clear ${counts.a_selection_clear ?? 0} 回、選択範囲 ${before.selection ? "あり" : "なし"} → ${afterD ? "あり" : "なし"}\n` +
      `  直線：${lineOk ? "成功" : "失敗"}（見た目 ${previewRect.join(",")}、確定 ${writtenRect.join(",")}）\n` +
      `  変形：${transformOk ? "成功" : "失敗"}（始めた ${began}、つかんだ ${grabbed}、動いた ${moved}、確定 ${counts.a_transform_commit ?? 0} 回）\n` +
      `  オニオン：${onionOk ? "成功" : "失敗"}（表示 ${shown}、前のずらし ${shifted.previous.shift_x},${shifted.previous.shift_y}）\n` +
      `  引き受けなかった操作：${unclaimed.join("、") || "なし"}　知らせ：${toast || "なし"}`,
  });
  await invoke("quit", { code: ok ? 0 : 1 });
}
