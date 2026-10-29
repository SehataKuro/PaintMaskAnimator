"""Write the coverage totals from a Cobertura XML report to the CI run.

``coverage.xml`` used to be uploaded as a build artifact that nothing ever
read -- no Codecov upload, no PR comment -- so the number was only visible
by opening the test log. This renders it into the job summary instead, where
GitHub shows it on the run page.

Outside Actions (``GITHUB_STEP_SUMMARY`` unset) it just prints to stdout, so
it is also usable locally after a ``--cov-report=xml`` run.
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def rate(root: ET.Element, attribute: str) -> float | None:
    """A Cobertura ``*-rate`` attribute as a percentage, if present."""
    raw = root.get(attribute)
    if raw is None:
        return None
    try:
        return float(raw) * 100.0
    except ValueError:
        return None


def summary(report: Path) -> str:
    root = ET.parse(report).getroot()
    lines = ["## Coverage", ""]
    for label, attribute in (("Lines", "line-rate"), ("Branches", "branch-rate")):
        value = rate(root, attribute)
        if value is not None:
            lines.append(f"- {label}: **{value:.2f}%**")
    covered = root.get("lines-covered")
    valid = root.get("lines-valid")
    if covered is not None and valid is not None:
        lines.append(f"- Covered statements: {covered} / {valid}")
    lines.append("")
    lines.append("The browsable HTML report is published with the feature map.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    report = Path(argv[1] if len(argv) > 1 else "coverage.xml")
    if not report.exists():
        print(f"coverage report not found: {report}", file=sys.stderr)
        return 1

    text = summary(report)
    print(text)
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
