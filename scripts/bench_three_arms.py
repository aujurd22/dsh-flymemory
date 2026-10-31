#!/usr/bin/env python3
"""Four-arm memory benchmark: NoMemory / RAG-top5 / FlyMemory / FlyMemory-hybrid.

Answers the questions docs/ROADMAP.md leaves open, with the reader held fixed:

  * does the memory store beat no memory at all?
  * does it beat plain top-5 RAG on the same turns?
  * does the pointer/agentic protocol (how the plugin actually works) beat
    stuffing full text into the prompt?
  * does injecting full text for the closest one or two hits make the one-shot
    case competitive? (the `hybrid` arm -- the shape the hook now uses)

Fixed on purpose:

  * reader and judge are one model with `reasoning_effort=off`, temperature 0
  * a fresh store per question, built from that question's haystack, so no arm
    sees more than another
  * every answer is recorded per question and per question type, so comparisons
    can be tested (exact McNemar) instead of eyeballed

Usage:
  CMDBASE=https://api.commandcode.ai/provider/v1 \
  CMMODEL=deepseek/deepseek-v4.1-flash CMKEY=... \
  python scripts/bench_three_arms.py --sample 200 --out reports/three_arms.json
"""
import argparse
import datetime
import json
import math
import os
import random
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "python"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from flymemory.v3 import SmartMemory  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

JUDGE_SYSTEM = """You are grading an answer against the ground truth. Reply with ONLY:
{"score": "correct|partial|wrong", "note": "one short sentence"}
- correct: the answer conveys the same key fact(s) as the ground truth
- partial: some key facts right, some missing or imprecise
- wrong: contradicts the ground truth or is off topic"""

ANSWER_SYSTEM = """You answer questions about your owner's past conversations using the
memory context provided. If the context does not contain the answer, say you do not
know -- do not guess. Answer with the fact only, in one short sentence."""

AGENTIC_SYSTEM = """You answer questions about your owner's past conversations. You have two
tools:

  search_memory(query)  -> returns pointers: [#id | date | one-line summary]
  read_memory(id)       -> returns the full text of that entry

Call them as many times as you need (at most %d rounds), then answer with the fact
only, in one short sentence. If the memory does not contain it, say you do not know."""


# ---------------------------------------------------------------- model client
class Model:
    def __init__(self, base_url, key, model):
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.model = model
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    def chat(self, messages, max_tokens=300, tools=None):
        message = self.chat_message(messages, max_tokens=max_tokens, tools=tools)
        return (message.get("content") or "").strip()

    def chat_message(self, messages, max_tokens=300, tools=None):
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            "reasoning_effort": "off",   # thinking off: no reasoning tokens
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Authorization": "Bearer " + self.key, "User-Agent": UA},
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=300) as response:
                    data = json.loads(response.read().decode())
                usage = data.get("usage", {})
                self.prompt_tokens += usage.get("prompt_tokens", 0)
                self.completion_tokens += usage.get("completion_tokens", 0)
                self.calls += 1
                return data["choices"][0]["message"]
            except Exception as error:              # noqa: BLE001
                if attempt == 2:
                    return {"content": f"[model error: {error}]"}
                time.sleep(3 * (attempt + 1))


# ---------------------------------------------------------------- store build
def parse_date(text):
    match = re.match(r"(\d{4})/(\d{2})/(\d{2})", text or "")
    if not match:
        return time.time()
    year, month, day = (int(part) for part in match.groups())
    return datetime.datetime(year, month, day).timestamp()


def build_store(record, granularity="sentence"):
    """Ingest one question's haystack at a chosen chunk granularity.

    "sentence" is what the engine does today (split_chunks: one sentence per
    entry). "turn" stores one entry per message, "session" one per session. The
    Cartesia H-Net result and our own P-DIACT null both point at ingestion
    granularity being a real variable, so measure it instead of assuming.
    """
    from flymemory.v3 import split_chunks
    memory = SmartMemory(n_bits=4096, decay_tau=2592000.0)
    for date_text, session in zip(record.get("haystack_dates", []),
                                  record.get("haystack_sessions", [])):
        stamp = parse_date(date_text)
        if granularity == "session":
            joined = "\n".join(f"{t.get('role') or 'user'}: {(t.get('content') or '').strip()}"
                               for t in session if (t.get("content") or "").strip())
            if joined.strip():
                memory.remember(text=f"[{date_text}] {joined}", source="import",
                                timestamp=stamp)
            continue
        for turn in session:
            role = turn.get("role") or "user"
            content = (turn.get("content") or "").strip()
            if not content:
                continue
            if granularity == "turn":
                memory.remember(text=f"[{date_text}] {role}: {content}",
                                source="import", timestamp=stamp)
                continue
            for chunk in split_chunks(f"[{date_text}] {role}: {content}"):
                memory.remember(text=chunk, source="import", timestamp=stamp)
    return memory


def entry_by_id(memory, memory_id):
    return next((m for m in memory.memories if m.memory_id == memory_id), None)


def pointer_lines(hits):
    lines = []
    for hit in hits:
        stamp = time.strftime("%Y-%m-%d", time.localtime(hit.timestamp))
        lines.append(f"[#{hit.memory_id} | {stamp} | {hit.source}] {hit.text[:110]}")
    return "\n".join(lines)


def retrieve(memory, question, top_k=8):
    return [(entry, sim) for entry, sim, _score in memory.recall(question, top_k=top_k)]


# ---------------------------------------------------------------- arms
def ask_with_context(model, question, record, context):
    return model.chat([
        {"role": "system", "content": ANSWER_SYSTEM},
        {"role": "user", "content": f"Question date: {record['question_date']}\n"
                                    f"Memory context:\n{context}\n\nQuestion: {question}"},
    ])


def run_nomem(question, record, model):
    return ask_with_context(model, question, record, "(no memory available)"), 0


def run_rag(question, record, model, memory, topk=5):
    hits = retrieve(memory, question, topk)
    context = "\n\n".join(entry.text for entry, _sim in hits) or "(nothing retrieved)"
    return ask_with_context(model, question, record, context), 0


_MEM0 = {"instances": {}, "dir": None}


def mem0_instance(use_llm=False):
    """A local mem0 store. `use_llm=True` runs mem0's real pipeline (fact extraction
    per message batch) through the local UA proxy; False is its raw-store mode.

    The distinction matters: mem0's published numbers come from infer=True, but at
    500 questions that mode costs an LLM call per message batch per question.
    """
    key = "llm" if use_llm else "raw"
    if _MEM0["instances"].get(key) is not None:
        return _MEM0["instances"][key]
    import tempfile as _tempfile
    os.environ.setdefault("OPENAI_API_KEY", "sk-placeholder-never-called")
    from mem0 import Memory
    if _MEM0["dir"] is None:
        _MEM0["dir"] = _tempfile.mkdtemp(prefix="mem0-lme-")
    config = {
        "vector_store": {"provider": "qdrant",
                         "config": {"path": _MEM0["dir"],
                                    "collection_name": "lme_" + key,
                                    "on_disk": True,
                                    "embedding_model_dims": 384}},
        "embedder": {"provider": "huggingface",
                     "config": {"model": os.environ.get(
                         "MEM0_EMBEDDER",
                         "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")}},
    }
    if use_llm:
        config["llm"] = {"provider": "openai",
                         "config": {"model": os.environ.get("CMMODEL",
                                                            "deepseek/deepseek-v4.1-flash"),
                                    "api_key": os.environ.get("CMKEY") or "sk-via-proxy",
                                    "openai_base_url": "http://127.0.0.1:8799/v1"}}
    _MEM0["instances"][key] = Memory.from_config(config)
    return _MEM0["instances"][key]


def _mem0_turns(record):
    turns = []
    for date_text, session in zip(record.get("haystack_dates", []),
                                  record.get("haystack_sessions", [])):
        for turn in session:
            content = (turn.get("content") or "").strip()
            if content:
                turns.append({"role": turn.get("role") or "user",
                              "content": f"[{date_text}] {content}"})
    return turns


def run_mem0_infer(question, record, model, memory, limit=5):
    """mem0 with its default pipeline: an LLM extracts facts from each batch."""
    client = mem0_instance(use_llm=True)
    user_id = "inf-" + str(record.get("question_id"))
    try:
        client.add(_mem0_turns(record), user_id=user_id)   # infer=True by default
        hits = client.search(question, filters={"user_id": user_id}, top_k=limit)
    except Exception as error:                             # noqa: BLE001
        return f"[mem0 infer error: {error}]", 0
    results = hits.get("results", hits) if isinstance(hits, dict) else hits
    texts = [hit.get("memory") or hit.get("text") or "" for hit in results]
    context = "\n\n".join(t for t in texts if t) or "(nothing retrieved)"
    return ask_with_context(model, question, record, context), 0


def run_mem0(question, record, model, memory, limit=5):
    """The real thing, same reader and judge: mem0's own store and search."""
    client = mem0_instance(use_llm=False)
    user_id = str(record.get("question_id"))
    try:
        client.add(_mem0_turns(record), user_id=user_id, infer=False)
        hits = client.search(question, filters={"user_id": user_id}, top_k=limit)
    except Exception as error:                      # noqa: BLE001
        return f"[mem0 error: {error}]", 0
    results = hits.get("results", hits) if isinstance(hits, dict) else hits
    texts = [hit.get("memory") or hit.get("text") or "" for hit in results]
    context = "\n\n".join(t for t in texts if t) or "(nothing retrieved)"
    return ask_with_context(model, question, record, context), 0


MULTI_HOP_TYPES = ("temporal-reasoning", "multi-session")


def run_router(question, record, model, memory, budget_chars=6000):
    """Route by question shape: multi-hop types get the tool protocol, the rest get
    one budgeted block.

    This is the *ceiling* of the routing idea -- it reads `question_type` from the
    dataset, which a real router must infer from the text. Measure the ceiling
    first; build the classifier only if it is worth it.
    """
    if record.get("question_type") in MULTI_HOP_TYPES:
        return run_fly_agentic(question, record, model, memory, max_rounds=3)
    return run_budget(question, record, model, memory, budget_chars=budget_chars)


def run_extract(question, record, model, memory, budget_chars=6000,
                per_entry_chars=600, topk=25):
    """Answer-side aggregation: retrieve once, extract facts verbatim, then answer.

    Upstream's P-ANSWER measured 6% -> 16% strict from this two-step protocol; the
    failure it targets is counting/aggregation over scattered evidence, which no
    retrieval change fixes.
    """
    from recall_block import pack
    hits = memory.recall(question, top_k=topk)
    entries = []
    for entry, sim, _score in hits:
        stamp = time.strftime("%Y-%m-%d", time.localtime(entry.timestamp))
        entries.append((f"[#{entry.memory_id} | {sim:.2f} | {stamp}]", entry.text))
    block, _stats = pack(entries, budget_chars=budget_chars,
                         per_entry_chars=per_entry_chars)
    facts = model.chat([
        {"role": "system", "content":
            "Extract every atomic fact relevant to the question from the context. "
            "Copy numbers, dates and names VERBATIM, one fact per line. Do not "
            "infer, do not summarise, do not answer the question yet."},
        {"role": "user", "content": f"Context:\n{block}\n\nQuestion: {question}"},
    ], max_tokens=700)
    answer = model.chat([
        {"role": "system", "content":
            "Answer using ONLY the fact list. If the answer needs a count or an "
            "arithmetic step, compute it explicitly from the facts. One short sentence."},
        {"role": "user", "content": f"Question: {question}\nQuestion date: "
                                    f"{record['question_date']}\nFacts:\n{facts}"},
    ], max_tokens=300)
    return answer, 1


def build_store_indexed(record):
    """Sentence-level store that also remembers which session each entry came from.

    That mapping is what makes a two-level read possible without a schema change:
    the fine entries are exactly today's store, and the session blocks are derived
    on demand (a materialised view, rebuildable, never the source of truth).
    """
    from flymemory.v3 import split_chunks
    memory = SmartMemory(n_bits=4096, decay_tau=2592000.0)
    id_to_session = {}
    session_texts = {}
    for index, (date_text, session) in enumerate(zip(record.get("haystack_dates", []),
                                                     record.get("haystack_sessions", []))):
        stamp = parse_date(date_text)
        parts = []
        for turn in session:
            role = turn.get("role") or "user"
            content = (turn.get("content") or "").strip()
            if not content:
                continue
            parts.append(f"{role}: {content}")
            for chunk in split_chunks(f"[{date_text}] {role}: {content}"):
                result = memory.remember(text=chunk, source="import", timestamp=stamp)
                memory_id = result.get("memory_id")
                if memory_id is not None:
                    id_to_session[memory_id] = index
        session_texts[index] = (date_text, "\n".join(parts))
    return memory, id_to_session, session_texts


# Gate validated offline on the 500 oracle questions (no LLM, no embeddings):
# counting/ordering cues fire on 80% of temporal, 74% of multi-session and only
# 19% of single-session-user questions -> precision 0.75, recall 0.77 at 54%
# coverage. Dispersion of hits, tried first, was pure noise (AUC 0.479).
AGGREGATION_CUES = re.compile(
    r"\bhow many\b|\bhow long\b|\btotal\b|\bnumber of\b|\btimes\b|\baltogether\b"
    r"|\bfirst\b|\blast\b|\bearliest\b|\blatest\b|\bbefore\b|\bafter\b|\border\b",
    re.I)


def wants_aggregation(question):
    return bool(AGGREGATION_CUES.search(question or ""))


def run_twolevel_gated(question, record, model, memory, budget_chars=6000,
                       per_entry_chars=900, topk=25, parents=3, fine_share=0.6):
    """Additive parents only when the question's form asks for a roll-up.

    Costs the parent blocks on ~54% of questions instead of all of them; the other
    half keeps the cheap sentence-only read, same store, one build.
    """
    return run_twolevel(question, record, model, memory,
                        budget_chars=budget_chars,
                        per_entry_chars=per_entry_chars, topk=topk,
                        parents=parents, fine_share=fine_share, gated=True)


def ask_extract(model, question, record, context):
    """Two-step answer: extract facts verbatim, then compute the answer from them.

    Upstream's P-ANSWER protocol (6% -> 16% strict); here it is applied to whatever
    context a retrieval design produces.
    """
    facts = model.chat([
        {"role": "system", "content":
            "Extract every atomic fact relevant to the question from the context. "
            "Copy numbers, dates and names VERBATIM, one fact per line. Do not "
            "infer, do not summarise, do not answer the question yet."},
        {"role": "user", "content": f"Context:\n{context[:14000]}\n\nQuestion: {question}"},
    ], max_tokens=800)
    return model.chat([
        {"role": "system", "content":
            "Answer using ONLY the fact list. If the answer needs a count or an "
            "arithmetic step, compute it explicitly from the facts. One short sentence."},
        {"role": "user", "content": f"Question: {question}\nQuestion date: "
                                    f"{record['question_date']}\nFacts:\n{facts}"},
    ], max_tokens=300)


def twolevel_context(record, question, budget_chars=6000, per_entry_chars=900,
                     topk=25, parents=3, fine_share=0.6):
    """The context that experiment B validated: sentence hits plus their sessions."""
    from recall_block import pack
    store, id_to_session, session_texts = build_store_indexed(record)
    hits = store.recall(question, top_k=topk)
    fine_entries, parent_scores = [], {}
    for entry, sim, _score in hits:
        stamp = time.strftime("%Y-%m-%d", time.localtime(entry.timestamp))
        fine_entries.append((f"[#{entry.memory_id} | {sim:.2f} | {stamp}]", entry.text))
        sid = id_to_session.get(entry.memory_id)
        if sid is not None and sid not in parent_scores:
            parent_scores[sid] = sim
    fine_block, _stats = pack(fine_entries,
                              budget_chars=int(budget_chars * fine_share),
                              per_entry_chars=per_entry_chars)
    context = "Memory (sentence-level hits):\n" + fine_block
    chosen = sorted(parent_scores.items(), key=lambda kv: -kv[1])[:parents]
    if chosen:
        blocks = []
        for sid, _sim in chosen:
            date_text, body = session_texts[sid]
            blocks.append(f"[session {date_text}]\n{body[:1500]}")
        context += ("\n\nSessions those hits came from (wider context):\n"
                    + "\n\n".join(blocks))
    return context


def run_twolevel_extract(question, record, model, memory, budget_chars=6000):
    """The two wins stacked: two-level injection + extract-then-compute answering."""
    context = twolevel_context(record, question, budget_chars=budget_chars)
    return ask_extract(model, question, record, context), 0


def run_twolevel(

question, record, model, memory, budget_chars=6000,
                 per_entry_chars=900, topk=25, parents=3, fine_share=0.6, gated=False):
    """Fine hits plus the sessions they came from -- additive, never a replacement.

    Measured reason for "additive": session-level ingestion alone lifted
    multi-session questions (+13pp) but destroyed single-session-user recall
    (71% -> 14%), because a coarse block spends the budget on fewer entries.
    So the parents ride alongside the sentence hits, on their own share.
    """
    from recall_block import pack
    store, id_to_session, session_texts = build_store_indexed(record)
    hits = store.recall(question, top_k=topk)
    fine_entries, parent_scores = [], {}
    for entry, sim, _score in hits:
        stamp = time.strftime("%Y-%m-%d", time.localtime(entry.timestamp))
        fine_entries.append((f"[#{entry.memory_id} | {sim:.2f} | {stamp}]", entry.text))
        sid = id_to_session.get(entry.memory_id)
        if sid is not None and sid not in parent_scores:
            parent_scores[sid] = sim
    fine_block, _stats = pack(fine_entries,
                              budget_chars=int(budget_chars * fine_share),
                              per_entry_chars=per_entry_chars)
    context = "Memory (sentence-level hits):\n" + fine_block
    if gated and not wants_aggregation(question):
        parents = 0
        fine_share = 1.0     # nothing to spend on parents: give the block to the hits
    chosen = sorted(parent_scores.items(), key=lambda kv: -kv[1])[:parents]
    if chosen:
        blocks = []
        for sid, _sim in chosen:
            date_text, body = session_texts[sid]
            blocks.append(f"[session {date_text}]\n{body[:1500]}")
        context += ("\n\nSessions those hits came from (wider context):\n"
                    + "\n\n".join(blocks))
    return ask_with_context(model, question, record, context), 0


def run_chunk(
question, record, model, memory, granularity="sentence",
              budget_chars=6000, per_entry_chars=900, topk=25):
    """Answer with a budgeted block over a store ingested at one granularity."""
    from recall_block import pack
    store = build_store(record, granularity=granularity)
    hits = store.recall(question, top_k=topk)
    entries = []
    for entry, sim, _score in hits:
        stamp = time.strftime("%Y-%m-%d", time.localtime(entry.timestamp))
        entries.append((f"[#{entry.memory_id} | {sim:.2f} | {stamp}]", entry.text))
    block, _stats = pack(entries, budget_chars=budget_chars,
                         per_entry_chars=per_entry_chars)
    answer = ask_with_context(model, question, record,
                              "Memory context (budget-aware):\n" + block)
    return answer, 0


def run_budget(
question, record, model, memory, budget_chars=6000,
               per_entry_chars=600, topk=25):
    """The injection shape the hook now uses: fill a character budget, best first."""
    from recall_block import pack
    hits = memory.recall(question, top_k=topk)
    entries = []
    for entry, sim, _score in hits:
        stamp = time.strftime("%Y-%m-%d", time.localtime(entry.timestamp))
        entries.append((f"[#{entry.memory_id} | {sim:.2f} | {stamp}]", entry.text))
    block, _stats = pack(entries, budget_chars=budget_chars,
                         per_entry_chars=per_entry_chars)
    return ask_with_context(model, question, record,
                            "Memory context (budget-aware):\n" + block), 0


def run_fly_oneshot(question, record, model, memory, topk=8):
    hits = retrieve(memory, question, topk)
    context = ("Memory index (pointers only -- you cannot expand them in this run):\n"
               + pointer_lines(entry for entry, _sim in hits))
    return ask_with_context(model, question, record, context), 0


def run_fly_hybrid(question, record, model, memory, topk=8, full=2, threshold=0.6):
    """The hook shape: the closest hits arrive as full text, the rest as pointers."""
    hits = retrieve(memory, question, topk)
    expanded = [entry for entry, sim in hits[:full] if sim >= threshold]
    expanded_ids = {entry.memory_id for entry in expanded}
    parts = []
    if expanded:
        parts.append("Memory context (closest entries, full text):\n"
                     + "\n\n".join(entry.text for entry in expanded))
    pointers = [entry for entry, _sim in hits if entry.memory_id not in expanded_ids]
    if pointers:
        parts.append("Memory index (further pointers):\n" + pointer_lines(pointers))
    return ask_with_context(model, question, record, "\n\n".join(parts) or "(nothing retrieved)"), 0


def build_transcript(record, max_chars=200_000):
    """The whole haystack conversation, oldest first -- LongMemEval's oracle setting."""
    parts, truncated = [], False
    for date_text, session in zip(record.get("haystack_dates", []),
                                  record.get("haystack_sessions", [])):
        for turn in session:
            role = turn.get("role") or "user"
            content = (turn.get("content") or "").strip()
            if content:
                parts.append(f"[{date_text}] {role}: {content}")
    text = "\n".join(parts)
    if len(text) > max_chars:
        text, truncated = text[:max_chars], True
    return text, truncated


def run_fullctx(question, record, model, memory, max_chars=200_000):
    """Strongest structure-free baseline: the entire history in one prompt.

    If the memory store still beats this, the win is not "more text". If it does
    not, our retrieval is losing information that simply reading everything keeps.
    """
    transcript, truncated = build_transcript(record, max_chars)
    context = ("Full conversation history (oldest first):\n" + transcript
               + ("\n[... truncated ...]" if truncated else ""))
    return ask_with_context(model, question, record, context), 0


def run_profile(question, record, model, memory, topk=6):
    """Aggregate a compact profile from memory before answering.

    Preference questions score ~1/11 for every retrieval arm because they need a
    synthesised picture of the user, not one matching turn. This arm tests the
    cheapest version of that idea: several preference-oriented queries, dedup the
    hits, and give the reader a profile block instead of raw turns.
    """
    seen = {}
    for query in ("user preferences likes dislikes", "favorite preferred style",
                  "always never prefers", question):
        for entry, _sim, _score in memory.recall(query, top_k=topk):
            seen[entry.memory_id] = entry
    lines = [f"- {entry.text[:220]}" for entry in seen.values()]
    context = ("User profile assembled from memory:\n" + "\n".join(lines)
               if lines else "(no profile entries)")
    return ask_with_context(model, question, record, context), 0


TOOLS = [
    {"type": "function", "function": {
        "name": "search_memory",
        "description": "Search the owner's long-term memory; returns pointer lines "
                       "(id, date, one-line summary) for the closest entries.",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string",
                                                "description": "search phrasing"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_memory",
        "description": "Read the full text of one memory entry by its id.",
        "parameters": {"type": "object",
                       "properties": {"memory_id": {"type": "integer"}},
                       "required": ["memory_id"]}}},
]


def run_fly_agentic(question, record, model, memory, max_rounds=3):
    messages = [
        {"role": "system", "content": AGENTIC_SYSTEM % max_rounds},
        {"role": "user", "content": f"Question date: {record['question_date']}\n"
                                    f"Question: {question}"},
    ]
    reads = 0
    for _ in range(max_rounds):
        message = model.chat_message(messages, max_tokens=500, tools=TOOLS)
        calls = message.get("tool_calls") or []
        if not calls:
            return (message.get("content") or "").strip(), reads
        messages.append({"role": "assistant",
                         "content": message.get("content") or "",
                         "tool_calls": calls})
        for call in calls[:2]:
            function = call.get("function") or {}
            name = function.get("name")
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except Exception:                      # noqa: BLE001
                arguments = {}
            if name == "search_memory":
                hits = retrieve(memory, arguments.get("query") or question)
                result = pointer_lines(entry for entry, _sim in hits) or "(nothing found)"
            elif name == "read_memory":
                reads += 1
                try:
                    entry = entry_by_id(memory, int(arguments.get("memory_id")))
                except Exception:                  # noqa: BLE001
                    entry = None
                result = entry.text if entry else "(not found)"
            else:
                result = f"(unknown tool {name})"
            messages.append({"role": "tool", "tool_call_id": call.get("id"),
                             "content": result})
    final = model.chat_message(messages, max_tokens=300)
    return (final.get("content") or "").strip(), reads


# ---------------------------------------------------------------- judging
def judge(model, question, truth, answer):
    raw = model.chat([
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": f"Question: {question}\nGround truth: {truth}\n"
                                    f"Model answer: {answer}"},
    ], max_tokens=120)
    match = re.search(r'"?score"?\s*:\s*"?(correct|partial|wrong)', raw or "", re.I)
    return (match.group(1).lower() if match else "wrong")


def mcnemar_exact(first, second):
    """Exact binomial McNemar on paired correct/wrong outcomes.

    Returns (gained_by_first, lost_by_first, p)."""
    gained = sum(1 for a, b in zip(first, second) if a and not b)
    lost = sum(1 for a, b in zip(first, second) if b and not a)
    n = gained + lost
    if n == 0:
        return gained, lost, 1.0
    tail = sum(math.comb(n, k) for k in range(min(gained, lost) + 1)) / (2 ** n)
    return gained, lost, min(1.0, 2 * tail)


# ---------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=os.environ.get(
        "LME_ORACLE", r"D:\djr82\flymemory\data_longmemeval\longmemeval_oracle.json"))
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--arms", default="nomem,rag,fly,profile,fullctx,agentic2,agentic3")
    parser.add_argument("--protocols", default="")
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--fullctx-chars", type=int, default=200_000)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    base = os.environ.get("CMDBASE", "https://api.commandcode.ai/provider/v1")
    key = os.environ.get("CMKEY") or os.environ.get("COMMANDGOAT_API_KEY")
    model_id = os.environ.get("CMMODEL", "deepseek/deepseek-v4.1-flash")
    if not key:
        sys.exit("CMKEY (or COMMANDGOAT_API_KEY) is not set")

    records = json.load(open(args.data, encoding="utf-8"))
    random.Random(args.seed).shuffle(records)
    records = records[: args.sample]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    protocols = [p.strip() for p in args.protocols.split(",") if p.strip()]

    clients = {arm: Model(base, key, model_id) for arm in arms}
    judge_client = Model(base, key, model_id)

    # Each arm is self-describing now: agenticN = the tool protocol capped at N
    # rounds, so the cost/accuracy curve is one run instead of one run per N.
    cells = list(arms)
    stats = {arm: {"correct": 0, "partial": 0, "wrong": 0, "n": 0,
                   "rounds": 0, "by_type": {}}
             for arm in cells}
    rows = []
    started = time.time()

    for index, record in enumerate(records, 1):
        question, truth = record["question"], record["answer"]
        qtype = record.get("question_type", "?")
        needs_store = any(not arm.startswith("chunk-") and arm not in ("nomem", "fullctx", "mem0", "mem0infer", "twolevel", "twolevel-gate", "twolevel-extract") for arm in cells)
        memory = build_store(record) if needs_store else None
        print(f"[{index}/{len(records)}] {qtype}: {question[:70]}", flush=True)
        for arm in cells:
            name = arm
            if arm == "nomem":
                answer, rounds = run_nomem(question, record, clients[arm])
            elif arm == "rag":
                answer, rounds = run_rag(question, record, clients[arm], memory)
            elif arm.startswith("rag"):
                topk = int(arm[3:] or 5)
                answer, rounds = run_rag(question, record, clients[arm], memory, topk=topk)
            elif arm == "hybrid":
                answer, rounds = run_fly_hybrid(question, record, clients[arm], memory)
            elif arm == "mem0infer":
                answer, rounds = run_mem0_infer(question, record, clients[arm], memory)
            elif arm == "mem0":
                answer, rounds = run_mem0(question, record, clients[arm], memory)
            elif arm == "twolevel-extract":
                answer, rounds = run_twolevel_extract(question, record, clients[arm], memory)
            elif arm == "twolevel-gate":
                answer, rounds = run_twolevel_gated(question, record, clients[arm], memory)
            elif arm == "twolevel":
                answer, rounds = run_twolevel(question, record, clients[arm], memory)
            elif arm.startswith("chunk-"):
                answer, rounds = run_chunk(question, record, clients[arm], memory,
                                           granularity=arm.split("-", 1)[1])
            elif arm == "router":
                answer, rounds = run_router(question, record, clients[arm], memory)
            elif arm == "extract":
                answer, rounds = run_extract(question, record, clients[arm], memory)
            elif arm == "budget":
                answer, rounds = run_budget(question, record, clients[arm], memory)
            elif arm == "fullctx":
                answer, rounds = run_fullctx(question, record, clients[arm], memory,
                                                 max_chars=args.fullctx_chars)
            elif arm == "profile":
                answer, rounds = run_profile(question, record, clients[arm], memory)
            elif arm.startswith("agentic"):
                rounds_cap = int(arm.replace("agentic", "") or args.max_rounds)
                answer, rounds = run_fly_agentic(question, record, clients[arm], memory,
                                                 max_rounds=rounds_cap)
            else:
                answer, rounds = run_fly_oneshot(question, record, clients[arm], memory)
            score = judge(judge_client, question, truth, answer)
            row = stats[name]
            row[score] = row.get(score, 0) + 1
            row["n"] += 1
            row["rounds"] += rounds
            bucket = row["by_type"].setdefault(qtype, {"correct": 0, "partial": 0,
                                                       "wrong": 0, "n": 0})
            bucket[score] += 1
            bucket["n"] += 1
            rows.append({"question_id": record.get("question_id"), "question_type": qtype,
                         "arm": name, "score": score, "rounds": rounds,
                         "answer": answer[:400]})
            print(f"    {name:18} {score:8} {answer[:60]!r}", flush=True)
        if args.out:
            json.dump({"model": model_id, "sample": args.sample, "seed": args.seed,
                       "base_url": base, "elapsed_s": time.time() - started,
                       "stats": stats, "rows": rows},
                      open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    elapsed = time.time() - started
    if args.out:   # incremental: an interrupted run still leaves usable data
        json.dump({"model": model_id, "sample": args.sample, "seed": args.seed,
                   "base_url": base, "elapsed_s": elapsed, "stats": stats, "rows": rows},
                  open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n=== results ===")
    strict = {}
    for name, row in stats.items():
        n = row["n"] or 1
        strict[name] = row["correct"] / n
        weighted = (row["correct"] + 0.5 * row["partial"]) / n
        print(f"{name:18} n={row['n']:3} strict={strict[name]:6.1%} "
              f"weighted={weighted:6.1%} rounds={row['rounds'] / n:4.1f}")

    print("\n=== by question type (strict) ===")
    types = sorted({t for row in stats.values() for t in row["by_type"]})
    print("type".ljust(30) + "".join(name.ljust(18) for name in stats))
    for qtype in types:
        line = qtype[:28].ljust(30)
        for row in stats.values():
            bucket = row["by_type"].get(qtype, {"n": 0, "correct": 0})
            line += f"{bucket['correct']}/{bucket['n']}".ljust(18)
        print(line)

    print("\n=== paired tests (exact McNemar) ===")
    def per_question(name):
        return {row["question_id"]: row["score"] == "correct"
                for row in rows if row["arm"] == name}

    pair_list = [("agentic3", "rag"), ("agentic3", "fly"), ("fullctx", "rag"),
                 ("agentic3", "fullctx"), ("agentic2", "fullctx"),
                 ("agentic1", "rag"), ("agentic2", "agentic1"), ("agentic3", "agentic2"),
                 ("profile", "agentic3")]
    for left, right in pair_list:
        if left in strict and right in strict:
            first, second = per_question(left), per_question(right)
            shared = sorted(set(first) & set(second))
            gained, lost, p = mcnemar_exact([first[q] for q in shared],
                                            [second[q] for q in shared])
            print(f"{right:18} -> {left:18} gained={gained:3} lost={lost:3} p={p:.3f} "
                  f"(n={len(shared)})")

    for arm in arms:
        used = clients[arm]
        print(f"tokens {arm:8} in={used.prompt_tokens:8} out={used.completion_tokens:7} "
              f"calls={used.calls}")
    print(f"judge tokens in={judge_client.prompt_tokens} out={judge_client.completion_tokens}")
    print(f"elapsed {elapsed/60:.1f} min")

    if args.out:
        json.dump({"model": model_id, "sample": args.sample, "seed": args.seed,
                   "base_url": base, "elapsed_s": elapsed, "stats": stats, "rows": rows},
                  open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("report:", args.out)


if __name__ == "__main__":
    main()
