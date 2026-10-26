"""新しい版があるとき、メニューバーで知らせる。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from paintmaskanimator import main_window_update_notice, preferences  # noqa: E402
from paintmaskanimator.main_window import MainWindow  # noqa: E402

AVAILABLE = {
    "status": "update_available",
    "latest": "9.9.9",
    "release": {"html_url": "https://example.invalid/releases/v9.9.9"},
    "asset": {"name": "PaintMaskAnimator-Setup-9.9.9.exe", "url": "https://example.invalid/x"},
}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    window = MainWindow()
    try:
        yield window
    finally:
        window.deleteLater()


def test_shows_a_menu_bar_notice_when_a_newer_version_exists(window):
    notice = window.update_notice
    notice.apply_result(AVAILABLE)
    assert notice.visible
    button = window.menuBar().cornerWidget()
    assert button is not None and "v9.9.9" in button.text()
    labels = [action.text() for action in button.menu().actions() if action.text()]
    assert labels == ["ダウンロードしてインストール…", "リリースノートを見る", "この版は知らせない"]


def test_up_to_date_hides_the_notice_but_errors_keep_it(window):
    notice = window.update_notice
    notice.apply_result(AVAILABLE)
    notice.apply_result({"status": "error", "message": "offline"})
    assert notice.visible, "一時的な失敗ではお知らせを消さない"
    notice.apply_result({"status": "up_to_date", "latest": "9.9.9"})
    assert not notice.visible
    assert window.menuBar().cornerWidget() is None


def test_skipping_a_version_hides_it_until_a_newer_one(window):
    notice = window.update_notice
    notice.apply_result(AVAILABLE)
    notice.skip_this_version()
    assert not notice.visible
    assert preferences.skipped_update_version() == "9.9.9"
    notice.apply_result(AVAILABLE)
    assert not notice.visible, "知らせないことにした版は出さない"
    notice.apply_result(dict(AVAILABLE, latest="10.0.0"))
    assert notice.visible


def test_install_reuses_the_download_flow(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "_download_and_run_installer", lambda asset, latest: calls.append((asset, latest)))
    window.update_notice.apply_result(AVAILABLE)
    window.update_notice.install()
    assert calls == [(AVAILABLE["asset"], "9.9.9")]


def test_background_check_reports_back_on_the_ui_thread(window, monkeypatch):
    monkeypatch.setattr(main_window_update_notice.updater, "check_for_update", lambda: AVAILABLE)
    window.update_notice.check_in_background()
    for _ in range(50):
        if window.update_notice.visible:
            break
        QTest.qWait(20)
    assert window.update_notice.visible


def test_automatic_check_can_be_turned_off(window, monkeypatch):
    called = []
    monkeypatch.setattr(main_window_update_notice.updater, "check_for_update", lambda: called.append(1) or AVAILABLE)
    preferences.set_check_updates_automatically(False)
    window.update_notice.start()
    window.update_notice.check_in_background()
    QTest.qWait(50)
    assert not called


def test_interactive_check_updates_the_notice(window, monkeypatch):
    monkeypatch.setattr("paintmaskanimator.main_window.updater.check_for_update", lambda: {"status": "up_to_date", "latest": "0"})
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    window.update_notice.apply_result(AVAILABLE)
    window.check_for_updates_interactive()
    assert not window.update_notice.visible
