/**
 * Version helpers: the update check must agree with what people see on npm.
 */
import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { PACKAGE_NAME, PACKAGE_VERSION, compareVersions } from '../lib/index.js'

describe('compareVersions', () => {
  it('orders plain releases', () => {
    assert.equal(compareVersions('1.0.3', '1.0.3'), 0)
    assert.equal(compareVersions('1.0.4', '1.0.3'), 1)
    assert.equal(compareVersions('1.0.3', '1.0.4'), -1)
    assert.equal(compareVersions('2.0.0', '1.9.9'), 1)
  })

  it('tolerates a leading v and short forms', () => {
    assert.equal(compareVersions('v1.2.0', '1.2.0'), 0)
    assert.equal(compareVersions('1.2', '1.2.0'), 0)
    assert.equal(compareVersions('1', '1.0.1'), -1)
  })

  it('sorts prereleases below their release', () => {
    assert.equal(compareVersions('1.0.0-rc.1', '1.0.0'), -1)
    assert.equal(compareVersions('1.0.0', '1.0.0-rc.1'), 1)
    assert.equal(compareVersions('1.0.0-rc.2', '1.0.0-rc.1'), 1)
    assert.equal(compareVersions('1.0.0-alpha', '1.0.0-beta'), -1)
  })

  it('treats missing input as zero rather than throwing', () => {
    assert.equal(compareVersions('', '0.0.0'), 0)
    assert.equal(compareVersions(undefined, '0.0.1'), -1)
  })
})

describe('package identity', () => {
  it('exposes the version and name the CLI reports', () => {
    assert.match(PACKAGE_NAME, /^dsh-flymemory$/)
    assert.match(PACKAGE_VERSION, /^\d+\.\d+\.\d+/)
  })
})
