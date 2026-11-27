// 言葉の切り替え（Qt 版の i18n.tr）。原文は日本語で、表（Rust の i18n.rs が
// paintmaskanimator_en.ts から作った JSON）に訳があれば訳を使う。
//
// 言語は起動のときに決まる（環境設定で変えると次の起動から。Qt と同じ）。

let table = {};
let language = "ja";

/// 起動のとき shell.js が呼ぶ。catalogue は「日本語 → 訳」の JSON 文字列。
export function setCatalogue(lang, catalogue) {
  language = lang;
  try {
    table = typeof catalogue === "string" ? JSON.parse(catalogue || "{}") : catalogue || {};
  } catch {
    table = {};
  }
  document.documentElement.lang = lang;
}

export function currentLanguage() {
  return language;
}

/// 訳す。{name} の形の差し込みは values で埋める（Qt の tr(...).format(...)）。
// Qt が自分で訳す標準のボタンと、Tauri の版で足した言葉（.ts に無い）。
const EXTRA_EN = {
  はい: "Yes",
  いいえ: "No",
  "PaintMaskAnimator を終了": "Quit PaintMaskAnimator",
  "この機能はまだ移していません：{name}": "Not ported yet: {name}",
  // バージョン情報（Qt では1つの HTML の文。この画面では段落ごとに分けた）。
  "本ソフトウェアは Apache License 2.0 のもとで配布されています。商用・非商用を問わず、自由に利用・改変・再配布できます。":
    "This software is distributed under the Apache License 2.0. You may use, modify and redistribute it freely, commercially or not.",
  "本ソフトウェアは「現状有姿」で提供され、明示黙示を問わずいかなる保証もありません。詳細はライセンス全文を参照してください。":
    "This software is provided \"as is\", without warranty of any kind, express or implied. See the full license text for details.",
  "ライセンス全文: ": "Full license: ",
  "ソースコード: ": "Source code: ",
  サードパーティコンポーネント: "Third-party components",
};

export function tr(text, values) {
  let out = table[text] ?? (language === "en" ? EXTRA_EN[text] : undefined) ?? text;
  if (values) {
    for (const [key, value] of Object.entries(values)) out = out.split(`{${key}}`).join(String(value));
  }
  return out;
}

/// data-i18n の付いた要素の文字と、data-i18n-title の付いた要素の説明を訳す。
export function translateTree(root = document) {
  for (const node of root.querySelectorAll("[data-i18n]")) node.textContent = tr(node.dataset.i18n);
  for (const node of root.querySelectorAll("[data-i18n-title]")) node.title = tr(node.dataset.i18nTitle);
}
