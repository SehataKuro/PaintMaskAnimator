//! MainLineRepaint の文書の操作（Qt 版の `line_ops.main_line_repaint`）。
//!
//! 選んだレイヤーを隠し、そのすぐ上に「Paint」、さらに上に「LINE」の列を入れる。元の絵の
//! 1 枚ごとに `image_ops::line_split::split_line` で分け、同じ絵（同じ番号）を見るコマは
//! 分けた同じ絵を見る。番号は分けた順に 1, 2, …（LINE と Paint で同じ番号）。
//!
//! Python は空の絵の `Layer` を各コマに作って `insert_track(staged=True)` で入れ、
//! `stage_numbered_drawing`・`stage_drawing_reference` で絵を置き、最後に
//! `install_legacy_frames` で番号の絵をバンクへ移す。ここはその結果を直接作り、
//! `reinstall_legacy_frames` を同じ順に当てる（import_commands.rs と同じ作り方）。

use super::access::ArtworkSlot;
use super::changes::{Change, ChangeSet};
use super::commands::TrackProperty;
use super::model::{Cell, CellNumber, Document, Reference, TrackSettings};
use crate::image_ops::line_split::{contains_any, split_line};
use crate::Error;

fn invalid(reason: &str) -> Error {
    Error::Invalid(format!("MainLineRepaint：{reason}"))
}

/// MainLineRepaint の結果。
#[derive(Clone, Debug)]
pub struct LineRepaint {
    /// 文書の変更。
    pub changes: ChangeSet,
    /// 新しい LINE の列の位置（選択した列）。
    pub line_index: usize,
}

impl Document {
    /// 位置の絵（`layer.has_content` が真の所だけ）と、同じ絵を見分ける鍵。
    fn content_artwork(&self, frame: usize, layer: usize) -> Option<(ArtworkSlot, &Cell)> {
        let tid = *self.track_order.get(layer)?;
        let fid = *self.frame_order.get(frame)?;
        let position = self.sheet.get(&fid)?.positions.get(&tid)?;
        let content = match position.reference {
            Reference::Number(_) => true,
            Reference::Blank => false,
            Reference::Empty => position.compatibility.unnumbered_content,
        };
        if !content {
            return None;
        }
        if position.references_bank() {
            let number = position.number()?;
            let cell = self.tracks.get(&tid)?.cells.get(&number)?;
            Some((ArtworkSlot::Bank(tid, number), cell))
        } else {
            let cell = position.compatibility.local.as_ref()?;
            Some((ArtworkSlot::Local(fid, tid), cell))
        }
    }

    /// 選んだ色が `layer` のどこかのコマにあるか（Qt の最初の走査）。
    pub fn line_colours_present(&self, layer: usize, selected: &[[u8; 3]]) -> bool {
        (0..self.frame_order.len()).any(|frame| {
            self.content_artwork(frame, layer)
                .is_some_and(|(_, cell)| contains_any(&cell.image, selected))
        })
    }

    /// `layer` を選んだ色の LINE と、それ以外の Paint に分ける。選んだ色がどのコマにも
    /// 無ければ `Ok(None)`（文書は変えない）。失敗したら文書を変えない。
    pub fn main_line_repaint(
        &mut self,
        layer: usize,
        selected: &[[u8; 3]],
    ) -> Result<Option<LineRepaint>, Error> {
        if layer >= self.track_order.len() {
            return Err(invalid("レイヤーが範囲の外"));
        }
        if self.frame_order.is_empty() || selected.is_empty() {
            return Ok(None);
        }
        if !self.line_colours_present(layer, selected) {
            return Ok(None);
        }
        let mut edited = self.clone();
        if edited.blank.is_none() {
            edited.blank = Some(edited.blank_artwork()?);
        }
        let mut changes =
            edited.set_track_properties(&[layer], &[TrackProperty::Visible(false)], false)?;
        let paint_index = layer + 1;
        let line_index = layer + 2;
        let settings = |name: &str| TrackSettings {
            name: name.into(),
            visible: true,
            opacity: 1.0,
            alpha_locked: false,
            is_paper: false,
            is_draft: false,
            color_filter_enabled: false,
            color_filter_rgb: None,
        };
        let mut paint = edited.blank_import_track(settings("Paint"))?;
        let mut line = edited.blank_import_track(settings("LINE"))?;

        // 分けた絵：元の絵の鍵 → (番号, LINE に絵があるか, Paint に絵があるか)。
        let mut separated: Vec<(ArtworkSlot, CellNumber, bool, bool)> = Vec::new();
        let mut pixels = Vec::new();
        for frame in 0..edited.frame_order.len() {
            let Some((key, cell)) = edited.content_artwork(frame, layer) else {
                continue;
            };
            let (number, has_line, has_paint) =
                match separated.iter().find(|(known, ..)| *known == key) {
                    Some(&(_, number, has_line, has_paint)) => (number, has_line, has_paint),
                    None => {
                        let split = split_line(&cell.image, selected)?;
                        let number = CellNumber::new(separated.len() as i64 + 1)?;
                        separated.push((key, number, split.has_line, split.has_paint));
                        for (track, image, content) in [
                            (&mut line, split.line, split.has_line),
                            (&mut paint, split.paint, split.has_paint),
                        ] {
                            if content {
                                track.track.cells.insert(
                                    number,
                                    Cell {
                                        image,
                                        initial_image: None,
                                        cell_name: None,
                                        is_genga: false,
                                    },
                                );
                            }
                        }
                        (number, split.has_line, split.has_paint)
                    }
                };
            for (track, content) in [(&mut line, has_line), (&mut paint, has_paint)] {
                if content {
                    let slot = &mut track.positions[frame];
                    slot.reference = Reference::Number(number);
                    slot.compatibility.local = None;
                }
            }
            pixels.push((frame, number, has_line, has_paint));
        }
        changes.extend(edited.insert_track(paint_index, paint, None)?);
        changes.extend(edited.insert_track(line_index, line, None)?);
        for (frame, number, has_line, has_paint) in pixels {
            for (index, content) in [(line_index, has_line), (paint_index, has_paint)] {
                changes.push(Change::DrawingPixels {
                    layer_index: index,
                    number: content.then_some(number),
                    frame: Some(frame),
                    rects: vec![],
                });
            }
        }
        edited.reinstall_legacy_frames()?;
        let frame = edited
            .frame_order
            .iter()
            .position(|&id| id == edited.current_frame)
            .unwrap_or(0);
        edited.set_selection_index(frame, line_index)?;
        changes.push(Change::Selection {
            layer_index: line_index,
        });
        *self = edited;
        Ok(Some(LineRepaint {
            changes,
            line_index,
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::image::RgbaImage;

    fn doc_with(images: &[[u8; 4]]) -> Document {
        let mut doc = Document::new_blank(2, 1).unwrap();
        let tracks = vec![super::super::import_commands::ImportedTrack::new(
            "A",
            images
                .iter()
                .map(|&[r, g, b, a]| {
                    let k = [0, 0, 0, 255];
                    RgbaImage::from_rgba(2, 1, &[k, [r, g, b, a]].concat()).unwrap()
                })
                .collect(),
        )];
        doc.import_image_tracks(0, tracks).unwrap();
        doc
    }

    #[test]
    fn splits_into_paint_and_line_above_the_hidden_source() {
        let mut doc = doc_with(&[[255, 0, 0, 255], [0, 255, 0, 255]]);
        let layer = doc.track_order.len() - 1;
        assert!(doc
            .main_line_repaint(layer, &[[9, 9, 9]])
            .unwrap()
            .is_none());
        let done = doc.main_line_repaint(layer, &[[0, 0, 0]]).unwrap().unwrap();
        assert_eq!(done.line_index, layer + 2);
        let names: Vec<_> = doc
            .track_order
            .iter()
            .map(|id| doc.tracks[id].settings.name.clone())
            .collect();
        assert_eq!(&names[layer..], ["A", "Paint", "LINE"]);
        assert!(!doc.tracks[&doc.track_order[layer]].settings.visible);
        let line = &doc.tracks[&doc.track_order[layer + 2]];
        let paint = &doc.tracks[&doc.track_order[layer + 1]];
        assert_eq!(line.cells.len(), 2);
        let one = CellNumber::new(1).unwrap();
        assert_eq!(line.cells[&one].image.rgba(), &[0, 0, 0, 255, 0, 0, 0, 0]);
        assert_eq!(
            paint.cells[&one].image.rgba(),
            &[255, 0, 0, 255, 255, 0, 0, 255]
        );
    }
}
