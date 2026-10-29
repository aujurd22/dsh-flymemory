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

## Where we actually stand

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
