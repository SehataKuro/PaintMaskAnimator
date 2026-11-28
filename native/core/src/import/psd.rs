//! PSD の読み込み（`main_window_import.py` の `psd_dialog` と psd-tools 1.x の `composite`）。
//!
//! Qt 版は psd-tools でレイヤーごとに `composite(viewport=文書全体, force=True)` を呼び、
//! 最上位のレイヤー1つを1列（グループは子ごとに1コマ）にする。ここでは同じ合成を
//! 純 Rust で行う。対応するのは RGB・グレースケールの 8/16 ビット、PSD と PSB、
//! 無圧縮・RLE・ZIP（予測つきを含む）、ラスターのレイヤーマスク、不透明度・塗りの
//! 不透明度、クリッピング、グループ（通過・分離）、分離できる描画モード。
//!
//! psd-tools と違うところ（docs に書いた差）:
//! - 調整レイヤーは何もしない（psd-tools は下の色に調整をかける）。
//! - レイヤー効果（境界線・カラーオーバーレイなど）、ベクトルマスク、塗りつぶしレイヤーの
//!   描き直し（force=True のときの再描画）、ノックアウトはしない。
//! - 色相・彩度・カラー・輝度・カラー比較（暗/明）はノーマルとして合成する。
//! - ICC プロファイルによる色の変換はしない（sRGB の文書ならほぼ同じ）。
//! - CMYK・Lab・インデックス・ビットマップ・32 ビットは読めない。

use crate::import::image::{MAX_IMAGE_DIMENSION, MAX_SINGLE_IMAGE_PIXELS};
use crate::project::archive::{MAX_PROJECT_LAYERS, MAX_PROJECT_LAYER_CELLS};

/// PSD を読めなかった理由（Qt が「PSDを読み込めませんでした。」の後に出す文）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PsdError(pub String);

impl std::fmt::Display for PsdError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for PsdError {}

fn err<T>(message: impl Into<String>) -> Result<T, PsdError> {
    Err(PsdError(message.into()))
}

fn truncated() -> PsdError {
    PsdError("PSDのデータが途中で切れています。".into())
}

/// 1列ぶん（最上位のレイヤー1つ）。
#[derive(Debug, Clone, PartialEq)]
pub struct PsdTrack {
    /// レイヤー名（空なら "Layer"）。
    pub name: String,
    /// 表示。
    pub visible: bool,
    /// 不透明度（0〜1）。
    pub opacity: f64,
    /// コマごとの絵（文書の大きさのストレート RGBA）。
    pub images: Vec<Vec<u8>>,
}

/// 読み込んだ PSD。
#[derive(Debug, Clone, PartialEq)]
pub struct PsdImport {
    /// 幅。
    pub width: usize,
    /// 高さ。
    pub height: usize,
    /// 列。
    pub tracks: Vec<PsdTrack>,
    /// 合成できずに捨てた項目の数。
    pub skipped: usize,
}

struct Reader<'a> {
    data: &'a [u8],
    pos: usize,
}

impl<'a> Reader<'a> {
    fn new(data: &'a [u8]) -> Self {
        Reader { data, pos: 0 }
    }

    fn take(&mut self, n: usize) -> Result<&'a [u8], PsdError> {
        let end = self.pos.checked_add(n).ok_or_else(truncated)?;
        let bytes = self.data.get(self.pos..end).ok_or_else(truncated)?;
        self.pos = end;
        Ok(bytes)
    }

    fn remaining(&self) -> usize {
        self.data.len().saturating_sub(self.pos)
    }

    fn u8(&mut self) -> Result<u8, PsdError> {
        Ok(self.take(1)?[0])
    }

    fn u16(&mut self) -> Result<u16, PsdError> {
        let b = self.take(2)?;
        Ok(u16::from_be_bytes([b[0], b[1]]))
    }

    fn i16(&mut self) -> Result<i16, PsdError> {
        Ok(self.u16()? as i16)
    }

    fn u32(&mut self) -> Result<u32, PsdError> {
        let b = self.take(4)?;
        Ok(u32::from_be_bytes([b[0], b[1], b[2], b[3]]))
    }

    fn i32(&mut self) -> Result<i32, PsdError> {
        Ok(self.u32()? as i32)
    }

    fn u64(&mut self) -> Result<u64, PsdError> {
        let b = self.take(8)?;
        Ok(u64::from_be_bytes([
            b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
        ]))
    }

    /// PSD は 4 バイト、PSB は 8 バイトの長さ。
    fn length(&mut self, psb: bool) -> Result<usize, PsdError> {
        let n = if psb { self.u64()? } else { self.u32()? as u64 };
        usize::try_from(n).map_err(|_| truncated())
    }

    fn block(&mut self, psb: bool) -> Result<&'a [u8], PsdError> {
        let n = self.length(psb)?;
        self.take(n)
    }

    fn skip_block(&mut self) -> Result<(), PsdError> {
        let n = self.u32()? as usize;
        self.take(n).map(|_| ())
    }
}

/// (left, top, right, bottom)
type Rect = (i32, i32, i32, i32);

fn rect_size(rect: Rect) -> (usize, usize) {
    let w = (rect.2 as i64 - rect.0 as i64).max(0) as usize;
    let h = (rect.3 as i64 - rect.1 as i64).max(0) as usize;
    (w, h)
}

#[derive(Debug, Clone)]
struct MaskInfo {
    rect: Rect,
    background: u8,
    disabled: bool,
    /// マスクの濃度（パラメーターがあるとき、`user ?? vector ?? 255`）。
    density: Option<u8>,
    /// 実マスク（-3）の範囲（`has_real` のとき psd-tools は範囲と地をこちらから取る）。
    real: Option<(Rect, u8)>,
}

#[derive(Debug, Clone)]
struct ChannelInfo {
    id: i16,
    length: usize,
}

#[derive(Debug, Clone, Default)]
struct Channel {
    id: i16,
    /// 復元した標本（ビッグエンディアンのまま）。空なら psd-tools は使わない。
    data: Vec<u8>,
    failed: bool,
}

#[derive(Debug, Clone)]
struct Record {
    rect: Rect,
    infos: Vec<ChannelInfo>,
    channels: Vec<Channel>,
    blend: [u8; 4],
    opacity: u8,
    clipping: bool,
    visible: bool,
    fill: u8,
    mask: Option<MaskInfo>,
    name: String,
    /// 区切り（lsct・lsdk）の種類と描画モード。
    divider: Option<(u32, Option<[u8; 4]>)>,
    adjustment: bool,
}

impl Record {
    fn blend_mode(&self) -> [u8; 4] {
        match self.divider {
            Some((_, Some(mode))) => mode,
            _ => self.blend,
        }
    }
}

const ADJUSTMENT_KEYS: [&[u8; 4]; 17] = [
    b"brit", b"levl", b"curv", b"expA", b"vibA", b"hue ", b"hue2", b"blnc", b"blwh", b"phfl",
    b"mixr", b"clrL", b"nvrt", b"post", b"thrs", b"grdm", b"selc",
];

/// PSB で長さが 8 バイトになる追加情報。
const LONG_KEYS: [&[u8; 4]; 13] = [
    b"LMsk", b"Lr16", b"Lr32", b"Layr", b"Mt16", b"Mt32", b"Mtrn", b"Alph", b"FMsk", b"lnk2",
    b"FEid", b"FXid", b"PxSD",
];

fn macroman(bytes: &[u8]) -> String {
    // 名前は普通 luni（UTF-16）が優先されるので、ASCII 以外は近い文字で足りる。
    bytes
        .iter()
        .map(|&b| if b < 0x80 { b as char } else { char::from(b) })
        .collect()
}

struct Header {
    psb: bool,
    width: usize,
    height: usize,
    depth: u16,
    channels: usize,
}

fn read_header(r: &mut Reader) -> Result<Header, PsdError> {
    if r.take(4).ok() != Some(b"8BPS") {
        return err("PSDファイルではありません。");
    }
    let version = r.u16()?;
    if version != 1 && version != 2 {
        return err(format!("対応していないPSDの版です。 ({version})"));
    }
    r.take(6)?;
    let _channels = r.u16()?;
    let height = r.u32()? as usize;
    let width = r.u32()? as usize;
    let depth = r.u16()?;
    let mode = r.u16()?;
    if width < 1
        || height < 1
        || width > MAX_IMAGE_DIMENSION
        || height > MAX_IMAGE_DIMENSION
        || width * height > MAX_SINGLE_IMAGE_PIXELS
    {
        return err(format!(
            "PSDの画像サイズが上限を超えています。 ({width} × {height}px)"
        ));
    }
    let channels = match mode {
        1 => 1,
        3 => 3,
        _ => return err("この色モードのPSDには対応していません（RGB・グレースケールのみ）。"),
    };
    if depth != 8 && depth != 16 {
        return err(format!("{depth}ビットのPSDには対応していません。"));
    }
    Ok(Header {
        psb: version == 2,
        width,
        height,
        depth,
        channels,
    })
}

/// 文書の大きさだけを読む。
pub fn psd_size(data: &[u8]) -> Result<(usize, usize), PsdError> {
    let header = read_header(&mut Reader::new(data))?;
    Ok((header.width, header.height))
}

fn read_mask(data: &[u8], has_real: bool) -> Result<Option<MaskInfo>, PsdError> {
    if data.is_empty() {
        return Ok(None);
    }
    let mut r = Reader::new(data);
    let (top, left, bottom, right) = (r.i32()?, r.i32()?, r.i32()?, r.i32()?);
    let background = r.u8()?;
    let flags = r.u8()?;
    let mut real = None;
    if has_real && data.len() >= 36 {
        let real_flags = r.u8()?;
        let real_background = r.u8()?;
        let (rt, rl, rb, rr) = (r.i32()?, r.i32()?, r.i32()?, r.i32()?);
        if real_flags & 0x10 != 0 {
            real = Some(((rl, rt, rr, rb), real_background));
        }
    }
    let mut density = None;
    if flags & 0x10 != 0 {
        let parameters = r.u8()?;
        let mut user = None;
        let mut vector = None;
        if parameters & 1 != 0 {
            user = Some(r.u8()?);
        }
        if parameters & 2 != 0 {
            r.take(8)?;
        }
        if parameters & 4 != 0 {
            vector = Some(r.u8()?);
        }
        density = Some(user.or(vector).unwrap_or(255));
    }
    Ok(Some(MaskInfo {
        rect: (left, top, right, bottom),
        background,
        disabled: flags & 0x02 != 0,
        density,
        real,
    }))
}

fn read_record(r: &mut Reader, psb: bool) -> Result<Record, PsdError> {
    let (top, left, bottom, right) = (r.i32()?, r.i32()?, r.i32()?, r.i32()?);
    let count = r.u16()? as usize;
    let mut infos = Vec::with_capacity(count.min(64));
    for _ in 0..count {
        let id = r.i16()?;
        let length = r.length(psb)?;
        infos.push(ChannelInfo { id, length });
    }
    let _signature = r.take(4)?;
    let mut blend = [0u8; 4];
    blend.copy_from_slice(r.take(4)?);
    let opacity = r.u8()?;
    let clipping = r.u8()? == 1;
    let flags = r.u8()?;
    r.u8()?;
    let extra = r.block(false)?;
    let mut e = Reader::new(extra);
    let has_real = infos.iter().any(|c| c.id == -3);
    let mask = read_mask(e.block(false)?, has_real)?;
    e.skip_block()?;
    let name_len = e.u8()? as usize;
    let mut name = macroman(e.take(name_len)?);
    let padded = (1 + name_len).div_ceil(4) * 4;
    e.take((padded - 1 - name_len).min(e.remaining()))?;
    let mut fill = 255u8;
    let mut divider = None;
    let mut adjustment = false;
    while e.remaining() >= 12 {
        let signature = e.take(4)?;
        if signature != b"8BIM" && signature != b"8B64" {
            break;
        }
        let key: [u8; 4] = e.take(4)?.try_into().unwrap();
        let long = psb && LONG_KEYS.contains(&&key);
        let data = e.block(long)?;
        match &key {
            b"luni" if data.len() >= 4 => {
                let n = u32::from_be_bytes([data[0], data[1], data[2], data[3]]) as usize;
                let units: Vec<u16> = data[4..]
                    .chunks_exact(2)
                    .take(n)
                    .map(|c| u16::from_be_bytes([c[0], c[1]]))
                    .collect();
                name = String::from_utf16_lossy(&units)
                    .trim_end_matches('\0')
                    .to_string();
            }
            b"lsct" | b"lsdk" if data.len() >= 4 => {
                let kind = u32::from_be_bytes([data[0], data[1], data[2], data[3]]);
                let mode = (data.len() >= 12).then(|| [data[8], data[9], data[10], data[11]]);
                divider = Some((kind, mode));
            }
            b"iOpa" if !data.is_empty() => fill = data[0],
            k if ADJUSTMENT_KEYS.contains(&k) => adjustment = true,
            _ => {}
        }
    }
    Ok(Record {
        rect: (left, top, right, bottom),
        infos,
        channels: Vec::new(),
        blend,
        opacity,
        clipping,
        visible: flags & 0x02 == 0,
        fill,
        mask,
        name,
        divider,
        adjustment,
    })
}

fn unpack_bits(src: &[u8], out: &mut Vec<u8>, expected: usize) -> Result<(), PsdError> {
    let start = out.len();
    let mut i = 0;
    while i < src.len() && out.len() - start < expected {
        let n = src[i] as i8;
        i += 1;
        if n >= 0 {
            let count = n as usize + 1;
            let bytes = src.get(i..i + count).ok_or_else(truncated)?;
            out.extend_from_slice(bytes);
            i += count;
        } else if n != -128 {
            let count = 1 - n as isize;
            let value = *src.get(i).ok_or_else(truncated)?;
            out.extend(std::iter::repeat_n(value, count as usize));
            i += 1;
        }
    }
    if out.len() - start != expected {
        return Err(truncated());
    }
    Ok(())
}

fn inflate(data: &[u8], expected: usize) -> Result<Vec<u8>, PsdError> {
    use std::io::Read;
    let mut out = Vec::with_capacity(expected);
    flate2::read::ZlibDecoder::new(data)
        .take(expected as u64)
        .read_to_end(&mut out)
        .map_err(|e| PsdError(e.to_string()))?;
    if out.len() != expected {
        return Err(truncated());
    }
    Ok(out)
}

fn decode_channel(
    data: &[u8],
    width: usize,
    height: usize,
    depth: u16,
    psb: bool,
) -> Result<Vec<u8>, PsdError> {
    let bpc = (depth / 8) as usize;
    let row = width * bpc;
    let expected = row * height;
    let mut r = Reader::new(data);
    if data.len() < 2 {
        return Ok(Vec::new());
    }
    let compression = r.u16()?;
    let rest = &data[2..];
    match compression {
        0 => rest
            .get(..expected)
            .map(<[u8]>::to_vec)
            .ok_or_else(truncated),
        1 => {
            let count_size = if psb { 4 } else { 2 };
            let mut counts = Vec::with_capacity(height);
            for _ in 0..height {
                counts.push(if psb {
                    r.u32()? as usize
                } else {
                    r.u16()? as usize
                });
            }
            let mut pos = 2 + height * count_size;
            let mut out = Vec::with_capacity(expected);
            for n in counts {
                let src = data.get(pos..pos + n).ok_or_else(truncated)?;
                unpack_bits(src, &mut out, row)?;
                pos += n;
            }
            Ok(out)
        }
        2 => inflate(rest, expected),
        3 => {
            let mut out = inflate(rest, expected)?;
            for line in out.chunks_exact_mut(row.max(1)) {
                if bpc == 1 {
                    for i in 1..line.len() {
                        line[i] = line[i].wrapping_add(line[i - 1]);
                    }
                } else {
                    for i in 1..width {
                        let prev = u16::from_be_bytes([line[2 * i - 2], line[2 * i - 1]]);
                        let cur = u16::from_be_bytes([line[2 * i], line[2 * i + 1]]);
                        let sum = cur.wrapping_add(prev).to_be_bytes();
                        line[2 * i] = sum[0];
                        line[2 * i + 1] = sum[1];
                    }
                }
            }
            Ok(out)
        }
        _ => err(format!("対応していない圧縮方式です。 ({compression})")),
    }
}

fn read_layer_info(data: &[u8], header: &Header) -> Result<Vec<Record>, PsdError> {
    let mut r = Reader::new(data);
    let count = r.i16()?.unsigned_abs() as usize;
    let mut records = Vec::with_capacity(count.min(4096));
    for _ in 0..count {
        records.push(read_record(&mut r, header.psb)?);
    }
    for record in &mut records {
        let infos = record.infos.clone();
        for info in infos {
            let bytes = r.take(info.length)?;
            let rect = match info.id {
                -2 => record.mask.as_ref().map(|m| m.rect).unwrap_or_default(),
                -3 => record
                    .mask
                    .as_ref()
                    .and_then(|m| m.real.map(|(rect, _)| rect))
                    .unwrap_or_default(),
                _ => record.rect,
            };
            let (w, h) = rect_size(rect);
            let channel = match decode_channel(bytes, w, h, header.depth, header.psb) {
                Ok(data) => Channel {
                    id: info.id,
                    data,
                    failed: false,
                },
                Err(_) => Channel {
                    id: info.id,
                    data: Vec::new(),
                    failed: true,
                },
            };
            record.channels.push(channel);
        }
    }
    Ok(records)
}

fn read_records(data: &[u8], header: &Header) -> Result<Vec<Record>, PsdError> {
    let mut r = Reader::new(data);
    r.take(26)?;
    r.skip_block()?;
    r.skip_block()?;
    if r.remaining() == 0 {
        return Ok(Vec::new());
    }
    let section = r.block(header.psb)?;
    let mut s = Reader::new(section);
    let info = s.block(header.psb)?;
    let mut records = if info.is_empty() {
        Vec::new()
    } else {
        read_layer_info(info, header)?
    };
    if s.remaining() >= 17 {
        s.skip_block()?;
    }
    // 16 ビットの文書は、レイヤーを全体の追加情報（Lr16）に入れる。
    while records.is_empty() && s.remaining() >= 12 {
        let signature = s.take(4)?;
        if signature != b"8BIM" && signature != b"8B64" {
            break;
        }
        let key: [u8; 4] = s.take(4)?.try_into().unwrap();
        let long = header.psb && LONG_KEYS.contains(&&key);
        let data = s.block(long)?;
        let pad = data.len().div_ceil(4) * 4 - data.len();
        s.take(pad.min(s.remaining()))?;
        if &key == b"Lr16" || &key == b"Lr32" {
            records = read_layer_info(data, header)?;
        }
    }
    Ok(records)
}

enum Node {
    Layer(usize),
    Group(usize, Vec<Node>),
}

impl Node {
    fn record(&self) -> usize {
        match self {
            Node::Layer(i) | Node::Group(i, _) => *i,
        }
    }
}

fn build_tree(records: &[Record]) -> Vec<Node> {
    let mut stack: Vec<Vec<Node>> = vec![Vec::new()];
    for (i, record) in records.iter().enumerate() {
        match record.divider.map(|d| d.0) {
            Some(3) => stack.push(Vec::new()),
            Some(1) | Some(2) => {
                let children = if stack.len() > 1 {
                    stack.pop().unwrap()
                } else {
                    Vec::new()
                };
                stack.last_mut().unwrap().push(Node::Group(i, children));
            }
            _ => stack.last_mut().unwrap().push(Node::Layer(i)),
        }
    }
    while stack.len() > 1 {
        let rest = stack.pop().unwrap();
        stack.last_mut().unwrap().extend(rest);
    }
    stack.pop().unwrap()
}

fn mark_visible(nodes: &[Node], records: &[Record], parent: bool, out: &mut [bool]) {
    for node in nodes {
        let visible = parent && records[node.record()].visible;
        out[node.record()] = visible;
        if let Node::Group(_, children) = node {
            mark_visible(children, records, visible, out);
        }
    }
}

struct Ctx<'a> {
    w: usize,
    h: usize,
    c: usize,
    depth: u16,
    records: &'a [Record],
    visible: &'a [bool],
}

impl Ctx<'_> {
    fn n(&self) -> usize {
        self.w * self.h
    }

    fn sample(&self, data: &[u8], i: usize) -> f32 {
        if self.depth == 8 {
            data[i] as f32 / 255.0
        } else {
            u16::from_be_bytes([data[2 * i], data[2 * i + 1]]) as f32 / 65535.0
        }
    }

    /// 範囲 `rect` の標本を文書全体へ貼る（外は `background`）。
    fn paste(
        &self,
        data: &[u8],
        rect: Rect,
        background: f32,
        out: &mut [f32],
        stride: usize,
        k: usize,
    ) {
        let (rw, rh) = rect_size(rect);
        for p in 0..self.n() {
            out[p * stride + k] = background;
        }
        for y in 0..rh {
            let dy = rect.1 as i64 + y as i64;
            if dy < 0 || dy >= self.h as i64 {
                continue;
            }
            for x in 0..rw {
                let dx = rect.0 as i64 + x as i64;
                if dx < 0 || dx >= self.w as i64 {
                    continue;
                }
                let p = dy as usize * self.w + dx as usize;
                out[p * stride + k] = self.sample(data, y * rw + x);
            }
        }
    }
}

fn intersects(ctx: &Ctx, rect: Rect) -> bool {
    let left = rect.0.max(0);
    let top = rect.1.max(0);
    let right = (rect.2 as i64).min(ctx.w as i64);
    let bottom = (rect.3 as i64).min(ctx.h as i64);
    (left as i64) < right && (top as i64) < bottom
}

fn union(a: f32, b: f32) -> f32 {
    a + b - a * b
}

fn clip(x: f32) -> f32 {
    x.clamp(0.0, 1.0)
}

const EPS: f32 = 1e-9;

fn blend(mode: &[u8; 4], cb: f32, cs: f32) -> f32 {
    match mode {
        b"mul " => cb * cs,
        b"scrn" => cb + cs - cb * cs,
        b"over" => hard_light(cs, cb),
        b"dark" => cb.min(cs),
        b"lite" => cb.max(cs),
        b"div " => {
            if cb == 0.0 {
                0.0
            } else if cs == 1.0 {
                1.0
            } else {
                (cb / (1.0 - cs + EPS)).min(1.0)
            }
        }
        b"idiv" => {
            if cb == 1.0 {
                1.0
            } else if cs == 0.0 {
                0.0
            } else {
                1.0 - ((1.0 - cb) / (cs + EPS)).min(1.0)
            }
        }
        b"lddg" => (cb + cs).min(1.0),
        b"lbrn" => (cb + cs - 1.0).max(0.0),
        b"hLit" => hard_light(cb, cs),
        b"sLit" => {
            let d = if cb <= 0.25 {
                ((16.0 * cb - 12.0) * cb + 4.0) * cb
            } else {
                cb.sqrt()
            };
            if cs <= 0.5 {
                cb - (1.0 - 2.0 * cs) * cb * (1.0 - cb)
            } else {
                cb + (2.0 * cs - 1.0) * (d - cb)
            }
        }
        b"vLit" => {
            if cs == 0.0 {
                0.0
            } else if cs == 1.0 {
                1.0
            } else if cs <= 0.5 {
                1.0 - ((1.0 - cb) / (2.0 * cs + EPS)).min(1.0)
            } else {
                (cb / (2.0 * (1.0 - cs) + EPS)).min(1.0)
            }
        }
        b"lLit" => {
            if cs > 0.5 {
                (cb + (2.0 * cs - 1.0)).min(1.0)
            } else {
                (cb + 2.0 * cs - 1.0).max(0.0)
            }
        }
        b"pLit" => {
            if cs > 0.5 {
                cb.max(2.0 * cs - 1.0)
            } else {
                cb.min(2.0 * cs)
            }
        }
        b"hMix" => {
            let total = cb + cs;
            if total > 1.0 || (total == 1.0 && cb > 0.5) {
                1.0
            } else {
                0.0
            }
        }
        b"diff" => (cb - cs).abs(),
        b"smud" => cb + cs - 2.0 * cb * cs,
        b"fsub" => (cb - cs).max(0.0),
        b"fdiv" => (cb / (cs + EPS)).min(1.0),
        // ノーマル・ディザ合成・分離できないモード（色相・彩度・カラー・輝度・カラー比較）。
        _ => cs,
    }
}

fn hard_light(cb: f32, cs: f32) -> f32 {
    if cs > 0.5 {
        let s = 2.0 * cs - 1.0;
        cb + s - cb * s
    } else {
        cb * (2.0 * cs)
    }
}

/// マスク・不透明度の値（psd-tools は「マスクなし」をスカラー 1.0 のまま扱う）。
enum Factor {
    Scalar(f64),
    Array(Vec<f32>),
}

impl Factor {
    fn at(&self, p: usize) -> f32 {
        match self {
            Factor::Scalar(v) => *v as f32,
            Factor::Array(a) => a[p],
        }
    }

    fn scaled(&self, k: f64) -> Factor {
        match self {
            Factor::Scalar(v) => Factor::Scalar(v * k),
            Factor::Array(a) => Factor::Array(a.iter().map(|v| v * k as f32).collect()),
        }
    }
}

struct Comp {
    color0: Vec<f32>,
    alpha0: Vec<f32>,
    shape_g: Vec<f32>,
    alpha_g: Vec<f32>,
    color: Vec<f32>,
    alpha: Vec<f32>,
    adjustment_isolated: bool,
}

struct Source {
    color: Vec<f32>,
    shape: Vec<f32>,
    alpha: Vec<f32>,
    mask: Factor,
    fill: f64,
    adjustment_isolated: Option<bool>,
}

fn clip_layers(siblings: &[Node], index: usize, records: &[Record]) -> Vec<usize> {
    if records[siblings[index].record()].clipping {
        return Vec::new();
    }
    let mut out = Vec::new();
    for (i, node) in siblings.iter().enumerate().skip(index + 1) {
        if !records[node.record()].clipping || matches!(node, Node::Group(..)) {
            break;
        }
        out.push(i);
    }
    out
}

impl Comp {
    fn new(ctx: &Ctx, color0: Vec<f32>, alpha0: Vec<f32>, adjustment_isolated: bool) -> Comp {
        let n = ctx.n();
        Comp {
            color: color0.clone(),
            alpha: alpha0.clone(),
            color0,
            alpha0,
            shape_g: vec![0.0; n],
            alpha_g: vec![0.0; n],
            adjustment_isolated,
        }
    }

    fn apply(
        &mut self,
        ctx: &Ctx,
        siblings: &[Node],
        index: usize,
        clip_compositing: bool,
    ) -> Result<(), PsdError> {
        let node = &siblings[index];
        let ri = node.record();
        let record = &ctx.records[ri];
        if !ctx.visible[ri] {
            return Ok(());
        }
        if matches!(node, Node::Layer(_)) && !record.adjustment && !intersects(ctx, record.rect) {
            return Ok(());
        }
        if !clip_compositing && record.clipping {
            return Ok(());
        }
        if record.adjustment {
            // 調整レイヤーは合成しない（psd-tools との差）。
            return Ok(());
        }
        let clips = clip_layers(siblings, index, ctx.records);
        let (mut color, mut shape, mut alpha, adjustment_isolated) = match node {
            Node::Group(_, children) => self.group(ctx, record, children, !clips.is_empty())?,
            Node::Layer(_) => {
                let (color, shape) = self.object(ctx, record)?;
                let alpha = shape.clone();
                (color, shape, alpha, None)
            }
        };
        if !clips.is_empty() {
            let mut sub = Comp::new(ctx, color, alpha.clone(), false);
            for i in clips {
                sub.apply(ctx, siblings, i, true)?;
            }
            color = sub.color;
        }
        let shape_mask = self.mask(ctx, record);
        let fill = record.fill as f64 / 255.0;
        let opacity = record.opacity as f64 / 255.0;
        let mask = shape_mask.scaled(opacity);
        for p in 0..ctx.n() {
            shape[p] *= shape_mask.at(p);
            alpha[p] *= mask.at(p);
        }
        let source = Source {
            color,
            shape,
            alpha,
            mask,
            fill,
            adjustment_isolated,
        };
        let mode = record.blend_mode();
        if &mode == b"pass" && source.adjustment_isolated == Some(false) {
            let f = source.fill as f32;
            let shape: Vec<f32> = source.shape.iter().map(|v| v * f).collect();
            let alpha: Vec<f32> = source.alpha.iter().map(|v| v * f).collect();
            let mask = source.mask.scaled(source.fill);
            self.passthrough(ctx, &source.color, &shape, &alpha, &mask);
        } else {
            let f = source.fill as f32;
            let shape: Vec<f32> = source.shape.iter().map(|v| v * f).collect();
            let alpha: Vec<f32> = source.alpha.iter().map(|v| v * f).collect();
            self.source(ctx, &source.color, &shape, &alpha, &mode);
        }
        Ok(())
    }

    fn object(&self, ctx: &Ctx, record: &Record) -> Result<(Vec<f32>, Vec<f32>), PsdError> {
        if record.channels.iter().any(|c| c.failed) {
            return err("レイヤーの画素を読めませんでした。");
        }
        let (rw, rh) = rect_size(record.rect);
        let n = ctx.n();
        let colors: Vec<&Channel> = record
            .channels
            .iter()
            .filter(|c| c.id >= 0 && !c.data.is_empty())
            .take(ctx.c)
            .collect();
        let alpha_channel = record
            .channels
            .iter()
            .find(|c| c.id == -1 && !c.data.is_empty());
        let has_color = !colors.is_empty() && rw * rh > 0;
        let has_shape = alpha_channel.is_some() && rw * rh > 0;
        let mut color = vec![1.0f32; n * ctx.c];
        if has_color {
            if colors.len() != ctx.c && colors.len() != 1 {
                return err("レイヤーの色の数が文書と合いません。");
            }
            for k in 0..ctx.c {
                let channel = colors[k.min(colors.len() - 1)];
                ctx.paste(&channel.data, record.rect, 1.0, &mut color, ctx.c, k);
            }
        }
        let mut shape = vec![if has_color { 1.0 } else { 0.0 }; n];
        if let (true, Some(channel)) = (has_shape, alpha_channel) {
            ctx.paste(&channel.data, record.rect, 0.0, &mut shape, 1, 0);
        } else if !has_color && !has_shape {
            shape.fill(0.0);
        } else {
            shape.fill(1.0);
        }
        Ok((color, shape))
    }

    fn mask(&self, ctx: &Ctx, record: &Record) -> Factor {
        let Some(mask) = record.mask.as_ref().filter(|m| !m.disabled) else {
            return Factor::Scalar(1.0);
        };
        let (rect, background) = mask.real.unwrap_or((mask.rect, mask.background));
        let mut shape = Factor::Scalar(1.0);
        if let Some(channel) = record.channels.iter().find(|c| c.id == -2) {
            let (w, h) = rect_size(rect);
            if !channel.data.is_empty() && channel.data.len() * 8 / ctx.depth as usize >= w * h {
                let mut out = vec![0.0f32; ctx.n()];
                ctx.paste(
                    &channel.data,
                    rect,
                    (background as f64 / 255.0) as f32,
                    &mut out,
                    1,
                    0,
                );
                shape = Factor::Array(out);
            }
        }
        if let Some(density) = mask.density {
            let d = density as f64 / 255.0;
            shape = match shape {
                Factor::Scalar(v) => Factor::Scalar(d * v + (1.0 - d)),
                Factor::Array(a) => {
                    let (df, rest) = (d as f32, (1.0 - d) as f32);
                    Factor::Array(a.iter().map(|v| df * v + rest).collect())
                }
            };
        }
        shape
    }

    #[allow(clippy::type_complexity)]
    fn group(
        &self,
        ctx: &Ctx,
        record: &Record,
        children: &[Node],
        has_clips: bool,
    ) -> Result<(Vec<f32>, Vec<f32>, Vec<f32>, Option<bool>), PsdError> {
        let passthrough = &record.blend_mode() == b"pass";
        let isolate = (record.fill as f64 / 255.0) < 1.0 || has_clips;
        let alpha0 = if passthrough {
            self.alpha.clone()
        } else {
            vec![0.0; ctx.n()]
        };
        let mut sub = Comp::new(
            ctx,
            self.color.clone(),
            alpha0,
            self.adjustment_isolated || isolate,
        );
        for i in 0..children.len() {
            sub.apply(ctx, children, i, false)?;
        }
        let color = if isolate {
            sub.result_isolated(ctx)
        } else {
            std::mem::take(&mut sub.color)
        };
        Ok((color, sub.shape_g, sub.alpha_g, Some(isolate)))
    }

    fn source(&mut self, ctx: &Ctx, color: &[f32], shape: &[f32], alpha: &[f32], mode: &[u8; 4]) {
        let c = ctx.c;
        for p in 0..ctx.n() {
            let (s, a) = (shape[p], alpha[p]);
            self.shape_g[p] = union(self.shape_g[p], s);
            self.alpha_g[p] = union(self.alpha_g[p], a);
            let previous = self.alpha[p];
            let next = union(self.alpha0[p], self.alpha_g[p]);
            self.alpha[p] = next;
            for k in 0..c {
                let i = p * c + k;
                let (cb, cs) = (self.color[i], color[i]);
                let t = (s - a) * previous * cb
                    + a * ((1.0 - previous) * cs + previous * blend(mode, cb, cs));
                let numerator = (1.0 - s) * previous * cb + t;
                self.color[i] = clip(if next > 0.0 { numerator / next } else { 1.0 });
            }
        }
    }

    fn passthrough(
        &mut self,
        ctx: &Ctx,
        color: &[f32],
        shape: &[f32],
        alpha: &[f32],
        mask: &Factor,
    ) {
        let c = ctx.c;
        for p in 0..ctx.n() {
            let ab = self.alpha[p];
            self.shape_g[p] = union(self.shape_g[p], shape[p]);
            self.alpha_g[p] = union(self.alpha_g[p], alpha[p]);
            let next = union(self.alpha0[p], self.alpha_g[p]);
            self.alpha[p] = next;
            let m = mask.at(p);
            for k in 0..c {
                let i = p * c + k;
                let (cb, cs) = (self.color[i], color[i]);
                let t = (1.0 - m) * ab * cb + (m * ab + alpha[p] * (1.0 - ab)) * cs;
                self.color[i] = clip(if next > 0.0 { t / next } else { cs });
            }
        }
    }

    fn result_isolated(&self, ctx: &Ctx) -> Vec<f32> {
        let c = ctx.c;
        let mut out = vec![0.0; ctx.n() * c];
        for p in 0..ctx.n() {
            let ratio = if self.alpha_g[p] > 0.0 {
                self.alpha0[p] / self.alpha_g[p]
            } else {
                1.0
            };
            let factor = ratio - self.alpha0[p];
            for k in 0..c {
                let i = p * c + k;
                out[i] = clip(self.color[i] + (self.color[i] - self.color0[i]) * factor);
            }
        }
        out
    }
}

/// 1項目を文書全体の大きさで合成する（`layer.composite(viewport, force=True)`）。
fn render(ctx: &Ctx, siblings: &[Node], index: usize) -> Result<Vec<u8>, PsdError> {
    let n = ctx.n();
    let mut comp = Comp::new(ctx, vec![1.0; n * ctx.c], vec![0.0; n], false);
    // グループも1つのレイヤーとして合成する（`Group.composite` は as_layer=True）。
    comp.apply(ctx, siblings, index, false)?;
    let color = comp.result_isolated(ctx);
    let byte = |v: f32| (255.0 * v).clamp(0.0, 255.0) as u8;
    let mut out = Vec::with_capacity(n * 4);
    for p in 0..n {
        if ctx.c == 3 {
            out.extend_from_slice(&[
                byte(color[p * 3]),
                byte(color[p * 3 + 1]),
                byte(color[p * 3 + 2]),
            ]);
        } else {
            let l = byte(color[p]);
            out.extend_from_slice(&[l, l, l]);
        }
        out.push(byte(comp.alpha_g[p]));
    }
    Ok(out)
}

/// PSD を読み、最上位のレイヤーごとに列を作る（`psd_dialog`）。
///
/// `progress(済んだ項目, 全項目)` が `false` を返すと中止して `Ok(None)` を返す。
pub fn read_psd(
    data: &[u8],
    progress: &mut dyn FnMut(usize, usize) -> bool,
) -> Result<Option<PsdImport>, PsdError> {
    let header = read_header(&mut Reader::new(data))?;
    let records = read_records(data, &header)?;
    let tree = build_tree(&records);
    if tree.len() > MAX_PROJECT_LAYERS {
        return err("PSDの最上位レイヤー数が上限を超えています。");
    }
    let mut visible = vec![false; records.len()];
    mark_visible(&tree, &records, true, &mut visible);
    let ctx = Ctx {
        w: header.width,
        h: header.height,
        c: header.channels,
        depth: header.depth,
        records: &records,
        visible: &visible,
    };
    let total: usize = tree
        .iter()
        .map(|node| match node {
            Node::Group(_, children) => children.len(),
            Node::Layer(_) => 1,
        })
        .sum();
    let mut tracks = Vec::new();
    let mut skipped = 0;
    let mut cells = 0;
    let mut done = 0;
    for (index, top) in tree.iter().enumerate() {
        let items: Vec<(&[Node], usize)> = match top {
            Node::Group(_, children) => (0..children.len()).map(|i| (&children[..], i)).collect(),
            Node::Layer(_) => vec![(&tree[..], index)],
        };
        cells += items.len();
        if cells > MAX_PROJECT_LAYER_CELLS {
            return err("PSDのレイヤー項目数が上限を超えています。");
        }
        let mut images = Vec::new();
        for (siblings, i) in items {
            if !progress(done, total) {
                return Ok(None);
            }
            match render(&ctx, siblings, i) {
                Ok(image) => images.push(image),
                Err(_) => skipped += 1,
            }
            done += 1;
        }
        let record = &records[top.record()];
        if images.is_empty() {
            skipped += 1;
            continue;
        }
        tracks.push(PsdTrack {
            name: if record.name.is_empty() {
                "Layer".into()
            } else {
                record.name.clone()
            },
            visible: record.visible,
            opacity: (record.opacity as f64 / 255.0).clamp(0.0, 1.0),
            images,
        });
    }
    progress(total, total);
    if tracks.is_empty() {
        return err("読み込める画像レイヤーがありません。");
    }
    Ok(Some(PsdImport {
        width: header.width,
        height: header.height,
        tracks,
        skipped,
    }))
}

/// 保存されている全体の合成画像（Image Data。Photoshop の「互換性を優先」）を 8 ビットの
/// ストレート RGBA で読む。無い（版の情報が合成無しと言う、8 ビットでない、壊れている）なら None。
fn merged_image(data: &[u8], header: &Header) -> Result<Option<Vec<u8>>, PsdError> {
    if header.depth != 8 || data.len() < 26 {
        return Ok(None);
    }
    let planes = u16::from_be_bytes([data[12], data[13]]) as usize;
    let mut r = Reader::new(data);
    r.take(26)?;
    r.skip_block()?;
    // 画像リソース：版の情報（0x0421）の hasRealMergedData。
    let resources = r.block(false)?;
    let mut res = Reader::new(resources);
    while res.remaining() >= 12 {
        if res.take(4)? != b"8BIM" {
            break;
        }
        let id = res.u16()?;
        let name = res.u8()? as usize;
        res.take(name + (name + 1) % 2)?;
        let size = res.u32()? as usize;
        let body = res.take(size)?;
        res.take((size % 2).min(res.remaining()))?;
        if id == 0x0421 && body.len() >= 5 && body[4] == 0 {
            return Ok(None);
        }
    }
    r.block(header.psb)?;
    if r.remaining() < 2 || planes < header.channels {
        return Ok(None);
    }
    let compression = r.u16()?;
    let (w, h) = (header.width, header.height);
    let plane = w * h;
    let total = plane * planes;
    let pixels = match compression {
        0 => r.take(total)?.to_vec(),
        1 => {
            let rows = planes * h;
            let mut counts = Vec::with_capacity(rows);
            for _ in 0..rows {
                counts.push(if header.psb {
                    r.u32()? as usize
                } else {
                    r.u16()? as usize
                });
            }
            let mut out = Vec::with_capacity(total);
            for count in counts {
                unpack_bits(r.take(count)?, &mut out, w)?;
            }
            out
        }
        2 | 3 => {
            let rest = r.take(r.remaining())?;
            let mut out = inflate(rest, total)?;
            if compression == 3 {
                for row in out.chunks_exact_mut(w) {
                    for x in 1..w {
                        row[x] = row[x].wrapping_add(row[x - 1]);
                    }
                }
            }
            out
        }
        _ => return Ok(None),
    };
    let mut rgba = Vec::with_capacity(plane * 4);
    for p in 0..plane {
        if header.channels == 3 {
            rgba.extend_from_slice(&[pixels[p], pixels[plane + p], pixels[2 * plane + p]]);
        } else {
            rgba.extend_from_slice(&[pixels[p]; 3]);
        }
        let alpha_plane = header.channels;
        rgba.push(if planes > alpha_plane {
            pixels[alpha_plane * plane + p]
        } else {
            255
        });
    }
    Ok(Some(rgba))
}

/// PSD の全体の見た目（サブビューの表示。psd-tools の `PSDImage.composite()`）を
/// (幅, 高さ, ストレート RGBA) で返す。保存された合成画像があればそれ（psd-tools も
/// `has_preview` なら `topil` を返す）、無ければ表示レイヤーを合成する。
pub fn composite_psd(data: &[u8]) -> Result<(usize, usize, Vec<u8>), PsdError> {
    let header = read_header(&mut Reader::new(data))?;
    if let Ok(Some(rgba)) = merged_image(data, &header) {
        return Ok((header.width, header.height, rgba));
    }
    let records = read_records(data, &header)?;
    let tree = build_tree(&records);
    let mut visible = vec![false; records.len()];
    mark_visible(&tree, &records, true, &mut visible);
    let ctx = Ctx {
        w: header.width,
        h: header.height,
        c: header.channels,
        depth: header.depth,
        records: &records,
        visible: &visible,
    };
    let n = ctx.n();
    let mut comp = Comp::new(&ctx, vec![1.0; n * ctx.c], vec![0.0; n], false);
    for index in 0..tree.len() {
        comp.apply(&ctx, &tree, index, false)?;
    }
    let color = comp.result_isolated(&ctx);
    let byte = |v: f32| (255.0 * v).clamp(0.0, 255.0) as u8;
    let mut out = Vec::with_capacity(n * 4);
    for p in 0..n {
        if ctx.c == 3 {
            out.extend_from_slice(&[
                byte(color[p * 3]),
                byte(color[p * 3 + 1]),
                byte(color[p * 3 + 2]),
            ]);
        } else {
            out.extend_from_slice(&[byte(color[p]); 3]);
        }
        out.push(byte(comp.alpha_g[p]));
    }
    Ok((header.width, header.height, out))
}

#[cfg(test)]
#[path = "psd_tests.rs"]
mod tests;
