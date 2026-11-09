"""Freeze the v1 boundary against output captured before its extraction."""
import copy
from dataclasses import fields
import hashlib
import json
import zipfile

from PySide6.QtGui import QImage

from paintmaskanimator import project_io
from paintmaskanimator.models import Frame, Layer, TRACK_FIELDS
from paintmaskanimator.tween_groups import make_spec


def document():
    image = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0xff123456)
    layers = [Layer("A", QImage(image), has_content=True, sequence_number=3,
                    cell_name="原画", initial_image=QImage(image)),
              Layer("A", QImage(image), has_content=True, sequence_number=4),
              Layer("A", QImage(image), has_content=True, sequence_number=3,
                    exposure=2),
              Layer("A", QImage(image)),
              Layer("A", QImage(image), is_blank_key=True),
              Layer("A", QImage(image), sequence_only=True, sequence_number=8)]
    layers[0].tween = make_spec("fixed-tween", 2, "free", False, [], [])
    layers[1].tween_member = "fixed-tween"
    archived = Layer("A", QImage(image), has_content=True, sequence_number=9,
                     cell_name="保管", initial_image=QImage(image))
    return [Frame([layer], 2 if i == 0 else 1) for i, layer in enumerate(layers)], {(0, 9): archived}


def metadata():
    return {"format": "PaintMaskAnimatorProject", "format_version": 1,
            "mask_format": 2, "canvas": {"width": 2, "height": 2}, "frames": []}


def json_bytes(data):
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


# Filled from the original writer, before moving its conversion loop.
LEGACY_JSON_SHA256 = "4b479aac8bb1cc06017bf6df0761531b56f1828a33e41f52f01d5fa4208ff4ea"
LEGACY_PNG_SHA256 = "d3fba234bbff8ce0849d2f216baee0fd536ea9f21419dc4764f57f42a5ca4970"


def test_payload_matches_original_v1_metadata_and_entry_order(tmp_path):
    frames, archive = document()
    frame_data, archive_data, pngs = project_io.build_v1_payload(frames, archive)
    data = metadata()
    data["frames"] = frame_data
    data["sequence_archive"] = archive_data
    assert hashlib.sha256(json_bytes(data)).hexdigest() == LEGACY_JSON_SHA256
    path = tmp_path / "payload.pman"
    project_io.write_project_archive(path, metadata(), frames, archive)
    with zipfile.ZipFile(path) as saved:
        assert saved.read("project.json") == json_bytes(data)
        assert saved.namelist() == [name for name, _image, _error in pngs] + ["project.json"]


def test_saved_json_and_png_bytes_match_original_writer(tmp_path):
    frames, archive = document()
    path = tmp_path / "original.pman"
    project_io.write_project_archive(path, metadata(), frames, archive)
    with zipfile.ZipFile(path) as saved:
        assert hashlib.sha256(saved.read("project.json")).hexdigest() == LEGACY_JSON_SHA256
        pngs = [(name, hashlib.sha256(saved.read(name)).hexdigest())
                for name in saved.namelist() if name.endswith(".png")]
    assert hashlib.sha256(json_bytes(pngs)).hexdigest() == LEGACY_PNG_SHA256


def test_v1_save_load_save_preserves_json(tmp_path):
    frames, archive = document()
    first, second = tmp_path / "first.pman", tmp_path / "second.pman"
    project_io.write_project_archive(first, metadata(), frames, archive)
    data, loaded, width, height = project_io.read_project_archive(first)
    assert (width, height) == (2, 2)
    loaded_archive = data.pop("_loaded_sequence_archive")
    # The reader adds absent application version fields; they are not document data.
    for key in ("created_app_version", "saved_app_version"):
        data.pop(key, None)
    data["frames"] = []
    project_io.write_project_archive(second, data, loaded, loaded_archive)
    with zipfile.ZipFile(first) as a, zipfile.ZipFile(second) as b:
        assert a.read("project.json") == b.read("project.json")


def test_build_payload_does_not_mutate_document():
    frames, archive = document()

    def state():
        def layer_state(layer):
            values = []
            for field in fields(Layer):
                value = getattr(layer, field.name)
                values.append((field.name, (id(value), value.cacheKey())
                               if isinstance(value, QImage) else copy.deepcopy(value)))
            return id(layer), id(layer._cell), id(layer._track), values
        return (id(frames), [(id(frame), frame.duration, id(frame.layers),
                             [layer_state(layer) for layer in frame.layers]) for frame in frames],
                id(archive), [(key, layer_state(layer)) for key, layer in archive.items()])

    before = state()
    project_io.build_v1_payload(frames, archive)
    assert state() == before


def test_track_fields_are_only_eight_display_settings():
    assert TRACK_FIELDS == ("name", "visible", "opacity", "is_paper", "is_draft",
                            "alpha_locked", "color_filter_enabled", "color_filter_rgb")
