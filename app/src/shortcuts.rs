//! ショートカットの保存と復元（Qt 版の main_window_shortcuts.ShortcutStore）。
//!
//! - 設定の `shortcuts` に `{"version": 1, "actions": {ID: {"keys": [...]}}}` の形で、既定から
//!   変えたものだけを保存する（押している間の操作は `{"hold": "Space"}`）。
//! - 保存した ID が今の一覧に無ければ無視する。パネルのボタン（`panel:` で始まる ID）の設定は、
//!   読み込めなかったときのために消さずに残す。
//! - 自分で決めたキーと既定のキーがぶつかったら、既定の側からそのキーを外す。

use crate::actions::{Action, ACTIONS};
use serde_json::{json, Map, Value};
use std::collections::{HashMap, HashSet};

pub const FORMAT_VERSION: i64 = 1;
pub const PANEL_PREFIX: &str = "panel:";

/// 設定の `shortcuts` から、保存した変更（ID → 値）を取り出す。
fn stored_overrides(stored: Option<&Value>) -> Map<String, Value> {
    stored
        .and_then(|v| v.get("actions"))
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default()
}

fn default_keys(action: &Action) -> Vec<String> {
    action.keys.iter().map(|k| k.to_string()).collect()
}

/// 保存した変更を当てた、各操作の今のキー。押している間の操作は 1 つ（空なら無し）。
pub fn effective(stored: Option<&Value>) -> HashMap<String, Vec<String>> {
    let overrides = stored_overrides(stored);
    let mut keys: HashMap<String, Vec<String>> = HashMap::new();
    let mut claimed = HashSet::new();
    let mut applied = HashSet::new();
    for (id, state) in &overrides {
        let Some(action) = ACTIONS.iter().find(|a| a.id == id) else {
            continue;
        };
        let Some(state) = state.as_object() else {
            continue;
        };
        let list: Vec<String> = if action.hold {
            let text = state
                .get("hold")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if !text.is_empty() {
                claimed.insert(text.clone());
            }
            if text.is_empty() {
                vec![]
            } else {
                vec![text]
            }
        } else {
            let list: Vec<String> = state
                .get("keys")
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .filter(|s| !s.is_empty())
                        .map(String::from)
                        .collect()
                })
                .unwrap_or_default();
            claimed.extend(list.iter().cloned());
            list
        };
        keys.insert(id.clone(), list);
        applied.insert(id.clone());
    }
    for action in ACTIONS {
        if applied.contains(action.id) {
            continue;
        }
        let mut list = default_keys(action);
        if !action.hold {
            list.retain(|k| !claimed.contains(k));
        }
        keys.insert(action.id.to_string(), list);
    }
    keys
}

/// 画面で決めた全部のキーから、保存する値を作る（既定と同じものは書かない）。
/// 変えたものが無ければ None（設定から消す）。
pub fn to_stored(previous: Option<&Value>, keys: &HashMap<String, Vec<String>>) -> Value {
    let mut overrides = Map::new();
    for (id, state) in stored_overrides(previous) {
        if id.starts_with(PANEL_PREFIX) && !ACTIONS.iter().any(|a| a.id == id) {
            overrides.insert(id, state);
        }
    }
    for action in ACTIONS {
        let Some(list) = keys.get(action.id) else {
            continue;
        };
        if *list == default_keys(action) {
            continue;
        }
        let state = if action.hold {
            json!({"hold": list.first().cloned().unwrap_or_default()})
        } else {
            json!({"keys": list})
        };
        overrides.insert(action.id.to_string(), state);
    }
    if overrides.is_empty() {
        Value::Null
    } else {
        json!({"version": FORMAT_VERSION, "actions": overrides})
    }
}

/// 同じキーが 2 つ以上の操作に付いていれば、その [(キー, 先の名前の ID, 後の ID)]。
pub fn duplicates(keys: &HashMap<String, Vec<String>>) -> Vec<(String, String, String)> {
    let mut used: HashMap<&str, &str> = HashMap::new();
    let mut out = Vec::new();
    for action in ACTIONS {
        // ショートカット設定に出る操作どうしで比べる（Qt の ShortcutDialog.apply）。
        if action.shortcut.is_none() {
            continue;
        }
        for key in keys.get(action.id).into_iter().flatten() {
            match used.get(key.as_str()) {
                Some(first) => out.push((key.clone(), first.to_string(), action.id.to_string())),
                None => {
                    used.insert(key, action.id);
                }
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_when_nothing_is_stored() {
        let keys = effective(None);
        assert_eq!(keys["a_new"], vec!["Ctrl+N"]);
        assert_eq!(keys["a_redo"], vec!["Ctrl+Y", "Ctrl+Shift+Z"]);
        assert_eq!(keys["a_hold_hand"], vec!["Space"]);
        assert_eq!(to_stored(None, &keys), Value::Null);
    }

    #[test]
    fn user_keys_win_over_defaults_and_round_trip() {
        let stored = json!({"version": 1, "actions": {
            "a_memo": {"keys": ["Ctrl+N"]},
            "a_hold_hand": {"hold": "H"},
            "panel:mine": {"keys": ["F5"]},
            "gone": {"keys": ["F6"]},
        }});
        let keys = effective(Some(&stored));
        assert_eq!(keys["a_memo"], vec!["Ctrl+N"]);
        // 既定の Ctrl+N は自分で決めた側に譲る。
        assert!(keys["a_new"].is_empty());
        assert_eq!(keys["a_hold_hand"], vec!["H"]);
        let again = to_stored(Some(&stored), &keys);
        let actions = again["actions"].as_object().unwrap();
        assert_eq!(actions["a_memo"], json!({"keys": ["Ctrl+N"]}));
        assert_eq!(actions["a_new"], json!({"keys": []}));
        assert_eq!(actions["a_hold_hand"], json!({"hold": "H"}));
        // パネルのボタンの設定は残し、消えた操作の設定は捨てる。
        assert!(actions.contains_key("panel:mine"));
        assert!(!actions.contains_key("gone"));
        assert_eq!(again["version"], json!(1));
    }

    #[test]
    fn finds_duplicates() {
        let mut keys = effective(None);
        keys.insert("a_memo".into(), vec!["Ctrl+N".into()]);
        let dups = duplicates(&keys);
        assert_eq!(dups.len(), 1);
        assert_eq!(dups[0].0, "Ctrl+N");
    }
}
