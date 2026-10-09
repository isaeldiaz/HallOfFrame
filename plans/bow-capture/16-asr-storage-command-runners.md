# Sheet 16 — storage suggestions + command builder + runners

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/storage.py`, `hallofframe/asr.py`, `tests/fakes.py`, `tests/test_storage.py` (append), `tests/test_asr.py` (append).
**Read first:** `storage.py` (`set_setting` — the no-`_touch` precedent; `_lock`/commit pattern), `asr.py`, `tests/fakes.py`, `transport.py` (Popen pattern).

### Spec
`storage.py`:
```python
def set_bow_suggestions(self, race_id: int, by_capture_id: dict[int, str | None]) -> None:
    """One transaction: UPDATE capture SET bow_suggested=NULL WHERE race_id=?; then per item
    UPDATE capture SET bow_suggested=? WHERE id=? AND race_id=?. Deliberately NO _touch and NO updated_at:
    a suggestion is not published, so the web ETag / "Results updated" must not move (same rule as ui.* settings)."""
```
`asr.py`:
```python
PROMPT = "null en to tre fire fem seks sju åtte ni"
def build_command(whisper_cli: str, model: Path, wav: Path, out_base: Path) -> list[str]:
    # exactly: [whisper_cli, "-m", str(model), "-f", str(wav), "-l", "no", "-t", "1", "-ojf", "-of", str(out_base), "-np", "--prompt", PROMPT]
def model_path(voice: dict, data_root: Path) -> Path:
    # model contains "/" or endswith ".bin" → Path(model) (expanduser); else data_root/"models"/f"ggml-nb-whisper-{model}.bin"
@dataclass
class RunResult: json_text: str | None; error: str | None
class Runner(Protocol):
    def run(self, cmd: list[str], out_base: Path, timeout_s: float) -> RunResult: ...
    def cancel(self) -> None: ...
class SubprocessRunner:
    """run(), in order — never raises:
    1. if shutil.which(cmd[0]) is None: return RunResult(None, f"{cmd[0]} not found")
    2. prefix = ["nice", "-n", "19"] if shutil.which("nice") else []      # low priority on Linux; absent on Windows
    3. self._cancelled = False; proc = self._proc = Popen(prefix + cmd, stdout=PIPE, stderr=PIPE, text=True)
    4. try: out, err = proc.communicate(timeout=timeout_s)
       except TimeoutExpired: proc.kill(); proc.communicate(); return RunResult(None, "timed out")
       except OSError as exc: return RunResult(None, str(exc))
    5. if self._cancelled: return RunResult(None, "cancelled")
    6. if proc.returncode != 0: return RunResult(None, err[-300:].strip() or f"{cmd[0]} exited {proc.returncode}")
    7. return RunResult(out_base.with_suffix(".json").read_text(encoding="utf-8"), None)   # OSError → RunResult(None, "no whisper output")
    cancel(): self._cancelled = True; kill the live process if any (ignore errors)."""
```
`tests/fakes.py`: `from hallofframe.asr import RunResult`; `class FakeRunner: __init__(self, json_text, error=None)` sets `self.json_text, self.error, self.calls = [], self.cancelled = False`; `run(cmd, out_base, timeout_s)` appends `(cmd, out_base, timeout_s)` to `self.calls` and returns `RunResult(self.json_text, self.error)`; `cancel()` sets `self.cancelled = True`.

### Tests
`tests/test_storage.py` (append): `test_set_bow_suggestions_writes_and_clears` (3 captures; `{c1:"14", c2:"7"}` → c1/c2 set, c3 NULL; then `{c3:"3"}` → c1/c2 NULL, c3 "3"); `test_set_bow_suggestions_does_not_touch` (`last_updated(race_id)`, `last_updated()`, and each `capture.updated_at` unchanged).
`tests/test_asr.py` (append): `test_build_command_exact_argv`; `test_model_path` (`"base"` → `data_root/models/ggml-nb-whisper-base.bin`; `"/x/y.bin"` → `Path("/x/y.bin")`); `test_subprocess_runner_missing_binary` (`SubprocessRunner().run(["definitely-missing-whisper"], tmp/"asr", 5)` → `error == "definitely-missing-whisper not found"`, no exception, on Windows and Linux alike thanks to the `shutil.which` check); `test_subprocess_runner_nonzero_exit` (`cmd=[sys.executable, "-c", "import sys; sys.exit(3)"]` → `error` non-empty, `json_text None`); `test_fake_runner_records_calls`.

**Commit:** `feat(asr): bow_suggested writer, whisper-cli command, runners (§13.3.4 step 16)`
