// 【アプリの外枠】メニュー・ファイルの開閉・最近使ったファイル・環境設定・ショートカット・
// ワークスペース・パネルの表示・メモ・更新のお知らせ・自動保存の復元・題名（Qt 版の MainWindow
// のうちパネルの外の部分）。Rust の側は shell.rs。
//
// メニューの項目は Rust（menu.rs）が `menu-action` {id, pending} で送る。キーで選んだ操作は
// shortcuts.js が同じ run へ渡す。まだ移していない操作（pending）は `pma-action` の事件を
// 窓へ出し、どのパネルも受けなければ「まだ移していません」と知らせる。

import { tr, setCatalogue } from "./i18n.js";
import { message, ask, prompt, newDocumentDialog, memoWindow, updateMemoWindow, aboutDialog, toast, openDialog, el } from "./dialogs.js";
import { createKeyHandler, shortcutDialog } from "./shortcuts.js";
import { preferencesDialog } from "./preferences.js";
import {
  panelStates, togglePanel, resetLayout, saveLayout, loadLayout, loadQtLayout, onLayoutChange, setLayoutTheme, translateTitles,
} from "./layout.js";

const REPO = "SehataKuro/PaintMaskAnimator";
const RELEASES_URL = `https://github.com/${REPO}/releases`;
const RELEASE_API = `https://api.github.com/repos/${REPO}/releases/latest`;
const FIRST_CHECK_DELAY_MS = 4000;
const RECHECK_HOURS = 6;

// タイムラインの操作は、タイムラインのパネルのボタンを押して行う（パネルが自分の表示を合わせる）。
const TIMELINE_BUTTONS = {
  a_tl_add_exposure: "コマを伸ばす",
  a_tl_delete_frame: "コマを削除",
  a_tl_previous: "前のフレーム",
  a_tl_previous_key: "前のコマ",
  a_tl_play: "再生／停止",
  a_tl_next_key: "次のコマ",
  a_tl_next: "次のフレーム",
  a_tl_onion: "オニオンスキン表示",
  a_tl_layer_add: "レイヤーを追加",
  a_tl_layer_delete: "レイヤーを削除",
  a_prev: "前のフレーム",
  a_next: "次のフレーム",
};

function isTextField(node) {
  return node instanceof HTMLInputElement && !["checkbox", "radio", "button", "range"].includes(node.type)
    || node instanceof HTMLTextAreaElement
    || node?.isContentEditable;
}

/// 外枠を始める。hooks は app.js が渡す表示の操作（zoomIn・zoomOut・fit・stepFrame・timelinePanel・status）。
export async function initShell({ invoke, listen, hooks, startup }) {
  const info = await invoke("shell_init", { languages: [...(navigator.languages ?? [navigator.language])] });
  setCatalogue(info.language, info.catalogue);
  if (info.language !== "ja") translateTitles(tr);
  let prefs = info.prefs;
  const selftest = Boolean(startup?.pen_selftest || startup?.playback_selftest || startup?.tools_selftest);
  const status = (text) => hooks.status?.(text);

  // ---- テーマ ----
  const dark = window.matchMedia?.("(prefers-color-scheme: dark)");
  function applyTheme() {
    const name = prefs.theme === "system" ? (dark?.matches ? "dark" : "light") : prefs.theme;
    const root = document.documentElement;
    root.dataset.theme = name;
    root.dataset.platform = info.platform;
    if (prefs.accent && prefs.accent !== "system") root.style.setProperty("--accent", prefs.accent);
    else root.style.removeProperty("--accent");
    setLayoutTheme(name);
    const dock = document.getElementById("dock");
    if (dock) dock.className = `dockview-theme-${name}`;
  }
  dark?.addEventListener?.("change", applyTheme);
  applyTheme();

  // ---- 題名・未保存 ----
  async function refreshTitle() {
    try {
      const state = await invoke("project_state");
      const title = state.title || `${tr("{NAME} — 新規プロジェクト", { NAME: state.app_name })}${state.suffix}`;
      await invoke("set_title", { title });
      return state;
    } catch {
      return null;
    }
  }
  let titleTimer = 0;
  listen("document-changed", ({ payload }) => {
    if (payload?.source === "playback") return;
    clearTimeout(titleTimer);
    titleTimer = setTimeout(refreshTitle, 120);
  });
  refreshTitle();

  // ---- メニューの状態（パネルの表示） ----
  const checked = { a_silhouette: false, a_preview_map: false };
  let menuTimer = 0;
  function sendMenuState() {
    clearTimeout(menuTimer);
    menuTimer = setTimeout(() => {
      invoke("shell_menu_state", { state: { panels: panelStates(), checked } }).catch(() => {});
    }, 30);
  }
  onLayoutChange(sendMenuState);
  sendMenuState();

  // ---- ショートカット ----
  let table = await invoke("shortcuts_get");
  const keys = createKeyHandler({ run: (id) => run(id, "key") });
  keys.setTable(table);
  const labelOf = (id) => table.actions.find((a) => a.id === id)?.label ?? id;

  // ---- ファイル ----
  function showError(title, text, error) {
    return message(title, tr(text, { error: String(error) }));
  }
  async function afterOpen(state) {
    await refreshTitle();
    updateMemoWindow(await invoke("memo_get"));
    const name = state?.path?.split(/[\\/]/).pop();
    if (name) status(tr("プロジェクトを開きました：{name}", { name }));
  }
  async function openPath(path) {
    try {
      await afterOpen(await invoke("project_open", { path }));
      return true;
    } catch (e) {
      await showError(tr("プロジェクト読込エラー"), "プロジェクトを開けませんでした。\n\n{error}", e);
      return false;
    }
  }
  async function saveAs() {
    const state = await invoke("project_state");
    const initial = state.path ? state.path.replace(/\.[^./\\]*$/, "") + ".pman" : "untitled.pman";
    const path = await invoke("pick_save_path", { title: tr("名前を付けて保存"), initial });
    if (!path) return false;
    return write(path);
  }
  async function write(path) {
    try {
      const state = await invoke("project_save", { path });
      await refreshTitle();
      const name = state.path?.split(/[\\/]/).pop() ?? "";
      status(tr("プロジェクトを保存しました：{name}", { name }));
      return true;
    } catch (e) {
      if (e === "save_as") return saveAs();
      await showError(tr("プロジェクト保存エラー"), "保存できませんでした。\n\n{error}", e);
      return false;
    }
  }
  const save = () => write(null);

  /// 未保存の変更があれば尋ねる。続けてよければ true。
  async function confirmUnsaved(title, text) {
    const state = await invoke("project_state");
    if (!state.dirty) return true;
    const answer = await message(title, text, [
      [tr("保存する"), "save", true],
      [tr("保存しない"), "discard"],
      [tr("キャンセル"), "cancel"],
    ], "cancel");
    if (answer === "save") return save();
    return answer === "discard";
  }
  let closing = false;
  async function requestClose() {
    if (closing) return;
    closing = true;
    try {
      if (await confirmUnsaved(tr("未保存の変更"), tr("変更を保存してから終了しますか？"))) {
        await invoke("shell_quit");
      }
    } finally {
      closing = false;
    }
  }
  listen("close-requested", requestClose);

  async function openDropped(path) {
    if (!path.toLowerCase().endsWith(".pman")) return false;
    const ok = await confirmUnsaved(tr("プロジェクトを開く"), tr("現在のキャンバスを保存してから、ドロップしたプロジェクトを開きますか？"));
    return ok && openPath(path);
  }

  async function newDocument() {
    const papers = await invoke("papers_list").catch(() => []);
    prefs = await invoke("prefs_get");
    const values = await newDocumentDialog({
      width: prefs.new_canvas_size[0],
      height: prefs.new_canvas_size[1],
      papers,
      lastPaper: prefs.layout_paper_last,
    });
    if (!values) return;
    try {
      const created = await invoke("project_new", values);
      await afterOpen(null);
      // 読み込み（imports.rs）：レイアウト用紙を入れられなかったときの知らせ。
      if (created?.warning) status(tr(created.warning));
    } catch (e) {
      await message(tr("新規作成"), String(e));
    }
  }

  async function openRecent(index) {
    try {
      const path = await invoke("recent_path", { index });
      await openPath(path);
    } catch (path) {
      if (path) await message(tr("最近使用したファイル"), tr("ファイルが見つかりませんでした。一覧から外します。\n\n{path}", { path }));
    }
  }

  // ---- ワークスペース（Qt の WorkspaceController） ----
  async function setPref(key, value) {
    prefs = await invoke("prefs_set", { key, value });
    return prefs;
  }
  async function saveWorkspace(name) {
    name = String(name ?? "").trim();
    if (!name) return false;
    const records = { ...(prefs.workspaces ?? {}) };
    records[name] = { dockview: saveLayout() };
    await setPref("workspaces", records);
    await setPref("active_workspace", name);
    ctx.reloadWorkspaces?.();
    return true;
  }
  async function applyWorkspace(name) {
    const record = prefs.workspaces?.[name];
    if (record?.dockview) {
      if (!loadLayout(record.dockview)) return;
    } else {
      // 【Group C】Qt 版で保存した配置（dock_state・window_geometry）は workspace.rs が読み替える。
      // 戻せなければ Qt の apply と同じく何もしない。
      const qt = record?.dock_state ? await invoke("workspace_from_qt", { dockState: String(record.dock_state), windowGeometry: record.window_geometry ? String(record.window_geometry) : null }) : null;
      if (!qt || !loadQtLayout(qt)) {
        sendMenuState();
        return;
      }
      if (qt.window) await invoke("workspace_set_window", { geometry: qt.window }).catch(() => {});
    }
    await setPref("active_workspace", name);
    sendMenuState();
  }

  // ---- 更新のお知らせ（Qt の UpdateNoticeController） ----
  let update = null;
  const parse = (text) => (String(text ?? "").trim().replace(/^[vV]/, "").match(/\d+/g) ?? []).map(Number);
  function isNewer(latest, current) {
    const a = parse(latest);
    const b = parse(current);
    if (!a.length) return false;
    const n = Math.max(a.length, b.length);
    for (let i = 0; i < n; i++) {
      const x = a[i] ?? 0;
      const y = b[i] ?? 0;
      if (x !== y) return x > y;
    }
    return false;
  }
  function pickAsset(release) {
    const suffix = { windows: ".exe", macos: ".dmg" }[info.platform];
    const list = Array.isArray(release.assets) ? release.assets : [];
    const candidates = list.filter((a) => suffix && String(a?.name ?? "").toLowerCase().endsWith(suffix) && a.browser_download_url);
    if (!candidates.length) return null;
    const chosen = candidates.find((a) => String(a.name).toLowerCase().includes("setup")) ?? candidates[0];
    return { name: String(chosen.name), url: String(chosen.browser_download_url) };
  }
  // 公開の GitHub Releases API を読む（認証は使わない）。
  async function checkForUpdate() {
    let response;
    try {
      response = await fetch(RELEASE_API, { headers: { Accept: "application/vnd.github+json" } });
    } catch (e) {
      return { status: "error", message: tr("ネットワークエラー: {reason}", { reason: String(e) }) };
    }
    if (response.status === 404) return { status: "error", message: tr("公開されているリリースが見つかりませんでした。") };
    if (response.status === 403 || response.status === 429) return { status: "error", message: tr("GitHubへの問い合わせが多すぎます。しばらくしてから再度お試しください。") };
    if (!response.ok) return { status: "error", message: tr("HTTPエラー: {code}", { code: response.status }) };
    let release;
    try {
      release = await response.json();
    } catch (e) {
      return { status: "error", message: String(e) };
    }
    if (!release || typeof release !== "object") return { status: "error", message: tr("リリース情報の形式が不正です。") };
    const latest = String(release.tag_name ?? "").replace(/^[vV]/, "");
    if (!isNewer(latest, info.version)) return { status: "up_to_date", latest, release };
    return { status: "update_available", latest, release, asset: pickAsset(release) };
  }
  function applyUpdate(result) {
    if (result?.status === "error") return;
    const latest = String(result?.latest ?? "");
    if (result?.status === "update_available" && latest !== prefs.skipped_update_version) {
      update = result;
      invoke("shell_set_update", { latest });
    } else {
      update = null;
      invoke("shell_set_update", { latest: null });
    }
  }
  let checking = false;
  async function backgroundCheck() {
    if (checking || !prefs.check_updates_automatically) return;
    checking = true;
    try {
      applyUpdate(await checkForUpdate());
    } finally {
      checking = false;
    }
  }
  async function interactiveCheck() {
    const result = await checkForUpdate();
    applyUpdate(result);
    if (result.status === "error") return message(tr("更新確認エラー"), result.message || tr("不明なエラー"));
    if (result.status === "up_to_date") return message(tr("更新の確認"), tr("最新版を使用しています。（現在: v{VERSION}）", { VERSION: info.version }));
    if (!result.asset) {
      return message(tr("更新あり"), tr("新しいバージョン {latest} が利用可能ですが、この環境向けの\nインストーラが見つかりませんでした。リリースページを確認してください。\n\n{url}", { latest: result.latest, url: RELEASES_URL }));
    }
    if (await ask(tr("更新があります"), tr("新しいバージョン {latest} が利用可能です。\n（現在: v{VERSION}）\n\nダウンロードしてインストールしますか？", { latest: result.latest, VERSION: info.version }))) {
      await downloadAndInstall(result.asset);
    }
  }
  // 【Group C】ダウンロードしてインストール（Qt の _download_and_run_installer。中身は updater.rs）。
  async function downloadAndInstall(asset) {
    const bar = el("progress", { max: "100", value: "0", style: "width:320px" });
    let box = null;
    const finished = new Promise((resolve) => {
      let unlisten = null;
      listen("update-download", ({ payload }) => {
        if (!payload.done) {
          if (payload.total > 0) bar.value = Math.floor((payload.downloaded * 100) / payload.total);
          return;
        }
        unlisten?.();
        resolve(payload);
      }).then((u) => (unlisten = u));
    });
    try {
      await invoke("update_download", { url: asset.url, name: asset.name });
    } catch (e) {
      return message(tr("ダウンロード失敗"), tr("更新を取得できませんでした。\n\n{error}", { error: tr(String(e)) }));
    }
    const dialog = openDialog({
      title: tr("更新のダウンロード"),
      body: el("div", { class: "sh-form" }, el("div", {}, tr("更新をダウンロードしています…")), bar),
      buttons: [{ label: tr("キャンセル"), value: "cancel", escape: true }],
    });
    box = [...document.querySelectorAll(".sh-dialog")].pop();
    dialog.then((v) => v === "cancel" && invoke("update_cancel"));
    const result = await finished;
    box?.close?.(null);
    if (result.cancelled) return;
    if (result.error) return message(tr("ダウンロード失敗"), tr("更新を取得できませんでした。\n\n{error}", { error: tr(result.error) }));
    if (!(await ask(tr("インストール"), tr("ダウンロードが完了しました。インストーラを起動して\nアプリを終了します。よろしいですか？")))) return;
    try {
      await invoke("update_launch");
    } catch (e) {
      return message(tr("起動失敗"), tr("インストーラを起動できませんでした。\n\n{error}", { error: String(e) }));
    }
    await requestClose();
  }
  const openUrl = (url) => invoke("open_url", { url }).catch((e) => status(String(e)));
  const releaseNotes = () => openUrl(update?.release?.html_url || RELEASES_URL);
  if (!selftest && prefs.check_updates_automatically) {
    setTimeout(backgroundCheck, FIRST_CHECK_DELAY_MS);
    setInterval(backgroundCheck, RECHECK_HOURS * 60 * 60 * 1000);
  }

  // ---- 環境設定 ----
  const ctx = {
    invoke,
    languages: info.languages,
    shell: {
      onPrefsChanged(next, key) {
        prefs = next;
        if (key === "ui_theme" || key === "ui_accent") applyTheme();
      },
      saveWorkspace,
    },
    reloadWorkspaces: null,
  };
  const showPreferences = (section) => preferencesDialog(ctx, section).then(async () => {
    prefs = await invoke("prefs_get");
  });

  // ---- 操作 ----
  function clickTimeline(title) {
    const panel = hooks.timelinePanel?.();
    const button = panel && [...panel.querySelectorAll("button")].find((b) => b.title.startsWith(title));
    if (!button || button.disabled) return false;
    button.click();
    return true;
  }
  function timelineKey(init) {
    const panel = hooks.timelinePanel?.();
    panel?.dispatchEvent(new KeyboardEvent("keydown", { bubbles: false, cancelable: true, ...init }));
  }
  function pending(id, source) {
    const handled = !window.dispatchEvent(new CustomEvent("pma-action", { detail: { id, source }, cancelable: true }));
    if (!handled) toast(tr("この機能はまだ移していません：{name}", { name: tr(labelOf(id)) }));
  }
  // 編集の操作：文字の欄ではその欄に、タイムラインではタイムラインに。
  const EDIT_COMMANDS = { a_undo: "undo", a_redo: "redo", a_cut: "cut", a_copy: "copy", a_paste: "paste", a_selection_all: "selectAll" };
  const EDIT_KEYS = { a_cut: "x", a_copy: "c", a_paste: "v" };
  function editInPlace(id) {
    const active = document.activeElement;
    if (isTextField(active) && EDIT_COMMANDS[id]) {
      if (id === "a_paste" && navigator.clipboard?.readText) {
        navigator.clipboard.readText().then((text) => document.execCommand("insertText", false, text)).catch(() => {});
      } else {
        document.execCommand(EDIT_COMMANDS[id]);
      }
      return true;
    }
    const panel = hooks.timelinePanel?.();
    if (EDIT_KEYS[id] && panel?.contains(active)) {
      // そのキーをタイムラインがもう受けていたら、二重にしない。
      const want = `Ctrl+${EDIT_KEYS[id].toUpperCase()}`;
      if (!(keys.last.combo === want && keys.last.handled && performance.now() - keys.last.time < 500)) {
        timelineKey({ key: EDIT_KEYS[id], metaKey: true, ctrlKey: true });
      }
      return true;
    }
    return false;
  }

  async function run(id, source = "menu", isPending = false) {
    if (id.startsWith("recent:")) {
      if (id === "recent:clear") await invoke("recent_clear");
      else if (/^recent:\d+$/.test(id)) await openRecent(Number(id.slice(7)));
      return;
    }
    if (id.startsWith("panel:")) {
      togglePanel(id.slice(6));
      sendMenuState();
      return;
    }
    if (id.startsWith("workspace:")) {
      const what = id.slice(10);
      if (what === "save") {
        const name = await prompt(tr("ワークスペースを保存"), tr("ワークスペース名："));
        if (name?.trim()) await saveWorkspace(name);
      } else if (what === "default") {
        resetLayout();
        await setPref("active_workspace", null);
        sendMenuState();
      } else if (what === "manage") {
        await showPreferences("workspaces");
      } else {
        const name = Object.keys(prefs.workspaces ?? {})[Number(what)];
        if (name) await applyWorkspace(name);
      }
      return;
    }
    switch (id) {
      case "app:quit":
        return requestClose();
      case "help:about":
        return aboutDialog({ appName: "PaintMaskAnimator", displayName: info.app_name, repo: `https://github.com/${REPO}`, openUrl });
      case "help:check_update":
        return interactiveCheck();
      case "update:install":
        if (update?.asset) return downloadAndInstall(update.asset);
        return releaseNotes();
      case "update:notes":
        return releaseNotes();
      case "update:skip":
        if (update) await setPref("skipped_update_version", String(update.latest ?? ""));
        update = null;
        return invoke("shell_set_update", { latest: null });
      case "a_new":
        return newDocument();
      case "a_open_project": {
        const path = await invoke("pick_open_path", { title: tr("プロジェクトを開く"), filter: "PaintMaskAnimator Project" });
        if (path) await openPath(path);
        return;
      }
      case "a_save_project":
        return save();
      case "a_save_project_as":
        return saveAs();
      case "a_undo":
      case "a_redo":
        if (source === "menu" && editInPlace(id)) return;
        return invoke(id === "a_undo" ? "undo" : "redo").catch((e) => status(String(e)));
      case "a_cut":
      case "a_copy":
      case "a_paste":
      case "a_selection_all":
        if (editInPlace(id)) return;
        return pending(id, source);
      case "a_preferences":
        return showPreferences();
      case "a_shortcuts": {
        const saved = await shortcutDialog({ invoke, table });
        if (saved) {
          table = saved;
          keys.setTable(table);
        }
        return;
      }
      case "a_memo":
        return memoWindow({
          text: await invoke("memo_get"),
          onChange: (text) => invoke("memo_set", { text }).then(refreshTitle),
        });
      case "a_view_zoom_in":
        return hooks.zoomIn?.();
      case "a_view_zoom_out":
        return hooks.zoomOut?.();
      case "a_view_fit":
        return hooks.fit?.();
      case "a_tl_layer_rename":
        return timelineKey({ key: "F2" });
      default:
        break;
    }
    if (TIMELINE_BUTTONS[id]) {
      if (clickTimeline(TIMELINE_BUTTONS[id])) return;
      if (id === "a_prev" || id === "a_tl_previous") return hooks.stepFrame?.(-1);
      if (id === "a_next" || id === "a_tl_next") return hooks.stepFrame?.(1);
      return;
    }
    if (isPending || id.startsWith("a_") || id.startsWith("tool:")) pending(id, source);
  }

  listen("menu-action", ({ payload }) => {
    run(payload.id, "menu", payload.pending).catch((e) => status(String(e)));
  });

  // 【Group C】起動したら、選んでいたワークスペースを当てる（Qt の _finalize_startup_dock_ui）。
  if (!selftest && prefs.active_workspace && prefs.workspaces?.[prefs.active_workspace]) {
    await applyWorkspace(prefs.active_workspace).catch(() => {});
  }

  // ---- 異常終了のあとの復元 ----
  if (info.recovery && !selftest && !startup?.open) {
    const restore = await ask(tr("作業の復元"), tr("前回のセッションが正常に終了しなかった可能性があります。\n自動保存された作業を復元しますか？"));
    if (restore) {
      try {
        await afterOpen(await invoke("autosave_restore"));
      } catch (e) {
        await showError(tr("プロジェクト読込エラー"), "プロジェクトを開けませんでした。\n\n{error}", e);
      }
    } else {
      await invoke("autosave_discard");
    }
  }

  // 確かめ用：PMA_SHELL_DEMO の項目を順に選ぶ（窓の写しを撮るため）。
  if (info.demo) {
    (async () => {
      for (const id of info.demo.split(",")) {
        await new Promise((r) => setTimeout(r, 600));
        run(id.trim(), "menu").catch((e) => status(String(e)));
      }
    })();
  }

  return { run, openPath, openDropped, refreshTitle };
}
