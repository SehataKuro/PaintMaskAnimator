//! 読み込み（imports.rs）の試験。画面の窓は通さず、文書への書き方を確かめる。

use super::*;
use pma_core::project::png::encode_png;

fn temp_png(name: &str, width: usize, height: usize, rgba: &[u8]) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("pma-imports-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(name);
    std::fs::write(&path, encode_png(width, height, rgba, None).unwrap()).unwrap();
    path
}

fn decoded(path: &Path, conversion: &Conversion) -> PremulImage {
    let image = read_premul(&path.display().to_string()).unwrap();
    convert_with(&image, false, conversion).unwrap()
}

#[test]
fn images_become_a_sequence_layer_and_expand_the_canvas() {
    let mut s = Session::empty(4, 3);
    // 白は透明になり、5×2 の絵でキャンバスの幅が広がる。
    let rgba: Vec<u8> = [[255, 255, 255, 255], [10, 20, 30, 255]]
        .iter()
        .cycle()
        .take(10)
        .flatten()
        .copied()
        .collect();
    let a = temp_png("a1.png", 5, 2, &rgba);
    let b = temp_png("a2.png", 5, 2, &rgba);
    let images: Vec<PremulImage> = [&a, &b]
        .iter()
        .map(|p| decoded(p, &Conversion::Plain))
        .collect();
    assert_eq!(images[0].pixels()[3], 0);
    let sizes = images.iter().map(|i| (i.width(), i.height())).collect();
    let applied = apply_tracks(
        &mut s,
        |doc| {
            let (w, h) = doc.size();
            let placed = images
                .iter()
                .map(|i| {
                    import_image::to_document_image(&import_image::place_centered(w, h, i).unwrap())
                        .unwrap()
                })
                .collect();
            Ok((vec![ImportedTrack::new("a1", placed)], Mode::Sequence))
        },
        sizes,
    )
    .unwrap();
    assert_eq!(applied.layers, vec![1]);
    assert_eq!(
        applied.expanded.as_deref(),
        Some("画像に合わせてキャンバスを 5 × 3px に拡張しました。")
    );
    let doc = &s.project.as_ref().unwrap().document;
    assert_eq!(doc.size(), (5, 3));
    assert_eq!(doc.frame_order().len(), 2);
    assert_eq!(s.timeline.mode, Mode::Sequence);
    // Undo 1回で読み込む前（4×3・1列）に戻る。
    assert!(s.undo().unwrap());
    let doc = &s.project.as_ref().unwrap().document;
    assert_eq!(doc.size(), (4, 3));
    assert_eq!(doc.track_order().len(), 1);
}

#[test]
fn layout_paper_goes_to_the_bottom_without_history() {
    let mut s = Session::empty(3, 2);
    let paper = temp_png("paper.png", 3, 2, &[200u8; 24]);
    insert_layout_paper(&mut s, &paper).unwrap();
    let p = s.project.as_ref().unwrap();
    assert_eq!(p.document.track_order().len(), 2);
    let bottom = p.document.track(p.document.track_order()[0]).unwrap();
    assert!(bottom.settings.is_draft);
    assert_eq!(bottom.settings.name, "レイアウト用紙");
    assert_eq!(current_index(&p.document).1, 1);
    assert!(!s.history_state().0);
    let missing = std::env::temp_dir().join("pma-imports-none.png");
    assert_eq!(
        insert_layout_paper(&mut s, &missing).unwrap_err(),
        "レイアウト用紙の画像を読み込めませんでした。"
    );
}

#[test]
fn reduce_form_uses_the_detected_background() {
    let form = ReduceForm {
        mode: "surface".into(),
        color_count: 8,
        flatten_paper: false,
        blur: 0.0,
        despeckle: 3,
        tone_curve_points: vec![(0.0, 0.0), (1.0, 1.0)],
        palette: Some(vec![[0, 0, 0]]),
    };
    let s = form.settings(Some([250, 250, 250]), vec![[0, 0, 0]]);
    assert!(s.opaque_background);
    assert_eq!(s.background, Some([250, 250, 250]));
    let paper = ReduceForm {
        flatten_paper: true,
        ..form.clone()
    };
    assert_eq!(
        paper.settings(None, vec![]).background,
        Some([255, 255, 255])
    );
    assert_eq!(form.background(None), (false, None));
}
