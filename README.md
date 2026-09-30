# dsh-flymemory

English | [中文](README.zh.md)

Long-term memory for [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness).

This is a plugin (a DSH *bundle*) that runs [FlyMemory](https://github.com/aujurd22/flymemory)
as a local MCP service and plugs it into the harness. You end up with 15 memory
tools, plus a pair of hooks that quietly record what you've been working on and
pull the relevant bits back when they matter — so you stop re-explaining the same
context every session.

Everything stays on your machine. The service listens on `127.0.0.1:8791`, the
library is a single file under your DSH home, and nothing is sent anywhere.

```
DSH session ──┬── mcp__flymemory__*   15 tools the model can call
              └── hooks               recall before a turn, store after it
                        │
                        ▼
              FlyMemory service on 127.0.0.1:8791
                        │
                        ▼
              $DSH_HOME/flymemory-data/flymemory_v3.pkl
```

## What you get

Fifteen tools, all under the `mcp__flymemory__` prefix:

| Tool | What it's for |
|---|---|
| `flymemory_remember` | Store a decision or finding. Near-duplicates are merged automatically. `compartment` groups entries by topic; `state_key`/`state_value` track "the current value of X" and retire the old one. |
| `flymemory_recall` | Search. Dense embeddings and keyword matching are fused, older entries fade, and results come back with id, age and where they came from. `include_superseded` digs into history. |
| `flymemory_auto` | Recall and store in one call. This is what the hooks use. |
| `flymemory_recall_index` / `flymemory_get_memory` | Look first, read later: a one-line index, then the full text of whatever looks interesting. |
| `flymemory_supersede` | Say "this old entry is out of date, that one replaces it". |
| `flymemory_state_lookup` / `flymemory_state_history` | Current value for an entity key, or the whole history of it. |
| `flymemory_consolidate` | Fold several entries into one conclusion; the originals stay as evidence. |
| `flymemory_find_conflicts` / `flymemory_insights` | Pairs that look contradictory; valuable entries that are fading. |
| `flymemory_forget` / `flymemory_cleanup` | Delete one entry, or sweep everything that has decayed past a threshold. |
| `flymemory_session_pack` | A short pack of the recent trail and the latest conclusions. |
| `flymemory_stats` | Counts, ages, access counts. |

And two hooks that work on their own:

- **On every prompt** — the prompt is stored, and memories that match it get
  appended to the turn. No model call, just local search.
- **On session start** — a recovery pack of recent activity, so a fresh session
  isn't starting from nothing.

Both hooks are silent when the service is down, and neither can block a turn.

## Requirements

- DeepSeek Harness with `@deepseek-ai/dsh-mcp-client` and
  `@deepseek-ai/dsh-hooks-claude-code` available. Built and tested against
  `0.2.0-rc.2`.
- Node 20+ (the add-on and its CLI).
- Python 3.10+ with the engine's dependencies:

  ```bash
  pip install torch sentence-transformers "mcp>=1.30,<2" numpy
  ```

  The plugin finds the interpreter for you: it checks `FLYMEMORY_PYTHON`, the
  usual install locations, then `PATH`, and it verifies that
  `sentence_transformers` actually imports before picking one. On a machine with
  several Pythons that saves you the "why won't it start" half hour.

  The first run downloads the multilingual embedding model (~470 MB) into the
  HuggingFace cache. After that it works offline.

## Install

Point `plugin_manager` at this directory (absolute path):

```
plugin_manager(action: "install_bundle", target: "/absolute/path/to/dsh-flymemory")
```

Or use the **Plugins** page in the Web sidebar and pick the folder. The harness
reloads, three rows come up (`flymemory`, `flymemory-mcp`, `flymemory-hooks`),
and the tools appear.

Check it:

```bash
node bin/flymemory.mjs status     # what it resolved, and what's live
node bin/flymemory.mjs doctor     # interpreter, dependencies, endpoint
```

Then ask the model to remember something and search for it. If the tools don't
show up, `doctor` usually says why.

Removing it is the same in reverse:

```
plugin_manager(action: "remove_bundle", target: "dsh-flymemory")
```

Your memory file is not deleted — remove it by hand if you want it gone.

## How it runs

The bundle inserts three rows:

| Row | Module | Job |
|---|---|---|
| `flymemory` | this package | Probes the port, starts the engine if nothing is there, writes the hook config |
| `flymemory-mcp` | `@deepseek-ai/dsh-mcp-client` | Turns the service's tools into `mcp__flymemory__<tool>` |
| `flymemory-hooks` | `@deepseek-ai/dsh-hooks-claude-code` | Runs the two hook scripts on the right events |

When the harness starts, the first row checks `127.0.0.1:8791`. If a FlyMemory
service is already answering there, it's reused — two harness windows share one
engine and one library. If the port is free, the bundled engine starts in the
background and the row waits for it before letting the next row connect. If
something else owns the port, the plugin says so and leaves it alone; it never
kills a process it didn't start.

Cold starts are slow: the engine imports torch before it can listen, which takes
15–20 seconds. The row waits up to 25 seconds by default, and the mcp-client
reconnects on its own if it has to.

If the engine can't start at all — no interpreter, missing dependency, port
conflict — the harness still boots. You just don't get the tools, and the reason
is in the log.

## Settings

Environment variables, read before the harness starts:

| Variable | Default | |
|---|---|---|
| `FLYMEMORY_MCP_PORT` | `8791` | Service port |
| `FLYMEMORY_MCP_URL` | `http://127.0.0.1:<port>/mcp` | Full endpoint |
| `FLYMEMORY_DATA_DIR` | `$DSH_HOME/flymemory-data` | Library, logs, hook config, pid file |
| `FLYMEMORY_PYTHON` | auto-detected | Interpreter for the engine |
| `FLYMEMORY_DEVICE` / `FLYMEMORY_MODEL` | `cpu` / multilingual MiniLM | Passed to the engine |

Anything else goes on the row itself, in your profile's `cordis.patch.yml`. A
patch entry replaces the whole `config`, so write out everything you need:

```yaml
- id: flymemory
  name: 'dsh-flymemory'
  config:
    port: 8791
    dataDir: 'D:\dsh-memory'
    libraryPath: 'D:\dsh-memory\my-lib.pkl'
    pythonExe: 'C:\Python313\python.exe'
    device: cpu
    readinessTimeoutMs: 25000   # 0 = don't wait, let the tools show up later
    shutdownOnDispose: true     # stop the engine this activation started
    writeHooksConfig: true
    seedFromUpstream: false     # don't import another library implicitly
    upstreamLibrary: ''         # set both to import one, once
    autostart: true
```

The rest of the options are documented in `resolveOptions()` in
[lib/index.js](lib/index.js). Unknown keys are ignored.

## Where your memory lives

`$DSH_HOME/flymemory-data/flymemory_v3.pkl` — one file, plain data, easy to back
up or delete. Alongside it:

- `hooks.json` — regenerated on every activation; describes the two hooks
- `engine.log` — stdout/stderr of the engine process
- `server.log` — the engine's own log
- `server.pid` — so `flymemory stop` knows what to kill

To run the service on its own, without the harness:

```bash
node bin/flymemory.mjs start
node bin/flymemory.mjs stop
node bin/flymemory.mjs log -n 40
```

After installing the bundle, the same CLI is linked into the profile at
`<profile>/node_modules/.bin/dsh-flymemory`.

## About the hooks

They're on by default, because automatic memory is the whole point of the thing.
Worth knowing exactly what that means:

- Every prompt you send is written to the memory file. It's local and cheap, but
  it is a record of what you typed.
- Recalled memories are appended to the turn, which costs some context — in
  practice around 1.5 KB per turn.
- Credential-shaped text never gets stored: the engine rejects things like
  `ghp_`, `github_pat_`, `sk-ant-`, `AKIA` and private key headers.

If you'd rather have the tools and no automation:

```
plugin_manager(action: "set_plugin", target: "include:flymemory-hooks", enabled: false)
```

## Importing a library you already have

Nothing is imported unless you ask. If you have a FlyMemory library from
somewhere else and want to move it in, point the plugin at it once:

```yaml
- id: flymemory
  name: 'dsh-flymemory'
  config:
    port: 8791
    upstreamLibrary: 'D:\path\to\flymemory_v3.pkl'
    seedFromUpstream: true
```

On the next activation the file is copied into place if the target doesn't exist
yet. The original is left alone. The format is the same in both directions, so
you can move it back out later.

## Working on it

```bash
node --check lib/index.js    # syntax
npm test                     # unit tests, no Python needed, ~0.3 s
node tests/engine_smoke.mjs  # end-to-end against the real engine (needs torch)
python tests/http_smoke.py   # protocol check against a running service
```

`npm test` covers the parts that are easy to get wrong: port and library
defaults, config precedence, the generated hook file, endpoint probing. The
engine smoke test runs `apply()` in a throwaway directory against a real Python
engine — it needs torch and sentence-transformers, and prints SKIP if they aren't
there. CI runs the syntax check and the unit tests on Linux, macOS and Windows
across Node 20, 22 and 24.

```
lib/index.js        Host half: port probing, engine supervision, hook config
bin/flymemory.mjs   CLI built on the same helpers
cordis.patch.yml    the three rows this bundle inserts
python/             vendored FlyMemory engine (server, hooks, engine package)
locale/{en,zh}.json title and description for the Plugins page
tests/              unit tests, engine smoke test, protocol smoke test
```

## Rough edges

- **The first start takes 15–20 seconds.** That's torch loading. Set
  `readinessTimeoutMs: 0` if you'd rather the harness start immediately and have
  the tools appear a few seconds later.
- **The Python dependencies are heavy.** The plugin won't install them for you;
  `doctor` tells you what's missing.
- **DSH has no `PreCompact` hook**, so the recovery pack runs at session start
  rather than right after a compaction.
- **Tool names are long.** `mcp__flymemory__flymemory_recall` is what the
  mcp-client naming rule produces — `mcp__<server>__<tool>` — and it's stable,
  just not pretty.
- **One engine per port.** Two harnesses share it; a third instance needs its own
  port and its own library, set explicitly.
- **Recall gets slower as the library grows.** Upstream measured ~11 ms per query
  at about 1,400 entries on CPU.

## License

MIT. See [LICENSE](LICENSE).

The bundled FlyMemory engine is MIT as well, Copyright (c) 2026 Junrong Du.
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) lists exactly which files were
copied, which were adapted, and why.
