"""新しい版のお知らせ; owned by ``MainWindow`` as ``window.update_notice``.

起動したあと裏で GitHub Releases を確かめ（:func:`updater.check_for_update`）、
新しい版があればメニューバーの右端に目立つボタンを出す。ボタンから
ダウンロードとインストール、リリースノートの表示、この版を知らせない設定を
選べる。macOS のように画面上部の共通メニューバーを使う環境では、ボタンを
置けないので、同じ中身のメニューをメニューバーの最後に足す。

問い合わせはネットワークを待つので別のスレッドで行い、結果だけを Qt の
シグナルで画面のスレッドへ戻す。失敗しても何も表示しない（ヘルプ ›
更新を確認… ではこれまでどおりエラーを表示する）。起動したままのときも、
:data:`RECHECK_HOURS` ごとに確かめ直す。

A collaborator rather than a mixin -- see ``main_window_export.py`` for why.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QMenu, QToolButton

from . import constants, preferences, updater
from .i18n import tr
from .logging_setup import get_logger

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger(__name__)

# 起動してから最初に確かめるまでの時間（ミリ秒）。起動直後の処理と重ねない。
FIRST_CHECK_DELAY_MS = 4000
# 起動したままのとき、確かめ直す間隔（時間）。GitHub の API は認証なしだと
# 1時間に60回までなので、十分に少ない。
RECHECK_HOURS = 6

_BUTTON_STYLE = """
QToolButton#updateNotice {{
    background-color: {accent};
    color: white;
    border: none;
    border-radius: 9px;
    padding: 1px 10px;
    margin: 2px 6px;
    font-weight: bold;
}}
QToolButton#updateNotice:hover {{ background-color: {hover}; }}
QToolButton#updateNotice::menu-indicator {{ image: none; width: 0; }}
"""


class _ResultRelay(QObject):
    """別のスレッドの結果を、画面のスレッドへ渡す。"""

    finished = Signal(object)


class UpdateNoticeController:
    """Owned by ``MainWindow`` as ``window.update_notice``."""

    def __init__(self, window: "MainWindow"):
        self.window = window
        self._relay = _ResultRelay()
        self._relay.finished.connect(self._on_result)
        self._checking = False
        self._timer: Optional[QTimer] = None
        # 今お知らせしている結果（:func:`updater.check_for_update` の戻り値）。
        self.result: Optional[dict] = None
        self._menu: Optional[QMenu] = None
        self._button: Optional[QToolButton] = None
        self._menu_action: Optional[QAction] = None

    # ------------------------------------------------------------------
    # 確かめる
    # ------------------------------------------------------------------
    def start(self):
        """起動後の自動確認を始める（環境設定で OFF なら何もしない）。"""
        if not preferences.check_updates_automatically():
            return
        QTimer.singleShot(FIRST_CHECK_DELAY_MS, self.check_in_background)
        self._timer = QTimer(self.window)
        self._timer.setInterval(RECHECK_HOURS * 60 * 60 * 1000)
        self._timer.timeout.connect(self.check_in_background)
        self._timer.start()

    def check_in_background(self):
        if self._checking or not preferences.check_updates_automatically():
            return
        self._checking = True

        def work():
            try:
                result = updater.check_for_update()
            except Exception as error:  # noqa: BLE001 - お知らせは失敗しても黙る
                log.warning("background update check failed: %s", error, exc_info=True)
                result = {"status": "error", "message": str(error)}
            self._relay.finished.emit(result)

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _on_result(self, result):
        self._checking = False
        self.apply_result(result)

    def apply_result(self, result):
        """確認の結果に合わせて、お知らせを出したり消したりする。"""
        status = (result or {}).get("status")
        if status == "error":
            return  # 一時的な失敗でお知らせを消さない
        latest = str((result or {}).get("latest") or "")
        if status == "update_available" and latest != preferences.skipped_update_version():
            self.result = result
            self._show(latest)
        else:
            self.result = None
            self._hide()

    # ------------------------------------------------------------------
    # メニューバーの表示
    # ------------------------------------------------------------------
    def _title(self, latest):
        return tr("新しいバージョン v{latest} があります").format(latest=latest)

    def _build_menu(self, latest):
        menu = QMenu(self._title(latest), self.window)
        install = menu.addAction(tr("ダウンロードしてインストール…"))
        install.triggered.connect(self.install)
        notes = menu.addAction(tr("リリースノートを見る"))
        notes.triggered.connect(self.open_release_notes)
        menu.addSeparator()
        skip = menu.addAction(tr("この版は知らせない"))
        skip.triggered.connect(self.skip_this_version)
        return menu

    def _show(self, latest):
        self._hide()
        self._menu = self._build_menu(latest)
        bar = self.window.menuBar()
        if bar.isNativeMenuBar():
            # 画面上部の共通メニューバーにはボタンを置けないので、メニューを足す。
            self._menu_action = bar.addMenu(self._menu)
            return
        button = QToolButton(bar)
        button.setObjectName("updateNotice")
        button.setText(self._title(latest))
        button.setToolTip(tr("クリックして更新できます（現在: v{version}）").format(
            version=constants.APP_VERSION
        ))
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(self._menu)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._style_button(button)
        bar.setCornerWidget(button, Qt.Corner.TopRightCorner)
        button.show()
        self._button = button

    def _style_button(self, button):
        from . import theme
        from PySide6.QtGui import QColor

        accent = QColor(str(theme.current_accent()))
        button.setStyleSheet(_BUTTON_STYLE.format(
            accent=accent.name(), hover=accent.darker(115).name()
        ))

    def refresh_style(self):
        """テーマやアクセントカラーを変えたら呼ぶ。"""
        if self._button is not None:
            self._style_button(self._button)

    def _hide(self):
        bar = self.window.menuBar()
        if self._button is not None:
            bar.setCornerWidget(None, Qt.Corner.TopRightCorner)  # pyright: ignore[reportArgumentType]  # Qt accepts None to clear the corner
            self._button.deleteLater()
            self._button = None
        if self._menu_action is not None:
            bar.removeAction(self._menu_action)
            self._menu_action = None
        if self._menu is not None:
            self._menu.deleteLater()
            self._menu = None

    @property
    def visible(self):
        return self._button is not None or self._menu_action is not None

    # ------------------------------------------------------------------
    # メニューの項目
    # ------------------------------------------------------------------
    def install(self):
        result = self.result
        if not result:
            return
        asset = result.get("asset")
        if not asset:
            self.open_release_notes()
            return
        self.window._download_and_run_installer(asset, result.get("latest", ""))

    def open_release_notes(self):
        release = (self.result or {}).get("release") or {}
        url = release.get("html_url") or constants.RELEASES_URL
        QDesktopServices.openUrl(QUrl(str(url)))

    def skip_this_version(self):
        if not self.result:
            return
        preferences.set_skipped_update_version(str(self.result.get("latest") or ""))
        self.result = None
        self._hide()


__all__ = ["UpdateNoticeController"]
