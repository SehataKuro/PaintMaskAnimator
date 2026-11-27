// 色のパネルの自己確認（PMA_COLORS_SELFTEST）。使用色パネル・カラーサークル・カラースライダー・
// カラーチャート・カラーピッカー・「色相・彩度・明度」を、画面の要素を押して動かし、
// Rust の状態（palette_view・ツールの設定 tools_state）と画面が合うかを調べて報告する。
//
// 1手ごとに `colors-step:名前` を報告して pause ms 待つ（そのあいだに窓を撮れる）。
// 色の多い作品を開いて使う（12色以上）。
import { adjustHsv, paletteOp, state, drawingOp } from "./color_state.js";
import { toolStore } from "./tools.js";

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function press(node, { ctrl = false, shift = false } = {}) {
  const r = node.getBoundingClientRect();
  const init = {
    bubbles: true,
    cancelable: true,
    clientX: r.left + r.width / 2,
    clientY: r.top + r.height / 2,
    button: 0,
    pointerId: 1,
    ctrlKey: ctrl,
    metaKey: false,
    shiftKey: shift,
  };
  node.dispatchEvent(new PointerEvent("pointerdown", init));
  node.dispatchEvent(new PointerEvent("pointerup", init));
}

async function until(test, ms = 5000) {
  const end = performance.now() + ms;
  while (performance.now() < end) {
    if (test()) return true;
    await wait(20);
  }
  return false;
}

export async function runColorsSelftest({ invoke, pause }) {
  const results = [];
  const check = (name, ok, detail = "") => results.push({ name, ok: !!ok, detail });
  const step = async (name) => {
    await invoke("report", { line: `colors-step:${name}` });
    await wait(pause);
  };
  const colorRows = () => [...document.querySelectorAll(".cu-row.cu-color")];
  // 描画色はツールの設定（config.json に保存される）なので、終わったら元へ戻す。
  const saved = (await invoke("tools_state")).settings;
  const KEYS = ["color_main", "color_sub", "color_mode", "background_color"];
  const view = () => state.palette;

  check("作品の使用色を数えた", await until(() => view()?.loaded && colorRows().length >= 12, 10000),
    `${colorRows().length} 行`);
  await step("normal");

  // 行を押して選ぶ。最後に押した色がフォーカス＝統合先。
  // 一覧は palette-changed のたびに作り直すので、押す直前に行を探し直す。
  const plainRows = () => colorRows().filter((n) => !n.textContent.startsWith("背景"));
  const pressRow = (i, opts) => {
    const node = plainRows()[i];
    node.scrollIntoView({ block: "nearest" });
    press(node.querySelector(".cu-code"), opts);
  };
  // Qt と同じく、ただのクリックはフォーカスだけ。Ctrl＋クリックで選択に足す。
  pressRow(0);
  await until(() => view()?.focus && view()?.selected_count === 0);
  pressRow(0, { ctrl: true });
  await until(() => view()?.selected_count === 1);
  pressRow(1, { ctrl: true });
  await until(() => view()?.selected_count === 2);
  const merge = document.querySelector(".cu-bottom-btn.merge");
  check("2色を選ぶと統合できる", view()?.selected_count === 2 && view()?.can_merge && !merge.disabled,
    `選択 ${view()?.selected_count}、統合 ${view()?.can_merge}`);
  check("統合の先を出す", merge.textContent.includes(view()?.focus?.slice(1) ?? "?"), merge.textContent);

  // 鍵の欄を押す（#104：描ける範囲を絞る）。
  colorRows()[3].scrollIntoView({ block: "nearest" });
  press(colorRows()[3].querySelector(".cu-lock"));
  await until(() => document.querySelectorAll(".cu-row.cu-color")[3]?.querySelector(".cu-lock.on"));
  check("鍵をかける", colorRows()[3].querySelector(".cu-lock.on") && view()?.restricted);
  await step("selected");

  // 置き換えに切り替える。行の高さは同じで、スクロールの位置も保つ（#105）。
  const scroll = document.querySelector(".cu-scroll");
  scroll.scrollTop = 30;
  const before = scroll.scrollTop;
  const heightBefore = colorRows()[1].getBoundingClientRect().height;
  document.querySelector(".cu-mode-btn.replace").click();
  await until(() => view()?.replace_mode && document.querySelector(".cu-replacement"));
  check("置き換えに切り替わる", view()?.replace_mode);
  check("スクロールの位置を保つ", scroll.scrollTop === before, `${before} → ${scroll.scrollTop}`);
  check("行の高さが変わらない", colorRows()[1].getBoundingClientRect().height === heightBefore);
  const bottomApply = document.querySelector(".cu-bottom-btn.apply");
  check("一番下の枠が［全コマを置き換え］になる",
    bottomApply.offsetParent !== null && merge.offsetParent === null);
  const unset = document.querySelectorAll(".cu-replacement.unset").length;
  check("未設定の置換色は斜線", unset === colorRows().length - 1, `${unset} 個`);
  const target = view().rows.find((r) => r.kind === "color" && !r.background);
  await paletteOp({ op: "set_replacement", targets: [target.rgb], color: [255, 0, 0] });
  await until(() => view()?.replacement_count === 1);
  check("置換色を決めると［全コマを置き換え］が押せる", view()?.replacement_count === 1 && !bottomApply.disabled);
  await step("replace");

  // カラーピッカー（置換色）を開いて、キャンセルで戻す。
  const second = colorRows().find((n) => n.querySelector(".cu-replacement.unset"));
  second.querySelector(".cu-replacement").click();
  await until(() => document.querySelector(".cp-dialog"));
  check("置換色のカラーピッカーが開く", document.querySelector(".cp-dialog"));
  await step("picker");
  [...document.querySelectorAll(".cp-dialog button")].find((b) => b.textContent === "キャンセル")?.click();
  await until(() => !document.querySelector(".cp-dialog"));
  await wait(200);
  check("キャンセルで置換色は変わらない", view()?.replacement_count === 1, `${view()?.replacement_count} 組`);

  // 親子付け（中央へ落とす）→ カラーチャートへ登録して窓で見る。
  const colors = view().rows.filter((r) => r.kind === "color" && !r.background);
  await paletteOp({ op: "drop", dragged: colors[2].rgb, target: colors[1].rgb, ratio: 0.5 });
  await paletteOp({ op: "drop", dragged: colors[3].rgb, target: colors[1].rgb, ratio: 0.5 });
  await until(() => view()?.rows.some((r) => r.parent));
  check("中央へ落とすと子になる", view()?.rows.filter((r) => r.parent).length === 2);
  document.querySelector(".cu-mode-btn:not(.replace)").click();
  await until(() => !view()?.replace_mode);
  [...document.querySelectorAll(".cu-icon-btn")].find((b) => b.textContent === "▦")?.click();
  await until(() => document.querySelector(".cc-window"));
  [...document.querySelectorAll(".cc-content > button")].find((b) => b.textContent === "現在の親子付けを登録")?.click();
  await until(() => document.querySelectorAll(".cc-tile").length >= 1);
  check("カラーチャートに親子が並ぶ", document.querySelectorAll(".cc-tile .cc-color.child").length === 2);
  await step("chart");
  document.querySelector(".cc-close")?.click();

  // 色相・彩度・明度の窓。
  const hsv = adjustHsv([colors[0].rgb, colors[4].rgb]);
  await until(() => document.querySelector(".hsv-dialog"));
  check("色相・彩度・明度の窓が開く", document.querySelector(".hsv-dialog"));
  await step("hsv");
  [...document.querySelectorAll(".hsv-dialog button")].find((b) => b.textContent === "キャンセル")?.click();
  await hsv;

  // 描画色：メインを変えるとスライダーとブラシの色が合う。サブ・背景色のモード。
  await drawingOp({ op: "set", which: "main", color: [12, 200, 99] });
  await drawingOp({ op: "mode", mode: "main" });
  await wait(100);
  const sliders = [...document.querySelectorAll(".cs-number")].map((n) => Number(n.value));
  check("スライダーがメインの色を出す", sliders.join(",") === "12,200,99", sliders.join(","));
  // 描画色の正本はツールの設定。ツールのパネルの色の欄とブラシ（paint_rgb）が同じ所を読む。
  const tools = (await invoke("tools_state")).settings;
  check("ツールの設定（ブラシの色）もメインの色", tools.color_main.join(",") === "12,200,99" && tools.color_mode === "main");
  document.querySelector(".cw-bg").click();
  await until(() => state.drawing?.mode === "transparent");
  check("背景色を選ぶとツールも背景色（消しゴム）", (await invoke("tools_state")).settings.color_mode === "transparent");
  // ツール側（E/C/X のキーと同じ tools_set）で変えると、サークルの欄が合わせる。
  await invoke("tools_set", { patch: { color_mode: "sub", color_sub: [1, 2, 3] } });
  await until(() => state.drawing?.mode === "sub");
  check("ツールで変えた描画色がサークルに出る",
    document.querySelector(".cw-well.sub")?.classList.contains("active") && state.drawing?.sub === "#010203");
  document.querySelector(".cw-well.main").click();
  await until(() => state.drawing?.mode === "main");
  await step("drawing");

  // メニュー・ショートカットの色の操作（shell.js の pma-action）を色のパネルが引き受ける。
  const action = (id) => {
    const e = new CustomEvent("pma-action", { detail: { id, source: "selftest" }, cancelable: true });
    window.dispatchEvent(e);
    return e.defaultPrevented;
  };
  await paletteOp({ op: "focus", rgb: colors[0].rgb });
  check("「この色を選択範囲にする」を引き受ける", action("a_focus_used_color"));
  await until(() => toolStore.view?.selection);
  check("フォーカスの色が画素の選択範囲になる", !!toolStore.view?.selection, JSON.stringify(toolStore.view?.selection?.bounds));
  await invoke("selection_edit", { args: { kind: "clear" } });
  check("「選択色だけ表示」「特定色表示を解除」を引き受ける", action("a_isolate_color") && action("a_clear_color_filter"));
  await invoke("tools_set", { patch: Object.fromEntries(KEYS.map((k) => [k, saved[k]])) });
  const ok = results.every((r) => r.ok);
  await invoke("report", {
    line: `色のパネルの自己確認：${ok ? "成功" : "失敗"}（${results.filter((r) => r.ok).length}/${results.length}）\n` +
      results.map((r) => `  ${r.ok ? "成功" : "失敗"}：${r.name}${r.detail ? `（${r.detail}）` : ""}`).join("\n"),
  });
  await invoke("quit", { code: ok ? 0 : 1 });
}
