"""Run the checks CI and the release workflow run, locally, before pushing.

Changes merged into ``main`` locally reach CI only when pushed, and a release
tag is the first push the release workflow sees -- so a stale translation
catalogue or a pyright error was found only after tagging. Run this first::

    python scripts/preflight.py                  # everything, including tests
    python scripts/preflight.py --quick          # skip the test suite
    python scripts/preflight.py --release 0.6.5  # also check the version/CHANGELOG

``.githooks/pre-push`` runs ``--quick`` automatically once enabled with
``git config core.hooksPath .githooks``.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _steps(args) -> list[tuple[str, list[str]]]:
    py = sys.executable
    version = [args.release] if args.release else []
    steps = [
        ("ruff", [py, "-m", "ruff", "check", "paintmaskanimator", "tests", "scripts"]),
        ("version", [py, "scripts/check_version.py", *version]),
        ("pyright", [py, "-m", "pyright"]),
        ("translations", [py, "scripts/update_translations.py", "--check"]),
    ]
    if not args.quick:
        steps.append(("tests", [py, "scripts/run_tests.py", "-q", "-p", "no:cacheprovider"]))
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true", help="skip the test suite")
    parser.add_argument("--release", metavar="VERSION", help="also validate this release version")
    args = parser.parse_args()

    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONIOENCODING": "utf-8"}
    failed = []
    for name, command in _steps(args):
        print(f"== {name}", flush=True)
        if subprocess.run(command, cwd=ROOT, env=env).returncode != 0:
            failed.append(name)
    if failed:
        print(f"\npreflight failed: {', '.join(failed)}")
        return 1
    print("\npreflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
