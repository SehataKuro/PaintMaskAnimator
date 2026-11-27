use super::*;
use crate::image::{Rect, RgbaImage};

/// `#` を選んだ画素とする文字の図からマスクを作る。
fn mask_from(rows: &[&str]) -> SelectionMask {
    let height = rows.len();
    let width = rows[0].len();
    let bits: Vec<u8> = rows
        .iter()
        .flat_map(|row| row.bytes().map(|cell| u8::from(cell == b'#')))
        .collect();
    SelectionMask::from_bits(width, height, &bits).unwrap()
}

fn render(mask: &SelectionMask) -> Vec<String> {
    (0..mask.height())
        .map(|y| {
            (0..mask.width())
                .map(|x| if mask.contains(x, y) { '#' } else { '.' })
                .collect()
        })
        .collect()
}

fn rect(x: usize, y: usize, width: usize, height: usize) -> Rect {
    Rect {
        x,
        y,
        width,
        height,
    }
}

/// 1色で塗った画像。
fn filled(width: usize, height: usize, pixel: [u8; 4]) -> RgbaImage {
    let pixels: Vec<u8> = (0..width * height).flat_map(|_| pixel).collect();
    RgbaImage::from_rgba(width, height, &pixels).unwrap()
}

fn pixel(image: &RgbaImage, x: usize, y: usize) -> [u8; 4] {
    let offset = (y * image.width() + x) * 4;
    image.rgba()[offset..offset + 4].try_into().unwrap()
}

// 以下の期待値は、PySide6（Qt 6）の QPainter.drawPolygon（アンチエイリアスなし）で
// 塗った画素を写したもの。画素の中心が辺の上に乗る場合の扱いも Qt に合わせる。

#[test]
fn polygon_matches_qt_on_half_pixel_ties() {
    let points = [(16.0, 22.0), (0.0, 6.0), (1.0, 13.0), (22.0, 12.0)];
    let mask = SelectionMask::from_polygon(&points, 24, 20, (0.0, 0.0));
    let expected = [
        "........................",
        "........................",
        "........................",
        "........................",
        "........................",
        "........................",
        "#.......................",
        "##......................",
        "###.....................",
        "####....................",
        ".####...................",
        ".#####..................",
        ".######.....##########..",
        "........#############...",
        ".........############...",
        "..........##########....",
        "...........########.....",
        "............#######.....",
        ".............#####......",
        "..............####......",
    ];
    assert_eq!(render(&mask), expected);
}

#[test]
fn polygon_matches_qt_with_fractional_points() {
    let points = [(1.3, 0.7), (9.6, 2.2), (4.49, 8.51), (-2.0, 5.5)];
    let mask = SelectionMask::from_polygon(&points, 10, 9, (0.0, 0.0));
    let expected = [
        "..........",
        ".#####....",
        "#########.",
        "#########.",
        "########..",
        "#######...",
        "######....",
        "..###.....",
        "..........",
    ];
    assert_eq!(render(&mask), expected);
}

#[test]
fn axis_rect_rounds_like_qt() {
    let points = [(1.5, 1.5), (6.5, 1.5), (6.5, 4.5), (1.5, 4.5)];
    let mask = SelectionMask::from_polygon(&points, 8, 6, (0.0, 0.0));
    let expected = [
        "........", "........", "..#####.", "..#####.", "..#####.", "........",
    ];
    assert_eq!(render(&mask), expected);
}

#[test]
fn concave_polygon_matches_qt() {
    let points = [(2.0, 1.0), (7.0, 1.0), (7.0, 5.0), (2.0, 5.0), (4.0, 3.0)];
    let mask = SelectionMask::from_polygon(&points, 8, 6, (0.0, 0.0));
    let expected = [
        "........", "...####.", "....###.", "....###.", "...####.", "........",
    ];
    assert_eq!(render(&mask), expected);
}

#[test]
fn polygon_offset_shifts_the_mask() {
    let points = [
        (12.0, 11.0),
        (17.0, 11.0),
        (17.0, 15.0),
        (12.0, 15.0),
        (14.0, 13.0),
    ];
    let shifted = SelectionMask::from_polygon(&points, 8, 6, (10.0, 10.0));
    let plain = SelectionMask::from_polygon(
        &[(2.0, 1.0), (7.0, 1.0), (7.0, 5.0), (2.0, 5.0), (4.0, 3.0)],
        8,
        6,
        (0.0, 0.0),
    );
    assert_eq!(shifted, plain);
}

#[test]
fn polygon_crossing_the_image_edges_is_clipped() {
    // 画像の外から入る辺の位置は、外での交点から積み上げて求める（端で止めない）。
    let points = [(30.0, -2.0), (10.0, 1.0), (25.0, 22.0), (24.0, 18.0)];
    let mask = SelectionMask::from_polygon(&points, 24, 20, (0.0, 0.0));
    assert!(mask.contains(23, 5));
    assert!(!mask.contains(0, 5));
    let wide = SelectionMask::from_polygon(&points, 40, 30, (0.0, 0.0));
    assert_eq!(wide.crop(24, 20, 0, 0), mask);
}

#[test]
fn degenerate_polygons_select_nothing() {
    assert!(SelectionMask::from_polygon(&[(1.0, 1.0), (5.0, 5.0)], 8, 8, (0.0, 0.0)).is_empty());
    let flat = Selection::rect((2.0, 2.0), (2.0, 6.0));
    assert!(flat.mask(8, 8, 0, 0).is_empty());
    assert!(
        SelectionMask::from_polygon(&[(0.0, 0.0), (4.0, 0.0), (4.0, 4.0)], 0, 0, (0.0, 0.0))
            .is_empty()
    );
}

#[test]
fn rect_selection_sorts_the_corners() {
    let selection = Selection::rect((6.2, 5.0), (1.0, 1.7));
    assert_eq!(
        selection.polygon_points().unwrap(),
        &[(1.0, 1.7), (6.2, 1.7), (6.2, 5.0), (1.0, 5.0)]
    );
    assert_eq!(
        selection.bounds(),
        IntRect {
            x: 1,
            y: 1,
            width: 6,
            height: 4
        }
    );
}

#[test]
fn contours_trace_outer_edge_and_holes() {
    let mask = mask_from(&[".....", ".###.", ".#.#.", ".###.", "....."]);
    let contours = mask.contours();
    assert_eq!(
        contours,
        vec![
            vec![(1, 1), (4, 1), (4, 4), (1, 4)],
            vec![(2, 2), (2, 3), (3, 3), (3, 2)],
        ]
    );
    assert_eq!(largest_contour(&contours), Some(0));
}

#[test]
fn contours_split_diagonal_touching_pixels() {
    // 角だけで接する画素は、右折を優先して別の輪郭に分ける（Python と同じ）。
    let mask = mask_from(&["#.", ".#"]);
    assert_eq!(
        mask.contours(),
        vec![
            vec![(0, 0), (1, 0), (1, 1), (0, 1)],
            vec![(1, 1), (2, 1), (2, 2), (1, 2)],
        ]
    );
    assert!(mask_from(&["..", ".."]).contours().is_empty());
}

#[test]
fn mask_selection_keeps_bounds_and_outlines() {
    let mask = mask_from(&["......", "..##..", "..#...", "......"]);
    let selection = Selection::from_mask(mask.clone()).unwrap();
    assert!(selection.is_mask());
    assert_eq!(
        selection.bounds(),
        IntRect {
            x: 2,
            y: 1,
            width: 2,
            height: 2
        }
    );
    assert_eq!(
        selection.outlines(),
        vec![vec![
            (2.0, 1.0),
            (4.0, 1.0),
            (4.0, 2.0),
            (3.0, 2.0),
            (3.0, 3.0),
            (2.0, 3.0)
        ]]
    );
    assert_eq!(selection.mask(6, 4, 0, 0), mask);
    // 部分を切り出すときは範囲外を選ばない。
    assert_eq!(
        render(&selection.mask(3, 3, 2, 0)),
        vec!["...", "##.", "#.."]
    );
    assert_eq!(render(&selection.mask(2, 2, -1, 1)), vec!["..", ".."]);
    assert!(Selection::from_mask(SelectionMask::empty(4, 4)).is_none());
}

#[test]
fn clip_mask_follows_apply_selection_clip() {
    let selection = Selection::polygon(vec![(0.0, 0.0), (5.0, 0.0)]).unwrap();
    assert!(selection.clip_mask(4, 4, (0.0, 0.0)).is_none());
    let square = Selection::rect((2.0, 2.0), (6.0, 6.0));
    let clip = square.clip_mask(4, 4, (3.0, 3.0)).unwrap();
    assert_eq!(render(&clip), vec!["###.", "###.", "###.", "...."]);
    // マスクの選択は、ずらしを偶数丸めした整数にする（Python の round）。
    let masked = Selection::from_mask(mask_from(&["....", ".##.", ".##.", "...."])).unwrap();
    let clip = masked.clip_mask(2, 2, (0.5, 1.5)).unwrap();
    assert_eq!(render(&clip), vec![".#", ".."]);
}

#[test]
fn mask_boolean_ops() {
    let a = mask_from(&["##..", "##.."]);
    let b = mask_from(&[".##.", ".##."]);
    assert_eq!(render(&a.union(&b).unwrap()), vec!["###.", "###."]);
    assert_eq!(render(&a.subtract(&b).unwrap()), vec!["#...", "#..."]);
    assert_eq!(render(&a.intersect(&b).unwrap()), vec![".#..", ".#.."]);
    assert!(a.union(&SelectionMask::empty(3, 2)).is_err());
    assert_eq!(a.bounds(), Some(rect(0, 0, 2, 2)));
    assert_eq!(SelectionMask::empty(3, 3).bounds(), None);
}

#[test]
fn invert_stays_inside_the_canvas_area() {
    let selection = Selection::rect((2.0, 2.0), (4.0, 4.0));
    let area = canvas_area(6, 6, 1, 4, 4).unwrap();
    assert_eq!(area, rect(1, 1, 4, 4));
    let inverted = selection.inverted(6, 6, area).unwrap();
    assert_eq!(
        render(&inverted.mask(6, 6, 0, 0)),
        vec!["......", ".####.", ".#..#.", ".#..#.", ".####.", "......"]
    );
    let all = Selection::from_rect(area);
    assert!(
        all.inverted(6, 6, area).is_none(),
        "全体を反転すると選択が消える"
    );
    assert_eq!(canvas_area(4, 4, 8, 4, 4), None);
}

#[test]
fn combine_replaces_adds_and_subtracts() {
    let current = Selection::rect((0.0, 0.0), (2.0, 2.0));
    let region = mask_from(&[".##.", ".##.", "....", "...."]);
    let replaced = Selection::combine(Some(&current), &region, CombineMode::Replace).unwrap();
    assert_eq!(replaced.unwrap().mask(4, 4, 0, 0), region);
    let added = Selection::combine(Some(&current), &region, CombineMode::Add)
        .unwrap()
        .unwrap();
    assert_eq!(
        render(&added.mask(4, 4, 0, 0)),
        vec!["###.", "###.", "....", "...."]
    );
    let subtracted = Selection::combine(Some(&current), &region, CombineMode::Subtract)
        .unwrap()
        .unwrap();
    assert_eq!(
        render(&subtracted.mask(4, 4, 0, 0)),
        vec!["#...", "#...", "....", "...."]
    );
    assert!(Selection::combine(None, &region, CombineMode::Subtract)
        .unwrap()
        .is_none());
}

/// 文字の図から画像を作る。`#` は黒、`r` は赤、`w` は白、`.` は透明。
fn picture(rows: &[&str]) -> RgbaImage {
    let height = rows.len();
    let width = rows[0].len();
    let pixels: Vec<u8> = rows
        .iter()
        .flat_map(|row| {
            row.bytes().flat_map(|cell| match cell {
                b'#' => [0, 0, 0, 255],
                b'r' => [200, 30, 30, 255],
                b'w' => [255, 255, 255, 255],
                _ => [0, 0, 0, 0],
            })
        })
        .collect();
    RgbaImage::from_rgba(width, height, &pixels).unwrap()
}

#[test]
fn contiguous_region_treats_white_as_background() {
    let image = picture(&["......", ".####.", ".#w.#.", ".#..#.", ".####.", "......"]);
    let options = ContiguousOptions {
        adjacent: true,
        close_gap: None,
        require_closed: false,
    };
    let ContiguousResult::Region(inside) = contiguous_region(&image, 2, 3, options) else {
        panic!("領域が選ばれない");
    };
    assert_eq!(
        render(&inside),
        vec!["......", "......", "..##..", "..##..", "......", "......"]
    );
    let closed = ContiguousOptions {
        require_closed: true,
        ..options
    };
    assert_eq!(
        contiguous_region(&image, 0, 0, closed),
        ContiguousResult::OpenToEdge
    );
    assert_eq!(
        contiguous_region(&image, 6, 0, options),
        ContiguousResult::OutOfBounds
    );
    let ContiguousResult::Region(line) = contiguous_region(&image, 1, 1, options) else {
        panic!("線が選ばれない");
    };
    assert_eq!(line.bits().iter().filter(|&&v| v != 0).count(), 12);
}

#[test]
fn contiguous_region_closes_gaps_and_selects_globally() {
    let image = picture(&[
        "........", ".######.", ".#....#.", ".#......", ".#....#.", ".######.", "........",
        "r......r",
    ]);
    let open = ContiguousOptions {
        adjacent: true,
        close_gap: None,
        require_closed: true,
    };
    assert_eq!(
        contiguous_region(&image, 3, 3, open),
        ContiguousResult::OpenToEdge,
        "隙間から外へ漏れる"
    );
    let gap = ContiguousOptions {
        close_gap: Some(1),
        ..open
    };
    let ContiguousResult::Region(inside) = contiguous_region(&image, 3, 3, gap) else {
        panic!("隙間を閉じると内側が選べる");
    };
    assert!(inside.contains(3, 3));
    assert!(!inside.contains(0, 0));
    let global = ContiguousOptions {
        adjacent: false,
        close_gap: None,
        require_closed: true,
    };
    let ContiguousResult::Region(reds) = contiguous_region(&image, 0, 7, global) else {
        panic!("同じ色を全体から選ぶ");
    };
    assert_eq!(render(&reds)[7], "#......#");
    assert_eq!(reds.bits().iter().filter(|&&v| v != 0).count(), 2);
}

#[test]
fn color_mask_and_used_area() {
    let image = picture(&["....", ".r#.", "..r.", "...."]);
    assert_eq!(
        render(&color_mask(&image, [200, 30, 30])),
        vec!["....", ".#..", "..#.", "...."]
    );
    let used = used_area(&image).unwrap();
    assert_eq!(
        used.polygon_points().unwrap(),
        &[(1.0, 1.0), (3.0, 1.0), (3.0, 3.0), (1.0, 3.0)]
    );
    assert!(used_area(&picture(&["..", ".."])).is_none());
}

const GREEN: [u8; 4] = [64, 192, 112, 255];

#[test]
fn delete_clears_only_the_selection_and_returns_the_undo_rect() {
    let mut image = filled(6, 6, GREEN);
    let original = image.clone();
    let selection = Selection::from_mask(mask_from(&[
        "......", ".#....", "..##..", "......", "......", "......",
    ]))
    .unwrap();
    let edit = delete_selection(&mut image, &selection).unwrap().unwrap();
    assert_eq!(edit.rect, rect(1, 1, 3, 2));
    assert_eq!(edit.before, original.copy_rgba_rect(edit.rect).unwrap());
    assert_eq!(pixel(&image, 1, 1), [0; 4]);
    assert_eq!(pixel(&image, 2, 1), GREEN, "外接矩形の中でも選択の外は残る");
    assert_eq!(pixel(&image, 3, 2), [0; 4]);
    let empty = Selection::rect((2.0, 2.0), (2.0, 5.0));
    assert!(delete_selection(&mut image, &empty).unwrap().is_none());
}

#[test]
fn copy_and_cut_follow_the_selection_shape() {
    let mut image = filled(6, 6, GREEN);
    let triangle = Selection::polygon(vec![(1.0, 1.0), (5.0, 1.0), (1.0, 5.0)]).unwrap();
    let (rect_copied, copied) = copy_selection(&image, Some(&triangle)).unwrap().unwrap();
    assert_eq!(rect_copied, rect(1, 1, 4, 4));
    assert_eq!(pixel(&copied, 0, 0), GREEN);
    assert_eq!(pixel(&copied, 3, 3), [0; 4], "選択の外は透明");
    let cut = cut_selection(&mut image, Some(&triangle)).unwrap().unwrap();
    assert_eq!(cut.clipboard.rgba(), copied.rgba());
    assert_eq!(cut.edit.unwrap().rect, rect(1, 1, 4, 4));
    assert_eq!(pixel(&image, 1, 1), [0; 4]);
    assert_eq!(pixel(&image, 4, 4), GREEN);

    let whole = cut_selection(&mut image, None).unwrap().unwrap();
    assert_eq!(whole.rect, rect(0, 0, 6, 6));
    assert_eq!(whole.edit.unwrap().rect, rect(0, 0, 6, 6));
    assert!(image.rgba().iter().all(|&v| v == 0));

    let outside = Selection::rect((10.0, 10.0), (12.0, 12.0));
    assert!(copy_selection(&image, Some(&outside)).unwrap().is_none());
}

#[test]
fn paste_composes_over_at_the_selection_corner() {
    let mut image = filled(6, 6, [0, 0, 255, 255]);
    let clip = RgbaImage::from_rgba(
        2,
        2,
        &[
            255, 0, 0, 255, //
            0, 0, 0, 0, //
            255, 0, 0, 128, //
            0, 255, 0, 255,
        ],
    )
    .unwrap();
    let selection = Selection::rect((4.4, 4.6), (9.0, 9.0));
    let edit = paste(&mut image, &clip, Some(&selection), (1, 1))
        .unwrap()
        .unwrap();
    assert_eq!(edit.rect, rect(4, 4, 2, 2));
    assert_eq!(pixel(&image, 4, 4), [255, 0, 0, 255]);
    assert_eq!(
        pixel(&image, 5, 4),
        [0, 0, 255, 255],
        "透明な画素は重ねない"
    );
    assert_eq!(pixel(&image, 4, 5), [128, 0, 127, 255]);
    assert_eq!(pixel(&image, 5, 5), [0, 255, 0, 255]);
    let edit = paste(&mut image, &clip, None, (-1, -1)).unwrap().unwrap();
    assert_eq!(edit.rect, rect(0, 0, 1, 1));
    assert_eq!(pixel(&image, 0, 0), [0, 255, 0, 255]);
    assert!(paste(&mut image, &clip, None, (6, 0)).unwrap().is_none());
}

#[test]
fn clip_write_keeps_pixels_outside_the_selection() {
    let mut image = filled(6, 6, GREEN);
    let selection = Selection::polygon(vec![(2.0, 2.0), (6.0, 2.0), (2.0, 6.0)]).unwrap();
    let area = rect(1, 1, 4, 4);
    let clip = selection.clip_mask(4, 4, (1.0, 1.0)).unwrap();
    let red: Vec<u8> = (0..16).flat_map(|_| [255, 0, 0, 255]).collect();
    let edit = clip_write(&mut image, area, &red, Some(&clip))
        .unwrap()
        .unwrap();
    assert_eq!(edit.rect, rect(2, 2, 3, 3));
    assert_eq!(pixel(&image, 2, 2), [255, 0, 0, 255]);
    assert_eq!(pixel(&image, 4, 4), GREEN);
    assert_eq!(pixel(&image, 1, 1), GREEN);
    let all = clip_write(&mut image, area, &red, None).unwrap().unwrap();
    assert_eq!(all.rect, area);
    assert_eq!(pixel(&image, 1, 1), [255, 0, 0, 255]);
    assert!(clip_write(&mut image, area, &red[4..], None).is_err());
    let none = SelectionMask::empty(4, 4);
    assert!(clip_write(&mut image, area, &red, Some(&none))
        .unwrap()
        .is_none());
}

#[test]
fn lift_cuts_out_the_transform_source_with_a_margin() {
    let mut image = filled(10, 10, GREEN);
    let selection = Selection::rect((3.0, 3.0), (5.0, 6.0));
    assert_eq!(
        transform_source_rect(&selection, 10, 10),
        Some(rect(1, 1, 6, 7))
    );
    let edge = Selection::rect((0.0, 0.0), (1.0, 1.0));
    assert_eq!(transform_source_rect(&edge, 10, 10), Some(rect(0, 0, 3, 3)));
    let lifted = lift_selection(&mut image, &selection).unwrap().unwrap();
    assert_eq!(lifted.edit.rect, rect(1, 1, 6, 7));
    assert_eq!(pixel(&lifted.source, 0, 0), [0; 4]);
    assert_eq!(pixel(&lifted.source, 2, 2), GREEN);
    assert_eq!(pixel(&image, 3, 3), [0; 4]);
    assert_eq!(pixel(&image, 5, 3), GREEN);
    let base = cleared_selection(&filled(10, 10, GREEN), &selection).unwrap();
    assert_eq!(base.rgba(), image.rgba());
}

#[test]
fn clearing_works_whatever_the_tool_and_cancels_a_transform() {
    let mut state = SelectionState::new();
    assert!(!state.can_clear());
    state.begin_lasso((1.0, 1.0));
    state.extend_lasso((5.0, 1.0));
    assert!(!state.finish_lasso(), "2点では選択にしない");
    assert!(state.lasso().is_empty());
    state.begin_lasso((1.0, 1.0));
    state.extend_lasso((5.0, 1.0));
    state.extend_lasso((1.0, 5.0));
    assert!(state.finish_lasso());
    assert!(state.can_clear());
    state.set_transform_active(true);
    assert!(
        !state.select_all(rect(0, 0, 8, 8)),
        "変形中はすべてを選択しない"
    );
    let cleared = state.clear();
    assert_eq!(
        cleared,
        Cleared {
            cancel_transform: true,
            changed: true
        }
    );
    assert!(!state.has_selection() && !state.transform_active());
    assert_eq!(
        state.clear(),
        Cleared {
            cancel_transform: false,
            changed: false
        }
    );

    state.begin_rect((6.0, 6.0));
    state.update_rect((2.0, 3.0));
    assert_eq!(state.rect_drag(), Some(((6.0, 6.0), (2.0, 3.0))));
    assert!(state.finish_rect());
    assert_eq!(state.selection().unwrap().bounds().x, 2);
    assert!(state.invert(8, 8, rect(0, 0, 8, 8)));
    assert!(state.selection().unwrap().is_mask());
    state
        .combine(&SelectionMask::empty(8, 8), CombineMode::Replace)
        .unwrap();
    assert!(!state.has_selection(), "空の自動選択は解除になる");
    state.set_mask(mask_from(&["#."]));
    assert!(state.has_selection());
    assert!(state.select_all(rect(0, 0, 2, 1)));
    assert!(!state.selection().unwrap().is_mask());
}
