import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/**
 * The prototype shipped hardcoded metrics — "Questions Asked 142",
 * "Avg Evaluation Score 94%" and two invented deadlines — rendered as if they
 * were real. This test makes their reintroduction a test failure.
 */
const MOCK_PHRASES = ['Questions Asked', 'Avg Evaluation Score', '94%']
const MOCK_NUMBER = /\b142\b/u

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const thisFile = fileURLToPath(import.meta.url)

/**
 * Tests are excluded from the source scan: they contain the mock strings in
 * order to assert their absence, which is the opposite of shipping them.
 */
function isTestFile(path: string): boolean {
  return path === thisFile || /\.test\.(ts|tsx)$/u.test(path) || path.includes('/src/test/')
}

function walk(directory: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const full = join(directory, entry.name)
    if (entry.isDirectory()) walk(full, acc)
    else acc.push(full)
  }
  return acc
}

function readAll(files: readonly string[]): { path: string; text: string }[] {
  return files.map((path) => ({ path, text: readFileSync(path, 'utf8') }))
}

describe('no mock data', () => {
  it('contains none of the prototype mock strings in the source tree', () => {
    const files = walk(resolve(webRoot, 'src')).filter((path) => !isTestFile(path))
    expect(files.length).toBeGreaterThan(10)

    for (const { path, text } of readAll(files)) {
      for (const phrase of MOCK_PHRASES) {
        expect(text.includes(phrase), `${path} contains the mock string "${phrase}"`).toBe(false)
      }
      expect(MOCK_NUMBER.test(text), `${path} contains the mock number 142`).toBe(false)
    }
  })

  it('contains none of them in the built bundle once dist/ exists', () => {
    const dist = resolve(webRoot, 'dist')
    if (!existsSync(dist)) {
      // `npm run build` has not run in this working copy. The source scan above
      // still guards the invariant; rebuilding is what the release pipeline
      // does before this test is run against the bundle.
      expect(existsSync(dist)).toBe(false)
      return
    }

    const bundle = readAll(walk(dist))
    expect(bundle.length).toBeGreaterThan(0)
    for (const { path, text } of bundle) {
      for (const phrase of MOCK_PHRASES) {
        expect(text.includes(phrase), `${path} contains the mock string "${phrase}"`).toBe(false)
      }
    }
    // A bare `142` is deliberately not asserted against the minified bundle:
    // three digits can occur inside a hash or a numeric literal, which would
    // make the test flaky rather than meaningful. The rendered prototype string
    // was "Questions Asked 142", and the phrase check above covers it.
  })
})
