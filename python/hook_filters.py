"""Prompt filters for the capture/recall hook (dsh-flymemory adaptation).

Upstream's hook injects two blocks that this plugin drops before they reach the
prompt:

* ``RECENT CONTEXT (last ~N min)`` — a clock window over the **whole** library.
  Entries carry no session id, so when several conversations share one store the
  block pulls another conversation's work into this one (measured 2026-10-29:
  a computer-use session saw a commerce session's trail).
* ``STORED: [MERGED #123 ...]`` — the write receipt for the message being
  captured right now. It describes the store, it is not remembered content.

Set ``FLYMEMORY_RECENT_MINUTES=90`` to keep the first block (upstream behaviour).
"""

import os
import re


def strip_recent(text: str) -> str:
    """Drop the store-wide RECENT block and write receipts from recall output."""
    if os.environ.get("FLYMEMORY_RECENT_MINUTES"):
        return text
    kept = []
    skipping = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("RECENT CONTEXT"):
            skipping = True
            continue
        if skipping:
            # The block's body is indented; a blank or unindented line ends it.
            if not stripped or line.startswith((" ", "\t")):
                continue
            skipping = False
        if stripped.startswith("STORED:"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def high_sim_ids(text, limit=2, threshold=0.6):
    """Ids of the closest high-similarity hits in a recall block.

    Recall lines look like `[#19687 | 1.00 | app=dsh | age] summary`. Those with
    similarity at or above `threshold` are the ones worth inlining as full text
    (measured 2026-10-30: pointers alone underperform full text).
    """
    ids = []
    for line in (text or "").splitlines():
        match = re.match(r"\s*\[#(\d+)\s*\|\s*([0-9.]+)", line)
        if match and float(match.group(2)) >= threshold:
            ids.append(int(match.group(1)))
        if len(ids) >= limit:
            break
    return ids