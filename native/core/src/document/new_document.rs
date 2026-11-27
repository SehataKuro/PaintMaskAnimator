//! 新しい文書（Qt 版の「新規作成」と起動直後の文書）。
//!
//! Python では `Document()` が `make_frame()`（コマ 1 つ・レイヤー「A」1 つ・空の絵）を
//! `install_legacy_frames` に通して作る。できあがる文書は、コマ・列とも ID 0、尺 1、
//! 列「A」は既定の表示設定でバンクは空、位置は番号の無い未使用で、局所の空の絵を
//! 1 枚持つ（v2 では `compatibility.local_states` の 1 件になる）。ここではそれを直に組む。

use std::collections::BTreeMap;
use std::sync::Arc;

use super::compatibility::PositionCompatibility;
use super::model::{
    layer_name, Cell, Document, Frame, FrameId, Position, Track, TrackId, TrackSettings,
};
use crate::{image::RgbaImage, Error};

/// 新しい列の既定の表示設定（Python の Track の既定値）。
pub fn default_track_settings(index: usize) -> TrackSettings {
    TrackSettings {
        name: layer_name(index),
        visible: true,
        opacity: 1.0,
        alpha_locked: false,
        is_paper: false,
        is_draft: false,
        color_filter_enabled: false,
        color_filter_rgb: None,
    }
}

impl Document {
    /// Python の `Document()`（`constants.CANVAS_WIDTH`×`CANVAS_HEIGHT`）と同じ新しい文書。
    ///
    /// 幅か高さが 0 ならエラー。
    pub fn new_blank(width: usize, height: usize) -> Result<Self, Error> {
        if width == 0 || height == 0 {
            return Err(Error::Invalid(format!(
                "キャンバスの大きさが不正です: {width}×{height}"
            )));
        }
        let blank = RgbaImage::blank(width, height)?;
        let frame = FrameId(0);
        let track = TrackId(0);
        let position = Position {
            compatibility: PositionCompatibility {
                local: Some(Cell {
                    image: blank.clone(),
                    initial_image: None,
                    cell_name: None,
                    is_genga: false,
                }),
                ..PositionCompatibility::default()
            },
            ..Position::default()
        };
        let mut sheet = BTreeMap::new();
        sheet.insert(
            frame,
            Arc::new(Frame {
                duration: 1,
                positions: BTreeMap::from([(track, position)]),
            }),
        );
        let tracks = BTreeMap::from([(
            track,
            Track {
                settings: default_track_settings(0),
                cells: BTreeMap::new(),
                legacy_archive_hints: BTreeMap::new(),
                import_sources: None,
            },
        )]);
        Ok(Self {
            width,
            height,
            frame_order: vec![frame],
            track_order: vec![track],
            sheet,
            tracks,
            current_frame: frame,
            active_layer: track,
            next_frame_id: 1,
            next_track_id: 1,
            // Python の from_graph と同じく雛形は持たない（命令が要るときに作る）。
            blank: None,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn new_blank_matches_python_graph() {
        let document = Document::new_blank(64, 48).unwrap();
        let (graph, images) = document.to_graph();
        assert_eq!(images.len(), 1);
        assert_eq!((images[0].width(), images[0].height()), (64, 48));
        assert!(images[0].rgba().iter().all(|&v| v == 0));
        assert_eq!(graph["width"], json!(64));
        assert_eq!(graph["height"], json!(48));
        assert_eq!(graph["next_frame_id"], json!(1));
        assert_eq!(graph["next_track_id"], json!(1));
        assert_eq!(graph["tracks"][0]["settings"]["name"], json!("A"));
        let position = &graph["frames"][0]["positions"][0];
        assert_eq!(position["local"]["image"], json!(0));
        assert_eq!(position["has_content"], json!(false));
        assert_eq!(position["sequence_number"], json!(null));
        // 読み直しても同じ文書になる。
        let again = Document::from_graph(&graph, &images).unwrap();
        assert_eq!(again.to_graph().0, graph);
    }

    #[test]
    fn new_blank_rejects_empty_size() {
        assert!(Document::new_blank(0, 10).is_err());
        assert!(Document::new_blank(10, 0).is_err());
    }
}
