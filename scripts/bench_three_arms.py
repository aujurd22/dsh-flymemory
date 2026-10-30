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


def build_store(record):
    memory = SmartMemory(n_bits=4096, decay_tau=2592000.0)
    for date_text, session in zip(record.get("haystack_dates", []),
                                  record.get("haystack_sessions", [])):
        stamp = parse_date(date_text)
        for turn in session:
            role = turn.get("role") or "user"
            content = (turn.get("content") or "").strip()
            if not content:
                continue
            memory.remember_text(text=f"[{date_text}] {role}: {content}",
                                 source="import", timestamp=stamp)
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
    parser.add_argument("--arms", default="nomem,rag,fly,hybrid")
    parser.add_argument("--protocols", default="oneshot,agentic")
    parser.add_argument("--max-rounds", type=int, default=3)
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

    # nomem/rag/hybrid are one-shot only; `fly` runs both protocols.
    cells = [(arm, protocol) for protocol in protocols for arm in arms
             if arm == "fly" or protocol == "oneshot"]
    stats = {f"{arm}/{protocol}": {"correct": 0, "partial": 0, "wrong": 0, "n": 0,
                                   "rounds": 0, "by_type": {}}
             for arm, protocol in cells}
    rows = []
    started = time.time()

    for index, record in enumerate(records, 1):
        question, truth = record["question"], record["answer"]
        qtype = record.get("question_type", "?")
        memory = None if all(arm == "nomem" for arm, _p in cells) else build_store(record)
        print(f"[{index}/{len(records)}] {qtype}: {question[:70]}", flush=True)
        for arm, protocol in cells:
            name = f"{arm}/{protocol}"
            if arm == "nomem":
                answer, rounds = run_nomem(question, record, clients[arm])
            elif arm == "rag":
                answer, rounds = run_rag(question, record, clients[arm], memory)
            elif arm == "hybrid":
                answer, rounds = run_fly_hybrid(question, record, clients[arm], memory)
            elif protocol == "oneshot":
                answer, rounds = run_fly_oneshot(question, record, clients[arm], memory)
            else:
                answer, rounds = run_fly_agentic(question, record, clients[arm], memory,
                                                  max_rounds=args.max_rounds)
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

    elapsed = time.time() - started
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

    for left, right in (("fly/agentic", "rag/oneshot"), ("fly/agentic", "fly/oneshot"),
                        ("hybrid/oneshot", "rag/oneshot"), ("hybrid/oneshot", "fly/oneshot"),
                        ("hybrid/oneshot", "fly/agentic")):
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
