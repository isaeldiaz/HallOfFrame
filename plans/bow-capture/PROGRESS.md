01 1999788 11 tests (tests/test_storage_scratch.py) 401 passed, 4 deselected OK
02 727e3b3 10 tests (tests/test_scratchpad.py) 411 passed, 4 deselected OK
03 4144d88 12 tests (tests/test_controller_scratch.py) 423 passed, 4 deselected OK
04 438c7e9 6 tests (tests/test_trigger.py) 429 passed, 4 deselected OK
05 108fbd9 10 tests (tests/test_scratchpad_widget.py) 439 passed, 4 deselected OK
06 d3a0ae4 6 tests (tests/test_race_screen_scratch.py) 445 passed, 4 deselected OK
07 8c8ab2d 9 tests (tests/test_main_window_scratch.py) 454 passed, 4 deselected OK
08 4513683 0 tests (docs) 454 passed, 4 deselected OK
09 cd66cf1 8 tests (tests/test_audio.py) 474 passed, 4 deselected OK
10 0ed3a2f 10 tests (tests/test_audio.py) 484 passed, 4 deselected OK
11 ca3caa9 5 tests (tests/test_controller_audio.py) 489 passed, 4 deselected OK
12 646335c 4 tests (tests/test_review_audio.py, tests/test_session.py) 493 passed, 4 deselected OK
13 b3efd78 0 tests (docs) 493 passed, 4 deselected OK
12b fbfbd29 10 tests (tests/test_audio_view.py, tests/test_audio.py, tests/test_review_audio.py) 507 passed (+7 Windows host artifacts), 4 deselected OK
12c 772347f 12 tests (tests/test_audio.py, tests/test_controller_audio.py, tests/test_review_audio.py) 519 passed (+7 Windows host artifacts), 4 deselected OK
21 6c98f43 21 tests (tests/test_reaction.py, tests/test_calibration.py) 572 passed (+7 Windows host artifacts), 4 deselected OK
21 note: the sheet's test table listed `iqr_ms == pytest.approx(37.5)`; that value was wrong. Sheet's own algorithm (`statistics.quantiles(samples, n=4)` as in `compute_latency`; default 'exclusive' → `[212.5, 235, 257.5]`, `q[2]-q[0]`) gives 45.0, which `tests/test_reaction.py` asserts and `calibration.py` returns. The sheet value has been corrected to 45.0.
22/23 48f2bef 16 tests (tests/test_audio.py, tests/test_reaction_page.py, tests/test_calibration_dialog.py, tests/test_main_window_calibrate.py) 588 passed, 4 deselected OK
22/23 note: sheets 22 and 23 were merged into one commit whose message names both steps (the sheets specify separate commit messages). Follow-up fixes (uncommitted at time of writing): Esc now aborts a running reaction trial — `StimulusWindow` emits `aborted` on `Qt.Key_Escape` (the dialog is hidden on `run_started`, so its own `keyPressEvent` never sees the key) and `ReactionPage.start` connects it to `abort`; covered by `test_stimulus_window_escape_emits_aborted` and `test_stimulus_escape_aborts`. Four sheet-22 page tests missing at commit time were added (`test_press_before_start_ignored`, `test_audio_kind_uses_beep`, `test_qt_fallback_uses_stimulus_keys`, `test_abort_closes_everything`), as were two sheet-23 tests (`test_hook_bypassed_while_armed`, `test_dialog_evdev_flag`). Omissions: sheet-23 `test_calibrate_opens_tabs` is dropped because the implementation is a `QStackedWidget` with Back/Skip/Finish, not a `QTabWidget`, so the tab-text assertion no longer applies; the sheet's reaction tests specify `trials=3` but `REACTION_MIN=6`, so the added tests use 6. Fast suite after the follow-up: 596 passed, 4 deselected.
