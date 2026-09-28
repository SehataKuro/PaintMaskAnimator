"""あとから直せるトゥイーン：キーのセルに持たせる設定と、区間の判定。

確定したトゥイーンは、中割りの絵を各コマに持つ（再生・書き出しは通常の
セルと同じ）。そのうえで、キーのセルの ``Layer.tween`` に設定を、中割りの
セルの ``Layer.tween_member`` に同じ id を持たせ、タイムラインでは 1 本の
トゥイーンの帯として見せる。中割りのセルを動かすなどして並びが崩れたら、
帯としては扱わず、通常のセルとして見せる。
"""
import uuid

from PySide6.QtCore import QPointF

MODES = ("free", "mesh")


def new_id():
    return uuid.uuid4().hex


def points_to_data(points):
    return [[float(point.x()), float(point.y())] for point in points]


def points_from_data(data):
    return [QPointF(float(x), float(y)) for x, y in data]


def make_spec(
    tween_id, length, mode, reverse, start_points, final_points,
    mesh_cols=4, mesh_rows=4, mesh_reference_points=(),
):
    """キーのセルに保存する設定（JSON にそのまま書ける値だけ）。"""
    return {
        "id": str(tween_id),
        "length": int(length),
        "mode": "mesh" if mode == "mesh" else "free",
        "reverse": bool(reverse),
        "start_points": points_to_data(start_points),
        "final_points": points_to_data(final_points),
        "mesh_cols": int(mesh_cols),
        "mesh_rows": int(mesh_rows),
        "mesh_reference_points": points_to_data(mesh_reference_points),
    }


def _points_ok(value):
    return isinstance(value, list) and all(
        isinstance(point, (list, tuple))
        and len(point) == 2
        and all(isinstance(v, (int, float)) for v in point)
        for point in value
    )


def sanitize_spec(value):
    """読み込んだ設定を検証する。不正なら None（通常のセルとして扱う）。"""
    if not isinstance(value, dict):
        return None
    try:
        spec = {
            "id": str(value["id"]),
            "length": int(value["length"]),
            "mode": "mesh" if value.get("mode") == "mesh" else "free",
            "reverse": bool(value.get("reverse", False)),
            "start_points": value.get("start_points", []),
            "final_points": value.get("final_points", []),
            "mesh_cols": int(value.get("mesh_cols", 4)),
            "mesh_rows": int(value.get("mesh_rows", 4)),
            "mesh_reference_points": value.get("mesh_reference_points", []),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if not spec["id"] or spec["length"] < 2:
        return None
    for key in ("start_points", "final_points", "mesh_reference_points"):
        if not _points_ok(spec[key]):
            return None
    return spec


def group_length(frames, layer_index, key_column):
    """``key_column`` が崩れていないトゥイーンの先頭なら、その長さを返す。"""
    if not (0 <= key_column < len(frames)):
        return None
    layers = frames[key_column].layers
    if not (0 <= layer_index < len(layers)):
        return None
    key = layers[layer_index]
    spec = getattr(key, "tween", None)
    if not spec or not key.has_content or int(key.exposure) != 1:
        return None
    length = int(spec.get("length", 0))
    if length < 2 or key_column + length > len(frames):
        return None
    for offset in range(1, length):
        member = frames[key_column + offset].layers[layer_index]
        if (
            getattr(member, "tween_member", None) != spec.get("id")
            or not member.has_content
            or int(member.exposure) != 1
        ):
            return None
    return length


def tween_groups(frames, layer_index):
    """{先頭のコマ: (長さ, 逆生成か)} を返す。"""
    groups = {}
    column = 0
    while column < len(frames):
        length = group_length(frames, layer_index, column)
        if length:
            spec = frames[column].layers[layer_index].tween
            groups[column] = (length, bool(spec.get("reverse", False)))
            column += length
        else:
            column += 1
    return groups
