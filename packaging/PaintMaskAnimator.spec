# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: build PaintMaskAnimator as a windowed Windows app.

Build from the repo root:
    pyinstaller packaging/PaintMaskAnimator.spec --noconfirm
Output: dist/PaintMaskAnimator/PaintMaskAnimator.exe (one-folder build).
"""
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
# Optional deps: bundle them if present in the build environment.
for optional in ("PIL", "psd_tools"):
    try:
        __import__(optional)
        hiddenimports += collect_submodules(optional)
    except Exception:
        pass

a = Analysis(
    ["../PaintMaskAnimator.py"],
    pathex=[],
    binaries=[],
    datas=[],
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
            "CFBundleShortVersionString": "0.6.1",
            "CFBundleVersion": "0.6.1",
            "NSHighResolutionCapable": True,
        },
    )
