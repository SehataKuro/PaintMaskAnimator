//! 線画を色塗り前の初期状態として読み込む（Qt 版 `InitialStateController._import_line_art`）。
//!
//! 読み込み元の絵（カットフォルダーのセル画像・CLIP のアニメーションセル）を、レイヤー名と
//! セル番号で今の絵に合わせ、初期状態だけを差し替える（今の絵とタイミングは変えない）。
//!
//! Qt との違い：Qt は番号の絵を置いたコマの数（同じ番号の絵を2か所に置けば2）と、
//! タイムシートの外のセルを数える。ここは列のバンクの絵（番号）を1つずつ数える。

use super::model::{CellNumber, Document};
use crate::{image::RgbaImage, Error};

/// 初期状態を合わせる相手（レイヤー・番号）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InitialTarget {
    /// レイヤーの位置（下から）。
    pub layer: usize,
    /// レイヤー名。
    pub name: String,
    /// セル番号。
    pub number: CellNumber,
}

impl Document {
    /// 線画を合わせられる絵（用紙・下書き以外のレイヤーの、番号のある絵）。
    pub fn initial_targets(&self) -> Vec<InitialTarget> {
        let mut out = Vec::new();
        for (layer, tid) in self.track_order.iter().enumerate() {
            let Some(track) = self.tracks.get(tid) else {
                continue;
            };
            if track.settings.is_paper || track.settings.is_draft {
                continue;
            }
            for number in track.cells.keys() {
                out.push(InitialTarget {
                    layer,
                    name: track.settings.name.clone(),
                    number: *number,
                });
            }
        }
        out
    }

    /// 番号の絵の初期状態を差し替える（`set_initial_images`）。絵の大きさは文書と同じこと。
    pub fn set_line_art_initial_images(
        &mut self,
        images: Vec<(usize, CellNumber, RgbaImage)>,
    ) -> Result<usize, Error> {
        let (width, height) = self.size();
        let mut count = 0;
        for (layer, number, image) in images {
            if (image.width(), image.height()) != (width, height) {
                return Err(Error::Image("初期状態の絵の大きさが文書と違います"));
            }
            let tid = *self
                .track_order
                .get(layer)
                .ok_or(Error::Image("レイヤーがありません"))?;
            let cell = self
                .tracks
                .get_mut(&tid)
                .and_then(|t| t.cells.get_mut(&number))
                .ok_or(Error::Image("セルがありません"))?;
            cell.initial_image = Some(image);
            count += 1;
        }
        Ok(count)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::document::import_commands::ImportedTrack;

    fn image(width: usize, height: usize, value: u8) -> RgbaImage {
        RgbaImage::from_rgba(width, height, &vec![value; width * height * 4]).unwrap()
    }

    #[test]
    fn targets_skip_drafts_and_set_replaces_initial_images() {
        let mut doc = Document::new_blank(2, 2).unwrap();
        let mut draft = ImportedTrack::new("下書き", vec![image(2, 2, 9)]);
        draft.draft = true;
        doc.import_image_tracks(
            0,
            vec![
                ImportedTrack::new("A", vec![image(2, 2, 1), image(2, 2, 2)]),
                draft,
            ],
        )
        .unwrap();
        let targets = doc.initial_targets();
        let named: Vec<(usize, &str, i64)> = targets
            .iter()
            .filter(|t| t.name == "A")
            .map(|t| (t.layer, t.name.as_str(), t.number.get()))
            .collect();
        assert_eq!(named, vec![(1, "A", 1), (1, "A", 2)]);
        let n = |v| CellNumber::new(v).unwrap();
        assert!(targets.iter().all(|t| t.name != "下書き"));
        assert_eq!(
            doc.set_line_art_initial_images(vec![(1, n(2), image(2, 2, 7))])
                .unwrap(),
            1
        );
        let tid = doc.track_order()[1];
        let cell = &doc.track(tid).unwrap().cells[&n(2)];
        assert_eq!(cell.initial_image.as_ref().unwrap().rgba()[0], 7);
        assert!(doc
            .set_line_art_initial_images(vec![(1, n(1), image(1, 1, 0))])
            .is_err());
    }
}
