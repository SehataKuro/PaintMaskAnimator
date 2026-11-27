//! 使用色の色相・彩度・明度の調整（#22、`hsv_adjust_dialog.shifted_color`）。
//!
//! Python 版は Qt 6 の QColor で RGB と HSV を行き来する。ここではその計算
//! （16bit の内部値・単精度の演算・qRound・qt_div_257）を Qt 6.11 と同じ順番で再現する。
//! HSV→RGB の `1 - s*f` は clang の融合積和（fp-contract）で 1 回だけ丸められる。
const MAX: f32 = 65535.0;

fn q_round(value: f32) -> i32 {
    if value >= 0.0 {
        (value + 0.5) as i32
    } else {
        (value - 0.5) as i32
    }
}

/// 16bit の値を 8bit へ（Qt 6.11 の `qt_div_257`：x/257 を四捨五入、ちょうど半分は切り捨て）。
fn div_257(x: i32) -> i32 {
    (x * 255 + 32767) / 65535
}

/// QColor の HSV（`hue()` は無彩色で -1、ほかは 0〜360。彩度・明度は 0〜255）。
///
/// Qt 6.11 の `QColor::toHsv` は 16bit の整数のまま最大・最小を取り、
/// 彩度と色相だけを単精度で求める。
pub fn qcolor_hsv(rgb: [u8; 3]) -> (i32, i32, i32) {
    let [r, g, b] = rgb.map(|v| i32::from(v) * 0x101);
    let min = r.min(g).min(b);
    let max = r.max(g).max(b);
    if min == max {
        return (-1, 0, div_257(max));
    }
    let chroma = (max - min) as f32;
    let saturation = q_round((chroma / max as f32) * MAX);
    let hue = if max == r {
        let hue = (g - b) as f32 / chroma;
        if hue < 0.0 {
            hue + 6.0
        } else {
            hue
        }
    } else if max == g {
        2.0 + (b - r) as f32 / chroma
    } else {
        4.0 + (r - g) as f32 / chroma
    };
    let hue16 = q_round(hue * 6000.0);
    (hue16 / 100, div_257(saturation), div_257(max))
}

/// `QColor.fromHsv(h, s, v)` の RGB（h は -1 か 0〜359、s・v は 0〜255）。
pub fn qcolor_from_hsv(hue: i32, saturation: i32, value: i32) -> [u8; 3] {
    let hue16 = if hue == -1 {
        65535
    } else {
        hue.rem_euclid(360) * 100
    };
    let saturation16 = saturation * 0x101;
    let value16 = value * 0x101;
    if saturation16 == 0 || hue16 == 65535 {
        let v = div_257(value16) as u8;
        return [v, v, v];
    }
    let h = if hue16 == 36000 {
        0.0
    } else {
        hue16 as f32 / 6000.0
    };
    let s = saturation16 as f32 / MAX;
    let v = value16 as f32 / MAX;
    let i = h as i32;
    let f = h - i as f32;
    let p = v * (1.0 - s);
    let (r, g, b) = if i & 1 == 1 {
        let q = v * (-s).mul_add(f, 1.0);
        match i {
            1 => (q, v, p),
            3 => (p, q, v),
            _ => (v, p, q),
        }
    } else {
        let t = v * (-s).mul_add(1.0 - f, 1.0);
        match i {
            0 => (v, t, p),
            2 => (p, v, t),
            _ => (t, p, v),
        }
    };
    let out = |c: f32| div_257(q_round(c * MAX)) as u8;
    [out(r), out(g), out(b)]
}

/// Python の `round`（偶数への丸め）で `percent * 2.55` を整数にする。
fn percent_step(percent: i32) -> i32 {
    (f64::from(percent) * 2.55).round_ties_even() as i32
}

/// `rgb` を HSV 空間でずらした色。`hue` は度、`saturation`・`value` は％。
pub fn shifted_color(rgb: [u8; 3], hue: i32, saturation: i32, value: i32) -> [u8; 3] {
    let (current_hue, current_saturation, current_value) = qcolor_hsv(rgb);
    let new_hue = if current_hue < 0 {
        -1
    } else {
        (current_hue + hue).rem_euclid(360)
    };
    let new_saturation = (current_saturation + percent_step(saturation)).clamp(0, 255);
    let new_value = (current_value + percent_step(value)).clamp(0, 255);
    qcolor_from_hsv(
        new_hue.max(0),
        if new_hue < 0 { 0 } else { new_saturation },
        new_value,
    )
}

/// `{元の RGB: ずらした RGB}`（入力の順）。変わらない色と白は含めない。
pub fn shifted_mapping(
    rgbs: &[[u8; 3]],
    hue: i32,
    saturation: i32,
    value: i32,
) -> Vec<([u8; 3], [u8; 3])> {
    let mut mapping: Vec<([u8; 3], [u8; 3])> = Vec::new();
    for &source in rgbs {
        if source == [255, 255, 255] {
            continue;
        }
        let destination = shifted_color(source, hue, saturation, value);
        if destination == source {
            continue;
        }
        match mapping.iter_mut().find(|(known, _)| *known == source) {
            Some(entry) => entry.1 = destination,
            None => mapping.push((source, destination)),
        }
    }
    mapping
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn primary_colours_round_trip() {
        assert_eq!(qcolor_hsv([255, 0, 0]), (0, 255, 255));
        assert_eq!(qcolor_hsv([0, 255, 0]), (120, 255, 255));
        assert_eq!(qcolor_hsv([128, 128, 128]), (-1, 0, 128));
        assert_eq!(qcolor_from_hsv(240, 255, 255), [0, 0, 255]);
        assert_eq!(qcolor_from_hsv(-1, 0, 77), [77, 77, 77]);
        for rgb in [[255, 128, 0], [1, 2, 3], [250, 251, 252]] {
            assert_eq!(shifted_color(rgb, 0, 0, 0), rgb);
        }
        // 8bit の HSV を通るので、ずらさなくても変わる色がある（Qt と同じ）。
        assert_eq!(shifted_color([12, 200, 99], 0, 0, 0), [12, 200, 96]);
    }

    #[test]
    fn shifts() {
        assert_eq!(shifted_color([255, 0, 0], 120, 0, 0), [0, 255, 0]);
        assert_eq!(shifted_color([255, 0, 0], -120, 0, 0), [0, 0, 255]);
        // 無彩色は色相を回しても色が付かない。
        assert_eq!(shifted_color([100, 100, 100], 90, 50, 0), [100, 100, 100]);
        assert_eq!(percent_step(50), 127);
        let mapping = shifted_mapping(&[[255, 0, 0], [255, 255, 255], [9, 9, 9]], 120, 0, 0);
        assert_eq!(mapping, [([255, 0, 0], [0, 255, 0])]);
    }
}
