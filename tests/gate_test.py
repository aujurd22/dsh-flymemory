"""Confidence gate: absolute floor, with a margin escape for sparse stores."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
import hook_auto

def test_no_sims_is_not_low_confidence():
    assert hook_auto._is_low_confidence([]) is False

def test_high_similarity_passes():
    assert hook_auto._is_low_confidence([0.72, 0.40], floor=0.40) is False

def test_low_and_tight_loses():
    assert hook_auto._is_low_confidence([0.22, 0.21], floor=0.40) is True

def test_low_but_clear_winner_passes():
    # 稀疏库上的正常情况：绝对分低，但第一名明显领先
    assert hook_auto._is_low_confidence([0.20, 0.05], floor=0.40) is False

def test_single_hit_below_floor_loses():
    assert hook_auto._is_low_confidence([0.18], floor=0.40) is True

def test_floor_comes_from_env():
    os.environ["FLYMEMORY_MIN_SIM"] = "0.90"
    try:
        assert hook_auto._is_low_confidence([0.80, 0.79]) is True
    finally:
        del os.environ["FLYMEMORY_MIN_SIM"]

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
