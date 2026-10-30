# Where this can go

A reading of the 2026 agent-memory landscape, an honest placement of this
plugin in it, and the work that would actually move it. Numbers in the first
section are other people's; numbers about this project are measured on the live
store (19,494 entries, 68 MB) unless stated otherwise.

## The field, in one screen

| System | Core idea | What it is good at |
|---|---|---|
| **Mem0** (~63k stars) | token-efficient memory service; extract → consolidate → retrieve | personalization at scale, smallest context cost |
| **Zep / Graphiti** | temporal knowledge graph; every fact is stamped valid-from/valid-to | temporal and multi-hop questions; audit trails |
| **Letta / MemGPT** | OS-style paging between a small context and external storage | agent-managed context, long-running agents |
| **HippoRAG 2** (ICML'25, [arXiv 2502.14802](https://arxiv.org/abs/2502.14802)) | graph + vector; recognition memory picks seed nodes for PPR | multi-hop recall without LLM-built triples |
| **MemOS** ([arXiv 2505.22101](https://arxiv.org/abs/2505.22101)) | a "memory OS" with MemCube units and lifecycle scheduling | unifying parametric/activation/plaintext memory |
| **EverMemOS** (ACL'26) | benchmark-driven synthesis of the above | reports beating Zep on LoCoMo and MemOS on LongMemEval |
| **This project** | explicit, inspectable state machine in one local file | auditable supersede/decay/state chains; local-only |

Two results from 2026 shape the plan more than any feature list:

- a **pre-registered selection study** ([Zenodo 23005211](https://zenodo.org/records/23005211))
  found that **keeping 3 of 30 candidates** — i.e. *selection*, not extraction —
  adds +17.4 points on LoCoMo and +9.1 on LongMemEval at a tight budget. "Point
  at few things and let the model pull" is not a compromise; it is the measured
  win.
- **auditability is becoming a compliance requirement**, not a nicety: the EU AI
  Act's Article 12 logging obligation becomes enforceable in August 2026, and
  projects like [waxseal](https://pypi.org/project/waxseal/) / ontogr-mem are
  shipping tamper-evident, hash-chained memory ledgers.

## Measured, 2026-10-30: four arms, 200 questions, one reader

`scripts/bench_three_arms.py`, LongMemEval oracle, 200 questions, reader and judge
both `deepseek/deepseek-v4.1-flash` with `reasoning_effort=off`, temperature 0, a
fresh store per question, every answer scored by the same rubric:

| arm | strict | weighted | prompt tokens / answer |
|---|---|---|---|
| no memory | 3.5% | 4.8% | ~100 |
| RAG top-5, full text | 41.0% | 44.8% | ~215 |
| FlyMemory, pointers only | 36.0% | 39.8% | — |
| FlyMemory, pointers + top-2 inlined | 37.0% | 40.8% | ~375 |
| **FlyMemory, agentic (search + read)** | **54.0%** | **59.2%** | ~3,300 |

Paired tests (exact McNemar, n=200):

| comparison | gained | lost | p |
|---|---|---|---|
| RAG → agentic | 37 | 11 | **<0.001** |
| pointers → agentic | 43 | 7 | **<0.001** |
| RAG → inlined top-2 | 14 | 22 | 0.243 |
| pointers → inlined top-2 | 10 | 8 | 0.815 |
| agentic → inlined top-2 | 9 | 43 | <0.001 |

By question type (strict, correct/total):

| type | none | RAG | pointers | +top-2 | agentic |
|---|---|---|---|---|---|
| temporal-reasoning | 1/47 | 14/47 | 16/47 | 16/47 | **25/47** |
| multi-session | 5/55 | 20/55 | 12/55 | 13/55 | **25/55** |
| single-session-user | 1/27 | 18/27 | 15/27 | 17/27 | **22/27** |
| single-session-assistant | 0/25 | 10/25 | 10/25 | 10/25 | **15/25** |
| knowledge-update | 0/35 | 19/35 | 19/35 | 18/35 | 19/35 |
| single-session-preference | 0/11 | 1/11 | 0/11 | 0/11 | 2/11 |

### What this confirms

**The protocol is the advantage.** Agentic retrieval beats top-5 RAG by 13 points
with the same reader and the same store, and the pairing is decisive (37 vs 11
discordant, p<0.001). The gain concentrates exactly where a flat store should be
weak: temporal reasoning (+11/47) and multi-session questions (+5/55).

### Two predictions this falsifies

1. **"Inlining the closest hit fixes the one-shot case."** It does not: 37.0% vs
   36.0% for pointers alone (p=0.815), and still below RAG's 41.0%. The hook
   feature is therefore off by default, kept only as `FLYMEMORY_HYBRID_FULL`.
   Pointers are only worth emitting when the model can expand them -- which is the
   P-USEGATE lesson restated from the injection side.
2. **"Knowledge-update is our strongest quadrant."** It is not: every arm lands on
   19/35, i.e. the reader resolves those questions regardless of how the memory is
   presented. Supersede keeps the store clean, but it does not make the answer
   easier -- the win there would have to come from answer-side presentation, not
   from retrieval.

### The rounds curve, and a second falsified prediction (120 questions)

Adding the rounds dimension as separate arms (`agentic1/2/3`) and a lightweight
profile arm (`profile`: several preference-oriented queries, deduped into a
profile block) gives one more table:

| arm | strict | weighted | reads/answer | prompt tokens/answer |
|---|---|---|---|---|
| no memory | 2.5% | 4.6% | 0 | ~99 |
| RAG top-5 | 37.5% | 42.5% | 0 | ~213 |
| pointers only | 35.0% | 39.6% | 0 | ~390 |
| profile block | 37.5% | 40.8% | 0 | ~539 |
| agentic, 1 round | 10.0% | 10.0% | 0.0 | ~1,036 |
| agentic, 2 rounds | 50.8% | 55.0% | 1.7 | ~2,249 |
| **agentic, 3 rounds** | **56.7%** | **61.3%** | 2.0 | ~2,987 |

- **One round is worse than no tools at all** (10.0%): the model spends its single
  round searching and then answers from pointer lines. A tool protocol needs room
  to read, and 2 rounds is where it starts to pay (50.8%, p<0.001 vs 1 round).
- **3 rounds vs 2** is +5.9pp for +33% tokens, McNemar 9 gained / 2 lost,
  **p=0.065** -- suggestive, not established. The 500-question run exists to
  decide this.
- **The lightweight profile does not help.** 37.5% strict, exactly RAG, p=1.000
  against it. Preference questions stay at 0-2/6 for every arm: a profile made by
  concatenating retrieved lines is not a profile. Building one properly means
  model-side synthesis over many turns, which this design deliberately avoids --
  so this stays a known gap, not a roadmap item.

### What it costs, and the gaps it leaves

Agentic costs ~15x the prompt tokens of RAG for 13 points, at 1.8 reads per
question. The preference category fails for every arm (1/11 at best): those
questions want a synthesised user profile, which this design deliberately does not
build. Both are the next measurement, not a mystery.

## Where we actually stand## Where we actually stand

Measured on the live store:

| Operation | Cost | Note |
|---|---|---|
| load the library | 1.03 s | acceptable for a local service |
| one write (embed + dedup) | 47 ms | fine |
| **save to disk** | **440 ms per write** | whole library re-dumped; six call sites |
| recall | 43 ms | fine |
| session pack | 4 ms | fine |

So the write path is ~0.5 s and **O(n)**; at 100k entries it is ~2.3 s per write
and the server is single-threaded.

Data model: `text, response, embedding, timestamp, last_accessed, access_count,
tags, memory_id, superseded_by, source, evidence_ids, updated_at, valid_from,
valid_to, state_key, state_value`. There is **no session, no project, no expiry**,
and the embedding-model identity is only surfaced as a stderr warning on mismatch.

Strengths worth keeping: supersede with tombstoned validity windows, power-law
decay, length-tiered dedup, entity-state chains, evidence-linked consolidation,
`app:` provenance, and the per-request pointer index with a use ledger.

## The plan

### Tier 1 — engineering, this week, no format change

1. **Debounce the save.** *(done 2026-10-29)* Batch writes and flush on
   idle/exit. Measured on a copy of the live 19,494-entry library, 20 writes
   each: **517 ms average / 453 ms median before, 60 ms median after**
   (`FLYMEMORY_SAVE_DEBOUNCE=0` restores write-through). The first write after a
   quiet period still persists immediately, and a background flusher plus an
   atexit hook mean nothing is dropped.
2. **Make embedder mismatch loud.** `stats` reports it and `doctor` fails; a
   background re-embed is the follow-up. Today a model swap silently degrades
   recall.
3. **Write-path regression test** so (1) cannot regress unnoticed.

### Tier 2 — capability, one to two weeks, additive fields

4. **Session and project dimensions.** `session_id` / `project` on the entry,
   captured from the host, filtered on read. This is the ceiling on everything
   else: per-conversation recall, safe recent-channels, multi-agent isolation,
   "what did I do on this project". Needs an engine change, so it goes upstream
   first.
5. **TTL for volatile facts.** `expires_at` + `remember(ttl=)`; volatile facts
   (today's price, a one-off link) stop competing with durable knowledge.
6. **Deterministic conflict verdicts.** Same `state_key` already supersedes; add
   "high similarity + cancellation/negation wording → mark `conflict`" so the
   prompt says *there is an unresolved conflict* instead of showing both.
7. **Feed the use ledger back into ranking.** We already log injected → used;
   using that rate as a ranking feature is the cheapest possible "learning" that
   cannot hallucinate.
8. **Hash-chained audit ledger.** One JSONL line per state change with
   `prev_hash`; `flymemory_verify` checks the chain. Cheap, and it turns
   "inspectable" into "provable" — the compliance story above.

### Tier 3 — structural, one to two months, pick one

9. **Temporal graph sidecar (Zep/Graphiti-shaped).** Entities and time-scoped
   edges next to the existing store, used only for multi-hop and "what changed
   when" questions. Keeps the state machine as the source of truth; the graph is
   a derived index that can be rebuilt (`maintenance.py export-facts` already
   prototypes that shape).
10. **HippoRAG-style node index** instead of the graph: consolidated entries
    become nodes, `evidence_ids` become edges, retrieval does a cheap PPR over
    them. Cheaper than a real graph database, and the edges already exist.

Recommendation: (9) if temporal questions dominate, (10) if multi-hop over the
existing consolidation matters more. Both are *additive*; neither replaces the
auditable state machine, which is the one thing competitors do not have.

### What we should not build

| Direction | Why not |
|---|---|
| adaptive read policies | P-DIACT measured NULL; adaptive retrieval did not replicate |
| wider recall pools | P-TREE / P-NEEDLE: pool width is not the bottleneck |
| automatic pointer expansion | P-USEGATE: +10.5pp coverage for 12.15× tokens |
| a general "memory OS" with memory types | over-engineering for a single-user local agent; MemOS solves a different problem |
| managed cloud / multi-tenant | out of scope; local-only is the product |

## What "a higher tier" means here

We will not out-benchmark Mem0 or Zep on their own infrastructure — they have
service teams and benchmark pipelines. The reachable position is narrower and
defensible:

> **the memory you can audit**: one local file, an explicit state machine,
> tombstones instead of deletions, provenance on every entry, a bounded pointer
> index with an injection→use ledger, and — after Tier 2 — a hash-chained record
> of every state change.

The selection result above says the retrieval half of that is the right shape.
Tier 1 makes it fast, Tier 2 makes it trustworthy, Tier 3 makes it smarter on the
questions where a flat store is genuinely weak.
