//! 取り込みの前処理としての弱いぼかし（#10、`soft_blur.soft_blur`）。
//!
//! RGB だけに縦・横の順でガウスの重みを畳み込む（端は縁の値で延ばす）。alpha は変えない。
//! 重みの合計は NumPy の `sum`（8 つに分けて足す pairwise 和）と同じ順番で求める。

/// ぼかしの半径（σ、px）の上限。
pub const MAX_SIGMA: f64 = 3.0;

/// NumPy の `pairwise_sum`（連続した倍精度の配列）。
pub(crate) fn pairwise_sum(values: &[f64]) -> f64 {
    let n = values.len();
    if n < 8 {
        let mut total = -0.0;
        for &value in values {
            total += value;
        }
        total
    } else if n <= 128 {
        let mut r = [0.0f64; 8];
        r.copy_from_slice(&values[..8]);
        let mut i = 8;
        while i < n - n % 8 {
            for (j, acc) in r.iter_mut().enumerate() {
                *acc += values[i + j];
            }
            i += 8;
        }
        let mut total = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        for &value in &values[i..] {
            total += value;
        }
        total
    } else {
        let half = (n / 2) - (n / 2) % 8;
        pairwise_sum(&values[..half]) + pairwise_sum(&values[half..])
    }
}

fn kernel(sigma: f64) -> Vec<f64> {
    let radius = ((sigma * 3.0).ceil() as i64).max(1);
    let denominator = 2.0 * sigma * sigma;
    let weights: Vec<f64> = (-radius..=radius)
        .map(|x| {
            let x = x as f64;
            (-(x * x) / denominator).exp()
        })
        .collect();
    let total = pairwise_sum(&weights);
    weights.iter().map(|w| w / total).collect()
}

fn convolve(
    values: &[f64],
    width: usize,
    height: usize,
    kernel: &[f64],
    vertical: bool,
) -> Vec<f64> {
    let radius = (kernel.len() / 2) as isize;
    let mut out = vec![0.0f64; values.len()];
    for (offset, &weight) in kernel.iter().enumerate() {
        let shift = offset as isize - radius;
        for y in 0..height {
            for x in 0..width {
                let (sx, sy) = if vertical {
                    (
                        x,
                        (y as isize + shift).clamp(0, height as isize - 1) as usize,
                    )
                } else {
                    (
                        (x as isize + shift).clamp(0, width as isize - 1) as usize,
                        y,
                    )
                };
                for c in 0..3 {
                    out[(y * width + x) * 3 + c] += weight * values[(sy * width + sx) * 3 + c];
                }
            }
        }
    }
    out
}

/// RGB に半径 `sigma` のガウスぼかしをかけた RGBA（0 以下ならそのまま）。
pub fn soft_blur(rgba: &[u8], width: usize, height: usize, sigma: f64) -> Vec<u8> {
    let sigma = MAX_SIGMA.min(sigma);
    if sigma.is_nan() || sigma <= 0.0 || width == 0 || height == 0 {
        return rgba.to_vec();
    }
    let kernel = kernel(sigma);
    let rgb: Vec<f64> = rgba
        .chunks_exact(4)
        .flat_map(|pixel| [pixel[0], pixel[1], pixel[2]].map(f64::from))
        .collect();
    let rgb = convolve(
        &convolve(&rgb, width, height, &kernel, true),
        width,
        height,
        &kernel,
        false,
    );
    let mut out = rgba.to_vec();
    for (pixel, value) in out.chunks_exact_mut(4).zip(rgb.chunks_exact(3)) {
        for c in 0..3 {
            pixel[c] = value[c].round_ties_even().clamp(0.0, 255.0) as u8;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn flat_image_is_unchanged_and_kernel_sums_to_one() {
        let rgba = [7u8, 8, 9, 200].repeat(12);
        assert_eq!(soft_blur(&rgba, 4, 3, 1.5), rgba);
        assert_eq!(soft_blur(&rgba, 4, 3, 0.0), rgba);
        let total: f64 = kernel(2.0).iter().sum();
        assert!((total - 1.0).abs() < 1e-12);
        let values: Vec<f64> = (0..20).map(f64::from).collect();
        assert_eq!(pairwise_sum(&values), 190.0);
    }
}
