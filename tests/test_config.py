"""Config loading (spec §8)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hallofframe.config import Config, load_config


def test_data_root_expands_tilde_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REGA_HOME", str(tmp_path))
    cfg = Config(data={"paths": {"data_root": "$REGA_HOME/data"}},
                 path=tmp_path / "config.toml")
    assert cfg.data_root == (tmp_path / "data").resolve()


def test_data_root_expands_tilde(monkeypatch):
    cfg = Config(data={"paths": {"data_root": "~/rega"}},
                 path=Path("/tmp/config.toml"))
    assert str(cfg.data_root).endswith("/rega")


def test_section_merges_overrides(config):
    cfg = config(voice={"input": "plughw:1,0"})
    cfg.overrides = {"voice": {"input": "hw:2,0", "gain_db": 6.0}}
    s = cfg.section("voice")
    assert s["input"] == "hw:2,0"
    assert s["gain_db"] == 6.0
    assert s["output"] == "default"


def test_write_then_load_overrides(config):
    cfg = config()
    p = cfg.write_overrides("voice", {"input": "hw:2,0", "mixer": ["Capture 60%"]})
    cfg.write_overrides("voice", {"gain_db": 3.0})
    fresh = Config(data={}, path=p.parent / "config.toml")
    fresh.data["paths"] = {"data_root": str(p.parent)}
    fresh.load_overrides()
    assert p == cfg.data_root / "settings.json"
    assert json.loads(p.read_text()) == {
        "voice": {"gain_db": 3.0, "input": "hw:2,0", "mixer": ["Capture 60%"]}}
    assert fresh.section("voice")["input"] == "hw:2,0"


def test_load_overrides_tolerates_garbage(config):
    cfg = config()
    p = cfg.settings_path
    p.write_text("{nope")
    cfg.load_overrides()
    assert cfg.overrides == {}
    p.write_text("[1]")
    cfg.load_overrides()
    assert cfg.overrides == {}
    p.write_text('{"voice": 5, "web": {"port": 9}}')
    cfg.load_overrides()
    assert cfg.overrides == {"web": {"port": 9}}
    assert cfg.section("web")["port"] == 9


def test_write_overrides_unknown_section(config):
    with pytest.raises(ValueError):
        config().write_overrides("nope", {})


def test_load_config_reads_settings(tmp_path):
    (tmp_path / "config.toml").write_text(f'[paths]\ndata_root = "{tmp_path.as_posix()}"\n')
    (tmp_path / "settings.json").write_text('{"voice": {"input": "hw:9,0"}}')
    cfg = load_config(tmp_path / "config.toml")
    assert cfg.section("voice")["input"] == "hw:9,0"
