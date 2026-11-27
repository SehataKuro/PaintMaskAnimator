//! PSD の書き出し（`ExportController.psd_dialog`。Qt 版は psd-tools で書く）。
//!
//! レイヤーごとにフォルダー（グループ）を作り、その中へキーの絵を1枚ずつ画素レイヤーとして
//! 入れる。バイト列は psd-tools 1.19.0 の `PSDImage.new("RGB", …, color=白)` →
//! `create_group` → `create_pixel_layer` → `save` と同じになるように組む。
//!
//! - 画素レイヤーは RGB（アルファを落とした色）と、すべて 255 の透明度チャンネル、アルファを
//!   写したレイヤーマスクを持つ（RGB の文書なので psd-tools はアルファをマスクに入れる）。
//! - 統合画像は psd-tools の合成（`composite`）を float32 の同じ順の計算でなぞる。白地の上に
//!   重ねた色から白地の分を引き戻す（`result_isolated`）ので、絵のある所は重ねる前の色になる。
//! - レイヤー名は MacRoman。psd-tools は MacRoman で書けない名前（日本語など）で失敗するが、
//!   ここでは書けない文字を `?` にし、画素レイヤーにも Unicode の名前（luni）を足して書き出す。
//!   書ける名前なら psd-tools と同じバイト列になる。

use crate::document::model::Document;
use crate::document::timeline_panel::default_layer_name;
use crate::project::qt_alpha::qt_premultiplied_roundtrip;
use crate::Error;

use super::sheet::{has_content, slot_image};

/// psd-tools が版情報（VersionInfo）に書く名前。
pub const WRITER_NAME: &str = "psd-tools 1.19.0";

/// MacRoman の 0x80〜0xFF の文字（Python の `mac_roman`）。
const MAC_ROMAN_HIGH: [u16; 128] = [
    0x00C4, 0x00C5, 0x00C7, 0x00C9, 0x00D1, 0x00D6, 0x00DC, 0x00E1, 0x00E0, 0x00E2, 0x00E4, 0x00E3,
    0x00E5, 0x00E7, 0x00E9, 0x00E8, 0x00EA, 0x00EB, 0x00ED, 0x00EC, 0x00EE, 0x00EF, 0x00F1, 0x00F3,
    0x00F2, 0x00F4, 0x00F6, 0x00F5, 0x00FA, 0x00F9, 0x00FB, 0x00FC, 0x2020, 0x00B0, 0x00A2, 0x00A3,
    0x00A7, 0x2022, 0x00B6, 0x00DF, 0x00AE, 0x00A9, 0x2122, 0x00B4, 0x00A8, 0x2260, 0x00C6, 0x00D8,
    0x221E, 0x00B1, 0x2264, 0x2265, 0x00A5, 0x00B5, 0x2202, 0x2211, 0x220F, 0x03C0, 0x222B, 0x00AA,
    0x00BA, 0x03A9, 0x00E6, 0x00F8, 0x00BF, 0x00A1, 0x00AC, 0x221A, 0x0192, 0x2248, 0x2206, 0x00AB,
    0x00BB, 0x2026, 0x00A0, 0x00C0, 0x00C3, 0x00D5, 0x0152, 0x0153, 0x2013, 0x2014, 0x201C, 0x201D,
    0x2018, 0x2019, 0x00F7, 0x25CA, 0x00FF, 0x0178, 0x2044, 0x20AC, 0x2039, 0x203A, 0xFB01, 0xFB02,
    0x2021, 0x00B7, 0x201A, 0x201E, 0x2030, 0x00C2, 0x00CA, 0x00C1, 0x00CB, 0x00C8, 0x00CD, 0x00CE,
    0x00CF, 0x00CC, 0x00D3, 0x00D4, 0xF8FF, 0x00D2, 0x00DA, 0x00DB, 0x00D9, 0x0131, 0x02C6, 0x02DC,
    0x00AF, 0x02D8, 0x02D9, 0x02DA, 0x00B8, 0x02DD, 0x02DB, 0x02C7,
];

/// MacRoman にする。書けない文字は `?`。全部書けたかも返す。
fn mac_roman(text: &str) -> (Vec<u8>, bool) {
    let mut out = Vec::with_capacity(text.len());
    let mut exact = true;
    for c in text.chars() {
        let code = c as u32;
        if code < 0x80 {
            out.push(code as u8);
        } else if let Some(index) = MAC_ROMAN_HIGH.iter().position(|&m| m as u32 == code) {
            out.push(0x80 + index as u8);
        } else {
            out.push(b'?');
            exact = false;
        }
    }
    (out, exact)
}

/// psd-tools の RLE（Apple PackBits、`psd_tools.compression.rle.encode`）。
pub fn packbits(data: &[u8]) -> Vec<u8> {
    const MAX_LEN: usize = 0x7f;
    let length = data.len();
    let mut result = Vec::new();
    match length {
        0 => return result,
        1 => return vec![0, data[0]],
        _ => {}
    }
    let (mut i, mut j) = (0usize, 0usize);
    while i < length {
        if j + 1 < length && data[j] == data[j + 1] {
            while j < length {
                if j - i >= MAX_LEN || j + 1 >= length || data[j] != data[j + 1] {
                    break;
                }
                j += 1;
            }
            result.push((256 - (j - i)) as u8);
            result.push(data[i]);
            j += 1;
            i = j;
        } else {
            while j < length {
                if j - i >= MAX_LEN {
                    break;
                }
                let differs = j + 1 < length && data[j] != data[j + 1];
                if !differs {
                    if (j + 2 == length || MAX_LEN - (j - i) <= 2)
                        && j + 1 != length
                        && data[j] == data[j + 1]
                    {
                        break;
                    }
                    if j + 2 < length && data[j] == data[j + 1] && data[j + 1] == data[j + 2] {
                        break;
                    }
                }
                j += 1;
            }
            result.push((j - i - 1) as u8);
            result.extend_from_slice(&data[i..j]);
            i = j;
        }
    }
    result
}

/// 1チャンネル（行ごとの RLE。先頭に行の長さの並び）。圧縮の種類の 2 バイトは含まない。
fn rle_channel(plane: &[u8], width: usize, height: usize) -> Vec<u8> {
    let rows: Vec<Vec<u8>> = (0..height)
        .map(|y| packbits(&plane[y * width..(y + 1) * width]))
        .collect();
    let mut out = Vec::with_capacity(height * 2 + rows.iter().map(Vec::len).sum::<usize>());
    for row in &rows {
        out.extend_from_slice(&(row.len() as u16).to_be_bytes());
    }
    for row in rows {
        out.extend(row);
    }
    out
}

fn pad(out: &mut Vec<u8>, written: usize, divisor: usize) {
    let remainder = written % divisor;
    if remainder != 0 {
        out.resize(out.len() + divisor - remainder, 0);
    }
}

fn pascal(out: &mut Vec<u8>, name: &[u8]) {
    let name = &name[..name.len().min(255)];
    out.push(name.len() as u8);
    out.extend_from_slice(name);
    pad(out, 1 + name.len(), 4);
}

fn tagged(out: &mut Vec<u8>, key: &[u8; 4], data: &[u8]) {
    out.extend_from_slice(b"8BIM");
    out.extend_from_slice(key);
    out.extend_from_slice(&(data.len() as u32).to_be_bytes());
    out.extend_from_slice(data);
}

/// luni（Unicode の名前、4 の倍数に詰める）。
fn unicode_name(name: &str) -> Vec<u8> {
    let units: Vec<u16> = name.encode_utf16().collect();
    let mut data = (units.len() as u32).to_be_bytes().to_vec();
    for unit in units {
        data.extend_from_slice(&unit.to_be_bytes());
    }
    let len = data.len();
    pad(&mut data, len, 4);
    data
}

/// レイヤーレコード1件（`LayerRecord.write`）。
struct Record {
    bounds: [i32; 4],
    channels: Vec<(i16, Vec<u8>)>,
    opacity: u8,
    visible: bool,
    mask: Option<[i32; 4]>,
    name: Vec<u8>,
    blocks: Vec<([u8; 4], Vec<u8>)>,
}

impl Record {
    fn write(&self, out: &mut Vec<u8>) {
        for value in self.bounds {
            out.extend_from_slice(&value.to_be_bytes());
        }
        out.extend_from_slice(&(self.channels.len() as u16).to_be_bytes());
        for (id, data) in &self.channels {
            out.extend_from_slice(&id.to_be_bytes());
            out.extend_from_slice(&(data.len() as u32 + 2).to_be_bytes());
        }
        out.extend_from_slice(b"8BIMnorm");
        out.push(self.opacity);
        out.push(0); // clipping
        out.push(if self.visible { 0x08 } else { 0x0a });
        out.push(0);
        let mut extra = Vec::new();
        match self.mask {
            Some(bounds) => {
                extra.extend_from_slice(&20u32.to_be_bytes());
                for value in bounds {
                    extra.extend_from_slice(&value.to_be_bytes());
                }
                extra.extend_from_slice(&[0, 0, 0, 0]); // 背景色・フラグ・詰め物
            }
            None => extra.extend_from_slice(&0u32.to_be_bytes()),
        }
        extra.extend_from_slice(&40u32.to_be_bytes());
        for _ in 0..10 {
            extra.extend_from_slice(&[0, 0, 0xff, 0xff]);
        }
        pascal(&mut extra, &self.name);
        for (key, data) in &self.blocks {
            tagged(&mut extra, key, data);
        }
        let len = extra.len();
        pad(&mut extra, len, 2);
        out.extend_from_slice(&(extra.len() as u32).to_be_bytes());
        out.extend(extra);
    }

    fn write_channels(&self, out: &mut Vec<u8>) {
        for (_, data) in &self.channels {
            out.extend_from_slice(&(if data.is_empty() { 0u16 } else { 1u16 }).to_be_bytes());
            out.extend_from_slice(data);
        }
    }
}

fn group_records(name: &str, opacity: u8, visible: bool) -> (Record, Record) {
    let empty = || {
        (-1..=2)
            .map(|id| (id as i16, Vec::new()))
            .collect::<Vec<_>>()
    };
    let divider = "</Layer group>";
    let bounding = Record {
        bounds: [0; 4],
        channels: empty(),
        opacity: 255,
        visible: true,
        mask: None,
        name: divider.as_bytes().to_vec(),
        blocks: vec![
            (*b"lsct", 3u32.to_be_bytes().to_vec()),
            (*b"luni", unicode_name(divider)),
        ],
    };
    let mut setting = 1u32.to_be_bytes().to_vec();
    setting.extend_from_slice(b"8BIMpass");
    let group = Record {
        bounds: [0; 4],
        channels: empty(),
        opacity,
        visible,
        mask: None,
        name: mac_roman(name).0,
        blocks: vec![(*b"lsct", setting), (*b"luni", unicode_name(name))],
    };
    (bounding, group)
}

/// 統合画像の合成の途中（psd-tools の `Compositor` の、1画素ずつの値）。
struct Canvas {
    color: Vec<[f32; 3]>,
    alpha: Vec<f32>,
    alpha_g: Vec<f32>,
}

fn union(backdrop: f32, source: f32) -> f32 {
    backdrop + source - backdrop * source
}

fn divide(a: f32, b: f32, fill: f32) -> f32 {
    if b > 0.0 {
        a / b
    } else {
        fill
    }
}

fn clip(value: f32) -> f32 {
    value.clamp(0.0, 1.0)
}

/// グループの中の合成（白地の上のグループに、画素レイヤーを `_apply_source` で重ねる）。
struct GroupCanvas {
    canvas: Canvas,
}

impl GroupCanvas {
    fn new(top: &Canvas) -> Self {
        Self {
            canvas: Canvas {
                color: top.color.clone(),
                alpha: top.alpha.clone(),
                alpha_g: vec![0.0; top.alpha.len()],
            },
        }
    }

    /// 画素レイヤー1枚（色はストレート、形は 1、マスクはアルファ）。
    fn apply(&mut self, alpha0: &[f32], rgba: &[u8]) {
        let c = &mut self.canvas;
        for (index, pixel) in rgba.chunks_exact(4).enumerate() {
            let mask = pixel[3] as f32 / 255.0;
            let shape = mask;
            let alpha = mask;
            c.alpha_g[index] = union(c.alpha_g[index], alpha);
            let alpha_previous = c.alpha[index];
            c.alpha[index] = union(alpha0[index], c.alpha_g[index]);
            let alpha_b = alpha_previous;
            for (channel, value) in pixel[..3].iter().enumerate() {
                let source = *value as f32 / 255.0;
                let color_b = c.color[index][channel];
                let color_t = (shape - alpha) * alpha_b * color_b
                    + alpha * ((1.0 - alpha_b) * source + alpha_b * source);
                c.color[index][channel] = clip(divide(
                    (1.0 - shape) * alpha_previous * color_b + color_t,
                    c.alpha[index],
                    1.0,
                ));
            }
        }
    }
}

impl Canvas {
    fn white(len: usize) -> Self {
        Self {
            color: vec![[1.0; 3]; len],
            alpha: vec![1.0; len],
            alpha_g: vec![0.0; len],
        }
    }

    /// 通過（pass through）のグループを重ねる（`_apply_passthrough_source`）。
    fn pass_through(&mut self, group: &Canvas, opacity: f64) {
        let mask32 = opacity as f32;
        let inverse = (1.0 - opacity) as f32;
        for index in 0..self.alpha.len() {
            let alpha = group.alpha_g[index] * mask32;
            let alpha_b = self.alpha[index];
            self.alpha_g[index] = union(self.alpha_g[index], alpha);
            self.alpha[index] = union(1.0, self.alpha_g[index]);
            for channel in 0..3 {
                let color_b = self.color[index][channel];
                let color = group.color[index][channel];
                let color_t = inverse * alpha_b * color_b
                    + (mask32 * alpha_b + alpha * (1.0 - alpha_b)) * color;
                self.color[index][channel] = clip(divide(color_t, self.alpha[index], color));
            }
        }
    }

    /// 白地の分を引き戻した色（`result_isolated`）を 8 bit の面（R, G, B）にする。
    fn merged(&self) -> Vec<u8> {
        let len = self.alpha.len();
        let mut planes = vec![0u8; len * 3];
        for index in 0..len {
            let factor = divide(1.0, self.alpha_g[index], 1.0) - 1.0;
            for channel in 0..3 {
                let color = self.color[index][channel];
                let value = clip(color + (color - 1.0) * factor);
                planes[channel * len + index] = (255.0 * value).clamp(0.0, 255.0) as u8;
            }
        }
        planes
    }
}

/// 書き出すグループ（文書のレイヤー1本）。
#[derive(Clone, Debug)]
pub struct PsdGroup {
    /// フォルダー名。
    pub name: String,
    /// 不透明度（0〜255）。
    pub opacity: u8,
    /// 表示するか。
    pub visible: bool,
    /// 画素レイヤーの名前（絵は書くときに `pixels` で1枚ずつ受け取る）。
    pub layers: Vec<String>,
}

/// 画素レイヤーの絵（グループの番号, グループの中の番号）→ ストレート RGBA。
pub type PixelSource<'a> = dyn FnMut(usize, usize) -> Result<Vec<u8>, Error> + 'a;

/// PSD のバイト列を組む。groups は下のレイヤーから。絵は1枚ずつ受け取って圧縮するので、
/// キーの数だけ画像を抱えない。progress は (書いた枚数, 全体) を受け、false なら止めて Err。
pub fn psd_bytes(
    width: usize,
    height: usize,
    groups: &[PsdGroup],
    pixels: &mut PixelSource<'_>,
    progress: &mut dyn FnMut(usize, usize) -> bool,
) -> Result<Vec<u8>, Error> {
    let len = width * height;
    let total: usize = groups.iter().map(|g| g.layers.len()).sum();
    let mut done = 0;
    let mut top = Canvas::white(len);
    let mut records = Vec::new();
    let full = [0, 0, height as i32, width as i32];
    let opaque = rle_channel(&vec![255u8; len], width, height);
    for (group_index, group) in groups.iter().enumerate() {
        let (bounding, record) = group_records(&group.name, group.opacity, group.visible);
        records.push(bounding);
        let mut inner = GroupCanvas::new(&top);
        let alpha0 = top.alpha.clone();
        for (layer_index, name) in group.layers.iter().enumerate() {
            if !progress(done, total) {
                return Err(Error::Invalid("PSD: 中止しました".into()));
            }
            let rgba = pixels(group_index, layer_index)?;
            if rgba.len() != len * 4 {
                return Err(Error::Invalid("PSD: 画像の大きさが違います".into()));
            }
            let plane =
                |channel: usize| -> Vec<u8> { rgba.chunks_exact(4).map(|p| p[channel]).collect() };
            let (pascal_name, exact) = mac_roman(name);
            let blocks = if exact {
                Vec::new()
            } else {
                vec![(*b"luni", unicode_name(name))]
            };
            records.push(Record {
                bounds: full,
                channels: vec![
                    (-1, opaque.clone()),
                    (0, rle_channel(&plane(0), width, height)),
                    (1, rle_channel(&plane(1), width, height)),
                    (2, rle_channel(&plane(2), width, height)),
                    (-2, rle_channel(&plane(3), width, height)),
                ],
                opacity: 255,
                visible: true,
                mask: Some(full),
                name: pascal_name,
                blocks,
            });
            if group.visible {
                inner.apply(&alpha0, &rgba);
            }
            done += 1;
        }
        if group.visible {
            top.pass_through(&inner.canvas, group.opacity as f64 / 255.0);
        }
        records.push(record);
    }
    progress(total, total);

    let mut out = Vec::new();
    out.extend_from_slice(b"8BPS");
    out.extend_from_slice(&1u16.to_be_bytes());
    out.extend_from_slice(&[0; 6]);
    out.extend_from_slice(&3u16.to_be_bytes());
    out.extend_from_slice(&(height as u32).to_be_bytes());
    out.extend_from_slice(&(width as u32).to_be_bytes());
    out.extend_from_slice(&8u16.to_be_bytes());
    out.extend_from_slice(&3u16.to_be_bytes());
    out.extend_from_slice(&0u32.to_be_bytes()); // 色モードのデータ

    // 画像リソース：VersionInfo（0x0421）だけ。
    let mut version = 1u32.to_be_bytes().to_vec();
    version.push(1);
    for name in [WRITER_NAME, WRITER_NAME] {
        version.extend_from_slice(&(name.encode_utf16().count() as u32).to_be_bytes());
        for unit in name.encode_utf16() {
            version.extend_from_slice(&unit.to_be_bytes());
        }
    }
    version.extend_from_slice(&1u32.to_be_bytes());
    let mut resource = b"8BIM".to_vec();
    resource.extend_from_slice(&0x0421u16.to_be_bytes());
    resource.extend_from_slice(&[0, 0]);
    resource.extend_from_slice(&(version.len() as u32).to_be_bytes());
    let version_len = version.len();
    resource.extend(version);
    pad(&mut resource, version_len, 2);
    out.extend_from_slice(&(resource.len() as u32).to_be_bytes());
    out.extend(resource);

    // レイヤーとマスクの情報。
    let mut info = (records.len() as i16).to_be_bytes().to_vec();
    for record in &records {
        record.write(&mut info);
    }
    for record in &records {
        record.write_channels(&mut info);
    }
    let info_len = info.len();
    pad(&mut info, info_len, 4);
    let mut section = (info.len() as u32).to_be_bytes().to_vec();
    section.extend(info);
    section.extend_from_slice(&0u32.to_be_bytes()); // グローバルなレイヤーマスク
    out.extend_from_slice(&(section.len() as u32).to_be_bytes());
    out.extend(section);

    // 統合画像（無圧縮）。
    out.extend_from_slice(&0u16.to_be_bytes());
    out.extend(top.merged());
    Ok(out)
}

/// 文書のフォルダー名・不透明度・表示とキーの位置 (コマ, レイヤー)（`psd_dialog` が作るグループ）。
pub fn document_groups(document: &Document) -> Vec<(PsdGroup, Vec<(usize, usize)>)> {
    let mut groups = Vec::new();
    for (layer, track_id) in document.track_order().iter().enumerate() {
        let Some(track) = document.track(*track_id) else {
            continue;
        };
        let settings = &track.settings;
        let name = if settings.name.is_empty() {
            default_layer_name(layer)
        } else {
            settings.name.clone()
        };
        let opacity = (settings.opacity * 255.0)
            .round_ties_even()
            .clamp(0.0, 255.0) as u8;
        let mut names = Vec::new();
        let mut keys = Vec::new();
        for (frame, frame_id) in document.frame_order().iter().enumerate() {
            let Some(position) = document
                .frame(*frame_id)
                .and_then(|row| row.positions.get(track_id))
            else {
                continue;
            };
            if !has_content(position) || position.pending {
                continue;
            }
            names.push(format!("{name}{:04}", names.len() + 1));
            keys.push((frame, layer));
        }
        let group = PsdGroup {
            name,
            opacity,
            visible: settings.visible,
            layers: names,
        };
        groups.push((group, keys));
    }
    groups
}

/// 文書を PSD にする。書けるキーがなければ None（Qt の「書き出せるキーフレームがありません。」）。
/// 返すのは (バイト列, キーの数)。キーの絵は Qt の `read_qimage` → PIL と同じ画素
/// （乗算済みの往復）。
pub fn document_psd(
    document: &Document,
    progress: &mut dyn FnMut(usize, usize) -> bool,
) -> Result<Option<(Vec<u8>, usize)>, Error> {
    let (width, height) = document.size();
    let planned = document_groups(document);
    let keys: usize = planned.iter().map(|(_, k)| k.len()).sum();
    if keys == 0 {
        return Ok(None);
    }
    let groups: Vec<PsdGroup> = planned.iter().map(|(g, _)| g.clone()).collect();
    let mut pixels = |group: usize, layer: usize| -> Result<Vec<u8>, Error> {
        let (frame, layer_index) = planned[group].1[layer];
        let mut pixels = match slot_image(document, frame, layer_index) {
            Some(image) => image.rgba().to_vec(),
            None => vec![0; width * height * 4],
        };
        qt_premultiplied_roundtrip(&mut pixels, width);
        Ok(pixels)
    };
    let data = psd_bytes(width, height, &groups, &mut pixels, progress)?;
    Ok(Some((data, keys)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn packbits_matches_psd_tools_examples() {
        assert_eq!(packbits(b"AAABCCCC"), vec![254, b'A', 0, b'B', 253, b'C']);
        assert_eq!(packbits(&[7]), vec![0, 7]);
        assert_eq!(packbits(&[255; 4]), vec![253, 255]);
        assert_eq!(packbits(&[0, 0, 10, 0]), vec![255, 0, 1, 10, 0]);
        let long = vec![9u8; 300];
        let encoded = packbits(&long);
        assert_eq!(encoded, vec![129, 9, 129, 9, 213, 9]);
    }

    #[test]
    fn mac_roman_names() {
        assert_eq!(mac_roman("Aé"), (vec![b'A', 0x8e], true));
        assert_eq!(mac_roman("線画1"), (vec![b'?', b'?', b'1'], false));
    }

    #[test]
    fn tiny_document_matches_psd_tools_layout() {
        // psd-tools 1.19.0 で書いた 4×2 の文書（scratch の tiny.py と同じ）の要所。
        let mut img = vec![0u8; 4 * 2 * 4];
        img[4..8].copy_from_slice(&[10, 20, 30, 100]);
        img[(4 + 2) * 4..(4 + 2) * 4 + 4].copy_from_slice(&[200, 20, 30, 255]);
        let groups = [
            PsdGroup {
                name: "Aé".into(),
                opacity: 128,
                visible: false,
                layers: vec!["Aé0001".into()],
            },
            PsdGroup {
                name: "B".into(),
                opacity: 255,
                visible: true,
                layers: vec!["B0001".into()],
            },
        ];
        let bytes =
            psd_bytes(4, 2, &groups, &mut |_, _| Ok(img.clone()), &mut |_, _| true).unwrap();
        assert_eq!(bytes.len(), 0x516);
        assert_eq!(&bytes[0x80..0x8a], &[0, 0, 4, 0x78, 0, 0, 4, 0x70, 0, 6]);
        let merged = &bytes[bytes.len() - 24..];
        assert_eq!(
            merged,
            &[
                255, 10, 255, 255, 255, 255, 200, 255, 255, 20, 255, 255, 255, 255, 20, 255, 255,
                30, 255, 255, 255, 255, 30, 255
            ]
        );
    }
}
