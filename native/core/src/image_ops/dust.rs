//! ゴミ取りツール（ゴミ取り・塗り抜け・線周りのノイズ）。
//!
//! Python 版 `main_window_line_ops.remove_dust_fill_surrounding` の 1 セル分の処理
//! （`despeckle` op）と同じ画素を返す。白（#FFFFFF）と透明は「白」とみなす。
use super::{components4, finish, pack, unpack, PixelEdit};
use crate::image::RgbaImage;
use crate::Error;
use std::collections::{BTreeSet, HashMap};

/// ゴミ取りツールの処理の種類（`dust_mode` の値）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DustMode {
    /// ゴミ取り（`"despeckle"`）：小さな色の塊を白にする。
    Despeckle,
    /// 塗り抜け（`"fill_holes"`）：外につながらない小さな白をまわりの色で埋める。
    FillHoles,
    /// 線周りのノイズ（`"line_noise"`）：線の近くの孤立した色を多数決で寄せる。
    LineNoise,
}

/// ゴミ取りツールの設定。
#[derive(Clone, Copy, Debug)]
pub struct DustOptions<'a> {
    /// 処理の種類。
    pub mode: DustMode,
    /// 適用サイズ（画素）。1〜100 に丸める。線周りのノイズでは線からの距離（1〜8）。
    pub max_area: usize,
    /// 「選択色を対象」のときの使用色（白は除く）。`None` なら全色が対象。
    pub selected: Option<&'a [[u8; 3]]>,
}

fn pseudo_white(pixel: &[u8]) -> bool {
    pixel[3] == 0 || pixel[..3] == [255, 255, 255]
}

/// 1 セル分のゴミ取り。変える画素が無ければ `None`。
pub fn remove_dust(
    image: &RgbaImage,
    options: &DustOptions<'_>,
) -> Result<Option<PixelEdit>, Error> {
    let (width, height) = (image.width(), image.height());
    if width == 0 || height == 0 {
        return Ok(None);
    }
    let max_area = options.max_area.clamp(1, 100);
    let mut pixels = image.rgba().to_vec();
    let white: Vec<bool> = pixels.chunks_exact(4).map(pseudo_white).collect();
    let changed = match options.mode {
        DustMode::FillHoles => fill_holes(
            &mut pixels,
            &white,
            width,
            height,
            max_area,
            options.selected,
        ),
        DustMode::LineNoise => {
            let radius = max_area.min(8);
            let (cleaned, changed) =
                crate::line_noise::remove_line_noise(&pixels, width, height, 110, radius, 5, 0);
            if changed > 0 {
                pixels = cleaned;
            }
            changed
        }
        DustMode::Despeckle => despeckle(
            &mut pixels,
            &white,
            width,
            height,
            max_area,
            options.selected,
        ),
    };
    if changed == 0 {
        return Ok(None);
    }
    finish(image, pixels, changed).map(Some)
}

fn despeckle(
    pixels: &mut [u8],
    white: &[bool],
    width: usize,
    height: usize,
    max_area: usize,
    selected: Option<&[[u8; 3]]>,
) -> usize {
    let mut removal = vec![false; white.len()];
    match selected {
        Some(colours) => {
            // 選択色ごとに独立に塊を見る（青 1px が黒に接していても青は 1px）。
            for colour in colours {
                let mask: Vec<bool> = pixels
                    .chunks_exact(4)
                    .zip(white)
                    .map(|(pixel, &white)| !white && pixel[..3] == colour[..])
                    .collect();
                for (component, _edge) in components4(&mask, width, height) {
                    if component.len() <= max_area {
                        for index in component {
                            removal[index] = true;
                        }
                    }
                }
            }
        }
        None => {
            let foreground: Vec<bool> = white.iter().map(|&white| !white).collect();
            for (component, edge) in components4(&foreground, width, height) {
                if !edge && component.len() <= max_area {
                    for index in component {
                        removal[index] = true;
                    }
                }
            }
        }
    }
    let mut changed = 0;
    for (pixel, _) in pixels
        .chunks_exact_mut(4)
        .zip(&removal)
        .filter(|(_, &remove)| remove)
    {
        pixel.copy_from_slice(&[255, 255, 255, 255]);
        changed += 1;
    }
    changed
}

fn fill_holes(
    pixels: &mut [u8],
    white: &[bool],
    width: usize,
    height: usize,
    max_area: usize,
    selected: Option<&[[u8; 3]]>,
) -> usize {
    // 外周の白から 4 近傍でつながる白が「外」。
    let seeds = super::border_seeds(|index| white[index], width, height);
    let passable: Vec<u8> = white.iter().map(|&white| u8::from(white)).collect();
    let outside = if seeds.is_empty() {
        vec![0; white.len()]
    } else {
        crate::regions::flood_fill(&passable, width, height, &seeds)
    };
    let holes: Vec<bool> = white
        .iter()
        .zip(&outside)
        .map(|(&white, &outside)| white && outside == 0)
        .collect();
    let mut changed = 0;
    for (component, _edge) in components4(&holes, width, height) {
        if component.len() > max_area {
            continue;
        }
        // 塊のまわり 8 画素のうち白でない画素（重複なし）。
        let mut border = BTreeSet::new();
        for &index in &component {
            let (x, y) = (index % width, index / width);
            for ny in y.saturating_sub(1)..(y + 2).min(height) {
                for nx in x.saturating_sub(1)..(x + 2).min(width) {
                    let next = ny * width + nx;
                    if next != index && !white[next] {
                        border.insert(next);
                    }
                }
            }
        }
        let mut counts: HashMap<u32, usize> = HashMap::new();
        for &index in &border {
            let rgb = &pixels[index * 4..index * 4 + 3];
            if let Some(colours) = selected {
                if !colours.iter().any(|colour| rgb == colour) {
                    continue;
                }
            }
            *counts.entry(pack(rgb)).or_default() += 1;
        }
        // np.unique の昇順で最初の最多の色。
        let Some(fill) = counts
            .iter()
            .max_by(|a, b| a.1.cmp(b.1).then(b.0.cmp(a.0)))
            .map(|(&value, _)| unpack(value))
        else {
            continue;
        };
        for &index in &component {
            pixels[index * 4..index * 4 + 3].copy_from_slice(&fill);
            pixels[index * 4 + 3] = 255;
        }
        changed += component.len();
    }
    changed
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::image_ops::test_support::image;

    const W: [u8; 4] = [255, 255, 255, 255];
    const T: [u8; 4] = [0, 0, 0, 0];
    const K: [u8; 4] = [0, 0, 0, 255];
    const R: [u8; 4] = [255, 0, 0, 255];

    fn options(mode: DustMode, max_area: usize) -> DustOptions<'static> {
        DustOptions {
            mode,
            max_area,
            selected: None,
        }
    }

    #[test]
    fn despeckle_removes_small_inner_blobs_only() {
        #[rustfmt::skip]
        let source = image(4, 4, &[
            K, T, T, T,
            T, T, R, T,
            T, T, T, T,
            T, T, T, T,
        ]);
        let edit = remove_dust(&source, &options(DustMode::Despeckle, 1))
            .unwrap()
            .unwrap();
        assert_eq!(edit.pixels, 1);
        assert_eq!(&edit.image.rgba()[(4 + 2) * 4..(4 + 3) * 4], &W);
        // 外周に触れる黒は残す。
        assert_eq!(&edit.image.rgba()[..4], &K);
        assert!(remove_dust(&source, &options(DustMode::Despeckle, 1))
            .unwrap()
            .is_some());
    }

    #[test]
    fn fill_holes_uses_most_common_border_colour() {
        #[rustfmt::skip]
        let source = image(3, 3, &[
            K, K, K,
            K, T, R,
            K, R, R,
        ]);
        let edit = remove_dust(&source, &options(DustMode::FillHoles, 4))
            .unwrap()
            .unwrap();
        assert_eq!(edit.pixels, 1);
        assert_eq!(&edit.image.rgba()[16..20], &K);
        let selected = [[255, 0, 0]];
        let only_red = DustOptions {
            selected: Some(&selected),
            ..options(DustMode::FillHoles, 4)
        };
        let edit = remove_dust(&source, &only_red).unwrap().unwrap();
        assert_eq!(&edit.image.rgba()[16..20], &R);
    }

    #[test]
    fn nothing_to_do_returns_none() {
        let source = image(2, 2, &[W, W, W, W]);
        for mode in [
            DustMode::Despeckle,
            DustMode::FillHoles,
            DustMode::LineNoise,
        ] {
            assert!(remove_dust(&source, &options(mode, 5)).unwrap().is_none());
        }
    }
}
