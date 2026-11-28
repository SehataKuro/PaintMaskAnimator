//! キャンバスの見え方（Qt 版のキャンバスの下のバーと、表示の環境設定）。
//!
//! - 背景以外を黒シルエット表示（`toggle_silhouette`）：pma-core の `DisplayOptions.silhouette`。
//! - 隣接色分け・チャンネル・露光量（`main_window_view_preview.py`）：`ViewOptions.preview`。
//! - 色塗り前の初期状態と比べる（`InitialStateController.set_compare`）：`ViewOptions.show_initial_state`。
//! - 背景の表示の市松模様（`paint_view_background`）：合成の下地を透明にし、画面が市松模様を敷く。
//! - 縮小表示（`display_shrink_mode`）の「線を残す」：pma-core の `display_frame` で縮めた画像を渡す。
//! - 今の絵をクリップボード・フォルダーへ（`main_window_export.py` の copy_canvas_to_clipboard・
//!   save_canvas_snapshot）。
//!
//! どれも画面の見え方だけを変え、文書・書き出し・履歴には影響しない（Qt と同じ）。
//! ズーム・回転・反転は画面（ui/viewer.js）の頂点の変換だけで行う。

use crate::prefs::{self, Prefs};
use crate::session::{current_index, Session, Shared};
use crate::shell::ShellShared;
use pma_core::compose::{
    self, view::background_color, Background, Channel, DisplayOptions, DisplayTarget,
    MapColorMapping, PremulImage, ShrinkMode, ViewOptions, ViewPreview,
};
use pma_core::document::model::Document;
use pma_core::image::{Rect, RgbaImage};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;
use tauri::ipc::Response;
use tauri::{Emitter, Manager, State};
use tauri_plugin_dialog::DialogExt;

/// 露光量の範囲（`view_preview.EXPOSURE_MIN`・`EXPOSURE_MAX`）。
pub const EXPOSURE_RANGE: (f64, f64) = compose::view::EXPOSURE_RANGE;
/// 市松模様の設定を変えていないときの色（`preferences.background_checker_color`）。
const DEFAULT_CHECKER_COLOR: [u8; 3] = [255, 255, 255];
/// 「今の絵をフォルダーへ」の保存先の設定の鍵（`preferences.SNAPSHOT_FOLDER_KEY`）。
const SNAPSHOT_FOLDER_KEY: &str = "snapshot_folder";

/// キャンバスの見え方の状態（Session が1つ持つ）。
#[derive(Clone, Debug)]
pub struct ViewState {
    /// 背景以外を黒シルエット表示（`silhouette_non_background`）。
    pub silhouette: bool,
    /// 初期状態を見せる（`show_initial_state`）。
    pub show_initial_state: bool,
    /// 露光量・チャンネル・隣接色分けと仮の色。
    pub preview: ViewPreview,
    /// 露光量のボタンで 0 に戻す前の値（`ViewPreviewController._last_exposure`）。
    pub last_exposure: f64,
    /// 隣接色分けの対応。仮の色の並びごとに持ち、コマ・レイヤー・作品をまたいで使う
    /// （`PaintCanvas._view_map_color_mappings`）。
    mappings: BTreeMap<Vec<[u8; 3]>, MapColorMapping>,
    /// 環境設定：指定した背景色を市松模様で表示する。
    pub checker_enabled: bool,
    /// 環境設定：市松模様にする背景色。
    pub checker_color: [u8; 3],
    /// 環境設定：縮小表示のしかた。
    pub shrink_mode: ShrinkMode,
}

impl Default for ViewState {
    fn default() -> Self {
        Self {
            silhouette: false,
            show_initial_state: false,
            preview: ViewPreview::default(),
            last_exposure: 1.0,
            mappings: BTreeMap::new(),
            checker_enabled: false,
            checker_color: DEFAULT_CHECKER_COLOR,
            shrink_mode: ShrinkMode::Nearest,
        }
    }
}

impl ViewState {
    /// 編集画面だけの表示設定（初期状態・表示プレビュー）を掛けるか。
    pub fn active(&self) -> bool {
        self.show_initial_state || self.preview.active()
    }

    fn view_options(&self) -> ViewOptions {
        ViewOptions {
            show_initial_state: self.show_initial_state,
            preview: self.preview.clone(),
        }
    }

    fn mapping(&mut self) -> &mut MapColorMapping {
        self.mappings
            .entry(self.preview.palette.clone())
            .or_default()
    }

    /// 市松模様を敷くか（`paint_view_background`：設定が有効で、背景の表示色が指定の色）。
    pub fn checker(&self, background: [u8; 3]) -> bool {
        self.checker_enabled && background == self.checker_color
    }

    /// 合成の下地（`view_background_color`）。市松模様のときは透明にし、画面が模様を敷く。
    pub fn background(&self, background: [u8; 3]) -> Background {
        if self.checker(background) {
            return Background::Transparent;
        }
        if self.preview.adjusts_pixels() {
            return Background::Color(background_color(background, &self.preview));
        }
        if background == [255, 255, 255] {
            Background::White
        } else {
            Background::Color(background)
        }
    }

    /// 使用色パネルの絞り込みに、シルエットを足した表示フィルタ。
    pub fn display_options(&self, colors: &DisplayOptions) -> DisplayOptions {
        DisplayOptions {
            silhouette: self.silhouette,
            ..colors.clone()
        }
    }

    /// 露光量を変える（`set_exposure`）。
    pub fn set_exposure(&mut self, value: f64) {
        let value = value.clamp(EXPOSURE_RANGE.0, EXPOSURE_RANGE.1);
        if value.abs() > 1e-9 {
            self.last_exposure = value;
        }
        self.preview.exposure = value;
    }

    /// 露光量を 0 に戻す。0 のときは直前の値に戻す（`toggle_exposure`）。
    pub fn toggle_exposure(&mut self) {
        if self.preview.exposure.abs() > 1e-9 {
            self.preview.exposure = 0.0;
        } else {
            self.set_exposure(self.last_exposure);
        }
    }

    /// チャンネルを選ぶ（`set_channel`）。同じチャンネルをもう一度選んだら RGB に戻す。
    pub fn toggle_channel(&mut self, channel: Channel) {
        self.preview.channel = if channel == self.preview.channel {
            Channel::Rgb
        } else {
            channel
        };
    }

    /// 環境設定（隣接色分けの仮の色・市松模様・縮小表示）を読む。
    pub fn apply_prefs(&mut self, prefs: &Prefs) {
        self.preview.palette = prefs
            .map_palette()
            .iter()
            .filter_map(|c| prefs::parse_hex(c))
            .collect();
        self.checker_enabled = prefs
            .get(prefs::BACKGROUND_CHECKER_ENABLED)
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false);
        self.checker_color =
            prefs::parse_hex(&prefs.background_checker_color()).unwrap_or(DEFAULT_CHECKER_COLOR);
        self.shrink_mode = match prefs
            .get(prefs::DISPLAY_SHRINK_MODE)
            .and_then(serde_json::Value::as_str)
        {
            Some("keep_lines") => ShrinkMode::KeepLines,
            _ => ShrinkMode::Nearest,
        };
    }
}

/// 編集画面の表示の合成（`draw_frame_direct` を元の解像度で）。`rect` が None なら全体。
///
/// 表示プレビューと初期状態を使わないときは書き出しと同じ合成（`composite`）、使うときは
/// pma-core の `view_composite`。どちらも Qt と同じ画素になる（tests/test_core_compose_compare.py）。
pub fn compose_display(
    document: &Document,
    frame: usize,
    colors: &DisplayOptions,
    view: &mut ViewState,
    settings: &crate::tools::Settings,
    rect: Option<Rect>,
) -> Result<PremulImage, pma_core::Error> {
    let options = view.display_options(colors);
    let rgb = settings.background_color;
    let background = if view.checker(rgb) || view.preview.adjusts_pixels() {
        view.background(rgb)
    } else {
        // 使用色パネル（colors.rs）と同じ背景の表示色。
        crate::colors::canvas_background(settings)
    };
    if !view.active() {
        return match rect {
            Some(rect) => compose::composite_rect(document, frame, &options, background, rect),
            None => compose::composite(document, frame, &options, background),
        };
    }
    let view_options = view.view_options();
    compose::view_composite(
        document,
        frame,
        &options,
        &view_options,
        view.mapping(),
        background,
        rect,
    )
}

/// Session の背景の表示色（描画色の「背景」）。
fn background_of(s: &Session) -> [u8; 3] {
    s.tools.settings.background_color
}

/// 色塗り前の初期状態を保持している絵があるか（`InitialStateController.count(cells())`）。
pub fn has_initial_state(document: &Document) -> bool {
    (0..document.frame_order().len()).any(|frame| {
        compose::frame_layers(document, frame).is_ok_and(|layers| {
            layers
                .iter()
                .flatten()
                .any(|layer| layer.initial_image.is_some())
        })
    })
}

// ---------------------------------------------------------------------------
// 画面へ渡す状態
// ---------------------------------------------------------------------------

/// 画面のバーとメニューの表示（`ViewPreviewController.sync`）。
#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct ViewInfo {
    pub silhouette: bool,
    pub map_colors: bool,
    pub channel: &'static str,
    pub exposure: f64,
    pub show_initial_state: bool,
    /// 色塗り前の初期状態を保持している絵があるか。
    pub has_initial_state: bool,
    /// 画面が市松模様を敷くか。
    pub checker: bool,
    /// 縮小表示のしかた（`nearest`・`keep_lines`）。
    pub shrink_mode: &'static str,
}

fn channel_name(channel: Channel) -> &'static str {
    match channel {
        Channel::Rgb => "rgb",
        Channel::Red => "red",
        Channel::Green => "green",
        Channel::Blue => "blue",
        Channel::Alpha => "alpha",
    }
}

fn parse_channel(name: &str) -> Result<Channel, String> {
    Ok(match name {
        "rgb" => Channel::Rgb,
        "red" => Channel::Red,
        "green" => Channel::Green,
        "blue" => Channel::Blue,
        "alpha" => Channel::Alpha,
        _ => return Err(format!("不明なチャンネル：{name}")),
    })
}

pub fn info(s: &Session) -> ViewInfo {
    let v = &s.view;
    ViewInfo {
        silhouette: v.silhouette,
        map_colors: v.preview.map_colors,
        channel: channel_name(v.preview.channel),
        exposure: v.preview.exposure,
        show_initial_state: v.show_initial_state,
        has_initial_state: s
            .project
            .as_ref()
            .is_some_and(|p| has_initial_state(&p.document)),
        checker: v.checker(background_of(s)),
        shrink_mode: match v.shrink_mode {
            ShrinkMode::KeepLines => "keep_lines",
            ShrinkMode::Nearest => "nearest",
        },
    }
}

/// 画面からの変更。どれも省ける。
#[derive(Clone, Debug, Default, Deserialize)]
pub struct ViewChange {
    pub silhouette: Option<bool>,
    pub map_colors: Option<bool>,
    /// チャンネルを選ぶ（チャンネルのボタンのメニュー）。
    pub channel: Option<String>,
    /// チャンネルを切り替える（`a_channel_*`：同じならRGBへ）。
    pub toggle_channel: Option<String>,
    pub exposure: Option<f64>,
    /// 露光量のボタン（`toggle_exposure`）。
    #[serde(default)]
    pub toggle_exposure: bool,
    pub show_initial_state: Option<bool>,
}

/// 変更の結果。`message` は Qt がステータスバーに出す文（画面が訳す）。
#[derive(Clone, Debug, Serialize)]
pub struct ViewChanged {
    pub info: ViewInfo,
    pub message: Option<&'static str>,
}

/// 変更を当てる。見え方が変わったら表示を作り直す。
pub fn apply(s: &mut Session, change: &ViewChange) -> Result<ViewChanged, String> {
    let before = s.view.clone();
    let mut message = None;
    let v = &mut s.view;
    if let Some(on) = change.silhouette {
        v.silhouette = on;
    }
    if let Some(on) = change.map_colors {
        v.preview.map_colors = on;
    }
    if let Some(name) = &change.channel {
        v.preview.channel = parse_channel(name)?;
    }
    if let Some(name) = &change.toggle_channel {
        v.toggle_channel(parse_channel(name)?);
    }
    if let Some(value) = change.exposure {
        v.set_exposure(value);
    }
    if change.toggle_exposure {
        v.toggle_exposure();
    }
    if let Some(mut on) = change.show_initial_state {
        let has = s
            .project
            .as_ref()
            .is_some_and(|p| has_initial_state(&p.document));
        if on && !has {
            on = false;
            message = Some("色塗り前の初期状態を保持していないので、比べられません。");
        } else if on {
            message = Some("色塗り前の初期状態を表示しています。もう一度押すと今の絵に戻ります。");
        }
        s.view.show_initial_state = on;
    }
    if !same_look(&before, &s.view) {
        s.redisplay()?;
    }
    Ok(ViewChanged {
        info: info(s),
        message,
    })
}

fn same_look(a: &ViewState, b: &ViewState) -> bool {
    a.silhouette == b.silhouette
        && a.show_initial_state == b.show_initial_state
        && a.preview == b.preview
        && a.checker_enabled == b.checker_enabled
        && a.checker_color == b.checker_color
}

fn notify(app: &tauri::AppHandle, info: &ViewInfo) {
    let _ = app.emit("view-changed", info);
    // キャンバスは display-changed で読み直す（app.js）。
    let _ = app.emit("display-changed", ());
}

/// 見え方の今の状態。
#[tauri::command]
pub fn view_state(session: State<Shared>) -> ViewInfo {
    info(&session.lock().unwrap())
}

/// 見え方を変える。
#[tauri::command]
pub fn view_set(
    app: tauri::AppHandle,
    session: State<Shared>,
    change: ViewChange,
) -> Result<ViewChanged, String> {
    let changed = apply(&mut session.lock().unwrap(), &change)?;
    notify(&app, &changed.info);
    Ok(changed)
}

/// 環境設定が変わった（shell.rs の prefs_set から呼ぶ）。表示に関わる鍵なら読み直す。
pub fn prefs_changed(app: &tauri::AppHandle, key: &str) {
    if ![
        prefs::MAP_PALETTE,
        prefs::BACKGROUND_CHECKER_ENABLED,
        prefs::BACKGROUND_CHECKER_COLOR,
        prefs::DISPLAY_SHRINK_MODE,
    ]
    .contains(&key)
    {
        return;
    }
    let info = {
        let shell = app.state::<ShellShared>();
        let session = app.state::<Shared>();
        let mut s = session.lock().unwrap();
        let before = s.view.clone();
        s.view.apply_prefs(&shell.lock().unwrap().prefs);
        if !same_look(&before, &s.view) {
            let _ = s.redisplay();
        }
        info(&s)
    };
    notify(app, &info);
}

// ---------------------------------------------------------------------------
// 縮小表示（線を残す）
// ---------------------------------------------------------------------------

/// 今のコマを `width` × `height` に「線を残す」で縮めた表示（`_display_image_for_rect`）。
///
/// 各レイヤーを縮めてから重ねる（pma-core の `display_frame`）。オニオンを重ねているときは
/// オニオンを含めた表示の画像をまとめて縮める（オニオンは Qt でも縮小のしかたを使わない）。
pub fn shrunk(s: &mut Session, width: usize, height: usize) -> Result<RgbaImage, String> {
    let err = |e: pma_core::Error| e.to_string();
    let (dw, dh) = (s.display.width(), s.display.height());
    let (width, height) = (width.clamp(1, dw.max(1)), height.clamp(1, dh.max(1)));
    let background = background_of(s);
    let onion = s.timeline.onion;
    let image = match &s.project {
        Some(p) if !onion => {
            let doc = &p.document;
            let (frame, _) = current_index(doc);
            let options = s.view.display_options(&s.colors.display);
            let bg = s.view.background(background);
            let view = s.view.view_options();
            let target = DisplayTarget {
                width,
                height,
                mode: ShrinkMode::KeepLines,
                flip_horizontal: false,
                flip_vertical: false,
            };
            compose::display_frame(
                doc,
                frame,
                &options,
                &view,
                s.view.mapping(),
                bg,
                target,
                None,
            )
            .map_err(err)?
        }
        _ => {
            let source = PremulImage::from_straight(dw, dh, s.display.rgba()).map_err(err)?;
            compose::shrink_keeping_lines(&source, width, height).map_err(err)?
        }
    };
    RgbaImage::from_rgba(image.width(), image.height(), &image.to_straight_bytes()).map_err(err)
}

/// [`shrunk`] を画面へ（先頭 16 バイトに 0, 0, 幅, 高さ、続けて RGBA）。
#[tauri::command]
pub fn view_shrunk(
    session: State<Shared>,
    width: usize,
    height: usize,
) -> Result<Response, String> {
    let mut s = session.lock().unwrap();
    let image = shrunk(&mut s, width, height)?;
    let rect = Rect {
        x: 0,
        y: 0,
        width: image.width(),
        height: image.height(),
    };
    Ok(Response::new(crate::session::pack_rect(&image, Some(rect))))
}

// ---------------------------------------------------------------------------
// 今の絵の書き出し（クリップボード・フォルダー）
// ---------------------------------------------------------------------------

/// 今のコマの白地の合成の PNG（`ExportController.canvas_image`：使用色の絞り込みと
/// シルエットは掛け、表示プレビューは掛けない）。
fn canvas_png(app: &tauri::AppHandle) -> Result<Vec<u8>, String> {
    let (document, frame, options) = {
        let state = app.state::<Shared>();
        let s = state.lock().unwrap();
        let project = s.project.as_ref().ok_or("作品を開いていません")?;
        let (frame, _) = current_index(&project.document);
        (
            project.document.clone(),
            frame,
            s.view.display_options(&s.colors.display),
        )
    };
    pma_core::export::frame_png(&document, frame, &options).map_err(|e| e.to_string())
}

/// `folder` に置く、年月日時分秒（14桁、画面が今の時刻から作る）の .png のパス
/// （`snapshot_path`：同じ秒に2枚撮ったら連番を足す）。
pub fn snapshot_path(folder: &Path, stamp: &str) -> PathBuf {
    let mut path = folder.join(format!("{stamp}.png"));
    let mut suffix = 2;
    while path.exists() {
        path = folder.join(format!("{stamp}_{suffix}.png"));
        suffix += 1;
    }
    path
}

fn snapshot_folder(app: &tauri::AppHandle) -> String {
    let shell = app.state::<ShellShared>();
    let sh = shell.lock().unwrap();
    sh.prefs
        .get(SNAPSHOT_FOLDER_KEY)
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default()
        .to_string()
}

/// 保存先を選ぶ（`choose_snapshot_folder`）。選んだら真。
fn choose_snapshot_folder(app: &tauri::AppHandle, title: &str) -> Result<bool, String> {
    let current = snapshot_folder(app);
    let mut dialog = app.dialog().file().set_title(title);
    if Path::new(&current).is_dir() {
        dialog = dialog.set_directory(&current);
    }
    let Some(folder) = dialog
        .blocking_pick_folder()
        .and_then(|p| p.into_path().ok())
    else {
        return Ok(false);
    };
    let shell = app.state::<ShellShared>();
    let mut sh = shell.lock().unwrap();
    sh.prefs.set(
        SNAPSHOT_FOLDER_KEY,
        serde_json::Value::String(folder.display().to_string()),
    )?;
    Ok(true)
}

/// 「今の絵をフォルダーへ書き出す」の結果。
#[derive(Debug, Serialize)]
pub struct Snapshot {
    /// 書いたファイルの名前。保存先を選ばなかったら None。
    pub name: Option<String>,
    /// 書けなかったときのパス（画面が「画像を保存できませんでした」と出す）。
    pub failed: Option<String>,
}

/// 今の絵を保存先フォルダーへ年月日時分秒の名前で書く（`save_canvas_snapshot`）。
/// 保存先が無い・見つからない・`choose` のときだけ選び直す。
#[tauri::command]
pub async fn view_save_snapshot(
    app: tauri::AppHandle,
    choose: bool,
    stamp: String,
    title: String,
) -> Result<Snapshot, String> {
    let folder = snapshot_folder(&app);
    if (choose || folder.is_empty() || !Path::new(&folder).is_dir())
        && !choose_snapshot_folder(&app, &title)?
    {
        return Ok(Snapshot {
            name: None,
            failed: None,
        });
    }
    let folder = PathBuf::from(snapshot_folder(&app));
    let stamp: String = stamp.chars().filter(char::is_ascii_digit).collect();
    let path = snapshot_path(&folder, &stamp);
    let written =
        canvas_png(&app).and_then(|png| std::fs::write(&path, png).map_err(|e| e.to_string()));
    Ok(match written {
        Ok(()) => Snapshot {
            name: path.file_name().map(|n| n.to_string_lossy().into_owned()),
            failed: None,
        },
        Err(e) => {
            eprintln!("今の絵を保存できなかった：{e}");
            Snapshot {
                name: None,
                failed: Some(path.display().to_string()),
            }
        }
    })
}

/// 今の絵をクリップボードへ（`copy_canvas_to_clipboard`）。PNG を一時ファイルに書き、
/// OS の道具（macOS は osascript、Windows は PowerShell、ほかは xclip）で画像として載せる。
#[tauri::command]
pub async fn view_copy_canvas(app: tauri::AppHandle) -> Result<(), String> {
    let png = canvas_png(&app)?;
    let path = std::env::temp_dir().join(format!("pma-canvas-{}.png", std::process::id()));
    std::fs::write(&path, png).map_err(|e| e.to_string())?;
    let result = copy_png_file(&path);
    let _ = std::fs::remove_file(&path);
    result
}

fn copy_png_file(path: &Path) -> Result<(), String> {
    let file = path.display().to_string();
    let mut command = if cfg!(target_os = "macos") {
        let mut c = Command::new("osascript");
        c.args([
            "-e",
            &format!(
                "set the clipboard to (read (POSIX file \"{}\") as «class PNGf»)",
                file.replace('\\', "\\\\").replace('"', "\\\"")
            ),
        ]);
        c
    } else if cfg!(target_os = "windows") {
        let mut c = Command::new("powershell");
        c.args([
            "-NoProfile",
            "-STA",
            "-Command",
            &format!(
                "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; \
                 $i=[System.Drawing.Image]::FromFile('{}'); \
                 [System.Windows.Forms.Clipboard]::SetImage($i); $i.Dispose()",
                file.replace('\'', "''")
            ),
        ]);
        c
    } else {
        let mut c = Command::new("xclip");
        c.args(["-selection", "clipboard", "-t", "image/png", "-i", &file]);
        c
    };
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let status = command.status().map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err("クリップボードへ書けませんでした".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(name: &str) -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../tests/fixtures/projects")
            .join(name)
    }

    fn opened() -> Session {
        let mut s = Session::unloaded(8, 8);
        s.open(&fixture("v2/basic-bank-sheet.pman")).unwrap();
        s
    }

    fn expected(
        s: &Session,
        options: &DisplayOptions,
        view: &ViewOptions,
        bg: Background,
    ) -> Vec<u8> {
        let doc = &s.project.as_ref().unwrap().document;
        let (frame, _) = current_index(doc);
        let mut mapping = MapColorMapping::new();
        let image = if view.show_initial_state || view.preview.active() {
            compose::view_composite(doc, frame, options, view, &mut mapping, bg, None).unwrap()
        } else {
            compose::composite(doc, frame, options, bg).unwrap()
        };
        image.to_straight_bytes()
    }

    #[test]
    fn view_silhouette_uses_the_core_display_options() {
        let mut s = opened();
        let plain = s.display.rgba().to_vec();
        let change = ViewChange {
            silhouette: Some(true),
            ..Default::default()
        };
        let out = apply(&mut s, &change).unwrap();
        assert!(out.info.silhouette);
        let options = DisplayOptions {
            silhouette: true,
            ..s.colors.display.clone()
        };
        let want = expected(&s, &options, &ViewOptions::default(), Background::White);
        assert_eq!(s.display.rgba(), &want[..]);
        assert_ne!(s.display.rgba(), &plain[..]);
        // 作品を開き直しても残る（Qt のキャンバスの属性と同じ）。
        s.open(&fixture("v2/basic-bank-sheet.pman")).unwrap();
        assert_eq!(s.display.rgba(), &want[..]);
    }

    #[test]
    fn view_preview_uses_view_composite_and_the_previewed_background() {
        let mut s = opened();
        for change in [
            ViewChange {
                map_colors: Some(true),
                ..Default::default()
            },
            ViewChange {
                map_colors: Some(false),
                exposure: Some(-1.5),
                ..Default::default()
            },
            ViewChange {
                exposure: Some(0.0),
                toggle_channel: Some("alpha".into()),
                ..Default::default()
            },
        ] {
            apply(&mut s, &change).unwrap();
            let view = s.view.view_options();
            let bg = Background::Color(background_color([255, 255, 255], &view.preview));
            let want = expected(&s, &s.colors.display.clone(), &view, bg);
            assert_eq!(s.display.rgba(), &want[..]);
        }
        // アルファチャンネルの背景は黒（view_preview.background_color）。
        assert_eq!(
            s.view.background([255, 255, 255]),
            Background::Color([0, 0, 0])
        );
    }

    #[test]
    fn view_exposure_and_channel_toggle_like_qt() {
        let mut v = ViewState::default();
        v.toggle_exposure();
        assert_eq!(v.preview.exposure, 1.0);
        v.set_exposure(2.5);
        v.toggle_exposure();
        assert_eq!(v.preview.exposure, 0.0);
        v.toggle_exposure();
        assert_eq!(v.preview.exposure, 2.5);
        v.set_exposure(42.0);
        assert_eq!(v.preview.exposure, EXPOSURE_RANGE.1);
        v.toggle_channel(Channel::Red);
        assert_eq!(v.preview.channel, Channel::Red);
        v.toggle_channel(Channel::Red);
        assert_eq!(v.preview.channel, Channel::Rgb);
    }

    #[test]
    fn view_compare_needs_a_kept_initial_state() {
        let mut s = opened();
        let had = has_initial_state(&s.project.as_ref().unwrap().document);
        let on = ViewChange {
            show_initial_state: Some(true),
            ..Default::default()
        };
        let out = apply(&mut s, &on).unwrap();
        assert_eq!(out.info.show_initial_state, had);
        if !had {
            assert!(out.message.unwrap().contains("比べられません"));
        }
        let p = s.project.as_mut().unwrap();
        p.document.keep_initial_images(None);
        let out = apply(&mut s, &on).unwrap();
        assert!(out.info.show_initial_state && out.info.has_initial_state);
    }

    #[test]
    fn view_checker_makes_the_background_transparent() {
        let mut v = ViewState::default();
        assert_eq!(v.background([255, 255, 255]), Background::White);
        v.checker_enabled = true;
        assert_eq!(v.background([255, 255, 255]), Background::Transparent);
        assert_eq!(v.background([10, 20, 30]), Background::Color([10, 20, 30]));
    }

    #[test]
    fn view_shrunk_matches_display_frame() {
        let mut s = opened();
        let (w, h) = (s.display.width() / 3, s.display.height() / 3);
        let image = shrunk(&mut s, w, h).unwrap();
        let doc = &s.project.as_ref().unwrap().document;
        let (frame, _) = current_index(doc);
        let want = compose::display_frame(
            doc,
            frame,
            &s.colors.display,
            &ViewOptions::default(),
            &mut MapColorMapping::new(),
            Background::White,
            DisplayTarget {
                width: w,
                height: h,
                mode: ShrinkMode::KeepLines,
                flip_horizontal: false,
                flip_vertical: false,
            },
            None,
        )
        .unwrap();
        assert_eq!(image.rgba(), &want.to_straight_bytes()[..]);
    }

    #[test]
    fn view_snapshot_path_adds_a_number_in_the_same_second() {
        let dir = std::env::temp_dir().join(format!("pma-view-snap-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let first = snapshot_path(&dir, "20261128120000");
        assert_eq!(first.file_name().unwrap(), "20261128120000.png");
        std::fs::write(&first, b"x").unwrap();
        let second = snapshot_path(&dir, "20261128120000");
        assert_eq!(second.file_name().unwrap(), "20261128120000_2.png");
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
