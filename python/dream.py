#!/usr/bin/env python3
"""Idle-time consolidation ("dreaming") for the dsh-flymemory plugin.

Adapted from the upstream `flymemory/dream.py` (same distillation and audit
prompts, same overlay-by-write semantics). Local changes:

* configuration comes from the environment only -- no machine-local key file:
  `FLYMEMORY_DREAM_API_KEY` (or `AMDRADEON_API_KEY`), `FLYMEMORY_DREAM_BASE_URL`
  (default the AMD Radeon endpoint), `FLYMEMORY_DREAM_MODEL` (default
  Qwen3.8-Flash-Next, which was the least loaded capable model there on
  2026-10-31);
* the LLM call is plain urllib against an OpenAI-compatible endpoint, so no SDK
  is required;
* the service endpoint and the library path are parameters (`--url`, `--db`),
  supplied by the CLI from the plugin's own resolved options.

The store is only ever READ here; writes go through the running service's
`flymemory_remember` (the service owns the authoritative in-memory copy and would
overwrite a direct pkl write on its next save).

Safety gate kept from upstream: every distilled entry is judged against the
source turns, and unsupported entries are dropped rather than stored.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

DEFAULT_BASE = "https://developer.amd.com.cn/radeon/api/v1"
DEFAULT_MODEL = "Qwen3.8-Flash-Next"
# Measured on this endpoint 2026-10-31: Qwen3.8-Flash-Next returns clean JSON in
# ~13s; GLM-5.3-Flash works at ~27s; Qwen3.8-27B intermittently returns empty
# content; MiniCPM models wrap JSON in ``` fences or use CJK quotes; MiMo takes
# 61s; DeepSeek-V4.1-Flash answers 429 under load. Order reflects that.
FALLBACK_MODELS = ("GLM-5.3-Flash", "DeepSeek-V4-Flash")
DEFAULT_URL = "http://127.0.0.1:8791/mcp"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

DISTILL_SYSTEM = """You are the memory-consolidation module of a personal AI
assistant. Given a recent conversation window, distill the DURABLE facts and
decisions worth keeping long-term: preferences, numbers, names, dates,
plans, outcomes, corrections. Write 1-6 short self-contained entries. Do NOT
invent anything not present in the conversation. Reply with ONLY a JSON
array of strings."""

AUDIT_SYSTEM = """You are a strict factuality judge. Given SOURCE TURNS and a
distilled MEMORY ENTRY, decide whether every claim in the entry is supported
by the source turns. Reply with ONLY:
{"supported": true/false, "unsupported_claims": ["..."]}"""


def api_key():
    return (os.environ.get("FLYMEMORY_DREAM_API_KEY")
            or os.environ.get("AMDRADEON_API_KEY") or "")


def _request(base, key, body):
    return urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": "Bearer " + key, "User-Agent": UA})


def _clean_json_text(text):
    """Strip code fences and normalise CJK quotes (some models emit them)."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    return text.strip()


def llm_chat(system, user, max_tokens=900, model=None):
    """One OpenAI-compatible chat call; returns text ('' when nothing usable)."""
    key = api_key()
    if not key:
        return ""
    base = os.environ.get("FLYMEMORY_DREAM_BASE_URL", DEFAULT_BASE).rstrip("/")
    chosen = model or os.environ.get("FLYMEMORY_DREAM_MODEL", DEFAULT_MODEL)
    # The free endpoint returns 502 under load, so a chain with a retry each is the
    # difference between "dream does something" and "dream silently does nothing".
    chain = [chosen] + [m for m in FALLBACK_MODELS if m != chosen]

    for candidate in chain:
        for attempt, delay in enumerate((0, 3, 8), start=1):
            if delay:
                time.sleep(delay)
            body = {"model": candidate,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                    "temperature": 0, "max_tokens": max_tokens}
            try:
                with urllib.request.urlopen(_request(base, key, body), timeout=180) as response:
                    payload = json.loads(response.read().decode())
                text = payload["choices"][0]["message"].get("content") or ""
                if text.strip():
                    return text
            except Exception as error:       # noqa: BLE001 - next attempt / next model
                print(f"  [llm error] {candidate} try{attempt}: "
                      f"{type(error).__name__}: {str(error)[:120]}", flush=True)
                time.sleep(2)
    return ""


def mcp_call(url, name, arguments, timeout=60):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}})
    request = urllib.request.Request(
        url, data=body.encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8", "replace")
    for line in payload.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            break
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return "mcp_error: unparseable response"
    content = parsed.get("result", {}).get("content", [])
    return content[0].get("text", "") if content else json.dumps(parsed)[:200]


def parse_array(raw):
    """Extract a JSON string array from raw output, with truncation repair."""
    start = raw.find("[")
    if start < 0:
        return None, "no JSON array in output"
    body = raw[start:]
    candidates = [body]
    quote = body.rfind('"')
    while quote > 0:
        candidates.append(body[:quote + 1] + "]")
        quote = body.rfind('"', 0, quote)
    last_error = None
    for candidate in candidates:
        try:
            array = json.loads(candidate)
            if isinstance(array, list):
                return [str(item).strip() for item in array if str(item).strip()], None
        except json.JSONDecodeError as error:
            last_error = f"@{error.pos}: {error.msg}"
    return None, last_error or "unparseable"


def parse_decision(raw):
    """Pull the first JSON object out of the auditor's reply."""
    start = raw.find("{")
    if start < 0:
        return {}, "no JSON object"
    body = raw[start:]
    for end in range(len(body), 0, -1):
        if body[end - 1] != "}":
            continue
        try:
            return json.loads(body[:end]), None
        except json.JSONDecodeError:
            continue
    return {}, "unparseable"


def distill(turns, chunk_chars=3000):
    """Distil the window in small chunks: big single requests are what 502'd."""
    blocks, current, size = [], [], 0
    for turn in turns:
        if size + len(turn) > chunk_chars and current:
            blocks.append("\n".join(current))
            current, size = [], 0
        current.append(turn)
        size += len(turn) + 1
    if current:
        blocks.append("\n".join(current))

    entries, errors = [], []
    for block in blocks:
        raw = llm_chat(DISTILL_SYSTEM, block[:chunk_chars])
        chunk_entries, error = parse_array(_clean_json_text(raw))
        if error:
            errors.append(error)
        entries.extend(chunk_entries or [])
        if len(entries) >= 6:
            break
    if not entries:
        return [], (errors[0] if errors else "no entries")
    return entries[:6], None


def audit_entry(source_turns, entry):
    joined = "\n".join(source_turns)[:6000]
    prompt = f"SOURCE TURNS:\n{joined}\n\nENTRY:\n{entry}\n\nJudge now."
    decision, error = parse_decision(llm_chat(AUDIT_SYSTEM, prompt, max_tokens=300))
    if error:
        return False, f"audit error: {error}"
    return bool(decision.get("supported")), "; ".join(decision.get("unsupported_claims", [])[:2])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="path to flymemory_v3.pkl (read-only)")
    parser.add_argument("--url", default=os.environ.get("FLYMEMORY_MCP_URL", DEFAULT_URL))
    parser.add_argument("--window", type=float, default=90, help="RECENT window in minutes")
    parser.add_argument("--min-turns", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true", help="print, write nothing")
    parser.add_argument("--compartment", default="")
    args = parser.parse_args()

    if not api_key():
        print("dream: FLYMEMORY_DREAM_API_KEY is not set -- nothing to do", flush=True)
        return 0
    if not os.path.exists(args.db):
        print(f"dream: library not found: {args.db}", flush=True)
        return 1

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from flymemory.v3 import load

    mem = load(args.db, enable_hopfield=False)
    now = time.time()
    window_start = now - args.window * 60
    recent = [m for m in mem.memories
              if m.timestamp >= window_start and m.superseded_by is None]
    comp_tag = (f"comp:{args.compartment}"
                if args.compartment and args.compartment != "all" else None)
    if comp_tag:
        recent = [m for m in recent
                  if not any(t.startswith("comp:") and t != comp_tag for t in m.tags)]
    if len(recent) < args.min_turns:
        print(f"dream: only {len(recent)} turns in the last {args.window:.0f} min "
              f"(min {args.min_turns}) -- skipping", flush=True)
        return 0
    recent.sort(key=lambda m: m.timestamp)
    turns = [f"{m.source}: {m.text}" for m in recent]
    span = (now - recent[0].timestamp) / 60
    print(f"dream: window has {len(recent)} turns ({span:.0f} min ago .. now)", flush=True)

    entries, error = distill(turns)
    if error:
        print(f"  [distill error] {error}", flush=True)
    if not entries:
        print("dream: distillation produced nothing -- skipping", flush=True)
        return 0

    stored = 0
    for entry in entries:
        supported, note = audit_entry(turns, entry)
        print(f"  [{'KEEP' if supported else 'DROP'}] {entry[:80]}"
              + (f"  ({note})" if not supported else ""), flush=True)
        if not supported:
            continue
        if args.dry_run:
            stored += 1
            continue
        write_args = {"text": entry, "tags": "dream,distilled", "source": "model"}
        if args.compartment and args.compartment != "all":
            write_args["compartment"] = args.compartment
        result = mcp_call(args.url, "flymemory_remember", write_args)
        print(f"    -> {result[:100]}", flush=True)
        stored += 1
    print(f"dream: {stored} entries "
          + ("(dry-run, not stored)" if args.dry_run else "stored via MCP"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
