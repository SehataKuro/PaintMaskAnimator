//! 使用色の右クリックの「線の太さを調整」（Qt 版の
//! `main_window_color_interaction.adjust_parent_line_thickness` と
//! `widgets.TransformLineThicknessDialog`）。
//!
//! 今の位置の露出ブロック（●ーーー｜）のキー画像全体を、選んだ親色・子色を実線とする
//! クオリティ変形で開き、実線の太さのしきい値だけを動かして今のコマへ確定する。計算は
//! 変形（transform.rs、pma-core の tools::transform）と同じ。画面（used_colors.js）は窓を
//! 出し、始める・太さを変える（tools_set の transform_line_width）・終えるを送るだけ。

use std::sync::Mutex;

use crate::session::{current_index, Session};
use crate::tools::with_tools;
use serde::Serialize;

/// 始めたときの状態（終えたら戻す）。
struct Saved {
    original_frame: usize,
    layer: usize,
    key_frame: usize,
    block_end: usize,
    exposure: usize,
    colors: usize,
    quality: bool,
    threshold: i64,
}

static SAVED: Mutex<Option<Saved>> = Mutex::new(None);

/// 始めたときの答え（窓の初めの値）。
#[derive(Serialize)]
pub struct Begun {
    pub threshold: i64,
}

fn back_to(s: &mut Session, frame: usize, layer: usize) {
    if let Some(p) = s.project.as_mut() {
        let _ = p.document.set_selection_index(frame, layer);
    }
    if let Some(p) = s.project.as_ref() {
        s.timeline.select_current(&p.document);
    }
}

/// 始める。`colors` は選んだ線の色（空なら使用色の選択）。断る理由は文で返す。先頭の
/// `status:` は状態の帯、`info:` は知らせの窓、`warn:` は警告の窓（Qt の
/// statusBar・information・warning）。
pub fn begin(s: &mut Session, colors: &[[u8; 3]]) -> Result<Begun, String> {
    if crate::transform::tween_pending(s) {
        return Err("status:トゥイーン中は線の太さを変更できません。".into());
    }
    let mut line_colors: Vec<[u8; 3]> = colors.to_vec();
    if line_colors.is_empty() {
        line_colors = s
            .colors
            .palette
            .selected()
            .iter()
            .map(|c| [c.0, c.1, c.2])
            .collect();
    }
    let parent = s.colors.palette.focus().map(|c| [c.0, c.1, c.2]);
    if !parent.is_some_and(|p| line_colors.contains(&p)) {
        return Err("info:親として選択している色で右クリックしてください。".into());
    }
    if s.tools.transform.is_some() {
        return Err("warn:別の変形処理を確定またはキャンセルしてから実行してください。".into());
    }
    let p = s.project.as_mut().ok_or("作品を開いていません")?;
    let (original_frame, layer) = current_index(&p.document);
    let Some((false, key_frame, exposure)) = p.document.timeline_block_at(original_frame, layer)
    else {
        return Err("info:現在位置には調整できるキーフレームがありません。".into());
    };
    let has_content = pma_core::compose::frame_layers(&p.document, key_frame)
        .map_err(|e| e.to_string())?
        .get(layer)
        .and_then(|l| l.as_ref())
        .is_some_and(|l| l.image.is_some());
    if !has_content {
        return Err("info:現在の露出ブロックに画像がありません。".into());
    }
    // 保持セルから始めても、必ず●の画像本体へ移って全体を対象にする。
    back_to(s, key_frame, layer);
    s.tools.selection.clear();
    if !crate::tools::select_used_area(s)? {
        back_to(s, original_frame, layer);
        return Err("warn:キーフレーム全体から描画領域を検出できません。".into());
    }
    let quality = s.tools.settings.transform_quality;
    let threshold = s.tools.settings.transform_line_width;
    s.tools.settings.transform_quality = true;
    // 線の色は選んだ色（Qt の line_colors_override）。始めたら元の選択に戻す。
    let selection = std::mem::replace(&mut s.tools.paint.used_selection, line_colors.clone());
    let result = crate::transform::begin_plain(s, "free");
    s.tools.paint.used_selection = selection;
    if let Err(e) = result {
        s.tools.settings.transform_quality = quality;
        s.tools.selection.clear();
        back_to(s, original_frame, layer);
        return Err(format!("warn:{e}"));
    }
    *SAVED.lock().unwrap() = Some(Saved {
        original_frame,
        layer,
        key_frame,
        block_end: key_frame + exposure - 1,
        exposure,
        colors: line_colors.len(),
        quality,
        threshold,
    });
    Ok(Begun { threshold })
}

/// 終える。`accept` なら `value` の太さで今のコマだけに確定する。知らせを返す。
pub fn finish(s: &mut Session, accept: bool, value: i64) -> Result<Option<String>, String> {
    let Some(saved) = SAVED.lock().unwrap().take() else {
        return Ok(None);
    };
    let value = value.clamp(1, 254);
    let message = if accept && s.tools.transform.is_some() {
        s.tools.settings.transform_line_width = value;
        crate::transform::set_line_threshold(s, value);
        crate::transform::set_current_frame_only(s);
        crate::transform::commit(s)?;
        // 画像は●にだけ保存し、ーーーー｜は同じ露出を参照する。
        back_to(s, saved.original_frame.min(saved.block_end), saved.layer);
        Some(format!(
            "キーフレーム {}～{}（{}コマ）全体へ、選択中の{}色の太さ {} を適用しました。",
            saved.key_frame + 1,
            saved.block_end + 1,
            saved.exposure,
            saved.colors,
            255 - value
        ))
    } else {
        crate::transform::cancel(s)?;
        s.tools.settings.transform_line_width = saved.threshold;
        back_to(s, saved.original_frame, saved.layer);
        None
    };
    s.tools.selection.clear();
    s.tools.settings.transform_quality = saved.quality;
    s.touch_pixels();
    s.recompose()?;
    Ok(message)
}

#[tauri::command]
pub fn line_thickness_begin(app: tauri::AppHandle, colors: Vec<[u8; 3]>) -> Result<Begun, String> {
    with_tools(&app, false, |s| begin(s, &colors))
}

#[tauri::command]
pub fn line_thickness_finish(
    app: tauri::AppHandle,
    accept: bool,
    value: i64,
) -> Result<Option<String>, String> {
    with_tools(&app, true, |s| finish(s, accept, value))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refuses_without_a_parent_colour() {
        let mut s = Session::empty(8, 8);
        s.open_document(crate::timeline::sample_document(6, (32, 24)).unwrap())
            .unwrap();
        let err = begin(&mut s, &[]).err().unwrap();
        assert!(err.starts_with("info:親として"), "{err}");
        assert!(finish(&mut s, true, 100).unwrap().is_none());
    }
}
