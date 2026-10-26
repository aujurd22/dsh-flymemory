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
 * Shape one memory digest for the prompt.
 *
 * The service returns a plain-text pack; this bounds it by lines and characters
 * so a verbose store cannot crowd out the conversation, and wraps it in a tag
 * that says what it is (data, not instructions).
 *
 * @param {string} pack - raw `flymemory_session_pack` / recall text.
 * @param {{maxChars?: number, maxLines?: number, label?: string}} [options]
 * @returns {string} the prompt block, or '' when there is nothing worth sending.
 */
export function renderWorkingContext(pack, options = {}) {
  const maxChars = Math.max(120, Number(options.maxChars ?? 1200))
  const maxLines = Math.max(2, Number(options.maxLines ?? 14))
  const label = String(options.label || 'working state carried from earlier sessions')
  const open = `<working-context source="flymemory" note="${label}">`
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
    /** Prompt text for one assembly; '' until a digest has been fetched. */
    textFor(assembleContext) {
      const id = assembleContext?.agent?.id
      if (id && byAgent.has(id)) return byAgent.get(id).text
      return latest
    },

    /**
     * Refresh the digest for an agent, at most once per `workingContextRefreshMs`.
     * Never throws: a down memory service leaves the previous digest in place.
     * @returns {Promise<boolean>} true when a new digest was stored.
     */
    async refresh(agentId, log) {
      const key = agentId || 'global'
      const at = now()
      const previous = lastAttempt.get(key)
      // The first attempt for a key always runs; later ones are throttled.
      if (previous !== undefined && at - previous < options.workingContextRefreshMs) return false
      lastAttempt.set(key, at)
      try {
        const pack = await deps.fetchDigest(options.workingContextMinutes)
        const text = renderWorkingContext(pack, {
          maxChars: options.workingContextChars,
          maxLines: options.workingContextLines,
        })
        if (!text) return false
        byAgent.set(key, { text, at })
        latest = text
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
        await band.refresh(id, log)
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
