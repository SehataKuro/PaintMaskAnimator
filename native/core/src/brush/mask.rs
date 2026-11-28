//! 塗る画素の 2 値の面。原点つきで、画像の外へはみ出してもよい。

/// 左上 `(x, y)` から `width × height` の、塗る（真）か塗らない（偽）かの面。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Mask {
    /// 左端の x 座標（画像の座標）。
    pub x: i64,
    /// 上端の y 座標（画像の座標）。
    pub y: i64,
    /// 幅。
    pub width: usize,
    /// 高さ。
    pub height: usize,
    bits: Vec<bool>,
}

impl Mask {
    /// すべて偽の面を作る。
    pub fn new(x: i64, y: i64, width: usize, height: usize) -> Self {
        Self {
            x,
            y,
            width,
            height,
            bits: vec![false; width * height],
        }
    }

    /// 行優先の真偽の並びから面を作る。長さが合わなければ `None`。
    pub fn from_bits(x: i64, y: i64, width: usize, height: usize, bits: Vec<bool>) -> Option<Self> {
        (bits.len() == width * height).then_some(Self {
            x,
            y,
            width,
            height,
            bits,
        })
    }

    /// 行優先の真偽の並び。
    pub fn bits(&self) -> &[bool] {
        &self.bits
    }

    /// 面の中の座標 `(column, row)` の値。範囲外は偽。
    pub fn get_local(&self, column: usize, row: usize) -> bool {
        column < self.width && row < self.height && self.bits[row * self.width + column]
    }

    /// 画像の座標 `(x, y)` の値。範囲外は偽。
    pub fn get(&self, x: i64, y: i64) -> bool {
        let (column, row) = (x - self.x, y - self.y);
        column >= 0 && row >= 0 && self.get_local(column as usize, row as usize)
    }

    /// 面の中の行 `row` の `[left, right)` を真にする。範囲外は切り捨てる。
    pub(crate) fn fill_span(&mut self, row: usize, left: i64, right: i64) {
        if row >= self.height {
            return;
        }
        let left = left.clamp(0, self.width as i64) as usize;
        let right = right.clamp(0, self.width as i64) as usize;
        if left < right {
            let start = row * self.width;
            self.bits[start + left..start + right].fill(true);
        }
    }

    /// 面の中の座標を真にする。範囲外は無視する。
    pub(crate) fn set_local(&mut self, column: i64, row: i64) {
        if column >= 0 && row >= 0 && (column as usize) < self.width && (row as usize) < self.height
        {
            self.bits[row as usize * self.width + column as usize] = true;
        }
    }

    /// 真の画素の数。
    pub fn count(&self) -> usize {
        self.bits.iter().filter(|&&bit| bit).count()
    }

    /// 真の画素が無ければ真。
    pub fn is_empty(&self) -> bool {
        !self.bits.contains(&true)
    }

    /// `other` と重なる所だけを残す（`other` の外は偽にする）。
    pub fn intersect(&mut self, other: &Mask) {
        for row in 0..self.height {
            for column in 0..self.width {
                let index = row * self.width + column;
                if self.bits[index] && !other.get(self.x + column as i64, self.y + row as i64) {
                    self.bits[index] = false;
                }
            }
        }
    }

    /// 行ごとの真の区間 `(row, left, right)`（`right` は含まない、面の中の座標）。
    /// 画素単位の筆先の輪郭（カーソル）を描くのに使う。
    pub fn runs(&self) -> Vec<(usize, usize, usize)> {
        let mut runs = Vec::new();
        for row in 0..self.height {
            let line = &self.bits[row * self.width..(row + 1) * self.width];
            let mut start = None;
            for (column, &bit) in line.iter().enumerate() {
                match (bit, start) {
                    (true, None) => start = Some(column),
                    (false, Some(left)) => {
                        runs.push((row, left, column));
                        start = None;
                    }
                    _ => {}
                }
            }
            if let Some(left) = start {
                runs.push((row, left, self.width));
            }
        }
        runs
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn spans_are_clipped_and_runs_are_listed() {
        let mut mask = Mask::new(10, 20, 4, 2);
        mask.fill_span(0, -3, 2);
        mask.fill_span(1, 3, 9);
        mask.fill_span(5, 0, 4);
        assert_eq!(mask.runs(), vec![(0, 0, 2), (1, 3, 4)]);
        assert!(mask.get(11, 20) && !mask.get(12, 20) && mask.get(13, 21));
        let mut other = Mask::new(11, 20, 5, 2);
        other.fill_span(0, 0, 5);
        mask.intersect(&other);
        assert_eq!(mask.runs(), vec![(0, 1, 2)]);
    }
}
