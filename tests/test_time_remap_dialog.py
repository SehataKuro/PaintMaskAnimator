import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from paintmaskanimator.widgets import TimeRemapPasteDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_time_remap_field_normalizers_reject_scalar_values():
    assert TimeRemapPasteDialog._list_value(12) == []
    assert TimeRemapPasteDialog._list_value("123") == []
    assert TimeRemapPasteDialog._mapping_list([{"uid": "a"}, 2, None]) == [
        {"uid": "a"}
    ]


def test_time_remap_preview_rejects_non_mapping_parser_result(qapp):
    owner = QWidget()
    dialog = TimeRemapPasteDialog(
        "invalid", owner, parse_text=lambda _text: ["invalid"]
    )
    try:
        dialog._update_timesheet_preview()
        assert dialog._parsed_source is None
        assert dialog.preview_table.rowCount() == 0
    finally:
        dialog.deleteLater()
        owner.deleteLater()
