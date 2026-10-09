# Sheet 15 — `asr.py`: whisper JSON parsing + utterance grouping

> **Rules (same for every sheet).** 1) Read `AGENTS.md`, then this sheet, then ONLY the files under "Read first"; do not read the spec or other sheets. 2) Edit ONLY the files under "Files you may edit"; if another file must change, write `plans/bow-capture/BLOCKED-<nn>.md` (what / why / which file) and stop. 3) No new dependencies, config keys, tables or columns beyond what this sheet states verbatim. 4) Never change a test's expected value to make it pass. 5) Nothing you write runs on the trigger thread; nothing opens its own SQLite connection. 6) Run the fast suite before and after (`~/regatta/venv/bin/python -m pytest -m "not slow"` on the laptop; `python -m pytest -m "not slow"` on Windows, where 7 known host-artifact failures pre-exist — see `00-overview.md`). 7) Done = this sheet's tests pass + no new failures + one commit with the message at the bottom. 8) Short docstrings; one-line comments only where the sheet says "comment:".

**Files you may edit:** `hallofframe/asr.py`, `tests/test_asr.py` (append).
**Read first:** `hallofframe/asr.py`.

### Spec
```python
@dataclass(frozen=True)
class Word:      text: str; start_s: float; end_s: float; p: float
@dataclass(frozen=True)
class Utterance: value: str; start_s: float; end_s: float; p_min: float

def parse_whisper_json(obj: dict) -> list[Word]:
    """whisper.cpp --output-json-full: obj["transcription"][*] has "text", "offsets"{from,to} (ms) and usually
    "tokens"[*]{text, offsets{from,to}, p}. A token MAY lack "offsets" (whisper writes them only when it has a
    token time): such a token inherits the segment's offsets. Skip tokens whose text starts with "[_".
    A token whose text starts with a space starts a new word; other tokens append to the current word
    (" fjor"+"ten" → "fjorten"). Word.text is the joined text .strip()ed. Word start/end = first token
    offsets.from / last token offsets.to, in seconds; Word.p = min token p (missing p → 1.0).
    Segment without "tokens": split its "text" on whitespace, all words get the segment offsets, p = 1.0.
    Result sorted by start_s."""

def group_utterances(words: list[Word], gap_s: float, max_digits: int = MAX_DIGITS) -> list[Utterance]:
    """Walk words in time order. Words whose clean_word() is empty (punctuation tokens) are skipped WITHOUT
    splitting. A new utterance starts when word.start_s - prev.end_s > gap_s, or when the previous word was
    not a number (word_to_digits None). Inside an utterance an adjacent <tens> <unit> pair merges
    (tjue en → "21"); otherwise digit strings concatenate (en fire → "14", to en → "21"). Non-number words
    are dropped and split. Utterance.p_min = min(w.p) over its words. Utterances with no digits, or whose
    value is longer than max_digits, are dropped."""
```

### Tests (append to `tests/test_asr.py`; define `CANNED` once at module level — the §13.3.2 example "14, 7, 21, 3")
```python
CANNED = {"transcription": [
  {"offsets": {"from": 1000, "to": 2100}, "text": " en fire",
   "tokens": [{"text": "[_BEG_]", "offsets": {"from": 1000, "to": 1000}, "p": 1.0},
              {"text": " en",  "offsets": {"from": 1200, "to": 1500}, "p": 0.91},
              {"text": " fire","offsets": {"from": 1600, "to": 2100}, "p": 0.88}]},
  {"offsets": {"from": 4000, "to": 4400}, "text": " sju",
   "tokens": [{"text": " sju", "offsets": {"from": 4000, "to": 4400}, "p": 0.95}]},
  {"offsets": {"from": 7000, "to": 8300}, "text": " to en",
   "tokens": [{"text": " to", "offsets": {"from": 7000, "to": 7300}, "p": 0.9},
              {"text": " en", "offsets": {"from": 7500, "to": 7800}, "p": 0.9}]},
  {"offsets": {"from": 9000, "to": 9300}, "text": " tre",
   "tokens": [{"text": " tre", "offsets": {"from": 9000, "to": 9300}, "p": 0.9}]}]}
```
| Test | Input | Expected |
|---|---|---|
| `test_parse_skips_special_tokens_and_joins_subwords` | tokens `" fjor"`, `"ten"` (1200–1500, 1500–1900) | one `Word("fjorten", 1.2, 1.9, …)` |
| `test_parse_fallback_without_tokens` | segment `text=" en fire"`, no tokens | two words, both with segment offsets, p 1.0 |
| `test_parse_token_without_offsets_inherits_segment` | segment offsets 1000–2000; tokens `" en"` (no `offsets`), `" fire"` (1600–2000) | words `("en", 1.0, 2.0)`, `("fire", 1.6, 2.0)` |
| `test_group_skips_punctuation_without_splitting` | words "en", ",", "fire" within 0.1 s | `["14"]` |
| `test_group_canned` | `group_utterances(parse_whisper_json(CANNED), 0.4)` | values `["14","7","21","3"]`, starts `[1.2, 4.0, 7.0, 9.0]` |
| `test_group_splits_on_non_number_word` | "en","og","to" within 0.1 s | `["1","2"]` |
| `test_group_merges_tens_unit` | "tjue","en" 0.1 s apart | `["21"]` |
| `test_group_drops_repetition_loop` | six "en" 0.1 s apart | `[]` |
| `test_group_gap_splits` | "en" at 1.0–1.2, "fire" at 2.0–2.2, gap 0.4 | `["1","4"]` |

**Commit:** `feat(asr): whisper JSON parsing and utterance grouping (§13.3.4 step 15)`
