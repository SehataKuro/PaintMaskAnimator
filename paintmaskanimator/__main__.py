"""Application entry point."""
from .dependency_check import require_runtime_dependencies

# Must run before anything pulls in Qt, so a missing PySide6/numpy produces a
# readable dialog instead of a traceback that vanishes with the console.
require_runtime_dependencies()

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from . import i18n  # noqa: E402
from .i18n import tr  # noqa: E402
from .constants import APP_DISPLAY_NAME, APP_VERSION  # noqa: E402
from .main_window import MainWindow  # noqa: E402


def _show_unhandled_exception(exc_type, exc_value, exc_tb):
    import traceback
    details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    # Prefer the per-user config dir (always writable, unlike an installed
    # Program Files app dir); fall back to next to the executable.
    log_path = None
    try:
        from . import config
        import time
        log_path = config.config_dir() / "crash.log"
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            handle.write(details)
    except Exception:
        try:
            app_file = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)
            log_path = app_file.with_name("oekaki_animator_crash.log")
            log_path.write_text(details, encoding="utf-8")
        except Exception:
            log_path = None
    text = tr("起動中または実行中にエラーが発生しました。")
    if log_path:
        text += tr("\n\n詳細を保存しました：\n{path}").format(path=log_path)
    text += f"\n\n{exc_value}"
    app = QApplication.instance()
    # Never open another modal error dialog while Qt is shutting down. Doing so
    # can create an endless loop of errors from already-destroyed widgets.
    if app is not None and not app.closingDown():
        try:
            QMessageBox.critical(None, tr("{NAME} エラー").format(NAME=APP_DISPLAY_NAME), text)
        except RuntimeError:
            print(details)
    else:
        print(details)


def main():
    from .logging_setup import configure_logging
    configure_logging()
    sys.excepthook = _show_unhandled_exception
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationVersion(APP_VERSION)
    # Before any widget is built: Qt resolves tr() at call time, but menus and
    # dialogs constructed earlier would keep the source-language strings.
    i18n.install_preferred(app)
    from . import theme
    theme.apply_theme(app)
    window = MainWindow()
    window.show()
    QTimer.singleShot(0, window.fit_canvas)
    return app.exec()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        exc_type, exc_value, exc_tb = sys.exc_info()
        if exc_type is not None and exc_value is not None:
            sys.excepthook(exc_type, exc_value, exc_tb)
        raise SystemExit(1) from None
