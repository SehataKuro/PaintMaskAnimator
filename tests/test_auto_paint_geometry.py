"""Target geometry must not depend on reference colours or existing paint edges."""
import numpy as np

from paintmaskanimator import auto_paint, reference_check


SKIN = (255, 241, 232)
LEAF = (197, 174, 103)


def room():
    pixels = np.zeros((40, 40, 4), dtype=np.uint8)
    pixels[4, 4:36] = pixels[35, 4:36] = (0, 0, 0, 255)
    pixels[4:36, 4] = pixels[4:36, 35] = (0, 0, 0, 255)
    return pixels


def test_existing_paint_edges_do_not_split_target_geometry():
    raw = room()
    mixed = raw.copy()
    mixed[5:35, 5:20] = (*SKIN, 255)
    mixed[5:35, 20:35] = (*LEAF, 255)
    target = auto_paint._Cell(mixed, target=True)
    assert np.array_equal(target.regions.labels,
                          auto_paint._Cell(raw, target=True).regions.labels)
    guide = room()
    guide[5:35, 5:35] = (*SKIN, 255)
    result = auto_paint.paint_from_guide(mixed, guide)
    after = auto_paint.apply_paint(mixed, result)
    assert np.all(after[5:35, 5:35] == (*SKIN, 255))


def test_tp_line_is_a_target_boundary_even_when_both_sides_have_the_same_colour():
    raw = room()
    raw[5:35, 20] = (255, 0, 0, 255)
    raw[5:35, 5:20] = raw[5:35, 21:35] = (*SKIN, 255)
    target = auto_paint._Cell(raw, target=True)
    assert target.regions.labels[20, 19] != target.regions.labels[20, 21]


def test_ambiguous_reference_does_not_cut_tp_line_into_colour_fragments():
    raw = room()
    raw[5:35, 20] = (255, 0, 0, 255)
    target = auto_paint._Cell(raw, target=True)
    guide_pixels = room()
    guide_pixels[5:20, 5:35] = (*SKIN, 255)
    guide_pixels[20:35, 5:35] = (*LEAF, 255)
    guide = auto_paint._Cell(guide_pixels)
    painted = reference_check.color_mask(raw, None).astype(np.uint32)
    painted[:, :20] = reference_check._pack(SKIN)
    painted[:, 21:] = reference_check._pack(LEAF)
    index = target.regions.labels[20, 20]
    paints = auto_paint._line_paints(
        target, guide, target.regions.local_mask(index), target.regions.bounds(index),
        (0, 0), painted, target.regions.value(index), {}, {},
    )
    assert paints is None
