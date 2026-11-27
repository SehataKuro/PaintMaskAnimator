use serde_json::Value;

use super::v2::Projection;
use crate::Error;

const GENERATED: &[&str] = &[
    "format",
    "format_version",
    "mask_format",
    "canvas",
    "frame_durations",
    "tracks",
];

fn field_order(field: &str) -> &[&str] {
    match field {
        "canvas" => &["width", "height"],
        "tracks" => &[
            "name",
            "visible",
            "opacity",
            "is_paper",
            "is_draft",
            "color_filter_enabled",
            "color_filter_rgb",
            "import_sources",
            "cells",
            "sheet",
            "positions",
            "pending",
            "drawing_states",
            "compatibility",
        ],
        "cells" => &["number", "image", "initial", "cell_name", "genga"],
        "positions" => &["column", "blank_start", "tween", "tween_member"],
        "pending" => &["column", "number", "kind", "length"],
        "drawing_states" => &[
            "number",
            "has_content",
            "is_blank_key",
            "length",
            "pending",
            "tween",
            "tween_member",
        ],
        "compatibility" => &[
            "placement_keys",
            "length_overrides",
            "resolved_overrides",
            "local_states",
        ],
        "placement_keys" => &["column", "kind", "number", "length"],
        "length_overrides" => &["column", "length", "guard", "input_length"],
        "guard" => &["kind", "number", "pending", "blank_start", "imported"],
        "resolved_overrides" => &["column", "value", "source_column"],
        "local_states" => &[
            "column",
            "retained_number",
            "image",
            "initial",
            "cell_name",
            "genga",
        ],
        "tween" => &[
            "id",
            "length",
            "mode",
            "reverse",
            "start_points",
            "final_points",
            "mesh_cols",
            "mesh_rows",
            "mesh_reference_points",
        ],
        _ => &[],
    }
}

fn comma(out: &mut String, open: char) {
    if !out.ends_with(open) {
        out.push(',');
    }
}

fn key(out: &mut String, name: &str) {
    comma(out, '{');
    out.push_str(&Value::String(name.into()).to_string());
    out.push(':');
}

fn write_value(out: &mut String, field: &str, value: &Value) {
    match value {
        Value::Array(items) => {
            out.push('[');
            for item in items {
                comma(out, '[');
                write_value(out, field, item);
            }
            out.push(']');
        }
        Value::Object(fields) => {
            out.push('{');
            let order = field_order(field);
            for (name, value) in order
                .iter()
                .filter_map(|&name| fields.get(name).map(|value| (name, value)))
                .chain(
                    fields
                        .iter()
                        .filter(|(name, _)| !order.contains(&name.as_str()))
                        .map(|(name, value)| (name.as_str(), value)),
                )
            {
                key(out, name);
                write_value(out, name, value);
            }
            out.push('}');
        }
        _ => out.push_str(&value.to_string()),
    }
}

fn invalid() -> Error {
    Error::Invalid("順序付き v2 メタデータが不正です".into())
}

fn next_value(text: &str) -> Result<(Value, usize), Error> {
    let mut stream = serde_json::Deserializer::from_str(text).into_iter::<Value>();
    let value = stream.next().ok_or_else(invalid)?.map_err(|_| invalid())?;
    Ok((value, stream.byte_offset()))
}

impl Projection {
    /// Python build_payload のキー順で project.json を返す。
    /// 元と同じメタデータを JSON テキストで渡すと、未知の属性や入れ子の順序も保持する。
    pub fn ordered_json(&self, metadata_json: &str) -> Result<String, Error> {
        let metadata_json = metadata_json.trim();
        let mut rest = metadata_json
            .strip_prefix('{')
            .and_then(|s| s.strip_suffix('}'))
            .ok_or_else(invalid)?
            .trim();
        let mut out = String::from("{");
        let mut emitted = std::collections::BTreeSet::new();
        while !rest.is_empty() {
            let (name, size) = next_value(rest)?;
            let name = name.as_str().ok_or_else(invalid)?;
            let value_text = rest[size..]
                .trim_start()
                .strip_prefix(':')
                .ok_or_else(invalid)?
                .trim_start();
            let (_, size) = next_value(value_text)?;
            if let Some(value) = self.payload.get(name) {
                if !emitted.insert(name.to_owned()) {
                    return Err(invalid());
                }
                key(&mut out, name);
                if GENERATED.contains(&name) {
                    write_value(&mut out, name, value);
                } else {
                    out.push_str(&value_text[..size]);
                }
            }
            rest = value_text[size..].trim_start();
            if !rest.is_empty() {
                rest = rest.strip_prefix(',').ok_or_else(invalid)?.trim_start();
                if rest.is_empty() {
                    return Err(invalid());
                }
            }
        }
        for &name in GENERATED {
            if !emitted.contains(name) {
                key(&mut out, name);
                write_value(&mut out, name, &self.payload[name]);
            }
        }
        out.push('}');
        Ok(out)
    }
}
