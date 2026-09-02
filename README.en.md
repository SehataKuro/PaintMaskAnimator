# PaintMaskAnimator

English | [日本語](README.md)

A paint and mask animation tool built with PySide6.
Short name: **PMAn** (pronounced "piman").

## Related Links

- [Download page](https://jokomanato.com/paintmaskanimator/downloads/)
- [Feature verification map (automatically updated from test results for each commit)](https://jokomanato.com/paintmaskanimator/)

Basic authentication is required to view these pages.

- Username: `guest`
- Password: `6eCKEq`

## Project Background and Development Philosophy

PaintMaskAnimator was written by [Keisuke Kojima](https://x.com/kkeisuke220), who developed
it single-handedly through v0.5. From v0.5 onward it has been developed jointly by Keisuke
Kojima and Manato Joko.

The goals have stayed the same throughout:

- An animation finishing application capable of replacing PaintMan
- Drawing capabilities suitable for in-between animation work
- A timeline with controls similar to CLIP STUDIO PAINT

Development continues along that direction today.

## Requirements

- Python 3.10 or later
- PySide6, PySide6-QtAds, and numpy (required); Pillow and psd-tools (optional, enabling additional import and export features)

```bash
pip install -r requirements.txt
```

## Running the Application

```bash
python PaintMaskAnimator.py
```

Alternatively, run it as a module:

```bash
python -m paintmaskanimator
```

## Language

The interface is available in Japanese (the source language) and English.
Choose it under **View › Language**; by default the application follows the
operating system locale. The change takes effect on the next start.

Translations live in `paintmaskanimator/translations/` as Qt `.ts` catalogues.
After adding or changing a `tr()` string, regenerate them with
`python scripts/update_translations.py` — CI verifies they are current.

## Development

```bash
pip install -r requirements-dev.txt
python scripts/update_translations.py
QT_QPA_PLATFORM=offscreen python scripts/run_tests.py -q
```

Tests run headlessly using the offscreen Qt platform plugin. CI runs on every push.

## Project Structure

The application is organized as a Python package under [`paintmaskanimator/`](paintmaskanimator/):

| Module | Purpose |
| --- | --- |
| `document.py` | Project state model (frames, layers, cursor, and snapshots) |
| `imaging.py`, `geometry.py`, `colors.py`, `color_ops.py` | Pure algorithms with no Qt dependency |
| `project_io.py` | Serialization for saving and loading project (`.zip`) files |
| `canvas.py`, `timeline.py`, `toolpanel.py`, `main_window.py`, etc. | UI layer (PySide6 widgets) |
| `main_window_<topic>.py` | Per-feature controllers the window owns (`window.export`, …) |
| `progress.py` | Shared progress counter for long operations |
| `i18n.py` | Translation loading and `tr()` |
| `errors.py` | Exception types, and what the user-action handlers catch |
| `dependency_check.py`, `optional_deps.py` | Start-up check for required deps; fallbacks for optional ones |

`PaintMaskAnimator.py` at the repository root is a lightweight launcher.

## Python Actions

The Actions panel supports custom buttons defined in Python scripts. Select
**☰ → Edit Scripts** from the leftmost tab in the Actions panel to open the built-in editor,
where you can create, edit, and reload scripts. Each script must expose the following function:

```python
def register_actions(panel, window):
    panel.add_action(
        "my.unique.action",
        "My Action",
        lambda: window.status_bar.showMessage("Done", 3000),
        tooltip="Optional help text",
    )
```

See [`docs/actions.md`](docs/actions.md) for usage instructions, the API reference, a list of
built-in actions, and troubleshooting information. An example is available at
[`examples/actions/hello_status.py`](examples/actions/hello_status.py). Action scripts are regular
Python code, so only install them from sources you trust.

## License

PaintMaskAnimator is licensed under the **Apache License 2.0**. See
[`LICENSE`](LICENSE) for the full text and [`NOTICE`](NOTICE) for attribution.

```
Copyright (c) 2026 PaintMaskAnimator contributors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
```

You may use, modify and redistribute it freely, commercially or otherwise,
including as part of a closed-source product. All that is required is that you
retain the license and `NOTICE`, and state your changes (section 4).

Artwork you create with this software (images, animations, project files) is
yours; the license does not extend to your output.

### Trademarks

The Apache License 2.0 grants no trademark rights (section 6). Please do not
use the name "PaintMaskAnimator", or the short name "PMAn", for derivative works.

### Third-party licenses

This application uses and bundles PySide6 (LGPLv3), Qt Advanced Docking System
(LGPL-2.1) and others. **Those LGPL obligations fall on this project.** Before
changing how the application is packaged, read the "LGPL compliance" section of
[`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).

### Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Contributions are taken to be offered
under the Apache License 2.0 (section 5); no separate CLA is required.
