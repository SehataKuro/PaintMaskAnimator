"""Exercise the drawing API without a widget, event loop, or QImage fixtures."""
import numpy as np

from paintmaskanimator.document import Cell, Document, create_drawing
from paintmaskanimator.document_changes import ChangeSet, DrawingAttributes, DrawingPixels
from paintmaskanimator.pma_image import PmaImage


def test_numbered_patch_changes_shared_pixels():
    doc = Document()
    slot = doc.active_layer
    create_drawing(slot, PmaImage.blank(5, 4), number=7)
    patch = np.array([[[255, 17, 0, 255]]], dtype=np.uint8)
    changes = doc.write_drawing_patch(0, 0, patch, (2, 1, 1, 1), rgba=True)
    assert changes == ChangeSet((DrawingPixels(0, 7, 0, ((2, 1, 1, 1),)),))
    bank = doc.tracks[0].cells[7]
    assert bank.image is slot.image
    np.testing.assert_array_equal(bank.image.rgba()[1, 2], patch[0, 0])
    assert np.count_nonzero(bank.image.rgba()[..., 3]) == 1


def test_replacement_then_initial_image_retains_previous_pixels():
    doc = Document()
    image = PmaImage.blank(5, 4)
    assert doc.replace_drawing_image(0, 0, image, has_content=True) == ChangeSet(
        (DrawingPixels(0, None, 0, ()),),
    )
    assert doc.keep_initial_images([(0, 0)]) == ChangeSet(
        (DrawingAttributes(0, None, 0, ("initial_image",)),),
    )
    patch = np.array([[[255, 17, 0, 255]]], dtype=np.uint8)
    doc.write_drawing_patch(0, 0, patch, (0, 0, 1, 1), rgba=True)
    assert doc.active_layer.initial_image.rgba()[0, 0, 3] == 0
    assert doc.active_layer.image.rgba()[0, 0, 3] == 255


def test_patch_unplaced_number_without_creating_a_reference():
    doc = Document()
    bank = doc.tracks[0].cells
    bank[11] = Cell(PmaImage.blank(5, 4), cell_name="A-11")
    patch = np.array([[[2, 7, 199, 255]]], dtype=np.uint8)
    changes = doc.write_drawing_patch(None, 0, patch, (3, 1, 1, 1), number=11, rgba=True)
    assert changes == ChangeSet((DrawingPixels(0, 11, None, ((3, 1, 1, 1),)),))
    assert not doc.active_layer.has_content and doc.active_layer.sequence_number is None
    assert bank[11].cell_name == "A-11"
    np.testing.assert_array_equal(bank[11].image.rgba()[1, 3], patch[0, 0])
    assert not doc.write_drawing_patch(None, 0, patch, (3, 1, 1, 1), number=12, rgba=True)
    assert list(bank) == [11]
