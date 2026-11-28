//! Python の文字列・数値の変換のうち、書き出しと読み込みで使うものの再現。
//!
//! JSON の値は Python では dict・list・str・int… なので、`str(value)`・`int(value)` の
//! 結果をここで同じにする。Unicode の数字は ASCII と全角（U+FF10〜FF19）だけを数字とする
//! （Python は他の文字体系の十進数字も数字として扱うが、ファイル名・シートでは使われない）。

use crate::project::json::{float_repr, Json};

/// `str.isdecimal()` の1文字分（ASCII と全角の数字）。
pub fn is_decimal(c: char) -> bool {
    c.is_ascii_digit() || ('\u{ff10}'..='\u{ff19}').contains(&c)
}

/// 十進数字の値。数字でなければ None。
pub fn decimal_value(c: char) -> Option<u32> {
    if c.is_ascii_digit() {
        Some(c as u32 - '0' as u32)
    } else if ('\u{ff10}'..='\u{ff19}').contains(&c) {
        Some(c as u32 - 0xff10)
    } else {
        None
    }
}

/// `s.isdecimal()`（空文字列は偽）。
pub fn all_decimal(s: &str) -> bool {
    !s.is_empty() && s.chars().all(is_decimal)
}

/// 十進数字だけの文字列を整数にする（`int(s)`）。i64 を超えれば None。
pub fn decimal_to_i64(s: &str) -> Option<i64> {
    let mut value: i64 = 0;
    for c in s.chars() {
        let digit = decimal_value(c)?;
        value = value.checked_mul(10)?.checked_add(i64::from(digit))?;
    }
    (!s.is_empty()).then_some(value)
}

/// `str.casefold()` の近似（小文字化と ß の展開）。ファイル名の比較に使う。
pub fn casefold(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            'ß' | 'ẞ' => out.push_str("ss"),
            'ς' => out.push('σ'),
            c => out.extend(c.to_lowercase()),
        }
    }
    out
}

/// `str.isspace()` の1文字分。
pub fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `str.strip()`。
pub fn strip(s: &str) -> &str {
    s.trim_matches(is_space)
}

/// `str.strip(chars)`。
pub fn strip_chars<'a>(s: &'a str, chars: &str) -> &'a str {
    s.trim_matches(|c| chars.contains(c))
}

/// `str.rstrip(chars)`。
pub fn rstrip_chars<'a>(s: &'a str, chars: &str) -> &'a str {
    s.trim_end_matches(|c| chars.contains(c))
}

/// JSON の値に対する Python の `str(value)`。
pub fn py_str(value: &Json) -> String {
    match value {
        Json::Null => "None".into(),
        Json::Bool(true) => "True".into(),
        Json::Bool(false) => "False".into(),
        Json::Int(digits) => digits.clone(),
        Json::Float(f) => py_float_str(*f),
        Json::Str(s) => s.clone(),
        Json::Array(items) => {
            let parts: Vec<String> = items.iter().map(py_repr).collect();
            format!("[{}]", parts.join(", "))
        }
        Json::Object(fields) => {
            let parts: Vec<String> = fields
                .iter()
                .map(|(key, value)| {
                    format!("{}: {}", py_repr(&Json::Str(key.clone())), py_repr(value))
                })
                .collect();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

fn py_float_str(f: f64) -> String {
    if f.is_nan() {
        "nan".into()
    } else if f.is_infinite() {
        if f > 0.0 { "inf" } else { "-inf" }.into()
    } else {
        float_repr(f).unwrap_or_default()
    }
}

/// Python の `repr(value)`（リストの中の文字列は引用符付き）。
fn py_repr(value: &Json) -> String {
    match value {
        Json::Str(s) => {
            let quote = if s.contains('\'') && !s.contains('"') {
                '"'
            } else {
                '\''
            };
            let mut out = String::new();
            out.push(quote);
            for c in s.chars() {
                match c {
                    '\\' => out.push_str("\\\\"),
                    '\n' => out.push_str("\\n"),
                    '\r' => out.push_str("\\r"),
                    '\t' => out.push_str("\\t"),
                    c if c == quote => {
                        out.push('\\');
                        out.push(c);
                    }
                    c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                        out.push_str(&format!("\\x{:02x}", c as u32));
                    }
                    c => out.push(c),
                }
            }
            out.push(quote);
            out
        }
        other => py_str(other),
    }
}

/// JSON の値に対する Python の `int(value)`。変換できなければ None（Python は例外）。
pub fn py_int(value: &Json) -> Option<i64> {
    match value {
        Json::Bool(b) => Some(i64::from(*b)),
        Json::Int(digits) => digits.parse().ok(),
        Json::Float(f) if f.is_finite() => {
            let t = f.trunc();
            (t >= i64::MIN as f64 && t < i64::MAX as f64).then_some(t as i64)
        }
        Json::Str(s) => {
            let s = strip(s);
            let (negative, body) = match s.strip_prefix('-') {
                Some(rest) => (true, rest),
                None => (false, s.strip_prefix('+').unwrap_or(s)),
            };
            let body: String = body.chars().filter(|&c| c != '_').collect();
            if body.is_empty() || s.contains("__") || s.ends_with('_') || s.starts_with('_') {
                return None;
            }
            let value = decimal_to_i64(&body)?;
            Some(if negative { -value } else { value })
        }
        _ => None,
    }
}

/// オブジェクトのキーの値（dict.get）。オブジェクトでなければ None。
pub fn get<'a>(value: &'a Json, key: &str) -> Option<&'a Json> {
    match value {
        Json::Object(_) => value.get(key),
        _ => None,
    }
}

/// Python の真偽値としての評価（`if value:`）。
pub fn truthy(value: &Json) -> bool {
    match value {
        Json::Null => false,
        Json::Bool(b) => *b,
        Json::Int(digits) => digits != "0",
        Json::Float(f) => *f != 0.0,
        Json::Str(s) => !s.is_empty(),
        Json::Array(items) => !items.is_empty(),
        Json::Object(fields) => !fields.is_empty(),
    }
}

/// 整数の JSON 値。
pub fn int(value: impl Into<i64>) -> Json {
    Json::Int(value.into().to_string())
}

/// 文字列の JSON 値。
pub fn string(value: impl Into<String>) -> Json {
    Json::Str(value.into())
}

/// キーの順序を保ったオブジェクト。
pub fn object<const N: usize>(fields: [(&str, Json); N]) -> Json {
    Json::Object(
        fields
            .into_iter()
            .map(|(key, value)| (key.to_string(), value))
            .collect(),
    )
}

/// Python の `"%g" % value`（有効数字6桁、末尾の 0 を落とす）。
pub fn format_g(value: f64) -> String {
    if value.is_nan() {
        return "nan".into();
    }
    if value.is_infinite() {
        return if value > 0.0 { "inf" } else { "-inf" }.into();
    }
    if value == 0.0 {
        return if value.is_sign_negative() { "-0" } else { "0" }.into();
    }
    let precision = 6usize;
    let scientific = format!("{:.*e}", precision - 1, value);
    let (mantissa, exponent) = scientific.split_once('e').unwrap_or((&scientific, "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    if exponent < -4 || exponent >= precision as i32 {
        let mantissa = trim_zeros(mantissa);
        format!(
            "{mantissa}e{}{:02}",
            if exponent < 0 { '-' } else { '+' },
            exponent.unsigned_abs()
        )
    } else {
        let decimals = (precision as i32 - 1 - exponent).max(0) as usize;
        trim_zeros(&format!("{value:.decimals$}")).to_string()
    }
}

fn trim_zeros(text: &str) -> &str {
    if text.contains('.') {
        text.trim_end_matches('0').trim_end_matches('.')
    } else {
        text
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn str_and_int_follow_python() {
        assert_eq!(py_str(&Json::Float(1.0)), "1.0");
        assert_eq!(py_str(&Json::Bool(true)), "True");
        assert_eq!(py_str(&Json::Null), "None");
        assert_eq!(
            py_str(&Json::Array(vec![
                Json::Str("a".into()),
                Json::Int("1".into())
            ])),
            "['a', 1]"
        );
        assert_eq!(py_int(&Json::Str(" 12 ".into())), Some(12));
        assert_eq!(py_int(&Json::Str("1_0".into())), Some(10));
        assert_eq!(py_int(&Json::Str("1.5".into())), None);
        assert_eq!(py_int(&Json::Float(-2.7)), Some(-2));
        assert_eq!(py_int(&Json::Bool(true)), Some(1));
        assert_eq!(py_int(&Json::Null), None);
    }

    #[test]
    fn format_g_matches_python() {
        for (value, text) in [
            (24.0, "24"),
            (23.976, "23.976"),
            (0.5, "0.5"),
            (1234567.0, "1.23457e+06"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (100000.0, "100000"),
        ] {
            assert_eq!(format_g(value), text);
        }
    }

    #[test]
    fn decimal_helpers() {
        assert!(all_decimal("０１2"));
        assert_eq!(decimal_to_i64("０１2"), Some(12));
        assert!(!all_decimal(""));
        assert_eq!(casefold("ABß"), "abss");
    }
}
