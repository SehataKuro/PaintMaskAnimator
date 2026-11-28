//! PSD 読み込みの試験。小さな PSD をその場で組み立てて読む（psd-tools との比較は
//! tests/test_core_psd_compare.py）。

use super::*;

struct Layer {
    name: &'static str,
    rect: Rect,
    /// R, G, B, A の各面（rect の大きさ）。
    planes: Option<[Vec<u8>; 4]>,
    opacity: u8,
    clipping: bool,
    hidden: bool,
    divider: Option<u32>,
    rle: bool,
}

fn layer(name: &'static str, rect: Rect, rgba: [u8; 4]) -> Layer {
    let (w, h) = rect_size(rect);
    let planes = [0, 1, 2, 3].map(|k| vec![rgba[k]; w * h]);
    Layer {
        name,
        rect,
        planes: Some(planes),
        opacity: 255,
        clipping: false,
        hidden: false,
        divider: None,
        rle: false,
    }
}

fn divider(name: &'static str, kind: u32) -> Layer {
    Layer {
        name,
        rect: (0, 0, 0, 0),
        planes: None,
        opacity: 255,
        clipping: false,
        hidden: false,
        divider: Some(kind),
        rle: false,
    }
}

fn be16(v: u16) -> [u8; 2] {
    v.to_be_bytes()
}

fn be32(v: u32) -> [u8; 4] {
    v.to_be_bytes()
}

fn channel_data(plane: &[u8], width: usize, rle: bool) -> Vec<u8> {
    let mut out = Vec::new();
    if !rle || width == 0 {
        out.extend_from_slice(&be16(0));
        out.extend_from_slice(plane);
        return out;
    }
    out.extend_from_slice(&be16(1));
    let rows: Vec<&[u8]> = plane.chunks(width).collect();
    let packed: Vec<Vec<u8>> = rows
        .iter()
        .map(|row| {
            // 全部を同じ値の繰り返しとして詰める（幅 128 まで）。
            if row.iter().all(|v| *v == row[0]) && row.len() > 1 {
                vec![(1 - row.len() as i32) as i8 as u8, row[0]]
            } else {
                let mut v = vec![(row.len() - 1) as u8];
                v.extend_from_slice(row);
                v
            }
        })
        .collect();
    for p in &packed {
        out.extend_from_slice(&be16(p.len() as u16));
    }
    for p in packed {
        out.extend(p);
    }
    out
}

fn build(width: u32, height: u32, layers: &[Layer]) -> Vec<u8> {
    build_depth(width, height, layers, 8)
}

fn widen(plane: &[u8], depth: u16) -> Vec<u8> {
    if depth == 8 {
        plane.to_vec()
    } else {
        plane.iter().flat_map(|v| [*v, *v]).collect()
    }
}

fn build_depth(width: u32, height: u32, layers: &[Layer], depth: u16) -> Vec<u8> {
    let mut records = Vec::new();
    let mut images = Vec::new();
    for l in layers {
        let (w, _) = rect_size(l.rect);
        let ids: [i16; 4] = [-1, 0, 1, 2];
        let datas: Vec<Vec<u8>> = ids
            .iter()
            .map(|id| match &l.planes {
                Some(p) => {
                    let k = if *id < 0 { 3 } else { *id as usize };
                    channel_data(&widen(&p[k], depth), w * depth as usize / 8, l.rle)
                }
                None => be16(0).to_vec(),
            })
            .collect();
        for v in [l.rect.1, l.rect.0, l.rect.3, l.rect.2] {
            records.extend_from_slice(&be32(v as u32));
        }
        records.extend_from_slice(&be16(4));
        for (id, d) in ids.iter().zip(&datas) {
            records.extend_from_slice(&be16(*id as u16));
            records.extend_from_slice(&be32(d.len() as u32));
        }
        records.extend_from_slice(b"8BIMnorm");
        records.push(l.opacity);
        records.push(l.clipping as u8);
        records.push(if l.hidden { 2 } else { 0 });
        records.push(0);
        let mut extra = Vec::new();
        extra.extend_from_slice(&be32(0));
        extra.extend_from_slice(&be32(0));
        let name = l.name.as_bytes();
        extra.push(name.len() as u8);
        extra.extend_from_slice(name);
        while (extra.len() - 8) % 4 != 0 {
            extra.push(0);
        }
        if let Some(kind) = l.divider {
            extra.extend_from_slice(b"8BIMlsct");
            extra.extend_from_slice(&be32(4));
            extra.extend_from_slice(&be32(kind));
        }
        records.extend_from_slice(&be32(extra.len() as u32));
        records.extend(extra);
        for d in datas {
            images.extend(d);
        }
    }
    let mut info = Vec::new();
    info.extend_from_slice(&be16(layers.len() as u16));
    info.extend(records);
    info.extend(images);
    if info.len() % 2 == 1 {
        info.push(0);
    }
    let mut section = Vec::new();
    section.extend_from_slice(&be32(info.len() as u32));
    section.extend(info);
    section.extend_from_slice(&be32(0));
    let mut out = Vec::new();
    out.extend_from_slice(b"8BPS");
    out.extend_from_slice(&be16(1));
    out.extend_from_slice(&[0; 6]);
    out.extend_from_slice(&be16(3));
    out.extend_from_slice(&be32(height));
    out.extend_from_slice(&be32(width));
    out.extend_from_slice(&be16(depth));
    out.extend_from_slice(&be16(3));
    out.extend_from_slice(&be32(0));
    out.extend_from_slice(&be32(0));
    out.extend_from_slice(&be32(section.len() as u32));
    out.extend(section);
    out
}

fn read(data: &[u8]) -> PsdImport {
    read_psd(data, &mut |_, _| true).unwrap().unwrap()
}

fn pixel(image: &[u8], width: usize, x: usize, y: usize) -> [u8; 4] {
    let i = (y * width + x) * 4;
    [image[i], image[i + 1], image[i + 2], image[i + 3]]
}

#[test]
fn layers_and_groups_become_tracks() {
    // 下から: 背景、グループ（子 2 枚）、隠したレイヤー。
    let mut hidden = layer("hidden", (0, 0, 2, 2), [0, 0, 255, 255]);
    hidden.hidden = true;
    let mut second = layer("b", (1, 1, 3, 2), [0, 255, 0, 128]);
    second.rle = true;
    let data = build(
        3,
        2,
        &[
            layer("bg", (0, 0, 3, 2), [255, 0, 0, 255]),
            divider("</Layer group>", 3),
            layer("a", (0, 0, 1, 1), [10, 20, 30, 255]),
            second,
            divider("group", 1),
            hidden,
        ],
    );
    assert_eq!(psd_size(&data).unwrap(), (3, 2));
    let read = read(&data);
    assert_eq!(read.skipped, 0);
    let names: Vec<&str> = read.tracks.iter().map(|t| t.name.as_str()).collect();
    assert_eq!(names, ["bg", "group", "hidden"]);
    assert_eq!(read.tracks[1].images.len(), 2);
    assert!(!read.tracks[2].visible);
    assert_eq!(pixel(&read.tracks[0].images[0], 3, 2, 1), [255, 0, 0, 255]);
    let a = &read.tracks[1].images[0];
    assert_eq!(pixel(a, 3, 0, 0), [10, 20, 30, 255]);
    assert_eq!(pixel(a, 3, 1, 0)[3], 0);
    let b = &read.tracks[1].images[1];
    assert_eq!(pixel(b, 3, 2, 1), [0, 255, 0, 128]);
    // 隠したレイヤーは透明な絵になる（psd-tools も合成しない）。
    assert!(read.tracks[2].images[0].chunks(4).all(|p| p[3] == 0));
}

#[test]
fn opacity_and_clipping_are_composited() {
    let mut base = layer("base", (0, 0, 2, 1), [255, 255, 255, 255]);
    base.opacity = 128;
    let mut clip = layer("clip", (0, 0, 1, 1), [0, 0, 0, 255]);
    clip.clipping = true;
    let read = read(&build(2, 1, &[base, clip]));
    let base = &read.tracks[0];
    assert!((base.opacity - 128.0 / 255.0).abs() < 1e-6);
    // 不透明度は画素にも入る（Qt 版も同じで、列の不透明度と二重にかかる）。
    assert_eq!(pixel(&base.images[0], 2, 0, 0), [0, 0, 0, 128]);
    assert_eq!(pixel(&base.images[0], 2, 1, 0), [255, 255, 255, 128]);
    // クリッピングしたレイヤー自身は単独では何も描かない。
    assert!(read.tracks[1].images[0].chunks(4).all(|p| p[3] == 0));
}

#[test]
fn bad_files_and_limits() {
    assert_eq!(
        read_psd(b"nope", &mut |_, _| true).unwrap_err().0,
        "PSDファイルではありません。"
    );
    let big = build(20000, 1, &[]);
    assert_eq!(
        psd_size(&big).unwrap_err().0,
        "PSDの画像サイズが上限を超えています。 (20000 × 1px)"
    );
    assert_eq!(
        read_psd(&build(2, 2, &[]), &mut |_, _| true).unwrap_err().0,
        "読み込める画像レイヤーがありません。"
    );
    let data = build(2, 2, &[layer("a", (0, 0, 2, 2), [1, 2, 3, 255])]);
    assert!(read_psd(&data, &mut |_, _| false).unwrap().is_none());
}

#[test]
fn sixteen_bit_samples() {
    let mut a = layer("a", (0, 0, 2, 1), [10, 200, 30, 255]);
    a.planes.as_mut().unwrap()[3][1] = 51;
    let read = read(&build_depth(2, 1, &[a], 16));
    let image = &read.tracks[0].images[0];
    assert_eq!(pixel(image, 2, 0, 0), [10, 200, 30, 255]);
    assert_eq!(pixel(image, 2, 1, 0), [10, 200, 30, 51]);
}

#[test]
fn merged_alpha_plane_follows_psd_tools_has_transparency() {
    // (面の数, 色の面, レイヤー数, Mtrn など, Alpha Identifiers) → 透明度の面。
    // psd-tools 1.26.1 の has_transparency / get_transparency_index と同じ。
    assert_eq!(merged_alpha_plane(4, 3, 6, false, None), None);
    assert_eq!(merged_alpha_plane(4, 3, -6, false, None), Some(3));
    assert_eq!(merged_alpha_plane(4, 3, 0, false, None), Some(3));
    assert_eq!(merged_alpha_plane(2, 1, 1, false, None), None);
    assert_eq!(merged_alpha_plane(2, 1, 0, false, None), Some(1));
    // 余分の面がなくても Mtrn があれば透明度ありで、最後の面を使う。
    assert_eq!(merged_alpha_plane(3, 3, 1, true, None), Some(2));
    assert_eq!(merged_alpha_plane(4, 3, 0, false, Some(&[5])), None);
    assert_eq!(merged_alpha_plane(5, 3, 0, false, Some(&[7, 0])), Some(4));
    assert_eq!(merged_alpha_plane(4, 3, 0, false, Some(&[])), Some(3));
}

#[test]
fn remove_white_background_matches_pillow() {
    // psd-tools 1.26.1・Pillow 13 の `_remove_white_background` で求めた値。
    assert_eq!(remove_white_background(37, 0), 37);
    assert_eq!(remove_white_background(255, 1), 255);
    assert_eq!(remove_white_background(254, 1), 0);
    assert_eq!(remove_white_background(200, 128), 145);
    assert_eq!(remove_white_background(100, 128), 0);
    assert_eq!(remove_white_background(180, 255), 180);
}
