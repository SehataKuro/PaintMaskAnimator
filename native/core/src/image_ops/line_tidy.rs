//! 線整え（#64）：2 値の線の形を、仕上げで正しいとされる形へ整える。
//!
//! Python 版 `line_tidy` の 4 つの規則（角だけで接する段をつなぐ・突き出た 1 画素を消す・
//! 段の重なりが厚い所の 1 画素を消す・2×2 の塊が角でつながった 45° の線をそろえる）を
//! 同じ順番で当てる。すべての画素を同時に判定するので、結果は走査の順番によらない。
use super::{finish, MaskRef, PixelEdit};
use crate::image::{Rect, RgbaImage};
use crate::Error;

/// 2×2 の塊が角でつながった 45° の線の直し方（`DIAGONAL_MODES`）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Diagonal {
    /// 足して太さをそろえる（`"add"`）。
    Add,
    /// 消して 1 画素幅にする（`"remove"`）。
    Remove,
    /// 直さない（`None`）。
    Keep,
}

/// 行優先の真偽マスク。範囲外は偽。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Mask {
    width: usize,
    height: usize,
    values: Vec<bool>,
}

impl Mask {
    /// すべて偽のマスク。
    pub fn new(width: usize, height: usize) -> Self {
        Self {
            width,
            height,
            values: vec![false; width * height],
        }
    }

    /// 値から作る。長さは `width * height`。
    pub fn from_values(width: usize, height: usize, values: Vec<bool>) -> Self {
        assert_eq!(values.len(), width * height);
        Self {
            width,
            height,
            values,
        }
    }

    /// 行優先の値。
    pub fn values(&self) -> &[bool] {
        &self.values
    }

    /// 真の画素の数。
    pub fn count(&self) -> usize {
        self.values.iter().filter(|&&value| value).count()
    }

    fn at(&self, y: isize, x: isize) -> bool {
        y >= 0
            && x >= 0
            && (y as usize) < self.height
            && (x as usize) < self.width
            && self.values[y as usize * self.width + x as usize]
    }

    fn set(&mut self, y: isize, x: isize) {
        if y >= 0 && x >= 0 && (y as usize) < self.height && (x as usize) < self.width {
            self.values[y as usize * self.width + x as usize] = true;
        }
    }

    fn map(&self, f: impl Fn(isize, isize) -> bool) -> Self {
        let mut values = Vec::with_capacity(self.values.len());
        for y in 0..self.height as isize {
            for x in 0..self.width as isize {
                values.push(f(y, x));
            }
        }
        Self::from_values(self.width, self.height, values)
    }

    fn zip(&self, other: &Self, f: impl Fn(bool, bool) -> bool) -> Self {
        let values = self
            .values
            .iter()
            .zip(&other.values)
            .map(|(&a, &b)| f(a, b))
            .collect();
        Self::from_values(self.width, self.height, values)
    }

    fn and_not(&self, other: &Self) -> Self {
        self.zip(other, |a, b| a && !b)
    }

    fn or(&self, other: &Self) -> Self {
        self.zip(other, |a, b| a || b)
    }

    fn any(&self) -> bool {
        self.values.iter().any(|&value| value)
    }

    /// `np.rot90(m)`（反時計回りに 90°）。
    fn rot90_once(&self) -> Self {
        let (w, h) = (self.width, self.height);
        let mut values = Vec::with_capacity(w * h);
        for i in 0..w {
            for j in 0..h {
                values.push(self.values[j * w + (w - 1 - i)]);
            }
        }
        Self::from_values(h, w, values)
    }

    fn rot90(&self, k: i32) -> Self {
        let mut out = self.clone();
        for _ in 0..k.rem_euclid(4) {
            out = out.rot90_once();
        }
        out
    }

    /// `np.fliplr`。
    fn fliplr(&self) -> Self {
        let w = self.width;
        self.map(|y, x| self.values[y as usize * w + (w - 1 - x as usize)])
    }

    fn neighbour_count(&self) -> Vec<i8> {
        let mut count = Vec::with_capacity(self.values.len());
        for y in 0..self.height as isize {
            for x in 0..self.width as isize {
                let mut n = 0;
                for dy in -1..=1 {
                    for dx in -1..=1 {
                        if (dy != 0 || dx != 0) && self.at(y + dy, x + dx) {
                            n += 1;
                        }
                    }
                }
                count.push(n);
            }
        }
        count
    }
}

/// パターン 1：足す画素。段と段が角だけで接している所の、角の 1 画素。
pub fn corner_joins(mask: &Mask) -> Mask {
    let neighbours = mask.neighbour_count();
    let width = mask.width as isize;
    let mut add = Mask::new(mask.width, mask.height);
    for (dy, dx) in [(1isize, 1isize), (1, -1)] {
        for y in 0..mask.height as isize {
            for x in 0..width {
                let m = |oy: isize, ox: isize| mask.at(y + oy, x + ox);
                let found = m(0, 0)
                    && m(dy, dx)
                    && !m(0, dx)
                    && !m(dy, 0)
                    && (m(0, -dx) || m(-dy, 0))
                    && (m(dy, 2 * dx) || m(2 * dy, dx));
                if !found {
                    continue;
                }
                let (ay, ax) = (y, x + dx);
                let (by, bx) = (y + dy, x);
                let prefer_b =
                    neighbours[(by * width + bx) as usize] > neighbours[(ay * width + ax) as usize];
                if prefer_b {
                    add.set(by, bx);
                } else {
                    add.set(ay, ax);
                }
            }
        }
    }
    add.and_not(mask)
}

/// パターン 2：消す画素。線の側面から 1 画素だけ突き出たもの。
pub fn spurs(mask: &Mask) -> Mask {
    let neighbours = mask.neighbour_count();
    let width = mask.width as isize;
    mask.map(|y, x| {
        if !mask.at(y, x) || neighbours[(y * width + x) as usize] != 3 {
            return false;
        }
        [(1isize, 0isize), (-1, 0), (0, 1), (0, -1)]
            .into_iter()
            .any(|(dy, dx)| {
                let (py, px) = (dx, dy);
                mask.at(y + dy, x + dx)
                    && mask.at(y + dy + py, x + dx + px)
                    && mask.at(y + dy - py, x + dx - px)
            })
    })
}

fn run_lengths(mask: &Mask) -> (Vec<i32>, Vec<i32>) {
    let (w, h) = (mask.width, mask.height);
    let mut left = vec![0; w * h];
    let mut right = vec![0; w * h];
    for y in 0..h {
        let mut running = 0;
        for x in 0..w {
            running = if mask.values[y * w + x] {
                running + 1
            } else {
                0
            };
            left[y * w + x] = running;
        }
        running = 0;
        for x in (0..w).rev() {
            running = if mask.values[y * w + x] {
                running + 1
            } else {
                0
            };
            right[y * w + x] = running;
        }
    }
    (left, right)
}

fn thick_steps_canonical(mask: &Mask) -> Mask {
    let m = |y: isize, x: isize| mask.at(y, x);
    let found = mask.map(|y, x| {
        let upper_end = m(y, x) && m(y, x - 1) && m(y, x - 2) && !m(y, x + 1);
        let lower = m(y + 1, x) && m(y + 1, x - 1) && m(y + 1, x + 1) && !m(y + 1, x - 2);
        let thin = !m(y - 1, x) && !m(y - 1, x - 1) && !m(y + 2, x) && !m(y + 2, x - 1);
        upper_end && lower && thin
    });
    let mut remove = Mask::new(mask.width, mask.height);
    if !found.any() {
        return remove;
    }
    let (left, right) = run_lengths(mask);
    let w = mask.width;
    for (index, _) in found.values.iter().enumerate().filter(|(_, &v)| v) {
        let (y, x) = (index / w, index % w);
        let upper = left[index];
        let lower = right[(y + 1) * w + x - 1];
        if upper > lower {
            remove.set(y as isize, x as isize);
        } else {
            remove.set(y as isize + 1, x as isize - 1);
        }
    }
    remove
}

/// パターン 3：消す画素。段の重なりが 2 画素ある所の、片方の段の端。
pub fn thick_steps(mask: &Mask) -> Mask {
    let mut current = mask.clone();
    let mut removed = Mask::new(mask.width, mask.height);
    for k in 0..4 {
        for flip in [false, true] {
            let forward = if flip {
                current.rot90(k).fliplr()
            } else {
                current.rot90(k)
            };
            let found = thick_steps_canonical(&forward);
            let back = if flip {
                found.fliplr().rot90(-k)
            } else {
                found.rot90(-k)
            };
            let found = back.zip(&current, |a, b| a && b);
            current = current.and_not(&found);
            removed = removed.or(&found);
        }
    }
    removed
}

fn diagonal_blocks_canonical(mask: &Mask, mode: Diagonal) -> (Mask, Mask) {
    let m = |y: isize, x: isize| mask.at(y, x);
    let mut add = Mask::new(mask.width, mask.height);
    let mut remove = Mask::new(mask.width, mask.height);
    for y in 0..mask.height as isize {
        for x in 0..mask.width as isize {
            let block = m(y, x) && m(y, x + 1) && m(y + 1, x) && m(y + 1, x + 1);
            if !block {
                continue;
            }
            let sides = m(y - 1, x)
                || m(y - 1, x + 1)
                || m(y, x - 1)
                || m(y + 1, x - 1)
                || m(y + 2, x)
                || m(y + 2, x + 1)
                || m(y, x + 2)
                || m(y + 1, x + 2);
            let before = m(y - 1, x - 1);
            let after = m(y + 2, x + 2);
            if sides || !(before || after) {
                continue;
            }
            if mode == Diagonal::Remove {
                remove.set(y, x + 1);
                remove.set(y + 1, x);
                continue;
            }
            if before {
                add.set(y - 1, x);
                add.set(y, x - 1);
            }
            if after {
                add.set(y + 1, x + 2);
                add.set(y + 2, x + 1);
            }
        }
    }
    (add.and_not(mask), remove)
}

/// パターン 4：`(足す画素, 消す画素)`。右下がりと右上がりの両方を見る。
pub fn diagonal_blocks(mask: &Mask, mode: Diagonal) -> (Mask, Mask) {
    let mut current = mask.clone();
    let mut added = Mask::new(mask.width, mask.height);
    let mut removed = Mask::new(mask.width, mask.height);
    if mode == Diagonal::Keep {
        return (added, removed);
    }
    for flip in [false, true] {
        let view = if flip {
            current.fliplr()
        } else {
            current.clone()
        };
        let (mut add, mut remove) = diagonal_blocks_canonical(&view, mode);
        if flip {
            add = add.fliplr();
            remove = remove.fliplr();
        }
        let remove = remove.zip(&current, |a, b| a && b);
        current = current.or(&add).and_not(&remove);
        added = added.or(&add);
        removed = removed.or(&remove);
    }
    (added.and_not(&removed), removed)
}

/// `(足す画素, 消す画素)`。両方を当てた線は `(mask | add) & !remove`。
pub fn tidy_mask(mask: &Mask, diagonal: Diagonal) -> (Mask, Mask) {
    let mut remove = spurs(mask);
    let (mut add, diagonal_remove) = diagonal_blocks(&mask.and_not(&remove), diagonal);
    remove = remove.or(&diagonal_remove);
    remove = remove.or(&thick_steps(&mask.or(&add).and_not(&remove)));
    let current = mask.or(&add).and_not(&remove);
    add = add.or(&corner_joins(&current));
    (add.and_not(mask), remove)
}

fn boundary_colour(
    rgba: &[u8],
    width: usize,
    height: usize,
    y: usize,
    x: usize,
    exclude: [u8; 3],
) -> [u8; 4] {
    // 出てきた順に数え、同数なら先に出た色（dict の max と同じ）。
    let mut found: Vec<([u8; 4], usize)> = Vec::new();
    for (dy, dx) in [(-1isize, 0isize), (1, 0), (0, -1), (0, 1)] {
        let (ny, nx) = (y as isize + dy, x as isize + dx);
        if ny < 0 || nx < 0 || ny as usize >= height || nx as usize >= width {
            continue;
        }
        let index = (ny as usize * width + nx as usize) * 4;
        let mut pixel = [
            rgba[index],
            rgba[index + 1],
            rgba[index + 2],
            rgba[index + 3],
        ];
        if pixel[3] == 0 {
            pixel = [0; 4];
        } else if pixel[..3] == exclude {
            continue;
        }
        match found.iter_mut().find(|(colour, _)| *colour == pixel) {
            Some((_, count)) => *count += 1,
            None => found.push((pixel, 1)),
        }
    }
    let mut best: Option<([u8; 4], usize)> = None;
    for &(colour, count) in &found {
        if best.is_none_or(|(_, best_count)| count > best_count) {
            best = Some((colour, count));
        }
    }
    best.map_or([0; 4], |(colour, _)| colour)
}

fn colour_mask(rgba: &[u8], width: usize, height: usize, rgb: [u8; 3]) -> Mask {
    Mask::from_values(
        width,
        height,
        rgba.chunks_exact(4)
            .map(|pixel| pixel[3] > 0 && pixel[..3] == rgb)
            .collect(),
    )
}

/// `line_tidy.tidy_lines` の結果：整えた RGBA と、足した画素・消した画素。
#[derive(Clone, Debug)]
pub struct TidyResult {
    /// 整えたストレート RGBA。
    pub rgba: Vec<u8>,
    /// 足した画素。
    pub added: Mask,
    /// 消した画素。
    pub removed: Mask,
}

/// `colours` の線を整える。`region`（画像と同じ大きさにそろえる）の中だけを変える。
///
/// 足した画素は線の色になり、消した画素は、となりにある線以外の色（無ければ透明）に
/// なる。色ごとに別々に判定する。
pub fn tidy_lines_rgba(
    rgba: &[u8],
    width: usize,
    height: usize,
    colours: &[[u8; 3]],
    diagonal: Diagonal,
    region: Option<MaskRef<'_>>,
) -> TidyResult {
    let mut out = rgba.to_vec();
    let mut added = Mask::new(width, height);
    let mut removed = Mask::new(width, height);
    let region = region.map(|region| {
        Mask::from_values(
            width,
            height,
            (0..width * height)
                .map(|index| region.get(index % width, index / width))
                .collect(),
        )
    });
    for &rgb in colours {
        let mask = colour_mask(rgba, width, height, rgb);
        if !mask.any() {
            continue;
        }
        let (mut add, mut remove) = tidy_mask(&mask, diagonal);
        // ほかの線の色の上には足さない（色トレス線と主線が接する所など）。
        let mut others = Mask::new(width, height);
        for &other in colours {
            if other != rgb {
                others = others.or(&colour_mask(rgba, width, height, other));
            }
        }
        add = add.and_not(&others);
        if let Some(region) = &region {
            add = add.zip(region, |a, b| a && b);
            remove = remove.zip(region, |a, b| a && b);
        }
        for (index, _) in remove.values.iter().enumerate().filter(|(_, &v)| v) {
            let colour = boundary_colour(rgba, width, height, index / width, index % width, rgb);
            out[index * 4..index * 4 + 4].copy_from_slice(&colour);
        }
        for (index, _) in add.values.iter().enumerate().filter(|(_, &v)| v) {
            out[index * 4..index * 4 + 3].copy_from_slice(&rgb);
            out[index * 4 + 3] = 255;
        }
        added = added.or(&add);
        removed = removed.or(&remove);
    }
    TidyResult {
        rgba: out,
        added,
        removed,
    }
}

/// 線整えの結果。`edit` は直す所が無ければ `None`。
#[derive(Clone, Debug)]
pub struct TidyEdit {
    /// 書き換えた画像（変えた画素数は足した数と消した数の和）。
    pub edit: Option<PixelEdit>,
    /// 足した画素数。
    pub added: usize,
    /// 消した画素数。
    pub removed: usize,
}

/// 線整え（`MainWindow.line_ops.tidy_lines` の 1 セル分）。
pub fn tidy_lines(
    image: &RgbaImage,
    colours: &[[u8; 3]],
    diagonal: Diagonal,
    region: Option<MaskRef<'_>>,
) -> Result<TidyEdit, Error> {
    if let Some(region) = region {
        region.check()?;
    }
    let result = tidy_lines_rgba(
        image.rgba(),
        image.width(),
        image.height(),
        colours,
        diagonal,
        region,
    );
    let (added, removed) = (result.added.count(), result.removed.count());
    if added == 0 && removed == 0 {
        return Ok(TidyEdit {
            edit: None,
            added,
            removed,
        });
    }
    Ok(TidyEdit {
        edit: Some(finish(image, result.rgba, added + removed)?),
        added,
        removed,
    })
}

/// 線の色か（暗い色：RGB の各値が 48 以下、または赤・緑・青などの原色）。
pub fn is_line_colour(rgb: [u8; 3]) -> bool {
    let dark = rgb.iter().all(|&v| v <= 48);
    let strong = rgb.iter().filter(|&&v| v >= 215).count();
    let weak = rgb.iter().filter(|&&v| v <= 60).count();
    dark || ((1..=2).contains(&strong) && strong + weak == 3)
}

/// 線整えの対象色（`tidy_line_colours`）。
///
/// `selected`（使用色パネルで選んだ色）から白を除いた色が残ればそれを昇順で、無ければ
/// 画像の不透明な色のうち線の色を昇順で返す。
pub fn tidy_line_colours(image: &RgbaImage, selected: &[[u8; 3]]) -> Vec<[u8; 3]> {
    let mut chosen: Vec<[u8; 3]> = selected
        .iter()
        .copied()
        .filter(|&rgb| rgb != [255, 255, 255])
        .collect();
    chosen.sort_unstable();
    chosen.dedup();
    if !chosen.is_empty() {
        return chosen;
    }
    let mut colours: Vec<u32> = image
        .rgba()
        .chunks_exact(4)
        .filter(|pixel| pixel[3] > 0)
        .map(super::pack)
        .collect();
    colours.sort_unstable();
    colours.dedup();
    colours
        .into_iter()
        .map(super::unpack)
        .filter(|&rgb| is_line_colour(rgb))
        .collect()
}

/// 線整えのプレビュー（`line_tidy_preview`）：変える画素を色分けした拡大画像
/// （足す＝赤、消す＝青、透明は白）。直す所が無ければ `None`。
#[derive(Clone, Debug)]
pub struct TidyPreview {
    /// 拡大したストレート RGBA。
    pub image: RgbaImage,
    /// 元の画像で切り出した範囲。
    pub crop: Rect,
    /// 拡大率。
    pub scale: usize,
    /// 足す画素数。
    pub added: usize,
    /// 消す画素数。
    pub removed: usize,
}

/// プレビューの設定（Python 版の既定は拡大 8・余白 6・上限 480）。
#[derive(Clone, Copy, Debug)]
pub struct PreviewOptions {
    /// 拡大率の上限。
    pub scale: usize,
    /// 変える画素のまわりの余白。
    pub margin: usize,
    /// 拡大後の長辺の上限。
    pub limit: usize,
}

impl Default for PreviewOptions {
    fn default() -> Self {
        Self {
            scale: 8,
            margin: 6,
            limit: 480,
        }
    }
}

/// 線整えのプレビュー画像。
pub fn tidy_preview(
    image: &RgbaImage,
    colours: &[[u8; 3]],
    diagonal: Diagonal,
    region: Option<MaskRef<'_>>,
    options: PreviewOptions,
) -> Result<Option<TidyPreview>, Error> {
    if let Some(region) = region {
        region.check()?;
    }
    let (width, height) = (image.width(), image.height());
    let rgba = image.rgba();
    let result = tidy_lines_rgba(rgba, width, height, colours, diagonal, region);
    let changed = result.added.or(&result.removed);
    let (mut x0, mut y0, mut x1, mut y1) = (usize::MAX, usize::MAX, 0, 0);
    for (index, _) in changed.values.iter().enumerate().filter(|(_, &v)| v) {
        let (x, y) = (index % width, index / width);
        x0 = x0.min(x);
        y0 = y0.min(y);
        x1 = x1.max(x);
        y1 = y1.max(y);
    }
    if x0 == usize::MAX {
        return Ok(None);
    }
    let margin = options.margin;
    let (y0, y1) = (y0.saturating_sub(margin), (y1 + margin + 1).min(height));
    let (x0, x1) = (x0.saturating_sub(margin), (x1 + margin + 1).min(width));
    let (cw, ch) = (x1 - x0, y1 - y0);
    let scale = options.scale.min(options.limit / cw.max(ch).max(1)).max(1);
    let mut out = Vec::with_capacity(cw * scale * ch * scale * 4);
    for y in y0..y1 {
        let mut row = Vec::with_capacity(cw * scale * 4);
        for x in x0..x1 {
            let index = y * width + x;
            let mut pixel = [
                rgba[index * 4],
                rgba[index * 4 + 1],
                rgba[index * 4 + 2],
                rgba[index * 4 + 3],
            ];
            if pixel[3] == 0 {
                pixel = [255, 255, 255, 255];
            }
            if result.added.values[index] {
                pixel = [230, 40, 40, 255];
            }
            if result.removed.values[index] {
                pixel = [40, 90, 230, 255];
            }
            for _ in 0..scale {
                row.extend_from_slice(&pixel);
            }
        }
        for _ in 0..scale {
            out.extend_from_slice(&row);
        }
    }
    Ok(Some(TidyPreview {
        image: RgbaImage::from_rgba(cw * scale, ch * scale, &out)?,
        crop: Rect {
            x: x0,
            y: y0,
            width: cw,
            height: ch,
        },
        scale,
        added: result.added.count(),
        removed: result.removed.count(),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mask(rows: &[&str]) -> Mask {
        let height = rows.len();
        let width = rows[0].len();
        Mask::from_values(
            width,
            height,
            rows.iter()
                .flat_map(|row| row.bytes().map(|b| b == b'#'))
                .collect(),
        )
    }

    fn render(mask: &Mask) -> Vec<String> {
        mask.values
            .chunks(mask.width)
            .map(|row| row.iter().map(|&v| if v { '#' } else { '.' }).collect())
            .collect()
    }

    #[test]
    fn rot90_matches_numpy_orientation() {
        let m = mask(&["#..", "..."]);
        // np.rot90([[1,0,0],[0,0,0]]) == [[0,0],[0,0],[1,0]]
        assert_eq!(render(&m.rot90(1)), ["..", "..", "#."]);
        assert_eq!(m.rot90(1).rot90(-1), m);
        assert_eq!(render(&m.fliplr()), ["..#", "..."]);
    }

    #[test]
    fn corner_between_steps_is_joined() {
        let m = mask(&["......", ".##...", "...##.", "......"]);
        let add = corner_joins(&m);
        assert_eq!(add.count(), 1);
        // 45° の 1 画素幅の線は変えない。
        let diagonal = mask(&["#...", ".#..", "..#.", "...#"]);
        assert_eq!(corner_joins(&diagonal).count(), 0);
    }

    #[test]
    fn spur_is_removed_but_ends_are_kept() {
        let m = mask(&[".....", ".###.", "..#..", "....."]);
        assert_eq!(render(&spurs(&m)), [".....", ".....", "..#..", "....."]);
        let line = mask(&["#####"]);
        assert_eq!(spurs(&line).count(), 0);
    }

    #[test]
    fn diagonal_modes() {
        let m = mask(&["#.....", ".##...", ".##...", "...#..", "......"]);
        let (add, remove) = diagonal_blocks(&m, Diagonal::Remove);
        assert_eq!((add.count(), remove.count()), (0, 2));
        let (add, remove) = diagonal_blocks(&m, Diagonal::Add);
        assert_eq!((add.count(), remove.count()), (4, 0));
        let (add, remove) = diagonal_blocks(&m, Diagonal::Keep);
        assert_eq!((add.count(), remove.count()), (0, 0));
    }

    #[test]
    fn line_colours() {
        assert!(is_line_colour([0, 0, 0]));
        assert!(is_line_colour([255, 0, 0]));
        assert!(is_line_colour([230, 230, 20]));
        assert!(!is_line_colour([255, 255, 255]));
        assert!(!is_line_colour([128, 128, 128]));
    }

    #[test]
    fn tidy_fills_corner_with_line_colour() {
        let rows = ["......", ".##...", "...##.", "......"];
        let m = mask(&rows);
        let pixels: Vec<u8> = m
            .values
            .iter()
            .flat_map(|&v| {
                if v {
                    [0, 0, 0, 255]
                } else {
                    [255, 255, 255, 255]
                }
            })
            .collect();
        let image = RgbaImage::from_rgba(6, 4, &pixels).unwrap();
        let tidy = tidy_lines(&image, &[[0, 0, 0]], Diagonal::Add, None).unwrap();
        assert_eq!((tidy.added, tidy.removed), (1, 0));
        let edit = tidy.edit.unwrap();
        assert_eq!(edit.changed.unwrap().width, 1);
        let preview = tidy_preview(
            &image,
            &[[0, 0, 0]],
            Diagonal::Add,
            None,
            PreviewOptions::default(),
        )
        .unwrap()
        .unwrap();
        assert_eq!(preview.crop.width * preview.scale, preview.image.width());
        // 範囲の外なら変えない。
        let outside = vec![0u8; 24];
        let region = MaskRef {
            data: &outside,
            width: 6,
            height: 4,
        };
        assert!(
            tidy_lines(&image, &[[0, 0, 0]], Diagonal::Add, Some(region))
                .unwrap()
                .edit
                .is_none()
        );
    }
}
