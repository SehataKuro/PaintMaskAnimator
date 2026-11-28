//! paint_ops.rs の試験（画素・履歴の名前・知らせの言葉を Qt 版と合わせる）。
use super::*;
use serde_json::{json, Value};

const W: usize = 24;
const H: usize = 16;
const WHITE_PX: [u8; 4] = [255, 255, 255, 255];
const BLACK_PX: [u8; 4] = [0, 0, 0, 255];
const BLUE: [u8; 4] = [0x33, 0x55, 0xff, 255];

fn position(number: i64) -> Value {
    json!({
        "has_content": true, "is_blank_key": false, "sequence_number": number,
        "sequence_only": false, "blank_start": false, "tween": null, "tween_member": null,
        "local": null, "content_origin": null, "v1_position": null, "dense_import": null,
        "length_imported": null, "resolve_source": null, "legacy_missing": false,
    })
}

/// 白地に黒枠（左上 (x0, y0)、一辺 size）を描いた絵。
fn outlined(x0: usize, y0: usize, size: usize) -> RgbaImage {
    let mut pixels = WHITE_PX.repeat(W * H);
    let mut put = |x: usize, y: usize| pixels[(y * W + x) * 4..][..4].copy_from_slice(&BLACK_PX);
    for i in 0..size {
        put(x0 + i, y0);
        put(x0 + i, y0 + size - 1);
        put(x0, y0 + i);
        put(x0 + size - 1, y0 + i);
    }
    RgbaImage::from_rgba(W, H, &pixels).unwrap()
}

/// 1列「A」で、コマごとに番号 1, 2, ... の絵を持つ作品。描く色は青。
fn session(images: Vec<RgbaImage>) -> Session {
    let cells: Vec<Value> = (0..images.len())
        .map(|i| json!([i + 1, {"image": i, "initial_image": null, "cell_name": null}]))
        .collect();
    let frames: Vec<Value> = (0..images.len())
        .map(|i| json!({"duration": 1, "positions": [position(i as i64 + 1)]}))
        .collect();
    let settings = json!({
        "name": "A", "visible": true, "opacity": 1.0, "alpha_locked": false,
        "is_paper": false, "is_draft": false, "color_filter_enabled": false,
        "color_filter_rgb": null,
    });
    let value = json!({
        "width": W, "height": H, "current_frame": 0, "active_layer": 0,
        "tracks": [{"settings": settings, "hints": [], "cells": cells}],
        "frames": frames,
    });
    let doc = Document::from_graph(&value, &images).unwrap();
    let mut s = Session::empty(8, 8);
    s.open_document(doc).unwrap();
    s.tools.settings.color_main = [0x33, 0x55, 0xff];
    s
}

fn pixel(s: &Session, frame: usize, x: usize, y: usize) -> [u8; 4] {
    let image = bucket::position_image(document(s).unwrap(), frame, 0).unwrap();
    image.rgba()[(y * W + x) * 4..][..4].try_into().unwrap()
}

#[test]
fn bucket_click_fills_inside_and_records_one_drawing_entry() {
    let mut s = session(vec![outlined(4, 4, 8)]);
    let feedback = bucket_click(&mut s, 6.5, 6.5, false).unwrap();
    assert_eq!(pixel(&s, 0, 6, 6), BLUE);
    assert_eq!(pixel(&s, 0, 4, 4), BLACK_PX);
    assert_eq!(pixel(&s, 0, 1, 1), WHITE_PX);
    assert_eq!(feedback.colors, vec![[0x33, 0x55, 0xff]]);
    assert!(feedback.rect.is_some());
    assert_eq!(s.history().undo, vec!["描画（A の 1番）".to_string()]);
    s.undo().unwrap();
    assert_eq!(pixel(&s, 0, 6, 6), WHITE_PX);
}

#[test]
fn bucket_drag_fills_every_enclosed_part_in_one_undo() {
    // 2つの枠。左を押して右までなぞる。
    let mut image = outlined(1, 4, 7).rgba().to_vec();
    let right = outlined(12, 4, 7);
    for y in 0..H {
        for x in 12..W {
            let i = (y * W + x) * 4;
            image[i..i + 4].copy_from_slice(&right.rgba()[i..i + 4]);
        }
    }
    let mut s = session(vec![RgbaImage::from_rgba(W, H, &image).unwrap()]);
    bucket_begin(&mut s, 4.0, 7.0, false).unwrap();
    bucket_extend(&mut s, 15.0, 7.0, false).unwrap();
    bucket_end(&mut s).unwrap();
    assert_eq!(pixel(&s, 0, 4, 7), BLUE);
    assert_eq!(pixel(&s, 0, 15, 7), BLUE);
    assert_eq!(
        pixel(&s, 0, 10, 1),
        WHITE_PX,
        "外の背景はなぞっても塗らない"
    );
    assert_eq!(s.history().undo.len(), 1);
    s.undo().unwrap();
    assert_eq!(pixel(&s, 0, 4, 7), WHITE_PX);
    assert_eq!(pixel(&s, 0, 15, 7), WHITE_PX);
}

#[test]
fn bucket_reports_qt_failure_messages() {
    let mut s = session(vec![outlined(4, 4, 8)]);
    s.tools.settings.bucket_require_closed = true;
    let feedback = bucket_click(&mut s, 1.0, 1.0, false).unwrap();
    assert_eq!(
        feedback.message.as_deref(),
        Some("領域がキャンバス端まで開いているため、塗りを開始しませんでした。")
    );
    assert!(s.history().undo.is_empty());
}

#[test]
fn settings_follow_the_tool_panel() {
    let mut t = Settings {
        bucket_include_sub: true,
        bucket_island_fill: true,
        bucket_island_fill_width: 3,
        bucket_target_color: "transparent".into(),
        bucket_reference: "canvas".into(),
        bucket_include_colors: [Some([1, 2, 3]), Some(WHITE), None],
        bucket_include_colors_enabled: [true, true, true],
        bucket_island_replace: true,
        ..Settings::default()
    };
    let b = bucket_settings(&t, false, &[([1, 1, 1], [2, 2, 2])]);
    assert!(b.include_masks && b.include_background_mask);
    assert_eq!(b.mask_colors, vec![[1, 2, 3], WHITE]);
    assert_eq!(b.island_max_width, 3);
    assert_eq!(b.target, TargetKind::Transparent);
    assert_eq!(b.reference, ReferenceSource::Canvas);
    assert_eq!(b.island_replacements.len(), 1);
    assert!(
        !bucket_settings(&t, true, &[]).include_masks,
        "Shift で含み塗りを外す"
    );
    t.bucket_include_colors = [None; 3];
    assert_eq!(
        bucket_settings(&t, false, &[]).mask_colors,
        vec![t.color_sub]
    );
    let auto = auto_options(&t);
    assert_eq!(auto.island_max_width, 3);
}

#[test]
fn skewer_fills_every_frame_in_one_undo() {
    let mut s = session(vec![
        outlined(4, 4, 8),
        outlined(5, 4, 8),
        outlined(6, 4, 8),
    ]);
    let view = skewer_click(&mut s, 7.0, 7.0, false, &Labels::default()).unwrap();
    assert!(!view.pending);
    assert_eq!(
        view.message.as_deref(),
        Some("串刺し塗り：3 コマを塗りました。")
    );
    for frame in 0..3 {
        assert_eq!(pixel(&s, frame, 8, 8), BLUE, "{frame}コマ目");
    }
    assert_eq!(s.history().undo, vec!["串刺し塗り".to_string()]);
    assert!(s.tools.paint.skewer.is_none());
}

#[test]
fn skewer_on_open_area_reports_and_keys_do_nothing_without_session() {
    let mut s = session(vec![outlined(4, 4, 8)]);
    s.tools.settings.bucket_require_closed = true;
    let view = skewer_click(&mut s, 1.0, 1.0, false, &Labels::default()).unwrap();
    assert_eq!(
        view.message.as_deref(),
        Some("領域がキャンバス端まで開いているため、塗りを開始しませんでした。")
    );
    let view = skewer_key(&mut s, "skip", &Labels::default()).unwrap();
    assert!(!view.pending && view.message.is_none());
    assert!(skewer_guide(&s).iter().all(|b| *b == 0));
}

#[test]
fn replace_color_changes_all_frames_and_asks_for_line_colors() {
    let mut s = session(vec![outlined(4, 4, 8), outlined(5, 4, 8)]);
    // 黒の線は尋ねる。
    let (view, _) = replace_color(&mut s, 4.0, 4.0, false).unwrap();
    assert_eq!(view.confirm.as_deref(), Some("000000"));
    assert!(s.history().undo.is_empty());
    let (view, feedback) = replace_color(&mut s, 4.0, 4.0, true).unwrap();
    assert!(view.confirm.is_none());
    assert_eq!(pixel(&s, 0, 4, 4), BLUE);
    assert_eq!(pixel(&s, 1, 5, 4), BLUE);
    assert_eq!(s.history().undo, vec!["色置換".to_string()]);
    assert!(feedback
        .message
        .unwrap()
        .ends_with("ピクセルへ色置換を適用しました。"));
    // 今の色と同じ色。
    let (_, feedback) = replace_color(&mut s, 4.0, 4.0, true).unwrap();
    assert_eq!(
        feedback.message.as_deref(),
        Some("色置換：クリックした色は今の色と同じです。")
    );
}

#[test]
fn dust_uses_the_mode_label_and_qt_messages() {
    let mut image = WHITE_PX.repeat(W * H);
    image[(3 * W + 3) * 4..][..4].copy_from_slice(&BLACK_PX);
    let mut s = session(vec![RgbaImage::from_rgba(W, H, &image).unwrap()]);
    s.tools.settings.dust_mode = "despeckle".into();
    let feedback = dust_apply(&mut s, None).unwrap();
    assert_eq!(pixel(&s, 0, 3, 3), WHITE_PX);
    assert_eq!(
        feedback.message.as_deref(),
        Some("選択レイヤーの1コマで1ピクセルへゴミ取りを適用しました。")
    );
    assert_eq!(s.history().undo, vec!["ゴミ取り".to_string()]);
    let feedback = dust_apply(&mut s, None).unwrap();
    assert_eq!(
        feedback.message.as_deref(),
        Some("指定サイズ以内のゴミ取り対象は見つかりませんでした。")
    );
    s.tools.settings.dust_selected_only = true;
    let feedback = dust_apply(&mut s, None).unwrap();
    assert_eq!(
        feedback.message.as_deref(),
        Some("使用色パネルで対象色を選択してください。")
    );
}

#[test]
fn line_tidy_without_line_colours_says_so() {
    let mut s = session(vec![RgbaImage::blank(W, H).unwrap()]);
    s.tools.settings.dust_mode = "line_tidy".into();
    let feedback = dust_apply(&mut s, None).unwrap();
    assert_eq!(
        feedback.message.as_deref(),
        Some("線整え：整える線の色がありません。使用色パネルで線の色を選んでください。")
    );
}

#[test]
fn auto_dialog_lists_cells_and_needs_two() {
    let mut s = session(vec![outlined(4, 4, 8), outlined(5, 4, 8)]);
    let dialog = auto_paint_dialog(&mut s, &Labels::default())
        .unwrap()
        .unwrap();
    assert_eq!(dialog.layer_name, "A");
    assert_eq!(dialog.cells.len(), 2);
    assert_eq!(dialog.cells[1].label, "A2 · 2コマ目");
    assert_eq!(dialog.current, 0);
    let mut one = session(vec![outlined(4, 4, 8)]);
    assert_eq!(
        auto_paint_dialog(&mut one, &Labels::default())
            .unwrap()
            .err()
            .as_deref(),
        Some("塗れるセルが2つ以上ありません。")
    );
}

#[test]
fn auto_paint_writes_in_one_undo_from_the_reference() {
    // 見本（1コマ目）は枠の中を青で塗ってある。2コマ目は少しずれた枠だけ。
    let transparent = |pixels: &[u8]| {
        let mut px = pixels.to_vec();
        for p in px.chunks_exact_mut(4) {
            if p == WHITE_PX {
                p.fill(0);
            }
        }
        RgbaImage::from_rgba(W, H, &px).unwrap()
    };
    let mut painted = outlined(4, 4, 8).rgba().to_vec();
    for y in 5..11 {
        for x in 5..11 {
            painted[(y * W + x) * 4..][..4].copy_from_slice(&BLUE);
        }
    }
    let mut s = session(vec![
        transparent(&painted),
        transparent(outlined(5, 4, 8).rgba()),
    ]);
    let doc = document(&s).unwrap().clone();
    let outcome = auto_plan::paint_layer(
        &doc,
        0,
        &[0],
        &auto_options(&s.tools.settings),
        &mut Progress::default(),
    )
    .unwrap();
    let LayerOutcome::Done { writes, .. } = outcome else {
        panic!("塗れるはず");
    };
    assert!(!writes.is_empty());
    s.edit_with(Some("自動で塗る"), |doc, _| {
        auto_plan::apply_writes(doc, &writes).map_err(err)?;
        Ok(((), true))
    })
    .unwrap();
    assert_eq!(pixel(&s, 1, 8, 8), BLUE);
    assert_eq!(s.history().undo, vec!["自動で塗る".to_string()]);
}

#[test]
fn findings_select_their_place() {
    let mut s = session(vec![outlined(4, 4, 8), outlined(5, 4, 8)]);
    s.tools.paint.findings = vec![(
        1,
        0,
        Finding {
            kind: FindingKind::Extra,
            rgb: Some([0, 0, 0]),
            pixels: 1,
            bounds: Some((5, 4, 13, 12)),
            expected: None,
            mask: None,
        },
    )];
    show_finding(&mut s, 0).unwrap();
    assert_eq!(current_index(document(&s).unwrap()), (1, 0));
    let selection = s.tools.selection.selection().expect("選択される");
    let mask = selection.mask(W, H, 0, 0);
    assert_eq!(mask.bits()[4 * W + 5], 1, "黒の線を選ぶ");
    assert_eq!(mask.bits()[6 * W + 7], 0, "中の白は選ばない");
}

#[test]
fn labels_thousands_and_descriptions() {
    assert_eq!(thousands(1234567), "1,234,567");
    assert_eq!(thousands(12), "12");
    assert_eq!(Labels::default().position(2), "3コマ目");
    let seconds = Labels {
        fps: 24,
        seconds_frames: true,
    };
    assert_eq!(seconds.position(24), "1+1");
    assert_eq!(color_label(None), "透明");
    assert_eq!(dust_label("fill_holes"), "塗り抜け");
}
