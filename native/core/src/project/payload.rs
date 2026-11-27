//! v2 project.json の構造検証と、画像・GUI に依存しない読込計画。
use serde_json::json;
pub use serde_json::{Map, Value};
use std::collections::BTreeSet;

const FRAMES: i64 = 10_000;
const LAYERS: usize = 256;
const CELLS: usize = 100_000;

/// Python constants と同じ既定値。テスト等で変更された上限も入口から渡せる。
#[derive(Clone, Copy, Debug)]
pub struct PlanLimits {
    pub frames: i64,
    pub layers: usize,
    pub cells: usize,
}
impl Default for PlanLimits {
    fn default() -> Self {
        Self {
            frames: FRAMES,
            layers: LAYERS,
            cells: CELLS,
        }
    }
}

/// Python が画像を読み、Document を単純な代入で組み立てるための計画。
/// data は寸法・duration・track・slot、images は要求順の [path, paper, draft]。
/// track は display/cells/hints/slots。artwork は [image要求index, initial要求index, name]。
/// slots は [has_content, is_blank_key, number, pending, blank_start, local artwork,
/// legacy_kind, legacy_number, exposure, input_length, imported, ref, resolve_ref,
/// resolved有無, source_column, 元position配列index]。ref は [kind, number]。
/// hints は [number, has_content, is_blank_key, length, pending, 元drawing_states配列index]。
#[derive(Debug)]
pub struct V2Plan {
    pub data: Value,
    pub images: Vec<Value>,
}

/// 構造エラーと、その位置までに要求した画像（k = images.len()）。
#[derive(Debug)]
pub struct PlanError {
    pub images: Vec<Value>,
    pub width: i64,
    pub height: i64,
    pub mask_format: i64,
    pub reason: &'static str,
}

type R<T> = Result<T, &'static str>;
fn require(ok: bool) -> R<()> {
    if ok {
        Ok(())
    } else {
        Err("structure")
    }
}
fn integer(v: &Value, lo: i64, hi: i64) -> R<i64> {
    let n = v.as_i64().ok_or("integer")?;
    require(n >= lo && n <= hi)?;
    Ok(n)
}
fn boolean(v: &Value) -> R<bool> {
    v.as_bool().ok_or("boolean")
}
fn array(v: &Value) -> R<&Vec<Value>> {
    v.as_array().ok_or("array")
}
fn mapping(v: &Value) -> R<&Map<String, Value>> {
    v.as_object().ok_or("mapping")
}

fn get<'a>(v: &'a Value, key: &str, default: &'a Value) -> &'a Value {
    v.get(key).unwrap_or(default)
}
fn number(v: &Value, cells: &BTreeSet<i64>, frames: i64) -> R<i64> {
    let n = integer(v, 1, frames)?;
    require(cells.contains(&n))?;
    Ok(n)
}
fn optional(v: &Value, cells: &BTreeSet<i64>, frames: i64) -> R<Option<i64>> {
    if v.is_null() {
        Ok(None)
    } else {
        Ok(Some(number(v, cells, frames)?))
    }
}
fn reference(v: &Value, cells: &BTreeSet<i64>, frames: i64) -> R<Value> {
    if v.is_null() {
        Ok(json!(["EMPTY", null]))
    } else if v == "BLANK" {
        Ok(json!(["BLANK", null]))
    } else {
        Ok(json!(["CELL", number(v, cells, frames)?]))
    }
}
fn indexed(v: &Value, count: usize) -> R<Vec<Option<&Value>>> {
    let mut out = vec![None; count];
    for entry in array(v)? {
        mapping(entry)?;
        let c = integer(&entry["column"], 0, count as i64 - 1)? as usize;
        require(out[c].is_none())?;
        out[c] = Some(entry);
    }
    Ok(out)
}
/// PurePosixPath.parts と同じく空要素・`.` を除き、絶対パス・親参照を拒否する。
fn safe_path(path: &str) -> bool {
    !path.starts_with('/') && !path.contains(['\\', ':']) && !path.split('/').any(|p| p == "..")
}
fn png_path(v: &Value) -> R<&str> {
    let p = v.as_str().ok_or("image path")?;
    require(!p.is_empty() && safe_path(p))?;
    let last = p
        .split('/')
        .rev()
        .find(|p| !p.is_empty() && *p != ".")
        .unwrap_or("");
    // pathlib は `.png` 自体を拡張子とみなさない。
    let suffix = last
        .rsplit_once('.')
        .filter(|(stem, suffix)| !stem.is_empty() && !suffix.is_empty());
    require(suffix.is_some_and(|(_, s)| s.eq_ignore_ascii_case("png")))?;
    Ok(p)
}
struct Decoder {
    images: Vec<Value>,
    width: i64,
    height: i64,
    mask: i64,
    limits: PlanLimits,
}
impl Decoder {
    fn artwork(&mut self, e: &Value, paper: bool, draft: bool, optional: bool) -> R<Value> {
        require(e["cell_name"].is_null() || e["cell_name"].is_string())?;
        // 原画の印（#24）。無ければ false。
        let genga = boolean(get(e, "genga", &Value::Bool(false)))?;
        let mut request = |path: &Value| -> R<Value> {
            let path = png_path(path)?;
            let index = self.images.len();
            self.images.push(json!([path, paper, draft]));
            Ok(json!(index))
        };
        let image = if optional && e.get("image").is_none() {
            Value::Null
        } else {
            request(&e["image"])?
        };
        let initial = if e["initial"].is_null() {
            Value::Null
        } else {
            request(&e["initial"])?
        };
        Ok(json!([image, initial, e["cell_name"], genga]))
    }
    fn decode(&mut self, p: &Value, names: &[String]) -> R<Value> {
        self.mask = integer(get(p, "mask_format", &json!(1)), 1, self.limits.frames)?;
        mapping(&p["canvas"])?;
        self.width = integer(&p["canvas"]["width"], 1, 16384)?;
        self.height = integer(&p["canvas"]["height"], 1, 16384)?;
        let durations = array(&p["frame_durations"])?;
        let count = durations.len();
        require(count >= 1 && count as i64 <= self.limits.frames)?;
        for d in durations {
            integer(d, 1, self.limits.frames)?;
        }
        let tracks = array(&p["tracks"])?;
        require(
            !tracks.is_empty()
                && tracks.len() <= self.limits.layers
                && count
                    .checked_mul(tracks.len())
                    .is_some_and(|n| n <= self.limits.cells),
        )?;
        let empty = json!([]);
        let object = json!({});
        let mut total = 0usize;
        for t in tracks {
            mapping(t)?;
            total = total
                .checked_add(array(&t["cells"])?.len())
                .ok_or("artwork count")?;
            let compat = get(t, "compatibility", &object);
            mapping(compat)?;
            total = total
                .checked_add(array(get(compat, "local_states", &empty))?.len())
                .ok_or("artwork count")?;
        }
        require(total <= self.limits.cells)?;
        for n in names {
            require(safe_path(n))?;
        }
        let mut output = Vec::with_capacity(tracks.len());
        for t in tracks {
            let name = t.get("name");
            require(name.is_none_or(Value::is_string))?;
            let visible = boolean(get(t, "visible", &Value::Bool(true)))?;
            let paper = boolean(get(t, "is_paper", &Value::Bool(false)))?;
            let draft = boolean(get(t, "is_draft", &Value::Bool(false)))?;
            let filter = boolean(get(t, "color_filter_enabled", &Value::Bool(false)))?;
            let default_opacity = json!(1.0);
            let opacity = get(t, "opacity", &default_opacity);
            let opacity = opacity.as_f64().ok_or("opacity")?;
            require(opacity.is_finite())?;
            let opacity = if opacity <= 0.0 {
                0.0
            } else {
                opacity.min(1.0)
            };
            let rgb = &t["color_filter_rgb"];
            if !rgb.is_null() {
                let components = array(rgb)?;
                require(components.len() == 3)?;
                for c in components {
                    integer(c, 0, 255)?;
                }
            }
            // 連番として読み込んだ元のファイル（#89）。空の一覧は無しと同じ。
            let import_sources = match t.get("import_sources") {
                None | Some(Value::Null) => Value::Null,
                Some(sources) => {
                    let sources = array(sources)?;
                    require(sources.iter().all(Value::is_string))?;
                    if sources.is_empty() {
                        Value::Null
                    } else {
                        Value::Array(sources.clone())
                    }
                }
            };
            let mut cells = BTreeSet::new();
            let mut bank = Vec::new();
            for e in array(&t["cells"])? {
                mapping(e)?;
                let n = integer(&e["number"], 1, self.limits.frames)?;
                require(cells.insert(n))?;
                bank.push(json!([n, self.artwork(e, paper, draft, false)?]));
            }
            let sheet = array(&t["sheet"])?;
            require(sheet.len() == count)?;
            let refs: Vec<Value> = sheet
                .iter()
                .map(|v| reference(v, &cells, self.limits.frames))
                .collect::<R<_>>()?;
            let position_data = get(t, "positions", &empty);
            let positions = indexed(position_data, count)?;
            let mut position_indices = vec![None; count];
            for (i, e) in array(position_data)?.iter().enumerate() {
                position_indices[integer(&e["column"], 0, count as i64 - 1)? as usize] = Some(i);
            }
            let pending = indexed(get(t, "pending", &empty), count)?;
            let compat = get(t, "compatibility", &object);
            mapping(compat)?;
            let locals = indexed(get(compat, "local_states", &empty), count)?;
            let resolved = indexed(get(compat, "resolved_overrides", &empty), count)?;
            let lengths = indexed(get(compat, "length_overrides", &empty), count)?;
            let mut starts = vec![false; count];
            for (c, entry) in positions.iter().enumerate() {
                if let Some(e) = entry {
                    starts[c] = boolean(get(e, "blank_start", &Value::Bool(false)))?;
                    require(
                        !starts[c]
                            || sheet[c] == "BLANK"
                            || pending[c].is_some_and(|s| s["kind"] == "BLANK"),
                    )?;
                }
            }
            let mut ordinary = Vec::new();
            let mut c = 0;
            while c < count {
                let mut end = c + 1;
                while end < count && sheet[end] == sheet[c] && !(sheet[c] == "BLANK" && starts[end])
                {
                    end += 1;
                }
                if !sheet[c].is_null() && pending[c].is_none() {
                    ordinary.push(json!({"column":c,"kind":if sheet[c] == "BLANK" {"BLANK"} else {"CELL"},"number":if sheet[c] == "BLANK" {Value::Null} else {sheet[c].clone()},"length":end-c}));
                }
                c = end;
            }
            let ordinary = Value::Array(ordinary);
            let keys_data = get(compat, "placement_keys", &ordinary);
            let keys = indexed(keys_data, count)?;
            // 検証順は入力配列順（column 順ではない）。
            for e in array(keys_data)? {
                require(e["kind"] == "CELL" || e["kind"] == "BLANK")?;
                if e["kind"] == "BLANK" {
                    require(e["number"].is_null())?;
                } else {
                    number(&e["number"], &cells, self.limits.frames)?;
                }
                integer(&e["length"], 1, self.limits.frames)?;
            }
            let mut hints = Vec::new();
            let mut used = BTreeSet::new();
            for (i, e) in array(get(t, "drawing_states", &empty))?.iter().enumerate() {
                mapping(e)?;
                let n = number(&e["number"], &cells, self.limits.frames)?;
                require(used.insert(n))?;
                require(e["tween_member"].is_null() || e["tween_member"].is_string())?;
                hints.push(json!([
                    n,
                    boolean(get(e, "has_content", &Value::Bool(true)))?,
                    boolean(get(e, "is_blank_key", &Value::Bool(false)))?,
                    integer(get(e, "length", &json!(1)), 1, self.limits.frames)?,
                    boolean(get(e, "pending", &Value::Bool(false)))?,
                    i
                ]));
            }
            let mut slots = Vec::with_capacity(count);
            for c in 0..count {
                let r = &refs[c];
                let state = pending[c];
                let mut kind = r[0].as_str().ok_or("reference")?;
                let mut n = r[1].clone();
                if let Some(s) = state {
                    kind = s["kind"].as_str().ok_or("kind")?;
                    require(["CELL", "BLANK", "EMPTY"].contains(&kind))?;
                    n = json!(optional(&s["number"], &cells, self.limits.frames)?);
                    integer(get(s, "length", &json!(1)), 1, self.limits.frames)?;
                }
                let local = locals[c];
                if let Some(l) = local {
                    require(!((kind == "CELL" || state.is_some()) && !n.is_null()))?;
                    n = l["retained_number"].clone();
                    if !n.is_null() {
                        integer(&n, 1, self.limits.frames)?;
                    }
                }
                let artwork = if let Some(l) = local {
                    self.artwork(l, paper, draft, true)?
                } else {
                    Value::Null
                };
                let pos = positions[c].unwrap_or(&object);
                require(pos["tween_member"].is_null() || pos["tween_member"].is_string())?;
                let key = keys[c];
                let legacy_kind = state
                    .or(key)
                    .map(|e| &e["kind"])
                    .unwrap_or(&Value::Null)
                    .as_str()
                    .unwrap_or("EMPTY");
                let legacy_number = if legacy_kind != "CELL" {
                    n.clone()
                } else {
                    state
                        .or(key)
                        .map(|e| e["number"].clone())
                        .unwrap_or(n.clone())
                };
                let mut exposure = state
                    .or(key)
                    .map(|e| get(e, "length", &Value::Null))
                    .filter(|v| !v.is_null())
                    .cloned()
                    .unwrap_or(json!(1));
                let mut imported = Value::Null;
                let mut input = Value::Null;
                if let Some(l) = lengths[c] {
                    exposure = json!(integer(&l["length"], 1, self.limits.frames)?);
                    let g = &l["guard"];
                    mapping(g)?;
                    require([json!("CELL"), json!("BLANK"), json!("EMPTY")].contains(&g["kind"]))?;
                    optional(&g["number"], &cells, self.limits.frames)?;
                    for f in ["pending", "blank_start", "imported"] {
                        boolean(&g[f])?;
                    }
                    require(
                        g["kind"] == r[0]
                            && g["number"] == r[1]
                            && g["pending"] == state.is_some()
                            && g["blank_start"] == starts[c],
                    )?;
                    imported = g["imported"].clone();
                    input = json!(integer(
                        get(l, "input_length", &l["length"]),
                        1,
                        self.limits.frames
                    )?);
                }
                let mut resolve_ref = r.clone();
                let mut source = Value::Null;
                if let Some(o) = resolved[c] {
                    require(o.get("value").is_some() && o.get("source_column").is_some())?;
                    optional(&o["value"], &cells, self.limits.frames)?;
                    source = o["source_column"].clone();
                    require(o["value"].is_null() == source.is_null())?;
                    if !source.is_null() {
                        integer(&source, 0, count as i64 - 1)?;
                    }
                    resolve_ref = reference(&o["value"], &cells, self.limits.frames)?;
                }
                slots.push(json!([
                    kind == "CELL",
                    kind == "BLANK",
                    n,
                    state.is_some(),
                    starts[c],
                    artwork,
                    legacy_kind,
                    legacy_number,
                    exposure,
                    input,
                    imported,
                    r,
                    resolve_ref,
                    resolved[c].is_some(),
                    source,
                    position_indices[c]
                ]));
            }
            for e in resolved.iter().flatten() {
                if let Some(source) = e["source_column"].as_u64() {
                    let s = &slots[source as usize];
                    require(s[2] == e["value"] && (s[0] == true || s[3] == true))?;
                }
            }
            output.push(json!({"display":[name,visible,opacity,paper,draft,filter,rgb],"import_sources":import_sources,"cells":bank,"hints":hints,"slots":slots}));
        }
        let current = integer(get(p, "current_frame", &json!(0)), 0, self.limits.frames)?
            .min(count as i64 - 1);
        let active = integer(
            get(p, "active_layer_index", &json!(0)),
            0,
            self.limits.frames,
        )?
        .min(tracks.len() as i64 - 1);
        Ok(
            json!({"width":self.width,"height":self.height,"mask_format":self.mask,"durations":durations,"tracks":output,"current_frame":current,"active_layer_index":active}),
        )
    }
}
/// `_decode_payload_python` の require から各 slot の resolve 検証までを実行する。
/// 成功時は全ての構造計算、失敗時は理由とエラー前の画像要求を返す。
/// 定数上限は Python constants と同値。ファイルや画像の復号は行わない。
pub fn decode_v2_plan(payload: &Value, archive_names: &[String]) -> Result<V2Plan, PlanError> {
    decode_v2_plan_with_limits(payload, archive_names, PlanLimits::default())
}

/// 上限を指定して同じ計画を返す。Python 側の動的な上限も元と同じ順に検証する。
/// 構造が不正・上限超過の場合は画像要求済みの位置を含む Err を返す。
pub fn decode_v2_plan_with_limits(
    payload: &Value,
    archive_names: &[String],
    limits: PlanLimits,
) -> Result<V2Plan, PlanError> {
    let mut d = Decoder {
        images: Vec::new(),
        width: 0,
        height: 0,
        mask: 1,
        limits,
    };
    match d.decode(payload, archive_names) {
        Ok(data) => Ok(V2Plan {
            data,
            images: d.images,
        }),
        Err(reason) => Err(PlanError {
            images: d.images,
            width: d.width,
            height: d.height,
            mask_format: d.mask,
            reason,
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn small() -> Value {
        json!({"canvas":{"width":1,"height":2},"frame_durations":[1,2,3],
            "tracks":[{"cells":[{"number":1,"image":"a.png"}],
                "sheet":[1,"BLANK",null],"positions":[{"column":1,"blank_start":true}]}]})
    }
    #[test]
    fn plan_values_and_defaults() {
        let p = decode_v2_plan(&small(), &[]).unwrap();
        assert_eq!(p.images, vec![json!(["a.png", false, false])]);
        assert_eq!(p.data["width"], 1);
        assert_eq!(p.data["height"], 2);
        assert_eq!(p.data["mask_format"], 1);
        let t = &p.data["tracks"][0];
        assert_eq!(
            t["display"],
            json!([null, true, 1.0, false, false, false, null])
        );
        assert_eq!(t["cells"], json!([[1, [0, null, null, false]]]));
        assert_eq!(t["import_sources"], Value::Null);
        let s = &t["slots"];
        assert_eq!(s[0][0], true);
        assert_eq!(s[0][2], 1);
        assert_eq!(s[0][6], "CELL");
        assert_eq!(s[0][11], json!(["CELL", 1]));
        assert_eq!(s[1][1], true);
        assert_eq!(s[1][4], true);
        assert_eq!(s[1][15], 0);
        assert_eq!(s[2][6], "EMPTY");
        assert_eq!(p.data["current_frame"], 0);
    }
    #[test]
    fn errors_count_only_valid_requests() {
        let mut p = small();
        p["canvas"]["width"] = json!(true);
        let e = decode_v2_plan(&p, &[]).unwrap_err();
        assert_eq!(e.reason, "integer");
        assert_eq!(e.images.len(), 0);
        p = small();
        p["tracks"][0]["sheet"] = json!([]);
        assert_eq!(decode_v2_plan(&p, &[]).unwrap_err().images.len(), 1);
        p = small();
        p["tracks"][0]["cells"][0]["initial"] = json!("bad.jpg");
        let e = decode_v2_plan(&p, &[]).unwrap_err();
        assert_eq!(e.images.len(), 1);
        assert_eq!(e.reason, "structure");
        assert_eq!(
            decode_v2_plan(&small(), &["../bad".into()])
                .unwrap_err()
                .images
                .len(),
            0
        );
    }
    #[test]
    fn pending_local_lengths_and_resolved() {
        let mut p = small();
        let t = &mut p["tracks"][0];
        t["pending"] = json!([{"column":2,"number":1,"kind":"CELL","length":5}]);
        t["compatibility"] = json!({
            "local_states":[{"column":1,"retained_number":7,"image":"local.png"}],
            "length_overrides":[{"column":0,"length":4,"input_length":9,
                "guard":{"kind":"CELL","number":1,"pending":false,"blank_start":false,"imported":true}}],
            "resolved_overrides":[{"column":1,"value":1,"source_column":2}]});
        let out = decode_v2_plan(&p, &[]).unwrap();
        assert_eq!(out.images.len(), 2);
        let s = &out.data["tracks"][0]["slots"];
        assert_eq!(s[0][8], 4);
        assert_eq!(s[0][9], 9);
        assert_eq!(s[0][10], true);
        assert_eq!(s[1][2], 7);
        assert_eq!(s[1][5], json!([1, null, null, false]));
        assert_eq!(s[1][12], json!(["CELL", 1]));
        assert_eq!(s[1][14], 2);
        assert_eq!(s[2][3], true);
        p["tracks"][0]["compatibility"]["resolved_overrides"][0]["source_column"] = json!(1);
        assert_eq!(decode_v2_plan(&p, &[]).unwrap_err().images.len(), 2);
    }
    #[test]
    fn custom_limits_and_unknown_metadata_keys() {
        let p = small();
        for limits in [
            PlanLimits {
                frames: 2,
                ..PlanLimits::default()
            },
            PlanLimits {
                layers: 0,
                ..PlanLimits::default()
            },
            PlanLimits {
                cells: 2,
                ..PlanLimits::default()
            },
        ] {
            assert_eq!(
                decode_v2_plan_with_limits(&p, &[], limits)
                    .unwrap_err()
                    .images
                    .len(),
                0
            );
        }
        let mut p = small();
        p["tracks"][0]["compatibility"] = json!({"$invalid":true,"$large_integer":1});
        assert!(decode_v2_plan(&p, &[]).is_ok());
    }
    // 原画の印（#24）は真偽値だけ、読み込み元（#89）は文字列の一覧だけを受ける。空の一覧は無し。
    #[test]
    fn genga_marks_and_import_sources() {
        let mut p = small();
        p["tracks"][0]["cells"][0]["genga"] = json!(true);
        p["tracks"][0]["import_sources"] = json!(["a/0001.png"]);
        let t = &decode_v2_plan(&p, &[]).unwrap().data["tracks"][0];
        assert_eq!(t["cells"], json!([[1, [0, null, null, true]]]));
        assert_eq!(t["import_sources"], json!(["a/0001.png"]));
        p["tracks"][0]["import_sources"] = json!([]);
        let t = &decode_v2_plan(&p, &[]).unwrap().data["tracks"][0];
        assert_eq!(t["import_sources"], Value::Null);
        for bad in [json!("a.png"), json!([1]), json!({})] {
            let mut p = small();
            p["tracks"][0]["import_sources"] = bad;
            assert!(decode_v2_plan(&p, &[]).is_err());
        }
        for bad in [Value::Null, json!(1), json!("yes")] {
            let mut p = small();
            p["tracks"][0]["cells"][0]["genga"] = bad;
            // 印の検証は画像の要求より先。
            assert!(decode_v2_plan(&p, &[]).unwrap_err().images.is_empty());
        }
    }
    #[test]
    fn malformed_values_never_panic() {
        for v in [
            Value::Null,
            json!(true),
            json!(0),
            json!("bad"),
            json!([]),
            json!({}),
        ] {
            assert!(decode_v2_plan(&v, &[]).is_err());
            for key in ["canvas", "frame_durations", "tracks"] {
                let mut p = small();
                p[key] = v.clone();
                assert!(decode_v2_plan(&p, &[]).is_err());
            }
        }
    }
    #[test]
    fn pathlib_suffix_and_parts() {
        for p in ["a//b.PNG/", "./a.png", "a.png/.", "..png"] {
            assert!(png_path(&json!(p)).is_ok(), "{p}");
        }
        for p in [".png", "../a.png", "/a.png", "a\\b.png", "a:b.png", ""] {
            assert!(png_path(&json!(p)).is_err(), "{p}");
        }
    }
}
