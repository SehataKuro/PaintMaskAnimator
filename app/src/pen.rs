//! ペンの入力（筆圧・傾き）。
//!
//! macOS の WKWebView は、タブレットのペンを `pointerType=mouse`・`pressure=0` として
//! JavaScript に渡す（prototypes/README.md の確認 1）。そこで macOS では、主の窓に届く
//! 左ボタンの事件を NSEvent のローカルモニターで読み、ペンかどうかと筆圧・傾きを付けて
//! 画面へ `pen` の事件として送る。画面は macOS ではこの列だけで線を描き、
//! PointerEvent の位置は使わない（2つの列の順番が前後するのを避けるため）。
//!
//! Windows の WebView2 は Windows Ink のペンを PointerEvent の筆圧として渡す作りなので、
//! この部品は何もしない（画面は PointerEvent を使う）。実機での確認はまだ。

// macOS 以外では、画面へ送る部分を使わない（WebView2 は PointerEvent で筆圧を渡す）。
#![cfg_attr(not(target_os = "macos"), allow(dead_code))]

use serde::Serialize;

/// 画面へ送る事件の名前。
pub const EVENT: &str = "pen";

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Phase {
    Down,
    Move,
    Up,
}

/// ペン先か消しゴム側か（ペンでなければ Mouse）。
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Device {
    Mouse,
    Pen,
    Eraser,
}

/// 画面へ送る1点。位置は窓の中身の左上を原点にした CSS の px（＝point）。
#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct Sample {
    pub phase: Phase,
    pub device: Device,
    pub x: f64,
    pub y: f64,
    /// 0〜1。マウスのときは押している間 1、離したとき 0。
    pub pressure: f64,
    /// 傾き（-1〜1）。ペンでなければ 0。
    pub tilt_x: f64,
    pub tilt_y: f64,
    /// 事件の時刻（秒、OS の起動からの時間）。
    pub time: f64,
}

/// AppKit の窓の座標（左下が原点）を、中身の左上が原点の座標にする。
pub fn flip_to_top_left(x: f64, y: f64, content_height: f64) -> (f64, f64) {
    (x, content_height - y)
}

/// 筆圧を 0〜1 に収める（ドライバーによっては範囲の外や NaN が来る）。
pub fn clamp_pressure(p: f64) -> f64 {
    if p.is_nan() {
        0.0
    } else {
        p.clamp(0.0, 1.0)
    }
}

/// 押していない間のペンの筆圧は 0 で届くので、押した直後と線の途中では、
/// 0 を「とても弱い」として扱い、点が消えないようにする。離したときは 0 のまま。
pub fn stroke_pressure(phase: Phase, device: Device, raw: f64) -> f64 {
    match (device, phase) {
        (Device::Mouse, Phase::Up) => 0.0,
        (Device::Mouse, _) => 1.0,
        (_, Phase::Up) => 0.0,
        (_, _) => clamp_pressure(raw).max(MIN_PRESSURE),
    }
}

/// 押している間の筆圧の下限。
pub const MIN_PRESSURE: f64 = 0.001;

#[cfg(target_os = "macos")]
pub use macos::{install, observe};

#[cfg(not(target_os = "macos"))]
pub fn install(_app: &tauri::AppHandle) {}

#[cfg(target_os = "macos")]
mod macos {
    use super::{flip_to_top_left, stroke_pressure, Device, Phase, Sample, EVENT};
    use objc2::MainThreadMarker;
    use objc2_app_kit::{
        NSEvent, NSEventMask, NSEventSubtype, NSEventType, NSPointingDeviceType, NSWindow,
    };
    use std::ptr::NonNull;
    use std::sync::atomic::{AtomicU8, Ordering};
    use std::sync::OnceLock;
    use tauri::Emitter;

    static APP: OnceLock<tauri::AppHandle> = OnceLock::new();

    /// 近づいたときに分かったペンの側（0＝不明、1＝ペン先、2＝消しゴム）。
    static TIP: AtomicU8 = AtomicU8::new(0);

    /// 主の窓の事件を見るモニターを入れる。主スレッドから呼ぶ。
    pub fn install(app: &tauri::AppHandle) {
        use block2::RcBlock;
        if APP.set(app.clone()).is_err() {
            return;
        }
        let mask = NSEventMask::LeftMouseDown
            | NSEventMask::LeftMouseDragged
            | NSEventMask::LeftMouseUp
            | NSEventMask::TabletProximity;
        let block = RcBlock::new(|event: NonNull<NSEvent>| -> *mut NSEvent {
            observe(unsafe { event.as_ref() }, None);
            event.as_ptr()
        });
        let monitor =
            unsafe { NSEvent::addLocalMonitorForEventsMatchingMask_handler(mask, &block) };
        // アプリの寿命のあいだ外さない。
        std::mem::forget(monitor);
    }

    /// 1つの事件を読み、主の窓のものなら画面へ送る。自己確認は、作った事件が窓に
    /// 引き当てられないので、送り先の窓を `window` で渡す。
    pub fn observe(e: &NSEvent, window: Option<&NSWindow>) {
        let kind = e.r#type();
        if kind == NSEventType::TabletProximity {
            let tip = if !e.isEnteringProximity() {
                0
            } else if e.pointingDeviceType() == NSPointingDeviceType::Eraser {
                2
            } else {
                1
            };
            TIP.store(tip, Ordering::Relaxed);
            return;
        }
        let phase = if kind == NSEventType::LeftMouseDown {
            Phase::Down
        } else if kind == NSEventType::LeftMouseDragged {
            Phase::Move
        } else if kind == NSEventType::LeftMouseUp {
            Phase::Up
        } else {
            return;
        };
        let Some(app) = APP.get() else { return };
        let Some(mtm) = MainThreadMarker::new() else {
            return;
        };
        let Some(sample) = sample_for(e, phase, window, mtm) else {
            return;
        };
        let _ = app.emit_to("main", EVENT, sample);
    }

    fn sample_for(
        e: &NSEvent,
        phase: Phase,
        hint: Option<&NSWindow>,
        mtm: MainThreadMarker,
    ) -> Option<Sample> {
        let window = match hint {
            Some(w) => objc2::rc::Retained::from(w),
            None => e.window(mtm)?,
        };
        if !is_main_window(&window) {
            return None;
        }
        let content = window.contentView()?;
        let height = content.frame().size.height;
        let p = e.locationInWindow();
        let (x, y) = flip_to_top_left(p.x, p.y, height);
        let tablet = e.subtype() == NSEventSubtype::TabletPoint;
        let device = match (tablet, TIP.load(Ordering::Relaxed)) {
            (false, _) => Device::Mouse,
            (true, 2) => Device::Eraser,
            (true, _) => Device::Pen,
        };
        let (tilt_x, tilt_y) = if tablet {
            let t = e.tilt();
            (t.x, t.y)
        } else {
            (0.0, 0.0)
        };
        Some(Sample {
            phase,
            device,
            x,
            y,
            pressure: stroke_pressure(phase, device, e.pressure() as f64),
            tilt_x,
            tilt_y,
            time: e.timestamp(),
        })
    }

    fn is_main_window(window: &NSWindow) -> bool {
        use tauri::Manager;
        let Some(app) = APP.get() else { return false };
        let Some(main) = app.get_webview_window("main") else {
            return false;
        };
        let Ok(ptr) = main.ns_window() else {
            return false;
        };
        std::ptr::eq(ptr as *const NSWindow, window as *const _)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn flips_y_against_content_height() {
        assert_eq!(flip_to_top_left(10.0, 700.0, 800.0), (10.0, 100.0));
    }

    #[test]
    fn mouse_presses_at_full_pressure() {
        assert_eq!(stroke_pressure(Phase::Down, Device::Mouse, 0.0), 1.0);
        assert_eq!(stroke_pressure(Phase::Move, Device::Mouse, 0.3), 1.0);
        assert_eq!(stroke_pressure(Phase::Up, Device::Mouse, 1.0), 0.0);
    }

    #[test]
    fn pen_keeps_pressure_and_never_vanishes_mid_stroke() {
        assert_eq!(stroke_pressure(Phase::Move, Device::Pen, 0.42), 0.42);
        assert_eq!(stroke_pressure(Phase::Down, Device::Pen, 0.0), MIN_PRESSURE);
        assert_eq!(stroke_pressure(Phase::Move, Device::Eraser, 1.7), 1.0);
        assert_eq!(
            stroke_pressure(Phase::Move, Device::Pen, f64::NAN),
            MIN_PRESSURE
        );
        assert_eq!(stroke_pressure(Phase::Up, Device::Pen, 0.5), 0.0);
    }

    #[test]
    fn sample_serializes_for_javascript() {
        let s = Sample {
            phase: Phase::Move,
            device: Device::Pen,
            x: 1.0,
            y: 2.0,
            pressure: 0.5,
            tilt_x: 0.0,
            tilt_y: 0.0,
            time: 3.0,
        };
        let v = serde_json::to_value(s).unwrap();
        assert_eq!(v["phase"], "move");
        assert_eq!(v["device"], "pen");
        assert_eq!(v["pressure"], 0.5);
    }
}
