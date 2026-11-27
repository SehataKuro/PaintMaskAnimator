"""Compare pma-core's used-colour panel calculations (used_colors) with the Python/Qt app.

0.9.0 の画面（app/src/colors.rs）が使う計算を、今の Qt 版と同じ入力で比べる。

- 使用色を数える（main_window_used_color._extract）
- 統合・色置換・色削除の画素の置き換え（apply_palette_replacements。白は透明）
- 「色相・彩度・明度」のずらし（hsv_adjust_dialog.shifted_mapping。QColor の HSV）
- 置換色のファイル（replacement_sets.to_data / from_data）
- カラーチャート（color_chart.normalize / merge / move / assign_tag / read_pmag）
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from paintmaskanimator import replacement_sets
from paintmaskanimator.color_chart import (
    assign_chart_tag,
    merge_color_charts,
    move_chart_color,
    normalize_color_chart,
    read_pmag,
)
from paintmaskanimator.hsv_adjust_dialog import shifted_mapping
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.pma_image import image_rgba
import pma_native as native

PALETTE = [(255, 255, 255), (20, 20, 20), (230, 40, 60), (40, 200, 90), (30, 90, 220), (128, 128, 128)]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def window(qapp, tmp_path_factory):
    config = tmp_path_factory.mktemp("config")
    previous = {key: os.environ.get(key) for key in ("APPDATA", "XDG_CONFIG_HOME")}
    os.environ["APPDATA"] = str(config)
    os.environ["XDG_CONFIG_HOME"] = str(config)
    window = MainWindow()
    window.canvas.ensure_editable_key()
    try:
        yield window
    finally:
        window.deleteLater()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _size(canvas):
    image = canvas.active_layer.image
    return image.width, image.height


def _picture(rng, width, height, colors):
    """使用色だけの絵（透明は 0,0,0,0。乗算済みとの往復で値が変わらないように）。"""
    choice = rng.integers(0, len(colors), (height, width))
    rgb = np.asarray(colors, dtype=np.uint8)[choice]
    alpha = np.where(rng.random((height, width)) < 0.25, 0, 255).astype(np.uint8)
    rgb[alpha == 0] = 0
    return np.ascontiguousarray(np.concatenate([rgb, alpha[..., None]], axis=2))


def _set_layer(canvas, rgba):
    height, width = rgba.shape[:2]
    canvas.clear_selection()
    canvas._write_rgba_patch(np.ascontiguousarray(rgba), QRect(0, 0, width, height))


def _layer_rgba(canvas):
    rgba = image_rgba(canvas.active_layer.image)
    assert rgba is not None
    return np.ascontiguousarray(rgba)


def _same_pixels(actual, expected):
    """アルファは全部、色は見えている画素だけ比べる（透明の画素の色は Qt が 0 にする）。"""
    assert np.array_equal(actual[..., 3], expected[..., 3])
    visible = expected[..., 3] != 0
    assert np.array_equal(actual[..., :3][visible], expected[..., :3][visible])


# ---- 使用色を数える ----------------------------------------------------------


@pytest.mark.parametrize("count", [3, 40, 140])
def test_used_colors_scan_matches_qt(window, count):
    rng = np.random.default_rng(count)
    colors = [tuple(int(v) for v in rng.integers(0, 256, 3)) for _ in range(count)]
    width, height = _size(window.canvas)
    _set_layer(window.canvas, _picture(rng, width, height, colors))
    image = window.canvas.active_layer.image
    expected = window.used_color._extract(image, 101)
    actual = native.used_colors_image_colors(_layer_rgba(window.canvas).tobytes(), 101)
    assert [tuple(c) for c in actual] == expected


# ---- 統合・色置換・色削除 ------------------------------------------------------


@pytest.mark.parametrize("seed", range(4))
def test_remap_matches_qt_replacements(window, seed):
    rng = np.random.default_rng(100 + seed)
    canvas = window.canvas
    width, height = _size(canvas)
    before = _picture(rng, width, height, PALETTE)
    _set_layer(canvas, before)
    sources = [c for c in PALETTE if rng.random() < 0.6] or [PALETTE[1]]
    mapping = {}
    for source in sources:
        # 白（消しゴム）へ・同じ色へ・別の使用色へ・新しい色へ。
        roll = rng.random()
        if roll < 0.25:
            mapping[source] = (255, 255, 255)
        elif roll < 0.35:
            mapping[source] = source
        elif roll < 0.6:
            mapping[source] = PALETTE[int(rng.integers(0, len(PALETTE)))]
        else:
            mapping[source] = tuple(int(v) for v in rng.integers(0, 256, 3))
    expected_bytes, count = native.used_colors_remap_image(
        before.tobytes(), width, height, list(mapping.items()))
    expected = np.frombuffer(expected_bytes, dtype=np.uint8).reshape(height, width, 4)
    window.used_color.apply_palette_replacements(mapping, "replace")
    _same_pixels(_layer_rgba(canvas), expected)
    changed = (expected != before).any(axis=2).sum()
    assert count >= changed


def test_remap_inside_mask_only():
    rng = np.random.default_rng(7)
    width, height = 32, 24
    before = _picture(rng, width, height, PALETTE)
    mask = (rng.random((height, width)) < 0.5).astype(np.uint8)
    mapping = [((230, 40, 60), (1, 2, 3)), ((20, 20, 20), (255, 255, 255))]
    data, _count = native.used_colors_remap_image(
        before.tobytes(), width, height, mapping, mask.tobytes())
    after = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 4)
    expected = before.copy()
    for (src, dst) in mapping:
        hit = (before[..., :3] == src).all(axis=2) & (before[..., 3] != 0) & mask.astype(bool)
        expected[hit, :3] = dst
        if dst == (255, 255, 255):
            expected[hit, 3] = 0
    assert np.array_equal(after, expected)


# ---- 色相・彩度・明度 ----------------------------------------------------------


def test_hsv_shift_matches_qcolor(qapp):
    rng = np.random.default_rng(3)
    rgbs = [tuple(int(v) for v in rng.integers(0, 256, 3)) for _ in range(3000)]
    rgbs += [(0, 0, 0), (255, 255, 255), (128, 128, 128), (255, 0, 0), (1, 1, 2)]
    shifts = [(0, 0, 0), (180, 0, 0), (-180, 0, 0), (37, -20, 15), (-90, 100, -100), (12, 55, 0), (0, -100, 100)]
    shifts += [tuple(int(v) for v in (rng.integers(-180, 181), rng.integers(-100, 101), rng.integers(-100, 101))) for _ in range(20)]
    for hue, saturation, value in shifts:
        expected = shifted_mapping(rgbs, hue, saturation, value)
        actual = {tuple(s): tuple(d) for s, d in native.used_colors_shifted_mapping(rgbs, hue, saturation, value)}
        assert actual == expected, (hue, saturation, value)


# ---- 置換色のファイル（.pmarep） ----------------------------------------------


def test_replacement_set_round_trip_matches_python():
    mapping = {(10, 20, 30): (40, 50, 60), (230, 40, 60): (255, 255, 255), (1, 2, 3): (3, 2, 1)}
    # pma-core は組を元の色の順に持つ（Qt は登録した順）。中身は同じで、並びだけ色の順になる。
    expected = replacement_sets.to_data(dict(sorted(mapping.items())))
    actual = json.loads(native.replacement_set_to_data(list(mapping.items())))
    assert actual == expected
    data = {
        "format": "paintmaskanimator.replacements",
        "version": 1,
        "replacements": [
            {"from": "#0a141e", "to": "#28323c"},
            {"from": "#FFFFFF", "to": "#000000"},
            {"from": "#123456", "to": "#123456"},
            {"from": "bad", "to": "#000000"},
            {"to": "#000000"},
            "x",
            {"from": "e62a3c", "to": "#010203", "note": "future key"},
        ],
    }
    expected = replacement_sets.from_data(data)
    actual = {tuple(s): tuple(d) for s, d in native.replacement_set_from_data(json.dumps(data))}
    assert actual == expected
    for bad in ([], {"replacements": 3}, {}):
        with pytest.raises(ValueError):
            replacement_sets.from_data(bad)
        with pytest.raises(ValueError):
            native.replacement_set_from_data(json.dumps(bad))


# ---- カラーチャート ------------------------------------------------------------


def _hex(rng):
    return "#" + "".join(f"{int(v):02X}" for v in rng.integers(0, 256, 3))


def _random_chart(rng, tags=("Base", "Hi", "Shadow", "Line", "")):
    tiles = []
    for index in range(int(rng.integers(0, 7))):
        placeholder = rng.random() < 0.15
        parent = None if placeholder else _hex(rng)
        children = [{"color": _hex(rng), "tag": str(rng.choice(tags))} for _ in range(int(rng.integers(0, 5)))]
        if children and rng.random() < 0.2:
            children.append(dict(children[0]))  # 重なった子は1つにする
        if parent and children and rng.random() < 0.2:
            children.append({"color": parent, "tag": ""})  # 親と同じ子は除く
        tiles.append({
            "id": str(rng.choice(["", f"g{index}", "dup", "  spaced "])),
            "name": str(rng.choice(["", "肌", "髪の毛", " x " * 30])),
            "parent": {"color": parent, "tag": str(rng.choice(tags))},
            "placeholder": placeholder,
            "children": children,
        })
    return {
        "format": "PaintMaskGroup",
        "tag_library": [str(t) for t in rng.permutation(list(tags))][:int(rng.integers(0, len(tags) + 1))],
        "tag_colors": {"Hi": "#ff0000", "Shadow": "bad", "New": "#00ff00"},
        "tag_order": ["Shadow", "base", "Hi", "Hi"],
        "tiles": tiles,
    }


def _flat_chart(rng):
    parents = [_hex(rng) for _ in range(3)]
    return {
        "groups": [{"parent": str(rng.choice(parents)), "child": _hex(rng), "tag": str(rng.choice(["", "Hi"]))} for _ in range(8)]
        + [{"parent": parents[0], "child": parents[0]}, {"parent": "bad", "child": "#000000"}],
        "parent_tags": [{"parent": parents[0], "tag": "Line"}, {"parent": "x", "tag": "y"}],
        "group_names": {parents[1]: "  名前  "},
        "standalone": [{"color": _hex(rng), "tag": ""}, {"color": "nope"}],
    }


def _native_chart(text):
    return json.loads(text) if text is not None else None


@pytest.mark.parametrize("seed", range(12))
def test_chart_normalize_matches_python(seed):
    rng = np.random.default_rng(seed)
    for data in (_random_chart(rng), _flat_chart(rng), {}, {"tiles": "bad"}):
        expected = normalize_color_chart(data)
        actual = _native_chart(native.color_chart_normalize(json.dumps(data)))
        assert actual == expected
        assert list(actual) == list(expected)


@pytest.mark.parametrize("seed", range(12))
def test_chart_merge_matches_python(seed):
    rng = np.random.default_rng(1000 + seed)
    existing = _random_chart(rng)
    current = _random_chart(rng)
    if existing["tiles"] and current["tiles"] and rng.random() < 0.7:
        # 同じ親を持つタイルを混ぜる（子を足す側の道）。
        current["tiles"][0]["parent"] = dict(existing["tiles"][0]["parent"])
        current["tiles"][0]["placeholder"] = existing["tiles"][0]["placeholder"]
    expected = merge_color_charts(existing, current)
    actual = _native_chart(native.color_chart_merge(json.dumps(existing), json.dumps(current)))
    assert actual == expected


def _hits(chart):
    hits = []
    for tile in chart["tiles"]:
        hits.append({"group_id": tile["id"], "role": "parent", "index": 0})
        for index in range(len(tile["children"]) + 1):
            hits.append({"group_id": tile["id"], "role": "child", "index": index})
    hits.append({"group_id": "missing", "role": "parent", "index": 0})
    return hits


def _hit_tuple(hit):
    return (hit["group_id"], hit["role"], int(hit.get("index", 0)))


@pytest.mark.parametrize("seed", range(8))
def test_chart_move_matches_python(seed):
    rng = np.random.default_rng(2000 + seed)
    chart = normalize_color_chart(_random_chart(rng))
    hits = _hits(chart)
    text = json.dumps(chart)
    for _ in range(40):
        source = hits[int(rng.integers(0, len(hits)))]
        target = None if rng.random() < 0.2 else hits[int(rng.integers(0, len(hits)))]
        expected, changed = move_chart_color(chart, source, target)
        actual = native.color_chart_move(text, _hit_tuple(source), _hit_tuple(target) if target else None)
        if not changed:
            assert actual is None, (source, target)
        else:
            assert _native_chart(actual) == expected, (source, target)


@pytest.mark.parametrize("seed", range(6))
def test_chart_assign_tag_matches_python(seed):
    rng = np.random.default_rng(3000 + seed)
    chart = normalize_color_chart(_random_chart(rng))
    text = json.dumps(chart)
    for hit in _hits(chart):
        for tag in ("", "Hi", "New Tag", "  Base  "):
            expected, changed = assign_chart_tag(chart, hit, tag)
            actual = native.color_chart_assign_tag(text, _hit_tuple(hit), tag)
            if not changed:
                assert actual is None, (hit, tag)
            else:
                assert _native_chart(actual) == expected, (hit, tag)


def test_pmag_read_matches_python(tmp_path):
    rng = np.random.default_rng(9)
    chart = normalize_color_chart(_random_chart(rng))
    path = tmp_path / "chart.pmag"
    path.write_text(json.dumps(chart, ensure_ascii=False, indent=2), encoding="utf-8")
    assert _native_chart(native.color_chart_read_pmag(path.read_text(encoding="utf-8"))) == read_pmag(path)
    bad = tmp_path / "bad.pmag"
    bad.write_text(json.dumps({"format": "Other"}), encoding="utf-8")
    with pytest.raises(Exception):
        read_pmag(bad)
    with pytest.raises(ValueError):
        native.color_chart_read_pmag(bad.read_text(encoding="utf-8"))
