use super::chart::{normalize, Hit, Role};
use super::pixels::{remap_image, shifted_color, QtHsv};
use super::*;
use crate::image::RgbaImage;
use crate::project::json::{parse, Json};

const R: Rgb = (255, 0, 0);
const G: Rgb = (0, 255, 0);
const B: Rgb = (0, 0, 255);
const K: Rgb = (0, 0, 0);

fn palette(colors: &[Rgb]) -> UsedColors {
    let mut p = UsedColors::new();
    p.set_colors(colors, false);
    p
}

#[test]
fn set_colors_keeps_order_and_appends_new_colors() {
    let mut p = palette(&[R, G]);
    assert_eq!(p.colors(), &[BACKGROUND, R, G]);
    p.set_colors(&[B, G, R, BACKGROUND], false);
    assert_eq!(p.colors(), &[BACKGROUND, R, G, B]);
    p.set_colors(&[B, R], false);
    assert_eq!(p.colors(), &[BACKGROUND, R, B]);
    assert_eq!(p.count_text(), "3色");
    p.set_colors(&[R], true);
    assert_eq!(p.count_text(), "100色以上");
}

#[test]
fn removed_colors_drop_groups_visibility_and_replacements() {
    let mut p = palette(&[R, G, B]);
    p.drop_color(G, R, DropMode::Child).unwrap();
    p.set_visible(B, false).unwrap();
    p.set_replacements(&[G], K).unwrap();
    assert_eq!(p.parent_of(G), Some(R));
    p.set_colors(&[R, B], false);
    assert_eq!(p.parent_of(G), None);
    assert_eq!(p.replacement(G), None);
    assert!(!p.is_enabled(B));
}

#[test]
fn child_drop_groups_under_root_and_moves_after_parent() {
    let mut p = palette(&[R, G, B, K]);
    // K を R の子に、B を K の子に落とすと、B はルートの R へ直に付く。
    let rec = p.drop_color(K, R, DropMode::Child).unwrap();
    assert_eq!(rec.label, "親子付け");
    p.drop_color(B, K, DropMode::Child).unwrap();
    assert_eq!(p.parent_of(B), Some(R));
    // 子は今の並び順のまま親の直後へ並ぶ（Qt の `_reorder_children_under_parents`）。
    assert_eq!(p.colors(), &[BACKGROUND, R, K, B, G]);
    assert_eq!(p.group_line_color(R), Some(R));
    assert_eq!(p.group_line_color(B), Some(R));
    assert_eq!(p.group_line_color(G), None);
    // 循環になる落とし方は何もしない。
    assert!(p.drop_color(R, B, DropMode::Child).is_none());
}

#[test]
fn reorder_moves_and_detaches_dragged_child() {
    let mut p = palette(&[R, G, B, K]);
    let rec = p.drop_color(K, R, DropMode::Before).unwrap();
    assert_eq!(rec.label, "使用色の並べ替え");
    assert_eq!(p.colors(), &[BACKGROUND, K, R, G, B]);
    p.drop_color(G, R, DropMode::Child).unwrap();
    assert_eq!(p.colors(), &[BACKGROUND, K, R, G, B]);
    // 子を親の下から動かすと、その子の親子を外す。
    p.drop_color(G, B, DropMode::After).unwrap();
    assert_eq!(p.parent_of(G), None);
    assert_eq!(p.colors(), &[BACKGROUND, K, R, B, G]);
    // 背景より前には入れない。
    p.drop_color(G, K, DropMode::Before).unwrap();
    assert_eq!(p.colors()[0], BACKGROUND);
}

#[test]
fn parent_drag_moves_whole_group() {
    let mut p = palette(&[R, G, B, K]);
    p.drop_color(G, R, DropMode::Child).unwrap();
    p.drop_color(R, K, DropMode::After).unwrap();
    assert_eq!(p.colors(), &[BACKGROUND, B, K, R, G]);
    assert_eq!(p.parent_of(G), Some(R));
}

#[test]
fn selected_block_moves_together() {
    let mut p = palette(&[R, G, B, K]);
    p.set_selected(R, true);
    p.set_selected(G, true);
    p.drop_color(R, K, DropMode::After).unwrap();
    assert_eq!(p.colors(), &[BACKGROUND, B, K, R, G]);
}

#[test]
fn row_click_moves_focus_only_and_modifiers_select() {
    let mut p = palette(&[R, G, B, K]);
    p.click_row(G, Modifiers::default());
    assert_eq!(p.focus(), Some(G));
    assert!(p.selected().is_empty());
    p.click_row(
        K,
        Modifiers {
            shift: true,
            ctrl: false,
        },
    );
    assert_eq!(p.selected(), &[G, B, K]);
    assert_eq!(p.focus(), Some(K));
    p.click_row(
        G,
        Modifiers {
            shift: false,
            ctrl: true,
        },
    );
    assert_eq!(p.selected(), &[B, K]);
    // 背景の行のクリックは何もしない。
    p.click_row(BACKGROUND, Modifiers::default());
    assert_eq!(p.focus(), Some(G));
}

#[test]
fn eyedropper_moves_focus_without_touching_selection() {
    let mut p = palette(&[R, G]);
    p.set_selected(R, true);
    assert!(p.focus_matching(G));
    assert_eq!(p.focus(), Some(G));
    assert_eq!(p.selected(), &[R]);
    assert!(!p.focus_matching(B));
    assert_eq!(p.focus(), None);
    assert_eq!(p.selected(), &[R]);
}

#[test]
fn edit_range_is_selection_minus_locks() {
    let mut p = palette(&[R, G, B]);
    assert!(p.edit_range().is_unrestricted());
    p.sweep_begin(Column::Lock, B, true);
    assert_eq!(p.sweep_end().unwrap().label, "マスクの一括切り替え");
    let range = p.edit_range();
    assert!(range.permits(R) && !range.permits(B) && range.permits(BACKGROUND));
    p.set_selected(BACKGROUND, true);
    p.set_selected(B, true);
    let range = p.edit_range();
    assert!(range.permits(BACKGROUND));
    assert!(!range.permits(R));
    // 鍵は選択より優先する。
    assert!(!range.permits(B));
}

#[test]
fn sweep_records_bulk_labels_and_selection_is_not_recorded() {
    let mut p = palette(&[R, G, B]);
    p.sweep_begin(Column::Visibility, R, false);
    p.sweep_move(G);
    p.sweep_move(G);
    let rec = p.sweep_end().unwrap();
    assert_eq!(rec.label, "表示の一括切り替え");
    assert!(!p.is_enabled(R) && !p.is_enabled(G) && p.is_enabled(B));
    assert_eq!(p.visible_filter(), Some(vec![B, BACKGROUND]));
    p.sweep_begin(Column::Selection, R, true);
    p.sweep_move(B);
    assert!(p.sweep_end().is_none());
    assert_eq!(p.selected(), &[R, B]);
    assert!(p.show_all().is_some());
    assert_eq!(p.visible_filter(), None);
}

#[test]
fn isolate_toggles_back_to_previous_visibility() {
    let mut p = palette(&[R, G, B]);
    p.set_visible(B, false);
    let rec = p.isolate_visible(R).unwrap();
    assert_eq!(rec.label, "この色だけ表示");
    assert_eq!(p.enabled_set(), [R].into());
    p.isolate_visible(R).unwrap();
    assert!(p.is_enabled(R) && p.is_enabled(G) && !p.is_enabled(B));
}

#[test]
fn snapshot_restore_and_pending_restore_after_refresh() {
    let mut p = palette(&[R, G, B]);
    p.drop_color(G, R, DropMode::Child).unwrap();
    let (name, _) = p.create_category("線");
    p.move_to_category(B, &name).unwrap();
    let before = p.capture();
    // 統合で G・B が消えたあと、取り消して画像が戻る。
    p.set_colors(&[R], false);
    assert_eq!(p.parent_of(G), None);
    p.restore_after_refresh(before.clone());
    p.set_colors(&[R, G, B], false);
    assert_eq!(p.capture(), before);
    assert_eq!(p.category_of(B), Some("線"));
}

#[test]
fn edit_without_significant_change_is_not_recorded() {
    let mut p = palette(&[R, G]);
    assert!(p.set_visible(R, true).is_none());
    assert!(p.clear_groups().is_none());
    p.set_category_collapsed("無い", true);
    assert!(p.unlock_all().is_none());
}

#[test]
fn categories_rows_and_serialization_round_trip() {
    let mut p = palette(&[R, G, B]);
    let (a, rec) = p.create_category("");
    assert_eq!(a, "新規フォルダー");
    assert_eq!(rec.unwrap().label, "使用色フォルダーを作成");
    let (b, _) = p.create_category("新規フォルダー");
    assert_eq!(b, "新規フォルダー 2");
    p.set_selected(G, true);
    p.set_selected(B, true);
    p.move_to_category(G, &a).unwrap();
    p.set_category_collapsed(&a, true);
    assert_eq!(
        p.rows(),
        vec![
            categories::Row::Color {
                rgb: BACKGROUND,
                in_category: false
            },
            categories::Row::Folder { name: a.clone() },
            categories::Row::Folder { name: b.clone() },
            categories::Row::Color {
                rgb: R,
                in_category: false
            },
        ]
    );
    assert_eq!(
        p.rename_category(&a, "線").unwrap().label,
        "使用色フォルダー名を変更"
    );
    p.set_visible(R, false);
    let saved = p.serialize_categories();
    let text = saved.dumps(None).unwrap();
    assert_eq!(
        text,
        r##"{"folders": [{"name": "線", "collapsed": true}, {"name": "新規フォルダー 2", "collapsed": false}], "assignments": {"#0000FF": "線", "#00FF00": "線"}, "enabled": {"#0000FF": true, "#00FF00": true, "#FF0000": false, "#FFFFFF": true}}"##
    );
    let mut again = palette(&[R, G, B]);
    again.restore_categories(Some(&saved));
    assert_eq!(
        again.categories(),
        &["線".to_string(), "新規フォルダー 2".into()]
    );
    assert!(again.is_collapsed("線"));
    assert_eq!(again.category_of(G), Some("線"));
    assert!(!again.is_enabled(R));
    assert_eq!(
        again.delete_category("線").unwrap().label,
        "使用色フォルダーを削除"
    );
    assert_eq!(again.category_of(G), None);
}

#[test]
fn merge_plan_uses_focus_as_target() {
    let mut p = palette(&[R, G, B]);
    assert!(p.merge_plan().is_none());
    p.set_selected(G, true);
    p.set_selected(B, true);
    p.set_selected(BACKGROUND, true);
    p.click_row(R, Modifiers::default());
    assert_eq!(p.merge_plan(), Some((R, vec![G, B])));
}

#[test]
fn remap_image_erases_white_and_respects_mask() {
    let image =
        RgbaImage::from_rgba(3, 1, &[255, 0, 0, 255, 0, 255, 0, 128, 255, 0, 0, 0]).unwrap();
    let mapping: BTreeMap<Rgb, Rgb> = [(R, BACKGROUND), (G, B)].into();
    let (out, count) = remap_image(&image, &mapping, None).unwrap();
    assert_eq!(count, 2);
    // 透明の画素は PmaImage と同じく RGB も 0 に正規化する（バケツの色置換と同じ計算）。
    assert_eq!(out.rgba(), &[0, 0, 0, 0, 0, 0, 255, 128, 0, 0, 0, 0]);
    let (_, count) = remap_image(&image, &mapping, Some(&[0, 1, 1])).unwrap();
    assert_eq!(count, 1);
}

#[test]
fn qt_hsv_matches_known_values() {
    // PySide6 の QColor で確かめた値。
    let hsv = QtHsv::from_rgb((200, 100, 50));
    assert_eq!(
        (hsv.hue_degrees(), hsv.saturation8(), hsv.value8()),
        (20, 191, 200)
    );
    assert_eq!(QtHsv::from_ints(20, 191, 200).to_rgb(), (200, 100, 50));
    assert_eq!(QtHsv::from_rgb((128, 128, 128)).hue_degrees(), -1);
    assert_eq!(shifted_color((255, 0, 0), 120, 0, 0), (0, 255, 0));
    assert_eq!(shifted_color((128, 128, 128), 90, 50, 10), (154, 154, 154));
}

#[test]
fn replacement_set_round_trips() {
    let mapping: BTreeMap<Rgb, Rgb> = [(R, G), (B, K)].into();
    let json = pixels::replacement_set_to_json(&mapping);
    assert_eq!(pixels::replacement_set_from_json(&json).unwrap(), mapping);
    let bad = parse(r##"{"replacements": [{"from": "#zzzzzz", "to": "#000000"}, 3, {"from": "#ff0000", "to": "#FF0000"}]}"##)
        .unwrap()
        .value;
    assert!(pixels::replacement_set_from_json(&bad).unwrap().is_empty());
    assert!(pixels::replacement_set_from_json(&Json::Null).is_err());
}

#[test]
fn chart_normalizes_flat_format_and_round_trips() {
    let flat = parse(
        r##"{"groups": [{"parent": "#ff0000", "child": "#00ff00", "tag": "影"}, {"parent": "#f00", "child": "#0000ff"}],
            "parent_tags": [{"parent": "#FF0000", "tag": "肌"}], "standalone": [{"color": "#000000"}],
            "group_names": {"#FF0000": " 顔 "}, "tag_colors": {"影": "#123456", "Base": "#000"}}"##,
    )
    .unwrap()
    .value;
    let chart = normalize(&flat);
    assert_eq!(chart.tiles.len(), 2);
    assert_eq!(chart.tiles[0].name, "顔");
    assert_eq!(chart.tiles[0].parent_tag, "肌");
    assert_eq!(chart.tiles[0].children.len(), 2);
    assert_eq!(chart.tag_library, vec!["Base", "肌", "影"]);
    assert_eq!(chart.tag_color("Base"), "#000000");
    assert_eq!(chart.tag_order, vec!["肌", "影"]);
    assert_eq!(normalize(&chart.to_json()), chart);
    let again = super::chart::read_pmag(&chart.to_pmag()).unwrap();
    assert_eq!(again, chart);
}

#[test]
fn chart_move_and_assign_tags() {
    let mut p = palette(&[R, G, B]);
    p.drop_color(G, R, DropMode::Child);
    let chart = p.chart_from_groups();
    assert_eq!(chart.tiles.len(), 1);
    let id = chart.tiles[0].id.clone();
    let moved = chart
        .move_color(
            &Hit {
                group_id: id.clone(),
                role: Role::Child(0),
            },
            None,
        )
        .unwrap();
    assert_eq!(moved.tiles.len(), 2);
    assert_eq!(moved.tiles[1].parent.as_deref(), Some("#00FF00"));
    let tagged = moved
        .assign_tag(
            &Hit {
                group_id: id.clone(),
                role: Role::Parent,
            },
            "肌",
        )
        .unwrap();
    assert_eq!(tagged.tiles[0].parent_tag, "肌");
    assert!(tagged.tag_library.contains(&"肌".to_string()));
    // 親を動かすと、子のあるタイルは親のない（placeholder）タイルになる。
    let parented = chart
        .move_color(
            &Hit {
                group_id: id,
                role: Role::Parent,
            },
            None,
        )
        .unwrap();
    assert!(parented.tiles[0].placeholder);
}

#[test]
fn chart_apply_sets_groups_only_for_present_colors() {
    let mut p = palette(&[R, G, B]);
    let chart = normalize(
        &parse(r##"{"tiles": [{"parent": {"color": "#0000FF", "tag": "肌"}, "children": [{"color": "#FF0000", "tag": "影"}, {"color": "#123456"}]}]}"##)
            .unwrap()
            .value,
    );
    assert!(p.apply_chart(&chart));
    assert_eq!(p.parent_of(R), Some(B));
    assert_eq!(p.colors(), &[BACKGROUND, B, R, G]);
    assert_eq!(p.child_tags.get(&R).map(String::as_str), Some("影"));
    let merged = chart.merge(&p.chart_from_groups());
    assert_eq!(merged.tiles.len(), 1);
}

#[test]
fn lock_others_leaves_only_the_target_paintable() {
    let mut p = palette(&[R, G, B]);
    p.set_locked(R, true).unwrap();
    let rec = p.lock_others(R).unwrap();
    assert_eq!(rec.label, "対象以外のマスクOFF");
    assert!(!p.is_locked(R));
    assert!(p.is_locked(G) && p.is_locked(B));
    assert!(p.lock_others(R).is_none());
    assert_eq!(p.unlock_all().unwrap().label, "マスクを全体ON");
}
