//! Qt に依存しない色の処理。
use crate::Error;

/// `np.unique(pixels)[:limit]` と同じ、24 ビットの色を値の昇順で返す。
/// 入力はリトルエンディアンの u32。
pub fn unique_colors(pixels: &[u8], limit: usize) -> Result<Vec<u32>, Error> {
    if !pixels.len().is_multiple_of(4) {
        return Err(Error::Invalid("expected packed u32 pixels".into()));
    }
    if limit == 0 || pixels.is_empty() {
        return Ok(Vec::new());
    }

    // 24 ビットのビット集合なら、色が繰り返し現れても検索は定数時間。
    const WORDS_PER_BLOCK: usize = 64;
    const WORD_COUNT: usize = (1 << 24) / 64;
    let mut seen = vec![0u64; WORD_COUNT];
    // 小画像では、現れた色があるブロックだけを走査する。
    let track_blocks = pixels.len() <= 65_536 * 4;
    let mut blocks =
        [if track_blocks { 0u64 } else { u64::MAX }; WORD_COUNT / WORDS_PER_BLOCK / 64];
    let mut mark_color = |bytes| {
        let value = u32::from_le_bytes(bytes);
        if value > 0xffffff {
            return Err(Error::Invalid("expected 24-bit RGB".into()));
        }
        let word = value as usize / 64;
        seen[word] |= 1u64 << (value % 64);
        Ok(word)
    };
    let pixels = pixels.as_chunks::<4>().0;
    if track_blocks {
        let mut previous = None;
        for &pixel in pixels {
            if previous == Some(pixel) {
                continue;
            }
            let block = mark_color(pixel)? / WORDS_PER_BLOCK;
            blocks[block / 64] |= 1u64 << (block % 64);
            previous = Some(pixel);
        }
    } else {
        // まとまって同じ色が並ぶときは、同じビットを 8 回更新する必要はない。
        let (groups, tail) = pixels.as_chunks::<8>();
        for group in groups {
            if *group == [group[0]; 8] {
                mark_color(group[0])?;
            } else {
                for &pixel in group {
                    mark_color(pixel)?;
                }
            }
        }
        for &pixel in tail {
            mark_color(pixel)?;
        }
    }
    let mut colors = Vec::new();
    for (block, words) in seen.chunks_exact(WORDS_PER_BLOCK).enumerate() {
        if blocks[block / 64] & (1u64 << (block % 64)) == 0 {
            continue;
        }
        for (index, mut word) in words.iter().copied().enumerate() {
            let word_start = (block * WORDS_PER_BLOCK + index) * 64;
            while word != 0 {
                colors.push((word_start + word.trailing_zeros() as usize) as u32);
                if colors.len() == limit {
                    return Ok(colors);
                }
                word &= word - 1;
            }
        }
    }
    Ok(colors)
}

#[inline]
fn channel_difference<const N: usize>(left: [u8; N], right: [u8; N]) -> u8 {
    left[0]
        .abs_diff(right[0])
        .max(left[1].abs_diff(right[1]))
        .max(left[2].abs_diff(right[2]))
}

/// `colors.local_color_variation_numpy` と同じ、各 RGB8 画素の上下左右との最大差を 1 バイトずつ返す。
pub fn local_color_variation(rgb: &[u8], width: usize, height: usize) -> Result<Vec<u8>, Error> {
    let (pixels, remainder) = rgb.as_chunks::<3>();
    if !remainder.is_empty() || width.checked_mul(height) != Some(pixels.len()) {
        return Err(Error::Invalid(
            "expected width × height packed RGB pixels".into(),
        ));
    }
    Ok(local_color_variation_rgb(pixels, width))
}

// 画素数が行幅の倍数であることは呼び出し側で確認する。
pub(crate) fn local_color_variation_rgb<const N: usize>(
    pixels: &[[u8; N]],
    width: usize,
) -> Vec<u8> {
    let mut variation = vec![0; pixels.len()];
    if pixels.is_empty() {
        return variation;
    }

    for (source, output) in pixels
        .chunks_exact(width)
        .zip(variation.chunks_exact_mut(width))
    {
        for (x, pair) in source.windows(2).enumerate() {
            let difference = channel_difference(pair[0], pair[1]);
            output[x] = output[x].max(difference);
            output[x + 1] = difference;
        }
    }
    for (index, (&upper, &lower)) in pixels.iter().zip(&pixels[width..]).enumerate() {
        let difference = channel_difference(upper, lower);
        variation[index] = variation[index].max(difference);
        variation[index + width] = variation[index + width].max(difference);
    }
    variation
}

#[derive(Clone, Copy)]
pub(crate) struct ColorCount {
    pub(crate) rgb: [u8; 3],
    pub(crate) count: usize,
}

pub(crate) fn sorted_color_counts(sample: &[[u8; 3]]) -> Vec<ColorCount> {
    let mut sorted = sample.to_vec();
    sorted.sort_unstable();
    let mut colors = Vec::new();
    for run in sorted.chunk_by(|left, right| left == right) {
        colors.push(ColorCount {
            rgb: run[0],
            count: run.len(),
        });
    }
    colors
}

struct ColorBox<'a> {
    colors: &'a mut [ColorCount],
    axis: usize,
    population: u64,
    score: u64,
}

impl<'a> ColorBox<'a> {
    fn new(colors: &'a mut [ColorCount]) -> Self {
        let mut minimum = [255; 3];
        let mut maximum = [0; 3];
        let mut population = 0;
        for color in colors.iter() {
            for axis in 0..3 {
                minimum[axis] = minimum[axis].min(color.rgb[axis]);
                maximum[axis] = maximum[axis].max(color.rgb[axis]);
            }
            population += color.count as u64;
        }
        let ranges = std::array::from_fn::<_, 3, _>(|axis| maximum[axis] - minimum[axis]);
        let mut axis = 0;
        for candidate in 1..3 {
            if ranges[candidate] > ranges[axis] {
                axis = candidate;
            }
        }
        Self {
            colors,
            axis,
            population,
            score: u64::from(ranges[axis]) * population,
        }
    }

    fn split(self) -> (Self, Self) {
        self.colors.sort_by_key(|color| color.rgb[self.axis]);
        // 個数は整数なので、半分以上になる最初の位置は ceil(population / 2) で探せる。
        let half = self.population.div_ceil(2);
        let mut cumulative = 0;
        let split = self
            .colors
            .iter()
            .take_while(|color| {
                cumulative += color.count as u64;
                cumulative < half
            })
            .count()
            + 1;
        let (left, right) = self.colors.split_at_mut(split.min(self.colors.len() - 1));
        (Self::new(left), Self::new(right))
    }

    fn average(&self) -> [u8; 3] {
        let mut totals = [0; 3];
        for color in self.colors.iter() {
            for (total, channel) in totals.iter_mut().zip(color.rgb) {
                *total += u64::from(channel) * color.count as u64;
            }
        }
        totals.map(|total| (total as f64 / self.population as f64).round_ties_even() as u8)
    }
}

/// `colors.median_cut_palette_from_samples_numpy` と同じ頻度付き代表色を箱順に返す。
pub fn median_cut_palette_from_samples(
    sample: &[u8],
    color_count: usize,
) -> Result<Vec<[u8; 3]>, Error> {
    let (sample, remainder) = sample.as_chunks::<3>();
    if !remainder.is_empty() {
        return Err(Error::Invalid("expected packed RGB samples".into()));
    }
    // NumPy の重み付き f64 和がすべて正確な範囲。
    if sample.len() as u64 > (1u64 << 53) / 255 {
        return Err(Error::Invalid(
            "too many samples for exact median-cut sums".into(),
        ));
    }
    let color_count = color_count.max(1);
    let mut colors = sorted_color_counts(sample);
    if colors.len() <= color_count {
        return Ok(colors.into_iter().map(|color| color.rgb).collect());
    }
    let mut boxes = vec![ColorBox::new(&mut colors)];
    while boxes.len() < color_count {
        // 最大値が同じ場合は、元の配列で最初の箱を使う。
        let Some((index, _)) = boxes
            .iter()
            .enumerate()
            .filter(|(_, color_box)| color_box.colors.len() > 1)
            .max_by_key(|(index, color_box)| (color_box.score, std::cmp::Reverse(*index)))
        else {
            break;
        };
        let (left, right) = boxes.remove(index).split();
        boxes.extend([left, right]);
    }
    Ok(boxes.iter().map(ColorBox::average).collect())
}

/// `colors.normalize_tone_curve_points_numpy` と同じ、制御点を整列・統合する。
/// 入力は Python 側で float に変換済みの座標。
pub fn normalize_tone_curve_points(mut points: Vec<(f64, f64)>) -> Vec<(f64, f64)> {
    let clipped = |value: f64| {
        // Python の min(1.0, NaN) は最初の引数を残す。
        if value.is_nan() {
            1.0
        } else {
            value.clamp(0.0, 1.0) + 0.0
        }
    };
    for (x, y) in &mut points {
        *x = clipped(*x);
        *y = clipped(*y);
    }
    points.extend([(0.0, 0.0), (1.0, 1.0)]);
    points.sort_by(|left, right| left.0.total_cmp(&right.0));
    points.dedup_by(|later, earlier| {
        if later.0 - earlier.0 < 1e-5 {
            *earlier = *later;
            true
        } else {
            false
        }
    });
    points[0] = (0.0, 0.0);
    // 密な連鎖で 1 点まで統合された場合も、NumPy 版と同じ代入順を保つ。
    let last = points.len() - 1;
    points[last] = (1.0, 1.0);
    points
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unique_colors_orders_caps_and_checks_pixels() {
        let bytes: Vec<_> = [0xffffffu32, 64, 63, 0, 64]
            .into_iter()
            .flat_map(u32::to_le_bytes)
            .collect();
        assert_eq!(unique_colors(&bytes, 3).unwrap(), [0, 63, 64]);
        assert!(unique_colors(&[255; 4], 0).unwrap().is_empty());
        assert!(unique_colors(&[255; 4], 1).is_err());
        assert!(unique_colors(&[0], 0).is_err());
    }

    #[test]
    fn variation_compares_both_ends_of_each_edge() {
        let rgb = [0, 0, 0, 0, 0, 255, 42, 0, 0, 42, 0, 255];
        assert_eq!(local_color_variation(&rgb, 2, 2).unwrap(), [255; 4]);
        assert_eq!(local_color_variation(&rgb, 1, 4).unwrap(), [255; 4]);
        assert_eq!(local_color_variation(&rgb, 4, 1).unwrap(), [255; 4]);
        assert!(local_color_variation(&[], 0, 3).unwrap().is_empty());
        assert!(local_color_variation(&rgb, 2, 3).is_err());
    }

    #[test]
    fn tone_curve_merges_consecutive_points_and_keeps_last() {
        let points = vec![
            (0.2, 0.6),
            (0.2, -0.0),
            (0.5, 0.2),
            (0.500009, f64::NAN),
            (0.500018, f64::INFINITY),
        ];
        let actual = normalize_tone_curve_points(points);
        assert_eq!(
            actual,
            [(0.0, 0.0), (0.2, 0.0), (0.500018, 1.0), (1.0, 1.0)]
        );
        assert_eq!(actual[1].1.to_bits(), 0);
    }

    #[test]
    fn median_cut_preserves_order_and_rounds_ties_to_even() {
        let samples = [255, 0, 0, 0, 1, 2, 1, 2, 3, 255, 0, 0];
        assert_eq!(
            median_cut_palette_from_samples(&samples, 2).unwrap(),
            [[0, 2, 2], [255, 0, 0]]
        );
        assert_eq!(
            median_cut_palette_from_samples(&samples, 3).unwrap(),
            [[0, 1, 2], [1, 2, 3], [255, 0, 0]]
        );
        assert!(median_cut_palette_from_samples(&[], 0).unwrap().is_empty());
        assert!(median_cut_palette_from_samples(&[0], 1).is_err());
    }
}
