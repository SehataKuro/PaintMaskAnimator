//! 選択範囲の中の編集（コピー・切り取り・貼り付け・消去・描画のクリップ・変形の切り出し）。
//!
//! どの編集も、書き換えた矩形と、書き換える前のその矩形の画素（[`SelectionEdit`]）を
//! 返す。呼び出し側はこれで Undo を積む（Python の `LayerRegionUndo`）。
//!
//! Python は画素を Qt の乗算済み ARGB に変えて合成し、ストレートへ戻して書く。
//! 完全に不透明か透明な画素は値が変わらないので、Rust はストレートのまま扱い、
//! 書き換えない画素には触れない。半透明の画素は、Qt では選択の外でも往復の変換で
//! 値が ±1 動くことがあるが、Rust は元の値を保つ（乗算済みの値では一致する）。

use super::{IntRect, Selection, SelectionMask};
use crate::compose::pixel::{blend, premultiply, unpremultiply};
use crate::image::{Rect, RgbaImage};
use crate::Error;

/// 1回の編集で書き換えた矩形と、書き換える前の画素（Undo 用）。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SelectionEdit {
    /// 書き換えた矩形（画像の座標）。
    pub rect: Rect,
    /// 書き換える前の `rect` のストレート RGBA。
    pub before: Vec<u8>,
}

fn full_rect(image: &RgbaImage) -> Rect {
    Rect {
        x: 0,
        y: 0,
        width: image.width(),
        height: image.height(),
    }
}

fn shift(rect: Rect, dx: usize, dy: usize, width: usize, height: usize) -> Rect {
    Rect {
        x: rect.x + dx,
        y: rect.y + dy,
        width,
        height,
    }
}

/// `rect` の画素を `mask`（`rect` と同じ大きさ）の外だけ透明にした写し
/// （`_masked_selection_source`）。`selection` が無ければそのまま写す。
pub fn masked_copy(
    image: &RgbaImage,
    selection: Option<&Selection>,
    rect: Rect,
) -> Result<RgbaImage, Error> {
    let mut pixels = image.copy_rgba_rect(rect)?;
    if let Some(selection) = selection {
        let mask = selection.mask(rect.width, rect.height, rect.x as i64, rect.y as i64);
        for (pixel, &keep) in pixels.chunks_exact_mut(4).zip(mask.bits()) {
            if keep == 0 {
                pixel.fill(0);
            }
        }
    }
    RgbaImage::from_rgba(rect.width, rect.height, &pixels)
}

/// コピー（`copy_selection`）。選択の外接矩形（選択が無ければ画像全体）を、
/// 選択の形どおりに切り抜く（外は透明）。矩形が画像と重ならなければ `None`。
pub fn copy_selection(
    image: &RgbaImage,
    selection: Option<&Selection>,
) -> Result<Option<(Rect, RgbaImage)>, Error> {
    let bounds = selection.map_or_else(|| IntRect::from(full_rect(image)), Selection::bounds);
    let Some(rect) = bounds.clip_to(image.width(), image.height()) else {
        return Ok(None);
    };
    Ok(Some((rect, masked_copy(image, selection, rect)?)))
}

/// `mask`（画像と同じ大きさ）の画素を透明にする。何も選んでいなければ `None`。
fn clear_mask(image: &mut RgbaImage, mask: &SelectionMask) -> Result<Option<SelectionEdit>, Error> {
    let Some(rect) = mask.bounds() else {
        return Ok(None);
    };
    let before = image.copy_rgba_rect(rect)?;
    let mut after = before.clone();
    let local = mask.crop(rect.width, rect.height, rect.x as i64, rect.y as i64);
    for (pixel, &selected) in after.chunks_exact_mut(4).zip(local.bits()) {
        if selected != 0 {
            pixel.fill(0);
        }
    }
    image.write_rgba(rect, &after)?;
    Ok(Some(SelectionEdit { rect, before }))
}

/// 選択範囲の中を消す（`delete_selection_contents`）。
///
/// 選んだ画素が1つも無ければ何もしないで `None`。変形中は呼ばない（Python も消さない）。
pub fn delete_selection(
    image: &mut RgbaImage,
    selection: &Selection,
) -> Result<Option<SelectionEdit>, Error> {
    let mask = selection.mask(image.width(), image.height(), 0, 0);
    clear_mask(image, &mask)
}

/// 切り取りの結果。
#[derive(Clone, Debug)]
pub struct Cut {
    /// コピーした矩形（画像の座標）。
    pub rect: Rect,
    /// クリップボードへ渡す画像（選択の外は透明）。
    pub clipboard: RgbaImage,
    /// 消した範囲。選択の中に画素が無ければ `None`（コピーはする）。
    pub edit: Option<SelectionEdit>,
}

/// 切り取り（`cut_selection`）。コピーしてから選択の中だけを消す。
/// 選択が無ければレイヤー全体をコピーして全体を消す。コピーできなければ `None`。
pub fn cut_selection(
    image: &mut RgbaImage,
    selection: Option<&Selection>,
) -> Result<Option<Cut>, Error> {
    let Some((rect, clipboard)) = copy_selection(image, selection)? else {
        return Ok(None);
    };
    let edit = match selection {
        Some(selection) => delete_selection(image, selection)?,
        None => {
            let rect = full_rect(image);
            let before = image.rgba().to_vec();
            image.write_rgba(rect, &vec![0; before.len()])?;
            Some(SelectionEdit { rect, before })
        }
    };
    Ok(Some(Cut {
        rect,
        clipboard,
        edit,
    }))
}

/// 貼り付け（`paste_clipboard`）。`clip` を選択の外接矩形の左上（選択が無ければ
/// `origin`。Python は外側の余白の内側の角）へ、ソースオーバーで重ねる。
///
/// 画像と重ならなければ `None`。
pub fn paste(
    image: &mut RgbaImage,
    clip: &RgbaImage,
    selection: Option<&Selection>,
    origin: (i64, i64),
) -> Result<Option<SelectionEdit>, Error> {
    let (x, y) = selection.map_or(origin, |selection| {
        let bounds = selection.bounds();
        (bounds.x, bounds.y)
    });
    let target = IntRect {
        x,
        y,
        width: clip.width() as i64,
        height: clip.height() as i64,
    };
    let Some(rect) = target.clip_to(image.width(), image.height()) else {
        return Ok(None);
    };
    let before = image.copy_rgba_rect(rect)?;
    let mut after = before.clone();
    let (dx, dy) = ((rect.x as i64 - x) as usize, (rect.y as i64 - y) as usize);
    let source = clip.rgba();
    for row in 0..rect.height {
        for column in 0..rect.width {
            let offset = ((row + dy) * clip.width() + column + dx) * 4;
            let src = premultiply([
                source[offset],
                source[offset + 1],
                source[offset + 2],
                source[offset + 3],
            ]);
            if src == [0; 4] {
                continue;
            }
            let index = (row * rect.width + column) * 4;
            let pixel = &mut after[index..index + 4];
            let mut dst = premultiply([pixel[0], pixel[1], pixel[2], pixel[3]]);
            blend(&mut dst, src, 255);
            pixel.copy_from_slice(&unpremultiply(dst));
        }
    }
    image.write_rgba(rect, &after)?;
    Ok(Some(SelectionEdit { rect, before }))
}

/// 描画ツールの書き込みを選択でクリップする（`apply_selection_clip` を付けた描画）。
///
/// `pixels` は `rect` の大きさのストレート RGBA（描いたあとの値）。`clip` は
/// `rect` と同じ大きさのマスク（[`Selection::clip_mask`]）で、選ばれた画素だけを書き、
/// 外は元のまま残す。`clip` が `None` なら全体を書く。書いた画素を囲む矩形と、
/// その前の画素を返す。書く画素が無ければ `None`。
pub fn clip_write(
    image: &mut RgbaImage,
    rect: Rect,
    pixels: &[u8],
    clip: Option<&SelectionMask>,
) -> Result<Option<SelectionEdit>, Error> {
    if pixels.len() != rect.width * rect.height * 4 {
        return Err(Error::Image("矩形と画素バッファの長さが一致しません"));
    }
    let Some(clip) = clip else {
        if rect.width == 0 || rect.height == 0 {
            return Ok(None);
        }
        let before = image.copy_rgba_rect(rect)?;
        image.write_rgba(rect, pixels)?;
        return Ok(Some(SelectionEdit { rect, before }));
    };
    if clip.width() != rect.width || clip.height() != rect.height {
        return Err(Error::Image("マスクと矩形の寸法が一致しません"));
    }
    let Some(local) = clip.bounds() else {
        return Ok(None);
    };
    let changed = shift(rect, local.x, local.y, local.width, local.height);
    let before = image.copy_rgba_rect(changed)?;
    let mut after = before.clone();
    for row in 0..local.height {
        for column in 0..local.width {
            if !clip.contains(local.x + column, local.y + row) {
                continue;
            }
            let source = ((local.y + row) * rect.width + local.x + column) * 4;
            let target = (row * local.width + column) * 4;
            after[target..target + 4].copy_from_slice(&pixels[source..source + 4]);
        }
    }
    image.write_rgba(changed, &after)?;
    Ok(Some(SelectionEdit {
        rect: changed,
        before,
    }))
}

/// 選択範囲の変形で切り出す矩形（`begin_selection_transform`）。
///
/// 選択の外接矩形を画像に収め、縮小で外周が欠けないよう 2 画素ずつ広げて、
/// もう一度画像に収める。外接矩形が画像と重ならなければ `None`。
pub fn transform_source_rect(selection: &Selection, width: usize, height: usize) -> Option<Rect> {
    let inside = selection.bounds().clip_to(width, height)?;
    IntRect {
        x: inside.x as i64 - 2,
        y: inside.y as i64 - 2,
        width: inside.width as i64 + 4,
        height: inside.height as i64 + 4,
    }
    .clip_to(width, height)
}

/// 変形の開始で切り出した結果。
#[derive(Clone, Debug)]
pub struct Lifted {
    /// 変形の元の画像（`rect` の大きさ。選択の外は透明）。
    pub source: RgbaImage,
    /// 切り出した矩形と、レイヤーから選択の中を消す前の画素。
    pub edit: SelectionEdit,
}

/// 選択範囲の変形を始める（`begin_selection_transform` の画素の部分）。
///
/// 切り出す矩形（[`transform_source_rect`]）の選択の中を写して変形の元にし、
/// レイヤーからは選択の中を消す。切り出せなければ `None`。
pub fn lift_selection(
    image: &mut RgbaImage,
    selection: &Selection,
) -> Result<Option<Lifted>, Error> {
    let Some(rect) = transform_source_rect(selection, image.width(), image.height()) else {
        return Ok(None);
    };
    let source = masked_copy(image, Some(selection), rect)?;
    let before = image.copy_rgba_rect(rect)?;
    let mask = selection.mask(rect.width, rect.height, rect.x as i64, rect.y as i64);
    let mut after = before.clone();
    for (pixel, &selected) in after.chunks_exact_mut(4).zip(mask.bits()) {
        if selected != 0 {
            pixel.fill(0);
        }
    }
    image.write_rgba(rect, &after)?;
    Ok(Some(Lifted {
        source,
        edit: SelectionEdit { rect, before },
    }))
}

/// 選択の中を透明にした画像全体の写し（`_cleared_selection_base`）。
/// 変形の確定で、変形した画像を重ねる下地にする。
pub fn cleared_selection(image: &RgbaImage, selection: &Selection) -> Result<RgbaImage, Error> {
    let mut result = image.clone();
    let mask = selection.mask(image.width(), image.height(), 0, 0);
    clear_mask(&mut result, &mask)?;
    Ok(result)
}
