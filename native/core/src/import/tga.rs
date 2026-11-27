//! TGA の読み込み（Pillow の TgaImagePlugin と同じ解釈）。
//!
//! Python 版は Qt の読み込みを先に試すが、Qt 6 の TGA は 24bit の無圧縮しか読めず、
//! ほかは Pillow で読む。どちらも同じ画素になるので、ここでは Pillow の解釈だけを使う。
//! 対応：フルカラー（種別 2・10）の 24・32bit と、グレー（種別 3・11）の 8bit。
//! 32bit はアルファ付き。原点（下から上・右から左）の印に従って並べ直す。

use crate::Error;

fn invalid(reason: &str) -> Error {
    Error::Invalid(format!("TGA: {reason}"))
}

/// TGA のヘッダーから寸法を読む（画素は確保しない）。
pub fn tga_size(data: &[u8]) -> Result<(usize, usize), Error> {
    if data.len() < 18 {
        return Err(invalid("ヘッダーが短い"));
    }
    let width = u16::from_le_bytes([data[12], data[13]]) as usize;
    let height = u16::from_le_bytes([data[14], data[15]]) as usize;
    let image_type = data[2];
    let depth = data[16];
    let colormap_type = data[1];
    if colormap_type > 1 || ![2, 3, 10, 11].contains(&image_type) {
        return Err(Error::Unsupported("TGA の種別".into()));
    }
    let supported = match image_type {
        2 | 10 => depth == 24 || depth == 32,
        _ => depth == 8,
    };
    if !supported {
        return Err(Error::Unsupported("TGA の色深度".into()));
    }
    if width == 0 || height == 0 {
        return Err(invalid("ゼロ寸法"));
    }
    Ok((width, height))
}

/// TGA をストレート RGBA8888 にする。
pub fn decode_tga(data: &[u8]) -> Result<(usize, usize, Vec<u8>), Error> {
    let (width, height) = tga_size(data)?;
    let image_type = data[2];
    let depth = data[16] as usize;
    let flags = data[17];
    let id_length = data[0] as usize;
    let colormap_length = u16::from_le_bytes([data[5], data[6]]) as usize;
    let colormap_entry = data[7] as usize;
    let mut at = 18 + id_length;
    if data[1] == 1 {
        at += colormap_length * colormap_entry.div_ceil(8);
    }
    let bpp = depth / 8;
    let count = width * height;
    let size = count * bpp;
    let mut raw = Vec::new();
    raw.try_reserve_exact(size).map_err(|_| invalid("メモリ"))?;
    let body = data.get(at..).ok_or_else(|| invalid("途切れた画素"))?;
    if image_type >= 9 {
        let mut pos = 0;
        while raw.len() < size {
            let header = *body.get(pos).ok_or_else(|| invalid("途切れた画素"))?;
            pos += 1;
            let n = (header as usize & 0x7f) + 1;
            if header & 0x80 != 0 {
                let pixel = body
                    .get(pos..pos + bpp)
                    .ok_or_else(|| invalid("途切れた画素"))?;
                pos += bpp;
                for _ in 0..n {
                    raw.extend_from_slice(pixel);
                }
            } else {
                let pixels = body
                    .get(pos..pos + n * bpp)
                    .ok_or_else(|| invalid("途切れた画素"))?;
                pos += n * bpp;
                raw.extend_from_slice(pixels);
            }
        }
        raw.truncate(size);
    } else {
        raw.extend_from_slice(body.get(..size).ok_or_else(|| invalid("途切れた画素"))?);
    }
    let top_down = flags & 0x20 != 0;
    let right_to_left = flags & 0x10 != 0;
    let mut out = vec![0u8; count * 4];
    for y in 0..height {
        let source_row = if top_down { y } else { height - 1 - y };
        for x in 0..width {
            let source_x = if right_to_left { width - 1 - x } else { x };
            let p = &raw[(source_row * width + source_x) * bpp..][..bpp];
            let rgba = match bpp {
                4 => [p[2], p[1], p[0], p[3]],
                3 => [p[2], p[1], p[0], 255],
                _ => [p[0], p[0], p[0], 255],
            };
            out[(y * width + x) * 4..][..4].copy_from_slice(&rgba);
        }
    }
    Ok((width, height, out))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::export::tga::encode_tga_rle;

    #[test]
    fn reads_back_what_the_export_writes() {
        let pixels: Vec<u8> = (0..5 * 3 * 4).map(|i| (i * 37 % 256) as u8).collect();
        let data = encode_tga_rle(5, 3, &pixels).unwrap();
        assert_eq!(decode_tga(&data).unwrap(), (5, 3, pixels));
    }

    #[test]
    fn uncompressed_top_down_24bit() {
        let mut data = vec![0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 0, 1, 0, 24, 0x20];
        data.extend_from_slice(&[1, 2, 3, 4, 5, 6]);
        assert_eq!(
            decode_tga(&data).unwrap(),
            (2, 1, vec![3, 2, 1, 255, 6, 5, 4, 255])
        );
        data[16] = 16;
        assert!(matches!(decode_tga(&data), Err(Error::Unsupported(_))));
    }
}
