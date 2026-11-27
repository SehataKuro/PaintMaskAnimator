//! 自動塗り（Python の `main_window_auto_paint.AutoPaintController.run` と、そこから呼ぶ
//! `auto_paint.plan_auto_paint` のバケツの経路）。
//!
//! 見本のセルから前後へ1セルずつ、同じ場所の色を選んで通常のバケツで塗り進める。
//! 一通り塗ったら迷った所の少ないセルを見本に加えて塗り直す。結果は Python の CPU の
//! 経路と同じ画素になる。GPU の経路と、アプリが使わない手塗り・流れの経路は持たない。
mod alignment;
mod cell;
mod flow;
mod plan;
mod session;
mod trace;
mod tracker;
mod work;

pub use cell::{boundary_mask, line_only, white_as_unpainted};
pub use plan::{apply_writes, paint_layer, plan_auto_paint, AutoPlan, LayerOutcome, Write};
pub use session::AutoBucketOptions;

use crate::reference_check::Bounds;
use crate::reference_scan::Finding;
use crate::reference_scan::Rgb;

/// 1つの塗り：色・外接矩形（排他）・矩形の中のマスク（0/1、行優先）。
#[derive(Clone, Debug, PartialEq)]
pub struct Paint {
    pub rgb: Rgb,
    pub bounds: Bounds,
    pub mask: Vec<u8>,
}

/// 1セル分の塗り替え（Python の `auto_paint.CellPaint`）。
#[derive(Clone, Debug, Default, PartialEq)]
pub struct CellPaint {
    pub paints: Vec<Paint>,
    /// バケツを実行した位置と色。
    pub clicks: Vec<(usize, usize, Rgb)>,
    /// 塗った部分の数、決められなかった部分の数。
    pub filled: usize,
    pub unsure: usize,
    /// 塗り替えた色トレス線の画素数。
    pub line_pixels: usize,
    /// 調べた部分の数。
    pub checked: usize,
    /// 人が確かめたほうがよい所。
    pub notes: Vec<Finding>,
    /// 決められずに残した色トレス線と、透明のまま残した部分。
    pub undecided: Vec<(Bounds, Vec<u8>)>,
    pub unpainted: Vec<(Bounds, Vec<u8>)>,
}

impl CellPaint {
    /// 迷った割合。
    pub fn unsure_share(&self) -> f64 {
        if self.checked == 0 {
            0.0
        } else {
            self.unsure as f64 / self.checked as f64
        }
    }
}

/// `apply_paint`：塗りを当てた複製（次のセルの手本にする）。
pub fn apply_paint(pixels: &[u8], width: usize, paint: &CellPaint) -> Vec<u8> {
    let mut out = pixels.to_vec();
    for item in &paint.paints {
        let (x0, y0, x1, _) = item.bounds;
        let lw = x1 - x0;
        for (i, &v) in item.mask.iter().enumerate() {
            if v != 0 {
                let at = ((i / lw + y0) * width + i % lw + x0) * 4;
                out[at..at + 3].copy_from_slice(&item.rgb);
                out[at + 3] = 255;
            }
        }
    }
    out
}
