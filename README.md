# dsh-flymemory

English | [中文](README.zh.md)

**Private long-term memory for [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness).**
A DSH plugin (bundle) that gives the harness its own [FlyMemory](https://github.com/aujurd22/flymemory)
instance: 15 memory tools, automatic recall and capture hooks, and a supervised
local engine — on its **own port** and its **own library file**, so it never
mixes with another FlyMemory installation.

```
DSH session ──┬── mcp__flymemory__flymemory_*   15 tools, model-driven memory
              └── hooks                            automatic recall + capture
                        │
                        ▼
              FlyMemory MCP service on 127.0.0.1:8791
                        │
                        ▼
              $DSH_HOME/flymemory-data/flymemory_v3.pkl
```

## Why

FlyMemory keeps a durable, searchable memory of decisions, findings and entity
state across sessions. Its own MCP server already works with any MCP client, but
wiring it into a harness by hand means running a second service, choosing a port,
registering hooks and keeping all of that alive. This plugin does that part:

- **Independent storage.** Port `8791` and `$DSH_HOME/flymemory-data/flymemory_v3.pkl`.
  The FlyMemory HTTP service other tools use (usually port `8765`) is never
  touched, and no library is imported unless you ask for it.
- **Zero-config.** Install the bundle; the engine starts with the harness and the
  tools appear as `mcp__flymemory__*`.
- **Automatic memory.** Hooks capture each prompt and inject relevant memories
  into the turn, so the model does not have to remember to remember.
- **No dependencies.** The plugin imports nothing from `@deepseek-ai/*` and
  declares no npm dependencies; the two DSH rows it uses ship with the harness.

## Requirements

| | |
|---|---|
| DeepSeek Harness | A build shipping `@deepseek-ai/dsh-mcp-client` and `@deepseek-ai/dsh-hooks-claude-code` (developed against `0.2.0-rc.2`). |
| Node | 20 or newer (for the plugin and its CLI). |
| Python | 3.10+ with `torch`, `sentence-transformers`, `mcp>=1.30,<2`, `numpy`. |
| Disk / network | The multilingual embedder (`paraphrase-multilingual-MiniLM-L12-v2`, ~470 MB) is downloaded once into the HuggingFace cache, then everything runs offline. |

```bash
pip install torch sentence-transformers "mcp>=1.30,<2" numpy
```

The plugin finds the interpreter automatically (`pythonExe`/`FLYMEMORY_PYTHON`,
then the usual install locations, then `PATH`) and verifies that
`sentence_transformers` is importable before choosing one. `dsh-flymemory doctor`
shows what it picked and fixes the usual problems.

## Install

From a checkout, with the absolute path to this package directory:

```
plugin_manager(action: "install_bundle", target: "/absolute/path/to/dsh-flymemory")
```

or the Web sidebar's **Plugins** page, selecting this directory. Installation
adds the package to the profile and selects the bundle; the harness reloads and
three rows become active (`flymemory`, `flymemory-mcp`, `flymemory-hooks`).

Verify:

```bash
dsh-flymemory status      # endpoint, library, interpreter, bundle selection
dsh-flymemory doctor      # interpreter + dependency check
```

Then ask the harness to store something (`mcp__flymemory__flymemory_remember`)
and recall it (`mcp__flymemory__flymemory_recall`).

To remove it again:

```
plugin_manager(action: "remove_bundle", target: "dsh-flymemory")
```

## What you get

| Tool (`mcp__flymemory__…`) | Purpose |
|---|---|
| `flymemory_remember` | Store a finding or decision. Semantic dedup merges near-duplicates; `compartment` scopes a domain; `state_key`/`state_value` record entity state and mechanically supersede the older value. |
| `flymemory_recall` | Hybrid recall — dense embeddings fused with IDF lexical ranking (RRF), decay-weighted, with id/age/source stamps. `include_superseded` reaches history. |
| `flymemory_auto` | One call: recall for the current context **and** store it when novel. This is what the hooks use. |
| `flymemory_recall_index` / `flymemory_get_memory` | Progressive disclosure: cheap one-line index first, full text on demand. |
| `flymemory_supersede` | Mark an outdated memory as replaced (judgement by the calling model). |
| `flymemory_state_lookup` / `flymemory_state_history` | Current value for an entity key / full history of that key. |
| `flymemory_consolidate` | Fold related memories into one higher-order conclusion, keeping the raw entries as evidence. |
| `flymemory_find_conflicts` / `flymemory_insights` | Candidate contradictions; high-value entries about to fade. |
| `flymemory_forget` / `flymemory_cleanup` | Targeted deletion; decay-threshold forgetting. |
| `flymemory_session_pack` | Recent working trail + newest conclusions (the recovery pack). |
| `flymemory_stats` | Size, age, access counts, average decay weight. |

## How it works

The bundle patch inserts three rows:

| Row | Module | Role |
|---|---|---|
| `flymemory` | this package | Probes the port; reuses a FlyMemory service already there, starts the vendored engine otherwise; writes the hook config. |
| `flymemory-mcp` | `@deepseek-ai/dsh-mcp-client` | Bridges the service's tools into the registry as `mcp__flymemory__<tool>`. |
| `flymemory-hooks` | `@deepseek-ai/dsh-hooks-claude-code` | Runs `hook_auto.py` (per prompt) and `hook_compact.py` (per session start). |

Activation order and failure handling:

1. `flymemory` probes `host:port`. If a service there answers `tools/list` with
   `flymemory_*` tools it is reused — several harness processes share one engine
   and one library. A port held by something else is reported, never overwritten.
2. Otherwise the vendored engine starts detached with `--http`, its stdio logged
   to `<dataDir>/engine.log`, and the row waits (default 25 s) until the port
   answers, so the next row connects on its first attempt.
3. If the engine cannot start (no interpreter, missing dependency, occupied
   port), the harness still boots: the tools simply do not appear, the reason is
   in the log, and the mcp-client row keeps retrying with backoff.

## Configuration

Environment variables, read before the harness starts:

| Variable | Default | Meaning |
|---|---|---|
| `FLYMEMORY_MCP_PORT` | `8791` | Service port. |
| `FLYMEMORY_MCP_URL` | `http://127.0.0.1:<port>/mcp` | Full endpoint. |
| `FLYMEMORY_DATA_DIR` | `$DSH_HOME/flymemory-data` | Library, logs, hook config, pid file. |
| `FLYMEMORY_PYTHON` | auto-detected | Interpreter for the engine. |
| `FLYMEMORY_DEVICE` / `FLYMEMORY_MODEL` | `cpu` / multilingual MiniLM | Passed through to the engine. |

Row config (higher priority than the environment), for your profile's
`cordis.patch.yml` — a patch entry replaces the whole `config`, so restate what
you need:

```yaml
- id: flymemory
  name: 'dsh-flymemory'
  config:
    port: 8791
    dataDir: 'D:\dsh-memory'
    libraryPath: 'D:\dsh-memory\my-lib.pkl'
    pythonExe: 'C:\Python313\python.exe'
    device: cpu
    readinessTimeoutMs: 25000   # 0 = start DSH immediately, tools appear later
    shutdownOnDispose: true     # kill the engine this activation started
    writeHooksConfig: true
    seedFromUpstream: false     # never import another installation implicitly
    upstreamLibrary: ''         # set both to import an existing library once
    autostart: true
```

| Option | Default | Meaning |
|---|---|---|
| `port` / `host` / `mcpPath` / `mcpUrl` | `8791` / `127.0.0.1` / `/mcp` / derived | Endpoint. |
| `dataDir` / `libraryPath` | `$DSH_HOME/flymemory-data` / `<dataDir>/flymemory_v3.pkl` | Files this plugin owns. |
| `pythonExe` / `verifyInterpreter` | auto-detect / `true` | Interpreter selection. |
| `scriptPath` | `<package>/python/flymemory_server.py` | Use your own FlyMemory checkout instead of the vendored engine. |
| `seedFromUpstream` / `upstreamLibrary` | `false` / `''` | One-time import of another library (the source is copied, never moved). |
| `autostart` | `true` | Start the engine when nothing answers. |
| `writeHooksConfig` | `true` | Regenerate `<dataDir>/hooks.json` on every activation. |
| `shutdownOnDispose` | `true` | Stop the engine if this activation started it. |
| `readinessTimeoutMs` / `connectTimeoutMs` / `probeTimeoutMs` | `25000` / `600` / `4000` | Timing. |
| `device` / `model` | engine defaults | `FLYMEMORY_DEVICE` / `FLYMEMORY_MODEL` for the child. |

## Privacy: the hooks write, and you should know it

The `flymemory-hooks` row is **on by default**, because automatic memory is the
point of the plugin. It does two things:

- `UserPromptSubmit` stores every user prompt (`source=hook`, unjudged) and
  appends recalled memories to the turn (roughly 1.5 KB of context per turn).
- `SessionStart` injects a recovery pack of the recent working trail.

Everything lands in this plugin's own library. To run with the tools only:

```
plugin_manager(action: "set_plugin", target: "include:flymemory-hooks", enabled: false)
```

Credential-shaped content is refused before it is stored (the upstream filter
covers `ghp_`, `github_pat_`, `sk-ant-`, `sk-proj-`, `AKIA`, `xoxb`, private-key
headers, …), hooks never block a turn and exit 0 on any error. Memory is plain
files on your machine: `<dataDir>/flymemory_v3.pkl` plus logs. Nothing is sent
anywhere.

## CLI

From a checkout, run `node bin/flymemory.mjs <command>`. Once the bundle is
installed, the same CLI is linked into the profile
(`<profile>/node_modules/.bin/dsh-flymemory`), so `npx dsh-flymemory <command>`
or that path works from anywhere.

```bash
dsh-flymemory status     # resolved config, live endpoint, tool count, bundle selection
dsh-flymemory start      # start the service now (detached)
dsh-flymemory stop       # stop the process recorded in server.pid
dsh-flymemory log -n 40  # tail engine.log and server.log
dsh-flymemory doctor     # interpreter, dependencies, endpoint, paths, install hints
```

Accepted by every command: `--port`, `--host`, `--data-dir`, `--library`,
`--python`.

## Importing an existing FlyMemory library

The plugin keeps its store separate on purpose. If you do want to move an
existing library in, do it once and explicitly:

```yaml
- id: flymemory
  name: 'dsh-flymemory'
  config:
    port: 8791
    upstreamLibrary: 'D:\path\to\flymemory_v3.pkl'
    seedFromUpstream: true
```

On the next activation the file is copied to `libraryPath` if it is not there
yet; the source is left in place. The library format is the upstream schema, so
files move in both directions.

## Development

```bash
node --check lib/index.js        # syntax
node --test tests/               # unit tests: config, hooks config, endpoint probing
node tests/engine_smoke.mjs      # full engine round trip (needs torch + sentence-transformers)
python tests/http_smoke.py       # protocol-level smoke against a live endpoint
```

`engine_smoke.mjs` runs the real `apply()` in a throwaway data directory, so it
never touches your memory; it prints SKIP when no suitable interpreter exists.
CI runs the syntax check and the unit tests on Linux, macOS and Windows.

Layout:

```
lib/index.js        Host half: port probing, engine supervision, hook config
bin/flymemory.mjs   CLI over the same helpers
cordis.patch.yml    the three rows this bundle inserts
python/             vendored FlyMemory engine (server + hooks + engine package)
locale/{en,zh}.json Plugins-page title and description
tests/              unit tests, engine smoke test, protocol smoke test
```

## Limitations

- **Cold start is slow.** Importing torch takes ~15–20 s before the engine opens
  its port; the first activation waits for it. Reusing an already running service
  is instant. Set `readinessTimeoutMs: 0` to skip the wait.
- **Heavy Python dependency.** The engine needs torch and sentence-transformers.
  The plugin does not install them for you; `doctor` tells you what is missing.
- **DSH has no `PreCompact`/`PostCompact` hook.** The recovery pack therefore
  runs on `SessionStart` instead of immediately after compaction.
- **Long tool names.** `mcp__flymemory__flymemory_recall` is the mcp-client
  naming contract (`mcp__<serverName>__<rawName>`), stable but not short.
- **One engine per port.** Concurrent harnesses share it; a second engine would
  need a second port and a second library, configured explicitly.
- **Recall cost grows with the library.** Upstream benchmarks measured ~11 ms per
  query at ~1 400 entries; a much larger store is slower per query.
- Built and tested against DeepSeek Harness `0.2.0-rc.2`. The plugin only uses two
  shipped rows and imports no harness code, so harness upgrades should not need
  changes here — but the rows' config schemas are the harness's to change.

## License

MIT — see [LICENSE](LICENSE).

The vendored FlyMemory engine is MIT, Copyright (c) 2026 Junrong Du; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the exact file list and the
adaptations made.
