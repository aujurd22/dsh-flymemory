/**
 * Unit tests — dependency-free (`node --test tests/`), no Python and no network
 * outside localhost. They pin the properties this plugin promises:
 *
 *   * its own port and its own library, independent of any other FlyMemory run;
 *   * no personal absolute path baked into any default;
 *   * configuration precedence config > environment > default;
 *   * an endpoint is only treated as reusable when it really is FlyMemory.
 */
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from 'node:fs'
import { createServer as createTcpServer } from 'node:net'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { after, before, describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  DEFAULT_PORT,
  callTool,
  contextMessage,
  describe as describePlugin,
  findPython,
  isSkippableText,
  mcpUrl,
  portOpen,
  probeEndpoint,
  readEffectiveEndpoint,
  readPidFile,
  readState,
  registerHooks,
  resolveOptions,
  seedLibrary,
  stopEngine,
  typedUserText,
  userTextOf,
  writeHooksConfig,
  writeState,
} from '../lib/index.js'

const HOME = 'C:\\Users\\tester'

let scratch
before(() => {
  scratch = mkdtempSync(join(tmpdir(), 'dsh-flymemory-test-'))
})
after(() => {
  rmSync(scratch, { recursive: true, force: true })
})

/** An environment with a fixed DSH_HOME and nothing else interesting. */
const env = (extra = {}) => ({ DSH_HOME: join(HOME, '.dsh'), PATH: '', ...extra })

describe('resolveOptions', () => {
  it('defaults to a private port and a private library', () => {
    const options = resolveOptions({}, env())
    assert.equal(options.port, DEFAULT_PORT)
    assert.notEqual(options.port, 8765, 'must not default to a port a separate service may hold')
    assert.equal(options.host, '127.0.0.1')
    assert.equal(options.libraryPath, join(HOME, '.dsh', 'flymemory-data', 'flymemory_v3.pkl'))
    assert.equal(options.dataDir, join(HOME, '.dsh', 'flymemory-data'))
    assert.equal(options.seedFromUpstream, false, 'must not import a library by default')
    assert.equal(options.upstreamLibrary, '')
    assert.equal(options.autostart, true)
    assert.equal(options.writeHooksConfig, true)
    assert.equal(options.shutdownOnDispose, true)
    assert.equal(mcpUrl(options), `http://127.0.0.1:${DEFAULT_PORT}/mcp`)
  })

  it('reads environment overrides', () => {
    const options = resolveOptions({}, env({
      FLYMEMORY_MCP_PORT: '9123',
      FLYMEMORY_DATA_DIR: join(HOME, 'elsewhere'),
      FLYMEMORY_MCP_URL: 'http://127.0.0.1:9123/mcp',
      FLYMEMORY_PYTHON: 'C:\\python\\python.exe',
    }))
    assert.equal(options.port, 9123)
    assert.equal(options.dataDir, join(HOME, 'elsewhere'))
    assert.equal(options.libraryPath, join(HOME, 'elsewhere', 'flymemory_v3.pkl'))
    assert.equal(options.pythonExe, 'C:\\python\\python.exe')
    assert.equal(mcpUrl(options), 'http://127.0.0.1:9123/mcp')
  })

  it('lets row config win over the environment', () => {
    const options = resolveOptions(
      { port: 7000, dataDir: 'D:\\mem', libraryPath: 'D:\\mem\\custom.pkl', seedFromUpstream: true, upstreamLibrary: 'D:\\old\\lib.pkl' },
      env({ FLYMEMORY_MCP_PORT: '9123', FLYMEMORY_DATA_DIR: join(HOME, 'elsewhere') }),
    )
    assert.equal(options.port, 7000)
    assert.equal(options.dataDir, 'D:\\mem')
    assert.equal(options.libraryPath, 'D:\\mem\\custom.pkl')
    assert.equal(options.seedFromUpstream, true)
    assert.equal(options.upstreamLibrary, 'D:\\old\\lib.pkl')
  })

  it('falls back to the default port for junk input', () => {
    assert.equal(resolveOptions({ port: 'not-a-port' }, env()).port, DEFAULT_PORT)
    assert.equal(resolveOptions({ port: 0 }, env()).port, DEFAULT_PORT)
    assert.equal(resolveOptions({ port: -5 }, env()).port, DEFAULT_PORT)
  })

  it('builds the endpoint from config before the environment', () => {
    assert.equal(mcpUrl({ host: 'h', port: 1, mcpPath: '/x' }), 'http://h:1/x')
    assert.equal(mcpUrl({ host: 'h', port: 1, mcpPath: '/mcp', mcpUrl: 'http://z/mcp' }), 'http://z/mcp')
  })

  it('keeps personal paths out of the shipped sources', () => {
    const root = join(dirname(fileURLToPath(import.meta.url)), '..')
    for (const file of ['lib/index.js', 'bin/flymemory.mjs', 'cordis.patch.yml', 'locale/en.json', 'locale/zh.json']) {
      const text = readFileSync(join(root, file), 'utf8')
      for (const forbidden of ['djr82', 'D:\\djr82', 'C:\\Users\\djr']) {
        assert.equal(text.includes(forbidden), false, `${file} must not contain ${forbidden}`)
      }
    }
  })

  it('starts with its own empty store by default', () => {
    const options = resolveOptions({}, env())
    assert.equal(options.upstreamLibrary, '')
    assert.equal(options.seedFromUpstream, false)
    assert.equal(describePlugin({}, env()).upstreamLibraryExists, false)
  })
})

describe('writeHooksConfig', () => {
  it('writes both hook events with absolute commands', () => {
    const options = resolveOptions({ dataDir: scratch, port: 8899 }, env())
    const path = writeHooksConfig(options, 'C:\\python\\python.exe')
    assert.equal(path, join(scratch, 'hooks.json'))
    const document = JSON.parse(readFileSync(path, 'utf8'))
    const prompt = document.hooks.UserPromptSubmit[0].hooks[0]
    const session = document.hooks.SessionStart[0].hooks[0]
    assert.match(prompt.command, /hook_auto\.py/)
    assert.match(session.command, /hook_compact\.py/)
    for (const hook of [prompt, session]) {
      assert.equal(hook.type, 'command')
      assert.match(hook.command, /--url "http:\/\/127\.0\.0\.1:8899\/mcp"/)
      assert.equal(typeof hook.timeout, 'number')
      assert.ok(hook.command.startsWith('"C:\\python\\python.exe"'))
    }
  })

  it('follows a custom endpoint', () => {
    const options = resolveOptions({ dataDir: scratch, mcpUrl: 'http://127.0.0.1:9999/other' }, env())
    const document = JSON.parse(readFileSync(writeHooksConfig(options, 'python3'), 'utf8'))
    assert.match(document.hooks.UserPromptSubmit[0].hooks[0].command, /--url "http:\/\/127\.0\.0\.1:9999\/other"/)
  })

  it('gives the two-request capture hook enough time', () => {
    const options = resolveOptions({ dataDir: scratch, port: 8899 }, env())
    const document = JSON.parse(readFileSync(writeHooksConfig(options, 'C:\\python\\python.exe'), 'utf8'))
    const prompt = document.hooks.UserPromptSubmit[0].hooks[0]
    // hook_auto.py makes a recall request and a lessons request, 8 s each.
    assert.ok(prompt.timeout >= 16, `capture hook timeout must cover both requests, got ${prompt.timeout}`)
  })
})

describe('readEffectiveEndpoint', () => {
  it('returns nothing before the plugin has written a hook config', () => {
    assert.equal(readEffectiveEndpoint(join(scratch, 'never-written')), '')
  })

  it('reads back the endpoint the plugin actually resolved', () => {
    const dataDir = join(scratch, 'effective')
    const options = resolveOptions({ dataDir, mcpUrl: 'http://127.0.0.1:8765/mcp' }, env())
    writeHooksConfig(options, 'python3')
    assert.equal(readEffectiveEndpoint(dataDir), 'http://127.0.0.1:8765/mcp')
  })

  it('survives a malformed hook config', () => {
    const dataDir = join(scratch, 'broken')
    mkdirSync(dataDir, { recursive: true })
    writeFileSync(join(dataDir, 'hooks.json'), '{ not json')
    assert.equal(readEffectiveEndpoint(dataDir), '')
  })
})

describe('activation state record', () => {
  it('records and reads back what the plugin resolved', () => {
    const dataDir = join(scratch, 'state')
    const libraryPath = join(scratch, 'shared-lib.pkl')
    const options = resolveOptions({ dataDir, port: 8899, libraryPath }, env())
    assert.equal(writeState(options, { mode: 'reused', tools: 15 }), join(dataDir, 'state.json'))
    const state = readState(dataDir)
    assert.equal(state.endpoint, 'http://127.0.0.1:8899/mcp')
    assert.equal(state.port, 8899)
    assert.equal(state.libraryPath, libraryPath)
    assert.equal(state.mode, 'reused')
    assert.equal(state.tools, 15)
    assert.equal(typeof state.writtenAt, 'string')
  })

  it('returns null when the record is absent, malformed or not an object', () => {
    assert.equal(readState(join(scratch, 'no-state-here')), null)
    const broken = join(scratch, 'state-broken')
    mkdirSync(broken, { recursive: true })
    writeFileSync(join(broken, 'state.json'), '{ not json')
    assert.equal(readState(broken), null)
    const array = join(scratch, 'state-array')
    mkdirSync(array, { recursive: true })
    writeFileSync(join(array, 'state.json'), '[]')
    assert.equal(readState(array), null)
  })
})

describe('seedLibrary', () => {
  it('does nothing unless seeding is requested', () => {
    const options = resolveOptions({ dataDir: join(scratch, 'a'), upstreamLibrary: process.execPath }, env())
    assert.equal(seedLibrary(options), null)
    assert.equal(existsSync(options.libraryPath), false)
  })

  it('copies an explicit upstream library exactly once', () => {
    const upstream = join(scratch, 'upstream.pkl')
    writeFileSync(upstream, Buffer.from([1, 2, 3, 4]))
    const options = resolveOptions(
      { dataDir: join(scratch, 'b'), seedFromUpstream: true, upstreamLibrary: upstream },
      env(),
    )
    assert.match(seedLibrary(options) ?? '', /imported library/)
    assert.deepEqual([...readFileSync(options.libraryPath)], [1, 2, 3, 4])
    assert.equal(seedLibrary(options), null, 'never overwrites an existing library')
    assert.ok(existsSync(upstream), 'the source library is left in place')
  })
})

describe('findPython', () => {
  it('honours an explicit interpreter path', () => {
    const found = findPython(resolveOptions({ pythonExe: process.execPath, verifyInterpreter: false }, env()), env())
    assert.equal(found.pythonExe, process.execPath)
  })

  it('returns the verified flag and the candidates it tried', () => {
    const found = findPython(resolveOptions({ pythonExe: process.execPath, verifyInterpreter: false }, env()), env())
    assert.equal(found.verified, false)
    assert.ok(Array.isArray(found.candidates))
    assert.ok(found.candidates.includes(process.execPath))
  })

  it('never throws when nothing is installed', () => {
    const found = findPython(resolveOptions({ verifyInterpreter: false }, env()), { DSH_HOME: HOME, PATH: '' })
    assert.equal(typeof found.pythonExe, 'string')
  })
})

describe('portOpen', () => {
  it('reports a listening port and a closed one', async () => {
    const server = createTcpServer()
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
    const port = server.address().port
    assert.equal(await portOpen('127.0.0.1', port, 500), true)
    await new Promise((resolve) => server.close(resolve))
    assert.equal(await portOpen('127.0.0.1', port, 500), false)
  })
})

describe('probeEndpoint', () => {
  let server
  let base
  before(async () => {
    server = createServer((request, response) => {
      let body = ''
      request.on('data', (chunk) => { body += chunk })
      request.on('end', () => {
        if (request.url === '/mcp') {
          response.writeHead(200, { 'Content-Type': 'text/event-stream' })
          response.end(`event: message\ndata: ${JSON.stringify({
            jsonrpc: '2.0',
            id: 1,
            result: { tools: [{ name: 'flymemory_recall' }, { name: 'flymemory_remember' }] },
          })}\n\n`)
          return
        }
        response.writeHead(200, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ jsonrpc: '2.0', id: 1, result: { tools: [{ name: 'something_else' }] } }))
      })
    })
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
    base = `http://127.0.0.1:${server.address().port}`
  })
  after(async () => {
    await new Promise((resolve) => server.close(resolve))
  })

  it('parses an SSE tool list', async () => {
    const result = await probeEndpoint(`${base}/mcp`)
    assert.equal(result.ok, true)
    assert.deepEqual(result.tools, ['flymemory_recall', 'flymemory_remember'])
  })

  it('reports a foreign service as reachable but not FlyMemory', async () => {
    const result = await probeEndpoint(`${base}/foreign`)
    assert.equal(result.ok, true)
    assert.equal(result.tools.some((tool) => tool.startsWith('flymemory_')), false)
  })

  it('reports an unreachable endpoint', async () => {
    const result = await probeEndpoint('http://127.0.0.1:1/mcp', 500)
    assert.equal(result.ok, false)
    assert.ok(result.error)
  })
})

describe('pid file helpers', () => {
  it('reads a missing pid as zero and stops nothing', () => {
    const options = resolveOptions({ dataDir: join(scratch, 'empty') }, env())
    assert.equal(readPidFile(options), 0)
    assert.equal(stopEngine(options), 0)
  })

  it('stops the process named in the pid file', () => {
    const dataDir = join(scratch, 'pid')
    const options = resolveOptions({ dataDir }, env())
    mkdirSync(dataDir, { recursive: true })
    // A pid that cannot exist: killing it must be a safe no-op.
    writeFileSync(join(dataDir, 'server.pid'), '999999')
    assert.equal(stopEngine(options), 0)
  })
})

describe('native recall/capture hooks', () => {
  // A typed prompt: role user, `kind: 'user'`, and the rpcId the session
  // controller assigns when it admits a client prompt.
  const userMessage = (text) => ({ role: 'user', content: [{ type: 'text', text }], source: { kind: 'user', rpcId: 'rpc-test' } })

  it('collects only the user words entering a step', () => {
    const messages = [
      userMessage('hello'),
      { role: 'assistant', content: [{ type: 'text', text: 'ignored' }] },
      // our own injection must never feed the next recall
      { role: 'user', content: [{ type: 'text', text: 'injected' }], source: { kind: 'flymemory', form: 'snapshot' } },
      userMessage('world'),
    ]
    assert.equal(userTextOf(messages), 'hello\nworld')
    assert.equal(userTextOf([]), '')
    assert.equal(userTextOf(undefined), '')
  })

  it('prefers genuinely typed messages over other user-role ones', () => {
    const messages = [
      { role: 'user', content: [{ type: 'text', text: 'peer or system chatter' }], source: { kind: 'subagent' } },
      userMessage('what the human actually typed'),
    ]
    assert.equal(userTextOf(messages), 'what the human actually typed')
    // with no typed message present, the delegated task still drives recall
    assert.equal(userTextOf([messages[0]]), 'peer or system chatter')
  })

  it('takes only `source.kind === "user"` messages with an rpcId as typed input', () => {
    const typed = userMessage('typed by a human')
    const peer = { role: 'user', content: [{ type: 'text', text: 'Background subagent finished' }], source: { kind: 'subagent' } }
    // Peer deliveries can be user-role with kind "user" but never carry an rpcId.
    const delivery = { role: 'user', content: [{ type: 'text', text: 'Agent x sent a message' }], source: { kind: 'user' } }
    const notice = { role: 'user', content: [{ type: 'text', text: 'model changed' }], source: { kind: 'model-selection', form: 'notice' } }
    assert.equal(typedUserText([typed, peer, delivery, notice]), 'typed by a human')
    assert.equal(typedUserText([peer, delivery, notice]), '')
  })

  it('never captures automation prompts, system reminders or credentials', () => {
    assert.equal(isSkippableText('<system-reminder>hi</system-reminder>'), true)
    assert.equal(isSkippableText('AUTOMATION-DISPATCH: heartbeat'), true)
    assert.equal(isSkippableText('Continue working toward the active session goal'), true)
    assert.equal(isSkippableText('token ghp_abcdefghijklmnop'), true)
    assert.equal(isSkippableText('-----BEGIN PRIVATE KEY-----'), true)
    assert.equal(isSkippableText('ordinary user question'), false)
    assert.equal(
      userTextOf([userMessage('<system-reminder>ignored</system-reminder>'), userMessage('real question')]),
      'real question',
    )
  })

  it('builds a message shaped like the shipped context plugins', () => {
    const message = contextMessage('<flymemory>x</flymemory>', 'fixed-id')
    assert.equal(message.id, 'fixed-id')
    assert.equal(message.role, 'user')
    assert.deepEqual(message.content, [{ type: 'text', text: '<flymemory>x</flymemory>' }])
    assert.equal(message.source.kind, 'flymemory')
    assert.equal(message.source.form, 'snapshot')
    assert.equal(message.source.sections[0].name, 'flymemory')
    assert.ok(contextMessage('x').id.length > 10, 'generates a fresh id')
  })

  it('calls a tool over JSON-RPC and flattens its text', async () => {
    const server = createServer((request, response) => {
      let body = ''
      request.on('data', (chunk) => { body += chunk })
      request.on('end', () => {
        const call = JSON.parse(body)
        response.writeHead(200, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({
          jsonrpc: '2.0',
          id: 1,
          result: { content: [{ type: 'text', text: `tool=${call.params.name}` }] },
        }))
      })
    })
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
    const url = `http://127.0.0.1:${server.address().port}/mcp`
    try {
      assert.equal(await callTool(url, 'flymemory_stats', {}, 2000), 'tool=flymemory_stats')
    } finally {
      await new Promise((resolve) => server.close(resolve))
    }
    assert.equal(await callTool('http://127.0.0.1:1/mcp', 'x', {}, 500), '', 'a dead service yields no text')
  })

  it('passes the step through untouched when there is nothing to inject', async () => {
    const handlers = {}
    const ctx = { logger: { info() {}, warn() {} }, on: (name, fn) => { handlers[name] = fn; return () => {} } }
    const options = resolveOptions({ dataDir: join(scratch, 'hooks-off'), hookTimeoutMs: 300 }, env())
    registerHooks(ctx, options, ctx.logger)
    const handler = handlers['agent/pre-step']
    assert.equal(typeof handler, 'function')

    const decision = { kind: 'enter', messages: [userMessage('hello')] }
    // service down: the decision comes back exactly as next() produced it
    assert.equal(await handler({ agent: { id: 'a1' }, messages: [], signal: undefined }, async () => decision), decision)
    // nothing said: next() result passes through unchanged
    const empty = { kind: 'enter', messages: [] }
    assert.equal(await handler({ agent: { id: 'a2' }, messages: [], signal: undefined }, async () => empty), empty)
    // a rejected step is never rewritten
    const rejected = { kind: 'reject' }
    assert.equal(await handler({ agent: { id: 'a3' }, messages: [], signal: undefined }, async () => rejected), rejected)
    // a non-typed (peer/system) message is not a trigger at all
    const peerOnly = {
      kind: 'enter',
      messages: [{ role: 'user', content: [{ type: 'text', text: 'Agent x sent a message' }], source: { kind: 'user' } }],
    }
    assert.equal(await handler({ agent: { id: 'a4' }, messages: [], signal: undefined }, async () => peerOnly), peerOnly)
  })

  it('appends one context message when the memory service answers', async () => {
    const server = createServer((request, response) => {
      let body = ''
      request.on('data', (chunk) => { body += chunk })
      request.on('end', () => {
        const call = JSON.parse(body)
        const text = call.params?.name === 'flymemory_session_pack'
          ? 'RECENT SESSION TRAIL: one entry'
          : 'RECALLED MEMORIES: one entry'
        response.writeHead(200, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ jsonrpc: '2.0', id: 1, result: { content: [{ type: 'text', text }] } }))
      })
    })
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
    const url = `http://127.0.0.1:${server.address().port}/mcp`
    try {
      const handlers = {}
      const ctx = { logger: { info() {}, warn() {} }, on: (name, fn) => { handlers[name] = fn; return () => {} } }
      const options = resolveOptions({ mcpUrl: url, dataDir: join(scratch, 'hooks-live'), hookTimeoutMs: 2000 }, env())
      registerHooks(ctx, options, ctx.logger)
      const decision = { kind: 'enter', messages: [userMessage('what did we decide yesterday?')] }
      const out = await handlers['agent/pre-step']({ agent: { id: 'a' }, messages: [], signal: undefined }, async () => decision)
      assert.equal(out.messages.length, decision.messages.length + 1)
      const injected = out.messages[out.messages.length - 1]
      assert.equal(injected.role, 'user')
      assert.equal(injected.source.kind, 'flymemory')
      assert.match(injected.content[0].text, /RECALLED MEMORIES/)
      assert.match(injected.content[0].text, /RECENT SESSION TRAIL/, 'the first step also gets the recovery pack')
    } finally {
      await new Promise((resolve) => server.close(resolve))
    }
  })

  it('never treats a peer delivery as a trigger', async () => {
    const calls = []
    const server = createServer((request, response) => {
      let body = ''
      request.on('data', (chunk) => { body += chunk })
      request.on('end', () => {
        calls.push(JSON.parse(body).params?.name)
        response.writeHead(200, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ jsonrpc: '2.0', id: 1, result: { content: [{ type: 'text', text: 'RECALLED MEMORIES: x' }] } }))
      })
    })
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
    const url = `http://127.0.0.1:${server.address().port}/mcp`
    try {
      const handlers = {}
      const ctx = { logger: { info() {}, warn() {} }, on: (name, fn) => { handlers[name] = fn; return () => {} } }
      const options = resolveOptions({ mcpUrl: url, dataDir: join(scratch, 'hooks-peer'), hookTimeoutMs: 2000 }, env())
      registerHooks(ctx, options, ctx.logger)
      const handler = handlers['agent/pre-step']
      const peerDelivery = {
        kind: 'enter',
        messages: [{ role: 'user', content: [{ type: 'text', text: 'Agent x sent a message: report' }], source: { kind: 'user' } }],
      }
      // no rpcId, so not a typed prompt: no request, decision untouched
      const out = await handler({ agent: { id: 'peer-agent' }, messages: [], signal: undefined }, async () => peerDelivery)
      assert.equal(out, peerDelivery)
      assert.deepEqual(calls, [])
    } finally {
      await new Promise((resolve) => server.close(resolve))
    }
  })
})
