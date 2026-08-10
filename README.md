# PaintMaskAnimator

Paint / mask animation tool built with PySide6.

## Requirements

- Python 3.10+
- PySide6, PySide6-QtAds, numpy (required); Pillow, psd-tools (optional, enable extra import/export features)

```bash
pip install -r requirements.txt
```

## Run

```bash
python PaintMaskAnimator.py
```

or as a module:

```bash
python -m paintmaskanimator
```

## Development

```bash
pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen pytest -q
```

Tests run headless (offscreen Qt). CI runs them on every push.

## Project layout

The application is a Python package under [`paintmaskanimator/`](paintmaskanimator/):

| Module | Responsibility |
| --- | --- |
| `document.py` | Project state model (frames / layers / cursor, snapshot) |
| `imaging.py`, `geometry.py`, `colors.py`, `color_ops.py` | Pure, Qt-independent algorithms |
| `project_io.py` | Project (`.zip`) save / load serialization |
| `canvas.py`, `timeline.py`, `toolpanel.py`, `main_window.py`, … | UI layer (PySide6 widgets) |

`PaintMaskAnimator.py` at the repo root is a thin launcher.

## Python actions

The Action panel can load custom buttons from Python files. Click
`フォルダを開く` in the panel, copy a `*.py` file into that folder, and click
`再読み込み`. Each file must expose:

```python
def register_actions(panel, window):
    panel.add_action(
        "my.unique.action",
        "My Action",
        lambda: window.statusBar().showMessage("Done", 3000),
        tooltip="Optional help text",
    )
```

Checkable actions can use `checkable=True`; their callback receives the checked
state. See [`examples/actions/hello_status.py`](examples/actions/hello_status.py).
Action scripts are regular Python code and should only be installed from sources
you trust.
