import type { ReactNode } from 'react'

import { useTheme, type Theme } from '@/hooks/useTheme'

const OPTIONS: { value: Theme; label: string; icon: string }[] = [
  { value: 'light', label: 'Light', icon: '☀' },
  { value: 'dark', label: 'Dark', icon: '☾' },
  { value: 'system', label: 'System', icon: '🖥' },
]

/** A three-way theme control. `system` is the default, not a hidden mode. */
export function ThemeToggle(): ReactNode {
  const { theme, setTheme } = useTheme()
  return (
    <div className="field" style={{ margin: 0 }}>
      <span className="stat-label" id="theme-label">
        Theme
      </span>
      <div className="engine-toggle" role="group" aria-labelledby="theme-label">
        {OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={theme === option.value}
            onClick={() => setTheme(option.value)}
          >
            <span aria-hidden="true">{option.icon}</span> {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}
