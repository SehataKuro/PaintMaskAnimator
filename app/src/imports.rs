//! 読み込み（Qt 版 main_window.py の import_* ・prepare_color_reduction・
//! inspect_digital_import、main_window_import.py の PSD・CLIP、main_window_initial_state.py の
//! 線画の初期状態、main_window.py の insert_layout_paper の画面の側）。
//!
//! 計算は pma-core（`import::image`・`import::sequence`・`image_ops`・
//! `document::import_commands`）にあり、ここはファイルを読み、別のスレッドで変換し、
//! 進み具合を `import-job` の事件で送り（中止できる）、文書へは `Session::edit_with` で
//! Undo 1回（Qt と同じ「全体編集」）として書く。言葉は Qt と同じ。
//!
//! Qt との違い：Qt は画像がキャンバスより大きいとき、キャンバスの拡張と読み込みを別々に
//! Undo へ積む（2回）。ここは1回にまとめる。

use crate::session::{current_index, Session, Shared};
use crate::shell::ShellShared;
use crate::timeline::Mode;
use pma_core::color_stats;
use pma_core::compose::PremulImage;
use pma_core::document::import_commands::ImportedTrack;
use pma_core::document::model::Document;
use pma_core::export::thumbnail::qt_unpremultiply;
use pma_core::image::RgbaImage;
use pma_core::image_ops::palette_build::{build_color_reduction_palette, PaletteOptions};
use pma_core::image_ops::reduce::{self, ExtractionMode, ReduceSettings};
use pma_core::image_ops::{antialias, despeckle, scan_paper, soft_blur};
use pma_core::import::image::{
    self as import_image, convert_with, detect_opaque_border_background, Conversion, ReadError,
};
use pma_core::import::psd as psd_import;
use pma_core::import::sequence::{
    is_image_path, reference_frame_indices, sort_natural, REFERENCE_FRAME_COUNT,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use tauri::ipc::Response;
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_dialog::DialogExt;

/// Qt の「全体編集」（DocUndo の HISTORY_LABEL）。
pub const LABEL: &str = "全体編集";
/// 前回選んだ素材の種類（preferences.IMPORT_MATERIAL_KEY）。
const MATERIAL_KEY: &str = "import_material_kind";

// ---------------------------------------------------------------------------
// 状態
// ---------------------------------------------------------------------------

/// 読み込みの途中の状態（アプリに1つ）。
#[derive(Default)]
pub struct Imports {
    /// 動いている読み込みの中止の印。
    job: Option<Arc<AtomicBool>>,
    /// 2値化の窓で使う、1枚目と代表フレーム（ストレート RGBA）。
    prepared: Option<Prepared>,
}

pub type ImportsShared = Mutex<Imports>;

struct Prepared {
    paths: Vec<String>,
    first: Straight,
    references: Vec<Straight>,
    /// 1枚目の外周の背景（`detect_opaque_border_background`）。
    background: Option<[u8; 3]>,
}

#[derive(Clone)]
struct Straight {
    width: usize,
    height: usize,
    rgba: Vec<u8>,
}

fn file_name(path: &str) -> String {
    Path::new(path)
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| path.to_string())
}

/// `_read_image_file` の失敗の文言（形式と理由）。
fn read_error(path: &str, error: ReadError) -> String {
    match error {
        ReadError::TooLarge { .. } => error.to_string(),
        ReadError::Decode(reason) => {
            let suffix = Path::new(path)
                .extension()
                .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
                .unwrap_or_else(|| "拡張子なし".into());
            format!("形式: {suffix}\n{reason}")
        }
    }
}

fn read_file(path: &str) -> Result<Vec<u8>, String> {
    std::fs::read(path).map_err(|e| format!("形式: \n{e}"))
}

fn read_premul(path: &str) -> Result<PremulImage, String> {
    let data = read_file(path)?;
    import_image::read_image(&data).map_err(|e| read_error(path, e))
}

fn read_straight(path: &str) -> Result<Straight, String> {
    let image = read_premul(path)?;
    Ok(Straight {
        width: image.width(),
        height: image.height(),
        rgba: qt_unpremultiply(&image),
    })
}

// ---------------------------------------------------------------------------
// ファイルを選ぶ
// ---------------------------------------------------------------------------

/// 選んだファイル（フォルダーは中の画像を自然順に並べたもの）。
#[derive(Serialize)]
pub struct Picked {
    pub paths: Vec<String>,
    /// フォルダーの名前（「画像フォルダーを読み込む」のレイヤー名）。
    pub name: Option<String>,
}

fn image_dialog(
    app: &AppHandle,
    title: &str,
) -> tauri_plugin_dialog::FileDialogBuilder<tauri::Wry> {
    app.dialog()
        .file()
        .set_title(title)
        .add_filter("画像", &["png", "jpg", "jpeg", "tga"])
        .add_filter("PNG", &["png"])
        .add_filter("JPEG", &["jpg", "jpeg"])
        .add_filter("TGA", &["tga"])
}

fn path_string(p: tauri_plugin_dialog::FilePath) -> Option<String> {
    p.into_path().ok().map(|p| p.display().to_string())
}

/// ファイルを選ぶ窓。`kind`：images（画像・複数）・folder（画像フォルダー）・psd・clip・
/// line_art_folder（線画カットフォルダー）。選ばなければ None。
#[tauri::command]
pub async fn import_pick(
    app: AppHandle,
    kind: String,
    title: String,
) -> Result<Option<Picked>, String> {
    let picked = match kind.as_str() {
        "images" => image_dialog(&app, &title)
            .blocking_pick_files()
            .map(|files| Picked {
                paths: files.into_iter().filter_map(path_string).collect(),
                name: None,
            }),
        "folder" => {
            let Some(folder) = app
                .dialog()
                .file()
                .set_title(&title)
                .blocking_pick_folder()
                .and_then(|p| p.into_path().ok())
            else {
                return Ok(None);
            };
            let mut paths: Vec<String> = std::fs::read_dir(&folder)
                .map_err(|e| e.to_string())?
                .filter_map(|e| e.ok())
                .map(|e| e.path())
                .filter(|p| p.is_file())
                .map(|p| p.display().to_string())
                .filter(|p| is_image_path(p))
                .collect();
            sort_natural(&mut paths);
            Some(Picked {
                paths,
                name: folder.file_name().map(|n| n.to_string_lossy().into_owned()),
            })
        }
        "line_art_folder" => app
            .dialog()
            .file()
            .set_title(&title)
            .blocking_pick_folder()
            .and_then(path_string)
            .map(|p| Picked {
                paths: vec![p],
                name: None,
            }),
        "psd" => app
            .dialog()
            .file()
            .set_title(&title)
            .add_filter("Photoshop Document", &["psd"])
            .blocking_pick_file()
            .and_then(path_string)
            .map(|p| Picked {
                paths: vec![p],
                name: None,
            }),
        "clip" => app
            .dialog()
            .file()
            .set_title(&title)
            .add_filter("CLIP STUDIO PAINT", &["clip"])
            .add_filter("すべてのファイル", &["*"])
            .blocking_pick_file()
            .and_then(path_string)
            .map(|p| Picked {
                paths: vec![p],
                name: None,
            }),
        other => return Err(format!("読み込みの種類が不明です：{other}")),
    };
    Ok(picked)
}

// ---------------------------------------------------------------------------
// 読み込む前の確かめ（素材の種類・2値化）
// ---------------------------------------------------------------------------

/// 1枚目を調べた結果（`prepare_color_reduction`）。
#[derive(Serialize)]
pub struct Inspection {
    /// 自然順に並べたパス。
    pub paths: Vec<String>,
    /// 前回選んだ素材の種類（digital / analog）。
    pub material: String,
    /// 1枚目の不透明な色の数。
    pub color_count: usize,
    /// 1枚目の半透明の画素数。
    pub semi_transparent: usize,
    /// 1枚目の外周の背景色。
    pub background: Option<[u8; 3]>,
    /// 2値化の窓を出すか（多色・半透明・白背景）。
    pub needs_reduction: bool,
    /// 1枚目の大きさ。
    pub size: (usize, usize),
    /// 手本にする枚数（1枚目と読めた代表フレーム）。
    pub frames: usize,
}

fn material(app: &AppHandle) -> String {
    app.try_state::<ShellShared>()
        .and_then(|shell| {
            shell
                .lock()
                .ok()?
                .prefs
                .get(MATERIAL_KEY)
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .filter(|v| v == "digital" || v == "analog")
        .unwrap_or_else(|| "digital".into())
}

/// 素材の種類を覚える（`set_import_material_kind`）。
#[tauri::command]
pub fn import_set_material(app: AppHandle, kind: String) -> Result<(), String> {
    if kind != "digital" && kind != "analog" {
        return Err("素材の種類が不正です".into());
    }
    let shell = app.state::<ShellShared>();
    let mut sh = shell.lock().unwrap();
    sh.prefs.set(MATERIAL_KEY, Value::String(kind))
}

/// 1枚目を読み、色と透明度を調べる。2値化の窓のために代表フレームも読んでおく。
#[tauri::command]
pub async fn import_inspect(app: AppHandle, paths: Vec<String>) -> Result<Inspection, String> {
    let mut paths = paths;
    sort_natural(&mut paths);
    let Some(first_path) = paths.first().cloned() else {
        return Err("画像ファイルがありません。".into());
    };
    let material = material(&app);
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let first =
            read_straight(&first_path).map_err(|e| format!("{}\n{e}", file_name(&first_path)))?;
        let (w, h) = (first.width, first.height);
        let check = |e: pma_core::Error| format!("1枚目の色と透明度を確認できませんでした。\n{e}");
        let color_count = color_stats::opaque_rgb_color_count(&first.rgba, w, h).map_err(check)?;
        let semi_transparent = first
            .rgba
            .chunks_exact(4)
            .filter(|p| p[3] > 0 && p[3] < 255)
            .count();
        let background = detect_opaque_border_background(&first.rgba, w, h).map_err(check)?;
        let references: Vec<Straight> = reference_frame_indices(paths.len(), REFERENCE_FRAME_COUNT)
            .into_iter()
            .filter_map(|i| read_straight(&paths[i]).ok())
            .collect();
        let frames = references.len() + 1;
        let needs_reduction = color_count >= 100 || semi_transparent > 0 || background.is_some();
        handle.state::<ImportsShared>().lock().unwrap().prepared = Some(Prepared {
            paths: paths.clone(),
            first,
            references,
            background,
        });
        Ok(Inspection {
            paths,
            material,
            color_count,
            semi_transparent,
            background,
            needs_reduction,
            size: (w, h),
            frames,
        })
    })
    .await
    .map_err(|e| e.to_string())?
}

/// 2値化の窓で決めた設定（`_color_reduction_settings` の dict の窓の側）。
#[derive(Clone, Debug, Deserialize)]
pub struct ReduceForm {
    /// surface / line
    pub mode: String,
    pub color_count: usize,
    pub flatten_paper: bool,
    pub blur: f64,
    pub despeckle: usize,
    pub tone_curve_points: Vec<(f64, f64)>,
    /// プレビューで決めた共通パレット（読み込むとき）。
    #[serde(default)]
    pub palette: Option<Vec<[u8; 3]>>,
}

/// 透明背景のアルファ二値化は中央 50% に固定（`alpha_threshold_255`）。
const ALPHA_THRESHOLD: i64 = 128;

impl ReduceForm {
    fn line(&self) -> bool {
        self.mode == "line"
    }

    /// 背景の扱い（`_apply_paper_setting`・`resolve_color_preview_settings`）。
    fn background(&self, detected: Option<[u8; 3]>) -> (bool, Option<[u8; 3]>) {
        if self.line() || self.flatten_paper {
            (true, Some([255, 255, 255]))
        } else {
            (detected.is_some(), detected)
        }
    }

    fn settings(&self, detected: Option<[u8; 3]>, palette: Vec<[u8; 3]>) -> ReduceSettings {
        let (opaque_background, background) = if self.flatten_paper {
            (true, Some([255, 255, 255]))
        } else {
            (detected.is_some(), detected)
        };
        ReduceSettings {
            mode: if self.line() {
                ExtractionMode::Line
            } else {
                ExtractionMode::Surface
            },
            palette: Some(palette),
            flatten_paper: self.flatten_paper,
            blur: self.blur,
            despeckle: self.despeckle,
            alpha_threshold: ALPHA_THRESHOLD,
            opaque_background,
            background,
            tone_curve_points: self.tone_curve_points.clone(),
        }
    }

    /// 手本にする前処理（紙の地色→ぼかし、`_prepare_source`）。
    fn prepare(&self, image: &Straight) -> Straight {
        let (w, h) = (image.width, image.height);
        let mut rgba = image.rgba.clone();
        if self.flatten_paper {
            rgba = roundtrip(scan_paper::flatten_paper(&rgba, w, h), w);
        }
        if self.blur > 0.0 {
            rgba = roundtrip(soft_blur::soft_blur(&rgba, w, h, self.blur), w);
        }
        Straight {
            width: w,
            height: h,
            rgba,
        }
    }
}

fn roundtrip(mut pixels: Vec<u8>, width: usize) -> Vec<u8> {
    pma_core::project::qt_alpha::qt_premultiplied_roundtrip(&mut pixels, width);
    pixels
}

/// プレビューの結果：パレットと、元画像（トーンカーブ後）と 2値化の画像。
fn pack_preview(palette: &[[u8; 3]], semi: usize, images: [&Straight; 2]) -> Vec<u8> {
    let mut out = Vec::new();
    out.extend_from_slice(&(palette.len() as u32).to_le_bytes());
    out.extend_from_slice(&(semi as u32).to_le_bytes());
    for c in palette {
        out.extend_from_slice(&[c[0], c[1], c[2], 255]);
    }
    for image in images {
        out.extend_from_slice(&(image.width as u32).to_le_bytes());
        out.extend_from_slice(&(image.height as u32).to_le_bytes());
        out.extend_from_slice(&image.rgba);
    }
    out
}

/// 2値化の窓の「プレビュー」（`_update_preview`）。
/// 返すのは [色数 u32][半透明 u32][パレット RGBA…][幅 高さ 画素]×2（元画像・2値化）。
#[tauri::command]
pub async fn import_reduce_preview(app: AppHandle, form: ReduceForm) -> Result<Response, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<ImportsShared>();
        let guard = state.lock().unwrap();
        let prepared = guard
            .prepared
            .as_ref()
            .ok_or("読み込む画像を調べていません")?;
        let source = form.prepare(&prepared.first);
        let references: Vec<Straight> = prepared
            .references
            .iter()
            .map(|r| form.prepare(r))
            .collect();
        let detected = prepared.background;
        drop(guard);
        let (opaque_background, background) = form.background(detected);
        let error =
            |e: pma_core::Error| format!("2値化プレビューの生成中にエラーが発生しました。\n\n{e}");
        let tone = |image: &Straight| -> Result<Straight, String> {
            let rgba = reduce::apply_tone_curve(
                &image.rgba,
                image.width,
                image.height,
                &form.tone_curve_points,
                background,
            )
            .map_err(error)?;
            Ok(Straight { rgba, ..*image })
        };
        let toned = tone(&source)?;
        let toned_refs = references.iter().map(tone).collect::<Result<Vec<_>, _>>()?;
        let mut images =
            vec![RgbaImage::from_rgba(toned.width, toned.height, &toned.rgba).map_err(error)?];
        for r in &toned_refs {
            images.push(RgbaImage::from_rgba(r.width, r.height, &r.rgba).map_err(error)?);
        }
        let palette = build_color_reduction_palette(
            &images.iter().collect::<Vec<_>>(),
            &PaletteOptions {
                color_count: form.color_count,
                alpha_threshold: ALPHA_THRESHOLD,
                max_samples: 131072,
                opaque_background,
                background,
                line: form.line(),
            },
        )
        .map_err(error)?;
        let mut reduced = reduce::apply_palette(
            &toned.rgba,
            toned.width,
            toned.height,
            &palette,
            ALPHA_THRESHOLD,
            opaque_background,
            background,
            form.line(),
        )
        .map_err(error)?;
        if form.despeckle > 0 {
            let (cleaned, removed) = despeckle::remove_isolated_specks(
                &reduced,
                toned.width,
                toned.height,
                form.despeckle,
            );
            if removed > 0 {
                reduced = roundtrip(cleaned, toned.width);
            }
        }
        let semi = reduced
            .chunks_exact(4)
            .filter(|p| p[3] > 0 && p[3] < 255)
            .count();
        let reduced = Straight {
            rgba: reduced,
            ..toned
        };
        Ok(Response::new(pack_preview(
            &palette,
            semi,
            [&toned, &reduced],
        )))
    })
    .await
    .map_err(|e| e.to_string())?
}

// ---------------------------------------------------------------------------
// 進み具合（import-job の事件）
// ---------------------------------------------------------------------------

/// 読み込みの進み具合と結果。
#[derive(Clone, Debug, Serialize, Default)]
pub struct ImportEvent {
    /// progress / done
    pub phase: &'static str,
    pub value: usize,
    pub total: usize,
    pub label: String,
    /// 終わったときの知らせ（下の帯）。
    pub message: Option<String>,
    /// 失敗したときの窓の文言（題名, 本文）。
    pub error: Option<(String, String)>,
    /// 終わったときに見せる窓（題名, 本文）。PSD の「{n}レイヤーを読み込みました」など。
    pub info: Option<(String, String)>,
    /// 読み込んだあと「色塗り前の初期状態」を尋ねるレイヤー（None なら尋ねない）。
    pub offer_initial: Option<Vec<usize>>,
    /// 中止した。
    pub cancelled: bool,
}

fn progress(app: &AppHandle, value: usize, total: usize, label: String) {
    let _ = app.emit(
        "import-job",
        ImportEvent {
            phase: "progress",
            value,
            total: total.max(1),
            label,
            ..ImportEvent::default()
        },
    );
}

fn finish(app: &AppHandle, event: ImportEvent) {
    app.state::<ImportsShared>().lock().unwrap().job = None;
    let _ = app.emit(
        "import-job",
        ImportEvent {
            phase: "done",
            ..event
        },
    );
}

/// 動いている読み込みを止める。
#[tauri::command]
pub fn import_cancel(state: State<ImportsShared>) {
    if let Some(flag) = &state.lock().unwrap().job {
        flag.store(true, Ordering::Relaxed);
    }
}

/// 読み込みを始める印を立てる（ほかの読み込みが動いていれば Err）。
fn begin_job(app: &AppHandle) -> Result<Arc<AtomicBool>, String> {
    let state = app.state::<ImportsShared>();
    let mut guard = state.lock().unwrap();
    if guard.job.is_some() {
        return Err("ほかの読み込みが動いています".into());
    }
    let flag = Arc::new(AtomicBool::new(false));
    guard.job = Some(flag.clone());
    Ok(flag)
}

// ---------------------------------------------------------------------------
// デジタル TP のアンチエイリアスの検査（inspect_digital_import）
// ---------------------------------------------------------------------------

/// アンチエイリアスのあったコマ（何枚目, 名前, 画素数）。
#[derive(Clone, Debug, Serialize)]
pub struct Antialias {
    pub index: usize,
    pub name: String,
    pub pixels: usize,
}

/// 全コマのアンチエイリアスを数える。終わると `import-antialias` の事件で
/// {found, error, cancelled} を送る。
#[tauri::command]
pub fn import_scan_antialias(app: AppHandle, paths: Vec<String>) -> Result<(), String> {
    let cancel = begin_job(&app)?;
    std::thread::spawn(move || {
        let mut found = Vec::new();
        let mut error = None;
        let total = paths.len();
        for (index, path) in paths.iter().enumerate() {
            if cancel.load(Ordering::Relaxed) {
                break;
            }
            progress(&app, index, total, file_name(path));
            match read_straight(path) {
                Ok(image) => {
                    let count = antialias::count_antialias(&image.rgba, image.width, image.height);
                    if count > 0 {
                        found.push(Antialias {
                            index: index + 1,
                            name: file_name(path),
                            pixels: count,
                        });
                    }
                }
                Err(e) => {
                    error = Some(format!("{}\n{e}", file_name(path)));
                    break;
                }
            }
        }
        let cancelled = cancel.load(Ordering::Relaxed);
        app.state::<ImportsShared>().lock().unwrap().job = None;
        let _ = app.emit(
            "import-antialias",
            serde_json::json!({"found": found, "error": error, "cancelled": cancelled}),
        );
    });
    Ok(())
}

// ---------------------------------------------------------------------------
// 画像の読み込み（import_image_sequence）
// ---------------------------------------------------------------------------

/// 画像の読み込みの頼み。
#[derive(Clone, Debug, Deserialize)]
pub struct ImageRequest {
    pub paths: Vec<String>,
    /// draft（変換せず読み込む）・plain（2値化なし）・digital（アンチエイリアスを取り除く）・
    /// reduce（2値化、`form` を使う）。
    pub conversion: String,
    #[serde(default)]
    pub form: Option<ReduceForm>,
    /// レイヤー名（None なら1枚目のファイル名）。
    #[serde(default)]
    pub layer_name: Option<String>,
    /// 原画の印を付ける（「原画を読み込む」）。
    #[serde(default)]
    pub genga: bool,
    /// 窓の題名（失敗の窓に使う）。
    pub title: String,
    /// 1枚だけの読み込み（import_dropped_image）。
    #[serde(default)]
    pub single: bool,
    /// デジタル TP でアンチエイリアスを取り除いたコマの数（知らせに使う）。
    #[serde(default)]
    pub antialias_frames: usize,
}

fn conversion(request: &ImageRequest, detected: Option<[u8; 3]>) -> Result<Conversion, String> {
    Ok(match request.conversion.as_str() {
        "draft" => Conversion::Draft,
        "plain" => Conversion::Plain,
        "digital" => Conversion::Reduce(ReduceSettings {
            mode: ExtractionMode::Digital,
            palette: None,
            flatten_paper: false,
            blur: 0.0,
            despeckle: 0,
            alpha_threshold: ALPHA_THRESHOLD,
            opaque_background: false,
            background: None,
            tone_curve_points: Vec::new(),
        }),
        "reduce" => {
            let form = request.form.as_ref().ok_or("2値化の設定がありません")?;
            let palette = form.palette.clone().ok_or(
                "現在の設定はまだプレビューへ反映されていません。先に「プレビュー」を押してください。",
            )?;
            Conversion::Reduce(form.settings(detected, palette))
        }
        other => return Err(format!("変換の種類が不明です：{other}")),
    })
}

/// 画像を読み込む（別のスレッド）。終わると `import-job` の done を送る。
#[tauri::command]
pub fn import_images(app: AppHandle, request: ImageRequest) -> Result<(), String> {
    let mut request = request;
    sort_natural(&mut request.paths);
    if request.paths.is_empty() {
        return Err("画像ファイルがありません。".into());
    }
    let detected = {
        let state = app.state::<ImportsShared>();
        let guard = state.lock().unwrap();
        guard
            .prepared
            .as_ref()
            .filter(|p| p.paths == request.paths)
            .and_then(|p| p.background)
    };
    let conversion = conversion(&request, detected)?;
    {
        let session = app.state::<Shared>();
        let s = session.lock().unwrap();
        if s.project.is_none() {
            return Err("作品を開いていません".into());
        }
    }
    let cancel = begin_job(&app)?;
    std::thread::spawn(move || {
        let event = run_images(&app, &request, &conversion, &cancel);
        finish(&app, event);
    });
    Ok(())
}

fn run_images(
    app: &AppHandle,
    request: &ImageRequest,
    conversion: &Conversion,
    cancel: &AtomicBool,
) -> ImportEvent {
    let fail = |text: String| ImportEvent {
        error: Some((request.title.clone(), text)),
        ..ImportEvent::default()
    };
    let wrap = |error: String| -> String {
        if request.single {
            format!(
                "{} を読み込めませんでした。\n\n{error}",
                file_name(&request.paths[0])
            )
        } else {
            format!("画像を読み込めませんでした。\n\n{error}")
        }
    };
    let paths = &request.paths;
    let total = paths.len();
    // 画像の配置と、その直後の使用色の認識を同じ数え方で出す（Qt の combined_total）。
    let report = |value: usize, label: String| {
        progress(
            app,
            value,
            total,
            format!("{label}（画像 {value}/{total}）"),
        );
    };
    let reduce = matches!(conversion, Conversion::Reduce(s) if s.palette.is_some());
    let mut decoded: Vec<PremulImage> = Vec::with_capacity(total);
    for (index, path) in paths.iter().enumerate() {
        if cancel.load(Ordering::Relaxed) {
            return ImportEvent {
                cancelled: true,
                ..ImportEvent::default()
            };
        }
        report(index, format!("{} を読み込んでいます", file_name(path)));
        let image = match read_premul(path) {
            Ok(image) => image,
            Err(e) => return fail(wrap(format!("{}\n{e}", file_name(path)))),
        };
        if reduce {
            report(
                index,
                format!(
                    "{} の元画像へトーンカーブを適用し、共通パレットで2値化しています",
                    file_name(path)
                ),
            );
        }
        let jpeg = import_image::is_jpeg_path(path);
        match convert_with(&image, jpeg, conversion) {
            Ok(image) => decoded.push(image),
            Err(e) => return fail(wrap(format!("{}\n{e}", file_name(path)))),
        }
    }
    if cancel.load(Ordering::Relaxed) {
        return ImportEvent {
            cancelled: true,
            ..ImportEvent::default()
        };
    }
    report(total, "画像の配置を準備しています".into());
    let name = request
        .layer_name
        .as_deref()
        .map(str::trim)
        .filter(|n| !n.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| {
            Path::new(&paths[0])
                .file_stem()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default()
        });
    let draft = matches!(conversion, Conversion::Draft);
    let result = crate::with_session(app, "import", |s| {
        apply_tracks(
            s,
            |doc| {
                let (width, height) = doc.size();
                let mut images = Vec::with_capacity(decoded.len());
                for (offset, image) in decoded.iter().enumerate() {
                    report(
                        offset,
                        format!("{} / {} 枚を配置しています", offset + 1, total),
                    );
                    let placed = import_image::place_centered(width, height, image)
                        .and_then(|p| import_image::to_document_image(&p))
                        .map_err(|e| e.to_string())?;
                    images.push(placed);
                }
                let mut track = ImportedTrack::new(
                    if name.is_empty() {
                        format!("Image {}", doc.track_order().len())
                    } else {
                        name.clone()
                    },
                    images,
                );
                track.draft = draft;
                track.genga = request.genga;
                track.sources = Some(paths.clone());
                Ok((vec![track], Mode::Sequence))
            },
            decoded.iter().map(|i| (i.width(), i.height())).collect(),
        )
    });
    let applied = match result {
        Ok(applied) => applied,
        Err(e) => return fail(wrap(e)),
    };
    report(total, "読み込み完了".into());
    let mut message = applied.expanded;
    let note = if request.genga {
        Some(format!("{total}枚の原画を読み込み、原画の印を付けました。"))
    } else if draft {
        Some(format!(
            "{total}枚を下書きレイヤーへ変換せず読み込みました。"
        ))
    } else if total > 1 {
        let reduction = match conversion {
            Conversion::Reduce(s) if s.mode == ExtractionMode::Digital => format!(
                "、{}枚のアンチエイリアスを取り除いて",
                request.antialias_frames
            ),
            Conversion::Reduce(s) => {
                let method = if s.opaque_background {
                    "元画像へトーンカーブ適用後に2値化"
                } else {
                    "半透明を二値化"
                };
                format!(
                    "、{method}して1枚目の共通パレット{}色を適用",
                    s.palette.as_ref().map_or(0, Vec::len)
                )
            }
            _ => String::new(),
        };
        Some(format!(
            "{total}枚の画像をタイムラインへ連番配置{reduction}し、使用色認識まで完了しました。"
        ))
    } else {
        None
    };
    if note.is_some() {
        message = note;
    }
    ImportEvent {
        message,
        // 下書き（変換せず）と原画は初期状態を尋ねない（Qt も import_dropped_images だけ）。
        offer_initial: (!draft && !request.genga).then(|| applied.layers.clone()),
        ..ImportEvent::default()
    }
}

/// 文書へ書いた結果。
struct Applied {
    /// 足した列。
    layers: Vec<usize>,
    /// キャンバスを広げたときの知らせ。
    expanded: Option<String>,
}

/// 列を足す（キャンバスが小さければ先に広げる）。Undo 1回（「全体編集」）。
/// `build` は広げたあとの文書から列を作る。`sizes` は置く絵の大きさ。
fn apply_tracks(
    s: &mut Session,
    build: impl FnOnce(&Document) -> Result<(Vec<ImportedTrack>, Mode), String>,
    sizes: Vec<(usize, usize)>,
) -> Result<Applied, String> {
    s.edit_with(Some(LABEL), |doc, ui| {
        let (width, height) = doc.size();
        let max_w = sizes.iter().map(|s| s.0).fold(width, usize::max);
        let max_h = sizes.iter().map(|s| s.1).fold(height, usize::max);
        let mut expanded = None;
        if (max_w, max_h) != (width, height) {
            doc.resize_canvas(max_w, max_h).map_err(|e| e.to_string())?;
            expanded = Some(format!(
                "画像に合わせてキャンバスを {max_w} × {max_h}px に拡張しました。"
            ));
        }
        let (tracks, mode) = build(doc)?;
        let start = current_index(doc).0;
        let first = doc.track_order().len();
        let count = tracks.len();
        doc.import_image_tracks(start, tracks)
            .map_err(|e| e.to_string())?;
        ui.set_mode(doc, mode)?;
        Ok((
            Applied {
                layers: (first..first + count).collect(),
                expanded,
            },
            true,
        ))
    })
}

// ---------------------------------------------------------------------------
// PSD（ImportController.psd_dialog）
// ---------------------------------------------------------------------------

const PSD_TITLE: &str = "PSD読み込み";

/// PSD を読み、最上位のレイヤーごとに列を足す（タイムシート表示にする）。
#[tauri::command]
pub fn import_psd(app: AppHandle, path: String) -> Result<(), String> {
    {
        let session = app.state::<Shared>();
        if session.lock().unwrap().project.is_none() {
            return Err("作品を開いていません".into());
        }
    }
    let cancel = begin_job(&app)?;
    std::thread::spawn(move || {
        let event = run_psd(&app, &path, &cancel);
        finish(&app, event);
    });
    Ok(())
}

fn run_psd(app: &AppHandle, path: &str, cancel: &AtomicBool) -> ImportEvent {
    let fail = |text: String| ImportEvent {
        error: Some((
            PSD_TITLE.into(),
            format!("PSDを読み込めませんでした。\n\n{text}"),
        )),
        ..ImportEvent::default()
    };
    let data = match read_file(path) {
        Ok(data) => data,
        Err(e) => return fail(e),
    };
    let read = psd_import::read_psd(&data, &mut |done, total| {
        progress(
            app,
            done,
            total,
            format!("PSDのレイヤーを合成しています（{done}/{total}）"),
        );
        !cancel.load(Ordering::Relaxed)
    });
    let read = match read {
        Ok(Some(read)) => read,
        Ok(None) => {
            return ImportEvent {
                cancelled: true,
                ..ImportEvent::default()
            }
        }
        Err(e) => return fail(e.to_string()),
    };
    let (width, height) = (read.width, read.height);
    let count = read.tracks.len();
    let mut premultiplied = Vec::with_capacity(count);
    for track in &read.tracks {
        let mut images = Vec::with_capacity(track.images.len());
        for rgba in &track.images {
            match import_image::to_premultiplied(width, height, rgba) {
                Ok(image) => images.push(image),
                Err(e) => return fail(e.to_string()),
            }
        }
        premultiplied.push(images);
    }
    let result = crate::with_session(app, "import", |s| {
        apply_tracks(
            s,
            |doc| {
                let (w, h) = doc.size();
                let mut tracks = Vec::with_capacity(count);
                for (source, images) in read.tracks.iter().zip(&premultiplied) {
                    let mut placed = Vec::with_capacity(images.len());
                    for image in images {
                        placed.push(
                            import_image::place_centered(w, h, image)
                                .and_then(|p| import_image::to_document_image(&p))
                                .map_err(|e| e.to_string())?,
                        );
                    }
                    let mut track = ImportedTrack::new(source.name.clone(), placed);
                    track.visible = source.visible;
                    track.opacity = source.opacity;
                    tracks.push(track);
                }
                Ok((tracks, Mode::Sheet))
            },
            vec![(width, height)],
        )
    });
    if let Err(e) = result {
        return fail(e);
    }
    let mut text = format!("PSDから{count}レイヤーを読み込みました。");
    if read.skipped > 0 {
        text.push_str(&format!(
            "\n調整レイヤーなど{}項目は破棄しました。",
            read.skipped
        ));
    }
    ImportEvent {
        info: Some((PSD_TITLE.into(), text)),
        ..ImportEvent::default()
    }
}

// ---------------------------------------------------------------------------
// 線画カットフォルダーを初期状態として読み込む（InitialStateController._import_line_art）
// ---------------------------------------------------------------------------

const LINE_ART_TITLE: &str = "線画カットフォルダーを初期状態として読み込む";
/// InitialStateUndo.HISTORY_LABEL
const INITIAL_LABEL: &str = "初期状態の読み込み";

/// レイヤー名をカットフォルダーの列の名前（casefold）にする。見つからなければ
/// 使えない文字を `_` にした名前でも探す。
fn line_art_key<'a>(
    name: &str,
    cels: &'a [(String, std::collections::BTreeMap<i64, String>)],
) -> Option<&'a std::collections::BTreeMap<i64, String>> {
    use pma_core::export::cut_folder::INVALID_NAME_CHARS;
    use pma_core::export::py;
    let find = |key: &str| cels.iter().find(|(k, _)| k == key).map(|(_, v)| v);
    let name = py::strip(name);
    find(&py::casefold(name)).or_else(|| {
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
        find(&py::casefold(py::strip_chars(&replaced, " .")))
    })
}

/// 線画を合わせた結果（Undo の前に全部読んで確かめる）。
pub struct LineArt {
    /// (レイヤー, 番号, 絵)
    pub images: Vec<(usize, pma_core::document::model::CellNumber, RgbaImage)>,
    /// 対応する線画がなかったセルの数。
    pub missing: usize,
}

/// 文書の絵とカットフォルダーのセル画像を合わせる。失敗は Qt の文言。
pub fn match_line_art(doc: &Document, root: &Path) -> Result<LineArt, String> {
    let contents = pma_io::scan_cut_folder(root).map_err(|e| e.to_string())?;
    let (width, height) = doc.size();
    let mut loaded: std::collections::HashMap<String, RgbaImage> = Default::default();
    let mut images = Vec::new();
    let mut missing = 0;
    for target in doc.initial_targets() {
        let source = line_art_key(&target.name, &contents.cels)
            .and_then(|cels| cels.get(&target.number.get()));
        let Some(relative) = source else {
            missing += 1;
            continue;
        };
        let name = file_name(relative);
        if !loaded.contains_key(relative) {
            let path = root.join(relative);
            let image = pma_io::read_image_file(&path)
                .map_err(|error| format!("{name} を読み込めません。\n{error}"))?;
            if (image.width(), image.height()) != (width, height) {
                return Err(format!(
                    "線画のサイズが現在のセルと一致しません：{name}\n線画：{} × {}px／現在のセル：{width} × {height}px",
                    image.width(),
                    image.height()
                ));
            }
            let image = import_image::to_document_image(&image).map_err(|e| e.to_string())?;
            loaded.insert(relative.clone(), image);
        }
        images.push((target.layer, target.number, loaded[relative].clone()));
    }
    Ok(LineArt { images, missing })
}

/// 合わせた線画を初期状態にする（Undo 1回「初期状態の読み込み」）。知らせの文を返す。
pub fn apply_line_art(s: &mut Session, art: LineArt) -> Result<String, String> {
    let missing = art.missing;
    let count = s.edit_with(Some(INITIAL_LABEL), |doc, _ui| {
        let count = doc
            .set_line_art_initial_images(art.images)
            .map_err(|e| e.to_string())?;
        Ok((count, true))
    })?;
    Ok(format!(
        "{count}セルの線画を初期状態として読み込みました。対応する線画がない{missing}セルは変更していません。"
    ))
}

/// 線画カットフォルダーを読み、レイヤー名とセル番号で初期状態に合わせる。
#[tauri::command]
pub fn import_initial_state(app: AppHandle, path: String) -> Result<(), String> {
    let cancel = begin_job(&app)?;
    std::thread::spawn(move || {
        let fail = |text: String| ImportEvent {
            error: Some((
                LINE_ART_TITLE.into(),
                format!(
                    "線画を読み込めませんでした。現在の絵と初期状態は変更していません。\n\n{text}"
                ),
            )),
            ..ImportEvent::default()
        };
        progress(&app, 0, 1, "線画の初期状態を読み込んでいます…".into());
        // 読む間は文書の写しを使う（Document は共有の絵なので写しは軽い）。
        let doc = {
            let session = app.state::<Shared>();
            let s = session.lock().unwrap();
            s.project.as_ref().map(|p| p.document.clone())
        };
        let event = match doc {
            None => fail("作品を開いていません".into()),
            Some(doc) => match match_line_art(&doc, Path::new(&path)) {
                Err(e) => fail(e),
                Ok(_) if cancel.load(Ordering::Relaxed) => ImportEvent {
                    cancelled: true,
                    ..ImportEvent::default()
                },
                Ok(art) if art.images.is_empty() => ImportEvent {
                    info: Some((
                        LINE_ART_TITLE.into(),
                        "一致するセルがありません。読み込み元のレイヤー名とセル番号を確認してください。"
                            .into(),
                    )),
                    ..ImportEvent::default()
                },
                Ok(art) => match crate::with_session(&app, "import", |s| apply_line_art(s, art)) {
                    Ok(message) => ImportEvent {
                        message: Some(message),
                        ..ImportEvent::default()
                    },
                    Err(e) => fail(e),
                },
            },
        };
        finish(&app, event);
    });
    Ok(())
}

// ---------------------------------------------------------------------------
// 色塗り前の初期状態（offer_after_import）
// ---------------------------------------------------------------------------

/// 読み込んだレイヤーのうち、初期状態をまだ持たない絵の数（0 なら尋ねない）と、
/// 環境設定の「常に保持する」。
#[tauri::command]
pub fn import_initial_pending(
    session: State<Shared>,
    shell: State<ShellShared>,
    layers: Option<Vec<usize>>,
) -> (usize, bool) {
    let s = session.lock().unwrap();
    let count = s
        .project
        .as_ref()
        .map_or(0, |p| p.document.cells_without_initial(layers.as_deref()));
    let always = shell
        .lock()
        .unwrap()
        .prefs
        .resolved()
        .keep_initial_state_on_cut_folder;
    (count, always)
}

/// 読み込んだ絵を色塗り前の初期状態として保持する（Qt も Undo に積まない）。
/// `always` なら環境設定の「常に保持する」を入れる。保持した数を返す。
#[tauri::command]
pub fn import_keep_initial(
    app: AppHandle,
    layers: Option<Vec<usize>>,
    always: bool,
) -> Result<String, String> {
    if always {
        let shell = app.state::<ShellShared>();
        let mut sh = shell.lock().unwrap();
        sh.prefs
            .set(crate::prefs::KEEP_INITIAL_STATE, Value::Bool(true))?;
    }
    let kept = crate::with_session(&app, "import", |s| {
        s.edit_with(None, |doc, _| {
            let kept = doc.keep_initial_images(layers.as_deref());
            Ok((kept, kept > 0))
        })
    })?;
    Ok(if kept > 0 {
        format!(
            "{kept}セルを色塗り前の初期状態として保持しました。保存するとプロジェクトに入ります。"
        )
    } else {
        "初期状態として保持できるセルがありません。".into()
    })
}

// ---------------------------------------------------------------------------
// レイアウト用紙（insert_layout_paper）
// ---------------------------------------------------------------------------

/// 用紙の画像を、新しい文書の一番下の下書きレイヤーとして入れる（履歴には積まない）。
/// 読めなければ Qt と同じ知らせを Err で返す。
pub fn insert_layout_paper(s: &mut Session, path: &Path) -> Result<(), String> {
    const UNREADABLE: &str = "レイアウト用紙の画像を読み込めませんでした。";
    let data = std::fs::read(path).map_err(|_| UNREADABLE.to_string())?;
    let image = import_image::read_image(&data).map_err(|_| UNREADABLE.to_string())?;
    s.edit_with(None, |doc, _| {
        let (width, height) = doc.size();
        let placed = import_image::place_centered(width, height, &image)
            .and_then(|p| import_image::to_document_image(&p))
            .map_err(|e| e.to_string())?;
        doc.insert_layout_paper("レイアウト用紙", placed)
            .map_err(|e| e.to_string())?;
        Ok(((), true))
    })
}

/// 用紙の id から画像の場所を引く（`layout_paper.paper` と image_path）。
pub fn paper_path(app: &AppHandle, id: &str) -> Option<PathBuf> {
    crate::papers::papers_list(app.clone(), app.state())
        .ok()?
        .into_iter()
        .find(|p| p.id == id)
        .map(|p| PathBuf::from(p.path))
}

#[cfg(test)]
#[path = "imports_tests.rs"]
mod tests;
