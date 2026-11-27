//! 作品の付帯情報（.pman の project.json のうち文書の外の値）。
//!
//! Qt 版の MainWindow.build_project_metadata と ProjectIOController.open に対応する。
//! 値は `Session.project.metadata`（pma-core の Json、鍵の順を保つ）に持つ。
//!
//! - メモ（`memo`）はメモの窓が直に書く。
//! - fps・再生範囲（`playback_range`）・表示の種類（`timeline_mode`）はタイムラインの
//!   パネルの状態（TimelineUi）が持つので、開いたときに読み込み（[`load_into_timeline`]）、
//!   保存の前に書き戻す（[`store_from_session`]）。
//! - 色・オニオン・筆圧などの鍵は、それを持つパネル（ツール・色）が移ってから書く。今は
//!   開いた作品の値をそのまま残す。

use crate::session::Session;
use crate::timeline::Mode;
use pma_core::project::json::Json;

/// この画面の版（Qt の constants.APP_VERSION にあたる）。
pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

fn int(value: i64) -> Json {
    Json::Int(value.to_string())
}

/// 新規作成の付帯情報（Qt の build_project_metadata の、新しい文書での値のうち文書の外の鍵）。
pub fn new_metadata(width: usize, height: usize) -> Json {
    Json::Object(vec![
        (
            "format".into(),
            Json::Str("PaintMaskAnimatorProject".into()),
        ),
        ("format_version".into(), int(2)),
        ("mask_format".into(), int(2)),
        ("created_app_version".into(), Json::Str(APP_VERSION.into())),
        ("saved_app_version".into(), Json::Str(APP_VERSION.into())),
        (
            "canvas".into(),
            Json::Object(vec![
                ("width".into(), int(width as i64)),
                ("height".into(), int(height as i64)),
            ]),
        ),
        ("fps".into(), int(24)),
        (
            "playback_range".into(),
            Json::Array(vec![int(0), Json::Null]),
        ),
        ("timeline_mode".into(), Json::Str("sheet".into())),
        ("memo".into(), Json::Str(String::new())),
        ("clip_studio_source".into(), Json::Null),
        ("current_frame".into(), int(0)),
        ("active_layer_index".into(), int(0)),
        ("frames".into(), Json::Array(vec![])),
    ])
}

fn as_i64(value: &Json) -> Option<i64> {
    match value {
        Json::Int(text) => text.parse().ok(),
        Json::Float(f) if f.is_finite() => Some(f.trunc() as i64),
        Json::Bool(b) => Some(i64::from(*b)),
        _ => None,
    }
}

/// 作品のメモ（無ければ空）。
pub fn memo(metadata: &Json) -> String {
    match metadata.get("memo") {
        Some(Json::Str(text)) => text.clone(),
        _ => String::new(),
    }
}

pub fn set_memo(metadata: &mut Json, text: &str) {
    metadata.set("memo", Json::Str(text.to_string()));
}

/// 開いた作品の fps・再生範囲・表示の種類をタイムラインへ入れる（Qt の open の
/// timeline.fps.setValue・playback_range・set_mode）。
pub fn load_into_timeline(session: &mut Session) {
    let Some(p) = &session.project else {
        return;
    };
    let metadata = &p.metadata;
    let frames = p.document.frame_order().len();
    if let Some(fps) = metadata.get("fps").and_then(as_i64) {
        session.timeline.set_fps(fps.clamp(1, 60) as u32);
    }
    if let Some(Json::Array(range)) = metadata.get("playback_range") {
        let start = range.first().and_then(as_i64).unwrap_or(0).max(0) as usize;
        let end = range.get(1).and_then(as_i64).map(|e| e.max(0) as usize);
        let start = start.min(frames.saturating_sub(1));
        session.timeline.range_start = start;
        session.timeline.range_end = end.filter(|&e| e > start && e <= frames);
    }
    if let Some(Json::Str(mode)) = metadata.get("timeline_mode") {
        session.timeline.mode = if mode == "sequence" {
            Mode::Sequence
        } else {
            Mode::Sheet
        };
    }
}

/// 保存の前：タイムラインの状態と版を付帯情報へ書き戻す。
pub fn store_from_session(session: &mut Session) {
    let (fps, start, end, mode) = (
        session.timeline.fps,
        session.timeline.range_start,
        session.timeline.range_end,
        session.timeline.mode,
    );
    let (width, height) = (session.display.width(), session.display.height());
    let current = session.summary();
    let Some(p) = &mut session.project else {
        return;
    };
    let m = &mut p.metadata;
    m.set("saved_app_version", Json::Str(APP_VERSION.into()));
    m.set(
        "canvas",
        Json::Object(vec![
            ("width".into(), int(width as i64)),
            ("height".into(), int(height as i64)),
        ]),
    );
    m.set("fps", int(i64::from(fps)));
    m.set(
        "playback_range",
        Json::Array(vec![
            int(start as i64),
            end.map_or(Json::Null, |e| int(e as i64)),
        ]),
    );
    m.set(
        "timeline_mode",
        Json::Str(
            match mode {
                Mode::Sheet => "sheet",
                Mode::Sequence => "sequence",
            }
            .into(),
        ),
    );
    if m.get("memo").is_none() {
        set_memo(m, "");
    }
    m.set("current_frame", int(current.frame as i64));
    m.set("active_layer_index", int(current.layer as i64));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_metadata_has_qt_keys_and_round_trips_through_timeline() {
        let mut s = Session::empty(64, 48);
        let m = &s.project.as_ref().unwrap().metadata;
        assert_eq!(m.get("fps"), Some(&int(24)));
        assert_eq!(memo(m), "");
        s.timeline.set_fps(12);
        set_memo(
            &mut s.project.as_mut().unwrap().metadata,
            "3コマ目のタイミング要確認",
        );
        store_from_session(&mut s);
        let m = s.project.as_ref().unwrap().metadata.clone();
        assert_eq!(m.get("fps"), Some(&int(12)));
        s.timeline.set_fps(30);
        load_into_timeline(&mut s);
        assert_eq!(s.timeline.fps, 12);
        assert_eq!(memo(&m), "3コマ目のタイミング要確認");
        let canvas = m.get("canvas").unwrap();
        assert_eq!(canvas.get("width"), Some(&int(64)));
    }
}
