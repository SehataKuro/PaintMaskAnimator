"""The required-dependency gate must fail loudly and readably, never silently."""
from __future__ import annotations

import builtins

import pytest

from paintmaskanimator import dependency_check


def test_present_modules_pass_through():
    dependency_check.require_runtime_dependencies(("json", "sys"))


def test_required_modules_include_rust_backend():
    assert "paintmaskanimator.native" in dependency_check.REQUIRED_MODULES


def test_old_or_missing_rust_backend_reports_rebuild_instruction(monkeypatch, capsys):
    def missing_backend(name):
        raise ImportError("Rust extension unavailable: python -m pip install ./native")

    monkeypatch.setattr(dependency_check.importlib, "import_module", missing_backend)
    monkeypatch.setattr(builtins, "__import__", _blocking_import(builtins.__import__))
    with pytest.raises(SystemExit) as error:
        dependency_check.require_runtime_dependencies(("paintmaskanimator.native",))
    assert error.value.code == 1
    assert "pip install ./native" in capsys.readouterr().out


def test_missing_module_exits_with_a_readable_message(monkeypatch, capsys):
    monkeypatch.setattr(
        dependency_check.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name=name)),
    )
    # tkinter may or may not be importable in CI; the console message is the
    # contract that must hold either way.
    monkeypatch.setattr(builtins, "__import__", _blocking_import(builtins.__import__))

    with pytest.raises(SystemExit) as exit_info:
        dependency_check.require_runtime_dependencies(("PySide6.QtWidgets",))

    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "PySide6.QtWidgets" in out
    assert "pip install" in out


def _blocking_import(real):
    def guard(name, *args, **kwargs):
        if name == "tkinter" or name.startswith("tkinter."):
            raise ImportError("tkinter unavailable in this test")
        return real(name, *args, **kwargs)

    return guard
