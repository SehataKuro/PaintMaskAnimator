# PaintMaskAnimator

English | [日本語](README.md)

A paint and mask animation tool built with PySide6.

## Related Links

- [Download page](https://jokomanato.com/paintmaskanimator/downloads/)
- [Feature verification map (automatically updated from test results for each commit)](https://jokomanato.com/paintmaskanimator/)

Basic authentication is required to view these pages.

- Username: `guest`
- Password: `6eCKEq`

## Project Background and Development Philosophy

This repository is a fork developed from PaintMaskAnimator v0.5, which was handed over by
[Keisuke Kojima](https://x.com/kkeisuke220). Keisuke Kojima is the author of the original
version; the current maintainer of this repository did not create it from scratch.

The original version was developed with the following goals in mind:

- An animation finishing application capable of replacing PaintMan
- Drawing capabilities suitable for in-between animation work
- A timeline with controls similar to CLIP STUDIO PAINT

This fork continues development while carrying forward that direction.

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

## Development

```bash
pip install -r requirements-dev.txt
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
