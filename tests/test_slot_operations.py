"""Semantic entry points compared with the legacy statements they replace."""
import pytest
from PySide6.QtGui import QImage

from paintmaskanimator.document import (
    apply_number_mapping, apply_numbered_images, assign_numbers_to_unnumbered,
    clear_slot, clone_frame_graph, create_drawing, place_number, project_v1_state,
    put_archive, remove_archive,
)
from paintmaskanimator.models import Frame, Layer
from paintmaskanimator.utils import blank_image


def image(color):
    result = QImage(3, 2, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(color)
    return result


def graph():
    first = Layer('A', image(0xff123456), has_content=True,
                  sequence_number=5, exposure=3)
    first.initial_image = image(0xffabcdef)
    first.cell_name = 'five'
    second = first.clone_slot()
    third = Layer('A', image(0xff778899), has_content=True, sequence_number=2)
    archived = first.clone_slot()
    other = Layer('B', image(0xff998877), has_content=True, sequence_number=9)
    frames = [Frame([layer, other.clone_slot()]) for layer in (first, second, third)]
    return frames, {(0, 5): archived, (1, 9): other.clone_slot()}


def compare(left, right):
    """Compare payloads and the full Cell/image/initial sharing matrices."""
    lf, la = left
    rf, ra = right
    assert list(la) == list(ra)
    ls = [layer for f in lf for layer in f.layers] + list(la.values())
    rs = [layer for f in rf for layer in f.layers] + list(ra.values())
    for a, b in zip(ls, rs, strict=True):
        for attr in ('has_content', 'is_blank_key', 'exposure', 'sequence_number',
                     'sequence_only', 'cell_name', 'tween', 'tween_member'):
            assert getattr(a, attr) == getattr(b, attr)
        assert a.image == b.image
        assert a.initial_image == b.initial_image
    for i in range(len(ls)):
        for j in range(len(ls)):
            assert (ls[i].cell_id == ls[j].cell_id) == (rs[i].cell_id == rs[j].cell_id)
            assert (ls[i].image is ls[j].image) == (rs[i].image is rs[j].image)
            assert (ls[i].initial_image is ls[j].initial_image) == (
                rs[i].initial_image is rs[j].initial_image)


def pair():
    original = graph()
    return clone_frame_graph(*original), clone_frame_graph(*original)


@pytest.mark.parametrize('blank', [False, True])
def test_clear_slot(blank):
    new, old = pair()
    clear_slot(new[0][0].layers[0], blank=blank)
    layer = old[0][0].layers[0]
    layer.detach_cell()
    layer.image = blank_image()
    layer.has_content = False
    layer.is_blank_key = blank
    layer.exposure = 1
    layer.sequence_number = None
    layer.sequence_only = False
    layer.cell_name = None
    layer.tween = None
    layer.tween_member = None
    compare(new, old)


def test_place_number():
    new, old = pair()
    place_number(new[0][0].layers[0], new[0][2].layers[0], 2)
    target, source = old[0][0].layers[0], old[0][2].layers[0]
    copied = source.clone()
    target.detach_cell()
    target.image = source.image
    target.initial_image = copied.initial_image
    target.has_content = bool(copied.has_content)
    target.is_blank_key = not bool(copied.has_content)
    target.sequence_number = 2
    target.cell_name = getattr(copied, 'cell_name', None)
    target.sequence_only = False
    target.exposure = 1
    compare(new, old)


@pytest.mark.parametrize('number', [None, 9])
def test_create_drawing(number):
    new, old = pair()
    # Preparation remains at the call site, including its detach position.
    clear_slot(new[0][0].layers[0])
    clear_slot(old[0][0].layers[0])
    create_drawing(new[0][0].layers[0], image(0xff112233), number=number)
    layer = old[0][0].layers[0]
    layer.image = image(0xff112233)
    layer.has_content = True
    layer.exposure = 1
    layer.sequence_number = number
    compare(new, old)


def test_archive_updates():
    new, old = pair()
    put_archive(new[1], (0, 2), new[0][2].layers[0].clone())
    old[1][(0, 2)] = old[0][2].layers[0].clone()
    compare(new, old)
    remove_archive(new[1], (0, 5))
    old[1].pop((0, 5), None)
    assert remove_archive(new[1], (0, 99)) is None
    compare(new, old)


@pytest.mark.parametrize('normalize', [False, True])
def test_number_mapping(normalize):
    new, old = pair()
    for frames, _ in (new, old):
        frames.append(Frame([
            Layer('A', image(0xff111111), has_content=True),
            Layer('B', image(0), sequence_number=9),
        ]))
    mapping = {5: 1, 2: 2} if normalize else {5: 6, 2: 3}
    nf, na = new
    na = apply_number_mapping(nf, na, 0, mapping,
                              unnumbered_start=3 if normalize else None,
                              normalize_archive_keys=normalize)
    of, oa = old
    next_number = 3
    for frame in of:
        layer = frame.layers[0]
        if layer.sequence_number is not None:
            layer.sequence_number = mapping[int(layer.sequence_number)]
        elif normalize and layer.has_content and not layer.sequence_only:
            layer.sequence_number = next_number
            next_number += 1
    result = {}
    for (li, number), archived in oa.items():
        if li == 0:
            number = mapping.get(int(number), int(number))
            archived.sequence_number = number
        result[(li, number)] = archived
    compare((nf, na), (of, result))


def test_sequence_image_mapping():
    new, old = pair()
    def snapshots(frames):
        return {layer.sequence_number: (
            layer.image.copy(), bool(layer.has_content), bool(layer.is_blank_key),
            layer.cell_name,
            QImage(layer.initial_image) if layer.initial_image is not None else None,
        ) for layer in (frames[0].layers[0], frames[2].layers[0])}
    mapping = {5: 2, 2: 5}
    apply_numbered_images(new[0], 0, mapping, snapshots(new[0]))
    saved = snapshots(old[0])
    for frame in old[0]:
        layer = frame.layers[0]
        if layer.sequence_number in mapping:
            im, content, blank, name, initial = saved[mapping[layer.sequence_number]]
            layer.image = im.copy()
            layer.has_content = content
            layer.is_blank_key = blank
            layer.cell_name = name
            layer.initial_image = QImage(initial) if initial is not None else None
    compare(new, old)


@pytest.mark.parametrize('kind', ['normal', 'paper', 'draft'])
def test_assign_numbers_to_unnumbered(kind):
    # An exposure-three key is followed by two unused holding slots.
    layers = [Layer('A', image(0xff123456), has_content=True,
                    sequence_number=n, exposure=3 if i == 0 else 1)
              for i, n in enumerate([None, 2, None, 5, None])]
    layers[1:1] = [Layer('A', image(0)), Layer('A', image(0))]
    for layer in layers:
        layer.is_paper = kind == 'paper'
        layer.is_draft = kind == 'draft'
    frames = [Frame([layer]) for layer in layers]
    old = clone_frame_graph(frames)[0]
    assign_numbers_to_unnumbered(frames, 0)
    columns = [i for i in range(len(old)) if old[i].layers[0].has_content]
    used = [int(old[i].layers[0].sequence_number) for i in columns
            if old[i].layers[0].sequence_number is not None]
    next_number = max(used, default=0) + 1
    for i in columns:
        layer = old[i].layers[0]
        if layer.sequence_number is None:
            layer.sequence_number = next_number
            next_number += 1
    assert [frames[i].layers[0].sequence_number for i in (0, 4, 6)] == [6, 7, 8]
    assert frames[0].layers[0].exposure == 3
    compare((frames, {}), (old, {}))


def test_v1_projection():
    frames, archive = graph()
    projected_frames, projected_archive = project_v1_state(frames, archive)
    assert projected_frames is frames
    assert projected_archive is archive
    compare((projected_frames, projected_archive), (frames, archive))
    assert project_v1_state(frames)[1] is None
