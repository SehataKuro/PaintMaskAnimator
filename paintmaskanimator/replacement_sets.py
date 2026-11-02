"""使用色の色置換セットの保存と読み込み（Issue #42）。

シーン色への置き換えなど、カットをまたいで繰り返し使う色置換がある。毎回
登録し直さずに済むよう、「元の色 → 置き換える色」の組をファイル（``.pmarep``）
へ出し入れする。Qt に依存しない素の辞書として扱い、UI は
``main_window_used_color`` 側が受け持つ。
"""
import json
from pathlib import Path

#: ファイルの拡張子とダイアログのフィルター。
SUFFIX = ".pmarep"
FILE_FILTER = "Paint Mask Replacements (*.pmarep)"
FORMAT_VERSION = 1


def _hex(rgb):
    red, green, blue = (max(0, min(255, int(value))) for value in rgb[:3])
    return f"#{red:02x}{green:02x}{blue:02x}"


def _rgb(value):
    text = str(value).strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(value)
    return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))


def to_data(mapping):
    """``{元の RGB: 置換後の RGB}`` を、書き出せる素のデータにする。"""
    return {
        "format": "paintmaskanimator.replacements",
        "version": FORMAT_VERSION,
        "replacements": [
            {"from": _hex(source), "to": _hex(destination)}
            for source, destination in mapping.items()
        ],
    }


def from_data(data):
    """読み込んだデータを ``{元の RGB: 置換後の RGB}`` にする。

    読めない組は飛ばす。手で書き換えたファイルや、将来の項目が増えた
    ファイルでも、分かるところだけ使えるようにするため。
    """
    if not isinstance(data, dict):
        raise ValueError("replacement set must be an object")
    entries = data.get("replacements")
    if not isinstance(entries, list):
        raise ValueError("replacement set has no replacements")
    mapping = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            source = _rgb(entry["from"])
            destination = _rgb(entry["to"])
        except (KeyError, TypeError, ValueError):
            continue
        if source != destination:
            mapping[source] = destination
    return mapping


def save(path, mapping):
    path = Path(path)
    if path.suffix.lower() != SUFFIX:
        path = path.with_suffix(SUFFIX)
    path.write_text(
        json.dumps(to_data(mapping), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load(path):
    return from_data(json.loads(Path(path).read_text(encoding="utf-8")))
