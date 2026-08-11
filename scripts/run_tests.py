"""Run pytest without invoking unsafe QtAds static destructors.

PySide6-QtAds can access already-destroyed Qt objects during interpreter
shutdown after pytest has completely finished.  Exiting directly after
``pytest.main`` preserves pytest's real status while avoiding that third-party
native teardown defect.  This replaces the former CI ``|| true`` workaround.
"""
from __future__ import annotations

import os
import sys

import pytest


def main() -> None:
    status = int(pytest.main(sys.argv[1:]))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)


if __name__ == "__main__":
    main()
