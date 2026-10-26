"""Run pytest without invoking unsafe QtAds static destructors.

PySide6-QtAds can access already-destroyed Qt objects during interpreter
shutdown after pytest has completely finished.  Exiting directly after
``pytest.main`` preserves pytest's real status while avoiding that third-party
native teardown defect.  This replaces the former CI ``|| true`` workaround.

On Windows, ``os._exit`` is not direct enough: the C runtime's ``_exit`` still
runs enough teardown for a C++ exception to escape and abort the process, so
the run fails with 0xC0000409 (or an access violation) even though every test
passed -- intermittently, in roughly half of the runs.  ``TerminateProcess``
on our own process stops it there and then, with the status pytest reported.
"""
from __future__ import annotations

import os
import sys

import pytest


def _hard_exit(status: int) -> None:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), status)
    os._exit(status)


def main() -> None:
    status = int(pytest.main(sys.argv[1:]))
    sys.stdout.flush()
    sys.stderr.flush()
    _hard_exit(status)


if __name__ == "__main__":
    main()
