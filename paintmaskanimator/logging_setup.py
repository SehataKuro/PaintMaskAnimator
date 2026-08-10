"""Application logging setup (Qt-independent).

Provides a single ``configure_logging()`` entry point that installs a rotating
file handler under the per-user config directory, plus a ``get_logger()``
helper so modules can obtain a namespaced logger without repeating the package
prefix. Keeping this separate from ``__main__`` means non-GUI code paths (and
tests) can log without pulling in Qt.
"""
import logging
import logging.handlers

_CONFIGURED = False


def log_path():
    """Return the path to the application log file (dir created on demand)."""
    from . import config

    return config.config_dir() / "app.log"


def configure_logging(level=logging.INFO):
    """Install a rotating file handler for the package logger.

    Idempotent: repeated calls are no-ops so importing modules or re-entering
    ``main()`` never stacks duplicate handlers. Falls back silently to a
    console handler if the log file cannot be opened (e.g. read-only install
    dir), since logging must never itself crash the app.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger = logging.getLogger("paintmaskanimator")
    logger.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    handler = None
    try:
        path = log_path()
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    _CONFIGURED = True


def get_logger(name=None):
    """Return a logger under the ``paintmaskanimator`` namespace.

    ``name`` is usually ``__name__``; the package prefix is stripped so the
    logger name stays short and consistent regardless of import path.
    """
    base = "paintmaskanimator"
    if not name or name == base:
        return logging.getLogger(base)
    suffix = name
    if name.startswith(base + "."):
        suffix = name[len(base) + 1:]
    return logging.getLogger(f"{base}.{suffix}")
