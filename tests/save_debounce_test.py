"""Unit tests for debounced persistence (no torch, no model download)."""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

from save_debouncer import DebouncedSaver  # noqa: E402


def test_first_write_is_immediate():
    writes = []
    saver = DebouncedSaver(lambda: writes.append(1), interval=60, clock=lambda: 1000.0)
    saver.mark()
    assert writes == [1], "a single write must land before the call returns"
    assert saver.flushes == 1


def test_burst_collapses():
    writes = []
    now = [1000.0]
    saver = DebouncedSaver(lambda: writes.append(1), interval=5, clock=lambda: now[0])
    saver.mark()          # immediate (first)
    for _ in range(9):
        now[0] += 0.2
        saver.mark()      # inside the window: batched
    assert writes == [1], writes
    now[0] += 5.0
    saver.mark()          # window elapsed: flush once
    assert writes == [1, 1], writes


def test_quiet_store_still_flushes():
    writes = []
    now = [1000.0]
    saver = DebouncedSaver(lambda: writes.append(1), interval=5, clock=lambda: now[0])
    saver.mark()
    saver.mark()
    assert writes == [1]
    assert saver.flush_if_dirty() is True, "explicit flush must persist the batch"
    assert writes == [1, 1]
    assert saver.flush_if_dirty() is False, "nothing pending after a flush"


def test_zero_interval_restores_write_through():
    writes = []
    saver = DebouncedSaver(lambda: writes.append(1), interval=0)
    for _ in range(3):
        saver.mark()
    assert writes == [1, 1, 1]


def test_background_flusher_writes_pending():
    writes = []
    saver = DebouncedSaver(lambda: writes.append(1), interval=0.05)
    thread, stop = saver.start_background()
    try:
        saver.mark()      # immediate
        saver._dirty = True   # simulate a mutation queued behind the window
        deadline = time.time() + 2.0
        while time.time() < deadline and len(writes) < 2:
            time.sleep(0.01)
    finally:
        stop.set()
    assert len(writes) >= 2, f"background flusher did not run: {writes}"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as error:
                failures += 1
                print(f"  FAIL {name}: {error}")
    print("PASS" if failures == 0 else f"{failures} failure(s)")
    sys.exit(1 if failures else 0)
