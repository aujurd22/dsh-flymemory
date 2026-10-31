# Measured comparison: what this store is for

Every number here comes from one harness (`scripts/bench_three_arms.py`), one
reader, one judge, one rubric. Nothing is quoted from another project's paper
unless it says so. Raw reports: `reports/*.json` (git-ignored).

## Protocol

| | |
|---|---|
| data | LongMemEval oracle (500 q, haystack ≈ 6k tokens) and LongMemEval-S (500 q, haystack ≈ 153k tokens) |
| store | built fresh per question from that question's haystack sessions, dated |
| reader | `deepseek/deepseek-v4.1-flash`, `reasoning_effort=off`, temperature 0 |
| judge | the same model, one rubric: correct / partial / wrong (strict counts correct only) |
| statistics | exact McNemar on paired per-question outcomes |
| arms | no memory, RAG-topk (full text), pointer index, profile block, full context, agentic tool use (1–3 rounds) |

## Oracle split — haystack fits in the prompt (~6k tokens)

n=500 unless marked:

| arm | strict | weighted | prompt tokens / answer |
|---|---|---|---|
| no memory | 2.8% | 4.3% | ~98 |
| RAG top-5 | 40.6% | 43.5% | ~213 |
| agentic 2 rounds | 51.8% | 56.4% | ~2,150 |
| **agentic 3 rounds** | **54.8%** | **60.6%** | ~2,900 |
| pointer index only | 35.0% (n=120) | 39.6% | ~390 |
| profile block | 37.5% (n=120) | 40.8% | ~540 |
| RAG top-50 | 56.7% (n=120) | 60.8% | ~1,130 |
| **full context** | **76.7% (n=120)** | 79.2% | ~6,230 |

- agentic3 vs RAG: **+14.2pp, 94 gained / 23 lost, p<0.0001**.
- agentic2 vs agentic3: +3.0pp, 34/19, p=0.053 for +35% tokens.
- **full context beats every retrieval arm** (+35/−11 vs agentic3, p=0.0005).
- the k-sweep explains it: 5→40.0%, 10→44.2%, 20→49.2%, 50→56.7%, all→76.7%.
  Score tracks how much history reaches the reader. Retrieval compresses; at this
  size compression is pure loss.

By type at n=500 (agentic3 vs RAG): temporal-reasoning 59% vs 34%, multi-session
41% vs 32%, single-session-assistant 66% vs 38%, single-session-user 79% vs 71%,
knowledge-update 60% vs 56%, preference 10% vs 3%.

## Long split — haystack does not fit (~153k tokens/question)

n=150:

| arm | strict | weighted | prompt tokens / answer |
|---|---|---|---|
| no memory | 2.0% | 4.3% | ~99 |
| RAG top-20 | 42.0% | 47.7% | ~616 |
| **agentic 3 rounds** | **52.0%** | **56.0%** | ~3,040 |
| full context | 45.3% | 47.3% | **~111,283** |

- full context does not tie here, it **loses**: 45.3% against 52.0% (McNemar 33
  gained / 23 lost for us, p=0.229 -- direction ours, interval not tight enough to
  claim superiority).
- the cost gap is the decisive part: **180x** RAG-top-20's tokens and **37x**
  agentic3's, and full context only runs at all because this reader has a 1M
  window. A 128k-window agent cannot do it.
- **context rot shows up by type**: temporal-reasoning 12/37 for full context
  against 22/37 for the tool protocol (32% vs 59%), multi-session 10/43 against
  18/43. Full context still wins single-session-assistant (16/22 vs 10/22) and
  knowledge-update (18/24 vs 15/24).
- the oracle-split advantage of full context **reverses** when history is long:
  76.7% -> 45.3%, while the tool protocol only moves 56.7% -> 52.0%.

## mem0 with its extraction pipeline (n=40, metered)

mem0's default `infer=True` runs an LLM over each ingested batch to extract facts.
That is the mode its published numbers use, and it is the mode whose cost keeps
most harnesses away. A local UA proxy (`scripts/ua_proxy.py`, needed because the
gateway rejects SDK user agents) makes it runnable *and* meters what goes through:

| run | strict | index-time cost |
|---|---|---|
| mem0 infer=True, run 1 | 55.0% | -- |
| mem0 infer=True, run 2 | 65.0% | 1 LLM call, **14,636 prompt tokens per question** |
| mem0 infer=False (raw store) | 47.5% | 0 |
| our agentic 3 rounds | 57.5% | 0 index-time, ~3,040 prompt tokens per answer |

Two things stand out.

1. **The same 40 questions score 55.0% and 65.0% in two mem0 runs.** Its store is
   built by LLM decisions, so what it extracts -- and therefore its score -- is
   not reproducible run to run. Retrieval over raw text is.
2. **Extraction buys mem0 roughly +7 to +17 points over its own raw mode, for
   ~14.6k index-time tokens per question**, and lands in the same band as our
   protocol, which pays nothing at ingest and ~3k tokens per answer instead.

So the two designs trade *when* to spend. mem0 spends at write time and gets a
cheaper read; this store spends at read time and keeps the raw record. For a local
long-term memory the second trade is worth stating plainly: no extraction step
means an LLM upgrade does not invalidate the library, and a bad extraction cannot
permanently poison it.

Caveat: n=40 on a single split; the mem0 range is wide enough that this is
directional, not a ranking.

## Stacking two independent fixes: 69.2%

Two changes were measured separately, then together, on the same 120 questions:

| arm | strict | weighted | prompt tokens/answer |
|---|---|---|---|
| RAG top-20 (baseline) | 50.8% | 54.6% | ~545 |
| two-level injection (sentence hits + their sessions) | 57.5% | 60.4% | ~1,728 |
| extract-then-compute answering | 56.7% | 60.4% | ~1,272 |
| **both together** | **69.2%** | **71.2%** | ~1,947 |

Paired, exact McNemar: combined vs two-level **+19/-5, p=0.007**; combined vs
extract **+18/-3, p=0.001**. Neither component alone gets past 58%; together they
clear 69%, and the two are *independent* -- one changes what reaches the reader
(fine hits plus the sessions they came from), the other changes how the reader
answers (copy the facts verbatim first, then compute).

By type: multi-session 33/43 (77%), temporal-reasoning 15/24, single-session-user
14/16, single-session-assistant 12/16, knowledge-update 8/15, preference 1/6.

For scale: full context on these same questions scored 76.7% at ~6,230 tokens per
answer. The stacked protocol reaches **69.2% at ~1,947** -- 7.5 points below the
"unreadable-everything" ceiling for a third of the cost, with a single call and no
tool loop.

### Why each half works

- **Two-level injection.** Ingesting whole sessions alone lifted multi-session
  questions but destroyed single-session recall (71% -> 14%), because a coarse
  block spends the budget on fewer entries. Adding the sessions *alongside*
  sentence hits, on their own 40% of the budget, keeps both: aggregation +13.4pp
  and single-session -1.9pp.
- **Extract-then-compute.** Counting and ordering questions fail on arithmetic over
  scattered evidence; copying the facts out first and then computing lifts
  knowledge-update and single-session-user. It is upstream's P-ANSWER protocol and
  it stacks cleanly because it touches the answer, not the retrieval.

### What did not work

- **Routing by question type** (an oracle router reading `question_type`): 53.3%,
  below both components -- the problem was never "should I read more", it was
  "what reaches the reader and how is it used".
- **Gating the parent blocks** with a lexical count/order signal: 54.2% vs 55.8%
  ungated (p=0.688) for 13% fewer tokens. The gate is correct but not worth the
  complexity; the value is in looking at both levels, not in choosing one.
- **A cheap "profile block"** for preference questions: exactly RAG (37.5%,
  p=1.000). Preference questions still fail everywhere (0-17%).

## What this supports, and what it does not
## What this supports, and what it does not

Supported:

0. **A stacked protocol reaches 69.2%** at ~1.9k tokens per answer, 7.5 points
   under the full-context ceiling for a third of its cost (both components
   individually significant, p<0.01).
1. **A simple retrieval baseline is beaten decisively** by the tool protocol:
   40.6% → 54.8% at n=500, p<0.0001, with the gain concentrated in temporal and
   multi-session questions.
2. **Full context is not a competitor at scale**: it ties on accuracy and loses
   ~200× on tokens once the history is real.
3. **Nobody should quote a percentage without its reader**: the same system is
   76.7% under a full-context reader and 54.8% under a retrievable one. Published
   cross-project numbers (Mem0's 66.9 on LOCOMO, Zep's stronger temporal results,
   EverMemOS' claims) use different readers, judges, prompts and splits; they are
   **not comparable** to these numbers and are not compared here.

Not supported, and worth stating plainly:

- "better than Mem0 / Zep / Letta". mem0 *has* now been run on this harness (both
  modes), and the result is parity within noise -- not superiority. Zep, Letta,
  cognee and the graph-based systems have not been run: they need an external
  database or an LLM-built index, which is a different cost regime.
- "state-of-the-art on LongMemEval". n=150 on the long split, one reader, one
  judge; the long-split lead over full context is directional, p=0.229.
- any claim about preference questions: every arm scores 3–17% there. Synthesised
  user profiles are a gap, not a strength.

## Next experiments, in order

1. **Long split at n=500** — n=150 is directional; the claim needs a tighter
   interval.
2. **A real competitor arm** — `mem0` (local, `infer=False`) run through the same
   reader and judge. Cost is the blocker for `infer=True`: it calls an LLM per
   message.
3. **More reads per question only where they pay** — the tool protocol's gain is
   in temporal and multi-session questions; a router that sends only those through
   multi-round retrieval would keep most of the +14pp at a fraction of the cost.
