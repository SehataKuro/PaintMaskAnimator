//! 連番の並べ方と代表フレーム（`utils.natural_path_key`・`color_ops.reference_frame_indices`）。

use std::cmp::Ordering;

/// 対応する画像の拡張子（「画像フォルダーを読み込む」の `supported_suffixes`）。
pub const IMAGE_SUFFIXES: [&str; 4] = ["png", "jpg", "jpeg", "tga"];

/// 2値化の手本にする代表フレームの数（`REFERENCE_FRAME_COUNT`）。
pub const REFERENCE_FRAME_COUNT: usize = 4;

#[derive(Clone, Debug, PartialEq, Eq)]
enum Part {
    Text(String),
    /// 数字の並び（先頭の 0 を除いたもの）。
    Number(String),
}

impl Ord for Part {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self, other) {
            (Part::Number(a), Part::Number(b)) => a.len().cmp(&b.len()).then_with(|| a.cmp(b)),
            (Part::Text(a), Part::Text(b)) => a.cmp(b),
            // re.split は文字・数字を交互に返すので、同じ位置は同じ種類になる。
            (Part::Text(_), Part::Number(_)) => Ordering::Greater,
            (Part::Number(_), Part::Text(_)) => Ordering::Less,
        }
    }
}

impl PartialOrd for Part {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// ファイル名の自然順の鍵（`natural_path_key`：数字は数として、ほかは小文字で比べる）。
pub fn natural_key(path: &str) -> Vec<impl Ord + Clone + std::fmt::Debug> {
    let name = path.rsplit(['/', '\\']).next().unwrap_or(path);
    let mut parts = Vec::new();
    let mut text = String::new();
    let mut digits = String::new();
    for ch in name.chars() {
        if ch.is_ascii_digit() {
            if digits.is_empty() {
                parts.push(Part::Text(std::mem::take(&mut text).to_lowercase()));
            }
            digits.push(ch);
        } else {
            if !digits.is_empty() {
                let trimmed = digits.trim_start_matches('0');
                parts.push(Part::Number(trimmed.to_string()));
                digits.clear();
            }
            text.push(ch);
        }
    }
    if !digits.is_empty() {
        parts.push(Part::Number(digits.trim_start_matches('0').to_string()));
        parts.push(Part::Text(String::new()));
    } else {
        parts.push(Part::Text(text.to_lowercase()));
    }
    parts
}

/// パスを自然順に並べる（同じ鍵は元の順のまま）。
pub fn sort_natural(paths: &mut [String]) {
    paths.sort_by_cached_key(|p| natural_key(p));
}

/// 拡張子が読み込める画像か。
pub fn is_image_path(path: &str) -> bool {
    let name = path.rsplit(['/', '\\']).next().unwrap_or(path);
    let suffix = crate::export::cut_folder::path_suffix(name).to_lowercase();
    IMAGE_SUFFIXES
        .iter()
        .any(|s| suffix.strip_prefix('.') == Some(s))
}

/// `total` 枚の連番から代表フレームの位置を選ぶ（1枚目を除く、`reference_frame_indices`）。
pub fn reference_frame_indices(total: usize, count: usize) -> Vec<usize> {
    if total <= 1 || count == 0 {
        return Vec::new();
    }
    let mut picked = Vec::new();
    for step in 1..=count {
        let value = (step * (total - 1)) as f64 / count as f64;
        let index = (value.round_ties_even() as usize).clamp(1, total - 1);
        if !picked.contains(&index) {
            picked.push(index);
        }
    }
    picked
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn natural_order() {
        let mut paths: Vec<String> = [
            "a10.png",
            "a2.png",
            "A1.png",
            "b.png",
            "a02.png",
            "x/a1b.png",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        sort_natural(&mut paths);
        assert_eq!(
            paths,
            [
                "A1.png",
                "x/a1b.png",
                "a2.png",
                "a02.png",
                "a10.png",
                "b.png"
            ]
        );
        assert!(is_image_path("C:\\x\\a.JPEG"));
        assert!(!is_image_path("a.psd"));
    }

    #[test]
    fn reference_frames() {
        assert_eq!(reference_frame_indices(1, 4), Vec::<usize>::new());
        assert_eq!(reference_frame_indices(2, 4), vec![1]);
        assert_eq!(reference_frame_indices(10, 4), vec![2, 4, 7, 9]);
    }
}
