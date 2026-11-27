//! 筆圧の経路の自己確認（macOS）。`PMA_PEN_SELFTEST=1` で動かす。
//!
//! 本物のペンが無くても確かめられるように、タブレットのペンと同じ形の事件
//! （CGEvent の mouseEventSubtype＝tabletPoint、筆圧つき）を作り、主の窓に渡す。
//! 実機の Wacom などのペンも、AppKit には同じ subtype と pressure の NSEvent として届く。
//! ほかのアプリや画面の操作は奪わない（自分の窓にだけ渡す）。
//!
//! ペンの形の事件は窓に直接渡すのでローカルモニターを通らない。そこでペンの線では
//! モニターの代わりに `pen::observe` を直接呼ぶ。モニターが事件を拾うことは、続けて
//! マウスの線（窓の番号つきの事件をアプリの事件の列に入れる）で確かめる。実機のペンの
//! 事件をモニターが拾うかは、実機で確かめる。

// macOS 以外では、画面へ送る部分を使わない（WebView2 は PointerEvent で筆圧を渡す）。
#![cfg_attr(not(target_os = "macos"), allow(dead_code))]

/// 線の区間の数。筆圧は 0.1 → 1.0 → 0.1。
pub const SEGMENTS: usize = 60;

/// n 手目（1 から）の事件の形：(種類, 窓の中の割合の位置, 筆圧)。範囲の外は None。
pub fn plan(n: usize) -> Option<(Kind, f64, f64, f64)> {
    const X0: f64 = 0.3;
    const Y0: f64 = 0.5;
    match n {
        1 => Some((Kind::Down, X0, Y0, 0.1)),
        n if n <= SEGMENTS + 1 => {
            let t = (n - 1) as f64 / SEGMENTS as f64;
            Some((
                Kind::Drag,
                X0 + 0.4 * t,
                Y0 + 0.05 * (t * 6.0).sin(),
                0.1 + 0.9 * (std::f64::consts::PI * t).sin(),
            ))
        }
        n if n == SEGMENTS + 2 => Some((Kind::Up, X0 + 0.4, Y0, 0.0)),
        _ => None,
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Down,
    Drag,
    Up,
}

/// n 手目の事件を主の窓へ渡す。主スレッドから呼ぶ。渡せたら Some(true)、終わりなら None。
#[cfg(target_os = "macos")]
pub fn step(app: &tauri::AppHandle, n: usize) -> Option<bool> {
    let (kind, fx, fy, pressure) = plan(n)?;
    Some(post(app, kind, fx, fy, pressure))
}

#[cfg(not(target_os = "macos"))]
pub fn step(_app: &tauri::AppHandle, n: usize) -> Option<bool> {
    plan(n).map(|_| false)
}

/// マウスの線の m 手目（1 から）：(種類, 窓の中の割合の位置)。押す・3回動かす・離す。
pub fn mouse_plan(m: usize) -> Option<(Kind, f64, f64)> {
    match m {
        1 => Some((Kind::Down, 0.3, 0.4)),
        2..=4 => Some((Kind::Drag, 0.3 + 0.1 * (m - 1) as f64, 0.4)),
        5 => Some((Kind::Up, 0.6, 0.4)),
        _ => None,
    }
}

/// マウスの線の m 手目を、アプリの事件の列に入れる（ローカルモニターを通る）。
#[cfg(target_os = "macos")]
pub fn mouse_step(app: &tauri::AppHandle, m: usize) -> Option<bool> {
    use objc2_app_kit::{NSApplication, NSEvent, NSEventModifierFlags, NSEventType, NSWindow};
    use objc2_foundation::NSPoint;
    use tauri::Manager;

    let (kind, fx, fy) = mouse_plan(m)?;
    let posted = (|| {
        let mtm = objc2::MainThreadMarker::new()?;
        let main = app.get_webview_window("main")?;
        let ptr = main.ns_window().ok()?;
        let window: &NSWindow = unsafe { &*(ptr as *const NSWindow) };
        let content = window.contentView()?.frame();
        let location = NSPoint {
            x: content.origin.x + content.size.width * fx,
            y: content.origin.y + content.size.height * (1.0 - fy),
        };
        let kind = match kind {
            Kind::Down => NSEventType::LeftMouseDown,
            Kind::Drag => NSEventType::LeftMouseDragged,
            Kind::Up => NSEventType::LeftMouseUp,
        };
        let event = NSEvent::mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure(
            kind,
            location,
            NSEventModifierFlags::empty(),
            0.0,
            window.windowNumber(),
            None,
            0,
            1,
            1.0,
        )?;
        NSApplication::sharedApplication(mtm).postEvent_atStart(&event, false);
        Some(())
    })();
    Some(posted.is_some())
}

#[cfg(not(target_os = "macos"))]
pub fn mouse_step(_app: &tauri::AppHandle, m: usize) -> Option<bool> {
    mouse_plan(m).map(|_| false)
}

#[cfg(target_os = "macos")]
fn post(app: &tauri::AppHandle, kind: Kind, fx: f64, fy: f64, pressure: f64) -> bool {
    use objc2_app_kit::{NSEvent, NSWindow};
    use objc2_core_foundation::CGPoint;
    use objc2_core_graphics::{CGEvent, CGEventField, CGEventType, CGMouseButton};
    use tauri::Manager;

    let Some(main) = app.get_webview_window("main") else {
        return false;
    };
    let Ok(ptr) = main.ns_window() else {
        return false;
    };
    let window: &NSWindow = unsafe { &*(ptr as *const NSWindow) };
    let Some(content_view) = window.contentView() else {
        return false;
    };
    let content = content_view.frame();
    let kind = match kind {
        Kind::Down => CGEventType::LeftMouseDown,
        Kind::Drag => CGEventType::LeftMouseDragged,
        Kind::Up => CGEventType::LeftMouseUp,
    };
    // 作った事件は窓に引き当てられない（windowNumber が 0）ので、AppKit は位置を窓の座標
    // （左下が原点）として読む。CGEvent の位置は主の画面の左上が原点で、NSEvent にするときに
    // 主の画面の高さで裏返されるので、その高さで裏返して入れる。
    let Some(mtm) = objc2::MainThreadMarker::new() else {
        return false;
    };
    let Some(screen) = objc2_app_kit::NSScreen::screens(mtm).firstObject() else {
        return false;
    };
    let screen_h = screen.frame().size.height;
    let local_x = content.origin.x + content.size.width * fx;
    let local_y = content.origin.y + content.size.height * (1.0 - fy);
    let Some(cg) = CGEvent::new_mouse_event(
        None,
        kind,
        CGPoint {
            x: local_x,
            y: screen_h - local_y,
        },
        CGMouseButton::Left,
    ) else {
        return false;
    };
    CGEvent::set_integer_value_field(Some(&cg), CGEventField::MouseEventSubtype, 1); // tabletPoint
    CGEvent::set_double_value_field(Some(&cg), CGEventField::MouseEventPressure, pressure);
    CGEvent::set_double_value_field(Some(&cg), CGEventField::TabletEventPointPressure, pressure);
    let Some(event) = NSEvent::eventWithCGEvent(&cg) else {
        return false;
    };
    crate::pen::observe(&event, Some(window));
    window.sendEvent(&event);
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plan_is_one_stroke_with_rising_and_falling_pressure() {
        assert_eq!(plan(1).unwrap().0, Kind::Down);
        assert_eq!(plan(SEGMENTS + 2).unwrap().0, Kind::Up);
        assert!(plan(SEGMENTS + 3).is_none());
        let middle = plan(SEGMENTS / 2 + 1).unwrap().3;
        assert!((middle - 1.0).abs() < 1e-9);
        assert!((plan(1).unwrap().3 - 0.1).abs() < 1e-9);
        assert!((plan(SEGMENTS + 1).unwrap().3 - 0.1).abs() < 1e-9);
    }
}
