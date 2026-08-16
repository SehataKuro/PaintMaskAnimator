"""Shared third-party imports and constants (re-exported).

Every module does ``from .common import *`` to get numpy, the Qt names,
PIL/psd_tools, the stdlib modules, and the application constants.
"""

from .constants import *  # noqa: F401,F403

import csv
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, List, Optional

try:
    from PIL import Image as PILImage, ImageFilter as PILImageFilter
except (ImportError, OSError):
    # Pillow is optional; a missing or broken install disables PIL-backed paths.
    PILImage = None
    PILImageFilter = None

try:
    from psd_tools import PSDImage  # pyright: ignore[reportMissingImports]
except (ImportError, OSError):
    # psd_tools is optional; absence disables .psd import only.
    PSDImage = None

try:
    import numpy as np
    from PySide6.QtCore import QByteArray, QEvent, QItemSelectionModel, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
    from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QImage, QImageReader, QKeySequence, QPainter, QPainterPath, QPen, QPolygonF, QRegion, QTransform, QPixmap, QCursor, QValidator
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QColorDialog, QDialog, QDialogButtonBox,
        QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
        QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
        QMenu, QPushButton, QSlider, QSpinBox, QTableWidget, QComboBox,
        QTableWidgetItem, QTabBar, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QAbstractItemView, QHeaderView, QScrollArea,
        QKeySequenceEdit, QDockWidget, QProgressDialog, QPlainTextEdit, QStyledItemDelegate, QStyle,
        QSizePolicy, QAbstractButton, QAbstractSpinBox, QInputDialog, QSplitter,
    )
except ModuleNotFoundError as exc:
    # Double-clicking a .py normally closes the console immediately. Show a visible
    # explanation instead, and also print a copy-pasteable installation command.
    missing = exc.name or "必要なライブラリ"
    message = (
        f"{missing} がインストールされていないため起動できません。\n\n"
        "Windowsのコマンドプロンプトで次を実行してください。\n\n"
        "py -m pip install PySide6 numpy\n\n"
        "インストール後、このファイルをもう一度起動してください。"
    )
    print(message)
    try:
        import tkinter as _tk
        from tkinter import messagebox as _messagebox
        _root = _tk.Tk(); _root.withdraw()
        _messagebox.showerror(f"{APP_DISPLAY_NAME} 起動エラー", message)
        _root.destroy()
    except Exception:
        # tkinter itself may be missing or unusable (no display); the console
        # message above is the fallback, so ignore any GUI-dialog failure.
        pass
    raise SystemExit(1) from None
