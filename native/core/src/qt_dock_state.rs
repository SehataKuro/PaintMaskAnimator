//! Qt 版（0.8.0）で保存したワークスペースのパネル配置（`dock_state`）を読む。
//!
//! Qt 版は Qt Advanced Docking System の `CDockManager::saveState()` を base64 にして
//! 設定の `workspaces` に入れていた（`main_window_workspace.capture`）。中身は `qCompress`
//! （4 バイトの大きさ＋zlib）した XML で、次の形をしている。
//!
//! ```xml
//! <QtAdvancedDockingSystem Version="1" Containers="2" CentralWidget="canvasDock">
//!   <Container Floating="0">
//!     <Splitter Orientation="-" Count="2">
//!       <Splitter Orientation="|" Count="3"> <Area Tabs="1" Current="toolSelectorDock">
//!         <Widget Name="toolSelectorDock" Closed="0"/></Area> … <Sizes>130 856 206 </Sizes>
//!       </Splitter>
//!       …
//!     </Splitter>
//!   </Container>
//!   <Container Floating="1"><Geometry>01 d9 d0 cb …</Geometry><Splitter …>…</Splitter></Container>
//! </QtAdvancedDockingSystem>
//! ```
//!
//! ここは並び（分割の向き・大きさ・タブの組・閉じたパネル）を [`DockLayout`] にするだけで、
//! パネルの名前の読み替えと画面の配置は呼ぶ側（アプリ）がする。

use std::io::Read;

use crate::Error;

fn invalid(reason: &str) -> Error {
    Error::Invalid(format!("Qt 版のパネル配置：{reason}"))
}

/// 配置の木。
#[derive(Clone, Debug, PartialEq)]
pub enum DockNode {
    /// タブの組（`Area`）。`widgets` は閉じていないパネルの名前（Qt の objectName）。
    Area {
        widgets: Vec<String>,
        current: Option<String>,
    },
    /// 分割（`Splitter`）。`horizontal` は左右に並べる（Orientation="|"）。`sizes` は子の
    /// 大きさ（画素、子と同じ数。読めなければ等分）。
    Split {
        horizontal: bool,
        children: Vec<DockNode>,
        sizes: Vec<f64>,
    },
}

/// 浮いた窓（`Container Floating="1"`）。
#[derive(Clone, Debug, PartialEq)]
pub struct FloatingDock {
    pub root: DockNode,
    /// 窓の枠（x, y, 幅, 高さ。`QWidget::saveGeometry` の frameGeometry）。
    pub geometry: Option<(i32, i32, i32, i32)>,
}

/// 読んだ配置。閉じたパネルだけの組・空の分割は除き、子が一つの分割はその子にし、
/// 同じ向きの分割の入れ子は一つにまとめる（dockview は分割の向きが入れ子ごとに交互）。
#[derive(Clone, Debug, PartialEq)]
pub struct DockLayout {
    pub root: Option<DockNode>,
    pub floating: Vec<FloatingDock>,
}

// ---- base64・qUncompress ----------------------------------------------------------

/// base64（空白は飛ばす）。
pub fn decode_base64(text: &str) -> Result<Vec<u8>, Error> {
    let mut out = Vec::with_capacity(text.len() * 3 / 4);
    let mut buffer = 0u32;
    let mut bits = 0u32;
    for byte in text.bytes() {
        let value = match byte {
            b'A'..=b'Z' => byte - b'A',
            b'a'..=b'z' => byte - b'a' + 26,
            b'0'..=b'9' => byte - b'0' + 52,
            b'+' | b'-' => 62,
            b'/' | b'_' => 63,
            b'=' => break,
            b' ' | b'\n' | b'\r' | b'\t' => continue,
            _ => return Err(invalid("base64 ではありません")),
        };
        buffer = (buffer << 6) | u32::from(value);
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buffer >> bits) as u8);
            buffer &= (1 << bits) - 1;
        }
    }
    Ok(out)
}

/// `qCompress` した中身を戻す。圧縮していない XML ならそのまま。
fn uncompress(data: &[u8]) -> Result<Vec<u8>, Error> {
    if data.first() == Some(&b'<') {
        return Ok(data.to_vec());
    }
    if data.len() < 4 {
        return Err(invalid("短すぎます"));
    }
    let expected = u32::from_be_bytes([data[0], data[1], data[2], data[3]]) as usize;
    let mut out = Vec::with_capacity(expected.min(1 << 20));
    flate2::read::ZlibDecoder::new(&data[4..])
        .take(1 << 24)
        .read_to_end(&mut out)
        .map_err(|_| invalid("zlib を戻せません"))?;
    Ok(out)
}

// ---- 小さな XML ---------------------------------------------------------------------

#[derive(Debug, Default)]
struct Element {
    name: String,
    attributes: Vec<(String, String)>,
    children: Vec<Element>,
    text: String,
}

impl Element {
    fn attribute(&self, name: &str) -> Option<&str> {
        self.attributes
            .iter()
            .find(|(k, _)| k == name)
            .map(|(_, v)| v.as_str())
    }
}

fn unescape(text: &str) -> String {
    text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&apos;", "'")
        .replace("&amp;", "&")
}

struct Parser<'a> {
    text: &'a str,
    pos: usize,
}

impl<'a> Parser<'a> {
    fn rest(&self) -> &'a str {
        &self.text[self.pos..]
    }

    fn skip_misc(&mut self) -> Result<(), Error> {
        loop {
            let rest = self.rest();
            let trimmed = rest.trim_start();
            self.pos += rest.len() - trimmed.len();
            if trimmed.starts_with("<?") {
                let end = trimmed
                    .find("?>")
                    .ok_or_else(|| invalid("宣言が閉じていません"))?;
                self.pos += end + 2;
            } else if trimmed.starts_with("<!--") {
                let end = trimmed
                    .find("-->")
                    .ok_or_else(|| invalid("注釈が閉じていません"))?;
                self.pos += end + 3;
            } else {
                return Ok(());
            }
        }
    }

    fn element(&mut self, depth: usize) -> Result<Element, Error> {
        if depth > 64 {
            return Err(invalid("入れ子が深すぎます"));
        }
        self.skip_misc()?;
        let rest = self.rest();
        if !rest.starts_with('<') {
            return Err(invalid("要素がありません"));
        }
        let end = rest
            .find('>')
            .ok_or_else(|| invalid("タグが閉じていません"))?;
        let tag = &rest[1..end];
        self.pos += end + 1;
        let (tag, empty) = match tag.strip_suffix('/') {
            Some(t) => (t, true),
            None => (tag, false),
        };
        let mut element = Element::default();
        let name_end = tag.find(char::is_whitespace).unwrap_or(tag.len());
        element.name = tag[..name_end].to_string();
        let mut attrs = &tag[name_end..];
        loop {
            attrs = attrs.trim_start();
            let Some(eq) = attrs.find('=') else { break };
            let key = attrs[..eq].trim().to_string();
            let after = attrs[eq + 1..].trim_start();
            let quote = after
                .chars()
                .next()
                .ok_or_else(|| invalid("属性の値がありません"))?;
            if quote != '"' && quote != '\'' {
                return Err(invalid("属性の値が引用されていません"));
            }
            let close = after[1..]
                .find(quote)
                .ok_or_else(|| invalid("属性の値が閉じていません"))?;
            element
                .attributes
                .push((key, unescape(&after[1..1 + close])));
            attrs = &after[close + 2..];
        }
        if empty {
            return Ok(element);
        }
        let closing = format!("</{}", element.name);
        loop {
            let rest = self.rest();
            let next = rest
                .find('<')
                .ok_or_else(|| invalid("要素が閉じていません"))?;
            element.text.push_str(&unescape(&rest[..next]));
            self.pos += next;
            let rest = self.rest();
            if rest.starts_with(&closing) {
                let end = rest
                    .find('>')
                    .ok_or_else(|| invalid("タグが閉じていません"))?;
                self.pos += end + 1;
                return Ok(element);
            }
            if rest.starts_with("<!--") {
                self.skip_misc()?;
                continue;
            }
            element.children.push(self.element(depth + 1)?);
        }
    }
}

// ---- 配置の木 -----------------------------------------------------------------------

fn node(element: &Element) -> Option<DockNode> {
    match element.name.as_str() {
        "Area" => {
            let widgets: Vec<String> = element
                .children
                .iter()
                .filter(|c| c.name == "Widget" && c.attribute("Closed") != Some("1"))
                .filter_map(|c| c.attribute("Name").map(str::to_string))
                .collect();
            if widgets.is_empty() {
                return None;
            }
            let current = element
                .attribute("Current")
                .filter(|c| widgets.iter().any(|w| w == c))
                .map(str::to_string);
            Some(DockNode::Area { widgets, current })
        }
        "Splitter" => {
            let horizontal = element.attribute("Orientation") == Some("|");
            let raw_sizes: Vec<f64> = element
                .children
                .iter()
                .find(|c| c.name == "Sizes")
                .map(|c| {
                    c.text
                        .split_whitespace()
                        .filter_map(|v| v.parse::<f64>().ok())
                        .collect()
                })
                .unwrap_or_default();
            let parts: Vec<&Element> = element
                .children
                .iter()
                .filter(|c| c.name == "Area" || c.name == "Splitter")
                .collect();
            let mut children = Vec::new();
            let mut sizes = Vec::new();
            for (index, part) in parts.iter().enumerate() {
                let Some(child) = node(part) else { continue };
                let size = raw_sizes
                    .get(index)
                    .copied()
                    .filter(|s| s.is_finite() && *s > 0.0)
                    .unwrap_or(0.0);
                // 同じ向きの入れ子は親へまとめる（大きさは割合で配る）。
                match child {
                    DockNode::Split {
                        horizontal: h,
                        children: grand,
                        sizes: grand_sizes,
                    } if h == horizontal => {
                        let total: f64 = grand_sizes.iter().sum();
                        for (g, s) in grand.into_iter().zip(grand_sizes) {
                            children.push(g);
                            sizes.push(if total > 0.0 { size * s / total } else { 0.0 });
                        }
                    }
                    other => {
                        children.push(other);
                        sizes.push(size);
                    }
                }
            }
            match children.len() {
                0 => None,
                1 => children.pop(),
                _ => {
                    // 大きさが読めなければ等分。
                    if sizes.iter().all(|s| *s <= 0.0) {
                        sizes = vec![1.0; children.len()];
                    }
                    Some(DockNode::Split {
                        horizontal,
                        children,
                        sizes,
                    })
                }
            }
        }
        _ => None,
    }
}

/// `QWidget::saveGeometry` の frameGeometry（x, y, 幅, 高さ）。読めなければ None。
pub fn widget_geometry(data: &[u8]) -> Option<(i32, i32, i32, i32)> {
    if data.len() < 24 || data[..4] != [0x01, 0xD9, 0xD0, 0xCB] {
        return None;
    }
    let int = |at: usize| i32::from_be_bytes([data[at], data[at + 1], data[at + 2], data[at + 3]]);
    let (left, top, right, bottom) = (int(8), int(12), int(16), int(20));
    let (width, height) = (right - left + 1, bottom - top + 1);
    (width > 0 && height > 0).then_some((left, top, width, height))
}

fn hex_bytes(text: &str) -> Vec<u8> {
    text.split_whitespace()
        .filter_map(|v| u8::from_str_radix(v, 16).ok())
        .collect()
}

/// base64 の `dock_state` を読む。
pub fn parse_dock_state(base64: &str) -> Result<DockLayout, Error> {
    let data = uncompress(&decode_base64(base64)?)?;
    let text = String::from_utf8(data).map_err(|_| invalid("UTF-8 ではありません"))?;
    let mut parser = Parser {
        text: &text,
        pos: 0,
    };
    let root = parser.element(0)?;
    if root.name != "QtAdvancedDockingSystem" {
        return Err(invalid("Qt Advanced Docking System の配置ではありません"));
    }
    let mut layout = DockLayout {
        root: None,
        floating: Vec::new(),
    };
    for container in root.children.iter().filter(|c| c.name == "Container") {
        let tree = container
            .children
            .iter()
            .filter(|c| c.name == "Splitter" || c.name == "Area")
            .find_map(node);
        let Some(tree) = tree else { continue };
        if container.attribute("Floating") == Some("1") {
            let geometry = container
                .children
                .iter()
                .find(|c| c.name == "Geometry")
                .and_then(|g| widget_geometry(&hex_bytes(&g.text)));
            layout.floating.push(FloatingDock {
                root: tree,
                geometry,
            });
        } else if layout.root.is_none() {
            layout.root = Some(tree);
        }
    }
    Ok(layout)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Qt 版（PySide6QtAds）で作った配置：左にツール、中央にキャンバス、右に使用色と
    /// ヒストリーのタブ、下にタイムライン、浮いたサブビュー。
    const SAMPLE: &str = "AAAEbnjalVRRT4MwEH73V1x4n7QwJybAsszMN43Z1OcOLrOxtEvboTP+eMu2EGbKiAkBet/X767fHaTTr0pAjdpwJbOAXpMAUBaq5HKTBS+rxSgJpnn6bGdlzWSB5b0qPhy23BuLFby2GwN4MajbtZOZK2kZly6SBZFborSaiTdebtBmQcFkzUyjFuRpS4WFUMwechMXX24Ft9aFnzR32x3SaI8a7Z20jWwf56flxI4z08hgxdbmUOh8pzU2kFVKLFFgYZU+VXIsDx5ZhR4c5kIZLJviwjwNG9le8c4B3anY5gD/SdDlDEhHHel3blxFe1/JWybQueGRPKN1FbyZl/wbTU5jAsnNBCIygTQ8xtzz5Hi/r7xC4drp9bSLXUg9ScZAKfGmDdt58Y9O4/MDqgqt3ueEQnkHJYFiDYQAiQ/3wSuCpGVSwPgM8rw7Pu3wab9y7Oz8r1oatucZ/Cho/8Cb3brm+OnrSxe60JbxbTLYkrDnb5Ff/QKL61qZ";

    fn area(names: &[&str], current: &str) -> DockNode {
        DockNode::Area {
            widgets: names.iter().map(|s| s.to_string()).collect(),
            current: Some(current.into()),
        }
    }

    #[test]
    fn reads_a_saved_qt_layout() {
        let layout = parse_dock_state(SAMPLE).unwrap();
        assert_eq!(
            layout.root,
            Some(DockNode::Split {
                horizontal: false,
                children: vec![
                    DockNode::Split {
                        horizontal: true,
                        children: vec![
                            area(&["toolSelectorDock"], "toolSelectorDock"),
                            area(&["canvasDock"], "canvasDock"),
                            area(&["paletteDock", "historyDock"], "historyDock"),
                        ],
                        sizes: vec![130.0, 856.0, 206.0],
                    },
                    area(&["timelineDock"], "timelineDock"),
                ],
                sizes: vec![684.0, 110.0],
            })
        );
        assert_eq!(layout.floating.len(), 1);
        assert_eq!(
            layout.floating[0].root,
            area(&["subviewDock"], "subviewDock")
        );
        assert_eq!(layout.floating[0].geometry, Some((0, 0, 644, 484)));
    }

    #[test]
    fn closed_widgets_and_same_direction_nesting_are_simplified() {
        let xml = r#"<?xml version="1.0"?><QtAdvancedDockingSystem Version="1"><Container Floating="0">
            <Splitter Orientation="|" Count="2">
              <Area Tabs="1" Current="a"><Widget Name="a" Closed="0"/></Area>
              <Splitter Orientation="|" Count="2">
                <Area Current="b"><Widget Name="b" Closed="0"/></Area>
                <Area Current="c"><Widget Name="c" Closed="0"/></Area>
                <Sizes>100 300 </Sizes>
              </Splitter>
              <Sizes>200 400</Sizes>
            </Splitter></Container>
            <Container Floating="1"><Splitter Orientation="-"><Area Current="d"><Widget Name="d" Closed="1"/></Area></Splitter></Container>
            </QtAdvancedDockingSystem>"#;
        let base64 = {
            const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
            let mut out = String::new();
            for chunk in xml.as_bytes().chunks(3) {
                let b = [
                    chunk[0],
                    *chunk.get(1).unwrap_or(&0),
                    *chunk.get(2).unwrap_or(&0),
                ];
                let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
                for i in 0..=chunk.len() {
                    out.push(T[((n >> (18 - 6 * i)) & 63) as usize] as char);
                }
            }
            out
        };
        let layout = parse_dock_state(&base64).unwrap();
        assert!(layout.floating.is_empty());
        let Some(DockNode::Split {
            horizontal: true,
            children,
            sizes,
        }) = layout.root
        else {
            panic!("{:?}", layout.root);
        };
        assert_eq!(children.len(), 3);
        assert_eq!(sizes, vec![200.0, 100.0, 300.0]);
        assert!(parse_dock_state("AAAA").is_err());
        assert!(parse_dock_state("not base64!").is_err());
    }
}
