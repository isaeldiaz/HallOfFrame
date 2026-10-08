"""End-to-end pipeline test (plan step 7.5, slow).

The one test that proves the layers still fit: a fake MJPEG camera (step 7.4)
feeds the real :class:`MJPEGReader` into the real ring buffer, the real
``CaptureController`` records crossings and performs deferred frame selection
through the gun-indexed frame store, and the real web renderer shows the result.

No phone, no display, no calibration file: ``viewing_mode = "screen"`` so Δ
comes from config alone, and ``[transport] enabled = false`` so ``build_core``
skips the USB tunnel entirely.
"""
from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")   # qt suite skips cleanly when PySide6 is absent

from fakes import FakeScheduler
from hallofframe.main import build_core
from hallofframe.tools.fake_camera import start_server
from hallofframe.web import build_race_page


def _make_jpegs(folder: Path, n: int = 90) -> None:
    """*n* incompressible ~200 KB JPEGs.

    Each frame must exceed the reader's 64 KB read chunk: with smaller frames a
    single ``raw.read(64 KB)`` blocks until several frames accumulate and the
    stream arrives in multi-second bursts instead of one frame at a time."""
    from PIL import Image

    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        im = Image.frombytes("RGB", (640, 360), os.urandom(640 * 360 * 3))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=90)
        (folder / f"frame_{i:04d}.jpg").write_bytes(out.getvalue())


@pytest.mark.slow
def test_fake_camera_end_to_end(data_root, config):
    frames_src = data_root / "camera"
    _make_jpegs(frames_src, 90)

    server, thread, camera = start_server(
        frames_src, port=0, fps=30.0, loop=True)
    url = f"http://127.0.0.1:{server.server_address[1]}/video"

    cfg = config(
        transport={"enabled": False},
        stream={"url": url, "username": "", "password": "",
                "assumed_fps": 30, "buffer_seconds": 10.0,
                "require_content_length": True},
        timing={"viewing_mode": "screen", "reaction_offset_ms": 0.0,
                "image_mode": "auto"},
        capture={"window_before_ms": 150, "window_after_ms": 150},
    )

    core = build_core(cfg)
    scheduler = FakeScheduler()
    core["controller"]._scheduler = scheduler  # inject the deterministic timer
    storage = core["storage"]
    buffer = core["buffer"]
    controller = core["controller"]
    reader = core["reader"]

    try:
        reader.start()

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            alive, _fps, _age = buffer.health()
            if alive and len(buffer.recent(2)) >= 2:
                break
            time.sleep(0.02)
        else:
            pytest.fail("fake camera never became alive in the ring buffer")

        t0 = time.monotonic()
        race_id = controller.start_race(t0, name="E2E", race_no="1", heat_no="1")

        # Two crossings 200 ms apart (the times are the evdev-press stand-ins).
        controller.record_crossing(t0 + 0.3)
        time.sleep(0.2)
        controller.record_crossing(t0 + 0.5)

        # Let the single writer thread commit both rows and register both
        # deferred-selection timers (registration happens inside _handle_capture).
        controller._queue.join()

        # The after-window frames must actually exist before selection runs, so
        # wait on the REAL clock for the buffer to cover t2 + window_after. The
        # fake scheduler only controls when the timer fires, not frame arrival.
        window_after = cfg.section("capture")["window_after_ms"] / 1000.0
        deadline = t0 + 0.5 + window_after + 0.15
        while time.monotonic() < deadline:
            time.sleep(0.01)

        scheduler.advance(1.0)

        # --- two captures, each with a primary image ------------------------
        rows = storage.captures_for_race(race_id)
        assert [r["sequence"] for r in rows] == [1, 2]
        assert all(r["primary_image"] for r in rows)
        for r in rows:
            assert (storage.data_root / r["primary_image"]).exists()

        # --- frames live once per race in a shared frames/ directory ---------
        f0 = storage.frames_for_capture(rows[0]["id"])
        f1 = storage.frames_for_capture(rows[1]["id"])
        assert f0 and f1
        frame_dirs = {Path(f["path"]).parent for f in f0 + f1}
        assert frame_dirs == {Path(rows[0]["primary_image"]).parent}
        assert "frames" in frame_dirs.pop().parts
        shared = {f["id"] for f in f0} & {f["id"] for f in f1}
        assert shared, "200 ms-apart windows must share frames"
        on_disk = list(Path(storage.data_root / rows[0]["primary_image"]).parent
                       .glob("*.jpg"))
        union = {f["id"] for f in f0} | {f["id"] for f in f1}
        assert len(on_disk) == len(union), "one file per distinct t_ms"

        # --- the web page renders both crossings ----------------------------
        page = build_race_page(storage, race_id)
        assert page is not None
        assert page.count('data-search="') == 2
        assert "#001" in page and "#002" in page

        # --- clone the second: the page now renders three rows ---------------
        clone = controller.clone(rows[1]["id"])
        assert clone is not None and clone.sequence == 3
        src = storage.capture(rows[1]["id"])
        new = storage.capture(clone.id)
        assert new["target_ms"] == src["target_ms"]
        assert new["primary_frame_id"] == src["primary_frame_id"]
        assert storage.frames_for_capture(clone.id) == f1

        page = build_race_page(storage, race_id)
        assert page.count('data-search="') == 3
    finally:
        reader.stop()
        camera.stop()
        controller.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        storage.close()
