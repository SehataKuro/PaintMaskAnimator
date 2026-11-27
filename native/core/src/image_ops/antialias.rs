//! デジタル TP のアンチエイリアスを、いまある色面の色だけを残して取り除く（#63）。
//!
//! Python 版 `digital_antialias` と同じ規則・同じ倍精度の計算で、残す色と寄せる画素を
//! 決める。色数は決めない。
use super::{pack, unpack};
use std::collections::{BTreeMap, BTreeSet};

/// 平らな所がない色を「線の色」として残すのに要る画素数。
const MIN_LINE_PIXELS: usize = 4;
/// 線分の途中とみなす範囲（両端からこの割合より内側）。
const MIX_MARGIN: f64 = 0.08;
/// 線分からこれだけ離れていなければ「2 色を混ぜた色」とみなす（RGB の距離）。
const MIX_DISTANCE: f64 = 8.0;
/// 平らでない色の候補は、多い順にこの数まで調べる。
const MAX_CANDIDATES: usize = 256;
/// 半透明の画素は、この alpha 以上なら不透明にして色を寄せ、未満なら透明にする。
const ALPHA_THRESHOLD: u8 = 128;
/// 線の色は、同じくらい近い塗りの色に負ける（距離の上乗せ）。
const LINE_PENALTY: f64 = 1.0;

const NEIGHBOURS: [(isize, isize); 8] = [
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
];

fn flat_values(rgba: &[u8], width: usize, height: usize) -> BTreeSet<u32> {
    let mut flat = BTreeSet::new();
    if width < 3 || height < 3 {
        return flat;
    }
    let pixel = |x: usize, y: usize| &rgba[(y * width + x) * 4..(y * width + x) * 4 + 4];
    for y in 1..height - 1 {
        for x in 1..width - 1 {
            let centre = pixel(x, y);
            if centre[3] != 255 {
                continue;
            }
            let value = pack(centre);
            let same = NEIGHBOURS.iter().all(|&(dy, dx)| {
                let other = pixel((x as isize + dx) as usize, (y as isize + dy) as usize);
                other[3] == 255 && pack(other) == value
            });
            if same {
                flat.insert(value);
            }
        }
    }
    flat
}

fn sum3(values: [f64; 3]) -> f64 {
    values[0] + values[1] + values[2]
}

fn is_mix(colour: [f64; 3], palette: &[[f64; 3]]) -> bool {
    if palette.len() < 2 {
        return false;
    }
    for a in palette {
        for b in palette {
            let direction = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
            let length2 = sum3(direction.map(|d| d * d));
            if length2 <= 0.0 {
                continue;
            }
            let t = sum3([
                (colour[0] - a[0]) * direction[0],
                (colour[1] - a[1]) * direction[1],
                (colour[2] - a[2]) * direction[2],
            ]) / length2;
            if !(t > MIX_MARGIN && t < 1.0 - MIX_MARGIN) {
                continue;
            }
            let nearest = [
                a[0] + t * direction[0],
                a[1] + t * direction[1],
                a[2] + t * direction[2],
            ];
            let distance = sum3([
                (colour[0] - nearest[0]).powi(2),
                (colour[1] - nearest[1]).powi(2),
                (colour[2] - nearest[2]).powi(2),
            ])
            .sqrt();
            if distance <= MIX_DISTANCE {
                return true;
            }
        }
    }
    false
}

fn as_f64(value: u32) -> [f64; 3] {
    unpack(value).map(f64::from)
}

/// 残す色（`r<<16|g<<8|b` の昇順）。平らな色と、ほかの色を混ぜた色ではない線の色。
pub fn surface_palette(rgba: &[u8], width: usize, height: usize) -> Vec<u32> {
    let flat = flat_values(rgba, width, height);
    let mut counts: BTreeMap<u32, usize> = BTreeMap::new();
    for pixel in rgba.chunks_exact(4).filter(|pixel| pixel[3] == 255) {
        *counts.entry(pack(pixel)).or_default() += 1;
    }
    let mut candidates: Vec<(u32, usize)> = counts
        .into_iter()
        .filter(|(value, count)| !flat.contains(value) && *count >= MIN_LINE_PIXELS)
        .collect();
    // 多い順（同数なら値の昇順）。
    candidates.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    candidates.truncate(MAX_CANDIDATES);
    if candidates.is_empty() {
        return flat.into_iter().collect();
    }
    let flat_list: Vec<u32> = flat.iter().copied().collect();
    let pool: Vec<[f64; 3]> = flat_list
        .iter()
        .chain(candidates.iter().map(|(value, _)| value))
        .map(|&value| as_f64(value))
        .collect();
    let mut accepted = flat;
    for (index, &(value, _)) in candidates.iter().enumerate() {
        let skip = flat_list.len() + index;
        let others: Vec<[f64; 3]> = pool
            .iter()
            .enumerate()
            .filter(|&(i, _)| i != skip)
            .map(|(_, &colour)| colour)
            .collect();
        if !is_mix(as_f64(value), &others) {
            accepted.insert(value);
        }
    }
    accepted.into_iter().collect()
}

fn antialias_mask(rgba: &[u8], palette: &[u32]) -> Vec<bool> {
    rgba.chunks_exact(4)
        .map(|pixel| {
            let semi = pixel[3] > 0 && pixel[3] < 255;
            let stray = pixel[3] == 255 && palette.binary_search(&pack(pixel)).is_err();
            semi || stray
        })
        .collect()
}

/// アンチエイリアスとみなす画素の数（0 なら 2 値、`count_antialias`）。
pub fn count_antialias(rgba: &[u8], width: usize, height: usize) -> usize {
    let palette = surface_palette(rgba, width, height);
    antialias_mask(rgba, &palette)
        .iter()
        .filter(|&&v| v)
        .count()
}

fn line_colour(rgb: [f64; 3]) -> bool {
    super::line_tidy::is_line_colour(rgb.map(|v| v as u8))
}

/// アンチエイリアスを取り除いた RGBA と、書き換えた画素の数（`remove_antialias`）。
///
/// 寄せる画素は、まわり 8 画素の残す色のうち自分にいちばん近い色へ寄せ、決まるまで
/// 繰り返す。まわりに残す色が無い画素は、残す色のうちいちばん近い色にする。
pub fn remove_antialias(rgba: &[u8], width: usize, height: usize) -> (Vec<u8>, usize) {
    let palette = surface_palette(rgba, width, height);
    let mask = antialias_mask(rgba, &palette);
    let mut out = rgba.to_vec();
    let changed = mask.iter().filter(|&&v| v).count();
    if changed == 0 {
        return (out, 0);
    }
    let alpha = |index: usize| rgba[index * 4 + 3];
    let faint: Vec<bool> = (0..mask.len())
        .map(|index| mask[index] && alpha(index) < ALPHA_THRESHOLD)
        .collect();
    for (index, _) in faint.iter().enumerate().filter(|(_, &v)| v) {
        out[index * 4..index * 4 + 4].fill(0);
    }
    let mut pending: Vec<bool> = (0..mask.len()).map(|i| mask[i] && !faint[i]).collect();
    // 決まった画素の色（-1 は未定・透明）。
    let mut labels: Vec<i64> = (0..mask.len())
        .map(|index| {
            if alpha(index) == 255 && !mask[index] {
                i64::from(pack(&rgba[index * 4..index * 4 + 3]))
            } else {
                -1
            }
        })
        .collect();
    let own = |index: usize| {
        [
            f64::from(rgba[index * 4]),
            f64::from(rgba[index * 4 + 1]),
            f64::from(rgba[index * 4 + 2]),
        ]
    };
    while pending.iter().any(|&v| v) {
        let mut best = vec![f64::INFINITY; mask.len()];
        let mut choice = vec![-1i64; mask.len()];
        for &(dy, dx) in &NEIGHBOURS {
            for y in 0..height {
                for x in 0..width {
                    let index = y * width + x;
                    if !pending[index] {
                        continue;
                    }
                    let (ny, nx) = (y as isize + dy, x as isize + dx);
                    if ny < 0 || nx < 0 || ny as usize >= height || nx as usize >= width {
                        continue;
                    }
                    let neighbour = labels[ny as usize * width + nx as usize];
                    if neighbour < 0 {
                        continue;
                    }
                    let colour = as_f64(neighbour as u32);
                    let mine = own(index);
                    let mut distance = sum3([
                        (mine[0] - colour[0]).powi(2),
                        (mine[1] - colour[1]).powi(2),
                        (mine[2] - colour[2]).powi(2),
                    ])
                    .sqrt();
                    if line_colour(colour) {
                        distance += LINE_PENALTY;
                    }
                    if distance < best[index] {
                        best[index] = distance;
                        choice[index] = neighbour;
                    }
                }
            }
        }
        let mut decided = false;
        for index in 0..mask.len() {
            if pending[index] && choice[index] >= 0 {
                labels[index] = choice[index];
                pending[index] = false;
                decided = true;
            }
        }
        if !decided {
            break;
        }
    }
    if !palette.is_empty() {
        // まわりに残す色が 1 つもない画素（半透明だけの塊など）は、いちばん近い色へ。
        let colours: Vec<[f64; 3]> = palette.iter().map(|&value| as_f64(value)).collect();
        for index in 0..mask.len() {
            if !pending[index] {
                continue;
            }
            let mine = own(index);
            let mut best = (f64::INFINITY, 0usize);
            for (i, colour) in colours.iter().enumerate() {
                let distance = sum3([
                    (mine[0] - colour[0]).powi(2),
                    (mine[1] - colour[1]).powi(2),
                    (mine[2] - colour[2]).powi(2),
                ]);
                if distance < best.0 {
                    best = (distance, i);
                }
            }
            labels[index] = i64::from(palette[best.1]);
            pending[index] = false;
        }
    }
    for index in 0..mask.len() {
        if mask[index] && !faint[index] && labels[index] >= 0 {
            out[index * 4..index * 4 + 3].copy_from_slice(&unpack(labels[index] as u32));
            out[index * 4 + 3] = 255;
        }
    }
    (out, changed)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fill(width: usize, height: usize, colour: [u8; 4]) -> Vec<u8> {
        colour.repeat(width * height)
    }

    #[test]
    fn mixed_boundary_is_pulled_to_neighbour() {
        let (w, h) = (8, 4);
        let mut rgba = fill(w, h, [255, 255, 255, 255]);
        for y in 0..h {
            for x in 4..w {
                rgba[(y * w + x) * 4..(y * w + x) * 4 + 4].copy_from_slice(&[0, 0, 255, 255]);
            }
            rgba[(y * w + 4) * 4..(y * w + 4) * 4 + 4].copy_from_slice(&[128, 128, 255, 255]);
        }
        assert_eq!(count_antialias(&rgba, w, h), 4);
        let (out, changed) = remove_antialias(&rgba, w, h);
        assert_eq!(changed, 4);
        assert!(out
            .chunks_exact(4)
            .all(|p| p == [255, 255, 255, 255] || p == [0, 0, 255, 255]));
    }

    #[test]
    fn binary_image_is_unchanged() {
        let rgba = fill(5, 5, [10, 20, 30, 255]);
        assert_eq!(remove_antialias(&rgba, 5, 5), (rgba.clone(), 0));
        // 半分より薄い半透明は透明にする。
        let mut faint = rgba.clone();
        faint[12 * 4 + 3] = 40;
        let (out, changed) = remove_antialias(&faint, 5, 5);
        assert_eq!(changed, 1);
        assert_eq!(&out[48..52], &[0, 0, 0, 0]);
    }
}
