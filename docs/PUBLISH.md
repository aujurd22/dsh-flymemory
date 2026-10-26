# Publishing this plugin

Status of the four things every DSH plugin directory checks, and the copy-paste
kit for submitting to the ones that need it.

## Checklist

| Requirement | Status |
|---|---|
| Public GitHub repository, source visible | yes — <https://github.com/aujurd22/dsh-flymemory> |
| `dsh-plugin` topic on the repo | yes (`dsh-plugin`, plus `dsh`, `dsh-bundle`, `deepseek-harness`, `mcp`, `memory`, `agent-memory`, `long-term-memory`, `flymemory`) |
| README with a copyable install command | yes — `dsh plugin --profile desktop add dsh-flymemory` |
| Plugin exports `apply(ctx)` | yes — `lib/index.js`; the bundle patch mounts it as the `flymemory` row |
| `package.json` declares `dsh.bundle.patch` | yes — `cordis.patch.yml` |
| Clear license (SPDX) | MIT, `LICENSE` + `package.json` |
| Version compatibility stated | DSH `0.2.0-rc.2`, verified on `44.7.0` (README) |
| Installed as an npm package | **yes** — [`dsh-flymemory@1.0.0`](https://www.npmjs.com/package/dsh-flymemory), published 2026-10-26 |
| Listed in awesome-dsh-plugin | **in review** — [PR #9562](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin/pull/9562), checks green 2026-10-26 |

Topic scanning is the widest net: dshfind, dsh-market and similar aggregators
crawl the `dsh-plugin` topic, so the repository is already discoverable there.
Being listed is not the same as being verified — hubs list new plugins as
`unconfirmed` until someone checks them.

## npm

Published: [`dsh-flymemory@1.0.0`](https://www.npmjs.com/package/dsh-flymemory)
(2026-10-26, 243 KB unpacked, 23 files). Verified by installing it into a clean
directory and importing `lib/index.js` from the installed copy.

Releasing a new version:

```bash
npm version patch          # or minor / major — updates package.json + git tag
npm publish --access public
```

Publishing needs either a one-time password (`--otp=<code>`) or a **granular
access token with "Bypass 2FA" checked and `Read and write (publish and stage)`**
permission. npm is retiring direct publishing with bypass-2FA tokens in January
2027; after that, use `npm stage publish` plus approval, or Trusted Publishing.

Note: the registry lists a `0.0.0-stage` placeholder version. npm creates it as
part of the staged-publish bookkeeping; `latest` points at the real release and
the placeholder is not installed.

The `files` list keeps the published tarball to `lib/`, `bin/`, `python/`,
`locale/`, `examples/`, `cordis.patch.yml`, `icon.svg` and the docs — no test
fixtures and no memory library.

## Release checklist

Who notices a new version:

| Layer | Detection | Do we act? |
|---|---|---|
| npm registry | `latest` dist-tag; the install command resolves it | publishing is the release |
| A user's DSH | **nothing polls npm** — they re-run the install command | no |
| dsh-plugin.org / dshopen / dsh.so | they crawl repo metadata + registry | no, syncs on its own |
| awesome-dsh-plugin | the entry file has no version field | no (only if the description changes) |

So a release is one command:

```bash
node scripts/release.mjs patch      # tests -> bump -> commit -> tag -> push -> publish -> verify
node scripts/release.mjs minor --dry-run
```

It refuses to release a version that is not newer, prints the plan with
`--dry-run`, and verifies the registry actually serves the new version before it
exits. Authentication comes from npm itself (`npm login`, or
`NPM_CONFIG_USERCONFIG=<file with a publish token>`), so the script never touches
credentials.

Two things the script cannot check, both easy to forget:

- **vendored engine** — `python/flymemory/*.py` mirrors upstream. Re-syncing means
  updating the sync point in `THIRD_PARTY_NOTICES.md` and re-checking the
  byte-identical list;
- **`examples/early-band.patch.yml`** pins a copy of the standard agent preset.
  If DSH changes that preset, regenerate the example, or new preset rows stay
  hidden behind the pinned copy.

Users upgrade with the same command that installed the plugin; there is no
auto-update. A symlinked dev install (`link:`) tracks the working tree instead,
so reloading the plugin is enough there.

## Submission kit

### dsh-plugin.org / DSH Plugin Hub (submit form)

Requirements: public repo + topic + install command + `apply(ctx)`. All met.
Listing text:

> **Name:** dsh-flymemory
> **One-liner:** Long-term memory for DeepSeek Harness — 15 memory tools, automatic recall/capture, and a private local engine, with the context kept in a band.
> **What changes after install:** the model gets `mcp__flymemory__*` tools for storing, searching, superseding and consolidating memory; every typed prompt is captured and relevant memories are recalled into the turn; a bounded memory index is published per request; compaction checkpoints are archived back into the store.
> **Requirements:** Python 3.10+ with torch + sentence-transformers; a base-backed profile.
> **Compatibility:** DSH 0.2.0-rc.2, verified on 44.7.0.

### dshopen.org (Open Plugin Market — GitHub issue)

Issue title: `[plugin] dsh-flymemory — long-term memory for DSH`
Body: the one-liner and "what changes after install" above, plus the repo URL,
license, npm name (once published) and the install command.

### awesome-dsh-plugin (pull request)

Add `data/plugins/aujurd22__dsh-flymemory.yml`, following that repository's
`CONTRIBUTING.md` schema (fetch it before opening the PR; it can change). Then PR
with the description: "add dsh-flymemory (long-term memory plugin, MIT)".

### dsh.so / dshmarketplace.dev

Both accept a repository URL (or npm package name) and run an automated
scan — no PR needed. Paste <https://github.com/aujurd22/dsh-flymemory>.

## After listing

- Keep the compatibility note in the README current; preview releases move.
- The directories sync stars, description and README automatically, so the repo
  `description` is the listing card — keep it a one-line value statement.
- A listing badge can be added to the README once a listing URL exists.
