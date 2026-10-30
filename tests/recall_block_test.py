"""Unit tests for budget-aware recall packing (pure, no model)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
from recall_block import pack, top_sim


def test_uses_budget_not_count():
    entries = [(f"[#{i} | 0.9 | app=dsh | 1h]", "x" * 300) for i in range(20)]
    block, stats = pack(entries, budget_chars=1500, per_entry_chars=600)
    assert stats["used"] <= 1500, stats
    assert 2 <= stats["taken"] <= 5, stats
    assert stats["skipped"] == 20 - stats["taken"]
    assert "[memory block:" in block


def test_truncates_long_entries():
    entries = [("[#1 | 0.9 | app=dsh | 1h]", "y" * 5000)]
    block, stats = pack(entries, budget_chars=10_000, per_entry_chars=200)
    assert stats["taken"] == 1
    assert "…" in block and len(block) < 400


def test_never_exceeds_budget_even_with_many_small_entries():
    entries = [(f"[#{i} | 0.8 | app=dsh | 1h]", "z" * 50) for i in range(200)]
    block, stats = pack(entries, budget_chars=1000, per_entry_chars=100)
    assert stats["used"] <= 1000
    assert len(block) <= 1200


def test_skips_entries_that_do_not_fit():
    entries = [("[#1 | 0.9]", "a" * 400), ("[#2 | 0.8]", "b" * 400)]
    block, stats = pack(entries, budget_chars=500, per_entry_chars=600)
    assert stats["taken"] == 1, stats


def test_top_sim_reads_the_best_score():
    assert top_sim("[#12 | 0.83 | app=dsh | 2d]") == 0.83
    assert top_sim("no pointer here") is None


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
