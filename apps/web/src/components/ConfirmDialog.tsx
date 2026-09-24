import { useEffect, useId, useRef, type ReactNode } from 'react'

/**
 * A small accessible confirmation dialog.
 *
 * Focus moves to the confirm button on open, Escape cancels, and focus returns
 * to whatever was focused before. Destructive actions in this application are
 * never one click away from an accidental activation.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  destructive = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean
  title: string
  description?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  destructive?: boolean
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}): ReactNode {
  const titleId = useId()
  const confirmRef = useRef<HTMLButtonElement>(null)
  const previouslyFocused = useRef<Element | null>(null)

  useEffect(() => {
    if (!open) return undefined
    previouslyFocused.current = document.activeElement
    confirmRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      if (previouslyFocused.current instanceof HTMLElement) previouslyFocused.current.focus()
    }
  }, [open, onCancel])

  if (!open) return null

  return (
    <>
      <button
        type="button"
        className="dialog-backdrop"
        aria-label="Close dialog"
        tabIndex={-1}
        onClick={onCancel}
      />
      <div className="dialog-center">
        <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
          <h2 id={titleId}>{title}</h2>
          {description === undefined ? null : <div className="muted">{description}</div>}
          <div className="row" style={{ marginTop: '1rem', justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={onCancel} disabled={busy}>
              {cancelLabel}
            </button>
            <button
              type="button"
              className={destructive ? 'btn btn-danger' : 'btn btn-primary'}
              onClick={onConfirm}
              disabled={busy}
              ref={confirmRef}
            >
              {busy ? 'Working…' : confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
