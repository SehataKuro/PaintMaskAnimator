//! 32 ビット画素の画像で、アルファが 0 でない画素の横の並び（行ごとの区間）を取り出す。

use crate::Error;

/// 1画素4バイトの画像の各行で、`alpha_offset` 番目のバイトが 0 でない画素の続きを
/// `(y, left, right)`（`right` は含まない）として、上の行から、行の中は左から返す。
/// `stride` は1行のバイト数で、`width * 4` 以上。
pub fn alpha_runs(
    data: &[u8],
    width: usize,
    height: usize,
    stride: usize,
    alpha_offset: usize,
) -> Result<Vec<(usize, usize, usize)>, Error> {
    let row_bytes = width
        .checked_mul(4)
        .ok_or(Error::Image("width is too large"))?;
    if alpha_offset >= 4 || stride < row_bytes {
        return Err(Error::Image(
            "stride or alpha offset does not fit the width",
        ));
    }
    let needed = if height == 0 {
        0
    } else {
        stride
            .checked_mul(height - 1)
            .and_then(|n| n.checked_add(row_bytes))
            .ok_or(Error::Image("image is too large"))?
    };
    if data.len() < needed {
        return Err(Error::Image("pixel buffer is shorter than the image"));
    }
    let mut runs = Vec::new();
    for y in 0..height {
        let row = &data[y * stride..y * stride + row_bytes];
        let mut start = None;
        for x in 0..width {
            let opaque = row[x * 4 + alpha_offset] != 0;
            match (opaque, start) {
                (true, None) => start = Some(x),
                (false, Some(left)) => {
                    runs.push((y, left, x));
                    start = None;
                }
                _ => {}
            }
        }
        if let Some(left) = start {
            runs.push((y, left, width));
        }
    }
    Ok(runs)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn image(rows: &[&[u8]], stride: usize) -> Vec<u8> {
        let mut data = vec![0u8; stride * rows.len()];
        for (y, row) in rows.iter().enumerate() {
            for (x, &alpha) in row.iter().enumerate() {
                data[y * stride + x * 4 + 3] = alpha;
                data[y * stride + x * 4] = 255;
            }
        }
        data
    }

    #[test]
    fn runs_follow_rows_and_include_the_right_edge() {
        let data = image(&[&[0, 9, 9, 0, 1], &[0, 0, 0, 0, 0], &[5, 0, 0, 0, 0]], 24);
        assert_eq!(
            alpha_runs(&data, 5, 3, 24, 3).unwrap(),
            vec![(0, 1, 3), (0, 4, 5), (2, 0, 1)]
        );
    }

    #[test]
    fn other_channels_and_padding_are_ignored() {
        let mut data = image(&[&[0, 0]], 12);
        data[8..12].copy_from_slice(&[0, 0, 0, 7]);
        assert!(alpha_runs(&data, 2, 1, 12, 3).unwrap().is_empty());
    }

    #[test]
    fn short_data_and_small_stride_are_rejected() {
        assert!(alpha_runs(&[0; 7], 2, 1, 8, 3).is_err());
        assert!(alpha_runs(&[0; 16], 2, 2, 4, 3).is_err());
        assert!(alpha_runs(&[0; 8], 2, 1, 8, 4).is_err());
        assert!(alpha_runs(&[], 0, 0, 0, 3).unwrap().is_empty());
    }
}
