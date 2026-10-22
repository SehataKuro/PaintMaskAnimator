"""設定ファイルの読み込みキャッシュ（描画のたびに読まれるので覚えておく）。"""
import json
import os

from paintmaskanimator import config


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def test_set_value_is_visible_to_the_next_read(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert config.get_value("theme") is None
    config.set_value("theme", "dark")
    assert config.get_value("theme") == "dark"
    config.set_value("theme", None)
    assert config.get_value("theme") is None


def test_external_edit_is_picked_up(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    config.set_value("theme", "dark")
    assert config.get_value("theme") == "dark"
    path = config.config_path()
    path.write_text(json.dumps({"theme": "light", "extra": 1}), encoding="utf-8")
    # 同じ時刻・同じ大きさの書き換えでも読み直すよう、更新時刻をずらす。
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    # 更新の確認は一定時間に1回なので、その間は前の値のまま。
    clock = [config._now()]
    monkeypatch.setattr(config, "_now", lambda: clock[0])
    assert config.get_value("theme") == "dark"
    clock[0] += config._CHECK_INTERVAL + 0.01
    assert config.get_value("theme") == "light"


def test_returned_values_do_not_leak_into_the_cache(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    config.set_value("layout", {"docks": ["a"]})
    config.get_value("layout")["docks"].append("b")
    config.load_config()["layout"]["docks"].append("c")
    assert config.get_value("layout") == {"docks": ["a"]}


def test_each_config_home_is_separate(tmp_path, monkeypatch):
    _isolate(tmp_path / "one", monkeypatch)
    config.set_value("theme", "dark")
    _isolate(tmp_path / "two", monkeypatch)
    assert config.get_value("theme") is None
    assert config.config_dir().is_dir()
