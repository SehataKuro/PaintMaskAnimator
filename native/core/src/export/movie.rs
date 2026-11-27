//! MP4 書き出しの絵の列（`ExportController.mp4`・`movie_slate.py`）。
//!
//! 動画に入れる絵は、コマごとの白地の合成（`crop_image`）をコマの尺の数だけ並べたもの。
//! 頭の [`LEADING_FRAMES`] 枚だけ、カット名を重ねる（Issue #26。コマは足さないので
//! タイミングは変わらない）。カット名の文字は画面（Qt は QPainter、Tauri はブラウザの
//! canvas）が透明な画像に描き、ここではそれを重ねるだけ。符号化は呼び出し側（ffmpeg）。

use crate::compose::{self, Background, DisplayOptions};
use crate::document::model::Document;
use crate::Error;

use super::thumbnail::qt_unpremultiply;

/// カット名を重ねるコマ数。
pub const LEADING_FRAMES: usize = 8;
/// 画像の高さに対する文字の大きさ。
pub const FONT_SCALE: f64 = 0.07;
/// 画面端からの余白（画像の高さに対する割合）。
pub const MARGIN_SCALE: f64 = 0.04;

/// カット名の文字の大きさ（px）と余白（`draw_cut_name`）。
pub fn slate_metrics(height: usize) -> (usize, usize) {
    let font = ((height as f64 * FONT_SCALE) as usize).max(12);
    let margin = ((height as f64 * MARGIN_SCALE) as usize).max(6);
    (font, margin)
}

/// 書き出すカット名（`cut_name_for`）：プロジェクトのファイル名の拡張子を除いた部分。
pub fn cut_name_for(project_path: Option<&str>, fallback: &str) -> String {
    if let Some(path) = project_path.filter(|p| !p.is_empty()) {
        let name = path.rsplit(['/', '\\']).next().unwrap_or(path);
        let stem = super::cut_folder::path_stem(name);
        let stem = super::py::strip(stem);
        if !stem.is_empty() {
            return stem.to_string();
        }
    }
    super::py::strip(fallback).to_string()
}

/// 動画のコマごとに、どのコマの絵を使うか（尺の数だけ繰り返す。尺は 1 以上）。
pub fn output_frames(document: &Document) -> Vec<usize> {
    let mut out = Vec::new();
    for (index, id) in document.frame_order().iter().enumerate() {
        let duration = document.frame(*id).map_or(1, |f| f.duration.max(1)) as usize;
        out.extend(std::iter::repeat_n(index, duration));
    }
    out
}

/// コマの白地の合成（ストレート RGBA、不透明）。
pub fn movie_frame(
    document: &Document,
    frame: usize,
    options: &DisplayOptions,
) -> Result<Vec<u8>, Error> {
    let image = compose::composite(document, frame, options, Background::White)?;
    Ok(qt_unpremultiply(&image))
}

fn byte_mul(x: u32, a: u32) -> u32 {
    let t = x * a;
    (t + (t >> 8) + 0x80) >> 8
}

/// 不透明な絵に、乗算済み RGBA のカット名を重ねる（Qt の SourceOver と同じ丸め）。
pub fn overlay_slate(rgba: &mut [u8], overlay: &[u8]) -> Result<(), Error> {
    if rgba.len() != overlay.len() {
        return Err(Error::Invalid("カット名の画像の大きさが違います".into()));
    }
    for (dst, src) in rgba.chunks_exact_mut(4).zip(overlay.chunks_exact(4)) {
        let inverse = 255 - src[3] as u32;
        if inverse == 255 {
            continue;
        }
        for channel in 0..4 {
            dst[channel] =
                (src[channel] as u32 + byte_mul(dst[channel] as u32, inverse)).min(255) as u8;
        }
    }
    Ok(())
}

/// 動画の index 枚目（0 始まり）の絵。overlay はカット名（なければ重ねない）。
pub fn output_frame(
    document: &Document,
    frame: usize,
    index: usize,
    options: &DisplayOptions,
    overlay: Option<&[u8]>,
) -> Result<Vec<u8>, Error> {
    let mut rgba = movie_frame(document, frame, options)?;
    if let Some(overlay) = overlay.filter(|_| index < LEADING_FRAMES) {
        overlay_slate(&mut rgba, overlay)?;
    }
    Ok(rgba)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn metrics_and_names() {
        assert_eq!(slate_metrics(720), (50, 28));
        assert_eq!(slate_metrics(100), (12, 6));
        assert_eq!(cut_name_for(Some("/a/b/c012 .pman"), "x"), "c012");
        assert_eq!(cut_name_for(Some("C:\\a\\cut.v2.pman"), ""), "cut.v2");
        assert_eq!(cut_name_for(None, "  fb "), "fb");
    }

    #[test]
    fn overlay_source_over() {
        let mut rgba = vec![100, 100, 100, 255, 10, 20, 30, 255];
        overlay_slate(&mut rgba, &[0, 0, 0, 0, 128, 128, 128, 128]).unwrap();
        assert_eq!(rgba, vec![100, 100, 100, 255, 133, 138, 143, 255]);
    }
}
