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
  RECALL_PROTOCOL_SECTION_NAME,
  WORKING_CONTEXT_NAME,
  checkpointTextOf,
  createContextBand,
  createUseGate,
  pointerIdsOf,
  readMute,
  registerContextBand,
  registerUseGate,
  renderWorkingContext,
  writeMute,
} from '../lib/context.js'
import { isSkippableText } from '../lib/index.js'

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
  recallProtocol: true,
  protocolOrder: 11000,
  archiveCheckpoints: true,
  useGate: true,
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

    assert.deepEqual(injected, [['systemPrompt'], ['systemPrompt'], ['systemPrompt']])
    assert.equal(contexts.length, 1)
    const entry = contexts[0]
    assert.equal(entry.name, WORKING_CONTEXT_NAME)
    assert.equal(entry.required, false, 'optional: suppressRuntimeContext() can drop it')
    assert.equal(entry.interpolate, false)
    assert.equal(entry.order, 15000)
    digest = '<working-context>x</working-context>'
    assert.match(entry.text({ agent: { id: 'a' } }), /working-context/)

    assert.equal(sections.length, 2)
    const byName = Object.fromEntries(sections.map((entry) => [entry.name, entry]))
    assert.equal(byName[CHECKPOINT_SECTION_NAME].text, CHECKPOINT_CONTRACT)
    assert.equal(byName[RECALL_PROTOCOL_SECTION_NAME].order, 11000)
    assert.match(byName[RECALL_PROTOCOL_SECTION_NAME].text, /flymemory_get_memory/)
    assert.match(byName[RECALL_PROTOCOL_SECTION_NAME].text, /54\.0%/)
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
      options({ workingContext: false, checkpointContract: false, archiveCheckpoints: false, recallProtocol: false }),
      band,
      { info() {}, warn() {} },
      { storeCheckpoint: async () => {} },
    )
    assert.deepEqual(injected, [])
    assert.equal(contexts.length, 0)
    assert.equal(sections.length, 0)
  })
})

describe('pointer index form', () => {
  it('marks the block as an index and tells the model how to read an entry', () => {
    const text = renderWorkingContext(PACK)
    assert.match(text, /type="index"/)
    assert.match(text, /flymemory_get_memory/)
  })

  it('extracts exactly the ids it pointed at', () => {
    assert.deepEqual(pointerIdsOf('[#12 | a]\n[#7 | b]\n[#12 | c]'), [12, 7])
    assert.deepEqual(pointerIdsOf('no pointers here'), [])
    assert.deepEqual(pointerIdsOf(''), [])
  })
})

describe('createContextBand with mute', () => {
  it('serves nothing and refreshes nothing while muted', async () => {
    let calls = 0
    const band = createContextBand(options(), {
      fetchDigest: async () => { calls += 1; return PACK },
      isMuted: () => true,
    })
    assert.equal(await band.refresh('a'), false)
    assert.equal(calls, 0)
    assert.equal(band.textFor({}), '')
  })

  it('reports the injected pointers to the ledger', async () => {
    const injected = []
    const band = createContextBand(options(), {
      fetchDigest: async () => PACK,
      onInjected: (session, ids) => injected.push([session, ids]),
    })
    await band.refresh('agent-1')
    assert.deepEqual(injected, [['agent-1', [9001, 9002, 8990]]])
  })
})

describe('use_gate ledger', () => {
  it('accounts injection, expansion, re-call and supersede', () => {
    const rows = []
    let clock = 1000
    const gate = createUseGate({}, { append: (record) => rows.push(record), now: () => clock })
    gate.injected('s1', [11, 12])
    clock = 2000
    assert.equal(gate.observe({ session: 's1', toolName: 'flymemory_get_memory', args: { memory_id: 12 } }), 1)
    assert.equal(gate.observe({ session: 's1', toolName: 'flymemory_recall', args: {}, resultText: '[#11 | x]' }), 1)
    // ids never shown, repeats, and other sessions are not injections->uses
    assert.equal(gate.observe({ session: 's1', toolName: 'flymemory_get_memory', args: { memory_id: 99 } }), 0)
    assert.equal(gate.observe({ session: 's1', toolName: 'flymemory_get_memory', args: { memory_id: 12 } }), 0)
    assert.equal(gate.observe({ session: 'other', toolName: 'flymemory_get_memory', args: { memory_id: 11 } }), 0)

    const stats = gate.stats()
    assert.equal(stats.injected, 2)
    assert.equal(stats.used, 2)
    assert.equal(stats.expanded, 1)
    assert.equal(stats.recalled, 1)
    assert.equal(stats.useRate, 1)
    assert.deepEqual(stats.hot.map((hit) => hit.id).includes(11), true)
    assert.equal(rows.filter((row) => row.event === 'injected').length, 2)
    assert.deepEqual(rows.filter((row) => row.event !== 'injected').map((row) => row.event).sort(), ['expanded', 'recalled'])
  })

  it('reports supersede against the pointer it replaced', () => {
    const rows = []
    const gate = createUseGate({}, { append: (record) => rows.push(record) })
    gate.injected('s', [3])
    gate.observe({ session: 's', toolName: 'flymemory_supersede', args: { old_memory_id: 3, new_memory_id: 4 } })
    assert.equal(gate.stats().superseded, 1)
    assert.equal(gate.bySession()[0].used, 1)
  })

  it('attributes tool results through the tools/result listener', () => {
    const rows = []
    const handlers = {}
    const ctx = { on: (name, fn) => { handlers[name] = fn; return () => {} } }
    const gate = createUseGate(options(), { append: (record) => rows.push(record) })
    registerUseGate(ctx, options(), gate, { warn() {} })
    gate.injected('s', [5])
    // Through the MCP bridge the name is mcp__<server>__<rawName>.
    handlers['tools/result'](
      { agent: { id: 's' }, name: 'mcp__flymemory__flymemory_get_memory', arguments: { memory_id: 5 } },
      { content: [{ type: 'text', text: 'full body' }] },
    )
    assert.equal(rows.filter((row) => row.event === 'expanded' && row.id === 5).length, 1)
  })
})

describe('mute switch', () => {
  it('writes, reads, expires and clears', () => {
    const dir = join(scratch, 'mute-state')
    const now = 1_000_000
    assert.equal(readMute(dir, now), null)
    const path = writeMute(dir, now + 30 * 60000, now)
    assert.ok(path && path.endsWith('mute.json'))
    const state = readMute(dir, now + 60000)
    assert.ok(state)
    assert.equal(state.min, 29)
    assert.equal(readMute(dir, now + 31 * 60000), null, 'an expired mute stops counting')
    writeMute(dir, 0, now)
    assert.equal(readMute(dir, now), null)
  })
})

describe('credential filter covers registry tokens', () => {
  it('drops npm / gitlab / pypi / google / hf tokens', () => {
    // Synthetic sample: real tokens must never appear in this repository.
    assert.equal(isSkippableText('npm_FAKEtoken0123456789abcdef'), true)
    assert.equal(isSkippableText('here is my token glpat-AbCdEf123456'), true)
    assert.equal(isSkippableText('pypi-AgEIcHlwaS5vcmc'), true)
    assert.equal(isSkippableText('AIzaSyD-1234567890abcdef'), true)
    assert.equal(isSkippableText('hf_abcdefghijklmnop'), true)
    assert.equal(isSkippableText('ordinary sentence about npm packages'), false)
  })
})

describe('query-scoped working context', () => {
  it('passes the step query and the minute window to the fetcher', async () => {
    const seen = []
    const band = createContextBand(options(), {
      fetchDigest: async (args) => { seen.push(args); return PACK }
    })
    await band.refresh('a', null, 'why is the cart page stale')
    assert.deepEqual(seen, [{ minutes: 240, query: 'why is the cart page stale' }])
  })

  it('never fetches the store-wide pack when scoped mode has no query', async () => {
    let calls = 0
    const band = createContextBand(options({ workingContextRequireQuery: true }), {
      fetchDigest: async () => { calls += 1; return PACK },
    })
    assert.equal(await band.refresh('a', null, ''), false)
    assert.equal(calls, 0, 'a clock-scoped digest is exactly the cross-conversation leak')
  })

  it('takes the query from the entering step through queryOf', async () => {
    const handlers = {}
    const ctx = { on: (name, fn) => { handlers[name] = fn; return () => {} }, inject: () => {} }
    const fetched = []
    const band = createContextBand(options(), {
      fetchDigest: async (args) => { fetched.push(args.query); return PACK },
    })
    registerContextBand(ctx, options(), band, { info() {}, warn() {} }, {
      queryOf: (decision) => decision?.latestUserText || '',
      storeCheckpoint: async () => {},
    })
    const decision = { kind: 'enter', messages: [], latestUserText: 'fix the checkout badge' }
    await handlers['agent/pre-step']({ agent: { id: 'a' } }, async () => decision)
    assert.deepEqual(fetched, ['fix the checkout badge'])
  })
})
