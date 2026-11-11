"""Offline capture and read-only observations for Phase 5-6 A."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from zipfile import ZipFile

from PySide6.QtGui import QColor, QImage

from paintmaskanimator import project_io
from paintmaskanimator.legacy_v1 import raw_v1_slot
from paintmaskanimator.models import Frame
from paintmaskanimator.sheet import sheet_from_frames

ROOT = Path(__file__).parent / "fixtures" / "projects"
CASES = (
    "unnumbered-mask1", "numbered-paper-draft", "pending-collision-7-6",
    "pending-collision-2-2", "hidden-retained", "archive-conflict",
    "pending-none", "pending-duplicate", "blank-self-collision", "missing",
    "all-unplaced", "legacy-attribute-conflict", "tween-initial", "placement-key-boundary",
)
SETTINGS = ("name", "visible", "opacity", "is_paper", "is_draft", "alpha_locked",
            "color_filter_enabled", "color_filter_rgb")


def image(color="transparent"):
    result = QImage(4, 3, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(color))
    result.setPixelColor(1, 1, QColor(20, 80, 160, 128))
    result.setPixelColor(2, 1, QColor("white"))
    return result


def slot(number=None, color="transparent", **kwargs):
    return raw_v1_slot("A", image(color), sequence_number=number, **kwargs)


def source(case):
    slots = [slot(1, "red", has_content=True, exposure=4), slot(), slot(),
             slot(), slot(), slot(2, "green", has_content=True, sequence_only=True)]
    archive = {}
    if case.startswith("pending-collision"):
        slots[2] = slot(7 if case.endswith("7-6") else 2, "blue", has_content=True)
    elif case == "pending-none":
        slots[5] = slot(sequence_only=True)
    elif case == "pending-duplicate":
        slots[4] = slot(2, "green", has_content=True, sequence_only=True)
    elif case == "missing":
        slots[0] = slot(8, "red", has_content=True, exposure=4)
    elif case == "blank-self-collision":
        slots = [slot(1, "red", has_content=True), slot(2, is_blank_key=True),
                 slot(2, is_blank_key=True, sequence_only=True)]
    elif case == "all-unplaced":
        slots = [slot(), slot(), slot()]
        archive = {(0, n): slot(n, color, has_content=True)
                   for n, color in ((1, "red"), (8, "green"), (12, "blue"))}
    elif case == "unnumbered-mask1":
        slots = [slot(None, "white", has_content=True, exposure=2), slot(),
                 slot(None, "blue", has_content=True, initial_image=image("red"), cell_name="無番号")]
    elif case == "hidden-retained":
        slots = [slot(1, "red", has_content=True),
                 slot(12, "yellow", is_blank_key=True, exposure=2,
                      initial_image=image("blue"), cell_name="retained"),
                 slot(None, "cyan", initial_image=image("green"), cell_name="local"),
                 slot(is_blank_key=True), slot(is_blank_key=True), slot()]
    elif case in ("archive-conflict", "legacy-attribute-conflict"):
        slots = [slot(3, "red", has_content=True, initial_image=image("blue"), cell_name="first"),
                 slot(3, "green", has_content=True, initial_image=image("yellow"),
                      cell_name="second", visible=False, opacity=0.4, is_draft=True), slot()]
        archive = {(0, 3): slot(3, "cyan", has_content=False, is_blank_key=True,
                                initial_image=image("magenta"), cell_name="archive"),
                   (0, 9): slot(9, "blue", has_content=True, cell_name="unplaced")}
    elif case == "placement-key-boundary":
        slots = [slot(1, "red", has_content=True, exposure=2), slot(),
                 slot(1, "red", has_content=True, exposure=2), slot(),
                 slot(9, "blue", has_content=True, sequence_only=True, exposure=2), slot()]
    elif case == "tween-initial":
        slots = [slot(3, "red", has_content=True, initial_image=image("blue"),
                      cell_name="tween", tween={"id": "fixed", "length": 2, "mode": "free"}),
                 slot(3, "red", has_content=True, tween_member="fixed"), slot()]
    frames = [Frame([s], duration=i % 3 + 1) for i, s in enumerate(slots)]
    if case == "numbered-paper-draft":
        frames = [Frame([slot(3, "white", has_content=True, is_paper=True),
                         slot(8, "white", has_content=True, is_draft=True),
                         slot(1, "red", has_content=True, alpha_locked=True,
                              opacity=0.6, color_filter_enabled=True, color_filter_rgb=(80, 120, 160))]),
                  Frame([slot(4, is_blank_key=True, is_paper=True),
                         slot(9, "blue", has_content=True, is_draft=True), slot()]),
                  Frame([slot(is_paper=True), slot(is_draft=True), slot()])]
    return frames, archive


def create_v1(case, path):
    frames, archive = source(case)
    metadata = {"format": "PaintMaskAnimatorProject", "format_version": 1,
                "mask_format": 2, "canvas": {"width": 4, "height": 3},
                "frames": [], "current_frame": (1 if case == "legacy-attribute-conflict" else len(frames) - 1),
                "active_layer_index": 0,
                "timeline_mode": "sequence", "fps": 18, "memo": "固定採取",
                "playback_range": [0, None]}
    project_io.legacy_fixture_write_archive(path, metadata, frames, archive)
    if case == "unnumbered-mask1":
        # Historical mask flag; PNGs intentionally retain opaque white.
        with ZipFile(path) as z:
            entries = {name: z.read(name) for name in z.namelist()}
        data = json.loads(entries["project.json"])
        data["mask_format"] = 1
        entries["project.json"] = json.dumps(data).encode()
        with ZipFile(path, "w") as z:
            for name, data in entries.items():
                z.writestr(name, data)


def pixels(img):
    if img is None:
        return None
    rgba = img.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
    raw = bytes(rgba.constBits())
    packed = b"".join(raw[y * rgba.bytesPerLine():y * rgba.bytesPerLine() + rgba.width() * 4]
                      for y in range(rgba.height()))
    return {"size": [rgba.width(), rgba.height()], "sha256": hashlib.sha256(packed).hexdigest()}


def pending_length(slot):
    position = slot.__dict__.get("_v1_position")
    return position.legacy.exposure if position is not None and position.legacy is not None else 1


def observe(w):
    c = w.canvas
    sheet = sheet_from_frames(c.frames)
    names, rows, duration = w.export.sheet_tracks()
    metadata = w.build_project_metadata()
    ui_metadata = {k: metadata[k] for k in ("colors", "display", "pressure", "playback_range",
                                           "used_color_categories", "color_chart")}
    return {"render": [pixels(c.composite(i, white=False)) for i in range(len(c.frames))],
            "sheet": [[e.number if isinstance(e.number, int) else repr(e.number) for e in r] for r in sheet],
            "positions": [[{"span": c.timeline_block_at(i, li), "blank_start": s.blank_start, "resolve": c.resolve_key_frame(i, li),
                            "tween": s.tween, "member": s.tween_member,
                            "retained_number": s.sequence_number,
                            "local_image": pixels(s.image), "initial": pixels(s.initial_image),
                            "cell_name": s.cell_name} for li, s in enumerate(f.layers)]
                          for i, f in enumerate(c.frames)],
            "tracks": [{"settings": {k: getattr(t, k) for k in SETTINGS},
                        "bank": {str(n): {"image": pixels(v.image), "initial": pixels(v.initial_image),
                                          "cell_name": v.cell_name} for n, v in sorted(t.cells.items())},
                        "hints": {str(n): asdict(h) for n, h in sorted(t.legacy_archive_hints.items())},
                        "pending": [{"column": e.column, "number": e.slot.sequence_number,
                                     "content": e.slot.has_content, "blank": e.slot.is_blank_key,
                                     "length": pending_length(e.slot)}
                                    for e in c._document.pending_entries(t)],
                        "unplaced": sorted(c._document.unplaced_numbers(t))} for t in c._document.tracks],
            "durations": [f.duration for f in c.frames], "sheet_duration": w._sheet_duration(),
            "export": {"names": names, "rows": rows, "duration": duration},
            "ui": {"mode": c.timeline_mode, "cursor": c.current_frame,
                   "active": c.active_layer_index, "undo": [type(e).__name__ for e in c.undo_stack],
                   "redo": [type(e).__name__ for e in c.redo_stack], "fps": w.timeline.fps.value(),
                   "memo": w.project_memo, "metadata": ui_metadata, "dirty": w.project.has_unsaved_changes()}}


def capture(w, path, temp):
    metadata, frames, _width, _height = project_io.read_project_archive(path)
    c = w.canvas
    c.current_frame = metadata["current_frame"]
    c.active_layer_index = 0
    c.install_legacy_frames(frames, metadata.get("_loaded_sequence_archive", {}))
    result = {"read_install": observe(w)}
    c.timeline_mode = "sequence"
    w.timeline_ops.set_mode("sheet")
    result["mode_placement"] = observe(w)
    assert w.project.open(path)
    result["ui_open"] = observe(w)
    saved = temp / "roundtrip.pman"
    assert w.project.write(saved)
    assert w.project.open(saved)
    result["saved_reopened"] = observe(w)
    c.timeline_mode = "sequence"
    assert c.insert_sequence_blank(0, max(c._document.tracks[0].cells, default=0))
    w.timeline_ops.set_mode("sheet")
    result["additional_pending_placed"] = observe(w)
    assert w.project.write(saved)
    assert w.project.open(saved)
    result["additional_saved_reopened"] = observe(w)
    c.current_frame = 0
    c.delete_frame()
    result["delete"] = observe(w)
    c.undo()
    result["undo"] = observe(w)
    c.redo()
    result["redo"] = observe(w)
    assert w.project.write(saved)
    assert w.project.open(saved)
    result["delete_saved_reopened"] = observe(w)
    # Recall bank-only/retained artwork, then edit an empty/blank position.
    number = max(c._document.tracks[0].cells, default=None)
    if number is not None:
        w.timeline_ops.recall_sequence_number(len(c.layers) - 1, len(c.frames) - 1, number)
        result["recall"] = observe(w)
        c.undo()
        result["recall_undo"] = observe(w)
    c.current_frame = len(c.frames) - 1
    c.ensure_editable_key()
    c.push_layer_undo()
    c.active_layer.image.setPixelColor(0, 0, QColor("magenta"))
    c.cellChanged.emit(c.current_frame, c.active_layer_index)
    result["paint"] = observe(w)
    c.undo()
    result["paint_undo"] = observe(w)
    c.redo()
    result["paint_redo"] = observe(w)
    return json.loads(json.dumps(result))
