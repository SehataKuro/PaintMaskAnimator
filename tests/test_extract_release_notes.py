"""Tests for release-note extraction from CHANGELOG.md."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "extract_release_notes",
    Path(__file__).resolve().parent.parent / "scripts" / "extract_release_notes.py",
)
assert _spec is not None and _spec.loader is not None
ern = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ern)


def test_extracts_only_requested_version_body():
    changelog = """# 変更履歴

## [0.6.3] - 2026-08-11

### 追加
- 新機能

## [0.6.2] - 2026-08-10

### 修正
- 不具合修正

[0.6.3]: https://example.invalid/v0.6.3
"""

    assert ern.extract_release_notes(changelog, "v0.6.3") == (
        "### 追加\n- 新機能\n"
    )


def test_rejects_missing_version():
    with pytest.raises(ValueError, match="0.7.0"):
        ern.extract_release_notes("## [0.6.3]\n\n- 変更", "0.7.0")


def test_rejects_empty_version_body():
    with pytest.raises(ValueError, match="空"):
        ern.extract_release_notes("## [0.6.3]\n", "0.6.3")


def test_reference_links_are_not_included_in_oldest_release():
    changelog = """## [0.6.2] - 2026-08-10

- 不具合修正

[0.6.2]: https://example.invalid/v0.6.2
"""

    assert ern.extract_release_notes(changelog, "0.6.2") == "- 不具合修正\n"
