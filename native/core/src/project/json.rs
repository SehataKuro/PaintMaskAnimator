//! Python の json.loads / json.dumps と同じ解釈・書式の JSON。
//!
//! serde_json の Map はキーを並べ替えるため、project.json のキー順・重複キー・
//! NaN・桁の多い整数を Python と同じに扱うためにこの木を使う。
use serde_json::{Map, Number, Value};

/// Python の JSON 値。オブジェクトは最初に現れた位置と最後の値を持つ（dict と同じ）。
#[derive(Clone, Debug, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    /// 整数の十進表記（"-0" は "0" に正規化する）。Python の int は桁数に上限がない。
    Int(String),
    /// Python の float。NaN・Infinity も保持する。
    Float(f64),
    Str(String),
    Array(Vec<Json>),
    Object(Vec<(String, Json)>),
}

/// 解析の失敗。Python では JSONDecodeError（ValueError）になる場合。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct JsonError {
    /// 失敗した位置（文字列のバイト位置）。
    pub position: usize,
    /// 理由。
    pub reason: &'static str,
}

impl std::fmt::Display for JsonError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} (位置 {})", self.reason, self.position)
    }
}

/// 解析結果。duplicates は Python の object_pairs_hook で数える重複キー（全階層）。
#[derive(Debug)]
pub struct Parsed {
    pub value: Json,
    pub duplicates: usize,
}

// Python の再帰上限（既定 1000）より浅い深さで止める。Python では RecursionError。
const MAX_DEPTH: usize = 900;

struct Parser<'a> {
    text: &'a [u8],
    source: &'a str,
    at: usize,
    duplicates: usize,
}

impl<'a> Parser<'a> {
    fn error(&self, reason: &'static str) -> JsonError {
        JsonError {
            position: self.at,
            reason,
        }
    }

    fn skip_whitespace(&mut self) {
        while let Some(b' ' | b'\t' | b'\n' | b'\r') = self.text.get(self.at) {
            self.at += 1;
        }
    }

    fn literal(&mut self, word: &str) -> bool {
        if self.source[self.at..].starts_with(word) {
            self.at += word.len();
            true
        } else {
            false
        }
    }

    fn value(&mut self, depth: usize) -> Result<Json, JsonError> {
        if depth > MAX_DEPTH {
            return Err(self.error("入れ子が深すぎます"));
        }
        match self.text.get(self.at) {
            Some(b'"') => self.string().map(Json::Str),
            Some(b'{') => self.object(depth),
            Some(b'[') => self.array(depth),
            Some(b'n') if self.literal("null") => Ok(Json::Null),
            Some(b't') if self.literal("true") => Ok(Json::Bool(true)),
            Some(b'f') if self.literal("false") => Ok(Json::Bool(false)),
            Some(b'N') if self.literal("NaN") => Ok(Json::Float(f64::NAN)),
            Some(b'I') if self.literal("Infinity") => Ok(Json::Float(f64::INFINITY)),
            Some(b'-') if self.literal("-Infinity") => Ok(Json::Float(f64::NEG_INFINITY)),
            Some(b'-' | b'0'..=b'9') => self.number(),
            _ => Err(self.error("値がありません")),
        }
    }

    fn digits(&mut self) -> usize {
        let start = self.at;
        while self.text.get(self.at).is_some_and(u8::is_ascii_digit) {
            self.at += 1;
        }
        self.at - start
    }

    // Python の NUMBER_RE: (-?(?:0|[1-9]\d*))(\.\d+)?([eE][-+]?\d+)?
    fn number(&mut self) -> Result<Json, JsonError> {
        let start = self.at;
        if self.text[self.at] == b'-' {
            self.at += 1;
        }
        match self.text.get(self.at) {
            Some(b'0') => self.at += 1,
            Some(b'1'..=b'9') => {
                self.digits();
            }
            _ => {
                self.at = start;
                return Err(self.error("値がありません"));
            }
        }
        let integer_end = self.at;
        let mut float = false;
        if self.text.get(self.at) == Some(&b'.') {
            let dot = self.at;
            self.at += 1;
            if self.digits() == 0 {
                self.at = dot;
            } else {
                float = true;
            }
        }
        if let Some(b'e' | b'E') = self.text.get(self.at) {
            let mark = self.at;
            self.at += 1;
            if let Some(b'+' | b'-') = self.text.get(self.at) {
                self.at += 1;
            }
            if self.digits() == 0 {
                self.at = mark;
            } else {
                float = true;
            }
        }
        let text = &self.source[start..self.at];
        if float {
            // Rust の f64 解析は正しく丸め、桁あふれは Python の float() と同じく ±inf。
            let value: f64 = text.parse().map_err(|_| self.error("数値"))?;
            Ok(Json::Float(value))
        } else {
            let text = &self.source[start..integer_end];
            Ok(Json::Int(if text == "-0" {
                "0".into()
            } else {
                text.into()
            }))
        }
    }

    fn hex4(&mut self) -> Result<u32, JsonError> {
        let digits = self
            .source
            .get(self.at..self.at + 4)
            .filter(|s| s.bytes().all(|b| b.is_ascii_hexdigit()))
            .ok_or_else(|| self.error("不正な \\u エスケープ"))?;
        self.at += 4;
        u32::from_str_radix(digits, 16).map_err(|_| self.error("不正な \\u エスケープ"))
    }

    fn string(&mut self) -> Result<String, JsonError> {
        self.at += 1;
        let mut out = String::new();
        loop {
            let start = self.at;
            while let Some(&b) = self.text.get(self.at) {
                if b == b'"' || b == b'\\' || b < 0x20 {
                    break;
                }
                self.at += 1;
            }
            out.push_str(&self.source[start..self.at]);
            match self.text.get(self.at) {
                None => return Err(self.error("文字列が閉じていません")),
                Some(b'"') => {
                    self.at += 1;
                    return Ok(out);
                }
                Some(b'\\') => {
                    self.at += 1;
                    let escaped = *self
                        .text
                        .get(self.at)
                        .ok_or_else(|| self.error("文字列が閉じていません"))?;
                    self.at += 1;
                    match escaped {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let mut code = self.hex4()?;
                            if (0xd800..0xdc00).contains(&code)
                                && self.source[self.at..].starts_with("\\u")
                            {
                                let mark = self.at;
                                self.at += 2;
                                let low = self.hex4()?;
                                if (0xdc00..0xe000).contains(&low) {
                                    code = 0x10000 + ((code - 0xd800) << 10) + (low - 0xdc00);
                                } else {
                                    self.at = mark;
                                }
                            }
                            // Python は単独のサロゲートも str に入れるが、Rust の文字列には
                            // 入らない。ここでは読めない JSON として扱う。
                            let c = char::from_u32(code)
                                .ok_or_else(|| self.error("単独のサロゲートには対応しません"))?;
                            out.push(c);
                        }
                        _ => {
                            self.at -= 1;
                            return Err(self.error("不正なエスケープ"));
                        }
                    }
                }
                Some(_) => return Err(self.error("文字列内の制御文字")),
            }
        }
    }

    fn array(&mut self, depth: usize) -> Result<Json, JsonError> {
        self.at += 1;
        let mut items = Vec::new();
        self.skip_whitespace();
        if self.text.get(self.at) == Some(&b']') {
            self.at += 1;
            return Ok(Json::Array(items));
        }
        loop {
            self.skip_whitespace();
            items.push(self.value(depth + 1)?);
            self.skip_whitespace();
            match self.text.get(self.at) {
                Some(b',') => self.at += 1,
                Some(b']') => {
                    self.at += 1;
                    return Ok(Json::Array(items));
                }
                _ => return Err(self.error("',' か ']' がありません")),
            }
        }
    }

    fn object(&mut self, depth: usize) -> Result<Json, JsonError> {
        self.at += 1;
        let mut fields: Vec<(String, Json)> = Vec::new();
        self.skip_whitespace();
        if self.text.get(self.at) == Some(&b'}') {
            self.at += 1;
            return Ok(Json::Object(fields));
        }
        loop {
            self.skip_whitespace();
            if self.text.get(self.at) != Some(&b'"') {
                return Err(self.error("キーは二重引用符の文字列です"));
            }
            let key = self.string()?;
            self.skip_whitespace();
            if self.text.get(self.at) != Some(&b':') {
                return Err(self.error("':' がありません"));
            }
            self.at += 1;
            self.skip_whitespace();
            let value = self.value(depth + 1)?;
            // dict への代入と同じく、重複キーは最初の位置に最後の値を置く。
            match fields.iter_mut().find(|(name, _)| *name == key) {
                Some(field) => {
                    self.duplicates += 1;
                    field.1 = value;
                }
                None => fields.push((key, value)),
            }
            self.skip_whitespace();
            match self.text.get(self.at) {
                Some(b',') => self.at += 1,
                Some(b'}') => {
                    self.at += 1;
                    return Ok(Json::Object(fields));
                }
                _ => return Err(self.error("',' か '}' がありません")),
            }
        }
    }
}

/// json.loads と同じ規則で解析する（strict=True）。
///
/// Python と異なる点: 単独のサロゲートの \u エスケープと、900 段を超える入れ子は
/// エラーにする（Python はそれぞれ受け付ける・RecursionError）。
pub fn parse(text: &str) -> Result<Parsed, JsonError> {
    let mut parser = Parser {
        text: text.as_bytes(),
        source: text,
        at: 0,
        duplicates: 0,
    };
    parser.skip_whitespace();
    let value = parser.value(0)?;
    parser.skip_whitespace();
    if parser.at != text.len() {
        return Err(parser.error("余分なデータがあります"));
    }
    Ok(Parsed {
        value,
        duplicates: parser.duplicates,
    })
}

impl Json {
    /// オブジェクトのキーを引く。
    pub fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Self::Object(fields) => fields.iter().find(|(name, _)| name == key).map(|(_, v)| v),
            _ => None,
        }
    }

    /// オブジェクトに代入する（dict と同じく既存キーは位置を保つ）。オブジェクト以外は無視する。
    pub fn set(&mut self, key: &str, value: Json) {
        if let Self::Object(fields) = self {
            match fields.iter_mut().find(|(name, _)| name == key) {
                Some(field) => field.1 = value,
                None => fields.push((key.into(), value)),
            }
        }
    }

    /// オブジェクトからキーを除く。
    pub fn remove(&mut self, key: &str) {
        if let Self::Object(fields) = self {
            fields.retain(|(name, _)| name != key);
        }
    }

    /// 非有限の float を含むか（Python の project_v2 の finite 検査）。
    pub fn has_non_finite(&self) -> bool {
        match self {
            Self::Float(f) => !f.is_finite(),
            Self::Array(items) => items.iter().any(Self::has_non_finite),
            Self::Object(fields) => fields.iter().any(|(_, v)| v.has_non_finite()),
            _ => false,
        }
    }

    /// serde_json の値にする。json.dumps した文字列を serde_json で読んだ値と同じにする
    /// （i64・u64 に入らない整数は f64）。非有限の float は Null。
    pub fn to_value(&self) -> Value {
        match self {
            Self::Null => Value::Null,
            Self::Bool(b) => Value::Bool(*b),
            Self::Int(digits) => int_value(digits).unwrap_or_else(|| {
                digits
                    .parse::<f64>()
                    .ok()
                    .and_then(Number::from_f64)
                    .map_or(Value::Null, Value::Number)
            }),
            Self::Float(f) => Number::from_f64(*f).map_or(Value::Null, Value::Number),
            Self::Str(s) => Value::String(s.clone()),
            Self::Array(items) => Value::Array(items.iter().map(Self::to_value).collect()),
            Self::Object(fields) => Value::Object(
                fields
                    .iter()
                    .map(|(k, v)| (k.clone(), v.to_value()))
                    .collect::<Map<_, _>>(),
            ),
        }
    }

    /// json.dumps(value, ensure_ascii=False, allow_nan=False, indent=indent) と同じ文字列。
    /// indent が None なら区切りは ", " と ": "。非有限の float は Err（Python は ValueError）。
    pub fn dumps(&self, indent: Option<usize>) -> Result<String, &'static str> {
        let mut out = String::new();
        self.write(&mut out, indent, 0)?;
        Ok(out)
    }

    fn write(
        &self,
        out: &mut String,
        indent: Option<usize>,
        level: usize,
    ) -> Result<(), &'static str> {
        let newline = |out: &mut String, level: usize| {
            if let Some(width) = indent {
                out.push('\n');
                out.extend(std::iter::repeat_n(' ', width * level));
            }
        };
        let separator = if indent.is_some() { "," } else { ", " };
        match self {
            Self::Null => out.push_str("null"),
            Self::Bool(true) => out.push_str("true"),
            Self::Bool(false) => out.push_str("false"),
            Self::Int(digits) => out.push_str(digits),
            Self::Float(f) => out.push_str(&float_repr(*f)?),
            Self::Str(s) => write_string(out, s),
            Self::Array(items) => {
                if items.is_empty() {
                    out.push_str("[]");
                    return Ok(());
                }
                out.push('[');
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        out.push_str(separator);
                    }
                    newline(out, level + 1);
                    item.write(out, indent, level + 1)?;
                }
                newline(out, level);
                out.push(']');
            }
            Self::Object(fields) => {
                if fields.is_empty() {
                    out.push_str("{}");
                    return Ok(());
                }
                out.push('{');
                for (i, (key, value)) in fields.iter().enumerate() {
                    if i > 0 {
                        out.push_str(separator);
                    }
                    newline(out, level + 1);
                    write_string(out, key);
                    out.push_str(": ");
                    value.write(out, indent, level + 1)?;
                }
                newline(out, level);
                out.push('}');
            }
        }
        Ok(())
    }
}

fn int_value(digits: &str) -> Option<Value> {
    digits
        .parse::<i64>()
        .map(Value::from)
        .or_else(|_| digits.parse::<u64>().map(Value::from))
        .ok()
}

/// ensure_ascii=False の json.dumps と同じ文字列の書き方。
fn write_string(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// Python の float.__repr__（最短の往復表記。10 進の小数点位置が -4 以下か 16 より大きければ指数）。
pub fn float_repr(value: f64) -> Result<String, &'static str> {
    if !value.is_finite() {
        return Err("Out of range float values are not JSON compliant");
    }
    if value == 0.0 {
        return Ok(if value.is_sign_negative() {
            "-0.0"
        } else {
            "0.0"
        }
        .into());
    }
    // Rust の {:e} は最短の往復桁を "d.ddde±x" の形で返す。
    let scientific = format!("{:e}", value.abs());
    let (mantissa, exponent) = scientific.split_once('e').ok_or("float")?;
    let exponent: i32 = exponent.parse().map_err(|_| "float")?;
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let point = exponent + 1;
    let sign = if value < 0.0 { "-" } else { "" };
    let text = if point <= -4 || point > 16 {
        let mut mantissa = digits[..1].to_string();
        if digits.len() > 1 {
            mantissa.push('.');
            mantissa.push_str(&digits[1..]);
        }
        let e = point - 1;
        format!(
            "{mantissa}e{}{:02}",
            if e < 0 { '-' } else { '+' },
            e.unsigned_abs()
        )
    } else if point <= 0 {
        format!("0.{}{digits}", "0".repeat(point.unsigned_abs() as usize))
    } else {
        let point = point as usize;
        if digits.len() <= point {
            format!("{digits}{}.0", "0".repeat(point - digits.len()))
        } else {
            format!("{}.{}", &digits[..point], &digits[point..])
        }
    };
    Ok(format!("{sign}{text}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn float_repr_matches_python() {
        for (value, text) in [
            (1.0, "1.0"),
            (0.5, "0.5"),
            (-2.25, "-2.25"),
            (1e16, "1e+16"),
            (1.5e16, "1.5e+16"),
            (1e15, "1000000000000000.0"),
            (123456789012345678.0, "1.2345678901234568e+17"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1.25e-7, "1.25e-07"),
            (1e100, "1e+100"),
            (-0.0, "-0.0"),
            (0.1, "0.1"),
            (5e-324, "5e-324"),
            (1.7976931348623157e308, "1.7976931348623157e+308"),
        ] {
            assert_eq!(float_repr(value).unwrap(), text, "{value}");
        }
        assert!(float_repr(f64::NAN).is_err());
    }

    #[test]
    fn parse_python_rules() {
        let parsed = parse(" {\"a\": 1, \"b\": [1.0, -0, 1e400, NaN], \"a\": 2} ").unwrap();
        assert_eq!(parsed.duplicates, 1);
        let Json::Object(fields) = &parsed.value else {
            panic!()
        };
        assert_eq!(fields[0], ("a".into(), Json::Int("2".into())));
        assert!(parsed.value.has_non_finite());
        assert_eq!(
            parse("123456789012345678901234567890").unwrap().value,
            Json::Int("123456789012345678901234567890".into())
        );
        for bad in [
            "",
            "01",
            "1.",
            "[1,]",
            "{\"a\":1,}",
            "\"\t\"",
            "'a'",
            "\u{feff}{}",
            "[1] x",
            "\"\\ud800\"",
            "-",
        ] {
            assert!(parse(bad).is_err(), "{bad:?}");
        }
        assert_eq!(
            parse("\"\\ud83d\\ude00\\u00e9/\\/\"").unwrap().value,
            Json::Str("😀é//".into())
        );
    }

    #[test]
    fn dumps_python_format() {
        let value = parse(
            "{\"k\": [], \"o\": {}, \"s\": \"\\u0001\\n\\\"é\", \"l\": [1, true, null, 2.0]}",
        )
        .unwrap()
        .value;
        assert_eq!(
            value.dumps(Some(2)).unwrap(),
            "{\n  \"k\": [],\n  \"o\": {},\n  \"s\": \"\\u0001\\n\\\"é\",\n  \"l\": [\n    1,\n    true,\n    null,\n    2.0\n  ]\n}"
        );
        assert_eq!(
            value.dumps(None).unwrap(),
            "{\"k\": [], \"o\": {}, \"s\": \"\\u0001\\n\\\"é\", \"l\": [1, true, null, 2.0]}"
        );
    }
}
