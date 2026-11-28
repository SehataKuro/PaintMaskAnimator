"""Compare pma-core's CLIP STUDIO import with Qt's ``clip_animation.py``.

Tauri の画面の「CLIP STUDIOアニメーションを読み込む」「CLIPの線画を初期状態として読み込む」は
pma-core（``import::clip``・``import::clip_render``・``Document::from_animation``・
``match_clip_line_art``）で読み込む。ここでは

- セルの合成（``_CelRenderer``）を、合成モード・不透明度・クリッピング・マスク・通過・
  レイヤーカラー・ずらしを混ぜた乱数の木で、Python と画素まで比べる。
- 合成で作った .clip を、Python の ``read_clip_animation`` と比べ、Qt の読み込み
  （``ImportController.clip_animation``）が作る文書と文書グラフで比べる。
- 線画の初期状態（``import_clip_line_art``）の結果の文書を比べる。
"""

import json
import os
import random
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from paintmaskanimator import constants
from paintmaskanimator.clip_animation import ClipImportError, _CelRenderer, read_clip_animation
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.pma_image import PmaImage
from pma_native import pma_native as core

from test_clip_animation import (
    _LAYER_COLUMNS,
    _binc,
    _external,
    _offscreen_attribute,
    _pack_clip,
    _raster_container,
)
import zlib

pytestmark = pytest.mark.skipif(
    not hasattr(core, "clip_render_cel"), reason="pma_native has no pma-core CLIP import"
)

MODES = [0] * 8 + [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
                   21, 22, 23, 24, 25, 26, 36, 30, 30, 99]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_canvas_size():
    saved = (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)
    try:
        yield
    finally:
        constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT = saved


# ---------------------------------------------------------------------------
# セルの合成
# ---------------------------------------------------------------------------

def _raster(rng, width, height, *, mask=False):
    w = rng.randint(1, width + 2)
    h = rng.randint(1, height + 2)
    if mask:
        channels = rng.choice([1, 1, 2])
        alpha_only = channels == 1 and rng.random() < 0.6
    else:
        channels = rng.choice([4, 4, 4, 1, 2])
        alpha_only = channels == 1 and rng.random() < 0.7
    data = bytearray()
    for _ in range(w * h):
        if channels == 4:
            alpha = rng.choice([0, 255, 255, rng.randrange(256)])
            data += bytes([rng.randrange(256), rng.randrange(256), rng.randrange(256), alpha])
        elif channels == 2:
            data += bytes([rng.randrange(256), rng.choice([0, 255, rng.randrange(256)])])
        else:
            data += bytes([rng.choice([0, 255, rng.randrange(256)])])
    return (w, h, channels, alpha_only, rng.random() < 0.3, bytes(data))


def _color_value(rng):
    return rng.choice([0, 255, rng.randrange(256), 0x80808080, 0xFFFFFFFF, rng.randrange(1 << 32)])


def _tree(rng, depth, state, width, height, *, plain, root=False):
    layer_id = state["next"]
    state["next"] += 1
    row = {
        "MainId": layer_id, "LayerName": f"L{layer_id}", "children": [],
        "LayerOpacity": rng.choice([256, 256, 256, rng.randint(0, 256), 0, 300]),
        "LayerComposite": 0 if plain else rng.choice(MODES),
        "LayerClip": int(rng.random() < 0.3),
        "LayerFolder": 0,
        "LayerVisibility": rng.choice([1, 1, 1, 1, 0]) if plain else rng.choice([1, 1, 1, 3, 3, 0, 2]),
        "LayerFirstChildIndex": 0, "LayerNextIndex": 0,
        "LayerRenderMipmap": 0, "LayerLayerMaskMipmap": 0,
        "LayerOffsetX": rng.choice([0, 0, rng.randint(-3, 3)]),
        "LayerOffsetY": rng.choice([0, 0, rng.randint(-3, 3)]),
        "LayerRenderOffscrOffsetX": rng.choice([0, rng.randint(-2, 2)]),
        "LayerRenderOffscrOffsetY": rng.choice([0, rng.randint(-2, 2)]),
        "LayerMaskOffsetX": rng.randint(-2, 2), "LayerMaskOffsetY": rng.randint(-2, 2),
        "LayerMaskOffscrOffsetX": rng.randint(-1, 1), "LayerMaskOffscrOffsetY": rng.randint(-1, 1),
        "DrawColorEnable": int(rng.random() < 0.2),
        "LayerUsePaletteColor": int(rng.random() < 0.2),
    }
    for prefix in ("DrawColorMain", "DrawColorSub", "LayerPalette"):
        for channel in ("Red", "Green", "Blue"):
            row[prefix + channel] = _color_value(rng)
    if rng.random() < 0.15:
        # 列の無い .clip（サブ色の既定は白）。
        for channel in ("Red", "Green", "Blue"):
            del row["DrawColorSub" + channel]
    if root or (depth > 0 and rng.random() < 0.45):
        children = [_tree(rng, depth - 1, state, width, height, plain=plain)
                    for _ in range(rng.randint(2 if root else 0, 4))]
        row["children"] = children
        row["LayerFolder"] = 1
        row["LayerFirstChildIndex"] = children[0] if children else 0
    else:
        mipmap = state["next"]
        state["next"] += 1
        state["rasters"][mipmap] = _raster(rng, width, height)
        row["LayerRenderMipmap"] = mipmap
    if not plain and rng.random() < 0.3:
        mipmap = state["next"]
        state["next"] += 1
        state["rasters"][mipmap] = _raster(rng, width, height, mask=True)
        row["LayerLayerMaskMipmap"] = mipmap
    state["rows"][layer_id] = row
    return layer_id


class _Reader:
    def __init__(self, rasters):
        self.rasters = rasters

    def raster(self, mipmap):
        return self.rasters[mipmap]


def _rust_rows(rows):
    skip = {"MainId", "LayerName", "children"}
    return [(row["MainId"], row["LayerName"], list(row["children"]),
             [(key, int(value)) for key, value in row.items() if key not in skip])
            for row in rows.values()]


def _render_both(rows, rasters, cel, width, height):
    try:
        expected = PmaImage(_CelRenderer(_Reader(rasters), rows, width, height).render(rows[cel]))
        expected = expected._image.rgba_bytes()
    except ClipImportError as exc:
        with pytest.raises(ValueError, match=str(exc)):
            core.clip_render_cel(width, height, _rust_rows(rows), rasters, cel)
        return None
    actual = core.clip_render_cel(width, height, _rust_rows(rows), rasters, cel).rgba_bytes()
    return expected, actual


@pytest.mark.parametrize("seed", range(240))
def test_cel_compositing_matches_python(qapp, seed):
    rng = random.Random(seed)
    width, height = rng.randint(1, 13), rng.randint(1, 6)
    state = {"next": 1, "rows": {}, "rasters": {}}
    # 4つに1つは Qt の道（通常の合成とクリッピングだけ、マスクなし）。
    cel = _tree(rng, 3, state, width, height, plain=seed % 4 == 0, root=seed % 8 != 1)
    result = _render_both(state["rows"], state["rasters"], cel, width, height)
    if result is not None:
        expected, actual = result
        assert actual == expected


def test_missing_render_raster_reports_the_same_message(qapp):
    rows = {1: {"MainId": 1, "LayerName": "線", "children": [], "LayerRenderMipmap": 0,
                "LayerOpacity": 256, "LayerVisibility": 1}}
    with pytest.raises(ValueError, match="セル「線」の表示合成画像が.clip内にありません。"):
        core.clip_render_cel(2, 2, _rust_rows(rows), {}, 1)


def test_cyclic_cel_tree_reports_the_same_message(qapp):
    rows = {
        1: {"MainId": 1, "LayerName": "a", "children": [2], "LayerFolder": 1,
            "LayerComposite": 2, "LayerVisibility": 1, "LayerOpacity": 256},
        2: {"MainId": 2, "LayerName": "b", "children": [1], "LayerFolder": 1,
            "LayerComposite": 2, "LayerVisibility": 1, "LayerOpacity": 256},
    }
    assert _render_both(rows, {}, 1, 2, 2) is None


# ---------------------------------------------------------------------------
# .clip ファイル
# ---------------------------------------------------------------------------

def _random_raster_body(rng, width, height, *, mask=False):
    """width × height の絵（1ブロック 256×256 の左上）を乱数で作る。"""
    alpha = bytearray(256 * 256)
    bgra = bytearray(256 * 256 * 4)
    color = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
    for y in range(height):
        for x in range(width):
            index = y * 256 + x
            alpha[index] = rng.choice([0, 255, 255, rng.randrange(256)])
            red, green, blue = color if rng.random() < 0.7 else (
                rng.randrange(256), rng.randrange(256), rng.randrange(256))
            bgra[index * 4:index * 4 + 4] = bytes((blue, green, red, 0))
    return _raster_container(alpha if mask else alpha + bgra)


def _animation_clip(tmp_path, rng, *, width, height, start, end, folders, name="anim.clip"):
    """アニメーションフォルダーを並べた .clip（下から上の順）。

    ``folders`` は dict の列：name・cels（セル名 → レイヤーの spec の列、上から下）・
    keys（(コマ, セル名)）・visible・layer_color（フォルダーのレイヤーカラー）。
    レイヤーの spec は name・composite・opacity・clip・visibility・mask・children。
    """
    database_path = tmp_path / (name + ".sqlite")
    if database_path.exists():
        database_path.unlink()
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE Canvas (MainId INTEGER, CanvasWidth REAL, CanvasHeight REAL);
        CREATE TABLE TimeLine (
            MainId INTEGER, BankId INTEGER, FrameRate REAL,
            StartFrame REAL, EndFrame REAL, TimeLineName TEXT
        );
        CREATE TABLE AnimationCutBank (MainId INTEGER, FirstTimeLine INTEGER, Enable INTEGER);
        CREATE TABLE Track (
            MainId INTEGER, BankId INTEGER, TrackKind INTEGER,
            TrackActionMixer BLOB, LayerUuidWithTrack BLOB
        );
        CREATE TABLE Mipmap (MainId INTEGER, BaseMipmapInfo INTEGER);
        CREATE TABLE MipmapInfo (MainId INTEGER, Offscreen INTEGER);
        CREATE TABLE Offscreen (MainId INTEGER, BlockData BLOB, Attribute BLOB);
        """
    )
    connection.execute(
        "CREATE TABLE Layer ("
        + ", ".join(
            f"{column} {'BLOB' if column == 'LayerUuid' else 'TEXT' if column == 'LayerName' else 'INTEGER'}"
            for column in _LAYER_COLUMNS
        )
        + ")"
    )
    connection.execute("INSERT INTO Canvas VALUES (1, ?, ?)", (width, height))
    connection.execute("INSERT INTO TimeLine VALUES (1, 7, 24, ?, ?, 'TL')", (start, end))
    connection.execute("INSERT INTO AnimationCutBank VALUES (1, 1, 1)")
    externals = []
    raster_ids = [1000]
    layer_ids = [len(folders) + 1]

    def insert(values):
        row = {column: 0 for column in _LAYER_COLUMNS}
        row.update(values)
        connection.execute(
            f"INSERT INTO Layer VALUES ({', '.join('?' for _ in _LAYER_COLUMNS)})",
            tuple(row[column] for column in _LAYER_COLUMNS),
        )

    def add_raster(body, packing_type=(1, 4)):
        raster_id = raster_ids[0]
        raster_ids[0] += 1
        identifier = f"extrnlid_{raster_id}".encode()
        connection.execute("INSERT INTO Mipmap VALUES (?, ?)", (raster_id, raster_id))
        connection.execute("INSERT INTO MipmapInfo VALUES (?, ?)", (raster_id, raster_id))
        connection.execute(
            "INSERT INTO Offscreen VALUES (?, ?, ?)",
            (raster_id, identifier, _offscreen_attribute(width, height, packing_type=packing_type)),
        )
        externals.append(_external(identifier, body))
        return raster_id

    def add_layers(specs):
        ids = []
        for spec in reversed(specs):
            layer_id = layer_ids[0]
            layer_ids[0] += 1
            ids.append((layer_id, spec))
        for index, (layer_id, spec) in enumerate(ids):
            first_child = add_layers(spec["children"]) if "children" in spec else 0
            render = None if "children" in spec else add_raster(_random_raster_body(rng, width, height))
            mask = add_raster(_random_raster_body(rng, width, height, mask=True), (1, 0)) \
                if spec.get("mask") else None
            color = spec.get("layer_color")
            insert({
                "MainId": layer_id, "LayerUuid": f"uuid-{layer_id:011d}".encode(),
                "LayerName": spec["name"], "LayerFirstChildIndex": first_child,
                "LayerNextIndex": ids[index + 1][0] if index + 1 < len(ids) else 0,
                "LayerRenderMipmap": render, "LayerFolder": int("children" in spec),
                "LayerVisibility": spec.get("visibility", 1), "LayerOpacity": spec.get("opacity", 256),
                "LayerComposite": spec.get("composite", 0), "LayerClip": spec.get("clip", 0),
                "LayerLayerMaskMipmap": mask, "DrawColorEnable": int(color is not None),
                **{f"DrawColorMain{c}": v for c, v in zip(("Red", "Green", "Blue"), color or (0, 0, 0))},
                **{f"DrawColorSub{c}": v for c, v in zip(("Red", "Green", "Blue"), (255, 255, 255))},
            })
        return ids[0][0] if ids else 0

    for index, folder in enumerate(folders):
        folder_id = index + 1
        first = add_layers([{"name": cel, "children": specs} for cel, specs in folder["cels"].items()])
        uuid = bytes([65 + index]) * 16
        color = folder.get("layer_color")
        insert({
            "MainId": folder_id, "LayerUuid": uuid, "LayerName": folder["name"],
            "LayerFirstChildIndex": first,
            "LayerNextIndex": folder_id + 1 if index + 1 < len(folders) else 0,
            "AnimationFolder": 1, "LayerFolder": 1,
            "LayerVisibility": int(folder.get("visible", True)), "LayerOpacity": 256,
            "DrawColorEnable": int(color is not None),
            **{f"DrawColorMain{c}": v for c, v in zip(("Red", "Green", "Blue"), color or (0, 0, 0))},
            **{f"DrawColorSub{c}": v for c, v in zip(("Red", "Green", "Blue"), (255, 255, 255))},
        })
        mixer_id = f"extrnlid_mixer{index}".encode()
        connection.execute("INSERT INTO Track VALUES (?, 7, 2000, ?, ?)", (index + 1, mixer_id, uuid))
        keys = tuple((frame * 2.5, tag) for frame, tag in folder["keys"])
        compressed = zlib.compress(_binc(keys or None))
        externals.append(_external(mixer_id, len(compressed).to_bytes(4, "little") + compressed))
    connection.commit()
    connection.close()
    data = _pack_clip(database_path, externals)
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _random_cel_specs(rng, depth=2):
    specs = []
    for index in range(rng.randint(1, 3)):
        spec = {
            "name": f"l{index}",
            "composite": rng.choice(MODES),
            "opacity": rng.choice([256, 256, rng.randint(0, 256)]),
            "clip": int(index > 0 and rng.random() < 0.3),
            "visibility": rng.choice([1, 1, 1, 3, 0]),
            "mask": rng.random() < 0.25,
        }
        if rng.random() < 0.2:
            spec["layer_color"] = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        if depth > 0 and rng.random() < 0.3:
            spec["children"] = _random_cel_specs(rng, depth - 1)
        specs.append(spec)
    return specs


def _random_folders(rng, start, end):
    folders = []
    tags_pool = ["1", "2", "3", "A", "B-8", " 4 ", "07", "x"]
    for index in range(rng.randint(1, 3)):
        tags = rng.sample(tags_pool, rng.randint(1, 4))
        keys = []
        for _ in range(rng.randint(0, 5)):
            keys.append((rng.randint(max(0, start - 2), end + 2), rng.choice(tags + [""])))
        keys.sort(key=lambda k: k[0])
        folder = {
            "name": rng.choice([f"F{index}", f"動画{index}"]),
            "cels": {tag: _random_cel_specs(rng) for tag in tags},
            "keys": keys,
            "visible": rng.random() < 0.9,
        }
        if rng.random() < 0.2:
            folder["layer_color"] = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        folders.append(folder)
    return folders


def _read_both(path):
    try:
        expected = read_clip_animation(path)
    except ClipImportError as exc:
        with pytest.raises(ValueError) as caught:
            core.clip_read_animation(str(path))
        assert str(caught.value) == str(exc)
        return None
    actual = core.clip_read_animation(str(path))
    return expected, actual


def _compare_read(expected, actual):
    name, timeline, width, height, fps, start, end, layers = actual
    assert (name, timeline, width, height, fps, start, end) == (
        expected.source_name, expected.timeline_name, expected.width, expected.height,
        expected.fps, expected.start_frame, expected.end_frame)
    assert [layer[0] for layer in layers] == [layer.name for layer in expected.layers]
    for (_, keys, cels), layer in zip(layers, expected.layers):
        assert keys == [(key.frame, key.tag) for key in layer.keys]
        assert [tag for tag, _ in cels] == list(layer.cell_images)
        for tag, image in cels:
            assert image.rgba_bytes() == PmaImage(layer.cell_images[tag])._image.rgba_bytes(), tag


@pytest.mark.parametrize("seed", range(24))
def test_clip_file_cels_and_keys_match_python(qapp, tmp_path, seed):
    rng = random.Random(1000 + seed)
    width, height = rng.randint(1, 9), rng.randint(1, 5)
    start = rng.randint(0, 3)
    end = start + rng.randint(0, 6)
    path = _animation_clip(tmp_path, rng, width=width, height=height, start=start, end=end,
                           folders=_random_folders(rng, start, end))
    result = _read_both(path)
    if result is not None:
        _compare_read(*result)


def test_clip_errors_match_python(qapp, tmp_path):
    rng = random.Random(5)
    # 表示中のアニメーションフォルダーがない。
    hidden = _animation_clip(tmp_path, rng, width=2, height=2, start=0, end=1, name="hidden.clip",
                             folders=[{"name": "A", "cels": {"1": [{"name": "l"}]},
                                       "keys": [(0, "1")], "visible": False}])
    assert _read_both(hidden) is None
    # キーのセルがない。
    missing = _animation_clip(tmp_path, rng, width=2, height=2, start=0, end=1, name="missing.clip",
                              folders=[{"name": "A", "cels": {"1": [{"name": "l"}]},
                                        "keys": [(0, "1"), (1, "9")]}])
    assert _read_both(missing) is None
    # .clip でない。
    other = tmp_path / "other.png"
    other.write_bytes(b"")
    assert _read_both(other) is None


def _graph(core_document):
    """文書グラフ。コマ・列の ID と空キーの共有の組は並びの順に、絵の添字は画素に直す。"""
    text, images = core_document.to_graph()
    graph = json.loads(text)
    frames = {frame["id"]: index for index, frame in enumerate(graph["frames"])}
    tracks = {track["id"]: index for index, track in enumerate(graph["tracks"])}
    for frame in graph["frames"]:
        frame["id"] = frames[frame["id"]]
    for track in graph["tracks"]:
        track["id"] = tracks[track["id"]]
    graph["current_frame"] = frames.get(graph["current_frame"], graph["current_frame"])
    graph.pop("next_frame_id")
    graph.pop("next_track_id")
    groups = {}
    for frame in graph["frames"]:
        for position in frame["positions"]:
            if position.get("local_share") is not None:
                position["local_share"] = groups.setdefault(position["local_share"], len(groups))
    pixels = [(i.width, i.height, i.rgba_bytes()) for i in images]

    def resolve(value):
        if isinstance(value, dict):
            return {
                key: (pixels[item] if key in ("image", "initial_image") and isinstance(item, int)
                      else resolve(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    return resolve(graph)


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = MainWindow()
    yield result
    result.close()


@pytest.mark.parametrize("seed", range(16))
def test_clip_import_document_matches_qt(window, tmp_path, seed):
    rng = random.Random(2000 + seed)
    width, height = rng.randint(1, 7), rng.randint(1, 4)
    start = rng.randint(0, 2)
    end = start + rng.randint(0, 7)
    folders = _random_folders(rng, start, end)
    for folder in folders:
        folder["visible"] = True
        # 置かれない番号のセル（連番に残る）も混ぜる。
        folder["cels"].setdefault("99", [{"name": "l"}])
    path = _animation_clip(tmp_path, rng, width=width, height=height, start=start, end=end,
                           folders=folders)
    if _read_both(path) is None:
        pytest.skip("Qt でも読めない組み合わせ")
    replaced = (constants.CANVAS_WIDTH, constants.CANVAS_HEIGHT)
    assert window.importer.clip_animation(path, confirm_replace=False)
    message = window.statusBar().currentMessage()
    document, kept, archived = core.clip_animation_document(str(path), True, replaced)
    assert _graph(document) == _graph(window.canvas._document._live()["core"])
    if kept:
        assert f" {kept}セルを色塗り前の初期状態として保持しました。" in message
    else:
        assert "初期状態" not in message
    if archived:
        assert f" 未配置セル{archived}枚は連番に保持しました。" in message
    else:
        assert "未配置セル" not in message
    # 読み込んだ文書を置き換える（保持のコマの旧入力の空の絵は前の文書の大きさ）。
    assert window.importer.clip_animation(path, confirm_replace=False)
    document, _, _ = core.clip_animation_document(str(path), True, (width, height))
    assert _graph(document) == _graph(window.canvas._document._live()["core"])


def test_clip_line_art_initial_state_matches_qt(window, tmp_path):
    rng = random.Random(77)
    folders = [
        {"name": "A", "cels": {"1": [{"name": "l"}], "2": [{"name": "l"}], "x": [{"name": "l"}]},
         "keys": [(0, "1"), (2, "2"), (3, ""), (4, "1")]},
        {"name": "B", "cels": {"1": [{"name": "l"}]}, "keys": [(1, "1")]},
    ]
    path = _animation_clip(tmp_path, rng, width=4, height=3, start=0, end=5, folders=folders)
    assert window.importer.clip_animation(path, confirm_replace=False)
    # 同じ名前（大文字・空白違い）で絵の違う線画。B はない。
    line_art = _animation_clip(
        tmp_path, rng, width=4, height=3, start=0, end=2, name="line.clip",
        folders=[{"name": " a ", "cels": {"2": [{"name": "l"}], "1": [{"name": "l"}],
                                          "x": [{"name": "l"}]}, "keys": [(0, "1")]}])
    before = window.canvas._document._live()["core"]
    text, images = before.to_graph()
    rust = core._CoreDocument.from_graph(text, images)
    assert window.initial_state.import_clip_line_art(line_art) > 0
    count, missing = core.clip_line_art(rust, str(line_art))
    assert (count, missing) == (3, 1)
    assert _graph(rust) == _graph(window.canvas._document._live()["core"])


def test_clip_line_art_rejects_duplicate_folders_and_other_sizes(window, tmp_path):
    rng = random.Random(78)
    path = _animation_clip(tmp_path, rng, width=4, height=3, start=0, end=1,
                           folders=[{"name": "A", "cels": {"1": [{"name": "l"}]}, "keys": [(0, "1")]}])
    assert window.importer.clip_animation(path, confirm_replace=False)
    text, images = window.canvas._document._live()["core"].to_graph()
    rust = core._CoreDocument.from_graph(text, images)
    duplicate = _animation_clip(
        tmp_path, rng, width=4, height=3, start=0, end=1, name="dup.clip",
        folders=[{"name": "A", "cels": {"1": [{"name": "l"}]}, "keys": [(0, "1")]},
                 {"name": "a", "cels": {"1": [{"name": "l"}]}, "keys": [(0, "1")]}])
    with pytest.raises(ValueError, match="CLIP内のアニメーションフォルダー名が重複しています：a"):
        core.clip_line_art(rust, str(duplicate))
    other = _animation_clip(
        tmp_path, rng, width=5, height=3, start=0, end=1, name="size.clip",
        folders=[{"name": "A", "cels": {"1": [{"name": "l"}]}, "keys": [(0, "1")]}])
    with pytest.raises(ValueError, match="線画のサイズが現在のセルと一致しません：A/1\n"
                                         "線画：5 × 3px／現在のセル：4 × 3px"):
        core.clip_line_art(rust, str(other))
