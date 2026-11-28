//! タイムラインのレイヤーの「結合」（Qt 版の `main_window_layer_ops.merge_rows`）。
//!
//! 連続した列を、各コマで実際に表示される絵（保持コマは手前の実体）を下から
//! ソースオーバーで重ねた一つの列に置き換える。重ねる絵の組が前のコマと同じなら保持
//! （同じ番号）、違えば新しい番号、どの列にも絵が無いコマは未使用にする。番号は 1 から。
//! 表示フィルタ（使用色の絞り込みなど）は焼き込まず、元の絵を重ねる。

use std::collections::BTreeMap;

use super::changes::ChangeSet;
use super::commands::PreparedTrack;
use super::model::{Cell, CellNumber, Document, Position, Reference, Track, TrackSettings};
use crate::compose::layers::Sources;
use crate::compose::PremulImage;
use crate::image::Rect;
use crate::Error;

fn invalid(reason: &str) -> Error {
    Error::Invalid(format!("レイヤーの結合：{reason}"))
}

impl Document {
    /// 連続した列 `indices`（下から数えた番号、2つ以上）を一つに結合する。名前は一番上の列。
    /// 失敗したら文書を変えない。
    pub fn merge_layers(&mut self, indices: &[usize]) -> Result<ChangeSet, Error> {
        let [start, .., end] = indices else {
            return Err(invalid("2つ以上の列が必要です"));
        };
        if *end >= self.track_order.len() || !indices.iter().copied().eq(*start..=*end) {
            return Err(invalid("連続していません"));
        }
        let name = self.tracks[&self.track_order[*end]].settings.name.clone();
        let (width, height) = (self.width, self.height);
        let sources = Sources::new(self)?;
        let mut cells = BTreeMap::new();
        let mut positions = Vec::with_capacity(self.frame_order.len());
        let mut previous: Option<Vec<(usize, usize)>> = None;
        let mut number = 0i64;
        for frame in 0..self.frame_order.len() {
            let resolved: Vec<_> = indices
                .iter()
                .filter_map(|&layer| sources.layer(self, frame, layer))
                .filter(|source| {
                    source.image.is_some()
                        && source.settings.visible
                        && source.settings.opacity > 0.0
                })
                .collect();
            let mut position = Position::default();
            if resolved.is_empty() {
                // 白紙の区間では直前の露出を延ばさない。
                previous = None;
                positions.push(position);
                continue;
            }
            let signature: Vec<_> = resolved.iter().map(|s| (s.layer, s.key_frame)).collect();
            if previous.as_ref() != Some(&signature) {
                let mut merged = PremulImage::transparent(width, height)?;
                for source in &resolved {
                    let image = source.image.expect("絵のある列だけを残した");
                    let pixels = PremulImage::from_image_rect(
                        image,
                        Rect {
                            x: 0,
                            y: 0,
                            width,
                            height,
                        },
                    )?;
                    merged.draw(&pixels, 0, 0, source.settings.opacity.clamp(0.0, 1.0));
                }
                number += 1;
                cells.insert(
                    CellNumber::new(number)?,
                    Cell {
                        image: merged.to_straight()?,
                        initial_image: None,
                        cell_name: None,
                        is_genga: false,
                    },
                );
                previous = Some(signature);
            }
            position.reference = Reference::Number(CellNumber::new(number)?);
            positions.push(position);
        }
        let prepared = PreparedTrack {
            track: Track {
                settings: TrackSettings {
                    name,
                    visible: true,
                    opacity: 1.0,
                    alpha_locked: false,
                    is_paper: false,
                    is_draft: false,
                    color_filter_enabled: false,
                    color_filter_rgb: None,
                },
                cells,
                legacy_archive_hints: Default::default(),
                import_sources: None,
            },
            positions,
        };
        let mut edited = self.clone();
        let changes = edited.merge_tracks(indices, prepared)?;
        *self = edited;
        Ok(changes)
    }
}

#[cfg(test)]
mod tests {
    use super::super::import_commands::ImportedTrack;
    use super::*;
    use crate::image::RgbaImage;

    fn solid(pixel: [u8; 4]) -> RgbaImage {
        RgbaImage::from_rgba(2, 1, &pixel.repeat(2)).unwrap()
    }

    fn references(doc: &Document, layer: usize) -> Vec<Reference> {
        let tid = doc.track_order()[layer];
        doc.frame_order()
            .iter()
            .map(|f| doc.frame(*f).unwrap().positions[&tid].reference)
            .collect()
    }

    #[test]
    fn merges_resolved_cells_with_holds_and_blanks() {
        let mut doc = Document::new_blank(2, 1).unwrap();
        doc.ensure_frame_count(4).unwrap();
        doc.import_image_tracks(
            0,
            vec![ImportedTrack::new("A", vec![solid([255, 0, 0, 255])])],
        )
        .unwrap();
        let blue = solid([0, 0, 255, 128]);
        doc.import_image_tracks(
            0,
            vec![ImportedTrack::new(
                "B",
                vec![blue.clone(), blue, solid([0, 255, 0, 255])],
            )],
        )
        .unwrap();
        assert!(doc.clone().merge_layers(&[1]).is_err());
        assert!(doc.clone().merge_layers(&[0, 2]).is_err());
        let a = references(&doc, 1);
        doc.merge_layers(&[1, 2]).unwrap();
        assert_eq!(doc.track_order().len(), 2);
        let merged = doc.track(doc.track_order()[1]).unwrap();
        assert_eq!(merged.settings.name, "B");
        assert!(merged.settings.visible && !merged.settings.is_draft);
        let n = |v| Reference::Number(CellNumber::new(v).unwrap());
        // A が1コマ目だけなら 4コマ目は白紙。A が保持していれば 2・3コマ目は A＋B。
        let refs = references(&doc, 1);
        assert_eq!(refs[0], n(1));
        assert_eq!(refs[3], Reference::Empty);
        assert_eq!(merged.cells.len(), 3, "{a:?} {refs:?}");
        let first = merged.cells[&CellNumber::new(1).unwrap()]
            .image
            .rgba()
            .to_vec();
        assert_eq!(first[3], 255);
        assert_eq!(first[2], 128);
        assert!((126..=128).contains(&first[0]), "{first:?}");
        let last = merged.cells[&CellNumber::new(3).unwrap()]
            .image
            .rgba()
            .to_vec();
        assert_eq!(&last[..4], &[0, 255, 0, 255]);
    }
}
