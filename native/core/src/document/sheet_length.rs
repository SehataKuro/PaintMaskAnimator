//! タイムシートに書くコマ数（Python の `MainWindow._sheet_duration`）。

use super::model::Document;
use crate::Error;

impl Document {
    /// タイムシート（XDTS・TDTS）の長さ。
    ///
    /// 再生範囲の終わりがあればそれ（1 以上）。なければコマ数と、旧形式へ写したキーの
    /// 尺の終わりの大きい方。編集していない v1 のキーが密な文書の終わりより先へ延びて
    /// いれば、その元の長さも含める（取り込み時の入力の長さで、ライブの位置ではない）。
    pub fn sheet_duration(&self, playback_range_end: Option<i64>) -> Result<usize, Error> {
        if let Some(end) = playback_range_end {
            return Ok(end.max(1) as usize);
        }
        let mut duration = self.frame_order.len().max(1);
        let columns: Vec<Vec<_>> = self
            .track_order
            .iter()
            .map(|track| {
                self.frame_order
                    .iter()
                    .map(|frame| &self.sheet[frame].positions[track])
                    .collect()
            })
            .collect();
        for track in super::v2_placement::project(&columns)? {
            for (column, placement) in track.iter().enumerate() {
                if !placement.pending && (placement.content || placement.blank) {
                    duration = duration.max(column + placement.length.max(1));
                }
            }
        }
        for (column, frame) in self.frame_order.iter().enumerate() {
            for track in &self.track_order {
                let position = &self.sheet[frame].positions[track];
                let compatibility = &position.compatibility;
                let (Some(import), Some(old)) = (compatibility.dense_import, &compatibility.legacy)
                else {
                    continue;
                };
                if !import.guard.matches(position) {
                    continue;
                }
                let Some(legacy) = &old.legacy else { continue };
                let state = &legacy.state;
                if !state.pending && (state.has_content || state.is_blank_key) {
                    let length = old.input_length.unwrap_or(state.exposure).max(1);
                    duration = duration.max(column + length);
                }
            }
        }
        Ok(duration)
    }
}
