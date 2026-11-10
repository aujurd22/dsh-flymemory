# dsh-flymemory

English | [中文](README.zh.md)

[![npm](https://img.shields.io/npm/v/dsh-flymemory)](https://www.npmjs.com/package/dsh-flymemory)
[![Listed on dsh-plugin.org](https://dsh-plugin.org/badges/listed.svg)](https://dsh-plugin.org/plugins/aujurd22/dsh-flymemory)

Long-term memory for [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) —
the kind that also remembers **what changed**.

You know the feeling. New session, and you explain the project again: what it is, what
we decided, what we tried, what broke. Ten minutes later the model confidently hands you
a plan you abandoned three weeks ago, because that plan is still in its memory and
nothing in the store knows it stopped being true.

That last part is what this plugin is about. Most memory tools solve "remember more".
This one also solves "stop believing the old version".

## Try it

Three ways in, same plugin:

```bash
npm i dsh-flymemory          # command line, always the newest version
```

- **Inside DSH**: sidebar → Plugins → Market → search `flymemory` → install.
  (The market index syncs once a day, so a release from today may still show
  yesterday's number; `npm i dsh-flymemory@latest` gets it immediately.)
- **Web**: the listing on [dsh-plugin.org](https://dsh-plugin.org/plugins/aujurd22/dsh-flymemory)
  and [awesome-dsh-plugin.com](https://awesome-dsh-plugin.com/p/aujurd22/dsh-flymemory/).

Then check it:

```bash
dsh-flymemory status         # is it running, where is the library, what is in it
```

And in DSH just ask something you discussed in another session. If it answers with
context you didn't give it, it's working.

Two things happen on install:

- the first start takes **~10–13 seconds** (the embedding model loads); after that the
  service stays up and answers in milliseconds;
- the very first install also downloads a **915 MB** multilingual embedding model, once.

Everything stays on your machine: one library file and one ledger, both under
`$DSH_HOME/flymemory-data`. Nothing is sent anywhere.

## What it actually does

| | what you get |
|---|---|
| **Remembers across sessions and clients** | ZCode, Qoder and DSH can share one library; each entry is tagged with the client that wrote it, so the recall can say who told whom |
| **Knows what's out of date** | when a fact changes, the old entry is *retired*, not deleted: it keeps `valid_from`/`valid_to` and the entry that replaced it, and stops showing up in normal recall |
| **Answers "why did I think that back then"** | the retired entry is still there with its dates, so the history is inspectable instead of overwritten |
| **Keeps the per-turn cost small** | each turn injects a bounded index (≤1.2 KB) of pointers; the model pulls the full text only for what it needs |
| **Shows what got used** | `usegate.jsonl` records every injected pointer, whether it was expanded, recalled again, or superseded — you can finally see whether the memory is doing anything |
| **Cleans up after itself** | daily pass: merge duplicates with the model, retire stale state, fold groups, shrink the file |

## The three things you'll use most

```bash
dsh-flymemory doctor     # why isn't it working (python, model, port, library) in one screen
dsh-flymemory ledger     # what got injected last session, and what was actually used
dsh-flymemory wiki       # the library as a readable "current truth" page
```

And when you want it to think about itself:

```bash
dsh-flymemory dream --window 90          # distill the last 90 minutes into durable notes
dsh-flymemory dream --reorganize         # the daily macro pass (see below)
dsh-flymemory organize --apply           # retire stale states, collapse exact duplicates
dsh-flymemory vacuum --older-than 30     # physically shrink the file
```

## Why not just a note table with cosine search

A note table stores text and finds similar text. That covers half the job. The other
half is knowing which of those notes is still true.

| | note table + cosine | dsh-flymemory |
|---|---|---|
| a fact changed | old and new both come back | the old one is retired and hidden from recall |
| the same thing said twice | piles up | length-tiered dedup merges or strengthens |
| "what is X *now*" | you write the query | `state_key` keeps the current value plus its chain of previous values |
| age | everything looks equally fresh | power-law decay weights by retention |
| where it came from | lost | `source` and `app:<name>` on every entry |
| did it help | unknown | the usage ledger |

## Waking up once a day

A per-window pass can't see that two topics are the same thing. A whole-store rewrite
can, but it risks drift. The daily pass sits in between: it reads a bounded digest
(8 topic buckets × 12 entries), asks the model for at most three durable conclusions
plus redundant pairs, and writes them as **one** playbook entry — yesterday's playbook
is retired into it, so there is always exactly one current macro view.

On Windows it's a scheduled task at 04:30; elsewhere, a cron line:

```
30 4 * * *  dsh-flymemory dream --reorganize --reorganize-apply
```

## How well it works

Short version: on a public long-memory benchmark (LongMemEval oracle, 120 questions,
same reader and judge for every arm), this plugin's protocol scored **69.2%** where
plain retrieval scored 50.8%, using about a third of the tokens that pasting the whole
history costs. Against a widely used memory library it's a **tie** (58.3% vs 56.7%).

Things it is *not*: smarter than the model, better than reading everything when the
history is short, or good at guessing your preferences. The full numbers, the protocols
and the negative results are in [docs/COMPARISON.md](docs/COMPARISON.md) — including
one we falsified ourselves ("few but dense" notes do not work for raw conversation).

## Honest limits

- **One writer at a time.** The service serialises writes; two processes pointed at the
  same file will fight, and the second one now writes a `*.conflict-<time>` copy instead
  of silently clobbering.
- **Preferences are weak.** The benchmark says so; don't expect a profile feature.
- **Heavier than a pure-JS plugin.** Python plus a 915 MB model, versus a SQLite file.
  If you want zero dependencies, there are simpler options.
- **Recall is top-k.** An entry that is semantically distant won't come back; that's
  retrieval, not a bug we can wish away.

## Requirements

- DeepSeek Harness (this is a bundle plugin)
- Python 3.10+ with `torch` and `sentence-transformers` (the CLI finds it for you and
  `doctor` tells you if it can't)
- Node 20+

## Configuration

Set in your profile's `cordis.patch.yml`, or leave the defaults:

| key | default | what it does |
|---|---|---|
| `port` | `8791` | where the memory service listens |
| `libraryPath` | under the data dir | which library to use (point several clients at one) |
| `workingContextChars` | `1200` | size of the pointer index injected each turn |
| `workingContextRefreshMs` | `45000` | how often that index refreshes |
| `recallProtocol` | `true` | the "pull what you need" prompt section |
| `answerProtocol` | `true` | the "extract facts, then compute" section |
| `checkpointContract` | `true` | fold compaction summaries back into memory |

## Tools the model can call

23 MCP tools. The ones that matter most:

`flymemory_recall_block` (budgeted read), `flymemory_recall` (ranked search),
`flymemory_remember`, `flymemory_supersede` (retire a fact), `flymemory_state_lookup`,
`flymemory_wiki` (current-truth page), `flymemory_organize` (housekeeping),
`flymemory_vacuum` (shrink), `flymemory_redact` (scrub a secret, keep the audit),
`flymemory_digest`, `flymemory_clusters`.

## Data layout

```
$DSH_HOME/flymemory-data/
  flymemory_v3.pkl     the library (single file)
  usegate.jsonl        injection → use ledger
  dream.json           optional free-endpoint keys for `dream` (local only)
```

## License

MIT. The engine lives in [aujurd22/flymemory](https://github.com/aujurd22/flymemory); this
package vendors it under `python/flymemory/` and wires it into the harness.
