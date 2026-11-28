//! 画像の色から選択を作る（自動選択・色で選択・描画範囲の自動選択）。

use super::{Selection, SelectionMask};
use crate::image::{Rect, RgbaImage};
use crate::regions;

/// 透明（アルファ 0）か純白なら真。白は消しゴムの色なので背景として扱う。
fn is_pseudo_background(pixel: &[u8]) -> bool {
    pixel[3] == 0 || (pixel[0] == 255 && pixel[1] == 255 && pixel[2] == 255)
}

/// 不透明（アルファ 1 以上）で RGB が `rgb` の画素を選ぶ（「この色を選択範囲にする」）。
pub fn color_mask(image: &RgbaImage, rgb: [u8; 3]) -> SelectionMask {
    let bits: Vec<u8> = image
        .rgba()
        .chunks_exact(4)
        .map(|pixel| u8::from(pixel[3] > 0 && pixel[..3] == rgb))
        .collect();
    SelectionMask::from_bits(image.width(), image.height(), &bits)
        .unwrap_or_else(|_| SelectionMask::empty(image.width(), image.height()))
}

/// 自動選択の設定（ツールパネルのバケツの設定を使う）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ContiguousOptions {
    /// つながった領域だけを選ぶ（「隣接」）。偽なら同じ色を画像全体から選ぶ。
    pub adjacent: bool,
    /// 隙間を閉じる幅（画素）。`None` なら閉じない。`adjacent` のときだけ使う。
    pub close_gap: Option<usize>,
    /// 画像の端まで開いた領域は選ばない（「閉じた領域だけ」）。`adjacent` のときだけ使う。
    pub require_closed: bool,
}

/// 自動選択の結果。
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ContiguousResult {
    /// 押した位置が画像の外。
    OutOfBounds,
    /// 領域が画像の端まで開いていたので選ばなかった（`require_closed` のとき）。
    OpenToEdge,
    /// 選んだ領域（画像と同じ大きさ）。
    Region(SelectionMask),
}

/// バケツと同じ判定で、(`x`, `y`) から選ぶ領域を求める（`auto_select_region`）。
///
/// 押した画素が背景（透明か純白）なら背景をまとめて、そうでなければ同じ RGB の
/// 不透明な画素を対象にする。今の選択との足し引きは [`Selection::combine`] で行う。
pub fn contiguous_region(
    image: &RgbaImage,
    x: i64,
    y: i64,
    options: ContiguousOptions,
) -> ContiguousResult {
    let (width, height) = (image.width(), image.height());
    if x < 0 || y < 0 || x as usize >= width || y as usize >= height {
        return ContiguousResult::OutOfBounds;
    }
    let (sx, sy) = (x as usize, y as usize);
    let rgba = image.rgba();
    let seed = sy * width + sx;
    let target = &rgba[seed * 4..seed * 4 + 4];
    let target_mask: Vec<u8> = if is_pseudo_background(target) {
        rgba.chunks_exact(4)
            .map(|pixel| u8::from(is_pseudo_background(pixel)))
            .collect()
    } else {
        rgba.chunks_exact(4)
            .map(|pixel| u8::from(pixel[3] > 0 && pixel[..3] == target[..3]))
            .collect()
    };

    let region = if options.adjacent {
        let mut passable = target_mask.clone();
        if let Some(gap) = options.close_gap {
            let radius = gap.max(1);
            let boundary: Vec<u8> = target_mask.iter().map(|&value| 1 - value).collect();
            let expanded = regions::dilate(&boundary, width, height, radius);
            let virtual_boundary = regions::erode(&expanded, width, height, radius);
            for (cell, wall) in passable.iter_mut().zip(virtual_boundary) {
                *cell &= 1 - wall;
            }
            passable[seed] = target_mask[seed];
        }
        let region = regions::flood_fill(&passable, width, height, &[(x, y)]);
        if options.require_closed && touches_edge(&region, width, height) {
            return ContiguousResult::OpenToEdge;
        }
        region
    } else {
        target_mask
    };
    match SelectionMask::from_bits(width, height, &region) {
        Ok(mask) => ContiguousResult::Region(mask),
        Err(_) => ContiguousResult::Region(SelectionMask::empty(width, height)),
    }
}

fn touches_edge(region: &[u8], width: usize, height: usize) -> bool {
    let row = |y: usize| &region[y * width..(y + 1) * width];
    row(0).iter().any(|&v| v != 0)
        || row(height - 1).iter().any(|&v| v != 0)
        || (0..height).any(|y| region[y * width] != 0 || region[y * width + width - 1] != 0)
}

/// 不透明な画素（灰色も含む）をすべて囲む矩形の選択（`auto_select_used_area`）。
/// 不透明な画素が無ければ `None`。
pub fn used_area(image: &RgbaImage) -> Option<Selection> {
    let bits: Vec<u8> = image
        .rgba()
        .chunks_exact(4)
        .map(|pixel| u8::from(pixel[3] > 0))
        .collect();
    let mask = SelectionMask::from_bits(image.width(), image.height(), &bits).ok()?;
    let bounds: Rect = mask.bounds()?;
    Some(Selection::from_rect(bounds))
}
