# Sheet 24 — audio: device/mixer enumeration, level meter, machine-written `settings.json`

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/audio.py` (append), `hallofframe/config.py`, `tests/test_audio.py` (append), `tests/test_config.py` (append).
**Read first:** `hallofframe/audio.py` (`Source`, `ArecordSource`, `BLOCK_BYTES`, `apply_mixer`, `mixer_argv`, `parse_mixer_entry`, `envelope`), `hallofframe/config.py` (all), `tests/test_audio.py` (`FakeSource`, `_wait_for`, `test_apply_mixer_collects_failures`), `tests/test_config.py` (all), `tests/conftest.py` (`config` fixture).

### Goal
Everything the audio tab (sheet 25) needs that is not a widget: list ALSA PCMs, read/write mixer controls, a background mic-level reader, and a place to **persist** the operator's choices. `config.toml` stays hand-edited and is never written by the app; the choices go to a machine-written `<data_root>/settings.json` whose values override `config.toml` per section/key at load time.

### Spec

`audio.py` (append):
```python
def parse_pcm_list(text: str) -> list[tuple[str, str]]:
    """Parse `arecord -L`/`aplay -L`: unindented line = PCM name, following indented lines = description (joined with ' · '). Skips 'null'."""

def list_pcms(kind: str, *, runner=subprocess.run) -> list[tuple[str, str]]:
    """kind 'input' → arecord -L, 'output' → aplay -L. Never raises; [] on OSError/timeout/non-zero exit."""
```
`runner([binary, "-L"], capture_output=True, timeout=5.0, text=True)`; `kind` not in `("input", "output")` → `ValueError`. Always prepend `("default", "System default")` if the parsed list has no `default` entry.

```python
def parse_mixer_contents(text: str) -> list[dict]:
    """Parse `amixer scontents` blocks → [{'name','capture','playback','percent','switch'}]."""
def mixer_controls(card: str, *, runner=subprocess.run, binary: str = "amixer") -> list[dict]:
    """amixer [-c card] scontents; never raises; [] on failure."""
def set_mixer(card: str, control: str, value: str, *, runner=subprocess.run,
              binary: str = "amixer") -> str | None:
    """One amixer sset; None on success, else the failure message (apply_mixer semantics)."""
```
Block parse: a block starts at `Simple mixer control '<name>',<n>`; `capture = "cvolume" in Capabilities line`, `playback = "pvolume" in …`; `percent` = int of the first `[NN%]` in the block (None if none); `switch` = True/False from the first `[on]`/`[off]` (None if none). Keep only blocks with `capture or playback`. `set_mixer` = `apply_mixer(card, [f"{control} {value}"], runner=runner, binary=binary)` → first message or None.

```python
class LevelMeter:
    """Reads a Source on a daemon thread and reports RMS per block; never raises into the GUI."""
    def __init__(self, source_factory, on_level, *, on_error=None, on_block=None)
    def start(self) -> None     # opens the source on the thread; open failure → on_error(str)
    def stop(self, timeout: float = 1.0) -> None   # close source, join
    running: bool
```
Per block: `rms = sqrt(mean(x²))` over int16 (reuse the maths from `envelope`); call `on_level(rms)`; if `on_block` is set call `on_block(bytes)` too (sheet 25 uses it to capture a 3 s loopback). `b""` from `read()` ends the thread. `stop()` is idempotent and safe before `start()`.

`config.py`:
```python
SETTINGS_FILENAME = "settings.json"   # machine-written overrides: {"<section>": {"<key>": value}}

@dataclass
class Config:
    data: dict[str, Any]
    path: Path
    overrides: dict[str, Any] = field(default_factory=dict)

    @property
    def settings_path(self) -> Path: return self.data_root / SETTINGS_FILENAME
    def load_overrides(self) -> None
    def write_overrides(self, section: str, values: dict[str, Any]) -> Path
```
- `section()`: after `merged.update(user)` add `merged.update(self.overrides.get(name, {}))` (before the `{event_name}` pass).
- `load_overrides`: read `settings_path`; missing, unreadable, non-object JSON, or a section value that is not an object → that part ignored; result stored in `self.overrides` (always a dict).
- `write_overrides`: `self.overrides.setdefault(section, {}).update(values)`; `mkdir(parents=True, exist_ok=True)`; write the whole `self.overrides` as JSON (`indent=2`, sorted keys, trailing newline); return the path. `section` must be a key of `DEFAULTS`, else `ValueError`.
- `load_config`: call `cfg.load_overrides()` before returning. comment: `settings.json (calibration dialog output) overrides config.toml; the app never writes config.toml`.
- Module docstring: add one sentence saying the same.

### Tests

`tests/test_audio.py` (append; fixture strings below verbatim):
```python
PCM_TEXT = """null
    Discard all samples (playback) or generate zero samples (capture)
default
    Default ALSA Output (currently PipeWire Media Server)
sysdefault:CARD=PCH
    HDA Intel PCH, ALC293 Analog
    Default Audio Device
plughw:CARD=Headset,DEV=0
    Logitech USB Headset, USB Audio
    Hardware device with all software conversions
"""
MIXER_TEXT = """Simple mixer control 'Master',0
  Capabilities: pvolume pswitch
  Playback channels: Mono
  Limits: Playback 0 - 87
  Mono: Playback 87 [100%] [0.00dB] [on]
Simple mixer control 'Capture',0
  Capabilities: cvolume cswitch
  Capture channels: Front Left - Front Right
  Limits: Capture 0 - 63
  Front Left: Capture 38 [60%] [12.00dB] [on]
  Front Right: Capture 38 [60%] [12.00dB] [on]
Simple mixer control 'Mic Boost',0
  Capabilities: volume
  Playback channels: Front Left - Front Right
  Capture channels: Front Left - Front Right
  Limits: 0 - 3
  Front Left: 0 [0%]
  Front Right: 0 [0%]
Simple mixer control 'Auto-Mute Mode',0
  Capabilities: enum
  Items: 'Disabled' 'Enabled'
  Item0: 'Enabled'
"""
```
| Test | Steps | Expected |
|---|---|---|
| `test_parse_pcm_list` | `parse_pcm_list(PCM_TEXT)` | `[("default", "Default ALSA Output (currently PipeWire Media Server)"), ("sysdefault:CARD=PCH", "HDA Intel PCH, ALC293 Analog · Default Audio Device"), ("plughw:CARD=Headset,DEV=0", "Logitech USB Headset, USB Audio · Hardware device with all software conversions")]` |
| `test_list_pcms_runner` | runner records argv, returns `SimpleNamespace(returncode=0, stdout=PCM_TEXT)`; `list_pcms("input", runner=r)`; `list_pcms("output", runner=r)` | argvs `[["arecord", "-L"], ["aplay", "-L"]]`; 3 entries each; `list_pcms("x")` → `ValueError` |
| `test_list_pcms_failure_is_empty_default` | runner raises `OSError`; and runner returns `returncode=1, stdout=""` | both `== [("default", "System default")]` |
| `test_parse_mixer_contents` | `parse_mixer_contents(MIXER_TEXT)` | 3 entries (the `Auto-Mute Mode` enum block is dropped): `[{"name": "Master", "capture": False, "playback": True, "percent": 100, "switch": True}, {"name": "Capture", "capture": True, "playback": False, "percent": 60, "switch": True}, {"name": "Mic Boost", "capture": False, "playback": False, "percent": 0, "switch": None}]` — rule: keep a block when its Capabilities contain any of `cvolume`, `pvolume`, `volume` |
| `test_mixer_controls_runner` | runner records; returns `returncode=0, stdout=MIXER_TEXT`; `mixer_controls("1", runner=r)` | argv `== ["amixer", "-c", "1", "scontents"]`; 3 entries; `mixer_controls("", runner=r)` argv `== ["amixer", "scontents"]` |
| `test_set_mixer` | runner returns rc 0 → `set_mixer("0", "Capture", "60%", runner=r) is None`; rc 1 with stderr `b"amixer: Unable to find simple control 'Nope',0\n"` → message `== "Nope 50%: amixer: Unable to find simple control 'Nope',0"` | as listed; argv of the first call `== ["amixer", "-q", "-c", "0", "sset", "Capture", "60%"]` |
| `test_level_meter_reports_rms` | `FakeSource` (existing) yielding 3 blocks of `b"\x10\x27"*1600` (sample 10000) then `b""`; `levels = []`; `blocks = []`; `m = LevelMeter(lambda: src, levels.append, on_block=blocks.append)`; `m.start()`; `_wait_for(lambda: not m.running)` | `levels == [pytest.approx(10000.0)]*3`; `len(blocks) == 3`; `m.stop()` twice is fine |
| `test_level_meter_open_error` | `FailingSource` (existing); `errs = []`; `LevelMeter(lambda: FailingSource(), lambda r: None, on_error=errs.append).start()`; wait | `len(errs) == 1`; `m.running is False` |

`tests/test_config.py` (append):
| Test | Steps | Expected |
|---|---|---|
| `test_section_merges_overrides` | `cfg = config(voice={"input": "plughw:1,0"})`; `cfg.overrides = {"voice": {"input": "hw:2,0", "gain_db": 6.0}}` | `s = cfg.section("voice")`; `s["input"] == "hw:2,0"`; `s["gain_db"] == 6.0`; `s["output"] == "default"` |
| `test_write_then_load_overrides` | `p = cfg.write_overrides("voice", {"input": "hw:2,0", "mixer": ["Capture 60%"]})`; `cfg.write_overrides("voice", {"gain_db": 3.0})`; `fresh = Config(data={}, path=p.parent / "config.toml"); fresh.data["paths"] = {"data_root": str(p.parent)}; fresh.load_overrides()` | `p == cfg.data_root / "settings.json"`; file JSON `== {"voice": {"gain_db": 3.0, "input": "hw:2,0", "mixer": ["Capture 60%"]}}`; `fresh.section("voice")["input"] == "hw:2,0"` |
| `test_load_overrides_tolerates_garbage` | write `settings.json` as `"{nope"`; then as `[1]`; then as `{"voice": 5, "web": {"port": 9}}` | first two → `overrides == {}`; third → `overrides == {"web": {"port": 9}}` and `section("web")["port"] == 9` |
| `test_write_overrides_unknown_section` | `cfg.write_overrides("nope", {})` | `pytest.raises(ValueError)` |
| `test_load_config_reads_settings` | write a minimal `config.toml` (`[paths]\ndata_root = "<tmp>"`) and `settings.json` `{"voice": {"input": "hw:9,0"}}` in the same tmp dir; `load_config(path)` | `section("voice")["input"] == "hw:9,0"` |

**Commit:** `feat(audio): PCM/mixer enumeration, LevelMeter, settings.json overrides (step 24)`
