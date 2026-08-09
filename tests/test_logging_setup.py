"""Tests for the logging infrastructure."""
import logging

from paintmaskanimator import logging_setup


def test_log_path_under_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = logging_setup.log_path()
    assert path.name == "app.log"
    assert str(tmp_path) in str(path)


def test_configure_logging_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(logging_setup, "_CONFIGURED", False)
    logger = logging.getLogger("paintmaskanimator")
    logger.handlers = []
    try:
        logging_setup.configure_logging()
        first = list(logger.handlers)
        logging_setup.configure_logging()
        assert logger.handlers == first
        assert len(logger.handlers) == 1
    finally:
        logger.handlers = []
        logging_setup._CONFIGURED = False


def test_get_logger_namespacing():
    assert logging_setup.get_logger().name == "paintmaskanimator"
    assert logging_setup.get_logger("paintmaskanimator").name == "paintmaskanimator"
    assert (
        logging_setup.get_logger("paintmaskanimator.canvas").name
        == "paintmaskanimator.canvas"
    )
    assert logging_setup.get_logger("canvas").name == "paintmaskanimator.canvas"
