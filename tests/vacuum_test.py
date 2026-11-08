"""vacuum(): physically drop entries retired longer ago than the window."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
from flymemory.v3 import MemoryEntry, SmartMemory

OLD = time.time() - 60 * 86400      # retired 60 days ago
RECENT = time.time() - 2 * 86400    # retired 2 days ago

def add(mem, mid, ts, superseded_by=None):
    mem.memories.append(MemoryEntry(
        text=f"条目 {mid}", response="", embedding=None, timestamp=ts,
        last_accessed=0.0, access_count=0, tags=["app:test"], memory_id=mid,
        source="import", superseded_by=superseded_by, updated_at=ts, valid_to=ts))

def test_old_retired_entries_are_removed():
    mem = SmartMemory(n_bits=512, decay_tau=2592000.0)
    add(mem, 1, OLD, superseded_by=2)
    add(mem, 2, OLD)
    report = mem.vacuum(older_than_days=30, apply=True)
    assert report["removed"] == 1
    assert [m.memory_id for m in mem.memories] == [2]

def test_recent_retired_entries_are_kept():
    mem = SmartMemory(n_bits=512, decay_tau=2592000.0)
    add(mem, 1, RECENT, superseded_by=2)
    add(mem, 2, RECENT)
    report = mem.vacuum(older_than_days=30, apply=True)
    assert report["removed"] == 0 and len(mem.memories) == 2

def test_live_entries_never_removed():
    mem = SmartMemory(n_bits=512, decay_tau=2592000.0)
    add(mem, 1, OLD)                    # live but very old
    report = mem.vacuum(older_than_days=1, apply=True)
    assert report["removed"] == 0 and len(mem.memories) == 1

def test_dry_run_reports_without_deleting():
    mem = SmartMemory(n_bits=512, decay_tau=2592000.0)
    add(mem, 1, OLD, superseded_by=2)
    add(mem, 2, OLD)
    report = mem.vacuum(older_than_days=30, apply=False)
    assert report["candidates"] == 1 and report["removed"] == 0
    assert len(mem.memories) == 2

if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  ok   {name}")
            except AssertionError as e:
                failures += 1; print(f"  FAIL {name}: {e}")
    print("PASS" if failures == 0 else f"{failures} failure(s)")
    sys.exit(1 if failures else 0)
