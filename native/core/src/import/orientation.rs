//! JPEG の EXIF の向き（サブビューの `QImageReader.setAutoTransform(True)`）。
//!
//! 取り込み（`decode_image`）は向きを無視する（Qt 版も `setAutoTransform(False)`）。
//! サブビューだけは Qt と同じく向きに従って回す・裏返す。

/// JPEG の EXIF の向き（1〜8）。無い・読めなければ 1。
pub fn jpeg_orientation(data: &[u8]) -> u16 {
    exif_orientation(data)
        .filter(|o| (1..=8).contains(o))
        .unwrap_or(1)
}

fn exif_orientation(data: &[u8]) -> Option<u16> {
    if data.get(..2)? != [0xFF, 0xD8] {
        return None;
    }
    let mut at = 2usize;
    loop {
        if *data.get(at)? != 0xFF {
            return None;
        }
        let marker = *data.get(at + 1)?;
        if marker == 0xFF {
            at += 1;
            continue;
        }
        // 画素の始まり（SOS）か終わりより先には EXIF は無い。
        if marker == 0xDA || marker == 0xD9 {
            return None;
        }
        if (0xD0..=0xD7).contains(&marker) || marker == 0x01 {
            at += 2;
            continue;
        }
        let length = u16::from_be_bytes([*data.get(at + 2)?, *data.get(at + 3)?]) as usize;
        if length < 2 {
            return None;
        }
        let segment = data.get(at + 4..at + 2 + length)?;
        if marker == 0xE1 && segment.starts_with(b"Exif\0\0") {
            return tiff_orientation(&segment[6..]);
        }
        at += 2 + length;
    }
}

fn tiff_orientation(tiff: &[u8]) -> Option<u16> {
    let little = match tiff.get(..2)? {
        b"II" => true,
        b"MM" => false,
        _ => return None,
    };
    let u16_at = |i: usize| -> Option<u16> {
        let b = [*tiff.get(i)?, *tiff.get(i + 1)?];
        Some(if little {
            u16::from_le_bytes(b)
        } else {
            u16::from_be_bytes(b)
        })
    };
    let u32_at = |i: usize| -> Option<u32> {
        let b = [
            *tiff.get(i)?,
            *tiff.get(i + 1)?,
            *tiff.get(i + 2)?,
            *tiff.get(i + 3)?,
        ];
        Some(if little {
            u32::from_le_bytes(b)
        } else {
            u32::from_be_bytes(b)
        })
    };
    if u16_at(2)? != 42 {
        return None;
    }
    let ifd = u32_at(4)? as usize;
    let count = u16_at(ifd)? as usize;
    for i in 0..count {
        let entry = ifd + 2 + i * 12;
        if u16_at(entry)? == 0x0112 {
            // 型は SHORT（3）。値は項目の中に入っている。
            return (u16_at(entry + 2)? == 3)
                .then(|| u16_at(entry + 8))
                .flatten();
        }
    }
    None
}

/// RGBA（幅 × 高さ × 4）を EXIF の向きに従って直す。戻りは (幅, 高さ, 画素)。
pub fn apply_orientation(
    orientation: u16,
    width: usize,
    height: usize,
    rgba: &[u8],
) -> (usize, usize, Vec<u8>) {
    if !(2..=8).contains(&orientation) || rgba.len() != width * height * 4 {
        return (width, height, rgba.to_vec());
    }
    let swap = orientation >= 5;
    let (out_w, out_h) = if swap {
        (height, width)
    } else {
        (width, height)
    };
    let mut out = vec![0u8; rgba.len()];
    for y in 0..out_h {
        for x in 0..out_w {
            let (sx, sy) = match orientation {
                2 => (width - 1 - x, y),
                3 => (width - 1 - x, height - 1 - y),
                4 => (x, height - 1 - y),
                5 => (y, x),
                6 => (y, height - 1 - x),
                7 => (width - 1 - y, height - 1 - x),
                _ => (width - 1 - y, x),
            };
            let s = (sy * width + sx) * 4;
            let d = (y * out_w + x) * 4;
            out[d..d + 4].copy_from_slice(&rgba[s..s + 4]);
        }
    }
    (out_w, out_h, out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn jpeg_with_orientation(value: u16, little: bool) -> Vec<u8> {
        let mut tiff = Vec::new();
        let p16 = |v: u16| {
            if little {
                v.to_le_bytes()
            } else {
                v.to_be_bytes()
            }
        };
        let p32 = |v: u32| {
            if little {
                v.to_le_bytes()
            } else {
                v.to_be_bytes()
            }
        };
        tiff.extend_from_slice(if little { b"II" } else { b"MM" });
        tiff.extend_from_slice(&p16(42));
        tiff.extend_from_slice(&p32(8));
        tiff.extend_from_slice(&p16(1));
        tiff.extend_from_slice(&p16(0x0112));
        tiff.extend_from_slice(&p16(3));
        tiff.extend_from_slice(&p32(1));
        tiff.extend_from_slice(&p16(value));
        tiff.extend_from_slice(&[0, 0]);
        tiff.extend_from_slice(&p32(0));
        let mut segment = b"Exif\0\0".to_vec();
        segment.extend_from_slice(&tiff);
        let mut data = vec![0xFF, 0xD8, 0xFF, 0xE0, 0, 4, 0, 0, 0xFF, 0xE1];
        data.extend_from_slice(&((segment.len() + 2) as u16).to_be_bytes());
        data.extend_from_slice(&segment);
        data.extend_from_slice(&[0xFF, 0xDA, 0, 2, 0xFF, 0xD9]);
        data
    }

    #[test]
    fn reads_the_orientation_tag() {
        assert_eq!(jpeg_orientation(&jpeg_with_orientation(6, true)), 6);
        assert_eq!(jpeg_orientation(&jpeg_with_orientation(8, false)), 8);
        assert_eq!(jpeg_orientation(&jpeg_with_orientation(9, false)), 1);
        assert_eq!(jpeg_orientation(&[0xFF, 0xD8, 0xFF, 0xD9]), 1);
        assert_eq!(jpeg_orientation(b"\x89PNG"), 1);
    }

    #[test]
    fn rotates_and_mirrors_like_qt() {
        // 2×1: [A, B]
        let px = [1, 0, 0, 255, 2, 0, 0, 255];
        let first = |o| {
            let (w, h, out) = apply_orientation(o, 2, 1, &px);
            (w, h, out.chunks(4).map(|c| c[0]).collect::<Vec<_>>())
        };
        assert_eq!(first(1), (2, 1, vec![1, 2]));
        assert_eq!(first(2), (2, 1, vec![2, 1]));
        assert_eq!(first(3), (2, 1, vec![2, 1]));
        assert_eq!(first(4), (2, 1, vec![1, 2]));
        // 時計回りに 90°：左の A が上に来る。
        assert_eq!(first(6), (1, 2, vec![1, 2]));
        // 反時計回りに 90°：右の B が上に来る。
        assert_eq!(first(8), (1, 2, vec![2, 1]));
        assert_eq!(first(5), (1, 2, vec![1, 2]));
        assert_eq!(first(7), (1, 2, vec![2, 1]));
    }
}
