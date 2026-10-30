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

n=40:

| arm | strict | weighted | prompt tokens / answer |
|---|---|---|---|
| no memory | 2.5% | 5.0% | ~100 |
| RAG top-20 | 52.5% | 53.8% | ~578 |
| RAG top-50 | 47.5% | 47.5% | ~1,357 |
| **agentic 3 rounds** | **55.0%** | 58.8% | ~3,004 |
| full context | 52.5% | 52.5% | **~111,137** |

- full context vs agentic3: 7 gained / 6 lost, **p=1.000** — a tie.
- but full context costs **192×** the tokens of RAG-top-20 and **37×** agentic3
  for the same score, and it only works at all because this reader has a 1M
  window.
- the oracle-split advantage of full context **disappears** when the history is
  long: 76.7% → 52.5%.

## Injection shape (120 questions)

The hook used to inject five one-line summaries. Since the k-sweep showed the
score tracks how much history reaches the reader, 1.6.0 fills a character budget
instead (`flymemory_recall_block`, 6000 chars by default):

| injection | strict | prompt tokens / answer |
|---|---|---|
| 5 one-line summaries (the old default) | 40.0% | ~210 |
| 20 full entries | 48.3% | ~545 |
| **budgeted block (6000 chars)** | **50.8%** | ~1,095 |
| 50 full entries | 56.7% | ~1,130 |

So the change is worth it against what it replaced: **+10.8pp for ~+900 tokens**.
But at the same token cost, simply taking more full entries (top-50, 56.7%) still
edges out the 6000-char packing (50.8%) -- the packing is a bound, not a better
selector. The plateau is around 50-57% for every one-shot injection, which is why
the next real lever is the tool protocol (+14.2pp at n=500), not more text.

## mem0, on the same ruler (120 questions)

`mem0ai` 2.2.1, local Qdrant, `infer=False` (pure store, no LLM extraction), the
same embedder as ours (`paraphrase-multilingual-MiniLM-L12-v2`), the same reader
and judge, and per-question isolation through `user_id`:

| arm | strict | paired vs mem0 |
|---|---|---|
| mem0 (top-5) | **58.3%** | — |
| FlyMemory, agentic 3 rounds | 56.7% | 19 gained / 21 lost, **p=0.875** |
| RAG top-50 (ours) | 56.7% | 18 / 20, p=0.871 |
| RAG top-20 (ours) | 49.2% | 18 / 29, p=0.144 |
| pointer index only | 35.0% | 10 / 38, **p<0.001** |
| full context | 76.7% | 26 / 4, **p<0.001** |

By type, mem0 vs agentic3: single-session-assistant 16/16 vs 10/16 (mem0 clearly
better), single-session-user 16/16 vs 13/16, multi-session 19/43 vs 21/43,
temporal-reasoning 11/24 vs 14/24 (ours better), knowledge-update 8/15 each.

Reading: **parity overall**, with different strengths -- mem0 is better at
single-session recall (it hands the reader whole messages), this store is better
at temporal questions. Neither reaches full context on a short haystack. Note the
costs: mem0 ~1.2k prompt tokens/answer, agentic3 ~3.0k -- parity can be had for
less, which is what the `budget` arm is measuring.

Caveat that matters: `infer=False` is mem0's *raw store* mode. With its default
`infer=True` it runs an LLM per message to extract facts, which is the mode its
published numbers use -- and which would cost hundreds of thousands of LLM calls
across 500 questions. So this compares the two systems' **retrieval**, not mem0's
full extraction pipeline.

## What this supports, and what it does not

Supported:

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

- "better than Mem0 / Zep / Letta". We have not run them; our arms are baselines
  we built, not their systems. A honest claim needs their code on this harness,
  which is the next experiment rather than a footnote.
- "state-of-the-art on LongMemEval". n=40 on the long split, one reader, one
  judge; the Ci is wide and the split is a subset.
- any claim about preference questions: every arm scores 3–17% there. Synthesised
  user profiles are a gap, not a strength.

## Next experiments, in order

1. **Long split at n=150** — the n=40 tie needs a tighter interval before it
   becomes a positioning claim.
2. **A real competitor arm** — `mem0` (local, `infer=False`) run through the same
   reader and judge. Cost is the blocker for `infer=True`: it calls an LLM per
   message.
3. **Budget-aware injection** — the k-sweep says 340 extra tokens buy ~9pp in the
   oracle split; the hook should fill a token budget, not a fixed count.
