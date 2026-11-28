//! MainLineRepaint（`line_ops.main_line_repaint`）の 1 枚分：選んだ使用色の線（LINE）と、
//! 線を抜いて隙間をとなりの色で埋めた塗り（Paint）に分ける。
//!
//! 層の追加・元の層を隠す・絵番号の付け方は文書の操作で、呼び出し側が行う。
use crate::image::RgbaImage;
use crate::project::qt_alpha::qt_premultiplied_roundtrip;
use crate::Error;

/// 分けた 2 枚。どちらも `PmaImage` と同じ正規化を通したストレート RGBA。
#[derive(Clone, Debug)]
pub struct LineSplit {
    /// 選んだ色の画素だけの画像。
    pub line: RgbaImage,
    /// 線を抜き、となりの色で埋めた画像。
    pub paint: RgbaImage,
    /// 線の画素があったか。
    pub has_line: bool,
    /// 塗りの画素が残ったか。
    pub has_paint: bool,
}

/// 分ける前の確認（`main_line_repaint` の最初の走査）：選んだ色が画像にあるか。
pub fn contains_any(image: &RgbaImage, selected: &[[u8; 3]]) -> bool {
    image
        .rgba()
        .chunks_exact(4)
        .any(|pixel| pixel[3] > 0 && selected.iter().any(|rgb| pixel[..3] == rgb[..]))
}

/// 1 枚を LINE と Paint に分ける。線の隙間は 12 回まで、上下左右の一番多い色で埋める。
pub fn split_line(image: &RgbaImage, selected: &[[u8; 3]]) -> Result<LineSplit, Error> {
    let (width, height) = (image.width(), image.height());
    let pixels = image.rgba();
    let line_mask: Vec<bool> = pixels
        .chunks_exact(4)
        .map(|pixel| pixel[3] > 0 && selected.iter().any(|rgb| pixel[..3] == rgb[..]))
        .collect();
    let mut line = vec![0u8; pixels.len()];
    let mut paint = pixels.to_vec();
    let mut pending = Vec::new();
    for (index, _) in line_mask.iter().enumerate().filter(|(_, &v)| v) {
        line[index * 4..index * 4 + 4].copy_from_slice(&pixels[index * 4..index * 4 + 4]);
        paint[index * 4 + 3] = 0;
        pending.push(index);
    }
    for _ in 0..12 {
        if pending.is_empty() {
            break;
        }
        let mut remaining = Vec::new();
        for &index in &pending {
            let (x, y) = (index % width, index / width);
            // 左・右・上・下の順に数え、同数なら先に出た色。
            let mut counts: Vec<([u8; 3], usize)> = Vec::new();
            let neighbours = [
                (x > 0).then(|| index - 1),
                (x + 1 < width).then(|| index + 1),
                (y > 0).then(|| index - width),
                (y + 1 < height).then(|| index + width),
            ];
            for next in neighbours.into_iter().flatten() {
                if paint[next * 4 + 3] == 0 {
                    continue;
                }
                let colour = [paint[next * 4], paint[next * 4 + 1], paint[next * 4 + 2]];
                match counts.iter_mut().find(|(known, _)| *known == colour) {
                    Some((_, count)) => *count += 1,
                    None => counts.push((colour, 1)),
                }
            }
            let mut best: Option<([u8; 3], usize)> = None;
            for &(colour, count) in &counts {
                if best.is_none_or(|(_, best_count)| count > best_count) {
                    best = Some((colour, count));
                }
            }
            let Some((fill, _)) = best else {
                remaining.push(index);
                continue;
            };
            paint[index * 4..index * 4 + 3].copy_from_slice(&fill);
            paint[index * 4 + 3] = 255;
        }
        pending = remaining;
    }
    let has_line = line_mask.iter().any(|&v| v);
    let has_paint = paint.chunks_exact(4).any(|pixel| pixel[3] > 0);
    qt_premultiplied_roundtrip(&mut line, width);
    qt_premultiplied_roundtrip(&mut paint, width);
    Ok(LineSplit {
        line: RgbaImage::from_rgba(width, height, &line)?,
        paint: RgbaImage::from_rgba(width, height, &paint)?,
        has_line,
        has_paint,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::image_ops::test_support::image;

    #[test]
    fn line_gap_is_filled_from_neighbours() {
        let k = [0, 0, 0, 255];
        let r = [255, 0, 0, 255];
        let source = image(3, 1, &[r, k, r]);
        assert!(contains_any(&source, &[[0, 0, 0]]));
        let split = split_line(&source, &[[0, 0, 0]]).unwrap();
        assert!(split.has_line && split.has_paint);
        assert_eq!(split.line.rgba(), &[0, 0, 0, 0, 0, 0, 0, 255, 0, 0, 0, 0]);
        assert_eq!(split.paint.rgba(), &[r, r, r].concat());
    }
}
