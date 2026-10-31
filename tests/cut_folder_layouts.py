"""Layouts shared by the cut-folder tests."""

from paintmaskanimator import cut_folder as cf
from paintmaskanimator.cut_folder import block, text


def episode_layout():
    """A generic layout with 話数 and XDTS: ``作品名_03_c012/A/A0001.png``."""
    cut_name = (
        block("title"), text("_"), block("episode", digits=2), text("_"),
        block("cut", digits=3, prefix="c"),
    )
    return cf.CutFolderLayout(
        folder=cut_name,
        cell_folder=(block("cell"),),
        cell_file=(block("cell"), block("number", digits=4)),
        timesheet=cut_name,
        timesheet_folder=(),
        extra_folders=[],
        image_format="png",
    )
