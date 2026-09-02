"""Translation wiring.

The failure mode this guards is silent: if the runtime lookup context does not
match the one ``pyside6-lupdate`` writes into the catalogue, every lookup misses
and returns the source string -- which is already Japanese, so the UI looks
perfectly fine while no translation is ever applied. So these tests build a real
catalogue and assert the string actually changes.
"""
from __future__ import annotations

import os
import shutil
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paintmaskanimator import config, i18n  # noqa: E402

SOURCE = "システムに合わせる"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def restore_translators(qapp):
    yield
    i18n.install(qapp, i18n.DEFAULT_LANGUAGE)


def test_tr_is_identity_without_a_catalogue(qapp, restore_translators):
    i18n.install(qapp, i18n.DEFAULT_LANGUAGE)
    assert i18n.tr(SOURCE) == SOURCE


def test_a_compiled_catalogue_actually_translates(qapp, tmp_path, monkeypatch,
                                                  restore_translators):
    lrelease = shutil.which("pyside6-lrelease")
    if lrelease is None:
        pytest.skip("pyside6-lrelease not available")

    ts = tmp_path / f"{i18n.CATALOGUE}_en.ts"
    ts.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n'
        '<TS version="2.1" language="en" sourcelanguage="ja">\n<context>\n'
        f"    <name>{i18n.CONTEXT}</name>\n    <message>\n"
        f"        <source>{SOURCE}</source>\n"
        "        <translation>Follow system</translation>\n"
        "    </message>\n</context>\n</TS>\n",
        encoding="utf-8",
    )
    subprocess.run(
        [lrelease, str(ts), "-qm", str(ts.with_suffix(".qm"))],
        check=True, capture_output=True,
    )
    monkeypatch.setattr(i18n, "translations_dir", lambda: tmp_path)

    assert i18n.install(qapp, "en") == "en"
    assert i18n.tr(SOURCE) == "Follow system"


def test_install_falls_back_when_the_catalogue_is_missing(qapp, tmp_path, monkeypatch,
                                                          restore_translators):
    monkeypatch.setattr(i18n, "translations_dir", lambda: tmp_path)
    assert i18n.install(qapp, "en") == i18n.DEFAULT_LANGUAGE


def test_preferred_language_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    assert i18n.preferred_language() == i18n.SYSTEM
    i18n.set_preferred_language("en")
    assert i18n.preferred_language() == "en"
    with pytest.raises(ValueError):
        i18n.set_preferred_language("kl")


def test_unknown_saved_language_degrades_to_system(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    config.set_value(i18n.CONFIG_KEY, "kl")
    assert i18n.preferred_language() == i18n.SYSTEM


def test_lupdate_files_strings_under_the_runtime_context(tmp_path):
    """The extractor and the runtime must agree on the context name.

    ``pyside6-lupdate`` cannot see through the :func:`i18n.tr` wrapper to a
    class, so it writes the empty context. If :data:`i18n.CONTEXT` ever drifts
    from that, lookups miss silently.
    """
    lupdate = shutil.which("pyside6-lupdate")
    if lupdate is None:
        pytest.skip("pyside6-lupdate not available")

    source = tmp_path / "sample.py"
    source.write_text(
        "from paintmaskanimator.i18n import tr\n\n\ndef go():\n"
        f'    return tr("{SOURCE}")\n',
        encoding="utf-8",
    )
    catalogue = tmp_path / "sample_en.ts"
    subprocess.run(
        [lupdate, str(source), "-source-language", "ja",
         "-target-language", "en", "-ts", str(catalogue)],
        check=True, capture_output=True,
    )

    text = catalogue.read_text(encoding="utf-8")
    assert f"<source>{SOURCE}</source>" in text, "lupdate did not pick up tr()"
    assert f"<name>{i18n.CONTEXT}</name>" in text
