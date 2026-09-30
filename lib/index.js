/**
 * dsh-flymemory — Host half of the FlyMemory plugin bundle for DeepSeek Harness.
 *
 * One job: make sure a FlyMemory MCP service answers on `host:port` before the
 * bundle's `@deepseek-ai/dsh-mcp-client` row needs it, then stay out of the way.
 *
 * The service is private to this plugin:
 *
 *   - It binds its own port (default 8791) and its own library file under the
 *     harness home, so a DSH memory store never mixes with another FlyMemory
 *     instance (the FlyMemory HTTP service other tools use listens on 8765).
 *   - A port that already answers with FlyMemory tools is reused, so several
 *     harness processes on one machine share one engine and one library.
 *   - A port occupied by anything else is reported, never overwritten.
 *   - A missing library stays missing: nothing is copied from another
 *     installation unless `upstreamLibrary` is configured explicitly.
 *
 * The module is dependency-free on purpose: it imports nothing from
 * `@deepseek-ai/*`, so it carries no DSH peer-dependency range and can never
 * fail a version-compatibility check. Plugin options are plain fields on the
 * patch row (see cordis.patch.yml); unknown options are ignored.
 *
 * @module dsh-flymemory
 */
import { spawn, spawnSync } from 'node:child_process'
import { closeSync, copyFileSync, existsSync, mkdirSync, openSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { createConnection } from 'node:net'
import { homedir, tmpdir } from 'node:os'
import { delimiter, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/** Cordis plugin name used by loader diagnostics. */
export const name = 'flymemory'

/** Services required by this plugin: none, it only starts a process. */
export const inject = []

/** Port this plugin owns by default. Deliberately not 8765, which the
 *  FlyMemory HTTP service used by other clients (ZCode, Claude Code) binds. */
export const DEFAULT_PORT = 8791

/** Package root of this module (correct from a pnpm store copy as well). */
const packageDir = dirname(dirname(fileURLToPath(import.meta.url)))

/** Harness home, resolved exactly like the rest of the harness does. */
export function dshHome(env = process.env) {
  const fromEnv = env.DSH_HOME
  return fromEnv && fromEnv.trim().length > 0 ? fromEnv : join(homedir(), '.dsh')
}

/**
 * Merge the patch row's config over environment overrides and defaults.
 * @param {Record<string, unknown>} config - raw config from the cordis row.
 * @param {NodeJS.ProcessEnv} env - environment to read overrides from.
 * @returns resolved options.
 */
export function resolveOptions(config = {}, env = process.env) {
  const dataDir = String(config.dataDir || env.FLYMEMORY_DATA_DIR || join(dshHome(env), 'flymemory-data'))
  const port = Number(config.port ?? env.FLYMEMORY_MCP_PORT ?? DEFAULT_PORT)
  const libraryPath = String(config.libraryPath || join(dataDir, 'flymemory_v3.pkl'))
  return {
    dataDir,
    host: String(config.host || env.FLYMEMORY_HOST || '127.0.0.1'),
    port: Number.isFinite(port) && port > 0 ? port : DEFAULT_PORT,
    mcpPath: String(config.mcpPath || '/mcp'),
    mcpUrl: String(config.mcpUrl || env.FLYMEMORY_MCP_URL || ''),
    pythonExe: config.pythonExe ? String(config.pythonExe) : String(env.FLYMEMORY_PYTHON || ''),
    verifyInterpreter: config.verifyInterpreter !== false,
    scriptPath: String(config.scriptPath || join(packageDir, 'python', 'flymemory_server.py')),
    libraryPath,
    // Opt-in import of an existing FlyMemory library. Empty by default: this
    // plugin never reaches into another installation on its own.
    upstreamLibrary: String(config.upstreamLibrary || ''),
    seedFromUpstream: config.seedFromUpstream === true,
    autostart: config.autostart !== false,
    writeHooksConfig: config.writeHooksConfig !== false,
    shutdownOnDispose: config.shutdownOnDispose !== false,
    readinessTimeoutMs: Number(config.readinessTimeoutMs ?? 25000),
    connectTimeoutMs: Number(config.connectTimeoutMs ?? 600),
    probeTimeoutMs: Number(config.probeTimeoutMs ?? 4000),
    device: String(config.device || ''),
    model: String(config.model || ''),
  }
}

/** The MCP endpoint this bundle's mcp-client row points at. */
export function mcpUrl(options) {
  return options.mcpUrl || `http://${options.host}:${options.port}${options.mcpPath}`
}

/**
 * Does anything accept a TCP connection on host:port?
 * @returns {Promise<boolean>}
 */
export function portOpen(host, port, timeoutMs = 600) {
  return new Promise((resolve) => {
    const socket = createConnection({ host, port })
    let settled = false
    const finish = (value) => {
      if (settled) return
      settled = true
      socket.destroy()
      resolve(value)
    }
    socket.setTimeout(timeoutMs)
    socket.once('connect', () => finish(true))
    socket.once('timeout', () => finish(false))
    socket.once('error', () => finish(false))
  })
}

/** First existing file among absolute candidate paths. */
function firstFile(candidates) {
  for (const candidate of candidates) {
    if (!candidate) continue
    try {
      if (existsSync(candidate) && statSync(candidate).isFile()) return candidate
    } catch {
      // unreadable candidate: try the next one
    }
  }
  return ''
}

/** Absolute interpreter candidates for this platform, most specific first. */
function pythonCandidates(options, env) {
  const candidates = []
  if (options.pythonExe) candidates.push(options.pythonExe)
  if (env.FLYMEMORY_PYTHON) candidates.push(env.FLYMEMORY_PYTHON)
  if (process.platform === 'win32') {
    const localAppData = env.LOCALAPPDATA
    if (localAppData) {
      for (const version of ['314', '313', '312', '311', '310']) {
        candidates.push(join(localAppData, 'Programs', 'Python', `Python${version}`, 'python.exe'))
      }
    }
  } else {
    candidates.push('/opt/homebrew/bin/python3', '/usr/local/bin/python3', '/usr/bin/python3')
  }
  // Bare names are resolved through PATH below.
  const pathDirs = String(env.PATH || '').split(delimiter).filter(Boolean)
  const names = process.platform === 'win32' ? ['python.exe'] : ['python3', 'python']
  for (const name of names) {
    candidates.push(name)
  }
  return { candidates, pathDirs, names }
}

/** Resolve a bare command name against PATH. */
function which(name, pathDirs) {
  if (!name) return ''
  if (name.includes('/') || name.includes('\\')) return firstFile([name])
  const suffixes = process.platform === 'win32' ? ['', '.exe', '.cmd', '.bat'] : ['']
  const found = []
  for (const dir of pathDirs) {
    for (const suffix of suffixes) found.push(join(dir, name + suffix))
  }
  return firstFile(found)
}

/** True when the interpreter can find the engine's heavy dependency. */
function interpreterHasEngine(pythonExe, timeoutMs = 20000) {
  const probe = 'import importlib.util,sys;sys.exit(0 if importlib.util.find_spec("sentence_transformers") else 3)'
  try {
    const result = spawnSync(pythonExe, ['-c', probe], { encoding: 'utf8', windowsHide: true, timeout: timeoutMs })
    return result.status === 0
  } catch {
    return false
  }
}

/**
 * Locate a Python interpreter for the engine.
 *
 * A configured `pythonExe` (or `FLYMEMORY_PYTHON`) is honoured first, then the
 * platform's usual install locations, then PATH. Candidates are checked for
 * `sentence_transformers` with a cheap `find_spec` probe, so a machine with
 * several interpreters does not silently pick one without the dependency.
 *
 * @returns {{pythonExe: string, verified: boolean, candidates: string[]}}
 */
export function findPython(options, env = process.env) {
  const { candidates, pathDirs, names } = pythonCandidates(options, env)
  const resolved = []
  const explicit = new Set([options.pythonExe, env.FLYMEMORY_PYTHON].filter(Boolean))
  for (const candidate of candidates) {
    const exe = names.includes(candidate) ? which(candidate, pathDirs) : firstFile([candidate])
    if (exe && !resolved.includes(exe)) resolved.push(exe)
  }
  if (options.verifyInterpreter) {
    for (const exe of resolved) {
      if (interpreterHasEngine(exe)) return { pythonExe: exe, verified: true, candidates: resolved }
    }
  }
  // Nothing verified: prefer an explicitly configured interpreter, else the first.
  const fallback = resolved.find((exe) => explicit.has(exe)) || resolved[0] || ''
  return { pythonExe: fallback, verified: false, candidates: resolved }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/**
 * First-run seeding: copy a library from another installation, once.
 * Disabled by default — the plugin owns its own store.
 * @returns a human-readable note for the log, or null when nothing happened.
 */
export function seedLibrary(options) {
  if (!options.seedFromUpstream) return null
  if (existsSync(options.libraryPath)) return null
  if (!options.upstreamLibrary || !existsSync(options.upstreamLibrary)) return null
  mkdirSync(dirname(options.libraryPath), { recursive: true })
  copyFileSync(options.upstreamLibrary, options.libraryPath)
  return `imported library from ${options.upstreamLibrary} (source left untouched)`
}

/**
 * Write `<dataDir>/hooks.json`, the Claude-Code-format hook config consumed by
 * the bundle's `@deepseek-ai/dsh-hooks-claude-code` row. Regenerated on every
 * activation so it follows the installed package, the resolved interpreter and
 * the resolved endpoint.
 * @returns the config path.
 */
export function writeHooksConfig(options, pythonExe) {
  const hooksDir = join(packageDir, 'python')
  const url = mcpUrl(options)
  const entry = (script, timeout) => ({
    hooks: [{ type: 'command', command: `"${pythonExe}" "${join(hooksDir, script)}" --url "${url}"`, timeout }],
  })
  const document = {
    hooks: {
      // Mechanical capture + recall injection on every user prompt.
      UserPromptSubmit: [entry('hook_auto.py', 15)],
      // Working-trail recovery pack when a session starts.
      SessionStart: [entry('hook_compact.py', 20)],
    },
  }
  mkdirSync(options.dataDir, { recursive: true })
  const path = join(options.dataDir, 'hooks.json')
  writeFileSync(path, `${JSON.stringify(document, null, 2)}\n`, 'utf8')
  return path
}

/**
 * Ask an MCP endpoint which tools it publishes.
 *
 * A stateless `tools/list` needs no handshake, and the answer distinguishes a
 * FlyMemory service from an unrelated process that happens to hold the port.
 *
 * @returns {Promise<{ok: boolean, tools: string[], error?: string}>}
 */
export async function probeEndpoint(url, timeoutMs = 4000) {
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list', params: {} }),
      signal: AbortSignal.timeout(timeoutMs),
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
        // SSE framing noise, not a JSON-RPC line
      }
    }
    if (!payload) return { ok: false, tools: [], error: `no JSON-RPC payload (HTTP ${response.status})` }
    if (payload.error) return { ok: false, tools: [], error: JSON.stringify(payload.error) }
    const tools = (payload.result?.tools || []).map((tool) => tool.name).filter(Boolean)
    return { ok: true, tools }
  } catch (error) {
    return { ok: false, tools: [], error: String(error?.message || error) }
  }
}

/** Child environment for the engine: inherited plus the configured overrides. */
function engineEnv(options) {
  const env = { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' }
  if (options.device) env.FLYMEMORY_DEVICE = options.device
  if (options.model) env.FLYMEMORY_MODEL = options.model
  return env
}

/** Start the vendored engine detached, logging its stdio into the data dir. */
function spawnEngine(options, pythonExe, log) {
  mkdirSync(options.dataDir, { recursive: true })
  const logPath = join(options.dataDir, 'engine.log')
  const fd = openSync(logPath, 'a')
  const args = [
    options.scriptPath,
    '--http',
    '--host', options.host,
    '--port', String(options.port),
    '--data-dir', options.dataDir,
    '--db', options.libraryPath,
    '--write-pid-file',
  ]
  const child = spawn(pythonExe, args, {
    detached: true,
    windowsHide: true,
    stdio: ['ignore', fd, fd],
    env: engineEnv(options),
  })
  // The child inherited the descriptor; the parent copy can be closed.
  try { closeSync(fd) } catch { /* already closed */ }
  const exited = new Promise((resolve) => {
    child.once('error', (error) => {
      log?.warn(`flymemory: engine spawn failed: ${String(error)} (see ${logPath})`)
      resolve({ code: null, error })
    })
    child.once('exit', (code) => resolve({ code: code ?? null, error: null }))
  })
  child.unref()
  return { child, exited, logPath }
}

/**
 * Ensure a FlyMemory service answers on the configured endpoint.
 *
 * Reuses a service that already answers with FlyMemory tools, starts the
 * vendored engine otherwise, and never spawns onto a foreign port.
 *
 * @param {ReturnType<typeof resolveOptions>} options - resolved options.
 * @param {{info: Function, warn: Function}} [log] - optional logger.
 * @returns {Promise<{mode: 'reused'|'started'|'unavailable'|'occupied', url: string,
 *   tools: string[], child: import('node:child_process').ChildProcess|null,
 *   pythonExe: string, logPath: string|null, reason?: string, error?: string}>}
 */
export async function ensureService(options, log = console) {
  const url = mcpUrl(options)
  const base = { url, tools: [], child: null, pythonExe: '', logPath: null }

  if (await portOpen(options.host, options.port, options.connectTimeoutMs)) {
    const probe = await probeEndpoint(url, options.probeTimeoutMs)
    if (probe.ok && probe.tools.some((tool) => tool.startsWith('flymemory_'))) {
      log.info?.(`flymemory: reusing the FlyMemory service on ${options.host}:${options.port} (${probe.tools.length} tools)`)
      return { ...base, mode: 'reused', tools: probe.tools }
    }
    const reason = probe.ok
      ? `port ${options.port} answers but publishes no flymemory_ tools`
      : `port ${options.port} is occupied (${probe.error})`
    log.warn?.(`flymemory: ${reason} — set another port (config \`port\` or FLYMEMORY_MCP_PORT)`)
    return { ...base, mode: 'occupied', reason, error: probe.error }
  }

  if (!options.autostart) {
    const reason = `nothing listening on ${options.host}:${options.port} and autostart is disabled`
    log.warn?.(`flymemory: ${reason}`)
    return { ...base, mode: 'unavailable', reason }
  }

  const { pythonExe, verified, candidates } = findPython(options)
  if (!pythonExe) {
    const reason = 'no Python interpreter found — set `pythonExe` on the flymemory row or FLYMEMORY_PYTHON'
    log.warn?.(`flymemory: ${reason}`)
    return { ...base, mode: 'unavailable', reason }
  }
  if (!verified && options.verifyInterpreter) {
    log.warn?.(`flymemory: ${pythonExe} does not provide sentence_transformers — the engine will fail to start. Tried: ${candidates.join(', ') || 'none'}`)
  }
  if (!existsSync(options.scriptPath)) {
    const reason = `engine script missing at ${options.scriptPath}`
    log.warn?.(`flymemory: ${reason}`)
    return { ...base, mode: 'unavailable', reason, pythonExe }
  }

  try {
    const seeded = seedLibrary(options)
    if (seeded) log.info?.(`flymemory: ${seeded}`)
  } catch (error) {
    log.warn?.(`flymemory: library seeding skipped: ${String(error)}`)
  }

  const { child, exited, logPath } = spawnEngine(options, pythonExe, log)
  log.info?.(`flymemory: starting engine with ${pythonExe} (logs: ${logPath})`)

  // Readiness is awaited so the mcp-client row connects on its first attempt;
  // the engine opens its port only after torch/SentenceTransformers import
  // (~15-20 s cold). readinessTimeoutMs: 0 returns immediately and lets the
  // mcp-client reconnect policy pick the tools up later.
  const deadline = Date.now() + Math.max(0, options.readinessTimeoutMs)
  let ready = false
  let died = null
  while (Date.now() < deadline) {
    if (await portOpen(options.host, options.port, options.connectTimeoutMs)) {
      ready = true
      break
    }
    const settled = await Promise.race([exited.then((outcome) => outcome), sleep(500).then(() => null)])
    if (settled) {
      died = settled
      break
    }
  }

  if (!ready && died) {
    const reason = `engine exited with code ${died.code} before opening ${options.host}:${options.port} — see ${logPath}`
    log.warn?.(`flymemory: ${reason}`)
    return { ...base, mode: 'unavailable', reason, pythonExe, logPath, child }
  }
  if (!ready) {
    if (options.readinessTimeoutMs > 0) {
      // Not fatal: the mcp-client row keeps reconnecting. A cold torch import
      // can exceed the wait, so this is informational unless the engine died.
      log.warn?.(`flymemory: engine still starting after ${options.readinessTimeoutMs}ms — the memory tools appear once it answers (see ${logPath})`)
      return { ...base, mode: 'unavailable', reason: 'readiness timeout', pythonExe, logPath, child }
    }
    log.info?.('flymemory: not waiting for readiness; the mcp-client row will reconnect when the engine is up')
    return { ...base, mode: 'started', pythonExe, logPath, child }
  }

  const probe = await probeEndpoint(url, options.probeTimeoutMs)
  log.info?.(probe.ok
    ? `flymemory: engine ready at ${url} (${probe.tools.length} tools)`
    : `flymemory: engine ready at ${url}, tool list not readable yet`)
  return { ...base, mode: 'started', tools: probe.tools, pythonExe, logPath, child }
}

/**
 * Cordis plugin entry: bring the service up, refresh the hook config, and
 * arrange teardown of the engine this activation started.
 *
 * Declared `async` on purpose: Cordis awaits an async function plugin, so the
 * bundle's mcp-client row activates against a service that is already there.
 *
 * @param ctx - plugin context (logger only; no service dependency).
 * @param config - row config, see resolveOptions.
 */
export async function apply(ctx, config = {}) {
  const log = ctx.logger
  const options = resolveOptions(config)

  let child = null
  let disposed = false
  const shutdown = () => {
    if (disposed) return
    disposed = true
    if (child && options.shutdownOnDispose) {
      try { child.kill() } catch { /* already gone */ }
    }
  }
  ctx.effect(() => shutdown, 'flymemory.supervisor')

  const outcome = await ensureService(options, log)
  child = outcome.child

  if (options.writeHooksConfig) {
    try {
      const { pythonExe } = findPython(options)
      if (pythonExe) {
        const path = writeHooksConfig(options, pythonExe)
        log.info(`flymemory: hook config written to ${path}`)
      } else {
        log.warn('flymemory: hook config not written — no Python interpreter found')
      }
    } catch (error) {
      log.warn(`flymemory: hook config not written: ${String(error)}`)
    }
  }

  if (outcome.mode === 'reused' || outcome.mode === 'started') {
    log.info(`flymemory: store ${options.libraryPath} served at ${outcome.url}`)
  }
}

/**
 * Standalone report for the CLI and the tests: what this plugin would do,
 * without mounting it in a harness. Never starts or kills anything.
 */
export function describe(config = {}, env = process.env) {
  const options = resolveOptions(config, env)
  const { pythonExe, verified, candidates } = findPython(options, env)
  return {
    ...options,
    packageDir,
    url: mcpUrl(options),
    pythonExe,
    pythonVerified: verified,
    pythonCandidates: candidates,
    libraryExists: existsSync(options.libraryPath),
    scriptExists: existsSync(options.scriptPath),
    upstreamLibraryExists: options.upstreamLibrary ? existsSync(options.upstreamLibrary) : false,
    hooksConfigPath: join(options.dataDir, 'hooks.json'),
    engineLogPath: join(options.dataDir, 'engine.log'),
    pidPath: join(options.dataDir, 'server.pid'),
  }
}

/**
 * Run a probe command in the resolved interpreter, e.g. the dependency check
 * behind `dsh-flymemory doctor`.
 * @returns {{ok: boolean, pythonExe: string, detail: string}}
 */
export function probePython(config = {}, args = ['-c', 'import torch, sentence_transformers, mcp'], env = process.env) {
  const options = resolveOptions(config, env)
  const { pythonExe } = findPython(options, env)
  if (!pythonExe) return { ok: false, pythonExe: '', detail: 'no interpreter found' }
  const result = spawnSync(pythonExe, args, { encoding: 'utf8', windowsHide: true, timeout: 180000, env: engineEnv(options) })
  const output = `${result.stdout || ''}${result.stderr || ''}`.trim()
  return { ok: result.status === 0, pythonExe, detail: output.slice(-2000) }
}

/** Read `<dataDir>/server.pid` as a number, or 0 when absent/stale-format. */
export function readPidFile(options) {
  try {
    const value = Number.parseInt(readFileSync(join(options.dataDir, 'server.pid'), 'utf8').trim(), 10)
    return Number.isFinite(value) ? value : 0
  } catch {
    return 0
  }
}

/** Stop the engine recorded in the pid file. @returns the pid, or 0. */
export function stopEngine(options) {
  const pid = readPidFile(options)
  if (!pid) return 0
  try {
    process.kill(pid)
  } catch {
    return 0
  }
  try { rmSync(join(options.dataDir, 'server.pid')) } catch { /* best effort */ }
  return pid
}

/** A private scratch data directory, for tests and one-off runs. */
export function tempDataDir(prefix = 'dsh-flymemory') {
  return join(tmpdir(), `${prefix}-${process.pid}-${Date.now()}`)
}
