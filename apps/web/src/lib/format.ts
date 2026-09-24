/** Presentation helpers. Pure functions only — no data is invented here. */

export function formatNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value)
}

export function formatDecimal(value: number, digits = 2): string {
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)
}

/** Mastery values arrive as a 0–1 fraction; render them as a percentage. */
export function formatPercent(fraction: number, digits = 0): string {
  return `${formatDecimal(fraction * 100, digits)}%`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${formatDecimal(value, value >= 10 ? 0 : 1)} ${units[unitIndex] ?? 'B'}`
}

export function formatHours(hours: number): string {
  return `${formatDecimal(hours, hours % 1 === 0 ? 0 : 1)} h`
}

export function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)
}

export function formatDateTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

/** `reranker_unavailable` → `Reranker unavailable`. */
export function humaniseToken(token: string): string {
  const spaced = token.replace(/[_-]+/gu, ' ').trim()
  if (spaced === '') return token
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

export function pluralise(count: number, singular: string, plural?: string): string {
  return count === 1 ? singular : (plural ?? `${singular}s`)
}

export function truncate(value: string, max: number): string {
  return value.length <= max ? value : `${value.slice(0, max - 1)}…`
}

/** A short, human-quotable slice of a UUID for support tickets. */
export function shortId(id: string): string {
  return id.slice(0, 8)
}

export function percentOfTotal(part: number, total: number): number {
  return total === 0 ? 0 : part / total
}
