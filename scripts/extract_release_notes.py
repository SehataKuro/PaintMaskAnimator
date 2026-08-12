"""Extract one version's release notes from CHANGELOG.md."""
from __future__ import annotations

import argparse
import re
from pathlib import Path


def extract_release_notes(changelog: str, version: str) -> str:
    """Return the Markdown content below the requested version heading."""
    normalized = version.lstrip("vV")
    heading = re.compile(
        rf"^## \[(?:v)?{re.escape(normalized)}\](?:\s+-\s+.+)?\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    match = heading.search(changelog)
    if match is None:
        raise ValueError(f"CHANGELOG.md にバージョン {normalized} の項目がありません")

    remainder = changelog[match.end() :]
    boundary = re.search(r"^(?:## \[|\[[^]]+\]:\s)", remainder, re.MULTILINE)
    end = match.end() + boundary.start() if boundary else len(changelog)
    notes = changelog[match.end() : end].strip()
    if not notes:
        raise ValueError(f"バージョン {normalized} の変更内容が空です")
    return notes + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Version to extract, with or without a v prefix")
    parser.add_argument("--changelog", default="CHANGELOG.md")
    parser.add_argument("--output", default="release-notes.md")
    args = parser.parse_args()

    source = Path(args.changelog).read_text(encoding="utf-8")
    try:
        notes = extract_release_notes(source, args.version)
    except ValueError as error:
        parser.error(str(error))
    Path(args.output).write_text(notes, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
