//! バケツ・串刺し塗りの単体テスト。Python の画面との画素の突き合わせは
//! tests/test_core_bucket_tools_compare.py。

use serde_json::{json, Value};

use super::bucket::*;
use super::skewer::*;
use crate::document::model::Document;
use crate::image::RgbaImage;

const W: usize = 24;
const H: usize = 16;
const WHITE: [u8; 4] = [255, 255, 255, 255];
const BLACK: [u8; 4] = [0, 0, 0, 255];
const BLUE: Rgb = [0x33, 0x55, 0xff];

fn settings(name: &str) -> Value {
    json!({
        "name": name, "visible": true, "opacity": 1.0, "alpha_locked": false,
        "is_paper": false, "is_draft": false, "color_filter_enabled": false,
        "color_filter_rgb": null,
    })
}

fn position(number: Option<i64>) -> Value {
    json!({
        "has_content": number.is_some(), "is_blank_key": false, "sequence_number": number,
        "sequence_only": false, "blank_start": false, "tween": null, "tween_member": null,
        "local": null, "content_origin": null, "v1_position": null, "dense_import": null,
        "length_imported": null, "resolve_source": null, "legacy_missing": false,
    })
}

/// 白地に黒枠を描いた絵。枠は (x0, y0) から一辺 size。
fn outlined(x0: usize, y0: usize, size: usize) -> RgbaImage {
    let mut pixels = WHITE.repeat(W * H);
    let mut put = |x: usize, y: usize| pixels[(y * W + x) * 4..][..4].copy_from_slice(&BLACK);
    for i in 0..size {
        put(x0 + i, y0);
        put(x0 + i, y0 + size - 1);
        put(x0, y0 + i);
        put(x0 + size - 1, y0 + i);
    }
    RgbaImage::from_rgba(W, H, &pixels).unwrap()
}

/// 1列で、コマごとに番号 1, 2, ... の絵を持つ文書。`numbers` の None は保持（前の番号）。
fn document(images: Vec<RgbaImage>, numbers: &[i64]) -> Document {
    let cells: Vec<Value> = (0..images.len())
        .map(|i| json!([i + 1, {"image": i, "initial_image": null, "cell_name": null}]))
        .collect();
    let frames: Vec<Value> = numbers
        .iter()
        .map(|n| json!({"duration": 1, "positions": [position(Some(*n))]}))
        .collect();
    let value = json!({
        "width": W, "height": H, "current_frame": 0, "active_layer": 0,
        "tracks": [{"settings": settings("A"), "hints": [], "cells": cells}],
        "frames": frames,
    });
    Document::from_graph(&value, &images).unwrap()
}

fn pixel(document: &Document, frame: usize, x: usize, y: usize) -> [u8; 4] {
    let image = position_image(document, frame, 0).unwrap();
    let i = (y * W + x) * 4;
    image.rgba()[i..i + 4].try_into().unwrap()
}

fn paint(color: Rgb) -> PaintSettings {
    PaintSettings {
        color,
        opacity: 1.0,
    }
}

#[test]
fn fill_at_paints_inside_the_outline_only() {
    let mut doc = document(vec![outlined(4, 4, 8)], &[1]);
    let outcome = fill_at(
        &mut doc,
        0,
        0,
        &BucketSettings::default(),
        paint(BLUE),
        None,
        (6.7, 6.2),
    )
    .unwrap();
    let FillOutcome::Filled(edit) = outcome else {
        panic!("塗れるはず");
    };
    assert_eq!(edit.written_colors, vec![BLUE]);
    assert_eq!(edit.rect.x, 5);
    assert_eq!(edit.rect.width, 6);
    assert_eq!(pixel(&doc, 0, 6, 6), [0x33, 0x55, 0xff, 255]);
    assert_eq!(pixel(&doc, 0, 4, 4), BLACK, "線は塗らない");
    assert_eq!(pixel(&doc, 0, 1, 1), WHITE, "外は塗らない");
    assert_eq!(edit.before.len(), 6 * 6 * 4);
}

#[test]
fn white_bucket_erases_and_reports_no_color() {
    let mut doc = document(vec![outlined(4, 4, 8)], &[1]);
    let FillOutcome::Filled(edit) = fill_at(
        &mut doc,
        0,
        0,
        &BucketSettings::default(),
        paint([255; 3]),
        None,
        (6.0, 6.0),
    )
    .unwrap() else {
        panic!("塗れるはず");
    };
    assert!(edit.written_colors.is_empty());
    assert_eq!(pixel(&doc, 0, 6, 6), [0, 0, 0, 0]);
}

#[test]
fn failures_report_the_python_reasons() {
    let mut doc = document(vec![outlined(4, 4, 8)], &[1]);
    let closed = BucketSettings {
        require_closed: true,
        ..BucketSettings::default()
    };
    let FillOutcome::Skipped(result) =
        fill_at(&mut doc, 0, 0, &closed, paint(BLUE), None, (1.0, 1.0)).unwrap()
    else {
        panic!("開いた領域は塗らない");
    };
    assert_eq!(result.reason, Reason::OpenRegion);
    assert!(result.region.is_some(), "どこまで開いていたかは返す");
    let FillOutcome::Skipped(result) = fill_at(
        &mut doc,
        0,
        0,
        &BucketSettings::default(),
        paint(BLUE),
        None,
        (-1.5, 1.0),
    )
    .unwrap() else {
        panic!("外は塗らない");
    };
    assert_eq!(result.reason, Reason::OutOfBounds);
    // int(-0.5) は 0。
    assert!(matches!(
        fill_at(
            &mut doc,
            0,
            0,
            &BucketSettings::default(),
            paint(BLUE),
            None,
            (-0.5, 1.0)
        )
        .unwrap(),
        FillOutcome::Filled(_)
    ));
    let mut selection = vec![0u8; W * H];
    selection[6 * W + 6] = 1;
    let FillOutcome::Skipped(result) = fill_at(
        &mut doc,
        0,
        0,
        &BucketSettings::default(),
        paint(BLUE),
        Some(&selection),
        (7.0, 7.0),
    )
    .unwrap() else {
        panic!("選択範囲の外");
    };
    assert_eq!(result.reason, Reason::OutsideSelection);
    assert!(result.reason.is_reported());
    let black = BucketSettings {
        target: TargetKind::Black,
        ..BucketSettings::default()
    };
    let FillOutcome::Skipped(result) =
        fill_at(&mut doc, 0, 0, &black, paint(BLUE), None, (7.0, 7.0)).unwrap()
    else {
        panic!("対象色でない");
    };
    assert_eq!(result.reason, Reason::NotTarget);
}

#[test]
fn partial_opacity_mixes_with_white_for_transparent_pixels() {
    let mut patch = vec![0, 0, 0, 0, 10, 20, 30, 255, 1, 2, 3, 255];
    let colors = paint_patch(
        &mut patch,
        &[1, 1, 0],
        PaintSettings {
            color: [0, 0, 0],
            opacity: 0.5,
        },
    );
    // 透明は白とみなす：255 * 0.5 = 127.5 → 偶数丸めで 128。
    assert_eq!(&patch[..4], &[128, 128, 128, 255]);
    assert_eq!(&patch[4..8], &[5, 10, 15, 255]);
    assert_eq!(&patch[8..], &[1, 2, 3, 255], "マスクの外は変えない");
    assert_eq!(colors, vec![[5, 10, 15], [128, 128, 128]]);
    // 混色の結果が白なら 254。
    let mut white = vec![255, 255, 255, 255];
    let colors = paint_patch(
        &mut white,
        &[1],
        PaintSettings {
            color: [255, 255, 254],
            opacity: 0.01,
        },
    );
    assert_eq!(white, vec![254, 254, 254, 255]);
    assert_eq!(colors, vec![[254; 3]]);
}

#[test]
fn drag_fills_boxes_along_the_path_but_not_the_background() {
    let mut pixels = outlined(2, 2, 6).rgba().to_vec();
    let second = outlined(12, 2, 6);
    for y in 0..H {
        for x in 12..18 {
            let i = (y * W + x) * 4;
            pixels[i..i + 4].copy_from_slice(&second.rgba()[i..i + 4]);
        }
    }
    let image = RgbaImage::from_rgba(W, H, &pixels).unwrap();
    let mut doc = document(vec![image], &[1]);
    let (mut drag, step) = BucketDrag::begin(
        &mut doc,
        0,
        0,
        BucketSettings::default(),
        paint(BLUE),
        None,
        (4.0, 4.0),
    )
    .unwrap()
    .unwrap();
    assert_eq!(step.edits.len(), 1);
    // 外の背景（白）と2つ目の枠の中を通る。背景は大きいので塗らない。
    let step = drag.extend(&mut doc, (14.0, 4.0)).unwrap();
    assert_eq!(step.edits.len(), 1);
    assert_eq!(pixel(&doc, 0, 14, 4)[..3], BLUE);
    assert_eq!(pixel(&doc, 0, 10, 4), WHITE, "外の背景は塗らない");
    let undo = drag.finish(&doc).unwrap().unwrap();
    assert_eq!((undo.rect.x, undo.rect.y), (3, 3));
    assert_eq!(undo.rect.width, 14);
    assert_eq!(&undo.before[..4], &WHITE);
}

#[test]
fn drag_outside_the_canvas_does_nothing() {
    let mut doc = document(vec![outlined(4, 4, 8)], &[1]);
    assert!(BucketDrag::begin(
        &mut doc,
        0,
        0,
        BucketSettings::default(),
        paint(BLUE),
        None,
        (-3.0, 1.0)
    )
    .unwrap()
    .is_none());
}

#[test]
fn canvas_reference_skips_paper_and_hidden_layers() {
    let mut doc = document(vec![outlined(4, 4, 8)], &[1]);
    let blank = vec![0u8; W * H * 4];
    let pixels = reference_pixels(&doc, 0, 0, ReferenceSource::Canvas, &blank, W, H).unwrap();
    assert_eq!(&pixels[(4 * W + 4) * 4..][..4], &BLACK);
    let layer = reference_pixels(&doc, 0, 0, ReferenceSource::Layer, &blank, W, H).unwrap();
    assert_eq!(&layer[..], &blank[..]);
    // 初期画像がなければ今の絵。
    let initial = reference_pixels(&doc, 0, 0, ReferenceSource::Initial, &blank, W, H).unwrap();
    assert_eq!(&initial[..], &blank[..]);
    let FillOutcome::Filled(_) = fill_at(
        &mut doc,
        0,
        0,
        &BucketSettings {
            reference: ReferenceSource::Canvas,
            ..BucketSettings::default()
        },
        paint(BLUE),
        None,
        (6.0, 6.0),
    )
    .unwrap() else {
        panic!("キャンバス全体を見ても塗れる");
    };
}

#[test]
fn replace_color_request_reads_the_clicked_color() {
    let doc = document(vec![outlined(4, 4, 8)], &[1]);
    let request = replace_color_request(&doc, 0, 0, BLUE, (4.0, 4.0))
        .unwrap()
        .unwrap();
    assert_eq!(request.source, [0, 0, 0]);
    assert!(request.is_line, "黒は線の色");
    let request = replace_color_request(&doc, 0, 0, BLUE, (1.0, 1.0))
        .unwrap()
        .unwrap();
    assert!(!request.is_line);
    assert_eq!(
        replace_color_request(&doc, 0, 0, [255; 3], (1.0, 1.0)).unwrap(),
        Err(ReplaceRefusal::SameColor)
    );
    assert!(is_primary_line_color([255, 0, 0]));
    assert!(is_primary_line_color([250, 250, 10]));
    assert!(!is_primary_line_color([255, 255, 255]));
    assert!(!is_primary_line_color([128, 0, 0]));
}

#[test]
fn skewer_fill_tracks_a_moving_box_and_writes_once() {
    let images = (0..3).map(|i| outlined(2 + 3 * i, 2, 10)).collect();
    let mut doc = document(images, &[1, 2, 3]);
    let SkewerStart::Started(mut session) = SkewerFill::start(
        &doc,
        0,
        0,
        BucketSettings::default(),
        paint(BLUE),
        None,
        (4.0, 6.0),
    )
    .unwrap() else {
        panic!("始まるはず");
    };
    assert_eq!(session.advance(&doc).unwrap(), SkewerStatus::Ready);
    let SkewerApplied::Done {
        writes,
        changed_cells,
        skipped,
        written_colors,
        ..
    } = session.apply(&mut doc).unwrap()
    else {
        panic!("塗れるはず");
    };
    assert_eq!((writes.len(), changed_cells, skipped), (3, 3, 0));
    assert_eq!(written_colors, vec![BLUE]);
    for frame in 0..3 {
        let inside = 2 + 3 * frame + 5;
        assert_eq!(pixel(&doc, frame, inside, 6)[..3], BLUE);
        assert_eq!(pixel(&doc, frame, 1, 1), WHITE, "背景にはみ出さない");
    }
}

#[test]
fn skewer_fill_waits_for_low_confidence_frames() {
    let images = vec![outlined(2, 2, 6), outlined(4, 1, 14)];
    let mut doc = document(images, &[1, 2]);
    let SkewerStart::Started(mut session) = SkewerFill::start(
        &doc,
        0,
        0,
        BucketSettings::default(),
        paint(BLUE),
        None,
        (4.0, 4.0),
    )
    .unwrap() else {
        panic!("始まるはず");
    };
    assert_eq!(
        session.advance(&doc).unwrap(),
        SkewerStatus::Pending((1, 0))
    );
    assert!(session.guide().is_some());
    // 別のコマを見ているなら、待っているセルを表示し直させる。
    assert_eq!(
        session.resume_click(&doc, 0, (8.0, 8.0)).unwrap(),
        SkewerEvent::ShowPending((1, 0))
    );
    assert_eq!(
        session.resume_click(&doc, 1, (8.0, 8.0)).unwrap(),
        SkewerEvent::Advanced(SkewerStatus::Ready)
    );
    let SkewerApplied::Done { changed_cells, .. } = session.apply(&mut doc).unwrap() else {
        panic!("塗れるはず");
    };
    assert_eq!(changed_cells, 2);
    assert_eq!(pixel(&doc, 1, 8, 8)[..3], BLUE);
}

#[test]
fn skewer_fill_skips_and_detects_edits_while_waiting() {
    let images = vec![outlined(2, 2, 6), outlined(4, 1, 14)];
    let mut doc = document(images, &[1, 2]);
    let start = || {
        let SkewerStart::Started(session) = SkewerFill::start(
            &doc,
            0,
            0,
            BucketSettings::default(),
            paint(BLUE),
            None,
            (4.0, 4.0),
        )
        .unwrap() else {
            panic!("始まるはず");
        };
        session
    };
    let mut session = start();
    session.advance(&doc).unwrap();
    assert_eq!(
        session.skip_frame(&doc).unwrap(),
        SkewerEvent::Advanced(SkewerStatus::Ready)
    );
    assert_eq!(session.skipped(), 1);
    let mut session = start();
    session.advance(&doc).unwrap();
    // 待っている間に塗る予定の絵を書き換えると取り消す。
    let mut edited = doc.clone();
    fill_at(
        &mut edited,
        0,
        0,
        &BucketSettings::default(),
        paint([1, 2, 3]),
        None,
        (1.0, 1.0),
    )
    .unwrap();
    assert_eq!(
        session.skip_frame(&edited).unwrap(),
        SkewerEvent::Invalidated
    );
    assert!(matches!(
        session.apply(&mut edited).unwrap(),
        SkewerApplied::Invalidated
    ));
    let _ = &mut doc;
}

#[test]
fn skewer_fill_does_not_paint_an_empty_cell() {
    let doc = document(vec![outlined(2, 2, 6)], &[1]);
    let cells = skewer_cells(&doc, 0).unwrap();
    assert_eq!(cells, vec![(0, 0)]);
    assert!(matches!(
        SkewerFill::start(
            &doc,
            0,
            0,
            BucketSettings::default(),
            paint(BLUE),
            None,
            (50.0, 1.0)
        )
        .unwrap(),
        SkewerStart::Ignored
    ));
}
