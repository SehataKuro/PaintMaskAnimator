//! 日本語入力の自己確認（macOS）。IME が使う NSTextInputClient の窓口
//! （setMarkedText＝変換中、insertText＝確定）を、文字を受け取るビューに直接呼ぶ。
//! OS の IME から先（winit／WebKit から画面の部品まで）の経路を通す。キーボードは使わない。

#[cfg(target_os = "macos")]
pub fn marked(view: &objc2::runtime::AnyObject, text: &str) {
    use objc2::msg_send;
    use objc2_foundation::{NSRange, NSString};
    let s = NSString::from_str(text);
    let len = text.encode_utf16().count();
    let none = NSRange::new(usize::MAX >> 1, 0); // NSNotFound
    unsafe {
        let _: () = msg_send![view, setMarkedText: &*s, selectedRange: NSRange::new(len, 0), replacementRange: none];
    }
}

#[cfg(target_os = "macos")]
pub fn insert(view: &objc2::runtime::AnyObject, text: &str) {
    use objc2::msg_send;
    use objc2_foundation::{NSRange, NSString};
    let s = NSString::from_str(text);
    let none = NSRange::new(usize::MAX >> 1, 0);
    unsafe {
        let _: () = msg_send![view, insertText: &*s, replacementRange: none];
    }
}
