#!/usr/bin/env python3
"""Three-arm memory benchmark: NoMemory vs RAG-top5 vs FlyMemory.

Pre-registered questions this answers (see docs/ROADMAP.md):

  * does the memory store beat no memory at all, with the reader held fixed?
  * does it beat plain top-5 RAG on the same turns?
  * does the pointer/agentic protocol (which is how the plugin actually works)
    do better than stuffing full text into the prompt?

Design, fixed on purpose:

  * reader  = one model for every arm, `reasoning_effort=off`, temperature 0
  * judge   = the same model, one rubric, strict (correct) and weighted
              (correct 1.0 / partial 0.5 / wrong 0)
  * data    = LongMemEval oracle split: a fresh store per question, built from
              that question's haystack sessions, so no arm sees more than another
  * protocols = one-shot (bounded context, no tools) and agentic (the model may
              search/read, capped at --max-rounds)

Usage:
  python scripts/bench_three_arms.py --data D:/djr82/flymemory/data_longmemeval/longmemeval_oracle.json \
      --sample 10 --arms nomem,rag,fly --protocols oneshot,agentic

  CMDBASE=https://api.commandcode.ai/provider/v1 CMMODEL=deepseek/deepseek-v4.1-flash \
  CMKEY=... python scripts/bench_three_arms.py --sample 10
"""
import argparse
import json
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
        """Returns the assistant text (tool calls are dropped)."""
        message = self.chat_message(messages, max_tokens=max_tokens, tools=tools)
        return (message.get("content") or "").strip()

    def chat_message(self, messages, max_tokens=300, tools=None):
        """Returns the whole assistant message (content + tool_calls)."""
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
    """LongMemEval dates look like '2023/04/10 (Mon) 17:50'."""
    match = re.match(r"(\d{4})/(\d{2})/(\d{2})", text or "")
    if not match:
        return time.time()
    import datetime
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
    out = []
    for hit in hits:
        stamp = time.strftime("%Y-%m-%d", time.localtime(hit.timestamp))
        out.append(f"[#{hit.memory_id} | {stamp} | {hit.source}] {hit.text[:110]}")
    return "\n".join(out)


# ---------------------------------------------------------------- arms
def run_nomem(question, record, model):
    context = "(no memory available)"
    answer = model.chat([
        {"role": "system", "content": ANSWER_SYSTEM},
        {"role": "user", "content": f"Question date: {record['question_date']}\n"
                                    f"Memory context:\n{context}\n\nQuestion: {question}"},
    ])
    return answer, 0


def run_rag(question, record, model, memory, topk=5):
    hits = [entry for entry, _sim, _score in memory.recall(question, top_k=topk)]
    context = "\n\n".join(hit.text for hit in hits) or "(nothing retrieved)"
    answer = model.chat([
        {"role": "system", "content": ANSWER_SYSTEM},
        {"role": "user", "content": f"Question date: {record['question_date']}\n"
                                    f"Memory context:\n{context}\n\nQuestion: {question}"},
    ])
    return answer, 0


def run_fly_oneshot(question, record, model, memory, topk=8):
    hits = [entry for entry, _sim, _score in memory.recall(question, top_k=topk)]
    answer = model.chat([
        {"role": "system", "content": ANSWER_SYSTEM},
        {"role": "user", "content": f"Question date: {record['question_date']}\n"
                                    f"Memory index (pointers only -- "
                                    f"you cannot expand them in this run):\n"
                                    f"{pointer_lines(hits)}\n\nQuestion: {question}"},
    ])
    return answer, 0


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
    """The model drives retrieval through function calling: pointers, then reads."""
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
                hits = [entry for entry, _sim, _score in
                        memory.recall(arguments.get("query") or question, top_k=8)]
                result = pointer_lines(hits) or "(nothing found)"
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
    return (match.group(1).lower() if match else "wrong"), raw


# ---------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=os.environ.get(
        "LME_ORACLE", r"D:\djr82\flymemory\data_longmemeval\longmemeval_oracle.json"))
    parser.add_argument("--sample", type=int, default=10)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--arms", default="nomem,rag,fly")
    parser.add_argument("--protocols", default="oneshot,agentic")
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

    # One client per arm keeps token accounting separate.
    clients = {arm: Model(base, key, model_id) for arm in arms}
    judge_client = Model(base, key, model_id)

    stats = {f"{arm}/{protocol}": {"correct": 0, "partial": 0, "wrong": 0, "n": 0,
                                   "rounds": 0}
             for arm in arms for protocol in protocols}
    started = time.time()

    for index, record in enumerate(records, 1):
        question, truth = record["question"], record["answer"]
        memory = build_store(record) if ("rag" in arms or "fly" in arms) else None
        print(f"[{index}/{len(records)}] {record['question_type']}: {question[:70]}", flush=True)
        for arm in arms:
            for protocol in protocols:
                if arm != "fly" and protocol == "agentic":
                    continue      # a tool protocol only makes sense with pointers
                if arm == "nomem":
                    answer, rounds = run_nomem(question, record, clients[arm])
                elif arm == "rag":
                    answer, rounds = run_rag(question, record, clients[arm], memory)
                elif protocol == "oneshot":
                    answer, rounds = run_fly_oneshot(question, record, clients[arm], memory)
                else:
                    answer, rounds = run_fly_agentic(question, record, clients[arm], memory)
                score, _raw = judge(judge_client, question, truth, answer)
                key_name = f"{arm}/{protocol}"
                stats[key_name][score] = stats[key_name].get(score, 0) + 1
                stats[key_name]["n"] += 1
                stats[key_name]["rounds"] += rounds
                print(f"    {key_name:18} {score:8} {answer[:60]!r}", flush=True)

    elapsed = time.time() - started
    print("\n=== results ===")
    for name, row in stats.items():
        n = row["n"] or 1
        strict = row["correct"] / n
        weighted = (row["correct"] + 0.5 * row["partial"]) / n
        print(f"{name:18} n={row['n']:3} strict={strict:6.1%} weighted={weighted:6.1%} "
              f"rounds={row['rounds'] / n:4.1f}")

    total = judge_client
    for arm in arms:
        used = clients[arm]
        print(f"tokens {arm:8} in={used.prompt_tokens:8} out={used.completion_tokens:7} "
              f"calls={used.calls}")
    print(f"judge tokens in={total.prompt_tokens} out={total.completion_tokens}")
    print(f"elapsed {elapsed/60:.1f} min")

    if args.out:
        json.dump({"model": model_id, "sample": args.sample, "seed": args.seed,
                   "base_url": base, "stats": stats, "elapsed_s": elapsed},
                  open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("report:", args.out)


if __name__ == "__main__":
    main()
