"""organize(): retire structurally stale entries, never touch near-duplicates."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
from flymemory.v3 import MemoryEntry, SmartMemory

def make():
    return SmartMemory(n_bits=512, decay_tau=2592000.0)

def add(mem, text, mid, state_key=None, state_value=None, ts=None):
    mem.memories.append(MemoryEntry(
        text=text, response="", embedding=None,
        timestamp=ts if ts is not None else time.time(),
        last_accessed=0.0, access_count=0, tags=["app:test"], memory_id=mid,
        source="import", state_key=state_key, state_value=state_value))

def test_stale_states_retired_newest_wins():
    mem = make()
    add(mem, "旧端口 8765", 1, state_key="svc.port", state_value="8765", ts=1000.0)
    add(mem, "新端口 8766", 2, state_key="svc.port", state_value="8766", ts=2000.0)
    report = mem.organize(apply=True)
    assert report["stale_states"] == 1
    assert report["stale_state_ids"] == [1]
    old = next(m for m in mem.memories if m.memory_id == 1)
    assert old.superseded_by == 2
    new = next(m for m in mem.memories if m.memory_id == 2)
    assert new.superseded_by is None

def test_exact_duplicate_retired():
    mem = make()
    add(mem, "同一个事实  内容", 1, ts=1000.0)
    add(mem, "同一个事实 内容", 2, ts=2000.0)
    report = mem.organize(apply=True)
    assert report["exact_dups"] == 1
    dup = next(m for m in mem.memories if m.memory_id == 1)
    assert dup.superseded_by == 2

def test_report_only_changes_nothing():
    mem = make()
    add(mem, "同一个事实", 1, ts=1000.0)
    add(mem, "同一个事实", 2, ts=2000.0)
    report = mem.organize(apply=False)
    assert report["exact_dups"] == 1
    assert all(m.superseded_by is None for m in mem.memories)

def test_near_duplicates_are_never_applied():
    mem = make()
    add(mem, "部署用 docker compose", 1, ts=1000.0)
    add(mem, "部署改成 docker compose 了", 2, ts=2000.0)
    report = mem.organize(apply=True)
    # 文本不同 -> 只能报告，不能自动取代
    assert report["stale_states"] == 0 and report["exact_dups"] == 0
    assert all(m.superseded_by is None for m in mem.memories)

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
