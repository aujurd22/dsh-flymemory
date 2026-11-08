"""redact(): scrub a credential, keep the entry and its audit trail."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
import flymemory.v3 as v3
from flymemory.v3 import SmartMemory

def make(secret_text):
    # The write path refuses credentials, which is why a leaked entry can only
    # exist from before that filter. Disable the filter for the fixture only.
    pats, regex = v3._CRED_PATTERNS, v3._CRED_REGEX
    v3._CRED_PATTERNS, v3._CRED_REGEX = (), ()
    try:
        mem = SmartMemory(n_bits=512, decay_tau=2592000.0)
        result = mem.remember(text=secret_text, source="import")
        return mem, result.get("memory_id")
    finally:
        v3._CRED_PATTERNS, v3._CRED_REGEX = pats, regex

def test_redact_removes_secret_keeps_entry():
    mem, mid = make("部署说明：用 sk-abcdefghijklmnopqrstuvwx1234 调用，别外传")
    ok, msg = mem.redact(mid)
    assert ok, msg
    entry = next(m for m in mem.memories if m.memory_id == mid)
    assert "sk-abcdefghijklmnopqrstuvwx1234" not in entry.text
    assert "[REDACTED]" in entry.text
    assert "部署说明" in entry.text          # 非机密部分保留
    assert any(t.startswith("redacted:") for t in entry.tags)
    assert entry.updated_at is not None

def test_redact_is_idempotent():
    mem, mid = make("token gsk_abcdefghijklmnopqrstuvwx here")
    assert mem.redact(mid)[0] is True
    ok, msg = mem.redact(mid)
    assert ok is False and "nothing" in msg

def test_redact_unknown_id():
    mem, _ = make("普通内容一句")
    ok, msg = mem.redact(999999)
    assert ok is False and "no entry" in msg

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
