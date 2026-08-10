"""Validate a pytest JUnit-XML report, independent of the process exit code.

Running the whole GUI suite in one interpreter leaves several live PySide6
``CDockManager`` windows whose C++ static destructors tear down, at interpreter
exit, in an order that intermittently segfaults (exit code 139/134) *after*
every test has already passed and the report was written. Keying CI off the
process exit code would therefore fail spuriously.

This script reads the JUnit XML pytest wrote at end-of-session and fails only
on real problems:

* the report is missing or unparseable  -> a crash *before* results were
  written (e.g. mid-run), which must surface;
* any test failure or error;
* zero tests collected.

A post-run teardown segfault leaves a complete, all-green report, so it passes.
"""
import sys
import xml.etree.ElementTree as ET


def main(path):
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        print(f"pytest report {path!r} missing or unreadable: {exc}")
        print("A crash before results were written — failing.")
        return 1

    suites = list(root.iter("testsuite"))
    total = sum(int(s.get("tests", 0)) for s in suites)
    failures = sum(int(s.get("failures", 0)) for s in suites)
    errors = sum(int(s.get("errors", 0)) for s in suites)
    print(f"tests={total} failures={failures} errors={errors}")

    if total == 0:
        print("No tests ran — failing.")
        return 1
    if failures or errors:
        return 1
    return 0


if __name__ == "__main__":
    report = sys.argv[1] if len(sys.argv) > 1 else "pytest-results.xml"
    sys.exit(main(report))
