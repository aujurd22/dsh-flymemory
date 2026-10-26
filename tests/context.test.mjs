/**
 * Context-band unit tests: the pure builders plus the prompt-context wiring,
 * against a stub context. No Python, no network.
 */
import assert from 'node:assert/strict'
import { after, before, describe, it } from 'node:test'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import {
  CHECKPOINT_CONTRACT,
  CHECKPOINT_SECTION_NAME,
  WORKING_CONTEXT_NAME,
  checkpointTextOf,
  createContextBand,
  registerContextBand,
  renderWorkingContext,
} from '../lib/context.js'

const PACK = [
  'RECENT SESSION TRAIL (oldest → newest):',
  '  [#9001 | 12:40 | hook] fix the payment icon on the checkout page',
  '  [#9002 | 12:41 | hook] the Mercado Pago badge must be the official asset',
  '',
  'ACTIVE LONG-TERM CONCLUSIONS (newest first):',
  '  [#8990 | 10-24 00:49 | model] payment methods use official brand assets only',
].join('\n')

let scratch
before(() => {
  scratch = mkdtempSync(join(tmpdir(), 'dsh-flymemory-context-'))
})
after(() => {
  rmSync(scratch, { recursive: true, force: true })
})

const options = (extra = {}) => ({
  workingContext: true,
  workingContextChars: 1200,
  workingContextLines: 14,
  workingContextMinutes: 240,
  workingContextRefreshMs: 45000,
  workingContextTimeoutMs: 2500,
  contextOrder: 15000,
  checkpointContract: true,
  contractOrder: 12000,
  archiveCheckpoints: true,
  hookTimeoutMs: 6000,
  ...extra,
})

describe('renderWorkingContext', () => {
  it('wraps the digest and drops the pack framing', () => {
    const text = renderWorkingContext(PACK)
    assert.match(text, /^<working-context source="flymemory"/)
    assert.match(text, /<\/working-context>$/)
    assert.match(text, /payment icon/)
    assert.doesNotMatch(text, /^RECENT SESSION TRAIL/m)
    assert.doesNotMatch(text, /^ACTIVE LONG-TERM CONCLUSIONS/m)
  })

  it('bounds lines and characters', () => {
    const long = Array.from({ length: 40 }, (_, i) => `line ${i} ${'x'.repeat(80)}`).join('\n')
    const byLines = renderWorkingContext(long, { maxLines: 5 })
    assert.equal(byLines.split('\n').length, 7) // open + 5 + close
    const byChars = renderWorkingContext(long, { maxChars: 400, maxLines: 40 })
    assert.ok(byChars.length <= 400, `the whole block stays within maxChars, got ${byChars.length}`)
    assert.match(byChars, /…/, 'a clipped line is marked')
  })

  it('returns nothing for an empty pack', () => {
    assert.equal(renderWorkingContext(''), '')
    assert.equal(renderWorkingContext('\n\n  \n'), '')
  })
})

describe('checkpointTextOf', () => {
  it('reads the summary text of a compaction event', () => {
    assert.match(checkpointTextOf({ type: 'compaction/summary', summary: 'x'.repeat(60) }), /^x{60}$/)
    assert.match(checkpointTextOf({ type: 'compaction/summary', content: [{ type: 'text', text: 'y'.repeat(50) }] }), /^y{50}$/)
  })

  it('ignores unrelated or tiny events', () => {
    assert.equal(checkpointTextOf({ type: 'tool/result', summary: 'z'.repeat(80) }), '')
    assert.equal(checkpointTextOf({ type: 'compaction/summary', summary: 'too short' }), '')
    assert.equal(checkpointTextOf(null), '')
  })
})

describe('createContextBand', () => {
  it('serves the fetched digest and throttles refreshes', async () => {
    let calls = 0
    let clock = 1000
    const band = createContextBand(options(), {
      fetchDigest: async () => { calls += 1; return PACK },
      now: () => clock,
    })
    assert.equal(band.textFor({ agent: { id: 'a' } }), '', 'nothing before the first refresh')
    assert.equal(await band.refresh('a'), true)
    assert.equal(calls, 1)
    assert.match(band.textFor({ agent: { id: 'a' } }), /payment icon/)
    assert.match(band.textFor({}), /payment icon/, 'fallback serves the latest digest')
    // inside the refresh window: no second call
    clock += 1000
    assert.equal(await band.refresh('a'), false)
    assert.equal(calls, 1)
    // after the window: refreshed again
    clock += 60000
    assert.equal(await band.refresh('a'), true)
    assert.equal(calls, 2)
    assert.deepEqual(band.stats().agents, 1)
  })

  it('keeps the previous digest when the service is down', async () => {
    const band = createContextBand(options({ workingContextRefreshMs: 0 }), {
      fetchDigest: async () => { throw new Error('down') },
    })
    assert.equal(await band.refresh('a'), false)
    assert.equal(band.textFor({}), '')
  })
})

describe('registerContextBand', () => {
  function stubCtx() {
    const listeners = {}
    const injected = []
    const contexts = []
    const sections = []
    const ctx = {
      on: (name, fn) => { listeners[name] = fn; return () => {} },
      inject: (deps, callback) => {
        injected.push(deps)
        callback({
          systemPrompt: {
            context: (entry) => { contexts.push(entry); return () => {} },
            section: (entry) => { sections.push(entry); return () => {} },
          },
        })
      },
    }
    return { ctx, listeners, injected, contexts, sections }
  }

  it('publishes a dynamic, optional context and the checkpoint contract', () => {
    const { ctx, injected, contexts, sections } = stubCtx()
    let digest = ''
    const band = { textFor: () => digest, refresh: async () => false, stats: () => ({ chars: 0, agents: 0 }) }
    registerContextBand(ctx, options(), band, { info() {}, warn() {} }, { storeCheckpoint: async () => {} })

    assert.deepEqual(injected, [['systemPrompt'], ['systemPrompt']])
    assert.equal(contexts.length, 1)
    const entry = contexts[0]
    assert.equal(entry.name, WORKING_CONTEXT_NAME)
    assert.equal(entry.required, false, 'optional: suppressRuntimeContext() can drop it')
    assert.equal(entry.interpolate, false)
    assert.equal(entry.order, 15000)
    digest = '<working-context>x</working-context>'
    assert.match(entry.text({ agent: { id: 'a' } }), /working-context/)

    assert.equal(sections.length, 1)
    assert.equal(sections[0].name, CHECKPOINT_SECTION_NAME)
    assert.equal(sections[0].text, CHECKPOINT_CONTRACT)
  })

  it('archives only real compaction summaries', async () => {
    const { ctx, listeners } = stubCtx()
    const stored = []
    const band = { textFor: () => '', refresh: async () => false, stats: () => ({ chars: 0, agents: 0 }) }
    registerContextBand(ctx, options(), band, { info() {}, warn() {} }, {
      storeCheckpoint: async (text) => { stored.push(text) },
    })
    const onEvent = listeners['session/event']
    assert.equal(typeof onEvent, 'function')
    onEvent({}, { type: 'tool/result', summary: 'x'.repeat(80) })
    onEvent({}, { type: 'compaction/summary', summary: 'condensed: '.repeat(10) })
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(stored.length, 1)
    assert.match(stored[0], /condensed/)
  })

  it('refreshes on step open without touching the decision', async () => {
    const { ctx, listeners } = stubCtx()
    let refreshed = 0
    const band = { textFor: () => '', refresh: async () => { refreshed += 1; return true }, stats: () => ({ chars: 0, agents: 0 }) }
    registerContextBand(ctx, options(), band, { info() {}, warn() {} }, { storeCheckpoint: async () => {} })
    const decision = { kind: 'enter', messages: [] }
    const out = await listeners['agent/pre-step']({ agent: { id: 'a' } }, async () => decision)
    assert.equal(out, decision, 'the step is never rewritten by the band')
    assert.equal(refreshed, 1)
  })

  it('can be disabled piecewise', () => {
    const { ctx, injected, contexts, sections } = stubCtx()
    const band = { textFor: () => '', refresh: async () => false, stats: () => ({ chars: 0, agents: 0 }) }
    registerContextBand(
      ctx,
      options({ workingContext: false, checkpointContract: false, archiveCheckpoints: false }),
      band,
      { info() {}, warn() {} },
      { storeCheckpoint: async () => {} },
    )
    assert.deepEqual(injected, [])
    assert.equal(contexts.length, 0)
    assert.equal(sections.length, 0)
  })
})
