import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/**
 * A CSS contract test for the responsive claim.
 *
 * The component test proves the drawer *behaviour* at 375px; this proves the
 * stylesheet actually takes the sidebar off-canvas below `lg` and pins it back
 * at `lg`. Without it, a stray edit could leave `position: fixed` in place and
 * silently eat 16rem of a phone viewport again.
 */
const cssPath = resolve(dirname(fileURLToPath(import.meta.url)), 'global.css')
const css = readFileSync(cssPath, 'utf8')

function blockAt(source: string, startIndex: number): string {
  let depth = 0
  for (let index = startIndex; index < source.length; index += 1) {
    const character = source[index]
    if (character === '{') depth += 1
    else if (character === '}') {
      depth -= 1
      if (depth === 0) return source.slice(startIndex, index + 1)
    }
  }
  throw new Error('Unbalanced braces in global.css')
}

const LG_MEDIA = '@media (min-width: 1024px)'

describe('responsive stylesheet', () => {
  it('takes the sidebar off-canvas by default', () => {
    const sidebarRule = /\.app-sidebar\s*\{([^}]*)\}/u.exec(css)
    expect(sidebarRule).not.toBeNull()
    const body = sidebarRule?.[1] ?? ''
    expect(body).toContain('position: fixed')
    expect(body).toContain('transform: translateX(-102%)')
    expect(body).toContain('width: min(82vw, var(--sidebar-width))')
  })

  it('pins the sidebar back at lg and hides the drawer backdrop', () => {
    const mediaIndex = css.indexOf(LG_MEDIA)
    expect(mediaIndex).toBeGreaterThan(-1)
    const mediaBlock = blockAt(css, css.indexOf('{', mediaIndex))

    expect(mediaBlock).toContain('.app-sidebar')
    expect(mediaBlock).toContain('transform: none')
    expect(mediaBlock).toContain('.sidebar-backdrop')
    expect(mediaBlock).toMatch(/\.sidebar-backdrop\s*\{[^}]*display:\s*none/u)
  })

  it('never sets a fixed minimum width on the content column', () => {
    const contentRule = /\.app-content\s*\{([^}]*)\}/u.exec(css)
    expect(contentRule).not.toBeNull()
    expect(contentRule?.[1] ?? '').toContain('min-width: 0')
  })
})
