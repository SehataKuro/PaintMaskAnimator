"""Fail-friendly check for the required runtime dependencies.

Double-clicking a ``.py`` normally closes the console immediately, so a missing
PySide6/numpy would flash an unreadable traceback. This module turns that into a
visible dialog with a copy-pasteable install command.

It must run **before** anything imports Qt, so the application entry points call
:func:`require_runtime_dependencies` as their first statement. It deliberately
imports no Qt itself -- the whole point is that Qt may be absent.
"""
from __future__ import annotations

import importlib

from .i18n import tr
from .constants import APP_DISPLAY_NAME

#: Modules the application cannot start without.
REQUIRED_MODULES = ("PySide6.QtWidgets", "numpy")


def require_runtime_dependencies(modules: tuple[str, ...] = REQUIRED_MODULES) -> None:
    """Exit with a readable message if a required third-party module is missing."""
    for module in modules:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            _report_and_exit(exc.name or module)


def _report_and_exit(missing: str) -> None:
    message = (
        tr("{missing} がインストールされていないため起動できません。\n\nWindowsのコマンドプロンプトで次を実行してください。\n\npy -m pip install PySide6 numpy\n\nインストール後、このファイルをもう一度起動してください。").format(missing=missing)
    )
    print(message)
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(tr("{NAME} 起動エラー").format(NAME=APP_DISPLAY_NAME), message)
        root.destroy()
    except Exception:
        # tkinter itself may be missing or unusable (no display); the console
        # message above is the fallback, so ignore any GUI-dialog failure.
        pass
    raise SystemExit(1)
