# Third-Party Licenses

PaintMaskAnimator itself is licensed under the Apache License 2.0 (see
[`LICENSE`](LICENSE) and [`NOTICE`](NOTICE)). It depends on, and its binary
distributions bundle, the third-party components below, each under its own
license.

Because PaintMaskAnimator is *not* distributed under the GPL, the obligations
of the LGPL-licensed components below fall on this project directly. The
section "LGPL compliance" states how they are met — **read it before changing
how the application is packaged.**

---

## Qt for Python (PySide6)

- Copyright (C) The Qt Company Ltd. and other contributors
- License: **GNU Lesser General Public License v3.0** (LGPL-3.0-only)
- Homepage: <https://www.qt.io/qt-for-python>
- License text: <https://www.gnu.org/licenses/lgpl-3.0.html>
- Corresponding source: <https://download.qt.io/official_releases/QtForPython/pyside6/>
  (select the version bundled with this release; see below)

## Qt Advanced Docking System (PySide6-QtAds)

- Copyright (C) Uwe Kindler and contributors
- License: **GNU Lesser General Public License v2.1 or later**
- Homepage: <https://github.com/githubuser0xFFFF/Qt-Advanced-Docking-System>
- License text: <https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html>
- Corresponding source: <https://github.com/githubuser0xFFFF/Qt-Advanced-Docking-System>

The "or later" clause lets this component be treated as LGPL-3.0, which is the
basis on which it is combined with the Apache-2.0 licensed application.

## NumPy

- Copyright (C) NumPy Developers
- License: **BSD 3-Clause**
- Homepage: <https://numpy.org/>

## Pillow (optional)

- Copyright (C) Jeffrey A. Clark and contributors
- License: **MIT-CMU**
- Homepage: <https://python-pillow.org/>

## psd-tools (optional)

- Copyright (C) Mikhail Korobov and contributors
- License: **MIT**
- Homepage: <https://github.com/psd-tools/psd-tools>

## clip_to_psd / clipfile-rs (派生コード)

`paintmaskanimator/clip_animation.py` の CLIP STUDIO コンテナ・ラスタータイル・
アニメーションミキサーの読み込み処理は、以下のプロジェクトのフォーマット観察と
アルゴリズムを参考にしています。

- Copyright (c) 2024 dobrokot/clip_to_psd contributors
- Copyright (c) Aodaruma/clipfile-rs contributors
- License: **MIT**
- Homepage: <https://github.com/dobrokot/clip_to_psd>,
  <https://github.com/Aodaruma/clipfile-rs>

### MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.


---

# LGPL compliance

PySide6 and Qt Advanced Docking System are licensed under the LGPL. Combining
them with this Apache-2.0 application produces a "Combined Work" under LGPLv3
section 4, which imposes the following obligations. They are met as described.

### 1. Notice that the libraries are used (LGPLv3 §4a)

Stated in this file, in the application's **Help → バージョン情報** dialog, and
in the README.

### 2. A copy of the GPL and LGPL license texts (LGPLv3 §4b)

Distributed alongside the application. Links are given above; the binary
distribution includes this file.

### 3. Copyright notices displayed at run time (LGPLv3 §4c)

The **Help → バージョン情報** dialog credits the LGPL components and points to
this document.

### 4. The user must be able to relink against a modified library (LGPLv3 §4d)

**This is the constraint that packaging changes can break.** §4d offers two
alternative routes; this project currently takes the second.

- **§4d(0)** -- convey the application's own code in a form that lets the user
  recombine it with a modified build of the library. Publishing the full source
  of this application satisfies it, because anyone can then rebuild against a
  modified Qt.
- **§4d(1)** -- use a shared-library mechanism, so a modified,
  interface-compatible build of the library can be dropped in without rebuilding
  the application.

PaintMaskAnimator ships as a PyInstaller **one-folder** build
(`packaging/PaintMaskAnimator.spec` uses `COLLECT`, not a one-file bundle). The
PySide6 extension modules and the Qt shared libraries are laid down as ordinary,
separate files inside the application directory, so a user may replace them with
an interface-compatible modified build. That satisfies §4d(1) **on its own**,
independently of whether the application's source is published.

Note that this concerns the *runtime* layout only. Shipping a single downloadable
installer (as `packaging/installer.iss` does) is unaffected: what matters is the
form the application takes once installed.

The following changes would forfeit the §4d(1) route, and are therefore only
permissible while the complete source of this application remains publicly
available -- which is what would then satisfy §4d(0) instead:

- Switching to a PyInstaller **one-file** build, which unpacks to a temporary
  directory on each launch, so a replaced library would not persist.
- Statically linking Qt.

These changes are **not permissible under either route** and must not be made:

- Adding integrity checks, signature verification, or anti-tampering measures
  that would refuse to run with modified Qt libraries.
- Distributing through a channel that forbids modification of the installed
  application (for example the Mac App Store). See "App stores" below.

### 5. No prohibition on reverse engineering (LGPLv3 §4)

The LGPL forbids restricting reverse engineering undertaken to debug
modifications to the library. PaintMaskAnimator is distributed under the
Apache License 2.0, which imposes no such restriction, so this is satisfied.
**If an end-user licence agreement is ever added — for a store build, say — it
must not contain a blanket reverse-engineering prohibition.**

### Keeping the source references current

The "Corresponding source" links above must point to the *same versions* that
are actually bundled. When `PySide6` or `PySide6-QtAds` is upgraded in
`pyproject.toml`, check that the versions are still obtainable at those URLs.

### App stores

Store terms that prohibit modifying or redistributing the installed
application conflict with the relinking requirement in §4d. Distributing this
application through the Mac App Store would therefore require a commercial Qt
licence rather than the LGPL one. This is a property of Qt, not of
PaintMaskAnimator's own licence, and applies regardless of what that licence
is.
