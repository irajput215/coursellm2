import { useCallback, useSyncExternalStore } from 'react'

/**
 * Subscribe to a CSS media query.
 *
 * Implemented with `useSyncExternalStore` rather than `useState` + `useEffect`
 * so the snapshot is read during render and the subscription is the only thing
 * the effect does. Used to close the navigation drawer at `lg` and up.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onStoreChange: () => void): (() => void) => {
      if (typeof window.matchMedia !== 'function') return () => undefined
      const list = window.matchMedia(query)
      list.addEventListener('change', onStoreChange)
      return () => list.removeEventListener('change', onStoreChange)
    },
    [query],
  )

  const getSnapshot = useCallback((): boolean => {
    if (typeof window.matchMedia !== 'function') return false
    return window.matchMedia(query).matches
  }, [query])

  return useSyncExternalStore(subscribe, getSnapshot)
}
