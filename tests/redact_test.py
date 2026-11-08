"""redact(): scrub a credential, keep the entry and its audit trail.

Hermetic on purpose: entries are constructed directly instead of going through
`remember()`, so this test needs neither the embedding model nor the write-path
credential filter (which is exactly what would refuse the fixture).
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
from flymemory.v3 import MemoryEntry, SmartMemory  # noqa: E402

SECRET = "sk-FAKEredactfixture000000000000"
GROQ_SECRET = "gsk_FAKEredactfixture0000000000"


def make(text, memory_id=1):
    memory = SmartMemory(n_bits=512, decay_tau=2592000.0)
    memory.memories.append(MemoryEntry(
        text=text,
        response="",
        embedding=None,          # redact re-embeds; the CI job has no numpy/torch
        timestamp=time.time(),
        last_accessed=0.0,
        access_count=0,
        tags=["app:test"],
        memory_id=memory_id,
        source="import",
    ))
    return memory


def entry_of(memory, memory_id=1):
    return next(m for m in memory.memories if m.memory_id == memory_id)


def test_removes_secret_keeps_entry():
    memory = make("部署说明：用 " + SECRET + " 调用，别外传")
    ok, message = memory.redact(1)
    assert ok, message
    entry = entry_of(memory)
    assert SECRET not in entry.text
    assert "[REDACTED]" in entry.text
    assert "部署说明" in entry.text            # 非机密部分保留
    assert entry.updated_at is not None


def test_marks_the_redaction_in_tags():
    memory = make("token " + GROQ_SECRET + " here")
    assert memory.redact(1)[0] is True
    assert any(tag.startswith("redacted:") for tag in entry_of(memory).tags)


def test_is_idempotent():
    memory = make("token " + GROQ_SECRET + " here")
    assert memory.redact(1)[0] is True
    ok, message = memory.redact(1)
    assert ok is False and "nothing" in message


def test_rejects_unknown_id():
    memory = make("普通内容一句")
    ok, message = memory.redact(999999)
    assert ok is False and "no entry" in message


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
