//! 合成の単体テスト。Qt との画素の突き合わせは tests/test_core_compose_compare.py。

use serde_json::{json, Value};

use super::*;
use crate::document::model::Document;
use crate::image::RgbaImage;

fn settings(name: &str, opacity: f64) -> Value {
    json!({
        "name": name, "visible": true, "opacity": opacity, "alpha_locked": false,
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

fn solid(width: usize, height: usize, pixel: [u8; 4]) -> RgbaImage {
    RgbaImage::from_rgba(width, height, &pixel.repeat(width * height)).unwrap()
}

/// 2列・3コマ。下の列は番号1を3コマ保持、上の列は 1, 未使用, 2。
fn document(lower: [u8; 4], upper: [u8; 4], upper_opacity: f64) -> Document {
    let images = vec![
        solid(3, 2, lower),
        solid(3, 2, upper),
        solid(3, 2, [0, 0, 255, 255]),
    ];
    let cell = |image: usize| json!({"image": image, "initial_image": null, "cell_name": null});
    let value = json!({
        "width": 3, "height": 2, "current_frame": 0, "active_layer": 1,
        "tracks": [
            {"settings": settings("A", 1.0), "hints": [], "cells": [[1, cell(0)]]},
            {"settings": settings("B", upper_opacity), "hints": [],
             "cells": [[1, cell(1)], [2, cell(2)]]},
        ],
        "frames": [
            {"duration": 1, "positions": [position(Some(1)), position(Some(1))]},
            {"duration": 1, "positions": [position(Some(1)), position(None)]},
            {"duration": 1, "positions": [position(Some(1)), position(Some(2))]},
        ],
    });
    Document::from_graph(&value, &images).unwrap()
}

#[test]
fn frame_layers_follow_held_and_empty_cells() {
    let doc = document([255, 0, 0, 255], [0, 255, 0, 128], 1.0);
    let layers = frame_layers(&doc, 1).unwrap();
    assert_eq!(layers[0].unwrap().key_frame, 0);
    assert!(layers[1].is_none());
    let layers = frame_layers(&doc, 2).unwrap();
    assert_eq!(layers[1].unwrap().key_frame, 2);
    // 範囲外のコマは最後のコマへ寄せる。
    assert_eq!(frame_layers(&doc, 99).unwrap()[1].unwrap().key_frame, 2);
}

#[test]
fn composite_matches_qt_source_over() {
    let doc = document([255, 0, 0, 255], [0, 255, 0, 128], 0.5);
    let image = composite(&doc, 0, &DisplayOptions::default(), Background::White).unwrap();
    // 緑 (0,128,0,128) に定数アルファ 127 → (0,64,0,64)、赤の上へ重ねる。
    let expected = {
        let mut dst = [255, 0, 0, 255];
        pixel::blend(&mut dst, [0, 128, 0, 128], 127);
        dst
    };
    assert_eq!(&image.pixels()[..4], &expected);
    assert_eq!(expected, [191, 64, 0, 255]);
    let empty = composite(&doc, 1, &DisplayOptions::default(), Background::Transparent).unwrap();
    assert_eq!(&empty.pixels()[..4], &[255, 0, 0, 255]);
}

#[test]
fn composite_rect_is_a_crop_of_the_full_composite() {
    let doc = document([255, 0, 0, 200], [0, 255, 0, 128], 0.7);
    let options = DisplayOptions {
        visible_colors: Some(vec![(0, 255, 0)]),
        ..DisplayOptions::default()
    };
    let full = composite(&doc, 0, &options, Background::White).unwrap();
    let rect = Rect {
        x: 1,
        y: 1,
        width: 2,
        height: 1,
    };
    let part = composite_rect(&doc, 0, &options, Background::White, rect).unwrap();
    assert_eq!(part, full.crop(rect).unwrap());
    let outside = Rect {
        x: 2,
        y: 0,
        width: 2,
        height: 1,
    };
    assert!(composite_rect(&doc, 0, &options, Background::White, outside).is_err());
}

#[test]
fn palette_filter_applies_only_to_the_active_layer() {
    let doc = document([255, 0, 0, 255], [0, 255, 0, 255], 1.0);
    let options = DisplayOptions {
        visible_colors: Some(vec![(255, 0, 0)]),
        ..DisplayOptions::default()
    };
    // アクティブは上の列。緑は見えず、下の赤が見える。
    let image = composite(&doc, 0, &options, Background::Transparent).unwrap();
    assert_eq!(&image.pixels()[..4], &[255, 0, 0, 255]);
    let lower = layer_image(&doc, 0, 0, &options).unwrap().unwrap();
    assert_eq!(&lower.image.pixels()[..4], &[255, 0, 0, 255]);
    let remap = DisplayOptions {
        color_remap: vec![((0, 255, 0), (1, 2, 3))],
        ..DisplayOptions::default()
    };
    let image = composite(&doc, 0, &remap, Background::Transparent).unwrap();
    assert_eq!(&image.pixels()[..4], &[1, 2, 3, 255]);
}

#[test]
fn silhouette_blackens_all_but_paper() {
    let doc = document([255, 0, 0, 255], [0, 255, 0, 128], 1.0);
    let options = DisplayOptions {
        silhouette: true,
        ..DisplayOptions::default()
    };
    let upper = layer_image(&doc, 0, 1, &options).unwrap().unwrap();
    assert_eq!(&upper.image.pixels()[..4], &[0, 0, 0, 128]);
}

#[test]
fn view_preview_changes_only_the_view() {
    let doc = document([100, 0, 0, 255], [0, 0, 0, 0], 1.0);
    let view = ViewOptions {
        preview: ViewPreview {
            exposure: 1.0,
            ..ViewPreview::default()
        },
        ..ViewOptions::default()
    };
    let mut mapping = MapColorMapping::new();
    let options = DisplayOptions::default();
    let shown = view_composite(
        &doc,
        0,
        &options,
        &view,
        &mut mapping,
        Background::White,
        None,
    )
    .unwrap();
    assert_eq!(&shown.pixels()[..4], &[200, 0, 0, 255]);
    let exported = composite(&doc, 0, &options, Background::White).unwrap();
    assert_eq!(&exported.pixels()[..4], &[100, 0, 0, 255]);
}

#[test]
fn display_frame_shrinks_each_layer() {
    let doc = document([255, 0, 0, 255], [0, 255, 0, 255], 1.0);
    let mut mapping = MapColorMapping::new();
    let target = DisplayTarget {
        width: 1,
        height: 1,
        mode: ShrinkMode::KeepLines,
        flip_horizontal: true,
        flip_vertical: false,
    };
    let image = display_frame(
        &doc,
        0,
        &DisplayOptions::default(),
        &ViewOptions::default(),
        &mut mapping,
        Background::White,
        target,
        None,
    )
    .unwrap();
    assert_eq!((image.width(), image.height()), (1, 1));
    assert_eq!(image.pixels(), &[0, 255, 0, 255]);
}

#[test]
fn onion_skips_held_frames_and_fades_by_distance() {
    let doc = document([255, 0, 0, 255], [0, 255, 0, 255], 1.0);
    // 上の列（アクティブ）だけ。コマ1は未使用なので飛ばす。
    assert_eq!(
        onion_frame_indices(&doc, 0, 1, false, 1, 3).unwrap(),
        vec![2]
    );
    // 全レイヤーなら、コマ1は下の列だけが見える別の絵。
    assert_eq!(
        onion_frame_indices(&doc, 0, 1, true, 1, 3).unwrap(),
        vec![1, 2]
    );
    let mut settings = OnionSettings::default();
    settings.next.count = 2;
    settings.next.levels = vec![100.0];
    let draws = onion_draws(&doc, 0, &DisplayOptions::default(), &settings).unwrap();
    assert_eq!(draws.len(), 2);
    // 遠い絵から描く。2枚目は 1 - (1/2)*0.55 の濃さ。
    assert_eq!((draws[0].frame, draws[0].distance), (2, 2));
    assert!((draws[0].opacity - 0.22 * 0.725).abs() < 1e-12);
    assert_eq!((draws[1].frame, draws[1].opacity), (1, 0.22));
}

#[test]
fn tinted_onion_colors_by_luminance() {
    let doc = document([255, 255, 255, 255], [0, 0, 0, 0], 1.0);
    let onion = tinted_onion_image(
        &doc,
        0,
        &DisplayOptions::default(),
        (10, 20, 30),
        true,
        None,
        true,
        None,
    )
    .unwrap();
    // 白は輝度1で白のまま。
    assert_eq!(&onion.pixels()[..4], &[255, 255, 255, 255]);
    let small = tinted_onion_image(
        &doc,
        0,
        &DisplayOptions::default(),
        (10, 20, 30),
        false,
        Some(&[]),
        false,
        Some((2, 1)),
    )
    .unwrap();
    assert_eq!(small.pixels(), &[0; 8]);
}
