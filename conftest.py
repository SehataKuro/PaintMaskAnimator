"""Pytest bootstrap: force Qt into headless (offscreen) mode for the test run
so the suite never tries to open real windows."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Windows では QtAds の破棄が安全でないので、隔離した窓を強参照で抱えたまま、
# run_tests.py がプロセスを終わらせるのに任せる。
_retained_windows = []

# テスト中に作られた ``MainWindow`` の台帳。後始末の対象はここから引く。
# ``QApplication.topLevelWidgets()`` は歩かない（理由は
# :func:`_live_main_windows` のコメント）。
#
# 弱参照で持つ。強参照にすると、破棄したあとも Python 側のラッパーとその属性
# （canvas → document → フレームの QImage）が残り、常駐メモリのピークが
# 926MB から 4.6GB に増える。台帳は「どれを片付けるか」を知るためのもので、
# 生かしておくためのものではない。
_known_windows = []


@pytest.fixture(autouse=True)
def retain_windows_dock_hierarchies(monkeypatch):
    """共有テストプロセスでの QtAds のネイティブ破棄を防ぐ。

    pytest-qt はフィクスチャの後始末より先に deleteLater とそのイベント処理を
    行うので、フィクスチャのファイナライザーで遅延破棄を取り消すのでは遅い。

    台帳（弱参照）への登録は全環境で行う。後始末の対象をここから引くことで
    ``QApplication.topLevelWidgets()`` を歩かずに済む（理由は
    :func:`_live_main_windows`）。破棄を止めるのは Windows だけ。Linux で窓を
    保持すると常駐メモリが 926MB から 5.2GB に膨らみ、ウィジェットの生成中に
    別のセグフォルトに変わるため（PR の説明を参照）。
    """
    import sys

    from paintmaskanimator.main_window import MainWindow

    original_init = MainWindow.__init__

    def initialize(window, *args, **kwargs):
        import weakref

        original_init(window, *args, **kwargs)
        _known_windows.append(weakref.ref(window))
        if sys.platform == "win32":
            _retained_windows.append(window)

    def retain(window):
        from PySide6.QtCore import QCoreApplication, QEvent

        QCoreApplication.removePostedEvents(window, QEvent.Type.DeferredDelete)

    monkeypatch.setattr(MainWindow, "__init__", initialize)
    if sys.platform == "win32":
        monkeypatch.setattr(MainWindow, "deleteLater", retain)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item):
    # pytest-qt はフィクスチャの後始末より先に、登録済みウィジェットを
    # close() する。この時点でテストの assert は終わっているので、イベントを
    # 回される前にサブビューのフィルターを外しておく。
    #
    # ここで窓を「破棄」してはいけない。破棄すると、続く pytest-qt の
    # close() や e2e の app フィクスチャが死んだ C++ オブジェクトに触り、
    # ``libshiboken: Internal C++ object (MainWindow) already deleted`` が
    # 大量に出る（Linux の CI で 225 件以上）。破棄はフィクスチャの
    # ファイナライザー（``quiesce_leftover_main_windows``）に任せる。
    _quiesce_leftover_main_windows()
    yield


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
        # Existing UI tests close edited documents during teardown. Discard
        # there; save/close regression tests install their own dialog handler.
        if isinstance(self, QMessageBox) and self.objectName() == "unsavedCloseConfirmation":
            for button in self.buttons():
                if self.buttonRole(button) == QMessageBox.ButtonRole.DestructiveRole:
                    button.click()
                    return 0
        raise AssertionError(
            f"unexpected modal dialog: {type(self).__name__} / {self.windowTitle()!r}"
        )

    monkeypatch.setattr(QDialog, "exec", _fail_exec)


@pytest.fixture(autouse=True)
def quiesce_leftover_main_windows():
    """テストが残した ``MainWindow`` を黙らせる。

    テストは窓を ``close()`` するだけで、閉じても窓はセッションの間ずっと生き
    残る。窓はそれぞれタイマーを持ち、サブビューはアプリケーション全体の
    イベントを監視し続ける。閉じ忘れた窓は自分のフィルターも残す。すると
    あらゆるイベントが何十ものフィルターを通るようになり、あとに来る窓の
    テスト（``test_docking.py``）が 1 件に 1 分以上かかって固まったように
    見える。

    Windows では窓を破棄できない（QtAds が壊れる）ので、フィルターとタイマーを
    止めるだけにして窓は隔離したまま残す。それ以外では破棄する
    （``_tidy_up_leftover_main_windows`` のコメントを参照）。
    """
    _tidy_up_leftover_main_windows()
    yield
    _tidy_up_leftover_main_windows()


def _live_main_windows():
    """テスト中に作られ、まだ生きている ``MainWindow`` を列挙する。

    ``QApplication.topLevelWidgets()`` は歩かない。あの一覧には、誰も所有して
    いないウィジェットが混ざっているうえ、テストのたびに増えていく。
    ``MainWindow`` を 1 つ作って破棄すると、親なしのトップレベルが 6 つ
    （QCheckBox 1・QSpinBox 1・QWidget 4。Qt が QSS 適用のために作る見本
    ウィジェット）取り残され、破棄のたびに単調に積み上がることを確認した。
    さらに QtAds の ``CDockWidget`` は、破棄後に残ったキュー済みコールバック
    から触られて ``already deleted`` を出す。CI の ubuntu 3.12 のログに出て
    いた 30 件の ``QSpinBox already deleted`` はこの置き去りぶんである。
    その一覧を後始末のたびに（= 1300 回以上）歩き、要素ごとに Python ラッパー
    を作り直していたのが ``topLevelWidgets()`` でのセグフォルトの現場だった。

    代わりに、自分で登録した台帳（``_known_windows``）だけを見る。ここに入る
    のは ``MainWindow.__init__`` を通った窓だけなので、shiboken が破棄を把握
    しているオブジェクトしかなく、``isValid`` の判定が信頼できる。
    """
    from shiboken6 import isValid

    windows = []
    dead = []
    for reference in _known_windows:
        window = reference()
        if window is None:
            dead.append(reference)
        elif isValid(window):
            windows.append(window)
    for reference in dead:
        _known_windows.remove(reference)
    return windows


def _quiesce_leftover_main_windows():
    """窓を閉じてフィルターを外す。破棄はしない。

    共有の ``QApplication`` のなかで、QtAds のドックを持つ ``MainWindow`` を
    破棄するのは安全ではない。Windows では access violation やヒープ破壊で
    落ち、Linux では ``QApplication.topLevelWidgets()`` を歩いている最中に
    セグフォルトする。``scripts/run_tests.py`` がどの環境でもプロセスを強制
    終了しているのも、インタープリター終了時に QtAds が破棄済みオブジェクトへ
    触るという同じ問題のためである。

    なので破棄はせず、フィルターとタイマーだけ止める。遅くなる原因は破棄の
    有無ではなくフィルターの残留なので、これで足りる。窓は run_tests.py が
    プロセスを終わらせるまで生きたままになる。
    """
    windows = _live_main_windows()
    if not windows:
        return
    from PySide6.QtCore import QCoreApplication, QEvent

    from paintmaskanimator.utils import remove_app_event_filter

    # close() が Qt のイベントを出す*前*に、積まれた遅延破棄を取り消す。
    # あとからやると close() の途中でドック階層が消えることがある。
    for window in windows:
        QCoreApplication.removePostedEvents(window, QEvent.Type.DeferredDelete)

    for window in windows:
        # close() で止まるのはメインウィンドウ自身のフィルターとタイマーだけ。
        # サブビューのフィルターは残るので、ここで外す（遅くなる原因はこれ）。
        if not getattr(window, "_closing", False):
            window._closing = True
            window.close()
        remove_app_event_filter(window.subview)

    # close() の最中にテスト側が deleteLater() を積み直していることがある。
    for window in windows:
        QCoreApplication.removePostedEvents(window, QEvent.Type.DeferredDelete)


def _tidy_up_leftover_main_windows():
    """窓を黙らせ、そのうえで（Windows 以外では）破棄する。

    Windows では、ドッキング（QtAds）の窓を持つメインウィンドウを破棄すると
    access violation やヒープ破壊で落ちる。なので窓は隔離したまま残し、
    ``scripts/run_tests.py`` がプロセスを終わらせるのに任せる。

    それ以外では破棄する。保持したままだと窓 1 件あたり約 13MB が積み上がり、
    全件実行の常駐メモリのピークが 926MB から 5.2GB に増えて、こんどは
    ウィジェットの生成中にセグフォルトするようになる（CI で確認）。
    ``topLevelWidgets()`` を歩くのをやめた以上、破棄しても列挙の現場は踏まない。

    pytest-qt の close() とフィクスチャの後始末が済んだあとに呼ぶこと。
    """
    import sys

    _quiesce_leftover_main_windows()
    if sys.platform == "win32":
        return
    windows = _live_main_windows()
    if not windows:
        return
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid

    app = QApplication.instance()
    if app is not None:
        # 破棄する前に、積まれた 0ms タイマー（``QTimer.singleShot``）を消化
        # させる。窓が生きているあいだに流さないと、破棄したあとに発火して
        # ``CDockWidget already deleted`` になる（``main_window_ui_build`` の
        # ``_resize_tool_selector_area`` がこれ）。
        app.processEvents()
    for window in _live_main_windows():
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    if app is not None:
        app.processEvents()
    _known_windows[:] = [
        reference for reference in _known_windows
        if (window := reference()) is not None and isValid(window)
    ]
