"""Pytest bootstrap: force Qt into headless (offscreen) mode for the test run
so the suite never tries to open real windows."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path, monkeypatch):
    """Point the per-user config directory at a throwaway folder.

    Without this, tests read and write the developer's real settings. Worse,
    ``MainWindow`` offers crash recovery whenever a real ``autosave.pmap`` is
    left over, and that modal question hangs the run forever -- on the
    developer's machine only, since CI never has one.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("APPDATA", str(config_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    return config_dir


@pytest.fixture(autouse=True)
def no_unhandled_modal_dialogs(monkeypatch):
    """Fail instead of blocking when a test reaches a modal dialog.

    Offscreen, nobody can click a modal, so an unexpected one stalls the suite
    with no output. Tests that expect a dialog monkeypatch it themselves, which
    overrides this guard.
    """
    from PySide6.QtWidgets import QDialog, QMessageBox

    def _fail_static(level):
        def _fail(_parent, title, text, *_args, **_kwargs):
            raise AssertionError(f"unexpected QMessageBox.{level}: {title!r} / {text!r}")
        return staticmethod(_fail)

    for level in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, level, _fail_static(level))

    def _fail_exec(self):
        raise AssertionError(
            f"unexpected modal dialog: {type(self).__name__} / {self.windowTitle()!r}"
        )

    monkeypatch.setattr(QDialog, "exec", _fail_exec)


@pytest.fixture(autouse=True)
def destroy_leftover_main_windows():
    """Destroy every ``MainWindow`` a test leaves behind.

    Tests only ``close()`` their windows, which hides them but keeps them alive
    for the whole session. Each one keeps its timers and sub-view filtering
    application-wide events, and windows a test forgot to close also keep
    their own filter. Every event then runs through dozens of stale filters,
    so later window tests (``test_docking.py``) slow to a crawl -- over a
    minute per test after the files before it -- and look hung.
    """
    _tidy_up_leftover_main_windows()
    yield
    _tidy_up_leftover_main_windows()


def _tidy_up_leftover_main_windows():
    import sys

    main_window = sys.modules.get("paintmaskanimator.main_window")
    if main_window is None:
        return
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    windows = [
        widget for widget in QApplication.topLevelWidgets()
        if isinstance(widget, main_window.MainWindow)
    ]
    from paintmaskanimator.utils import remove_app_event_filter

    for window in windows:
        # close() で止まるのはメインウィンドウ自身のフィルターとタイマーだけ。
        # サブビューのフィルターは残るので、ここで外す（遅くなる原因はこれ）。
        window.close()
        remove_app_event_filter(window.subview)
    if sys.platform == "win32":
        # Windows では、ドッキング（QtAds）の窓を持つメインウィンドウを破棄すると
        # access violation やヒープ破壊で落ちる（CI の Windows で再現）。
        # テストの多くは片付けに ``window.deleteLater()`` を呼ぶが、その破棄は
        # 次にイベントを回したところ――たいていは別のテストの最中――で起こる。
        # そこで、積まれた破棄の予定を取り消して、窓は残しておく。
        # フィルターは外したので、残っていても遅くはならない。
        for window in windows:
            QCoreApplication.removePostedEvents(window, QEvent.Type.DeferredDelete)
        return
    for window in windows:
        window.deleteLater()
    if windows:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
