#!/usr/bin/env node
/**
 * Release pipeline for dsh-flymemory.
 *
 *   node scripts/release.mjs patch            # 1.0.3 -> 1.0.4
 *   node scripts/release.mjs minor            # 1.0.3 -> 1.1.0
 *   node scripts/release.mjs 2.0.0            # explicit version
 *   node scripts/release.mjs minor --dry-run  # print the plan, change nothing
 *   node scripts/release.mjs patch --no-publish
 *
 * Steps: tests -> bump package.json -> commit -> tag -> push -> npm publish ->
 * wait for the registry to serve the new version.
 *
 * Authentication is npm's own: run `npm login`, or point
 * NPM_CONFIG_USERCONFIG at an npmrc holding a publish token. This script never
 * reads or writes credentials.
 *
 * Manual items it cannot check are printed at the end.
 */
import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { compareVersions } from '../lib/index.js'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..')
const argv = process.argv.slice(2)
const dryRun = argv.includes('--dry-run')
const skipTests = argv.includes('--skip-tests')
const noPublish = argv.includes('--no-publish')
const verbose = argv.includes('--verbose')
const bumpArg = argv.find((arg) => !arg.startsWith('-')) || 'patch'
const npmCmd = process.platform === 'win32' ? 'npm.cmd' : 'npm'
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function run(command, args, { inherit = false } = {}) {
  const line = [command, ...args].join(' ')
  if (dryRun) {
    console.log(`    (dry) ${line}`)
    return ''
  }
  if (inherit) {
    execFileSync(command, args, { cwd: root, stdio: 'inherit' })
    return ''
  }
  return execFileSync(command, args, { cwd: root, encoding: 'utf8' }).trim()
}

function bumpVersion(version, kind) {
  const [major, minor, patch] = String(version).split('-')[0].split('.').map((part) => Number.parseInt(part, 10) || 0)
  if (kind === 'major') return `${major + 1}.0.0`
  if (kind === 'minor') return `${major}.${minor + 1}.0`
  if (kind === 'patch') return `${major}.${minor}.${patch + 1}`
  throw new Error(`unknown bump "${kind}" — use patch, minor, major, or an explicit x.y.z`)
}

const packagePath = join(root, 'package.json')
const pkg = JSON.parse(readFileSync(packagePath, 'utf8'))
const next = /^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$/.test(bumpArg) ? bumpArg : bumpVersion(pkg.version, bumpArg)
if (compareVersions(next, pkg.version) <= 0) {
  console.error(`refusing to release ${next}: it is not newer than ${pkg.version}`)
  process.exit(1)
}

console.log(`release ${pkg.name} ${pkg.version} -> ${next}${dryRun ? '  [dry run]' : ''}`)

if (!skipTests) {
  console.log('- tests')
  if (verbose) {
    run(process.execPath, ['tests/run.mjs'], { inherit: true })
  } else {
    const output = run(process.execPath, ['tests/run.mjs'])
    const summary = output.split('\n').filter((line) => /^ℹ (tests|pass|fail|suites)\b/.test(line))
    for (const line of summary) console.log(`    ${line}`)
    if (!summary.some((line) => /^ℹ fail 0$/.test(line))) {
      console.error(output)
      console.error('tests failed — not releasing')
      process.exit(1)
    }
  }
}

const dirty = dryRun ? '' : run('git', ['status', '--porcelain'])
if (dirty) console.log(`  note: ${dirty.split('\n').length} changed file(s) will be committed with the release`)
const branch = dryRun ? 'main' : run('git', ['rev-parse', '--abbrev-ref', 'HEAD'])
if (branch && branch !== 'main') console.log(`  note: releasing from branch "${branch}"`)

console.log(`- bump to ${next}`)
pkg.version = next
if (!dryRun) writeFileSync(packagePath, `${JSON.stringify(pkg, null, 2)}\n`)

console.log('- commit, tag, push')
run('git', ['add', '-A'])
run('git', ['commit', '-m', `release v${next}`])
run('git', ['tag', '-f', `v${next}`])
run('git', ['push', 'origin', 'HEAD'])
run('git', ['push', 'origin', `v${next}`])

if (!noPublish) {
  console.log('- npm publish')
  run(npmCmd, ['publish', '--access', 'public'], { inherit: true })

  console.log('- waiting for the registry')
  const deadline = Date.now() + 4 * 60 * 1000
  let live = ''
  while (!dryRun && Date.now() < deadline) {
    try {
      const response = await fetch(`https://registry.npmjs.org/${pkg.name}`, {
        signal: AbortSignal.timeout(8000),
        headers: { accept: 'application/vnd.npm.install-v1+json' },
      })
      if (response.ok) {
        live = String((await response.json())?.['dist-tags']?.latest || '')
        if (live && compareVersions(live, next) === 0) break
      }
    } catch {
      /* keep waiting */
    }
    await sleep(10000)
  }
  if (dryRun) {
    console.log('  (dry) would wait for the registry to serve the new version')
  } else if (compareVersions(live, next) === 0) {
    console.log(`  registry serves ${pkg.name}@${live}`)
  } else {
    console.log(`  registry still reports "${live || 'unknown'}" — publish may still be propagating`)
  }
}

console.log(`
Manual items this script cannot check:
  - README: update the "verified on DSH <version>" compatibility note if the harness moved
  - THIRD_PARTY_NOTICES.md: update the sync point if the vendored engine was re-synced
  - examples/early-band.patch.yml: regenerate if DSH changed the standard preset
  - directory listings (dsh-plugin.org, dshopen, dsh.so) sync on their own; nothing to do
`)
