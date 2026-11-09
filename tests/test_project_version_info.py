"""App provenance and early format compatibility checks."""
import json
import zipfile

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from paintmaskanimator import config, constants, i18n, project_io
from paintmaskanimator.errors import OperationError
from paintmaskanimator.main_window import MainWindow
from paintmaskanimator.main_window_autosave import AutosaveController


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(AutosaveController, "start", lambda self: None)
    monkeypatch.setattr(AutosaveController, "maybe_restore", lambda self: None)
    i18n.install(app, i18n.DEFAULT_LANGUAGE)
    widget = MainWindow()
    monkeypatch.setattr(widget.project, "confirm_save_before_close", lambda: True)
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()
    app.processEvents()


def metadata(path):
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("project.json"))


def rewrite(path, **changes):
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    data = json.loads(entries["project.json"])
    for key, value in changes.items():
        if value is ...:
            data.pop(key, None)
        else:
            data[key] = value
    entries["project.json"] = json.dumps(data).encode()
    with zipfile.ZipFile(path, "w") as archive:
        for name, blob in entries.items():
            archive.writestr(name, blob)


def test_new_project_save(window, tmp_path):
    path = tmp_path / "new.pman"
    assert window.project.write(path)
    data = metadata(path)
    assert data["created_app_version"] == constants.APP_VERSION
    assert data["saved_app_version"] == constants.APP_VERSION
    assert data["format_version"] == 1
    assert "application_version" not in data


@pytest.mark.parametrize("created, expected", [("0.1.0", "0.1.0"), (..., None), (123, None)])
def test_open_and_save_as_preserves_creation(window, tmp_path, created, expected):
    path = tmp_path / "old.pman"
    assert window.project.write(path)
    rewrite(path, created_app_version=created, saved_app_version="0.1.0")
    assert window.project.open(path)
    output = tmp_path / "copy.pman"
    assert window.project.write(output)
    assert metadata(output)["created_app_version"] == expected
    assert metadata(output)["saved_app_version"] == constants.APP_VERSION
    window.replace_doc(32, 32)
    assert window.created_app_version == constants.APP_VERSION


@pytest.mark.parametrize("saved", ["9.0.0", ..., 123])
def test_future_format_rejected_before_images(window, tmp_path, monkeypatch, saved):
    path = tmp_path / "future.pman"
    assert window.project.write(path)
    rewrite(path, format_version=2, saved_app_version=saved)
    original = zipfile.ZipFile.read

    def guarded_read(archive, name, *args, **kwargs):
        assert name == "project.json", "must reject before reading images"
        return original(archive, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "read", guarded_read)
    with pytest.raises(OperationError) as caught:
        project_io.read_project_archive(path)
    if isinstance(saved, str):
        expected = f"このファイルは新しいバージョンのアプリ（v{saved}）で保存されています。アプリを更新してから開いてください。"
    else:
        expected = "このファイルは新しいバージョンのアプリで保存されています。アプリを更新してから開いてください。"
    assert str(caught.value) == expected


@pytest.mark.parametrize("version", [..., 1])
def test_supported_format(window, tmp_path, version):
    path = tmp_path / "supported.pman"
    assert window.project.write(path)
    rewrite(path, format_version=version, saved_app_version=123)
    data, frames, _, _ = project_io.read_project_archive(path)
    assert frames
    assert data["saved_app_version"] is None


@pytest.mark.parametrize("version", ["1", 1.0, True, None])
def test_non_integer_format(window, tmp_path, version):
    path = tmp_path / "invalid.pman"
    assert window.project.write(path)
    rewrite(path, format_version=version)
    with pytest.raises(OperationError, match="対応していないプロジェクト形式です。"):
        project_io.read_project_archive(path)


@pytest.mark.parametrize("created", ["0.1.0", None])
def test_autosave_restore(window, tmp_path, monkeypatch, created):
    window.created_app_version = created
    window.autosave.save()
    window.created_app_version = constants.APP_VERSION
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    # The fixture suppresses the startup callback; call the real recovery flow.
    _restore(window.autosave)
    assert window.created_app_version == created
    assert window.project.write(tmp_path / "restored.pman")
    assert metadata(tmp_path / "restored.pman")["created_app_version"] == created


_restore = AutosaveController.maybe_restore
