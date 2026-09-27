"""タイムラインの見出しは 1, 7, 13, 19… と6コマごとの先頭に番号を出す。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator.main_window import MainWindow  # noqa: E402


def test_header_numbers_mark_the_first_frame_of_each_six():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.refresh_ui()
        table = window.timeline.table
        labels = {}
        for column in range(min(table.columnCount(), 25)):
            item = table.horizontalHeaderItem(column)
            labels[column] = item.text() if item is not None else ""
        numbered = [column + 1 for column, text in labels.items() if text]
        assert numbered[:4] == [1, 7, 13, 19]
        assert all(labels[number - 1] == str(number) for number in numbered)
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
