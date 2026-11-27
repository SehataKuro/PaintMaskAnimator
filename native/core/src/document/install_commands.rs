//! 接続済みのコマを旧形式の取り込み手順で再公開する。

use std::collections::{btree_map::Entry, BTreeSet};
use std::sync::Arc;

use super::model::{Cell, CellNumber, Document, FrameId, Reference, TrackId};
use crate::Error;

/// 再公開で窓口の更新が必要になった位置とバンク。
#[derive(Debug, Default)]
pub struct LegacyInstall {
    /// Layer.timeline_revision の加算回数。
    pub revisions: usize,
    /// 値を書き換えた位置。
    pub changed: Vec<(FrameId, TrackId)>,
    /// バンクへ移した局所の絵と、その番号。
    pub moved: Vec<(FrameId, TrackId, CellNumber)>,
    /// 外した未使用の局所の絵。
    pub cleared: Vec<(FrameId, TrackId)>,
    /// バンクを書き換えた列。
    pub banks: BTreeSet<TrackId>,
}

fn unsupported(reason: &str) -> Error {
    Error::Invalid(format!("install_legacy_frames: {reason}"))
}

impl Document {
    /// 今のコマを現在の順序で再取り込みする。番号と配置待ちの所属を整える。
    ///
    /// 新しい入力や旧連番保管の統合は呼び出し側の手順に任せる。Rust で安全に
    /// 移せない局所の絵があれば、文書に手を加えずエラーを返す。
    pub fn reinstall_legacy_frames(&mut self) -> Result<LegacyInstall, Error> {
        let mut result = LegacyInstall::default();
        let mut edited = self.clone();
        for &tid in &self.track_order {
            let track = edited
                .tracks
                .get_mut(&tid)
                .ok_or_else(|| unsupported("列がない"))?;

            // 接続済みの内容キーと番号付きの配置待ちは、すでにバンクにある。
            // 番号を保持するだけの位置は、未登録なら最初の候補の絵を複製する。
            let original = self
                .tracks
                .get(&tid)
                .ok_or_else(|| unsupported("列がない"))?;
            for &fid in &self.frame_order {
                let position = self
                    .frame(fid)
                    .and_then(|row| row.positions.get(&tid))
                    .ok_or_else(|| unsupported("位置がない"))?;
                let Some(number) = position.number() else {
                    continue;
                };
                if original.cells.contains_key(&number) {
                    continue;
                }
                let compat = &position.compatibility;
                let local = compat
                    .local
                    .as_ref()
                    .filter(|cell| cell.image.width() != 0 && cell.image.height() != 0)
                    .ok_or_else(|| unsupported("救出する局所の絵がない"))?;
                if compat.local_share.is_some() {
                    return Err(unsupported("救出する局所の絵を共有している"));
                }
                match track.cells.entry(number) {
                    Entry::Vacant(entry) => {
                        // Python は初期画像・セル名だけを選んで新しい Cell を作るので、
                        // 原画の印は付かない。
                        entry.insert(Cell {
                            is_genga: false,
                            ..local.clone()
                        });
                        result.banks.insert(tid);
                    }
                    Entry::Occupied(mut entry) => {
                        let cell = entry.get_mut();
                        if cell.initial_image.is_none() {
                            cell.initial_image = local.initial_image.clone();
                        }
                        if cell.cell_name.is_none() {
                            cell.cell_name = local.cell_name.clone();
                        }
                    }
                }
            }

            let mut last = track
                .cells
                .last_key_value()
                .map_or(0, |(number, _)| number.get());
            for &fid in &self.frame_order {
                let position = self
                    .frame(fid)
                    .and_then(|row| row.positions.get(&tid))
                    .ok_or_else(|| unsupported("位置がない"))?;
                let compat = &position.compatibility;
                let numberless = compat.unnumbered_content;
                let clear = (position.references_bank() || position.pending)
                    && (compat.local.is_some() || compat.local_bank);
                if (clear || numberless) && compat.local_share.is_some() {
                    return Err(unsupported("局所の絵を共有している"));
                }
                // attach_track は札を一度外す。登録し直すと set_pending が一回進む。
                let delta = usize::from(position.pending) + usize::from(numberless);
                result.revisions = result
                    .revisions
                    .checked_add(delta)
                    .ok_or_else(|| unsupported("位置の変更数が上限を超えた"))?;
                if !clear && !numberless && !compat.pending_detached {
                    continue;
                }
                let row = edited
                    .sheet
                    .get_mut(&fid)
                    .ok_or_else(|| unsupported("コマがない"))?;
                let slot = Arc::make_mut(row)
                    .positions
                    .get_mut(&tid)
                    .ok_or_else(|| unsupported("位置がない"))?;
                let compat = &mut slot.compatibility;
                if numberless {
                    let cell = compat
                        .local
                        .take()
                        .ok_or_else(|| unsupported("番号を付ける局所の絵がない"))?;
                    last = last
                        .checked_add(1)
                        .ok_or_else(|| unsupported("番号が上限を超えた"))?;
                    let number = CellNumber::new(last)?;
                    track.cells.insert(number, cell);
                    result.banks.insert(tid);
                    result.moved.push((fid, tid, number));
                    slot.reference = Reference::Number(number);
                    compat.unnumbered_content = false;
                    compat.dense_import = None;
                } else if clear {
                    if compat.local.take().is_some() {
                        result.cleared.push((fid, tid));
                    }
                    compat.local_bank = false;
                }
                compat.pending_detached = false;
                result.changed.push((fid, tid));
            }
        }
        *self = edited;
        Ok(result)
    }
}
