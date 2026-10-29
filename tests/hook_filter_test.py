"""Unit tests for the hook prompt filters (no network, no model download)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

from hook_filters import strip_recent  # noqa: E402

RECALL = """RECALLED MEMORIES:
  [#19687 | 0.86 | app=dsh | 23分钟前(10-29)] 看一下zcode那边的更新
  [#18776 | 0.71 | app=unknown+dsh | 5天前(10-24)] 看看zcode那边的更新
RECENT CONTEXT (last ~90 min):
  [#19682 | app=dsh | 1小时前(10-29)] 看看这个的购票人名字
  [#19683 | app=dsh | 1小时前(10-29)] 也就是说只要修改账号名字
HINT: entries above are one-line summaries; fetch full text with flymemory_get_memory(memory_id)
STORED: [MERGED #19687 | app=dsh] zcode 那边更新了什么
"""


def test_drops_recent_block_and_stored_receipt():
    out = strip_recent(RECALL)
    assert "RECENT CONTEXT" not in out, out
    assert "购票人名字" not in out, "another conversation's trail leaked"
    assert "STORED:" not in out, out
    assert "RECALLED MEMORIES:" in out
    assert "#19687" in out and "#18776" in out
    assert "HINT:" in out


def test_keeps_everything_when_the_window_is_enabled(monkeypatch=None):
    os.environ["FLYMEMORY_RECENT_MINUTES"] = "90"
    try:
        out = strip_recent(RECALL)
    finally:
        os.environ.pop("FLYMEMORY_RECENT_MINUTES", None)
    assert "RECENT CONTEXT" in out
    assert "购票人名字" in out
    assert "STORED:" in out


def test_handles_output_without_any_block():
    plain = "RECALL: no relevant memories for this topic."
    assert strip_recent(plain) == plain


def test_block_at_end_of_output_is_removed():
    text = "RECALLED MEMORIES:\n  [#1 | a] x\nRECENT CONTEXT (last ~90 min):\n  [#2 | b] y"
    out = strip_recent(text)
    assert out == "RECALLED MEMORIES:\n  [#1 | a] x", repr(out)


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
