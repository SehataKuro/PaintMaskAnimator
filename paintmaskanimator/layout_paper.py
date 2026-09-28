"""Layout-paper images registered in Preferences and offered by File › New.

A registered image is copied into the config directory, so it keeps working
after the original is moved or deleted. ``File › New…`` can put the chosen
one into the new document as the bottom draft layer, and size the canvas to it.
"""
import shutil
import uuid
from pathlib import Path

from PySide6.QtGui import QImage, QImageReader

from . import config
from .i18n import tr

PAPERS_KEY = "layout_papers"
LAST_USED_KEY = "layout_paper_last"
SUFFIXES = (".png", ".jpg", ".jpeg", ".tga")


def papers_dir():
    path = config.config_dir() / "layout_papers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _entries():
    return [
        e for e in (config.get_value(PAPERS_KEY, []) or [])
        if isinstance(e, dict) and e.get("id") and e.get("file")
    ]


def papers():
    """Registered papers whose image file still exists: ``[{id, name, file}]``."""
    return [e for e in _entries() if image_path(e).is_file()]


def paper(paper_id):
    return next((e for e in papers() if e["id"] == paper_id), None)


def image_path(entry):
    return papers_dir() / Path(str(entry["file"])).name


def image_size(entry):
    """``(width, height)`` of a paper's image, or ``None`` if unreadable."""
    reader = QImageReader(str(image_path(entry)))
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid():
        # Some formats only report their size once decoded.
        image = load_image(entry)
        return None if image is None else (image.width(), image.height())
    return size.width(), size.height()


def load_image(entry):
    reader = QImageReader(str(image_path(entry)))
    reader.setAutoTransform(True)
    image = reader.read()
    return None if image.isNull() else image.convertToFormat(
        QImage.Format.Format_ARGB32_Premultiplied
    )


def add_paper(source, name=None):
    """Copy ``source`` into the paper store; return the new entry or ``None``."""
    source = Path(source)
    if source.suffix.lower() not in SUFFIXES or QImageReader(str(source)).read().isNull():
        return None
    paper_id = uuid.uuid4().hex
    target = papers_dir() / f"{paper_id}{source.suffix.lower()}"
    shutil.copyfile(source, target)
    entry = {"id": paper_id, "name": str(name or source.stem), "file": target.name}
    config.set_value(PAPERS_KEY, _entries() + [entry])
    return entry


def rename_paper(paper_id, name):
    name = str(name).strip()
    if not name:
        return
    entries = _entries()
    for entry in entries:
        if entry["id"] == paper_id:
            entry["name"] = name
    config.set_value(PAPERS_KEY, entries)


def remove_paper(paper_id):
    entries = _entries()
    for entry in entries:
        if entry["id"] == paper_id:
            image_path(entry).unlink(missing_ok=True)
    config.set_value(PAPERS_KEY, [e for e in entries if e["id"] != paper_id])


def last_used():
    """The paper chosen in the previous File › New, if it still exists."""
    paper_id = config.get_value(LAST_USED_KEY)
    return paper_id if paper(paper_id) is not None else None


def set_last_used(paper_id):
    config.set_value(LAST_USED_KEY, paper_id)


def layer_name():
    return tr("レイアウト用紙")
