# Third-party notices

`dsh-flymemory` is MIT licensed (see [LICENSE](LICENSE)). It bundles a copy of
the FlyMemory engine so the plugin works without a separate checkout.

## FlyMemory (vendored engine)

- Upstream project: <https://github.com/aujurd22/flymemory>
- License: MIT — Copyright (c) 2026 Junrong Du
- Vendored under [`python/`](python/)
- Synced with upstream `7328000` (2026-10-29). Since the previous sync point the
  engine changed in one place: `v3.py` now canonicalises `state_key`
  (`strip() + casefold()`) at the public entry point before dedup and the I1
  active-unique rule. The fix was first written here, then ported upstream
  (`1c1c056`), so the file is byte-identical again. Verified on the public path:
  three writes as `user.phone` / `User.Phone` / `user.phone ` leave one active
  state, `state_lookup` returns the newest value, and `state_history` keeps the
  three-entry chain.
- `mcp_v3.py`, `hook_auto.py`, `hook_compact.py`, `clusters.py` and
  `maintenance.py` did not change since the previous sync point; the three
  adapted files keep their documented differences.
- Upstream also adopted two things this project raised: `flymemory/facts.db` is
  now gitignored (`86fab3e`), and the P-USEGATE verdict was recorded with the
  same reading we acted on (`581e3f7`).
  (multi-app provenance). Included since the first import: the keyed-state write
  fix, the dated age stamps, the FORM/LESSON/aggregation guidance, the lesson
  channel with its low-confidence gate, the automation-message filter, the
  per-entry `app:<name>` provenance tags, and the rebuildable-store maintenance
  tool.

### Files copied byte for byte

| Vendored path | Upstream path |
|---|---|
| `python/flymemory/v3.py` | `flymemory/v3.py` |
| `python/flymemory/encoder.py` | `flymemory/encoder.py` |
| `python/flymemory/hopfield.py` | `flymemory/hopfield.py` |
| `python/flymemory/memory_store.py` | `flymemory/memory_store.py` |
| `python/flymemory/__init__.py` | `flymemory/__init__.py` |
| `python/flymemory/clusters.py` | `flymemory/clusters.py` |

`clusters.py` is an experimental module upstream keeps deliberately outside the
recall path (its own docstring says so); nothing in this plugin imports it. It is
vendored only so the copied `flymemory` package is not missing a module.

`maintenance.py` is the upstream maintenance CLI (`export-facts` to SQLite,
`rebuild-embeddings`). It is not imported by the server either; it is included so
the package is complete. Its default paths point at the package layout and every
path is overridable with `--pkl` / `--to` / `--from` / `--out`, which is what you
want for a data directory outside the package.

### Files copied with adaptations

| Vendored path | Upstream path | Change |
|---|---|---|
| `python/flymemory_server.py` | `flymemory/mcp_v3.py` | Runtime paths became configurable (`--data-dir`, `--db`, `--host`, `--port`, `--write-pid-file`), so the library, logs and pid file no longer sit next to the code — an installed package may be a read-only copy. The built-in fallback port is 8791 instead of 8765, matching this project; the plugin always passes `--port` explicitly, so the fallback only matters when you run the server by hand. Tool behaviour is upstream`s, including the multi-app `app:<name>` tags. One further change: the recall output`s `RECENT CONTEXT (last ~90 min)` block is a clock window over the whole library and entries carry no session id, so with several conversations sharing one store it injected another conversation`s work; it is now off unless `FLYMEMORY_RECENT_MINUTES` is set. |
| `python/hook_auto.py` | `flymemory/hook_auto.py` | The endpoint moved from a hard-coded URL to `--url` → `FLYMEMORY_MCP_URL` → built-in default (8791). The `--client` app tag and the ZCode/Qoder output shapes are upstream verbatim. |
| `python/hook_compact.py` | `flymemory/hook_compact.py` | Same endpoint change. |
| `python/flymemory/maintenance.py` | `flymemory/maintenance.py` | One line: the "service is still running" guard probes `FLYMEMORY_PORT` (default 8765) instead of a hard-coded 8765. |

The upstream MIT notice above applies to all of these files. If you only want
the plugin without a vendored engine, point the row's `scriptPath` at your own
FlyMemory checkout instead (`FLYMEMORY_ENGINE` is not used; use the row config).

### A bug that used to need a local fix

Earlier versions of this copy patched `flymemory_auto` because upstream bound
`recall_parts` only inside one branch, so an empty library raised
`UnboundLocalError` before storing anything. Upstream fixed it in `264aa69`
("recall_parts scoping"), and this copy now carries the upstream code unchanged.
No local tool-behaviour patches remain.

## DeepSeek Harness packages

`@deepseek-ai/dsh-mcp-client` is referenced by module name only. It ships with
DeepSeek Harness and resolves from the dsh installation, so this package declares
no dependency on it and redistributes none of its code. Earlier versions of the
plugin also used `@deepseek-ai/dsh-hooks-claude-code`; DSH 44.7 removed that
package, and the hooks now run inside this plugin instead.
