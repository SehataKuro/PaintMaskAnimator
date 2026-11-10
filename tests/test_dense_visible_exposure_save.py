"""Edited exposures save their visible lengths, including after Undo/Redo."""
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QMessageBox

from paintmaskanimator import constants
from paintmaskanimator.document import project_v1_state
from paintmaskanimator.main_window import MainWindow
from test_p5_3_characterization import install, payload, pixels, row, sheet, slot


@pytest.mark.parametrize("operation", ["cut-paste", "tween-member-move"])
def test_edited_exposure_saves_visible_length(qtbot, monkeypatch, tmp_path, operation):
    monkeypatch.setattr(constants, "CANVAS_WIDTH", 16)
    monkeypatch.setattr(constants, "CANVAS_HEIGHT", 16)
    window = MainWindow()
    qtbot.addWidget(window)
    for timer in (window._autosave_timer, window._used_color_timer, window._title_timer):
        timer.stop()
    if operation == "cut-paste":
        c = sheet(window)
        assert window.timeline_ops.cut_cell_to_clipboard(row(c), 0)
        assert window.timeline_ops.paste_cell_from_clipboard(row(c), 5)
        start, length = 5, 1
        saved_lengths = {5: 1}
    else:
        monkeypatch.setattr(QMessageBox, "question", staticmethod(
            lambda *_: QMessageBox.StandardButton.Ok))
        c = install(window, [slot(1, "red", has_content=True, exposure=3), slot(), slot(),
                             slot(), slot(2, "blue", has_content=True)])
        c.current_frame = 0
        window.tween.enable(row(c), 0, "free")
        c.transform_points = [QPointF(p.x() + 2, p.y() + 1) for p in c.transform_points]
        window.tween.commit_transform_or_tween()
        window.tween.edit(row(c), 0)
        window.tween.cancel_transform_or_tween()
        assert c.move_timeline_cell(1, 0, 3, 0)
        start, length = 0, 2
        # v1 keeps each tween specification/member position as a separate key.
        # The visible two-frame run is saved as 1 + 1, never the old latent 3.
        saved_lengths = {0: 1, 1: 1, 3: 1}

    def check(phase):
        assert c.timeline_block_at(start, 0) == ("content", start, length)
        projected, _ = project_v1_state(c.frames)
        for column, expected in saved_lengths.items():
            assert projected[column].layers[0].exposure == expected
        path = tmp_path / f"{phase}.pman"
        assert window.project.write(path)
        saved = payload(path)
        for column, expected in saved_lengths.items():
            assert saved["metadata"]["frames"][column]["layers"][0]["exposure"] == expected
        return saved

    saved = check("after")
    visible = [pixels(c.composite(i, white=False)) for i in range(len(c.frames))]
    c.undo()
    c.redo()
    assert check("redo") == saved
    path = tmp_path / "redo.pman"
    assert window.project.open(path)
    assert window.project.write(tmp_path / "reopened.pman")
    assert payload(tmp_path / "reopened.pman")["metadata"] == saved["metadata"]
    assert [pixels(c.composite(i, white=False)) for i in range(len(c.frames))] == visible
    assert c.timeline_block_at(start, 0) == ("content", start, length)
    window._closing = True
