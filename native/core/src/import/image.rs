//! 画像ファイルの読み込みと、置く前の変換・キャンバスへの置き方
//! （`canvas_image_import.ImageImportMixin` の `_read_image_file`・`convert_import_image`・
//! `_make_white_transparent`・`make_border_background_transparent`・`_placed_imported_image`）。
//!
//! Python は画像を Qt の乗算済み ARGB32 で持ち、変換のたびにストレート（RGBA8888）との間を
//! 行き来する。半透明の画素はその往復で丸められるので、ここも乗算済みの画素
//! （[`PremulImage`]）を持ち、ストレートへは Qt と同じ割り算（aarch64 の NEON 版、
//! [`qt_unpremultiply`]）で戻す。

use crate::color_stats::border_background;
use crate::compose::{premultiply, PremulImage};
use crate::export::thumbnail::qt_unpremultiply;
use crate::image::RgbaImage;
use crate::project::png::{decode_png, png_size};
use crate::Error;

use super::tga::{decode_tga, tga_size};

/// 読み込める画像の一辺の上限（`constants.MAX_IMAGE_DIMENSION`）。
pub const MAX_IMAGE_DIMENSION: usize = 16384;
/// 読み込める画像の画素数の上限（`constants.MAX_SINGLE_IMAGE_PIXELS`）。
pub const MAX_SINGLE_IMAGE_PIXELS: usize = MAX_IMAGE_DIMENSION * MAX_IMAGE_DIMENSION;

/// 画像の形式（中身で判断する、`setDecideFormatFromContent(True)`）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ImageKind {
    /// PNG。
    Png,
    /// JPEG。
    Jpeg,
    /// TGA（識別子がないので、PNG・JPEG でなければ TGA として読む）。
    Tga,
}

/// 中身から形式を決める。
pub fn image_kind(data: &[u8]) -> ImageKind {
    if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        ImageKind::Png
    } else if data.starts_with(&[0xff, 0xd8, 0xff]) {
        ImageKind::Jpeg
    } else {
        ImageKind::Tga
    }
}

/// 読み込みの失敗（`_read_image_file` の文言）。
#[derive(Debug)]
pub enum ReadError {
    /// 宣言された寸法が上限を超える。
    TooLarge {
        /// 幅。
        width: usize,
        /// 高さ。
        height: usize,
    },
    /// 読めない（理由）。
    Decode(String),
}

impl std::fmt::Display for ReadError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ReadError::TooLarge { width, height } => {
                write!(f, "画像サイズが上限を超えています。\n{width} × {height}px")
            }
            ReadError::Decode(reason) => write!(f, "{reason}"),
        }
    }
}

impl std::error::Error for ReadError {}

fn check_size(width: usize, height: usize) -> Result<(), ReadError> {
    if width < 1
        || height < 1
        || width > MAX_IMAGE_DIMENSION
        || height > MAX_IMAGE_DIMENSION
        || width * height > MAX_SINGLE_IMAGE_PIXELS
    {
        return Err(ReadError::TooLarge { width, height });
    }
    Ok(())
}

fn decode_error(error: impl std::fmt::Display) -> ReadError {
    ReadError::Decode(error.to_string())
}

fn decode_jpeg(data: &[u8]) -> Result<(usize, usize, Vec<u8>), ReadError> {
    use zune_jpeg::zune_core::bytestream::ZCursor;
    use zune_jpeg::zune_core::colorspace::ColorSpace;
    use zune_jpeg::zune_core::options::DecoderOptions;

    let options = DecoderOptions::default()
        .jpeg_set_out_colorspace(ColorSpace::RGBA)
        .set_max_width(MAX_IMAGE_DIMENSION)
        .set_max_height(MAX_IMAGE_DIMENSION);
    let mut decoder = zune_jpeg::JpegDecoder::new_with_options(ZCursor::new(data), options);
    decoder.decode_headers().map_err(decode_error)?;
    let (width, height) = decoder
        .dimensions()
        .ok_or_else(|| decode_error("JPEG の寸法"))?;
    check_size(width, height)?;
    let pixels = decoder.decode().map_err(decode_error)?;
    if pixels.len() != width * height * 4 {
        return Err(decode_error("JPEG の画素数"));
    }
    Ok((width, height, pixels))
}

/// 画像ファイルのバイト列をストレート RGBA8888 にする（ファイルの値のまま）。
///
/// 宣言された寸法を先に調べ、上限（一辺 16384・画素数 16384²）を超えれば画素を確保しない。
/// JPEG は EXIF の向きを無視する（Python も `setAutoTransform(False)`）。
pub fn decode_image(data: &[u8]) -> Result<(usize, usize, Vec<u8>), ReadError> {
    match image_kind(data) {
        ImageKind::Png => {
            let (width, height) = png_size(data, u64::MAX).map_err(decode_error)?;
            check_size(width, height)?;
            let image = decode_png(data, u64::MAX).map_err(decode_error)?;
            Ok((image.width, image.height, image.pixels))
        }
        ImageKind::Jpeg => decode_jpeg(data),
        ImageKind::Tga => {
            let (width, height) = tga_size(data).map_err(decode_error)?;
            check_size(width, height)?;
            decode_tga(data).map_err(decode_error)
        }
    }
}

/// ストレート RGBA を Qt の乗算済み ARGB32 にする（`convertToFormat(ARGB32_Premultiplied)`）。
pub fn to_premultiplied(width: usize, height: usize, rgba: &[u8]) -> Result<PremulImage, Error> {
    let mut pixels = Vec::new();
    pixels
        .try_reserve_exact(rgba.len())
        .map_err(|_| Error::Image("画素バッファを確保できません"))?;
    for pixel in rgba.chunks_exact(4) {
        pixels.extend_from_slice(&premultiply([pixel[0], pixel[1], pixel[2], pixel[3]]));
    }
    PremulImage::from_premultiplied(width, height, pixels)
}

/// 画像ファイルを読み、Qt と同じ乗算済みの画素にする（`_read_image_file`）。
pub fn read_image(data: &[u8]) -> Result<PremulImage, ReadError> {
    let (width, height, rgba) = decode_image(data)?;
    to_premultiplied(width, height, &rgba).map_err(decode_error)
}

/// 不透明度が 0 でない純白 (#FFFFFF) を透明にする（`_make_white_transparent`）。
pub fn make_white_transparent(image: &PremulImage) -> Result<PremulImage, Error> {
    let mut rgba = qt_unpremultiply(image);
    for pixel in rgba.chunks_exact_mut(4) {
        if pixel[..3] == [255, 255, 255] && pixel[3] != 0 {
            pixel[3] = 0;
        }
    }
    to_premultiplied(image.width(), image.height(), &rgba)
}

/// 外周からつながる明るい背景を透明にする（JPEG 用、Issue #10、
/// `make_border_background_transparent`）。背景が見つからなければ元の画像のまま。
pub fn make_border_background_transparent(
    image: &PremulImage,
    tolerance: i32,
) -> Result<PremulImage, Error> {
    let (width, height) = (image.width(), image.height());
    let rgba = qt_unpremultiply(image);
    // color_ops.detect_opaque_border_background：半透明を含む画像は対象外。
    if rgba.chunks_exact(4).any(|p| p[3] < 250) {
        return Ok(image.clone());
    }
    let rgb = |x: usize, y: usize| -> [u8; 3] {
        let i = (y * width + x) * 4;
        [rgba[i], rgba[i + 1], rgba[i + 2]]
    };
    let mut border = Vec::new();
    for y in [0, height - 1] {
        for x in 0..width {
            border.extend_from_slice(&rgb(x, y));
        }
    }
    if height > 2 {
        for x in [0, width - 1] {
            for y in 1..height - 1 {
                border.extend_from_slice(&rgb(x, y));
            }
        }
    }
    let Some(background) = border_background(&border)? else {
        return Ok(image.clone());
    };
    let limit = tolerance * tolerance * 3;
    let near: Vec<bool> = rgba
        .chunks_exact(4)
        .map(|p| {
            (0..3)
                .map(|k| {
                    let d = p[k] as i32 - background[k] as i32;
                    d * d
                })
                .sum::<i32>()
                <= limit
        })
        .collect();
    let mut starts: Vec<(usize, usize)> = (0..width).map(|x| (x, 0)).collect();
    if height > 1 {
        starts.extend((0..width).map(|x| (x, height - 1)));
    }
    if width > 1 {
        starts.extend((0..height).map(|y| (0, y)));
        starts.extend((0..height).map(|y| (width - 1, y)));
    }
    let mut outside = vec![false; width * height];
    let mut stack: Vec<usize> = starts
        .into_iter()
        .map(|(x, y)| y * width + x)
        .filter(|&i| near[i])
        .collect();
    if stack.is_empty() {
        return Ok(image.clone());
    }
    while let Some(i) = stack.pop() {
        if outside[i] || !near[i] {
            continue;
        }
        outside[i] = true;
        let (x, y) = (i % width, i / width);
        if x > 0 {
            stack.push(i - 1);
        }
        if x + 1 < width {
            stack.push(i + 1);
        }
        if y > 0 {
            stack.push(i - width);
        }
        if y + 1 < height {
            stack.push(i + width);
        }
    }
    let mut changed = rgba;
    for (pixel, &out) in changed.chunks_exact_mut(4).zip(&outside) {
        if out {
            pixel[3] = 0;
        }
    }
    to_premultiplied(width, height, &changed)
}

/// 置く前の変換（`convert_import_image`、2値化の設定なし）。
///
/// 下書き（変換なし）ならそのまま、JPEG は外周の背景を、それ以外は純白を透明にする。
/// 2値化の設定（color_reduction）を掛ける読み込みはここでは扱わない。
pub fn convert_import_image(
    image: &PremulImage,
    is_jpeg: bool,
    draft: bool,
) -> Result<PremulImage, Error> {
    if draft {
        Ok(image.clone())
    } else if is_jpeg {
        make_border_background_transparent(image, 24)
    } else {
        make_white_transparent(image)
    }
}

/// パスの拡張子が JPEG か（`.jpg`・`.jpeg`、大文字小文字を区別しない）。
pub fn is_jpeg_path(path: &str) -> bool {
    let name = path.rsplit(['/', '\\']).next().unwrap_or(path);
    let suffix = crate::export::cut_folder::path_suffix(name).to_lowercase();
    suffix == ".jpg" || suffix == ".jpeg"
}

/// 画像をキャンバスの中央に置いた透明な画像（`_placed_imported_image`）。
///
/// はみ出す分は切り捨てる（CompositionMode_Source の drawImage）。位置は Python の
/// 切り捨ての割り算 `(W - w) // 2`。
pub fn place_centered(
    canvas_width: usize,
    canvas_height: usize,
    image: &PremulImage,
) -> Result<PremulImage, Error> {
    let mut target = PremulImage::transparent(canvas_width, canvas_height)?;
    let x = (canvas_width as i64 - image.width() as i64).div_euclid(2);
    let y = (canvas_height as i64 - image.height() as i64).div_euclid(2);
    let (sw, cw) = (image.width(), canvas_width);
    let source = image.pixels();
    let pixels = target.pixels_mut();
    for sy in 0..image.height() {
        let ty = y + sy as i64;
        if ty < 0 || ty >= canvas_height as i64 {
            continue;
        }
        let left = (-x).max(0) as usize;
        let right = (cw as i64 - x).min(sw as i64);
        if right <= left as i64 {
            continue;
        }
        let right = right as usize;
        let tx = (x + left as i64) as usize;
        let ty = ty as usize;
        pixels[(ty * cw + tx) * 4..(ty * cw + tx + right - left) * 4]
            .copy_from_slice(&source[(sy * sw + left) * 4..(sy * sw + right) * 4]);
    }
    Ok(target)
}

/// 乗算済みのキャンバス画像を文書の絵（ストレート、Qt で正規化した値）にする（`to_pma_image`）。
pub fn to_document_image(image: &PremulImage) -> Result<RgbaImage, Error> {
    RgbaImage::from_rgba(image.width(), image.height(), &qt_unpremultiply(image))
}

/// 画像ファイル1枚を読み、変換してキャンバスの中央に置いた文書の絵にする
/// （`import_image_sequence` の1枚分、2値化の設定なし）。
pub fn import_image_file(
    data: &[u8],
    path: &str,
    canvas_width: usize,
    canvas_height: usize,
    draft: bool,
) -> Result<RgbaImage, ReadError> {
    let image = read_image(data)?;
    let converted =
        convert_import_image(&image, is_jpeg_path(path), draft).map_err(decode_error)?;
    let placed = place_centered(canvas_width, canvas_height, &converted).map_err(decode_error)?;
    to_document_image(&placed).map_err(decode_error)
}

/// 置く前の変換のやり方（`convert_import_image` の引数）。
#[derive(Clone, Debug)]
pub enum Conversion {
    /// 変換せず読み込む（下書きレイヤー）。
    Draft,
    /// 2値化の設定なし：JPEG は外周の背景、それ以外は純白を透明にする。
    Plain,
    /// 2値化の設定をかける（`reduce_image_with_settings`。デジタル TP のアンチエイリアス除去も）。
    Reduce(crate::image_ops::reduce::ReduceSettings),
}

/// 読み込んだ1枚に [`Conversion`] をかける（`convert_import_image`）。
pub fn convert_with(
    image: &PremulImage,
    is_jpeg: bool,
    conversion: &Conversion,
) -> Result<PremulImage, Error> {
    match conversion {
        Conversion::Draft => convert_import_image(image, is_jpeg, true),
        Conversion::Plain => convert_import_image(image, is_jpeg, false),
        Conversion::Reduce(settings) => {
            // Python は QImage（乗算済み）を qimage_rgba_array でストレートへ戻してから計算し、
            // 結果を乗算済みの QImage に戻す。
            let rgba = qt_unpremultiply(image);
            let out = crate::image_ops::reduce::reduce_rgba(
                &rgba,
                image.width(),
                image.height(),
                settings,
            )?;
            to_premultiplied(image.width(), image.height(), &out)
        }
    }
}

/// 画像ファイル1枚を読み、`conversion` をかけてキャンバスの中央に置いた文書の絵にする
/// （`import_image_sequence` の1枚分）。
pub fn import_image_file_with(
    data: &[u8],
    path: &str,
    canvas_width: usize,
    canvas_height: usize,
    conversion: &Conversion,
) -> Result<RgbaImage, ReadError> {
    let image = read_image(data)?;
    let converted = convert_with(&image, is_jpeg_path(path), conversion).map_err(decode_error)?;
    let placed = place_centered(canvas_width, canvas_height, &converted).map_err(decode_error)?;
    to_document_image(&placed).map_err(decode_error)
}

/// 画像を Qt が計算に使うストレート RGBA（`qimage_rgba_array`）で読む。
pub fn read_straight(data: &[u8]) -> Result<(usize, usize, Vec<u8>), ReadError> {
    let image = read_image(data)?;
    Ok((image.width(), image.height(), qt_unpremultiply(&image)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn premul(width: usize, height: usize, rgba: &[u8]) -> PremulImage {
        to_premultiplied(width, height, rgba).unwrap()
    }

    #[test]
    fn white_becomes_transparent() {
        let image = premul(2, 1, &[255, 255, 255, 255, 255, 255, 254, 255]);
        let out = make_white_transparent(&image).unwrap();
        assert_eq!(out.pixels(), &[0, 0, 0, 0, 255, 255, 254, 255]);
    }

    #[test]
    fn border_background_only_from_the_edge() {
        // 5×5 の白地の中央に黒の輪と白の穴。
        let mut rgba = vec![250u8; 5 * 5 * 4];
        for (i, pixel) in rgba.chunks_exact_mut(4).enumerate() {
            pixel[3] = 255;
            let (x, y) = (i % 5, i / 5);
            if (1..=3).contains(&x) && (1..=3).contains(&y) && (x, y) != (2, 2) {
                pixel[..3].copy_from_slice(&[0, 0, 0]);
            }
        }
        let out = make_border_background_transparent(&premul(5, 5, &rgba), 24).unwrap();
        let alpha: Vec<u8> = out.pixels().chunks_exact(4).map(|p| p[3]).collect();
        assert_eq!(alpha[0], 0);
        assert_eq!(alpha[2 * 5 + 2], 255);
        assert_eq!(alpha[5 + 1], 255);
    }

    #[test]
    fn placement_is_centered_and_clipped() {
        let image = premul(3, 1, &[1, 1, 1, 255, 2, 2, 2, 255, 3, 3, 3, 255]);
        let placed = place_centered(2, 3, &image).unwrap();
        // x = (2 - 3) // 2 = -1、y = 1。
        assert_eq!(&placed.pixels()[8..16], &[2, 2, 2, 255, 3, 3, 3, 255]);
        assert!(placed.pixels()[..8].iter().all(|&v| v == 0));
        let placed = place_centered(5, 1, &image).unwrap();
        assert_eq!(&placed.pixels()[4..8], &[1, 1, 1, 255]);
    }

    #[test]
    fn kinds_and_limits() {
        assert_eq!(image_kind(b"\x89PNG\r\n\x1a\nxx"), ImageKind::Png);
        assert_eq!(image_kind(&[0xff, 0xd8, 0xff, 0xe0]), ImageKind::Jpeg);
        assert!(matches!(
            check_size(16385, 1),
            Err(ReadError::TooLarge { width: 16385, .. })
        ));
        assert!(is_jpeg_path("a/b.JPEG"));
        assert!(!is_jpeg_path("a.png"));
    }
}
