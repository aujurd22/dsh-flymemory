#!/usr/bin/env node
/**
 * Portable test entry point.
 *
 * `node --test "<glob>"` only understands globs from Node 21, and passing a
 * directory stopped behaving consistently in Node 24, so this collects the test
 * files itself and hands the explicit list to the runner. `npm test` therefore
 * does the same thing on Node 20, 22 and 24, on every platform.
 *
 * Any `tests/*.test.mjs` file is picked up automatically; add new suites there.
 */
import { spawnSync } from 'node:child_process'
import { readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const files = readdirSync(here)
  .filter((name) => name.endsWith('.test.mjs'))
  .sort()
  .map((name) => join(here, name))

if (files.length === 0) {
  console.error(`no *.test.mjs files found in ${here}`)
  process.exit(1)
}

console.log(`running ${files.length} test file(s) with ${process.version}`)
const result = spawnSync(process.execPath, ['--test', ...files], { stdio: 'inherit' })
process.exit(result.status ?? 1)
