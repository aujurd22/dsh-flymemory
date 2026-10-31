"""Unit tests for capture-batch grouping (pure)."""
import os, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
from recall_block import batch_key, group_by_batch


def entry(ts, app="dsh", mid=1):
    return types.SimpleNamespace(timestamp=ts, tags=[f"app:{app}"], memory_id=mid)


def test_same_batch_within_window():
    a, b = entry(1000.0), entry(1000.9)
    assert batch_key("dsh", a.timestamp) == batch_key("dsh", b.timestamp)


def test_different_batches_split():
    a, b = entry(1000.0), entry(1010.0)
    assert batch_key("dsh", a.timestamp) != batch_key("dsh", b.timestamp)


def test_app_separates_batches():
    assert batch_key("dsh", 1000.0) != batch_key("zcode", 1000.0)


def test_groups_keep_best_similarity_and_limit():
    hits = [(entry(1000.0, mid=1), 0.80), (entry(1000.4, mid=2), 0.90),
            (entry(1020.0, mid=3), 0.70), (entry(1040.0, mid=4), 0.60)]
    groups = group_by_batch(hits, parents=2)
    assert len(groups) == 2
    assert abs(groups[0][1] - 0.90) < 1e-9


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  ok   {name}")
            except AssertionError as error:
                failures += 1; print(f"  FAIL {name}: {error}")
    print("PASS" if failures == 0 else f"{failures} failure(s)")
    sys.exit(1 if failures else 0)
