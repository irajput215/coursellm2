import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'

export type Theme = 'light' | 'dark' | 'system'

export const THEME_STORAGE_KEY = 'coursellm.theme'

function readStoredTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  } catch {
    // Storage unavailable; fall through to the system default.
  }
  return 'system'
}

function systemPrefersDark(): boolean {
  if (typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

function applyTheme(theme: Theme): void {
  if (typeof document === 'undefined') return
  const dark = theme === 'dark' || (theme === 'system' && systemPrefersDark())
  document.documentElement.classList.toggle('dark', dark)
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
}

// ---------------------------------------------------------------------------
// A tiny shared store.
//
// The theme has more than one consumer (the sidebar toggle and the settings
// page), so it cannot be per-hook `useState`: two copies would drift apart.
// ---------------------------------------------------------------------------

let currentTheme: Theme = typeof window === 'undefined' ? 'system' : readStoredTheme()
const themeListeners = new Set<() => void>()

function subscribeToTheme(listener: () => void): () => void {
  themeListeners.add(listener)
  return () => {
    themeListeners.delete(listener)
  }
}

function getThemeSnapshot(): Theme {
  return currentTheme
}

applyTheme(currentTheme)

export function setTheme(next: Theme): void {
  currentTheme = next
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, next)
  } catch {
    // Persisting the preference is best-effort.
  }
  applyTheme(next)
  for (const listener of themeListeners) listener()
}

export function toggleTheme(): void {
  const resolved = currentTheme === 'system' ? (systemPrefersDark() ? 'dark' : 'light') : currentTheme
  setTheme(resolved === 'dark' ? 'light' : 'dark')
}

/** Only for tests: reset the module store between cases. */
export function __resetThemeForTests(theme: Theme = 'system'): void {
  currentTheme = theme
  applyTheme(theme)
  for (const listener of themeListeners) listener()
}

export interface ThemeController {
  theme: Theme
  resolved: 'light' | 'dark'
  setTheme: (theme: Theme) => void
  toggle: () => void
}

export function useTheme(): ThemeController {
  const theme = useSyncExternalStore(subscribeToTheme, getThemeSnapshot)
  const [prefersDark, setPrefersDark] = useState<boolean>(systemPrefersDark)

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const listener = (event: MediaQueryListEvent): void => {
      setPrefersDark(event.matches)
      // Re-apply so `system` reacts to an OS change immediately.
      if (currentTheme === 'system') applyTheme('system')
    }
    query.addEventListener('change', listener)
    return () => query.removeEventListener('change', listener)
  }, [])

  const resolved: 'light' | 'dark' = theme === 'system' ? (prefersDark ? 'dark' : 'light') : theme

  const set = useCallback((next: Theme) => setTheme(next), [])
  const toggle = useCallback(() => toggleTheme(), [])

  return { theme, resolved, setTheme: set, toggle }
}
