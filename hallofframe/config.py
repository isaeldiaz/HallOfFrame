"""Configuration load for HallOfFrame.

config.toml is HAND-EDITED and never written by the application (spec v1.2
§6.7, §8). Everything machine-produced — the latency calibration result — lives
in calibration.json; the application derives ``delta`` from that plus
``reaction_offset_ms`` out of config.toml at race start (spec §5.4, §8).
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised when configuration is missing or malformed."""


DEFAULTS: dict[str, Any] = {
    "paths": {"data_root": "~/regatta-data"},
    "transport": {
        "local_port": 8081,
        "device_port": 8081,
        "udid": "",
        "iproxy_path": "iproxy",
        "max_restarts_idle": 3,
        "max_restarts_racing": 0,  # 0 = unlimited with backoff (§6.5)
    },
    "stream": {
        # Path verified on IP Camera Lite (2026-08-22): /video, not /live.
        # App- and version-dependent per spec §8, §9.2.
        "url": "http://127.0.0.1:8081/video",
        "username": "admin",
        "password": "admin",
        "buffer_seconds": 10.0,
        "assumed_fps": 30,
        "require_content_length": True,
    },
    "timing": {
        "viewing_mode": "water",
        "reaction_offset_ms": 0.0,
        "debounce_ms": 20,
        "start_mode": "direct",
        "radio_delay_ms": 0.0,
    },
    "capture": {
        "window_before_ms": 500,
        "window_after_ms": 500,
    },
    "trigger": {
        "device_path": "/dev/input/event3",  # internal keyboard
        "crossing_keycodes": [57],  # KEY_SPACE — operator choice (see §6.4)
        "start_keycodes": [28],  # KEY_ENTER
        "end_keycodes": [88],  # KEY_F12 — finish the race (see §6.4)
        "grab_device": True,
    },
    "races": {
        "excel_path": "~/regatta-data/races.xlsx",
    },
    "archive": {
        "enabled": True,
        "every_nth_frame": 1,
        "min_free_gb": 60,
        "degrade_at_gb": 10,
        "stop_at_gb": 3,
        "ballast_gb": 3,
    },
    "ui": {
        "finish_line_x": 0.5,
        "preview_fps": 10,
    },
}


@dataclass
class Config:
    data: dict[str, Any]
    path: Path

    @property
    def data_root(self) -> Path:
        raw = self.data.get("paths", {}).get("data_root", "~/regatta-data")
        return Path(os.path.expanduser(raw)).resolve()

    def section(self, name: str) -> dict[str, Any]:
        user = self.data.get(name, {})
        merged = dict(DEFAULTS[name])
        merged.update(user)
        return merged


def _validate(raw: dict[str, Any], path: Path) -> None:
    timing = raw.get("timing", {})
    if timing.get("viewing_mode", "water") not in ("water", "screen"):
        raise ConfigError(
            f"{path}: [timing] viewing_mode must be 'water' or 'screen'")
    if timing.get("start_mode", "direct") not in ("direct", "radio", "external"):
        raise ConfigError(
            f"{path}: [timing] start_mode must be direct|radio|external")


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load config.toml. If *path* is None, look in the default data root."""
    if path is None:
        data_root = Path(os.path.expanduser("~/regatta-data"))
        path = data_root / "config.toml"
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise ConfigError(f"config file not found: {cfg_path}")
    with open(cfg_path, "rb") as fh:
        raw = tomllib.load(fh)
    _validate(raw, cfg_path)
    return Config(data=raw, path=cfg_path)
