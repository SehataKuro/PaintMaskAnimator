//! TGA（RLE 圧縮・32bit）の書き出し。Pillow の `save(format="TGA", compression="tga_rle")`
//! （`MainWindow.save_tga_image`）とバイト単位で同じファイルを作る。
//!
//! ヘッダーの属性は「アルファ 8bit・下から上」、行は下から順に書き、パケットは行を
//! またがない（Pillow の TgaRleEncode.c と同じ区切り方）。最後に TGA 2.0 のフッターを付ける。

use crate::Error;

/// ストレート RGBA8888 の画像を Pillow と同じ RLE の TGA にする。
pub fn encode_tga_rle(width: usize, height: usize, rgba: &[u8]) -> Result<Vec<u8>, Error> {
    if width == 0
        || height == 0
        || width > 0xffff
        || height > 0xffff
        || width.checked_mul(height).and_then(|n| n.checked_mul(4)) != Some(rgba.len())
    {
        return Err(Error::Invalid("TGA の寸法または画素数".into()));
    }
    let mut out = Vec::with_capacity(18 + rgba.len() / 2 + 26);
    out.extend_from_slice(&[0, 0, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
    out.extend_from_slice(&(width as u16).to_le_bytes());
    out.extend_from_slice(&(height as u16).to_le_bytes());
    out.extend_from_slice(&[32, 8]);
    let bgra = |x: usize, row: &[u8]| {
        let p = &row[x * 4..x * 4 + 4];
        [p[2], p[1], p[0], p[3]]
    };
    for y in (0..height).rev() {
        let row = &rgba[y * width * 4..(y + 1) * width * 4];
        let same = |a: usize| row[a * 4..a * 4 + 4] == row[(a + 1) * 4..(a + 2) * 4];
        let mut x = 0;
        while x < width {
            let start = x;
            let mut run = false;
            if x + 1 < width {
                let raw = !same(x);
                x += 1;
                let max_lookup = (x + 126).min(width - 1);
                if raw {
                    while x < max_lookup {
                        if !same(x) {
                            x += 1;
                        } else {
                            // 同じ画素が2つ続けば次の RLE パケットへ回す。
                            x -= 1;
                            break;
                        }
                    }
                } else {
                    run = true;
                    while x < max_lookup && same(x) {
                        x += 1;
                    }
                }
            }
            x += 1;
            let count = x - start;
            out.push(if run { 0x80 } else { 0 } | (count - 1) as u8);
            if run {
                out.extend_from_slice(&bgra(start, row));
            } else {
                for i in start..x {
                    out.extend_from_slice(&bgra(i, row));
                }
            }
        }
    }
    out.extend_from_slice(&[0; 8]);
    out.extend_from_slice(b"TRUEVISION-XFILE.\0");
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn packets_follow_pillow() {
        let a = [1, 2, 3, 255];
        let b = [4, 5, 6, 128];
        let c = [7, 8, 9, 0];
        let row1: Vec<u8> = [a, a, a, b, c, b, b].concat();
        let row0: Vec<u8> = [c; 7].concat();
        let data = encode_tga_rle(7, 2, &[row0, row1].concat()).unwrap();
        assert_eq!(
            &data[..18],
            &[0, 0, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 7, 0, 2, 0, 32, 8]
        );
        let body = &data[18..data.len() - 26];
        assert_eq!(
            body,
            &[
                0x82, 3, 2, 1, 255, // A×3（下の行が先）
                0x01, 6, 5, 4, 128, 9, 8, 7, 0, // B C（生）
                0x81, 6, 5, 4, 128, // B×2
                0x86, 9, 8, 7, 0, // 上の行 C×7
            ]
        );
        assert!(data.ends_with(b"\0\0\0\0\0\0\0\0TRUEVISION-XFILE.\0"));
    }

    #[test]
    fn long_runs_split_at_128() {
        let pixels = [9u8, 9, 9, 9].repeat(300);
        let data = encode_tga_rle(300, 1, &pixels).unwrap();
        let body = &data[18..data.len() - 26];
        assert_eq!(
            body,
            &[0xff, 9, 9, 9, 9, 0xff, 9, 9, 9, 9, 0xab, 9, 9, 9, 9]
        );
        let mut raw = Vec::new();
        for i in 0..130u8 {
            raw.extend_from_slice(&[i, 0, 0, 255]);
        }
        let data = encode_tga_rle(130, 1, &raw).unwrap();
        assert_eq!(data[18], 0x7f);
        assert_eq!(data[18 + 1 + 128 * 4], 0x01);
        assert!(encode_tga_rle(0, 1, &[]).is_err());
    }
}
