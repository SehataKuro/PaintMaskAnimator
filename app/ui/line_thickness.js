// 使用色の右クリックの「線の太さを調整」（Qt の adjust_parent_line_thickness と
// widgets.TransformLineThicknessDialog）。計算と状態は Rust（src/line_thickness.rs）。
// ここは「太さを調整」の窓を出し、始める・太さを変える・終えるを送るだけ。
import { el, message, openDialog } from "./dialogs.js";
import { notify } from "./tool_properties.js";
import { tr } from "./i18n.js";

const { invoke } = window.__TAURI__.core;
const TITLE = "太さを調整";

/// 選んだ線の色（親色・子色）で始める。
export async function adjustLineThickness(colors) {
  let begun;
  try {
    begun = await invoke("line_thickness_begin", { colors });
  } catch (e) {
    const text = String(e);
    const [kind, ...rest] = text.split(":");
    const body = rest.join(":") || text;
    if (kind === "status") notify(tr(body));
    else await message(tr(TITLE), tr(kind === "info" || kind === "warn" ? body : text));
    return;
  }
  // 値はしきい値（1〜254）。見せる太さは 255 − しきい値で、右ほど太い（Qt の setInvertedAppearance）。
  const slider = el("input", { type: "range", min: "1", max: "254", step: "1", value: String(Math.max(1, Math.min(254, begun.threshold))) });
  slider.style.direction = "rtl";
  slider.style.flex = "1";
  const label = el("span", { style: "width:42px;text-align:right;display:inline-block" }, String(255 - Number(slider.value)));
  slider.addEventListener("input", () => (label.textContent = String(255 - Number(slider.value))));
  // 重いクオリティの作り直しは、つまみを離したときに一度だけ（Qt の finish_transform_line_adjustment）。
  slider.addEventListener("change", () => invoke("tools_set", { patch: { transform_line_width: Number(slider.value) } }).catch(() => {}));
  const body = el("div", { style: "display:flex;flex-direction:column;gap:8px;width:360px" },
    el("div", {}, tr("選択中の複数色を実線として残す太さをまとめて調整します。")),
    el("div", { style: "display:flex;align-items:center;gap:6px" }, slider, label),
  );
  const ok = await openDialog({
    title: tr(TITLE),
    body,
    buttons: [
      { label: tr("OK"), value: true, primary: true },
      { label: tr("キャンセル"), value: false, escape: true },
    ],
    initialFocus: () => slider,
  });
  try {
    const status = await invoke("line_thickness_finish", { accept: !!ok, value: Number(slider.value) });
    if (status) notify(tr(status));
  } catch (e) {
    await message(tr(TITLE), tr(String(e)));
  }
}
