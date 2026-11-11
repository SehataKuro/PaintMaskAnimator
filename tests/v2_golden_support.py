"""Source-side golden observations: no payload builder or v2 reader oracle."""
from copy import deepcopy
from dataclasses import replace
import json

from paintmaskanimator import project_io
from paintmaskanimator.document import Document, project_v1_state
from paintmaskanimator.models import Frame
from p5_6_support import ROOT, image, observe, pixels, slot, source

V2_ROOT = ROOT / "v2"
CASES = ("basic-bank-sheet", "pending-compat", "paper-draft", "tween-initial-display")


def source_snapshot(case):
    # Frozen A inputs supply artwork and legacy import semantics; additions below
    # deliberately cover combinations absent from any single historical file.
    origin = {"basic-bank-sheet": "archive-conflict", "pending-compat": "pending-duplicate",
              "paper-draft": "numbered-paper-draft", "tween-initial-display": "tween-initial"}[case]
    loaded = project_io.load_project(ROOT / "v1" / f"{origin}.pman")
    historical = json.loads((ROOT / "expected" / f"{origin}.json").read_text(encoding="utf-8"))["read_install"]
    for track, old in zip(loaded.document.tracks, historical["tracks"]):
        assert {str(n): {"image": pixels(c.image), "initial": pixels(c.initial_image),
                         "cell_name": c.cell_name} for n, c in track.cells.items()} == old["bank"]
    metadata = deepcopy(loaded.metadata)
    if case == "basic-bank-sheet":
        frames = [Frame([s], i % 3 + 1) for i, s in enumerate([
            slot(3, "red", has_content=True, initial_image=image("blue"), cell_name="first"),
            slot(3, "red", has_content=True),
            slot(12, "yellow", is_blank_key=True, initial_image=image("blue"), cell_name="retained"),
            slot(is_blank_key=True), slot(None, "cyan", initial_image=image("green"), cell_name="local"),
            slot(3, "red", has_content=True), slot()])]
        archive = {(0, 1): slot(1, "green", has_content=True, cell_name="unplaced-1"),
                   (0, 9): slot(9, "blue", has_content=True, cell_name="unplaced-9")}
    elif case == "pending-compat":
        # A's duplicate/None/old-key-boundary pending tracks.
        inputs = [source(name) for name in
                  ("pending-duplicate", "pending-none", "placement-key-boundary")]
        frames = [Frame([rows[i].layers[0] if i < len(rows) else slot()
                         for rows, _ in inputs], i % 3 + 1) for i in range(7)]
        archive = {}
    elif case == "paper-draft":
        frames, archive = source(origin)
        # Paper also has a second numbered drawing; retain ○ and EMPTY.
        frames.append(Frame([slot(7, "yellow", has_content=True, is_paper=True),
                             slot(is_draft=True), slot()]))
    else:
        return project_io.ProjectSnapshot(metadata | {
            "display": {"onion_skin": True, "onion_previous_count": 2,
                        "onion_previous_levels": [80, 35], "onion_next_shift_x": 3.5,
                        "onion_next_rotation": 15, "onion_next_scale": 110,
                        "silhouette_non_background": True},
            "colors": {"main": "#123456", "sub": "#ABCDEF", "mode": "sub",
                       "background": "#EEEEEE"}, "playback_range": [1, 3]},
            loaded.document.clone_graph(), 4, 3)
    document = Document()
    document.install_legacy_frames(frames, archive, canvas_size=(4, 3))
    document.current_frame = 0
    document.active_layer_index = 0
    for track in document.tracks:
        track.alpha_locked = False
    if case == "pending-compat":
        first = document.frames[0].layers[0]
        first._v1_position = replace(first._v1_position, input_length=8)
    metadata.update(current_frame=0, active_layer_index=0)
    return project_io.ProjectSnapshot(metadata, document, 4, 3)


def low_observation(window, document):
    window.canvas._document = document
    window.canvas._invalidate_exposure_index()
    silhouette = window.canvas.silhouette_non_background
    window.canvas.silhouette_non_background = False
    try:
        result = observe(window)
    finally:
        window.canvas.silhouette_non_background = silhouette
    result.pop("ui")
    projected, _ = project_v1_state(document.frames)
    result["projection"] = [[{
        "content": s.has_content, "blank": s.is_blank_key,
        "number": s.sequence_number if s.has_content or s.is_blank_key or s.sequence_only else None,
        "pending": s.sequence_only, "length": s.exposure}
        for s in f.layers] for f in projected]
    input_lengths = []
    for column, frame in enumerate(document.frames):
        row = []
        for li, s in enumerate(frame.layers):
            old = s.__dict__.get("_v1_position")
            imported = s.__dict__.get("_dense_import")
            signature = (s.has_content, s.is_blank_key, s.sequence_number,
                         s.sequence_only, s.blank_start)
            legacy = projected[column].layers[li]
            length = legacy.exposure if not legacy.sequence_only and (
                legacy.has_content or legacy.is_blank_key) else None
            if (imported is not None and imported[0] == signature
                    and s.__dict__.get("_v2_length_imported", True)
                    and old is not None and old.legacy is not None
                    and not old.legacy.sequence_only
                    and (old.legacy.has_content or old.legacy.is_blank_key)):
                length = old.input_length if old.input_length is not None else old.legacy.exposure
            row.append(length)
        input_lengths.append(row)
    result["input_lengths"] = input_lengths
    return json.loads(json.dumps(result))


def source_expectation(window, snapshot, patch):
    low = low_observation(window, snapshot.document.clone_graph())
    loaded = project_io.LoadedProject(deepcopy(snapshot.metadata), snapshot.document.clone_graph(), 4, 3, 2)
    # Exercise UI preparation directly on the source, without serialized output.
    with patch.context() as scoped:
        scoped.setattr(project_io, "load_project", lambda *_: loaded)
        assert window.project.open(V2_ROOT / "source.pman")
    return {"low": low, "ui": json.loads(json.dumps(observe(window)))}
