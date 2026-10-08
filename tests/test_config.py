"""Config loading (spec §8)."""
from __future__ import annotations

from pathlib import Path

from hallofframe.config import Config


def test_data_root_expands_tilde_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REGA_HOME", str(tmp_path))
    cfg = Config(data={"paths": {"data_root": "$REGA_HOME/data"}},
                 path=tmp_path / "config.toml")
    assert cfg.data_root == (tmp_path / "data").resolve()


def test_data_root_expands_tilde(monkeypatch):
    cfg = Config(data={"paths": {"data_root": "~/rega"}},
                 path=Path("/tmp/config.toml"))
    assert str(cfg.data_root).endswith("/rega")
