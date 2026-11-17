//! Python の値を投影し、純 Rust の v2 計画を Python に返す入口。
use pma_core::project::payload::{self, Value};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use std::collections::HashMap;

// どの既知フィールドにも有効でない型・値。変換時には例外を出さず、
// core が元の検証位置で拒否する（それ以前の画像要求を失わない）。
fn invalid() -> Value {
    Value::Array(vec![Value::Object(payload::Map::new())])
}
// Python の str は単独 surrogate も許す。JSON Value の String に投影する際は
// ASCII の path 検証を変えない可逆な符号にし、返却時に元の Python str に戻す。
const ESCAPE: char = '\u{f0000}';
type Strings = HashMap<String, Py<PyAny>>;
fn project_string(v: &Bound<'_, PyAny>, strings: &mut Strings) -> PyResult<String> {
    if let Ok(s) = v.extract::<String>() {
        if !s.contains(ESCAPE) {
            return Ok(s);
        }
        let encoded = s.replace(ESCAPE, &format!("{ESCAPE}{ESCAPE}"));
        strings.insert(encoded.clone(), v.clone().unbind());
        return Ok(encoded);
    }
    let encoded = v.call_method1("encode", ("utf-8", "surrogatepass"))?;
    let bytes = encoded.cast::<PyBytes>()?.as_bytes();
    let escape_bytes = [0xf3, 0xb0, 0x80, 0x80];
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i..].starts_with(&escape_bytes) {
            out.extend_from_slice(&escape_bytes);
            out.extend_from_slice(&escape_bytes);
            i += 4;
        } else if i + 2 < bytes.len()
            && bytes[i] == 0xed
            && bytes[i + 1] >= 0xa0
            && bytes[i + 1] <= 0xbf
        {
            let code = (u32::from(bytes[i + 1] & 0x3f) << 6) | u32::from(bytes[i + 2] & 0x3f);
            let replacement = char::from_u32(0xe000 + code - 0x800)
                .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("surrogate"))?;
            out.extend_from_slice(&escape_bytes);
            let mut buffer = [0; 4];
            out.extend_from_slice(replacement.encode_utf8(&mut buffer).as_bytes());
            i += 3;
        } else {
            out.push(bytes[i]);
            i += 1;
        }
    }
    let result =
        String::from_utf8(out).map_err(|_| pyo3::exceptions::PyValueError::new_err("string"))?;
    strings.insert(result.clone(), v.clone().unbind());
    Ok(result)
}
const KEYS: &[&str] = &[
    "mask_format",
    "canvas",
    "width",
    "height",
    "frame_durations",
    "tracks",
    "cells",
    "compatibility",
    "local_states",
    "name",
    "visible",
    "opacity",
    "is_paper",
    "is_draft",
    "color_filter_enabled",
    "color_filter_rgb",
    "number",
    "image",
    "initial",
    "cell_name",
    "sheet",
    "positions",
    "pending",
    "drawing_states",
    "resolved_overrides",
    "length_overrides",
    "placement_keys",
    "column",
    "blank_start",
    "kind",
    "length",
    "has_content",
    "is_blank_key",
    "tween_member",
    "retained_number",
    "guard",
    "imported",
    "input_length",
    "value",
    "source_column",
    "current_frame",
    "active_layer_index",
];
fn convert(v: &Bound<'_, PyAny>, depth: usize, field: &str, strings: &mut Strings) -> Value {
    if depth > 32 {
        return invalid();
    }
    if v.is_none() {
        return Value::Null;
    }
    if v.is_exact_instance_of::<PyBool>() {
        return Value::Bool(v.extract().unwrap_or(false));
    }
    if v.is_exact_instance_of::<PyInt>() {
        if let Ok(n) = v.extract::<i64>() {
            return Value::from(n);
        }
        if let Ok(n) = v.extract::<u64>() {
            return Value::from(n);
        }
        // 大きな整数も opacity だけは Python の math.isfinite と clamp を通る。
        if field == "opacity" {
            if let Ok(n) = v.extract::<f64>() {
                if n.is_finite() {
                    return Value::from(n);
                }
            }
        }
        return invalid();
    }
    if v.is_exact_instance_of::<PyFloat>() {
        return v
            .extract::<f64>()
            .ok()
            .filter(|n| n.is_finite())
            .map(Value::from)
            .unwrap_or_else(invalid);
    }
    if v.is_instance_of::<PyString>() {
        return project_string(v, strings)
            .map(Value::String)
            .unwrap_or_else(|_| invalid());
    }
    if let Ok(d) = v.cast::<PyDict>() {
        let mut m = payload::Map::new();
        for (k, child) in d.iter() {
            if !k.is_instance_of::<PyString>() {
                return invalid();
            }
            let Ok(k) = project_string(&k, strings) else {
                return invalid();
            };
            if KEYS.contains(&k.as_str()) {
                m.insert(k.clone(), convert(&child, depth + 1, &k, strings));
            }
        }
        return Value::Object(m);
    }
    if let Ok(a) = v.cast::<PyList>() {
        return Value::Array(
            a.iter()
                .map(|x| convert(&x, depth + 1, "", strings))
                .collect(),
        );
    }
    if let Ok(a) = v.cast::<PyTuple>() {
        // JSON Array への変換前に、従来 list 限定だった構造フィールドを区別する。
        // RGB などそれ以外の tuple は通常の Array にする。
        if [
            "frame_durations",
            "tracks",
            "cells",
            "sheet",
            "positions",
            "pending",
            "drawing_states",
            "local_states",
            "resolved_overrides",
            "length_overrides",
            "placement_keys",
        ]
        .contains(&field)
        {
            return invalid();
        }
        return Value::Array(
            a.iter()
                .map(|x| convert(&x, depth + 1, "", strings))
                .collect(),
        );
    }
    invalid()
}
fn to_python(py: Python<'_>, v: &Value, strings: &Strings) -> PyResult<Py<PyAny>> {
    Ok(match v {
        Value::Null => py.None(),
        Value::Bool(b) => b.into_pyobject(py)?.to_owned().into_any().unbind(),
        Value::Number(n) => {
            if let Some(n) = n.as_i64() {
                n.into_pyobject(py)?.into_any().unbind()
            } else if let Some(n) = n.as_u64() {
                n.into_pyobject(py)?.into_any().unbind()
            } else {
                n.as_f64()
                    .unwrap_or(0.0)
                    .into_pyobject(py)?
                    .into_any()
                    .unbind()
            }
        }
        Value::String(s) => match strings.get(s) {
            Some(original) => original.clone_ref(py),
            None => s.into_pyobject(py)?.into_any().unbind(),
        },
        Value::Array(a) => {
            let items = a
                .iter()
                .map(|v| to_python(py, v, strings))
                .collect::<PyResult<Vec<_>>>()?;
            PyList::new(py, items)?.into_any().unbind()
        }
        Value::Object(m) => {
            let out = PyDict::new(py);
            for (k, v) in m {
                out.set_item(k, to_python(py, v, strings)?)?;
            }
            out.into_any().unbind()
        }
    })
}
/// v2 の検証結果と、成功・失敗位置までの画像要求を返す。
#[pyfunction]
#[pyo3(signature = (payload, archive_names, limits=None))]
pub fn decode_v2_plan(
    py: Python<'_>,
    payload: &Bound<'_, PyDict>,
    archive_names: Vec<Py<PyString>>,
    limits: Option<(i64, usize, usize)>,
) -> PyResult<Py<PyAny>> {
    let mut strings = Strings::new();
    let input = convert(payload.as_any(), 0, "", &mut strings);
    let archive_names = archive_names
        .iter()
        .map(|n| project_string(n.bind(py).as_any(), &mut strings))
        .collect::<PyResult<Vec<_>>>()?;
    // 変換と出力は GIL 下、純粋な検証だけ GIL を解放する。
    let limits = limits
        .map(|(frames, layers, cells)| payload::PlanLimits {
            frames,
            layers,
            cells,
        })
        .unwrap_or_default();
    let result = py.detach(|| payload::decode_v2_plan_with_limits(&input, &archive_names, limits));
    let (data, images, error) = match result {
        Ok(p) => (p.data, p.images, Value::Null),
        Err(e) => {
            let mut m = payload::Map::new();
            m.insert("width".into(), Value::from(e.width));
            m.insert("height".into(), Value::from(e.height));
            m.insert("mask_format".into(), Value::from(e.mask_format));
            (Value::Object(m), e.images, Value::String(e.reason.into()))
        }
    };
    let values = [
        to_python(py, &data, &strings)?,
        to_python(py, &Value::Array(images), &strings)?,
        to_python(py, &error, &strings)?,
    ];
    Ok(PyTuple::new(py, values)?.into_any().unbind())
}
