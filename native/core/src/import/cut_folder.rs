//! カットフォルダーを開く（Qt 版 `ImportController.read_cut_folder`）。
//!
//! カットフォルダー（列ごとのセル画像のフォルダーと XDTS／TDTS）を、CLIP の読み込みと
//! 同じ [`ClipAnimation`] にする。文書にするのは `Document::from_animation`。
//! ファイルには触れない：一覧は [`CutFolderContents`]、タイムシートの文字列とセル画像は
//! 呼び出し側が渡す関数で読む。言葉は Qt と同じ。

use crate::compose::PremulImage;
use crate::export::cut_folder::{exposure_keys, CutFolderContents, INVALID_NAME_CHARS};
use crate::export::py;
use crate::export::timesheet::{parse_timesheet, ColumnGroup};
use crate::import::clip::{check_frames, AnimationLayer, CelKey, ClipAnimation};
use crate::import::image::{place_centered, to_document_image, MAX_IMAGE_DIMENSION};

/// 読み込んだカットフォルダー。
#[derive(Clone, Debug)]
pub struct CutFolderRead {
    /// CLIP と同じ形のアニメーション（fps は渡した値、始まりは 0）。
    pub animation: ClipAnimation,
    /// 読み込めたが知らせたいこと（Qt の notes）。
    pub notes: Vec<String>,
}

/// 書き出しで使えない文字を `_` にした、列のフォルダー名の鍵。
fn replaced_key(name: &str) -> String {
    let replaced: String = name
        .chars()
        .map(|c| {
            if INVALID_NAME_CHARS.contains(c) || c == '/' {
                '_'
            } else {
                c
            }
        })
        .collect();
    py::casefold(py::strip_chars(&replaced, " ."))
}

/// カットフォルダーを読む（`read_cut_folder`）。
///
/// * `root_name`：フォルダーの名前（文書の出どころの名前）。
/// * `read_text(相対パス)`：タイムシートの文字列（UTF-8、BOM は付いていてよい）。
/// * `load(相対パス)`：セル画像（乗算済み）。失敗は理由の文字列。
/// * `fps`：今のタイムラインの fps（Qt は読み込んだ文書にそのまま使う）。
///
/// 失敗は Qt の文言（`OperationError` の文字列）。
pub fn read_cut_folder(
    root_name: &str,
    contents: &CutFolderContents,
    read_text: &mut dyn FnMut(&str) -> Result<String, String>,
    load: &mut dyn FnMut(&str) -> Result<PremulImage, String>,
    fps: f64,
) -> Result<CutFolderRead, String> {
    let Some(sheet_path) = contents.timesheets.first() else {
        return Err("タイムシート（.xdts／.tdts）が見つかりません。".into());
    };
    let mut notes = Vec::new();
    if contents.timesheets.len() > 1 {
        notes.push(format!(
            "タイムシートが複数あるため「{sheet_path}」を使いました。"
        ));
    }
    let text = read_text(sheet_path)?;
    let sheet = parse_timesheet(&text).map_err(|e| e.to_string())?;
    let duration = sheet.end_frame + 1;
    let columns: Vec<_> = sheet
        .tracks
        .iter()
        .filter(|track| track.group == ColumnGroup::Cell)
        .collect();
    if columns.is_empty() {
        return Err("タイムシートにセルの列がありません。".into());
    }

    let mut size: Option<(usize, usize)> = None;
    let mut resized = 0usize;
    let mut column_images =
        |drawings: Option<&std::collections::BTreeMap<i64, String>>| -> Result<Vec<(String, crate::image::RgbaImage)>, String> {
            let mut out = Vec::new();
            for (number, path) in drawings.into_iter().flatten() {
                let image = load(path)
                    .map_err(|error| format!("{path} を読み込めません。\n{error}"))?;
                let image = match size {
                    None => {
                        size = Some((image.width(), image.height()));
                        image
                    }
                    Some(s) if s != (image.width(), image.height()) => {
                        // セルの大きさが揃っていなければ、最初のセルの大きさの中央に置く。
                        resized += 1;
                        place_centered(s.0, s.1, &image).map_err(|e| e.to_string())?
                    }
                    Some(_) => image,
                };
                out.push((
                    number.to_string(),
                    to_document_image(&image).map_err(|e| e.to_string())?,
                ));
            }
            Ok(out)
        };

    let mut layers = Vec::new();
    let mut used: Vec<String> = Vec::new();
    let mut missing: Vec<String> = Vec::new();
    for column in &columns {
        let name = py::strip(&column.name).to_string();
        let mut key = py::casefold(&name);
        if contents.column(&key).is_none() {
            key = replaced_key(&name);
        }
        used.push(key.clone());
        let drawings = contents
            .cels
            .iter()
            .find(|(k, _)| *k == key)
            .map(|(_, d)| d);
        let cels = column_images(drawings)?;
        let mut keys = Vec::new();
        for (frame, number) in exposure_keys(&column.states) {
            let mut tag = number.map(|n| n.to_string()).unwrap_or_default();
            if !tag.is_empty() && !cels.iter().any(|(t, _)| *t == tag) {
                missing.push(format!("{name}{tag}"));
                tag.clear();
            }
            keys.push(CelKey {
                frame: frame as i64,
                tag,
            });
        }
        layers.push(AnimationLayer { name, keys, cels });
    }
    for (key, drawings) in &contents.cels {
        if used.contains(key) {
            continue;
        }
        let cels = column_images(Some(drawings))?;
        layers.push(AnimationLayer {
            name: contents.name_of(key).unwrap_or(key).to_string(),
            keys: Vec::new(),
            cels,
        });
    }
    let Some((width, height)) = size else {
        return Err("セルの画像（PNG／TGA）が見つかりません。".into());
    };
    if width > MAX_IMAGE_DIMENSION || height > MAX_IMAGE_DIMENSION {
        return Err(format!(
            "画像サイズが上限を超えています。\n{width} × {height}px"
        ));
    }
    let end_frame = duration as i64 - 1;
    check_frames(width, height, 0, end_frame, &layers).map_err(|e| e.0)?;
    if !missing.is_empty() {
        let mut shown = missing
            .iter()
            .take(10)
            .cloned()
            .collect::<Vec<_>>()
            .join("、");
        if missing.len() > 10 {
            shown.push('…');
        }
        notes.push(format!("画像が見つからないセルは空セルにしました：{shown}"));
    }
    if resized > 0 {
        notes.push(format!(
            "大きさの違うセル{resized}枚は、中央に置いて大きさを揃えました。"
        ));
    }
    let timeline_name =
        crate::export::cut_folder::path_stem(sheet_path.rsplit('/').next().unwrap_or(sheet_path))
            .to_string();
    Ok(CutFolderRead {
        animation: ClipAnimation {
            source_name: root_name.to_string(),
            timeline_name,
            width,
            height,
            fps,
            start_frame: 0,
            end_frame,
            layers,
        },
        notes,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::export::cut_folder::scan_cut_folder;
    use crate::export::cut_folder::ListedEntry;

    fn file(name: &str) -> ListedEntry {
        ListedEntry {
            name: name.into(),
            is_file: true,
            is_dir: false,
            children: Vec::new(),
        }
    }

    fn dir(name: &str, children: Vec<ListedEntry>) -> ListedEntry {
        ListedEntry {
            name: name.into(),
            is_file: false,
            is_dir: true,
            children,
        }
    }

    fn xdts(columns: &[(&str, &[Option<i64>])]) -> String {
        let tracks: Vec<String> = columns
            .iter()
            .enumerate()
            .map(|(no, (_, states))| {
                let frames: Vec<String> = states
                    .iter()
                    .enumerate()
                    .map(|(frame, state)| {
                        let value = match state {
                            Some(n) => n.to_string(),
                            None => "SYMBOL_NULL_CELL".into(),
                        };
                        format!(r#"{{"frame":{frame},"data":[{{"id":0,"values":["{value}"]}}]}}"#)
                    })
                    .collect();
                format!(r#"{{"trackNo":{no},"frames":[{}]}}"#, frames.join(","))
            })
            .collect();
        let names: Vec<String> = columns.iter().map(|(n, _)| format!("\"{n}\"")).collect();
        let duration = columns.iter().map(|(_, s)| s.len()).max().unwrap_or(1);
        format!(
            "exchangeDigitalTimeSheet Save Data\n{{\"version\":5,\"timeTables\":[{{\"duration\":{duration},\"timeTableHeaders\":[{{\"fieldId\":0,\"names\":[{}]}}],\"fields\":[{{\"fieldId\":0,\"tracks\":[{}]}}]}}]}}",
            names.join(","),
            tracks.join(",")
        )
    }

    fn image(width: usize, height: usize, value: u8) -> PremulImage {
        crate::import::image::to_premultiplied(
            width,
            height,
            &[value, 0, 0, 255].repeat(width * height),
        )
        .unwrap()
    }

    fn read(
        entries: Vec<ListedEntry>,
        sheet: String,
        sizes: &dyn Fn(&str) -> (usize, usize),
    ) -> Result<CutFolderRead, String> {
        let contents = scan_cut_folder(&entries);
        read_cut_folder(
            "cut",
            &contents,
            &mut |_| Ok(sheet.clone()),
            &mut |path| {
                let (w, h) = sizes(path);
                Ok(image(w, h, path.len() as u8))
            },
            24.0,
        )
    }

    #[test]
    fn columns_follow_the_sheet_and_extra_folders_go_last() {
        let entries = vec![
            file("cut.xdts"),
            dir("A", vec![file("A_0001.png"), file("A_0002.png")]),
            dir("B", vec![file("B_0001.png")]),
            dir("C", vec![file("C_0003.png")]),
        ];
        let sheet = xdts(&[
            ("B", &[Some(1), Some(1)]),
            ("A", &[Some(1), Some(2), Some(5)]),
        ]);
        let read = read(entries, sheet, &|_| (4, 3)).unwrap();
        let a = &read.animation;
        assert_eq!((a.width, a.height, a.end_frame), (4, 3, 2));
        let names: Vec<&str> = a.layers.iter().map(|l| l.name.as_str()).collect();
        assert_eq!(names, ["B", "A", "C"]);
        assert_eq!(a.layers[1].keys.len(), 3);
        // A5 は画像が無いので空セル。
        assert_eq!(a.layers[1].keys[2].tag, "");
        assert_eq!(a.timeline_name, "cut");
        assert!(read.notes[0].contains("A5"));
        assert!(a.layers[2].keys.is_empty());
    }

    #[test]
    fn other_sizes_are_centred_and_missing_sheets_fail() {
        let entries = vec![
            file("cut.xdts"),
            dir("A", vec![file("A_0001.png"), file("A_0002.png")]),
        ];
        let sheet = xdts(&[("A", &[Some(1), Some(2)])]);
        let read = read(entries, sheet.clone(), &|p| {
            if p.ends_with("2.png") {
                (2, 2)
            } else {
                (4, 4)
            }
        })
        .unwrap();
        assert!(read.notes.iter().any(|n| n.contains("1枚")));
        let error = super::read_cut_folder(
            "cut",
            &scan_cut_folder(&[dir("A", vec![file("A_0001.png")])]),
            &mut |_| Ok(sheet.clone()),
            &mut |_| Ok(image(1, 1, 0)),
            24.0,
        )
        .unwrap_err();
        assert_eq!(error, "タイムシート（.xdts／.tdts）が見つかりません。");
    }
}
