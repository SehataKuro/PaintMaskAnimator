//! Qt 版（0.8.0）で保存したワークスペース（`dock_state`・`window_geometry`）を、この画面の
//! パネルの並びにする（Qt 版の `main_window_workspace.apply`）。
//!
//! 読むのは pma-core の `qt_dock_state`。ここは Qt のパネルの名前（objectName）をこの画面の
//! パネルの id（layout.js の PANELS）へ読み替え、知らないパネルを除いて画面（shell.js）へ渡す。
//! 画面は dockview の addPanel で同じ並びを組み、大きさを割合で配る。

use pma_core::qt_dock_state::{self, DockNode};
use serde::Serialize;

/// Qt の objectName → この画面のパネルの id。
const NAMES: [(&str, &str); 10] = [
    ("canvasDock", "canvas"),
    ("toolSelectorDock", "tools"),
    ("toolsDock", "tool-properties"),
    ("actionPanelDock", "actions"),
    ("colorWheelDock", "color-wheel"),
    ("colorSliderDock", "color-sliders"),
    ("paletteDock", "used-colors"),
    ("historyDock", "history"),
    ("subviewDock", "subview"),
    ("timelineDock", "timeline"),
];

fn panel_id(name: &str) -> Option<&'static str> {
    NAMES.iter().find(|(q, _)| *q == name).map(|(_, id)| *id)
}

/// 画面へ渡す配置の木。
#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Node {
    Area {
        panels: Vec<String>,
        current: Option<String>,
    },
    Split {
        horizontal: bool,
        children: Vec<Node>,
        /// 子の大きさの割合（合計 1）。
        sizes: Vec<f64>,
    },
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct Floating {
    pub root: Node,
    /// [x, y, 幅, 高さ]（Qt の画面の座標）。
    pub geometry: Option<[i32; 4]>,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct QtWorkspace {
    pub root: Node,
    pub floating: Vec<Floating>,
    /// 窓の [x, y, 幅, 高さ]（`window_geometry`）。
    pub window: Option<[i32; 4]>,
}

fn convert(node: &DockNode) -> Option<Node> {
    match node {
        DockNode::Area { widgets, current } => {
            let panels: Vec<String> = widgets
                .iter()
                .filter_map(|w| panel_id(w))
                .map(str::to_string)
                .collect();
            if panels.is_empty() {
                return None;
            }
            let current = current
                .as_deref()
                .and_then(panel_id)
                .filter(|c| panels.iter().any(|p| p == c))
                .map(str::to_string);
            Some(Node::Area { panels, current })
        }
        DockNode::Split {
            horizontal,
            children,
            sizes,
        } => {
            let mut kept = Vec::new();
            let mut kept_sizes = Vec::new();
            for (child, size) in children.iter().zip(sizes) {
                if let Some(c) = convert(child) {
                    kept.push(c);
                    kept_sizes.push(size.max(0.0));
                }
            }
            match kept.len() {
                0 => None,
                1 => kept.pop(),
                _ => {
                    let total: f64 = kept_sizes.iter().sum();
                    let n = kept.len() as f64;
                    let sizes = kept_sizes
                        .iter()
                        .map(|s| if total > 0.0 { s / total } else { 1.0 / n })
                        .collect();
                    Some(Node::Split {
                        horizontal: *horizontal,
                        children: kept,
                        sizes,
                    })
                }
            }
        }
    }
}

fn rect((x, y, w, h): (i32, i32, i32, i32)) -> [i32; 4] {
    [x, y, w, h]
}

/// Qt 版のワークスペースを読む。戻せなければ None（Qt の apply が False のとき）。
pub fn from_qt(dock_state: &str, window_geometry: Option<&str>) -> Option<QtWorkspace> {
    let layout = qt_dock_state::parse_dock_state(dock_state).ok()?;
    let root = layout.root.as_ref().and_then(convert)?;
    let floating = layout
        .floating
        .iter()
        .filter_map(|f| {
            Some(Floating {
                root: convert(&f.root)?,
                geometry: f.geometry.map(rect),
            })
        })
        .collect();
    let window = window_geometry
        .and_then(|g| qt_dock_state::decode_base64(g).ok())
        .and_then(|bytes| qt_dock_state::widget_geometry(&bytes))
        .map(rect);
    Some(QtWorkspace {
        root,
        floating,
        window,
    })
}

#[tauri::command]
pub fn workspace_from_qt(
    dock_state: String,
    window_geometry: Option<String>,
) -> Option<QtWorkspace> {
    from_qt(&dock_state, window_geometry.as_deref())
}

/// 窓の位置と大きさを戻す（Qt の restoreGeometry）。今の画面の作業領域に収める
/// （`clamped_geometry` と同じ考え）。
#[tauri::command]
pub fn workspace_set_window(app: tauri::AppHandle, geometry: [i32; 4]) -> Result<(), String> {
    use tauri::Manager;
    let window = app.get_webview_window("main").ok_or("窓が無い")?;
    let [mut x, mut y, mut w, mut h] = geometry.map(f64::from);
    if let Ok(Some(monitor)) = window.current_monitor() {
        let scale = monitor.scale_factor();
        let area = monitor.work_area();
        let (ax, ay) = (
            area.position.x as f64 / scale,
            area.position.y as f64 / scale,
        );
        let (aw, ah) = (
            area.size.width as f64 / scale,
            area.size.height as f64 / scale,
        );
        w = w.min(aw);
        h = h.min(ah);
        x = x.clamp(ax, ax + aw - w);
        y = y.clamp(ay, ay + ah - h);
    }
    window
        .set_size(tauri::LogicalSize::new(w.max(200.0), h.max(150.0)))
        .map_err(|e| e.to_string())?;
    window
        .set_position(tauri::LogicalPosition::new(x, y))
        .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = "AAAEbnjalVRRT4MwEH73V1x4n7QwJybAsszMN43Z1OcOLrOxtEvboTP+eMu2EGbKiAkBet/X767fHaTTr0pAjdpwJbOAXpMAUBaq5HKTBS+rxSgJpnn6bGdlzWSB5b0qPhy23BuLFby2GwN4MajbtZOZK2kZly6SBZFborSaiTdebtBmQcFkzUyjFuRpS4WFUMwechMXX24Ft9aFnzR32x3SaI8a7Z20jWwf56flxI4z08hgxdbmUOh8pzU2kFVKLFFgYZU+VXIsDx5ZhR4c5kIZLJviwjwNG9le8c4B3anY5gD/SdDlDEhHHel3blxFe1/JWybQueGRPKN1FbyZl/wbTU5jAsnNBCIygTQ8xtzz5Hi/r7xC4drp9bSLXUg9ScZAKfGmDdt58Y9O4/MDqgqt3ueEQnkHJYFiDYQAiQ/3wSuCpGVSwPgM8rw7Pu3wab9y7Oz8r1oatucZ/Cho/8Cb3brm+OnrSxe60JbxbTLYkrDnb5Ff/QKL61qZ";

    #[test]
    fn maps_qt_names_to_panels() {
        let w = from_qt(SAMPLE, None).unwrap();
        let Node::Split {
            horizontal: false,
            children,
            sizes,
        } = &w.root
        else {
            panic!("{:?}", w.root);
        };
        assert!((sizes[0] - 684.0 / 794.0).abs() < 1e-12);
        assert_eq!(
            children[1],
            Node::Area {
                panels: vec!["timeline".into()],
                current: Some("timeline".into())
            }
        );
        let Node::Split { children: top, .. } = &children[0] else {
            panic!()
        };
        assert_eq!(
            top[2],
            Node::Area {
                panels: vec!["used-colors".into(), "history".into()],
                current: Some("history".into())
            }
        );
        assert_eq!(w.floating.len(), 1);
        assert_eq!(w.floating[0].geometry, Some([0, 0, 644, 484]));
        assert!(from_qt("", None).is_none());
    }
}
