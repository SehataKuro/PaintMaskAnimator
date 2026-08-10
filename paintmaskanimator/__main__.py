from .common import *  # noqa: F401,F403
from .main_window import MainWindow


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
    text = "起動中または実行中にエラーが発生しました。"
    if log_path:
        text += f"\n\n詳細を保存しました：\n{log_path}"
    text += f"\n\n{exc_value}"
    app = QApplication.instance()
    # Never open another modal error dialog while Qt is shutting down. Doing so
    # can create an endless loop of errors from already-destroyed widgets.
    if app is not None and not app.closingDown():
        try:
            QMessageBox.critical(None, f"{APP_DISPLAY_NAME} エラー", text)
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
        sys.excepthook(*sys.exc_info())
        raise SystemExit(1) from None
