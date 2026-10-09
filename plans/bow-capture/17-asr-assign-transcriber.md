# Sheet 17 — `asr.py`: assignment + `Transcriber`

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/asr.py`, `tests/test_asr.py` (append).
**Read first:** `asr.py`, `storage.py` (`get_race`, `captures_for_race`, `set_bow_suggestions`, `set_race_audio`), `tests/conftest.py` (`storage`, `data_root`, `config`).

### Spec
```python
def assign(utterances: list[Utterance], capture_ids: list[int]) -> dict[int, str | None]:
    """k-th utterance (time order) → k-th capture id; ids beyond the utterance count map to None."""
@dataclass
class TranscribeResult:
    race_id: int; count: int; utterances: list[Utterance]; error: str | None
    # count = number of captures that received a suggestion (not the number of utterances)
class Transcriber:
    def __init__(self, storage, runner, *, whisper_cli: str, model: Path, gap_s: float, timeout_s: float, data_root: Path, logger=None)
    @classmethod
    def from_config(cls, config, storage, runner=None, logger=None) -> "Transcriber":
        # v = config.section("voice"); cls(storage, runner or SubprocessRunner(), whisper_cli=str(v["whisper_cli"]),
        #     model=model_path(v, config.data_root), gap_s=float(v["utterance_gap_s"]),
        #     timeout_s=float(v["transcribe_timeout_s"]), data_root=config.data_root, logger=logger)
    def run(self, race_id: int) -> TranscribeResult:
        """Never raises: the whole body is wrapped in `try/except Exception as exc: return TranscribeResult(race_id, 0, [], str(exc))`.
        err(msg) below means `return TranscribeResult(race_id, 0, [], msg)`.
        race = storage.get_race(race_id); None or race["audio_path"] None → err("no audio for this race").
        wav = data_root / race["audio_path"]; not wav.exists() → err("audio file missing"). not model.exists() → err(f"model not found: {model}").
        out_base = wav.with_name("asr"); res = runner.run(build_command(whisper_cli, model, wav, out_base), out_base, timeout_s)
        if res.json_text is None: err(res.error or "no whisper output")
        try: obj = json.loads(res.json_text) except (ValueError, TypeError): err("unreadable whisper output")
        utts = group_utterances(parse_whisper_json(obj), gap_s)
        offset = race["audio_t0_offset_s"]; if offset is not None: utts = [u for u in utts if u.start_s + offset >= 0]
          # comment: audio second s is gun-elapsed s + offset; a negative result is chatter before the gun
        caps = storage.captures_for_race(race_id)              # non-deleted, by sequence
        mapping = assign(utts, [c["id"] for c in caps]); storage.set_bow_suggestions(race_id, mapping)
        count = sum(1 for v in mapping.values() if v is not None); log asr/done; return TranscribeResult(race_id, count, utts, None)."""
    def cancel(self) -> None: runner.cancel()
```
Writes go straight to `Storage` from the caller's thread (the lock makes that safe; the review screen already writes from the GUI thread). `grep -n bow_number hallofframe/asr.py` must return nothing.

### Tests (append to `tests/test_asr.py`)
Setup helper `_race_with_audio(storage, data_root, offset=0.5)`:
```python
rid = storage.create_race("R", 0.0, 0.0, "direct", 0.0, 0.0, "screen")
wav = data_root / "races" / "0001_R" / "audio.wav"; wav.parent.mkdir(parents=True); wav.write_bytes(b"RIFF")
storage.set_race_audio(rid, "races/0001_R/audio.wav", offset)
model = data_root / "models" / "ggml-nb-whisper-base.bin"; model.parent.mkdir(); model.touch()
ids = [storage.insert_capture(rid, seq, float(seq), float(seq), float(seq), 0.0,
                              bow_number="7" if seq == 2 else None) for seq in (1, 2, 3, 4)]
extra = storage.insert_capture(rid, 5, 5.0, 5.0, 5.0, 0.0); storage.update_capture(extra, deleted=1)
tr = Transcriber(storage, FakeRunner(json.dumps(CANNED)), whisper_cli="whisper-cli", model=model, gap_s=0.4, timeout_s=5, data_root=data_root)
```
| Test | Expected |
|---|---|
| `test_assign_order_and_padding` | `assign([u14,u7], [a,b,c])` → `{a:"14", b:"7", c:None}` |
| `test_transcriber_writes_suggestions_in_sequence_order` | `bow_suggested` by sequence `["14","7","21","3"]`; deleted row NULL; `runner.calls[0][0] == build_command(...)`; `result.count == 4`; `result.race_id == rid` |
| `test_transcriber_never_writes_bow_number` | every row's `bow_number`/`bow_source` identical before/after |
| `test_transcriber_drops_pre_gun_utterances` | `offset=-5.0` (synthetic: recording began 5 s before the gun) → seq1 `"21"`, seq2 `"3"`, seq3/4 NULL; `count == 2` |
| `test_transcriber_count_is_captures_filled` | only 2 captures → `count == 2` although CANNED has 4 utterances |
| `test_transcriber_reports_runner_error` | `FakeRunner(None, "timed out")` → `error == "timed out"`; no `bow_suggested` written |
| `test_transcriber_no_audio` | race without `audio_path` → error; `runner.calls == []` |
| `test_transcriber_bad_json_is_error_not_exception` | `FakeRunner("{not json")` → `error == "unreadable whisper output"` |
| `test_transcriber_rerun_is_idempotent` | run twice → same values, no duplicates |

**Commit:** `feat(asr): order-rule assignment and Transcriber (§13.3.4 step 17)`
