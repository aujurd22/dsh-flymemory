# Third-party notices

`dsh-flymemory` is MIT licensed (see [LICENSE](LICENSE)). It bundles a copy of
the FlyMemory engine so the plugin works without a separate checkout.

## FlyMemory (vendored engine)

- Upstream project: <https://github.com/aujurd22/flymemory>
- License: MIT — Copyright (c) 2026 Junrong Du
- Vendored under [`python/`](python/)

### Files copied byte for byte

| Vendored path | Upstream path |
|---|---|
| `python/flymemory/v3.py` | `flymemory/v3.py` |
| `python/flymemory/encoder.py` | `flymemory/encoder.py` |
| `python/flymemory/hopfield.py` | `flymemory/hopfield.py` |
| `python/flymemory/memory_store.py` | `flymemory/memory_store.py` |
| `python/flymemory/__init__.py` | `flymemory/__init__.py` |

### Files copied with adaptations

| Vendored path | Upstream path | Change |
|---|---|---|
| `python/flymemory_server.py` | `flymemory/mcp_v3.py` | Runtime paths became configurable (`--data-dir`, `--db`, `--host`, `--port`, `--write-pid-file`), so the library, logs and pid file no longer sit next to the code — an installed package may be a read-only copy. The built-in fallback port is 8791 instead of 8765, matching this project; the plugin always passes `--port` explicitly, so the fallback only matters when you run the server by hand. No tool behaviour otherwise. |
| `python/hook_auto.py` | `flymemory/hook_auto.py` | The endpoint moved from a hard-coded URL to `--url` → `FLYMEMORY_MCP_URL` → built-in default (8791). |
| `python/hook_compact.py` | `flymemory/hook_compact.py` | Same endpoint change. |

The upstream MIT notice above applies to all of these files. If you only want
the plugin without a vendored engine, point the row's `scriptPath` at your own
FlyMemory checkout instead (`FLYMEMORY_ENGINE` is not used; use the row config).

### Local fix carried in `python/flymemory_server.py`

`flymemory_auto` raised `UnboundLocalError: cannot access local variable
'recall_parts'` whenever the library was empty, because the upstream code binds
that variable only inside the `if results:` branch. On an empty library the tool
failed *before* storing anything, so the auto-capture path broke on exactly the
state a fresh installation starts in. The vendored copy binds `recall_parts`
before the branch; the behaviour on a non-empty library is unchanged. Worth
fixing upstream as well — reported here rather than patched in the original
project.

## DeepSeek Harness packages

`@deepseek-ai/dsh-mcp-client` and `@deepseek-ai/dsh-hooks-claude-code` are
referenced by module name only. They ship with DeepSeek Harness and resolve from
the dsh installation, so this package declares no dependency on them and
redistributes none of their code.
