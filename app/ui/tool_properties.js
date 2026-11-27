// ツールプロパティのパネル（Qt 版の toolpanel.py の ToolPanel）。
//
// 項目の並び・名前・範囲・出し分け（select_tool）は Qt と同じにする。値は toolStore
// （Rust の tools.rs の Settings）に読み書きする。バケツ・串刺し塗り・変形・ゴミ取りは、
// 計算を pma-core へ移しているところなので、ボタンは Rust の窓口（HOOK）を呼ぶだけ。
import { toolStore, toolLabel, el, openDialog, openMenu, openToolPreferences, hex } from "./tools.js";
import { setTip } from "./tooltip.js";
import { PressureEditor } from "./pressure.js";

const { invoke } = window.__TAURI__.core;

/// フッターに知らせを出す。
export function notify(text) {
  const status = document.getElementById("status");
  if (status) status.textContent = text;
}

const SELECTION_TOOLS = ["lasso", "rect_select", "auto_select"];

/// ツールの種類ごとの出し分けの元（Qt の select_tool の is_*）。
export function toolFlags(t, s) {
  const isSelection = SELECTION_TOOLS.includes(t);
  const isShapeTool = t === "shape";
  const isLine = t === "line" || (isShapeTool && ["straight", "curve"].includes(s.shape_type));
  const isBucket = t === "bucket" || t === "skewer_fill";
  return {
    isSelection,
    isShapeTool,
    isLine,
    isShape: isShapeTool && !isLine,
    isBucket,
    isDust: t === "dust",
    isLassoFill: t === "lasso_fill",
    usesSize: t === "brush" || isLine,
    usesRegion: isBucket || t === "auto_select",
  };
}

// 値をまとめて少し後に送る（スライダーを動かしているあいだ、毎回は保存しない）。
let pending = null;
let pendingTimer = 0;
function setSoon(patch) {
  pending = { ...(pending ?? {}), ...patch };
  clearTimeout(pendingTimer);
  pendingTimer = setTimeout(() => {
    const p = pending;
    pending = null;
    toolStore.set(p);
  }, 80);
}

export function createToolPropertiesPanel() {
  const panel = el("div", { class: "panel pma-props" });
  // {node, visible(t, s, f), enabled?(t, s, f), update?(s, view)}
  const parts = [];
  const add = (parent, node, visible, extra = {}) => {
    parent.appendChild(node);
    parts.push({ node, visible, ...extra });
    return node;
  };
  const always = () => true;

  // ---- 部品 ----
  const check = (key, label, tip) => {
    const input = el("input", { type: "checkbox" });
    input.addEventListener("change", () => toolStore.set({ [key]: input.checked }));
    const node = el("label", { class: "pma-check" }, input, label);
    if (tip) setTip(node, tip);
    node.update = (s) => (input.checked = !!s[key]);
    node.input = input;
    return node;
  };
  const range = (key, min, max, { invert = false, onInput } = {}) => {
    const input = el("input", { type: "range", min, max, step: 1, class: "pma-range" });
    if (invert) input.classList.add("inverted");
    input.addEventListener("input", () => {
      const v = Number(input.value);
      onInput?.(v);
      setSoon({ [key]: v });
    });
    input.update = (s) => {
      if (document.activeElement !== input) input.value = s[key];
      onInput?.(Number(input.value));
    };
    return input;
  };
  const label = (text) => el("div", { class: "pma-label", text });
  const combo = (key, items, tip, after) => {
    const select = el("select", { class: "pma-combo" });
    items.forEach(([text, value], i) => select.appendChild(el("option", { value: String(i), text, _value: value })));
    select.addEventListener("change", () => {
      const value = items[Number(select.value)][1];
      const patch = { [key]: value };
      after?.(value, patch);
      toolStore.set(patch);
    });
    if (tip) setTip(select, tip);
    select.update = (s) => {
      const i = items.findIndex(([, v]) => v === s[key]);
      select.value = String(Math.max(0, i));
    };
    return select;
  };
  const row = (...children) => el("div", { class: "pma-row" }, ...children);
  const button = (text, onclick, tip) => el("button", { type: "button", class: "pma-button", text, onclick, tip });
  const updateAll = (node, s, view) => {
    node.update?.(s, view);
    for (const child of node.querySelectorAll("*")) child.update?.(s, view);
  };

  // ---- 使っているツールの名前 ----
  const active = add(panel, el("div", { class: "pma-active" }), always, { update: (s) => (active.textContent = toolLabel(s.tool)) });

  // ---- ツールコマンド ----
  const box = add(panel, el("div", { class: "pma-command-box" }), (t, s, f) =>
    f.isSelection || f.isLine || f.isShapeTool || f.isBucket || f.isDust || f.isLassoFill);
  box.appendChild(el("div", { class: "pma-section", text: "ツールコマンド" }));
  const sel = (t, s, f) => f.isSelection;
  const transform = (kind) => async () => {
    try {
      await invoke("tool_transform", { kind });
    } catch (e) {
      notify(String(e));
    }
  };
  add(box, button("自由変形", transform("free")), sel);
  add(box, button("拡大縮小", transform("scale")), sel);
  add(box, button("メッシュ変形", transform("mesh")), sel);
  const clear = add(box, button("選択範囲を解除", () => invoke("selection_edit", { args: { kind: "clear" } }),
    "選択ツール使用時に、現在の選択範囲を解除します。どのツールからでも右上の「選択解除」を使用できます。"), sel,
  { update: (s, view) => (clear.disabled = !view.can_clear_selection) });
  add(box, button("左へ90°回転", transform("rotate_left")), sel);
  add(box, button("右へ90°回転", transform("rotate_right")), sel);
  add(box, label("メッシュ格子数"), sel);
  for (const [key, prefix] of [["transform_mesh_x", "横 "], ["transform_mesh_y", "縦 "]]) {
    const spin = el("input", { type: "number", min: 2, max: 12, step: 1, class: "pma-spin" });
    setTip(spin, "メッシュ変形中でも格子数を変更できます。");
    const slider = range(key, 2, 12, { onInput: (v) => document.activeElement !== spin && (spin.value = v) });
    spin.addEventListener("change", () => toolStore.set({ [key]: Number(spin.value) }));
    spin.update = (s) => (spin.value = s[key]);
    add(box, row(el("span", { class: "pma-prefix", text: prefix }), spin), sel);
    add(box, slider, sel);
  }
  add(box, check("transform_quality", "クオリティ（Tp_mask v0.7方式）", "色ごとに分離して変形し、中間色を作らずに再合成します。拡大・縮小・回転で線や塗りが崩れにくくなります。"), sel);
  add(box, el("div", { class: "pma-note", text: "色選択があるときは実線の太さを調整できます。" }), sel);
  // 実線の太さは、使用色パネルで色を選んでいるときだけ出す（使用色パネルができたらつなぐ）。
  const lineWidthLabel = label("");
  add(box, lineWidthLabel, () => false);
  const lineWidth = range("transform_line_width", 1, 254, { invert: true, onInput: (v) => (lineWidthLabel.textContent = `実線の太さ：${255 - v}`) });
  setTip(lineWidth, "クオリティ変形で、使用色パネルの選択色を実線として残す太さを調整します。右へ動かすほど太くなります。");
  add(box, lineWidth, () => false);
  const allFrames = add(box, check("selection_all_frames", "すべてのコマに適用"), sel);
  setTip(allFrames, () => "選択範囲の変形をすべてのコマへ適用します。" + (toolStore.settings?.transform_quality ? "クオリティ変形はコマごとに時間がかかります。" : ""));
  add(box, check("transform_as_tween", "保持のあるコマの変形はトゥイーンにする",
    "2コマ以上保持しているキーのコマで自由変形・メッシュ変形を始めると、確認なしでトゥイーンになります。ハンドルを動かして確定すると、保持の各コマに中割りができます。"), sel);
  add(box, button("変形を確定", transform("commit")), sel);
  add(box, button("変形をキャンセル", transform("cancel")), sel);

  // ---- 図形・ライン ----
  const curveTip = "曲線は、1回目のドラッグで始点と終点を決め、次のクリックで弓なりのカーブを確定します。";
  add(box, label("図形種類"), (t, s, f) => f.isShapeTool);
  add(box, combo("shape_type", [["直線", "straight"], ["曲線", "curve"], ["多角形", "polygon"], ["楕円", "ellipse"]], curveTip,
    (value, patch) => {
      if (value === "straight" || value === "curve") patch.line_type = value;
    }), (t, s, f) => f.isShapeTool);
  add(box, label("ライン種類"), (t) => t === "line");
  add(box, combo("line_type", [["直線", "straight"], ["曲線", "curve"]], curveTip), (t) => t === "line");
  const line = (t, s, f) => f.isLine;
  for (const which of ["in", "out"]) {
    const name = which === "in" ? "入り" : "抜き";
    const key = `line_taper_${which}`;
    add(box, check(key, name), line);
    const sizeLabel = el("div", { class: "pma-label pma-clickable" });
    setTip(sizeLabel, `クリックすると${name}カーブ設定がポップアップします。`);
    sizeLabel.addEventListener("click", (e) => {
      if (!sizeLabel.classList.contains("disabled")) openTaperPopup(which, e.clientX, e.clientY);
    });
    const slider = range(`${key}_size`, 1, 800, { onInput: (v) => (sizeLabel.textContent = `${name}サイズ：${(v / 2).toFixed(1)} px`) });
    const on = (t, s, f) => f.isLine && s[key];
    add(box, sizeLabel, line, { enabled: on });
    add(box, slider, line, { enabled: on });
  }
  const shape = (t, s, f) => f.isShape;
  const polygon = (t, s, f) => f.isShape && s.shape_type === "polygon";
  add(box, label("角の数"), shape, { enabled: polygon });
  const corners = el("input", { type: "number", min: 3, max: 32, step: 1, class: "pma-spin" });
  corners.addEventListener("change", () => toolStore.set({ shape_corners: Number(corners.value) }));
  corners.update = (s) => (corners.value = s.shape_corners);
  add(box, corners, shape, { enabled: polygon });
  add(box, range("shape_corners", 3, 32, { onInput: (v) => document.activeElement !== corners && (corners.value = v) }), shape, { enabled: polygon });
  add(box, check("shape_lock_ratio", "比率固定"), shape);
  add(box, check("shape_fill_inside", "内側を塗る"), shape);
  add(box, check("shape_sub_outline_main_fill", "サブ色を実線、メイン色を内面にする"), shape);
  const shapeWidthLabel = add(box, label(""), shape);
  add(box, range("shape_outline_width", 1, 80, { onInput: (v) => (shapeWidthLabel.textContent = `線の太さ：${(v / 2).toFixed(1)} px`) }), shape);

  // ---- 投げ縄塗り ----
  const lasso = (t, s, f) => f.isLassoFill;
  const lassoWidthOn = (t, s, f) => f.isLassoFill && s.lasso_main_outline_sub_fill;
  add(box, check("lasso_inside_boundary", "境界線の内側だけを塗る"), lasso);
  add(box, check("lasso_main_outline_sub_fill", "サブ色を実線、メイン色を内面にする"), lasso);
  const lassoWidthLabel = add(box, label(""), lasso, { enabled: lassoWidthOn });
  add(box, range("lasso_outline_width", 1, 40, { onInput: (v) => (lassoWidthLabel.textContent = `外線の太さ：${(v / 2).toFixed(1)} px`) }), lasso, { enabled: lassoWidthOn });

  // ---- バケツ（串刺し塗り・自動選択・投げ縄塗りも使う） ----
  const bucketOrLasso = (t, s, f) => f.isBucket || f.isLassoFill;
  const region = (t, s, f) => f.usesRegion;
  const bucket = (t, s, f) => f.isBucket;
  add(box, row(el("span", { class: "pma-row-title", text: "参照" }), combo("bucket_reference", [
    ["選択したレイヤーのみ", "layer"], ["キャンバス全体", "canvas"], ["色塗り前の初期状態", "initial"],
  ], "どの絵を見て塗る範囲を決めるかを選びます。塗る先はいつも選択中のレイヤーです。\n「選択したレイヤーのみ」はそのレイヤーの絵だけで判定します。\n「キャンバス全体」は表示しているレイヤーを合成した絵で判定するので、線が別のレイヤーにあっても、その線で区切られた部分を塗れます。\n「色塗り前の初期状態」は保持した塗る前の絵で判定するので、塗りかけの色に邪魔されません（保持していなければ今の絵で判定します）。")), bucketOrLasso);
  add(box, row(el("span", { class: "pma-row-title", text: "対象色" }), combo("bucket_target_color", [
    ["すべての色を対象", "all"], ["透明部分のみ", "transparent"], ["透明に囲まれた部分", "surrounded_by_transparent"],
    ["黒部分のみ", "black"], ["黒に囲まれた部分", "surrounded_by_black"], ["白と透明部分のみ", "white_transparent"],
    ["白と透明に囲まれた部分", "surrounded_by_white_transparent"], ["薄い半透明を透明扱い", "faint_as_transparent"],
    ["透明以外と内部の透明", "opaque_and_inner_transparent"], ["透明以外は閉領域にも", "transparent_and_closed_opaque"],
  ], "どの画素をたどって塗るかを選びます。「すべての色を対象」はクリックした色の領域を塗ります。「〜のみ」はその画素だけ、「〜に囲まれた部分」はそれ以外の画素を、クリック位置からつながる範囲で塗ります。")), bucketOrLasso);
  add(box, check("bucket_adjacent", "隣接", "ON：クリック位置につながる同色領域だけを塗ります。OFF：レイヤー内の同じ色を一括で塗ります。"), region);
  add(box, check("bucket_include_sub", "含み塗り", "ON：塗る領域に接している「含む色」の部分も一緒に塗ります。色トレス線の色を登録しておくと、線ごと塗りつぶせます。色を登録していないときはサブカラーを含みます。"), bucket);
  add(box, includeColorSlots(), bucket);
  add(box, check("bucket_island_replace", "囲まれた色を置換色で塗る", "塗った範囲に囲まれた線や点のうち、使用色パネルで置換色を登録してある色を、その置換色で塗ります。頬の範囲線の中を塗るときに、中の斜線も決まった色にできます。"), bucket);
  // 隙間閉じ（自動選択では チェックは出さず、幅だけ）。
  const gapLabel = el("span", { class: "pma-value" });
  const gapCheck = check("bucket_close_gap", "隙間閉じ");
  const gapOn = (t, s, f) => s.bucket_adjacent && s.bucket_close_gap;
  const gapSlider = range("bucket_gap_width", 1, 20, { onInput: (v) => (gapLabel.textContent = `${v} px`) });
  const gapRow = add(box, row(gapCheck, gapSlider, gapLabel), region);
  parts.push({ node: gapCheck, visible: bucket, enabled: (t, s) => s.bucket_adjacent });
  parts.push({ node: gapSlider, visible: always, enabled: gapOn });
  parts.push({ node: gapLabel, visible: always, enabled: gapOn });
  void gapRow;
  const islandLabel = el("span", { class: "pma-value" });
  const islandOn = (t, s) => s.bucket_island_fill;
  const islandSlider = range("bucket_island_fill_width", 1, 20, { onInput: (v) => (islandLabel.textContent = `幅 ${v} px 以下`) });
  const islandCheck = check("bucket_island_fill", "塗り残し拾い", "塗った領域と角で直接つながる、同じ対象色の空白を指定した幅以下なら先端まで拾います。長さや面積は制限せず、線は飛び越えません。自動塗りにも適用します。");
  add(box, row(islandCheck, islandSlider, islandLabel), region);
  parts.push({ node: islandSlider, visible: always, enabled: islandOn });
  parts.push({ node: islandLabel, visible: always, enabled: islandOn });
  const extendLabel = el("span", { class: "pma-value" });
  const extendOn = (t, s) => s.bucket_adjacent && s.bucket_extend_lines;
  const extendCheck = check("bucket_extend_lines", "線の延長", "キャンバス端の手前で途切れた線を、線の向きのまま端まで延ばしたものとして塗ります。スタンダードフレームの外側で線を端まで引いていない素材でも、線の切れ目から隣へ漏れずに塗れます。端からスライダーの長さより遠い線の端は延長しません。");
  const extendSlider = range("bucket_extend_length", 10, 1000, { onInput: (v) => (extendLabel.textContent = `${v} px`) });
  add(box, row(extendCheck, extendSlider, extendLabel), bucket);
  parts.push({ node: extendCheck, visible: always, enabled: (t, s) => s.bucket_adjacent });
  parts.push({ node: extendSlider, visible: always, enabled: extendOn });
  parts.push({ node: extendLabel, visible: always, enabled: extendOn });
  add(box, check("bucket_require_closed", "領域が開いている場合は塗りを開始しない"), region, { enabled: (t, s) => s.bucket_adjacent });
  const opacityTip = "塗りつぶし・投げ縄塗りの不透明度。100%は選択した色をそのまま塗ります（近似色は増えません）。100%未満は下地の色と混ぜた色で塗ります。";
  const opacityLabel = el("span", { class: "pma-value" });
  const opacityRow = add(box, row(el("span", { class: "pma-row-title", text: "不透明度" }),
    range("fill_opacity", 1, 100, { onInput: (v) => (opacityLabel.textContent = `${v}%`) }), opacityLabel), bucketOrLasso);
  setTip(opacityRow, opacityTip);
  add(box, el("div", { class: "pma-note", text: "クリックした色と完全に同じ色の画素だけを、つながりに関係なく、選択レイヤーのすべてのコマで今の色へ置き換えます。選択範囲があれば、その中だけを置き換えます。線の色をクリックしたときは確認します。" }), (t) => t === "replace_color");

  // ---- ゴミ取り ----
  const dust = (t, s, f) => f.isDust;
  const tidy = (t, s, f) => f.isDust && s.dust_mode === "line_tidy";
  const dustModes = [["ゴミ取り", "despeckle"], ["塗り抜け", "fill_holes"], ["線周りのノイズ", "line_noise"], ["線整え", "line_tidy"]];
  add(box, label("処理モード"), dust);
  add(box, combo("dust_mode", dustModes, "ゴミ取り：小さな色点を白（#FFFFFF）へ変更します。塗り抜け：小さな白い穴を周囲色で埋めます。線周りのノイズ：線の近くで、まわりに同じ色が1つも無い孤立した1ドットを、8近傍の多数決でまわりの色へ寄せます。色面のふちは同じ色の隣を必ず持つため、塗り分けは壊れません。線整え：キャンバスをブラシか投げ縄でなぞった所の線を整えます（角だけで接している段をつなぎ、突き出た1画素を消す）。"), dust);
  const dustSizeLabel = add(box, label(""), (t, s, f) => f.isDust && !tidy(t, s, f));
  add(box, range("dust_size", 1, 100, { onInput: (v) => (dustSizeLabel.textContent = `適用サイズ：${v} px`) }), (t, s, f) => f.isDust && !tidy(t, s, f));
  add(box, label("なぞり方"), tidy);
  add(box, combo("line_tidy_shape", [["ブラシ", "brush"], ["投げ縄", "lasso"]], "キャンバスをなぞった所の線を整えます。ブラシはなぞった帯、投げ縄は囲んだ中が対象です。離したときに整えます。"), tidy);
  const tidyBrush = (t, s, f) => tidy(t, s, f) && s.line_tidy_shape === "brush";
  const tidySizeLabel = add(box, label(""), tidyBrush);
  add(box, range("line_tidy_brush_size", 2, 200, { onInput: (v) => (tidySizeLabel.textContent = `ブラシの太さ：${v} px`) }), tidyBrush);
  add(box, label("2×2の45°の線"), tidy);
  add(box, combo("line_tidy_diagonal", [["足して太さをそろえる", "add"], ["消して1画素幅にする", "remove"], ["直さない", null]]), tidy);
  add(box, el("div", { class: "pma-note", text: "なぞった所だけ整えます。下のボタンは、選択範囲があればその中、無ければコマ全体を整えます。" }), tidy);
  add(box, check("dust_selected_only", "選択色を対象", "使用色パネルで選択している色だけを対象にします。色ごとに独立判定するため、別色と隣接していても小さな選択色を削除できます。"), dust);
  add(box, check("dust_all_frames", "選択レイヤーのすべてのコマに適用"), dust);
  const dustApply = add(box, button("", async () => {
    try {
      await invoke("tool_dust_apply");
    } catch (e) {
      notify(String(e));
    }
  }), dust, { update: (s) => (dustApply.textContent = `${dustModes.find(([, v]) => v === s.dust_mode)?.[0] ?? "ゴミ取り"}を適用`) });

  // ---- ブラシサイズ・ラインサイズ ----
  const sizeTitle = el("b");
  const pressureButton = el("button", { type: "button", class: "pma-button pma-small", text: "筆圧…", onclick: openPressureDialog });
  add(panel, row(sizeTitle, el("span", { class: "pma-stretch" }), pressureButton), (t, s, f) => f.usesSize, {
    update: (s) => {
      const f = toolFlags(s.tool, s);
      sizeTitle.textContent = f.isLine ? "ラインサイズ" : "ブラシサイズ";
      pressureButton.style.display = s.tool === "brush" ? "" : "none";
    },
  });
  const sizeValue = el("span", { class: "pma-size-value" });
  setTip(sizeValue, () => "▲▼をクリック：0.5ずつ変更" + (toolStore.settings?.tool === "brush" ? "／数値部分をクリック：筆圧設定" : ""));
  sizeValue.addEventListener("click", () => toolStore.settings?.tool === "brush" && openPressureDialog());
  const step = (d) => toolStore.set({ brush_size: Math.max(0.5, Math.min(400, toolStore.settings.brush_size + d)) });
  const sizeSlider = el("input", { type: "range", min: 1, max: 800, step: 1, class: "pma-range" });
  sizeSlider.addEventListener("input", () => {
    sizeValue.textContent = (Number(sizeSlider.value) / 2).toFixed(1);
    setSoon({ brush_size: Number(sizeSlider.value) / 2 });
  });
  sizeSlider.update = (s) => {
    if (document.activeElement !== sizeSlider) sizeSlider.value = Math.round(s.brush_size * 2);
    sizeValue.textContent = s.brush_size.toFixed(1);
  };
  add(panel, row(sizeSlider, el("span", { class: "pma-size" }, sizeValue,
    el("span", { class: "pma-steppers" },
      el("button", { type: "button", text: "▲", onclick: () => step(0.5) }),
      el("button", { type: "button", text: "▼", onclick: () => step(-0.5) })))), (t, s, f) => f.usesSize);
  const stabLabel = add(panel, label(""), (t) => t === "brush");
  const stab = range("stabilizer", 0, 300, { onInput: (v) => (stabLabel.textContent = `手振れ補正：${v}`) });
  setTip(stab, "ブラシ軌跡を移動平均と遅延半径で滑らかにします。0～300。値が大きいほど補正を強くします。");
  add(panel, stab, (t) => t === "brush");

  function render(view) {
    if (!view) return;
    const s = view.settings;
    const t = s.tool;
    const f = toolFlags(t, s);
    for (const part of parts) {
      part.node.style.display = part.visible(t, s, f) ? "" : "none";
      if (part.enabled) {
        const on = part.enabled(t, s, f);
        part.node.classList.toggle("disabled", !on);
        if ("disabled" in part.node && part.node.tagName !== "DIV") part.node.disabled = !on;
        for (const input of part.node.querySelectorAll?.("input,select,button") ?? []) input.disabled = !on;
      }
      if (part.update) part.update(s, view);
    }
    updateAll(panel, s, view);
  }
  toolStore.subscribe(render);
  return panel;
}

// ---------------------------------------------------------------------------
// 含む色（Qt の IncludeColorSlots）
// ---------------------------------------------------------------------------

function includeColorSlots() {
  const node = el("div", { class: "pma-row pma-include" }, el("span", { class: "pma-row-title", text: "含む色" }));
  const slots = [0, 1, 2].map((i) => {
    const slot = el("button", { type: "button", class: "pma-slot" });
    slot.addEventListener("click", () => register(i));
    slot.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      const s = toolStore.settings;
      const items = [];
      if (s.bucket_include_colors[i]) {
        items.push({
          label: "この色を含み塗りに使う",
          checked: s.bucket_include_colors_enabled[i],
          action: () => {
            const enabled = [...s.bucket_include_colors_enabled];
            enabled[i] = !enabled[i];
            toolStore.set({ bucket_include_colors_enabled: enabled });
          },
        }, "-");
      }
      items.push({ label: "現在の描画色を登録", action: () => register(i) });
      items.push({
        label: "消去",
        action: () => {
          const colors = [...s.bucket_include_colors];
          colors[i] = null;
          toolStore.set({ bucket_include_colors: colors });
        },
      });
      openMenu(e.clientX, e.clientY, items);
    });
    setTip(slot, () => {
      const s = toolStore.settings;
      const color = s?.bucket_include_colors[i];
      if (!color) return "クリックで現在の描画色を含み塗りの色に登録します。";
      const code = hex(color).toUpperCase();
      return s.bucket_include_colors_enabled[i]
        ? `${code}\nクリックで現在の描画色に置き換え、右クリックで ON/OFF と消去ができます。`
        : `${code}（含み塗りに使っていません）\n右クリックで ON に戻せます。`;
    });
    node.appendChild(slot);
    return slot;
  });
  // 登録する色（背景色のモードでは白）。登録したら含み塗りを入れる。
  function register(i) {
    const s = toolStore.settings;
    const rgb = s.color_mode === "transparent" ? [255, 255, 255] : s.color_mode === "sub" ? s.color_sub : s.color_main;
    const colors = [...s.bucket_include_colors];
    const enabled = [...s.bucket_include_colors_enabled];
    colors[i] = rgb;
    enabled[i] = true;
    toolStore.set({ bucket_include_colors: colors, bucket_include_colors_enabled: enabled, bucket_include_sub: true });
  }
  node.update = (s) => {
    slots.forEach((slot, i) => {
      const color = s.bucket_include_colors[i];
      slot.classList.toggle("empty", !color);
      slot.classList.toggle("off", !!color && !s.bucket_include_colors_enabled[i]);
      slot.style.setProperty("--slot", color ? hex(color) : "transparent");
    });
  };
  return node;
}

// ---------------------------------------------------------------------------
// 入り／抜きカーブ（Qt の LineTaperCurvePopup）
// ---------------------------------------------------------------------------

let taperPopup = null;
function openTaperPopup(which, x, y) {
  taperPopup?.remove();
  const key = `line_taper_${which}_curve`;
  const value = el("div", { class: "pma-center" });
  const slider = el("input", { type: "range", min: 20, max: 400, step: 1, class: "pma-range" });
  slider.value = toolStore.settings[key];
  const show = () => (value.textContent = (Number(slider.value) / 100).toFixed(2));
  slider.addEventListener("input", () => {
    show();
    setSoon({ [key]: Number(slider.value) });
  });
  show();
  const popup = el("div", { class: "pma-popup" },
    el("b", { text: which === "in" ? "入りカーブ" : "抜きカーブ" }),
    value, slider,
    el("div", { class: "pma-small-note", text: "小さいほど緩やか、大きいほど先端付近で急に変化します。" }));
  document.body.appendChild(popup);
  popup.style.left = `${Math.max(4, Math.min(x, window.innerWidth - popup.offsetWidth - 4))}px`;
  popup.style.top = `${Math.max(4, Math.min(y, window.innerHeight - popup.offsetHeight - 4))}px`;
  taperPopup = popup;
  const away = (e) => {
    if (!popup.contains(e.target)) {
      popup.remove();
      window.removeEventListener("pointerdown", away, true);
    }
  };
  setTimeout(() => window.addEventListener("pointerdown", away, true));
}

// ---------------------------------------------------------------------------
// ブラシの筆圧（Qt の PressureDialog）
// ---------------------------------------------------------------------------

export function openPressureDialog() {
  const s = toolStore.settings;
  if (!s) return;
  let brush = { ...s.brush_pressure };
  const name = `pressure-mode-${Date.now()}`;
  const useGlobal = el("input", { type: "radio", name, checked: s.pressure_uses_global });
  const useBrush = el("input", { type: "radio", name, checked: !s.pressure_uses_global });
  const preset = el("select", { class: "pma-combo" }, ...s.pressure_presets.map((p) => el("option", { value: p.name, text: p.name })));
  preset.value = s.pressure_active_preset;
  const edit = el("button", { type: "button", text: "プリセットを編集…", onclick: () => openToolPreferences() });
  const editor = new PressureEditor(brush);
  editor.onchange = (v) => {
    if (useBrush.checked) brush = v;
  };
  const sync = () => {
    const global = useGlobal.checked;
    preset.disabled = !global;
    editor.setEnabled(!global);
    editor.element.style.opacity = global ? "0.45" : "1";
    if (global) {
      const p = toolStore.settings.pressure_presets.find((x) => x.name === preset.value) ?? toolStore.settings.pressure_presets[0];
      editor.setSettings(p);
    } else {
      editor.setSettings(brush);
    }
  };
  useGlobal.addEventListener("change", sync);
  useBrush.addEventListener("change", sync);
  preset.addEventListener("change", sync);
  const content = el("div", { class: "pma-pressure-dialog" },
    el("label", {}, useGlobal, "全体の設定を使う"),
    el("div", { class: "pma-row pma-indent" }, el("span", { text: "プリセット" }), preset, edit),
    el("label", {}, useBrush, "このブラシの設定を使う"),
    editor.element);
  sync();
  openDialog("ブラシの筆圧", content, [
    { label: "キャンセル" },
    {
      label: "OK",
      primary: true,
      action: () => toolStore.set({
        pressure_uses_global: useGlobal.checked,
        pressure_active_preset: preset.value,
        brush_pressure: brush,
      }),
    },
  ]);
}
