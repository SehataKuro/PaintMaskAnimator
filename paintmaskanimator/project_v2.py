"""Number-bank v2 payload preparation, independent of ZIP and PNG encoding.

Compatibility contains scalar placement/length/resolution exceptions and current
local artwork only. Historical v1 image payloads never enter the PNG list.
"""
from copy import deepcopy
from bisect import bisect_right

from .i18n import tr
from .document import project_v1_state
from .timeline_metadata import reference_for


DISPLAY_FIELDS = (
    "name", "visible", "opacity", "is_paper", "is_draft",
    "color_filter_enabled", "color_filter_rgb",
)


def sheet_value(reference):
    return reference.number if reference.kind == "CELL" else (
        "BLANK" if reference.kind == "BLANK" else None)


def placement_keys(sheet, blank_starts, pending):
    """Derive ordinary keys from a number column without legacy boundaries."""
    result = []
    column = 0
    while column < len(sheet):
        value = sheet[column]
        end = column + 1
        while end < len(sheet) and sheet[end] == value:
            if value == "BLANK" and blank_starts[end]:
                break
            end += 1
        if value is not None and column not in pending:
            result.append({"column": column, "kind": "BLANK" if value == "BLANK" else "CELL",
                           "number": None if value == "BLANK" else value,
                           "length": end - column})
        column = end
    return result


def compatibility_scalars(sheet, blank_starts, pending, projected, inputs, resolved):
    """Pure conversion of image-free observations to the three scalar lists.

    ``inputs`` carries an original length only while its import guard is valid.
    No guard is refreshed here, so editing cannot revive an expired exception.
    ``resolved`` holds (JSON sheet value, editing source column) pairs.
    """
    ordinary = placement_keys(sheet, blank_starts, pending)
    keys = [dict(entry) for entry in projected if entry["column"] not in pending]
    result = {}
    if keys != ordinary:
        result["placement_keys"] = keys
    lengths = {entry["column"]: entry["length"] for entry in ordinary}
    overrides = []
    by_column = {entry["column"]: entry for entry in keys}
    for column in sorted(by_column.keys() | inputs.keys()):
        entry = by_column.get(column)
        length = entry["length"] if entry is not None else 1
        original = inputs.get(column)
        normal = lengths.get(column, 1)
        if length == normal and (original is None or original["length"] == normal):
            continue
        value = sheet[column]
        guard = {"kind": "BLANK" if value == "BLANK" else (
            "EMPTY" if value is None else "CELL"),
            "number": value if type(value) is int else None,
            "pending": column in pending, "blank_start": blank_starts[column],
            "imported": original is not None}
        override = {"column": column, "length": length, "guard": guard}
        if original is not None:
            override["input_length"] = original["length"]
        overrides.append(override)
    if overrides:
        result["length_overrides"] = overrides
    differences = []
    run_start = 0
    for column, (value, source) in enumerate(resolved):
        if column == 0 or sheet[column] != sheet[column - 1]:
            run_start = column
        ordinary_value = sheet[column] if type(sheet[column]) is int else None
        ordinary_source = run_start if ordinary_value is not None else None
        if (value, source) != (ordinary_value, ordinary_source):
            differences.append({"column": column, "value": value, "source_column": source})
    if differences:
        result["resolved_overrides"] = differences
    return result


def build_payload(snapshot, *, include_default_local_images=False):
    """Return a new metadata dictionary and ordered (path, QImage, error) PNGs.

    The snapshot must contain an installed dense document. This builder never
    numbers drawings, synthesizes hints, encodes images or mutates metadata.
    Validation and atomic archive publication belong to the v2 writer boundary.
    """
    from .native import resolve_timeline

    document = snapshot.document
    frames = document.frames
    projected, _archive = project_v1_state(frames)
    resolved_sources = resolve_timeline(frames)
    payload = deepcopy(snapshot.metadata)
    for key in ("frames", "sequence_archive", "_loaded_sequence_archive"):
        payload.pop(key, None)
    payload.update(format="PaintMaskAnimatorProject", format_version=2, mask_format=2,
                   canvas={"width": snapshot.width, "height": snapshot.height},
                   frame_durations=[frame.duration for frame in frames], tracks=[])
    pngs = []

    def artwork(cell, path):
        entry = {"image": path, "initial": None, "cell_name": cell.cell_name}
        pngs.append((path, cell.image, "画像を書き出せませんでした: {name}"))
        if cell.initial_image is not None:
            entry["initial"] = f"initial/{path}"
            pngs.append((entry["initial"], cell.initial_image,
                         "初期状態の画像を書き出せませんでした: {name}"))
        return entry

    for li, track in enumerate(document.tracks):
        data = {key: deepcopy(getattr(track, key)) for key in DISPLAY_FIELDS}
        if data["color_filter_rgb"] is not None:
            data["color_filter_rgb"] = list(data["color_filter_rgb"])
        data.update(cells=[], sheet=[], positions=[], pending=[], drawing_states=[])
        for number, cell in sorted(track.cells.items()):
            data["cells"].append({"number": number, **artwork(
                cell, f"layers/layer_{li:04d}/cell_{number:06d}.png")})
        for number, hint in sorted(track.legacy_archive_hints.items()):
            data["drawing_states"].append({
                "number": number, "has_content": hint.has_content,
                "is_blank_key": hint.is_blank_key, "length": hint.exposure,
                "pending": hint.sequence_only, "tween": deepcopy(hint.tween),
                "tween_member": deepcopy(hint.tween_member)})
        starts, pending, keys, inputs, resolved, locals_ = [], set(), [], {}, [], []
        for column, frame in enumerate(frames):
            slot = frame.layers[li]
            reference = reference_for(slot)
            data["sheet"].append(sheet_value(reference))
            starts.append(bool(slot.blank_start))
            position = {"column": column}
            if slot.blank_start:
                position["blank_start"] = True
            if slot.tween is not None:
                position["tween"] = deepcopy(slot.tween)
            if slot.tween_member is not None:
                position["tween_member"] = deepcopy(slot.tween_member)
            if len(position) > 1:
                data["positions"].append(position)
            legacy = projected[column].layers[li]
            if slot.sequence_only:
                pending.add(column)
                data["pending"].append({
                    "column": column, "number": slot.sequence_number,
                    "kind": "CELL" if slot.has_content else (
                        "BLANK" if slot.is_blank_key else "EMPTY"),
                    "length": max(1, legacy.exposure)})
            if not legacy.sequence_only and (legacy.has_content or legacy.is_blank_key):
                keys.append({"column": column,
                             "kind": "CELL" if legacy.has_content else "BLANK",
                             "number": legacy.sequence_number if legacy.has_content else None,
                             "length": max(1, legacy.exposure)})
            imported = slot.__dict__.get("_dense_import")
            old = slot.__dict__.get("_v1_position")
            signature = (slot.has_content, slot.is_blank_key, slot.sequence_number,
                         slot.sequence_only, slot.blank_start)
            if (imported is not None and imported[0] == signature and old is not None
                    and slot.__dict__.get("_v2_length_imported", True)):
                original = old.legacy
                if original is not None and not original.sequence_only and (
                        original.has_content or original.is_blank_key):
                    inputs[column] = {"length": max(1, old.input_length if old.input_length is not None
                                                     else original.exposure)}
            source = int(resolved_sources[column, li])
            value = sheet_value(reference_for(slot, resolve=True))
            resolved.append((value if type(value) is int else None,
                             source if source >= 0 else None))
            # Only live local Cells are collected, never old.legacy artwork.
            if not ((slot.has_content or slot.sequence_only) and slot.sequence_number is not None):
                from PySide6.QtGui import QImage
                cell = slot._cell
                local = {"column": column, "retained_number": slot.sequence_number}
                keep_image = (include_default_local_images or cell.image.isNull()
                              or not cell.image.hasAlphaChannel()
                              or cell.initial_image is not None or cell.cell_name is not None)
                if not keep_image:
                    transparent = QImage(cell.image.size(), cell.image.format())
                    transparent.fill(0)
                    keep_image = cell.image != transparent
                if keep_image:
                    local.update(artwork(cell,
                        f"compatibility/layer_{li:04d}/position_{column:06d}.png"))
                locals_.append(local)
        # Match the reader's reconstructed source slots, not virtual resolve
        # tails retained by legacy imports. Pending takes precedence over sheet.
        states = {entry["column"]: entry for entry in data["pending"]}
        sources = {}
        for column, value in enumerate(data["sheet"]):
            state = states.get(column)
            if state is not None:
                value = state["number"]
            if type(value) is int:
                sources.setdefault(value, []).append(column)
        for column, (value, source) in enumerate(resolved):
            candidates = sources.get(value, [])
            if source is not None and candidates and source not in candidates:
                # Compare live source artwork with the bank artwork used on load.
                # This pixel comparison belongs only to payload preparation.
                original = frames[source].layers[li].image
                bank = track.cells[value].image
                if original is not bank and original != bank:
                    continue
                index = bisect_right(candidates, source)
                resolved[column] = (value, candidates[max(0, index - 1)])
        compatibility = compatibility_scalars(
            data["sheet"], starts, pending, keys, inputs, resolved)
        if locals_:
            compatibility["local_states"] = locals_
        if compatibility:
            data["compatibility"] = compatibility
        payload["tracks"].append(data)
    return payload, pngs


def _decode_payload(payload, archive, png_cache=None):
    """Build a private dense graph directly, without legacy installation."""
    import math
    import struct
    from pathlib import PurePosixPath
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from . import project_io as io
    from .document import Document
    from .errors import OperationError
    from .legacy_v1 import DenseReference, LegacyLayerV1, V1Position
    from .models import ArchiveHint, Cell, Frame, Layer, Track, default_layer_name
    from .tween_groups import sanitize_spec

    def require(ok):
        if not ok:
            raise OperationError(tr("プロジェクトの構造が不正です。"))

    def integer(value, lower, upper):
        require(type(value) is int and lower <= value <= upper)
        return value

    def boolean(value):
        require(type(value) is bool)
        return value

    def array(value):
        require(isinstance(value, list))
        return value

    def mapping(value):
        require(isinstance(value, dict))
        return value

    def number(value, cells, nullable=False):
        if nullable and value is None:
            return value
        integer(value, 1, io.MAX_PROJECT_FRAMES)
        require(value in cells)
        return value

    def reference(value, cells):
        if value is None:
            return DenseReference()
        if value == "BLANK":
            return DenseReference("BLANK")
        return DenseReference("CELL", number(value, cells))

    def finite(value):
        if isinstance(value, float):
            require(math.isfinite(value))
        elif isinstance(value, dict):
            for child in value.values():
                finite(child)
        elif isinstance(value, list):
            for child in value:
                finite(child)
    finite(payload)
    integer(payload.get("mask_format", 1), 1, io.MAX_PROJECT_FRAMES)
    canvas = mapping(payload.get("canvas"))
    width = integer(canvas.get("width"), 1, 16384)
    height = integer(canvas.get("height"), 1, 16384)
    durations = array(payload.get("frame_durations"))
    count = integer(len(durations), 1, io.MAX_PROJECT_FRAMES)
    for duration in durations:
        integer(duration, 1, io.MAX_PROJECT_FRAMES)
    tracks_data = array(payload.get("tracks"))
    integer(len(tracks_data), 1, io.MAX_PROJECT_LAYERS)
    require(count * len(tracks_data) <= io.MAX_PROJECT_LAYER_CELLS)
    # Count all artwork before decoding any PNG.
    total = 0
    for data in tracks_data:
        mapping(data)
        total += len(array(data.get("cells")))
        total += len(array(mapping(data.get("compatibility", {})).get("local_states", [])))
    require(total <= io.MAX_PROJECT_LAYER_CELLS)
    for info in archive.infolist():
        name = info.filename
        normalized = PurePosixPath(name)
        require(not normalized.is_absolute() and ".." not in normalized.parts
                and "\\" not in name and ":" not in name)
    decoded, seeded = {}, []

    def blank():
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        return image

    def image(path, track):
        require(isinstance(path, str) and bool(path))
        normalized = PurePosixPath(path)
        require(not normalized.is_absolute() and ".." not in normalized.parts
                and "\\" not in path and ":" not in path
                and normalized.suffix.lower() == ".png")
        try:
            info = archive.getinfo(path)
        except KeyError as error:
            raise OperationError(tr("プロジェクト内の画像がありません。")) from error
        require(info.file_size <= io.MAX_PROJECT_IMAGE_BYTES)
        blob = archive.read(path)
        require(len(blob) >= 24 and blob[:8] == b"\x89PNG\r\n\x1a\n"
                and blob[12:16] == b"IHDR")
        require(struct.unpack(">II", blob[16:24]) == (width, height))
        if blob not in decoded:
            require((len(decoded) + 1) * width * height <= io.MAX_PROJECT_DECODED_PIXELS)
            result = io._decode_png(blob)
            require(not result.isNull() and result.width() == width and result.height() == height)
            decoded[blob] = result
        result = decoded[blob]
        if payload.get("mask_format", 1) < io.CURRENT_MASK_FORMAT and not (
                track.is_paper or track.is_draft):
            result = io.white_to_transparent_qimage(result)
        if payload.get("mask_format", 1) >= io.CURRENT_MASK_FORMAT or track.is_paper or track.is_draft:
            seeded.append((result, blob))
        return QImage(result)

    def artwork(entry, track, optional=False):
        name = entry.get("cell_name")
        require(name is None or isinstance(name, str))
        initial = entry.get("initial")
        return Cell(image(entry["image"], track) if "image" in entry else (
            blank() if optional else image(None, track)),
            image(initial, track) if initial is not None else None, name)

    def indexed(entries):
        result = {}
        for entry in array(entries):
            mapping(entry)
            column = integer(entry.get("column"), 0, count - 1)
            require(column not in result)
            result[column] = entry
        return result

    document = object.__new__(Document)
    document.frames = [Frame([], duration) for duration in durations]
    document.tracks = []
    for li, data in enumerate(tracks_data):
        track = Track(default_layer_name(li))
        for key in DISPLAY_FIELDS:
            if key in data:
                setattr(track, key, deepcopy(data[key]))
        require(isinstance(track.name, str))
        for key in ("visible", "is_paper", "is_draft", "color_filter_enabled"):
            boolean(getattr(track, key))
        require(type(track.opacity) in (int, float) and math.isfinite(track.opacity))
        track.opacity = min(1.0, max(0.0, track.opacity))
        if track.color_filter_rgb is not None:
            require(isinstance(track.color_filter_rgb, (list, tuple))
                    and len(track.color_filter_rgb) == 3)
            for component in track.color_filter_rgb:
                integer(component, 0, 255)
            track.color_filter_rgb = tuple(track.color_filter_rgb)
        for entry in data["cells"]:
            mapping(entry)
            n = integer(entry.get("number"), 1, io.MAX_PROJECT_FRAMES)
            require(n not in track.cells)
            track.cells[n] = artwork(entry, track)
        sheet = array(data.get("sheet"))
        require(len(sheet) == count)
        refs = [reference(value, track.cells) for value in sheet]
        positions = indexed(data.get("positions", []))
        pending = indexed(data.get("pending", []))
        compatibility = mapping(data.get("compatibility", {}))
        locals_ = indexed(compatibility.get("local_states", []))
        resolved = indexed(compatibility.get("resolved_overrides", []))
        lengths = indexed(compatibility.get("length_overrides", []))
        starts = [False] * count
        for column, entry in positions.items():
            starts[column] = boolean(entry.get("blank_start", False))
            require(not starts[column] or sheet[column] == "BLANK"
                    or pending.get(column, {}).get("kind") == "BLANK")
        keys = indexed(compatibility.get("placement_keys", placement_keys(sheet, starts, set(pending))))
        for entry in keys.values():
            require(entry.get("kind") in ("CELL", "BLANK"))
            if entry["kind"] == "BLANK":
                require(entry.get("number") is None)
            else:
                number(entry.get("number"), track.cells)
            integer(entry.get("length"), 1, io.MAX_PROJECT_FRAMES)
        hints = set()
        for entry in array(data.get("drawing_states", [])):
            mapping(entry)
            n = number(entry.get("number"), track.cells)
            require(n not in hints)
            hints.add(n)
            require(entry.get("tween_member") is None or isinstance(entry["tween_member"], str))
            track.legacy_archive_hints[n] = ArchiveHint(
                boolean(entry.get("has_content", True)), boolean(entry.get("is_blank_key", False)),
                integer(entry.get("length", 1), 1, io.MAX_PROJECT_FRAMES),
                boolean(entry.get("pending", False)), sanitize_spec(entry.get("tween")),
                entry.get("tween_member"))
        track.connected = True
        for column, ref in enumerate(refs):
            state = pending.get(column)
            kind, n = ref.kind, ref.number
            if state is not None:
                require(state.get("kind") in ("CELL", "BLANK", "EMPTY"))
                kind = state["kind"]
                n = number(state.get("number"), track.cells, nullable=True)
                integer(state.get("length", 1), 1, io.MAX_PROJECT_FRAMES)
            local = locals_.get(column)
            if local is not None:
                require(not ((kind == "CELL" or state is not None) and n is not None))
                n = local.get("retained_number")
                if n is not None:
                    integer(n, 1, io.MAX_PROJECT_FRAMES)
            slot = Layer(track.name, blank(), has_content=kind == "CELL",
                         is_blank_key=kind == "BLANK", sequence_number=n,
                         sequence_only=state is not None, blank_start=starts[column])
            if local is not None:
                slot.__dict__["_cell"] = artwork(local, track, optional=True)
            slot.attach_track(track)
            if n is not None and (slot.has_content or slot.sequence_only):
                slot.__dict__.pop("_cell", None)
            position = positions.get(column, {})
            slot.tween = sanitize_spec(position.get("tween"))
            slot.tween_member = position.get("tween_member")
            require(slot.tween_member is None or isinstance(slot.tween_member, str))
            key = keys.get(column)
            length = lengths.get(column)
            legacy_kind = state["kind"] if state else key["kind"] if key else "EMPTY"
            legacy_number = state.get("number") if state else key.get("number") if key else n
            if legacy_kind != "CELL":
                legacy_number = n  # local retained number also belongs to the old projection
            exposure = state.get("length", 1) if state else key["length"] if key else 1
            input_length = None
            if length is not None:
                exposure = integer(length.get("length"), 1, io.MAX_PROJECT_FRAMES)
                guard = mapping(length.get("guard"))
                require(guard.get("kind") in ("CELL", "BLANK", "EMPTY"))
                number(guard.get("number"), track.cells, nullable=True)
                for field in ("pending", "blank_start", "imported"):
                    boolean(guard.get(field))
                require((guard["kind"], guard.get("number"), guard["pending"], guard["blank_start"])
                        == (ref.kind, ref.number, state is not None, starts[column]))
                slot.__dict__["_v2_length_imported"] = guard["imported"]
                input_length = integer(length.get("input_length", length["length"]),
                                   1, io.MAX_PROJECT_FRAMES)
            legacy = LegacyLayerV1(track.name, slot.image,
                has_content=legacy_kind == "CELL", is_blank_key=legacy_kind == "BLANK",
                sequence_number=legacy_number, sequence_only=state is not None,
                exposure=exposure, tween=deepcopy(slot.tween), tween_member=slot.tween_member)
            slot.__dict__["_v1_position"] = V1Position(legacy, legacy_kind in ("CELL", "BLANK"),
                                                       starts[column], state is not None, input_length)
            resolve_ref = ref
            if column in resolved:
                override = resolved[column]
                require("value" in override and "source_column" in override)
                value = override.get("value")
                number(value, track.cells, nullable=True)
                source = override.get("source_column")
                require((value is None) == (source is None))
                if source is not None:
                    integer(source, 0, count - 1)
                resolve_ref = DenseReference("CELL", value) if value is not None else DenseReference()
            signature = (slot.has_content, slot.is_blank_key, slot.sequence_number,
                         slot.sequence_only, slot.blank_start)
            slot.__dict__["_dense_import"] = (signature, ref, resolve_ref)
            if column in resolved:
                slot.__dict__["_v2_resolve_source"] = (signature, resolved[column].get("source_column"), column, count)
            document.frames[column].layers.append(slot)
        for entry in resolved.values():
            source = entry.get("source_column")
            if source is not None:
                source_slot = document.frames[source].layers[li]
                require(source_slot.sequence_number == entry["value"]
                        and (source_slot.has_content or source_slot.sequence_only))
        document.tracks.append(track)
    for key, upper in (("current_frame", count - 1), ("active_layer_index", len(document.tracks) - 1)):
        value = payload.get(key, 0)
        integer(value, 0, io.MAX_PROJECT_FRAMES)
        setattr(document, key, min(value, upper))
    metadata = deepcopy(payload)
    for key in ("tracks", "frame_durations"):
        metadata.pop(key, None)
    if png_cache is not None:
        png_cache.clear()
        for result, blob in seeded:
            png_cache.remember(result, blob)
    return io.LoadedProject(metadata, document, width, height, 2)


def write_snapshot(path, snapshot, png_cache=None):
    """Publish a validated v2 ZIP atomically."""
    import json
    import zipfile
    from pathlib import Path
    from . import project_io as io
    from .errors import OperationError

    payload, pngs = build_payload(snapshot)
    if (not payload["tracks"] or len(payload["tracks"]) > io.MAX_PROJECT_LAYERS
            or not payload["frame_durations"]
            or len(payload["frame_durations"]) > io.MAX_PROJECT_FRAMES
            or len(payload["tracks"]) * len(payload["frame_durations"]) > io.MAX_PROJECT_LAYER_CELLS
            or sum(len(t["cells"]) + len(t.get("compatibility", {}).get("local_states", []))
                   for t in payload["tracks"]) > io.MAX_PROJECT_LAYER_CELLS):
        raise OperationError(tr("プロジェクト内のセル数が多すぎます。"))
    for _, image, _ in pngs:
        if (image.isNull() or image.width() != snapshot.width or image.height() != snapshot.height):
            raise OperationError(tr("プロジェクト内の画像サイズがキャンバスと一致しません。"))
    cache = png_cache if png_cache is not None else io.PngCache()
    used = set()
    metadata = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(metadata) > io.MAX_PROJECT_METADATA_BYTES:
        raise OperationError(tr("プロジェクト情報が大きすぎます。"))
    encoded = cache.encode_many((image for _, image, _ in pngs), used)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            thumbnail = io._thumbnail_png(snapshot.thumbnail)
            if thumbnail is not None:
                archive.writestr(io.THUMBNAIL_NAME, thumbnail, compress_type=zipfile.ZIP_STORED)
            for name, image, message in pngs:
                blob = encoded.get(int(image.cacheKey()))
                if blob is None:
                    raise OperationError(tr(message).format(name=name))
                archive.writestr(name, blob, compress_type=zipfile.ZIP_STORED)
            archive.writestr("project.json", metadata)
        # The public reader applies identical ZIP, PNG and graph limits before replace.
        io.load_project(temporary)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    cache.retain_only(used)


def decode_payload(payload, archive, png_cache=None):
    """Normalize malformed external structure failures to OperationError."""
    from .errors import OperationError
    try:
        return _decode_payload(payload, archive, png_cache)
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        raise OperationError(tr("プロジェクトの構造が不正です。")) from error
