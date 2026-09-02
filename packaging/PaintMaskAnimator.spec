# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: build PaintMaskAnimator as a windowed Windows app.

Build from the repo root:
    pyinstaller packaging/PaintMaskAnimator.spec --noconfirm
Output: dist/PaintMaskAnimator/PaintMaskAnimator.exe (one-folder build).
"""
from PyInstaller.utils.hooks import collect_submodules
import pathlib
import tomllib

PROJECT_VERSION = tomllib.loads(
    pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
)["project"]["version"]

hiddenimports = []
# Optional deps: bundle them if present in the build environment.
for optional in ("PIL", "psd_tools", "PySide6QtAds"):
    try:
        __import__(optional)
        hiddenimports += collect_submodules(optional)
    except Exception:
        pass

a = Analysis(
    ["../PaintMaskAnimator.py"],
    pathex=[],
    binaries=[],
    # Bundle the canonical version source as well as the application assets.
    # constants.py reads this before importlib distribution metadata, avoiding
    # stale version strings in every UI location that displays APP_VERSION.
    datas=[
        ("../paintmaskanimator/assets", "paintmaskanimator/assets"),
        # Compiled translation catalogues; i18n.install() reads them from here.
        ("../paintmaskanimator/translations", "paintmaskanimator/translations"),
        ("../pyproject.toml", "."),
        # LGPL compliance: ship the license texts with the binary.
        ("../LICENSE", "."),
        ("../NOTICE", "."),
        ("../THIRD_PARTY_LICENSES.md", "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PaintMaskAnimator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed GUI app
    icon="app.ico" if __import__("os").path.exists(
        __import__("os").path.join(__import__("os").path.dirname(SPEC), "app.ico")
    ) else None,
)

# NOTE: keep this a one-folder build. The bundled PySide6/Qt libraries are LGPL,
# and laying them down as separate, replaceable files is how the app satisfies
# the relinking requirement -- via LGPLv3 4d(1), which holds whether or not this
# project's own source stays public. A one-file bundle unpacks to a temp
# directory on each launch, so it forfeits that route and would leave the app
# relying on 4d(0), i.e. on the source remaining published. Integrity checks over
# these files breach both routes. The downloadable *installer* is unaffected --
# only the installed layout matters. See THIRD_PARTY_LICENSES.md.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PaintMaskAnimator",
)

# On macOS, wrap the collected app into a proper .app bundle for the .dmg.
import sys as _sys
if _sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="PaintMaskAnimator.app",
        icon="app.icns" if __import__("os").path.exists(
            __import__("os").path.join(__import__("os").path.dirname(SPEC), "app.icns")
        ) else None,
        bundle_identifier="com.sehatakuro.paintmaskanimator",
        info_plist={
            "CFBundleName": "PaintMaskAnimator",
            "CFBundleDisplayName": "PaintMaskAnimator",
            "CFBundleShortVersionString": PROJECT_VERSION,
            "CFBundleVersion": PROJECT_VERSION,
            "NSHighResolutionCapable": True,
        },
    )
