"""find_clusters(): three or more entries that state one fact become one cluster."""
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
from flymemory.v3 import MemoryEntry, SmartMemory

def vec(x, y=0.0, z=0.0):
    v = np.zeros(384, dtype=np.float32)
    v[0], v[1], v[2] = x, y, z
    return v

def add(mem, mid, text, emb, ts=None):
    mem.memories.append(MemoryEntry(
        text=text, response="", embedding=emb,
        timestamp=ts if ts is not None else time.time() + mid,
        last_accessed=0.0, access_count=0, tags=["app:test"], memory_id=mid,
        source="import"))

def test_three_synonyms_form_one_cluster():
    mem = SmartMemory(n_bits=512, decay_tau=2592000.0)
    add(mem, 1, "部署用 docker compose", vec(1.0))
    add(mem, 2, "部署方式：docker compose", vec(1.0, 0.02))
    add(mem, 3, "部署就是 docker compose", vec(1.0, 0.04))
    add(mem, 4, "数据库是 postgres", vec(0.0, 1.0))
    clusters = mem.find_clusters(min_sim=0.9, min_size=3)
    assert len(clusters) == 1, clusters
    assert clusters[0]["size"] == 3
    assert set(clusters[0]["ids"]) == {1, 2, 3}

def test_pairs_are_not_clusters():
    mem = SmartMemory(n_bits=512, decay_tau=2592000.0)
    add(mem, 1, "端口 8765", vec(1.0))
    add(mem, 2, "端口是 8765", vec(1.0, 0.01))
    assert mem.find_clusters(min_sim=0.9, min_size=3) == []

def test_unrelated_entries_stay_apart():
    mem = SmartMemory(n_bits=512, decay_tau=2592000.0)
    add(mem, 1, "A", vec(1.0))
    add(mem, 2, "B", vec(0.0, 1.0))
    add(mem, 3, "C", vec(0.0, 0.0, 1.0))
    assert mem.find_clusters(min_sim=0.5, min_size=2) == []

def test_max_size_caps_the_group():
    mem = SmartMemory(n_bits=512, decay_tau=2592000.0)
    for i in range(1, 7):
        add(mem, i, f"同一件事 {i}", vec(1.0, i * 0.001))
    clusters = mem.find_clusters(min_sim=0.9, min_size=3, max_size=4)
    assert clusters and clusters[0]["size"] == 4

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
