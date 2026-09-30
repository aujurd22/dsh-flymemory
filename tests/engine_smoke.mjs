/**
 * Engine smoke test — the full Host path against the real Python engine.
 *
 *   node tests/engine_smoke.mjs [--port 8791] [--keep]
 *
 * Requires a Python interpreter with torch + sentence-transformers. When none
 * is found the test prints SKIP and exits 0, so it is safe to run anywhere.
 *
 * It runs the real `apply()` against a stub cordis context in a throwaway data
 * directory, then checks: engine start -> own library created in that directory
 * -> hook config written -> 15 tools published -> remember/recall/get/forget
 * round trip -> teardown stops the engine it started.
 *
 * Nothing outside the temporary directory is written; `--keep` leaves it behind
 * for inspection.
 */
import { existsSync, readFileSync, rmSync } from 'node:fs'
import { setTimeout as sleep } from 'node:timers/promises'

import { DEFAULT_PORT, apply, findPython, resolveOptions, tempDataDir } from '../lib/index.js'

const argv = process.argv.slice(2)
const flag = (name, fallback) => {
  const index = argv.indexOf(name)
  return index === -1 ? fallback : argv[index + 1]
}
const port = Number(flag('--port', DEFAULT_PORT))
const keep = argv.includes('--keep')
const dataDir = flag('--data-dir', tempDataDir('dsh-flymemory-smoke'))

const failures = []
const check = (ok, label, detail = '') => {
  console.log(`${ok ? '  ok  ' : ' FAIL '} ${label}${detail ? ` — ${detail}` : ''}`)
  if (!ok) failures.push(label)
}

const base = resolveOptions({ dataDir, port, seedFromUpstream: false, readinessTimeoutMs: 120000 })
const url = `http://127.0.0.1:${port}/mcp`
const { pythonExe, verified } = findPython(base)
console.log(`\nengine smoke test — port ${port}, data dir ${dataDir}`)
console.log(`interpreter: ${pythonExe || '(none found)'}${verified ? '' : ' — dependencies not found'}\n`)

if (!pythonExe || !verified) {
  console.log('SKIP: no Python interpreter with torch + sentence-transformers is available.')
  console.log('      install them, or point FLYMEMORY_PYTHON at one, then re-run.')
  process.exit(0)
}

const rpc = async (method, params) => {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
  })
  const text = await response.text()
  let payload = null
  for (const line of text.split(/\r?\n/)) {
    const raw = line.startsWith('data:') ? line.slice(5).trim() : line.trim()
    if (!raw) continue
    try {
      const parsed = JSON.parse(raw)
      if (parsed && (parsed.result !== undefined || parsed.error !== undefined)) payload = parsed
    } catch {
      // SSE framing noise
    }
  }
  if (!payload) throw new Error(`no JSON-RPC payload (HTTP ${response.status})`)
  if (payload.error) throw new Error(JSON.stringify(payload.error))
  return payload.result
}
const callTool = async (name, args = {}) => {
  const result = await rpc('tools/call', { name, arguments: args })
  return (result.content || []).map((block) => block.text ?? '').join('\n')
}
const endpointUp = async () => {
  try {
    await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list', params: {} }),
      signal: AbortSignal.timeout(1500),
    })
    return true
  } catch {
    return false
  }
}

let disposer = null
const logs = []
const ctx = {
  logger: {
    info: (message) => logs.push(`info: ${message}`),
    warn: (message) => logs.push(`warn: ${message}`),
    error: (message) => logs.push(`error: ${message}`),
  },
  effect: (callback) => {
    disposer = callback()
    return () => disposer?.()
  },
}

const started = Date.now()
await apply(ctx, {
  dataDir,
  port,
  seedFromUpstream: false,
  readinessTimeoutMs: 120000,
})
console.log(`apply() returned after ${Date.now() - started}ms`)
for (const line of logs) console.log(`   ${line}`)
console.log('')

check(port !== 8765, 'uses a private port by default', String(port))
check(logs.some((line) => line.includes('engine ready') || line.includes('reusing')), 'engine became ready')
check(base.upstreamLibrary === '', 'no upstream library configured, so nothing was imported')

const hooksPath = `${dataDir}/hooks.json`
check(existsSync(hooksPath), 'hook config written')
if (existsSync(hooksPath)) {
  const hooks = JSON.parse(readFileSync(hooksPath, 'utf8'))
  const command = hooks?.hooks?.UserPromptSubmit?.[0]?.hooks?.[0]?.command ?? ''
  check(command.includes('hook_auto.py'), 'UserPromptSubmit command targets hook_auto.py')
  check(command.includes(`--url "${url}"`), 'hook command carries the resolved endpoint')
}

let names = []
try {
  const listed = await rpc('tools/list', {})
  names = (listed.tools || []).map((tool) => tool.name)
  check(names.length >= 15, `tools discovered (${names.length})`, `${names.slice(0, 3).join(', ')}, …`)
  check(names.every((name) => name.startsWith('flymemory_')), 'every tool is namespaced flymemory_')
} catch (error) {
  check(false, 'tools/list succeeded', String(error))
}

const created = []
try {
  // Regression: `flymemory_auto` used to raise UnboundLocalError on a library
  // that is still empty — the exact state every fresh install starts in, where
  // the hooks then failed before storing anything.
  const auto = await callTool('flymemory_auto', { context: 'dsh-flymemory empty-store auto probe' })
  check(!auto.includes('Error executing tool'), 'flymemory_auto survives an empty store', auto.split('\n')[0].slice(0, 80))
  const storedLine = auto.split('\n').find((line) => line.startsWith('STORED:')) ?? ''
  check(storedLine.length > 0 && !storedLine.includes('[SKIPPED]'), 'flymemory_auto captured the context', storedLine.slice(0, 70))
  created.push(...[...storedLine.matchAll(/#(\d+)/g)].map((match) => Number(match[1])))

  const stats = await callTool('flymemory_stats')
  check(stats.includes('Memory empty') || stats.includes('Memories'), 'flymemory_stats answered', stats.slice(0, 60))

  const stored = await callTool('flymemory_remember', {
    text: `dsh-flymemory engine smoke probe ${new Date().toISOString()}`,
    tags: 'smoke,dsh-flymemory',
  })
  check(!stored.includes('[REJECTED]'), 'flymemory_remember stored the probe', stored.slice(0, 80))
  created.push(...[...stored.matchAll(/#(\d+)/g)].map((match) => Number(match[1])))
  // The engine creates the library file on its first write, not at startup.
  check(existsSync(base.libraryPath), 'own library persisted inside the data directory', base.libraryPath)

  const recalled = await callTool('flymemory_recall', {
    query: 'dsh-flymemory engine smoke probe',
    top_k: 3,
  })
  check(recalled.includes('smoke probe'), 'flymemory_recall returned the probe')
  if (created.length > 0) {
    const full = await callTool('flymemory_get_memory', { memory_id: created[created.length - 1] })
    check(full.includes('smoke probe'), 'flymemory_get_memory returned the full text')
  }
} catch (error) {
  check(false, 'auto/remember/recall/get_memory round trip', String(error))
}

for (const id of [...new Set(created)]) {
  try {
    const removed = await callTool('flymemory_forget', { memory_id: id })
    check(removed.includes('[FORGOT'), `flymemory_forget cleaned up #${id}`, removed.slice(0, 50))
  } catch (error) {
    check(false, `flymemory_forget cleaned up #${id}`, String(error))
  }
}

if (!keep) {
  disposer?.()
  let stopped = false
  for (let attempt = 0; attempt < 20 && !stopped; attempt += 1) {
    await sleep(500)
    stopped = !(await endpointUp())
  }
  check(stopped, 'teardown stopped the engine it started')
  try {
    rmSync(dataDir, { recursive: true, force: true })
  } catch {
    // Windows may hold the library briefly; the temp dir is disposable anyway
  }
} else {
  console.log(`\n--keep: data directory left at ${dataDir}`)
}

console.log(failures.length === 0 ? '\nENGINE SMOKE: PASS' : `\nENGINE SMOKE: FAIL (${failures.length})`)
process.exit(failures.length === 0 ? 0 : 1)
