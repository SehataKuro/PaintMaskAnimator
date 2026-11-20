"""Freeze the v1 boundary against output captured before its extraction."""
import copy
from dataclasses import fields
import hashlib
import json
import zipfile

from PySide6.QtGui import QImage

from paintmaskanimator.pma_image import as_qimage

from paintmaskanimator import native, project_io
from paintmaskanimator.models import Frame, Layer, TRACK_FIELDS
from paintmaskanimator.legacy_v1 import raw_v1_slot
from paintmaskanimator.document import project_v1_state
from paintmaskanimator.tween_groups import make_spec


def document():
    image = QImage(2, 2, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0xff123456)
    # A new QImage takes its resolution from the screen once a QApplication
    # exists (96 dpi offscreen) and 100 dpi before that. PNG stores it in pHYs,
    # so the byte hashes below would depend on which tests ran first.
    image.setDotsPerMeterX(3937)
    image.setDotsPerMeterY(3937)
    layers = [raw_v1_slot("A", QImage(image), has_content=True, sequence_number=3,
                    cell_name="原画", initial_image=QImage(image)),
              raw_v1_slot("A", QImage(image), has_content=True, sequence_number=4),
              raw_v1_slot("A", QImage(image), has_content=True, sequence_number=3,
                    exposure=2),
              raw_v1_slot("A", QImage(image)),
              raw_v1_slot("A", QImage(image), is_blank_key=True),
              raw_v1_slot("A", QImage(image), sequence_only=True, sequence_number=8)]
    layers[0].tween = make_spec("fixed-tween", 2, "free", False, [], [])
    layers[1].tween_member = "fixed-tween"
    archived = raw_v1_slot("A", QImage(image), has_content=True, sequence_number=9,
                     cell_name="保管", initial_image=QImage(image))
    return [Frame([layer], 2 if i == 0 else 1) for i, layer in enumerate(layers)], {(0, 9): archived}


def metadata():
    return {"format": "PaintMaskAnimatorProject", "format_version": 1,
            "mask_format": 2, "canvas": {"width": 2, "height": 2}, "frames": []}


def json_bytes(data):
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


# Filled from the original writer, before moving its conversion loop.
LEGACY_JSON_SHA256 = "4b479aac8bb1cc06017bf6df0761531b56f1828a33e41f52f01d5fa4208ff4ea"
# Stage 3-2 switched PNG writing to Rust, changing the encoded bytes, so compare
# decoded pixels, IHDR, and pHYs. This baseline comes from the Qt path
# (_encode_png_qt) output.
LEGACY_PNG_CONTENT_SHA256 = "02fe2f637af73091bd85dbdf41cf6299359c1bf21ba39697f5d2677191a008fb"


def _png_content_sha256(data):
    chunks = []
    offset = 8
    while offset < len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        chunks.append((data[offset + 4:offset + 8], data[offset + 8:offset + 8 + size]))
        offset += size + 12
    assert {kind for kind, _ in chunks} <= {b"IHDR", b"pHYs", b"IDAT", b"IEND"}
    ihdr = next(payload for kind, payload in chunks if kind == b"IHDR")
    assert len(ihdr) == 13
    phys = next((payload for kind, payload in chunks if kind == b"pHYs"), b"")
    _width, _height, rgba = native.decode_png(data, 1 << 30)
    return hashlib.sha256(ihdr + phys + rgba).hexdigest()


def test_payload_matches_original_v1_metadata_and_entry_order(tmp_path):
    frames, archive = document()
    frame_data, archive_data, pngs = project_io.legacy_fixture_build_v1_payload(*project_v1_state(frames, archive))
    data = metadata()
    data["frames"] = frame_data
    data["sequence_archive"] = archive_data
    assert hashlib.sha256(json_bytes(data)).hexdigest() == LEGACY_JSON_SHA256
    path = tmp_path / "payload.pman"
    project_io.legacy_fixture_write_archive(path, metadata(), frames, archive)
    with zipfile.ZipFile(path) as saved:
        assert saved.read("project.json") == json_bytes(data)
        assert saved.namelist() == [name for name, _image, _error in pngs] + ["project.json"]


def test_saved_json_and_png_content_match_original_writer(tmp_path):
    frames, archive = document()
    path = tmp_path / "original.pman"
    project_io.legacy_fixture_write_archive(path, metadata(), frames, archive)
    with zipfile.ZipFile(path) as saved:
        assert hashlib.sha256(saved.read("project.json")).hexdigest() == LEGACY_JSON_SHA256
        pngs = [(name, _png_content_sha256(saved.read(name)))
                for name in saved.namelist() if name.endswith(".png")]
    assert hashlib.sha256(json_bytes(pngs)).hexdigest() == LEGACY_PNG_CONTENT_SHA256


def test_v1_save_load_save_preserves_json(tmp_path):
    frames, archive = document()
    first, second = tmp_path / "first.pman", tmp_path / "second.pman"
    project_io.legacy_fixture_write_archive(first, metadata(), frames, archive)
    data, loaded, width, height = project_io.read_project_archive(first)
    assert (width, height) == (2, 2)
    loaded_archive = data.pop("_loaded_sequence_archive")
    # The reader adds absent application version fields; they are not document data.
    for key in ("created_app_version", "saved_app_version"):
        data.pop(key, None)
    data["frames"] = []
    project_io.legacy_fixture_write_archive(second, data, loaded, loaded_archive)
    with zipfile.ZipFile(first) as a, zipfile.ZipFile(second) as b:
        assert a.read("project.json") == b.read("project.json")


def test_build_payload_does_not_mutate_document():
    frames, archive = document()

    def state():
        def layer_state(layer):
            values = []
            for field in fields(Layer):
                value = getattr(layer, field.name)
                image = as_qimage(value)
                values.append((field.name, (id(value), image.cacheKey())
                               if isinstance(image, QImage) else copy.deepcopy(value)))
            return id(layer), id(layer._cell), id(layer._track), values
        return (id(frames), [(id(frame), frame.duration, id(frame.layers),
                             [layer_state(layer) for layer in frame.layers]) for frame in frames],
                id(archive), [(key, layer_state(layer)) for key, layer in archive.items()])

    before = state()
    project_io.legacy_fixture_build_v1_payload(*project_v1_state(frames, archive))
    assert state() == before


def test_track_fields_are_only_eight_display_settings():
    assert TRACK_FIELDS == ("name", "visible", "opacity", "is_paper", "is_draft",
                            "alpha_locked", "color_filter_enabled", "color_filter_rgb")
