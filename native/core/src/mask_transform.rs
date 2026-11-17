//! 色マスクの変形（逆写像＋面積被覆）。
//!
//! Python の `mask_transform.render_labels_numpy` と同じ結果を返す。出力画素
//! ごとに `subsamples * subsamples` 個の probe を元画像へ逆写像し、もっとも多く
//! 取った番号がその画素を取る（同数なら小さい番号が勝つ）。
//!
//! 逆写像の作り方は変形の種類で違う。平面四角形（拡大縮小・回転・自由変形）は
//! 3x3 の行列から probe ごとに直接求められる。メッシュ変形は曲がった格子を
//! 三角形ごとに出力側へ塗って、重心座標から元座標を得る。

/// probe が元画像の外を読むときの番号（「何もない」）。
pub const OUTSIDE: u16 = 0;

/// 元画像。番号は 0 が「何もない」、1 以上が優先順位の並び。
/// `labels` は `width * height` 個の行優先。numpy 版 `_sample_labels` の入力に対応。
pub struct LabelImage<'a> {
    pub labels: &'a [u16],
    pub width: usize,
    pub height: usize,
}

impl LabelImage<'_> {
    /// 元座標 `(x, y)` の番号。範囲外は [`OUTSIDE`]。
    #[inline]
    fn at(&self, x: f64, y: f64) -> u16 {
        let (fx, fy) = (x.floor(), y.floor());
        if fx < 0.0 || fy < 0.0 || fx >= self.width as f64 || fy >= self.height as f64 {
            return OUTSIDE;
        }
        self.labels[fy as usize * self.width + fx as usize]
    }
}

/// 逆写像の行優先 3x3（出力座標 → 元座標）。numpy 版 `QuadMapper.map_band` に対応。
pub struct Homography(pub [f64; 9]);

impl Homography {
    /// 出力座標 `(x, y)` の元座標。射影の分母が 0 に近ければ `None`。
    #[inline]
    fn map(&self, x: f64, y: f64) -> Option<(f64, f64)> {
        let m = &self.0;
        let denominator = m[6] * x + m[7] * y + m[8];
        if denominator.abs() <= 1e-12 {
            return None;
        }
        Some((
            (m[0] * x + m[1] * y + m[2]) / denominator,
            (m[3] * x + m[4] * y + m[5]) / denominator,
        ))
    }
}

/// numpy 版 `MeshMapper.map_band` の入力に対応し、点数は `cols * rows`、各辺2点以上。
/// 曲がった格子。`points` は出力座標での格子点で、`cols x rows` の行優先。
pub struct Mesh<'a> {
    pub points: &'a [(f64, f64)],
    pub cols: usize,
    pub rows: usize,
}

/// 出力の 1 画素を、その probe の番号から決めて書き込む。
///
/// `sampled` は `probes` 個の番号。`coverage` は `coverage_labels` と同じ並びで、
/// その番号が取った probe の割合（0-255）を書く先。
fn resolve_pixel(
    sampled: &[u16],
    coverage_labels: &[u16],
    labels_out: &mut u16,
    coverage_out: &mut [&mut [u8]],
    x: usize,
    tally: &mut Vec<(u16, usize)>,
) {
    let probes = sampled.len();
    // 境目でない画素（probe が全部同じ）は数えずに決める。
    let first = sampled[0];
    let uniform = sampled.iter().all(|value| *value == first);
    *labels_out = if uniform {
        first
    } else {
        tally.clear();
        for value in sampled {
            match tally.iter_mut().find(|(label, _)| label == value) {
                Some((_, count)) => *count += 1,
                None => tally.push((*value, 1)),
            }
        }
        // 同数なら小さい番号が勝つ（番号は優先順位の並び）。
        tally
            .iter()
            .copied()
            .reduce(|best, here| match here.1.cmp(&best.1) {
                std::cmp::Ordering::Greater => here,
                std::cmp::Ordering::Equal if here.0 < best.0 => here,
                _ => best,
            })
            .map_or(OUTSIDE, |(label, _)| label)
    };
    for (slot, label) in coverage_labels.iter().enumerate() {
        let hits = if uniform {
            if first == *label {
                probes
            } else {
                0
            }
        } else {
            sampled.iter().filter(|value| *value == label).count()
        };
        coverage_out[slot][x] = ((hits * 255) / probes) as u8;
    }
}

/// 出力の帯（`rows` 行）を書き込む先。
/// 各バッファは出力幅×行数分。numpy 版 `render_labels_numpy` の帯出力に対応。
pub struct BandOutput<'a> {
    /// 帯の先頭の行（出力全体での位置）。
    pub y0: usize,
    pub rows: usize,
    pub labels: &'a mut [u16],
    /// `coverage_labels` と同じ並びの、帯の分の被覆。
    pub coverage: Vec<&'a mut [u8]>,
}

impl BandOutput<'_> {
    /// 帯の中の `row` 行目を書き込む先を切り出す。
    fn row(&mut self, row: usize, out_width: usize) -> (&mut [u16], Vec<&mut [u8]>) {
        let span = row * out_width..(row + 1) * out_width;
        (
            &mut self.labels[span.clone()],
            self.coverage
                .iter_mut()
                .map(|buffer| &mut buffer[span.clone()])
                .collect(),
        )
    }
}

/// 連続行の塊を帯に分けて描く（戻り値なし）。
/// `out_width > 0`、`band_height > 0`、各バッファは同じ行数分を持つこと。
/// `chunk_start` は帯の高さの倍数とし、numpy 版 `render_labels_numpy` の帯順で呼ぶ。
pub fn render_chunk<F: Fn(BandOutput)>(
    out_width: usize,
    band_height: usize,
    chunk_start: usize,
    labels: &mut [u16],
    mut coverage_chunk: Vec<&mut [u8]>,
    render: &F,
) {
    let chunk_height = labels.len() / out_width;
    let mut label_rest: &mut [u16] = labels;
    let mut done = 0;
    while done < chunk_height {
        let rows = band_height.min(chunk_height - done);
        let (labels, rest) = label_rest.split_at_mut(rows * out_width);
        label_rest = rest;
        let coverage = coverage_chunk
            .iter_mut()
            .map(|buffer| {
                // 使った分を取り除き、残りを次の帯へ回す。
                let taken = std::mem::take(buffer);
                let (band, rest) = taken.split_at_mut(rows * out_width);
                *buffer = rest;
                band
            })
            .collect();
        render(BandOutput {
            y0: chunk_start + done,
            rows,
            labels,
            coverage,
        });
        done += rows;
    }
}

/// 番号と指定番号ごとの被覆（0–255）を行優先で返す逐次版。
/// サイズの積が収まることを前提とし、空の出力は描画しない。
/// 帯の区切りは `band_height`（1以上に補正）の倍数で、並列版と同じ。
/// numpy 版 `render_labels_numpy` の帯分割と一致する。
pub fn render_in_bands<F: Fn(BandOutput)>(
    out_width: usize,
    out_height: usize,
    coverage_labels: &[u16],
    band_height: usize,
    render: F,
) -> (Vec<u16>, Vec<Vec<u8>>) {
    let mut out_labels = vec![OUTSIDE; out_width * out_height];
    let mut out_coverage: Vec<Vec<u8>> = coverage_labels
        .iter()
        .map(|_| vec![0u8; out_width * out_height])
        .collect();
    if out_width == 0 || out_height == 0 {
        return (out_labels, out_coverage);
    }

    let band_height = band_height.clamp(1, out_height);
    let coverage = out_coverage
        .iter_mut()
        .map(|buffer| buffer.as_mut_slice())
        .collect();
    render_chunk(
        out_width,
        band_height,
        0,
        &mut out_labels,
        coverage,
        &render,
    );
    (out_labels, out_coverage)
}

/// 帯を描く関数を返す。準備用の確保はこの呼び出し時に一度だけ行う。
/// `image` は幅×高さの番号、`subsamples` は1〜16、サイズの積は収まること。
/// numpy 版 `render_labels_numpy` と `QuadMapper.map_band` に対応する。
/// 返す関数には幅が `out_width`、`rows == 1` の帯を渡す。
pub fn quad_band_renderer<'a>(
    image: &'a LabelImage<'_>,
    matrix: &'a Homography,
    out_width: usize,
    subsamples: usize,
    coverage_labels: &'a [u16],
) -> impl Fn(BandOutput) + Send + Sync + 'a {
    let probes = subsamples * subsamples;
    let step = 1.0 / subsamples as f64;
    // probe は画素の内側に半歩入った位置にある。
    let offsets: Vec<f64> = (0..subsamples).map(|i| (i as f64 + 0.5) * step).collect();
    move |mut band| {
        let row_y = band.y0 as f64;
        let (labels, mut coverage) = band.row(0, out_width);
        let mut sampled = vec![OUTSIDE; probes];
        let mut tally: Vec<(u16, usize)> = Vec::with_capacity(probes);
        for (x, out) in labels.iter_mut().enumerate() {
            for (sy, offset_y) in offsets.iter().enumerate() {
                let probe_y = row_y + offset_y;
                for (sx, offset_x) in offsets.iter().enumerate() {
                    let probe_x = x as f64 + offset_x;
                    sampled[sy * subsamples + sx] = match matrix.map(probe_x, probe_y) {
                        // 射影の地平線は「何もない」として数える。
                        None => OUTSIDE,
                        Some((source_x, source_y)) => image.at(source_x, source_y),
                    };
                }
            }
            resolve_pixel(&sampled, coverage_labels, out, &mut coverage, x, &mut tally);
        }
    }
}

/// メッシュの三角形を帯へ塗り、probe ごとの番号を書く。
///
/// `probe_labels` は `(rows * subsamples) x (out_width * subsamples)` の並び。
/// どの三角形にも入らない probe は [`OUTSIDE`] のまま残る。
fn rasterize_mesh_band(
    image: &LabelImage,
    mesh: &Mesh,
    out_width: usize,
    band_y0: usize,
    rows: usize,
    subsamples: usize,
    probe_labels: &mut [u16],
) {
    let scale = subsamples as f64;
    let probe_width = out_width * subsamples;
    let probe_height = rows * subsamples;
    let origin_y = band_y0 as f64 * scale;
    let span_x = (mesh.cols.max(2) - 1) as f64;
    let span_y = (mesh.rows.max(2) - 1) as f64;
    // 格子は元画像の画素の中心に載っている。
    let last_x = image.width as f64 - 1.0;
    let last_y = image.height as f64 - 1.0;

    let mut raster = |corners: [usize; 3], source: [(f64, f64); 3]| {
        let target_x = corners.map(|index| mesh.points[index].0 * scale);
        let target_y = corners.map(|index| mesh.points[index].1 * scale - origin_y);
        let low = |values: [f64; 3], limit: usize| {
            let least = values[0].min(values[1]).min(values[2]).floor();
            least.max(0.0).min(limit as f64) as usize
        };
        let high = |values: [f64; 3], limit: usize| {
            let most = values[0].max(values[1]).max(values[2]).ceil();
            most.max(-1.0).min(limit as f64 - 1.0)
        };
        let (min_x, min_y) = (low(target_x, probe_width), low(target_y, probe_height));
        let (max_x, max_y) = (high(target_x, probe_width), high(target_y, probe_height));
        if max_x < min_x as f64 || max_y < min_y as f64 {
            return;
        }
        let (max_x, max_y) = (max_x as usize, max_y as usize);
        let denominator = (target_y[1] - target_y[2]) * (target_x[0] - target_x[2])
            + (target_x[2] - target_x[1]) * (target_y[0] - target_y[2]);
        if denominator.abs() < 1e-8 {
            return;
        }
        for probe_y in min_y..=max_y {
            // probe は升目の内側に半歩入った位置にある。
            let yy = probe_y as f64 + 0.5;
            for probe_x in min_x..=max_x {
                let xx = probe_x as f64 + 0.5;
                let weight0 = ((target_y[1] - target_y[2]) * (xx - target_x[2])
                    + (target_x[2] - target_x[1]) * (yy - target_y[2]))
                    / denominator;
                let weight1 = ((target_y[2] - target_y[0]) * (xx - target_x[2])
                    + (target_x[0] - target_x[2]) * (yy - target_y[2]))
                    / denominator;
                let weight2 = 1.0 - weight0 - weight1;
                if weight0 < -1e-6 || weight1 < -1e-6 || weight2 < -1e-6 {
                    continue;
                }
                // 重心座標がそのまま元座標の重みになる。格子は画素の中心に
                // 載っているので、半画素ずらして面の約束（画素 n は [n, n+1)）
                // に合わせる。
                let source_x =
                    weight0 * source[0].0 + weight1 * source[1].0 + weight2 * source[2].0 + 0.5;
                let source_y =
                    weight0 * source[0].1 + weight1 * source[1].1 + weight2 * source[2].1 + 0.5;
                probe_labels[probe_y * probe_width + probe_x] = image.at(source_x, source_y);
            }
        }
    };

    for grid_y in 0..mesh.rows.saturating_sub(1) {
        let top = last_y * grid_y as f64 / span_y;
        let bottom = last_y * (grid_y + 1) as f64 / span_y;
        let row_base = grid_y * mesh.cols;
        for grid_x in 0..mesh.cols.saturating_sub(1) {
            let left = last_x * grid_x as f64 / span_x;
            let right = last_x * (grid_x + 1) as f64 / span_x;
            let index = row_base + grid_x;
            raster(
                [index, index + 1, index + mesh.cols + 1],
                [(left, top), (right, top), (right, bottom)],
            );
            raster(
                [index, index + mesh.cols + 1, index + mesh.cols],
                [(left, top), (right, bottom), (left, bottom)],
            );
        }
    }
}

/// 帯を描く関数を返す。準備用の確保はこの呼び出し時に一度だけ行う。
/// `image` は幅×高さの番号、`subsamples` は1〜16、サイズの積は収まること。
/// numpy 版 `render_labels_numpy` と `MeshMapper.map_band` に対応する。
/// 格子は `Mesh` の前提を満たし、帯は幅が `out_width` であること。
pub fn mesh_band_renderer<'a>(
    image: &'a LabelImage<'_>,
    mesh: &'a Mesh,
    out_width: usize,
    subsamples: usize,
    coverage_labels: &'a [u16],
) -> impl Fn(BandOutput) + Send + Sync + 'a {
    let probes = subsamples * subsamples;
    let probe_width = out_width * subsamples;
    move |mut band| {
        let mut probe_labels = vec![OUTSIDE; probe_width * band.rows * subsamples];
        rasterize_mesh_band(
            image,
            mesh,
            out_width,
            band.y0,
            band.rows,
            subsamples,
            &mut probe_labels,
        );
        let mut sampled = vec![OUTSIDE; probes];
        let mut tally: Vec<(u16, usize)> = Vec::with_capacity(probes);
        for row in 0..band.rows {
            let (labels, mut coverage) = band.row(row, out_width);
            for (x, out) in labels.iter_mut().enumerate() {
                for sy in 0..subsamples {
                    let probe_row = (row * subsamples + sy) * probe_width + x * subsamples;
                    sampled[sy * subsamples..(sy + 1) * subsamples]
                        .copy_from_slice(&probe_labels[probe_row..probe_row + subsamples]);
                }
                resolve_pixel(&sampled, coverage_labels, out, &mut coverage, x, &mut tally);
            }
        }
    }
}

/// 平面四角形の番号と指定番号ごとの被覆（0–255）を逐次計算して返す。
/// 入力の前提は `quad_band_renderer` と同じ。出力は行優先。
/// numpy 版 `render_labels_numpy` の `QuadMapper` 使用時に対応する。
/// 線の保護（`_protect_lines`）は呼び出し側で行う。
pub fn render_quad(
    image: &LabelImage,
    matrix: &Homography,
    out_width: usize,
    out_height: usize,
    subsamples: usize,
    coverage_labels: &[u16],
) -> (Vec<u16>, Vec<Vec<u8>>) {
    let render = quad_band_renderer(image, matrix, out_width, subsamples, coverage_labels);
    render_in_bands(out_width, out_height, coverage_labels, 1, render)
}

/// メッシュの番号と指定番号ごとの被覆（0–255）を逐次計算して返す。
/// 入力の前提は `mesh_band_renderer` と同じ。出力は行優先。
/// numpy 版 `render_labels_numpy` の `MeshMapper` 使用時に対応する。
/// 線の保護（`_protect_lines`）は呼び出し側で行う。
pub fn render_mesh(
    image: &LabelImage,
    mesh: &Mesh,
    out_width: usize,
    out_height: usize,
    subsamples: usize,
    band_height: usize,
    coverage_labels: &[u16],
) -> (Vec<u16>, Vec<Vec<u8>>) {
    let render = mesh_band_renderer(image, mesh, out_width, subsamples, coverage_labels);
    render_in_bands(out_width, out_height, coverage_labels, band_height, render)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn image<'a>(labels: &'a [u16], width: usize, height: usize) -> LabelImage<'a> {
        LabelImage {
            labels,
            width,
            height,
        }
    }

    /// 恒等変換。probe は画素の中に並ぶので、そのまま写る。
    fn identity() -> Homography {
        Homography([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0])
    }

    fn render_quad(
        labels: &[u16],
        size: (usize, usize),
        matrix: &Homography,
        out: (usize, usize),
        subsamples: usize,
        coverage_labels: &[u16],
    ) -> (Vec<u16>, Vec<Vec<u8>>) {
        super::render_quad(
            &image(labels, size.0, size.1),
            matrix,
            out.0,
            out.1,
            subsamples,
            coverage_labels,
        )
    }

    #[test]
    fn identity_keeps_labels_and_full_coverage() {
        let labels = vec![0u16, 1, 2, 0];
        let (result, coverage) = render_quad(&labels, (2, 2), &identity(), (2, 2), 4, &[1, 2]);
        assert_eq!(result, labels);
        assert_eq!(coverage[0], vec![0, 255, 0, 0]);
        assert_eq!(coverage[1], vec![0, 0, 255, 0]);
    }

    #[test]
    fn outside_probes_read_nothing() {
        let labels = vec![1u16];
        let (result, _coverage) = render_quad(&labels, (1, 1), &identity(), (3, 1), 2, &[]);
        assert_eq!(result, vec![1, 0, 0]);
    }

    #[test]
    fn half_coverage_splits_a_doubled_pixel() {
        // 横に 2 倍へ伸ばすと、元の 1 画素が出力の 2 画素を埋める。
        let labels = vec![1u16, 2];
        let matrix = Homography([0.5, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]);
        let (result, coverage) = render_quad(&labels, (2, 1), &matrix, (4, 1), 4, &[2]);
        assert_eq!(result, vec![1, 1, 2, 2]);
        assert_eq!(coverage[0], vec![0, 0, 255, 255]);
    }

    #[test]
    fn ties_go_to_the_smaller_label() {
        // 2 画素を 1 画素に縮める。probe が 2 つずつに分かれて同数になる。
        let labels = vec![2u16, 1];
        let matrix = Homography([2.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]);
        let (result, _coverage) = render_quad(&labels, (2, 1), &matrix, (1, 1), 2, &[]);
        assert_eq!(result, vec![1]);
    }

    #[test]
    fn the_projective_horizon_reads_nothing() {
        // 分母が 0 になる行を含む行列。無限にせず「何もない」として扱う。
        let labels = vec![1u16; 4];
        let matrix = Homography([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, -0.5]);
        let (result, _coverage) = render_quad(&labels, (2, 2), &matrix, (2, 2), 1, &[]);
        assert_eq!(result[0], 0);
    }

    /// 元画像を等倍で覆う 2x2 の格子。格子点は画素の中心に載るので、
    /// `width x height` の元画像が覆うのは出力の `(width-1) x (height-1)` 分。
    fn flat_mesh(width: usize, height: usize) -> Vec<(f64, f64)> {
        let (last_x, last_y) = (width as f64 - 1.0, height as f64 - 1.0);
        vec![(0.0, 0.0), (last_x, 0.0), (0.0, last_y), (last_x, last_y)]
    }

    #[allow(clippy::too_many_arguments)]
    fn render_mesh(
        labels: &[u16],
        size: (usize, usize),
        points: &[(f64, f64)],
        grid: (usize, usize),
        out: (usize, usize),
        subsamples: usize,
        band_rows: usize,
        coverage_labels: &[u16],
    ) -> (Vec<u16>, Vec<Vec<u8>>) {
        super::render_mesh(
            &image(labels, size.0, size.1),
            &Mesh {
                points,
                cols: grid.0,
                rows: grid.1,
            },
            out.0,
            out.1,
            subsamples,
            band_rows,
            coverage_labels,
        )
    }

    #[test]
    fn the_centre_of_a_flat_mesh_reads_the_far_corner() {
        // 2x2 の格子が覆うのは出力の 1 画素分。その真ん中の probe は重心が
        // 半分ずつなので、元座標 (1.0, 1.0) すなわち右下の画素を読む。
        let labels = vec![1u16, 2, 3, 4];
        let points = flat_mesh(2, 2);
        let (result, coverage) = render_mesh(&labels, (2, 2), &points, (2, 2), (1, 1), 1, 64, &[4]);
        assert_eq!(result, vec![4]);
        assert_eq!(coverage[0], vec![255]);
    }

    #[test]
    fn probes_outside_every_triangle_read_nothing() {
        // 格子は出力の左上の 1 画素だけを覆う。残りは「何もない」まま。
        let labels = vec![1u16; 4];
        let points = flat_mesh(2, 2);
        let (result, _coverage) = render_mesh(&labels, (2, 2), &points, (2, 2), (3, 1), 1, 64, &[]);
        assert_eq!(result, vec![1, 0, 0]);
    }

    #[test]
    fn mesh_bands_do_not_change_the_result() {
        let labels: Vec<u16> = (0..64u16).map(|value| value % 4).collect();
        // 3x3 の格子を曲げる（真ん中を持ち上げる）。
        let points = vec![
            (0.0, 0.0),
            (6.0, -1.0),
            (13.0, 1.0),
            (1.0, 7.0),
            (7.0, 5.0),
            (12.0, 8.0),
            (0.5, 13.0),
            (6.0, 14.0),
            (13.0, 12.0),
        ];
        let whole = render_mesh(&labels, (8, 8), &points, (3, 3), (15, 15), 4, 64, &[1, 2]);
        let banded = render_mesh(&labels, (8, 8), &points, (3, 3), (15, 15), 4, 4, &[1, 2]);
        assert_eq!(whole, banded);
    }
}
