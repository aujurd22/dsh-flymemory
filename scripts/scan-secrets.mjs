#!/usr/bin/env node
/**
 * Refuse a commit that contains a real-looking credential.
 *
 * Why this exists: on 2026-11-01 three live keys (a DeepSeek key, a Command Code
 * gateway key and the AMD free-endpoint key) were pasted into
 * `tests/unit.test.mjs` as fixtures and pushed. A filter can only protect the
 * library; a repository needs a guard *before* the push, which is this file.
 *
 * It scans tracked text files for credential shapes and fails when one appears
 * outside an explicit allow-list of synthetic markers. Run it standalone
 * (`node scripts/scan-secrets.mjs`) or from CI.
 */
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'

const PATTERNS = [
  [/rc-[0-9a-f]{32,}/i, 'AMD free-endpoint key'],
  [/user_[A-Za-z0-9]{32,}/, 'Command Code gateway key'],
  [/\bsk-[A-Za-z0-9_-]{20,}/, 'OpenAI/DeepSeek style key'],
  [/\b(?:ghp_|gho_|github_pat_)[A-Za-z0-9_]{20,}/, 'GitHub token'],
  [/\bglpat-[A-Za-z0-9_-]{20,}/, 'GitLab token'],
  [/\bnpm_[A-Za-z0-9]{20,}/, 'npm token'],
  [/\bhf_[A-Za-z0-9]{20,}/, 'Hugging Face token'],
  [/\bAIza[0-9A-Za-z_-]{30,}/, 'Google API key'],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----[A-Za-z0-9+/=]{40,}/, 'private key'],
]

// Synthetic values the tests may use: they match the shapes above on purpose.
const ALLOWED = /FAKE|fake|0123456789abcdef|placeholder|example|xxx/i

function trackedFiles() {
  const out = execFileSync('git', ['ls-files'], { encoding: 'utf8' })
  return out.split('\n').filter(Boolean)
}

function main() {
  const findings = []
  for (const file of trackedFiles()) {
    if (/\.(png|jpg|jpeg|gif|webp|ico|pkl|zip|gz)$/i.test(file)) continue
    // The working tree, not HEAD: this must catch what is about to be committed.
    let text
    try {
      text = readFileSync(file, 'utf8')
    } catch {
      continue
    }
    const lines = text.split('\n')
    for (let index = 0; index < lines.length; index++) {
      const line = lines[index]
      if (ALLOWED.test(line)) continue
      for (const [pattern, label] of PATTERNS) {
        const match = line.match(pattern)
        if (match) findings.push({ file, line: index + 1, label, sample: match[0].slice(0, 12) + '…' })
      }
    }
  }
  if (!findings.length) {
    console.log('scan-secrets: clean')
    return
  }
  console.error('scan-secrets: credential-shaped text found — fix before pushing')
  for (const item of findings) {
    console.error(`  ${item.file}:${item.line}  ${item.label}  ${item.sample}`)
  }
  process.exit(1)
}

main()
