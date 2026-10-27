/**
 * FlyMemory context band — keep the model's working context in a range instead
 * of letting it grow until the window is full.
 *
 * Two mechanisms, both in-contract and additive:
 *
 *   1. A per-request *working context*: a bounded digest rebuilt from the memory
 *      service (recent trail + standing conclusions) and published through
 *      `ctx.systemPrompt.context()`, whose `text` callback runs on every
 *      assembly. This is the "page-in" half of the MemGPT idea: the model always
 *      starts a request with the state that matters, whatever fell out of the
 *      verbatim window.
 *
 *   2. Memory keeps what compaction condenses: when the session logs a
 *      `compaction/summary`, its text is stored back into FlyMemory, so the
 *      durable store stays the authority and details can be recalled later.
 *
 * The third piece — making compaction fire early instead of at 80% of the
 * window — is a composition choice (the `compaction-basic` row lives in the
 * agent preset), shipped as `examples/early-band.patch.yml` rather than forced
 * on every profile.
 *
 * Prior art this borrows from:
 *   - MemGPT/Letta (arXiv 2310.08560): context window as RAM, external store as
 *     disk, with explicit paging between them.
 *   - InfiAgent: keep the reasoning context bounded by reconstructing it from a
 *     state snapshot plus a fixed recent window, rather than compressing on
 *     overflow.
 *   - Chroma's "context rot" measurements: long contexts degrade, so the useful
 *     move is fewer, higher-signal tokens rather than a bigger window.
 *
 * @module dsh-flymemory/context
 */
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

/** Registered name of the dynamic prompt context. */
export const WORKING_CONTEXT_NAME = 'flymemory-working-context'

/** Registered name of the summarizer contract section. */
export const CHECKPOINT_SECTION_NAME = 'flymemory:checkpoint-contract'

/** What a condensed checkpoint must keep, derived from how the store is shaped. */
export const CHECKPOINT_CONTRACT = [
  'Condensing this conversation: keep every decision, open thread, entity state',
  '(who/what/which version) and dated fact that later turns may rely on. Prefer',
  'terse bullets over prose; keep exact paths, commands, identifiers and numbers.',
  'Entries tagged flymemory-working-context are live memory state — preserve the',
  'facts they name, and drop facts they contradict.',
].join(' ')

/**
 * Shape one memory digest for the prompt — as an INDEX of pointers.
 *
 * A/B evidence (upstream P-USEGATE, 2026-10-26): a 70-80 char truncation reads
 * like the whole entry, while an explicit pointer ("id + one-line + fetch the
 * full text with get_memory") is what the model can actually act on. The
 * coverage gain measured there came from *expansion*, which cost 12x tokens and
 * is therefore NOT done here: this block only points, and the model pulls.
 *
 * Bounded by lines and characters so a verbose store cannot crowd out the
 * conversation.
 *
 * @param {string} pack - raw `flymemory_session_pack` / recall text.
 * @param {{maxChars?: number, maxLines?: number, label?: string}} [options]
 * @returns {string} the prompt block, or '' when there is nothing worth sending.
 */
export function renderWorkingContext(pack, options = {}) {
  const maxChars = Math.max(120, Number(options.maxChars ?? 1200))
  const maxLines = Math.max(2, Number(options.maxLines ?? 14))
  const label = String(options.label || 'index of remembered context; call flymemory_get_memory(id) to read an entry')
  const open = `<working-context source="flymemory" type="index" note="${label}">`
  const close = '</working-context>'
  // `maxChars` bounds the whole injected block, wrapper included.
  const budget = Math.max(40, maxChars - open.length - close.length - 2)
  const lines = String(pack || '')
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter((line) => line.trim().length > 0)
    // Drop the pack's own framing lines; the wrapper below supplies them.
    .filter((line) => !/^(RECENT SESSION TRAIL|RECENT CONTEXT|ACTIVE LONG-TERM CONCLUSIONS)\s*\(?/i.test(line.trim()))
  if (lines.length === 0) return ''
  const body = []
  let used = 0
  for (const line of lines) {
    if (body.length >= maxLines) break
    const room = budget - used - 1
    if (room < 24) break
    const trimmed = line.length > room ? `${line.slice(0, Math.max(0, room - 1))}…` : line
    body.push(trimmed)
    used += trimmed.length + 1
  }
  if (body.length === 0) return ''
  return [open, ...body, close].join('\n')
}

/**
 * The ids referenced by a digest — the pointers that were actually shown.
 * Mirrors how the recall output stamps entries (`[#123 | …]`).
 * @returns {number[]} unique ids in first-seen order.
 */
export function pointerIdsOf(text) {
  const ids = []
  for (const match of String(text || '').matchAll(/#(\d+)/g)) {
    const id = Number(match[1])
    if (Number.isFinite(id) && !ids.includes(id)) ids.push(id)
  }
  return ids
}

/**
 * The summary text carried by a `compaction/summary` session event.
 *
 * The event shape is upstream's; this reads the plausible fields and returns ''
 * for anything else, so an unrelated event can never be stored as a checkpoint.
 */
export function checkpointTextOf(event) {
  if (!event || typeof event !== 'object') return ''
  if (typeof event.type === 'string' && !event.type.startsWith('compaction/')) return ''
  const candidates = [event.summary, event.text, event.content, event.payload?.summary, event.payload?.text]
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && candidate.trim().length >= 40) return candidate.trim()
    if (Array.isArray(candidate)) {
      const text = candidate
        .map((block) => (block && typeof block.text === 'string' ? block.text : ''))
        .join('\n')
        .trim()
      if (text.length >= 40) return text
    }
  }
  return ''
}

/**
 * Cache and throttle for the working-context digest.
 *
 * `text` callbacks on the prompt registry are synchronous and run on every
 * assembly, so the digest is fetched ahead of time (in `agent/pre-step`) and
 * served from cache. `latest` is the fallback for assemblies that do not name an
 * agent, which is the common single-agent desktop case.
 *
 * @param {object} options - resolved plugin options.
 * @param {{fetchDigest: (minutes: number) => Promise<string>, now?: () => number}} deps
 */
export function createContextBand(options, deps = {}) {
  const now = deps.now ?? (() => Date.now())
  const byAgent = new Map()
  const lastAttempt = new Map()
  let latest = ''

  return {
    /** Prompt text for one assembly; '' until a digest has been fetched, or while muted. */
    textFor(assembleContext) {
      if (deps.isMuted?.()) return ''
      const id = assembleContext?.agent?.id
      if (id && byAgent.has(id)) return byAgent.get(id).text
      return latest
    },

    /**
     * Refresh the digest for an agent, at most once per `workingContextRefreshMs`.
     * Never throws: a down memory service leaves the previous digest in place.
     *
     * `query` is the typed text entering this step. When the band is
     * query-scoped (`workingContextRequireQuery`), no query means no fetch: a
     * time-windowed digest is store-wide and would pull another conversation's
     * work into this one.
     *
     * @returns {Promise<boolean>} true when a new digest was stored.
     */
    async refresh(agentId, log, query) {
      if (deps.isMuted?.()) return false
      if (options.workingContextRequireQuery && !query) return false
      const key = agentId || 'global'
      const at = now()
      const previous = lastAttempt.get(key)
      // The first attempt for a key always runs; later ones are throttled.
      if (previous !== undefined && at - previous < options.workingContextRefreshMs) return false
      lastAttempt.set(key, at)
      try {
        const pack = await deps.fetchDigest({ minutes: options.workingContextMinutes, query })
        const text = renderWorkingContext(pack, {
          maxChars: options.workingContextChars,
          maxLines: options.workingContextLines,
        })
        if (!text) return false
        byAgent.set(key, { text, at })
        latest = text
        // use_gate: stamp exactly the pointers that reached the prompt.
        deps.onInjected?.(key, pointerIdsOf(text))
        return true
      } catch (error) {
        log?.warn?.(`flymemory: working context refresh failed: ${String(error)}`)
        return false
      }
    },

    /** Diagnostics for `state.json` / the CLI. */
    stats() {
      return { chars: latest.length, agents: byAgent.size }
    },
  }
}

/**
 * Wire the band into the harness.
 *
 * Registers three things, each independently optional and each failing softly:
 * the dynamic prompt context, the throttled refresh, and the checkpoint archive.
 * Optional context is used on purpose so `suppressRuntimeContext()` can drop it.
 *
 * @param ctx - plugin context.
 * @param {object} options - resolved plugin options.
 * @param {ReturnType<typeof createContextBand>} band - the cache.
 * @param {{info?: Function, warn?: Function}} log
 * @param {{storeCheckpoint: (text: string) => Promise<unknown>}} deps
 */
export function registerContextBand(ctx, options, band, log, deps) {
  if (options.workingContext) {
    ctx.inject(['systemPrompt'], (scoped) => {
      scoped.systemPrompt.context({
        name: WORKING_CONTEXT_NAME,
        order: options.contextOrder,
        text: (assembleContext) => band.textFor(assembleContext),
        interpolate: false,
        required: false,
      })
      log?.info?.(`flymemory: working-context prompt context registered (order ${options.contextOrder})`)
    })
  }

  // Keep the cached digest fresh without delaying assembly: the refresh runs
  // when a step opens, throttled, and never modifies the step.
  const refreshing = new Set()
  ctx.on('agent/pre-step', async ({ agent }, next) => {
    const decision = await next()
    if (!options.workingContext) return decision
    const id = agent?.id
    if (id && !refreshing.has(id)) {
      refreshing.add(id)
      try {
        // Scope the digest to the text entering this step, not to a clock.
        await band.refresh(id, log, deps.queryOf?.(decision) || '')
      } finally {
        refreshing.delete(id)
      }
    }
    return decision
  })

  if (options.archiveCheckpoints) {
    ctx.on('session/event', (session, event) => {
      const text = checkpointTextOf(event)
      if (!text) return
      void deps.storeCheckpoint(text).catch((error) => {
        log?.warn?.(`flymemory: checkpoint not archived: ${String(error)}`)
      })
    })
  }

  if (options.checkpointContract) {
    ctx.inject(['systemPrompt'], (scoped) => {
      scoped.systemPrompt.section({
        name: CHECKPOINT_SECTION_NAME,
        order: options.contractOrder,
        text: CHECKPOINT_CONTRACT,
        interpolate: false,
      })
    })
  }
}

// ---------------------------------------------------------------------------
// use_gate ledger — upstream P-USEGATE Change 1 (adopted 2026-10-26)
// ---------------------------------------------------------------------------

/**
 * Accounting for injected pointers.
 *
 * Upstream registration (research/RESEARCH.md, P-2026-10-26-USEGATE): every
 * injection stamps its ids into a per-session ledger, and a later use_gate
 * query reports which ids were expanded, re-called or superseded. The bench's
 * structure is `ledger: injected id -> uses` per session; this keeps that shape
 * and adds the event kind, so a server-side query can ingest the JSONL without
 * translation.
 *
 * Only ids we actually showed are accounted — a tool call that touches an id
 * the prompt never pointed at is not an injection→use event.
 *
 * @param {object} options - resolved plugin options.
 * @param {{now?: () => number, append?: (record: object) => void}} deps
 */
export function createUseGate(options, deps = {}) {
  const now = deps.now ?? (() => Date.now())
  const sessions = new Map() // session -> Map<id, {count, first, last, events:Set}>
  const emit = (record) => {
    try { deps.append?.(record) } catch { /* the ledger is advisory */ }
  }

  function slot(session, id) {
    let byId = sessions.get(session)
    if (!byId) {
      byId = new Map()
      sessions.set(session, byId)
    }
    let entry = byId.get(id)
    if (!entry) {
      entry = { id, count: 0, first: now(), last: now(), events: new Set() }
      byId.set(id, entry)
    }
    return entry
  }

  return {
    /** Record the pointers that just reached a session's prompt. */
    injected(session, ids) {
      const list = Array.isArray(ids) ? ids : []
      for (const id of list) {
        const entry = slot(session, id)
        entry.count += 1
        entry.last = now()
        if (!entry.events.has('injected')) {
          entry.events.add('injected')
          emit({ v: 1, session, id, event: 'injected', at: entry.first })
        }
      }
      return list.length
    },

    /** Attribute one finished tool call to previously injected pointers. */
    observe({ session, toolName, args, resultText }) {
      if (!session) return 0
      const byId = sessions.get(session)
      if (!byId) return 0
      let hits = 0
      const mark = (id, event) => {
        if (!Number.isFinite(id)) return
        const entry = byId.get(id)
        if (!entry || entry.events.has(event)) return
        entry.events.add(event)
        hits += 1
        emit({ v: 1, session, id, event, at: now() })
      }
      const name = String(toolName || '')
      // Through the MCP bridge the model calls `mcp__<server>__<rawName>`, e.g.
      // `mcp__flymemory__flymemory_get_memory`; the ledger keys on the raw name.
      const raw = name.includes('__') ? name.slice(name.lastIndexOf('__') + 2) : name
      if (raw === 'flymemory_get_memory') {
        mark(Number(args?.memory_id), 'expanded')
      } else if (raw === 'flymemory_supersede') {
        mark(Number(args?.old_memory_id), 'superseded')
      } else if (raw.startsWith('flymemory_')) {
        for (const id of pointerIdsOf(resultText)) mark(id, 'recalled')
      }
      return hits
    },

    /** Bench-shaped aggregate: injected, used, rate, hot ids. */
    stats() {
      const out = {
        sessions: sessions.size, injected: 0, used: 0,
        expanded: 0, recalled: 0, superseded: 0, useRate: 0, hot: [],
      }
      const counts = new Map()
      for (const byId of sessions.values()) {
        for (const entry of byId.values()) {
          out.injected += 1
          const used = entry.events.has('expanded') || entry.events.has('recalled') || entry.events.has('superseded')
          if (used) out.used += 1
          if (entry.events.has('expanded')) out.expanded += 1
          if (entry.events.has('recalled')) out.recalled += 1
          if (entry.events.has('superseded')) out.superseded += 1
          counts.set(entry.id, (counts.get(entry.id) || 0) + entry.count)
        }
      }
      out.useRate = out.injected === 0 ? 0 : out.used / out.injected
      out.hot = [...counts.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
        .map(([id, count]) => ({ id, count }))
      return out
    },

    /** Per-session rows for `dsh-flymemory ledger`. */
    bySession() {
      return [...sessions.entries()].map(([session, byId]) => ({
        session,
        injected: byId.size,
        used: [...byId.values()].filter(
          (e) => e.events.has('expanded') || e.events.has('recalled') || e.events.has('superseded'),
        ).length,
      }))
    },
  }
}

/**
 * Observe tool results so expansions and re-calls are attributed.
 * `tools/result` is the frozen final outcome; its arguments are the tool call's.
 */
export function registerUseGate(ctx, options, gate, log) {
  if (!options.useGate) return
  ctx.on('tools/result', (exec, result) => {
    try {
      const text = Array.isArray(result?.content)
        ? result.content.map((block) => (block && typeof block.text === 'string' ? block.text : '')).join('\n')
        : ''
      gate.observe({
        session: exec?.agent?.id,
        toolName: exec?.name,
        args: exec?.arguments,
        resultText: text,
      })
    } catch (error) {
      log?.warn?.(`flymemory: use_gate observe failed: ${String(error)}`)
    }
  })
}

// ---------------------------------------------------------------------------
// Mute switch — the per-turn cost of the working context, on demand
// ---------------------------------------------------------------------------

/**
 * Read the mute state. A mute is "until", so an expired file simply stops
 * counting; nothing has to be reset.
 * @returns {{until: number, at: number, min: number}|null}
 */
export function readMute(dataDir, now = Date.now()) {
  try {
    const state = JSON.parse(readFileSync(join(dataDir, 'mute.json'), 'utf8'))
    const until = Number(state?.until ?? 0)
    if (!Number.isFinite(until) || until <= now) return null
    return { until, at: Number(state.at ?? 0), min: Math.max(1, Math.ceil((until - now) / 60000)) }
  } catch {
    return null
  }
}

/**
 * Set or clear the mute. `until <= now` clears it.
 * @returns the file path written, or '' when cleared.
 */
export function writeMute(dataDir, until, now = Date.now()) {
  mkdirSync(dataDir, { recursive: true })
  const path = join(dataDir, 'mute.json')
  if (!until || until <= now) {
    try { rmSync(path) } catch { /* already gone */ }
    return ''
  }
  writeFileSync(path, `${JSON.stringify({ until, at: now }, null, 2)}\n`, 'utf8')
  return path
}
