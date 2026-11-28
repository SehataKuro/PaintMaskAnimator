//! 選択レイヤーのセルへまとめて操作をかける（Python の `frame_scope.apply_over_scope` と、
//! それを使う色置換・ゴミ取り・線整え・塗り抜けの呼び出し）。
//!
//! * [`scope_cells`]：今のコマ／すべてのコマのセル（保持は露出元へ畳み、同じセルは1度）。
//! * [`apply_over_cells`]：同じ絵を共有するセルは1回だけ処理し、すべて終えてから書く
//!   （中止したら文書は変えない）。
//! * [`replace_colors`]・[`remove_dust`]・[`tidy_lines`]：各操作を範囲にかける。
//!
//! Undo を1回にまとめるのは呼び出し側（画面は変える前の文書を履歴に積む）。

use crate::compose::layers::Sources;
use crate::document::changes::ChangeSet;
use crate::document::drawing_commands::DrawingImageGroup;
use crate::document::model::Document;
use crate::image::RgbaImage;
use crate::image_ops::{self, dust, line_tidy, MaskRef, PixelEdit};
use crate::reference_scan::Progress;
use crate::Error;

use super::bucket::{position_image, position_slot, Rgb};

/// セル `(コマ, レイヤー)`。コマは露出元（キー）のコマ。
pub type Cell = (usize, usize);

/// Python の `resolve_cells(FrameScope.choose(doc, all_frames))`：今のコマ `frame`
/// （`all_frames` ならすべてのコマ）の `layer` のセル。保持は露出元へ畳み、絵の無い位置は
/// 除き、同じセルは1度だけ、コマ順に返す。
pub fn scope_cells(
    document: &Document,
    frame: usize,
    layer: usize,
    all_frames: bool,
) -> Result<Vec<Cell>, Error> {
    let sources = Sources::new(document)?;
    if layer >= sources.layer_count() || sources.frame_count() == 0 {
        return Ok(Vec::new());
    }
    let frames: Vec<usize> = if all_frames {
        (0..sources.frame_count()).collect()
    } else if frame < sources.frame_count() {
        vec![frame]
    } else {
        Vec::new()
    };
    let mut cells: Vec<Cell> = frames
        .into_iter()
        .filter_map(|f| sources.key_frame(f, layer))
        .map(|key| (key, layer))
        .collect();
    cells.sort_unstable();
    cells.dedup();
    Ok(cells)
}

/// [`apply_over_cells`] の結果（Python の `ScopeResult`）。
#[derive(Clone, Debug, Default)]
pub struct ScopeOutcome {
    /// 変えたセルの数（絵を共有するセルも数える）。
    pub changed_cells: usize,
    /// 操作が数えた「変えた画素数」の合計。
    pub changed_pixels: usize,
    /// 変えたセル（共有するセルを含む）。
    pub changed: Vec<Cell>,
    /// 中止した（文書は変えていない）。
    pub cancelled: bool,
    /// 文書の変更通知。
    pub changes: ChangeSet,
}

/// 同じ絵を指すセルをまとめる（Python の `_group_shared_images`）。先頭のセルで処理する。
fn group_shared(document: &Document, cells: &[Cell]) -> Vec<Vec<Cell>> {
    let mut groups: Vec<(Option<crate::document::access::ArtworkSlot>, Vec<Cell>)> = Vec::new();
    for &cell in cells {
        let slot = position_slot(document, cell.0, cell.1);
        match groups
            .iter_mut()
            .find(|(s, _)| slot.is_some() && *s == slot)
        {
            Some((_, members)) => members.push(cell),
            None => groups.push((slot, vec![cell])),
        }
    }
    groups.into_iter().map(|(_, members)| members).collect()
}

/// `cells` の各絵へ `op` をかける。`op` は絵を受け取り、変えたときだけ
/// `(新しい絵, 変えた画素数)` を返す。`progress` は `(済んだ数, 全体)` で呼ぶ。
///
/// 書き込みはすべての `op` が終わってから一度に行う。中止したら何も書かない。
pub fn apply_over_cells(
    document: &mut Document,
    cells: &[Cell],
    progress: &mut Progress<'_>,
    mut op: impl FnMut(Cell, &RgbaImage) -> Result<Option<(RgbaImage, usize)>, Error>,
) -> Result<ScopeOutcome, Error> {
    let mut outcome = ScopeOutcome::default();
    let groups = group_shared(document, cells);
    let total = groups.len();
    let mut pending: Vec<(Vec<Cell>, RgbaImage, usize)> = Vec::new();
    for (done, members) in groups.into_iter().enumerate() {
        if !progress.step(done, total) {
            outcome.cancelled = true;
            return Ok(outcome);
        }
        let (frame, layer) = members[0];
        let image = position_image(document, frame, layer)?;
        if let Some((produced, pixels)) = op(members[0], &image)? {
            pending.push((members, produced, pixels));
        }
    }
    if total > 0 && !progress.step(total, total) {
        outcome.cancelled = true;
        return Ok(outcome);
    }
    if pending.is_empty() {
        return Ok(outcome);
    }
    let groups: Vec<DrawingImageGroup<'_>> = pending
        .iter()
        .map(|(members, image, _)| DrawingImageGroup {
            positions: members,
            image,
        })
        .collect();
    outcome.changes = document.replace_drawing_images(&groups)?;
    for (members, _, pixels) in pending {
        outcome.changed_cells += members.len();
        outcome.changed_pixels += pixels;
        outcome.changed.extend(members);
    }
    Ok(outcome)
}

fn edited(edit: Option<PixelEdit>) -> Option<(RgbaImage, usize)> {
    edit.map(|edit| (edit.image, edit.pixels))
}

/// 選択範囲のマスク（キャンバス全体の 0/1）。大きさが違えば全体とみなす（Python と同じ）。
fn selection_ref<'a>(selection: Option<&'a [u8]>, image: &RgbaImage) -> Option<MaskRef<'a>> {
    selection.map(|data| MaskRef {
        data,
        width: image.width(),
        height: image.height(),
    })
}

/// 使用色の色置換（`apply_palette_replacements`）を `cells` にかける。
/// 置き換える画素の無いセルは変えない。`selection` があればその中だけ。
pub fn replace_colors(
    document: &mut Document,
    cells: &[Cell],
    mapping: &[(Rgb, Rgb)],
    selection: Option<&[u8]>,
    progress: &mut Progress<'_>,
) -> Result<ScopeOutcome, Error> {
    apply_over_cells(document, cells, progress, |_, image| {
        let mask = selection
            .filter(|s| s.len() == image.width() * image.height())
            .and_then(|s| selection_ref(Some(s), image));
        Ok(edited(image_ops::color_replace::replace_colors(
            image, mapping, mask,
        )?))
    })
}

/// ゴミ取り・塗り抜け・線周りのノイズ（`remove_dust_fill_surrounding`）を `cells` にかける。
pub fn remove_dust(
    document: &mut Document,
    cells: &[Cell],
    options: &dust::DustOptions<'_>,
    progress: &mut Progress<'_>,
) -> Result<ScopeOutcome, Error> {
    apply_over_cells(document, cells, progress, |_, image| {
        Ok(edited(dust::remove_dust(image, options)?))
    })
}

/// [`tidy_lines`] の結果：範囲の結果と、足した・消した画素の数。
#[derive(Clone, Debug, Default)]
pub struct TidyOutcome {
    pub scope: ScopeOutcome,
    pub added: usize,
    pub removed: usize,
}

/// 線整え（`tidy_lines`）を `cells` にかける。`region` があればその中だけ。
pub fn tidy_lines(
    document: &mut Document,
    cells: &[Cell],
    colours: &[Rgb],
    diagonal: line_tidy::Diagonal,
    region: Option<&[u8]>,
    progress: &mut Progress<'_>,
) -> Result<TidyOutcome, Error> {
    let (mut added, mut removed) = (0, 0);
    let scope = apply_over_cells(document, cells, progress, |_, image| {
        let mask = selection_ref(region, image);
        let result = line_tidy::tidy_lines(image, colours, diagonal, mask)?;
        added += result.added;
        removed += result.removed;
        Ok(result
            .edit
            .map(|edit| (edit.image, result.added + result.removed)))
    })?;
    Ok(TidyOutcome {
        scope,
        added,
        removed,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};
    use std::sync::atomic::AtomicBool;

    const W: usize = 8;
    const H: usize = 6;
    const RED: Rgb = [255, 0, 0];
    const BLUE: Rgb = [0, 0, 255];

    fn position(number: i64) -> Value {
        json!({
            "has_content": true, "is_blank_key": false, "sequence_number": number,
            "sequence_only": false, "blank_start": false, "tween": null, "tween_member": null,
            "local": null, "content_origin": null, "v1_position": null, "dense_import": null,
            "length_imported": null, "resolve_source": null, "legacy_missing": false,
        })
    }

    fn filled(rgb: Rgb) -> RgbaImage {
        let mut pixels = vec![0u8; W * H * 4];
        for px in pixels.chunks_exact_mut(4).take(W * 2) {
            px.copy_from_slice(&[rgb[0], rgb[1], rgb[2], 255]);
        }
        RgbaImage::from_rgba(W, H, &pixels).unwrap()
    }

    /// 1列・コマごとの番号 `numbers`（同じ番号は同じ絵を共有する）。
    fn document(images: Vec<RgbaImage>, numbers: &[i64]) -> Document {
        let cells: Vec<Value> = (0..images.len())
            .map(|i| json!([i + 1, {"image": i, "initial_image": null, "cell_name": null}]))
            .collect();
        let frames: Vec<Value> = numbers
            .iter()
            .map(|n| json!({"duration": 1, "positions": [position(*n)]}))
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
        Document::from_graph(&value, &images).unwrap()
    }

    fn top_left(document: &Document, frame: usize) -> [u8; 4] {
        position_image(document, frame, 0).unwrap().rgba()[..4]
            .try_into()
            .unwrap()
    }

    #[test]
    fn cells_fold_shared_numbers_and_current_frame() {
        let doc = document(vec![filled(RED), filled(BLUE)], &[1, 1, 2]);
        let all = scope_cells(&doc, 0, 0, true).unwrap();
        assert!(all.contains(&(2, 0)));
        assert_eq!(scope_cells(&doc, 2, 0, false).unwrap(), vec![(2, 0)]);
        assert!(scope_cells(&doc, 0, 3, true).unwrap().is_empty());
    }

    #[test]
    fn replace_colors_over_all_cells_counts_shared_cells_once_per_image() {
        let mut doc = document(vec![filled(RED), filled(BLUE)], &[1, 1, 2]);
        let cells = scope_cells(&doc, 0, 0, true).unwrap();
        let outcome = replace_colors(
            &mut doc,
            &cells,
            &[(RED, BLUE)],
            None,
            &mut Progress::default(),
        )
        .unwrap();
        assert_eq!(outcome.changed_pixels, W * 2);
        assert!(outcome.changed_cells >= 1);
        assert_eq!(top_left(&doc, 0), [0, 0, 255, 255]);
        assert_eq!(top_left(&doc, 1), [0, 0, 255, 255], "同じ絵の保持も変わる");
        assert_eq!(top_left(&doc, 2), [0, 0, 255, 255], "もとから青");
    }

    #[test]
    fn replace_colors_respects_selection_and_white_erases() {
        let mut doc = document(vec![filled(RED)], &[1]);
        let mut selection = vec![0u8; W * H];
        selection[1] = 1;
        let outcome = replace_colors(
            &mut doc,
            &[(0, 0)],
            &[(RED, [255, 255, 255])],
            Some(&selection),
            &mut Progress::default(),
        )
        .unwrap();
        assert_eq!(outcome.changed_pixels, 1);
        let image = position_image(&doc, 0, 0).unwrap();
        assert_eq!(&image.rgba()[..4], &[255, 0, 0, 255]);
        assert_eq!(image.rgba()[7], 0, "置換先が白なら透明にする");
    }

    #[test]
    fn cancel_leaves_the_document_unchanged() {
        let mut doc = document(vec![filled(RED)], &[1]);
        let cancel = AtomicBool::new(true);
        let outcome = replace_colors(
            &mut doc,
            &[(0, 0)],
            &[(RED, BLUE)],
            None,
            &mut Progress {
                report: None,
                cancel: Some(&cancel),
            },
        )
        .unwrap();
        assert!(outcome.cancelled);
        assert_eq!(top_left(&doc, 0), [255, 0, 0, 255]);
    }

    #[test]
    fn dust_removes_a_small_speck() {
        let mut pixels = [255u8; W * H * 4].to_vec();
        pixels[(3 * W + 4) * 4..][..4].copy_from_slice(&[0, 0, 0, 255]);
        let image = RgbaImage::from_rgba(W, H, &pixels).unwrap();
        let mut doc = document(vec![image], &[1]);
        let outcome = remove_dust(
            &mut doc,
            &[(0, 0)],
            &dust::DustOptions {
                mode: dust::DustMode::Despeckle,
                max_area: 3,
                selected: None,
            },
            &mut Progress::default(),
        )
        .unwrap();
        assert_eq!(outcome.changed_pixels, 1);
        let after = position_image(&doc, 0, 0).unwrap();
        assert_eq!(&after.rgba()[(3 * W + 4) * 4..][..3], &[255, 255, 255]);
    }
}
