"""Shared third-party imports and constants (re-exported).

Every module does ``from .common import *`` to get numpy, the Qt names,
PIL/psd_tools, the stdlib modules, and the application constants.
"""

from .constants import *  # noqa: F401,F403

import csv, json, math, re, shutil, subprocess, sys, tempfile, time, zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import List, Optional

try:
    from PIL import Image as PILImage, ImageFilter as PILImageFilter
except Exception:
    PILImage = None
    PILImageFilter = None

try:
    from psd_tools import PSDImage
except Exception:
    PSDImage = None

try:
    import numpy as np
    from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
    from PySide6.QtGui import QAction, QColor, QImage, QImageReader, QKeySequence, QPainter, QPainterPath, QPen, QPolygonF, QRegion, QTransform, QPixmap, QCursor, QValidator
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QColorDialog, QDialog, QDialogButtonBox,
        QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
        QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
        QMenu, QPushButton, QSlider, QSpinBox, QTableWidget, QComboBox,
        QTableWidgetItem, QTabBar, QTabWidget, QToolButton, QVBoxLayout, QWidget, QAbstractItemView, QHeaderView, QScrollArea,
        QKeySequenceEdit, QDockWidget, QProgressDialog, QPlainTextEdit, QStyledItemDelegate, QStyle,
        QSizePolicy, QAbstractSpinBox,
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
        pass
    raise SystemExit(1) from None
