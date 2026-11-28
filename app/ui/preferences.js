// 【アプリの外枠】環境設定（Qt 版の preferences_dialog.PreferencesDialog）。
//
// 変えたらすぐ保存する（Qt と同じく OK・キャンセルは無い）。値は Rust の prefs_set が丸めて
// 保存し、丸めたあとの全体（Resolved）を返す。

import { tr } from "./i18n.js";
import { el, openDialog, message, prompt, ask } from "./dialogs.js";

const SYSTEM = "system";
const ACCENT_PRESETS = [
  ["ブルー", "#2f6fed"],
  ["ティール", "#0d9488"],
  ["グリーン", "#1a8a4a"],
  ["パープル", "#7c4dff"],
  ["オレンジ", "#e0730a"],
  ["レッド", "#d64545"],
];
const DEFAULT_MAP_PALETTE = [
  "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4",
  "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", "#008080", "#e6beff",
];
const PRESSURE_DEFAULT = { enabled: true, minimum: 0.1, maximum: 1.0, points: [[0, 0], [0.5, 0.5], [1, 1]] };

const hint = (text) => el("div", { class: "sh-hint" }, text);
const label = (text) => el("span", { class: "sh-label" }, text);
const form = (...rows) => {
  const node = el("div", { class: "sh-form" });
  for (const row of rows) {
    if (row instanceof Node) node.append(row);
    else node.append(label(row[0]), row[1]);
  }
  return node;
};
function select(options, value, onchange) {
  const node = el("select");
  for (const [v, text] of options) node.append(el("option", { value: v }, text));
  node.value = value;
  node.addEventListener("change", () => onchange(node.value));
  return node;
}
function spin(value, [min, max], step, suffix, onchange) {
  const input = el("input", { type: "number", min: String(min), max: String(max), step: String(step), value: String(value), style: { width: "90px" } });
  input.addEventListener("change", () => {
    const v = Math.max(min, Math.min(max, Math.round(Number(input.value) || min)));
    input.value = String(v);
    onchange(v);
  });
  return el("span", { class: "sh-row" }, input, suffix ? el("span", {}, suffix.trim()) : null);
}
function check(text, value, onchange) {
  const box = el("input", { type: "checkbox", checked: value });
  box.addEventListener("change", () => onchange(box.checked));
  return el("label", { class: "sh-check" }, box, text);
}
/// 色を選ぶ（Qt の ColorPickerDialog の代わりに OS の色の窓）。
function pickColor(initial) {
  return new Promise((resolve) => {
    const input = el("input", { type: "color", value: initial, style: { position: "fixed", left: "-100px" } });
    document.body.append(input);
    let done = false;
    const finish = (v) => {
      if (done) return;
      done = true;
      input.remove();
      resolve(v);
    };
    input.addEventListener("change", () => finish(input.value));
    input.addEventListener("blur", () => setTimeout(() => finish(null), 300));
    input.click();
  });
}

/// 環境設定の窓。section は最初に出す場所（"workspaces" など）。
export async function preferencesDialog(ctx, section = "general") {
  const { invoke, shell } = ctx;
  let prefs = await invoke("prefs_get");
  const set = async (key, value) => {
    try {
      prefs = await invoke("prefs_set", { key, value });
      shell.onPrefsChanged(prefs, key);
    } catch (e) {
      await message(tr("環境設定"), String(e));
    }
    return prefs;
  };

  // ---- 一般 ----
  function general() {
    const languages = [[SYSTEM, tr("システムに合わせる")], ...ctx.languages];
    const interval = spin(prefs.autosave_interval_minutes, [1, 60], 1, tr(" 分ごと"), (v) => set("autosave_interval_minutes", v));
    const holdMs = spin(prefs.tool_hold_switch_ms, [0, 3000], 50, tr(" ms 以上押したら"), (v) => set("tool_hold_switch_ms", v));
    const enable = (node, on) => node.querySelectorAll("input").forEach((i) => (i.disabled = !on));
    enable(interval, prefs.autosave_enabled);
    enable(holdMs, prefs.tool_hold_switch_enabled);
    return form(
      [tr("言語 / Language"), select(languages, prefs.language, (v) => set("language", v))],
      hint(tr("言語は次回の起動から切り替わります。")),
      [tr("時間の表示"), select([["frames", tr("1コマ目")], ["seconds_frames", "0+1"]], prefs.time_display_mode, (v) => set("time_display_mode", v))],
      [tr("背景色選択時の描画ツール"), select([["brush", tr("ブラシ")], ["bucket", tr("バケツ")]], prefs.background_color_tool, (v) => set("background_color_tool", v))],
      hint(tr("描画ツール以外で背景色のショートカット（E）を押したときに使います。")),
      [tr("初期状態"), check(tr("カットフォルダー・連番画像の読み込みは常に色塗り前の初期状態として保存する"), prefs.keep_initial_state_on_cut_folder, (v) => set("keep_initial_state_on_cut_folder", v))],
      hint(tr("初期状態として保存すると自動色塗りの精度が上がります。外すと、読み込みのたびに保存するか尋ねます。")),
      [tr("自動保存"), check(tr("自動保存する"), prefs.autosave_enabled, async (v) => {
        await set("autosave_enabled", v);
        enable(interval, v);
      })],
      ["", interval],
      hint(tr("アプリが異常終了したとき、次の起動で自動保存から作業を復元できます。")),
      [tr("更新"), check(tr("起動したら新しいバージョンを確かめる"), prefs.check_updates_automatically, (v) => set("check_updates_automatically", v))],
      hint(tr("新しいバージョンがあると、メニューバーの右端（macOS では画面上部のメニューの最後）でお知らせします。ヘルプ › 更新を確認… はいつでも使えます。")),
      [tr("ツールの一時切り替え"), check(tr("ツールのキーを長押ししたら、離したときに元のツールへ戻す"), prefs.tool_hold_switch_enabled, async (v) => {
        await set("tool_hold_switch_enabled", v);
        enable(holdMs, v);
      })],
      ["", holdMs],
      hint(tr("ツールのキー（P・G など）を押すとすぐ切り替わり、この長さ以上押してから離すと元のツールへ戻ります。0 ms にすると押している間だけ切り替わり、離せば必ず元のツールに戻ります（完全な一時切り替え）。短く押して切り替えたままにしたいときは 300〜500 ms ほどにします。")),
    );
  }

  // ---- 外観 ----
  function appearance() {
    const theme = select([[SYSTEM, tr("システムに合わせる")], ["light", tr("ライト（明るい）")], ["dark", tr("ダーク（暗い）")]], prefs.theme, (v) => set("ui_theme", v));
    const accent = el("select");
    const fillAccent = () => {
      accent.replaceChildren(el("option", { value: SYSTEM }, tr("システムに合わせる")));
      for (const [name, hex] of ACCENT_PRESETS) accent.append(el("option", { value: hex, style: { color: hex } }, `■ ${tr(name)}`));
      const current = prefs.accent;
      if (current !== SYSTEM && !ACCENT_PRESETS.some(([, h]) => h === current)) {
        accent.append(el("option", { value: current, style: { color: current } }, `■ ${tr("カスタム（{color}）", { color: current })}`));
      }
      accent.append(el("option", { value: "custom" }, tr("カスタム…")));
      accent.value = current;
    };
    fillAccent();
    accent.addEventListener("change", async () => {
      let value = accent.value;
      if (value === "custom") {
        value = await pickColor(prefs.accent === SYSTEM ? "#2f6fed" : prefs.accent);
        if (!value) {
          fillAccent();
          return;
        }
      }
      await set("ui_accent", value);
      fillAccent();
    });
    const colorButton = el("button", { type: "button" });
    const showColor = () => {
      colorButton.replaceChildren(el("span", { style: { display: "inline-block", width: "14px", height: "14px", background: prefs.background_checker_color, border: "1px solid var(--border)", marginRight: "6px", verticalAlign: "middle" } }), prefs.background_checker_color.toUpperCase());
    };
    showColor();
    colorButton.addEventListener("click", async () => {
      const v = await pickColor(prefs.background_checker_color);
      if (v) {
        await set("background_checker_color", v);
        showColor();
      }
    });
    return form(
      [tr("テーマ"), theme],
      [tr("アクセントカラー"), accent],
      [tr("背景の表示"), check(tr("指定した背景色を市松模様で表示する"), prefs.background_checker_enabled, (v) => set("background_checker_enabled", v))],
      [tr("市松模様にする背景色"), el("div", {}, colorButton)],
      hint(tr("背景の表示色が指定色と一致するときに適用します。画像や書き出しには影響しません。")),
    );
  }

  // ---- 新規キャンバス ----
  function canvas() {
    const [w, h] = prefs.new_canvas_size;
    const size = { w, h };
    const save = () => set("new_canvas_size", [size.w, size.h]);
    const list = el("div", { class: "sh-list" });
    let selected = null;
    const rename = el("button", { type: "button", disabled: true }, tr("名前を変更…"));
    const remove = el("button", { type: "button", disabled: true }, tr("削除"));
    async function reload(selectId) {
      const papers = await invoke("papers_list");
      list.replaceChildren();
      selected = papers.some((p) => p.id === selectId) ? selectId : null;
      for (const p of papers) {
        const item = el("div", { class: `sh-list-item${p.id === selected ? " active" : ""}` }, p.size ? `${p.name}\n${p.size[0]} × ${p.size[1]} px` : p.name);
        item.addEventListener("click", () => {
          selected = p.id;
          list.querySelectorAll(".sh-list-item").forEach((n) => n.classList.toggle("active", n === item));
          rename.disabled = remove.disabled = false;
        });
        item.dataset.name = p.name;
        item.dataset.id = p.id;
        list.append(item);
      }
      rename.disabled = remove.disabled = !selected;
    }
    const add = el("button", { type: "button" }, tr("追加…"));
    add.addEventListener("click", async () => {
      const failed = await invoke("papers_add", { title: tr("レイアウト用紙の画像を追加"), filter: tr("画像 (*.png *.jpg *.jpeg *.tga)").replace(/\s*\(.*\)$/, "") });
      await reload(selected);
      if (failed.length) await message(tr("レイアウト用紙"), tr("次の画像は読み込めませんでした。\n\n{names}", { names: failed.join("\n") }));
    });
    rename.addEventListener("click", async () => {
      const item = list.querySelector(`[data-id="${selected}"]`);
      const name = await prompt(tr("名前を変更"), tr("用紙の名前"), item?.dataset.name ?? "");
      if (name?.trim()) {
        await invoke("papers_rename", { id: selected, name });
        await reload(selected);
      }
    });
    remove.addEventListener("click", async () => {
      if (!selected) return;
      await invoke("papers_remove", { id: selected });
      await reload(null);
    });
    reload(null);
    const paperBox = el("div", { style: { display: "flex", flexDirection: "column", gap: "6px" } }, list, el("div", { class: "sh-row" }, add, rename, remove));
    return form(
      [tr("幅"), spin(w, [64, 16384], 1, " px", (v) => { size.w = v; save(); })],
      [tr("高さ"), spin(h, [64, 16384], 1, " px", (v) => { size.h = v; save(); })],
      hint(tr("起動したときと、ファイル › 新規作成… で使う大きさです。")),
      [tr("レイアウト用紙"), paperBox],
      hint(tr("ファイル › 新規作成… で選ぶと、一番下の下書きレイヤーとして入り、キャンバスサイズを用紙に合わせられます。画像は環境設定の中にコピーして保管します。")),
    );
  }

  // ---- 筆圧 ----
  function pressure() {
    const normalize = (s) => {
      const points = (s.points ?? []).map(([x, y]) => [Math.max(0, Math.min(1, +x)), Math.max(0, Math.min(1, +y))]).sort((a, b) => a[0] - b[0]);
      return {
        enabled: s.enabled ?? true,
        minimum: Math.max(0.01, Math.min(1, Number(s.minimum ?? 0.1))),
        maximum: Math.max(0.1, Math.min(3, Number(s.maximum ?? 1))),
        points: points.length >= 2 ? points : PRESSURE_DEFAULT.points.map((p) => [...p]),
      };
    };
    const presets = () => {
      const list = (Array.isArray(prefs.pressure_presets) ? prefs.pressure_presets : [])
        .filter((e) => e && String(e.name ?? "").trim())
        .map((e) => ({ name: String(e.name).trim(), ...normalize(e) }));
      return list.length ? list : [{ name: tr("標準"), ...normalize(PRESSURE_DEFAULT) }];
    };
    const activeName = () => {
      const names = presets().map((p) => p.name);
      return names.includes(prefs.pressure_active_preset) ? prefs.pressure_active_preset : names[0];
    };
    const savePresets = (list) => set("pressure_presets", list.map((e) => ({ name: e.name, ...normalize(e) })));
    const choose = el("select", { style: { flex: "1" } });
    const removeButton = el("button", { type: "button" }, tr("削除"));
    const editor = el("div");
    function reload() {
      const list = presets();
      choose.replaceChildren(...list.map((p) => el("option", { value: p.name }, p.name)));
      choose.value = activeName();
      removeButton.disabled = list.length <= 1;
      drawEditor(list.find((p) => p.name === activeName()));
    }
    function update(settings) {
      const list = presets();
      const name = activeName();
      for (const e of list) if (e.name === name) Object.assign(e, normalize(settings));
      savePresets(list);
    }
    function drawEditor(settings) {
      const s = normalize(settings);
      const enabled = check(tr("筆圧をブラシサイズに反映"), s.enabled, (v) => { s.enabled = v; update(s); });
      enabled.title = tr("筆圧はブラシサイズだけに反映されます。描画不透明度・色・アルファ値には一切影響しません。");
      const slider = (value, min, max, onchange) => {
        const out = el("span", { style: { minWidth: "40px" } }, `${value}%`);
        const input = el("input", { type: "range", min: String(min), max: String(max), value: String(value), style: { flex: "1" } });
        input.addEventListener("input", () => (out.textContent = `${input.value}%`));
        input.addEventListener("change", () => onchange(Number(input.value)));
        return el("div", { class: "sh-row", style: { flexWrap: "nowrap" } }, input, out);
      };
      const curve = curveEditor(s.points, (points) => { s.points = points; update(s); });
      editor.replaceChildren(
        enabled,
        form(
          [tr("最小サイズ"), slider(Math.round(s.minimum * 100), 1, 100, (v) => { s.minimum = v / 100; update(s); })],
          [tr("最大倍率"), slider(Math.round(s.maximum * 100), 10, 300, (v) => { s.maximum = v / 100; update(s); })],
        ),
        el("div", { style: { margin: "8px 0 4px" } }, tr("筆圧カーブ")),
        curve,
      );
    }
    choose.addEventListener("change", async () => {
      await set("pressure_active_preset", choose.value);
      reload();
    });
    const add = el("button", { type: "button", title: tr("今のプリセットを元に、新しいプリセットを作ります。") }, tr("追加"));
    add.addEventListener("click", async () => {
      const list = presets();
      const names = new Set(list.map((p) => p.name));
      const base = tr("新しいプリセット");
      let unique = base;
      for (let n = 2; names.has(unique); n++) unique = `${base} ${n}`;
      const current = list.find((p) => p.name === activeName());
      list.push({ ...normalize(current), name: unique });
      await savePresets(list);
      await set("pressure_active_preset", unique);
      reload();
    });
    const rename = el("button", { type: "button" }, tr("名前を変更…"));
    rename.addEventListener("click", async () => {
      const old = activeName();
      const name = await prompt(tr("名前を変更"), tr("プリセットの名前"), old);
      if (name === null || name.trim() === old) return;
      const list = presets();
      const fresh = name.trim();
      if (!fresh || list.some((p) => p.name === fresh)) {
        await message(tr("名前を変更"), tr("その名前は使えません。空欄か、ほかのプリセットと同じ名前です。"));
        return;
      }
      for (const p of list) if (p.name === old) p.name = fresh;
      await savePresets(list);
      if (prefs.pressure_active_preset === old || !prefs.pressure_active_preset) await set("pressure_active_preset", fresh);
      reload();
    });
    removeButton.addEventListener("click", async () => {
      const list = presets().filter((p) => p.name !== activeName());
      if (!list.length) return;
      await savePresets(list);
      reload();
    });
    reload();
    return el("div", { style: { display: "flex", flexDirection: "column", gap: "10px" } },
      el("div", {}, tr("使うプリセット")),
      el("div", { class: "sh-row", style: { flexWrap: "nowrap" } }, choose, add, rename, removeButton),
      el("div", { class: "sh-hint", style: { gridColumn: "auto" } }, tr("ブラシが「全体の設定を使う」のとき、ここで選んだプリセットで描きます。ブラシだけの設定は、ツールプロパティの 筆圧… で切り替えます。")),
      editor,
    );
  }

  // ---- 表示チェック ----
  function viewCheck() {
    const grid = el("div", { class: "sh-palette" });
    const show = () => {
      grid.replaceChildren();
      prefs.map_color_palette.forEach((color, index) => {
        const b = el("button", { type: "button", class: "sh-swatch", title: tr("{number} 番目の色（クリックで変更）", { number: index + 1 }), style: { background: color }, "aria-label": color });
        b.addEventListener("click", async () => {
          const v = await pickColor(color);
          if (!v) return;
          const colors = [...prefs.map_color_palette];
          colors[index] = v;
          await set("map_color_palette", colors);
          show();
        });
        grid.append(b);
      });
    };
    show();
    const reset = el("button", { type: "button" }, tr("既定に戻す"));
    reset.addEventListener("click", async () => {
      await set("map_color_palette", DEFAULT_MAP_PALETTE);
      show();
    });
    return form(
      [tr("隣接色分けの色"), grid],
      ["", el("div", {}, reset)],
      hint(tr("キャンバスの下の「隣接色分け」で、隣り合う部分を塗り分ける仮の色です。大きい部分から順に、隣でまだ使っていない色を 1 番から選ぶので、前の番号の色ほど多く使われます。見分けやすいよう、互いに離れた色にしてください。主線（黒）と背景（白）に近い色は避けると見やすくなります。")),
    );
  }

  // ---- ワークスペース ----
  function workspaces() {
    const list = el("div", { class: "sh-list" });
    let selected = null;
    const buttons = {
      up: el("button", { type: "button" }, tr("上へ")),
      down: el("button", { type: "button" }, tr("下へ")),
      rename: el("button", { type: "button" }, tr("名前を変更…")),
      overwrite: el("button", { type: "button", title: tr("選んだワークスペースを、今のパネルの配置で保存し直します。") }, tr("現在の配置で上書き")),
      remove: el("button", { type: "button" }, tr("削除…")),
    };
    const names = () => Object.keys(prefs.workspaces ?? {});
    const sync = () => {
      const all = names();
      const row = all.indexOf(selected);
      buttons.up.disabled = row <= 0;
      buttons.down.disabled = row < 0 || row >= all.length - 1;
      buttons.rename.disabled = buttons.overwrite.disabled = buttons.remove.disabled = row < 0;
    };
    let dragged = null;
    const reload = () => {
      list.replaceChildren();
      for (const name of names()) {
        const item = el("div", { class: `sh-list-item${name === selected ? " active" : ""}`, draggable: "true", style: { minHeight: "18px" } },
          name === prefs.active_workspace ? tr("{name}（使用中）", { name }) : name);
        item.addEventListener("click", () => {
          selected = name;
          reload();
        });
        item.addEventListener("dblclick", () => {
          selected = name;
          buttons.rename.click();
        });
        item.addEventListener("dragstart", () => {
          dragged = name;
          item.classList.add("dragging");
        });
        item.addEventListener("dragover", (e) => e.preventDefault());
        item.addEventListener("drop", async (e) => {
          e.preventDefault();
          if (!dragged || dragged === name) return;
          const order = names().filter((n) => n !== dragged);
          order.splice(order.indexOf(name), 0, dragged);
          await reorder(order);
        });
        item.addEventListener("dragend", () => (dragged = null));
        list.append(item);
      }
      sync();
    };
    const reorder = async (order) => {
      const records = prefs.workspaces ?? {};
      const next = {};
      for (const n of order) next[n] = records[n];
      await set("workspaces", next);
      reload();
    };
    const move = (step) => {
      const order = names();
      const row = order.indexOf(selected);
      const target = row + step;
      if (row < 0 || target < 0 || target >= order.length) return;
      [order[row], order[target]] = [order[target], order[row]];
      reorder(order);
    };
    buttons.up.addEventListener("click", () => move(-1));
    buttons.down.addEventListener("click", () => move(1));
    buttons.rename.addEventListener("click", async () => {
      const old = selected;
      if (!old) return;
      const name = await prompt(tr("名前を変更"), tr("ワークスペース名："), old);
      if (name === null || name.trim() === old) return;
      const fresh = name.trim();
      if (!fresh || names().includes(fresh)) {
        await message(tr("名前を変更"), tr("その名前は使えません。空欄か、ほかのワークスペースと同じ名前です。"));
        return;
      }
      const next = {};
      for (const [n, r] of Object.entries(prefs.workspaces)) next[n === old ? fresh : n] = r;
      await set("workspaces", next);
      if (prefs.active_workspace === old) await set("active_workspace", fresh);
      selected = fresh;
      reload();
    });
    buttons.overwrite.addEventListener("click", async () => {
      if (selected) await shell.saveWorkspace(selected);
      prefs = await invoke("prefs_get");
      reload();
    });
    buttons.remove.addEventListener("click", async () => {
      const name = selected;
      if (!name) return;
      if (!(await ask(tr("ワークスペースを削除"), tr("ワークスペース「{name}」を削除しますか？", { name })))) return;
      const next = { ...prefs.workspaces };
      delete next[name];
      await set("workspaces", next);
      if (prefs.active_workspace === name) await set("active_workspace", null);
      selected = null;
      reload();
    });
    reload();
    return el("div", { style: { display: "flex", flexDirection: "column", gap: "10px", height: "100%" } },
      el("div", { style: { display: "flex", gap: "8px", flex: "1", minHeight: "200px" } }, list, el("div", { class: "sh-side-buttons" }, ...Object.values(buttons))),
      el("div", { class: "sh-hint", style: { gridColumn: "auto" } }, tr("ワークスペース メニューに、この順で並びます。ドラッグでも並べ替えられます。新しいワークスペースは ワークスペース › 現在の配置を保存… で作ります。")),
    );
  }

  // ---- パフォーマンス ----
  function performance() {
    return form(
      [tr("縮小表示"), select([["nearest", tr("速さ優先（間引き）")], ["keep_lines", tr("線を残す")]], prefs.display_shrink_mode, (v) => set("display_shrink_mode", v))],
      hint(tr("キャンバス全体が見えるところまで縮めると、1画素の線は拾われたり落ちたりして荒れます。「線を残す」は細い線を消さずに縮めますが、大きなキャンバスでは表示の更新が少し重くなります。元の絵は変わりません。")),
      [tr("取り消せる回数"), spin(prefs.undo_max_steps, [5, 500], 1, tr(" 回"), (v) => set("undo_max_steps", v))],
      [tr("取り消し履歴のメモリ"), spin(prefs.undo_max_memory_mb, [128, 16384], 128, " MB", (v) => set("undo_max_memory_mb", v))],
      hint(tr("どちらかの上限に達すると、古い履歴から消えます。大きなキャンバスでは、回数より先にメモリの上限に達します。減らしたときは、次に操作したときに古い履歴が消えます。")),
    );
  }

  const sections = [
    ["general", tr("一般"), general],
    ["appearance", tr("外観"), appearance],
    ["canvas", tr("新規キャンバス"), canvas],
    ["pressure", tr("筆圧"), pressure],
    ["view_check", tr("表示チェック"), viewCheck],
    ["workspaces", tr("ワークスペース"), workspaces],
    ["performance", tr("パフォーマンス"), performance],
  ];
  const side = el("div", { class: "sh-prefs-sections" });
  const page = el("div", { class: "sh-prefs-page" });
  const show = (key) => {
    const entry = sections.find(([k]) => k === key) ?? sections[0];
    side.querySelectorAll(".sh-prefs-section").forEach((n) => n.classList.toggle("active", n.dataset.key === entry[0]));
    page.replaceChildren(entry[2]());
    page.scrollTop = 0;
  };
  for (const [key, title] of sections) {
    const item = el("div", { class: "sh-prefs-section" }, title);
    item.dataset.key = key;
    item.addEventListener("click", () => show(key));
    side.append(item);
  }
  show(section);
  ctx.reloadWorkspaces = () => {
    if (side.querySelector(".active")?.dataset.key === "workspaces") invoke("prefs_get").then((p) => { prefs = p; show("workspaces"); });
  };
  await openDialog({
    title: tr("環境設定"),
    body: el("div", { class: "sh-prefs" }, side, page),
    buttons: [{ label: tr("閉じる"), value: null, escape: true, primary: true }],
    className: "sh-prefs-dialog",
  });
  ctx.reloadWorkspaces = null;
}

/// 筆圧カーブ（Qt の PressureCurveWidget）：クリックで点を足す・ドラッグで動かす・右クリックで
/// 中の点を消す。線は点を通るなめらかな曲線（見た目だけ。描くときの計算は筆圧のパネルが持つ）。
function curveEditor(points, onchange) {
  const W = 260;
  const H = 170;
  const P = 14;
  const NS = "http://www.w3.org/2000/svg";
  const node = document.createElementNS(NS, "svg");
  node.setAttribute("width", W);
  node.setAttribute("height", H);
  node.style.background = "var(--window)";
  node.style.border = "1px solid var(--border)";
  node.style.borderRadius = "4px";
  node.style.cursor = "crosshair";
  const title = document.createElementNS(NS, "title");
  title.textContent = tr("左クリック：アンカーポイント追加／ドラッグ：移動／右クリック：中間アンカーポイント削除／曲線：3次ベジエ補間");
  let pts = points.map((p) => [...p]);
  const sx = (x) => P + x * (W - 2 * P);
  const sy = (y) => H - P - y * (H - 2 * P);
  const fromEvent = (e) => {
    const r = node.getBoundingClientRect();
    return [Math.max(0, Math.min(1, (e.clientX - r.left - P) / (W - 2 * P))), Math.max(0, Math.min(1, (H - P - (e.clientY - r.top)) / (H - 2 * P)))];
  };
  const draw = () => {
    node.replaceChildren(title);
    const mk = (tag, attrs) => {
      const n = document.createElementNS(NS, tag);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      node.append(n);
      return n;
    };
    mk("rect", { x: P, y: P, width: W - 2 * P, height: H - 2 * P, fill: "none", stroke: "var(--border)" });
    mk("line", { x1: sx(0), y1: sy(0), x2: sx(1), y2: sy(1), stroke: "var(--border)", "stroke-dasharray": "3 3" });
    let d = `M${sx(pts[0][0])},${sy(pts[0][1])}`;
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[Math.max(0, i - 1)];
      const p1 = pts[i];
      const p2 = pts[i + 1];
      const p3 = pts[Math.min(pts.length - 1, i + 2)];
      const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
      const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
      d += ` C${sx(c1[0])},${sy(c1[1])} ${sx(c2[0])},${sy(c2[1])} ${sx(p2[0])},${sy(p2[1])}`;
    }
    mk("path", { d, fill: "none", stroke: "var(--accent)", "stroke-width": 2 });
    pts.forEach(([x, y], i) => {
      const c = mk("circle", { cx: sx(x), cy: sy(y), r: 5, fill: "var(--surface)", stroke: "var(--accent)", "stroke-width": 2 });
      c.dataset.index = i;
    });
    const t1 = mk("text", { x: 4, y: P + 10, fill: "var(--text-muted)", "font-size": 10 });
    t1.textContent = tr("出力");
    const t2 = mk("text", { x: W - 28, y: H - 3, fill: "var(--text-muted)", "font-size": 10 });
    t2.textContent = tr("入力");
  };
  let drag = null;
  node.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const index = e.target.dataset?.index;
    if (index !== undefined) drag = Number(index);
    else {
      const p = fromEvent(e);
      pts.push(p);
      pts.sort((a, b) => a[0] - b[0]);
      drag = pts.indexOf(p);
      draw();
    }
    node.setPointerCapture(e.pointerId);
  });
  node.addEventListener("pointermove", (e) => {
    if (drag === null) return;
    const [x, y] = fromEvent(e);
    const lo = drag === 0 ? 0 : pts[drag - 1][0];
    const hi = drag === pts.length - 1 ? 1 : pts[drag + 1][0];
    // 両端の点は横に動かさない。
    pts[drag] = [drag === 0 ? 0 : drag === pts.length - 1 ? 1 : Math.max(lo, Math.min(hi, x)), y];
    draw();
  });
  node.addEventListener("pointerup", () => {
    if (drag === null) return;
    drag = null;
    onchange(pts.map((p) => [...p]));
  });
  node.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    const index = Number(e.target.dataset?.index);
    if (Number.isInteger(index) && index > 0 && index < pts.length - 1) {
      pts.splice(index, 1);
      draw();
      onchange(pts.map((p) => [...p]));
    }
  });
  draw();
  return node;
}
