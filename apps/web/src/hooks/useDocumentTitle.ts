import { useEffect } from 'react'

const SUFFIX = 'CourseLLM'

/** Set a per-route document title; restored to the product name on unmount. */
export function useDocumentTitle(title?: string): void {
  useEffect(() => {
    document.title = title === undefined || title === '' ? SUFFIX : `${title} · ${SUFFIX}`
    return () => {
      document.title = SUFFIX
    }
  }, [title])
}
