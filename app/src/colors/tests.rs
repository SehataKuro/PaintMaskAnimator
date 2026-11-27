use super::*;
use crate::session::{Point, StrokeTool};
use std::path::Path;

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../tests/fixtures/projects/v2")
        .join(name)
}

/// 見本を開いて使用色を数えた Session。
fn opened(name: &str) -> Session {
    let mut s = Session::empty(64, 64);
    s.open(&fixture(name)).unwrap();
    sync(&mut s);
    s
}

/// 背景以外で2色以上ある見本のレイヤーを選ぶ。
fn with_two_colors() -> (Session, Rgb, Rgb) {
    for name in [
        "basic-bank-sheet.pman",
        "paper-draft.pman",
        "tween-initial-display.pman",
        "pending-compat.pman",
    ] {
        let mut s = opened(name);
        let layers = s.project.as_ref().unwrap().document.track_order().len();
        for layer in 0..layers {
            let p = s.project.as_mut().unwrap();
            let (frame, _) = crate::session::current_index(&p.document);
            p.document.set_selection_index(frame, layer).unwrap();
            s.touch_structure();
            sync(&mut s);
            let colors = s.colors.palette.ordered_non_background();
            if colors.len() >= 2 {
                return (s, colors[0], colors[1]);
            }
        }
    }
    panic!("2色以上ある見本のレイヤーが無い");
}

fn undo_labels(s: &Session) -> Vec<String> {
    s.history().undo
}

#[test]
fn opening_scans_the_selected_layer() {
    let (s, a, b) = with_two_colors();
    let view = s.palette_view();
    assert!(view.loaded);
    assert!(matches!(
        &view.rows[0],
        RowView::Color {
            background: true,
            ..
        }
    ));
    assert!(s.colors.palette.contains(a) && s.colors.palette.contains(b));
    assert_eq!(
        view.count_text,
        format!("{}色", s.colors.palette.colors().len())
    );
}

#[test]
fn reorder_is_recorded_and_undone_with_the_palette_state() {
    let (mut s, a, b) = with_two_colors();
    let before = s.colors.palette.colors().to_vec();
    let out = s
        .palette_op(PaletteOp::Drop {
            dragged: rgb3(b),
            target: rgb3(a),
            ratio: 0.1,
        })
        .unwrap();
    assert!(out.changed);
    assert_eq!(undo_labels(&s).last().unwrap(), "使用色の並べ替え");
    assert_ne!(s.colors.palette.colors(), &before[..]);
    s.undo().unwrap();
    sync(&mut s);
    assert_eq!(s.colors.palette.colors(), &before[..]);
    s.redo().unwrap();
    sync(&mut s);
    assert_ne!(s.colors.palette.colors(), &before[..]);
}

#[test]
fn visibility_drives_the_canvas_display_options() {
    let (mut s, a, _) = with_two_colors();
    assert_eq!(s.colors.display, DisplayOptions::default());
    s.palette_op(PaletteOp::SweepBegin {
        column: ColumnArg::Visibility,
        rgb: rgb3(a),
        checked: false,
    })
    .unwrap();
    s.palette_op(PaletteOp::SweepEnd).unwrap();
    assert_eq!(undo_labels(&s).last().unwrap(), "表示の一括切り替え");
    let synced = sync(&mut s);
    assert!(synced.display);
    let visible = s.colors.display.visible_colors.clone().unwrap();
    assert!(!visible.contains(&a));
    s.palette_op(PaletteOp::ShowAll).unwrap();
    assert_eq!(undo_labels(&s).last().unwrap(), "全表示");
    sync(&mut s);
    assert_eq!(s.colors.display.visible_colors, None);
}

#[test]
fn replacement_preview_remaps_only_in_replace_mode() {
    let (mut s, a, _) = with_two_colors();
    s.palette_op(PaletteOp::SetReplacement {
        targets: vec![rgb3(a)],
        color: [1, 2, 3],
    })
    .unwrap();
    assert_eq!(undo_labels(&s).last().unwrap(), "置換色を設定");
    sync(&mut s);
    assert!(s.colors.display.color_remap.is_empty());
    s.palette_op(PaletteOp::ReplaceMode { on: true }).unwrap();
    sync(&mut s);
    assert_eq!(s.colors.display.color_remap, vec![(a, (1, 2, 3))]);
    s.palette_op(PaletteOp::ReplacementPreview { on: false })
        .unwrap();
    sync(&mut s);
    assert!(s.colors.display.color_remap.is_empty());
}

#[test]
fn merge_into_focus_rewrites_pixels_and_undo_restores_rows() {
    let (mut s, a, b) = with_two_colors();
    let order = s.colors.palette.colors().to_vec();
    s.palette_op(PaletteOp::Click {
        rgb: rgb3(b),
        shift: false,
        ctrl: true,
    })
    .unwrap();
    s.palette_op(PaletteOp::Click {
        rgb: rgb3(a),
        shift: false,
        ctrl: false,
    })
    .unwrap();
    assert!(s.palette_view().can_merge);
    let out = s.palette_op(PaletteOp::Merge).unwrap();
    assert!(out.changed, "{:?}", out.message);
    assert!(out
        .message
        .unwrap()
        .ends_with("ピクセルへ色統合を適用しました。"));
    assert_eq!(undo_labels(&s).last().unwrap(), "色統合");
    sync(&mut s);
    assert!(!s.colors.palette.contains(b));
    assert_eq!(s.colors.palette.focus(), Some(a));
    s.undo().unwrap();
    sync(&mut s);
    assert_eq!(s.colors.palette.colors(), &order[..]);
}

#[test]
fn delete_turns_pixels_transparent() {
    let (mut s, a, _) = with_two_colors();
    let out = s.palette_op(PaletteOp::Delete { rgb: rgb3(a) }).unwrap();
    assert!(out.changed);
    assert_eq!(out.message.unwrap(), "1色を #FFFFFF へ統合しました。");
    assert_eq!(undo_labels(&s).last().unwrap(), "色削除");
    sync(&mut s);
    assert!(!s.colors.palette.contains(a));
}

/// 選択レイヤーの今のコマの絵の1画素。
fn layer_pixel(s: &Session, x: usize, y: usize) -> [u8; 4] {
    let doc = &s.project.as_ref().unwrap().document;
    let (frame, layer) = crate::session::current_index(doc);
    let layers = pma_core::compose::frame_layers(doc, frame).unwrap();
    let image = layers[layer].as_ref().unwrap().image.unwrap();
    let i = (y * image.width() + x) * 4;
    image.rgba()[i..i + 4].try_into().unwrap()
}

#[test]
fn locked_color_is_protected_from_the_brush() {
    let mut s = opened("basic-bank-sheet.pman");
    let (w, h) = (s.display.width(), s.display.height());
    let (cx, cy) = (w / 2, h / 2);
    let tool = |color| StrokeTool {
        size: 9.0,
        color,
        eraser: false,
        stabilizer: 0,
        zoom: 1.0,
    };
    let pt = |x: usize, y: usize| Point {
        x: x as f64 + 0.5,
        y: y as f64 + 0.5,
        pressure: 1.0,
        pen: false,
        time: 0.0,
    };
    // 赤い点を描き、使用色に赤が出る。
    s.stroke_begin(&tool([255, 0, 0]), pt(cx, cy)).unwrap();
    s.stroke_end(None).unwrap();
    sync(&mut s);
    assert!(s.colors.palette.contains((255, 0, 0)));
    assert_eq!(layer_pixel(&s, cx, cy), [255, 0, 0, 255]);
    // 赤に鍵をかけると、同じところへ青を描いても赤のまま。
    s.palette_op(PaletteOp::SweepBegin {
        column: ColumnArg::Lock,
        rgb: [255, 0, 0],
        checked: true,
    })
    .unwrap();
    s.palette_op(PaletteOp::SweepEnd).unwrap();
    assert_eq!(undo_labels(&s).last().unwrap(), "マスクの一括切り替え");
    s.stroke_begin(&tool([0, 0, 255]), pt(cx, cy)).unwrap();
    s.stroke_end(None).unwrap();
    assert_eq!(layer_pixel(&s, cx, cy), [255, 0, 0, 255]);
    // 鍵を外せば描ける。
    s.palette_op(PaletteOp::UnlockAll).unwrap();
    s.stroke_begin(&tool([0, 0, 255]), pt(cx, cy)).unwrap();
    s.stroke_end(None).unwrap();
    assert_eq!(layer_pixel(&s, cx, cy), [0, 0, 255, 255]);
}

#[test]
fn metadata_round_trips_drawing_colors_categories_and_chart() {
    let (mut s, a, b) = with_two_colors();
    s.colors.drawing_op(DrawingOp::Set {
        which: DrawMode::Sub,
        color: [1, 2, 3],
    });
    s.colors.drawing_op(DrawingOp::Mode {
        mode: DrawMode::Transparent,
    });
    assert!(s.colors.current().eraser);
    s.palette_op(PaletteOp::CreateCategory { name: "線".into() })
        .unwrap();
    s.palette_op(PaletteOp::DropToCategory {
        dragged: rgb3(a),
        name: "線".into(),
    })
    .unwrap();
    s.palette_op(PaletteOp::Drop {
        dragged: rgb3(b),
        target: rgb3(a),
        ratio: 0.5,
    })
    .unwrap();
    s.colors.chart_op(ChartOp::Capture).unwrap();
    let mut metadata = s.project.as_ref().unwrap().metadata.clone();
    s.colors.store(&mut metadata);
    let mut again = ColorUi::from_metadata(&metadata);
    assert_eq!(again.sub, (1, 2, 3));
    assert_eq!(again.mode, DrawMode::Transparent);
    assert_eq!(again.chart, s.colors.chart);
    again
        .palette
        .set_colors(&s.colors.palette.ordered_non_background(), false);
    again
        .palette
        .restore_categories(again.pending_categories.as_ref());
    assert_eq!(again.palette.category_of(a), Some("線"));
}

#[test]
fn chart_view_lists_tiles_and_tags() {
    let (mut s, a, b) = with_two_colors();
    assert!(s.colors.chart_op(ChartOp::Capture).is_err());
    s.palette_op(PaletteOp::Drop {
        dragged: rgb3(b),
        target: rgb3(a),
        ratio: 0.5,
    })
    .unwrap();
    assert_eq!(undo_labels(&s).last().unwrap(), "親子付け");
    let out = s.colors.chart_op(ChartOp::Capture).unwrap();
    assert_eq!(
        out.message.unwrap(),
        "現在の親子付けをカラーチャートへ登録しました（新規 1組）。"
    );
    let view = s.colors.chart_view();
    assert_eq!(view.summary, "1グループ・1子色");
    assert_eq!(view.tiles[0].parent.as_deref(), Some(hex(a).as_str()));
    assert_eq!(view.tags[0].0, "Base");
}

#[test]
fn thousands_separator_matches_python() {
    assert_eq!(thousands(0), "0");
    assert_eq!(thousands(999), "999");
    assert_eq!(thousands(1000), "1,000");
    assert_eq!(thousands(1234567), "1,234,567");
}
