//! 読み込んだアニメーション（CLIP STUDIO のアニメーションフォルダー）で新しい文書を作る
//! （Qt 版 `clip_animation.build_pma_frames` と `ImportController._replace_document`）。
//!
//! Python は列ごとに密な `Layer` を並べ（保持のコマは `clone_reference`）、
//! `install_legacy_frames` で番号の絵をバンクへ移し、置かれていないセルを旧連番保管として
//! もう一度 `install_legacy_frames` に渡す。ここはその結果の文書を直接作る。

use std::collections::BTreeMap;
use std::sync::Arc;

use super::compatibility::{ArchiveHint, LegacyPosition, LegacySlot};
use super::model::{
    Cell, CellNumber, Document, Frame, FrameId, Position, Reference, Track, TrackId, TrackSettings,
};
use crate::import::clip::{cell_numbers_of, layer_runs, numeric_cell_name, AnimationLayer};
use crate::{image::RgbaImage, Error};

/// 作った文書と、知らせに使う数。
#[derive(Clone, Debug)]
pub struct AnimationDocument {
    pub document: Document,
    /// 絵のあるキーの数（Qt の「色塗り前の初期状態」に保持するセルの数）。
    pub content_keys: usize,
    /// タイムシートに置かれず連番に残したセルの数。
    pub archived: usize,
}

fn blank_cell(image: &RgbaImage) -> Cell {
    Cell {
        image: image.clone(),
        initial_image: None,
        cell_name: None,
        is_genga: false,
    }
}

/// `clone_reference` が保持のコマに残す旧入力の写し（絵のない旧位置）。
fn held_legacy(settings: &TrackSettings, blank: &RgbaImage) -> LegacyPosition {
    LegacyPosition {
        legacy: Some(LegacySlot {
            settings: settings.clone(),
            state: ArchiveHint {
                has_content: false,
                is_blank_key: false,
                exposure: 1,
                pending: false,
                tween: None,
                tween_member: None,
            },
            number: None,
            artwork: Some(blank_cell(blank)),
        }),
        key_start: false,
        blank_start: false,
        pending: false,
        input_length: None,
    }
}

impl Document {
    /// `start_frame`〜`end_frame` のアニメーションの列で新しい文書を作る。今のコマと選択は
    /// 先頭。キーのセルは番号の絵（数字のセル名はその番号、ほかは空いている番号）、
    /// 空のセル名は空セル。置かれていないセルは旧連番保管としてバンクに残す。
    /// 上限の検査は [`crate::import::clip::check_frames`] で先に済ませること。
    ///
    /// `replaced` は置き換える前の文書の大きさ。Python の `clone_reference` は保持のコマの
    /// 旧入力に `blank_model_image()`（その時のキャンバスの大きさの空の絵）を残し、読み込みは
    /// キャンバスの大きさを変える前に動くので、その絵は前の文書の大きさになる。
    pub fn from_animation(
        width: usize,
        height: usize,
        start_frame: i64,
        end_frame: i64,
        layers: &[AnimationLayer],
        replaced: (usize, usize),
    ) -> Result<AnimationDocument, Error> {
        let invalid = |reason: &str| Error::Invalid(format!("アニメーションの読み込み：{reason}"));
        if layers.is_empty() || end_frame < start_frame {
            return Err(invalid("列かコマがない"));
        }
        let duration = (end_frame - start_frame + 1) as usize;
        let blank = RgbaImage::blank(width, height)?;
        let held_blank = if replaced == (width, height) {
            blank.clone()
        } else {
            RgbaImage::blank(replaced.0, replaced.1)?
        };
        let numbers = cell_numbers_of(layers).map_err(|e| invalid(&e.0))?;
        let mut tracks = BTreeMap::new();
        let mut columns: Vec<Vec<Position>> = Vec::with_capacity(layers.len());
        let mut share = 0u64;
        // 2コマ以上の空キーは同じ空の絵を共有する（Python の clone_reference）。組の番号は
        // Python の文書グラフと同じくコマ・列の順で振る。
        let mut blank_runs: Vec<(usize, usize, usize)> = Vec::new();
        let mut content_keys = 0;
        let mut archived = 0;
        for (li, (layer, numbers)) in layers.iter().zip(&numbers).enumerate() {
            let settings = TrackSettings {
                name: layer.name.clone(),
                visible: true,
                opacity: 1.0,
                alpha_locked: false,
                is_paper: false,
                is_draft: false,
                color_filter_enabled: false,
                color_filter_rgb: None,
            };
            let mut cells: BTreeMap<CellNumber, Cell> = BTreeMap::new();
            let mut column = vec![Position::default(); duration];
            for (start, length, tag) in layer_runs(&layer.keys, start_frame, end_frame) {
                for offset in 0..length {
                    let position = &mut column[start + offset];
                    if offset > 0 {
                        position.compatibility.legacy = Some(held_legacy(&settings, &held_blank));
                    }
                    if tag.is_empty() {
                        position.reference = Reference::Blank;
                        position.blank_start = offset == 0;
                        position.compatibility.local = Some(blank_cell(&blank));
                    }
                }
                if tag.is_empty() {
                    if length > 1 {
                        blank_runs.push((start, li, length));
                    }
                    continue;
                }
                let index = layer
                    .cels
                    .iter()
                    .position(|(t, _)| *t == tag)
                    .ok_or_else(|| invalid("セルの絵がない"))?;
                let number = CellNumber::new(numbers[index])?;
                content_keys += 1;
                cells.entry(number).or_insert_with(|| Cell {
                    image: layer.cels[index].1.clone(),
                    initial_image: None,
                    cell_name: (numeric_cell_name(&tag) != Some(number.get())).then(|| tag.clone()),
                    is_genga: false,
                });
                for position in &mut column[start..start + length] {
                    position.reference = Reference::Number(number);
                }
            }
            // 置かれていないセルは旧連番保管（_replace_document の sequence_archive）。
            let mut hints = BTreeMap::new();
            for ((tag, image), &number) in layer.cels.iter().zip(numbers) {
                let number = CellNumber::new(number)?;
                if cells.contains_key(&number) {
                    continue;
                }
                let stripped = crate::export::py::strip(tag);
                let normalized = stripped.trim_start_matches('0');
                let normalized = if normalized.is_empty() {
                    "0"
                } else {
                    normalized
                };
                let numeric = crate::export::py::all_decimal(stripped)
                    && normalized == number.get().to_string();
                cells.insert(
                    number,
                    Cell {
                        image: image.clone(),
                        initial_image: None,
                        cell_name: (!numeric).then(|| tag.clone()),
                        is_genga: false,
                    },
                );
                hints.insert(
                    number,
                    ArchiveHint {
                        has_content: true,
                        is_blank_key: false,
                        exposure: 1,
                        pending: false,
                        tween: None,
                        tween_member: None,
                    },
                );
                archived += 1;
            }
            tracks.insert(
                TrackId(li as u64),
                Track {
                    settings,
                    cells,
                    legacy_archive_hints: hints,
                    import_sources: None,
                },
            );
            columns.push(column);
        }
        blank_runs.sort_unstable();
        for (start, li, length) in blank_runs {
            for position in &mut columns[li][start..start + length] {
                position.compatibility.local_share = Some(share);
            }
            share += 1;
        }

        let frame_order: Vec<FrameId> = (0..duration as u64).map(FrameId).collect();
        let track_order: Vec<TrackId> = (0..layers.len() as u64).map(TrackId).collect();
        let mut sheet = BTreeMap::new();
        for (column_index, &id) in frame_order.iter().enumerate() {
            let positions = track_order
                .iter()
                .zip(&columns)
                .map(|(&track, column)| (track, column[column_index].clone()))
                .collect();
            sheet.insert(
                id,
                Arc::new(Frame {
                    duration: 1,
                    positions,
                }),
            );
        }
        let document = Self {
            width,
            height,
            next_frame_id: frame_order.len() as u64,
            next_track_id: track_order.len() as u64,
            current_frame: frame_order[0],
            active_layer: track_order[0],
            frame_order,
            track_order,
            sheet,
            tracks,
            blank: None,
        };
        Ok(AnimationDocument {
            document,
            content_keys,
            archived,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::import::clip::CelKey;

    fn key(frame: i64, tag: &str) -> CelKey {
        CelKey {
            frame,
            tag: tag.into(),
        }
    }

    #[test]
    fn keys_become_numbers_blanks_and_archived_cells() {
        let cel = |v: u8| RgbaImage::from_rgba(2, 1, &[v, 0, 0, 255, 0, 0, 0, 0]).unwrap();
        let layers = [AnimationLayer {
            name: "A".into(),
            keys: vec![key(1, "1"), key(2, ""), key(4, "b")],
            cels: vec![
                ("1".into(), cel(1)),
                ("b".into(), cel(2)),
                ("7".into(), cel(3)),
            ],
        }];
        let built = Document::from_animation(2, 1, 1, 5, &layers, (4, 4)).unwrap();
        assert_eq!((built.content_keys, built.archived), (2, 1));
        let doc = &built.document;
        let track = doc.track(doc.track_order()[0]).unwrap();
        let refs: Vec<Reference> = doc
            .frame_order()
            .iter()
            .map(|&id| doc.frame(id).unwrap().positions[&TrackId(0)].reference)
            .collect();
        let n = |v| Reference::Number(CellNumber::new(v).unwrap());
        // 「b」は空いている番号（2）、置かれていない「7」は連番に残る。
        assert_eq!(
            refs,
            vec![n(1), Reference::Blank, Reference::Blank, n(2), n(2)]
        );
        let b = &track.cells[&CellNumber::new(2).unwrap()];
        assert_eq!(b.cell_name.as_deref(), Some("b"));
        assert!(track.cells[&CellNumber::new(1).unwrap()]
            .cell_name
            .is_none());
        assert!(track
            .legacy_archive_hints
            .contains_key(&CellNumber::new(7).unwrap()));
        // 2コマの空キーは空の絵を共有し、保持のコマの旧入力は前の大きさの空の絵。
        let blank = &doc.frame(FrameId(2)).unwrap().positions[&TrackId(0)];
        assert!(blank.compatibility.local_share.is_some());
        let held = blank.compatibility.legacy.as_ref().unwrap();
        let artwork = held.legacy.as_ref().unwrap().artwork.as_ref().unwrap();
        assert_eq!((artwork.image.width(), artwork.image.height()), (4, 4));
    }
}
