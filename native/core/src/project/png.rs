//! Qt と画素が一致する PNG のエンコードと 8bit 以下のデコード。
use std::io::Cursor;

use png::{BitDepth, ColorType, Transformations};

use crate::Error;

/// 乗算していない RGBA8888 の行優先画像。Qt の乗算済み形式への変換は呼び出し側で行う。
#[derive(Debug)]
pub struct RgbaImage {
    /// 画像の幅（画素）。
    pub width: usize,
    /// 画像の高さ（画素）。
    pub height: usize,
    /// R, G, B, A の順に並べた全画素。
    pub pixels: Vec<u8>,
}

fn invalid(error: impl std::fmt::Display) -> Error {
    Error::Invalid(error.to_string())
}

fn allocate(size: usize) -> Result<Vec<u8>, Error> {
    let mut bytes = Vec::new();
    bytes.try_reserve_exact(size).map_err(invalid)?;
    bytes.resize(size, 0);
    Ok(bytes)
}

/// PNG の事前検査だけを行い、画素を確保せず幅と高さを返す。
///
/// 8bit 以下のグレー・RGB・RGBA・パレット（tRNS とインターレースを含む）は、
/// Qt (`project_io._decode_png_qt`) と同じ乗算処理に渡せる画素を返す。
/// 16bit・APNG は Unsupported、破損・ゼロ寸法は Invalid、
/// width * height * 4 が max_bytes を超える場合は TooLarge を返す。
pub fn png_size(data: &[u8], max_bytes: u64) -> Result<(usize, usize), Error> {
    // acTL が IDAT より後にある不正な APNG も Rust では扱わない。
    if !data.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Err(invalid("シグネチャ"));
    }
    let mut chunks = &data[8..];
    loop {
        if chunks.len() < 12 {
            return Err(invalid("途切れたチャンク"));
        }
        let size = u32::from_be_bytes([chunks[0], chunks[1], chunks[2], chunks[3]]) as usize;
        let end = size
            .checked_add(12)
            .filter(|&n| n <= chunks.len())
            .ok_or_else(|| invalid("途切れたチャンク"))?;
        let kind = &chunks[4..8];
        if kind == b"acTL" {
            return Err(Error::Unsupported("APNG".into()));
        }
        if kind == b"IEND" {
            break;
        }
        chunks = &chunks[end..];
    }
    // IHDR から先に寸法を調べ、デコーダ内部の確保よりも前に上限を判定する。
    if data.len() < 33 || &data[12..16] != b"IHDR" {
        return Err(invalid("IHDR"));
    }
    let width = u32::from_be_bytes([data[16], data[17], data[18], data[19]]);
    let height = u32::from_be_bytes([data[20], data[21], data[22], data[23]]);
    if width == 0 || height == 0 {
        return Err(invalid("ゼロ寸法"));
    }
    if data[24] == 16 {
        return Err(Error::Unsupported("16bit".into()));
    }
    if u64::from(width)
        .checked_mul(u64::from(height))
        .and_then(|n| n.checked_mul(4))
        .is_none_or(|n| n > max_bytes)
    {
        return Err(Error::TooLarge {
            width,
            height,
            max_bytes,
        });
    }
    let size = u64::from(width) * u64::from(height) * 4;
    usize::try_from(size).map_err(|_| Error::TooLarge {
        width,
        height,
        max_bytes,
    })?;
    Ok((width as usize, height as usize))
}

/// 呼び出し側の width * height * 4 バイトに RGBA8888 を直接展開する。
/// 長さ不一致は Invalid。失敗時の out の内容は未規定。
/// 対応形式・寸法エラーは png_size と同じ。色空間は適用しない。
/// 画素用バッファの追加確保は行わず、末尾からその場で広げる。
pub fn decode_png_into(data: &[u8], max_bytes: u64, out: &mut [u8]) -> Result<(), Error> {
    let (width, height) = png_size(data, max_bytes)?;
    if out.len() != width * height * 4 {
        return Err(invalid("展開後の画素数"));
    }
    let mut decoder = png::Decoder::new(Cursor::new(data));
    decoder.set_transformations(Transformations::EXPAND);
    decoder.set_limits(png::Limits {
        bytes: usize::try_from(max_bytes).unwrap_or(usize::MAX),
    });
    let mut reader = decoder.read_info().map_err(invalid)?;
    let info = reader.info();
    let (width, height) = (info.width, info.height);
    if width == 0 || height == 0 {
        return Err(invalid("ゼロ寸法"));
    }
    if info.bit_depth == BitDepth::Sixteen {
        return Err(Error::Unsupported("16bit".into()));
    }
    let too_large = || Error::TooLarge {
        width,
        height,
        max_bytes,
    };
    let size = u64::from(width)
        .checked_mul(u64::from(height))
        .and_then(|n| n.checked_mul(4))
        .filter(|&n| n <= max_bytes)
        .ok_or_else(too_large)?;
    let size = usize::try_from(size).map_err(|_| too_large())?;
    let decoded_size = reader.output_buffer_size().ok_or_else(too_large)?;
    if decoded_size > out.len() {
        return Err(invalid("展開後の画素数"));
    }
    let output = reader
        .next_frame(&mut out[..decoded_size])
        .map_err(invalid)?;
    reader.finish().map_err(invalid)?;
    let channels = match output.color_type {
        ColorType::Grayscale => 1,
        ColorType::GrayscaleAlpha => 2,
        ColorType::Rgb => 3,
        ColorType::Rgba => 4,
        ColorType::Indexed => return Err(invalid("パレットが未展開")),
    };
    if output.bit_depth != BitDepth::Eight || output.buffer_size() != size / 4 * channels {
        return Err(invalid("展開後の画素数"));
    }
    for index in (0..size / 4).rev() {
        let source = &out[index * channels..(index + 1) * channels];
        let rgba = match output.color_type {
            ColorType::Grayscale => [source[0], source[0], source[0], 255],
            ColorType::GrayscaleAlpha => [source[0], source[0], source[0], source[1]],
            ColorType::Rgb => [source[0], source[1], source[2], 255],
            ColorType::Rgba => [source[0], source[1], source[2], source[3]],
            ColorType::Indexed => return Err(invalid("パレットが未展開")),
        };
        out[index * 4..(index + 1) * 4].copy_from_slice(&rgba);
    }
    Ok(())
}

/// PNG を乗算していない RGBA8888 に展開する。対応形式とエラーは png_size と同じ。
/// 色空間は適用せず、画素用 Vec は 1 回だけ確保する。
pub fn decode_png(data: &[u8], max_bytes: u64) -> Result<RgbaImage, Error> {
    let (width, height) = png_size(data, max_bytes)?;
    let mut pixels = allocate(width * height * 4)?;
    decode_png_into(data, max_bytes, &mut pixels)?;
    Ok(RgbaImage {
        width,
        height,
        pixels,
    })
}

/// 乗算していない RGBA8888 を RGBA 8bit・非インターレースの PNG にする。
/// dots_per_meter が Some ならメートル単位の pHYs を書き、ほかの付加チャンクは書かない。
/// Qt (`project_io._encode_png_qt`) と画素が一致するのは、テキストのない乗算済み
/// ARGB32 を Qt で RGBA8888 に変換して渡した場合。色空間の除去も呼び出し側で行う。
/// ゼロ寸法・u32 を超える寸法・画素数のオーバーフロー・長さ不一致、
/// PNG 書き出し失敗は Invalid を返す。画素全体の複製は作らない。
pub fn encode_png(
    width: usize,
    height: usize,
    rgba: &[u8],
    dots_per_meter: Option<(u32, u32)>,
) -> Result<Vec<u8>, Error> {
    if width == 0
        || height == 0
        || width.checked_mul(height).and_then(|n| n.checked_mul(4)) != Some(rgba.len())
    {
        return Err(invalid("寸法または画素数"));
    }
    let width = u32::try_from(width).map_err(invalid)?;
    let height = u32::try_from(height).map_err(invalid)?;
    let mut data = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut data, width, height);
        encoder.set_color(ColorType::Rgba);
        encoder.set_depth(BitDepth::Eight);
        // 4見本・7回中央値: 合計1,023,553 bytes（Qt 1,033,200）。
        // 線30.786ms/塗26.848ms/ノイズ43.767ms/4K線91.152ms。
        // 全見本でQt以下。Level6〜9 / Upの候補では容量合計が最小。
        encoder.set_deflate_compression(png::DeflateCompression::Level(7));
        encoder.set_filter(png::Filter::Up);
        encoder.set_pixel_dims(dots_per_meter.map(|(xppu, yppu)| png::PixelDimensions {
            xppu,
            yppu,
            unit: png::Unit::Meter,
        }));
        let mut writer = encoder.write_header().map_err(invalid)?;
        writer.write_image_data(rgba).map_err(invalid)?;
        writer.finish().map_err(invalid)?;
    }
    Ok(data)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encoding_roundtrip_and_chunks() {
        let pixels = [1, 2, 3, 0, 255, 128, 64, 127];
        for dimensions in [None, Some((2835, 3780)), Some((0, 0))] {
            let data = encode_png(2, 1, &pixels, dimensions).unwrap();
            assert_eq!(decode_png(&data, 8).unwrap().pixels, pixels);
            let mut chunks = &data[8..];
            let mut kinds = Vec::new();
            while !chunks.is_empty() {
                let size = u32::from_be_bytes(chunks[..4].try_into().unwrap()) as usize;
                let kind = &chunks[4..8];
                kinds.push(kind.to_vec());
                if kind == b"pHYs" {
                    let (x, y) = dimensions.unwrap();
                    assert_eq!(&chunks[8..12], &x.to_be_bytes());
                    assert_eq!(&chunks[12..16], &y.to_be_bytes());
                    assert_eq!(chunks[16], 1);
                }
                chunks = &chunks[size + 12..];
            }
            assert_eq!(kinds.first().unwrap(), b"IHDR");
            assert_eq!(kinds.last().unwrap(), b"IEND");
            let start = if dimensions.is_some() {
                assert_eq!(kinds[1], b"pHYs");
                2
            } else {
                1
            };
            assert!(kinds[start..kinds.len() - 1].iter().all(|k| k == b"IDAT"));
            assert!(kinds.len() > start + 1);
        }
    }

    #[test]
    fn encoding_rejects_invalid_dimensions_and_length() {
        for (w, h, pixels) in [
            (0, 1, &[][..]),
            (1, 0, &[][..]),
            (1, 1, &[][..]),
            (1, 1, &[0; 5][..]),
            (usize::MAX, 2, &[][..]),
            (u32::MAX as usize, 1, &[][..]),
        ] {
            assert!(encode_png(w, h, pixels, None).is_err());
        }
    }

    fn encode(color: ColorType, depth: BitDepth, pixels: &[u8]) -> Vec<u8> {
        let mut data = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut data, 2, 1);
            encoder.set_color(color);
            encoder.set_depth(depth);
            if color == ColorType::Indexed {
                encoder.set_palette(vec![10, 20, 30, 40, 50, 60]);
                encoder.set_trns(vec![0, 127]);
            }
            encoder
                .write_header()
                .unwrap()
                .write_image_data(pixels)
                .unwrap();
        }
        data
    }

    #[test]
    fn into_checks_length_and_expands_in_place() {
        for (color, input, expected) in [
            (
                ColorType::Grayscale,
                vec![10, 20],
                vec![10, 10, 10, 255, 20, 20, 20, 255],
            ),
            (
                ColorType::GrayscaleAlpha,
                vec![10, 20, 30, 40],
                vec![10, 10, 10, 20, 30, 30, 30, 40],
            ),
            (
                ColorType::Rgb,
                vec![1, 2, 3, 4, 5, 6],
                vec![1, 2, 3, 255, 4, 5, 6, 255],
            ),
            (
                ColorType::Indexed,
                vec![0, 1],
                vec![10, 20, 30, 0, 40, 50, 60, 127],
            ),
        ] {
            let data = encode(color, BitDepth::Eight, &input);
            assert_eq!(png_size(&data, 8).unwrap(), (2, 1));
            let mut guarded = [99; 10];
            decode_png_into(&data, 8, &mut guarded[1..9]).unwrap();
            assert_eq!(&guarded[1..9], expected);
            assert_eq!((guarded[0], guarded[9]), (99, 99));
            for length in [0, 7, 9] {
                let mut out = vec![99; length];
                assert!(matches!(
                    decode_png_into(&data, 8, &mut out),
                    Err(Error::Invalid(_))
                ));
                assert_eq!(out, vec![99; length]);
            }
        }
    }

    #[test]
    fn expands_pixels() {
        for (color, depth, input, expected) in [
            (
                ColorType::Rgba,
                BitDepth::Eight,
                vec![1, 2, 3, 4, 5, 6, 7, 8],
                vec![1, 2, 3, 4, 5, 6, 7, 8],
            ),
            (
                ColorType::Grayscale,
                BitDepth::One,
                vec![0x40],
                vec![0, 0, 0, 255, 255, 255, 255, 255],
            ),
            (
                ColorType::Indexed,
                BitDepth::Eight,
                vec![0, 1],
                vec![10, 20, 30, 0, 40, 50, 60, 127],
            ),
        ] {
            let image = decode_png(&encode(color, depth, &input), 1_000_000).unwrap();
            assert_eq!((image.width, image.height), (2, 1));
            assert_eq!(image.pixels, expected);
        }
    }

    #[test]
    fn expands_low_depth_and_transparency() {
        for (depth, input, expected) in [
            (BitDepth::Two, 0x30, vec![0, 0, 0, 255, 255, 255, 255, 255]),
            (BitDepth::Four, 0x0f, vec![0, 0, 0, 255, 255, 255, 255, 255]),
        ] {
            assert_eq!(
                decode_png(&encode(ColorType::Grayscale, depth, &[input]), 1_000_000)
                    .unwrap()
                    .pixels,
                expected
            );
        }
        for (color, input, transparency, expected) in [
            (
                ColorType::GrayscaleAlpha,
                vec![10, 20, 30, 40],
                None,
                vec![10, 10, 10, 20, 30, 30, 30, 40],
            ),
            (
                ColorType::Grayscale,
                vec![10, 20],
                Some(vec![0, 10]),
                vec![10, 10, 10, 0, 20, 20, 20, 255],
            ),
            (
                ColorType::Rgb,
                vec![10, 20, 30, 40, 50, 60],
                Some(vec![0, 10, 0, 20, 0, 30]),
                vec![10, 20, 30, 0, 40, 50, 60, 255],
            ),
        ] {
            let mut data = Vec::new();
            {
                let mut encoder = png::Encoder::new(&mut data, 2, 1);
                encoder.set_color(color);
                encoder.set_depth(BitDepth::Eight);
                if let Some(trns) = transparency {
                    encoder.set_trns(trns);
                }
                encoder
                    .write_header()
                    .unwrap()
                    .write_image_data(&input)
                    .unwrap();
            }
            assert_eq!(decode_png(&data, 1_000_000).unwrap().pixels, expected);
        }
    }

    #[test]
    fn rejects_apng() {
        let mut data = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut data, 1, 1);
            encoder.set_color(ColorType::Rgba);
            encoder.set_depth(BitDepth::Eight);
            encoder.set_animated(1, 0).unwrap();
            encoder
                .write_header()
                .unwrap()
                .write_image_data(&[1, 2, 3, 4])
                .unwrap();
        }
        assert!(matches!(
            decode_png(&data, 1_000_000),
            Err(Error::Unsupported(_))
        ));
    }

    #[test]
    fn rejects_unsupported_and_invalid() {
        let sixteen = encode(ColorType::Grayscale, BitDepth::Sixteen, &[0, 0, 255, 255]);
        assert!(matches!(
            decode_png(&sixteen, 1_000_000),
            Err(Error::Unsupported(_))
        ));
        let data = encode(ColorType::Rgba, BitDepth::Eight, &[0; 8]);
        assert!(matches!(decode_png(&data, 7), Err(Error::TooLarge { .. })));
        for input in [&data[..data.len() / 2], &data[..8], &[]] {
            assert!(decode_png(input, 1_000_000).is_err());
        }
        let mut zero = data.clone();
        zero[16..24].fill(0);
        assert!(decode_png(&zero, 1_000_000).is_err());
    }
}
