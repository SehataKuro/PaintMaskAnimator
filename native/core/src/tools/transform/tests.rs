//! 変形ツールの単体テスト。Python の画面との画素の突き合わせは
//! tests/test_core_transform_compare.py。

use super::*;
use crate::selection::Selection;

fn layer(width: usize, height: usize) -> RgbaImage {
    let mut pixels = vec![0u8; width * height * 4];
    for y in 4..12 {
        for x in 6..14 {
            let at = (y * width + x) * 4;
            pixels[at..at + 4].copy_from_slice(&[(x * 10) as u8, (y * 9) as u8, 40, 255]);
        }
    }
    // 1 画素の縦線。
    for y in 3..13 {
        let at = (y * width + 9) * 4;
        pixels[at..at + 4].copy_from_slice(&[0, 0, 0, 255]);
    }
    RgbaImage::from_rgba(width, height, &pixels).unwrap()
}

fn selection() -> Selection {
    Selection::polygon(vec![(5.0, 2.0), (15.0, 2.0), (15.0, 14.0), (5.0, 14.0)]).unwrap()
}

fn begin(mode: Mode, quality: bool) -> Begun {
    let options = TransformOptions {
        quality,
        line_colors: vec![[0, 0, 0]],
        ..TransformOptions::default()
    };
    TransformSession::begin(&layer(40, 30), Some(&selection()), mode, options)
        .unwrap()
        .unwrap()
}

fn shifted(image: &RgbaImage, dx: usize, dy: usize) -> Vec<u8> {
    let (w, h) = (image.width(), image.height());
    let mut out = vec![0u8; w * h * 4];
    for y in 0..h - dy {
        for x in 0..w - dx {
            let from = (y * w + x) * 4;
            let to = ((y + dy) * w + x + dx) * 4;
            out[to..to + 4].copy_from_slice(&image.rgba()[from..from + 4]);
        }
    }
    out
}

#[test]
fn begin_lifts_selection_and_cancel_restores() {
    let begun = begin(Mode::Free, false);
    assert_eq!(
        begun.edit.rect,
        Rect {
            x: 3,
            y: 0,
            width: 14,
            height: 16
        }
    );
    assert!(begun.layer.rgba().iter().all(|&v| v == 0));
    assert_eq!(begun.session.points().len(), 4);
    let original = begun.session.cancel();
    assert_eq!(original.rgba(), layer(40, 30).rgba());
}

#[test]
fn move_is_whole_pixels_and_keeps_line_width() {
    for quality in [false, true] {
        let mut session = begin(Mode::Free, quality).session;
        assert_eq!(session.begin_drag((10.0, 8.0), 4.0), Some(DragKind::Move));
        // 0.5 画素ずれる所へドラッグしても、1 画素単位で動く（#116）。
        session.update_drag((13.5, 10.4));
        assert!(session.end_drag());
        assert_eq!(session.points()[0], (7.0, 2.0));
        let committed = session.commit().unwrap();
        let expected = shifted(&layer(40, 30), 4, 2);
        assert_eq!(committed.image.rgba(), &expected[..], "quality={quality}");
    }
}

#[test]
fn history_undo_redo_and_unchanged_drag() {
    let mut session = begin(Mode::Free, false).session;
    let before = session.snapshot();
    session.rotate(30.0);
    assert_eq!(session.history_len(), 1);
    let after = session.snapshot();
    assert!(session.undo());
    assert_eq!(session.snapshot(), before);
    assert!(session.redo());
    assert_eq!(session.snapshot(), after);
    session.begin_drag((10.0, 8.0), 4.0);
    session.update_drag((10.2, 8.1));
    assert!(!session.end_drag());
    assert_eq!(session.history_len(), 1);
}

#[test]
fn scale_handle_keeps_opposite_corner() {
    let mut session = begin(Mode::Scale, false).session;
    let p = session.points().to_vec();
    assert_eq!(session.begin_drag(p[2], 1.0), Some(DragKind::Handle(2)));
    session.update_drag((p[2].0 + 14.0, p[2].1 + 16.0));
    session.end_drag();
    assert_eq!(session.points()[0], p[0]);
    let preview = session.preview().unwrap();
    assert!(preview.window.width > 20);
    let committed = session.commit().unwrap();
    assert!(committed.undo.rect.width >= 28);
}

#[test]
fn mesh_grid_resamples_and_previews() {
    let mut session = begin(Mode::Mesh, false).session;
    assert_eq!(session.points().len(), 16);
    session.set_mesh_grid(3, 5);
    assert_eq!(session.mesh_size(), (3, 5));
    assert_eq!(session.points().len(), 15);
    let rendered = session.preview().unwrap();
    assert!(rendered.pixels.pixels().iter().any(|&v| v != 0));
    assert!(session.undo());
    assert_eq!(session.mesh_size(), (4, 4));
}

#[test]
fn flip_mirrors_pixels() {
    let mut session = begin(Mode::Free, false).session;
    session.flip(true);
    let image = session.commit().unwrap().image;
    // 元の (6, 4) は、切り出した矩形 x 3..17 の中で左右に写る。
    let x = 3 + 17 - 1 - 6;
    let at = (4 * 40 + x) * 4;
    assert_eq!(&image.rgba()[at..at + 4], &[60, 36, 40, 255]);
}
