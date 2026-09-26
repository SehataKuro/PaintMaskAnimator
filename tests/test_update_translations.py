"""Tests for the untranslated-entry check in scripts/update_translations.py."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "update_translations",
    Path(__file__).resolve().parent.parent / "scripts" / "update_translations.py",
)
assert _spec is not None and _spec.loader is not None
ut = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ut)


def test_untranslated_lists_only_unfinished_entries(tmp_path):
    catalogue = tmp_path / "paintmaskanimator_en.ts"
    catalogue.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="en" sourcelanguage="ja">
<context>
    <name></name>
    <message>
        <source>保存</source>
        <translation>Save</translation>
    </message>
    <message>
        <source>書き出す</source>
        <translation type="unfinished"></translation>
    </message>
    <message>
        <source>古い文字列</source>
        <translation type="vanished">Old string</translation>
    </message>
</context>
</TS>
""",
        encoding="utf-8",
    )
    assert ut.untranslated(catalogue) == ["書き出す"]


def test_committed_catalogues_are_fully_translated():
    for path in ut.TRANSLATIONS.glob("*.ts"):
        assert ut.untranslated(path) == [], path.name
