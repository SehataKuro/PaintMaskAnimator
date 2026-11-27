//! 読み込んだ絵を新しい列として文書へ入れる（Qt 版の `import_image_sequence`・`psd_dialog`・
//! `insert_layout_paper`）。
//!
//! Python は空の絵の `Layer` を各コマに作って `insert_track(staged=True)` で入れ、
//! `stage_numbered_drawing` で絵と番号を置き、最後に `install_legacy_frames`（用紙は
//! `link_layer_tracks`）で番号の絵をバンクへ移す。ここも同じ手順を同じ順で当てる。

use std::collections::BTreeMap;

use super::changes::{Change, ChangeSet};
use super::commands::PreparedTrack;
use super::model::{Cell, CellNumber, Document, Position, Reference, Track, TrackSettings};
use crate::{image::RgbaImage, Error};

/// 新しく入れる1列。
#[derive(Clone, Debug)]
pub struct ImportedTrack {
    /// レイヤー名。
    pub name: String,
    /// 下書きレイヤーか（「変換せず読み込む」・レイアウト用紙）。
    pub draft: bool,
    /// 表示するか（PSD のレイヤーの表示）。
    pub visible: bool,
    /// 不透明度（PSD のレイヤーの不透明度、0〜1）。
    pub opacity: f64,
    /// コマの順の絵（キャンバスの大きさ、1枚目が番号 1）。
    pub images: Vec<RgbaImage>,
    /// 読み込んだファイル（「元のファイルから再変換」が使う、`track.import_sources`）。
    pub sources: Option<Vec<String>>,
}

impl ImportedTrack {
    /// 表示・不透明度 1 の列。
    pub fn new(name: impl Into<String>, images: Vec<RgbaImage>) -> Self {
        Self {
            name: name.into(),
            draft: false,
            visible: true,
            opacity: 1.0,
            images,
            sources: None,
        }
    }
}

fn invalid(reason: &str) -> Error {
    Error::Invalid(format!("読み込み：{reason}"))
}

impl Document {
    /// `Layer(name, blank_image(), ...)` を各コマに並べた、まだ番号の無い列。
    fn blank_import_track(&self, settings: TrackSettings) -> Result<PreparedTrack, Error> {
        let mut empty = Position::default();
        empty.compatibility.local = Some(Cell {
            image: self.blank_artwork()?,
            initial_image: None,
            cell_name: None,
            is_genga: false,
        });
        Ok(PreparedTrack {
            track: Track {
                settings,
                cells: BTreeMap::new(),
                legacy_archive_hints: Default::default(),
                import_sources: None,
            },
            positions: vec![empty; self.frame_order.len()],
        })
    }

    /// `tracks` を末尾に足し、各列の絵を `start_frame` から1コマずつ番号 1, 2, … で置く
    /// （`import_image_sequence`・`psd_dialog`）。今のコマは `start_frame`、選択は最初に
    /// 足した列。足りないコマは末尾に延ばす。失敗したら文書を変えない。
    pub fn import_image_tracks(
        &mut self,
        start_frame: usize,
        tracks: Vec<ImportedTrack>,
    ) -> Result<ChangeSet, Error> {
        let longest = tracks.iter().map(|t| t.images.len()).max().unwrap_or(0);
        if longest == 0 {
            return Err(invalid("絵がない"));
        }
        if start_frame > self.frame_order.len() {
            return Err(invalid("始めのコマが範囲外"));
        }
        let (width, height) = (self.width, self.height);
        if tracks
            .iter()
            .flat_map(|t| &t.images)
            .any(|image| image.width() != width || image.height() != height)
        {
            return Err(invalid("絵の大きさがキャンバスと違う"));
        }
        let mut edited = self.clone();
        // 新しい空の絵は1枚を共有する（Python の blank_image() の雛形）。
        if edited.blank.is_none() {
            edited.blank = Some(edited.blank_artwork()?);
        }
        let end = start_frame
            .checked_add(longest)
            .ok_or(Error::TimelineSizeOverflow)?;
        let mut changes = edited.ensure_frame_count(end)?;
        let first = edited.track_order.len();
        let mut sources = Vec::new();
        for imported in tracks {
            let index = edited.track_order.len();
            let mut prepared = edited.blank_import_track(TrackSettings {
                name: imported.name,
                visible: imported.visible,
                opacity: imported.opacity,
                alpha_locked: false,
                is_paper: false,
                is_draft: imported.draft,
                color_filter_enabled: false,
                color_filter_rgb: None,
            })?;
            // stage_numbered_drawing で置いた絵は install_legacy_frames で番号のバンクへ
            // 移り、位置の局所の絵は外れる。その結果を直接作る。
            for (offset, image) in imported.images.into_iter().enumerate() {
                let number = CellNumber::new(offset as i64 + 1)?;
                prepared.track.cells.insert(
                    number,
                    Cell {
                        image,
                        initial_image: None,
                        cell_name: None,
                        is_genga: false,
                    },
                );
                let slot = &mut prepared.positions[start_frame + offset];
                slot.reference = Reference::Number(number);
                slot.compatibility.local = None;
                changes.push(Change::DrawingPixels {
                    layer_index: index,
                    number: Some(number),
                    frame: Some(start_frame + offset),
                    rects: vec![],
                });
            }
            changes.extend(edited.insert_track(index, prepared, None)?);
            sources.push((index, imported.sources));
        }
        edited.reinstall_legacy_frames()?;
        for (index, paths) in sources {
            let id = edited.track_order[index];
            if let Some(track) = edited.tracks.get_mut(&id) {
                track.import_sources = paths;
            }
        }
        edited.set_selection_index(start_frame, first)?;
        changes.push(Change::Selection { layer_index: first });
        *self = edited;
        Ok(changes)
    }

    /// レイアウト用紙を一番下の下書きレイヤーとして各コマに入れる（`insert_layout_paper`）。
    /// 選択は用紙のすぐ上の列。失敗したら文書を変えない。
    pub fn insert_layout_paper(
        &mut self,
        name: impl Into<String>,
        image: RgbaImage,
    ) -> Result<ChangeSet, Error> {
        if image.width() != self.width || image.height() != self.height {
            return Err(invalid("用紙の大きさがキャンバスと違う"));
        }
        let mut edited = self.clone();
        if edited.blank.is_none() {
            edited.blank = Some(edited.blank_artwork()?);
        }
        let prepared = edited.blank_import_track(TrackSettings {
            name: name.into(),
            visible: true,
            opacity: 1.0,
            alpha_locked: false,
            is_paper: false,
            is_draft: true,
            color_filter_enabled: false,
            color_filter_rgb: None,
        })?;
        // 番号の無い絵（has_content だけ）として置き、link_layer_tracks の
        // install_legacy_frames で番号を付けてバンクへ移す。
        let mut prepared = prepared;
        for slot in &mut prepared.positions {
            if let Some(cell) = &mut slot.compatibility.local {
                cell.image = image.clone();
            }
            slot.compatibility.unnumbered_content = true;
        }
        let mut changes = edited.insert_track(0, prepared, None)?;
        edited.reinstall_legacy_frames()?;
        let frame = edited
            .frame_order
            .iter()
            .position(|&id| id == self.current_frame)
            .unwrap_or(0);
        let layer = 1.min(edited.track_order.len() - 1);
        edited.set_selection_index(frame, layer)?;
        changes.push(Change::Selection { layer_index: layer });
        *self = edited;
        Ok(changes)
    }
}

/// 左上をそろえて大きさを変えた絵（はみ出す分は切り、足りない分は透明）。
fn resized(image: &RgbaImage, width: usize, height: usize) -> Result<RgbaImage, Error> {
    let mut out = vec![0u8; width * height * 4];
    let (w, h) = (image.width().min(width), image.height().min(height));
    let source = image.rgba();
    for y in 0..h {
        let from = y * image.width() * 4;
        let to = y * width * 4;
        out[to..to + w * 4].copy_from_slice(&source[from..from + w * 4]);
    }
    Ok(RgbaImage::from_rgba(width, height, &out)?.with_tag(image.tag()))
}

impl Document {
    /// キャンバスの大きさを変える（Qt の `replace_doc(w, h, preserve=True)`）。
    ///
    /// 絵は左上をそろえて写し、はみ出す分は切る。同じ絵を共有していた所は共有したまま。
    /// Qt は初期状態の絵を写さない（大きさが合わなくなる）が、ここでは同じように写す。
    pub fn resize_canvas(&mut self, width: usize, height: usize) -> Result<bool, Error> {
        if width == 0 || height == 0 {
            return Err(invalid("キャンバスの大きさが 0"));
        }
        if (width, height) == (self.width, self.height) {
            return Ok(false);
        }
        let mut done: BTreeMap<u64, RgbaImage> = BTreeMap::new();
        let mut resize = |image: &mut RgbaImage| -> Result<(), Error> {
            if image.width() == 0 || image.height() == 0 {
                return Ok(());
            }
            let new = match done.get(&image.generation()) {
                Some(new) => new.clone(),
                None => {
                    let new = resized(image, width, height)?;
                    done.insert(image.generation(), new.clone());
                    new
                }
            };
            *image = new;
            Ok(())
        };
        let mut edited = self.clone();
        let mut cell = |cell: &mut Cell| -> Result<(), Error> {
            resize(&mut cell.image)?;
            if let Some(initial) = &mut cell.initial_image {
                resize(initial)?;
            }
            Ok(())
        };
        for track in edited.tracks.values_mut() {
            for c in track.cells.values_mut() {
                cell(c)?;
            }
        }
        for row in edited.sheet.values_mut() {
            for position in std::sync::Arc::make_mut(row).positions.values_mut() {
                let compat = &mut position.compatibility;
                if let Some(c) = &mut compat.local {
                    cell(c)?;
                }
                if let Some(c) = compat
                    .legacy
                    .as_mut()
                    .and_then(|l| l.legacy.as_mut())
                    .and_then(|l| l.artwork.as_mut())
                {
                    cell(c)?;
                }
            }
        }
        edited.width = width;
        edited.height = height;
        edited.blank = None;
        *self = edited;
        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn image(doc: &Document, value: u8) -> RgbaImage {
        let (w, h) = doc.size();
        RgbaImage::from_rgba(w, h, &vec![value; w * h * 4]).unwrap()
    }

    #[test]
    fn sequence_is_numbered_from_the_current_frame() {
        let mut doc = Document::new_blank(4, 3).unwrap();
        doc.ensure_frame_count(2).unwrap();
        doc.set_selection_index(1, 0).unwrap();
        let images = vec![image(&doc, 10), image(&doc, 20), image(&doc, 30)];
        let mut track = ImportedTrack::new("B", images);
        track.draft = true;
        track.sources = Some(vec!["a.png".into()]);
        doc.import_image_tracks(1, vec![track]).unwrap();
        assert_eq!(doc.frame_order().len(), 4);
        let tid = doc.track_order()[1];
        let track = doc.track(tid).unwrap();
        assert!(track.settings.is_draft);
        assert_eq!(track.settings.name, "B");
        assert_eq!(track.cells.len(), 3);
        assert_eq!(
            track.import_sources.as_deref(),
            Some(&["a.png".to_string()][..])
        );
        let refs: Vec<_> = doc
            .frame_order()
            .iter()
            .map(|f| doc.frame(*f).unwrap().positions[&tid].reference)
            .collect();
        assert!(matches!(refs[0], Reference::Empty));
        assert!(matches!(refs[1], Reference::Number(n) if n.get() == 1));
        assert!(matches!(refs[3], Reference::Number(n) if n.get() == 3));
        assert_eq!(doc.selection(), (doc.frame_order()[1], tid));
    }

    #[test]
    fn wrong_size_leaves_the_document() {
        let mut doc = Document::new_blank(4, 3).unwrap();
        let bad = RgbaImage::blank(2, 2).unwrap();
        let before = doc.to_graph_text().0;
        assert!(doc
            .import_image_tracks(0, vec![ImportedTrack::new("B", vec![bad])])
            .is_err());
        assert!(doc
            .insert_layout_paper("用紙", RgbaImage::blank(1, 1).unwrap())
            .is_err());
        assert_eq!(doc.to_graph_text().0, before);
    }

    #[test]
    fn resize_keeps_the_top_left_and_sharing() {
        let mut doc = Document::new_blank(2, 2).unwrap();
        doc.ensure_frame_count(2).unwrap();
        let px: Vec<u8> = (0..16).collect();
        let art = RgbaImage::from_rgba(2, 2, &px).unwrap();
        doc.import_image_tracks(0, vec![ImportedTrack::new("B", vec![art])])
            .unwrap();
        assert!(doc.resize_canvas(3, 1).unwrap());
        assert!(!doc.resize_canvas(3, 1).unwrap());
        assert_eq!(doc.size(), (3, 1));
        let tid = doc.track_order()[1];
        let cell = doc.track(tid).unwrap().cells.values().next().unwrap();
        assert_eq!(cell.image.rgba(), &[0, 1, 2, 3, 4, 5, 6, 7, 0, 0, 0, 0]);
        let first = doc.track_order()[0];
        for f in doc.frame_order() {
            let local = doc.frame(*f).unwrap().positions[&first]
                .compatibility
                .local
                .as_ref();
            assert_eq!(
                local.map(|c| (c.image.width(), c.image.height())),
                Some((3, 1))
            );
        }
    }

    #[test]
    fn paper_goes_under_every_layer() {
        let mut doc = Document::new_blank(4, 3).unwrap();
        let paper = image(&doc, 200);
        doc.insert_layout_paper("レイアウト用紙", paper).unwrap();
        let tid = doc.track_order()[0];
        let track = doc.track(tid).unwrap();
        assert!(track.settings.is_draft);
        assert_eq!(track.cells.len(), 1);
        assert_eq!(doc.selection().1, doc.track_order()[1]);
    }
}
