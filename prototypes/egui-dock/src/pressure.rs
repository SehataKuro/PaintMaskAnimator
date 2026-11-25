//! ペンの筆圧。winit 0.30 は macOS・Windows のタブレットの筆圧を渡さないので、
//! macOS では NSEvent のローカルモニターで pressure を直接読む（試作）。
//! Windows では WM_POINTER（または Wintab）を別に読む必要がある（未実装）。

use std::sync::atomic::{AtomicU32, AtomicBool, Ordering};

static PRESSURE_BITS: AtomicU32 = AtomicU32::new(0);
static SEEN_TABLET: AtomicBool = AtomicBool::new(false);

/// 最後に見たペンの筆圧（ペンの事件をまだ見ていなければ None）。
pub fn current() -> Option<f32> {
    SEEN_TABLET
        .load(Ordering::Relaxed)
        .then(|| f32::from_bits(PRESSURE_BITS.load(Ordering::Relaxed)))
}

pub fn status() -> &'static str {
    if cfg!(target_os = "macos") {
        if SEEN_TABLET.load(Ordering::Relaxed) {
            "筆圧：NSEvent から取得中"
        } else {
            "筆圧：NSEvent を監視中（ペンの事件はまだ無い）"
        }
    } else {
        "筆圧：この OS では未実装（winit は渡さない）"
    }
}

#[cfg(target_os = "macos")]
pub fn install() {
    use block2::RcBlock;
    use objc2_app_kit::{NSEvent, NSEventMask, NSEventSubtype, NSEventType};
    use std::ptr::NonNull;

    let mask = NSEventMask::LeftMouseDown
        | NSEventMask::LeftMouseDragged
        | NSEventMask::LeftMouseUp
        | NSEventMask::TabletPoint;
    let block = RcBlock::new(|event: NonNull<NSEvent>| -> *mut NSEvent {
        let e = unsafe { event.as_ref() };
        let kind = e.r#type();
        if std::env::var_os("PMA_DEBUG").is_some() {
            eprintln!("監視：type={:?} subtype={:?} pressure={} window#={}", kind, e.subtype(), e.pressure(), e.windowNumber());
        }
        let is_tablet = kind == NSEventType::TabletPoint
            || ((kind == NSEventType::LeftMouseDragged || kind == NSEventType::LeftMouseDown)
                && e.subtype() == NSEventSubtype::TabletPoint);
        if is_tablet {
            PRESSURE_BITS.store(e.pressure().to_bits(), Ordering::Relaxed);
            SEEN_TABLET.store(true, Ordering::Relaxed);
        }
        event.as_ptr()
    });
    let monitor = unsafe { NSEvent::addLocalMonitorForEventsMatchingMask_handler(mask, &block) };
    // 試作なので、アプリの寿命のあいだ外さない。
    std::mem::forget(monitor);
}

#[cfg(not(target_os = "macos"))]
pub fn install() {}
