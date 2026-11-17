//! 前処理済みマスクからバケツの最終領域を求める。
use crate::regions::{dilate, flood_fill, label_regions, INVALID_CODE};

/// numpy 版の含む色との距離二乗の上限。
pub const MASK_DISTANCE_SQUARED: i32 = 56 * 56;
/// numpy 版 `_enclosed_by` で回収する alpha の条件。
#[derive(Clone, Copy, PartialEq)]
pub enum Enclosed {
    None,
    InnerTransparent,
    ClosedOpaque,
}
/// Python BucketOptions のうち前処理後に使う設定。
pub struct FillOptions {
    pub adjacent: bool,
    pub require_closed: bool,
    pub island_max_width: usize,
    pub include_masks: bool,
    pub include_background_mask: bool,
    pub mask_colors: Vec<[u8; 3]>,
    pub enclosed: Enclosed,
}
/// numpy 版 compute_fill_region と同じ理由、領域、半開矩形を返す。
#[derive(Debug, PartialEq)]
pub enum FillOutcome {
    NotTarget,
    Empty,
    Open {
        region: Vec<u8>,
    },
    Ok {
        region: Vec<u8>,
        bounds: (usize, usize, usize, usize),
    },
}
pub(crate) fn bounds(mask: &[u8], w: usize, h: usize) -> Option<(usize, usize, usize, usize)> {
    let (mut x0, mut y0, mut x1, mut y1) = (w, h, 0, 0);
    for (y, row) in mask.chunks_exact(w).enumerate() {
        if let Some(left) = row.iter().position(|v| *v != 0) {
            let right = row.iter().rposition(|v| *v != 0).unwrap_or(left);
            x0 = x0.min(left);
            x1 = x1.max(right + 1);
            y0 = y0.min(y);
            y1 = y + 1;
        }
    }
    (x1 > 0).then_some((x0, y0, x1, y1))
}
fn merge(a: &mut [u8], b: &[u8]) {
    for (a, b) in a.iter_mut().zip(b) {
        *a |= *b;
    }
}
fn islands(region: &mut [u8], target: &[u8], w: usize, h: usize, max_width: usize) {
    let touching = dilate(region, w, h, 1);
    if !target
        .iter()
        .zip(&touching)
        .zip(region.iter())
        .any(|((t, a), r)| *t != 0 && *a != 0 && *r == 0)
    {
        return;
    }
    let codes: Vec<u32> = target
        .iter()
        .map(|v| if *v == 0 { INVALID_CODE } else { 1 })
        .collect();
    let (labels, count) = label_regions(&codes, w, h);
    let mut boxes = vec![(w, h, 0, 0); count as usize + 1];
    for (y, row) in labels.chunks_exact(w).enumerate() {
        for (x, &l) in row.iter().enumerate() {
            if l != 0 {
                let b = &mut boxes[l as usize];
                b.0 = b.0.min(x);
                b.1 = b.1.min(y);
                b.2 = b.2.max(x + 1);
                b.3 = b.3.max(y + 1);
            }
        }
    }
    let mut visited = vec![false; boxes.len()];
    visited[0] = true;
    let mut pending = Vec::new();
    for i in 0..target.len() {
        if region[i] != 0 && target[i] != 0 {
            visited[labels[i] as usize] = true;
        }
        if touching[i] != 0 && target[i] != 0 && region[i] == 0 {
            pending.push(labels[i] as usize);
        }
    }
    while let Some(l) = pending.pop() {
        if visited[l] {
            continue;
        }
        visited[l] = true;
        let (x0, y0, x1, y1) = boxes[l];
        if x0 == 0 || y0 == 0 || x1 == w || y1 == h {
            continue;
        }
        let bw = x1 - x0;
        let bh = y1 - y0;
        let side = max_width.saturating_add(1);
        if bw >= side && bh >= side {
            let stride = bw + 1;
            let mut sums = vec![0usize; stride * (bh + 1)];
            let mut fits = true;
            for y in 0..bh {
                for x in 0..bw {
                    let j = (y + 1) * stride + x + 1;
                    sums[j] = usize::from(labels[(y0 + y) * w + x0 + x] as usize == l)
                        + sums[j - 1]
                        + sums[j - stride]
                        - sums[j - stride - 1];
                    if x + 1 >= side
                        && y + 1 >= side
                        && sums[j] + sums[j - side * stride - side]
                            - sums[j - side]
                            - sums[j - side * stride]
                            == side * side
                    {
                        fits = false;
                    }
                }
            }
            if !fits {
                continue;
            }
        }
        for y in y0..y1 {
            for x in x0..x1 {
                let i = y * w + x;
                if labels[i] as usize != l {
                    continue;
                }
                region[i] = 1;
                for ny in y.saturating_sub(1)..=(y + 1).min(h - 1) {
                    for nx in x.saturating_sub(1)..=(x + 1).min(w - 1) {
                        let other = labels[ny * w + nx] as usize;
                        if !visited[other] {
                            pending.push(other);
                        }
                    }
                }
            }
        }
    }
}
/// numpy 版 compute_fill_region の前処理後から bounds 決定までと一致する。
/// マスクは行優先の 0/1。長さ・開始点・必須マスクが不正なら Err を返す。
#[allow(clippy::too_many_arguments)]
pub fn compute_fill_region(
    rgba: &[u8],
    w: usize,
    h: usize,
    start: (usize, usize),
    target: &[u8],
    passable: Option<&[u8]>,
    gap: Option<&[u8]>,
    background: Option<&[u8]>,
    selection: Option<&[u8]>,
    initial: Option<&[u8]>,
    o: &FillOptions,
) -> Result<FillOutcome, String> {
    let n = w.checked_mul(h).ok_or("dimensions overflow")?;
    if w == 0
        || h == 0
        || n.checked_mul(4) != Some(rgba.len())
        || target.len() != n
        || start.0 >= w
        || start.1 >= h
        || [passable, gap, background, selection, initial]
            .iter()
            .flatten()
            .any(|m| m.len() != n)
        || (o.adjacent && passable.is_none())
        || (o.include_masks && o.include_background_mask && background.is_none())
    {
        return Err("invalid fill dimensions or masks".into());
    }
    let seed = start.1 * w + start.0;
    if target[seed] == 0 {
        return Ok(FillOutcome::NotTarget);
    }
    let mut region = if o.adjacent {
        if let Some(initial) = initial {
            initial.to_vec()
        } else {
            let p = passable.ok_or("missing passable")?;
            let (x, y) = start;
            let mut starts = vec![(x as i64, y as i64)];
            if p[seed] == 0 {
                if x > 0 {
                    starts.push(((x - 1) as i64, y as i64));
                }
                if x + 1 < w {
                    starts.push(((x + 1) as i64, y as i64));
                }
                if y > 0 {
                    starts.push((x as i64, (y - 1) as i64));
                }
                if y + 1 < h {
                    starts.push((x as i64, (y + 1) as i64));
                }
            }
            let mut filled = flood_fill(p, w, h, &starts);
            filled[seed] = 1;
            filled
        }
    } else {
        target.to_vec()
    };
    if o.adjacent {
        if region[seed] == 0 {
            return Ok(FillOutcome::Empty);
        }
        if selection.is_none()
            && o.require_closed
            && (region[..w].iter().any(|v| *v != 0)
                || region[(h - 1) * w..].iter().any(|v| *v != 0)
                || region.chunks_exact(w).any(|r| r[0] != 0 || r[w - 1] != 0))
        {
            return Ok(FillOutcome::Open { region });
        }
        if let Some(gap) = gap {
            let (x0, y0, x1, y1) = bounds(&region, w, h).ok_or("empty seeded region")?;
            let (x0, y0, x1, y1) = (
                x0.saturating_sub(1),
                y0.saturating_sub(1),
                (x1 + 1).min(w),
                (y1 + 1).min(h),
            );
            let mut local = Vec::with_capacity((x1 - x0) * (y1 - y0));
            for y in y0..y1 {
                local.extend_from_slice(&region[y * w + x0..y * w + x1]);
            }
            let touch = dilate(&local, x1 - x0, y1 - y0, 1);
            let mut starts = Vec::new();
            for y in y0..y1 {
                for x in x0..x1 {
                    if touch[(y - y0) * (x1 - x0) + x - x0] != 0 && gap[y * w + x] != 0 {
                        starts.push((x as i64, y as i64));
                    }
                }
            }
            if !starts.is_empty() {
                merge(&mut region, &flood_fill(gap, w, h, &starts));
            }
        }
        if o.enclosed != Enclosed::None {
            let inverse: Vec<u8> = region.iter().map(|v| u8::from(*v == 0)).collect();
            let mut starts = Vec::new();
            for x in 0..w {
                if inverse[x] != 0 {
                    starts.push((x as i64, 0));
                }
                if inverse[(h - 1) * w + x] != 0 {
                    starts.push((x as i64, (h - 1) as i64));
                }
            }
            for y in 1..h.saturating_sub(1) {
                if inverse[y * w] != 0 {
                    starts.push((0, y as i64));
                }
                if inverse[y * w + w - 1] != 0 {
                    starts.push(((w - 1) as i64, y as i64));
                }
            }
            let outside = flood_fill(&inverse, w, h, &starts);
            for i in 0..n {
                if inverse[i] != 0
                    && outside[i] == 0
                    && ((rgba[i * 4 + 3] == 0) == (o.enclosed == Enclosed::InnerTransparent))
                {
                    region[i] = 1;
                }
            }
        }
    }
    if o.include_masks {
        let mut family = vec![0; n];
        for i in 0..n {
            let p = &rgba[i * 4..i * 4 + 4];
            family[i] = u8::from(
                (p[3] != 0
                    && o.mask_colors.iter().any(|c| {
                        (0..3)
                            .map(|k| {
                                let d = i32::from(p[k]) - i32::from(c[k]);
                                d * d
                            })
                            .sum::<i32>()
                            <= MASK_DISTANCE_SQUARED
                    }))
                    || (o.include_background_mask && background.is_some_and(|b| b[i] != 0)),
            );
            if selection.is_some_and(|s| s[i] == 0) {
                family[i] = 0;
            }
        }
        if o.adjacent {
            let mut starts = Vec::new();
            if let Some((x0, y0, x1, y1)) = bounds(&region, w, h) {
                for y in y0.saturating_sub(1)..(y1 + 1).min(h) {
                    for x in x0.saturating_sub(1)..(x1 + 1).min(w) {
                        let i = y * w + x;
                        if family[i] != 0
                            && ((x > 0 && region[i - 1] != 0)
                                || (x + 1 < w && region[i + 1] != 0)
                                || (y > 0 && region[i - w] != 0)
                                || (y + 1 < h && region[i + w] != 0))
                        {
                            starts.push((x as i64, y as i64));
                        }
                    }
                }
            }
            if !starts.is_empty() {
                merge(&mut region, &flood_fill(&family, w, h, &starts));
            }
        } else {
            merge(&mut region, &family);
        }
    }
    if o.adjacent && o.island_max_width > 0 {
        islands(&mut region, target, w, h, o.island_max_width);
    }
    if let Some(s) = selection {
        for i in 0..n {
            region[i] &= s[i];
        }
    }
    Ok(match bounds(&region, w, h) {
        None => FillOutcome::Empty,
        Some(bounds) => FillOutcome::Ok { region, bounds },
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn options() -> FillOptions {
        FillOptions {
            adjacent: true,
            require_closed: false,
            island_max_width: 0,
            include_masks: false,
            include_background_mask: false,
            mask_colors: vec![],
            enclosed: Enclosed::None,
        }
    }
    fn run(
        target: &[u8],
        pass: &[u8],
        initial: Option<&[u8]>,
        o: &FillOptions,
    ) -> Result<FillOutcome, String> {
        compute_fill_region(
            &[0; 36],
            3,
            3,
            (1, 1),
            target,
            Some(pass),
            None,
            None,
            None,
            initial,
            o,
        )
    }
    #[test]
    fn not_target() {
        assert_eq!(
            run(&[0; 9], &[1; 9], None, &options()),
            Ok(FillOutcome::NotTarget)
        );
    }
    #[test]
    fn seed_recovers_without_mutation() {
        let pass = [0; 9];
        let mut expected = vec![0; 9];
        expected[4] = 1;
        assert_eq!(
            run(&[1; 9], &pass, None, &options()),
            Ok(FillOutcome::Ok {
                region: expected,
                bounds: (1, 1, 2, 2)
            })
        );
        assert_eq!(pass, [0; 9]);
    }
    #[test]
    fn open_returns_region() {
        let mut o = options();
        o.require_closed = true;
        assert_eq!(
            run(&[1; 9], &[1; 9], None, &o),
            Ok(FillOutcome::Open { region: vec![1; 9] })
        );
    }
    #[test]
    fn empty_initial() {
        assert_eq!(
            run(&[1; 9], &[1; 9], Some(&[0; 9]), &options()),
            Ok(FillOutcome::Empty)
        );
    }
    #[test]
    fn invalid_lengths() {
        assert!(run(&[1; 8], &[1; 9], None, &options()).is_err());
    }
    #[test]
    fn nonadjacent() {
        let mut o = options();
        o.adjacent = false;
        assert_eq!(
            run(&[1; 9], &[0; 9], None, &o),
            Ok(FillOutcome::Ok {
                region: vec![1; 9],
                bounds: (0, 0, 3, 3)
            })
        );
    }
    #[test]
    fn diagonal_small_island() {
        let mut r = vec![0; 25];
        r[6] = 1;
        let mut t = r.clone();
        t[12] = 1;
        islands(&mut r, &t, 5, 5, 1);
        assert_eq!(r, t);
    }
    #[test]
    fn wide_island_rejected() {
        let mut r = vec![0; 36];
        r[7] = 1;
        let mut t = r.clone();
        for i in [14, 15, 20, 21] {
            t[i] = 1;
        }
        islands(&mut r, &t, 6, 6, 1);
        assert_eq!(r.iter().sum::<u8>(), 1);
    }
}
