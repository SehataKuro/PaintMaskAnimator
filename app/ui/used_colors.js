// 使用色のパネル（Qt の color_panel.py と color_panel_widgets.py）。
//
// 行の並び・親子・フォルダー・表示・鍵・選択・置換色は Rust（src/colors.rs と
// pma-core の used_colors）が持ち、`palette-changed` で全体を送ってくる。ここは描くことと、
// 操作を palette_op へ渡すことだけをする。
//
// 行の形：［見本・コード（置き換えでは → 置換色）］［表示］［鍵］［選択］
// - 行の高さは固定（#105。置き換えに切り替えても行の位置が動かない）
// - 一番下の枠は1つ：通常では［統合］、置き換えでは［全コマを置き換え］（#105）
// - 通常と置き換えを切り替えても、スクロールの位置は保つ
// - 置換色が未設定の色は、見本に赤い斜線を引く
import { hex, parseHex } from "./color_math.js";
import {
  adjustHsv,
  alertBox,
  drawingOp,
  el,
  eyedrop,
  paletteOp,
  pickColor,
  promptText,
  replacementLoad,
  replacementSave,
  showMenu,
  startColors,
  state,
  subscribe,
} from "./color_state.js";
import { openColorChart } from "./color_chart.js";
import { adjustLineThickness } from "./line_thickness.js"; // 【Group C】
import { pendingTween } from "./tween.js"; // 【Group C】

const ROW_HEIGHT = 24;
const IS_MAC = /Mac/.test(navigator.platform);
const CTRL = IS_MAC ? "⌘" : "Ctrl";
const ALT = IS_MAC ? "⌥" : "Alt";

const HELP =
  "使用色：クリックで選択（Shift＝範囲／Ctrl＝追加）。" +
  "行の右の目で表示／非表示、鍵で保護（その色の上に描かない）を切り替えます。" +
  "フォルダーを作り、色をドラッグして格納。" +
  "ドラッグで並べ替え、色の中央へドロップ＝その色の「子」として整理。" +
  "色を2色以上選んで［統合］を押すと、最後に選んだ色（枠で囲んだ行）へ実画像を塗り替えます。" +
  "左上を「置き換え」にすると、色ごとに置き換え後の色を決めて、" +
  "［全コマを置き換え］でまとめて置き換えます。";

const rgbOf = (row) => row.rgb;
const keyOf = (rgb) => rgb.join(",");

function uniqueName(base, existing) {
  const names = new Set(existing.map((n) => n.toLowerCase()));
  let name = base;
  let n = 2;
  while (names.has(name.toLowerCase())) name = `${base} ${n++}`;
  return name;
}

export function createUsedColorsPanel() {
  startColors();
  const root = el("div", { class: "cu-panel" });

  // ---- 1行目 ----
  const normalBtn = el("button", { class: "cu-mode-btn", title: "使用色の表示・保護・選択を扱います。" }, "通常");
  const replaceBtn = el("button", { class: "cu-mode-btn replace", title: "色ごとに置き換え後の色を決め、全コマをまとめて置き換えます。" }, "置き換え");
  const modeSwitch = el("div", { class: "cu-mode" }, normalBtn, replaceBtn);
  const count = el("span", { class: "cu-count" }, "0色");
  const previewBtn = el("button", {
    class: "cu-icon-btn cu-preview",
    title: "キャンバスでプレビュー：ON のあいだ、置き換えた見た目を表示します（画像は変えません）。",
  }, "👁");
  const chartBtn = el("button", { class: "cu-icon-btn", title: "カラーチャート（親子色とタグ）を開きます。" }, "▦");
  const folderBtn = el("button", { class: "cu-icon-btn", title: "使用色をまとめるフォルダーを作成します。" }, "＋▭");
  const menuBtn = el("button", { class: "cu-icon-btn", title: "まとめて行う操作" }, "☰");
  root.append(el("div", { class: "cu-top" }, modeSwitch, count, el("span", { class: "cu-stretch" }), previewBtn, chartBtn, folderBtn, menuBtn));

  // ---- 置き換えの道具 ----
  const tool = (label, title, action) => el("button", { class: "cu-tool", title, onclick: action }, label);
  const replaceTools = el("div", { class: "cu-replace-tools" },
    tool("描画色", "選んだ色の置き換え後の色を、今の描画色にします。", () => paletteOp({ op: "replace_with_drawing_color" })),
    tool("スポイト", "キャンバスの色をクリックして、選んだ色の置き換え後の色にします。Escで中止。", async () => {
      const c = await eyedrop.begin("置き換え後の色：キャンバスの色をクリックしてください（Esc でやめる）");
      if (c) await paletteOp({ op: "set_replacement", targets: [], color: c });
    }),
    tool("色を選ぶ", "カラーピッカーで、選んだ色の置き換え後の色を決めます。", () => editReplacementForSelection()),
    tool("解除", "選んだ色の置き換えをやめます。", () => paletteOp({ op: "clear_replacements", targets: [] })),
  );
  root.append(replaceTools);

  // ---- 行 ----
  const scroll = el("div", { class: "cu-scroll" });
  const list = el("div", { class: "cu-list" });
  scroll.append(list);
  root.append(scroll);
  const empty = el("div", { class: "cu-empty" }, "作品を開くと、使われている色が並びます。");
  scroll.append(empty);

  // ---- 一番下の枠（#105） ----
  const mergeBtn = el("button", {
    class: "cu-bottom-btn merge",
    title: "選択した使用色（2色以上）を、最後に選んだ色（枠で囲んだ行）へ統合します。実際の画像を塗り替えます。Undoで元に戻せます。",
  });
  const applyBtn = el("button", {
    class: "cu-bottom-btn apply",
    title: "置き換え後の色を決めた色を、選択レイヤーのすべてのコマで置き換えます。一部だけ変えたいときはバケツの色置換を使います。",
  }, "全コマを置き換え");
  const bottom = el("div", { class: "cu-bottom" }, mergeBtn, applyBtn);
  const selectionStatus = el("div", { class: "cu-selection" }, "選択なし");
  root.append(bottom, selectionStatus);

  let view = null;

  normalBtn.addEventListener("click", () => setMode(false));
  replaceBtn.addEventListener("click", () => setMode(true));
  function setMode(on) {
    if (view?.replace_mode === on) return;
    // 置き換えへ切り替えてもスクロールの位置は保つ（行の高さは同じ）。
    keepScroll = scroll.scrollTop;
    paletteOp({ op: "replace_mode", on });
  }
  let keepScroll = null;
  previewBtn.addEventListener("click", () => paletteOp({ op: "replacement_preview", on: !view?.replacement_preview }));
  chartBtn.addEventListener("click", () => openColorChart());
  folderBtn.addEventListener("click", () => createFolder());
  menuBtn.addEventListener("click", () => {
    const r = menuBtn.getBoundingClientRect();
    showMenu(panelMenu(), r.left, r.bottom + 2, root.ownerDocument);
  });
  mergeBtn.addEventListener("click", () => paletteOp({ op: "merge" }));
  applyBtn.addEventListener("click", () => paletteOp({ op: "apply_replacements" }));

  function panelMenu() {
    const has = (view?.replacement_count ?? 0) > 0;
    return [
      { label: "色の選択をすべて解除", disabled: !view?.selected_count, action: () => paletteOp({ op: "clear_selection" }) },
      { label: "すべての色を表示", action: () => paletteOp({ op: "show_all" }) },
      { label: "すべての色の保護を外す", action: () => paletteOp({ op: "unlock_all" }) },
      "-",
      { label: "置換色を保存…", disabled: !has, action: () => replacementSave() },
      { label: "置換色を読み込む…", action: () => replacementLoad() },
      { label: "置換色をすべて外す", disabled: !has, action: () => paletteOp({ op: "clear_all_replacements" }) },
      "-",
      { label: "カラーチャート…", action: () => openColorChart() },
      "-",
      { label: "使用色パネルの使い方", action: () => alertBox("使用色パネルの使い方", HELP) },
    ];
  }

  const folderNames = () => (view?.rows ?? []).filter((r) => r.kind === "folder").map((r) => r.name);

  async function createFolder() {
    const name = await promptText("使用色フォルダー", "フォルダー名：", uniqueName("新規フォルダー", folderNames()));
    if (name && name.trim()) await paletteOp({ op: "create_category", name: name.trim() });
  }

  // ---- 置換色の編集（Qt の ReplacementColorPopup） ----
  let editing = false;
  async function editReplacement(rgb, initial) {
    if (editing) return;
    editing = true;
    try {
      await paletteOp({ op: "replacement_edit_begin" });
      const code = hex(rgb).slice(1);
      let cleared = false;
      const picked = await pickColor(initial, {
        title: `置換色　元色 #${code}`,
        preview: (c) => paletteOp({ op: "replacement_edit_preview", rgb, color: c }),
        extra: { label: "外す", title: "この色の置換色を未設定に戻します。", action: () => (cleared = true) },
      });
      if (cleared) await paletteOp({ op: "replacement_edit_end", commit: false, clear: rgb });
      else if (picked) {
        await paletteOp({ op: "replacement_edit_preview", rgb, color: picked });
        await paletteOp({ op: "replacement_edit_end", commit: true });
      } else await paletteOp({ op: "replacement_edit_end", commit: false });
    } finally {
      editing = false;
    }
  }

  async function editReplacementForSelection() {
    const p = view;
    if (!p) return;
    const targets = p.rows.filter((r) => r.kind === "color" && !r.background && r.selected);
    const focus = p.rows.find((r) => r.kind === "color" && r.focus && !r.background);
    const list = targets.length ? targets : focus ? [focus] : [];
    if (!list.length) {
      await paletteOp({ op: "set_replacement", targets: [], color: [0, 0, 0] });
      return;
    }
    if (list.length === 1) {
      const r = list[0];
      editReplacement(r.rgb, parseHex(r.replacement ?? r.hex));
      return;
    }
    const first = list[0];
    const c = await pickColor(parseHex(first.replacement ?? first.hex), { title: `置換色（${list.length}色）` });
    if (c) await paletteOp({ op: "set_replacement", targets: list.map(rgbOf), color: c });
  }

  // ---- 行を作る ----
  function folderRow(row) {
    const chevron = el("span", { class: "cu-chevron" }, row.collapsed ? "▸" : "▾");
    const vis = el("span", {
      class: `cu-eye${row.visible === false ? " off" : row.visible == null ? " mixed" : ""}`,
      title: "フォルダーの中の色の表示を切り替えます。",
    });
    const name = el("span", { class: "cu-folder-name" }, `${row.name}　${row.count}`);
    const node = el("div", { class: "cu-row cu-folder", "data-folder": row.name, title: "フォルダーへ色をドラッグして格納できます。ダブルクリックで名前を変更します。" },
      chevron, el("span", { class: "cu-folder-icon" }, "▭"), name, el("span", { class: "cu-stretch" }), vis);
    chevron.addEventListener("click", (e) => {
      e.stopPropagation();
      paletteOp({ op: "collapse", name: row.name, collapsed: !row.collapsed });
    });
    vis.addEventListener("click", (e) => {
      e.stopPropagation();
      paletteOp({ op: "category_visible", name: row.name, visible: row.visible === false });
    });
    const rename = async () => {
      const next = await promptText("使用色フォルダー", "フォルダー名：", row.name);
      if (next && next.trim() && next.trim() !== row.name) await paletteOp({ op: "rename_category", old: row.name, new: next.trim() });
    };
    name.addEventListener("dblclick", rename);
    node.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      showMenu([
        { label: "名前を変更…", action: rename },
        { label: "フォルダーを削除", action: () => paletteOp({ op: "delete_category", name: row.name }) },
      ], e.clientX, e.clientY, root.ownerDocument);
    });
    return node;
  }

  function checkCell(kind, row, on, title) {
    const cell = el("span", { class: `cu-check cu-${kind}${on ? " on" : ""}`, "data-column": kind, title });
    cell.append(el("span", { class: "cu-check-mark" }));
    return cell;
  }

  function colorRow(row, replaceMode) {
    const code = row.background ? `背景 ${row.code}` : row.children ? `${row.code}（親・${row.children}）` : row.code;
    const swatchEl = el("span", { class: "cu-swatch" });
    swatchEl.style.background = row.hex;
    const source = el("span", { class: "cu-source" }, swatchEl, el("span", { class: "cu-code" }, code));
    if (replaceMode && !row.background) {
      const rep = el("span", {
        class: `cu-swatch cu-replacement${row.replacement ? "" : " unset"}`,
        title: row.replacement
          ? `置き換え後の色 ${row.replacement}。クリックで色を変えます。右クリックでスポイト。`
          : "置き換え後の色：未設定。クリックで色を選びます。",
      });
      if (row.replacement) rep.style.background = row.replacement;
      rep.addEventListener("pointerdown", (e) => e.stopPropagation());
      rep.addEventListener("click", (e) => {
        e.stopPropagation();
        editReplacement(row.rgb, parseHex(row.replacement ?? row.hex));
      });
      rep.addEventListener("contextmenu", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const c = await eyedrop.begin(`置換色　元色 #${row.code}：キャンバスの色をクリックしてください（Esc でやめる）`);
        if (c) await paletteOp({ op: "set_replacement", targets: [row.rgb], color: c });
      });
      source.append(el("span", { class: "cu-arrow" }, "→"), rep, el("span", { class: "cu-code rep" }, row.replacement ? row.replacement.slice(1) : "未設定"));
    }
    const vis = checkCell("visibility", row, row.enabled, row.background
      ? `表示：クリックでON/OFF／上下になぞる：一括ON/OFF／${ALT}＋クリック：背景だけ表示／右クリック：表示メニュー`
      : `表示：クリックでON/OFF／上下になぞる：一括ON/OFF／${ALT}＋クリック：この色だけ表示／右クリック：表示メニュー`);
    const lock = checkCell("lock", row, row.locked, row.background
      ? "保護：鍵をかけると背景の上には描けません。クリック：ON/OFF／上下になぞる：一括ON/OFF／右クリック：保護メニュー"
      : "保護：鍵をかけた色の上には描けません。クリック：ON/OFF／上下になぞる：一括ON/OFF／右クリック：保護メニュー");
    const sel = checkCell("selection", row, row.selected, row.background
      ? "背景色は選択できません。"
      : `クリック：この色だけ選択／Shift＋クリック：範囲選択／${CTRL}＋クリック：追加・解除／上下になぞる：一括選択／${ALT}＋クリック：この色だけ選択`);
    if (row.background) sel.classList.add("disabled");
    const node = el("div", {
      class: `cu-row cu-color${row.selected ? " selected" : ""}${row.focus ? " focus" : ""}${row.enabled ? "" : " hidden"}${row.in_category ? " in-folder" : ""}${row.parent ? " child" : ""}`,
      "data-rgb": keyOf(row.rgb),
      title: row.background
        ? `背景色 ${state.drawing?.background ?? ""}。並べ替えや親子付けの対象にはできません。`
        : `クリック：この色だけ選択／Shift＋クリック：範囲選択／${CTRL}＋クリック：追加・解除。ドラッグで並べ替え、色の中央へドロップで子にします。${row.parent ? `　現在 ${row.parent} の子です。` : ""}`,
    });
    if (row.line) {
      const line = el("span", { class: "cu-line" });
      line.style.background = row.line;
      node.append(line);
    }
    node.append(source, vis, lock, sel);
    return node;
  }

  const rowsByKey = new Map();
  function render(p) {
    view = p;
    const scrollTop = keepScroll ?? scroll.scrollTop;
    keepScroll = null;
    normalBtn.classList.toggle("checked", !p.replace_mode);
    replaceBtn.classList.toggle("checked", p.replace_mode);
    count.textContent = p.count_text;
    previewBtn.style.display = p.replace_mode ? "" : "none";
    previewBtn.classList.toggle("checked", p.replacement_preview);
    replaceTools.style.display = p.replace_mode ? "" : "none";
    root.classList.toggle("replace", p.replace_mode);
    // 一番下の枠：同じ場所に、通常は統合、置き換えでは全コマを置き換え（#105）。
    mergeBtn.style.display = p.replace_mode ? "none" : "";
    applyBtn.style.display = p.replace_mode ? "" : "none";
    mergeBtn.disabled = !p.can_merge;
    mergeBtn.replaceChildren("統合");
    if (p.focus && p.selected_count) {
      const s = el("span", { class: "cu-swatch small" });
      s.style.background = p.focus;
      mergeBtn.append(el("span", { class: "cu-merge-arrow" }, "  →"), s, el("span", { class: "cu-code" }, p.focus.slice(1)));
    }
    applyBtn.disabled = !p.replacement_count;
    applyBtn.textContent = p.replacement_count ? `全コマを置き換え（${p.replacement_count}色）` : "全コマを置き換え";
    selectionStatus.textContent = p.selected_count ? `${p.selected_count}色を選択中` : "選択なし";
    if (p.restricted) selectionStatus.textContent += "　描ける範囲を絞っています";

    rowsByKey.clear();
    const nodes = [];
    for (const row of p.rows) {
      if (row.kind === "folder") nodes.push(folderRow(row));
      else {
        const n = colorRow(row, p.replace_mode);
        rowsByKey.set(keyOf(row.rgb), row);
        nodes.push(n);
      }
    }
    list.replaceChildren(...nodes);
    empty.style.display = p.loaded ? "none" : "";
    scroll.scrollTop = scrollTop;
  }

  // ---- マウス：選択・なぞり・ドラッグ ----
  const rowAt = (x, y) => {
    const doc = root.ownerDocument;
    const hit = doc.elementFromPoint(x, y)?.closest?.(".cu-row");
    return hit && list.contains(hit) ? hit : null;
  };
  const rowData = (node) => (node?.dataset.rgb ? rowsByKey.get(node.dataset.rgb) : null);

  // なぞり・ドラッグのあいだ、行の外でも事件を受ける（作った事件では掴めないので、そのときは掴まない）。
  const capture = (e) => {
    try {
      list.setPointerCapture(e.pointerId);
    } catch {
      // 自己確認の作った事件。
    }
  };
  let sweep = null;
  let drag = null;
  list.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const node = e.target.closest(".cu-row");
    const row = rowData(node);
    if (!row) return;
    const cell = e.target.closest(".cu-check");
    if (cell) {
      const column = cell.dataset.column;
      if (column === "selection" && row.background) return;
      e.preventDefault();
      if (e.altKey) {
        if (column === "visibility") paletteOp({ op: "isolate", rgb: row.rgb });
        else if (column === "selection") paletteOp({ op: "select_only", rgb: row.rgb });
        return;
      }
      const checked = !(column === "visibility" ? row.enabled : column === "lock" ? row.locked : row.selected);
      capture(e);
      sweep = { last: keyOf(row.rgb) };
      paletteOp({ op: "sweep_begin", column, rgb: row.rgb, checked });
      return;
    }
    e.preventDefault();
    capture(e);
    drag = { row, x: e.clientX, y: e.clientY, moved: false, shift: e.shiftKey, ctrl: e.metaKey || e.ctrlKey, ghost: null };
  });
  list.addEventListener("pointermove", (e) => {
    if (sweep) {
      const row = rowData(rowAt(e.clientX, e.clientY));
      if (row && keyOf(row.rgb) !== sweep.last) {
        sweep.last = keyOf(row.rgb);
        paletteOp({ op: "sweep_move", rgb: row.rgb });
      }
      return;
    }
    if (!drag) return;
    if (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 5) return;
    if (drag.row.background) return;
    drag.moved = true;
    if (!drag.ghost) {
      drag.ghost = el("div", { class: "cu-ghost" }, el("span", { class: "cu-swatch" }), drag.row.code);
      drag.ghost.firstChild.style.background = drag.row.hex;
      root.ownerDocument.body.append(drag.ghost);
    }
    drag.ghost.style.left = `${e.clientX + 10}px`;
    drag.ghost.style.top = `${e.clientY + 6}px`;
    list.querySelectorAll(".drop-before,.drop-child,.drop-after,.drop-folder").forEach((n) => n.classList.remove("drop-before", "drop-child", "drop-after", "drop-folder"));
    const target = rowAt(e.clientX, e.clientY);
    if (!target) return;
    if (target.dataset.folder) {
      target.classList.add("drop-folder");
      return;
    }
    const r = target.getBoundingClientRect();
    const ratio = (e.clientY - r.top) / r.height;
    target.classList.add(ratio < 0.25 ? "drop-before" : ratio > 0.75 ? "drop-after" : "drop-child");
  });
  const finish = (e) => {
    if (sweep) {
      sweep = null;
      paletteOp({ op: "sweep_end" });
      return;
    }
    if (!drag) return;
    const d = drag;
    drag = null;
    d.ghost?.remove();
    list.querySelectorAll(".drop-before,.drop-child,.drop-after,.drop-folder").forEach((n) => n.classList.remove("drop-before", "drop-child", "drop-after", "drop-folder"));
    if (!d.moved) {
      paletteOp({ op: "click", rgb: d.row.rgb, shift: d.shift, ctrl: d.ctrl });
      return;
    }
    if (e.type === "pointercancel") return;
    const target = rowAt(e.clientX, e.clientY);
    if (!target) return;
    if (target.dataset.folder) {
      paletteOp({ op: "drop_to_category", dragged: d.row.rgb, name: target.dataset.folder });
      return;
    }
    const t = rowData(target);
    if (!t || keyOf(t.rgb) === keyOf(d.row.rgb)) return;
    const r = target.getBoundingClientRect();
    paletteOp({ op: "drop", dragged: d.row.rgb, target: t.rgb, ratio: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) });
  };
  list.addEventListener("pointerup", finish);
  list.addEventListener("pointercancel", finish);

  // ---- 右クリック ----
  list.addEventListener("contextmenu", (e) => {
    const node = e.target.closest(".cu-row");
    const row = rowData(node);
    if (!row) return;
    e.preventDefault();
    const cell = e.target.closest(".cu-check");
    const at = [e.clientX, e.clientY, root.ownerDocument];
    if (cell?.dataset.column === "visibility") {
      showMenu([
        { label: "すべての色を表示", action: () => paletteOp({ op: "show_all" }) },
        { label: row.background ? "背景だけ表示" : "この色だけ表示", action: () => paletteOp({ op: "show_only", rgb: row.rgb }) },
      ], ...at);
      return;
    }
    if (cell?.dataset.column === "lock") {
      // 【Group C】Qt の _show_mask_context_menu。Qt のマスク ON は「描ける」＝鍵を外す（#104 で鍵は守る色の一覧）。
      showMenu([
        { label: "ON", title: "この色に描けるようにします（鍵を外す）。", action: () => paletteOp({ op: "set_locked", rgb: row.rgb, locked: false }) },
        { label: "OFF", title: "この色を守ります（鍵をかける）。", action: () => paletteOp({ op: "set_locked", rgb: row.rgb, locked: true }) },
        "-",
        { label: "対象以外をOFF", title: "この色だけ描けるようにし、ほかの色すべてに鍵をかけます。", action: () => paletteOp({ op: "lock_others", rgb: row.rgb }) },
        { label: "全表示", title: "すべての色の保護を外す", action: () => paletteOp({ op: "unlock_all" }) },
      ], ...at);
      return;
    }
    showMenu(colorMenu(row), ...at);
  });

  function colorMenu(row) {
    const mainColor = { label: "メインカラーにする", action: () => drawingOp({ op: "set", which: "main", color: row.rgb }).then(() => drawingOp({ op: "mode", mode: "main" })) };
    if (row.background) return [mainColor];
    const rows = view.rows.filter((r) => r.kind === "color");
    const selected = rows.filter((r) => r.selected && !r.background);
    const inSelection = row.selected;
    const sources = inSelection ? selected : [row];
    const items = [mainColor, "-"];
    items.push({
      label: sources.length <= 1 ? "この色を消す" : `選択した${sources.length}色を消す`,
      title: "選択した使用色を #FFFFFF へ統合します。",
      action: () => paletteOp({ op: "delete", rgb: row.rgb }),
    });
    if (inSelection && selected.length >= 2) {
      items.push({
        label: `選択した${selected.length}色をこの色へ統合`,
        action: async () => {
          await paletteOp({ op: "focus", rgb: row.rgb });
          await paletteOp({ op: "merge" });
        },
      });
    }
    // 【Group C】線の太さを調整（Qt の can_adjust_thickness：トゥイーン中でなく、選択中の親色を右クリック）。
    if (!pendingTween() && row.focus && inSelection) {
      items.push({
        label: "線の太さを調整",
        title: "選択中の親色・子色をまとめて調整します。",
        action: () => adjustLineThickness(selected.map(rgbOf)),
      });
    }
    items.push({
      label: sources.length <= 1 ? "色相・彩度・明度を調整" : `選択した${sources.length}色の色相・彩度・明度を調整`,
      title: "プレビューを見ながら調整し、OKで全コマへ適用します。",
      action: () => adjustHsv(sources.map(rgbOf)),
    });
    items.push({ label: "この色を選択範囲にする", action: () => paletteOp({ op: "select_pixels", rgb: row.rgb }) });
    const moveSources = inSelection ? selected : [row];
    items.push({
      label: "フォルダーへ移動",
      items: [
        { label: "未分類", action: () => Promise.all(moveSources.map((r) => paletteOp({ op: "to_uncategorized", rgb: r.rgb }))) },
        "-",
        ...folderNames().map((name) => ({ label: name, action: () => paletteOp({ op: "drop_to_category", dragged: row.rgb, name }) })),
        "-",
        {
          label: "新規フォルダー…",
          action: async () => {
            const name = await promptText("使用色フォルダー", "フォルダー名：", uniqueName("新規フォルダー", folderNames()));
            if (name && name.trim()) await paletteOp({ op: "to_new_category", rgb: row.rgb, name: name.trim() });
          },
        },
      ],
    });
    items.push("-");
    const inGroup = row.parent || row.children;
    if (inGroup) items.push({ label: "親子を解除", title: "この色に関わる親子関係を解除します。", action: () => paletteOp({ op: "ungroup", rgb: row.rgb }) });
    if (rows.some((r) => r.parent)) items.push({ label: "親子をすべて解除", action: () => paletteOp({ op: "clear_groups" }) });
    if (row.replacement) items.push("-", { label: "置換色を外す", action: () => paletteOp({ op: "clear_replacements", targets: [row.rgb] }) });
    if (view.replacement_count) {
      if (!row.replacement) items.push("-");
      items.push({ label: "置換色をすべて外す", action: () => paletteOp({ op: "clear_all_replacements" }) });
    }
    if (view.selected_count) items.push("-", { label: "色の選択をすべて解除", action: () => paletteOp({ op: "clear_selection" }) });
    return items;
  }

  subscribe("palette", render);
  root.style.setProperty("--cu-row", `${ROW_HEIGHT}px`);
  return root;
}
