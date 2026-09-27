<p align="center">
  <img src="docs/assets/logo.png" alt="PaintMaskAnimator (PMA)" width="180">
</p>

# PaintMaskAnimator

English | [日本語](README.md)

**[Website](https://sehatakuro.github.io/PaintMaskAnimator/)** (Japanese) — downloads, release notes, past versions and help

[![CI](https://github.com/SehataKuro/PaintMaskAnimator/actions/workflows/ci.yml/badge.svg)](https://github.com/SehataKuro/PaintMaskAnimator/actions/workflows/ci.yml)
[![Release](https://img.shields.io/badge/release-v0.6.5-blue)](https://github.com/SehataKuro/PaintMaskAnimator/releases)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/PySide6-6.5%2B-41CD52?logo=qt&logoColor=white)](https://doc.qt.io/qtforpython/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey)](https://github.com/SehataKuro/PaintMaskAnimator/releases)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

A paint and mask animation tool for animation finishing (colouring).
Short name: **PMAn** (pronounced "piman").

## Download

Get the latest version from
[**Releases**](https://github.com/SehataKuro/PaintMaskAnimator/releases).

| OS | File | Supported |
| --- | --- | --- |
| Windows | `PaintMaskAnimator-Setup-<version>.exe` | 64-bit Windows |
| macOS | `PaintMaskAnimator-<version>-macOS.dmg` | Apple Silicon (M1 or later). Intel Macs are not supported |

Once installed, the app's update check fetches new versions. Version 0.6.4 and
earlier cannot receive in-app updates; reinstall once from Releases.

### If macOS refuses to open it

The app is not notarized by Apple, so macOS blocks the first launch.

1. Drag the app into Applications, open it once and dismiss the warning.
2. In **System Settings › Privacy & Security**, click **Open Anyway** next to
   "PaintMaskAnimator was blocked…".

Or clear the quarantine flag from Terminal:

```bash
xattr -dr com.apple.quarantine /Applications/PaintMaskAnimator.app
```

## Features

- Paint and mask animation with frames and layers, a timeline, onion skin and
  tweening
- Used-colour panel: organise colours with parent/child links, tags and folders;
  replace, merge and reduce colours
- Colour charts (`.pmag`) that record parent/child colour relationships and
  apply them to the current image
- Import: images and sequences, PSD, CLIP STUDIO animations (`.clip`), time
  sheets (XDTS / TDTS), pasted time remaps
- Export: PNG / TGA sequences + CSV, PSD, MP4, XDTS, cut folders (cell images
  and the time sheet in one folder structure)
- Autosave with crash recovery, customisable panel layout, light / dark themes
- Japanese and English UI (**View › 言語 / Language**; follows the OS by default)
- Custom actions written in Python (see below)

The project file format (`.pman`) is documented in [`FORMAT.md`](FORMAT.md)
(Japanese).

## Running from source

Requires Python 3.10 or later.

```bash
pip install -r requirements.txt
python PaintMaskAnimator.py        # or: python -m paintmaskanimator
```

PySide6, PySide6-QtAds and numpy are required. Installing Pillow and psd-tools
enables PSD import/export and other optional features.

## Python actions

The action panel can be extended with Python scripts. Open the in-app editor from
**☰ › Edit the script** at the left end of the panel's tab.

```python
def register_actions(panel, window):
    panel.add_action(
        "my.unique.action",
        "My Action",
        lambda: window.status_bar.showMessage("Done", 3000),
        tooltip="Optional help text",
    )
```

See [`docs/actions.md`](docs/actions.md) for the API reference and the built-in
actions, and [`examples/actions/hello_status.py`](examples/actions/hello_status.py)
for a sample. Actions run as regular Python code, so only install them from
sources you trust.

## Contributing

Setting up, the pre-submit checks, translations and the release procedure are
described in [`CONTRIBUTING.md`](CONTRIBUTING.md) (Japanese).

```bash
pip install -e ".[dev,full]"
python scripts/preflight.py
```

The application is the Python package under [`paintmaskanimator/`](paintmaskanimator/);
`PaintMaskAnimator.py` at the repository root is a thin launcher.

| Module | Role |
| --- | --- |
| `document.py` | Project state model (frames / layers / cursor) |
| `imaging.py`, `geometry.py`, `colors.py`, `color_ops.py` | Qt-independent algorithms |
| `project_io.py` | Saving and loading project files |
| `canvas.py`, `timeline.py`, `toolpanel.py`, `main_window.py`, … | UI (PySide6 widgets) |
| `main_window_<topic>.py` | Per-feature controllers (`window.export`, …) |
| `i18n.py` | Translation loading and `tr()` |

## Background

PaintMaskAnimator was written by [Keisuke Kojima](https://x.com/kkeisuke220), who
developed it alone through v0.5. From v0.5 onward it has been developed jointly by
Keisuke Kojima and Manato Joko.

The goals have stayed the same throughout:

- An animation finishing application capable of replacing PaintMan
- Drawing capabilities suitable for in-between animation work
- A timeline with controls similar to CLIP STUDIO PAINT

## License

Licensed under the [Apache License 2.0](LICENSE). See [`NOTICE`](NOTICE) for
attribution.

- You may use, modify and redistribute it freely, commercially or otherwise,
  including as part of a closed-source product. All that is required is that you
  retain the license and `NOTICE`, and state your changes (section 4).
- Artwork you create with this software (images, animations, project files) is
  yours; the license does not extend to your output.
- No trademark rights are granted (section 6). Please do not use the names
  "PaintMaskAnimator" or "PMAn" for derivative works.
- PySide6 (LGPLv3), Qt Advanced Docking System (LGPL-2.1) and others are bundled.
  Before changing how the application is packaged, read the "LGPL compliance"
  section of [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).
- Contributions are taken to be offered under the Apache License 2.0
  (section 5); no separate CLA is required.
