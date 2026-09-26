"""アクション編集（WebEngine）を開いている間、アプリ全体のイベントフィルターを外す。

PySide6 6.11 では、アプリ全体に Python のイベントフィルターがあると
QtWebEngine の表示中にセグメンテーション違反で落ちるため。
"""
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget

from paintmaskanimator import utils


class _Recorder(QObject):
    def __init__(self):
        super().__init__()
        self.count = 0

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.User:
            self.count += 1
        return False


def _app():
    return QApplication.instance() or QApplication([])


def _send_user_event(target):
    QApplication.sendEvent(target, QEvent(QEvent.Type.User))


def test_filters_are_suspended_and_restored():
    app = _app()
    target = QWidget()
    recorder = _Recorder()
    utils.install_app_event_filter(recorder)
    try:
        _send_user_event(target)
        assert recorder.count == 1
        with utils.app_event_filters_suspended():
            _send_user_event(target)
            assert recorder.count == 1
        _send_user_event(target)
        assert recorder.count == 2
    finally:
        utils.remove_app_event_filter(recorder)
        target.close()
    assert app is QApplication.instance()


def test_filter_removed_while_suspended_is_not_reinstalled():
    _app()
    target = QWidget()
    recorder = _Recorder()
    utils.install_app_event_filter(recorder)
    try:
        with utils.app_event_filters_suspended():
            utils.remove_app_event_filter(recorder)
        _send_user_event(target)
        assert recorder.count == 0
    finally:
        utils.remove_app_event_filter(recorder)
        target.close()
