"""Debounced persistence for the memory service.

Every mutation currently re-dumps the whole library: 19,494 entries / 68 MB took
440 ms per write, measured 2026-10-29, and the cost is linear in entry count while
the server is single-threaded. Batching writes without losing data needs three
things, which is what this class provides:

* the first write persists immediately, so a single remembered fact is on disk
  before the call returns;
* a burst of writes inside ``interval`` seconds collapses into one flush;
* nothing is ever dropped: a background flusher and an atexit hook force the last
  batch out, and ``flush_if_dirty`` is the explicit escape hatch.

Set ``FLYMEMORY_SAVE_DEBOUNCE=0`` to restore the write-through behaviour.
"""

import atexit
import threading
import time


class DebouncedSaver:
    def __init__(self, write, interval=5.0, clock=time.time):
        self._write = write
        self._interval = max(0.0, float(interval))
        self._clock = clock
        self._last = 0.0
        self._dirty = False
        self._lock = threading.Lock()
        self.flushes = 0

    @property
    def interval(self):
        return self._interval

    def mark(self):
        """Record a mutation; flush now when the interval has elapsed."""
        if self._interval <= 0:
            with self._lock:
                self._dirty = True
            self.flush()
            return
        with self._lock:
            self._dirty = True
            due = (self._clock() - self._last) >= self._interval
        if due:
            self.flush()

    def flush(self):
        """Write if there is anything pending. Returns True when it wrote."""
        with self._lock:
            if not self._dirty:
                return False
            self._dirty = False
            self._last = self._clock()
        self._write()
        with self._lock:
            self.flushes += 1
        return True

    def flush_if_dirty(self):
        return self.flush()

    def start_background(self, name="flymemory-save"):
        """Periodically flush so a quiet store still reaches disk."""
        interval = self._interval if self._interval > 0 else 1.0
        stop = threading.Event()

        def loop():
            while not stop.wait(interval):
                try:
                    self.flush()
                except Exception:  # a failed flush must not kill the thread
                    pass

        thread = threading.Thread(target=loop, name=name, daemon=True)
        thread.start()
        atexit.register(self.flush_if_dirty)
        return thread, stop
