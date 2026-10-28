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

# Validate the required Rust API before building; never silently omit it.
from paintmaskanimator import native
native.validate_backend(native._native)
hiddenimports = collect_submodules("pma_native")
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


# --- 配布物の容量削減 -------------------------------------------------------
# PyInstaller の Qt フックは、QtQml を読み込むだけで QML 一式と、それが使う
# Qt のライブラリ（Quick3D、Charts、Multimedia など）をまとめて入れてしまう。
# このアプリが使うのは Widgets と、アクション編集の Monaco 用の WebEngine
# （Widgets 版）だけなので、使わないものを外す。
import os
import re
from PyInstaller.depend import bindepend


def _norm(dest):
    return dest.replace("\\", "/")


# WebEngine の画面の言語（右クリックメニューなど）。アプリの言語だけ残す。
WEBENGINE_LOCALES = {"en-US.pak", "ja.pak"}
# Qt 自身の翻訳（i18n.py が qtbase_<言語> を読む）。アプリの言語だけ残す。
QT_TRANSLATION_LANGS = ("_en", "_ja")
# Monaco の言語ワーカー。エディターは Python だけなので、TypeScript・CSS・
# HTML・JSON 用は要らない（エディター本体の editor.worker は残す）。
MONACO_UNUSED_WORKERS = re.compile(r"^(ts|css|html|json)\.worker\b")
# 使わない Qt のプラグイン。QML のデバッグ、PDF の画像読み込み（QtPdf を連れて
# くる）、仮想キーボード（QtQuick 版）、位置情報。
UNUSED_QT_PLUGINS = re.compile(
    r"^(lib)?(qmldbg_\w+|qpdf|qtvirtualkeyboardplugin|qtposition_\w+)\.(dylib|dll|so)$"
)


def _drop_file(dest):
    d = _norm(dest)
    base = os.path.basename(d)
    if d.startswith(("PySide6/Qt/qml/", "PySide6/qml/")):
        return True
    if "/plugins/" in d and UNUSED_QT_PLUGINS.match(base):
        return True
    if os.path.basename(os.path.dirname(d)) == "qtwebengine_locales":
        return base not in WEBENGINE_LOCALES
    # 開発者ツールは開かない。
    if base == "qtwebengine_devtools_resources.pak":
        return True
    if d.startswith(("PySide6/Qt/translations/", "PySide6/translations/")) \
            and base.endswith(".qm"):
        return not base[:-3].endswith(QT_TRANSLATION_LANGS)
    # vs/ 直下の同名ファイルは、エディター本体が起動時に読み込む小さな登録用の
    # ファイルなので残す。中身は vs/assets/ と vs/language/ の下にある。
    if d.startswith(("paintmaskanimator/assets/monaco/vs/assets/",
                     "paintmaskanimator/assets/monaco/vs/language/")):
        return bool(MONACO_UNUSED_WORKERS.match(base))
    return False


def _qt_lib_key(dest):
    """Qt のライブラリなら名前（小文字）、そうでなければ None。"""
    d = _norm(dest)
    m = re.match(r"PySide6/Qt/lib/(Qt\w+)\.framework/", d)  # macOS
    if m:
        return m.group(1).lower()
    base = os.path.basename(d)
    if re.fullmatch(r"Qt6\w+\.dll", base, re.IGNORECASE) and d.count("/") == 1:
        return base.lower()  # Windows: PySide6/Qt6Core.dll
    return None


def _trim(binaries, datas):
    binaries = [e for e in binaries if not _drop_file(e[0])]
    datas = [e for e in datas if not _drop_file(e[0])]

    # 残ったモジュール・プラグインから実際にたどれる Qt のライブラリだけ残す。
    by_key = {}
    for entry in binaries:
        key = _qt_lib_key(entry[0])
        if key is not None:
            by_key.setdefault(key, []).append(entry)
    keep = set()
    pending = [e for e in binaries if _qt_lib_key(e[0]) is None]
    while pending:
        entry = pending.pop()
        try:
            imports = bindepend.get_imports(entry[1])
        except Exception:
            continue
        for name, _path in imports:
            key = os.path.basename(_norm(name)).lower()
            if key in by_key and key not in keep:
                keep.add(key)
                pending += by_key[key]

    def wanted(entry):
        key = _qt_lib_key(entry[0])
        return key is None or key in keep

    # 依存をたどれなかったら、何も入らない壊れた配布物になる。ここで止める。
    for core in ("qtwebenginecore", "qt6webenginecore.dll"):
        if core in by_key and core not in keep:
            raise SystemExit(f"[spec] {core} was not reached; refusing to trim Qt libraries")

    dropped = sorted(k for k in by_key if k not in keep)
    print(f"[spec] dropping {len(dropped)} unused Qt libraries: {', '.join(dropped)}")
    return [e for e in binaries if wanted(e)], [e for e in datas if wanted(e)]


a.binaries, a.datas = _trim(a.binaries, a.datas)

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
            # .pman をダブルクリックで開く。中身は zip だが、zip として
            # 展開されないよう public.data にだけ準ずる独自の種類にする。
            "UTExportedTypeDeclarations": [{
                "UTTypeIdentifier": "com.sehatakuro.paintmaskanimator.project",
                "UTTypeDescription": "PaintMaskAnimator Project",
                "UTTypeConformsTo": ["public.data", "public.content"],
                "UTTypeTagSpecification": {"public.filename-extension": ["pman"]},
            }],
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "PaintMaskAnimator Project",
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Owner",
                "LSItemContentTypes": ["com.sehatakuro.paintmaskanimator.project"],
            }],
        },
    )

    # Finder のアイコンを保存時のサムネイルにする Quick Look の拡張。
    _sys.path.insert(0, os.path.join(os.path.dirname(SPEC), "macos"))
    import build_thumbnail_extension
    build_thumbnail_extension.build(
        os.path.join(DISTPATH, "PaintMaskAnimator.app"), PROJECT_VERSION
    )
