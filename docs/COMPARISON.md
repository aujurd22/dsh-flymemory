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
