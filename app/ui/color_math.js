// 色の計算（Qt の QColor の整数の API に合わせる）。描画色の欄・カラーサークル・
// カラースライダー・カラーピッカーが使う。
//
// - HSV：色相 0〜359（無彩色は -1）、彩度・明度 0〜255（QColor.hsvHue など）
// - HSL：色相 0〜359、彩度・輝度 0〜255（QColor.hslHue など）
// - CMYK：0〜255（QColor.getCmyk）

export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export function hex([r, g, b]) {
  return "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase();
}

/// "#RRGGBB"・"RRGGBB"・"#RGB" を読む。読めなければ null。
export function parseHex(text) {
  let t = String(text ?? "").trim().replace(/^#/, "");
  if (/^[0-9a-fA-F]{3}$/.test(t)) t = t.split("").map((c) => c + c).join("");
  if (!/^[0-9a-fA-F]{6}$/.test(t)) return null;
  return [0, 2, 4].map((i) => parseInt(t.slice(i, i + 2), 16));
}

export const sameColor = (a, b) => a && b && a[0] === b[0] && a[1] === b[1] && a[2] === b[2];

/// QColor.toHsv（Qt 6.11。16 ビットの整数のまま差を取り、彩度と色相だけを float で求める。
/// pma-core の used_colors::pixels::QtHsv::from_rgb と同じ）。
export function toHsv([r8, g8, b8]) {
  const f = Math.fround;
  const [r, g, b] = [r8, g8, b8].map((v) => v * 257);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const div257 = (x) => Math.floor((x + 128) / 257);
  const qRound = (x) => Math.trunc(f(x + 0.5));
  if (max === min) return { h: -1, s: 0, v: div257(max) };
  const chroma = f(max - min);
  const s = qRound(f(f(chroma / max) * 65535));
  let h;
  if (max === r) {
    h = f((g - b) / chroma);
    if (h < 0) h = f(h + 6);
  } else if (max === g) h = f(2 + f((b - r) / chroma));
  else h = f(4 + f((r - g) / chroma));
  return { h: Math.floor(qRound(f(h * 6000)) / 100), s: div257(s), v: div257(max) };
}

/// QColor.fromHsv(h, s, v)（整数）の toRgb。16 ビットの内部値から float で求める
/// （pma-core の QtHsv::to_rgb と同じ。`1 - S * F` は Qt と同じく積和で、1回だけ丸める）。
export function fromHsv(h, s, v) {
  const f = Math.fround;
  const div257 = (x) => Math.floor((x + 128) / 257);
  const qRound = (x) => Math.trunc(f(x + 0.5));
  const hue = h < 0 ? -1 : (((h % 360) + 360) % 360) * 100;
  const sat = clamp(s, 0, 255) * 257;
  const val = clamp(v, 0, 255) * 257;
  if (sat === 0 || hue < 0) {
    const g = div257(val);
    return [g, g, g];
  }
  const H = f(hue / 6000);
  const S = f(sat / 65535);
  const V = f(val / 65535);
  const i = Math.trunc(H);
  const F = f(H - i);
  const p = f(V * f(1 - S));
  let rgb;
  if (i & 1) {
    const q = f(V * f(1 - S * F));
    rgb = i === 1 ? [q, V, p] : i === 3 ? [p, q, V] : [V, p, q];
  } else {
    const t = f(V * f(1 - S * f(1 - F)));
    rgb = i === 0 ? [V, t, p] : i === 2 ? [p, V, t] : [t, p, V];
  }
  return rgb.map((c) => div257(qRound(f(c * 65535))));
}

// Qt 6.11 の float の計算をそのまま写す。`fround` で float に丸め、Qt が arm64 の clang で
// 積和（fma）にまとめる式は fma で1回だけ丸める（a*b は float どうしなので f64 で正確）。
const f32 = Math.fround;
const fma = (a, b, c) => f32(a * b + c);
const qRound = (x) => (x >= 0 ? Math.trunc(f32(x + 0.5)) : Math.trunc(f32(x - 0.5)));
const div257 = (x) => Math.floor((x + 128) / 257);
const U = 65535;

/// QColor の HSL（hslHue・hslSaturation・lightness）。
export function toHsl([r8, g8, b8]) {
  const [r, g, b] = [r8, g8, b8].map((v) => v * 257);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  if (max === min) return { h: -1, s: 0, l: div257(max) };
  const chroma = f32(max - min);
  const lightness = f32(0.5 * (max + min));
  const sat = f32(f32(0.5 * chroma) / Math.min(lightness, f32(U - lightness)));
  let h;
  if (max === r) {
    h = f32((g - b) / chroma);
    if (h < 0) h = f32(h + 6);
  } else if (max === g) h = f32(2 + f32((b - r) / chroma));
  else h = f32(4 + f32((r - g) / chroma));
  return {
    h: Math.floor(qRound(f32(h * 6000)) / 100),
    s: div257(qRound(f32(sat * U))),
    l: div257(qRound(lightness)),
  };
}

/// QColor.fromHsl(h, s, l)（整数）の toRgb。
export function fromHsl(h, s, l) {
  const hue = h < 0 ? -1 : (((h % 360) + 360) % 360) * 100;
  const sat = clamp(s, 0, 255) * 257;
  const light = clamp(l, 0, 255) * 257;
  if (sat === 0 || hue < 0) {
    const g = div257(light);
    return [g, g, g];
  }
  if (light === 0) return [0, 0, 0];
  const H = hue === 36000 ? 0 : f32(hue / 36000);
  const S = f32(sat / U);
  const L = f32(light / U);
  const temp2 = L < 0.5 ? f32(L * f32(1 + S)) : fma(-L, S, f32(L + S));
  const temp1 = fma(2, L, -temp2);
  const out = [f32(H + f32(1 / 3)), H, f32(H - f32(1 / 3))].map((t0) => {
    let t = t0;
    if (t < 0) t = f32(t + 1);
    else if (t > 1) t = f32(t - 1);
    let c;
    if (f32(t * 6) < 1) c = fma(f32(temp2 - temp1), f32(t * 6), temp1);
    else if (f32(t * 2) < 1) c = temp2;
    else if (f32(t * 3) < 2) c = fma(f32(f32(temp2 - temp1) * f32(f32(2 / 3) - t)), 6, temp1);
    else c = temp1;
    let v = qRound(f32(c * U));
    if (v === 1) v = 0;
    return v;
  });
  return out.map(div257);
}

/// QColor.getCmyk（0〜255）。
export function toCmyk([r8, g8, b8]) {
  if (r8 === 0 && g8 === 0 && b8 === 0) return [0, 0, 0, 255];
  const [c0, m0, y0] = [r8, g8, b8].map((v) => f32(1 - f32((v * 257) / U)));
  const k = Math.min(c0, m0, y0);
  const d = f32(1 - k);
  return [...[c0, m0, y0].map((x) => div257(qRound(f32(f32(f32(x - k) / d) * U)))), div257(qRound(f32(k * U)))];
}

/// QColor.fromCmyk(c, m, y, k) の toRgb。
export function fromCmyk(c, m, y, k) {
  const K = f32((clamp(k, 0, 255) * 257) / U);
  const one = f32(1 - K);
  return [c, m, y].map((x) => {
    const X = f32((clamp(x, 0, 255) * 257) / U);
    return div257(qRound(f32(f32(1 - fma(X, one, K)) * U)));
  });
}

/// 文字の色（チャートのタイルと同じ判断：明るい地には黒っぽい字）。
export function textColor([r, g, b]) {
  return (r * 299 + g * 587 + b * 114) / 1000 >= 145 ? "#111111" : "#FFFFFF";
}
