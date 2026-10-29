"""サブビュードックのネイティブ化と、画面構成が変わったときの復元位置。

マルチモニタ環境で ``QWindowsWindow::setGeometry: Unable to set geometry ...
on QWidgetWindow/"subviewDockWindow"`` と
``ads::CDockSplitterClassWindow must be a top level window`` が大量に出た件の
回帰テスト。原因は子ウィジェットに対する ``winId()`` が、先祖のスプリッターや
ドックウィジェットまでネイティブウィンドウへ昇格させていたこと。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from paintmaskanimator.main_window import MainWindow  # noqa: E402
from paintmaskanimator.main_window_autosave import AutosaveController  # noqa: E402
from paintmaskanimator.main_window_workspace import WorkspaceController  # noqa: E402
from paintmaskanimator.utils import (  # noqa: E402
    disable_windows_ink_feedback,
    top_level_window_ids,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(AutosaveController, "maybe_restore", lambda self: None)
    main_window = MainWindow()
    main_window.show()
    for _ in range(5):
        qapp.processEvents()
    try:
        yield main_window
    finally:
        # 閉じただけのウィンドウはアプリ全体のイベントフィルタを握ったまま
        # 残り、後続テストのホバー操作を飲んでしまうので破棄まで行う。
        main_window.close()
        main_window.deleteLater()
        for _ in range(3):
            qapp.processEvents()


class _FakeDockManager:
    """``floatingWidgets()`` だけを持つ、クランプ検証用の最小の代役。"""

    def __init__(self, *floating):
        self._floating = floating

    def floatingWidgets(self):
        return self._floating


class _FakeDockWindow:
    def __init__(self, *floating):
        self.dock_manager = _FakeDockManager(*floating)


def _native_children(main_window):
    return [
        child.objectName() or type(child).__name__
        for child in main_window.findChildren(QWidget)
        if not child.isWindow() and child.internalWinId()
    ]


def test_ink_feedback_keeps_docks_and_splitters_alien(qapp, window):
    """ペン操作の設定は、ドックをネイティブウィンドウにしてはいけない。"""
    disable_windows_ink_feedback(window, window.canvas, window.subview)
    for _ in range(3):
        qapp.processEvents()
    assert _native_children(window) == []
    assert window.subview_dock.internalWinId() == 0
    assert window.canvas.internalWinId() == 0


def test_top_level_window_ids_collapses_children_to_their_window(window):
    """子ウィジェットは所属ウィンドウのHWNDへまとめ、重複は返さない。"""
    handles = top_level_window_ids([window, window.canvas, window.subview, None])
    assert handles == [int(window.winId())]
    assert _native_children(window) == []


def test_clamped_geometry_pushes_offscreen_rect_into_available_area(qapp):
    """保存時より画面が減っていても、復元位置は作業領域の中に収まる。"""
    screen = QApplication.primaryScreen()
    assert screen is not None
    available = screen.availableGeometry()
    far_away = QRect(available.x() + 12000, available.y() + 9000, 400, 300)
    clamped = WorkspaceController.clamped_geometry(far_away)
    assert available.contains(clamped)
    assert clamped.size() == far_away.size()

    # はみ出していない矩形はそのまま残す。
    inside = QRect(available.x() + 10, available.y() + 10, 200, 150)
    assert WorkspaceController.clamped_geometry(inside) == inside


def test_clamped_geometry_shrinks_a_window_larger_than_the_screen(qapp):
    available = QApplication.primaryScreen().availableGeometry()
    oversized = QRect(
        available.x() - 500,
        available.y() - 500,
        available.width() + 1000,
        available.height() + 1000,
    )
    clamped = WorkspaceController.clamped_geometry(oversized)
    assert available.contains(clamped)


def test_clamp_floating_windows_moves_a_restored_panel_back_on_screen(qapp):
    """画面外に保存されたフロートパネルは、復元後に画面内へ戻る。"""
    available = QApplication.primaryScreen().availableGeometry()
    floating = QWidget()
    floating.setGeometry(available.x() + 9000, available.y() + 7000, 420, 320)
    offscreen = QRect(floating.geometry())
    controller = WorkspaceController.__new__(WorkspaceController)
    # 画面構成のクランプだけを見たいので、ドックマネージャだけの代役を挿す。
    controller.window = _FakeDockWindow(floating)  # pyright: ignore[reportAttributeAccessIssue]
    try:
        controller.clamp_floating_windows()
        assert floating.geometry() != offscreen
        assert available.contains(floating.geometry())
    finally:
        floating.deleteLater()
