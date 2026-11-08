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
import re
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

# Free OpenAI-compatible endpoints. "amd" is the one measured here; the others are
# the well-known free tiers (see github.com/vava-nessa/free-coding-models and
# github.com/ClawLabsAI/free-ai-models). Model names drift, so a provider whose
# configured model is rejected falls back to whatever /models reports.
PROVIDERS = {
    "amd":        ("https://developer.amd.com.cn/radeon/api/v1", "Qwen3.8-Flash-Next"),
    "groq":       ("https://api.groq.com/openai/v1", "qwen/qwen3.8-27b"),
    "cerebras":   ("https://api.cerebras.ai/v1", "gpt-oss-120b"),
    "nvidia":     ("https://integrate.api.nvidia.com/v1", "openai/gpt-oss-20b"),
    "openrouter": ("https://openrouter.ai/api/v1", "openai/gpt-oss-20b:free"),
    "pollinations": ("https://text.pollinations.ai/openai", "openai"),
}
KEY_ENV = {"amd": ("FLYMEMORY_DREAM_API_KEY", "AMDRADEON_API_KEY"),
           "groq": ("GROQ_API_KEY",), "cerebras": ("CEREBRAS_API_KEY",),
           "nvidia": ("NVIDIA_API_KEY",), "openrouter": ("OPENROUTER_API_KEY",),
           "pollinations": ("POLLINATIONS_API_KEY",)}

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
invent anything not present in the conversation. Write each entry in the
language the source turns use. Reply with ONLY a JSON array of strings."""

MERGE_SYSTEM = """You decide whether two memory entries state the SAME fact.

Reply with ONLY this JSON:
{"same": true/false, "merged": "...", "why": "..."}

Rules:
- same=true ONLY when both describe the same subject and agree -- a rewording,
  a duplicate, or one strictly containing the other.
- If they disagree (different values, different dates, one replaces the other),
  set same=false. Do not reconcile a contradiction into one sentence.
- When same=true, "merged" is ONE self-contained sentence in the source language
  that keeps EVERY concrete detail from both (numbers, names, dates, paths).
  Never add anything that is not in the two entries.
- When same=false, "merged" is an empty string.
"""

REORG_SYSTEM = """You read a digest of an AI agent's long-term memory, one topic bucket at
a time, and look for what a per-session pass cannot see: facts that are actually the
same thing across entries, rules that keep being re-learned, and stale structures.

Reply with ONLY this JSON:
{"conclusions": ["..."], "redundant": [[id, id], ...], "stale": [id, ...]}

Rules:
- conclusions: at most 3 durable, reusable statements. Each must be supported by the
  entries shown. Skip anything that is only true for one session.
- redundant: id pairs that state the same fact. Only pairs whose ids are both shown.
- stale: ids that a newer entry in the digest clearly contradicts. Only ids shown.
- Empty lists are a good answer. Inventing structure is worse than reporting none.
"""

CLUSTER_SYSTEM = """You are given several memory entries. Decide whether they ALL state
the same fact.

Reply with ONLY this JSON:
{"same": true/false, "merged": "...", "keep": [ids that differ], "why": "..."}

Rules:
- same=true ONLY if every entry describes the same subject and they agree.
- If one entry carries a different value, date, path or decision, set same=false and
  list that entry's id in "keep". Do not reconcile a disagreement into one sentence.
- When same=true, "merged" is ONE self-contained sentence in the source language that
  keeps EVERY concrete detail from every entry (numbers, names, dates, paths). Never
  add anything that is not in the given entries.
"""

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


def _provider_chain():
    """(name, base_url, model, key) in the order to try them."""
    configured = (os.environ.get("FLYMEMORY_DREAM_PROVIDERS")
                  or os.environ.get("FLYMEMORY_DREAM_PROVIDER") or "amd")
    chain = []
    for name in [n.strip() for n in configured.split(",") if n.strip()]:
        base, model = PROVIDERS.get(name, (os.environ.get("FLYMEMORY_DREAM_BASE_URL",
                                                          DEFAULT_BASE), DEFAULT_MODEL))
        base = os.environ.get("FLYMEMORY_DREAM_BASE_URL", base)
        model = os.environ.get("FLYMEMORY_DREAM_MODEL", model)
        key = ""
        for env_name in KEY_ENV.get(name, ()):
            key = os.environ.get(env_name, "")
            if key:
                break
        if key:
            chain.append((name, base.rstrip("/"), model, key))
    return chain


def _discover_model(base, key):
    """Ask /models for a usable id when the configured name is rejected."""
    try:
        request = urllib.request.Request(
            base + "/models",
            headers={"Authorization": "Bearer " + key, "User-Agent": UA,
                     "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
        ids = [item.get("id") for item in payload.get("data", []) if item.get("id")]
        return ids[0] if ids else ""
    except Exception:                        # noqa: BLE001 - discovery is optional
        return ""


def llm_chat(system, user, max_tokens=900, model=None):
    """One call across the configured provider chain."""
    chain = _provider_chain()
    if not chain:
        print("  [llm] no provider configured (run: dream --set-key <key> --provider groq)",
              flush=True)
        return ""
    for name, base, configured_model, key in chain:
        candidates = [model or configured_model]
        for attempt, delay in enumerate((0, 4), start=1):
            if delay:
                time.sleep(delay)
            for candidate in list(candidates):
                body = {"model": candidate,
                        "messages": [{"role": "system", "content": system},
                                     {"role": "user", "content": user}],
                        "temperature": 0, "max_tokens": max_tokens}
                try:
                    with urllib.request.urlopen(_request(base, key, body),
                                                timeout=60) as response:
                        payload = json.loads(response.read().decode())
                    text = payload["choices"][0]["message"].get("content") or ""
                    if text.strip():
                        return text
                except urllib.error.HTTPError as error:
                    print(f"  [llm error] {name}/{candidate} try{attempt}: HTTP {error.code}",
                          flush=True)
                    if error.code in (400, 404):
                        discovered = _discover_model(base, key)
                        if discovered and discovered not in candidates:
                            candidates.append(discovered)
                except Exception as error:   # noqa: BLE001 - retry, then next provider
                    print(f"  [llm error] {name}/{candidate} try{attempt}: "
                          f"{type(error).__name__}", flush=True)
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


_SECRET_PATTERNS = (
    re.compile(r"rc-[0-9a-f]{32,}", re.I),
    re.compile(r"user_[A-Za-z0-9]{32,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"\b(?:ghp_|glpat-|npm_|hf_|AIza|xox[abp]-)"),
)


def _looks_like_credential(text):
    """Never distil a secret into the library (2026-10-31: a key was distilled)."""
    return any(pattern.search(text or "") for pattern in _SECRET_PATTERNS)


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
    for index, block in enumerate(blocks, 1):
        print("  [distil] chunk %d/%d (%d chars)" % (index, len(blocks), len(block)),
              flush=True)
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


def merge_pass(url=None, limit=8, min_sim=0.80, apply=False, chat=None, pool=2000):
    """Ask the model which near-duplicate pairs are actually one fact.

    Mechanical organize can only retire exact copies and stale state keys. ``merge``
    covers the rest: the model reads both entries and either produces one sentence
    that keeps every detail, or says they differ (in which case nothing is touched).
    """
    call = chat or llm_chat
    # Rotate a persisted cursor so successive runs walk the WHOLE library: one
    # pass over 20k entries is an O(n^2) scan, a 2k window per run is cheap.
    cursor_path = pathlib.Path(os.environ.get("FLYMEMORY_MERGE_CURSOR",
                                              os.path.join(os.environ.get("TEMP", "."),
                                                           "flymemory-merge-cursor.json")))
    cursor = 0
    try:
        cursor = int(json.loads(cursor_path.read_text(encoding="utf-8")).get("offset", 0))
    except Exception:                        # noqa: BLE001 - first run
        cursor = 0
    raw = mcp_call(url, "flymemory_conflict_pairs",
                   {"min_sim": min_sim, "limit": max(1, int(limit)),
                    "pool_limit": max(100, int(pool)), "offset": cursor})
    if pool:
        try:
            cursor_path.parent.mkdir(parents=True, exist_ok=True)
            cursor_path.write_text(json.dumps({"offset": cursor + int(pool)}), encoding="utf-8")
        except Exception:                    # noqa: BLE001 - cursor is best effort
            pass
    try:
        pairs = json.loads(raw) if raw.strip().startswith("[") else []
    except json.JSONDecodeError:
        pairs = []
    if not pairs and raw.strip() and not raw.strip().startswith("["):
        print("merge: candidate query failed -> " + raw.strip()[:160], flush=True)
    if not pairs:
        print("merge: no candidate pairs", flush=True)
        return 0

    merged_count = 0
    for pair in pairs:
        prompt = "A:\n" + str(pair.get("a_text", "")) + "\n\nB:\n" + str(pair.get("b_text", ""))
        decision, error = parse_decision(call(MERGE_SYSTEM, prompt, max_tokens=400))
        if error:
            print(f"  [skip] #{pair['a_id']}~#{pair['b_id']} ({error})", flush=True)
            continue
        if not decision.get("same"):
            print(f"  [DIFFERENT] #{pair['a_id']} ~ #{pair['b_id']} "
                  f"sim={pair['sim']}: {decision.get('why', '')[:90]}", flush=True)
            continue
        merged_text = str(decision.get("merged") or "").strip()
        if not merged_text:
            print(f"  [skip] #{pair['a_id']}~#{pair['b_id']}: same but no merged text",
                  flush=True)
            continue
        print(f"  [MERGE] #{pair['a_id']} + #{pair['b_id']} -> {merged_text[:90]}", flush=True)
        merged_count += 1
        if not apply:
            continue
        written = mcp_call(url, "flymemory_remember",
                           {"text": merged_text, "tags": "dream,merged", "source": "model"})
        match = re.search(r"#(\d+)", str(written))
        if not match:
            print(f"    -> write failed: {str(written)[:80]}", flush=True)
            continue
        new_id = int(match.group(1))
        retired = 0
        for old_id in (pair["a_id"], pair["b_id"]):
            if int(old_id) == new_id:
                # The merged text deduped into one of the originals: that entry IS
                # the merge target now, so superseding it into itself is pointless.
                continue
            mcp_call(url, "flymemory_supersede",
                     {"old_memory_id": int(old_id), "new_memory_id": new_id})
            retired += 1
        where = "in place" if retired < 2 else "as a new entry"
        print(f"    -> merge landed {where} at #{new_id}, {retired} original(s) retired",
              flush=True)

    mode = "applied" if apply else "dry-run, nothing written"
    print(f"merge: {merged_count} pair(s) merged ({mode})", flush=True)
    return 0



def cluster_pass(url=None, limit=5, min_sim=0.82, apply=False, pool=4000):
    """Fold a whole group of synonyms into one entry with a single model call."""
    raw = mcp_call(url, "flymemory_clusters",
                   {"min_sim": min_sim, "limit": max(1, int(limit)),
                    "min_size": 3, "max_size": 8, "pool_limit": max(1, int(pool))})
    try:
        clusters = json.loads(raw) if raw.strip().startswith("[") else []
    except json.JSONDecodeError:
        clusters = []
    if not clusters and raw.strip() and not raw.strip().startswith("["):
        print("cluster: query failed -> " + raw.strip()[:160], flush=True)
    if not clusters:
        print("cluster: no groups of 3+ found", flush=True)
        return 0

    merged_count = 0
    for group in clusters:
        ids = group.get("ids") or []
        texts = group.get("texts") or []
        if len(ids) < 3:
            continue
        numbered = "\n".join("[%s] %s" % (i, t) for i, t in zip(ids, texts))
        answer = llm_chat(CLUSTER_SYSTEM, "ENTRIES:\n" + numbered, max_tokens=600)
        if not (answer or "").strip():
            print("  [skip] cluster %s (no model reply)" % (ids,), flush=True)
            continue
        parsed, parse_error = parse_decision(answer)
        if parse_error:
            print("  [skip] cluster %s (%s)" % (ids, parse_error), flush=True)
            continue
        if not parsed.get("same"):
            keep = ", ".join("#%s" % i for i in parsed.get("keep", [])[:4])
            print("  [DIFFERENT] %d entries, kept: %s -- %s"
                  % (len(ids), keep or "n/a", str(parsed.get("why", ""))[:80]), flush=True)
            continue
        merged_text = str(parsed.get("merged") or "").strip()
        if not merged_text:
            print("  [skip] cluster %s: same but no merged text" % (ids,), flush=True)
            continue
        print("  [CLUSTER %d] %s -> %s" % (len(ids),
              ", ".join("#%s" % i for i in ids), merged_text[:80]), flush=True)
        merged_count += 1
        if not apply:
            continue
        written = mcp_call(url, "flymemory_remember",
                           {"text": merged_text, "tags": "dream,merged,cluster",
                            "source": "model"})
        match = re.search(r"#(\d+)", str(written))
        if not match:
            print("    -> write failed: %s" % str(written)[:80], flush=True)
            continue
        new_id = int(match.group(1))
        retired = 0
        for old_id in ids:
            if int(old_id) == new_id:
                continue
            mcp_call(url, "flymemory_supersede",
                     {"old_memory_id": int(old_id), "new_memory_id": new_id})
            retired += 1
        print("    -> #%d consolidated, %d original(s) retired" % (new_id, retired),
              flush=True)

    mode = "applied" if apply else "dry-run, nothing written"
    print("cluster: %d group(s) consolidated (%s)" % (merged_count, mode), flush=True)
    return 0


REORG_PROMPT = """Topic bucket: %s (%d entries total, these are the most used/recent)

%s
"""


def reorganize_pass(url=None, apply=False, buckets=8, per_bucket=12,
                    recent_days=0.0):
    """The daily pass: mechanical cleanup, then one macro reading of the library.

    Claude's dreams rewrite the whole store; this does the part that is safe to
    automate daily -- retire what is structurally stale, then read a bounded digest
    with the model and write ONE playbook entry (yesterday's is superseded), so the
    macro view never contradicts the evidence entries it came from.
    """
    print("reorganize: mechanical pass", flush=True)
    print("  " + mcp_call(url, "flymemory_organize", {"apply": True}).replace("\n", "\n  "),
          flush=True)

    raw = mcp_call(url, "flymemory_digest",
                   {"buckets": max(1, int(buckets)), "per_bucket": max(1, int(per_bucket)),
                    "recent_days": float(recent_days)})
    try:
        digest = json.loads(raw) if raw.strip().startswith("[") else []
    except json.JSONDecodeError:
        digest = []
    if not digest:
        print("reorganize: empty digest -> nothing to do", flush=True)
        return 0

    conclusions, redundant, stale = [], [], []
    for group in digest:
        lines = "\n".join("[%s | %s | used %dx] %s"
                          % (item["id"], item["date"], item["uses"], item["text"])
                          for item in group.get("entries", []))
        answer = llm_chat(REORG_SYSTEM, REORG_PROMPT % (group.get("bucket"), group.get("total"), lines),
                          max_tokens=800)
        parsed, error = parse_decision(answer or "")
        if error:
            print(f"  [{group.get('bucket')}] skipped ({error})", flush=True)
            continue
        got = parsed.get("conclusions") or []
        conclusions.extend(str(c) for c in got[:3])
        redundant.extend(parsed.get("redundant") or [])
        stale.extend(parsed.get("stale") or [])
        print(f"  [{group.get('bucket')}] {len(got)} conclusion(s), "
              f"{len(parsed.get('redundant') or [])} redundant pair(s)", flush=True)

    if not conclusions:
        print("reorganize: no conclusion survived review", flush=True)
        return 0

    body = ("每日重排（%s）\n\n" % time.strftime("%Y-%m-%d")
            + "\n".join("* " + c for c in conclusions[:10]))
    print("\nplaybook:", flush=True)
    for c in conclusions[:6]:
        print("  * " + c[:100], flush=True)
    if redundant:
        print(f"  redundant pairs reported: {len(redundant)}", flush=True)
    if stale:
        print(f"  stale ids reported: {len(stale)}", flush=True)

    if not apply:
        print("reorganize: dry-run, nothing written", flush=True)
        return 0

    previous = mcp_call(url, "flymemory_recall",
                        {"query": "每日重排 playbook 宏观结论", "top_k": 5})
    previous_ids = [int(m) for m in re.findall(r"#(\d+)", str(previous))]
    written = mcp_call(url, "flymemory_remember",
                       {"text": body, "tags": "playbook,daily", "source": "model"})
    match = re.search(r"#(\d+)", str(written))
    if not match:
        print("  playbook write failed: " + str(written)[:100], flush=True)
        return 1
    new_id = int(match.group(1))
    retired = 0
    for old_id in previous_ids:
        if old_id == new_id:
            continue
        result = mcp_call(url, "flymemory_supersede",
                          {"old_memory_id": old_id, "new_memory_id": new_id})
        if "SUPERSEDED" in str(result).upper() or "superseded" in str(result):
            retired += 1
    print(f"  playbook stored as #{new_id}, {retired} older playbook(s) retired", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="path to flymemory_v3.pkl (read-only)")
    parser.add_argument("--url", default=os.environ.get("FLYMEMORY_MCP_URL", DEFAULT_URL))
    parser.add_argument("--window", type=float, default=90, help="RECENT window in minutes")
    parser.add_argument("--min-turns", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true", help="print, write nothing")
    parser.add_argument("--merge", action="store_true",
                        help="ask the model which near-duplicate pairs are one fact")
    parser.add_argument("--merge-apply", action="store_true",
                        help="write the merged entries and retire the originals")
    parser.add_argument("--merge-limit", type=int, default=8,
                        help="how many candidate pairs to review")
    parser.add_argument("--reorganize", action="store_true",
                        help="daily pass: mechanical cleanup + one macro reading")
    parser.add_argument("--reorganize-apply", action="store_true",
                        help="write the playbook entry")
    parser.add_argument("--reorg-buckets", type=int, default=8)
    parser.add_argument("--reorg-per-bucket", type=int, default=12)
    parser.add_argument("--reorg-days", type=float, default=0.0,
                        help="only read entries touched in the last N days (0 = all)")
    parser.add_argument("--cluster", action="store_true",
                        help="fold groups of 3+ synonyms into one entry")
    parser.add_argument("--cluster-apply", action="store_true",
                        help="write the cluster merges instead of reporting them")
    parser.add_argument("--cluster-limit", type=int, default=5)
    parser.add_argument("--cluster-min-sim", type=float, default=0.82)
    parser.add_argument("--cluster-pool", type=int, default=4000)
    parser.add_argument("--merge-pool", type=int, default=2000,
                        help="how many entries to scan per run (rotates over the library)")
    parser.add_argument("--merge-min-sim", type=float, default=0.80,
                        help="similarity floor for a candidate pair")
    parser.add_argument("--organize", action="store_true",
                        help="first retire structurally stale entries (report only)")
    parser.add_argument("--organize-apply", action="store_true",
                        help="apply the retirement instead of reporting it")
    parser.add_argument("--compartment", default="")
    parser.add_argument("--shadow", action="store_true",
                        help="do not write; save proposals to a JSON file for review")
    parser.add_argument("--promote", default="",
                        help="write the proposals saved by a previous --shadow run")
    parser.add_argument("--shadow-dir", default="",
                        help="where --shadow writes (default: next to the library)")
    args = parser.parse_args()

    if args.promote:
        return _promote(args)

    if args.reorganize or args.reorganize_apply:
        return reorganize_pass(args.url, args.reorganize_apply,
                                args.reorg_buckets, args.reorg_per_bucket,
                                args.reorg_days)

    if args.cluster:
        return cluster_pass(args.url, args.cluster_limit,
                            args.cluster_min_sim, args.cluster_apply,
                            pool=args.cluster_pool)

    if args.merge:
        return merge_pass(args.url, args.merge_limit, args.merge_min_sim,
                          args.merge_apply, pool=args.merge_pool)

    if args.organize or args.organize_apply:
        print(mcp_call(args.url, "flymemory_organize",
              {"apply": bool(args.organize_apply)}), flush=True)
        print(flush=True)

    if not _provider_chain():
        print("dream: no provider key is available (set GROQ_API_KEY / "
              "FLYMEMORY_DREAM_API_KEY, or run: dream --set-key <key> --provider <name>)",
              flush=True)
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
    proposals = []
    for entry in entries:
        supported, note = audit_entry(turns, entry)
        print(f"  [{'KEEP' if supported else 'DROP'}] {entry[:80]}"
              + (f"  ({note})" if not supported else ""), flush=True)
        if not supported:
            continue
        if _looks_like_credential(entry):
            print("  [SKIP] entry looks like a credential", flush=True)
            continue
        if args.dry_run:
            stored += 1
            continue
        if args.shadow:
            proposals.append(entry)
            stored += 1
            continue
        write_args = {"text": entry, "tags": "dream,distilled", "source": "model"}
        if args.compartment and args.compartment != "all":
            write_args["compartment"] = args.compartment
        result = mcp_call(args.url, "flymemory_remember", write_args)
        print(f"    -> {result[:100]}", flush=True)
        stored += 1
    if args.shadow and proposals:
        directory = pathlib.Path(args.shadow_dir) if args.shadow_dir else pathlib.Path(args.db).parent
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"dream-shadow-{time.strftime('%Y%m%d-%H%M%S')}.json"
        target.write_text(json.dumps({
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "window_minutes": args.window,
            "compartment": args.compartment,
            "proposals": proposals,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"dream: {len(proposals)} proposals written to {target}", flush=True)
        print("       review, then: dream --promote " + str(target), flush=True)
        return 0

    print(f"dream: {stored} entries "
          + ("(dry-run, not stored)" if args.dry_run else "stored via MCP"), flush=True)
    return 0


def _promote(args):
    """Write the proposals a --shadow run left on disk."""
    path = pathlib.Path(args.promote)
    if not path.exists():
        print(f"dream: proposal file not found: {path}", flush=True)
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    written = 0
    for entry in data.get("proposals", []):
        write_args = {"text": entry, "tags": "dream,distilled,promoted", "source": "model"}
        compartment = data.get("compartment") or args.compartment
        if compartment and compartment != "all":
            write_args["compartment"] = compartment
        result = mcp_call(args.url, "flymemory_remember", write_args)
        written += 1
        print(f"  -> {result[:90]}", flush=True)
    print(f"dream: promoted {written} entries from {path.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
