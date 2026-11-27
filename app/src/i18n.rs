//! 画面の言葉の翻訳（Qt 版の i18n.py）。
//!
//! 画面は日本語で書き、日本語の文がそのまま鍵になる（翻訳が無ければ日本語のまま）。
//! 英語の訳は Qt 版の `paintmaskanimator/translations/paintmaskanimator_en.ts` から作った
//! `i18n/en.json`（日本語 → 英語の表）を使う。メニュー（Rust）も画面（i18n.js）も同じ表を引く。
//!
//! 表を作り直すには：`PMA_UPDATE_I18N=1 cargo test i18n`（.ts から en.json を書き出す）。
//! 普段の `cargo test` は、en.json が .ts と同じかを確かめる。

use std::collections::BTreeMap;
use std::sync::OnceLock;

/// 英語の表（日本語 → 英語）。
const EN_JSON: &str = include_str!("../i18n/en.json");

/// 元の言語。
pub const DEFAULT_LANGUAGE: &str = "ja";

fn english() -> &'static BTreeMap<String, String> {
    static TABLE: OnceLock<BTreeMap<String, String>> = OnceLock::new();
    TABLE.get_or_init(|| serde_json::from_str(EN_JSON).unwrap_or_default())
}

/// 言語の表の JSON（画面の i18n.js が読む）。日本語なら空の表。
pub fn catalogue_json(language: &str) -> &'static str {
    if language == "en" {
        EN_JSON
    } else {
        "{}"
    }
}

/// `text` を `language` へ訳す。訳が無ければそのまま返す。
pub fn tr(language: &str, text: &str) -> String {
    if language == "en" {
        if let Some(found) = english().get(text) {
            return found.clone();
        }
    }
    text.to_string()
}

/// 設定の言語（"system"・"ja"・"en"）と OS の言語の並びから、使う言語を決める
/// （i18n.system_language：最初に合う言語。無ければ日本語）。
pub fn resolve(setting: &str, system: &[String]) -> String {
    if setting == "ja" || setting == "en" {
        return setting.to_string();
    }
    for name in system {
        let code = name
            .replace('-', "_")
            .split('_')
            .next()
            .unwrap_or("")
            .to_lowercase();
        if code == "ja" || code == "en" {
            return code;
        }
    }
    DEFAULT_LANGUAGE.to_string()
}

/// Qt Linguist の .ts（文脈は一つ）から、訳のある項目を日本語 → 訳の表にする。
#[cfg(test)]
fn parse_ts(text: &str) -> BTreeMap<String, String> {
    fn unescape(s: &str) -> String {
        let mut out = String::with_capacity(s.len());
        let mut rest = s;
        while let Some(i) = rest.find('&') {
            out.push_str(&rest[..i]);
            rest = &rest[i..];
            let Some(end) = rest.find(';') else { break };
            let entity = &rest[1..end];
            let decoded = match entity {
                "lt" => Some('<'),
                "gt" => Some('>'),
                "amp" => Some('&'),
                "quot" => Some('"'),
                "apos" => Some('\''),
                _ if entity.starts_with("#x") => u32::from_str_radix(&entity[2..], 16)
                    .ok()
                    .and_then(char::from_u32),
                _ if entity.starts_with('#') => entity[1..].parse().ok().and_then(char::from_u32),
                _ => None,
            };
            match decoded {
                Some(c) => {
                    out.push(c);
                    rest = &rest[end + 1..];
                }
                None => {
                    out.push('&');
                    rest = &rest[1..];
                }
            }
        }
        out.push_str(rest);
        out
    }
    fn between<'a>(text: &'a str, open: &str, close: &str) -> Option<&'a str> {
        let start = text.find(open)? + open.len();
        let end = text[start..].find(close)? + start;
        Some(&text[start..end])
    }
    let mut table = BTreeMap::new();
    for message in text.split("<message>").skip(1) {
        let Some(message) = message.split("</message>").next() else {
            continue;
        };
        let Some(source) = between(message, "<source>", "</source>") else {
            continue;
        };
        // <translation> か <translation type="unfinished"> など。
        let Some(start) = message.find("<translation") else {
            continue;
        };
        let rest = &message[start..];
        if rest.starts_with("<translation type=\"vanished\"")
            || rest.starts_with("<translation type=\"obsolete\"")
            || rest.starts_with("<translation type=\"unfinished\"")
        {
            continue;
        }
        let Some(open_end) = rest.find('>') else {
            continue;
        };
        if rest[..open_end].ends_with('/') {
            continue;
        }
        let Some(close) = rest.find("</translation>") else {
            continue;
        };
        let translation = &rest[open_end + 1..close];
        if translation.is_empty() {
            continue;
        }
        table.insert(unescape(source), unescape(translation));
    }
    table
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn i18n_catalogue_matches_qt_ts() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"));
        let ts = root.join("../paintmaskanimator/translations/paintmaskanimator_en.ts");
        let Ok(text) = std::fs::read_to_string(&ts) else {
            // Qt 版を外したあとは、en.json がそのまま正本になる。
            return;
        };
        let table = parse_ts(&text);
        let json = serde_json::to_string_pretty(&table).unwrap() + "\n";
        if std::env::var_os("PMA_UPDATE_I18N").is_some() {
            std::fs::write(root.join("i18n/en.json"), &json).unwrap();
            return;
        }
        assert!(table.len() > 1000, "訳の数が少なすぎる：{}", table.len());
        assert_eq!(
            EN_JSON, json,
            "i18n/en.json が .ts と違う。PMA_UPDATE_I18N=1 cargo test i18n で作り直す"
        );
    }

    #[test]
    fn i18n_translates_menu_words() {
        assert_eq!(tr("en", "元に戻す"), "Undo");
        assert_eq!(tr("ja", "元に戻す"), "元に戻す");
        assert_eq!(tr("en", "訳の無い言葉"), "訳の無い言葉");
    }

    #[test]
    fn i18n_resolves_system_language() {
        let langs = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(resolve("system", &langs(&["fr-FR", "en-US"])), "en");
        assert_eq!(resolve("system", &langs(&["ja-JP"])), "ja");
        assert_eq!(resolve("system", &langs(&["de"])), "ja");
        assert_eq!(resolve("en", &langs(&["ja"])), "en");
    }

    #[test]
    fn i18n_parses_entities() {
        let ts = "<message><source>a&amp;b&#x3000;</source><translation>x&lt;y</translation></message>\
                  <message><source>z</source><translation type=\"unfinished\"></translation></message>";
        let table = parse_ts(ts);
        assert_eq!(table.get("a&b\u{3000}").map(String::as_str), Some("x<y"));
        assert!(!table.contains_key("z"));
    }
}
