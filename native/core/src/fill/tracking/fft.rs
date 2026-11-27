//! ゼロ詰めした実数の二次元相関。
use super::{invalid, Run, Size};
use crate::Error;
use realfft::{num_complex::Complex64, ComplexToReal, RealFftPlanner, RealToComplex};
use std::sync::Arc;

pub(super) fn fast_length(mut n: usize) -> usize {
    loop {
        let mut rest = n;
        for factor in [2, 3, 5] {
            while rest.is_multiple_of(factor) {
                rest /= factor;
            }
        }
        if rest == 1 {
            return n;
        }
        n += 1;
    }
}

pub(super) fn dimensions(output: Size) -> Size {
    Size {
        w: fast_length(output.w),
        h: fast_length(output.h),
    }
}

pub(super) fn work(size: Size, colors: usize) -> usize {
    let points = size.w.saturating_mul(size.h);
    let stages = (size.w.ilog2() + size.h.ilog2()) as usize;
    points.saturating_mul(stages).saturating_mul(2 * colors + 1) / 4
}

fn zeros<T: Default + Clone>(len: usize) -> Result<Vec<T>, Error> {
    let mut values = Vec::new();
    values.try_reserve_exact(len).map_err(|_| invalid())?;
    values.resize(len, T::default());
    Ok(values)
}

struct Vertical {
    plan: Arc<dyn RealToComplex<f64>>,
    input: Vec<f64>,
    real: Vec<Complex64>,
    imag: Vec<Complex64>,
    scratch: Vec<Complex64>,
}

impl Vertical {
    fn new(plan: Arc<dyn RealToComplex<f64>>) -> Self {
        Self {
            input: plan.make_input_vec(),
            real: plan.make_output_vec(),
            imag: plan.make_output_vec(),
            scratch: plan.make_scratch_vec(),
            plan,
        }
    }

    fn transform(&mut self, column: &mut [Complex64], inverse: bool) -> Result<(), Error> {
        for (a, b) in self.input.iter_mut().zip(column.iter()) {
            *a = b.re;
        }
        self.plan
            .process_with_scratch(&mut self.input, &mut self.real, &mut self.scratch)
            .map_err(|_| invalid())?;
        for (a, b) in self.input.iter_mut().zip(column.iter()) {
            *a = b.im;
        }
        self.plan
            .process_with_scratch(&mut self.input, &mut self.imag, &mut self.scratch)
            .map_err(|_| invalid())?;
        let h = column.len();
        // 実部・虚部それぞれの共役対称性から複素列の全周波数を復元する。
        for (y, (&real, &imag)) in self.real.iter().zip(&self.imag).enumerate() {
            let positive = Complex64::new(real.re - imag.im, real.im + imag.re);
            let negative = Complex64::new(real.re + imag.im, -real.im + imag.re);
            if inverse && y != 0 {
                column[h - y] = positive;
                column[y] = negative;
            } else {
                column[y] = positive;
                if y != 0 {
                    column[h - y] = negative;
                }
            }
        }
        Ok(())
    }
}

struct Transform {
    size: Size,
    forward: Arc<dyn RealToComplex<f64>>,
    inverse: Arc<dyn ComplexToReal<f64>>,
    horizontal: Vec<f64>,
    row: Vec<Complex64>,
    scratch: Vec<Complex64>,
    vertical: Vertical,
    block: Vec<Complex64>,
}

impl Transform {
    const BLOCK_ROWS: usize = 16;

    fn new(size: Size) -> Self {
        let mut planner = RealFftPlanner::<f64>::new();
        let forward = planner.plan_fft_forward(size.w);
        let inverse = planner.plan_fft_inverse(size.w);
        let vertical = Vertical::new(planner.plan_fft_forward(size.h));
        Self {
            size,
            horizontal: forward.make_input_vec(),
            row: forward.make_output_vec(),
            scratch: vec![
                Complex64::default();
                forward.get_scratch_len().max(inverse.get_scratch_len())
            ],
            block: vec![Complex64::default(); Self::BLOCK_ROWS * (size.w / 2 + 1)],
            forward,
            inverse,
            vertical,
        }
    }

    fn forward(&mut self, mut runs: &[Run], columns: &mut [Complex64]) -> Result<(), Error> {
        self.forward_rows(columns, runs.last().map_or(0, |r| r.y + 1), |y, row| {
            let end = runs.partition_point(|r| r.y <= y);
            for r in &runs[..end] {
                row[r.x..r.end].fill(1.0);
            }
            runs = &runs[end..];
            end != 0
        })
    }

    fn forward_rows(
        &mut self,
        columns: &mut [Complex64],
        active_rows: usize,
        mut fill: impl FnMut(usize, &mut [f64]) -> bool,
    ) -> Result<(), Error> {
        let Size { w, h } = self.size;
        let bins = w / 2 + 1;
        columns.fill(Complex64::default());
        for start in (0..active_rows).step_by(Self::BLOCK_ROWS) {
            let rows = (active_rows - start).min(Self::BLOCK_ROWS);
            for y in 0..rows {
                let row = &mut self.block[y * bins..][..bins];
                self.horizontal.fill(0.0);
                if fill(start + y, &mut self.horizontal) {
                    self.forward
                        .process_with_scratch(&mut self.horizontal, row, &mut self.scratch)
                        .map_err(|_| invalid())?;
                } else {
                    row.fill(Complex64::default());
                }
            }
            for (x, column) in columns.chunks_exact_mut(h).enumerate() {
                for (y, value) in column[start..start + rows].iter_mut().enumerate() {
                    *value = self.block[y * bins + x];
                }
            }
        }
        for column in columns.chunks_exact_mut(h) {
            self.vertical.transform(column, false)?;
        }
        Ok(())
    }

    fn inverse_row(&mut self) -> Result<(), Error> {
        // 端の周波数の虚部は理論上ゼロ。数値誤差を取り除く。
        self.row[0].im = 0.0;
        if self.size.w.is_multiple_of(2) {
            self.row[self.size.w / 2].im = 0.0;
        }
        self.inverse
            .process_with_scratch(&mut self.row, &mut self.horizontal, &mut self.scratch)
            .map_err(|_| invalid())
    }

    fn inverse(
        &mut self,
        columns: &mut [Complex64],
        output: Size,
        before: Size,
        after: Size,
        reach: Option<usize>,
    ) -> Result<(Vec<i64>, f64), Error> {
        let Size { w, h } = self.size;
        let bins = w / 2 + 1;
        let mut values = zeros(output.w.checked_mul(output.h).ok_or_else(invalid)?)?;
        let scale = 1.0 / (w as f64 * h as f64);
        let mut max_error = 0.0_f64;
        for column in columns.chunks_exact_mut(h) {
            self.vertical.transform(column, true)?;
        }
        // 元の表の正の領域と、末尾に配置した負の領域をそれぞれコピーする。
        let (positive, negative) = reach.map_or((after.h + 1, before.h - 1), |reach| {
            ((reach + 1).min(after.h), reach.min(before.h - 1))
        });
        let y_ranges = [0..positive, (output.h - negative)..output.h];
        let x_ranges = [0..after.w + 1, (output.w - before.w + 1)..output.w];
        for range in y_ranges {
            for start in range.clone().step_by(Self::BLOCK_ROWS) {
                let rows = (range.end - start).min(Self::BLOCK_ROWS);
                let src = if start < positive {
                    start
                } else {
                    h - (output.h - start)
                };
                for (x, column) in columns.chunks_exact(h).enumerate() {
                    for (y, &value) in column[src..src + rows].iter().enumerate() {
                        self.block[y * bins + x] = value;
                    }
                }
                for y in 0..rows {
                    self.row.copy_from_slice(&self.block[y * bins..][..bins]);
                    self.inverse_row()?;
                    let row = &mut values[(start + y) * output.w..][..output.w];
                    for range in &x_ranges {
                        let src = if range.start == 0 {
                            0
                        } else {
                            w - (output.w - range.start)
                        };
                        for (v, &raw) in row[range.clone()].iter_mut().zip(&self.horizontal[src..])
                        {
                            let scaled = raw * scale;
                            let rounded = scaled.round();
                            if cfg!(test) {
                                max_error = max_error.max((scaled - rounded).abs());
                            }
                            *v = rounded as i64;
                        }
                    }
                }
            }
        }
        Ok((values, max_error))
    }
}

pub(super) fn correlate<'a>(
    pairs: impl IntoIterator<Item = (&'a [Run], &'a [Run])>,
    fft: Size,
    output: Size,
    before: Size,
    after: Size,
    reach: Option<usize>,
) -> Result<(Vec<i64>, f64), Error> {
    let mut plan = Transform::new(fft);
    let n = (fft.w / 2 + 1).checked_mul(fft.h).ok_or_else(invalid)?;
    let mut template = zeros(n)?;
    let mut target = zeros(n)?;
    let mut pairs = pairs
        .into_iter()
        .filter(|(a, b)| !a.is_empty() && !b.is_empty());
    let (a, b) = pairs.next().ok_or_else(invalid)?;
    plan.forward(a, &mut template)?;
    plan.forward(b, &mut target)?;
    for (a, b) in template.iter_mut().zip(&target) {
        *a = a.conj() * b;
    }
    let mut sum = template;
    if let Some((a, b)) = pairs.next() {
        template = zeros(n)?;
        for (a, b) in std::iter::once((a, b)).chain(pairs) {
            plan.forward(a, &mut template)?;
            plan.forward(b, &mut target)?;
            for ((sum, a), b) in sum.iter_mut().zip(&template).zip(&target) {
                *sum += a.conj() * b;
            }
        }
    }
    drop(target);
    plan.inverse(&mut sum, output, before, after, reach)
}

// 二値窓の相関を、呼び出し元が指定した周期と行・列の順で取り出す。
#[allow(clippy::too_many_arguments, clippy::type_complexity)]
pub(crate) fn circular_counts(
    before: &[u8],
    exact: &[u8],
    wide: &[u8],
    shape: (usize, usize),
    period: (usize, usize),
    ys: &[usize],
    xs: &[usize],
    radix: u64,
) -> Result<(Vec<(usize, usize)>, f64), Error> {
    let (height, width) = shape;
    let (h, w) = period;
    let mut plan = Transform::new(Size { w, h });
    let bins = w / 2 + 1;
    let mut template = zeros(bins * h)?;
    let mut guide = zeros(bins * h)?;
    plan.forward_rows(&mut template, height, |y, row| {
        for (value, &pixel) in row.iter_mut().zip(&before[y * width..][..width]) {
            *value = f64::from(pixel);
        }
        true
    })?;
    plan.forward_rows(&mut guide, height, |y, row| {
        for (x, value) in row[..width].iter_mut().enumerate() {
            let i = y * width + x;
            *value = (u64::from(wide[i]) * radix + u64::from(exact[i])) as f64;
        }
        true
    })?;
    for (b, a) in guide.iter_mut().zip(template) {
        *b *= a.conj();
    }
    for column in guide.chunks_exact_mut(h) {
        plan.vertical.transform(column, true)?;
    }
    let mut values = zeros(ys.len() * xs.len())?;
    let mut max_error = 0.0_f64;
    let scale = 1.0 / (w * h) as f64;
    for (&y, output) in ys.iter().zip(values.chunks_exact_mut(xs.len())) {
        for (value, column) in plan.row.iter_mut().zip(guide.chunks_exact(h)) {
            *value = column[y];
        }
        plan.inverse_row()?;
        for (&x, value) in xs.iter().zip(output) {
            let raw = plan.horizontal[x] * scale;
            let rounded = raw.round();
            max_error = max_error.max((raw - rounded).abs());
            let packed = rounded as u64;
            *value = ((packed % radix) as usize, (packed / radix) as usize);
        }
    }
    Ok((values, max_error))
}
