//! 使用色の置換・削除・統合（`main_window_used_color.apply_palette_replacements`）と、
//! 同じ形の 2 枚の画像から置換色を求める処理（`register_same_image_replacements`）。
use super::{finish, pack, unpack, MaskRef, PixelEdit};
use crate::image::RgbaImage;
use crate::Error;
use std::collections::{BTreeMap, HashMap};

const WHITE: [u8; 3] = [255, 255, 255];

/// 置換表を Python 版と同じ規則で整える：元が白の組と、元と先が同じ組は除く。
/// 同じ元の色が何度もあれば、あとの組が勝つ（dict への代入と同じ）。
pub fn normalize_mapping(mapping: &[([u8; 3], [u8; 3])]) -> BTreeMap<u32, [u8; 3]> {
    let mut packed = BTreeMap::new();
    for &(source, destination) in mapping {
        if source == WHITE || source == destination {
            continue;
        }
        packed.insert(pack(&source), destination);
    }
    packed
}

/// 1 セル分の色置換。置換先が白（#FFFFFF）の画素は消しゴムとして透明にする。
///
/// `selection` が画像と同じ大きさなら、その中だけを置き換える（違う大きさなら全体）。
/// 置き換える画素が無ければ `None`。
pub fn replace_colors(
    image: &RgbaImage,
    mapping: &[([u8; 3], [u8; 3])],
    selection: Option<MaskRef<'_>>,
) -> Result<Option<PixelEdit>, Error> {
    let table = normalize_mapping(mapping);
    let (width, height) = (image.width(), image.height());
    if table.is_empty() || width == 0 || height == 0 {
        return Ok(None);
    }
    let selection = selection.filter(|mask| mask.same_size(width, height));
    let mut pixels = image.rgba().to_vec();
    let mut count = 0;
    for (index, pixel) in pixels.chunks_exact_mut(4).enumerate() {
        if pixel[3] == 0 {
            continue;
        }
        let Some(&destination) = table.get(&pack(pixel)) else {
            continue;
        };
        if selection.is_some_and(|mask| mask.data[index] == 0) {
            continue;
        }
        pixel[..3].copy_from_slice(&destination);
        if destination == WHITE {
            pixel[3] = 0;
        }
        count += 1;
    }
    if count == 0 {
        return Ok(None);
    }
    finish(image, pixels, count).map(Some)
}

/// 色削除（`apply_palette_delete`）の置換表：選んだ色（白を除く）をすべて白へ。
pub fn delete_mapping(selected: &[[u8; 3]]) -> Vec<([u8; 3], [u8; 3])> {
    let mut colours: Vec<[u8; 3]> = selected.iter().copied().filter(|&c| c != WHITE).collect();
    colours.sort_unstable();
    colours.dedup();
    colours.into_iter().map(|colour| (colour, WHITE)).collect()
}

/// 色統合（`apply_palette_merge`）の置換表：選んだ色を親の色へ。親が白なら `None`。
pub fn merge_mapping(parent: [u8; 3], selected: &[[u8; 3]]) -> Option<Vec<([u8; 3], [u8; 3])>> {
    if parent == WHITE {
        return None;
    }
    let mut colours: Vec<[u8; 3]> = selected
        .iter()
        .copied()
        .filter(|&c| c != parent && c != WHITE)
        .collect();
    colours.sort_unstable();
    colours.dedup();
    Some(colours.into_iter().map(|colour| (colour, parent)).collect())
}

/// 同じ大きさ・同じ透明度の 2 枚で、元の各色が先の 1 色にだけ対応するときの置換表
/// （`_replacement_mapping_for_aligned_images`）。元の色の昇順。対応しなければ `None`。
pub fn aligned_replacement_mapping(
    source: &RgbaImage,
    target: &RgbaImage,
) -> Option<Vec<([u8; 3], [u8; 3])>> {
    if source.width() != target.width()
        || source.height() != target.height()
        || source.rgba().is_empty()
    {
        return None;
    }
    let mut mapping: HashMap<u32, u32> = HashMap::new();
    let mut any = false;
    for (a, b) in source
        .rgba()
        .chunks_exact(4)
        .zip(target.rgba().chunks_exact(4))
    {
        if a[3] != b[3] {
            return None;
        }
    }
    for (a, b) in source
        .rgba()
        .chunks_exact(4)
        .zip(target.rgba().chunks_exact(4))
    {
        if a[3] == 0 {
            continue;
        }
        any = true;
        let (from, to) = (pack(a), pack(b));
        match mapping.get(&from) {
            Some(&known) if known != to => return None,
            _ => {
                mapping.insert(from, to);
            }
        }
    }
    if !any {
        return None;
    }
    let mut pairs: Vec<(u32, u32)> = mapping.into_iter().collect();
    pairs.sort_unstable();
    Some(
        pairs
            .into_iter()
            .map(|(from, to)| (unpack(from), unpack(to)))
            .collect(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::image_ops::test_support::image;

    #[test]
    fn replace_respects_selection_and_eraser() {
        let source = image(3, 1, &[[1, 2, 3, 255], [1, 2, 3, 255], [9, 9, 9, 255]]);
        let mapping = [([1, 2, 3], [4, 5, 6]), ([9, 9, 9], WHITE)];
        let edit = replace_colors(&source, &mapping, None).unwrap().unwrap();
        assert_eq!(edit.pixels, 3);
        assert_eq!(edit.image.rgba(), &[4, 5, 6, 255, 4, 5, 6, 255, 0, 0, 0, 0]);
        let mask = [0u8, 1, 0];
        let selection = MaskRef {
            data: &mask,
            width: 3,
            height: 1,
        };
        let edit = replace_colors(&source, &mapping, Some(selection))
            .unwrap()
            .unwrap();
        assert_eq!(edit.pixels, 1);
        assert_eq!(&edit.image.rgba()[..8], &[1, 2, 3, 255, 4, 5, 6, 255]);
        assert!(replace_colors(&source, &[([7, 7, 7], [1, 1, 1])], None)
            .unwrap()
            .is_none());
    }

    #[test]
    fn mappings() {
        assert_eq!(
            delete_mapping(&[[1, 1, 1], WHITE, [1, 1, 1]]),
            [([1, 1, 1], WHITE)]
        );
        assert_eq!(merge_mapping(WHITE, &[[1, 1, 1]]), None);
        assert_eq!(
            merge_mapping([5, 5, 5], &[[5, 5, 5], [1, 1, 1]]).unwrap(),
            [([1, 1, 1], [5, 5, 5])]
        );
    }

    #[test]
    fn aligned_mapping_requires_consistency() {
        let a = image(2, 1, &[[1, 1, 1, 255], [2, 2, 2, 255]]);
        let b = image(2, 1, &[[3, 3, 3, 255], [4, 4, 4, 255]]);
        assert_eq!(
            aligned_replacement_mapping(&a, &b).unwrap(),
            [([1, 1, 1], [3, 3, 3]), ([2, 2, 2], [4, 4, 4])]
        );
        let c = image(2, 1, &[[1, 1, 1, 255], [1, 1, 1, 255]]);
        assert!(aligned_replacement_mapping(&c, &b).is_none());
        let d = image(2, 1, &[[1, 1, 1, 255], [1, 1, 1, 0]]);
        assert!(aligned_replacement_mapping(&d, &b).is_none());
    }
}
