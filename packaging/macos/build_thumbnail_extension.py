"""Build the Quick Look thumbnail extension and put it into the .app.

Called from PaintMaskAnimator.spec after BUNDLE on macOS; can also be run by
hand on an existing build:
    python packaging/macos/build_thumbnail_extension.py dist/PaintMaskAnimator.app 1.2.3

Needs the Xcode command line tools (swiftc). The app is signed ad hoc, like
PyInstaller already does, because macOS only loads signed, sandboxed
extensions -- so the outer app is re-signed after the extension goes in.
"""
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent / "thumbnail_extension"
NAME = "PmanThumbnail"
MIN_MACOS = "11.0"


def build(app_path, version):
    app_path = Path(app_path)
    appex = app_path / "Contents" / "PlugIns" / f"{NAME}.appex"
    if appex.exists():
        shutil.rmtree(appex)
    macos_dir = appex / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)

    info = (HERE / "Info.plist").read_text(encoding="utf-8")
    info = info.replace("@VERSION@", version).replace("@MIN_MACOS@", MIN_MACOS)
    (appex / "Contents" / "Info.plist").write_text(info, encoding="utf-8")

    arch = platform.machine()  # same architecture as the bundled Python
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [
                "xcrun", "swiftc",
                "-O",
                "-parse-as-library",
                "-module-name", NAME,
                "-target", f"{arch}-apple-macos{MIN_MACOS}",
                "-application-extension",
                # Extensions start in NSExtensionMain, not a main() of ours.
                "-Xlinker", "-e", "-Xlinker", "_NSExtensionMain",
                "-framework", "QuickLookThumbnailing",
                "-module-cache-path", tmp,
                "-o", str(macos_dir / NAME),
                str(HERE / "ThumbnailProvider.swift"),
            ],
            check=True,
        )

    subprocess.run(
        [
            "codesign", "--force", "--sign", "-",
            "--entitlements", str(HERE / "entitlements.plist"),
            str(appex),
        ],
        check=True,
    )
    # Inside out: the app's signature seals the extension it contains.
    subprocess.run(["codesign", "--force", "--sign", "-", str(app_path)], check=True)

    with open(appex / "Contents" / "Info.plist", "rb") as handle:
        plistlib.load(handle)  # fail the build on a broken plist
    print(f"[thumbnail] built {appex}")


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "0")
