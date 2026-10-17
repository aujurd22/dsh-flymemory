#!/usr/bin/env node
/**
 * dsh-flymemory CLI — inspect, start, stop and debug the memory service that
 * the DSH plugin uses. Cross-platform, dependency-free.
 *
 *   dsh-flymemory status          what the plugin would use, and what is live
 *   dsh-flymemory start           start the service now (detached)
 *   dsh-flymemory stop            stop the service recorded in server.pid
 *   dsh-flymemory log [-n 40]     tail the engine logs
 *   dsh-flymemory doctor          check the interpreter, dependencies, paths
 *
 * The CLI never needs DSH to be running; the plugin starts the same service by
 * itself when the harness boots.
 */
import { existsSync, readFileSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

import {
  describe,
  ensureService,
  probeEndpoint,
  probePython,
  readEffectiveEndpoint,
  readState,
  resolveOptions,
  stopEngine,
} from '../lib/index.js'

const argv = process.argv.slice(2)
const command = (argv[0] || 'status').toLowerCase()
const flag = (name, fallback = undefined) => {
  const index = argv.indexOf(name)
  return index === -1 ? fallback : argv[index + 1]
}

const config = {}
if (flag('--port')) config.port = Number(flag('--port'))
if (flag('--host')) config.host = flag('--host')
if (flag('--data-dir')) config.dataDir = flag('--data-dir')
if (flag('--library')) config.libraryPath = flag('--library')
if (flag('--python')) config.pythonExe = flag('--python')

const options = resolveOptions(config)
const info = describe(config)
const log = {
  info: (message) => console.log(message),
  warn: (message) => console.warn(message),
}

const profileManifest = (() => {
  const dir = process.env.DSH_PROFILE_DIR
    || join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'profiles', process.env.DSH_PROFILE || 'desktop')
  return join(dir, 'package.json')
})()

const megabytes = (path) => {
  try {
    return `${(statSync(path).size / 1024 / 1024).toFixed(1)} MB`
  } catch {
    return '—'
  }
}

const tail = (path, lines) => {
  try {
    const content = readFileSync(path, 'utf8').split(/\r?\n/)
    return content.slice(Math.max(0, content.length - lines - 1)).join('\n').trimEnd()
  } catch {
    return ''
  }
}

async function status() {
  // A profile patch may point the plugin somewhere other than this module's
  // defaults. `state.json` (written on every activation) and the generated hook
  // config are the records of what actually won; prefer them over the defaults.
  const state = readState(info.dataDir)
  const endpoint = state?.endpoint || readEffectiveEndpoint(info.dataDir) || info.url
  const library = state?.libraryPath || info.libraryPath
  const libraryExists = existsSync(library)
  const overridden = Boolean(state && (endpoint !== info.url || library !== info.libraryPath))

  console.log('dsh-flymemory status')
  console.log(`  endpoint        ${endpoint}`)
  if (overridden) {
    console.log(`                  resolved by the running plugin; this checkout's default is ${info.url}`)
    console.log('                  (a profile patch override is in effect)')
  } else if (!state && !readEffectiveEndpoint(info.dataDir)) {
    console.log('                  no state.json or hooks.json yet — the plugin has not activated against this data dir')
  }
  console.log(`  data dir        ${info.dataDir}`)
  console.log(`  library         ${library}  ${libraryExists ? `[${megabytes(library)}]` : '[not created yet]'}`)
  console.log(`  interpreter     ${info.pythonExe || 'NOT FOUND'}${info.pythonExe ? (info.pythonVerified ? '  (sentence_transformers available)' : '  (sentence_transformers NOT found)') : ''}`)
  console.log(`  engine script   ${info.scriptExists ? info.scriptPath : `MISSING (${info.scriptPath})`}`)
  console.log(`  hooks config    ${existsSync(info.hooksConfigPath) ? info.hooksConfigPath : 'not written yet'}`)

  if (existsSync(profileManifest)) {
    try {
      const manifest = JSON.parse(readFileSync(profileManifest, 'utf8'))
      const bundles = manifest?.dsh?.profile?.bundles || []
      const selected = bundles.includes('dsh-flymemory')
      console.log(`  profile         ${profileManifest}`)
      console.log(`  bundle selected ${selected ? 'yes' : 'NO'}${manifest?.dependencies?.['dsh-flymemory'] ? `  (${manifest.dependencies['dsh-flymemory']})` : ''}`)
    } catch (error) {
      console.log(`  profile         unreadable: ${String(error)}`)
    }
  } else {
    console.log(`  profile         no manifest at ${profileManifest} (DSH may be running without a profile)`)
  }

  const live = await probeEndpoint(endpoint, 4000)
  if (live.ok) {
    console.log(`  live service     yes — ${live.tools.length} tools`)
  } else {
    console.log(`  live service     no (${live.error})`)
    console.log('                  the plugin starts it during DSH boot; run `dsh-flymemory start` to start it now')
  }
  process.exit(0)
}

async function start() {
  const outcome = await ensureService(options, log)
  if (outcome.mode === 'unavailable' || outcome.mode === 'occupied') {
    console.error(`\nnot started: ${outcome.reason}`)
    process.exit(1)
  }
  console.log(`\n${outcome.mode === 'reused' ? 'already running' : 'started'} at ${outcome.url}${outcome.tools.length ? ` — ${outcome.tools.length} tools` : ''}`)
  if (outcome.logPath) console.log(`logs: ${outcome.logPath}`)
  process.exit(0)
}

function stop() {
  const pid = stopEngine(options)
  if (pid) console.log(`stopped engine pid ${pid}`)
  else console.log('no running engine recorded in server.pid (it may be served by another process)')
  process.exit(0)
}

function logCommand() {
  const lines = Number(flag('-n', flag('--lines', 40))) || 40
  const engine = tail(info.engineLogPath, lines)
  const server = tail(join(info.dataDir, 'server.log'), lines)
  if (!engine && !server) {
    console.log(`no logs under ${info.dataDir} yet`)
    process.exit(0)
  }
  if (engine) console.log(`--- ${info.engineLogPath} ---\n${engine}`)
  if (server) console.log(`--- ${join(info.dataDir, 'server.log')} ---\n${server}`)
  process.exit(0)
}

async function doctor() {
  console.log('dsh-flymemory doctor\n')
  const problems = []
  if (!info.pythonExe) problems.push('no Python interpreter found — set FLYMEMORY_PYTHON or the row\'s pythonExe')
  if (!info.scriptExists) problems.push(`engine script missing: ${info.scriptPath}`)
  console.log(`interpreter candidates tried:\n  ${info.pythonCandidates.join('\n  ') || '(none)'}`)
  if (info.pythonExe) {
    const probe = probePython(config)
    console.log(`\n${info.pythonExe}\n  import torch, sentence_transformers, mcp → ${probe.ok ? 'ok' : 'FAILED'}`)
    if (!probe.ok) {
      problems.push(`dependencies missing — ${probe.detail.split(/\r?\n/).slice(-1)[0] || 'see output above'}`)
      console.log(`  ${probe.detail.split(/\r?\n/).slice(-3).join('\n  ')}`)
      console.log('\n  install them with:')
      console.log(`    "${info.pythonExe}" -m pip install torch sentence-transformers "mcp>=1.30,<2" numpy`)
    }
  }
  // Probe the endpoint the plugin really uses: a profile patch may override
  // this checkout's own port, and the activation record names the winner.
  const state = readState(info.dataDir)
  const endpoint = state?.endpoint || readEffectiveEndpoint(info.dataDir) || info.url
  const library = state?.libraryPath || info.libraryPath
  const live = await probeEndpoint(endpoint, 4000)
  console.log(`\nendpoint: ${endpoint}${endpoint !== info.url ? '  (resolved by the plugin; a profile patch override is in effect)' : ''}`)
  console.log(`  ${live.ok ? `answers with ${live.tools.length} tools` : `not reachable (${live.error})`}`)
  if (live.ok && !live.tools.some((tool) => tool.startsWith('flymemory_'))) {
    problems.push('the port answers but publishes no flymemory_ tools — another program is using it')
  }
  console.log(`library: ${library}  ${existsSync(library) ? '' : '(not created yet)'}`)
  console.log(`\ndata dir: ${info.dataDir}`)
  if (problems.length === 0) {
    console.log('\nno problems found.')
    process.exit(0)
  }
  console.log(`\n${problems.length} problem(s):`)
  for (const problem of problems) console.log(`  - ${problem}`)
  process.exit(1)
}

const commands = { status, start, stop, log: logCommand, doctor }
const run = commands[command]
if (!run) {
  console.error(`unknown command "${command}"\n\nusage: dsh-flymemory [status|start|stop|log|doctor] [--port N] [--data-dir DIR] [--python PATH]`)
  process.exit(2)
}
await run()
