//! 筆圧の経路の自己確認（macOS、Tauri 版）。本物のペンが無いので、タブレットのペンと同じ形の事件
//! （CGEvent の mouseEventSubtype＝tabletPoint、筆圧つき）を作り、自分のアプリの事件の列に入れる。
//! 実機の Wacom などのペンも、AppKit には同じ subtype と pressure の NSEvent として届く。
//! ほかのアプリや画面の操作は奪わない（自分の事件の列にだけ入れる）。

#[derive(Clone, Copy)]
pub enum Kind {
    Down,
    Drag,
    Up,
}

/// 自分の一番大きい窓の中の割合の位置（左上が 0,0）に、筆圧つきのペンの事件を1つ入れる。
#[cfg(target_os = "macos")]
pub fn post(kind: Kind, fx: f64, fy: f64, pressure: f64) -> bool {
    use objc2::MainThreadMarker;
    use objc2_app_kit::{NSApplication, NSEvent, NSScreen};
    use objc2_core_foundation::CGPoint;
    use objc2_core_graphics::{CGEvent, CGEventField, CGEventType, CGMouseButton};

    let Some(mtm) = MainThreadMarker::new() else { return false };
    let app = NSApplication::sharedApplication(mtm);
    let windows = app.windows();
    let Some(window) = windows
        .iter()
        .filter(|w| w.isVisible())
        .max_by(|a, b| {
            let (fa, fb) = (a.frame(), b.frame());
            (fa.size.width * fa.size.height).total_cmp(&(fb.size.width * fb.size.height))
        })
    else {
        return false;
    };
    let Some(screen) = NSScreen::screens(mtm).firstObject() else { return false };
    let screen_h = screen.frame().size.height;
    let content = window.contentRectForFrameRect(window.frame());
    // AppKit は左下が原点、CGEvent は左上が原点。
    let x = content.origin.x + content.size.width * fx;
    let y = screen_h - (content.origin.y + content.size.height * (1.0 - fy));
    let kind = match kind {
        Kind::Down => CGEventType::LeftMouseDown,
        Kind::Drag => CGEventType::LeftMouseDragged,
        Kind::Up => CGEventType::LeftMouseUp,
    };
    let Some(cg) = CGEvent::new_mouse_event(None, kind, CGPoint { x, y }, CGMouseButton::Left) else {
        return false;
    };
    let number = window.windowNumber() as i64;
    CGEvent::set_integer_value_field(Some(&cg), CGEventField::MouseEventSubtype, 1); // tabletPoint
    CGEvent::set_double_value_field(Some(&cg), CGEventField::MouseEventPressure, pressure);
    CGEvent::set_double_value_field(Some(&cg), CGEventField::TabletEventPointPressure, pressure);
    CGEvent::set_integer_value_field(Some(&cg), CGEventField::MouseEventWindowUnderMousePointer, number);
    CGEvent::set_integer_value_field(
        Some(&cg),
        CGEventField::MouseEventWindowUnderMousePointerThatCanHandleThisEvent,
        number,
    );
    // 自分で作った事件は窓に引き当てられない（windowNumber が 0）ので、窓の sendEvent に直接渡す。
    // そのとき AppKit は事件の位置を窓の座標として読むので、窓の中の位置になるように作り直す。
    let frame = window.frame();
    let local_x = content.origin.x - frame.origin.x + content.size.width * fx;
    let local_y = content.origin.y - frame.origin.y + content.size.height * (1.0 - fy);
    let _ = (x, y);
    CGEvent::set_location(Some(&cg), CGPoint { x: local_x, y: screen_h - local_y });
    let Some(event) = NSEvent::eventWithCGEvent(&cg) else { return false };
    window.sendEvent(&event);
    true
}

#[cfg(not(target_os = "macos"))]
pub fn post(_kind: Kind, _fx: f64, _fy: f64, _pressure: f64) -> bool {
    false
}

/// n 手目（1 から）に入れる事件。60 区間の線で、筆圧は 0.1 → 1.0 → 0.1。
pub fn step(n: usize, fx0: f64, fy0: f64) -> Option<bool> {
    const SEGMENTS: usize = 60;
    match n {
        1 => Some(post(Kind::Down, fx0, fy0, 0.1)),
        n if n <= SEGMENTS + 1 => {
            let t = (n - 1) as f64 / SEGMENTS as f64;
            Some(post(
                Kind::Drag,
                fx0 + 0.25 * t,
                fy0 + 0.03 * (t * 6.0).sin(),
                0.1 + 0.9 * (std::f64::consts::PI * t).sin(),
            ))
        }
        n if n == SEGMENTS + 2 => Some(post(Kind::Up, fx0 + 0.25, fy0, 0.0)),
        _ => None,
    }
}
