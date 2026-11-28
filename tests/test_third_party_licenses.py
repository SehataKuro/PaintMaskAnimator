"""Tests for scripts/generate_third_party_licenses.py and app/THIRD_PARTY_LICENSES.md."""
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "generate_third_party_licenses",
    ROOT / "scripts" / "generate_third_party_licenses.py",
)
assert _spec is not None and _spec.loader is not None
gtl = importlib.util.module_from_spec(_spec)
# dataclasses looks the module up in sys.modules while the class is built.
sys.modules[_spec.name] = gtl
_spec.loader.exec_module(gtl)


def _pkg(name, manifest, *, source="registry+https://github.com/rust-lang/crates.io-index",
         kind="lib", license="MIT"):
    return {
        "id": name, "name": name, "version": "1.0.0", "license": license,
        "manifest_path": str(manifest), "source": source,
        "targets": [{"kind": [kind]}],
    }


def _dep(pkg, kind=None):
    return {"pkg": pkg, "dep_kinds": [{"kind": kind, "target": None}]}


def _metadata(tmp_path):
    reg = tmp_path / "registry"
    packages = [
        _pkg("app", gtl.ROOT / "app" / "Cargo.toml", source=None),
        _pkg("core", gtl.ROOT / "native" / "core" / "Cargo.toml", source=None),
        _pkg("vendored", gtl.VENDOR_DIR / "x" / "Cargo.toml", source=None),
        _pkg("linked", reg / "linked" / "Cargo.toml"),
        _pkg("deep", reg / "deep" / "Cargo.toml"),
        _pkg("macro", reg / "macro" / "Cargo.toml", kind="proc-macro"),
        _pkg("macro-only", reg / "macro-only" / "Cargo.toml"),
        _pkg("build-only", reg / "build-only" / "Cargo.toml"),
        _pkg("dev-only", reg / "dev-only" / "Cargo.toml"),
    ]
    nodes = [
        {"id": "app", "deps": [_dep("core"), _dep("linked"), _dep("macro"),
                               _dep("build-only", "build"), _dep("dev-only", "dev")]},
        {"id": "core", "deps": [_dep("vendored")]},
        {"id": "vendored", "deps": []},
        {"id": "linked", "deps": [_dep("deep")]},
        {"id": "deep", "deps": []},
        {"id": "macro", "deps": [_dep("macro-only")]},
        {"id": "macro-only", "deps": []},
        {"id": "build-only", "deps": []},
        {"id": "dev-only", "deps": []},
    ]
    return {"packages": packages, "resolve": {"root": "app", "nodes": nodes}}


def test_linked_packages_follow_normal_edges_only(tmp_path):
    names = {p["name"] for p in gtl.linked_packages(_metadata(tmp_path))}
    # First-party crates are left out; vendored third-party code is kept.
    # Build/dev dependencies and proc-macros (and what only they use) never
    # reach the binary.
    assert names == {"vendored", "linked", "deep"}


def test_license_ids_handles_old_and_new_syntax():
    assert gtl.license_ids("MIT/Apache-2.0") == ["MIT", "Apache-2.0"]
    assert gtl.license_ids("(MIT OR Apache-2.0) AND Unicode-3.0") == [
        "MIT", "Apache-2.0", "Unicode-3.0"]


def _crate(license, name="c"):
    return gtl.Crate(name=name, version="1.0.0", license=license, repository="",
                     authors=[], manifest_dir=Path("."), vendored=False)


@pytest.mark.parametrize("license", ["GPL-3.0-only", "LGPL-2.1-or-later", "MPL-2.0", "", "Foo"])
def test_concerns_flags_copyleft_and_unknown(license):
    assert gtl.concerns([_crate(license)])


@pytest.mark.parametrize("license", ["MIT OR Apache-2.0", "Unicode-3.0", "BSL-1.0", "ISC"])
def test_concerns_passes_permissive(license):
    assert gtl.concerns([_crate(license)]) == []


def test_fallback_text_names_the_authors():
    crate = _crate("Zlib OR Apache-2.0 OR MIT")
    crate.authors = ["Someone <a@b.c>"]
    spdx, text = gtl.fallback_text(crate)
    assert spdx == "MIT"
    assert text.startswith("Copyright (c) Someone\n")


@pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo is not installed")
def test_every_linked_crate_is_in_the_notice():
    """A crate added to app/ (or the thumbnail DLL) without regenerating fails here."""
    notice = gtl.OUTPUT.read_text(encoding="utf-8")
    rows = {tuple(cell.strip() for cell in line.split("|")[1:3])
            for line in notice.splitlines() if line.startswith("| ")}
    missing = []
    for crate in gtl.collect():
        name_cell = f"[{crate.name}]" if crate.repository else crate.name
        if not any(r[0].startswith(name_cell) and r[1] == crate.version for r in rows):
            missing.append(crate.label)
    assert not missing, (
        f"not in {gtl.OUTPUT.relative_to(ROOT)}: {', '.join(missing)} — "
        "run python scripts/generate_third_party_licenses.py")
