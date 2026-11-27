//! 書き出し（`main_window_export.py`・`cut_folder.py`・`timesheet_file.py`・
//! `shadow_export.py`・`project_io._thumbnail_png`・タイムリマップのコピー）。
//!
//! ファイルには触れない。書くファイルの相対パスとバイト列をここで決め、フォルダーの
//! 作成と書き込みは呼び出し側（`pma-io`）が行う。
//!
//! 一致の程度：タイムシート（XDTS・TDTS）・TS.csv・メモ・TGA は Python とバイト単位で
//! 同じ。PNG は画素が同じで、圧縮（Qt は libpng/zlib）が違うのでバイト列は一致しない。
//! PSD は psd-tools 1.19.0 とバイト単位で同じ（MacRoman で書けるレイヤー名のとき）。

pub mod cut_folder;
pub mod movie;
pub mod psd;
pub mod py;
pub mod shadow;
pub mod sheet;
pub mod tga;
pub mod thumbnail;
pub mod time_remap;
pub mod timesheet;

use crate::compose::{self, Background, DisplayOptions};
use crate::document::model::Document;
use crate::image::RgbaImage;
use crate::project::archive::DEFAULT_DOTS_PER_METER;
use crate::project::png::encode_png;
use crate::project::qt_alpha::qt_premultiplied_roundtrip;
use crate::Error;

use cut_folder::{CelSource, ExportPlan, ImageFormat};

/// 書き出す画像の形式（連番・カットフォルダー）。
pub type CelFormat = ImageFormat;

/// 画像の画素を Qt の書き出しと同じ値にする（乗算済みの QImage を経由した丸め）。
fn qt_written_pixels(image: &RgbaImage) -> Vec<u8> {
    let mut pixels = image.rgba().to_vec();
    qt_premultiplied_roundtrip(&mut pixels, image.width());
    pixels
}

/// ストレート RGBA を PNG にする（Qt の `QImage.save(path, "PNG")` と同じ画素・pHYs）。
pub fn png_bytes(width: usize, height: usize, rgba: &[u8]) -> Result<Vec<u8>, Error> {
    encode_png(
        width,
        height,
        rgba,
        Some((DEFAULT_DOTS_PER_METER, DEFAULT_DOTS_PER_METER)),
    )
}

/// 文書の絵1枚を画像ファイルのバイト列にする（`read_qimage` → PNG・TGA）。
pub fn encode_drawing(image: &RgbaImage, format: ImageFormat) -> Result<Vec<u8>, Error> {
    let pixels = qt_written_pixels(image);
    match format {
        ImageFormat::Png => png_bytes(image.width(), image.height(), &pixels),
        ImageFormat::Tga => tga::encode_tga_rle(image.width(), image.height(), &pixels),
    }
}

/// 位置 (frame, layer) の絵（なければ文書の大きさの透明な画像）。
pub fn drawing_at(document: &Document, frame: usize, layer: usize) -> Result<RgbaImage, Error> {
    match sheet::slot_image(document, frame, layer) {
        Some(image) => Ok(image.clone()),
        None => {
            let (width, height) = document.size();
            RgbaImage::blank(width, height)
        }
    }
}

/// カットフォルダーのセル画像1枚のバイト列。
pub fn encode_cel(
    document: &Document,
    cel: &CelSource,
    format: ImageFormat,
) -> Result<Vec<u8>, Error> {
    encode_drawing(&drawing_at(document, cel.frame, cel.layer_index)?, format)
}

/// カットフォルダーのタイムシートの (相対パス, 文字列)。書かない構成なら None。
pub fn cut_folder_sheet(
    document: &Document,
    plan: &ExportPlan,
    playback_range_end: Option<i64>,
) -> Result<Option<(String, String)>, Error> {
    let (Some(path), Some(name)) = (plan.timesheet_path.clone(), plan.timesheet_name()) else {
        return Ok(None);
    };
    let text = sheet::sheet_text(
        document,
        plan.timesheet_format,
        &name,
        &plan.sheet_header,
        playback_range_end,
    )?;
    Ok(Some((path, text)))
}

/// メモ（`_memo.txt`）の (相対パス, 文字列)。前後の空白を落として空なら None（Issue #25）。
pub fn cut_folder_memo(plan: &ExportPlan, memo: &str) -> Option<(String, String)> {
    let memo = py::strip(memo);
    (!memo.is_empty()).then(|| (plan.memo_path(), format!("{memo}\n")))
}

/// 今のコマの白地の合成（`MainWindow.crop_image`）を PNG にする（カメラボタン・PNG 保存）。
pub fn frame_png(
    document: &Document,
    frame: usize,
    options: &DisplayOptions,
) -> Result<Vec<u8>, Error> {
    let image = compose::composite(document, frame, options, Background::White)?;
    let rgba = thumbnail::qt_unpremultiply(&image);
    png_bytes(image.width(), image.height(), &rgba)
}

/// 今のコマの白地の合成を TGA にする（`MainWindow.save_tga`）。
pub fn frame_tga(
    document: &Document,
    frame: usize,
    options: &DisplayOptions,
) -> Result<Vec<u8>, Error> {
    let image = compose::composite(document, frame, options, Background::White)?;
    let rgba = thumbnail::qt_unpremultiply(&image);
    tga::encode_tga_rle(image.width(), image.height(), &rgba)
}

/// 影素材1枚分の3種類の PNG（[`shadow::KINDS`] の順）。
pub fn shadow_pngs(image: &RgbaImage, shadow_rgbs: &[[u8; 3]]) -> Result<[Vec<u8>; 3], Error> {
    let [a, b, c] = shadow::materials(image.rgba(), shadow_rgbs);
    let (w, h) = (image.width(), image.height());
    Ok([
        png_bytes(w, h, &a)?,
        png_bytes(w, h, &b)?,
        png_bytes(w, h, &c)?,
    ])
}

/// 影素材の相対パス（`種類/レイヤー名/レイヤー名0001.png`）。
pub fn shadow_path(kind: &str, layer_name: &str, number: i64) -> String {
    format!("{kind}/{layer_name}/{layer_name}{number:04}.png")
}
