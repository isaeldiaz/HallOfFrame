"""Calibration dialog: multi-phase flow with a per-phase Skip."""
from __future__ import annotations

from unittest import mock

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog

from hallofframe.calibration import calibration_path
from hallofframe.ui.calibration_dialog import CalibrationDialog

pytestmark = pytest.mark.qt


def _dialog(buffer, config):
    return CalibrationDialog(buffer, config.data_root, config)


def test_skip_advances_one_phase(qapp, buffer, config):
    cfg = config()
    dlg = _dialog(buffer, cfg)
    assert dlg.phase_label.text() == "Phase 1 of 2 — Latency"
    dlg.skip_btn.click()
    assert dlg._index == 1
    assert dlg.phase_label.text() == "Phase 2 of 2 — Reaction"
    assert dlg.skipped == {0}
    assert not calibration_path(cfg.data_root).exists()


def test_skip_last_phase_closes(qapp, buffer, config):
    cfg = config()
    dlg = _dialog(buffer, cfg)
    dlg.show()
    qapp.processEvents()
    dlg.skip_btn.click()  # skip latency -> reaction
    assert dlg._index == 1
    dlg.skip_btn.click()  # skip reaction -> finish
    assert dlg.result() == QDialog.Accepted
    assert not dlg.isVisible()
    assert not calibration_path(cfg.data_root).exists()


def test_back_returns_to_first_phase(qapp, buffer, config):
    cfg = config()
    dlg = _dialog(buffer, cfg)
    assert not dlg.back_btn.isEnabled()
    dlg.skip_btn.click()
    assert dlg.back_btn.isEnabled()
    dlg.back_btn.click()
    assert dlg._index == 0
    assert not dlg.back_btn.isEnabled()


def test_finish_closes(qapp, buffer, config):
    cfg = config()
    dlg = _dialog(buffer, cfg)
    dlg.show()
    qapp.processEvents()
    dlg.finish_btn.click()
    assert dlg.result() == QDialog.Accepted
    assert not dlg.isVisible()


def test_save_reaction_keeps_dialog_open(qapp, buffer, config):
    cfg = config()
    dlg = _dialog(buffer, cfg)
    dlg.show()
    qapp.processEvents()
    dlg.skip_btn.click()  # skip latency -> reaction
    assert dlg._index == 1

    page = dlg.reaction
    page.run = mock.Mock(kind="visual", samples_ms=[250.0])
    page.stats = {"median_ms": 250.0, "iqr_ms": 0.0, "mean_ms": 250.0, "n": 1}
    page.save_btn.setEnabled(True)
    page.save_btn.click()
    qapp.processEvents()

    assert dlg.isVisible()
    assert dlg._index == 1
    assert calibration_path(cfg.data_root).exists()
