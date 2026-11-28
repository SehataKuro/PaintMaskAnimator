//! CLIP STUDIO アニメーションの読み込み（imports_clip.rs）の試験。.clip は通さず、
//! 読んだ結果（ClipAnimation）から文書と付帯情報への書き方を確かめる。

use super::*;
use pma_core::image::RgbaImage;
use pma_core::import::clip::{AnimationLayer, CelKey};

fn cel(width: usize, height: usize, value: u8) -> RgbaImage {
    let mut pixels = vec![0u8; width * height * 4];
    pixels[..4].copy_from_slice(&[value, 0, 0, 255]);
    RgbaImage::from_rgba(width, height, &pixels).unwrap()
}

fn key(frame: i64, tag: &str) -> CelKey {
    CelKey {
        frame,
        tag: tag.into(),
    }
}

/// A：1・空・2（2は2コマ）、置かれていない「3」。B：「x」を通しで。
fn animation(fps: f64) -> ClipAnimation {
    ClipAnimation {
        source_name: "cut.clip".into(),
        timeline_name: "タイムライン1".into(),
        width: 8,
        height: 6,
        fps,
        start_frame: 1,
        end_frame: 4,
        layers: vec![
            AnimationLayer {
                name: "A".into(),
                keys: vec![key(1, "1"), key(2, ""), key(3, "2")],
                cels: vec![
                    ("1".into(), cel(8, 6, 10)),
                    ("2".into(), cel(8, 6, 20)),
                    ("3".into(), cel(8, 6, 30)),
                ],
            },
            AnimationLayer {
                name: "B".into(),
                keys: vec![key(1, "x")],
                cels: vec![("x".into(), cel(8, 6, 40))],
            },
        ],
    }
}

#[test]
fn a_fresh_document_needs_no_confirmation_but_edits_do() {
    let mut s = Session::empty(16, 9);
    assert!(!needs_confirmation(&s));
    s.edit_with(None, |doc, _| {
        doc.create_blank_key(0, 0).map_err(|e| e.to_string())?;
        Ok(((), true))
    })
    .unwrap();
    assert!(needs_confirmation(&s));
    let mut s = Session::empty(16, 9);
    s.edit_with(None, |doc, _| {
        doc.add_track().map_err(|e| e.to_string())?;
        Ok(((), true))
    })
    .unwrap();
    assert!(needs_confirmation(&s));
    assert!(!needs_confirmation(&Session::unloaded(16, 9)));
}

#[test]
fn replacing_builds_the_sheet_and_reports_like_qt() {
    let mut s = Session::empty(16, 9);
    project_meta::set_memo(&mut s.project.as_mut().unwrap().metadata, "メモ");
    let message = replace_with_clip(&mut s, &animation(23.976)).unwrap();
    assert_eq!(
        message,
        "CLIP STUDIOから2フォルダー・4フレームを読み込みました。 未配置セル1枚は連番に保持しました。\
         （元FPS 23.976、PMA表示 24 fps） 3セルを色塗り前の初期状態として保持しました。"
    );
    let summary = s.summary();
    assert_eq!((summary.width, summary.height), (8, 6));
    assert_eq!((summary.frames, summary.layers), (4, 2));
    assert_eq!(s.timeline.fps, 24);
    let p = s.project.as_ref().unwrap();
    assert!(p.path.is_none());
    assert!(!p.undo.can_undo());
    let m = &p.metadata;
    assert_eq!(project_meta::memo(m), "メモ");
    assert_eq!(m.get("fps"), Some(&Json::Int("24".into())));
    let source = m.get("clip_studio_source").unwrap();
    assert_eq!(source.get("file_name"), Some(&Json::Str("cut.clip".into())));
    assert_eq!(source.get("fps"), Some(&Json::Float(23.976)));
    assert_eq!(source.get("end_frame"), Some(&Json::Int("4".into())));
    assert!(m.get("used_color_categories").is_none());
    // 置いた絵はすべて初期状態を持つ。
    assert_eq!(p.document.cells_without_initial(None), 0);
}

#[test]
fn whole_fps_has_no_note_and_is_clamped() {
    let mut s = Session::empty(16, 9);
    let message = replace_with_clip(&mut s, &animation(24.0)).unwrap();
    assert!(!message.contains("元FPS"), "{message}");
    assert_eq!(display_fps(120.0), 60);
    assert_eq!(display_fps(0.2), 1);
    // Python の round は偶数へ丸める。
    assert_eq!(display_fps(12.5), 12);
}
