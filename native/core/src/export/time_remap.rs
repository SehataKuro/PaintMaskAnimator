//! タイムラインをタイムリマップのキーフレームとして書き出す（Issue #23、
//! `TimeRemapController.after_effects_text`）。

use super::py::format_g;

/// After Effects の Time Remap キーフレームデータ。書けるセルがなければ None。
///
/// 絵番号 N は (N - 1) / fps 秒。空セルは不透明度 0 のキーで表す。fps が 0 以下なら 24。
pub fn after_effects_text(states: &[Option<i64>], fps: f64) -> Option<String> {
    let fps = if fps > 0.0 { fps } else { 24.0 };
    let mut time_lines = Vec::new();
    let mut opacity_lines = Vec::new();
    let mut visible: Option<bool> = None;
    for (index, state) in states.iter().enumerate() {
        let frame = index + 1;
        let Some(state) = state else {
            if visible != Some(false) {
                opacity_lines.push(format!("\t{frame}\t0"));
                visible = Some(false);
            }
            continue;
        };
        if visible != Some(true) {
            opacity_lines.push(format!("\t{frame}\t100"));
            visible = Some(true);
        }
        time_lines.push(format!("\t{frame}\t{:.6}", (*state - 1) as f64 / fps));
    }
    if time_lines.is_empty() {
        return None;
    }
    let mut lines = vec![
        "Adobe After Effects 8.0 Keyframe Data".to_string(),
        String::new(),
        format!("\tUnits Per Second\t{}", format_g(fps)),
        "\tSource Width\t0".into(),
        "\tSource Height\t0".into(),
        "\tSource Pixel Aspect Ratio\t1".into(),
        "\tComp Pixel Aspect Ratio\t1".into(),
        String::new(),
        "Time Remap".into(),
        "\tFrame\tseconds".into(),
    ];
    lines.extend(time_lines);
    lines.extend([
        String::new(),
        "Transform\tOpacity".into(),
        "\tFrame\tpercent".into(),
    ]);
    lines.extend(opacity_lines);
    lines.extend([String::new(), "End of Keyframe Data".into(), String::new()]);
    Some(lines.join("\n"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys_and_opacity() {
        let text = after_effects_text(&[Some(1), Some(1), None, Some(3)], 24.0).unwrap();
        assert!(text.contains("\tUnits Per Second\t24\n"));
        assert!(text.contains(
            "Time Remap\n\tFrame\tseconds\n\t1\t0.000000\n\t2\t0.000000\n\t4\t0.083333\n"
        ));
        assert!(
            text.contains("\tFrame\tpercent\n\t1\t100\n\t3\t0\n\t4\t100\n\nEnd of Keyframe Data\n")
        );
        assert_eq!(after_effects_text(&[None], 24.0), None);
    }
}
