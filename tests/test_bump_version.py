"""Tests for scripts/bump_version.py."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "bump_version",
    Path(__file__).resolve().parent.parent / "scripts" / "bump_version.py",
)
assert _spec is not None and _spec.loader is not None
bv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bv)

CHANGELOG = """# 変更履歴

## [未リリース]

### 追加

- 新機能

## [0.6.4] - 2026-08-23

### 修正

- 不具合修正

[0.6.4]: https://github.com/SehataKuro/PaintMaskAnimator/releases/tag/v0.6.4
"""


def test_changelog_unreleased_becomes_the_new_version():
    bumped = bv.bump_changelog(CHANGELOG, "0.6.5", "2026-09-30")
    assert "## [未リリース]\n\n## [0.6.5] - 2026-09-30\n\n### 追加\n\n- 新機能" in bumped
    assert bumped.index("[0.6.5]: ") < bumped.index("[0.6.4]: ")
    assert "releases/tag/v0.6.5\n[0.6.4]" in bumped


def test_bumped_changelog_yields_release_notes():
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location(
        "extract_release_notes",
        Path(__file__).resolve().parent.parent / "scripts" / "extract_release_notes.py",
    )
    assert spec is not None and spec.loader is not None
    ern = module_from_spec(spec)
    spec.loader.exec_module(ern)
    bumped = bv.bump_changelog(CHANGELOG, "0.6.5", "2026-09-30")
    assert ern.extract_release_notes(bumped, "0.6.5") == "### 追加\n\n- 新機能\n"


def test_empty_unreleased_section_is_refused():
    empty = CHANGELOG.replace("### 追加\n\n- 新機能\n\n", "")
    with pytest.raises(ValueError, match="empty"):
        bv.bump_changelog(empty, "0.6.5", "2026-09-30")


def test_existing_version_is_refused():
    with pytest.raises(ValueError, match="already exists"):
        bv.bump_changelog(CHANGELOG, "0.6.4", "2026-09-30")


def test_pyproject_only_the_project_version_changes():
    pyproject = '[build-system]\nrequires = ["x"]\n\n[project]\nname = "p"\nversion = "0.6.4"\n\n[tool.ruff]\ntarget-version = "py310"\n'
    bumped = bv.bump_pyproject(pyproject, "0.6.5")
    assert 'version = "0.6.5"' in bumped
    assert 'target-version = "py310"' in bumped


def test_badge():
    readme = "[![Release](https://img.shields.io/badge/release-v0.6.4-blue)](x)"
    assert "release-v0.6.5-blue" in bv.bump_badge(readme, "0.6.5")
