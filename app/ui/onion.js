// オニオンスキンの設定の窓と、キャンバスで合わせる操作（Qt 版の onion.py の
// OnionSkinSettingsBrowser・main_window_onion.py・canvas_onion_interaction.py）。
//
// - 設定・描画・ドラッグの計算は Rust（app/src/onion.rs → pma-core の compose::onion・
//   tools::onion）。ここは窓の欄と、キャンバスの押す・動かす・離すを送るだけ。
// - 「X・Yシフト」「表示位置」のボタンを押すと、次のキャンバスのドラッグ 1 回で合わせる。
//   Esc でドラッグ前に戻す。
// - Ctrl＋クリック（#60）：オニオンに見えている前後のコマへ編集を移す（canvas_tools.js）。
import { el, openDialog } from "./dialogs.js";
import { notify } from "./tool_properties.js";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

let view = null;
let windowBox = null;
let refreshWindow = null;
const subscribers = new Set();

function publish(next) {
  view = next;
  refreshWindow?.();
  for (const fn of subscribers) fn(view);
}

export const onionStore = {
  get view() {
    return view;
  },
  subscribe(fn) {
    subscribers.add(fn);
    return () => subscribers.delete(fn);
  },
  async refresh() {
    publish(await invoke("onion_state"));
  },
};

listen("onion-changed", ({ payload }) => publish(payload));
listen("document-changed", ({ payload }) => {
  // 作品を開いた・タイムラインのボタンで ON／OFF したときに読み直す。
  if (payload.source !== "onion" && payload.source !== "canvas") onionStore.refresh();
});

function viewState(viewer) {
  return { pan: [viewer.pan.x, viewer.pan.y], rotation: viewer.rotation, zoom: viewer.zoom };
}

function applyView(viewer, state) {
  if (!state) return;
  viewer.pan = { x: state.pan[0], y: state.pan[1] };
  viewer.setRotation(state.rotation);
}

const set = (patch) => invoke("onion_set", { patch }).catch((e) => notify(String(e)));

/// キャンバスで合わせる操作。canvas_tools.js が押す・動かす・離すを渡す。
export function onionInteraction(viewer) {
  let dragging = false;
  // 押す・動かす・離すは順に送る。動かすは最後の 1 点だけにまとめる。
  let chain = Promise.resolve();
  let pending = null;
  const queue = (fn) => (chain = chain.then(fn).catch((e) => notify(String(e))));
  const pump = async () => {
    const next = pending;
    pending = null;
    if (!next) return;
    applyView(viewer, await invoke("onion_drag", { ...next, rotate: false, view: viewState(viewer) }));
  };
  return {
    /// 合わせる操作の途中か（押したときに先に受ける）。
    get armed() {
      return !!view?.interaction;
    },
    get dragging() {
      return dragging;
    },
    down(x, y, rotate) {
      dragging = true;
      queue(() => invoke("onion_drag", { phase: "begin", x, y, rotate, view: viewState(viewer) }));
    },
    move(x, y, phase = "move") {
      if (!dragging) return;
      if (phase === "end") dragging = false;
      const coalesce = pending && pending.phase === "move";
      pending = { phase, x, y };
      if (!coalesce) queue(pump);
    },
    /// Esc：ドラッグ中なら始めた所へ戻して、操作をやめる。
    cancel() {
      dragging = false;
      pending = null;
      queue(async () => {
        const reply = await invoke("onion_interaction", { mode: "none", restore: true, view: viewState(viewer) });
        applyView(viewer, reply.view);
      });
    },
  };
}

/// 設定の窓（Qt の「オニオンスキン設定」）。開いていれば閉じる（タイムラインのボタンの再クリック）。
export async function toggleOnionSettings(viewer) {
  if (windowBox?.isConnected) {
    windowBox.close(null);
    return;
  }
  await onionStore.refresh();
  const num = (min, max, step = 1) => el("input", { type: "number", min: String(min), max: String(max), step: String(step) });
  const row = (label, ...controls) => el("label", { class: "pma-onion-row" }, el("span", {}, label), ...controls);
  const hex = (c) => `#${c.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
  const rgb = (text) => [1, 3, 5].map((i) => parseInt(text.slice(i, i + 2), 16));
  const refreshers = [];

  function sideBox(key, title) {
    const count = num(0, 12);
    const opacity = num(1, 100);
    const color = el("input", { type: "color" });
    const colorOn = el("input", { type: "checkbox" });
    const levels = el("div", { class: "pma-onion-levels" });
    const shiftX = num(-10000, 10000, 0.1);
    const shiftY = num(-10000, 10000, 0.1);
    const rotation = num(-180, 180, 0.1);
    const scale = num(1, 199, 0.1);
    // 【Group C】TU/TB拡大率のスライダー（Qt の OnionScaleSlider。中央が100%、1%刻み、Shiftで3%）。
    const scaleSlider = el("input", {
      type: "range", min: "1", max: "199", step: "1", class: "pma-onion-scale",
      title: "中央が100%。左で縮小、右で拡大します。通常は1%ずつ、Shiftを押しながら操作すると3%ずつ動きます。",
    });
    scaleSlider.addEventListener("keydown", (e) => {
      const keys = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -1, PageUp: 1 };
      if (!(e.key in keys)) return;
      e.preventDefault();
      const value = Math.max(1, Math.min(199, Number(scaleSlider.value) + keys[e.key] * (e.shiftKey ? 3 : 1)));
      scaleSlider.value = String(value);
      scale.value = String(value);
      send({ scale: value });
    });
    scaleSlider.addEventListener("input", () => {
      scale.value = scaleSlider.value;
    });
    scaleSlider.addEventListener("change", () => send({ scale: Number(scaleSlider.value) || 100 }));
    const direction = key === "previous" ? -1 : 1;
    const dragButton = (axis, label) =>
      el("button", {
        type: "button",
        title: "押してからキャンバスをドラッグすると、ずらしを合わせます（Esc で戻す）。",
        onclick: async () => {
          const reply = await invoke("onion_interaction", { mode: "shift", direction, axis });
          if (reply.message) notify(reply.message);
        },
      }, label);
    const send = (patch) => set({ [key]: patch });
    count.addEventListener("change", () => send({ count: Number(count.value) || 0 }));
    opacity.addEventListener("change", () => send({ opacity: (Number(opacity.value) || 1) / 100 }));
    color.addEventListener("change", () => send({ color: rgb(color.value) }));
    colorOn.addEventListener("change", () => send({ color_enabled: colorOn.checked }));
    shiftX.addEventListener("change", () => send({ shift_x: Number(shiftX.value) || 0 }));
    shiftY.addEventListener("change", () => send({ shift_y: Number(shiftY.value) || 0 }));
    rotation.addEventListener("change", () => send({ rotation: Number(rotation.value) || 0 }));
    scale.addEventListener("change", () => send({ scale: Number(scale.value) || 100 }));
    const reset = el("button", {
      type: "button",
      onclick: () => invoke("onion_reset", { direction, both: false }),
    }, "表示をリセット");
    refreshers.push(() => {
      const s = view[key];
      const focused = document.activeElement;
      const put = (input, value) => {
        if (input !== focused) input.value = String(value);
      };
      put(count, s.count);
      put(opacity, Math.round(s.opacity * 100));
      put(color, hex(s.color));
      colorOn.checked = s.color_enabled;
      put(shiftX, Math.round(s.shift_x * 10) / 10);
      put(shiftY, Math.round(s.shift_y * 10) / 10);
      put(rotation, Math.round(s.rotation * 10) / 10);
      put(scale, Math.round(s.scale * 10) / 10);
      put(scaleSlider, Math.round(s.scale));
      // コマ別濃度（近い順）。
      if (levels.children.length !== s.levels.length) {
        levels.replaceChildren(...s.levels.map((_, i) => {
          const input = num(0, 100);
          input.title = `現在コマから${i + 1}枚離れたオニオンスキン`;
          input.addEventListener("change", () => {
            const values = [...levels.children].map((c) => Number(c.value) || 0);
            send({ levels: values });
          });
          return input;
        }));
      }
      s.levels.forEach((v, i) => put(levels.children[i], v));
    });
    return el("fieldset", { class: "pma-onion-side" },
      el("legend", {}, title),
      row("表示枚数", count, el("span", {}, " 枚")),
      row("全体濃度", opacity, el("span", {}, "%")),
      row("表示色", color, el("label", {}, colorOn, "表示色を適用")),
      row("コマ別濃度", levels),
      row("X", shiftX, dragButton("x", "X")),
      row("Y", shiftY, dragButton("y", "Y")),
      row("X・Yシフト", dragButton("xy", "X・Yシフト")),
      row("回転", rotation),
      row("拡大率", scaleSlider, scale, el("span", {}, "%")),
      reset,
    );
  }

  const enabled = el("input", { type: "checkbox" });
  enabled.addEventListener("change", () => set({ enabled: enabled.checked }));
  const selectedOnly = el("input", { type: "checkbox" });
  selectedOnly.addEventListener("change", () => set({ selected_colors_only: selectedOnly.checked }));
  const canvasRotation = num(-180, 180, 0.1);
  canvasRotation.addEventListener("change", async () => {
    const next = await invoke("onion_set_rotation", { value: Number(canvasRotation.value) || 0, view: viewState(viewer) });
    applyView(viewer, next);
  });
  const position = el("button", {
    type: "button",
    title: "左：移動　Shift／右：回転",
    onclick: async () => {
      const reply = await invoke("onion_interaction", { mode: "canvas" });
      if (reply.message) notify(reply.message);
    },
  }, "表示位置");
  // 【Group C】「キャンバスを中央位置へ」と中央％（Qt の center_canvas_button・center_percent_slider）。
  const centerSlider = el("input", { type: "range", min: "0", max: "100", step: "1" });
  const centerValue = num(0, 100);
  centerSlider.addEventListener("input", () => (centerValue.value = centerSlider.value));
  const sendCenterPercent = (v) => set({ center_percent: Math.max(0, Math.min(100, Math.round(Number(v) || 0))) });
  centerSlider.addEventListener("change", () => sendCenterPercent(centerSlider.value));
  centerValue.addEventListener("change", () => {
    centerSlider.value = centerValue.value;
    sendCenterPercent(centerValue.value);
  });
  const centerButton = el("button", {
    type: "button",
    title: "中央％で指定した前後間の位置・回転・TU／TB拡大率をキャンバス基準へ取り込みます。キャンバス回転は0°へ戻し、表示用デジタルズームは変更しません。",
    onclick: async () => {
      try {
        const reply = await invoke("onion_center", { percent: Number(centerValue.value) || 0, view: viewState(viewer) });
        applyView(viewer, reply.view);
        notify(reply.message);
      } catch (e) {
        notify(String(e));
      }
    },
  }, "キャンバスを中央位置へ");
  refreshers.push(() => {
    const focused = document.activeElement;
    const value = String(Math.round(view.center_percent));
    if (centerSlider !== focused) centerSlider.value = value;
    if (centerValue !== focused) centerValue.value = value;
  });
  const resetBoth = el("button", { type: "button", onclick: () => invoke("onion_reset", { direction: 0, both: true }) }, "前後の表示をリセット");
  refreshers.push(() => {
    enabled.checked = view.enabled;
    selectedOnly.checked = view.selected_colors_only;
    if (document.activeElement !== canvasRotation) canvasRotation.value = String(Math.round(viewer.rotation * 10) / 10);
    position.classList.toggle("active", view.interaction === "canvas");
  });
  const body = el("div", { class: "pma-onion" },
    el("label", { class: "pma-onion-row" }, enabled, "オニオンスキンを表示"),
    el("div", { class: "pma-onion-sides" }, sideBox("previous", "前のコマ"), sideBox("next", "後のコマ")),
    el("label", { class: "pma-onion-row" }, selectedOnly, "選択した使用色のみ表示"),
    row("前後", position, el("span", {}, "回転"), canvasRotation),
    el("div", { class: "pma-onion-row" }, centerButton, el("span", {}, "中央％"), centerSlider, centerValue, el("span", {}, "%")),
    resetBoth,
  );
  refreshWindow = () => {
    if (view) refreshers.forEach((fn) => fn());
  };
  refreshWindow();
  const closed = openDialog({
    title: "オニオンスキン設定",
    body,
    modal: false,
    className: "pma-onion-window",
    buttons: [{ label: "設定ブラウザを閉じる", value: null, escape: true }],
    initialFocus: () => enabled,
  });
  windowBox = body.closest(".sh-dialog");
  closed.then(() => {
    windowBox = null;
    refreshWindow = null;
    // 閉じたら合わせる操作もやめる（Qt の _settings_browser_destroyed）。
    invoke("onion_interaction", { mode: "none", restore: false });
    for (const fn of subscribers) fn(view);
  });
  for (const fn of subscribers) fn(view);
}

export const onionSettingsOpen = () => !!windowBox?.isConnected;
