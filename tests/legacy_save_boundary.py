"""Explicit v1 save boundary for frozen pre-v2 characterization only."""
from paintmaskanimator import project_io


def install_legacy_save_boundary(monkeypatch):
    """Run old UI orchestration against the real legacy fixture writer."""
    monkeypatch.setattr(project_io, "write_project_snapshot",
                        project_io.legacy_fixture_write_snapshot)
    from paintmaskanimator.main_window import MainWindow
    build = MainWindow.build_project_metadata

    def legacy_metadata(window):
        return build(window) | {"format_version": 1}

    monkeypatch.setattr(MainWindow, "build_project_metadata", legacy_metadata)
