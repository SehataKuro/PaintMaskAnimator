"""Cut-folder layouts: block templates that name a cut's folders and files.

A *template* is a tuple of :class:`Token` -- either literal text or a *field*
block (作品名, 話数, カット名, セル名, セル番号 ...). The same template renders
paths on export and, compiled to a regular expression, matches them on import,
so a folder written with one layout reads back with the same layout. The
template, not a regex, is what gets stored: a regex cannot be turned back into
file names, and the blocks are what the editor shows.

Pure logic only -- no Qt -- so it is unit-testable without a display.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping, Optional, Sequence

#: Field ids in suggestion order. The id is protocol (saved in presets); the
#: label is what the editor shows.
FIELD_IDS = ("title", "scene", "episode", "cut", "cell", "number")

#: Fields whose value comes from the dialog's input boxes rather than the cel.
VALUE_FIELDS = ("title", "scene", "episode", "cut")

#: Fields that only make sense per cel (not in a cut-wide name).
CEL_FIELDS = ("cell", "number")

#: Fields that hold a number and therefore take a digit count.
NUMERIC_FIELDS = ("episode", "cut", "number")

CASES = ("keep", "upper", "lower")

DIGIT_CHOICES = (None, 2, 3, 4, 5)

#: Characters Windows (and therefore portable folders) refuse in a name.
INVALID_NAME_CHARS = '<>:"\\|?*'

IMAGE_EXTENSIONS = ("png", "tga")
TIMESHEET_EXTENSIONS = ("xdts", "tdts")


@dataclass(frozen=True)
class Token:
    """One element of a template: a literal character run or a field block."""

    text: Optional[str] = None
    field: Optional[str] = None
    digits: Optional[int] = None
    case: str = "keep"
    prefix: str = ""
    suffix: str = ""

    @property
    def is_field(self):
        return self.field is not None

    def to_json(self):
        if not self.is_field:
            return {"text": self.text}
        data = {"field": self.field}
        if self.digits is not None:
            data["digits"] = int(self.digits)
        if self.case != "keep":
            data["case"] = self.case
        if self.prefix:
            data["prefix"] = self.prefix
        if self.suffix:
            data["suffix"] = self.suffix
        return data

    @classmethod
    def from_json(cls, data):
        if "field" in data:
            name = str(data["field"])
            if name not in FIELD_IDS:
                raise ValueError(f"unknown field: {name}")
            digits = data.get("digits")
            case = str(data.get("case", "keep"))
            return cls(
                field=name,
                digits=int(digits) if digits is not None else None,
                case=case if case in CASES else "keep",
                prefix=str(data.get("prefix", "")),
                suffix=str(data.get("suffix", "")),
            )
        return cls(text=str(data.get("text", "")))


def text(value):
    return Token(text=value)


def block(name, **options):
    return Token(field=name, **options)


def explode(template: Sequence[Token]):
    """Split literal runs into one token per character (the editor's unit)."""
    out = []
    for token in template:
        if token.is_field:
            out.append(token)
        else:
            out.extend(Token(text=character) for character in token.text or "")
    return out


def compact(template: Sequence[Token]):
    """Merge adjacent literal tokens (the stored form)."""
    out = []
    for token in template:
        if not token.is_field and out and not out[-1].is_field:
            out[-1] = Token(text=(out[-1].text or "") + (token.text or ""))
        elif token.is_field or token.text:
            out.append(token)
    return tuple(out)


def template_to_json(template):
    return [token.to_json() for token in compact(template)]


def template_from_json(data):
    return compact(Token.from_json(item) for item in data or [])


def format_field(token: Token, value) -> str:
    """Render one field block; an empty value drops its prefix and suffix too."""
    raw = "" if value is None else str(value)
    if raw == "":
        return ""
    if token.field in NUMERIC_FIELDS and token.digits and raw.isdigit():
        raw = raw.zfill(int(token.digits))
    if token.case == "upper":
        raw = raw.upper()
    elif token.case == "lower":
        raw = raw.lower()
    return f"{token.prefix}{raw}{token.suffix}"


def render(template: Sequence[Token], values: Mapping[str, object]) -> str:
    parts = []
    for token in template:
        if token.is_field:
            parts.append(format_field(token, values.get(token.field)))
        else:
            parts.append(token.text or "")
    return "".join(parts)


def fields_in(template: Sequence[Token]):
    return {token.field for token in template if token.is_field}


_FIELD_PATTERNS = {
    "title": r".+?",
    "scene": r".+?",
    "episode": r"\d+",
    "cut": r"\d+",
    "cell": r"[^/]+?",
    "number": r"\d+",
}


def to_regex(template: Sequence[Token]) -> "re.Pattern[str]":
    """Compile a template to a full-match pattern with one group per field.

    Digit counts are *not* enforced: ``A1`` and ``A00001`` both match a 4-digit
    セル番号, so folders written by other tools still read. A field used twice
    must match the same text both times (back-reference).
    """
    parts = []
    seen = set()
    for token in compact(template):
        if not token.is_field:
            parts.append(re.escape(token.text or ""))
            continue
        name = token.field
        if name in seen:
            body = f"(?P={name})"
        else:
            seen.add(name)
            body = f"(?P<{name}>{_FIELD_PATTERNS[name]})"
        parts.append(
            f"(?:{re.escape(token.prefix)}{body}{re.escape(token.suffix)})"
        )
    return re.compile("".join(parts), re.IGNORECASE)


@dataclass
class CutFolderLayout:
    """Everything the export dialog configures, minus the per-cut values."""

    folder: tuple = ()
    cell_folder: tuple = ()
    cell_file: tuple = ()
    timesheet: tuple = ()
    #: Sub-folder of the cut folder for the time sheet; empty = directly inside.
    timesheet_folder: tuple = ()
    extra_folders: list = field(default_factory=list)
    image_format: str = "png"
    #: ``xdts`` (exchange format) or ``tdts`` (Toei Digital Timesheet).
    timesheet_format: str = "xdts"

    def to_json(self):
        return {
            "folder": template_to_json(self.folder),
            "cell_folder": template_to_json(self.cell_folder),
            "cell_file": template_to_json(self.cell_file),
            "timesheet": template_to_json(self.timesheet),
            "timesheet_folder": template_to_json(self.timesheet_folder),
            "extra_folders": [template_to_json(t) for t in self.extra_folders],
            "image_format": self.image_format,
            "timesheet_format": self.timesheet_format,
        }

    @classmethod
    def from_json(cls, data):
        image_format = str(data.get("image_format", "png")).lower()
        timesheet_format = str(data.get("timesheet_format", "xdts")).lower()
        return cls(
            folder=template_from_json(data.get("folder")),
            cell_folder=template_from_json(data.get("cell_folder")),
            cell_file=template_from_json(data.get("cell_file")),
            timesheet=template_from_json(data.get("timesheet")),
            timesheet_folder=template_from_json(data.get("timesheet_folder")),
            extra_folders=[
                template_from_json(item)
                for item in data.get("extra_folders") or []
            ],
            image_format=image_format if image_format in IMAGE_EXTENSIONS else "png",
            timesheet_format=(
                timesheet_format if timesheet_format in TIMESHEET_EXTENSIONS else "xdts"
            ),
        )

    def copy(self):
        return replace(self, extra_folders=list(self.extra_folders))


def pma_standard_layout():
    """PMA標準: the per-layer folders of 連番書き出し inside a named cut folder.

    Folder ``作品名_話数_c012``, cels ``A/A0001.png`` -- the same cel naming as
    the existing key-sequence export -- and the XDTS named after the folder.
    """
    cut_name = (
        block("title"), text("_"), block("episode", digits=2), text("_"),
        block("cut", digits=3, prefix="c"),
    )
    return CutFolderLayout(
        folder=cut_name,
        cell_folder=(block("cell"),),
        cell_file=(block("cell"), block("number", digits=4)),
        timesheet=cut_name,
        timesheet_folder=(),
        extra_folders=[],
        image_format="png",
    )


def ts_pool_layout():
    """_ts・_pool: the layout used for compositing handoff.

    Folder ``作品名_C002`` (no episode), cels ``A/A_0001.png``, the time sheet
    as ``_ts/c002.tdts`` and an empty ``_pool`` for source files such as the
    ``.clip``.
    """
    return CutFolderLayout(
        folder=(block("title"), text("_"), block("cut", digits=3, prefix="C")),
        cell_folder=(block("cell"),),
        cell_file=(block("cell"), text("_"), block("number", digits=4)),
        timesheet=(block("cut", digits=3, prefix="c"),),
        timesheet_folder=(text("_ts"),),
        extra_folders=[(text("_pool"),)],
        image_format="png",
        timesheet_format="tdts",
    )


BUILTIN_PRESETS = {"PMA標準": pma_standard_layout, "_ts・_pool": ts_pool_layout}


# --- export planning ---------------------------------------------------------

@dataclass(frozen=True)
class CelSource:
    """One distinct drawing to write: a layer column and its セル番号."""

    layer_index: int
    cell: str
    number: int


@dataclass
class ExportPlan:
    folder_name: str
    #: (path relative to the cut folder, source) in write order.
    files: list
    #: Relative to the cut folder (always inside it).
    timesheet_path: str
    #: Every folder to create, relative to the cut folder (cell + extra).
    folders: list
    problems: list
    timesheet_format: str = "xdts"
    #: 作品情報 for formats whose sheet carries it (TDTS).
    sheet_header: dict = field(default_factory=dict)

    @property
    def ok(self):
        return not self.problems


def invalid_name_reason(name: str) -> Optional[str]:
    """Why ``name`` cannot be a single file/folder name, or None if it can."""
    if not name.strip():
        return "empty"
    if name in (".", ".."):
        return "dots"
    if any(character in INVALID_NAME_CHARS for character in name):
        return "chars"
    if any(ord(character) < 32 for character in name):
        return "chars"
    if name != name.rstrip(" ."):
        return "trailing"
    return None


def _split_path(value: str):
    return [part for part in value.split("/")]


def plan_export(
    layout: CutFolderLayout,
    values: Mapping[str, object],
    cels: Iterable[CelSource],
) -> ExportPlan:
    """Resolve every path the export will write and collect what is wrong.

    Problems are returned as ``(kind, detail)`` pairs rather than raised so the
    dialog can show all of them next to the preview; the caller translates.
    """
    problems = []
    base = {key: values.get(key) for key in VALUE_FIELDS}

    for key in VALUE_FIELDS:
        used = any(
            key in fields_in(template)
            for template in (
                layout.folder, layout.cell_folder, layout.cell_file,
                layout.timesheet, layout.timesheet_folder, *layout.extra_folders,
            )
        )
        if used and not str(base.get(key) or "").strip():
            problems.append(("missing_value", key))

    folder_name = render(layout.folder, base)
    reason = invalid_name_reason(folder_name) or (
        "slash" if "/" in folder_name else None
    )
    if reason:
        problems.append(("bad_name", folder_name or "", "folder"))

    if not fields_in(layout.cell_file) & {"number"}:
        problems.append(("no_number", "cell_file"))

    files = []
    folders = []
    seen_paths = {}
    for cel in cels:
        cel_values = dict(base, cell=cel.cell, number=cel.number)
        directory = render(layout.cell_folder, cel_values)
        name = render(layout.cell_file, cel_values)
        if not name:
            problems.append(("bad_name", "", "cell_file"))
            break
        relative = f"{directory}/{name}.{layout.image_format}" if directory else (
            f"{name}.{layout.image_format}"
        )
        for part in _split_path(relative):
            if invalid_name_reason(part):
                problems.append(("bad_name", relative, "cell_file"))
                break
        key = relative.casefold()
        if key in seen_paths:
            problems.append(("duplicate", relative))
            continue
        seen_paths[key] = cel
        if directory and directory not in folders:
            folders.append(directory)
        files.append((relative, cel))

    for template in layout.extra_folders:
        extra = render(template, base).strip("/")
        if not extra:
            continue
        if any(invalid_name_reason(part) for part in _split_path(extra)):
            problems.append(("bad_name", extra, "extra_folder"))
            continue
        if extra not in folders:
            folders.append(extra)

    timesheet_name = render(layout.timesheet, base)
    if invalid_name_reason(timesheet_name) or "/" in timesheet_name:
        problems.append(("bad_name", timesheet_name, "timesheet"))
    timesheet_path = f"{timesheet_name}.{layout.timesheet_format}"
    sheet_folder = render(layout.timesheet_folder, base).strip("/")
    if sheet_folder:
        if any(invalid_name_reason(part) for part in _split_path(sheet_folder)):
            problems.append(("bad_name", sheet_folder, "timesheet_folder"))
        else:
            if sheet_folder not in folders:
                folders.append(sheet_folder)
            timesheet_path = f"{sheet_folder}/{timesheet_path}"

    # Deduplicate while keeping the first occurrence of each problem.
    unique = []
    for problem in problems:
        if problem not in unique:
            unique.append(problem)
    return ExportPlan(
        folder_name=folder_name,
        files=files,
        timesheet_path=timesheet_path,
        folders=folders,
        problems=unique,
        timesheet_format=layout.timesheet_format,
        sheet_header={
            name: str(values.get(name, "") or "").strip()
            for name in ("cut", "episode", "scene")
        },
    )


def guess_cut_number(name: str) -> str:
    """The cut number in a project name, for pre-filling カット名.

    Prefers digits introduced by c/C/cut (``s03_c012_v2`` -> ``012``) and falls
    back to the last run of digits (``shot7`` -> ``7``).
    """
    if not name:
        return ""
    match = re.search(r"(?:^|[^a-z])(?:cut|c)[_-]?(\d+)", name, re.IGNORECASE)
    if match:
        return match.group(1)
    runs = re.findall(r"\d+", name)
    return runs[-1] if runs else ""
